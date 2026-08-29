from app.core.database import (
    users_collection,
    leads_collection,
    reviews_collection,
    slots_collection,
)
from app.core.logger import logger
from app.core.messages import Messages
from app.core.text_matching import contains_keyword
from app.core.constants import LeadStatus, Defaults, Actor, WorkerConstants
from app.core.phone import to_chat_id
from app.services.lead_manager_service import set_lead_status
from app.services.context_manager_service import ContextManager
from app.services.state_manager_service import StateManager
from bson import ObjectId
from datetime import datetime, timedelta, timezone
import pytz
import re

_IL_TZ = pytz.timezone("Asia/Jerusalem")


def completion_check_due_filter(now_utc: datetime | None = None) -> dict:
    """Mongo sub-filter selecting BOOKED leads that may still be nudged.

    Shared by the Tier-2 scheduler query and the atomic claim below so the two
    can't drift. `$or … $exists` rather than a bare `$lt`: a lead that has never
    been nudged has neither field, and `$lt` does not match a missing field.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(
        hours=WorkerConstants.CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS
    )
    return {
        "$and": [
            {
                "$or": [
                    {"completion_check_sent_count": {"$exists": False}},
                    {
                        "completion_check_sent_count": {
                            "$lt": WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS
                        }
                    },
                ]
            },
            {
                "$or": [
                    {"completion_check_sent_at": {"$exists": False}},
                    {"completion_check_sent_at": {"$lt": cutoff}},
                ]
            },
        ]
    }


async def send_customer_completion_check(
    lead_id: str, whatsapp, triggered_by: str = "auto"
):
    """Asks the customer if the job has been completed (numeric reply, PRO-86 text-only).

    The send is *claimed* first, with a single conditional `find_one_and_update`:
    the cap and the cooldown live in the update filter, so two worker replicas
    ticking at the same moment can never both win the claim. Mirrors the cap
    `send_pro_reminder` has always had on the professional side.

    An operator-triggered send (`triggered_by != "auto"`) bypasses the cap and
    the cooldown — it is a deliberate human action — but still stamps the lead,
    so the scheduler waits a full cooldown before piling on top of it.
    """
    try:
        oid = ObjectId(lead_id)
        now_utc = datetime.now(timezone.utc)
        is_auto = triggered_by == "auto"

        claim_filter = {"_id": oid, "status": LeadStatus.BOOKED}
        if is_auto:
            claim_filter.update(completion_check_due_filter(now_utc))

        # Returns the pre-update document (Mongo's default), so
        # completion_check_sent_count here is the count *before* this send.
        lead = await leads_collection.find_one_and_update(
            claim_filter,
            {
                "$inc": {"completion_check_sent_count": 1},
                "$set": {"completion_check_sent_at": now_utc},
            },
        )

        if not lead:
            existing = await leads_collection.find_one({"_id": oid})
            if not existing or existing.get("status") != LeadStatus.BOOKED:
                logger.warning(
                    f"send_customer_completion_check called for invalid/non-booked lead: {lead_id}"
                )
            else:
                logger.info(
                    f"[CompletionCheck] Lead {lead_id} suppressed — already sent "
                    f"{existing.get('completion_check_sent_count', 0)}/"
                    f"{WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS}, last at "
                    f"{existing.get('completion_check_sent_at')} (cooldown "
                    f"{WorkerConstants.CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS}h)."
                )
            return

        customer_chat_id = lead["chat_id"]
        pro = await users_collection.find_one({"_id": lead["pro_id"]})
        pro_name = (
            pro.get("business_name", Defaults.GENERIC_PRO_NAME)
            if pro
            else Defaults.GENERIC_PRO_NAME
        )

        await whatsapp.send_message(
            customer_chat_id,
            Messages.Customer.COMPLETION_CHECK.format(pro_name=pro_name),
        )
        sent_no = lead.get("completion_check_sent_count", 0) + 1
        logger.success(
            f"Sent customer completion check for lead {lead_id} "
            f"({sent_no}/{WorkerConstants.MAX_CUSTOMER_COMPLETION_CHECKS}, "
            f"Trigger: {triggered_by})"
        )
    except Exception as e:
        logger.error(f"Error in send_customer_completion_check for lead {lead_id}: {e}")


_NOT_YET_TOKENS = {"2", "עדיין לא", "לא", "עוד לא", "not yet", "no"}


async def _handle_completion_check_decline(
    chat_id: str, stripped: str, normalized: str
):
    """Answer "2 / עדיין לא" to a completion check: acknowledge and push the
    cooldown forward so the customer is not asked again for another full
    cooldown window.

    Deliberately narrow — it only fires when this chat has a BOOKED lead that
    was *actually* nudged (`completion_check_sent_at` present). Without that
    guard a bare "2" typed into any other numeric menu while a job is booked
    would be swallowed here instead of reaching the real handler.
    """
    if normalized not in _NOT_YET_TOKENS and stripped not in _NOT_YET_TOKENS:
        return None

    lead = await leads_collection.find_one_and_update(
        {
            "chat_id": chat_id,
            "status": LeadStatus.BOOKED,
            "completion_check_sent_at": {"$exists": True},
        },
        {"$set": {"completion_check_sent_at": datetime.now(timezone.utc)}},
        sort=[("created_at", -1)],
    )
    if not lead:
        return None

    logger.info(
        f"[CompletionCheck] Customer {chat_id} answered 'not yet' for lead "
        f"{lead['_id']} — cooldown restarted."
    )
    return Messages.Customer.COMPLETION_NOT_YET_ACK


def rating_prompt_open_filter(now_utc: datetime | None = None) -> dict:
    """Mongo sub-filter for a rating prompt that is still live (PRO-122).

    Shared by the deferral guard below and by `handle_customer_rating_text`, so
    the handler that *defers* and the handler that *acts* can never disagree
    about which prompts count. Both writers of `waiting_for_rating`
    (`handle_customer_completion_text` here and `pro_flow._execute_finish`)
    stamp `completed_at` in the same update, so one cutoff covers both.
    """
    now_utc = now_utc or datetime.now(timezone.utc)
    cutoff = now_utc - timedelta(hours=WorkerConstants.RATING_PROMPT_MAX_AGE_HOURS)
    return {"waiting_for_rating": True, "completed_at": {"$gte": cutoff}}


async def _rating_pending(chat_id: str) -> bool:
    """True when this chat still owes an answer to a *live* 1-5 rating question."""
    return bool(
        await leads_collection.find_one(
            {"chat_id": chat_id, **rating_prompt_open_filter()}, {"_id": 1}
        )
    )


async def handle_customer_completion_text(chat_id: str, text: str, whatsapp):
    """Checks if the user confirmed job completion via text."""
    stripped = text.strip()
    normalized = stripped.lower()

    # PRO-122: the completion menu ("1 — כן" / "2 — עדיין לא") shares its digits
    # with the 1-5 rating scale, and the dispatcher runs this handler *before*
    # the rating one. A customer holding both a rating prompt and a separate
    # BOOKED lead would have "1" complete that other job and "2" swallowed as
    # "not yet" — neither of which is what they answered. A bare digit while a
    # rating is pending belongs to the rating question; defer to it. The skip
    # tokens overlap too — `_NOT_YET_TOKENS ∩ SKIP_TOKENS = {"לא", "no"}` — so a
    # customer meaning "I don't want to rate" would otherwise only ever restart
    # some booked lead's cooldown, and could never reach the rating skip at all.
    if (
        stripped in Messages.Keywords.RATING_OPTIONS or is_skip_token(text)
    ) and await _rating_pending(chat_id):
        return None

    yes_tokens = {"1", "כן", "כן הסתיים", "כן, הסתיים", "הסתיים", "yes", "done"}
    is_completion = (
        normalized in yes_tokens
        or Messages.Keywords.CUSTOMER_COMPLETION_INDICATOR in stripped
    )

    if not is_completion:
        return await _handle_completion_check_decline(chat_id, stripped, normalized)

    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED}, sort=[("created_at", -1)]
    )

    if not lead:
        return None

    pro = await users_collection.find_one({"_id": lead["pro_id"]})

    await set_lead_status(
        lead["_id"],
        LeadStatus.COMPLETED,
        Actor.CUSTOMER,
        extra_set={
            "completed_at": datetime.now(timezone.utc),
            "waiting_for_rating": True,
        },
    )
    logger.success(f"✅ Lead {lead['_id']} marked as COMPLETED by customer.")

    # Clear cached context — lead is done, next conversation starts fresh
    await ContextManager.clear_context(chat_id)

    if pro and pro.get("phone_number"):
        pro_chat_id = to_chat_id(pro["phone_number"])
        await whatsapp.send_message(
            pro_chat_id, Messages.Pro.CUSTOMER_REPORTED_COMPLETION
        )

    pro_name = (
        pro.get("business_name", Defaults.EXPERT_NAME) if pro else Defaults.EXPERT_NAME
    )
    return Messages.Customer.COMPLETION_ACK.format(pro_name=pro_name)


# PRO-122: the rating prompt asks for "1-5" but people answer like people —
# "5 כוכבים", "חמש", "5!", "5/5". Every one of those used to return None and
# fall through to the dispatcher which, with the context already cleared on
# completion, greeted the customer afresh and asked their name.
_HEBREW_RATING_WORDS = {
    "אחת": 1,
    "אחד": 1,
    "שתיים": 2,
    "שניים": 2,
    "שתי": 2,
    "שלוש": 3,
    "שלושה": 3,
    "ארבע": 4,
    "ארבעה": 4,
    "חמש": 5,
    "חמישה": 5,
}
# Words that mark a number as a *score* rather than a quantity. Without one of
# these, a digit inside a sentence is left alone: "רחוב הרצל 5", "קומה 2",
# "3 ימים עברו והוא לא חזר" and "5 דקות" are addresses, floors and durations,
# and reading any of them as a rating writes a number the customer never gave
# into the pro's permanent public average — and then persists their *next*
# message as that pro's public review.
_RATING_CONTEXT_WORDS = {
    "כוכב",
    "כוכבים",
    "כוכבי",
    "דירוג",
    "מדרג",
    "ציון",
    "ניקוד",
    "מתוך",
    "נותן",
    "נותנת",
    "מגיע",
    "star",
    "stars",
    "rating",
    "score",
}
# The whole reply is one 1-5 score and nothing else: "5", "5!", "*5*", "5/5".
# Anchored at both ends, so "4.5" and "10" fall through to the re-prompt rather
# than being silently truncated to 4 and 1.
_BARE_RATING_RE = re.compile(r"^[\s*_~\-\"'.]*([1-5])(?:\s*/\s*5)?[\s*_~\-\"'!?.]*$")
_DIGIT_RUN_RE = re.compile(r"\d+")
_STAR_CHARS = "⭐★✩✪✰"
_PUNCT_TO_STRIP = "!?.,;:*\"'()[]־-"


def parse_rating(text: str) -> int | None:
    """Read a 1-5 rating out of a free-text reply, or None if there isn't one.

    Pure and side-effect free so it can be table-tested. Two deliberate refusals,
    both for the same reason — a rating is a *permanent* write to a pro's public
    average, so a wrong guess costs far more than the one message a re-prompt
    costs:

    * sentiment words ("מצוין", "מעולה") are never mapped onto a number; and
    * a digit only counts when the reply is nothing but that digit, or when a
      rating-context word ("כוכבים", "מתוך", "דירוג") marks it as a score.
    """
    if not text:
        return None
    stripped = text.strip()

    bare = _BARE_RATING_RE.match(stripped)
    if bare:
        return int(bare.group(1))

    # "⭐⭐⭐⭐" — stars and nothing else.
    star_count = sum(stripped.count(char) for char in _STAR_CHARS)
    if star_count and not stripped.strip(_STAR_CHARS + " " + _PUNCT_TO_STRIP):
        return star_count if 1 <= star_count <= 5 else None

    words = [word.strip(_PUNCT_TO_STRIP) for word in stripped.lower().split()]
    has_context = any(word in _RATING_CONTEXT_WORDS for word in words)

    in_range = [
        run
        for run in _DIGIT_RUN_RE.findall(stripped)
        if run in Messages.Keywords.RATING_OPTIONS
    ]
    if has_context and in_range:
        # "4 מתוך 5" — the score is the first in-range number, not the scale.
        return int(in_range[0])

    hebrew = next(
        (_HEBREW_RATING_WORDS[word] for word in words if word in _HEBREW_RATING_WORDS),
        None,
    )
    # A Hebrew number word counts on its own ("חמש") or when a context word
    # backs it ("חמישה כוכבים") — but not inside a sentence that merely happens
    # to contain one ("בעוד שלוש שעות").
    if hebrew is not None and (has_context or len(words) == 1):
        return hebrew
    return None


def is_skip_token(text: str) -> bool:
    """True for an explicit opt-out of an optional prompt (PRO-122).

    Exact match after strip/lower, never substring: "לא" declines, but
    "לא היה טוב" is a real (negative) review and must survive.
    """
    if not text:
        return False
    return (
        text.strip().lower().strip(_PUNCT_TO_STRIP).strip()
        in Messages.Keywords.SKIP_TOKENS
    )


async def _release_rating_prompt(lead: dict, reason: str) -> None:
    """Stop waiting for a rating on this lead, without recording one."""
    await leads_collection.update_one(
        {"_id": lead["_id"]}, {"$set": {"waiting_for_rating": False}}
    )
    logger.info(f"[Rating] Lead {lead['_id']} — rating prompt released ({reason}).")


async def _handle_unparsed_rating(
    chat_id: str, lead: dict, text: str, has_media: bool = False
):
    """A rating is pending but the reply isn't a number — skip, re-prompt, or release."""
    # An emergency outranks the closing pleasantries. `is_emergency_detected` is
    # not consulted until *after* this handler runs, so a re-prompt here would
    # stall "הצפה דחוף" for up to MAX_RATING_REPROMPTS messages. Let it through
    # on the first one instead.
    if contains_keyword(text, Messages.Keywords.EMERGENCY_KEYWORDS):
        await _release_rating_prompt(lead, "emergency keyword")
        return None

    # A photo of the damage with a caption must reach the media handler, which
    # runs after this one. Fall through untouched — and don't spend a re-prompt
    # on it either.
    if has_media:
        return None

    if is_skip_token(text):
        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {"$set": {"waiting_for_rating": False, "rating_skipped": True}},
        )
        await ContextManager.clear_context(chat_id)
        logger.info(f"[Rating] Lead {lead['_id']} — customer declined to rate.")
        return Messages.Customer.RATING_SKIPPED

    reprompts = lead.get("rating_reprompt_count", 0)
    if reprompts < WorkerConstants.MAX_RATING_REPROMPTS:
        await leads_collection.update_one(
            {"_id": lead["_id"]}, {"$inc": {"rating_reprompt_count": 1}}
        )
        logger.info(
            f"[Rating] Lead {lead['_id']} — unreadable rating reply, re-prompting "
            f"({reprompts + 1}/{WorkerConstants.MAX_RATING_REPROMPTS})."
        )
        return Messages.Customer.RATING_REPROMPT

    # Cap reached. `waiting_for_rating` never clears on its own, so holding it
    # here would re-prompt this customer forever; release it and let the message
    # reach the dispatcher as it did before this handler existed.
    await _release_rating_prompt(lead, "re-prompt cap reached")
    return None


async def handle_customer_rating_text(chat_id: str, text: str, has_media: bool = False):
    """Checks if the user sent a rating (1-5), tolerating how people actually type it.

    The lead is looked up *before* the text is judged: with no rating pending a
    stray number still falls through untouched, and only a customer who was
    genuinely asked earns a re-prompt. The lookup is newest-first and bounded to
    live prompts, so with two unrated jobs the rating lands on the one the
    customer was actually just asked about rather than on whichever Mongo
    happened to return.
    """
    lead = await leads_collection.find_one(
        {"chat_id": chat_id, **rating_prompt_open_filter()},
        sort=[("completed_at", -1)],
    )

    if not lead:
        return None

    rating = parse_rating(text)
    if rating is None:
        return await _handle_unparsed_rating(chat_id, lead, text, has_media)

    try:
        pro_id = lead["pro_id"]
        pro = await users_collection.find_one({"_id": pro_id})

        if not pro:
            logger.error(f"Pro {pro_id} not found for rating on lead {lead['_id']}")
            return None

        # Compute new rating in Python to avoid $round aggregation pipeline (not supported by all drivers/mocks)
        current_count = pro.get("social_proof", {}).get("review_count") or 0
        current_rating = pro.get("social_proof", {}).get("rating") or 5.0
        new_count = current_count + 1
        new_rating = round((current_rating * current_count + rating) / new_count, 1)

        await users_collection.update_one(
            {"_id": pro_id},
            {
                "$set": {
                    "social_proof.rating": new_rating,
                    "social_proof.review_count": new_count,
                }
            },
        )

        await leads_collection.update_one(
            {"_id": lead["_id"]},
            {
                "$set": {
                    "waiting_for_rating": False,
                    "rating_given": rating,
                    "waiting_for_review_comment": True,
                }
            },
        )

        business_name = pro.get("business_name", Defaults.GENERIC_PRO_NAME)
        logger.success(f"⭐ Rating {rating} saved for {business_name}")
        return Messages.Customer.REVIEW_REQUEST
    except Exception as e:
        logger.error(f"Error handling rating for lead {lead['_id']}: {e}")
        return None


async def handle_customer_review_comment(chat_id: str, text: str):
    """Checks if the user sent a textual review after rating."""
    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "waiting_for_review_comment": True}
    )

    if not lead:
        return None

    # PRO-122: REVIEW_REQUEST is optional but had no skip path, so "לא" /
    # "לא תודה" was persisted verbatim as the pro's public review.
    if is_skip_token(text):
        # The *score* is still theirs and still counts: the admin analytics
        # average is computed over `reviews.rating`, so dropping the row would
        # quietly delete the rating the customer did give. Persist it with an
        # empty comment — `pro_flow._handle_reviews` only ever displays rows
        # with non-empty text, so nothing surfaces the blank.
        if lead.get("pro_id"):
            await reviews_collection.insert_one(
                {
                    "pro_id": lead["pro_id"],
                    "customer_chat_id": chat_id,
                    "rating": lead.get("rating_given", 5),
                    "comment": "",
                    "created_at": datetime.now(timezone.utc),
                }
            )
        await leads_collection.update_one(
            {"_id": lead["_id"]}, {"$set": {"waiting_for_review_comment": False}}
        )
        await ContextManager.clear_context(chat_id)
        logger.info(
            f"📝 Review declined for lead {lead['_id']} — score kept, no comment text."
        )
        return Messages.Customer.REVIEW_DECLINED

    pro_id = lead.get("pro_id")
    if not pro_id:
        logger.warning(
            f"handle_customer_review_comment: lead {lead['_id']} has no pro_id, skipping review"
        )
        return None

    rating_given = lead.get("rating_given", 5)

    review_doc = {
        "pro_id": pro_id,
        "customer_chat_id": chat_id,
        "rating": rating_given,
        "comment": text,
        "created_at": datetime.now(timezone.utc),
    }

    await reviews_collection.insert_one(review_doc)

    await leads_collection.update_one(
        {"_id": lead["_id"]}, {"$set": {"waiting_for_review_comment": False}}
    )

    # Clear context so the next conversation starts fresh
    await ContextManager.clear_context(chat_id)

    logger.success(f"📝 Review comment saved for lead {lead['_id']}")
    return Messages.Customer.REVIEW_SAVED


async def handle_reschedule_selection(chat_id: str, user_text: str, whatsapp) -> None:
    normalized = user_text.strip().lower()

    if contains_keyword(normalized, Messages.Keywords.CANCEL_KEYWORDS):
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Customer.RESCHEDULE_CANCELLED)
        return

    meta = await StateManager.get_metadata(chat_id)
    slots_context = meta.get("reschedule_slots_context", {})
    pick = user_text.strip()

    if pick not in slots_context:
        await whatsapp.send_message(
            chat_id, Messages.Customer.RESCHEDULE_INVALID_CHOICE
        )
        return  # state preserved — let customer retry

    slot_id = ObjectId(slots_context[pick])

    lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": LeadStatus.BOOKED},
        sort=[("created_at", -1)],
    )
    if not lead:
        await StateManager.clear_state(chat_id)
        await whatsapp.send_message(chat_id, Messages.Errors.GENERIC_ERROR)
        return

    # Atomically claim the chosen slot (guards against race conditions)
    chosen_slot = await slots_collection.find_one_and_update(
        {"_id": slot_id, "is_taken": False},
        {"$set": {"is_taken": True}},
    )
    if not chosen_slot:
        await whatsapp.send_message(
            chat_id, Messages.Customer.RESCHEDULE_INVALID_CHOICE
        )
        return  # state preserved — let customer retry

    # Free previously booked slot if we have a reference to it
    old_slot_id = lead.get("booked_slot_id")
    if old_slot_id:
        await slots_collection.update_one(
            {"_id": old_slot_id, "is_taken": True},
            {"$set": {"is_taken": False}},
        )

    old_time = lead.get("appointment_time", "לא ידוע")
    new_time = chosen_slot["start_time"].astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")

    await leads_collection.update_one(
        {"_id": lead["_id"]},
        {
            "$set": {
                "appointment_time": new_time,
                "booked_slot_id": slot_id,
                "rescheduled_at": datetime.now(timezone.utc),
                "rescheduled_count": lead.get("rescheduled_count", 0) + 1,
            }
        },
    )

    await StateManager.clear_state(chat_id)
    await whatsapp.send_message(
        chat_id, Messages.Customer.RESCHEDULE_SUCCESS.format(new_time=new_time)
    )

    pro = await users_collection.find_one({"_id": lead["pro_id"]})
    if pro and pro.get("phone_number"):
        pro_phone = to_chat_id(pro["phone_number"])
        await whatsapp.send_message(
            pro_phone,
            Messages.Pro.CUSTOMER_RESCHEDULED_SUCCESS.format(
                customer_name=lead.get("customer_name") or "הלקוח",
                address=lead.get("full_address") or "לא ידועה",
                old_time=old_time,
                new_time=new_time,
            ),
        )
    logger.success(
        f"📅 Lead {lead['_id']} rescheduled to {new_time} by customer {chat_id}"
    )


def _format_il_datetime(dt) -> str:
    if not dt:
        return "—"
    if dt.tzinfo is None:
        dt = pytz.utc.localize(dt)
    return dt.astimezone(_IL_TZ).strftime("%d/%m/%Y %H:%M")


def _format_status_for_lead(lead: dict, pro: dict | None) -> str:
    status = lead.get("status")
    issue = lead.get("issue_type") or lead.get("issue") or "—"
    appointment = lead.get("appointment_time") or "טרם נקבע"
    pro_name = (pro or {}).get("business_name", "איש מקצוע")
    updated_at = _format_il_datetime(lead.get("updated_at") or lead.get("created_at"))

    mapping = {
        LeadStatus.NEW: Messages.Customer.STATUS_NEW,
        LeadStatus.CONTACTED: Messages.Customer.STATUS_CONTACTED,
        LeadStatus.BOOKED: Messages.Customer.STATUS_BOOKED,
        LeadStatus.PENDING_ADMIN_REVIEW: Messages.Customer.STATUS_PENDING_ADMIN_REVIEW,
        LeadStatus.COMPLETED: Messages.Customer.STATUS_COMPLETED,
        LeadStatus.CANCELLED: Messages.Customer.STATUS_CANCELLED,
        LeadStatus.REJECTED: Messages.Customer.STATUS_REJECTED_OR_CLOSED,
        LeadStatus.CLOSED: Messages.Customer.STATUS_REJECTED_OR_CLOSED,
    }
    template = mapping.get(status, Messages.Customer.STATUS_NO_ACTIVE_LEAD)
    return template.format(
        issue=issue,
        appointment_time=appointment,
        pro_name=pro_name,
        updated_at=updated_at,
    )


async def handle_status_query(chat_id: str) -> str:
    """Return the status of the customer's most recent non-terminal lead.

    Always returns a user-facing string — caller does not need to guard against None.
    """
    lead = await leads_collection.find_one(
        {
            "chat_id": chat_id,
            "status": {
                "$in": [
                    LeadStatus.NEW,
                    LeadStatus.CONTACTED,
                    LeadStatus.BOOKED,
                    LeadStatus.PENDING_ADMIN_REVIEW,
                ]
            },
        },
        sort=[("created_at", -1)],
    )

    if lead:
        pro = None
        if lead.get("pro_id"):
            pro = await users_collection.find_one({"_id": lead["pro_id"]})
        return _format_status_for_lead(lead, pro)

    # No active lead — check for a recent terminal lead to give context
    recent_terminal = await leads_collection.find_one(
        {
            "chat_id": chat_id,
            "status": {
                "$in": [
                    LeadStatus.COMPLETED,
                    LeadStatus.CANCELLED,
                    LeadStatus.REJECTED,
                    LeadStatus.CLOSED,
                ]
            },
        },
        sort=[("updated_at", -1), ("created_at", -1)],
    )
    if recent_terminal:
        return _format_status_for_lead(recent_terminal, pro=None)

    return Messages.Customer.STATUS_NO_ACTIVE_LEAD
