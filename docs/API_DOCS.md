# Proli Backend API Documentation

## Base URL
Local: `http://localhost:8000`
Production: Your Railway or custom domain with HTTPS.

## Endpoints

### 1. Health Check
**GET** `/health`

Checks the health of all external dependencies (MongoDB, Redis, WhatsApp/Green API).

**Response (200 OK):**
```json
{
  "status": "healthy",
  "checks": {
    "mongodb": {"status": "up", "latency_ms": 4.2},
    "redis": {"status": "up", "latency_ms": 1.1},
    "worker": {"status": "up", "last_heartbeat": "1715000000.0"},
    "whatsapp": {"status": "up"}
  },
  "uptime_seconds": 3600
}
```

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

The main entry point for receiving messages from Green API.

**Headers:**
- `Content-Type: application/json`

**Request Body (Green API Standard):**
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
- **Webhook Token:** If `WEBHOOK_TOKEN` env var is set, requests must include `?token=<value>` in the query string. Requests with missing or invalid tokens receive `403 Forbidden`. Configure the full URL (with token) in the Green API dashboard.
- `idMessage` is used for idempotency (Redis `SET NX`, 24h TTL). Duplicate messages are ignored.
- `instanceData.idInstance` must match the configured `GREEN_API_INSTANCE_ID`.
- Rate limiting: 10 requests per 60 seconds per `chatId`.
- Group messages (`@g.us`) are silently ignored.

**Responses:**
All responses return `200 OK` to prevent Green API from retrying:

| Status | Meaning |
|---|---|
| `processing_message` | Task queued for worker |
| `ignored_group` | Group message, ignored |
| `ignored_no_data` | Missing sender or message data |
| `ignored_type` | Unsupported webhook type |
| `ignored_wrong_instance` | Instance ID mismatch (security) |
| `ignored_rate_limit` | Rate limit exceeded |
| `forbidden` | Invalid or missing webhook token (403) |
| `error` | Internal processing error |
