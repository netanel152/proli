"""
Prepare staging for an end-to-end run on real handsets.

STAGING ONLY. Refuses to touch any database that is not `proli_staging_db`;
see `assert_staging_target`. Dry run by default: nothing is written without
`--apply`.

What it does, for one customer handset and one pro handset:

1. Resets the customer's and the pro's chats: their leads, their messages, the
   leads held by the E2E pros, and the Redis FSM state, held pre-consent
   messages, chat context and rate-limit / daily-AI-cap counters for both
   numbers. The 24h service-window tracker is left alone: it mirrors Meta.
   The customer's consent row is removed too (PRO-297), so the handset is back
   at first contact and the consent request, with its policy links, shows again.
   `delete_user_data` is not used for that: it also clears the service-window
   tracker and deletes rows this script does not own. A pro profile that holds
   the customer's number is never touched, only reported.
2. Seeds the pro handset as an approved, active pro in the given city and
   trade, with a calendar for the next 7 days.
3. Seeds one extra approved pro with the same city and trade and **no
   handset** (a reserved, structurally unreachable number), rated lower, so a
   rejected or timed-out offer has someone to be reassigned to. An offer to it
   cannot be delivered: that is what to expect on screen, and it ends in the
   `pro_offer_send_failed` escalation, not in a message anyone reads.

It reuses the application's own paths rather than writing documents by hand:
the WhatsApp registration's `_create_pending_pro` creates each pro, and the
steps the admin panel's Approve click runs (service-area geocode, persona
prompt, `create_initial_schedule`) approve it, and `record_consent` writes the
pro's consent. The one thing it deliberately
leaves out of the approval is the WhatsApp message that tells the pro they were
approved.

Every document it writes carries `seed_batch: "e2e_v1"`. A re-run removes the
previous batch first, so a changed phone number never leaves the old handset
competing for leads. A *different* approved pro already holding either phone is
never touched: the run refuses.

Usage:
    # Dry run (default): reports what it would change
    python scripts/prepare_staging_e2e.py --customer-phone 0501234567 \\
        --pro-phone 0507654321 --city "תל אביב" --trade plumber

    # Write
    python scripts/prepare_staging_e2e.py ... --apply

Against staging, run the `🧪 Prepare staging E2E data` workflow instead: the
connection settings come from repo secrets and the report lands in the job
summary.

Exit codes (so the run can gate a checklist):
    0  done (dry run: nothing is in the way)
    2  the city could not be placed on the map; nothing was written
    3  refused: not the staging database, a Redis that is not remote, bad input,
       or a foreign pro holds a phone; nothing was written
    4  the apply stopped part-way, or read back differently from what it wrote
       (Redis state still set, a pro with no slots); safe to run again
"""

import argparse
import asyncio
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings  # noqa: E402
from app.core.constants import DEVELOPMENT_ENV, STAGING_ENV  # noqa: E402
from app.core.database import (  # noqa: E402
    DB_NAME,
    consent_collection,
    leads_collection,
    messages_collection,
    slots_collection,
    users_collection,
)
from app.core.messages import Messages  # noqa: E402
from app.core.phone import CHAT_SUFFIX, mask_chat_id, to_il_mobile  # noqa: E402
from app.core.redis_client import get_redis_client  # noqa: E402
from app.core.pro_trades import (  # noqa: E402
    LISTABLE_TRADES,
    categories_for_save,
    needs_listed_trade,
)
from app.services.context_manager_service import ContextManager  # noqa: E402
from app.services.data_management_service import (  # noqa: E402
    _PER_CHAT_KEY_PATTERNS,
    _PER_CHAT_KEY_TEMPLATES,
    record_consent,
)
from app.services.geocoding_service import resolve_service_areas  # noqa: E402
from app.services.pro_onboarding_service import _create_pending_pro  # noqa: E402
from app.services.state_manager_service import StateManager  # noqa: E402
from admin_panel.core.utils import (  # noqa: E402
    create_initial_schedule,
    generate_system_prompt,
)

EXIT_OK = 0
EXIT_CITY_UNRESOLVED = 2
EXIT_REFUSED = 3
EXIT_INCOMPLETE = 4

# The only database this script may write. Pinned equal to
# `seed_coverage_matrix.STAGING_DB_NAME` by tests/test_prepare_staging_e2e.py.
STAGING_DB_NAME = "proli_staging_db"

# Every document written here carries this tag. The reset removes this batch and
# nothing else, so it can never reach a real pro, the admin account or the
# coverage matrix (`coverage_v1`).
E2E_BATCH = "e2e_v1"

# The extra pro's number. `972` followed by `0` is not a valid Israeli mobile, so
# no subscriber can hold it (the same reasoning as `tests/e2e/reserved_numbers.py`
# and `seed_coverage_matrix`'s block 100-199, which this stays clear of).
NO_HANDSET_PHONE = "972000000200"
RESERVED_PREFIX = "9720"

# Handset first, extra second: both carry slots, so rating decides.
HANDSET_RATING = 5.0
EXTRA_RATING = 4.0

_MAX_CITY_LENGTH = 60
_CITY_PUNCTUATION = ".'\"-"
_PRICES = "ביקור: 250₪"
_ALLOWED_ENVIRONMENTS = (DEVELOPMENT_ENV, STAGING_ENV)


class Refused(Exception):
    """A guard or an input check said no. Raised before the first write."""


# --- Guards -----------------------------------------------------------------


def assert_staging_target(environment: str, *db_names: str) -> None:
    """Refuse anything that is not the staging database.

    The database name is the wall: `ENVIRONMENT` alone is not enough, because the
    sibling workflows deliberately run with `development` so the deployed
    services' boot checks stay off, and the workflow's secrets are picked by
    branch. Every name given must be the staging one, so the async and the sync
    connection this script uses cannot disagree. Production is refused by name,
    and an environment that is not on the allow-list is refused too, so a value
    added later fails closed.
    """
    if environment not in _ALLOWED_ENVIRONMENTS:
        raise Refused(
            f"ENVIRONMENT={environment!r} is not development or staging. "
            "This script only ever runs against the staging database."
        )
    for name in db_names:
        if name != STAGING_DB_NAME:
            raise Refused(
                f"the target database is {name!r}, expected {STAGING_DB_NAME!r}. "
                "Nothing was changed."
            )


def normalize_inputs(
    customer_phone: str, pro_phone: str, city: str, trade: str
) -> tuple[str, str, str, str]:
    """The four inputs in the form the bot looks them up by, or `Refused`.

    Phones go through `to_il_mobile` (bare `9725…`), which is also what makes a
    reserved `9720…` number impossible to pass as a handset.
    """
    customer = to_il_mobile(customer_phone)
    pro = to_il_mobile(pro_phone)
    if not customer:
        raise Refused("the customer phone is not an Israeli mobile number.")
    if not pro:
        raise Refused("the pro phone is not an Israeli mobile number.")
    if customer == pro:
        raise Refused("the customer and the pro are the same number.")
    # An allow-list, checked on the raw value: the city reaches a fenced block in
    # the job summary, the pro's business name, the persona prompt and the
    # geocoder, and collapsing whitespace first would turn a newline into a space.
    raw_city = city or ""
    city = " ".join(raw_city.split())
    if (
        not city
        or len(city) > _MAX_CITY_LENGTH
        or not all(c.isalpha() or c in _CITY_PUNCTUATION or c == " " for c in raw_city)
    ):
        raise Refused(
            "the city is empty, too long, or has a character other than letters, "
            "spaces and . ' \" - (a backtick, a digit, a control or invisible mark)."
        )
    if trade not in LISTABLE_TRADES:
        raise Refused(f"trade {trade!r} is not one of: {', '.join(LISTABLE_TRADES)}.")
    return customer, pro, city, trade


# --- The approval, as the admin panel's Approve click runs it ----------------


def approval_update(pro: dict, resolution, *, rating: float, now: datetime) -> dict:
    """The `update_one` document the panel's `_approve_pending_pro` writes, plus
    this batch's tag and the pro's explicit trade list.

    The panel's function is Streamlit and cannot be imported here;
    `tests/test_prepare_staging_e2e.py` pins that the fields it sets are all set
    here too. Not repeated: the "you were approved" WhatsApp, the audit entry
    and the flash.
    """
    categories = categories_for_save(pro["type"], [pro["type"]])
    if needs_listed_trade(pro["type"], categories):  # pragma: no cover — guarded
        raise Refused(f"{pro['type']!r} would be approved with no trade listed.")
    prompt, keywords = generate_system_prompt(
        pro["business_name"],
        pro["type"],
        ", ".join(pro.get("service_areas", [])),
        pro.get("prices_for_prompt", ""),
        categories,
    )
    update = resolution.mongo_update(now=now, include_location=True)
    update["$set"].update(
        {
            "is_active": True,
            "pending_approval": False,
            "trades_set_at": now,
            "categories": categories,
            "system_prompt": prompt,
            "keywords": keywords,
            "social_proof": {"rating": rating, "review_count": 0},
            "seed_batch": E2E_BATCH,
        }
    )
    return update


def business_names(city: str, trade: str) -> tuple[str, str]:
    label = Messages.Onboarding.TYPE_LABELS[trade]
    return f"[E2E] {label} {city}", f"[E2E] {label} {city} (ללא מכשיר)"


# --- Reads ------------------------------------------------------------------


def _chat_id(phone: str) -> str:
    return f"{phone}{CHAT_SUFFIX}"


def _phone_filter(phone: str) -> dict:
    return {"phone_number": {"$in": [phone, _chat_id(phone)]}}


async def classify_phone_holders(phones: list[str]) -> dict[str, str]:
    """Who holds each phone now: `free`, `batch` (an earlier run of this script),
    `pending` (an unapproved registration, which a reset removes, as
    `reset_test.py` does) or `foreign` (anything else: refuse)."""
    out = {}
    for phone in phones:
        docs = [d async for d in users_collection.find(_phone_filter(phone))]
        if not docs:
            out[phone] = "free"
        elif all(d.get("seed_batch") == E2E_BATCH for d in docs):
            out[phone] = "batch"
        elif all(
            d.get("seed_batch") == E2E_BATCH or d.get("pending_approval") is True
            for d in docs
        ):
            out[phone] = "pending"
        else:
            out[phone] = "foreign"
    return out


async def batch_pro_ids() -> list:
    return [
        d["_id"]
        async for d in users_collection.find({"seed_batch": E2E_BATCH}, {"_id": 1})
    ]


def _consent_filter(phone: str) -> dict:
    return {"chat_id": {"$in": [phone, _chat_id(phone)]}}


async def foreign_pro_profiles(phone: str) -> int:
    """Pro profiles on this number that are not part of this batch. A reset
    never touches them, but a number that is also a pro can be routed as one."""
    return await users_collection.count_documents(
        {
            **_phone_filter(phone),
            "role": "professional",
            "seed_batch": {"$ne": E2E_BATCH},
        }
    )


def _lead_filter(chat_ids: list[str], pro_ids: list) -> dict:
    return {"$or": [{"chat_id": {"$in": chat_ids}}, {"pro_id": {"$in": pro_ids}}]}


async def competing_pros() -> int:
    """Approved, active pros that are not this batch: they sit in the same rings
    and may outrank the handset pro."""
    return await users_collection.count_documents(
        {
            "role": "professional",
            "is_active": True,
            "pending_approval": {"$ne": True},
            "seed_batch": {"$ne": E2E_BATCH},
        }
    )


# --- The run ----------------------------------------------------------------


async def run(
    *,
    customer_phone: str,
    pro_phone: str,
    city: str,
    trade: str,
    apply: bool,
    resolver=resolve_service_areas,
    environment: str | None = None,
    db_names: tuple[str, ...] | None = None,
    redis_url: str | None = None,
    out=print,
) -> int:
    mode = "--apply (writing)" if apply else "dry run (no writes)"
    try:
        # Before anything is read, let alone written.
        assert_staging_target(
            environment if environment is not None else settings.ENVIRONMENT,
            *(db_names if db_names is not None else _connected_db_names()),
        )
        customer, pro, city, trade = normalize_inputs(
            customer_phone, pro_phone, city, trade
        )
    except Refused as exc:
        out(f"❌ Refusing: {exc}")
        return EXIT_REFUSED

    # Nothing names a Redis as "staging", so the one thing checked is that it is
    # not the localhost default `REDIS_URL` silently falls back to: a run would
    # reset the chats of a Redis nobody is talking to and report success.
    if redis_url is not None:
        url = redis_url
    else:  # unset in Settings is None, which reads as no Redis at all
        url = settings.REDIS_URL.get_secret_value() if settings.REDIS_URL else ""
    problem = redis_target_problem(url)
    if problem:
        out(f"❌ Refusing: {problem} Nothing was written.")
        return EXIT_REFUSED

    # StateManager and ContextManager swallow a Redis failure into a log line, so
    # without this a run against an unreachable Redis would report the chats reset.
    try:
        await (await get_redis_client()).ping()
    except Exception as exc:
        out(
            f"❌ Refusing: Redis is not reachable ({type(exc).__name__}), so the "
            "chat state could not be reset. Nothing was written."
        )
        return EXIT_REFUSED

    out(f"🧪 Prepare staging E2E data — database {STAGING_DB_NAME} — {mode}")
    out(f"   redis: a non-loopback host, db {urlsplit(url).path.lstrip('/') or '0'}")
    out(
        f"   customer {mask_chat_id(customer)} · pro {mask_chat_id(pro)} · "
        f"city {city} · trade {trade}"
    )

    resolution = await resolver([city])
    # Stricter than the panel, which approves through a geocoder outage: a pro
    # with no `location` is invisible to `$geoNear`, so the run would prove nothing.
    if resolution.unresolved or not resolution.resolved:
        out(
            f"❌ {city!r} could not be placed on the map (unresolved: "
            f"{list(resolution.unresolved)}, geocoder unavailable: "
            f"{list(resolution.unavailable)}). A pro there would be approved "
            "into silence, so nothing was written."
        )
        return EXIT_CITY_UNRESOLVED

    holders = await classify_phone_holders([pro, NO_HANDSET_PHONE])
    foreign = [p for p, kind in holders.items() if kind == "foreign"]
    if foreign:
        out(
            "❌ Refusing: a pro that is not part of this batch already holds "
            f"{', '.join(mask_chat_id(p) for p in foreign)}. It is never "
            "overwritten. Nothing was changed."
        )
        return EXIT_REFUSED

    chat_ids = [_chat_id(customer), _chat_id(pro)]
    old_pro_ids = await batch_pro_ids()
    lead_filter = _lead_filter(chat_ids, old_pro_ids)
    out("")
    out("Reset")
    out(f"   leads:    {await leads_collection.count_documents(lead_filter)}")
    out(
        "   messages: "
        f"{await messages_collection.count_documents({'chat_id': {'$in': chat_ids}})}"
    )
    out(
        "   slots:    "
        f"{await slots_collection.count_documents({'pro_id': {'$in': old_pro_ids}})}"
    )
    out(f"   earlier E2E pros removed: {len(old_pro_ids)}")
    customer_consent = await consent_collection.count_documents(
        _consent_filter(customer)
    )
    out(
        f"   customer consent: {customer_consent} "
        + (
            "(removed on --apply, so the consent request shows again)"
            if customer_consent
            else "(none on file: the consent request already shows)"
        )
    )
    customer_pro_profiles = await foreign_pro_profiles(customer)
    if customer_pro_profiles:
        out(
            f"⚠️  The customer number {mask_chat_id(customer)} also belongs to "
            f"{customer_pro_profiles} pro profile(s) in this database. The reset "
            "does not touch it, and a number that is a pro can be routed as one "
            "instead of as a new customer, so scenario 1 may not start at first "
            "contact. Use a number that is not a pro."
        )
    if holders[pro] == "pending":
        out(f"   unapproved registration on {mask_chat_id(pro)} removed")
    for chat_id in chat_ids:
        history = await ContextManager.get_history(chat_id) or []
        state = await StateManager.get_state(chat_id)
        out(
            f"   chat {mask_chat_id(chat_id)}: state {state} → idle, "
            f"{len(history)} context messages cleared"
        )

    rival_count = await competing_pros()
    out("")
    out("Seed")
    out(f"   {business_names(city, trade)[0]} — approved, 7 days of slots, consent")
    out(
        f"   {business_names(city, trade)[1]} — approved, 7 days of slots, "
        f"number {NO_HANDSET_PHONE} (nobody holds it; offers to it cannot be delivered)"
    )
    if rival_count:
        out(
            f"⚠️  {rival_count} other approved, active pro(s) are in this database "
            "and compete for the same leads. If one outranks the handset pro, the "
            "offer goes to it instead. The coverage matrix (`coverage_v1`) is one "
            "such source; do not run both on one database."
        )

    if not apply:
        out("")
        out("Dry run: nothing was written. Tick `apply` / pass --apply to write.")
        return EXIT_OK

    # --- Write ---------------------------------------------------------------
    # Delete-then-create, so a failure part-way leaves the old batch gone and the
    # new one incomplete. It is idempotent: a re-run heals it, and says so.
    try:
        await leads_collection.delete_many(lead_filter)
        await messages_collection.delete_many({"chat_id": {"$in": chat_ids}})
        await slots_collection.delete_many({"pro_id": {"$in": old_pro_ids}})
        await users_collection.delete_many({"seed_batch": E2E_BATCH})
        for phone in (pro, NO_HANDSET_PHONE):
            await users_collection.delete_many(
                {**_phone_filter(phone), "pending_approval": True}
            )
        await consent_collection.delete_many({"seed_batch": E2E_BATCH})
        # The customer handset goes back to first contact (PRO-297). The pro's
        # consent is written again below, after this reset.
        await consent_collection.delete_many(_consent_filter(customer))
        for chat_id in chat_ids:
            await StateManager.clear_state(chat_id)
            await ContextManager.clear_context(chat_id)
            await clear_throttles(chat_id)

        handset_name, extra_name = business_names(city, trade)
        now = datetime.now(timezone.utc)
        for phone, name, rating in (
            (pro, handset_name, HANDSET_RATING),
            (NO_HANDSET_PHONE, extra_name, EXTRA_RATING),
        ):
            pro_id = await _create_pending_pro(
                _chat_id(phone),
                {"name": name, "type": trade, "areas": [city], "prices": _PRICES},
            )
            doc = await users_collection.find_one({"_id": pro_id})
            await users_collection.update_one(
                {"_id": pro_id},
                approval_update(doc, resolution, rating=rating, now=now),
            )
            # Blocking PyMongo, so off the event loop.
            await asyncio.to_thread(create_initial_schedule, pro_id)

        # The pro's first message must not stop at the consent gate. The app's own
        # consent write, then this batch's tag so the next run removes it.
        await record_consent(_chat_id(pro), True)
        await consent_collection.update_one(
            {"chat_id": _chat_id(pro)}, {"$set": {"seed_batch": E2E_BATCH}}
        )
    except Exception as exc:
        out(
            f"❌ The apply stopped part-way ({type(exc).__name__}). Part of the "
            "reset or the seed may be written. It is safe to run it again."
        )
        return EXIT_INCOMPLETE

    # Trust nothing that swallows errors: StateManager, ContextManager and the
    # slot insert all turn a failure into a log line or a count.
    problems = await verify_written(chat_ids, pro, customer_phone=customer)
    if problems:
        out("")
        for line in problems:
            out(f"❌ {line}")
        out("The apply did not leave staging as described. Run it again.")
        return EXIT_INCOMPLETE

    out("")
    out("✅ Done. Written and checked:")
    out(
        f"   pros in batch {E2E_BATCH}: {await users_collection.count_documents({'seed_batch': E2E_BATCH})}"
    )
    out(
        "   slots: "
        f"{await slots_collection.count_documents({'pro_id': {'$in': await batch_pro_ids()}})}"
    )
    out(
        "   The customer's consent record was removed: the first message from "
        "that handset gets the consent request again."
    )
    return EXIT_OK


_LOOPBACK_HOSTS = ("", "localhost", "127.0.0.1", "::1", "0.0.0.0")


def redis_target_problem(redis_url: str) -> str | None:
    """Why this Redis URL cannot be staging's, or None.

    A loopback host is what `REDIS_URL` falls back to when it is unset; the staging
    database is remote, so a loopback Redis beside it is a misconfiguration.
    """
    host = (urlsplit(redis_url or "").hostname or "").lower()
    if host in _LOOPBACK_HOSTS:
        return "REDIS_URL is unset or points at this machine, not at staging's Redis."
    return None


async def clear_throttles(chat_id: str) -> None:
    """The rate-limit, inbound-window and daily-AI-cap counters for one chat.

    Same key families as the data-deletion path (`data_management_service`),
    minus its `wa:*` ones: the 24h service-window tracker mirrors what Meta holds,
    and clearing it would block replies Meta would still deliver. Without this a
    second run on the same handset meets the 40-a-day AI cap.
    """
    redis = await get_redis_client()
    keys = [
        t.format(chat_id=chat_id)
        for t in _PER_CHAT_KEY_TEMPLATES
        if not t.startswith("wa:")
    ]
    for pattern in _PER_CHAT_KEY_PATTERNS:
        keys.extend(
            [k async for k in redis.scan_iter(match=pattern.format(chat_id=chat_id))]
        )
    if keys:
        await redis.delete(*keys)


async def verify_written(
    chat_ids: list[str], pro_phone: str, *, customer_phone: str | None = None
) -> list[str]:
    """Read back what an apply claims, from the raw stores. Empty means it holds."""
    problems = []
    if customer_phone and await consent_collection.count_documents(
        _consent_filter(customer_phone)
    ):
        problems.append(
            f"a consent record for {mask_chat_id(customer_phone)} is still there."
        )
    redis = await get_redis_client()
    for chat_id in chat_ids:
        held = await redis.exists(
            f"state:{chat_id}",
            f"state_meta:{chat_id}",
            f"consent_hold:{chat_id}",
            f"context:{chat_id}",
        )
        if held:
            problems.append(
                f"Redis still holds chat state for {mask_chat_id(chat_id)}."
            )
    if await leads_collection.count_documents({"chat_id": {"$in": chat_ids}}):
        problems.append("a lead of one of the two chats is still there.")
    for phone in (pro_phone, NO_HANDSET_PHONE):
        doc = await users_collection.find_one(_phone_filter(phone))
        if not (
            doc
            and doc.get("is_active") is True
            and doc.get("pending_approval") is False
            and (doc.get("location") or {}).get("coordinates")
        ):
            problems.append(f"{mask_chat_id(phone)} is not an approved, located pro.")
        elif not await slots_collection.count_documents({"pro_id": doc["_id"]}):
            problems.append(f"{mask_chat_id(phone)} has no calendar slots.")
    if not await consent_collection.find_one(
        {"chat_id": _chat_id(pro_phone), "accepted": True}
    ):
        problems.append(f"no consent record for {mask_chat_id(pro_phone)}.")
    return problems


def _connected_db_names() -> tuple[str, ...]:
    """The database each of this script's two Mongo connections points at: the
    app's async one and the admin panel helper's own sync one."""
    from admin_panel.core import utils as admin_utils

    return DB_NAME, admin_utils.db.name


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--customer-phone", required=True)
    parser.add_argument("--pro-phone", required=True)
    parser.add_argument("--city", required=True)
    parser.add_argument("--trade", required=True, choices=list(LISTABLE_TRADES))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write. Without it the run only reports what it would change.",
    )
    args = parser.parse_args(argv)

    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    return asyncio.run(
        run(
            customer_phone=args.customer_phone,
            pro_phone=args.pro_phone,
            city=args.city,
            trade=args.trade,
            apply=args.apply,
        )
    )


if __name__ == "__main__":
    sys.exit(main())
