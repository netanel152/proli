# Proli Backend API Documentation

## Base URL
Local: `http://localhost:8000`
Production: Your Railway or custom domain with HTTPS.

## Endpoints

### 1. Health Check
**GET** `/health`

Checks the health of all external dependencies (MongoDB, Redis, the WhatsApp provider).

**Response (200 OK):**
```json
{
  "status": "healthy",
  "checks": {
    "mongodb": {"status": "up", "latency_ms": 4.2},
    "redis": {"status": "up", "latency_ms": 1.1},
    "worker": {"status": "up", "last_heartbeat": "1715000000.0"},
    "whatsapp": {"status": "up", "state": "authorized", "provider": "cloud", "transmits": true}
  },
  "uptime_seconds": 3600
}
```

`whatsapp.status` is `up` (provider reports `authorized`), `degraded` (either the configured provider cannot transmit at all — e.g. `dryrun` — or a transmitting provider reports `yellowCard`), or `down` (not authorized/blocked/unreachable). `whatsapp.state` is the raw value returned by the provider's `get_state()` (PRO-86; was the legacy vendor's raw `stateInstance` value). `whatsapp.provider` is the configured provider's name and `whatsapp.transmits` is whether it can reach a real handset.

**Response (503 Service Unavailable):**
```json
{
  "status": "unhealthy",
  "checks": {
    "mongodb": {"status": "down", "latency_ms": null},
    "redis": {"status": "up", "latency_ms": 1.1},
    "worker": {"status": "no_heartbeat", "last_heartbeat": null},
    "whatsapp": {"status": "up"}
  },
  "uptime_seconds": 120
}
```

Critical components are MongoDB and Redis. If either is down, the endpoint returns 503.

### 3. Lead Pipeline Health
**GET** `/health/leads`

Business-level signal for the lead pipeline. Returns counts of stuck leads for monitoring.

**Response (200 OK):**
```json
{
  "status": "ok",
  "pending_review_count": 2,
  "stuck_contacted_count": 0,
  "stuck_threshold_hours": 24,
  "environment": "production",
  "checked_at": "2026-05-09T08:00:00+00:00"
}
```

Returns 503 if the database is unavailable. Use this endpoint with a synthetic monitor to alert when `pending_review_count > 5` for more than 30 minutes.

### 2. WhatsApp Webhook
**POST** `/webhook`

The legacy entry point for receiving inbound WhatsApp messages, still live alongside the PRO-89 Meta webhook below — the payload shape here is the historical legacy-vendor webhook envelope, unchanged by the PRO-86 provider-facade migration, which only touched outbound.

**Headers:**
- `Content-Type: application/json`

**Request Body:**
```json
{
  "typeWebhook": "incomingMessageReceived",
  "idMessage": "BAE5...",
  "instanceData": {
    "idInstance": 12345,
    "wid": "11001234567@c.us",
    "typeInstance": "whatsapp"
  },
  "senderData": {
    "chatId": "972500000000@c.us",
    "senderName": "Israel Israeli"
  },
  "messageData": {
    "typeMessage": "textMessage",
    "textMessageData": {
      "textMessage": "Hi, I have a leak in the kitchen."
    }
  }
}
```

**Supported Message Types:**
| Type | Data Field | Description |
|---|---|---|
| `textMessage` | `textMessageData.textMessage` | Plain text |
| `extendedTextMessage` | `extendedTextMessageData.text` | Text with URL preview |
| `locationMessage` | `locationMessageData` | Location pin (lat/lon/name/address) |
| `imageMessage` | `fileMessageData.downloadUrl` | Image with optional caption |
| `audioMessage` | `fileMessageData.downloadUrl` | Voice note |
| `videoMessage` | `fileMessageData.downloadUrl` | Video message |

**Security:**
- **Webhook Token:** the *only* authentication on this route (PRO-86 removed the sender instance-id check that used to be the other half). If `WEBHOOK_TOKEN` env var is set, requests must include `?token=<value>` in the query string. Requests with missing or invalid tokens receive `403 Forbidden`. Configure the full URL (with token) at your WhatsApp provider. `WEBHOOK_TOKEN` is **required** whenever `ENVIRONMENT` is `staging`/`production` — the app refuses to boot without it.
- `idMessage` is used for idempotency (Redis `SET NX`, 24h TTL). Duplicate messages are ignored.
- Rate limiting: 10 requests per 60 seconds per `chatId`.
- Group messages (`@g.us`) are silently ignored.

**Responses:**
All responses return `200 OK` to prevent the sender from retrying:

| Status | Meaning |
|---|---|
| `processing_message` | Task queued for worker |
| `ignored_group` | Group message, ignored |
| `ignored_no_data` | Missing sender or message data |
| `ignored_type` | Unsupported webhook type |
| `ignored_rate_limit` | Rate limit exceeded |
| `forbidden` | Invalid or missing webhook token (403) |
| `error` | Internal processing error |

### 4. Meta Cloud API Webhook
**GET / POST** `/webhook/meta`

The PRO-89 inbound entry point for the Meta Cloud API transport. Mounted alongside the legacy `/webhook` route above and live even when `WHATSAPP_PROVIDER=dryrun`, so Meta's subscription handshake and inbound delivery can be verified during PRO-87 onboarding while outbound stays muted.

**GET — subscription handshake:** Meta calls this once when the webhook is registered, with `hub.mode=subscribe`, `hub.verify_token`, and `hub.challenge` query params. Echoes `hub.challenge` back (`200`) iff `hub.verify_token` matches `META_VERIFY_TOKEN`; otherwise `403 forbidden`.

**POST — inbound messages and delivery statuses:**
- **Auth:** `X-Hub-Signature-256` — an HMAC-SHA256 of the raw request body keyed by `META_APP_SECRET`, checked in constant time. If `META_APP_SECRET` is unset, the request is rejected (`403`) in a prod-like environment and allowed through only in dev-like ones (the `cloud`-provider boot validator already refuses to start without the secret in prod-like environments).
- Opens the sender's 24h service window (`wa:window:{chat_id}` in Redis) on every inbound message, including stickers/reactions, before normalization.
- Delivery-status events (`sent`/`delivered`/`read`/`failed`) are applied first via `delivery.apply_status_event`; a `failed` with Meta error code `131047` (window closed) drives the template-retry path.
- Each normalized message is deduped (Redis `SET NX`, 24h TTL, same idempotency namespace as `/webhook`) and enqueued as a `process_message_task`.
- **Response policy:** `200` for everything decided (processed, duplicate, ignored, malformed body); `503` only for an infrastructure failure that prevented enqueueing, with the idempotency claim released so Meta's retry can land.
