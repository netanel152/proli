---
description: Check the health of the running Proli system (API, Docker containers, worker logs)
allowed-tools: Bash(curl:*), Bash(docker-compose:*), Bash(docker:*), Read
---

Check the health of the running Proli system:

1. Hit `GET http://localhost:8000/health` and display the response. Note (PRO-136): the public response is only `status` + `uptime_seconds`; the per-dependency `checks` detail (MongoDB, Redis, worker latencies) requires the `X-Health-Token` header with the value of `HEALTH_TOKEN` from `.env` — send it if the token is configured locally.
2. Check if Docker containers are running: `docker-compose ps`
3. Check recent worker logs for errors: `docker-compose logs --tail=50 worker`
4. Report any issues found and suggest fixes.
