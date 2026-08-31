---
description: Show recent Proli logs (Docker or local), filtered for errors and warnings
allowed-tools: Bash(docker-compose:*), Bash(docker:*), Read, Grep
---

Show recent Proli logs, filtered by importance.

## Where the logs actually are

| environment | log of record | how to read it |
|---|---|---|
| local (bare processes) | `logs/proli.log` — rotating at 10 MB, retained 10 days | `Read`/`Grep` the file |
| local (docker-compose) | container stdout | `docker-compose logs --tail=100 worker` / `… api` |
| staging / production | container **stdout**, captured by Railway | Railway dashboard → service → Logs, or the `railway` MCP's `get_logs` |

**There is no `logs/proli.log` in staging or production** (PRO-174). The file
sink is development-only: on Railway it wrote to an ephemeral container
filesystem that is discarded on every deploy and restart and is unreachable
while the service runs, so it bought an archive nobody could read. Prod-like
environments emit structured JSON on stdout instead — one object per line,
with the fields below.

## Following one conversation turn

Every line emitted while handling one inbound message carries a **`trace_id`**
(PRO-174) — a 12-hex-char correlation id minted at the webhook and forwarded to
the ARQ job, so the API's lines and the worker's lines for the same turn share
it. It is random, tied to nothing: never the customer's phone, and not
derivable from it.

It renders differently per environment, because the sinks differ:

```bash
# local — human-readable, the id sits in a `trace=` column
grep 'trace=9448970be26b' logs/proli.log
# docker-compose — same column, on stdout
docker-compose logs worker | grep 'trace=9448970be26b'
# staging / production — structured JSON on stdout, trace_id a top-level
# field (PRO-184) — search Railway's log search for:
#   @trace_id:9448970be26b
```

`@level:error` / `@level:warn` work the same way against the `level` field.

A line logged outside a request or a task — a scheduler tick, a startup
line — shows `trace=-`. That is a line nobody minted an id for, not a bug.

## What to report

Filter for errors and warnings. Highlight:
- Failed WhatsApp sends
- AI/Gemini failures
- MongoDB connection issues
- Redis connection issues
- SOS alerts
- Rate limit hits

Summarize what's healthy and what needs attention.

Note that phone numbers are masked (`97252****567`), street addresses are
replaced with `***ADDRESS***`, and known secrets with `***REDACTED***` before
anything is written — a line that looks redacted is working as intended, not
truncated. Open the lead in the admin panel by its `lead=<id>` for the detail
the logs deliberately do not keep.
