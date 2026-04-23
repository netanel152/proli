"""
Fully-automated post-deploy smoke test for Proli on Railway (or any deployment).

Runs a full customer ↔ pro lifecycle end-to-end by posting simulated Green API
webhooks and polling MongoDB directly to verify each FSM transition. No manual
interaction required. Exits 0 on success, 1 on failure.

Requirements:
  - The deployed backend + worker must be running and reachable via --base-url.
  - MONGO_URI must point to the SAME MongoDB the deployment uses.
  - The pro 972524828796 must be seeded (run scripts/seed_db.py or create it via
    admin panel). The script will fail preflight and tell you if it's missing.
  - The customer 972523651414 is virtual — no real phone needed. The script
    auto-upserts consent for it so the consent gate is bypassed.

⚠️  The pro 972524828796 is a REAL phone. Running this test will send real
    WhatsApp messages to that number (lead offer, deal details, rating thank-you).

Usage:
    python scripts/smoke_test_railway.py \
        --base-url https://<your-app>.up.railway.app \
        --mongo-uri "mongodb+srv://..."

All args can also come from env vars:
    SMOKE_BASE_URL, MONGO_URI, GREEN_API_INSTANCE_ID, WEBHOOK_TOKEN
"""

import asyncio
import argparse
import os
import sys
import time
import uuid
from datetime import datetime, timezone

import httpx
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import uri_parser
import certifi
from dotenv import load_dotenv

load_dotenv()

CUSTOMER_PHONE = "972523651414"
CUSTOMER_CHAT_ID = f"{CUSTOMER_PHONE}@c.us"
PRO_PHONE = "972524828796"
PRO_CHAT_ID = f"{PRO_PHONE}@c.us"

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
NC = "\033[0m"


def _ok(m): print(f"  {GREEN}✓ {m}{NC}")
def _fail(m): print(f"  {RED}✗ {m}{NC}")
def _info(m): print(f"  {CYAN}→ {m}{NC}")
def _warn(m): print(f"  {YELLOW}⚠ {m}{NC}")
def _step(n, t): print(f"\n{BOLD}[{n}] {t}{NC}")


class SmokeTest:
    def __init__(self, base_url, instance_id, webhook_token, mongo_uri, db_name):
        self.base_url = base_url.rstrip("/")
        self.instance_id = int(instance_id)
        self.webhook_token = webhook_token
        self.http = httpx.AsyncClient(timeout=30)
        
        ca_file = certifi.where() if "+srv" in mongo_uri else None
        kwargs = {"tlsCAFile": ca_file} if ca_file else {}
        self.mongo = AsyncIOMotorClient(mongo_uri, **kwargs)
        self.db = self.mongo[db_name]
        self.failures: list[str] = []

    # --- HTTP helpers ---
    def _url(self) -> str:
        url = f"{self.base_url}/webhook"
        if self.webhook_token:
            url += f"?token={self.webhook_token}"
        return url

    def _payload(self, chat_id: str, text: str, sender_name: str, media_url: str = None) -> dict:
        base = {
            "typeWebhook": "incomingMessageReceived",
            "idMessage": uuid.uuid4().hex[:24],
            "instanceData": {
                "idInstance": self.instance_id,
                "wid": chat_id,
                "typeInstance": "whatsapp",
            },
            "senderData": {"chatId": chat_id, "senderName": sender_name},
        }
        if media_url:
            base["messageData"] = {
                "typeMessage": "imageMessage",
                "fileMessageData": {
                    "downloadUrl": media_url,
                    "caption": text,
                    "mimeType": "image/jpeg",
                    "fileName": "test_image.jpg"
                }
            }
        else:
            base["messageData"] = {
                "typeMessage": "textMessage",
                "textMessageData": {"textMessage": text},
            }
        return base

    async def _send(self, chat_id: str, text: str, sender_name: str = "Smoke Test", media_url: str = None) -> dict:
        r = await self.http.post(
            self._url(),
            json=self._payload(chat_id, text, sender_name, media_url),
            headers=self._headers(),
        )
        r.raise_for_status()
        return r.json()

    async def _health(self) -> dict:
        r = await self.http.get(f"{self.base_url}/health", timeout=10)
        r.raise_for_status()
        return r.json()

    # --- Mongo polling ---
    async def _poll_lead(self, predicate, timeout: float = 40.0, poll: float = 1.5):
        """Poll customer's latest lead until predicate(lead) returns True."""
        deadline = time.monotonic() + timeout
        last = None
        while time.monotonic() < deadline:
            last = await self.db.leads.find_one(
                {"chat_id": CUSTOMER_CHAT_ID},
                sort=[("created_at", -1)],
            )
            if last and predicate(last):
                return last
            await asyncio.sleep(poll)
        return last

    # --- Preflight ---
    async def preflight(self) -> bool:
        _step("PRE", "Preflight checks")
        try:
            h = await self._health()
        except Exception as e:
            _fail(f"/health unreachable: {e}")
            self.failures.append("health-unreachable")
            return False
        if h.get("status") != "healthy":
            _fail(f"/health returned {h}")
            self.failures.append("health-unhealthy")
            return False
        _ok(f"/health → {h}")

        pro = await self.db.users.find_one(
            {"phone_number": PRO_PHONE, "role": "professional"}
        )
        if not pro:
            _fail(f"Pro {PRO_PHONE} NOT seeded. Run `python scripts/seed_db.py` first.")
            self.failures.append("pro-missing")
            return False
        if not pro.get("is_active"):
            _fail(f"Pro {PRO_PHONE} exists but is_active=False.")
            self.failures.append("pro-inactive")
            return False
        self.pro_id = pro["_id"]
        _ok(f"Pro seeded: {pro.get('business_name')} (is_active=True)")

        await self.db.consent.update_one(
            {"chat_id": CUSTOMER_CHAT_ID},
            {"$set": {
                "chat_id": CUSTOMER_CHAT_ID,
                "accepted": True,
                "timestamp": datetime.now(timezone.utc),
            }},
            upsert=True,
        )
        _ok(f"Consent present for customer {CUSTOMER_PHONE}")

        del_l = await self.db.leads.delete_many({"chat_id": CUSTOMER_CHAT_ID})
        del_m = await self.db.messages.delete_many({"chat_id": CUSTOMER_CHAT_ID})
        _ok(f"Cleaned {del_l.deleted_count} lead(s) + {del_m.deleted_count} message(s) for customer")

        # Reset customer FSM state/context via "תפריט" keyword (doesn't touch Mongo)
        await self._send(CUSTOMER_CHAT_ID, "תפריט", "עדי")
        await asyncio.sleep(2)
        _ok("Reset customer FSM state via 'תפריט'")

        return True

    # --- Steps ---
    async def step0_bailout_flow(self) -> bool:
        _step(0, "Test Bailout: Silent Media + AWAITING_ADDRESS Cancel")
        # 1. Image only (Silent Media intent)
        await self._send(CUSTOMER_CHAT_ID, "", "עדי", media_url="https://res.cloudinary.com/dvv4qlcyu/image/upload/v1776296828/6aa179c001e5bbee97112f3f7ba3878d_q6tdht.png")
        await asyncio.sleep(8)
        
        # 2. Provide problem and city to trigger match
        await self._send(CUSTOMER_CHAT_ID, "יש לי פיצוץ בצנרת ואני מתל אביב", "עדי")
        _info("Waiting for lead to hit AWAITING_ADDRESS (contacted)...")
        lead = await self._poll_lead(
            lambda d: d.get("pro_id") is not None and d.get("status") in ("contacted", "new"),
            timeout=45,
        )
        if not lead:
            _fail("Lead not matched/contacted for bailout test")
            self.failures.append("step0-no-match")
            return False
            
        _ok(f"Lead {lead['_id']} matched to pro, waiting at address gate")
        
        # 3. Cancel the flow (Nevermind trap)
        await self._send(CUSTOMER_CHAT_ID, "בטל", "עדי")
        _info("Waiting for lead status → CANCELLED...")
        lead_cancelled = await self._poll_lead(
            lambda d: d.get("status") == "cancelled",
            timeout=25,
        )
        if not lead_cancelled:
            _fail("Lead did not transition to cancelled")
            self.failures.append("step0-not-cancelled")
            return False
            
        _ok(f"Lead {lead_cancelled['_id']} successfully cancelled by bailout")
        
        # Give system a moment to settle
        await asyncio.sleep(2)
        return True

    async def step0b_empty_radius_bailout(self) -> bool:
        _step("0b", "Test Empty Radius (30km) Bailout: PENDING_ADMIN_REVIEW")
        # 1. Reset user state first
        await self._send(CUSTOMER_CHAT_ID, "תפריט", "ישראל")
        await asyncio.sleep(2)

        # 2. Report a problem in a distant city (e.g. Eilat, assuming pro is in Tel Aviv/Center)
        await self._send(CUSTOMER_CHAT_ID, "יש לי פיצוץ בצנרת ואני מאילת", "ישראל")
        _info("Waiting for lead to hit PENDING_ADMIN_REVIEW (≤45s)...")
        lead = await self._poll_lead(
            lambda d: d.get("status") == "pending_admin_review",
            timeout=45,
        )
        if not lead:
            _fail("Lead did not escalate to pending_admin_review for distant city")
            self.failures.append("step0b-no-admin-review")
            return False
            
        _ok(f"Lead {lead['_id']} correctly escalated to PENDING_ADMIN_REVIEW for Eilat")
        
        # 3. Clean up the lead so it doesn't interfere with the next steps
        await self.db.leads.delete_many({"chat_id": CUSTOMER_CHAT_ID})
        await self._send(CUSTOMER_CHAT_ID, "תפריט", "ישראל")
        await asyncio.sleep(2)
        return True

    async def step1_customer_reports_problem(self) -> bool:
        _step(1, "Customer reports plumbing issue with pictures and gives name")
        # Greet and give name
        await self._send(CUSTOMER_CHAT_ID, "שלום! קוראים לי ישראל ישראלי", "ישראל")
        await asyncio.sleep(5)
        
        # Send first photo with issue description
        await self._send(CUSTOMER_CHAT_ID, "יש לי נזילה רצינית בכיור המטבח, הנה תמונה ראשונה", "ישראל", media_url="https://res.cloudinary.com/dvv4qlcyu/image/upload/v1776296828/6aa179c001e5bbee97112f3f7ba3878d_q6tdht.png")
        await asyncio.sleep(5)
        
        # Send second photo
        await self._send(CUSTOMER_CHAT_ID, "והנה עוד תמונה מזווית אחרת", "ישראל", media_url="https://res.cloudinary.com/dvv4qlcyu/image/upload/v1765029750/az7qo1eiwfbbq5tvsudo.jpg")
        await asyncio.sleep(5)
        
        # City
        await self._send(CUSTOMER_CHAT_ID, "אני גר בתל אביב", "ישראל")
        
        _info("Waiting for dispatcher to create a lead and match a pro (≤40s)...")
        lead = await self._poll_lead(
            lambda d: d.get("pro_id") is not None and d.get("status") != "cancelled",
            timeout=45,
        )
        if not lead or not lead.get("pro_id"):
            _fail(f"Lead not matched to any pro within 45s. Last lead: {lead}")
            self.failures.append("step1-no-match")
            return False
        if str(lead["pro_id"]) != str(self.pro_id):
            _fail(f"Matched a different pro. Expected {self.pro_id}, got {lead['pro_id']}")
            self.failures.append("step1-wrong-pro")
            return False
            
        # Check customer_name capture
        cust_name = lead.get("customer_name")
        if not cust_name:
            _fail("customer_name not captured in lead")
            self.failures.append("step1-no-customer-name")
            return False

        # Check media capture
        media_urls = lead.get("media_urls", [])
        if len(media_urls) < 2:
            _fail(f"Expected at least 2 media_urls, got {len(media_urls)}")
            self.failures.append("step1-missing-media")
            return False
            
        _ok(f"Lead {lead['_id']} matched to pro (issue={lead.get('issue_type')}, city={lead.get('city')}, name={cust_name}, {len(media_urls)} images attached)")
        return True

    async def step2_customer_provides_full_address(self) -> bool:
        _step(2, "Customer provides bilingual full address + appointment time")
        await self._send(
            CUSTOMER_CHAT_ID,
            "הבעיה היא בברז וזה קורה כבר יומיים. הכתובת היא Herzl 15 Tel Aviv, floor 2 apt 4, מחר ב-10 בבוקר. אנא אשר/י את העבודה וסגור/י את העסקה כעת.",
            "ישראל",
        )
        _info("Waiting for address gate to pass and deal to finalize (≤50s)...")
        lead = await self._poll_lead(
            lambda d: (
                d.get("status") == "new"
                and d.get("appointment_time")
                and d.get("full_address")
                and d.get("street")
                and d.get("street_number")
                and d.get("floor")
                and d.get("apartment")
            ),
            timeout=50,
        )
        if not lead:
            _fail("Deal not finalized — address gate may still reject, or AI did not extract all fields from bilingual address")
            self.failures.append("step2-not-finalized")
            return False
        missing = [f for f in ("street", "street_number", "city", "floor", "apartment", "appointment_time") if not lead.get(f)]
        if missing:
            _fail(f"Lead missing fields after finalization: {missing}")
            self.failures.append(f"step2-missing-{','.join(missing)}")
            return False
        _ok(f"Address gate passed — full_address='{lead['full_address']}'")
        _ok(f"Parts: street={lead['street']} {lead['street_number']} · floor={lead['floor']} · apt={lead['apartment']}")
        _ok(f"Appointment: {lead['appointment_time']}")
        return True

    async def step3_pro_approves(self) -> bool:
        _step(3, "Pro approves the lead ('אשר') + Fat Finger check")
        await self._send(PRO_CHAT_ID, "אשר", "Netanel Pro")
        
        # Send immediately again to test the redis lock / fat finger guard (should gracefully ignore/return ALREADY_RESPONDED)
        await self._send(PRO_CHAT_ID, "אשר", "Netanel Pro")
        
        _info("Waiting for lead status → BOOKED (≤25s)...")
        lead = await self._poll_lead(lambda d: d.get("status") == "booked", timeout=25)
        if not lead or lead.get("status") != "booked":
            _fail(f"Lead not BOOKED. Current status: {lead.get('status') if lead else 'None'}")
            self.failures.append("step3-not-booked")
            return False
        _ok(f"Lead {lead['_id']} → BOOKED")
        return True

    async def step4_pro_finishes(self) -> bool:
        _step(4, "Pro marks the job finished ('סיימתי')")
        await self._send(PRO_CHAT_ID, "סיימתי", "Netanel Pro")
        _info("Waiting for lead status → COMPLETED (≤25s)...")
        lead = await self._poll_lead(lambda d: d.get("status") == "completed", timeout=25)
        if not lead or lead.get("status") != "completed":
            _fail(f"Lead not COMPLETED. Current status: {lead.get('status') if lead else 'None'}")
            self.failures.append("step4-not-completed")
            return False
        _ok(f"Lead {lead['_id']} → COMPLETED")
        return True

    async def step5_customer_rates(self) -> bool:
        _step(5, "Customer submits 5-star rating")
        pro_before = await self.db.users.find_one({"_id": self.pro_id})
        prior = (pro_before.get("social_proof") or {}).get("review_count", 0) or 0

        await self._send(CUSTOMER_CHAT_ID, "5", "ישראל")
        _info("Waiting for pro review_count to increase (≤25s)...")
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            pro_after = await self.db.users.find_one({"_id": self.pro_id})
            cur = (pro_after.get("social_proof") or {}).get("review_count", 0) or 0
            if cur > prior:
                _ok(f"Pro rating recorded (review_count: {prior} → {cur})")
                return True
            await asyncio.sleep(1.5)
        _fail(f"Pro review_count did not increase within 25s (still {prior})")
        self.failures.append("step5-rating-missing")
        return False

    # --- Runner ---
    async def run(self) -> bool:
        print(f"\n{BOLD}{CYAN}{'='*62}\n  Proli Post-Deploy Smoke Test\n{'='*62}{NC}")
        print(f"  Target:   {self.base_url}")
        print(f"  Customer: {CUSTOMER_PHONE} (virtual)")
        print(f"  Pro:      {PRO_PHONE}  {YELLOW}(real phone — will receive WhatsApp messages){NC}")

        if not await self.preflight():
            return False

        for stepfn in (
            self.step0_bailout_flow,
            self.step0b_empty_radius_bailout,
            self.step1_customer_reports_problem,
            self.step2_customer_provides_full_address,
            self.step3_pro_approves,
            self.step4_pro_finishes,
            self.step5_customer_rates,
        ):
            try:
                passed = await stepfn()
            except Exception as e:
                _fail(f"{stepfn.__name__} raised {type(e).__name__}: {e}")
                self.failures.append(f"{stepfn.__name__}-exception")
                passed = False
            if not passed:
                return False

        return True

    async def close(self):
        await self.http.aclose()
        self.mongo.close()


async def main():
    parser = argparse.ArgumentParser(description="Post-deploy smoke test for Proli")
    parser.add_argument("--base-url", default=os.getenv("SMOKE_BASE_URL", "http://localhost:8000"))
    parser.add_argument("--instance-id", default=os.getenv("GREEN_API_INSTANCE_ID", "0"))
    parser.add_argument("--token", default=os.getenv("WEBHOOK_TOKEN", ""))
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI") or os.getenv("MONGODB_URI"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB_NAME"))
    args = parser.parse_args()

    if not args.mongo_uri:
        print(f"{RED}Missing MONGO_URI — set it in .env or pass --mongo-uri{NC}")
        sys.exit(2)

    db_name = args.db
    if not db_name:
        try:
            _parsed = uri_parser.parse_uri(args.mongo_uri)
            db_name = _parsed.get("database") or "proli_db"
        except Exception:
            db_name = "proli_db"

    if args.instance_id == "0":
        print(f"{RED}Missing GREEN_API_INSTANCE_ID — set it in .env or pass --instance-id{NC}")
        sys.exit(2)

    test = SmokeTest(args.base_url, args.instance_id, args.token, args.mongo_uri, db_name)
    passed = False
    try:
        passed = await test.run()
    finally:
        await test.close()

    print(f"\n{BOLD}{'='*62}{NC}")
    if passed:
        print(f"{BOLD}{GREEN}  ✅ SMOKE TEST PASSED — full lifecycle works end-to-end{NC}")
        sys.exit(0)
    else:
        print(f"{BOLD}{RED}  ❌ SMOKE TEST FAILED — {len(test.failures)} issue(s){NC}")
        for f in test.failures:
            print(f"    - {f}")
        sys.exit(1)


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
