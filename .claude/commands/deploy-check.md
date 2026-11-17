---
description: Pre-deployment checklist for Proli (tests, env vars, provider config, Docker build)
---

Pre-deployment checklist for Proli. Run through these checks:

1. Delegate the full suite to the **test-runner** subagent — do not run pytest in the main thread (it floods this context with output that belongs in an isolated one; see `.claude/commands/test.md`). All tests must pass. There is no known-failing whitelist.
2. Check `.env` has all required vars: GEMINI_API_KEY, CLOUDINARY_CLOUD_NAME, CLOUDINARY_API_KEY, CLOUDINARY_API_SECRET.
3. Check `META_APP_SECRET` and `META_VERIFY_TOKEN` are set and non-empty when `ENVIRONMENT` is `staging` or `production` — they are the only authentication on `GET`/`POST /webhook/meta`, the sole inbound route, and the api refuses to boot without them whatever `WHATSAPP_PROVIDER` says (PRO-270). A warning only in `development`. `WEBHOOK_TOKEN` is no longer read (the legacy `POST /webhook` route was removed in PRO-254): if it is still set, flag it for removal from Railway rather than as a failure.
4. Check `WHATSAPP_PROVIDER` (`dryrun` | `cloud`, default `dryrun`) against intent, and note whether `WHATSAPP_DRY_RUN=true` is overriding it — that combination means the deploy will transmit nothing. `cloud` is code-complete (PRO-89) but requires `META_ACCESS_TOKEN` + `META_PHONE_NUMBER_ID` to boot un-muted (plus `META_APP_SECRET` + `META_VERIFY_TOKEN` in staging/production), and there is no live Meta account until PRO-87 completes — so `dryrun` remains the only value that should reach a deploy today.
5. Check ENVIRONMENT is one of `development` | `staging` | `production`. Accept `staging` and `production` for a deploy; warn only if it is `development` (or unset). Any other value is a hard fail — the app raises at startup.
6. Verify `python scripts/create_indexes.py` has been run (check for index creation script)
7. Check Docker build: `docker-compose build --no-cache`
8. Report a pass/fail checklist summary.
