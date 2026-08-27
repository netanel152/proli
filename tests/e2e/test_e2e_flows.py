"""Full-flow coverage for the offline E2E harness (PRO-83).

Every test here drives the production orchestrator with synthetic inbound messages
and asserts on three things a unit test cannot see together: the **fully rendered
Hebrew** each participant received, the **Mongo state** that resulted, and the
**Redis state** the FSM landed in.

Four tests are marked ``xfail(strict=True)``. They assert the behaviour the system
is *supposed* to have and currently does not — defects this harness found on its
first run, documented in PRO-83's PR and in ``docs/TESTING.md``. Strict mode means
each one turns into a hard failure the moment it is fixed, forcing the expectation
to be updated rather than quietly drifting.
"""

import pytest

from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.phone import to_local_phone
from tests.e2e import reserved_numbers as R
from tests.e2e.ai_replay import deal, reply
from tests.e2e.world import media_url_for


# ===========================================================================
# 1. The customer happy path
# ===========================================================================


@pytest.mark.asyncio
async def test_happy_path_inbound_to_rating(world):
    """inbound → clarify → media → address → pro approval → booking → completion → rating."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)

    world.ai.script(
        reply("שלום! 👋 אני פרולי. איך קוראים לך ומה קרה?"),
        reply(
            "נעים מאוד דנה. איפה בדיוק הנזילה?",
            customer_name="דנה",
            city="תל אביב",
            issue="נזילה מתחת לכיור",
        ),
        reply("ראיתי את התמונה. מה הכתובת המדויקת ומתי נוח לך?"),
        deal(
            "מצוין דנה, קבענו למחר ב-10:00. 👍",
            customer_name="דנה",
            city="תל אביב",
            issue="נזילה מתחת לכיור",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
            appointment_time="מחר ב-10:00",
            quoted_price="450",
        ),
    )

    # --- first contact: no lead yet, just conversation
    await world.send("היי")
    assert await world.lead() is None
    world.recorder.assert_text_to(world.customer, "איך קוראים לך")

    # --- issue + photo: lead created, pro matched by real geo routing, pre-notified
    await world.send("יש לי נזילה מתחת לכיור בתל אביב", media="image")
    lead = await world.lead()
    assert lead["status"] == LeadStatus.CONTACTED
    assert lead["pro_id"] == pro["_id"]
    assert media_url_for("image") in lead["media_urls"]
    early = world.recorder.assert_text_to(
        pro_chat, "שיחה בתהליך", "נזילה מתחת לכיור", "תל אביב"
    )
    assert "אין צורך לפעול עכשיו" in early.body

    # --- the close: address gate passes, both sides notified
    world.recorder.clear()
    await world.send("דיזנגוף 50, קומה 3 דירה 12, מחר ב-10")
    lead = await world.lead()
    assert lead["status"] == LeadStatus.NEW
    assert lead["full_address"] == "דיזנגוף 50, תל אביב"
    assert lead["quoted_price"] == "450"
    await world.assert_state(UserStates.AWAITING_PRO_APPROVAL)
    assert await world.state_ttl() <= WorkerConstants.PRO_APPROVAL_TTL_SECONDS

    world.recorder.assert_text_to(
        world.customer, "העברתי את הפנייה שלך", pro["business_name"]
    )
    approval = world.recorder.assert_text_to(
        pro_chat,
        "פרטי עבודה חדשה לאישורך",
        "דנה",
        "דיזנגוף 50, תל אביב",
        "קומה 3, דירה 12",
        "מחר ב-10:00",
        "450₪",
    )
    assert media_url_for("image") in approval.body, "media links belong in the approval"
    world.recorder.assert_text_to(pro_chat, "נווט לכתובת", "waze.com")

    # --- approval: BOOKED, the exact slot is reserved, customer told
    world.recorder.clear()
    await world.send("אשר", chat_id=pro_chat)
    lead = await world.lead()
    assert lead["status"] == LeadStatus.BOOKED
    assert lead["booked_slot_id"] is not None
    assert (await world.slot(lead["booked_slot_id"]))["is_taken"] is True
    await world.assert_state(UserStates.IDLE)
    world.recorder.assert_text_to(
        world.customer,
        "נמצא לך איש מקצוע",
        pro["business_name"],
        to_local_phone(R.PRO_PRIMARY),  # the customer sees the local 0… form
        "אינסטלטור",
        "450₪",
    )
    world.recorder.assert_text_to(pro_chat, "העבודה אושרה")

    # --- completion: customer is asked to rate
    world.recorder.clear()
    await world.send("סיימתי", chat_id=pro_chat)
    lead = await world.lead()
    assert lead["status"] == LeadStatus.COMPLETED
    assert lead["waiting_for_rating"] is True
    world.recorder.assert_text_to(
        world.customer, "איך היה השירות", pro["business_name"]
    )
    world.recorder.assert_text_to(pro_chat, "כמה גבית על העבודה")

    # --- rating + free-text review
    world.recorder.clear()
    await world.send("5")
    world.recorder.assert_text_to(world.customer, "תודה על הדירוג")
    await world.send("הגיע בזמן, עבודה נקייה")
    world.recorder.assert_text_to(world.customer, "הביקורת שלך נשמרה")

    reviews = await world.reviews()
    assert len(reviews) == 1
    assert reviews[0]["comment"] == "הגיע בזמן, עבודה נקייה"
    assert reviews[0]["rating"] == 5
    assert (await world.pro_doc(R.PRO_PRIMARY))["social_proof"]["review_count"] == 11

    # PRO-57: the whole lifecycle is on the lead, in order, from creation onward.
    assert await world.status_history(lead["_id"]) == [
        LeadStatus.CONTACTED,
        LeadStatus.NEW,
        LeadStatus.BOOKED,
        LeadStatus.COMPLETED,
    ]


# ===========================================================================
# 2. Rejection, reassignment, escalation
# ===========================================================================


@pytest.mark.asyncio
async def test_pro_rejection_records_rejected_then_reassigns(world):
    """PRO-117: a reject is no longer a terminal write. _handle_reject still
    marks the lead REJECTED (actor=pro) first — the rejecting pro's own
    acknowledgement text and the status_history entry both reflect that — but
    then hands off to monitor_service.reassign_lead, which immediately
    re-opens the lead as NEW under the next pro. See the companion test below
    for the reassignment's own effects (new pro notified, customer told)."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.awaiting_approval_job(pro)

    await world.send("דחה", chat_id=world.pro_chat(R.PRO_PRIMARY))

    world.recorder.assert_text_to(world.pro_chat(R.PRO_PRIMARY), "העבודה נדחתה")
    assert (await world.status_history(lead["_id"]))[-2:] == [
        LeadStatus.REJECTED,
        LeadStatus.NEW,
    ]
    # Terminal REJECTED was only a way-station — a replacement was found, so
    # the lead is alive again, not dead-ended.
    assert (await world.lead_by_id(lead["_id"]))["status"] == LeadStatus.NEW


@pytest.mark.asyncio
async def test_pro_rejection_reassigns_to_the_next_pro(world):
    """The DEFECT this test used to document (xfail, strict) is fixed by
    PRO-117: a pro rejection is no longer a silent dead end. reassign_lead —
    the same helper the SOS Healer and the PRO-56 approval-SLA offer use —
    now re-opens the lead under the next-best pro and keeps the customer in
    the loop, instead of leaving REJECTED unqueried by any scheduler job."""
    await world.standard_cast()
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])

    await world.send("דחה", chat_id=world.pro_chat(R.PRO_PRIMARY))

    updated = await world.lead_by_id(lead["_id"])
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]
    assert updated["reassignment_count"] == 1
    world.recorder.assert_text_to(world.pro_chat(R.PRO_SECONDARY), "הצעת עבודה חדשה")
    # The customer is told who was found — the thread doesn't go silent.
    world.recorder.assert_text_to(
        world.customer, world.pros[R.PRO_SECONDARY]["business_name"]
    )
    # The rejecting pro must not also get the "lost lead" copy — that message
    # is for pros who went silent, not ones who explicitly rejected.
    lost_lead_sends = [
        s
        for s in world.recorder.to(world.pro_chat(R.PRO_PRIMARY))
        if "הועברה עקב חוסר מענה" in s.body
    ]
    assert not lost_lead_sends, (
        "rejecting pro must not receive PRO_LOST_LEAD "
        f"(that copy is for silent pros): {lost_lead_sends}"
    )


@pytest.mark.asyncio
async def test_stale_lead_is_reassigned_to_the_next_pro(world):
    """The SOS healer path that does work: a NEW lead nobody answered for an hour
    moves to the next-best pro, with the reassignment lifecycle reset."""
    from app.services.monitor_service import check_and_reassign_stale_leads

    await world.standard_cast()
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(lead["_id"], WorkerConstants.SOS_TIMEOUT_MINUTES + 5, "created_at")
    world.recorder.clear()

    await check_and_reassign_stale_leads()

    updated = await world.lead_by_id(lead["_id"])
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]
    assert updated["reassignment_count"] == 1
    assert updated["approval_nudged"] is False
    world.recorder.assert_text_to(world.customer, "מאתרים עבורך איש מקצוע זמין")
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_SECONDARY), "הצעת עבודה חדשה", "דיזנגוף 50"
    )
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY), "הועברה לאיש מקצוע אחר"
    )


@pytest.mark.asyncio
async def test_exhausted_reassignments_escalate_to_admin_never_closed(world):
    """PRO-63: the dead end that used to auto-CLOSE now hands off to a human, with
    an immediate admin page rather than the 4-hourly batch."""
    from app.services.monitor_service import reassign_lead

    await world.standard_cast()
    await world.add_admin_pro()
    lead = await world.awaiting_approval_job(
        world.pros[R.PRO_PRIMARY],
        reassignment_count=WorkerConstants.MAX_REASSIGNMENTS,
    )
    world.recorder.clear()

    assert await reassign_lead(await world.lead_by_id(lead["_id"])) is False

    updated = await world.lead_by_id(lead["_id"])
    assert updated["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    assert updated["status"] != LeadStatus.CLOSED
    assert updated["escalation_reason"] == "max_reassignments_exhausted"
    world.recorder.assert_text_to(world.customer, "מעביר אותך לנציג", "תוך שעה")
    # PRO-88: the admin is paged out of band, never over WhatsApp.
    world.recorder.assert_silent(
        world.admin, "operator alerts moved to Sentry — no template needed"
    )
    world.assert_paged("PENDING_ADMIN_REVIEW", "נזילה במטבח")
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_escalation_is_idempotent(world):
    from app.services.monitor_service import reassign_lead

    await world.standard_cast()
    await world.add_admin_pro()
    lead = await world.awaiting_approval_job(
        world.pros[R.PRO_PRIMARY],
        reassignment_count=WorkerConstants.MAX_REASSIGNMENTS,
    )
    await reassign_lead(await world.lead_by_id(lead["_id"]))
    world.recorder.clear()

    await reassign_lead(await world.lead_by_id(lead["_id"]))

    world.recorder.assert_silent(
        world.admin, "a second pass must not re-page the admin"
    )


@pytest.mark.asyncio
async def test_no_pro_at_max_radius_falls_back_without_paging_the_operator(world):
    """PRO-77: an unmatchable lead is a routine coverage gap, not an infra page.

    The only seeded pro is ~90 km away in Haifa, so all three real radius steps
    (10 → 20 → 30 km) genuinely come back empty.

    The worker forwards CRITICAL-only to Sentry, so "does this email the operator"
    is exactly "was anything logged at CRITICAL". Asserted with a real loguru sink
    rather than pytest's ``caplog``, which only sees stdlib logging.
    """
    from app.core.logger import logger

    await world.add_pro(R.PRO_FAR, name="הצפוני", city="חיפה", service_areas=["חיפה"])
    await world.grant_consent()
    world.ai.script(reply("מאתר עבורך איש מקצוע.", city="תל אביב", issue="נזילה"))

    paged: list[str] = []
    sink_id = logger.add(lambda message: paged.append(str(message)), level="CRITICAL")
    try:
        await world.send("יש לי נזילה בתל אביב")
    finally:
        logger.remove(sink_id)

    lead = await world.lead()
    assert lead["status"] == LeadStatus.PENDING_ADMIN_REVIEW
    world.recorder.assert_text_to(world.customer, "מחפשים את איש המקצוע המתאים ביותר")
    assert not paged, f"no-pro-available must never page the operator (PRO-77): {paged}"


@pytest.mark.asyncio
async def test_pending_admin_review_short_circuits_then_expires(world):
    """PRO-63: the short-circuit stops duplicate leads, but is bounded so an
    unworked escalation cannot brick the customer's chat forever."""
    await world.standard_cast()
    lead = await world.booked_job(
        world.pros[R.PRO_PRIMARY], status=LeadStatus.PENDING_ADMIN_REVIEW
    )
    world.recorder.clear()

    await world.send("מה קורה עם הפנייה שלי?")
    world.recorder.assert_text_to(world.customer, "עדיין בבדיקה אצל צוות Proli")
    assert await world.lead(status=LeadStatus.CONTACTED) is None, "no duplicate lead"

    # Past the window, the next message starts a fresh request.
    await world.age(
        lead["_id"],
        (WorkerConstants.PENDING_REVIEW_SHORTCIRCUIT_HOURS + 1) * 60,
        "updated_at",
    )
    world.ai.script(reply("שלום! מה קרה?"))
    world.recorder.clear()
    await world.send("היי, יש בעיה חדשה")
    world.recorder.assert_text_to(world.customer, "מה קרה")


# ===========================================================================
# 3. SOS, pausing, SLA deflection
# ===========================================================================


@pytest.mark.asyncio
async def test_sos_pauses_the_bot_and_deflects_after_the_idle_window(world):
    from app.services.monitor_service import check_sla_deflection

    await world.standard_cast()
    await world.add_admin_pro()
    lead = await world.booked_job(world.pros[R.PRO_PRIMARY])

    await world.send("אני רוצה נציג אנושי")

    await world.assert_state(UserStates.PAUSED_FOR_HUMAN)
    assert await world.state_ttl() <= WorkerConstants.PAUSE_TTL_SECONDS
    assert (await world.lead_by_id(lead["_id"]))["is_paused"] is True
    world.recorder.assert_text_to(world.customer, "מעביר אותך לנציג אנושי")
    # PRO-88: SOS still reaches the operator, just not over WhatsApp.
    world.recorder.assert_silent(
        world.admin, "operator alerts moved to Sentry — no template needed"
    )
    world.assert_paged("SOS from customer")
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY), "הלקוח מבקש מענה אנושי"
    )

    # Each further message resets the rolling window instead of answering.
    world.recorder.clear()
    await world.send("הלו? מישהו שם?")
    world.recorder.assert_nothing_sent("the bot is paused for a human")
    await world.assert_state(UserStates.PAUSED_FOR_HUMAN)

    # Nobody picked it up within the window → SLA deflection.
    await world.age(
        lead["_id"], WorkerConstants.PAUSE_TTL_SECONDS / 60 + 5, "paused_at"
    )
    await check_sla_deflection()

    assert (await world.lead_by_id(lead["_id"]))["sla_deflected"] is True
    world.recorder.assert_text_to(world.customer, "באמצע עבודה מורכבת")
    await world.assert_state(UserStates.IDLE)


# ===========================================================================
# 4. Approval SLA (PRO-56)
# ===========================================================================


@pytest.mark.asyncio
async def test_approval_sla_nudges_the_pro_at_t10(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"], WorkerConstants.APPROVAL_NUDGE_MINUTES + 1, "pro_notified_at"
    )
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY),
        "ליד ממתין לאישורך",
        str(WorkerConstants.APPROVAL_NUDGE_MINUTES),
    )
    world.recorder.assert_silent(world.customer, "T+10 nudges the pro only")
    assert (await world.lead_by_id(lead["_id"]))["approval_nudged"] is True


@pytest.mark.asyncio
async def test_approval_sla_nudge_is_idempotent(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"], WorkerConstants.APPROVAL_NUDGE_MINUTES + 1, "pro_notified_at"
    )
    await check_pro_approval_sla()
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_silent(
        world.pro_chat(R.PRO_PRIMARY), "a nudge fires once per lead"
    )


@pytest.mark.asyncio
async def test_approval_sla_offers_the_customer_a_reassignment_at_t25(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_text_to(
        world.customer, "עדיין לא אישר את הפנייה", "לחפש לך איש מקצוע אחר"
    )
    assert (await world.lead_by_id(lead["_id"]))["reassign_offered"] is True

    # "1" → reassign to the next pro.
    world.recorder.clear()
    await world.send("1")
    updated = await world.lead_by_id(lead["_id"])
    assert updated["pro_id"] == world.pros[R.PRO_SECONDARY]["_id"]


@pytest.mark.asyncio
async def test_approval_sla_reply_two_restarts_the_window(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    await check_pro_approval_sla()
    world.recorder.clear()

    await world.send("2")

    updated = await world.lead_by_id(lead["_id"])
    assert updated["reassign_offered"] is False
    assert updated["approval_nudged"] is False
    assert updated["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    world.recorder.assert_text_to(world.customer, "ממשיכים להמתין לאישור")


@pytest.mark.asyncio
async def test_emergency_leads_halve_the_sla_thresholds(world):
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(True)
    lead = await world.awaiting_approval_job(
        world.pros[R.PRO_PRIMARY], is_emergency=True
    )
    # Past the halved nudge threshold, well short of the standard one.
    await world.age(
        lead["_id"], WorkerConstants.APPROVAL_NUDGE_MINUTES // 2 + 1, "pro_notified_at"
    )
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_text_to(world.pro_chat(R.PRO_PRIMARY), "ליד ממתין לאישורך")


@pytest.mark.asyncio
async def test_quiet_hours_suppress_the_customer_reassignment_offer(world):
    """PRO-73: the customer-facing half of the SLA is gated; the pro nudge is not."""
    from app.services.monitor_service import check_pro_approval_sla

    await world.standard_cast()
    world.set_business_hours(False)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(
        lead["_id"],
        WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES + 1,
        "pro_notified_at",
    )
    world.recorder.clear()

    await check_pro_approval_sla()

    world.recorder.assert_silent(world.customer, "quiet hours gate the cold offer")
    world.recorder.assert_text_to(world.pro_chat(R.PRO_PRIMARY), "ליד ממתין לאישורך")


@pytest.mark.asyncio
async def test_quiet_hours_suppress_the_cold_sos_healer(world):
    """PRO-73: a cold customer-facing job needs both the toggle *and* the hours."""
    import app.scheduler as scheduler_module

    await world.standard_cast()
    await world.set_scheduler_config(sos_healer_active=True)
    lead = await world.awaiting_approval_job(world.pros[R.PRO_PRIMARY])
    await world.age(lead["_id"], WorkerConstants.SOS_TIMEOUT_MINUTES + 5, "created_at")

    world.set_business_hours(False)
    world.recorder.clear()
    await scheduler_module.run_sos_healer()
    world.recorder.assert_nothing_sent("outside business hours nothing cold goes out")

    world.set_business_hours(True)
    await scheduler_module.run_sos_healer()
    world.recorder.assert_text_to(world.customer, "מאתרים עבורך איש מקצוע זמין")


# ===========================================================================
# 5. Bilateral cancellation and rescheduling
# ===========================================================================


@pytest.mark.asyncio
async def test_customer_cancellation_releases_the_right_slot(world):
    """PRO-32 + PRO-43 + PRO-118: with two active jobs, cancelling one must
    free that job's slot and leave the other reservation untouched.

    PRO-118: a cancel keyword no longer cancels on the first hit — it asks
    for explicit confirmation first, so this drives both steps. Phrasing is
    the imperative "בטל את העבודה"; the infinitive "אני רוצה לבטל את העבודה"
    from the original PRO-32/PRO-43 wording has its own regression test,
    test_infinitive_cancel_phrasing_triggers_confirmation, below."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    other_customer = R.chat(R.CUSTOMER_B)

    mine = await world.booked_job(pro)
    theirs = await world.booked_job(pro, chat_id=other_customer)
    assert mine["booked_slot_id"] != theirs["booked_slot_id"]
    world.recorder.clear()

    await world.send("בטל את העבודה")

    # Step 1: nothing destructive yet — the confirmation prompt went out and
    # the lead/slot are untouched.
    still_booked = await world.lead_by_id(mine["_id"])
    assert still_booked["status"] == LeadStatus.BOOKED
    assert (await world.slot(mine["booked_slot_id"]))["is_taken"] is True
    world.recorder.assert_text_to(world.customer, "לבטל את העבודה")
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)

    world.recorder.clear()
    await world.send("1")

    updated = await world.lead_by_id(mine["_id"])
    assert updated["status"] == LeadStatus.CANCELLED
    assert updated["cancel_reason"] == "customer_requested"
    assert (await world.slot(mine["booked_slot_id"]))["is_taken"] is False
    assert (await world.slot(theirs["booked_slot_id"]))["is_taken"] is True
    world.recorder.assert_text_to(world.customer, "ביטלתי את העבודה כבקשתך")
    world.recorder.assert_text_to(
        world.pro_chat(R.PRO_PRIMARY), "ביטל/ה את העבודה", "דיזנגוף 50"
    )
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_infinitive_cancel_phrasing_triggers_confirmation(world):
    """Regression: the natural Hebrew phrase 'אני רוצה לבטל את העבודה' (the
    infinitive 'לבטל', with the Hebrew ל- prefix) must trigger the same
    cancel-confirmation flow as the imperative 'בטל'. Before PRO-118,
    substring matching caught this by accident ('בטל' ⊂ 'לבטל'); whole-token
    matching initially broke it until 'לבטל' was added to CANCEL_KEYWORDS
    explicitly (the fix for the bug this test used to pin as xfail). The
    job must stay BOOKED — untouched — until the customer explicitly
    confirms with '1'."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    booked = await world.booked_job(pro)
    world.recorder.clear()

    await world.send("אני רוצה לבטל את העבודה")

    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    world.recorder.assert_text_to(world.customer, "לבטל את העבודה")
    still_booked = await world.lead_by_id(booked["_id"])
    assert still_booked["status"] == LeadStatus.BOOKED
    assert (await world.slot(booked["booked_slot_id"]))["is_taken"] is True

    world.recorder.clear()
    await world.send("1")

    updated = await world.lead_by_id(booked["_id"])
    assert updated["status"] == LeadStatus.CANCELLED
    assert (await world.slot(booked["booked_slot_id"]))["is_taken"] is False
    world.recorder.assert_text_to(world.customer, "ביטלתי את העבודה כבקשתך")
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_cancel_confirmation_abort_restores_prior_flow_state(world):
    """A customer mid-AWAITING_ADDRESS on a second, in-flight lead who asks to
    cancel their older BOOKED job gets the confirmation prompt (the
    interceptor stashes 'AWAITING_ADDRESS' as the resume state). Declining
    ('2') must restore AWAITING_ADDRESS rather than dumping them to IDLE, and
    the older BOOKED job must stay untouched throughout."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    older_booked = await world.booked_job(pro)

    # A second, in-flight request — the customer is mid address-collection.
    await world.booked_job(pro, status="new", booked_slot_id=None)
    await world.set_state(UserStates.AWAITING_ADDRESS)
    world.recorder.clear()

    await world.send("לבטל")

    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)
    meta = await world.metadata()
    assert meta["cancel_confirm_lead_id"] == str(older_booked["_id"])
    assert meta.get("cancel_confirm_resume_state")
    # Nothing destructive happened yet
    still_booked = await world.lead_by_id(older_booked["_id"])
    assert still_booked["status"] == LeadStatus.BOOKED

    world.recorder.clear()
    await world.send("2")

    await world.assert_state(UserStates.AWAITING_ADDRESS)
    unchanged = await world.lead_by_id(older_booked["_id"])
    assert unchanged["status"] == LeadStatus.BOOKED


@pytest.mark.asyncio
async def test_cancel_confirmation_abort_restores_idle_default_state(world):
    """Same resume-state guarantee, for the most common case: a customer with
    no explicit prior state declines the cancel and must land back in a
    *working* IDLE.

    Regression guard for the stash's value normalization. ``get_state``
    returns a plain Redis string on every real path but the enum member
    ``UserStates.IDLE`` as its default, and ``str()`` on that member yields
    the display form ``"UserStates.IDLE"`` — which, persisted verbatim,
    matches no state downstream. The interceptor stores
    ``getattr(current_state, "value", current_state)`` for that reason; the
    final exchange below proves the restored state still behaves like IDLE
    rather than merely comparing equal to it."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    booked = await world.booked_job(pro)
    world.recorder.clear()

    await world.send("בטל")
    await world.assert_state(UserStates.AWAITING_CANCEL_CONFIRMATION)

    world.recorder.clear()
    await world.send("2")

    await world.assert_state(UserStates.IDLE)
    unchanged = await world.lead_by_id(booked["_id"])
    assert unchanged["status"] == LeadStatus.BOOKED

    # The restored state must actually behave like IDLE for the next
    # message, not merely compare equal to it.
    world.recorder.clear()
    await world.send("תודה")
    world.recorder.assert_text_to(world.customer, "בכיף")


@pytest.mark.asyncio
async def test_pro_cancellation_releases_the_right_slot(world):
    """The other half of PRO-43: the pro picks job 2 from their list and only that
    job's slot is freed."""
    from app.services.pro_flow import handle_pro_text_command
    from app.services.workflow_service import lead_manager

    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    first = await world.booked_job(pro)
    second = await world.booked_job(pro, chat_id=R.chat(R.CUSTOMER_B))
    world.recorder.clear()

    listing = await handle_pro_text_command(
        pro_chat, "ביטול", world.client, lead_manager
    )
    assert "איזו עבודה לבטל" in listing
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_CANCEL, chat_id=pro_chat)

    mapping = (await world.metadata(pro_chat))["cancelling_jobs_context"]
    pick = next(k for k, v in mapping.items() if v == str(second["_id"]))
    await handle_pro_text_command(pro_chat, pick, world.client, lead_manager)

    assert (await world.lead_by_id(second["_id"]))["status"] == LeadStatus.CANCELLED
    assert (await world.lead_by_id(first["_id"]))["status"] == LeadStatus.BOOKED
    assert (await world.slot(second["booked_slot_id"]))["is_taken"] is False
    assert (await world.slot(first["booked_slot_id"]))["is_taken"] is True
    world.recorder.assert_text_to(R.chat(R.CUSTOMER_B), "איש המקצוע ביטל את העבודה")


@pytest.mark.asyncio
async def test_reschedule_swaps_the_reservation(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    lead = await world.booked_job(pro)
    old_slot_id = lead["booked_slot_id"]
    world.recorder.clear()

    await world.send("אני צריך מועד אחר")
    await world.assert_state(UserStates.AWAITING_RESCHEDULE_TIME)
    offer = world.recorder.assert_text_to(world.customer, "בוא נתאם מועד חדש")
    assert "1." in offer.body

    world.recorder.clear()
    await world.send("1")

    updated = await world.lead_by_id(lead["_id"])
    assert updated["booked_slot_id"] != old_slot_id
    assert updated["rescheduled_count"] == 1
    assert updated["status"] == LeadStatus.BOOKED
    assert (await world.slot(old_slot_id))["is_taken"] is False
    assert (await world.slot(updated["booked_slot_id"]))["is_taken"] is True
    world.recorder.assert_text_to(world.customer, "המועד שונה בהצלחה")
    world.recorder.assert_text_to(world.pro_chat(R.PRO_PRIMARY), "עדכון יומן")
    await world.assert_state(UserStates.IDLE)


# ===========================================================================
# 6. Dual-role routing (PRO-69)
# ===========================================================================


@pytest.mark.asyncio
async def test_pro_as_customer_entry_and_sticky_customer_mode(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    world.ai.script(
        reply("מאתר איש מקצוע.", city="תל אביב", issue="דוד מים"),
        reply("מה הכתובת המדויקת?"),
    )

    # A pro who types לקוח switches deterministically — no AI, no confirmation.
    await world.send("לקוח", chat_id=pro_chat)
    await world.assert_state(UserStates.CUSTOMER_MODE, chat_id=pro_chat)
    world.recorder.assert_text_to(pro_chat, "עברת למצב לקוח")

    # Their own request is served as a customer's, and they stay on that side.
    await world.send("הדוד שלי דולף בתל אביב", chat_id=pro_chat)
    own_lead = await world.lead(chat_id=pro_chat)
    assert own_lead is not None
    await world.assert_state(UserStates.CUSTOMER_MODE, chat_id=pro_chat)


@pytest.mark.asyncio
async def test_pro_keyword_hijacks_back_to_pro_mode_mid_customer_flow(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.awaiting_approval_job(pro, chat_id=R.chat(R.CUSTOMER_B))
    await world.set_state(UserStates.CUSTOMER_MODE, chat_id=pro_chat)
    world.recorder.clear()

    # A pro-only keyword always wins, even mid-CUSTOMER_MODE.
    await world.send("סיימתי", chat_id=pro_chat)

    await world.assert_state(UserStates.PRO_MODE, chat_id=pro_chat)


@pytest.mark.asyncio
async def test_ambiguous_digit_defers_to_an_open_customer_prompt(world):
    """A bare digit is a pro command *and* a customer menu pick. While a
    customer-side question is open it belongs to the customer side."""
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.booked_job(pro, chat_id=pro_chat)
    await world.set_state(UserStates.AWAITING_RESCHEDULE_TIME, chat_id=pro_chat)
    await world.set_metadata(
        {
            "reschedule_slots_context": {
                "1": str(
                    (
                        await world.db.slots.find_one(
                            {"pro_id": pro["_id"], "is_taken": False}
                        )
                    )["_id"]
                )
            }
        },
        chat_id=pro_chat,
    )
    world.recorder.clear()

    await world.send("1", chat_id=pro_chat)

    # It was read as a slot pick, not as "approve".
    assert (await world.lead_by_id(lead["_id"]))["rescheduled_count"] == 1
    world.recorder.assert_text_to(pro_chat, "המועד שונה בהצלחה")


# ===========================================================================
# 7. Pro onboarding and the admin wizard
# ===========================================================================


@pytest.mark.asyncio
async def test_pro_self_service_onboarding(world):
    await world.grant_consent(R.chat(R.PRO_THIRD))
    applicant = R.chat(R.PRO_THIRD)

    await world.send("הרשמה", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "ברוכים הבאים להרשמה")
    await world.assert_state(UserStates.ONBOARDING_NAME, chat_id=applicant)

    await world.send("שרברבות הצפון", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "סוג המקצוע")
    await world.send("1", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "ערים/אזורים")
    await world.send("תל אביב, רמת גן", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "המחירים")
    await world.send("דלג", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "סיכום הפרופיל שלך", "שרברבות הצפון")
    await world.send("אשר", chat_id=applicant)
    world.recorder.assert_text_to(applicant, "הפרופיל שלך נשלח לאישור")

    created = await world.pro_doc(R.PRO_THIRD)
    assert created["type"] == "plumber"
    assert created["service_areas"] == ["תל אביב", "רמת גן"]
    assert created["is_active"] is False
    assert created["pending_approval"] is True
    # Awaiting approval means invisible to routing.
    assert created["location"]["coordinates"] == [34.7818, 32.0853]
    await world.assert_state(UserStates.IDLE, chat_id=applicant)


@pytest.mark.asyncio
async def test_admin_wizard_routes_a_stuck_lead(world):
    await world.standard_cast()
    lead = await world.booked_job(
        world.pros[R.PRO_FAR], status=LeadStatus.PENDING_ADMIN_REVIEW
    )
    await world.db.leads.update_one({"_id": lead["_id"]}, {"$unset": {"pro_id": ""}})
    world.recorder.clear()

    await world.send("ניהול", chat_id=world.admin)
    world.recorder.assert_text_to(world.admin, "לידים הממתינים לטיפול")
    await world.assert_state(UserStates.ADMIN_SELECTING_LEAD, chat_id=world.admin)

    await world.send("1", chat_id=world.admin)
    world.recorder.assert_text_to(world.admin, "למי להעביר")

    await world.send("2", chat_id=world.admin)
    world.recorder.assert_text_to(world.admin, "אנשי מקצוע פנויים")

    await world.send("1", chat_id=world.admin)
    world.recorder.assert_text_to(world.admin, "הליד הועבר")

    updated = await world.lead_by_id(lead["_id"])
    assert updated["status"] == LeadStatus.NEW
    assert updated["pro_id"] is not None
    assert updated["reassignment_count"] == 0
    assert "escalation_reason" not in updated
    await world.assert_state(UserStates.IDLE, chat_id=world.admin)


@pytest.mark.asyncio
async def test_pro_stuck_lead_search_is_rate_limited(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    await world.booked_job(
        world.pros[R.PRO_FAR], status=LeadStatus.PENDING_ADMIN_REVIEW
    )
    world.recorder.clear()

    await world.send("מצא", chat_id=pro_chat)
    world.recorder.assert_text_to(pro_chat, "נמצא ליד תקוע", "נזילה במטבח")
    assert (
        await world.redis.ttl(f"rate_limit:pro_search:{pro_chat}")
        <= WorkerConstants.PRO_SEARCH_RATE_LIMIT_SECONDS
    )

    world.recorder.clear()
    await world.send("מצא", chat_id=pro_chat)
    world.recorder.assert_text_to(pro_chat, "חיפשת לאחרונה", "המתן")


# ===========================================================================
# 8. Breaker, rate limits, consent
# ===========================================================================


@pytest.mark.asyncio
async def test_engaged_circuit_breaker_blocks_every_outbound(world):
    """PRO-71/PRO-82. This assertion is only meaningful because PRO-83 moved the
    dry-run divergence below the breaker — before that, a dry run returned first
    and the breaker was never consulted."""
    await world.standard_cast()
    await world.redis.set("wa:instance:paused", "notAuthorized", ex=360)
    world.ai.script(reply("שלום! מה קרה?"))

    await world.send("היי")

    world.recorder.assert_nothing_sent("the outbound breaker is engaged")


@pytest.mark.asyncio
async def test_manual_kill_switch_blocks_every_outbound(world):
    await world.standard_cast()
    await world.redis.set("wa:instance:paused:manual", "1")
    world.ai.script(reply("שלום! מה קרה?"))

    await world.send("היי")

    world.recorder.assert_nothing_sent("the operator kill switch is set")


@pytest.mark.asyncio
async def test_inbound_rate_limit_throttles_gracefully(world):
    """PRO-21. `תודה` short-circuits before the AI, so this exercises the limiter
    itself rather than burning 20 model calls."""
    await world.standard_cast()

    for _ in range(WorkerConstants.INBOUND_RATE_LIMIT_MAX):
        await world.send("תודה")
    world.recorder.assert_text_to(world.customer, "בכיף")

    world.recorder.clear()
    await world.send("תודה")
    world.recorder.assert_text_to(world.customer, "קיבלנו ממך הרבה הודעות ברצף")


@pytest.mark.asyncio
async def test_daily_ai_cap_throttles_gracefully(world):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.core.config import settings

    await world.standard_cast()
    today = datetime.now(ZoneInfo(settings.TIMEZONE)).date().isoformat()
    await world.redis.set(
        f"ai:daily:{world.customer}:{today}", WorkerConstants.DAILY_AI_CALL_CAP
    )

    await world.send("יש לי נזילה")

    world.recorder.assert_text_to(world.customer, "הגעת למכסת הפניות היומית")
    assert world.ai.calls == [], "the model must not be called past the cap"


@pytest.mark.asyncio
async def test_pros_are_exempt_from_the_customer_rate_limit(world):
    await world.standard_cast()
    pro_chat = world.pro_chat(R.PRO_PRIMARY)

    for _ in range(WorkerConstants.INBOUND_RATE_LIMIT_MAX + 3):
        await world.send("תפריט", chat_id=pro_chat)

    world.recorder.assert_never_contains(
        "קיבלנו ממך הרבה הודעות ברצף", "pros are exempt from the inbound limit"
    )


@pytest.mark.asyncio
async def test_consent_is_required_on_first_contact(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה")

    await world.send("היי, יש לי נזילה")
    world.recorder.assert_text_to(world.customer, "ברוכים הבאים ל-Proli", "השב/י *כן*")
    await world.assert_state(UserStates.AWAITING_CONSENT)
    assert await world.lead() is None, "no lead may be created before consent"

    # Unclear reply re-asks rather than proceeding.
    world.recorder.clear()
    await world.send("מה זה?")
    world.recorder.assert_text_to(world.customer, "ברוכים הבאים ל-Proli")
    await world.assert_state(UserStates.AWAITING_CONSENT)

    world.recorder.clear()
    await world.send("כן")
    world.recorder.assert_text_to(world.customer, "אפשר להתחיל")
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_declining_consent_stops_the_flow_and_re_asks_later(world):
    await world.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה")

    await world.send("היי")
    await world.send("לא")
    world.recorder.assert_text_to(world.customer, "לא נשמור מידע עליך")

    world.recorder.clear()
    await world.send("היי שוב")
    world.recorder.assert_text_to(world.customer, "ברוכים הבאים ל-Proli")


# ===========================================================================
# 9. Edge cases
# ===========================================================================


@pytest.mark.asyncio
async def test_incomplete_address_parks_in_awaiting_address_and_recovers(world):
    await world.standard_cast()
    world.ai.script(
        reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה"),
        reply("מה הכתובת המדויקת?"),
        # The close arrives with no floor/apartment → the gate rejects it.
        deal(
            "קבענו!",
            city="תל אביב",
            issue="נזילה",
            street="דיזנגוף",
            street_number="50",
            appointment_time="מחר ב-10:00",
        ),
        # The re-extraction turn fills in what was missing.
        reply(
            "תודה!",
            city="תל אביב",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
        ),
    )

    await world.send("יש לי נזילה בתל אביב")
    world.recorder.clear()
    await world.send("דיזנגוף 50, מחר ב-10")

    await world.assert_state(UserStates.AWAITING_ADDRESS)
    world.recorder.assert_text_to(
        world.customer, "עוד פרטים לכתובת", "קומה", "מספר דירה"
    )
    assert (await world.lead())["status"] == LeadStatus.CONTACTED

    world.recorder.clear()
    await world.send("קומה 3 דירה 12")
    world.recorder.assert_text_to(world.customer, "הכתובת עודכנה בהצלחה")
    assert (await world.lead())["full_address"] == "דיזנגוף 50, תל אביב"
    await world.assert_state(UserStates.IDLE)


@pytest.mark.asyncio
async def test_unknown_city_falls_back_to_service_area_matching(world):
    """A city outside ISRAEL_CITIES_COORDS cannot be geocoded offline, so routing
    must fall through to the service_areas text match rather than escalating."""
    await world.add_pro(
        R.PRO_PRIMARY,
        name="מקומי",
        city="תל אביב",
        service_areas=["ראש העין"],
    )
    await world.grant_consent()
    world.ai.script(
        reply("מאתר איש מקצוע.", city="ראש העין", issue="נזילה"),
        reply("מה הכתובת המדויקת?"),
    )

    await world.send("יש לי נזילה בראש העין")

    lead = await world.lead()
    assert lead["status"] == LeadStatus.CONTACTED
    assert lead["pro_id"] == world.pros[R.PRO_PRIMARY]["_id"]
    world.recorder.assert_text_to(world.pro_chat(R.PRO_PRIMARY), "שיחה בתהליך")


@pytest.mark.asyncio
async def test_deal_marker_never_leaks_to_the_customer(world):
    """PRO-44: the internal marker is a detection signal, not copy."""
    await world.standard_cast()
    world.ai.script(
        reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה"),
        reply(
            "מעולה, קבענו למחר ב-10:00! [DEAL: מחר ב-10 | דיזנגוף 50 | נזילה]",
            city="תל אביב",
            issue="נזילה",
            street="דיזנגוף",
            street_number="50",
            floor="3",
            apartment="12",
            appointment_time="מחר ב-10:00",
        ),
    )

    # One turn: the dispatcher extracts and matches, then the pro persona closes.
    await world.send("יש לי נזילה בתל אביב, דיזנגוף 50 קומה 3 דירה 12, מחר ב-10")

    world.recorder.assert_never_contains("[DEAL:", "the internal marker is not copy")
    world.recorder.assert_text_to(world.customer, "קבענו למחר")
    # The marker still did its job: the deal was finalized.
    assert (await world.lead())["status"] == LeadStatus.NEW


@pytest.mark.asyncio
async def test_reset_mid_flow_clears_state_and_context(world):
    await world.standard_cast()
    world.ai.script(reply("מאתר איש מקצוע.", city="תל אביב", issue="נזילה"))

    await world.send("יש לי נזילה בתל אביב")
    await world.set_state(UserStates.AWAITING_ADDRESS)
    world.recorder.clear()

    await world.send("התחלה")

    # The reset is deliberately silent (operator decision, 2026-08-27):
    # state and context are cleared with no confirmation message.
    world.recorder.assert_silent(world.customer, "reset sends no confirmation")
    await world.assert_state(UserStates.IDLE)
    assert await world.redis.llen(f"context:{world.customer}") == 0


@pytest.mark.asyncio
async def test_duplicate_webhook_is_processed_once(world):
    """Idempotency lives in the webhook route, so this drives the real HTTP path."""
    await world.standard_cast()
    world.ai.script(reply("שלום! מה קרה?"))

    first = await world.deliver("היי", id_message="dup-1")
    assert first["status"] == "processing_message"
    sends_after_first = len(world.recorder.sends)

    second = await world.deliver("היי", id_message="dup-1")

    assert second["detail"] == "duplicate"
    assert (
        len(world.recorder.sends) == sends_after_first
    ), "a replayed webhook must not produce a second reply"


@pytest.mark.asyncio
async def test_out_of_order_webhooks_do_not_corrupt_the_lead(world):
    """The address arrives before the problem description. The sticky-facts merge
    must keep both rather than letting the later turn erase the earlier one."""
    await world.standard_cast()
    world.ai.script(
        reply("מה קרה?", city="תל אביב", customer_name="דנה"),
        reply("הבנתי.", city="תל אביב", issue="נזילה מתחת לכיור"),
        reply("מה הכתובת?"),
    )

    await world.deliver("אני דנה מתל אביב", id_message="ooo-1")
    await world.deliver("יש לי נזילה מתחת לכיור", id_message="ooo-2")

    lead = await world.lead()
    assert lead["customer_name"] == "דנה"
    assert lead["city"] == "תל אביב"
    assert lead["issue_type"] == "נזילה מתחת לכיור"


@pytest.mark.asyncio
async def test_a_second_message_mid_flight_is_deferred_not_dropped(world):
    """The per-chat Redis lock serializes concurrent ARQ tasks; the loser raises
    ChatLockBusyError so the task wrapper can requeue instead of racing."""
    from app.core.redis_client import ChatLockBusyError, acquire_chat_lock
    from app.services.workflow_service import process_incoming_message

    await world.standard_cast()
    assert await acquire_chat_lock(world.customer, ttl=10) is True

    with pytest.raises(ChatLockBusyError):
        await process_incoming_message(world.customer, "הודעה שנייה")

    world.recorder.assert_nothing_sent("a deferred message is requeued, not answered")


# ===========================================================================
# 10. Defects this harness found (see the PR for PRO-83)
# ===========================================================================


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT found by this harness: the reverse-match fallback in "
        "matching_service.determine_best_pro (matching_service.py:117) queries "
        "{is_active, role} directly instead of spreading base_filter, so both "
        "excluded_pro_ids and the pending_approval guard are dropped on that branch. "
        "A pro who just timed out on a lead can be handed the same lead straight "
        "back — an escalation loop — and a pro still awaiting admin approval can be "
        "routed live work."
    ),
)
@pytest.mark.asyncio
async def test_text_fallback_routing_still_honours_exclusions(world):
    """The regex branch misses on a full address, so the reverse match runs — which
    is the production shape: `full_address` is "הרצל 5, ראש העין", not a bare city."""
    from app.services.matching_service import determine_best_pro

    primary = await world.add_pro(
        R.PRO_PRIMARY, name="ראשון", service_areas=["ראש העין"], rating=4.9
    )
    secondary = await world.add_pro(
        R.PRO_SECONDARY, name="שני", service_areas=["ראש העין"], rating=4.5
    )
    await world.add_pro(
        R.PRO_UNAPPROVED,
        name="ממתין לאישור",
        service_areas=["ראש העין"],
        rating=5.0,
        pending_approval=True,
    )

    chosen = await determine_best_pro(
        issue_type="נזילה",
        location="הרצל 5, ראש העין",
        excluded_pro_ids=[str(primary["_id"])],
    )

    assert chosen is not None
    assert (
        chosen["_id"] == secondary["_id"]
    ), "an excluded pro (or one pending approval) was routed the lead"


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT found by this harness: PRO_SELECTING_JOB_TO_FINISH is unreachable "
        "through the orchestrator. workflow_service's PRO_BUSINESS_KEYWORDS bypass "
        "(line ~686) sees the bare digit, overwrites the state to PRO_MODE, and only "
        "then calls pro_flow — which re-reads the state and no longer sees the "
        "selection state. So '1' runs _handle_approve instead of picking job 1."
    ),
)
@pytest.mark.asyncio
async def test_pro_can_select_which_job_to_finish(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    first = await world.booked_job(pro)
    await world.booked_job(pro, chat_id=R.chat(R.CUSTOMER_B))

    await world.send("סיימתי", chat_id=pro_chat)
    await world.assert_state(UserStates.PRO_SELECTING_JOB_TO_FINISH, chat_id=pro_chat)

    await world.send("1", chat_id=pro_chat)

    assert (await world.lead_by_id(first["_id"]))["status"] == LeadStatus.COMPLETED


@pytest.mark.xfail(
    strict=True,
    reason=(
        "DEFECT found by this harness: PRO_AWAITING_FINAL_PRICE is not in "
        "workflow_service's dispatch. Only PRO_MODE routes to pro_flow, so the pro's "
        "price reply falls through to the *customer* dispatcher — it burns a Gemini "
        "call, answers a pro as if they were a customer, and PRO-33's final_price / "
        "commission_amount can never be captured in production."
    ),
)
@pytest.mark.asyncio
async def test_pro_final_price_is_recorded(world):
    await world.standard_cast()
    pro = world.pros[R.PRO_PRIMARY]
    pro_chat = world.pro_chat(R.PRO_PRIMARY)
    lead = await world.booked_job(pro)

    await world.send("סיימתי", chat_id=pro_chat)
    await world.assert_state(UserStates.PRO_AWAITING_FINAL_PRICE, chat_id=pro_chat)

    await world.send("450", chat_id=pro_chat)

    updated = await world.lead_by_id(lead["_id"])
    assert updated["final_price"] == 450
    assert updated["commission_amount"] == pytest.approx(
        450 * WorkerConstants.COMMISSION_RATE
    )
