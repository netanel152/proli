"""A rerun queued during an offer's send must not lose what the send did.

An auto-refresh tick or another click arriving during the send queues a rerun on
the real ``ScriptRequests``, and the next yield point raises ``RerunException``.
On the pinned Streamlit 1.31.1 every ``st.session_state`` access is a yield point
(``SafeSessionState.__getitem__``/``__setitem__`` call ``_yield_callback`` first),
not only page writes, so storing an outcome in session state after the send can
raise before it writes. ``ScriptControlException`` is a ``BaseException``, so
``except Exception`` does not stop it either.

This covers what the view does once a rerun is queued, which is what
``runner.fastReruns = false`` guarantees in a real session; AppTest does not go
through ``AppSession``, so ``test_admin_fast_reruns.py`` covers that part.

These run the real view through the real ``ScriptRunner`` and ``SafeSessionState``.
Only the offer itself is stubbed, and that stub does what a tick does: it queues a
rerun while the send is in flight. The Mongo collections are mongomock.
"""

from datetime import datetime, timezone

import mongomock
import pandas as pd
import pytest
import streamlit as st
from streamlit.runtime.scriptrunner import RerunData, get_script_run_ctx
from streamlit.testing.v1 import AppTest

import admin_panel.core.auth as auth
import admin_panel.views.home as home
from admin_panel.core.config import TRANS
from admin_panel.core.labels import lead_status_label

EN = TRANS["EN"]
PENDING = "pending_admin_review"
ASSIGNED_SENTENCE = EN["assign_ok"].replace("{pro}", "Dana")


def _app():
    import streamlit as st

    import admin_panel.views.home as home
    from admin_panel.core.config import TRANS

    st.session_state["admin_role"] = "owner"
    st.session_state["admin_username"] = "operator"
    home.view_leads_dashboard(TRANS["EN"])


@pytest.fixture
def panel(monkeypatch):
    """The view on mongomock, with an offer that queues a rerun mid-send."""
    db = mongomock.MongoClient()["rerun_test"]
    monkeypatch.setattr(home, "users_collection", db["users"])
    monkeypatch.setattr(home, "leads_collection", db["leads"])
    monkeypatch.setattr(home, "messages_collection", db["messages"])
    monkeypatch.setattr(auth, "_audit_col", db["audit"])
    # AppTest keeps a button's trigger across st.rerun and would loop; the code
    # under test never needs st.rerun to run for these assertions.
    monkeypatch.setattr(st, "rerun", st.stop)

    db["users"].insert_one(
        {
            "role": "professional",
            "business_name": "Dana",
            "is_active": True,
            "pending_approval": False,
            "is_verified": True,
        }
    )

    sends = []
    send_runs = []
    runs = [0]
    real_view = home.view_leads_dashboard

    def counted_view(T):
        runs[0] += 1
        real_view(T)

    monkeypatch.setattr(home, "view_leads_dashboard", counted_view)

    def offer(lead_id, pro_id, *, find_pro):
        assert find_pro(pro_id)
        sends.append(lead_id)
        send_runs.append(runs[0])
        # What assign_lead_to_pro does to the lead, so a replayed click is a no-op.
        db["leads"].update_one(
            {"_id": pending_lead_ids[lead_id]},
            {"$set": {"status": "new", "pro_id": pro_id}},
        )
        get_script_run_ctx().script_requests.request_rerun(RerunData())
        return "sent", "Dana"

    monkeypatch.setattr(home, "offer_pending_lead", offer)
    pending_lead_ids = {}

    def add_lead(name):
        lead_id = (
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
        pending_lead_ids[str(lead_id)] = lead_id
        return lead_id

    st.cache_data.clear()
    yield db, sends, add_lead, send_runs
    st.cache_data.clear()


def _normalise_selectboxes(at, status=PENDING):
    """format_func widgets: AppTest holds the raw value but lists the labels."""
    label = lead_status_label(EN, status)
    for box in at.selectbox:
        try:
            box.index
        except ValueError:
            box.set_value(label if label in box.options else box.options[0])


def _shown(at):
    """Every alert and toast the final run rendered."""
    return [
        e.value
        for kind in ("success", "warning", "error", "toast")
        for e in at.get(kind)
    ]


def _run(at):
    at.run()
    assert not at.exception, at.exception


def _audited(db, action):
    return [entry["details"] for entry in db["audit"].find({"action": action})]


def test_strip_keeps_the_outcome_and_the_audit(panel):
    db, sends, add_lead, _ = panel
    lead_id = add_lead("Yossi")
    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)

    _normalise_selectboxes(at)
    at.selectbox(key=f"assign_pro_{lead_id}").set_value("Dana")
    at.button(key=f"assign_go_{lead_id}").click()
    _run(at)

    assert sends == [str(lead_id)]
    assert [e["outcome"] for e in _audited(db, "assign_lead")] == ["sent"]
    assert any(ASSIGNED_SENTENCE in text for text in _shown(at)), _shown(at)


def test_edit_form_keeps_the_outcome_and_the_audit(panel):
    db, sends, add_lead, _ = panel
    lead_id = add_lead("Yossi")
    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)

    _normalise_selectboxes(at)
    box = [s for s in at.selectbox if s.label == EN["professional_label"]][0]
    box.set_value("Dana")
    [b for b in at.button if b.label == EN["save_changes_btn"]][0].click()
    _run(at)

    assert sends == [str(lead_id)]
    assert [e["outcome"] for e in _audited(db, "assign_lead")] == ["sent"]
    assert any(ASSIGNED_SENTENCE in text for text in _shown(at)), _shown(at)


def test_table_finishes_the_batch_and_keeps_every_outcome(panel, monkeypatch):
    """The first offer queues the rerun; the second row must still be processed."""
    db, sends, add_lead, send_runs = panel
    first, second = add_lead("Yossi"), add_lead("Rina")

    def editor(df, key=None, **_kwargs):
        # AppTest cannot type into a data_editor. The real widget hands the view
        # this frame and this state, so the Save handler below it is the real one.
        rows = {
            int(i): {"professional": "Dana"}
            for i in df.index[df["id"].isin([str(first), str(second)])]
        }
        st.session_state[key] = {"edited_rows": rows}
        edited = df.copy()
        edited["professional"] = "Dana"
        return pd.DataFrame(edited)

    monkeypatch.setattr(st, "data_editor", editor)

    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)
    _normalise_selectboxes(at)
    at.button(key="save_dashboard_changes").click()
    _run(at)

    assert sorted(sends) == sorted([str(first), str(second)])
    # In one script run: the queued rerun must not abandon the second row. AppTest
    # replaying the click would otherwise hide that.
    assert len(set(send_runs)) == 1, send_runs
    assert len(_audited(db, "assign_lead")) == 2
    shown = _shown(at)
    assert any("Yossi" in t and ASSIGNED_SENTENCE in t for t in shown), shown
    assert any("Rina" in t and ASSIGNED_SENTENCE in t for t in shown), shown


def _toasts(at):
    return [e.value for e in at.get("toast")]


def test_the_outcome_is_toasted_because_the_inline_alert_is_off_screen(panel):
    _, _, add_lead, _ = panel
    lead_id = add_lead("Yossi")
    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)

    _normalise_selectboxes(at)
    at.selectbox(key=f"assign_pro_{lead_id}").set_value("Dana")
    at.button(key=f"assign_go_{lead_id}").click()
    _run(at)

    assert any(ASSIGNED_SENTENCE in text for text in _toasts(at)), _toasts(at)


def test_a_held_pro_warning_is_toasted_on_the_next_run(panel):
    db, sends, add_lead, _ = panel
    pro = db["users"].find_one()["_id"]
    lead_id = add_lead("Yossi")
    db["leads"].update_one({"_id": lead_id}, {"$set": {"pro_id": pro}})
    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)

    _normalise_selectboxes(at)
    [b for b in at.button if b.label == EN["save_changes_btn"]][0].click()
    _run(at)
    _normalise_selectboxes(at, status="pending_admin_review")
    _run(at)

    assert sends == []
    assert any(
        "still on the record" in text and "Dana" in text for text in _toasts(at)
    ), _toasts(at)


def test_a_no_change_save_is_toasted(panel):
    _, _, add_lead, _ = panel
    db = panel[0]
    lead_id = add_lead("Yossi")
    db["leads"].update_one({"_id": lead_id}, {"$set": {"status": "new"}})
    at = AppTest.from_function(_app, default_timeout=60)
    _run(at)

    _normalise_selectboxes(at, status="new")
    [b for b in at.button if b.label == EN["save_changes_btn"]][0].click()
    _run(at)
    _normalise_selectboxes(at, status="new")
    _run(at)

    assert EN["no_changes"] in _toasts(at), _toasts(at)


def test_a_large_report_gets_one_summary_toast_and_every_inline_alert():
    def app():
        import streamlit as st

        import admin_panel.views.home as home
        from admin_panel.core.config import TRANS
        from admin_panel.core.lead_queries import new_save_report

        st.session_state["leads_flash"] = [
            {
                **new_save_report(),
                "assignments": [
                    {"client": f"C{i}", "outcome": "sent", "pro_name": "Dana"}
                    for i in range(6)
                ],
            }
        ]
        home._render_leads_flash(TRANS["EN"])

    at = AppTest.from_function(app, default_timeout=30)
    _run(at)

    assert len(_toasts(at)) == 1
    assert "6" in _toasts(at)[0]
    assert len(at.success) == 6


def test_held_pro_wording_does_not_claim_an_escalation_after_a_manual_move():
    report = {
        "held_pro": [
            {"client": "A", "pro": "Dana"},
            {"client": "B", "pro": "Dana", "moved_to_waiting": True},
        ]
    }

    first, second = home._report_messages(EN, report)

    assert "escalation" in first[0]
    assert "escalation" not in second[0]
