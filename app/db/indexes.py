"""MongoDB index definitions — the single source of truth (PRO-12).

Nobody has to remember to run this by hand: ``app/main.py``'s lifespan calls
``create_all_indexes(silent=True)`` on every API boot, and ``create_index`` is
idempotent (MongoDB skips indexes that already exist). A manual run
(``python scripts/create_indexes.py``, a CLI wrapper around this module) is
only useful to prime a brand-new database before its first API boot, or to
eyeball the full list. This lives in ``app/`` because production imports it
(restructure phase 3 — ``app/`` never imports ``scripts/``).

To add an index, add one row to ``INDEX_SPECS`` below — both the boot hook and
the CLI execute exactly that table, so they can never drift apart.
"""

from pymongo import ASCENDING, DESCENDING, TEXT

from app.core.logger import logger
from app.core.database import (
    users_collection,
    leads_collection,
    messages_collection,
    slots_collection,
    audit_log_collection,
    consent_collection,
    admins_collection,
    wa_delivery_collection,
    db,
)

SLOT_KEY = [("pro_id", ASCENDING), ("start_time", ASCENDING)]

# Every index in the system: (collection label, collection, keys, kwargs).
# Rationale that isn't obvious from the keys lives in a comment on the row.
INDEX_SPECS = [
    # --- Users ---
    ("Users", users_collection, [("phone_number", ASCENDING)], {"unique": True}),
    ("Users", users_collection, [("business_name", TEXT)], {}),
    ("Users", users_collection, [("service_areas", ASCENDING)], {}),
    # $geoNear in matching_service requires the 2dsphere type — a plain
    # ascending index on `location` breaks matching entirely.
    ("Users", users_collection, [("location", "2dsphere")], {}),
    # --- Leads ---
    ("Leads", leads_collection, [("chat_id", ASCENDING)], {}),
    ("Leads", leads_collection, [("status", ASCENDING)], {}),
    ("Leads", leads_collection, [("created_at", ASCENDING)], {}),
    ("Leads", leads_collection, [("pro_id", ASCENDING), ("status", ASCENDING)], {}),
    ("Leads", leads_collection, [("status", ASCENDING), ("created_at", ASCENDING)], {}),
    ("Leads", leads_collection, [("chat_id", ASCENDING), ("status", ASCENDING)], {}),
    # PRO-162: the SOS Reporter's dedup clause. Its query is
    # status + created_at + an $or over admin_reported_at, and every clause of
    # an $or must be indexed or the whole query degrades to a collection scan.
    # Deliberately NOT sparse: almost no lead carries the field, which makes
    # `{"sparse": True}` look like free savings — but a sparse index cannot
    # serve the `$exists: False` clause that is the whole point of the filter.
    ("Leads", leads_collection, [("admin_reported_at", ASCENDING)], {}),
    # PRO-117: second clause of the fat-finger guard's $or in
    # _recently_responded_lead — every clause of an $or must be indexed
    # or the whole query degrades to a collection scan.
    (
        "Leads",
        leads_collection,
        [("rejected_by", ASCENDING), ("last_rejected_at", ASCENDING)],
        {},
    ),
    # --- Messages ---
    ("Messages", messages_collection, [("chat_id", ASCENDING)], {}),
    (
        "Messages",
        messages_collection,
        [("timestamp", ASCENDING)],
        {"expireAfterSeconds": 7776000, "background": True},  # 90 days TTL
    ),
    # --- Slots ---
    ("Slots", slots_collection, [("pro_id", ASCENDING)], {}),
    # PRO-277: unique. Without it two generators (or one double click) left two
    # free documents for the same pro and start time, the atomic claim in
    # book_slot_for_lead took one copy per approval, and the pro was booked
    # twice. Every generator inserts through app/core/slot_writes.py, which
    # treats a duplicate as "already there". A database built before this had
    # the same key non-unique; prepare_unique_slot_index() dedupes it and drops
    # the old index first, since Mongo refuses a second index on the same key.
    ("Slots", slots_collection, SLOT_KEY, {"unique": True}),
    # --- Audit Log ---
    # PRO-142: compound, and the `_id` half is not optional. The viewer
    # pages with skip/limit and sorts `[("timestamp", -1), ("_id", -1)]` —
    # `_id` breaks ties so an entry cannot land on two pages or none. An
    # index only serves a sort whose key is a *prefix* of it, so a
    # timestamp-only index would leave every page doing a blocking
    # in-memory sort, and that path errors at the 32MB find-sort limit
    # rather than spilling to disk. This supersedes the timestamp-only
    # index it replaces, which was a prefix of this one.
    (
        "Audit Log",
        audit_log_collection,
        [("timestamp", DESCENDING), ("_id", DESCENDING)],
        {},
    ),
    ("Audit Log", audit_log_collection, [("admin_user", ASCENDING)], {}),
    # --- Consent ---
    ("Consent", consent_collection, [("chat_id", ASCENDING)], {"unique": True}),
    # --- Admins ---
    ("Admins", admins_collection, [("username", ASCENDING)], {"unique": True}),
    # --- WhatsApp Delivery Statuses (PRO-89) ---
    # Unique: every status callback does an upsert keyed on wamid, and 3-4
    # callbacks per message (sent/delivered/read) would otherwise race into
    # duplicate docs on an unindexed scan.
    (
        "WA Delivery",
        wa_delivery_collection,
        [("wa_message_id", ASCENDING)],
        {"unique": True},
    ),
    # Delivery facts have no business value after the lead has long since
    # closed; 30 days matches the debugging horizon, not the data model.
    (
        "WA Delivery",
        wa_delivery_collection,
        [("created_at", ASCENDING)],
        {"expireAfterSeconds": 30 * 24 * 3600, "background": True},
    ),
    # --- Admin Sessions ---
    ("Admin Sessions", db.admin_sessions, [("_token", ASCENDING)], {"unique": True}),
    (
        "Admin Sessions",
        db.admin_sessions,
        [("expiry", ASCENDING)],
        {"expireAfterSeconds": 0},
    ),
]


def _key_spec(keys) -> tuple:
    """Index keys as a comparable tuple: ``[("a", 1), ("b", -1)]``."""
    return tuple((field, direction) for field, direction in keys)


async def find_unlisted_indexes() -> list[tuple[str, str, tuple]]:
    """Indexes present in the database but absent from ``INDEX_SPECS``.

    Report-only (PRO-250): a superseded index (the old timestamp-only
    ``audit_log`` index, say) costs every write and is never dropped by
    ``create_index``. Nothing here drops anything; an operator decides.
    Returns ``(collection name, index name, key spec)`` rows; ``_id_`` and
    text indexes (whose stored keys differ from their spec) are skipped.
    """
    wanted: dict[str, set[tuple]] = {}
    collections = {}
    for _label, collection, keys, _kwargs in INDEX_SPECS:
        wanted.setdefault(collection.name, set()).add(_key_spec(keys))
        collections[collection.name] = collection

    unlisted = []
    for name, collection in collections.items():
        info = await collection.index_information()
        for index_name, spec in info.items():
            if index_name == "_id_" or "_fts" in dict(spec.get("key", [])):
                continue
            key = _key_spec(spec.get("key", []))
            if key not in wanted[name]:
                unlisted.append((name, index_name, key))
    return unlisted


async def prepare_unique_slot_index(slots) -> None:
    """Make room for the unique ``(pro_id, start_time)`` slot index (PRO-277).

    A no-op once the index is unique. Otherwise it removes duplicate slots, then
    drops the old non-unique index so the table loop can build the unique one.
    Within each group of documents sharing a pro and a start time it keeps one:
    the copy a lead's ``booked_slot_id`` points to, else a taken one, else the
    oldest. It deletes only copies that are free and that no lead points to. A
    group still holding two such copies is a real double booking, which no
    automatic step may resolve: it is logged at ERROR and the old index stays,
    so the unique build fails loudly too (PRO-250) until a human picks one.
    """
    leads = slots.database["leads"]
    info = await slots.index_information()
    current = next(
        (
            (name, spec)
            for name, spec in info.items()
            if _key_spec(spec.get("key", [])) == _key_spec(SLOT_KEY)
        ),
        None,
    )
    if current is None or current[1].get("unique"):
        return

    groups = await slots.aggregate(
        [
            {
                "$group": {
                    "_id": {"pro_id": "$pro_id", "start_time": "$start_time"},
                    "ids": {"$push": "$_id"},
                    "n": {"$sum": 1},
                }
            },
            {"$match": {"n": {"$gt": 1}}},
        ]
    ).to_list(length=None)

    removed = 0
    unresolved = 0
    for group in groups:
        docs = await slots.find({"_id": {"$in": group["ids"]}}).to_list(length=None)
        held = {
            lead["booked_slot_id"]
            for lead in await leads.find(
                {"booked_slot_id": {"$in": group["ids"]}}, {"booked_slot_id": 1}
            ).to_list(length=None)
        }
        docs.sort(key=lambda d: (d["_id"] not in held, not d.get("is_taken"), d["_id"]))
        extra = docs[1:]
        removable = [
            d["_id"] for d in extra if not d.get("is_taken") and d["_id"] not in held
        ]
        if removable:
            result = await slots.delete_many({"_id": {"$in": removable}})
            removed += result.deleted_count
        if len(extra) > len(removable):
            unresolved += 1

    if unresolved:
        logger.error(
            f"[Indexes] slots: {unresolved} pro/start-time pair(s) hold more than "
            "one taken or booked slot — a double booking to resolve by hand. The "
            "unique slot index cannot be built until then."
        )
        return

    await slots.drop_index(current[0])
    logger.warning(
        f"[Indexes] slots: removed {removed} duplicate free slot(s) and dropped the "
        f"non-unique index {current[0]!r}; the unique one is built next."
    )


async def create_all_indexes(silent: bool = False) -> list[dict]:
    """Create every index in INDEX_SPECS and return the ones that failed.

    Safe to call on every startup -- MongoDB skips existing indexes.
    ``silent=True`` (the API boot) suppresses the progress lines only: a
    failed index is always logged at ERROR (PRO-250 — it used to go through
    the same muted helper, so a unique index that failed on duplicates, or an
    options conflict such as a changed TTL, vanished at boot). One failing
    index is skipped so the rest still get created.

    Returns one ``{"label", "keys", "error"}`` dict per failed index; empty
    means every index is in place. Indexes in the database that the table
    does not list are logged at WARNING and never dropped.
    """

    def log(msg):
        if not silent:
            print(msg)

    log("Starting index creation...")

    # The slots collection is read off the table, so a test that swaps
    # INDEX_SPECS for its own never reaches the real database here.
    slot_specs = [
        spec
        for spec in INDEX_SPECS
        if _key_spec(spec[2]) == _key_spec(SLOT_KEY) and spec[3].get("unique")
    ]
    try:
        for _label, slots, _keys, _kwargs in slot_specs:
            await prepare_unique_slot_index(slots)
    except Exception as e:
        logger.error(f"[Indexes] could not prepare the unique slot index: {e}")

    failures = []
    current_label = None
    for label, collection, keys, kwargs in INDEX_SPECS:
        if label != current_label:
            log(f"Indexing {label} Collection...")
            current_label = label
        try:
            await collection.create_index(keys, **kwargs)
        except Exception as e:
            failures.append({"label": label, "keys": keys, "error": str(e)})
            logger.error(f"[Indexes] {label} {keys} could not be created: {e}")

    try:
        for coll_name, index_name, key in await find_unlisted_indexes():
            logger.warning(
                f"[Indexes] {coll_name}.{index_name} {list(key)} exists in the "
                "database but not in INDEX_SPECS (superseded?). Not dropped."
            )
    except Exception as e:
        logger.warning(f"[Indexes] could not list existing indexes: {e}")

    log(
        f"Index creation completed: {len(INDEX_SPECS) - len(failures)} of "
        f"{len(INDEX_SPECS)} in place."
    )
    return failures
