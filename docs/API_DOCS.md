# Proli Backend API Documentation

## Base URL
Local: `http://localhost:8000`
Production: Your Railway or custom domain with HTTPS.

## Interactive docs

FastAPI's `/docs` (Swagger UI), `/redoc` and the `/openapi.json` schema behind them are served in development and staging only. With `ENVIRONMENT=production` all three return 404 (`app.main.api_docs_kwargs`), because the schema lists every route and payload shape of an internet-facing API.

## Endpoints

### 1. Health Check
**GET** `/health`

Checks the health of all external dependencies (MongoDB, Redis, the WhatsApp provider). PRO-136: the unauthenticated response carries only `status` and `uptime_seconds` — exactly what the Docker HEALTHCHECK and the promotion workflow's deploy verifier read. The per-dependency `checks` object (latencies, provider name, transmits flag, worker heartbeat) requires an `X-Health-Token` header matching the `HEALTH_TOKEN` env var; without it (or with an unset `HEALTH_TOKEN` in a prod-like environment) `checks` is simply omitted, not an error.

**Response (200 OK, unauthenticated):**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600
}
```

**Response (200 OK, with a valid `X-Health-Token` header):**
```json
{
  "status": "healthy",
  "uptime_seconds": 3600,
  "checks": {
    "mongodb": {"status": "up", "latency_ms": 4.2},
    "redis": {"status": "up", "latency_ms": 1.1},
    "worker": {"status": "up", "last_heartbeat": "1715000000.0"},
    "backup": {"status": "fresh", "last_success": "2026-09-05T00:12:34+00:00", "age_hours": 4.1, "max_age_hours": 48, "enabled": true, "platform_environment": "production"},
    "whatsapp": {"status": "up", "state": "authorized", "provider": "cloud", "transmits": true}
  }
}
```

`whatsapp.status` is `up` (provider reports `authorized`), `degraded` (either the configured provider cannot transmit at all — e.g. `dryrun` — or a transmitting provider reports `yellowCard`), or `down` (not authorized/blocked/unreachable). `whatsapp.state` is the raw value returned by the provider's `get_state()` (PRO-86; was the legacy vendor's raw `stateInstance` value). `whatsapp.provider` is the configured provider's name and `whatsapp.transmits` is whether it can reach a real handset.

`whatsapp` also carries the quality watchdog's fields (PRO-90): `quality_rating`, `messaging_tier`, `window_state`, `can_send_message`, `quality_poll`, `conditions`, `tightened`, `alerts_enabled`, `business_initiated_24h`, and `last_known` when the last poll did not succeed. A failed or stale poll reports `UNKNOWN`, never the last good value. These do not affect the top-level `status`. The state table is in `docs/RUNBOOK_WHATSAPP_OUTAGE.md` §8.

`backup` (PRO-185) reports the nightly backup's freshness, independent of the escalation in `docs/OPERATIONS_GUIDE.md`: `status` is `fresh`/`stale` (against `max_age_hours`), `never` (no successful backup recorded in either Redis or its Mongo mirror, `settings.backup_state`), or `unknown` (both stores unreachable, the stored value non-finite or `<= 0`, or `last_success` beyond clock-skew tolerance in the future). `last_success`/`age_hours` are `null` unless `status` is `fresh` or `stale`. `enabled` mirrors `settings.is_production` — the nightly backup and its watchdog are production-only, so `never` on staging is expected, not an alarm. `platform_environment` is Railway's `RAILWAY_ENVIRONMENT_NAME` (`null` outside Railway) — if it reads `production` while `enabled` is `false`, that is the PRO-96 misconfiguration and `never` **is** the alarm. This freshness value does not affect the top-level `status`.

**Response (503 Service Unavailable, authenticated):**
```json
{
  "status": "unhealthy",
  "uptime_seconds": 120,
  "checks": {
    "mongodb": {"status": "down", "latency_ms": null},
    "redis": {"status": "up", "latency_ms": 1.1},
    "worker": {"status": "no_heartbeat", "last_heartbeat": null},
    "whatsapp": {"status": "up"}
  }
}
```

Critical components are MongoDB and Redis. If either is down, the endpoint returns 503 regardless of authentication.

### 2. Lead Pipeline Health
**GET** `/health/leads`

Business-level signal for the lead pipeline. Returns counts of stuck leads for monitoring. Token-only (PRO-136): requests without a matching `X-Health-Token` header get `403 Forbidden` with `{"status": "forbidden"}` — there is no unauthenticated payload for this endpoint.

**Response (200 OK, with a valid `X-Health-Token` header):**
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

Returns 503 if the database is unavailable, with a fixed `{"status": "error", "error": "internal error"}` body — the real exception goes to logs/Sentry only, never the HTTP response. Polled every 10 minutes by the `🩺 Lead pipeline monitor` workflow, which alerts on `pending_review_count > 5` for more than 30 minutes and on a climbing `stuck_contacted_count` — see "Lead pipeline monitor" in `OPERATIONS_GUIDE.md`.

### 3. WhatsApp Webhook (Meta Cloud API)
**GET / POST** `/webhook/meta`

The inbound entry point for WhatsApp messages and delivery statuses (PRO-89), and the only one: the legacy `POST /webhook` route — the retired vendor's envelope, authenticated by a `?token=` query parameter (`WEBHOOK_TOKEN`) — was removed in PRO-254 and now answers `404`. Live even when `WHATSAPP_PROVIDER=dryrun`, so Meta's subscription handshake and inbound delivery can be verified during PRO-87 onboarding while outbound stays muted.

**GET — subscription handshake:** Meta calls this once when the webhook is registered, with `hub.mode=subscribe`, `hub.verify_token`, and `hub.challenge` query params. Echoes `hub.challenge` back (`200`) iff `hub.verify_token` matches `META_VERIFY_TOKEN`; otherwise `403 forbidden`.

**POST — inbound messages and delivery statuses:**
- **Auth:** `X-Hub-Signature-256` — an HMAC-SHA256 of the raw request body keyed by `META_APP_SECRET`, checked in constant time. If `META_APP_SECRET` is unset **or empty**, the request is rejected (`403`) in a prod-like environment and allowed through only in dev-like ones. An empty value counts as unset because an HMAC keyed with an empty string is one anyone can compute (PRO-270). The API also refuses to boot in a prod-like environment without a non-empty `META_APP_SECRET` and `META_VERIFY_TOKEN`, whatever `WHATSAPP_PROVIDER` says, because the route is served under `dryrun` too (`meta_webhook.require_inbound_secrets`, called first in the lifespan).
- Opens the sender's 24h service window (`wa:window:{chat_id}` in Redis) on every inbound message, including stickers/reactions, before normalization.
- Delivery-status events (`sent`/`delivered`/`read`/`failed`) are applied first via `delivery.apply_status_event`; a `failed` with Meta error code `131047` (window closed) drives the template-retry path.
- Each normalized message is deduped (Redis `SET NX` on `webhook:<wamid>`, 24h TTL) and enqueued as a `process_message_task`.
- **Response policy:** `200` for everything decided (processed, duplicate, ignored, malformed body); `503` only for an infrastructure failure that prevented enqueueing, with the idempotency claim released so Meta's retry can land.

### 4. Public Pages
**GET** `/privacy`, **GET** `/terms`

Static Hebrew-first HTML, no auth, `Cache-Control: public, max-age=3600`. `/terms` answers `404` until `TERMS_ENABLED=true` and `app/static/terms.html` has no draft banner or `[להשלים` placeholder left (`app/core/legal_links.py`).
