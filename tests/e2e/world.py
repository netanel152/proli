"""The E2E world: seed it, drive real messages through it, assert on what it did.

One ``World`` is one deterministic universe per test — a wiped Mongo, a fresh
fakeredis, a fresh outbound recorder. Messages go through the production
orchestrator; nothing about the flow is stubbed except the three things that
cannot be real offline, each of which is a deliberate, documented substitution:

* **The Green API socket** — replaced by ``OutboundRecorder`` at the httpx
  transport (see ``recorder.py``). Everything above it is production code.
* **Gemini** — replayed from recorded fixtures (see ``ai_replay.py``).
* **Media fetch** — deterministic fixture bytes instead of a real Cloudinary
  round-trip (see ``MEDIA_FIXTURES`` below).

Time is injected rather than read: a test never sleeps and never waits on the wall
clock. It backdates the stored timestamps the scheduled jobs actually read
(``pro_notified_at``, ``paused_at``, ``created_at``, ``updated_at``) relative to an
anchor computed *inside* the fixture, so a slow suite run cannot drift a threshold
(PRO-68).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
from bson import ObjectId

from app.core.constants import ISRAEL_CITIES_COORDS, LeadStatus, UserStates
from app.core.phone import to_chat_id
from app.services.state_manager_service import StateManager

from tests.e2e import reserved_numbers as R

# --- Deliberate decision: media is a fixture, not a real upload ---------------
#
# The acceptance criteria require this to be a conscious choice rather than an
# accident. It is: this harness runs on every PR, so it must be free, offline and
# byte-identical between runs. A real Cloudinary upload plus a real Gemini
# multimodal call is none of those, and it would put a live API key in CI. What
# the harness therefore proves about media is the *routing*: that a media webhook
# reaches the orchestrator, that the bytes and MIME type reach the AI call, that
# `media_urls` is persisted on the lead, and that the URL reaches the pro's
# approval message. Real image/audio/video comprehension is verified by hand in
# docs/PILOT_E2E_CHECKLIST.md (PRO-64).
MEDIA_FIXTURES = {
    "image": (b"\xff\xd8\xff\xe0-e2e-fixture-jpeg", "image/jpeg"),
    "audio": (None, "audio/ogg"),
    "video": (None, "video/mp4"),
}


def media_url_for(kind: str) -> str:
    return f"https://fixtures.invalid/e2e/{kind}"


async def fake_resolve_city_to_coords(name: str):
    """Offline stand-in for the Google-backed geocoder.

    Also a deliberate substitution: geocoding is a paid external API, so CI cannot
    call it. Resolving it offline matters because *production geocodes* — with
    ``GOOGLE_MAPS_API_KEY`` set, a full address like "דיזנגוף 50, תל אביב" resolves
    and routing takes the ``$geoNear`` path. Without this stub every harness lead
    would silently fall down the text-fallback branch and the harness would be
    testing the wrong half of ``determine_best_pro``.

    Mirrors Google's useful behaviour — find the locality inside a free-form
    address — and returns None for anything Proli has no coordinates for, so the
    text-fallback branch stays genuinely reachable.
    """
    if not name or not name.strip():
        return None
    lowered = name.strip().lower()
    exact = ISRAEL_CITIES_COORDS.get(lowered)
    if exact:
        return (exact[0], exact[1])
    for city, coords in ISRAEL_CITIES_COORDS.items():
        if city in lowered:
            return (coords[0], coords[1])
    return None


async def fake_detect_and_fetch_media(media_url: str):
    """Stand-in for ``media_handler.detect_and_fetch_media``.

    Mirrors the real contract exactly: images return (bytes, mime); audio and
    video return (None, mime) so the URL is handed to the AI engine instead.
    """
    for kind, payload in MEDIA_FIXTURES.items():
        if media_url.endswith(kind):
            return payload
    return None, None


class World:
    def __init__(self, db, recorder, ai, monkeypatch, redis):
        self.db = db
        self.recorder = recorder
        self.ai = ai
        self.monkeypatch = monkeypatch
        self.redis = redis
        # Every relative time in a test is measured from here, computed per-test.
        self.anchor = datetime.now(timezone.utc)
        # The shared real WhatsAppClient wired to the recorder; injected by the
        # conftest. Tests that call a flow function directly (pro_flow takes its
        # client as a parameter) pass this in.
        self.client = None
        self.customer = R.chat(R.CUSTOMER)
        self.admin = R.chat(R.ADMIN)
        self.pros: dict[str, dict] = {}
        self._enqueued: list[tuple] = []
        # PRO-88: operator pages (logger.critical → Sentry), captured by the
        # conftest. Not WhatsApp sends, so they never reach the recorder.
        self.pages: list[str] = []

    def assert_paged(self, *fragments: str) -> str:
        """Assert some operator page contains every fragment.

        The counterpart to ``recorder.assert_text_to`` for alerts that left
        the WhatsApp channel in PRO-88. Keeping a dedicated assertion (rather
        than reading a log) means a test states *the operator was told*, which
        is the behaviour that matters and survives a change of paging channel.
        """
        for page in self.pages:
            if all(f in page for f in fragments):
                return page
        raise AssertionError(
            f"no operator page contained all of {list(fragments)}\n"
            f"--- pages ---\n" + ("\n".join(self.pages) or "(none)")
        )

    # =====================================================================
    # Seeding
    # =====================================================================
    async def reset(self) -> None:
        """Wipe every collection.

        Belt and braces: ``tests/e2e/conftest.py`` already overrides ``mock_db`` to
        function scope, so each test starts on a fresh database. This also clears
        anything an arranger seeded when a test re-arranges mid-way.
        """
        for name in (
            "users",
            "leads",
            "slots",
            "messages",
            "settings",
            "reviews",
            "consent",
            "audit_log",
            "admins",
        ):
            await self.db[name].delete_many({})

    async def add_pro(
        self,
        phone: str,
        *,
        name: str,
        city: str = "תל אביב",
        coordinates: list[float] | None = None,
        rating: float = 5.0,
        is_active: bool = True,
        pending_approval: bool = False,
        service_areas: list[str] | None = None,
        with_slots: int = 4,
        profession: str = "plumber",
    ) -> dict:
        assert R.is_reserved(phone), f"{phone} is not in the reserved test range"
        coords = coordinates or ISRAEL_CITIES_COORDS[city]
        doc = {
            "_id": ObjectId(),
            "business_name": name,
            "phone_number": phone,
            "role": "professional",
            "is_active": is_active,
            "pending_approval": pending_approval,
            "profession_type": profession,
            "type": profession,
            "categories": [profession],
            "service_areas": service_areas if service_areas is not None else [city],
            "location": {"type": "Point", "coordinates": list(coords)},
            "social_proof": {"rating": rating, "review_count": 10},
            "plan": "basic",
            "price_list": "",
            "system_prompt": "",
            "no_show_count": 0,
            "created_at": self.anchor - timedelta(days=30),
        }
        await self.db.users.insert_one(doc)
        if with_slots:
            await self.add_slots(doc, count=with_slots)
        self.pros[phone] = doc
        return doc

    async def add_slots(self, pro: dict, count: int = 4, first_offset_hours: int = 1):
        """Free hourly slots starting an hour out.

        ``book_slot_for_lead`` searches ±2h around the hour after the lead was
        created, so the first slot must be inside that window for approval to
        actually reserve one.
        """
        base = self.anchor.replace(minute=0, second=0, microsecond=0)
        slots = []
        for i in range(count):
            start = base + timedelta(hours=first_offset_hours + i)
            slots.append(
                {
                    "_id": ObjectId(),
                    "pro_id": pro["_id"],
                    "start_time": start,
                    "end_time": start + timedelta(hours=1),
                    "is_taken": False,
                    "created_at": self.anchor,
                }
            )
        await self.db.slots.insert_many(slots)
        return slots

    async def add_admin_pro(self) -> dict:
        """The admin's own professional profile, used by the `ניהול` wizard's
        "take it myself" branch."""
        return await self.add_pro(R.ADMIN, name="מנהל המערכת", rating=5.0)

    async def set_scheduler_config(self, **toggles) -> None:
        await self.db.settings.update_one(
            {"_id": "scheduler_config"}, {"$set": toggles}, upsert=True
        )

    async def grant_consent(self, chat_id: str | None = None) -> None:
        await self.db.consent.insert_one(
            {
                "chat_id": chat_id or self.customer,
                "accepted": True,
                "timestamp": self.anchor,
            }
        )

    # =====================================================================
    # Driving
    # =====================================================================
    async def send(
        self, text: str | None, *, chat_id: str | None = None, media: str | None = None
    ) -> None:
        """Deliver one inbound message straight to the orchestrator."""
        from app.services.workflow_service import process_incoming_message

        await process_incoming_message(
            chat_id or self.customer,
            text,
            media_url_for(media) if media else None,
        )
        await self.settle()

    async def settle(self) -> None:
        """Let the orchestrator's fire-and-forget tasks (typing indicator, token
        accounting) finish, so their outbound traffic is recorded before a test
        asserts on the transcript."""
        for _ in range(4):
            await asyncio.sleep(0)

    def webhook_payload(
        self,
        text: str | None,
        *,
        chat_id: str | None = None,
        id_message: str = "e2e-1",
        media_kind: str | None = None,
    ) -> dict:
        """A synthetic Green API ``incomingMessageReceived`` payload."""
        from app.core.config import settings

        sender = chat_id or self.customer
        if media_kind:
            _, mime = MEDIA_FIXTURES[media_kind]
            message_data = {
                "typeMessage": {
                    "image": "imageMessage",
                    "audio": "audioMessage",
                    "video": "videoMessage",
                }[media_kind],
                "fileMessageData": {
                    "downloadUrl": media_url_for(media_kind),
                    "caption": text or "",
                    "mimeType": mime,
                },
            }
        else:
            message_data = {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": text or ""},
            }
        return {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": id_message,
            # PRO-86: /webhook no longer compares this against a configured
            # instance id — the check went with the Green provider. Kept as a
            # constant so the recorded fixtures keep their exact shape.
            "instanceData": {"idInstance": 1111111111},
            "senderData": {"chatId": sender, "senderName": "E2E"},
            "messageData": message_data,
        }

    async def post_webhook(self, payload: dict) -> dict:
        """POST a payload to the real FastAPI ``/webhook`` route in-process.

        Exercises the production token check, idempotency guard, group filter,
        DDoS shield and text extraction. Jobs land in ``self._enqueued`` instead of
        Redis; ``drain()`` runs them through the real ARQ task wrapper.
        """
        from app.main import app

        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://e2e"
        ) as client:
            response = await client.post("/webhook", json=payload)
        return response.json()

    async def drain(self) -> int:
        """Run every enqueued job through ``process_message_task``."""
        from app.core.arq_worker import process_message_task

        # Drain in place: the capturing ARQ pool in conftest holds a reference to
        # this exact list, so rebinding it would orphan every later enqueue.
        jobs = list(self._enqueued)
        self._enqueued.clear()
        for _name, args in jobs:
            await process_message_task({}, *args)
        await self.settle()
        return len(jobs)

    async def deliver(
        self,
        text: str | None,
        *,
        chat_id: str | None = None,
        id_message: str = "e2e-1",
        media_kind: str | None = None,
    ) -> dict:
        """Full path: webhook → enqueue → worker task."""
        result = await self.post_webhook(
            self.webhook_payload(
                text, chat_id=chat_id, id_message=id_message, media_kind=media_kind
            )
        )
        await self.drain()
        return result

    # =====================================================================
    # State & time
    # =====================================================================
    async def state(self, chat_id: str | None = None) -> str:
        return await StateManager.get_state(chat_id or self.customer)

    async def state_ttl(self, chat_id: str | None = None) -> int:
        return await self.redis.ttl(f"state:{chat_id or self.customer}")

    async def metadata(self, chat_id: str | None = None) -> dict:
        return await StateManager.get_metadata(chat_id or self.customer)

    async def set_state(
        self, state: str, *, chat_id: str | None = None, ttl: int | None = None
    ) -> None:
        await StateManager.set_state(chat_id or self.customer, state, ttl=ttl)

    async def set_metadata(self, data: dict, *, chat_id: str | None = None) -> None:
        await StateManager.set_metadata(chat_id or self.customer, data)

    async def age(self, lead_id, minutes: float, *fields: str) -> None:
        """Move stored timestamps back by ``minutes`` — the harness's clock.

        The scheduled jobs compute elapsed time as ``now - <stored timestamp>``, so
        backdating the timestamp is indistinguishable from time having passed, and
        it needs no global clock patch or new dependency.
        """
        shifted = self.anchor - timedelta(minutes=minutes)
        await self.db.leads.update_one(
            {"_id": lead_id}, {"$set": {field: shifted for field in fields}}
        )

    def set_business_hours(self, inside: bool) -> None:
        """Force the PRO-73 quiet-hours gate on or off.

        Patched at both bound sites: ``app.scheduler`` (job toggles) and
        ``app.services.monitor_service`` (the approval-SLA reassignment offer).
        """
        import app.scheduler as scheduler_module
        import app.services.monitor_service as monitor_module

        for module in (scheduler_module, monitor_module):
            self.monkeypatch.setattr(
                module, "within_business_hours", lambda *a, **k: inside
            )

    # =====================================================================
    # Reading the world back
    # =====================================================================
    async def lead(self, *, chat_id: str | None = None, **query):
        """The most recent lead for a chat (newest first)."""
        return await self.db.leads.find_one(
            {"chat_id": chat_id or self.customer, **query},
            sort=[("created_at", -1)],
        )

    async def lead_by_id(self, lead_id):
        return await self.db.leads.find_one({"_id": lead_id})

    async def leads(self, **query) -> list:
        return await self.db.leads.find(query).to_list(length=100)

    async def slot(self, slot_id):
        return await self.db.slots.find_one({"_id": slot_id})

    async def pro_doc(self, phone: str):
        return await self.db.users.find_one({"phone_number": phone})

    async def reviews(self) -> list:
        return await self.db.reviews.find({}).to_list(length=100)

    def pro_chat(self, phone: str) -> str:
        return to_chat_id(phone)

    async def status_history(self, lead_id) -> list[str]:
        """PRO-57: the ordered status transitions recorded on a lead."""
        lead = await self.lead_by_id(lead_id)
        return [entry.get("status") for entry in (lead or {}).get("status_history", [])]

    async def assert_lead_status(self, expected: str, *, chat_id: str | None = None):
        lead = await self.lead(chat_id=chat_id)
        assert lead is not None, "expected a lead to exist"
        assert (
            lead["status"] == expected
        ), f"expected lead status {expected!r}, got {lead['status']!r}"
        return lead

    async def assert_state(self, expected: str, *, chat_id: str | None = None):
        actual = await self.state(chat_id)
        assert actual == expected, f"expected state {expected!r}, got {actual!r}"

    # =====================================================================
    # Common arrangements
    # =====================================================================
    async def standard_cast(self) -> None:
        """Two Tel Aviv pros, one 60 km away, consent granted, scheduler idle.

        The far pro is what makes "no pro at max radius" a real assertion: it sits
        outside all three of the 10/20/30 km steps, so the fallback fires because
        the geo search genuinely found nobody.
        """
        await self.add_pro(R.PRO_PRIMARY, name="נתנאל אינסטלציה", rating=4.9)
        await self.add_pro(R.PRO_SECONDARY, name="אבי שרברבות", rating=4.5)
        await self.add_pro(
            R.PRO_FAR,
            name="הצפוני",
            city="חיפה",
            rating=5.0,
            service_areas=["חיפה"],
        )
        await self.grant_consent()
        await self.set_scheduler_config(
            stale_monitor_active=True,
            sos_healer_active=False,
            lead_janitor_active=False,
            sla_monitor_active=False,
            sos_reporter_active=True,
            whatsapp_monitor_active=True,
        )

    async def booked_job(self, pro: dict, **overrides) -> dict:
        """Insert a BOOKED lead with a reserved slot, as approval would have left it."""
        slot = await self.db.slots.find_one({"pro_id": pro["_id"], "is_taken": False})
        if slot:
            await self.db.slots.update_one(
                {"_id": slot["_id"]}, {"$set": {"is_taken": True}}
            )
        lead = {
            "_id": ObjectId(),
            "chat_id": self.customer,
            "status": LeadStatus.BOOKED,
            "pro_id": pro["_id"],
            "issue_type": "נזילה במטבח",
            "full_address": "דיזנגוף 50, תל אביב",
            "city": "תל אביב",
            "customer_name": "דנה",
            "appointment_time": "מחר ב-10:00",
            "booked_slot_id": slot["_id"] if slot else None,
            "created_at": self.anchor,
            "updated_at": self.anchor,
            "status_history": [],
        }
        lead.update(overrides)
        await self.db.leads.insert_one(lead)
        return lead

    async def awaiting_approval_job(self, pro: dict, **overrides) -> dict:
        """A NEW lead sitting with a pro, customer parked in AWAITING_PRO_APPROVAL —
        the state the PRO-56 approval SLA acts on."""
        lead = {
            "_id": ObjectId(),
            "chat_id": self.customer,
            "status": LeadStatus.NEW,
            "pro_id": pro["_id"],
            "issue_type": "נזילה במטבח",
            "full_address": "דיזנגוף 50, תל אביב",
            "city": "תל אביב",
            "customer_name": "דנה",
            "appointment_time": "מחר ב-10:00",
            "pro_notified_at": self.anchor,
            "approval_nudged": False,
            "reassign_offered": False,
            "created_at": self.anchor,
            "updated_at": self.anchor,
            "status_history": [],
        }
        lead.update(overrides)
        await self.db.leads.insert_one(lead)
        await self.set_state(
            UserStates.AWAITING_PRO_APPROVAL, chat_id=lead["chat_id"], ttl=3600
        )
        return lead
