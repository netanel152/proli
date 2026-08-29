import time
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from app.core.database import leads_collection, users_collection
from app.core.constants import (
    LeadStatus,
    UserStates,
    WorkerConstants,
    Defaults,
    Actor,
    ISRAEL_CITIES_COORDS,
)
from app.core.phone import to_chat_id, to_local_phone
from app.services.lead_manager_service import set_lead_status
from app.core.logger import logger, page_critical
from app.core.redis_client import get_redis_client
from app.core.datetime_utils import within_business_hours
from app.providers.whatsapp import get_whatsapp, record_account_state
from app.providers.whatsapp.facade import _PAUSE_KEY
from app.services import matching_service
from app.services.notification_service import (
    send_oncall_alert,
    notify_pro_new_lead,
    page_operator,
)
from app.core.messages import Messages
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from bson import ObjectId

whatsapp = get_whatsapp()


def page_safe_city(lead: Mapping[str, Any]) -> str:
    """City-only location context for an operator page (PRO-173).

    Operator pages mask the customer's phone to its last 4 digits; putting
    ``full_address`` into the same Sentry event undid that one field later. A
    street address is at least as identifying as the number just redacted, and
    ``page_critical``'s scrubbers know how to recognise phone numbers and
    secrets, not Hebrew street names. Every page already carries ``lead=<id>``,
    which is the admin-panel lookup key the docstrings promise; the city is
    genuine triage context (it says which pro pool is short), so it is the only
    part of the location worth keeping.

    **The output is closed**: an ``ISRAEL_CITIES_COORDS`` key or the literal
    "unknown city", never a value copied out of the lead. ``city`` is not
    trusted over ``full_address`` — the two are the same AI parse of the same
    customer message (``ai_engine_service.ExtractedData.city`` is a plain
    ``Optional[str]`` with no vocabulary check), and ``workflow_service``'s
    sticky-facts fallback copies ``full_address`` straight into ``city`` on a
    turn that extracts no city, so a composed street address genuinely reaches
    that field. Both go through the allowlist instead.

    The known cost: the dict is far smaller than the list of Israeli cities
    (which is why ``geocoding_service`` exists), so a real city sometimes
    degrades to "unknown city". That is the right way to fail here — the
    ``lead=<id>`` in the page is the actual lookup key.

    Matching prefers the **rightmost** allowlist name, since a Hebrew address
    puts the city last and Israeli streets are routinely named after other
    cities ("רחוב באר שבע 3, חולון" is in חולון). Longest wins the tie, so
    "תל אביב יפו" beats "תל אביב". ``isinstance`` rather than ``or ""``:
    the Reporter calls this between claiming a lead and paging about it, a
    window in which nothing may raise (see ``send_periodic_admin_report``), so
    a legacy non-string field must degrade rather than throw.
    """
    for raw in (lead.get("city"), lead.get("full_address")):
        # casefold both sides so the dict's lowercase Latin keys can match a
        # Latin-script address; a no-op on Hebrew, which is the normal case.
        text = (raw if isinstance(raw, str) else "").casefold()
        matches = [name for name in ISRAEL_CITIES_COORDS if name.casefold() in text]
        if matches:
            return max(
                matches, key=lambda name: (text.rfind(name.casefold()), len(name))
            )
    return "unknown city"


async def _alert_admin_lead_escalated(lead, attempts: int) -> None:
    """Page the admin the moment a lead exhausts its reassignments (PRO-63).

    The customer copy commits to a callback within the hour, but the only other
    admin notification for ``PENDING_ADMIN_REVIEW`` leads is the 4-hourly batched
    Reporter — too slow to honour that promise. This fires immediately; the
    Reporter stays the safety net.

    PRO-88 moved this off WhatsApp. The admin never messages the bot, so their
    Cloud API service window is permanently closed and this alert would have
    needed its own approved template to keep working. It now pages via
    ``page_critical`` → Sentry → email, the channel PRO-75 already made the
    guaranteed one.

    Still best-effort: an alert failure must never abort the escalation, so
    exceptions are swallowed after logging. The phone is masked to its last 4
    digits and the location is narrowed to the city (PRO-173) — the operator
    opens the lead in the admin panel for the rest, rather than the full number
    or a street address being retained in a Sentry event.
    """
    try:
        local_phone = to_local_phone(lead.get("chat_id")) or ""
        page_operator(
            f"Lead escalated to PENDING_ADMIN_REVIEW after {attempts} failed "
            f"reassignments — customer ***{local_phone[-4:]}, "
            f"issue={lead.get('issue_type') or 'unknown'}, "
            # PRO-173: city only, never `full_address`. Masking the phone and
            # then paging the customer's street address in the same event
            # undoes the masking; `page_safe_city` cannot emit free-form text.
            f"city={page_safe_city(lead)}, "
            f"lead={lead.get('_id')}. Customer was promised a callback within "
            "the hour."
        )
        logger.info(f"📣 [Reassign] Admin paged for escalated lead {lead.get('_id')}")
    except Exception as e:
        logger.error(
            f"Failed to alert admin about escalated lead {lead.get('_id')}: {e}. "
            "Falling back to the periodic stuck-lead report."
        )


async def reassign_lead(lead, notify_old_pro: bool = True) -> bool:
    """Reassign one lead to the next-best pro, excluding its current pro.

    Notifies the customer, the new pro, and the old pro; escalates to
    ``PENDING_ADMIN_REVIEW`` both when ``MAX_REASSIGNMENTS`` is exhausted
    (PRO-63 — a human takes over rather than the lead being closed) and when no
    replacement exists. Resets the approval-SLA clock (``pro_notified_at`` +
    flags) for the new pro so PRO-56 re-arms. Returns True iff a new pro was
    assigned.

    Shared by the SOS Healer (60-min stale sweep), the PRO-56 approval-SLA
    reassignment offer (customer chose "find someone else"), and the PRO-117
    pro-reject handoff. ``notify_old_pro=False`` skips the ``PRO_LOST_LEAD``
    message ("הועברה עקב חוסר מענה") — wrong for a pro who explicitly rejected
    and already got the reject acknowledgement.
    """
    lead_id = lead["_id"]
    chat_id = lead["chat_id"]
    current_pro_id = lead.get("pro_id")
    reassignment_count = lead.get("reassignment_count", 0)

    # Hard stop, checked FIRST. PRO-63 — hand the lead to a human instead of
    # closing it. This customer has been failed MAX_REASSIGNMENTS times;
    # auto-CLOSED made our worst experience "the bot gave up", with no path back.
    # PENDING_ADMIN_REVIEW keeps the lead alive and actionable (admin `ניהול`
    # wizard, admin panel, the pro `מצא` search). CLOSED stays reserved for an
    # explicit human give-up and the janitor's never-assigned sweep.
    #
    # This runs before CUSTOMER_REASSIGNING and before the geo query on purpose:
    # telling an exhausted customer "finding you another pro" and then
    # immediately "I couldn't find one" is the exact whiplash this ticket exists
    # to remove, and the matching round would be discarded anyway.
    if reassignment_count >= WorkerConstants.MAX_REASSIGNMENTS:
        # Idempotency guard. A lead already sitting in PENDING_ADMIN_REVIEW for
        # this reason must not be re-escalated: `reassignment_count` is still at
        # the max after a human re-assigns, so without this a re-entry would
        # yank the lead back off the pro, re-promise the customer a callback,
        # and page the admin again on every pass.
        if lead.get("escalation_reason") == "max_reassignments_exhausted":
            logger.info(
                f"⏭️ [Reassign] Lead {lead_id} already escalated for exhausted "
                "reassignments — not re-escalating."
            )
            return False

        # Race-safe: two callers can reach this concurrently (the Healer sweep
        # and the PRO-56 "1" reply land in different processes). Guarding on the
        # status we read means the loser gets None and skips the customer message
        # and the admin page instead of duplicating both.
        escalated = await set_lead_status(
            lead_id,
            LeadStatus.PENDING_ADMIN_REVIEW,
            Actor.SYSTEM,
            extra_set={"escalation_reason": "max_reassignments_exhausted"},
            expected_status=lead.get("status"),
        )
        if not escalated:
            logger.info(
                f"⏭️ [Reassign] Lead {lead_id} escalated by a concurrent caller — "
                "skipping duplicate notifications."
            )
            return False

        try:
            await whatsapp.send_message(chat_id, Messages.SOS.MAX_REASSIGNMENTS_REACHED)
        except Exception as e:
            logger.error(
                f"Failed to notify customer ...{chat_id[-8:]} of escalation: {e}"
            )
        # The customer message promises a callback within the hour; the batched
        # Reporter only runs every 4h, so page the admin now. Best-effort — a
        # failed alert must not abort the escalation (the lead is already in
        # PENDING_ADMIN_REVIEW, and the Reporter remains the safety net).
        await _alert_admin_lead_escalated(lead, reassignment_count)
        # Release the customer's FSM state (not just context) — this branch is
        # reachable from the PRO-56 "1" reply, so a customer whose lead just
        # escalated must not stay parked in AWAITING_PRO_APPROVAL.
        await StateManager.clear_state(chat_id)
        await ContextManager.clear_context(chat_id)
        logger.warning(
            f"🚨 [Reassign] Lead {lead_id} escalated to PENDING_ADMIN_REVIEW after "
            f"{reassignment_count} reassignments."
        )
        return False

    # Skip leads without a real, usable location — geo matching would always fail
    # and escalate to PENDING_ADMIN_REVIEW, burning a CUSTOMER_REASSIGNING notice
    # each cycle. Hand to the admin directly.
    raw_location = lead.get("full_address")
    if not raw_location or raw_location == Defaults.UNKNOWN_ADDRESS:
        logger.info(
            f"⏭️ [Reassign] Skipping lead {lead_id} for ...{chat_id[-8:]} — no usable "
            f"location (full_address={raw_location!r}). Escalating to PENDING_ADMIN_REVIEW."
        )
        await set_lead_status(
            lead_id,
            LeadStatus.PENDING_ADMIN_REVIEW,
            Actor.SYSTEM,
            extra_set={"escalation_reason": "no_usable_location"},
        )
        # PRO-117: this branch used to escalate silently — the customer's state
        # was cleared with no message, the exact "ghosting" the other two
        # escalation branches already avoid. Fail-open like them.
        try:
            await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
        except Exception as e:
            logger.error(
                f"Failed to notify customer ...{chat_id[-8:]} of pending review: {e}"
            )
        await StateManager.clear_state(chat_id)
        await ContextManager.clear_context(chat_id)
        return False

    # 1. Notify customer
    try:
        await whatsapp.send_message(chat_id, Messages.SOS.CUSTOMER_REASSIGNING)
    except Exception as e:
        logger.error(f"Failed to notify customer ...{chat_id[-8:]}: {e}")

    # 2. Find replacement — exclude the current pro AND everyone who already
    # explicitly rejected this lead (PRO-117: excluding only the current pro
    # lets the reject chain ping-pong A→B→A within MAX_REASSIGNMENTS).
    excluded_ids = list(lead.get("rejected_by") or [])
    if current_pro_id and current_pro_id not in excluded_ids:
        excluded_ids.append(current_pro_id)
    new_pro = await matching_service.determine_best_pro(
        issue_type=lead.get("issue_type"),
        location=raw_location,
        excluded_pro_ids=excluded_ids,
    )

    if new_pro:
        new_pro_id = new_pro["_id"]

        # 3. Update lead — increment counter, reset timers + PRO-56 SLA clock.
        # expected_status guards against a concurrent reassignment (PRO-117:
        # a pro's דחה and the Healer's 60-min tick can race in different
        # processes) — the loser aborts before notifying a second pro.
        updated = await set_lead_status(
            lead_id,
            LeadStatus.NEW,
            Actor.SYSTEM,
            extra_set={
                "pro_id": new_pro_id,
                "created_at": datetime.now(timezone.utc),
                "pro_notified_at": datetime.now(timezone.utc),
                "approval_nudged": False,
                "reassign_offered": False,
                "reassigned_from": current_pro_id,
                "reassignment_count": reassignment_count + 1,
            },
            # PRO-162: same reasoning as the `created_at` reset above. The lead
            # has a fresh owner, so if it goes stuck *again* that is a new
            # incident and must be able to page the operator immediately rather
            # than inherit the previous incident's 24h mute.
            #
            # PRO-121 rides the same rule: `emergency_hold_acked` throttles the
            # "I've marked it urgent" reply to once per wait, and
            # `emergency_paused_alerted` throttles the paused-for-human operator
            # page. A new pro is a new wait, so both re-arm here — otherwise a
            # customer who declares an emergency against the *replacement* pro
            # gets the generic soft-hold reply and nobody is paged.
            extra_unset={
                "admin_reported_at": "",
                "emergency_hold_acked": "",
                "emergency_paused_alerted": "",
            },
            expected_status=lead.get("status"),
        )
        if updated is None:
            logger.info(
                f"⏭️ [Reassign] Lead {lead_id} changed status under a concurrent "
                "caller — aborting this reassignment."
            )
            return False

        # 4. Notify new pro. PRO-159 made this return honest: False now means
        # the offer genuinely did not reach the pro — closed 24h window with no
        # approved template (the pre-PRO-87 steady state), breaker engaged, or
        # a raised send. In every one of those the pro cannot answer an offer
        # they never saw, so continuing to "נמצא לך איש מקצוע" reports success
        # over a known failure. Treat it as an assignment failure instead:
        # escalate to a human immediately, mirroring the no-location branch
        # above. (The armed SLA would recover in ≤25 min via the customer-side
        # offer, but its pro-facing nudge fails on the very same closed window
        # — a human with a phone is the only actor who can reach the pro.)
        notified = await notify_pro_new_lead(lead, new_pro, whatsapp)
        if not notified:
            logger.error(
                f"⛔ [Reassign] Offer for lead {lead_id} did not reach pro "
                f"{new_pro_id} — escalating to PENDING_ADMIN_REVIEW."
            )
            escalated = await set_lead_status(
                lead_id,
                LeadStatus.PENDING_ADMIN_REVIEW,
                Actor.SYSTEM,
                extra_set={"escalation_reason": "pro_offer_send_failed"},
                expected_status=LeadStatus.NEW,
            )
            if escalated is None:
                # Someone else moved the lead between our write and now —
                # leave it to them rather than fight over it.
                logger.info(
                    f"⏭️ [Reassign] Lead {lead_id} changed status before the "
                    "offer-failure escalation — leaving it to the new owner."
                )
                return False
            page_operator(
                f"Lead {lead_id}: offer send to the reassigned pro failed "
                "(closed 24h window / blocked send) — lead moved to "
                "PENDING_ADMIN_REVIEW, needs manual contact."
            )
            try:
                await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
            except Exception as e:
                logger.error(
                    f"Failed to notify customer ...{chat_id[-8:]} of pending "
                    f"review: {e}"
                )
            await StateManager.clear_state(chat_id)
            await ContextManager.clear_context(chat_id)
            return False

        # 4b. Close the loop for the customer: they were told "מאתרים עבורך איש
        # מקצוע" (CUSTOMER_REASSIGNING) earlier — tell them who was found so the
        # thread doesn't go silent until the new pro engages. Fail-open.
        try:
            await whatsapp.send_message(
                chat_id,
                Messages.Customer.AWAITING_APPROVAL_TRANSPARENT.format(
                    pro_name=new_pro.get("business_name", "איש המקצוע")
                ),
            )
        except Exception as e:
            logger.error(
                f"Failed to notify customer ...{chat_id[-8:]} of reassignment: {e}"
            )

        # 5. Notify old pro
        if notify_old_pro and current_pro_id:
            old_pro = await users_collection.find_one({"_id": current_pro_id})
            if old_pro and old_pro.get("phone_number"):
                old_phone = to_chat_id(old_pro["phone_number"])
                await whatsapp.send_message(old_phone, Messages.SOS.PRO_LOST_LEAD)

        # Re-arm the PRO-56 approval SLA for the NEW pro (PRO-117): the SLA
        # monitor and the 1/2 reassign-offer reply both require the customer to
        # be in AWAITING_PRO_APPROVAL. Clearing state here (the old behavior)
        # silently disarmed the nudge/offer for every reassigned lead, leaving
        # only the 60-min Healer. Mirrors the initial-assignment path in
        # workflow_service, bounded TTL included.
        #
        # Only for leads genuinely in the approval funnel, though: a CONTACTED
        # lead the Healer swept was never finalized — its customer may be
        # mid-conversation (AWAITING_ADDRESS/MEDIA/TIME/CONSENT), and
        # AWAITING_PRO_APPROVAL soft-holds every message they send
        # (STILL_WAITING, before the AI) for PRO_APPROVAL_TTL_SECONDS. Those
        # keep the old clear-state semantics and degrade gracefully to the AI.
        if lead.get("status") == LeadStatus.CONTACTED:
            await StateManager.clear_state(chat_id)
        else:
            await StateManager.set_state(
                chat_id,
                UserStates.AWAITING_PRO_APPROVAL,
                ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
            )
        logger.info(
            f"✅ [Reassign] Lead {lead_id} reassigned from {current_pro_id} to "
            f"{new_pro_id} (attempt {reassignment_count + 1})"
        )
        return True

    # No replacement — escalate to admin review and release the customer.
    logger.warning(
        f"⚠️ [Reassign] Could not find replacement for lead {lead_id} — "
        f"escalating to PENDING_ADMIN_REVIEW."
    )
    await set_lead_status(lead_id, LeadStatus.PENDING_ADMIN_REVIEW, Actor.SYSTEM)
    try:
        await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
    except Exception as e:
        logger.error(
            f"Failed to notify customer ...{chat_id[-8:]} of pending review: {e}"
        )
    await StateManager.clear_state(chat_id)
    await ContextManager.clear_context(chat_id)
    return False


async def check_and_reassign_stale_leads():
    """
    AUTO-RECOVERY ("The Healer"):
    Runs frequently (e.g., every 10 mins).
    Finds stale leads and automatically re-assigns them to a new pro.
    """
    logger.info("🕵️ [SOS Healer] Checking for stale leads to reassign...")

    timeout_minutes = WorkerConstants.SOS_TIMEOUT_MINUTES
    threshold_time = datetime.now(timezone.utc) - timedelta(minutes=timeout_minutes)

    # Patch #3: Exclude PENDING_ADMIN_REVIEW from the Healer query.
    # PENDING_ADMIN_REVIEW is a *terminal* state for the Healer — it means the
    # Healer already gave up on this lead and handed it to a human. Re-running
    # the reassignment flow on it just re-notifies the customer with
    # CUSTOMER_REASSIGNING, re-fails the match, and re-sets the status to
    # PENDING_ADMIN_REVIEW on every 10-minute tick (see logs 2026-04-18).
    query = {
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]},
        "created_at": {"$lt": threshold_time},
    }

    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_leads:
            logger.info("✅ [SOS Healer] No stale leads found.")
            return

        logger.warning(
            f"🕵️ [SOS Healer] Found {len(stale_leads)} stale leads. Attempting reassignment..."
        )

        for lead in stale_leads:
            await reassign_lead(lead)

    except Exception as e:
        logger.error(f"❌ [SOS Healer] Error: {e}")


async def auto_reject_unassigned_leads():
    """
    AUTO-REJECTION ("The Janitor"):
    Finds CONTACTED leads that have no assigned pro_id and are older than
    UNASSIGNED_LEAD_TIMEOUT_HOURS. Closes them and notifies the customer.
    Prevents leads from accumulating forever with no escape path.
    """
    logger.info("🧹 [Janitor] Checking for unassigned stale leads...")

    timeout_hours = WorkerConstants.UNASSIGNED_LEAD_TIMEOUT_HOURS
    threshold_time = datetime.now(timezone.utc) - timedelta(hours=timeout_hours)

    query = {
        "status": LeadStatus.CONTACTED,
        "pro_id": {"$exists": False},
        "created_at": {"$lt": threshold_time},
    }

    try:
        cursor = leads_collection.find(query)
        stale_unassigned = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_unassigned:
            logger.info("✅ [Janitor] No unassigned stale leads found.")
            return

        logger.warning(
            f"🧹 [Janitor] Closing {len(stale_unassigned)} unassigned leads."
        )

        for lead in stale_unassigned:
            lead_id = lead["_id"]
            chat_id = lead.get("chat_id")

            await set_lead_status(
                lead_id,
                LeadStatus.CLOSED,
                Actor.SYSTEM,
                extra_set={"closed_reason": "no_pro_available"},
            )

            if chat_id:
                try:
                    await whatsapp.send_message(chat_id, Messages.SOS.NO_PRO_AVAILABLE)
                except Exception as e:
                    logger.error(f"Failed to notify customer {chat_id} of closure: {e}")
                await ContextManager.clear_context(chat_id)

            logger.info(
                f"🧹 [Janitor] Closed unassigned lead {lead_id} (chat: {chat_id})"
            )

    except Exception as e:
        logger.error(f"❌ [Janitor] Error: {e}")


# A tuple, not a list: this is shared by three call sites here and imported by
# tests, and it encodes to the same BSON array either way.
REPORTABLE_STUCK_STATUSES = (
    LeadStatus.NEW,
    LeadStatus.CONTACTED,
    LeadStatus.PENDING_ADMIN_REVIEW,
)


def stuck_lead_report_due_filter(now_utc: datetime | None = None) -> dict:
    """Mongo sub-filter selecting stuck leads the Reporter may still page about.

    PRO-162. Shared by the Reporter's scheduler query and its per-lead atomic
    claim below so the two can't drift. ``$or … $exists`` rather than a bare
    ``$lt``: a lead nobody has been paged about has no ``admin_reported_at``
    field at all, and ``$lt`` does not match a missing field.

    Deliberately mirrors ``customer_flow.completion_check_due_filter`` — same
    problem (a periodic job that must not re-fire on the same document every
    tick), same shape.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=WorkerConstants.SOS_REPORT_REPAGE_HOURS)
    return {
        "$and": [
            {
                "$or": [
                    {"admin_reported_at": {"$exists": False}},
                    # Explicit rather than implied. Mongo's BSON ordering puts
                    # null below a date, so `$lt: cutoff` would match a null
                    # too — but relying on that makes the filter's correctness
                    # depend on comparison-order trivia that mongomock need not
                    # reproduce. One clause per case, spelled out.
                    {"admin_reported_at": None},
                    {"admin_reported_at": {"$lt": cutoff}},
                ]
            }
        ]
    }


async def send_periodic_admin_report():
    """
    ADMIN REPORTING ("The Reporter"):
    Runs periodically (e.g., every 4 hours).
    Sends a batched summary of leads that are STILL stuck (reassignment failed).

    PRO-162 — pages **once per lead**, not once per tick. Every lead that goes
    into the digest is first claimed with a conditional ``find_one_and_update``
    stamping ``admin_reported_at``; the same due filter lives in the claim
    predicate, so two worker replicas ticking at the same moment cannot both
    page the same lead, and the next tick skips it entirely. A lead still stuck
    ``SOS_REPORT_REPAGE_HOURS`` later becomes claimable again, so one nobody
    ever resolves resurfaces instead of vanishing.

    Before this, a single unresolvable lead paged ``page_critical`` → Sentry →
    operator email every 4 hours indefinitely (Sentry PYTHON-Y fired 20 times
    over 4 days for one staging lead) — the same defect class as PRO-77 at a
    different call site, and how a real page gets missed. ``PENDING_ADMIN_REVIEW``
    stays *in* the query: unlike the Healer, for which it is a terminal state,
    telling the operator a human is needed is precisely the Reporter's job. It
    now says so once rather than forever.
    """
    logger.info("🕵️ [SOS Reporter] Generating admin report...")

    timeout_minutes = WorkerConstants.SOS_TIMEOUT_MINUTES
    now_utc = datetime.now(timezone.utc)
    threshold_time = now_utc - timedelta(minutes=timeout_minutes)

    # Same statuses as the Healer plus PENDING_ADMIN_REVIEW - if they are still
    # here, the Healer failed, no one accepted, or a human owes the lead work.
    query = {
        "status": {"$in": REPORTABLE_STUCK_STATUSES},
        "created_at": {"$lt": threshold_time},
        **stuck_lead_report_due_filter(now_utc),
    }

    try:
        cursor = leads_collection.find(query)
        candidates = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        # Claim each candidate before reporting it. find_one_and_update returns
        # the pre-update document (Mongo's default); a None means another
        # replica ticking concurrently already took this lead, so it is not
        # ours to page about.
        #
        # A stamped lead is muted for SOS_REPORT_REPAGE_HOURS, so between the
        # stamp and page_operator nothing fallible may run: a raise in there
        # would silently mute leads nobody was ever told about — strictly worse
        # than the duplicate paging this fix removes. Hence the per-lead try
        # (one bad claim must not discard the leads already stamped above) and
        # the backlog count moved below the page.
        stuck_leads = []
        for candidate in candidates:
            try:
                claimed = await leads_collection.find_one_and_update(
                    {
                        "_id": candidate["_id"],
                        "status": {"$in": REPORTABLE_STUCK_STATUSES},
                        **stuck_lead_report_due_filter(now_utc),
                    },
                    {"$set": {"admin_reported_at": now_utc}},
                )
            except Exception as e:
                logger.error(
                    f"❌ [SOS Reporter] Claim failed for lead "
                    f"{candidate.get('_id')}: {e}"
                )
                continue
            if claimed:
                stuck_leads.append(claimed)

        count = len(stuck_leads)

        if not stuck_leads:
            logger.info("✅ [SOS Reporter] No newly stuck leads to page about.")
        else:
            logger.warning(f"🕵️ [SOS Reporter] Found {count} newly stuck leads.")

            # PRO-88: paged via Sentry, not WhatsApp. The admin's Cloud API
            # service window is permanently closed, so this batched digest would
            # have needed its own approved template. Phones are masked to their
            # last 4 digits and the location is the city only (PRO-173, which
            # also added the `lead=` id) — this is a "go look at the panel"
            # signal, not a data export, and it now carries the id to look up.
            report_lines = [
                f"{count} lead(s) stuck for more than {timeout_minutes} minutes:"
            ]
            for lead in stuck_leads:
                # isinstance for the same reason as page_safe_city: this is
                # inside the claim→page window, so a legacy non-string chat_id
                # must mask to nothing rather than raise and mute a lead the
                # operator was never told about.
                raw_chat = lead.get("chat_id")
                local = (raw_chat if isinstance(raw_chat, str) else "").split("@")[0]
                created_at = lead.get("created_at")
                # hasattr, not a truthiness check: a legacy or string
                # created_at must not cost the whole batch its page.
                since = (
                    created_at.strftime("%H:%M")
                    if hasattr(created_at, "strftime")
                    else "??"
                )
                report_lines.append(
                    f"- ***{local[-4:]}: {lead.get('issue_type') or 'unknown issue'}"
                    f" in {page_safe_city(lead)}"
                    f" (waiting since {since}, lead={lead.get('_id')})"
                )
            report_lines.append("Open the admin panel to reassign or call.")

            page_operator("\n".join(report_lines))
            logger.info(f"✅ [SOS Reporter] Paged operator about {count} stuck leads.")

        # PRO-162: the standing backlog is state, not news — it goes to the log,
        # never the pager (PRO-46 already puts it on the admin Kanban board).
        # Deliberately after the page and in its own guard: this is a second,
        # independent Mongo round trip, and scheduler jobs hitting transient
        # Mongo failures is a documented condition (PRO-112). It must never be
        # able to mute a lead the operator has not heard about.
        try:
            standing = await leads_collection.count_documents(
                {
                    "status": {"$in": REPORTABLE_STUCK_STATUSES},
                    "created_at": {"$lt": threshold_time},
                }
            )
            if standing > count:
                logger.warning(
                    f"🕵️ [SOS Reporter] {standing - count} stuck lead(s) already "
                    "paged and still open — not re-paged (see admin panel)."
                )
        except Exception as e:
            logger.warning(f"[SOS Reporter] standing-backlog count failed: {e}")

    except Exception as e:
        logger.error(f"❌ [SOS Reporter] Error: {e}")


async def check_whatsapp_instance_state():
    """PRO-20 — WhatsApp account deauth watchdog (SPOF protection).

    Polls getStateInstance. The WhatsApp instance is a single point of failure:
    if it loses authorization (phone offline, ban, session drop) no customer or
    pro message is processed, silently. This pages the on-call operator once the
    instance has been non-authorized for longer than the threshold.

    Redis-backed so the alert survives across the short polling interval without
    flapping or re-paging every tick:
      * ``wa:instance:down_since`` — unix ts of first non-authorized probe.
      * ``wa:instance:alerted``    — set once we actually page; gates the
        recovery notice so a brief blip that never crossed the threshold stays
        quiet.
      * ``wa:instance:last_alert`` — TTL dedup so we re-page at most once per
        WA_STATE_REALERT_MINUTES while the instance stays down.

    Fail-open: any Redis error degrades to a single log line and returns —
    a monitoring job must never take down the worker.
    """
    # PRO-86: a provider that cannot transmit has no account to watch, and its
    # synthetic "authorized" must never reach the breaker, the deauth clock or the
    # recovery notice. Skipping outright is the honest behaviour: under
    # WHATSAPP_DRY_RUN there is genuinely nothing to page about, and pretending
    # otherwise would clear a real incident's pause key and fire a false recovery.
    if not whatsapp.provider.transmits:
        logger.debug(
            f"[WA Monitor] provider {whatsapp.provider.name!r} cannot transmit — "
            "skipping tick."
        )
        return

    state = await whatsapp.get_state_instance()
    is_authorized = state == "authorized"

    # PRO-82/PRO-86: publish the *positive* confirmation the outbound facade
    # fails closed without. Written before any of the paging bookkeeping below
    # so a Redis hiccup in the alerting path can never leave the breaker relying
    # on a probe that did succeed.
    await record_account_state(state, transmits=whatsapp.provider.transmits)

    try:
        redis = await get_redis_client()
    except Exception as e:
        logger.warning(f"[WA Monitor] Redis unavailable, skipping tick: {e}")
        return

    DOWN_SINCE_KEY = "wa:instance:down_since"
    ALERTED_KEY = "wa:instance:alerted"
    LAST_ALERT_KEY = "wa:instance:last_alert"
    # PRO-71 outbound circuit breaker. Imported from the facade rather than
    # re-declared, so the two halves of the breaker — the writer here and the
    # reader in app/providers/whatsapp/facade.py — cannot drift apart.
    PAUSED_KEY = _PAUSE_KEY

    try:
        if is_authorized:
            # Recovery path: only announce if we previously paged.
            down_since = await redis.get(DOWN_SINCE_KEY)
            alerted = await redis.get(ALERTED_KEY)
            # Release the auto breaker on recovery. The manual kill switch lives in
            # a separate key (wa:instance:paused:manual) the monitor never touches,
            # so an operator-set halt survives instance recovery.
            await redis.delete(DOWN_SINCE_KEY, ALERTED_KEY, LAST_ALERT_KEY, PAUSED_KEY)
            if down_since and alerted:
                logger.info("✅ [WA Monitor] WhatsApp account recovered (authorized).")
                await send_oncall_alert(
                    Messages.Alerts.WHATSAPP_RECOVERED, assume_authorized=True
                )
            return

        # Non-authorized (or unreachable → state is None).
        now = time.time()
        # Circuit breaker (PRO-71): halt outbound IMMEDIATELY on the first
        # non-authorized tick — before the paging threshold — so we stop feeding
        # messages into a filtering/blocked instance. The TTL is a safety net: a
        # live monitor refreshes it every tick; if the monitor dies the breaker
        # auto-releases so outbound is never halted forever.
        await redis.set(
            PAUSED_KEY,
            state or "unreachable",
            ex=WorkerConstants.WA_STATE_PAUSE_TTL_SECONDS,
        )
        down_since_raw = await redis.get(DOWN_SINCE_KEY)
        if not down_since_raw:
            # First detection — start the clock, don't page yet.
            await redis.set(DOWN_SINCE_KEY, str(now), ex=86400)
            logger.warning(
                f"[WA Monitor] WhatsApp account not authorized (state={state}). "
                "Starting deauth timer."
            )
            return

        # Still down — refresh the timer's TTL so a multi-day outage doesn't
        # let down_since expire and silently reset the clock mid-incident.
        await redis.expire(DOWN_SINCE_KEY, 86400)
        downtime_minutes = (now - float(down_since_raw)) / 60
        threshold = WorkerConstants.WA_STATE_ALERT_THRESHOLD_MINUTES
        if downtime_minutes < threshold:
            logger.warning(
                f"[WA Monitor] Instance still not authorized (state={state}) "
                f"for ~{downtime_minutes:.1f}m (< {threshold}m threshold)."
            )
            return

        # Threshold crossed — page, deduped to once per realert window.
        realert_ttl = WorkerConstants.WA_STATE_REALERT_MINUTES * 60
        is_new_alert = await redis.set(LAST_ALERT_KEY, "1", ex=realert_ttl, nx=True)
        if not is_new_alert:
            return  # already paged within the realert window

        await redis.set(ALERTED_KEY, "1", ex=86400)
        # page_critical → forwarded to Sentry as an issue (worker is CRITICAL-only),
        # which is the out-of-band operator page. We deliberately do NOT try to
        # send an on-call alert over WhatsApp here: WhatsApp is the down channel,
        # so paging over it would only amplify the outage (PRO-75). The structured
        # context below (state, downtime, instance) makes the Sentry email actionable.
        # yellowCard is the insidious case: the provider returns 200 and the message
        # is silently filtered (accepted, never delivered). notAuthorized/blocked/
        # unreachable stop processing outright. Branch the text so a paged operator
        # looks in the right place.
        if state == "yellowCard":
            impact = "messages are being silently filtered by WhatsApp (accepted, never delivered)"
        else:
            impact = "no messages are being processed"
        page_critical(
            f"🚨 [WA Monitor] WhatsApp account NON-AUTHORIZED for "
            f"~{downtime_minutes:.0f}m (state={state or 'unreachable'}, "
            f"provider={whatsapp.provider.name}) — {impact}. "
            "Outbound is halted (circuit breaker). Paging on-call via Sentry email."
        )
    except Exception as e:
        logger.error(f"[WA Monitor] Error during instance-state check: {e}")


async def check_pro_approval_sla():
    """PRO-56 — chase a silent pro fast instead of waiting for the 60-min Healer.

    Over leads in NEW with an assigned pro whose customer is still parked in
    AWAITING_PRO_APPROVAL, timed from ``pro_notified_at``:
      * T+APPROVAL_NUDGE_MINUTES → nudge the pro once (``approval_nudged`` flag).
      * T+APPROVAL_REASSIGN_OFFER_MINUTES → offer the customer a reassignment once
        (``reassign_offered`` flag); the 1/2 reply is handled in workflow_service.
    Emergency leads use half the thresholds. Idempotent via the boolean flags.
    """
    logger.info("⏰ [Approval SLA] Checking leads awaiting pro approval...")
    now = datetime.now(timezone.utc)
    try:
        query = {
            "status": LeadStatus.NEW,
            "pro_id": {"$ne": None},
            "pro_notified_at": {"$ne": None},
            "$or": [
                {"approval_nudged": {"$ne": True}},
                {"reassign_offered": {"$ne": True}},
            ],
        }
        leads = await leads_collection.find(query).to_list(
            length=WorkerConstants.DB_QUERY_LIMIT
        )
    except Exception as e:
        logger.error(f"❌ [Approval SLA] Query failed: {e}")
        return

    for lead in leads:
        try:
            chat_id = lead["chat_id"]
            # Only act while the customer is genuinely waiting for approval.
            if (
                await StateManager.get_state(chat_id)
                != UserStates.AWAITING_PRO_APPROVAL
            ):
                continue

            notified_at = lead.get("pro_notified_at")
            if not notified_at:
                continue
            # Mongo hands datetimes back tz-naive; make it UTC-aware before the
            # subtraction (matches the guard used across the codebase). Without
            # this the arithmetic raises and the per-lead except swallows it —
            # the whole feature would silently never fire.
            if notified_at.tzinfo is None:
                notified_at = notified_at.replace(tzinfo=timezone.utc)
            waited_min = (now - notified_at).total_seconds() / 60

            nudge_after = WorkerConstants.APPROVAL_NUDGE_MINUTES
            offer_after = WorkerConstants.APPROVAL_REASSIGN_OFFER_MINUTES
            if lead.get("is_emergency"):
                nudge_after //= 2  # 5 min
                offer_after //= 2  # 12 min

            # T+offer: reassignment offer to the customer (once). Claim the flag
            # atomically (gated on status=NEW + not-yet-offered) BEFORE sending, so
            # overlapping ticks — or a Redis-down scheduler lock that fails open —
            # can't double-send. NOTE: quiet-hours / Shabbat gating of this
            # customer-facing message is deferred to PRO-73.
            # PRO-73: the customer-facing offer is gated to business hours — never
            # message a customer at 3am. Outside hours we skip (reassign_offered
            # stays False) so it fires on the next in-hours tick. The pro nudge
            # below is pro-facing and stays ungated.
            if (
                waited_min >= offer_after
                and not lead.get("reassign_offered")
                and within_business_hours()
            ):
                claimed = await leads_collection.update_one(
                    {
                        "_id": lead["_id"],
                        "status": LeadStatus.NEW,
                        "reassign_offered": {"$ne": True},
                    },
                    {"$set": {"reassign_offered": True}},
                )
                if claimed.modified_count == 1:
                    await whatsapp.send_message(
                        chat_id, Messages.Customer.REASSIGN_OFFER
                    )
                    logger.info(
                        f"⏰ [Approval SLA] Offered reassignment to ...{chat_id[-8:]} "
                        f"after ~{waited_min:.0f}m."
                    )
                continue  # don't also nudge on the same tick

            # T+nudge: nudge the silent pro (once) — same atomic-claim pattern.
            if waited_min >= nudge_after and not lead.get("approval_nudged"):
                claimed = await leads_collection.update_one(
                    {
                        "_id": lead["_id"],
                        "status": LeadStatus.NEW,
                        "approval_nudged": {"$ne": True},
                    },
                    {"$set": {"approval_nudged": True}},
                )
                if claimed.modified_count == 1:
                    pro = await users_collection.find_one({"_id": lead.get("pro_id")})
                    if pro and pro.get("phone_number"):
                        pro_phone = to_chat_id(pro["phone_number"])
                        await whatsapp.send_message(
                            pro_phone,
                            Messages.Pro.APPROVAL_NUDGE.format(minutes=nudge_after),
                        )
                    logger.info(
                        f"⏰ [Approval SLA] Nudged pro for lead {lead['_id']} "
                        f"after ~{waited_min:.0f}m."
                    )
        except Exception as e:
            logger.error(f"❌ [Approval SLA] Error on lead {lead.get('_id')}: {e}")


async def check_sla_deflection():
    """
    SLA MONITOR:
    Finds leads where bot is paused for human, checks if it's been silent for 15 mins.
    """
    logger.info("🕵️ [SLA Monitor] Checking for silent human handoffs...")

    # We use WorkerConstants.PAUSE_TTL_SECONDS as the inactivity threshold (currently 900s / 15m)
    threshold_time = datetime.now(timezone.utc) - timedelta(
        seconds=WorkerConstants.PAUSE_TTL_SECONDS
    )

    # Find leads that are paused and haven't had activity for the threshold time
    query = {
        "is_paused": True,
        "paused_at": {"$lt": threshold_time},
        "status": {"$in": [LeadStatus.NEW, LeadStatus.BOOKED]},
    }

    try:
        cursor = leads_collection.find(query)
        paused_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not paused_leads:
            logger.info("✅ [SLA Monitor] No silent handoffs found.")
            return

        for lead in paused_leads:
            chat_id = lead["chat_id"]

            # Double check with Redis state
            state = await StateManager.get_state(chat_id)
            if state != UserStates.PAUSED_FOR_HUMAN:
                # State already cleared or changed, just cleanup the DB flag
                await leads_collection.update_one(
                    {"_id": lead["_id"]}, {"$set": {"is_paused": False}}
                )
                continue

            # It's been 15 mins of silence. Trigger deflection.
            logger.warning(
                f"⏰ [SLA Monitor] SLA exceeded for {chat_id}. Deflecting to phone check."
            )

            # 1. Clear state
            await StateManager.clear_state(chat_id)

            # 2. Update lead doc
            await leads_collection.update_one(
                {"_id": lead["_id"]},
                {"$set": {"is_paused": False, "sla_deflected": True}},
            )

            # 3. Send Deflection Message
            await whatsapp.send_message(
                chat_id, Messages.Customer.SLA_DEFLECTION_MESSAGE
            )

            logger.info(
                f"✅ [SLA Monitor] Deflected customer {chat_id} after inactivity."
            )

    except Exception as e:
        logger.error(f"❌ [SLA Monitor] Error: {e}")


async def remind_stale_booked_leads():
    """
    STALE LEAD NUDGER:
    Finds leads in BOOKED status that are older than STALE_BOOKED_LEAD_HOURS.
    Sends a reminder to the pro to close the job, preventing MAX_PRO_LOAD issues.
    """
    logger.info("⏰ [Stale Lead Nudger] Checking for stale booked leads...")

    threshold_time = datetime.now(timezone.utc) - timedelta(
        hours=WorkerConstants.STALE_BOOKED_LEAD_HOURS
    )

    # Query for BOOKED leads older than 24 hours with reminders < max
    # We use $and to ensure both the time threshold and the reminder count are checked
    query = {
        "status": LeadStatus.BOOKED,
        "$and": [
            {
                "$or": [
                    {"appointment_datetime": {"$lt": threshold_time}},
                    {"updated_at": {"$lt": threshold_time}},
                ]
            },
            {
                "$or": [
                    {"reminders_sent": {"$exists": False}},
                    {"reminders_sent": {"$lt": WorkerConstants.MAX_PRO_REMINDERS}},
                ]
            },
        ],
    }

    try:
        cursor = leads_collection.find(query)
        stale_leads = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        if not stale_leads:
            logger.info("✅ [Stale Lead Nudger] No stale booked leads found.")
            return

        logger.warning(
            f"⏰ [Stale Lead Nudger] Found {len(stale_leads)} stale leads. Sending reminders..."
        )

        for lead in stale_leads:
            lead_id = lead["_id"]
            pro_id = lead.get("pro_id")
            customer_name = lead.get("customer_name") or "לקוח"

            if not pro_id:
                continue

            pro = await users_collection.find_one({"_id": pro_id})
            if not pro or not pro.get("phone_number"):
                continue

            pro_name = pro.get("business_name") or pro.get("name") or "איש מקצוע"
            pro_phone = pro["phone_number"]
            pro_phone = to_chat_id(pro_phone)

            # Send Message
            message = Messages.Pro.STALE_LEAD_REMINDER.format(
                pro_name=pro_name, customer_name=customer_name
            )

            try:
                await whatsapp.send_message(pro_phone, message)

                # Update lead
                await leads_collection.update_one(
                    {"_id": lead_id},
                    {
                        "$inc": {"reminders_sent": 1},
                        "$set": {"last_reminder_at": datetime.now(timezone.utc)},
                    },
                )
                logger.info(
                    f"✅ [Stale Lead Nudger] Sent reminder to pro {pro_id} for lead {lead_id}"
                )
            except Exception as e:
                logger.error(
                    f"❌ [Stale Lead Nudger] Failed to send reminder to {pro_phone}: {e}"
                )

    except Exception as e:
        logger.error(f"❌ [Stale Lead Nudger] Error: {e}")
