"""`scripts/check_health_leads.py` and its scheduled workflow.

The alert rules run as pure logic; `main()` runs the real urllib paths against a
local HTTP server standing in for /health/leads and Sentry's ingest. Nothing here
touches a live environment.
"""

import json
import sys
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import check_health_leads as monitor  # noqa: E402

WORKFLOW = ROOT / ".github" / "workflows" / "health-leads-monitor.yml"
T0 = datetime(2026, 11, 6, 12, 0, tzinfo=timezone.utc)
TOKEN = "tok-distinctive-9f3a"
SENTRY_KEY = "publickey7c1"


def _run(samples, threshold=5):
    """Feed (minutes, counts) samples through `evaluate`, recording each page."""
    state, kinds_by_minute = {}, {}
    for minutes, counts in samples:
        now = T0 + timedelta(minutes=minutes)
        state, pages, _ = monitor.evaluate(state, counts, "HTTP 503", now, threshold)
        for kind, _message in pages:
            monitor.record_page(state, kind, now, counts)
        kinds_by_minute[minutes] = [kind for kind, _ in pages]
    return state, kinds_by_minute


# --- The alert rules ---


def test_pending_backlog_pages_only_after_more_than_30_minutes():
    _, pages = _run([(0, (6, 0)), (30, (6, 0)), (31, (6, 0))])
    assert pages == {0: [], 30: [], 31: ["pending_backlog"]}


def test_pending_at_the_threshold_is_not_a_breach():
    _, pages = _run([(0, (5, 0)), (60, (5, 0))])
    assert pages == {0: [], 60: []}


def test_a_dip_below_the_threshold_restarts_the_30_minutes():
    _, pages = _run([(0, (6, 0)), (20, (5, 0)), (40, (6, 0)), (60, (6, 0))])
    assert pages[60] == []


def test_a_sustained_backlog_repages_hourly_and_a_new_episode_pages_at_once():
    _, pages = _run(
        [
            (0, (6, 0)),
            (31, (6, 0)),
            (61, (6, 0)),
            (91, (6, 0)),
            (92, (0, 0)),
            (100, (6, 0)),
            (131, (6, 0)),
        ]
    )
    assert pages[31] == ["pending_backlog"]
    assert pages[61] == []
    assert pages[91] == ["pending_backlog"]
    assert pages[131] == ["pending_backlog"]


def test_a_stuck_rise_the_healer_clears_inside_the_window_does_not_page():
    _, pages = _run([(0, (0, 1)), (10, (0, 2)), (30, (0, 1)), (50, (0, 1))])
    assert not any(pages.values())


def test_a_stuck_climb_pages_once_then_only_on_a_further_climb():
    _, pages = _run(
        [
            (0, (0, 1)),
            (10, (0, 2)),
            (41, (0, 2)),
            (120, (0, 2)),
            (130, (0, 3)),
            (161, (0, 3)),
        ]
    )
    assert pages[41] == ["stuck_climbing"]
    assert pages[120] == []
    assert pages[161] == ["stuck_climbing"]


def test_a_steady_nonzero_stuck_count_is_not_a_climb():
    _, pages = _run([(0, (0, 4)), (120, (0, 4))])
    assert not any(pages.values())


def test_one_failed_poll_is_tolerated_and_the_second_pages():
    _, pages = _run([(0, None), (10, None), (20, None), (70, None)])
    assert pages == {0: [], 10: ["poll_failing"], 20: [], 70: ["poll_failing"]}


def test_a_failed_poll_does_not_reset_the_breach_timer():
    _, pages = _run([(0, (6, 0)), (20, None), (31, (6, 0))])
    assert pages[31] == ["pending_backlog"]


def test_a_page_that_was_not_recorded_is_due_again_next_run():
    state, pages, _ = monitor.evaluate({}, (6, 0), "", T0, 5)
    later = T0 + timedelta(minutes=31)
    state, pages, _ = monitor.evaluate(state, (6, 0), "", later, 5)
    assert [kind for kind, _ in pages] == ["pending_backlog"]
    # Delivery failed, so main() never called record_page.
    _, pages, _ = monitor.evaluate(state, (6, 0), "", later, 5)
    assert [kind for kind, _ in pages] == ["pending_backlog"]


# --- main() over real HTTP ---


class _Fake:
    def __init__(self):
        self.health_status = 200
        self.health_body = {
            "status": "ok",
            "pending_review_count": 123456,
            "stuck_contacted_count": 654321,
            "environment": "production",
        }
        self.sentry_status = 200
        self.tokens = []
        self.envelopes = []
        self.sentry_auth = []


@pytest.fixture
def fake():
    state = _Fake()

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, body=b"{}"):
            self.send_response(code)
            if code == 302:
                self.send_header("Location", "/redirected")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            state.tokens.append((self.path, self.headers.get("X-Health-Token")))
            body = state.health_body
            raw = body if isinstance(body, bytes) else json.dumps(body).encode()
            self._reply(state.health_status, raw)

        def do_POST(self):
            size = int(self.headers["Content-Length"])
            lines = self.rfile.read(size).decode().split("\n")
            state.envelopes.append(
                (self.path, json.loads(lines[1]), json.loads(lines[2]))
            )
            state.sentry_auth.append(self.headers["X-Sentry-Auth"])
            self._reply(state.sentry_status)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state.port = server.server_port
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def run_main(fake, tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "state" / "state.json"

    def _main(**overrides):
        env = {
            "HEALTH_URL": f"http://127.0.0.1:{fake.port}/health/",
            "STATE_FILE": str(state_file),
            "PRODUCTION_HEALTH_TOKEN": TOKEN,
            "PRODUCTION_SENTRY_DSN": f"http://{SENTRY_KEY}@127.0.0.1:{fake.port}/42",
            "PENDING_THRESHOLD": "",
            "DRILL": "false",
        }
        env.update(overrides)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        code = monitor.main()
        return code, capsys.readouterr().out

    _main.state_file = state_file
    return _main


def test_a_clean_read_is_green_and_sends_the_token_to_the_leads_path(fake, run_main):
    code, out = run_main()
    assert code == 0, out
    assert fake.tokens == [("/health/leads", TOKEN)]
    assert fake.envelopes == []


@pytest.mark.parametrize(
    "missing, named",
    [
        ("PRODUCTION_HEALTH_TOKEN", "PRODUCTION_HEALTH_TOKEN"),
        ("PRODUCTION_SENTRY_DSN", "PRODUCTION_SENTRY_DSN"),
    ],
)
def test_a_missing_secret_is_red_and_names_the_secret(run_main, missing, named):
    code, out = run_main(**{missing: ""})
    assert code == 1
    assert "::error::" in out and named in out


def test_a_missing_token_never_reaches_the_endpoint(fake, run_main):
    run_main(PRODUCTION_HEALTH_TOKEN="")
    assert fake.tokens == []


@pytest.mark.parametrize(
    "status, body",
    [
        pytest.param(403, {"status": "forbidden"}, id="wrong-token"),
        pytest.param(503, {"status": "error"}, id="database-down"),
        pytest.param(302, {}, id="redirect-not-followed"),
        pytest.param(200, b"<html>not json</html>", id="not-json"),
        pytest.param(200, {"status": "forbidden"}, id="200-but-not-ok"),
        pytest.param(
            200,
            {
                "status": "ok",
                "pending_review_count": 0,
                "stuck_contacted_count": 0,
                "environment": "staging",
            },
            id="pointed-at-staging",
        ),
        pytest.param(
            200,
            {"status": "ok", "pending_review_count": 0, "environment": "production"},
            id="count-missing",
        ),
        pytest.param(
            200,
            {
                "status": "ok",
                "pending_review_count": True,
                "stuck_contacted_count": 0,
                "environment": "production",
            },
            id="count-not-an-integer",
        ),
    ],
)
def test_an_unusable_response_is_red_and_the_second_one_pages(
    fake, run_main, status, body
):
    fake.health_status, fake.health_body = status, body

    code, _ = run_main()
    assert code == 1
    assert fake.envelopes == []

    code, _ = run_main()
    assert code == 1
    assert [e[2]["tags"]["monitor_kind"] for e in fake.envelopes] == ["poll_failing"]
    # The token never followed the redirect.
    assert all(path == "/health/leads" for path, _ in fake.tokens)


def _backdate_pending(run_main):
    """One real run to create state, with the breach timer moved 31 minutes back."""
    run_main()
    state = json.loads(run_main.state_file.read_text(encoding="utf-8"))
    since = datetime.fromisoformat(state["pending_since"]) - timedelta(minutes=31)
    state["pending_since"] = since.isoformat()
    run_main.state_file.write_text(json.dumps(state), encoding="utf-8")


def test_a_page_is_a_fatal_production_event_on_its_own_sentry_issue(fake, run_main):
    fake.health_body["stuck_contacted_count"] = 0
    _backdate_pending(run_main)

    code, _ = run_main()
    assert code == 1
    code, _ = run_main()  # still breached, but throttled: red with no second page
    assert code == 1

    assert len(fake.envelopes) == 1
    path, item_header, event = fake.envelopes[0]
    assert path == "/api/42/envelope/"
    assert item_header == {"type": "event"}
    assert event["level"] == "fatal"
    assert event["environment"] == "production"
    assert "123456" in event["message"]["formatted"]
    # A timestamped fingerprint makes each page a new issue, which is the only
    # thing the *issue created* alert rule emails on.
    assert event["timestamp"] in event["fingerprint"]
    assert f"sentry_key={SENTRY_KEY}" in fake.sentry_auth[0]


def test_sentry_refusing_a_page_is_red_and_retried_next_run(fake, run_main):
    _backdate_pending(run_main)
    fake.sentry_status = 403

    code, out = run_main()
    assert code == 1
    assert "Sentry refused" in out

    fake.sentry_status = 200
    run_main()
    assert len(fake.envelopes) == 2


def test_the_threshold_variable_can_force_a_breach_for_the_end_to_end_proof(
    fake, run_main
):
    fake.health_body["pending_review_count"] = 0
    code, out = run_main(PENDING_THRESHOLD="-1")
    assert "::warning::" in out
    state = json.loads(run_main.state_file.read_text(encoding="utf-8"))
    assert "pending_since" in state
    assert code == 0  # the 30 minutes have only started


def test_a_mistyped_threshold_variable_is_red(run_main):
    code, out = run_main(PENDING_THRESHOLD="five")
    assert code == 1
    assert "not an integer" in out


def test_a_drill_sends_one_marked_test_page_and_stays_green(fake, run_main):
    code, _ = run_main(DRILL="true")
    assert code == 0
    assert [e[2]["tags"]["monitor_kind"] for e in fake.envelopes] == ["drill"]
    assert "TEST" in fake.envelopes[0][2]["message"]["formatted"]


def test_a_drill_sentry_refuses_is_red(fake, run_main):
    fake.sentry_status = 401
    code, _ = run_main(DRILL="true")
    assert code == 1


def test_corrupt_state_restarts_the_timers_with_a_warning(fake, run_main):
    run_main.state_file.parent.mkdir(parents=True)
    run_main.state_file.write_text("[not state", encoding="utf-8")
    code, out = run_main()
    assert code == 0
    assert "::warning::" in out


def test_public_logs_never_carry_counts_or_secrets(fake, run_main):
    _backdate_pending(run_main)
    fake.sentry_status = 500
    _, out = run_main(DRILL="true")
    fake.health_status = 503
    _, out_failing = run_main()
    for text in (out, out_failing):
        for private in ("123456", "654321", TOKEN, SENTRY_KEY):
            assert private not in text


# --- The workflow ---


@pytest.fixture(scope="module")
def workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def _steps(workflow):
    return workflow["jobs"]["monitor"]["steps"]


def _step(workflow, name):
    return next(s for s in _steps(workflow) if s.get("name") == name)


def test_it_runs_on_a_schedule_and_the_drill_is_off_by_default(workflow):
    triggers = workflow[True]  # PyYAML reads the bare `on` key as True
    assert triggers["schedule"] == [{"cron": "3,13,23,33,43,53 * * * *"}]
    assert triggers["workflow_dispatch"]["inputs"]["drill"]["default"] is False


def test_secrets_reach_the_script_through_env_with_the_documented_names(workflow):
    check = _step(workflow, "Check the lead pipeline")
    assert check["run"] == "python3 scripts/check_health_leads.py"
    for name in ("PRODUCTION_HEALTH_TOKEN", "PRODUCTION_SENTRY_DSN"):
        assert check["env"][name] == "${{ secrets." + name + " }}"


def test_a_failing_check_can_turn_the_run_red(workflow):
    for step in _steps(workflow):
        assert "continue-on-error" not in step


def test_state_is_saved_after_a_red_run_and_keys_share_the_restore_prefix(workflow):
    restore = _step(workflow, "Restore the last reading")
    save = _step(workflow, "Save this reading")
    assert "always()" in save["if"]
    assert restore["with"]["path"] == save["with"]["path"]
    assert restore["with"]["key"] == save["with"]["key"]
    assert save["with"]["key"].startswith(restore["with"]["restore-keys"])
    # The script's state file has to live inside the cached directory.
    state_file = workflow["jobs"]["monitor"]["env"]["STATE_FILE"]
    assert state_file.startswith(save["with"]["path"] + "/")


def test_the_token_is_read_only(workflow):
    assert workflow["permissions"] == {"contents": "read"}
