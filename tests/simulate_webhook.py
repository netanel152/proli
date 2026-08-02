import httpx
import asyncio
import json
import os


async def simulate_message():
    print("--- WhatsApp Webhook Simulator ---")

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
    # Matches app.schemas.whatsapp.WebhookPayload
    payload = {
        "typeWebhook": "incomingMessageReceived",
        "senderData": {"chatId": chat_id, "senderName": "Simulator User"},
        "messageData": {
            "typeMessage": "textMessage",
            "textMessageData": {"textMessage": text},
        },
    }

    print(f"\nSending Payload:\n{json.dumps(payload, indent=2)}")

    # 3. Send Request
    url = "http://localhost:8000/webhook"

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(url, json=payload)

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
