"""PRO-230: tapping a template's quick-reply button is the same turn as typing it.

A tap arrives as a Meta ``button`` message; ``normalize_meta_message`` reads its
payload back as the message text, so the flow sees a string, never a "tap". What
this pins is that for every template that carries buttons, in the state the
template is sent in, the payload and the reply the body tells the user to type
produce the same outcome. The typed reply comes from the body itself
(``advertised_replies``), the tapped one from the button's payload, so the two
come from different places and the test fails if they ever part.

Each (template, button) runs twice, typed and tapped, against the same expected
outcome. The state a template is sent in is one of five scenarios below. The two
"talk to a person" templates share the booked-job arrangement: what the handler
reads is the customer's chat and the keyword, not why they were messaged.
"""

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.providers.whatsapp.cloud_api import normalize_meta_message
from app.providers.whatsapp.template_registry import TEMPLATES, advertised_replies
from tests.copy_util import longest_static_chunk, static_prefix
from tests.e2e import reserved_numbers as R

BUTTONED = [spec for spec in TEMPLATES.values() if spec.buttons]

# Per template: the scenario it is sent in, then what each of its buttons does,
# in button order.
SCENARIOS = {
    "lead_offer": ("offer", ["approve", "reject"]),
    "lead_offer_reassigned": ("offer", ["approve", "reject"]),
    "approval_nudge": ("offer", ["approve", "reject"]),
    "sos_alert_pending": ("offer", ["approve", "reject"]),
    "stale_lead_reminder": ("booked_pro", ["finish"]),
    "finish_reminder": ("booked_pro", ["finish", "still_working"]),
    "daily_agenda": ("booked_pro", ["jobs"]),
    "no_pro_available": ("customer_sos", ["human"]),
    "pro_cancelled_booking": ("customer_sos", ["human"]),
    "completion_check": ("completion", ["done", "not_yet", "no_show"]),
    "reassign_offer": ("reassign", ["find_another", "keep_waiting"]),
}

CASES = [
    (spec.key, index, action)
    for spec in BUTTONED
    for index, action in enumerate(SCENARIOS.get(spec.key, ("", []))[1])
]


def test_every_template_with_buttons_has_a_scenario_for_each_button():
    """A new buttoned template must say what a tap on it does, here."""
    assert {spec.key for spec in BUTTONED} == set(SCENARIOS)
    for spec in BUTTONED:
        assert len(SCENARIOS[spec.key][1]) == len(spec.buttons), spec.key


async def _arrange(world, scenario):
    """The state the template is sent in. Returns (lead, who writes, extra)."""
    await world.standard_cast()
    primary = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    if scenario == "offer":
        lead = await world.awaiting_approval_job(primary)
        return lead, pro_chat
    if scenario == "booked_pro":
        return await world.booked_job(primary), pro_chat
    if scenario == "customer_sos":
        return await world.booked_job(primary), world.customer
    if scenario == "completion":
        lead = await world.booked_job(primary, completion_check_sent_at=world.anchor)
        return lead, world.customer
    if scenario == "reassign":
        return await _offered_lead(world), world.customer
    raise AssertionError(scenario)


async def _offered_lead(world):
    """A NEW lead whose pro stayed silent until the customer was offered a
    reassignment, as in test_e2e_reassign_offer_reply.py."""
    from app.services.monitor_service import check_pro_approval_sla

    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    await check_pro_approval_sla()
    offered = await world.lead_by_id(lead["_id"])
    assert offered["reassign_offered"] is True  # the arrangement, not the behaviour
    return offered


def _tap(spec, index, chat_id):
    """The webhook message a tap on ``spec``'s button ``index`` produces, read
    back the way the Cloud API route reads it."""
    button = spec.buttons[index]
    return normalize_meta_message(
        {
            "from": chat_id.split("@")[0],
            "id": "wamid.TAP",
            "type": "button",
            "button": {"payload": button.payload, "text": button.label},
        }
    )


async def _check(world, action, lead, pro_chat):
    """What the action did, whichever way it was written."""
    customer = world.customer
    after = await world.lead_by_id(lead["_id"])
    sent = world.recorder
    primary = world.pros[R.PRO_PRIMARY]["_id"]
    secondary = world.pros[R.PRO_SECONDARY]["_id"]

    if action == "approve":
        assert after["status"] == LeadStatus.BOOKED
        assert after["pro_id"] == primary
        sent.assert_text_to(pro_chat, Messages.Pro.APPROVE_SUCCESS)
    elif action == "reject":
        assert after["status"] == LeadStatus.NEW
        assert after["pro_id"] == secondary
        sent.assert_text_to(pro_chat, Messages.Pro.REJECT_SUCCESS)
    elif action == "finish":
        assert after["status"] == LeadStatus.COMPLETED
        sent.assert_text_to(pro_chat, Messages.Pro.FINISH_SUCCESS_ASK_PRICE)
    elif action == "still_working":
        assert after["status"] == LeadStatus.BOOKED
        sent.assert_text_to(pro_chat, Messages.Pro.STILL_WORKING_ACK)
    elif action == "jobs":
        assert after["status"] == LeadStatus.BOOKED
        sent.assert_text_to(
            pro_chat, longest_static_chunk(Messages.Pro.ACTIVE_JOBS_HEADER)
        )
    elif action == "human":
        await world.assert_state(UserStates.PAUSED_FOR_HUMAN)
        sent.assert_text_to(customer, Messages.Customer.BOT_PAUSED_BY_CUSTOMER)
    elif action == "done":
        assert after["status"] == LeadStatus.COMPLETED
        assert after.get("waiting_for_rating") is True
        sent.assert_text_to(customer, static_prefix(Messages.Customer.COMPLETION_ACK))
    elif action == "not_yet":
        assert after["status"] == LeadStatus.BOOKED
        sent.assert_text_to(customer, Messages.Customer.COMPLETION_NOT_YET_ACK)
    elif action == "no_show":
        assert after["status"] != LeadStatus.COMPLETED
        sent.assert_text_to(customer, Messages.Customer.NO_SHOW_ACK)
    elif action == "find_another":
        assert after["status"] == LeadStatus.NEW
        assert after["pro_id"] == secondary
        sent.assert_text_to(customer, Messages.SOS.CUSTOMER_REASSIGNING)
    elif action == "keep_waiting":
        assert after["status"] == LeadStatus.NEW
        assert after["pro_id"] == primary
        assert after["reassign_offered"] is False
        sent.assert_text_to(customer, Messages.Customer.REASSIGN_WAIT_ACK)
    else:
        raise AssertionError(action)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["typed", "tapped"])
@pytest.mark.parametrize(
    "key, index, action", CASES, ids=[f"{k}-{a}" for k, _, a in CASES]
)
async def test_a_tap_and_the_typed_reply_reach_the_same_outcome(
    world, key, index, action, mode
):
    spec = TEMPLATES[key]
    scenario, _ = SCENARIOS[key]
    lead, writer = await _arrange(world, scenario)
    typed = advertised_replies(spec.body)[index]
    world.recorder.clear()

    if mode == "typed":
        await world.send(typed, chat_id=writer)
    else:
        tapped = _tap(spec, index, writer)
        # Whatever the label says, what the flow receives is the typed reply.
        assert tapped.chat_id == writer
        assert tapped.text == typed
        await world.send(
            tapped.text, chat_id=tapped.chat_id, message_id=tapped.message_id
        )

    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await _check(world, action, lead, pro_chat)
