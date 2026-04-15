"""
Simulate the full test flow using fake webhook calls.
No need for a third phone — the "customer" is simulated via HTTP.

Requirements: Only 2 phones needed:
  - Green API bot phone (sends messages)
  - Your real phone (972524828796) — registered as pro, receives real WhatsApp messages

The customer (972523651414) is virtual — responses appear in worker logs.

Usage:
    python scripts/simulate_test.py                    # Run all tests
    python scripts/simulate_test.py --test tc3         # Run specific test
    python scripts/simulate_test.py --test tc1,tc3,tc5 # Run multiple tests
    python scripts/simulate_test.py --list             # List available tests
    python scripts/simulate_test.py --base-url http://localhost:8001  # Custom URL
"""

import httpx
import asyncio
import argparse
import uuid
import time
import sys
import os
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import uri_parser
import certifi
from dotenv import load_dotenv

load_dotenv()

# --- Config ---
BASE_URL = os.getenv("SMOKE_BASE_URL", "http://localhost:8000")
INSTANCE_ID = int(os.getenv("GREEN_API_INSTANCE_ID", "7105567180"))
WEBHOOK_TOKEN = os.getenv("WEBHOOK_TOKEN", "")

# Virtual customer (no real phone needed)
CUSTOMER_PHONE = "972523651414"
CUSTOMER_CHAT_ID = f"{CUSTOMER_PHONE}@c.us"

# Real pro (your phone — will receive actual WhatsApp messages)
PRO_PHONE = "972524828796"
PRO_CHAT_ID = f"{PRO_PHONE}@c.us"

# Colors for terminal output
GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"


def make_text_payload(chat_id: str, text: str, sender_name: str = "Test User") -> dict:
    """Build a fake Green API webhook payload for a text message."""
    return {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": uuid.uuid4().hex[:24],
        "instanceData": {
            "idInstance": INSTANCE_ID,
            "wid": f"{chat_id}",
            "typeInstance": "whatsapp"
        },
        "senderData": {
            "chatId": chat_id,
            "senderName": sender_name
        },
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {
                "textMessage": text
            }
        }
    }


def _webhook_url() -> str:
    """Webhook URL with token appended if configured."""
    url = f"{BASE_URL}/webhook"
    if WEBHOOK_TOKEN:
        url += f"?token={WEBHOOK_TOKEN}"
    return url


async def send_message(client: httpx.AsyncClient, chat_id: str, text: str, sender_name: str = "Test User") -> dict:
    """Send a simulated webhook and return the response."""
    payload = make_text_payload(chat_id, text, sender_name)
    resp = await client.post(_webhook_url(), json=payload)
    return resp.json()


async def send_and_wait(client: httpx.AsyncClient, chat_id: str, text: str, sender_name: str = "Test User", wait: float = 4.0) -> dict:
    """Send message and wait for the worker to process it."""
    result = await send_message(client, chat_id, text, sender_name)
    # Wait for ARQ worker to pick up and process
    await asyncio.sleep(wait)
    return result


def header(title: str):
    print(f"\n{'='*60}")
    print(f"{BOLD}{CYAN}  {title}{RESET}")
    print(f"{'='*60}")


def step(num: int, who: str, text: str):
    role_color = YELLOW if "Customer" in who else GREEN
    print(f"\n  {BOLD}Step {num}:{RESET} {role_color}{who}{RESET} sends: \"{text}\"")


def info(msg: str):
    print(f"  {CYAN}→ {msg}{RESET}")


def check(msg: str):
    print(f"  {GREEN}✓ {msg}{RESET}")


def warn(msg: str):
    print(f"  {YELLOW}⚠ {msg}{RESET}")


# ============================================================
# TEST CASES
# ============================================================

async def tc1_consent_flow(client: httpx.AsyncClient):
    """TC-1: Customer Consent Flow (First Contact)"""
    header("TC-1: Customer Consent Flow")
    info("Pre-condition: Customer has no consent. Run reset_test.py --all first.")
    input(f"  {YELLOW}Press Enter to start (make sure you ran reset_test.py --all)...{RESET}")

    step(1, "Customer (virtual)", "שלום")
    r = await send_and_wait(client, CUSTOMER_CHAT_ID, "שלום", "עדי")
    check(f"Webhook accepted: {r}")
    info("Check worker logs: should see consent request sent to customer")
    info("Check Redis: state:972523651414@c.us = AWAITING_CONSENT")

    step(2, "Customer (virtual)", "בלה בלה")
    r = await send_and_wait(client, CUSTOMER_CHAT_ID, "בלה בלה", "עדי")
    check(f"Webhook accepted: {r}")
    info("Check worker logs: consent request repeated")

    step(3, "Customer (virtual)", "כן")
    r = await send_and_wait(client, CUSTOMER_CHAT_ID, "כן", "עדי")
    check(f"Webhook accepted: {r}")
    info("Check worker logs: consent accepted, state cleared")


async def tc2_consent_decline(client: httpx.AsyncClient):
    """TC-2: Customer Consent Decline"""
    header("TC-2: Customer Consent Decline")
    info("Pre-condition: Clear customer consent first.")

    step(1, "Customer (virtual)", "שלום")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "שלום", "עדי")

    step(2, "Customer (virtual)", "לא")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "לא", "עדי")
    info("Check worker logs: consent declined")


async def tc3_full_happy_path(client: httpx.AsyncClient):
    """TC-3: Full Lead Flow — Customer → Pro match → Pro approves"""
    header("TC-3: Full Happy Path (Customer → Pro → Deal)")
    info("Pre-condition: Customer has consent, state is idle.")
    info(f"Your phone ({PRO_PHONE}) will receive REAL WhatsApp messages!")
    input(f"  {YELLOW}Press Enter to start...{RESET}")

    step(1, "Customer (virtual)", "היי יש לי נזילה")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "היי יש לי נזילה", "עדי")
    info("Bot should ask about the problem. Check worker logs.")

    step(2, "Customer (virtual)", "נוזל מים מהברז במטבח כבר יומיים")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "נוזל מים מהברז במטבח כבר יומיים", "עדי")
    info("Bot should ask about location. Check worker logs.")

    step(3, "Customer (virtual)", "תל אביב")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "תל אביב", "עדי", wait=5)
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): You should receive '📢 הצעת עבודה חדשה'")
    info("Bot (as pro persona) should now ask the customer for full address + preferred time.")
    input(f"  {YELLOW}Read the bot's response above, then Press Enter to send the address...{RESET}")

    step(4, "Customer (virtual)", "ברקוביץ 4 תל אביב, יום חמישי ב-16:00")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "ברקוביץ 4 תל אביב, יום חמישי ב-16:00", "עדי", wait=5)
    info("Check logs: is_deal=true → _finalize_deal runs")
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): You should receive '✅ הלקוח אישר! פרטי העבודה'")

    step(5, f"Pro (YOUR phone {PRO_PHONE})", "אשר")
    info("NOW: Send 'אשר' from your real phone to the bot!")
    input(f"  {YELLOW}Press Enter AFTER you sent 'אשר' from your phone...{RESET}")
    await asyncio.sleep(3)
    info("Check logs: lead status → BOOKED, customer notified with pro details")
    check("TC-3 complete!")


async def tc4_pro_reject(client: httpx.AsyncClient):
    """TC-4: Pro Rejects Lead"""
    header("TC-4: Pro Rejects Lead")
    info("Pre-condition: Run TC-3 up to step 4 (lead exists with status NEW)")

    step(1, "Customer (virtual)", "היי יש לי נזילה בתל אביב, מהברז במטבח")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "היי יש לי נזילה בתל אביב, מהברז במטבח", "עדי", wait=5)
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): early notification should arrive")
    info("Bot (as pro) should be asking for address and time.")
    input(f"  {YELLOW}Press Enter to send address+time...{RESET}")

    step(2, "Customer (virtual)", "ברקוביץ 4, מחר ב-10:00")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "ברקוביץ 4, מחר ב-10:00", "עדי", wait=5)
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): '✅ הלקוח אישר' should arrive now")

    step(3, f"Pro ({PRO_PHONE})", "דחה")
    info("Send 'דחה' from your phone!")
    input(f"  {YELLOW}Press Enter AFTER you sent 'דחה'...{RESET}")
    await asyncio.sleep(3)
    info("Check logs: lead status → REJECTED")


async def tc5_pro_finish(client: httpx.AsyncClient):
    """TC-5: Pro Finishes Job"""
    header("TC-5: Pro Finishes Job")
    info("Pre-condition: Lead exists with status BOOKED (run TC-3 first)")

    step(1, f"Pro ({PRO_PHONE})", "סיימתי")
    info("Send 'סיימתי' from your phone!")
    input(f"  {YELLOW}Press Enter AFTER you sent 'סיימתי'...{RESET}")
    await asyncio.sleep(3)
    info("Check logs: lead status → COMPLETED, customer gets rating request")


async def tc6_rating_review(client: httpx.AsyncClient):
    """TC-6: Customer Rating + Review"""
    header("TC-6: Customer Rating + Review")
    info("Pre-condition: TC-5 completed (lead COMPLETED)")

    step(1, "Customer (virtual)", "5")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "5", "עדי")
    info("Check logs: rating saved, review request sent")

    step(2, "Customer (virtual)", "שירות מעולה, מקצועי ומהיר!")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "שירות מעולה, מקצועי ומהיר!", "עדי")
    info("Check logs: review saved")


async def tc7_pro_help(client: httpx.AsyncClient):
    """TC-7: Pro Help Menu"""
    header("TC-7: Pro Help Menu")

    step(1, f"Pro ({PRO_PHONE})", "בלה בלה")
    info("Send any random text from your phone")
    input(f"  {YELLOW}Press Enter AFTER you sent a message...{RESET}")
    await asyncio.sleep(3)
    warn("CHECK YOUR PHONE: should see the pro help menu")


async def tc8_reset(client: httpx.AsyncClient):
    """TC-8: Global Reset Command"""
    header("TC-8: Reset Command")

    step(1, "Customer (virtual)", "תפריט")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "תפריט", "עדי")
    info("Check logs: state cleared, reset message sent")

    step(2, f"Pro ({PRO_PHONE})", "reset")
    info("Send 'reset' from your phone")
    input(f"  {YELLOW}Press Enter AFTER you sent 'reset'...{RESET}")
    await asyncio.sleep(3)
    warn("CHECK YOUR PHONE: should see reset confirmation")


async def tc9_sos(client: httpx.AsyncClient):
    """TC-9: SOS / Human Handoff"""
    header("TC-9: SOS / Human Handoff")

    step(1, "Customer (virtual)", "נציג")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "נציג", "עדי")
    info("Check logs: SOS state set, admin notified")

    step(2, "Customer (virtual)", "sos")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "sos", "עדי")
    info("Check logs: SOS triggered again (note: 'עזרה' is now a reset command, use 'נציג' or 'sos' for SOS)")


async def tc12_idempotency(client: httpx.AsyncClient):
    """TC-12: Duplicate Message (Idempotency)"""
    header("TC-12: Idempotency Check")

    # Send same payload with same idMessage
    payload = make_text_payload(CUSTOMER_CHAT_ID, "בדיקת כפילות", "עדי")
    fixed_id = uuid.uuid4().hex[:24]
    payload["idMessage"] = fixed_id

    step(1, "Customer (virtual)", "בדיקת כפילות (first send)")
    r1 = await client.post(_webhook_url(), json=payload)
    info(f"Response 1: {r1.json()}")

    await asyncio.sleep(1)

    step(2, "Customer (virtual)", "בדיקת כפילות (duplicate)")
    r2 = await client.post(_webhook_url(), json=payload)
    result = r2.json()
    info(f"Response 2: {result}")
    if result.get("detail") == "duplicate":
        check("Idempotency works! Duplicate was rejected.")
    else:
        warn("Duplicate was NOT rejected — check idempotency logic")


# ============================================================
# TC-10: Pro Onboarding (Self-Registration)
# ============================================================

async def tc10_pro_onboarding(client: httpx.AsyncClient):
    """TC-10: Pro Onboarding — full self-registration flow"""
    header("TC-10: Pro Onboarding (Self-Registration)")
    info("Pre-condition: Use a phone NOT registered as pro, with consent given.")
    info("This test uses the CUSTOMER phone as the 'new pro' candidate.")

    step(1, "New Pro (virtual)", "הרשמה")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "הרשמה", "מועמד")
    info("Check logs: state = ONBOARDING_NAME, welcome message sent")

    step(2, "New Pro (virtual)", "בדיקה שרברב")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "בדיקה שרברב", "מועמד")
    info("Check logs: state = ONBOARDING_TYPE")

    step(3, "New Pro (virtual)", "1")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "1", "מועמד")
    info("Check logs: state = ONBOARDING_AREAS")

    step(4, "New Pro (virtual)", "תל אביב, רמת גן")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "תל אביב, רמת גן", "מועמד")
    info("Check logs: state = ONBOARDING_PRICES")

    step(5, "New Pro (virtual)", "ביקור 200, תיקון 400")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "ביקור 200, תיקון 400", "מועמד")
    info("Check logs: state = ONBOARDING_CONFIRM, summary shown")

    step(6, "New Pro (virtual)", "אשר")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "אשר", "מועמד")
    info("Check logs: pro created with pending_approval=true")
    check("TC-10 done — verify in MongoDB: db.users.findOne({business_name: 'בדיקה שרברב'})")
    warn("NOTE: TC-10 created a pending pro entry for customer phone.")
    warn("To run TC-11 cleanly, delete it first:")
    warn("  db.users.deleteOne({phone_number: '972523651414', pending_approval: true})")


# ============================================================
# TC-11: Pro Onboarding Cancel
# ============================================================

async def tc11_pro_onboarding_cancel(client: httpx.AsyncClient):
    """TC-11: Pro Onboarding Cancel"""
    header("TC-11: Pro Onboarding Cancel")
    info("Pre-condition: Customer phone must NOT have a pending pro registration.")
    warn("If you ran TC-10 before this, delete the pending entry first:")
    warn("  db.users.deleteOne({phone_number: '972523651414', pending_approval: true})")
    input(f"  {YELLOW}Press Enter once ready...{RESET}")

    step(1, "New Pro (virtual)", "הרשמה")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "הרשמה", "מועמד")
    info("Check logs: state = ONBOARDING_NAME")

    step(2, "New Pro (virtual)", "ביטול")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "ביטול", "מועמד")
    info("Check logs: state cleared, cancellation message sent")
    check("TC-11 done")


# ============================================================
# TC-13: Media Message (simulated with fake URL)
# ============================================================

async def tc13_media(client: httpx.AsyncClient):
    """TC-13: Media Message — image and audio simulation"""
    header("TC-13: Media Message (Image + Audio)")
    info("Sending fake media webhooks — AI will fail to download but flow is validated.")

    # Image with caption
    step(1, "Customer (virtual)", "[photo] + 'נזילה בתל אביב'")
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "idMessage": uuid.uuid4().hex[:24],
        "instanceData": {"idInstance": INSTANCE_ID, "wid": CUSTOMER_CHAT_ID},
        "senderData": {"chatId": CUSTOMER_CHAT_ID, "senderName": "עדי"},
        "messageData": {
            "typeMessage": "imageMessage",
            "fileMessageData": {
                "downloadUrl": "https://example.com/fake_leak.jpg",
                "caption": "נזילה בתל אביב",
                "mimeType": "image/jpeg",
                "fileName": "leak.jpg"
            }
        }
    }
    r = await client.post(_webhook_url(), json=payload)
    info(f"Webhook response: {r.json()}")
    await asyncio.sleep(5)
    info("Check logs: media_url present, AI processes image+text")

    # Audio message
    step(2, "Customer (virtual)", "[voice message]")
    payload["idMessage"] = uuid.uuid4().hex[:24]
    payload["messageData"] = {
        "typeMessage": "audioMessage",
        "fileMessageData": {
            "downloadUrl": "https://example.com/fake_voice.ogg",
            "caption": "",
            "mimeType": "audio/ogg",
            "fileName": "voice.ogg"
        }
    }
    r = await client.post(_webhook_url(), json=payload)
    info(f"Webhook response: {r.json()}")
    await asyncio.sleep(5)
    info("Check logs: audio media_url present, AI attempts transcription")
    check("TC-13 done (check logs for media handling)")


# ============================================================
# TC-PRO: Pro Menu Commands
# ============================================================

async def tc_pro_commands(client: httpx.AsyncClient):
    """TC-PRO: Test all pro menu commands"""
    header("TC-PRO: Pro Menu Commands")
    info(f"All commands sent from your real phone ({PRO_PHONE}).")
    info("Each step: send the command from your phone, then press Enter.")

    commands = [
        ("תפריט", "תפריט איש המקצוע"),
        ("עזרה",  "תפריט איש המקצוע (alias)"),
        ("4",     "עבודות פעילות"),
        ("5",     "היסטוריה"),
        ('דו"ח',  "סטטיסטיקות"),
        ("7",     "ביקורות"),
    ]

    for cmd, desc in commands:
        step(0, f"Pro ({PRO_PHONE})", cmd)
        info(f"Expected: {desc}")
        input(f"  {YELLOW}Send '{cmd}' from your phone, then Press Enter...{RESET}")
        await asyncio.sleep(3)
        warn(f"CHECK YOUR PHONE: should see {desc}")

    check("TC-PRO done — verify all 6 commands returned correct responses")


# ============================================================
# FULL HAPPY PATH (automated, minimal interaction)
# ============================================================

async def full_lifecycle(client: httpx.AsyncClient):
    """Run the complete lifecycle: consent → lead → deal → approve → finish → rating"""
    header("FULL LIFECYCLE TEST")
    info(f"This tests the complete flow. Your phone ({PRO_PHONE}) will receive messages.")
    info("You'll need to interact at 2 points: 'אשר' and 'סיימתי'")
    input(f"  {YELLOW}Press Enter to begin...{RESET}")

    # 1. Consent
    print(f"\n{BOLD}--- Phase 1: Consent ---{RESET}")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "שלום", "עדי")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "כן", "עדי")
    check("Consent accepted")

    # 2. Customer conversation
    print(f"\n{BOLD}--- Phase 2: Customer Conversation ---{RESET}")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "היי, יש לי נזילה מהברז במטבח", "עדי")
    info("Bot should ask follow-up questions about the problem.")

    await send_and_wait(client, CUSTOMER_CHAT_ID, "כבר יומיים, לא דחוף אבל מפריע", "עדי")
    info("Bot should ask about location.")

    await send_and_wait(client, CUSTOMER_CHAT_ID, "אני בתל אביב", "עדי", wait=5)
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): early notification '📢 הצעת עבודה חדשה' should have arrived")
    info("Bot (as pro persona) is now asking the customer for full address + preferred time.")
    input(f"  {YELLOW}Read the bot's response in worker logs, then Press Enter to send address...{RESET}")

    # 3. Deal closure
    print(f"\n{BOLD}--- Phase 3: Deal Closure ---{RESET}")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "ברקוביץ 4 תל אביב, יום חמישי ב-16:00", "עדי", wait=5)
    warn(f"CHECK YOUR PHONE ({PRO_PHONE}): '✅ הלקוח אישר! פרטי העבודה' should arrive now")

    # 4. Pro approves
    print(f"\n{BOLD}--- Phase 4: Pro Approval ---{RESET}")
    info("Send 'אשר' from your phone NOW")
    input(f"  {YELLOW}Press Enter after sending 'אשר'...{RESET}")
    await asyncio.sleep(4)
    check("Lead should be BOOKED")

    # 5. Pro finishes
    print(f"\n{BOLD}--- Phase 5: Pro Finishes ---{RESET}")
    info("Send 'סיימתי' from your phone NOW")
    input(f"  {YELLOW}Press Enter after sending 'סיימתי'...{RESET}")
    await asyncio.sleep(4)
    check("Lead should be COMPLETED")

    # 6. Customer rates
    print(f"\n{BOLD}--- Phase 6: Rating + Review ---{RESET}")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "5", "עדי")
    await send_and_wait(client, CUSTOMER_CHAT_ID, "שירות מעולה!", "עדי")
    check("Rating + review saved")

    print(f"\n{'='*60}")
    print(f"{BOLD}{GREEN}  ✅ FULL LIFECYCLE TEST COMPLETE!{RESET}")
    print(f"{'='*60}")
    info("Verify in worker logs that all steps completed successfully.")
    info("Verify in MongoDB: db.leads.find().sort({created_at:-1}).limit(1)")


# ============================================================
# RUNNER
# ============================================================

TESTS = {
    "tc2":   ("Consent Decline",           tc2_consent_decline),   # runs first: no consent → decline
    "tc1":   ("Consent Flow",              tc1_consent_flow),      # runs second: consent=False → re-ask → accept
    "tc3":   ("Full Happy Path",           tc3_full_happy_path),
    "tc4":   ("Pro Reject",                tc4_pro_reject),
    "tc5":   ("Pro Finish",                tc5_pro_finish),
    "tc6":   ("Rating + Review",           tc6_rating_review),
    "tc7":   ("Pro Help Menu",             tc7_pro_help),
    "tc8":   ("Reset Command",             tc8_reset),
    "tc9":   ("SOS Handoff",               tc9_sos),
    "tc12":  ("Idempotency",               tc12_idempotency),   # must run before tc10 (no pending pro)
    "tc13":  ("Media Message",             tc13_media),         # must run before tc10 (no pending pro)
    "tc10":  ("Pro Onboarding",            tc10_pro_onboarding),
    "tc11":  ("Pro Onboarding Cancel",     tc11_pro_onboarding_cancel),
    "tcpro": ("Pro Menu Commands",         tc_pro_commands),
    "full":  ("Full Lifecycle",            full_lifecycle),
}


async def main():
    global BASE_URL, INSTANCE_ID, WEBHOOK_TOKEN

    parser = argparse.ArgumentParser(description="Simulate Proli test flow via webhook")
    parser.add_argument("--test", type=str, default="full",
                        help="Test(s) to run: tc1,tc3,tc5 or 'full' or 'all'")
    parser.add_argument("--list", action="store_true", help="List available tests")
    parser.add_argument("--base-url", type=str, default=BASE_URL, help="Server URL")
    parser.add_argument("--instance-id", type=int, default=INSTANCE_ID, help="Green API instance ID")
    parser.add_argument("--token", type=str, default=WEBHOOK_TOKEN, help="Webhook token (overrides .env)")
    parser.add_argument("--mongo-uri", default=os.getenv("MONGO_URI") or os.getenv("MONGODB_URI"))
    parser.add_argument("--db", default=os.getenv("MONGO_DB_NAME"))
    args = parser.parse_args()

    if args.list:
        print(f"\n{BOLD}Available tests:{RESET}")
        for key, (name, _) in TESTS.items():
            print(f"  {GREEN}{key:6s}{RESET} — {name}")
        print(f"\n  Usage: python tests/simulate_test.py --test tc1,tc3")
        print(f"         python tests/simulate_test.py --test all")
        print(f"         python tests/simulate_test.py --test full")
        return

    BASE_URL = args.base_url
    INSTANCE_ID = args.instance_id
    WEBHOOK_TOKEN = args.token

    # Database setup for pre-verification
    mongo_uri = args.mongo_uri
    db_name = args.db
    if mongo_uri and not db_name:
        try:
            _parsed = uri_parser.parse_uri(mongo_uri)
            db_name = _parsed.get("database") or "proli_db"
        except Exception:
            db_name = "proli_db"

    # Determine which tests to run
    if args.test == "all":
        test_keys = [k for k in TESTS if k != "full"]
    else:
        test_keys = [t.strip() for t in args.test.split(",")]

    # Validate
    for k in test_keys:
        if k not in TESTS:
            print(f"{RED}Unknown test: {k}{RESET}")
            print(f"Use --list to see available tests")
            return

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}{CYAN}  Proli Test Simulator{RESET}")
    print(f"{BOLD}{'='*60}{RESET}")
    print(f"  Server:   {BASE_URL}")
    print(f"  Instance: {INSTANCE_ID}")
    print(f"  Customer: {CUSTOMER_PHONE} (virtual — no phone needed)")
    print(f"  Pro:      {PRO_PHONE} (your real phone)")
    print(f"  Tests:    {', '.join(test_keys)}")

    # Preflight Check: Health + MongoDB Pro
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            r = await client.get(f"{BASE_URL}/health")
            health = r.json()
            if health.get("status") == "healthy":
                print(f"  Health:   {GREEN}✓ healthy{RESET}")
            else:
                print(f"  Health:   {RED}✗ {health.get('status')}{RESET}")
        except Exception:
            print(f"  Health:   {RED}✗ Server not reachable at {BASE_URL}{RESET}")
            return

    if mongo_uri:
        try:
            ca_file = certifi.where() if "+srv" in mongo_uri else None
            kwargs = {"tlsCAFile": ca_file} if ca_file else {}
            m_client = AsyncIOMotorClient(mongo_uri, **kwargs)
            m_db = m_client[db_name]
            pro = await m_db.users.find_one({"phone_number": PRO_PHONE, "role": "professional"})
            if not pro:
                print(f"  Pro Check: {RED}✗ Pro {PRO_PHONE} not found in DB {db_name}{RESET}")
                print(f"             Run 'python scripts/seed_db.py' first.")
                m_client.close()
                return
            if not pro.get("is_active"):
                print(f"  Pro Check: {YELLOW}⚠ Pro {PRO_PHONE} found but is_active=False{RESET}")
            else:
                print(f"  Pro Check: {GREEN}✓ Pro seeded and active{RESET}")
            m_client.close()
        except Exception as e:
            print(f"  Pro Check: {YELLOW}⚠ Could not verify pro in DB (skipping check): {e}{RESET}")

    async with httpx.AsyncClient(timeout=30) as client:
        for key in test_keys:
            name, func = TESTS[key]
            try:
                await func(client)
                print(f"\n  {GREEN}✓ {name} — done{RESET}")
            except KeyboardInterrupt:
                print(f"\n  {YELLOW}⚠ Interrupted{RESET}")
                return
            except Exception as e:
                print(f"\n  {RED}✗ {name} — failed: {e}{RESET}")

    print(f"\n{BOLD}{'='*60}{RESET}")
    print(f"{BOLD}  All done! Check worker logs for details.{RESET}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    asyncio.run(main())
