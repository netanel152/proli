"""Synthetic monitor for production's GET /health/leads.

Run by .github/workflows/health-leads-monitor.yml every 10 minutes. Pages the
operator through Sentry (level `fatal`, the tier the alert rule emails, see
docs/SENTRY_SETUP.md) when:

- `pending_review_count` has stayed above the threshold for more than 30 min,
- `stuck_contacted_count` has stayed above its last known level for more than
  30 min. A rise that clears inside the window is the Healer doing its job,
- the endpoint could not be read on consecutive runs, or a token is missing.

Stdlib only: `app.core.logger.page_critical` needs the whole app's settings and
its SDK transport swallows a rejected event, while this must see Sentry's answer
so that an undeliverable page fails the run.

The repo is public, so Actions logs are too: the counts the endpoint keeps behind
its token go to Sentry only. Print verdicts here, never numbers or bodies.
"""

import http.client
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_PENDING_THRESHOLD = 5
SUSTAINED = timedelta(minutes=30)
REPAGE_EVERY = timedelta(minutes=60)  # SENTRY_SETUP.md's outage re-page cadence
POLL_FAILURES_BEFORE_PAGE = 2  # one miss is a deploy restarting, not an incident
HTTP_TIMEOUT_SECONDS = 20
EXPECTED_ENVIRONMENT = "production"
USER_AGENT = "proli-health-leads-monitor"

PUBLIC_VERDICTS = {
    "pending_backlog": "The admin-review backlog stayed over its threshold for more than 30 minutes.",
    "stuck_climbing": "The stuck-lead count climbed and stayed up for more than 30 minutes.",
    "poll_failing": "The lead-health endpoint could not be read on consecutive runs.",
}


class PollFailed(Exception):
    """The endpoint gave no usable reading. The message is safe for public logs."""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    # urllib would replay X-Health-Token to wherever a redirect points.
    def redirect_request(self, *args, **kwargs):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def fetch_counts(url: str, token: str) -> tuple[int, int]:
    request = urllib.request.Request(
        url, headers={"X-Health-Token": token, "User-Agent": USER_AGENT}
    )
    try:
        with _opener.open(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
            body = json.load(response)
    except urllib.error.HTTPError as e:
        raise PollFailed(f"HTTP {e.code}") from None
    # URLError and timeouts are OSError; a truncated body is HTTPException.
    except (OSError, ValueError, http.client.HTTPException):
        raise PollFailed("no readable response") from None

    if not isinstance(body, dict) or body.get("status") != "ok":
        raise PollFailed("response status is not ok")
    # A mispointed URL must not pass as production being healthy.
    if body.get("environment") != EXPECTED_ENVIRONMENT:
        raise PollFailed(f"response is not from {EXPECTED_ENVIRONMENT}")
    counts = body.get("pending_review_count"), body.get("stuck_contacted_count")
    if not all(type(c) is int and c >= 0 for c in counts):
        raise PollFailed("response counts are missing or malformed")
    return counts


def _elapsed(state: dict, key: str, now: datetime) -> timedelta:
    return now - datetime.fromisoformat(state.setdefault(key, now.isoformat()))


def _page_is_due(state: dict, kind: str, now: datetime) -> bool:
    last = state.get("last_paged", {}).get(kind)
    return last is None or now - datetime.fromisoformat(last) >= REPAGE_EVERY


def _clear_episode(state: dict, kind: str) -> None:
    state.get("last_paged", {}).pop(
        kind, None
    )  # the next episode pages as soon as it is due


def evaluate(
    state: dict,
    counts: tuple[int, int] | None,
    reason: str,
    now: datetime,
    pending_threshold: int,
) -> tuple[dict, list[tuple[str, str]], set[str]]:
    """Return (new state, pages to send now, conditions currently alerting)."""
    state = dict(state)
    pages: list[tuple[str, str]] = []
    active: set[str] = set()

    if counts is None:
        # The breach timers stay as they are: a blind run proves nothing either way.
        state["poll_failures"] = state.get("poll_failures", 0) + 1
        if state["poll_failures"] >= POLL_FAILURES_BEFORE_PAGE:
            active.add("poll_failing")
            if _page_is_due(state, "poll_failing", now):
                pages.append(
                    (
                        "poll_failing",
                        f"Production /health/leads unreadable for "
                        f"{state['poll_failures']} runs in a row: {reason}. "
                        "The lead pipeline is unmonitored until this clears.",
                    )
                )
        return state, pages, active

    state["poll_failures"] = 0
    _clear_episode(state, "poll_failing")
    pending, stuck = counts

    if pending > pending_threshold:
        if _elapsed(state, "pending_since", now) > SUSTAINED:
            active.add("pending_backlog")
            if _page_is_due(state, "pending_backlog", now):
                pages.append(
                    (
                        "pending_backlog",
                        f"pending_review_count={pending} has been above "
                        f"{pending_threshold} for more than 30 min: the admin queue "
                        "is not being worked, or the Healer is looping.",
                    )
                )
    else:
        state.pop("pending_since", None)
        _clear_episode(state, "pending_backlog")

    baseline = state.get("stuck_baseline")
    if baseline is None or stuck <= baseline:
        state["stuck_baseline"] = stuck
        state.pop("stuck_rise_since", None)
    elif _elapsed(state, "stuck_rise_since", now) > SUSTAINED:
        active.add("stuck_climbing")
        pages.append(
            (
                "stuck_climbing",
                f"stuck_contacted_count rose from {baseline} to {stuck} and has not "
                "come back for more than 30 min: the Healer is off or silently failing.",
            )
        )
    return state, pages, active


def record_page(state: dict, kind: str, now: datetime, counts) -> None:
    if kind == "stuck_climbing":
        # One page per climb: only a further rise pages again. The SOS Reporter
        # already re-pages individual stuck leads.
        state["stuck_baseline"] = counts[1]
        state.pop("stuck_rise_since", None)
    else:
        state.setdefault("last_paged", {})[kind] = now.isoformat()


def send_page(dsn: str, kind: str, message: str, now: datetime) -> None:
    """POST one fatal event to Sentry. Raises unless Sentry accepted it."""
    parts = urlsplit(dsn)
    project = parts.path.rsplit("/", 1)[-1]
    if not (parts.scheme and parts.hostname and parts.username and project):
        raise ValueError("the DSN is malformed")
    netloc = parts.hostname + (f":{parts.port}" if parts.port else "")

    event_id = uuid.uuid4().hex
    sent_at = now.isoformat()
    event = {
        "event_id": event_id,
        "timestamp": sent_at,
        "platform": "python",
        "level": "fatal",
        "logger": "proli.health_leads_monitor",
        "environment": EXPECTED_ENVIRONMENT,
        "message": {"formatted": message},
        "tags": {"service": "proli-health-monitor", "monitor_kind": kind},
        # The alert rule fires on *issue created*, and a regrouped event only
        # reopens an old issue. So every page has to be a new issue to send email.
        "fingerprint": ["proli-health-leads-monitor", kind, sent_at],
    }
    envelope = "\n".join(
        json.dumps(line)
        for line in (
            {"event_id": event_id, "sent_at": sent_at},
            {"type": "event"},
            event,
        )
    )
    request = urllib.request.Request(
        f"{parts.scheme}://{netloc}/api/{project}/envelope/",
        data=envelope.encode("utf-8"),
        headers={
            "Content-Type": "application/x-sentry-envelope",
            "User-Agent": USER_AGENT,
            "X-Sentry-Auth": f"Sentry sentry_version=7, sentry_client={USER_AGENT}/1.0, "
            f"sentry_key={parts.username}",
        },
    )
    with _opener.open(request, timeout=HTTP_TIMEOUT_SECONDS):
        pass  # any non-2xx raises HTTPError


def _try_page(dsn: str, kind: str, message: str, now: datetime) -> bool:
    try:
        send_page(dsn, kind, message, now)
    except urllib.error.HTTPError as e:
        print(f"::error::Sentry refused the {kind} page (HTTP {e.code}).")
    except (OSError, ValueError, http.client.HTTPException):
        # Fixed text: these exceptions can quote the DSN.
        print(f"::error::The {kind} page could not be delivered to Sentry.")
    else:
        print(f"::notice::Sentry accepted the {kind} page.")
        return True
    return False


def _load_state(path: Path) -> dict | None:
    """None means no memory of earlier runs, so no sustained rule could have fired."""
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        for key in ("pending_since", "stuck_rise_since"):
            if key in state:
                datetime.fromisoformat(state[key])
        for stamp in state.get("last_paged", {}).values():
            datetime.fromisoformat(stamp)
        return state
    except FileNotFoundError:
        print("::error::No earlier reading: the 30-minute timers start only now.")
    except (OSError, ValueError, TypeError, AttributeError):
        print("::error::Monitor state was unreadable: the 30-minute timers restart.")
    return None


def main() -> int:
    token = os.environ.get("PRODUCTION_HEALTH_TOKEN", "")
    dsn = os.environ.get("PRODUCTION_SENTRY_DSN", "")
    url = os.environ["HEALTH_URL"].rstrip("/") + "/leads"
    state_file = Path(os.environ["STATE_FILE"])
    now = datetime.now(timezone.utc)

    # A typo here must not quietly become "no threshold".
    raw_threshold = os.environ.get("PENDING_THRESHOLD", "").strip()
    try:
        threshold = int(raw_threshold) if raw_threshold else DEFAULT_PENDING_THRESHOLD
    except ValueError:
        print("::error::The HEALTH_LEADS_PENDING_THRESHOLD variable is not an integer.")
        return 1
    if threshold != DEFAULT_PENDING_THRESHOLD:
        print("::warning::HEALTH_LEADS_PENDING_THRESHOLD is overriding the default.")

    healthy = True
    if not dsn:
        healthy = False
        print(
            "::error::PRODUCTION_SENTRY_DSN secret is not set, "
            "so no alert can reach the operator."
        )

    if os.environ.get("DRILL") == "true" and dsn:
        healthy &= _try_page(
            dsn,
            "drill",
            f"🧪 TEST page from the lead-health monitor, sent {now.isoformat()}. "
            "If this reached your inbox, the alert route works. Safe to resolve.",
            now,
        )

    counts, reason = None, ""
    if token:
        try:
            counts = fetch_counts(url, token)
        except PollFailed as e:
            reason = str(e)
    else:
        reason = "PRODUCTION_HEALTH_TOKEN secret is not set"

    if counts is None:
        healthy = False
        print(f"::error::Nothing was checked: {reason}.")

    # Red, not green: if the cache keeps failing, the timers never reach 30 minutes
    # and every run would otherwise report a healthy pipeline it cannot judge.
    earlier = _load_state(state_file)
    if earlier is None:
        healthy = False

    state, pages, active = evaluate(earlier or {}, counts, reason, now, threshold)
    for kind, message in pages:
        if dsn and _try_page(dsn, kind, message, now):
            record_page(state, kind, now, counts)
        else:
            healthy = False  # stays unrecorded, so the next run tries again

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(json.dumps(state), encoding="utf-8")

    for kind in sorted(active):
        healthy = False
        print(f"::error::{PUBLIC_VERDICTS[kind]}")
    if healthy:
        print("Lead pipeline read successfully; no alert condition is active.")
    return 0 if healthy else 1


if __name__ == "__main__":
    sys.exit(main())
