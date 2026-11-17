"""PRO-249 — the admin panel and the worker agree on ``scheduler_config``.

The panel inserted a partial document, so the worker's ``$setOnInsert``
defaults never applied, and it showed a missing ``sos_healer_active`` as on
while the worker treated it as off. Both now read every toggle through
``app.core.scheduler_config.scheduler_toggle`` and insert the same
``SCHEDULER_CONFIG_DEFAULTS``.

The panel's view body is Streamlit and never runs under pytest, so its half is
pinned on the source (``ast``), like ``test_admin_view_call_arity.py``.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from mongomock_motor import AsyncMongoMockClient

import app.scheduler as scheduler
from app.core.scheduler_config import (
    SCHEDULER_CONFIG_DEFAULTS,
    SCHEDULER_CONFIG_ID,
    SCHEDULER_TOGGLES,
    scheduler_toggle,
)

_ROOT = Path(__file__).resolve().parents[1]
_PANEL = _ROOT / "admin_panel" / "views" / "settings.py"
_WORKER = _ROOT / "app" / "scheduler.py"

# The panel's insert before PRO-249: a document exists, but with no toggles.
_OLD_PANEL_DOC = {
    "_id": SCHEDULER_CONFIG_ID,
    "run_time": "08:00",
    "is_active": True,
    "trigger_now": False,
}


# ---------------------------------------------------------------- the defaults


def test_cold_customer_jobs_default_off_everything_else_on():
    """PRO-73, written down once: the jobs that message a customer because a
    chat went silent stay dark until an operator enables them."""
    off = {k for k in SCHEDULER_TOGGLES if not SCHEDULER_CONFIG_DEFAULTS[k]}
    assert off == {"sos_healer_active", "lead_janitor_active", "sla_monitor_active"}
    assert set(SCHEDULER_TOGGLES) == {
        "is_active",
        "stale_monitor_active",
        "sos_reporter_active",
        "whatsapp_monitor_active",
        "sos_healer_active",
        "lead_janitor_active",
        "sla_monitor_active",
    }


@pytest.mark.parametrize("key", SCHEDULER_TOGGLES)
@pytest.mark.parametrize("config", [None, {}, _OLD_PANEL_DOC])
def test_missing_key_reads_as_its_default(config, key):
    if config is _OLD_PANEL_DOC and key == "is_active":
        pytest.skip("the old panel doc does carry is_active")
    assert scheduler_toggle(config, key) is SCHEDULER_CONFIG_DEFAULTS[key]


@pytest.mark.parametrize("key", SCHEDULER_TOGGLES)
@pytest.mark.parametrize("stored", [True, False])
def test_a_stored_value_wins_over_the_default(key, stored):
    assert scheduler_toggle({key: stored}, key) is stored


def test_unknown_toggle_is_an_error_not_a_silent_off():
    with pytest.raises(KeyError):
        scheduler_toggle({}, "sos_healr_active")


def test_defaults_cannot_be_mutated_by_a_caller():
    with pytest.raises(TypeError):
        SCHEDULER_CONFIG_DEFAULTS["sos_healer_active"] = True  # type: ignore[index]


# ------------------------------------------- the worker does what the panel shows

# (wrapper, toggle, the job it calls) — every toggle-gated worker job whose gate
# does not also depend on the clock. monitor_unfinished_jobs (stale monitor)
# also checks its own business-hours window inline; it is pinned on source below.
_GATED_JOBS = [
    ("run_sos_healer", "sos_healer_active", "check_and_reassign_stale_leads"),
    ("run_lead_janitor", "lead_janitor_active", "auto_reject_unassigned_leads"),
    ("run_sla_monitor", "sla_monitor_active", "check_sla_deflection"),
    ("run_sos_reporter", "sos_reporter_active", "send_periodic_admin_report"),
    (
        "run_whatsapp_state_monitor",
        "whatsapp_monitor_active",
        "check_whatsapp_instance_state",
    ),
    (
        "run_whatsapp_quality_monitor",
        "whatsapp_monitor_active",
        "check_whatsapp_quality",
    ),
]


@pytest.mark.asyncio
@pytest.mark.parametrize("wrapper, toggle, job", _GATED_JOBS)
@pytest.mark.parametrize("doc", [None, _OLD_PANEL_DOC], ids=["no-doc", "old-panel-doc"])
async def test_worker_runs_a_job_exactly_when_the_panel_shows_it_on(
    monkeypatch, fake_redis, wrapper, toggle, job, doc
):
    settings_col = AsyncMongoMockClient().proli_db.settings
    if doc is not None:
        await settings_col.insert_one(dict(doc))
    monkeypatch.setattr(scheduler, "settings_collection", settings_col)
    monkeypatch.setattr(scheduler, "within_business_hours", lambda *a, **k: True)
    called = AsyncMock()
    monkeypatch.setattr(scheduler, job, called)

    await getattr(scheduler, wrapper)()

    shown = scheduler_toggle(
        await settings_col.find_one({"_id": SCHEDULER_CONFIG_ID}), toggle
    )
    assert called.await_count == (1 if shown else 0)
    assert shown is SCHEDULER_CONFIG_DEFAULTS[toggle]


@pytest.mark.asyncio
async def test_worker_upsert_writes_the_shared_defaults(monkeypatch, fake_redis):
    settings_col = AsyncMongoMockClient().proli_db.settings
    monkeypatch.setattr(scheduler, "settings_collection", settings_col)
    monkeypatch.setattr(scheduler, "send_daily_reminders", AsyncMock())

    await scheduler.daily_reminders_job()

    doc = await settings_col.find_one({"_id": SCHEDULER_CONFIG_ID})
    for key in SCHEDULER_TOGGLES:
        assert doc[key] is SCHEDULER_CONFIG_DEFAULTS[key], key


# ------------------------------------------------------------ source pins


def _calls(path: Path):
    return [
        n
        for n in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(n, ast.Call)
    ]


@pytest.mark.parametrize("path", [_PANEL, _WORKER], ids=["panel", "worker"])
def test_no_toggle_is_read_with_its_own_default(path):
    """A ``config.get("<x>_active", <default>)`` is a second copy of a default."""
    offenders = []
    for call in _calls(path):
        f = call.func
        if (
            isinstance(f, ast.Attribute)
            and f.attr == "get"
            # `config.get`, not `T.get`: translation keys share the toggles' names.
            and isinstance(f.value, ast.Name)
            and f.value.id == "config"
            and call.args
            and isinstance(call.args[0], ast.Constant)
            and call.args[0].value in SCHEDULER_TOGGLES
        ):
            offenders.append(f"{path.name}:{call.lineno} {call.args[0].value}")
    assert not offenders


def test_panel_checkboxes_read_every_toggle_they_show_through_the_shared_helper():
    shown = set()
    for call in _calls(_PANEL):
        if isinstance(call.func, ast.Attribute) and call.func.attr == "checkbox":
            for kw in call.keywords:
                if kw.arg != "value":
                    continue
                v = kw.value
                assert (
                    isinstance(v, ast.Call)
                    and isinstance(v.func, ast.Name)
                    and v.func.id == "scheduler_toggle"
                ), f"settings.py:{call.lineno} checkbox not read through scheduler_toggle"
                shown.add(v.args[1].value)
    assert {
        "is_active",
        "stale_monitor_active",
        "sos_healer_active",
        "sos_reporter_active",
    } <= shown


@pytest.mark.parametrize("path", [_PANEL, _WORKER], ids=["panel", "worker"])
def test_both_processes_insert_the_shared_default_document(path):
    src = path.read_text(encoding="utf-8")
    assert '"$setOnInsert": dict(SCHEDULER_CONFIG_DEFAULTS)' in src
    assert (
        "insert_one(" not in src or path is not _PANEL or "scheduler_config" not in src
    )


def test_the_document_id_is_spelled_once():
    hits = []
    for top in ("app", "admin_panel", "scripts"):
        for path in (_ROOT / top).rglob("*.py"):
            if path.name == "scheduler_config.py":
                continue
            if '"scheduler_config"' in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(_ROOT)))
    assert hits == []
