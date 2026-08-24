---
description: Pre-deployment checklist for Proli (tests, env vars, provider config, Docker build)
---

Pre-deployment checklist for Proli. Run through these checks:

1. Run `pytest --tb=short` — all tests must pass; compare against the "Current status" baseline in `docs/TESTING.md`. There is no known-failing whitelist.
2. Check `.env` has all required vars: GEMINI_API_KEY, CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET.
3. Check `WEBHOOK_TOKEN` is set. It is **required** when `ENVIRONMENT` is `staging` or `production` — the app refuses to boot without it, because PRO-86 removed the sender instance-id check and it is now the only thing authenticating `POST /webhook`. A warning only in `development`.
4. Check `WHATSAPP_PROVIDER` (`dryrun` | `cloud`, default `dryrun`) against intent, and note whether `WHATSAPP_DRY_RUN=true` is overriding it — that combination means the deploy will transmit nothing. `cloud` is code-complete (PRO-89) but requires `META_ACCESS_TOKEN` + `META_PHONE_NUMBER_ID` to boot un-muted (plus `META_APP_SECRET` + `META_VERIFY_TOKEN` in staging/production), and there is no live Meta account until PRO-87 completes — so `dryrun` remains the only value that should reach a deploy today.
5. Check ENVIRONMENT is one of `development` | `staging` | `production`. Accept `staging` and `production` for a deploy; warn only if it is `development` (or unset). Any other value is a hard fail — the app raises at startup.
6. Verify `python scripts/create_indexes.py` has been run (check for index creation script)
7. Check Docker build: `docker-compose build --no-cache`
8. Report a pass/fail checklist summary.
