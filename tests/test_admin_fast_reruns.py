"""``runner.fastReruns`` decides whether the operator sees an offer's outcome.

A browser rerun goes through ``AppSession.request_rerun``. With fast reruns on, that
stops the running script and starts a second run at once on the same session state,
while the first is still in its WhatsApp send. The second run renders the save
report before there is anything in it and drops it; the first fills a report nobody
holds and then stops at the spinner's exit. With them off, the request is queued for
the first run's next yield point, which comes after the outcome is stored.

So these drive the real ``AppSession``, ``ScriptRunner`` and view. Only the offer is
stubbed, to block until the rerun has been requested from the event loop, and the
widgets are forced, because no browser is sending their state.
"""

import asyncio
import os
import subprocess
import sys
import threading
import tomllib
import types
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import mongomock
import pandas as pd
import pytest
import streamlit as st
from bson import ObjectId
from streamlit import config
from streamlit.delta_generator import DeltaGenerator
from streamlit.runtime import Runtime
from streamlit.runtime.app_session import AppSession
from streamlit.runtime.caching.storage.dummy_cache_storage import (
    MemoryCacheStorageManager,
)
from streamlit.runtime.media_file_manager import MediaFileManager
from streamlit.runtime.memory_media_file_storage import MemoryMediaFileStorage
from streamlit.runtime.script_data import ScriptData
from streamlit.runtime.scriptrunner.script_cache import ScriptCache

import admin_panel.core.auth as auth
import admin_panel.core.runtime_config as runtime_config
import admin_panel.views.home as home
from admin_panel.core.config import TRANS

ROOT = Path(__file__).parents[1]
EN = TRANS["EN"]
PENDING = "pending_admin_review"
SENTENCE = EN["assign_ok"].replace("{pro}", "Dana")
HARNESS = "_fast_reruns_harness"


def _force_widgets(monkeypatch, forced):
    """``forced(name, label, key, result)`` may replace what a widget returned."""

    def wrap(name):
        real = getattr(DeltaGenerator, name)

        def widget(self, label, *args, **kwargs):
            result = real(self, label, *args, **kwargs)
            return forced(name, label, kwargs.get("key"), result)

        monkeypatch.setattr(DeltaGenerator, name, widget)
        monkeypatch.setattr(st, name, lambda *a, **k: widget(st._main, *a, **k))

    for name in ("button", "form_submit_button", "selectbox"):
        wrap(name)


def _drive(surface, fast, monkeypatch, tmp_path):
    """Run one save with a rerun requested mid-send. Returns what the session showed."""
    db = mongomock.MongoClient()["fast_reruns"]
    monkeypatch.setattr(home, "users_collection", db["users"])
    monkeypatch.setattr(home, "leads_collection", db["leads"])
    monkeypatch.setattr(home, "messages_collection", db["messages"])
    monkeypatch.setattr(auth, "_audit_col", db["audit"])
    monkeypatch.setitem(sys.modules, "__main__", sys.modules["__main__"])

    runtime = MagicMock()
    runtime.media_file_mgr = MediaFileManager(MemoryMediaFileStorage("/mock/media"))
    runtime.cache_storage_manager = MemoryCacheStorageManager()
    monkeypatch.setattr(Runtime, "_instance", runtime)

    previous = config.get_option("runner.fastReruns")
    config.set_option("runner.fastReruns", fast)

    pro = db["users"].insert_one(
        {
            "role": "professional",
            "business_name": "Dana",
            "is_active": True,
            "pending_approval": False,
            "is_verified": True,
        }
    )

    def add_lead(name):
        return (
            db["leads"]
            .insert_one(
                {
                    "chat_id": "972500000000@c.us",
                    "display_name": name,
                    "status": PENDING,
                    "pro_id": None,
                    "details": "d",
                    "issue_type": "d",
                    "created_at": datetime.now(timezone.utc),
                }
            )
            .inserted_id
        )

    names = ["Yossi", "Rina"] if surface == "table" else ["Yossi"]
    lead_ids = [add_lead(name) for name in names]

    in_send = threading.Event()
    release = threading.Event()
    second_run_done = threading.Event()
    state = {"runs": 0, "armed": True, "blocked": False}

    def offer(lead_id, pro_id, *, find_pro):
        assert find_pro(pro_id)
        db["leads"].update_one(
            {"_id": ObjectId(lead_id)}, {"$set": {"status": "new", "pro_id": pro_id}}
        )
        if not state["blocked"]:
            state["blocked"] = True
            in_send.set()
            assert release.wait(30)
        return "sent", "Dana"

    monkeypatch.setattr(home, "offer_pending_lead", offer)

    def forced(name, label, key, result):
        if not state["armed"]:
            return result
        if surface == "strip":
            if name == "selectbox" and key == f"assign_pro_{lead_ids[0]}":
                return db["users"].find_one({"_id": pro.inserted_id})
            if name == "button" and key == f"assign_go_{lead_ids[0]}":
                return True
        if surface == "form":
            if name == "selectbox" and label == EN["professional_label"]:
                return "Dana"
            if name == "form_submit_button" and label == EN["save_changes_btn"]:
                return True
        if surface == "table" and name == "button" and key == "save_dashboard_changes":
            return True
        return result

    _force_widgets(monkeypatch, forced)

    def editor(df, key=None, **_kwargs):
        wanted = {str(lead) for lead in lead_ids}
        arm = surface == "table" and state["armed"]
        rows = (
            {int(i): {"professional": "Dana"} for i in df.index[df["id"].isin(wanted)]}
            if arm
            else {}
        )
        st.session_state[key] = {"edited_rows": rows}
        edited = df.copy()
        if rows:
            edited["professional"] = "Dana"
        return pd.DataFrame(edited)

    monkeypatch.setattr(st, "data_editor", editor)

    def main():
        state["runs"] += 1
        this_run = state["runs"]
        if this_run > 1:
            state["armed"] = False
        st.session_state["admin_role"] = "owner"
        st.session_state["admin_username"] = "operator"
        home.view_leads_dashboard(EN)
        if this_run == 2:
            second_run_done.set()

    harness = types.ModuleType(HARNESS)
    harness.main = main
    monkeypatch.setitem(sys.modules, HARNESS, harness)
    script = tmp_path / "app.py"
    script.write_text(
        f"import sys\nsys.modules[{HARNESS!r}].main()\n", encoding="utf-8"
    )

    st.cache_data.clear()

    async def run():
        session = AppSession(
            script_data=ScriptData(str(script), False),
            uploaded_file_manager=MagicMock(),
            script_cache=ScriptCache(),
            message_enqueued_callback=None,
            local_sources_watcher=MagicMock(),
            user_info={"email": "operator"},
        )
        shown = []

        def collect():
            for message in session.flush_browser_queue():
                if message.WhichOneof("type") != "delta":
                    continue
                element = message.delta.new_element
                kind = element.WhichOneof("type")
                if kind == "alert":
                    shown.append(("alert", element.alert.body))
                elif kind == "toast":
                    shown.append(("toast", element.toast.body))

        try:
            session.request_rerun(None)
            for _ in range(600):
                if in_send.is_set():
                    break
                await asyncio.sleep(0.05)
            assert in_send.is_set(), "the offer never ran"

            # From the event loop, as a browser's rerun request arrives.
            session.request_rerun(None)
            if fast:
                for _ in range(200):
                    if second_run_done.is_set():
                        break
                    await asyncio.sleep(0.05)
                assert second_run_done.is_set(), "no second run alongside the send"
            else:
                await asyncio.sleep(0.5)
                assert state["runs"] == 1, "a second run started during the send"
            release.set()

            # Exits as soon as every outcome is in; only a lost one waits it out.
            deadline = 40 if fast else 200
            for _ in range(deadline):
                collect()
                if sum(
                    SENTENCE in text for kind, text in shown if kind == "alert"
                ) >= len(names):
                    await asyncio.sleep(0.2)
                    collect()
                    break
                await asyncio.sleep(0.1)
        finally:
            release.set()
            session.shutdown()
        return shown

    try:
        shown = asyncio.run(run())
    finally:
        config.set_option("runner.fastReruns", previous)
        st.cache_data.clear()
    return shown, list(db["audit"].find({"action": "assign_lead"})), names


def _assert_outcome_reached_the_operator(surface, fast, monkeypatch, tmp_path):
    shown, audited, names = _drive(surface, fast, monkeypatch, tmp_path)

    # The offers and the audit go through either way.
    assert len(audited) == len(names)
    for kind in ("alert", "toast"):
        for name in names:
            found = [
                text
                for shown_kind, text in shown
                if shown_kind == kind and name in text and SENTENCE in text
            ]
            assert len(found) == 1, f"{kind} for {name}: {shown}"


@pytest.mark.parametrize("surface", ["strip", "form", "table"])
def test_with_fast_reruns_off_the_operator_sees_the_outcome(
    surface, monkeypatch, tmp_path
):
    _assert_outcome_reached_the_operator(surface, False, monkeypatch, tmp_path)


@pytest.mark.parametrize("surface", ["strip", "form", "table"])
def test_with_fast_reruns_on_the_outcome_is_lost(surface, monkeypatch, tmp_path):
    """Why the setting exists. If a Streamlit upgrade makes this fail, the setting
    may no longer be needed; re-check before relying on that."""
    with pytest.raises(AssertionError, match=r"(alert|toast) for ") as lost:
        _assert_outcome_reached_the_operator(surface, True, monkeypatch, tmp_path)
    print(f"{surface}: {lost.value}")


# --- the setting really applies ---


def test_config_file_turns_fast_reruns_off():
    with open(ROOT / ".streamlit" / "config.toml", "rb") as f:
        assert tomllib.load(f)["runner"]["fastReruns"] is False


def test_streamlit_itself_reads_it_from_the_repo_root():
    env = {k: v for k, v in os.environ.items() if k != "STREAMLIT_RUNNER_FAST_RERUNS"}
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from streamlit import config; print(config.get_option('runner.fastReruns'))",
        ],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip().splitlines()[-1] == "False"


def test_docker_build_keeps_the_config_file():
    ignored = (ROOT / ".dockerignore").read_text(encoding="utf-8")
    assert ".streamlit" not in ignored
    assert "*.toml" not in ignored
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "WORKDIR /app" in dockerfile
    assert "COPY . ." in dockerfile


@pytest.mark.parametrize(
    "path", ["docker-compose.yml", "scripts/start.sh", "docs/RAILWAY_SETUP.md"]
)
def test_start_commands_stay_at_the_repo_root_with_no_override(path):
    text = (ROOT / path).read_text(encoding="utf-8")
    commands = [line for line in text.splitlines() if "streamlit run" in line]
    assert commands, f"no start command found in {path}"
    for command in commands:
        assert "streamlit run admin_panel/main.py" in command
        assert "fastReruns" not in command


def test_startup_log_reports_the_effective_value(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(runtime_config, "logger", log)

    monkeypatch.setattr(runtime_config, "_reported", False)
    runtime_config.report_fast_reruns(get_option=lambda _: False)
    log.info.assert_called_once()
    assert "fastReruns=False" in log.info.call_args[0][0]
    log.error.assert_not_called()

    # Once per process: main.py runs again on every interaction.
    runtime_config.report_fast_reruns(get_option=lambda _: True)
    log.error.assert_not_called()

    monkeypatch.setattr(runtime_config, "_reported", False)
    runtime_config.report_fast_reruns(get_option=lambda _: True)
    log.error.assert_called_once()
