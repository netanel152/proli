"""A panel send through a provider that cannot transmit must not report success.

The ``admin`` service picks its provider from its own variables. Staging admin has
no ``WHATSAPP_PROVIDER`` and production admin has ``WHATSAPP_DRY_RUN=true``. Both
resolve to ``DryRunProvider``, whose ``send_text`` returns a dict, and a dict used
to read as "sent" on every panel path.

Nothing here mocks the provider: ``muted`` builds the real one through the real
``build_provider`` from those two configurations.
"""

import ast
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import mongomock
import pytest
import streamlit as st
from bson import ObjectId
from pymongo import ReturnDocument
from streamlit.testing.v1 import AppTest

import admin_panel.core.auth as auth
import admin_panel.core.utils as panel_utils
import admin_panel.views.home as home
import admin_panel.views.professionals as professionals
from admin_panel.core import assignment, runtime_config
from admin_panel.core.config import TRANS
from admin_panel.core.labels import lead_status_label
from app.core import startup_checks
from app.core.config import settings
from app.core.messages import Messages
from app.providers.whatsapp import (
    DryRunProvider,
    get_whatsapp,
    reset_whatsapp,
    sync,
)
from app.providers.whatsapp.facade import WhatsAppFacade
from app.services import admin_flow, notification_service
from app.services.geocoding_service import ServiceAreaResolution

EN = TRANS["EN"]
HE = TRANS["HE"]
PENDING = "pending_admin_review"
PRO_PHONE = "972500000003"
CUSTOMER_CHAT = "972500000002@c.us"


@pytest.fixture(
    params=[("dryrun", False), ("cloud", True)],
    ids=["no_provider_like_staging_admin", "dry_run_like_production_admin"],
)
def muted(request, monkeypatch):
    """The real facade over the real provider each ``admin`` environment builds."""
    provider, dry_run = request.param
    monkeypatch.setattr(settings, "WHATSAPP_PROVIDER", provider)
    monkeypatch.setattr(settings, "WHATSAPP_DRY_RUN", dry_run)
    reset_whatsapp()
    facade = get_whatsapp()
    assert isinstance(facade.provider, DryRunProvider)
    yield facade
    reset_whatsapp()


@pytest.fixture
def offer_db(monkeypatch, mock_db):
    """One pending lead on the suite's async Mongo, for the real assignment core."""
    monkeypatch.setattr(admin_flow, "leads_collection", mock_db.leads)
    lead_id = ObjectId()
    asyncio.run(
        mock_db.leads.insert_one(
            {
                "_id": lead_id,
                "chat_id": CUSTOMER_CHAT,
                "status": PENDING,
                "issue_type": "leak",
            }
        )
    )
    return mock_db, str(lead_id)


def _pro(**overrides):
    return {
        "_id": ObjectId(),
        "business_name": "Dana",
        "phone_number": PRO_PHONE,
        **overrides,
    }


def test_send_text_sync_does_not_report_a_muted_send_as_sent(muted):
    assert sync.send_text_sync(CUSTOMER_CHAT, "hi") is False
    assert sync.can_transmit() is False


def test_completion_check_is_not_reported_or_stamped_as_sent(muted, monkeypatch):
    db = mongomock.MongoClient()["muted"]
    pro_id = db["users"].insert_one({"business_name": "Dana"}).inserted_id
    lead_id = (
        db["leads"]
        .insert_one({"chat_id": CUSTOMER_CHAT, "pro_id": pro_id, "status": "booked"})
        .inserted_id
    )
    monkeypatch.setattr(panel_utils, "leads_collection", db["leads"])
    monkeypatch.setattr(panel_utils, "users_collection", db["users"])

    assert not panel_utils.send_completion_check_sync(str(lead_id))

    # A stamp would restart the worker's cooldown for a check nobody received.
    lead = db["leads"].find_one({"_id": lead_id})
    assert "completion_check_sent_count" not in lead
    assert "completion_check_sent_at" not in lead


class _Transmitting(DryRunProvider):
    transmits = True


def test_a_completion_check_that_went_is_stamped(monkeypatch):
    """Without the stamp the monitor nudges a customer right after an operator's
    check, because the cooldown never restarted."""
    facade = WhatsAppFacade(_Transmitting())
    monkeypatch.setattr(
        facade, "_outbound_blocked_reason", AsyncMock(return_value=None)
    )
    monkeypatch.setattr(sync, "get_whatsapp", lambda: facade)
    db = mongomock.MongoClient()["muted"]
    pro_id = db["users"].insert_one({"business_name": "Dana"}).inserted_id
    lead_id = (
        db["leads"]
        .insert_one({"chat_id": CUSTOMER_CHAT, "pro_id": pro_id, "status": "booked"})
        .inserted_id
    )
    monkeypatch.setattr(panel_utils, "leads_collection", db["leads"])
    monkeypatch.setattr(panel_utils, "users_collection", db["users"])

    assert panel_utils.send_completion_check_sync(str(lead_id)) is True

    assert [e["kind"] for e in facade.provider.sent] == ["text"]
    lead = db["leads"].find_one({"_id": lead_id})
    assert lead["completion_check_sent_count"] == 1
    assert lead["completion_check_sent_at"]


def test_offer_is_not_reported_as_sent_and_the_assignment_stands(muted, offer_db):
    db, lead_id = offer_db
    pro = _pro()

    outcome, pro_name = assignment.offer_pending_lead(
        lead_id, pro["_id"], find_pro=lambda _: pro
    )

    assert outcome != assignment.ASSIGN_SENT
    assert outcome == assignment.ASSIGN_NOT_SENT_MUTED
    assert pro_name == "Dana"
    # The same lead state as any other offer that did not arrive.
    lead = asyncio.run(db.leads.find_one({"_id": ObjectId(lead_id)}))
    assert lead["status"] == "new"
    assert lead["pro_id"] == pro["_id"]


def test_a_lead_somebody_else_took_is_still_reported_as_taken(muted, offer_db):
    """``LEAD_ALREADY_TAKEN`` is truthy, and nothing was written or attempted."""
    db, lead_id = offer_db
    asyncio.run(
        db.leads.update_one({"_id": ObjectId(lead_id)}, {"$set": {"status": "booked"}})
    )

    outcome, _ = assignment.assign_pending_lead_sync(lead_id, _pro())

    assert outcome == assignment.ASSIGN_STALE


def test_a_pro_with_no_phone_keeps_the_plain_not_sent_outcome(muted, offer_db):
    """Unmuting would not help here, so the operator must not be told it would."""
    _, lead_id = offer_db

    outcome, _ = assignment.assign_pending_lead_sync(lead_id, _pro(phone_number=None))

    assert outcome == assignment.ASSIGN_NOT_SENT


@pytest.mark.parametrize("T", [EN, HE], ids=["EN", "HE"])
def test_the_muted_offer_says_so_and_differs_from_a_failed_send(T):
    message, level = home._assign_feedback(T, assignment.ASSIGN_NOT_SENT_MUTED, "Dana")
    failed, _ = home._assign_feedback(T, assignment.ASSIGN_NOT_SENT, "Dana")

    assert level == "warning"
    assert message == T["assign_muted"].format(pro="Dana")
    assert message != failed


def test_worker_callers_of_the_offer_are_unchanged(muted, offer_db):
    """A dry-run worker keeps behaving as it does today: only the panel's crossing
    reads ``transmits``, so the core still answers True and the flow carries on."""
    db, lead_id = offer_db
    lead = {"_id": lead_id, "chat_id": CUSTOMER_CHAT, "issue_type": "leak"}

    assert asyncio.run(notification_service.notify_pro_new_lead(lead, _pro(), muted))

    sent, _ = asyncio.run(admin_flow.assign_lead_to_pro(lead_id, _pro(), muted))
    assert sent is True
    assert [e["kind"] for e in muted.provider.sent] == ["text"] * 3


def test_the_whatsapp_wizard_still_answers_success_on_a_dry_run(muted, offer_db):
    _, lead_id = offer_db
    state_manager = SimpleNamespace(clear_state=_noop_async)

    asyncio.run(
        admin_flow._assign_lead_to_pro(
            "972500000009@c.us", lead_id, _pro(), state_manager, muted
        )
    )

    expected = Messages.Admin.ASSIGN_SUCCESS.format(pro_name="Dana")
    assert muted.provider.sent[-1]["text"] == expected


async def _noop_async(*_args, **_kwargs):
    return None


# ---------------------------------------------------------------------------
# Pro approval and rejection
# ---------------------------------------------------------------------------


def test_pro_notifications_report_whether_they_went(muted):
    assert professionals._notify_pro_approved(PRO_PHONE) is False
    assert professionals._notify_pro_rejected(PRO_PHONE) is False
    # None: never attempted, which is a different problem from a muted send.
    assert professionals._notify_pro_approved(None) is None
    assert professionals._notify_pro_rejected(None) is None


def test_approval_flash_says_the_pro_was_not_told(muted, monkeypatch):
    db = mongomock.MongoClient()["muted"]
    pro_id = db["users"].insert_one({"pending_approval": True}).inserted_id
    flashes = []
    monkeypatch.setattr(professionals, "users_collection", db["users"])
    monkeypatch.setattr(professionals, "create_initial_schedule", lambda _: None)
    monkeypatch.setattr(professionals, "log_audit", lambda *_: None)
    monkeypatch.setattr(professionals, "set_flash", lambda *args: flashes.append(args))
    monkeypatch.setattr(professionals.st, "rerun", lambda: None)

    professionals._approve_pending_pro(
        {"_id": pro_id, "business_name": "Dana", "phone_number": PRO_PHONE},
        EN,
        ServiceAreaResolution(),
    )

    ((key, message, level),) = flashes
    assert key == "pro_flash"
    assert level == "warning"
    assert EN["approved_ok"].format(name="Dana") in message
    assert EN["pro_not_told_muted"] in message


def test_approving_a_pro_with_no_phone_says_plainly_it_was_not_sent(muted, monkeypatch):
    """Unmuting would not reach this pro, so the sentence must not blame the mute."""
    db = mongomock.MongoClient()["muted"]
    pro_id = db["users"].insert_one({"pending_approval": True}).inserted_id
    flashes = []
    monkeypatch.setattr(professionals, "users_collection", db["users"])
    monkeypatch.setattr(professionals, "create_initial_schedule", lambda _: None)
    monkeypatch.setattr(professionals, "log_audit", lambda *_: None)
    monkeypatch.setattr(professionals, "set_flash", lambda *args: flashes.append(args))
    monkeypatch.setattr(professionals.st, "rerun", lambda: None)

    professionals._approve_pending_pro(
        {"_id": pro_id, "business_name": "Dana", "phone_number": None},
        EN,
        ServiceAreaResolution(),
    )

    ((_, message, level),) = flashes
    assert level == "warning"
    assert EN["pro_not_told"] in message
    assert EN["pro_not_told_muted"] not in message


def test_flash_tells_a_muted_environment_from_a_failed_send(monkeypatch):
    flashes = []
    monkeypatch.setattr(professionals, "set_flash", lambda *args: flashes.append(args))

    professionals._flash_pro_result(EN, "Done.", True)
    assert flashes.pop() == ("pro_flash", "Done.", "success")

    # Whichever level the caller asked for still stands on a delivered message.
    professionals._flash_pro_result(EN, "Done.", True, "warning")
    assert flashes.pop() == ("pro_flash", "Done.", "warning")

    monkeypatch.setattr(sync, "can_transmit", lambda: True)
    professionals._flash_pro_result(EN, "Done.", False)
    assert flashes.pop() == (
        "pro_flash",
        f"Done. {EN['pro_not_told']}",
        "warning",
    )

    monkeypatch.setattr(sync, "can_transmit", lambda: False)
    professionals._flash_pro_result(EN, "Done.", False)
    assert flashes.pop() == (
        "pro_flash",
        f"Done. {EN['pro_not_told_muted']}",
        "warning",
    )


def _reject_app():
    import streamlit as st

    import admin_panel.views.professionals as professionals
    from admin_panel.core.config import TRANS

    st.session_state["admin_role"] = "owner"
    st.session_state["admin_username"] = "operator"
    professionals.render_pending_approvals(TRANS["EN"])


@pytest.mark.parametrize(
    "phone, note, other",
    [
        (PRO_PHONE, "pro_not_told_muted", "pro_not_told"),
        (None, "pro_not_told", "pro_not_told_muted"),
    ],
    ids=["has_phone", "no_phone"],
)
def test_rejecting_from_the_real_view_does_not_claim_the_pro_was_told(
    muted, monkeypatch, phone, note, other
):
    db = mongomock.MongoClient()["muted"]
    pro_id = (
        db["users"]
        .insert_one(
            {
                "business_name": "Dana",
                "phone_number": phone,
                "pending_approval": True,
                "created_at": datetime.now(timezone.utc),
            }
        )
        .inserted_id
    )
    monkeypatch.setattr(professionals, "users_collection", db["users"])
    monkeypatch.setattr(auth, "_audit_col", db["audit"])
    monkeypatch.setattr(st, "rerun", st.stop)

    at = AppTest.from_function(_reject_app, default_timeout=60)
    at.session_state[f"confirm_reject_{pro_id}"] = True
    at.run()
    assert not at.exception, at.exception
    at.button(key=f"yes_reject_{pro_id}").click()
    at.run()
    assert not at.exception, at.exception

    level, message = at.session_state["pro_flash"]
    assert level == "warning"
    assert EN["pro_rejected"].replace("{name}", "Dana") not in message
    assert EN["pro_rejected_untold"].replace("{name}", "Dana") in message
    assert EN[note] in message
    assert EN[other] not in message


# ---------------------------------------------------------------------------
# The strip and the Edit form, through the real view and the real offer
# ---------------------------------------------------------------------------


def _leads_app():
    import streamlit as st

    import admin_panel.views.home as home
    from admin_panel.core.config import TRANS

    st.session_state["admin_role"] = "owner"
    st.session_state["admin_username"] = "operator"
    home.view_leads_dashboard(TRANS["EN"])


@pytest.fixture
def panel(muted, monkeypatch):
    """The real view, offer path and provider. Only the store is mongomock, and
    the async core is pointed at the same store the view reads."""
    db = mongomock.MongoClient()["muted"]
    monkeypatch.setattr(home, "users_collection", db["users"])
    monkeypatch.setattr(home, "leads_collection", db["leads"])
    monkeypatch.setattr(home, "messages_collection", db["messages"])
    monkeypatch.setattr(auth, "_audit_col", db["audit"])
    monkeypatch.setattr(st, "rerun", st.stop)

    async def set_lead_status(
        lead_id, status, _actor, extra_set=None, extra_unset=None, expected_status=None
    ):
        return db["leads"].find_one_and_update(
            {"_id": ObjectId(lead_id), "status": expected_status},
            {"$set": {"status": status, **(extra_set or {})}},
            return_document=ReturnDocument.AFTER,
        )

    async def find_one(query):
        return db["leads"].find_one(query)

    monkeypatch.setattr(admin_flow, "set_lead_status", set_lead_status)
    monkeypatch.setattr(
        admin_flow, "leads_collection", SimpleNamespace(find_one=find_one)
    )

    db["users"].insert_one(
        {
            "role": "professional",
            "business_name": "Dana",
            "phone_number": PRO_PHONE,
            "is_active": True,
            "pending_approval": False,
            "is_verified": True,
        }
    )
    lead_id = (
        db["leads"]
        .insert_one(
            {
                "chat_id": CUSTOMER_CHAT,
                "display_name": "Yossi",
                "status": PENDING,
                "pro_id": None,
                "details": "d",
                "issue_type": "d",
                "created_at": datetime.now(timezone.utc),
            }
        )
        .inserted_id
    )
    st.cache_data.clear()
    yield db, lead_id
    st.cache_data.clear()


def _normalise_selectboxes(at):
    """format_func widgets: AppTest holds the raw value but lists the labels."""
    label = lead_status_label(EN, PENDING)
    for box in at.selectbox:
        try:
            box.index
        except ValueError:
            box.set_value(label if label in box.options else box.options[0])


def _assert_operator_told_muted(at, db):
    # The run stopped at st.rerun, so the report waits in the outbox for the next
    # one. A second AppTest run trips over its own format_func selectboxes.
    shown = [
        text
        for report in at.session_state["leads_flash"]
        for text, _ in home._report_messages(EN, report)
    ]
    assert not any(EN["assign_ok"].format(pro="Dana") in text for text in shown), shown
    assert any(EN["assign_muted"].format(pro="Dana") in text for text in shown), shown
    audited = [
        e["details"]["outcome"] for e in db["audit"].find({"action": "assign_lead"})
    ]
    assert audited == [assignment.ASSIGN_NOT_SENT_MUTED]


def test_strip_offer_through_the_real_view(panel):
    db, lead_id = panel
    at = AppTest.from_function(_leads_app, default_timeout=60)
    at.run()
    assert not at.exception, at.exception

    _normalise_selectboxes(at)
    at.selectbox(key=f"assign_pro_{lead_id}").set_value("Dana")
    at.button(key=f"assign_go_{lead_id}").click()
    at.run()
    assert not at.exception, at.exception

    _assert_operator_told_muted(at, db)
    assert db["leads"].find_one({"_id": lead_id})["status"] == "new"


def test_edit_form_offer_through_the_real_view(panel):
    db, lead_id = panel
    at = AppTest.from_function(_leads_app, default_timeout=60)
    at.run()
    assert not at.exception, at.exception

    _normalise_selectboxes(at)
    box = [s for s in at.selectbox if s.label == EN["professional_label"]][0]
    box.set_value("Dana")
    [b for b in at.button if b.label == EN["save_changes_btn"]][0].click()
    at.run()
    assert not at.exception, at.exception

    _assert_operator_told_muted(at, db)
    assert db["leads"].find_one({"_id": lead_id})["status"] == "new"


def _check_app():
    import pandas as pd
    import streamlit as st

    import admin_panel.views.home as home
    from admin_panel.core.config import TRANS

    st.session_state["admin_role"] = "owner"
    lead = pd.Series(
        {
            "id": st.session_state["lead_id"],
            "status": "booked",
            "client": "Yossi",
            "_chat_id": "972500000002@c.us",
        }
    )
    home._render_selected_lead_actions(lead, TRANS["EN"], {})


def test_customer_check_button_says_the_environment_is_muted(muted, monkeypatch):
    db = mongomock.MongoClient()["muted"]
    pro_id = db["users"].insert_one({"business_name": "Dana"}).inserted_id
    lead_id = str(
        db["leads"]
        .insert_one({"chat_id": CUSTOMER_CHAT, "pro_id": pro_id, "status": "booked"})
        .inserted_id
    )
    monkeypatch.setattr(panel_utils, "leads_collection", db["leads"])
    monkeypatch.setattr(panel_utils, "users_collection", db["users"])
    monkeypatch.setattr(home, "leads_collection", db["leads"])
    monkeypatch.setattr(home, "messages_collection", db["messages"])
    monkeypatch.setattr(auth, "_audit_col", db["audit"])

    at = AppTest.from_function(_check_app, default_timeout=60)
    at.session_state["lead_id"] = lead_id
    at.run()
    assert not at.exception, at.exception
    _normalise_selectboxes(at)
    at.button(key=f"check_kanban_{lead_id}").click()
    at.run()
    assert not at.exception, at.exception

    assert not [e.value for e in at.success]
    assert EN["check_muted"] in [e.value for e in at.warning]
    audited = db["audit"].find_one({"action": "send_completion_check"})
    assert audited["details"]["sent"] is False


def test_table_report_renders_the_muted_sentence():
    """The table's Save reaches `_offer_pending` and renders through this."""
    messages = home._report_messages(
        EN,
        {
            "assignments": [
                {
                    "client": "Yossi",
                    "outcome": assignment.ASSIGN_NOT_SENT_MUTED,
                    "pro_name": "Dana",
                }
            ]
        },
    )

    assert (f"Yossi: {EN['assign_muted'].format(pro='Dana')}", "warning") in messages


# ---------------------------------------------------------------------------
# Boot visibility
# ---------------------------------------------------------------------------


def _boot_settings(prod_like):
    return SimpleNamespace(
        is_prod_like=prod_like, WHATSAPP_PROVIDER="dryrun", WHATSAPP_DRY_RUN=False
    )


def test_a_muted_prod_like_panel_warns_at_boot(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(startup_checks, "logger", log)

    reported = startup_checks.warn_if_cannot_transmit(
        _boot_settings(True), DryRunProvider()
    )

    assert reported is True
    log.warning.assert_called_once()
    assert "WHATSAPP_PROVIDER" in log.warning.call_args[0][0]


def test_a_muted_local_panel_only_notes_it(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(startup_checks, "logger", log)

    assert startup_checks.warn_if_cannot_transmit(
        _boot_settings(False), DryRunProvider()
    )

    log.info.assert_called_once()
    log.warning.assert_not_called()


def test_a_transmitting_panel_reports_nothing(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(startup_checks, "logger", log)

    assert not startup_checks.warn_if_cannot_transmit(
        _boot_settings(True), SimpleNamespace(transmits=True)
    )

    assert not log.method_calls


def test_main_really_makes_the_report_at_module_level():
    """``main.py`` cannot run under pytest, and an uncalled report is silent."""
    source = Path(__file__).parents[1] / "admin_panel" / "main.py"
    called = [
        node.value.func.id
        for node in ast.parse(source.read_text(encoding="utf-8")).body
        if isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Call)
        and isinstance(node.value.func, ast.Name)
    ]
    assert "report_whatsapp_egress" in called


def test_the_panel_reports_once_per_process_and_never_breaks_boot(monkeypatch):
    log = MagicMock()
    monkeypatch.setattr(runtime_config, "logger", log)
    monkeypatch.setattr(runtime_config, "_reported_egress", False)
    factory = MagicMock(return_value=SimpleNamespace(provider=DryRunProvider()))

    # Once per process: main.py runs again on every interaction.
    runtime_config.report_whatsapp_egress(whatsapp_factory=factory)
    runtime_config.report_whatsapp_egress(whatsapp_factory=factory)
    factory.assert_called_once()

    monkeypatch.setattr(runtime_config, "_reported_egress", False)
    runtime_config.report_whatsapp_egress(
        whatsapp_factory=MagicMock(side_effect=RuntimeError("boom"))
    )
    log.error.assert_called_once()
