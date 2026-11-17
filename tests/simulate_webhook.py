import asyncio
import hashlib
import hmac
import json
import os
import time

import httpx


async def simulate_message():
    print("--- WhatsApp Webhook Simulator (Meta Cloud API) ---")

    # 1. Get User Input
    text = input("Enter message text (e.g., 'Plumber in Tel Aviv'): ").strip()
    if not text:
        print("Message cannot be empty.")
        return

    # Optional: Allow overriding the chat ID.
    # The default MUST stay in the reserved test range (972 followed by 0 is not a
    # valid Israeli MSISDN, so it can never reach a subscriber). It used to be the
    # operator's real number, which meant an absent-minded Enter here produced a
    # genuine outbound WhatsApp from the pilot instance — the PRO-72 failure mode.
    default_chat_id = "972000000001@c.us"
    chat_id = (
        input(f"Enter Chat ID (default: {default_chat_id}): ").strip()
        or default_chat_id
    )

    # 2. Construct Payload
    # A Meta Cloud API "messages" webhook carrying one text message, the shape
    # app.providers.whatsapp.cloud_api.normalize_meta_message reads. Meta sends
    # the sender as bare digits, so any "@c.us" suffix is dropped here.
    sender = chat_id.split("@", 1)[0]
    wamid = f"wamid.SIM{int(time.time() * 1000)}"
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "SIMULATOR_WABA",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "contacts": [
                                {"wa_id": sender, "profile": {"name": "Simulator User"}}
                            ],
                            "messages": [
                                {
                                    "from": sender,
                                    "id": wamid,
                                    "timestamp": str(int(time.time())),
                                    "type": "text",
                                    "text": {"body": text},
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }

    print(f"\nSending Payload:\n{json.dumps(payload, indent=2, ensure_ascii=False)}")

    # 3. Sign and send. The route verifies X-Hub-Signature-256 over the raw body
    # whenever the server has META_APP_SECRET set; export the same value here.
    # Unset, the body goes unsigned, which only a development server accepts.
    url = "http://localhost:8000/webhook/meta"
    raw = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"}
    app_secret = os.environ.get("META_APP_SECRET")
    if app_secret:
        digest = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
        headers["X-Hub-Signature-256"] = f"sha256={digest}"
    else:
        print("META_APP_SECRET is not set: sending unsigned (development only).")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, content=raw, headers=headers)

        print(f"\nResponse Status: {response.status_code}")
        try:
            print(f"Response Body: {response.json()}")
        except json.JSONDecodeError:
            print(f"Response Body (Text): {response.text}")

    except httpx.ConnectError:
        print(f"\n❌ Connection Error: Could not connect to {url}")
        print("Make sure your server is running (uvicorn app.main:app --reload)")
    except Exception as e:
        print(f"\n❌ Error: {e}")


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(simulate_message())
