---
description: Simulate a WhatsApp message to the local Proli backend for testing
argument-hint: [message text]
---

Simulate a WhatsApp message to the local Proli backend for testing.

Ask the user what message they want to send (or use the argument if provided: $ARGUMENTS).

Then send a Meta Cloud API webhook POST to `http://localhost:8000/webhook/meta` with the message text: a `whatsapp_business_account` payload with one `text` message, `from` set to a test phone number in bare-digit form (972501234567) and a unique `id` (the wamid; the route drops a repeated one as a duplicate). The quickest way is `python tests/simulate_webhook.py`, which builds and posts exactly that.

If the backend has `META_APP_SECRET` set, the body must carry `X-Hub-Signature-256: sha256=<HMAC-SHA256 of the raw body, keyed with META_APP_SECRET>`, or the route answers 403. The simulator signs automatically when `META_APP_SECRET` is in its environment. With no secret configured, a development backend accepts the body unsigned.

A processed message answers `{"status": "processing_message"}`; a duplicate or unsupported one answers `{"status": "ignored_type"}`. Show the response and then check worker logs for how the message was processed.

Note: To specifically test **SLA Deflection** (15m silence), use `python scripts/simulate_sla_deflection.py <phone>` (run with the project venv interpreter).
