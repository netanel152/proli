# CI workflows

What each workflow does, what a red run means, and the incidents behind the odd-looking parts. The YAML keeps only the traps. `tests/test_ci_workflows.py` pins the hardening; each release-path workflow has its own test file that runs its step bodies verbatim.

| Workflow | Trigger | Secrets and vars | A red run means |
|---|---|---|---|
| `tests.yml` | PRs, push to `dev`/`production` | none | A guard, actionlint, a linter or the suite failed, or the PR passes fewer tests than `dev` |
| `🤖 claude-review.yml` | PRs to `dev`/`production` (not drafts) | `ANTHROPIC_API_KEY` or `CLAUDE_CODE_OAUTH_TOKEN` (also as a Dependabot secret) | The review found a BLOCKER, returned no verdict, or no credential was visible (a fork PR never gets one) |
| `🔎 verify-staging-deploy.yml` | push to `dev` | `STAGING_HEALTH_TOKEN`; `STAGING_HEALTH_URL` (var, optional) | Staging did not serve this commit within 12 minutes; the deploy trigger is probably dead (`docs/RAILWAY_SETUP.md`) |
| `🚢 promote-to-production.yml` | manual | `PRODUCTION_HEALTH_URL` (var, optional) | Not a fast-forward, `dev` is not green (the annotation says which case), a short `skip_reason`, or production did not restart |
| `🛑 stop_railway_services.yml` | manual, branch is the target | `RAILWAY_TOKEN_PRODUCTION`, `RAILWAY_TOKEN_STAGING` | A named service is **still running**; stop it from the Railway dashboard |
| `🗺️ check_pro_service_areas.yml` | manual, branch is the target | `{PRODUCTION,STAGING}_MONGO_URI`, `{PRODUCTION,STAGING}_REDIS_URL`, `GOOGLE_MAPS_API_KEY` | A pro cannot be placed on the map, the geocoder was down, nothing was checked, or a secret is missing (PRO-189) |
| `🧰 backfill_pro_trades.yml` | manual, branch is the target | `{PRODUCTION,STAGING}_MONGO_URI`, `{PRODUCTION,STAGING}_REDIS_URL` | The backfill did not complete, or a secret is missing; unticked `apply` is read-only, and exit `1` on a dry run means pros still need it (PRO-234) |
| `🧪 prepare_staging_e2e.yml` | manual, `dev` only (staging; no production arm, `production` is refused by name) | `STAGING_MONGO_URI`, `STAGING_REDIS_URL`, `GOOGLE_MAPS_API_KEY` | The script refused (not `proli_staging_db`, an unusable input, an unreachable Redis, or another pro already holds the phone), the city could not be placed on the map, or the apply stopped part-way (safe to run again); a missing `STAGING_*` secret or a URI naming another database also fails it. Unticked `apply` is read-only |
| `🩺 health-leads-monitor.yml` | every 10 minutes, manual | `PRODUCTION_HEALTH_TOKEN`, `PRODUCTION_SENTRY_DSN` | See "Lead pipeline monitor" in `docs/OPERATIONS_GUIDE.md` |

## Conventions

Every workflow declares `permissions` (`contents: read`; promote adds what it needs to push and to read check runs, and the review job alone adds `pull-requests: write` at job level, to comment) and a job timeout, pins actions to a full SHA with a `# vX.Y.Z` comment, and passes secrets and step outputs to scripts through `env:`. Only promote's checkout keeps its credentials. Nothing uses `pull_request_target`: this repo is public, and `pull_request` never exposes secrets to a fork.

Dependabot bumps the action pins in one weekly PR. Its `pip` entry (`.github/dependabot.yml`) sets `open-pull-requests-limit: 0`, so no routine version-bump PR opens for `requirements.txt`; only security updates do (they ignore that limit), and only while "Dependabot security updates" is on in the repository's Code security settings. It cannot see the two versions that live in scripts, so bump those by hand: actionlint in `tests.yml` (version and SHA-256 from the release's checksums file) and `@railway/cli` in the stop workflow.

## The test floor

No file holds a test count, so no PR edits one. The floor is `dev`'s newest passed count, kept in the Actions cache.

- A push to `dev` whose suite passes saves the count under `pytest-passed-<sha>`. A push to `production` neither saves nor checks.
- A PR restores the newest `pytest-passed-*` from `dev` and **fails if it passes fewer tests**. Growth is silent.
- **Removing tests on purpose:** add the `allow-test-drop` label. The failure becomes a warning, and the next merge to `dev` lowers the floor. Adding or removing any label starts a new run, because a re-run replays the original event and would not see it.
- **No floor found** (first run, or evicted after a week without access): `::warning::No test floor was checked` and the job passes. A missing floor must not block every PR, so that warning is the only sign nothing was compared.
- An unreadable pytest summary fails the job on every event.

Why it can be trusted: per GitHub's [cache rules](https://docs.github.com/en/actions/reference/workflows-and-actions/dependency-caching), a PR run can restore from its base and the default branch, including from a fork, while a cache a PR saves stays scoped to its own merge ref. So a PR cannot lower the floor.

Known limits:

- **Re-run** tests the same merge commit against the current floor. If `dev` grew meanwhile, the run stays red until you merge `dev` and push; the error says so.
- Two `dev` merges finishing out of order leave the older count as the newest for a while, which errs lenient.
- Each label change costs a full run of the suite.

The `Current status: N passed` line this replaced was edited in 36 of the last 40 commits on `dev` before the change, and was the main source of merge conflicts between open PRs. Earlier versions: an equality check, which failed whichever of two test-adding PRs merged second; then a floor in the file, kept current by `refresh-test-baseline.yml`.

## Claude review

`claude-review.yml` is the review pass every PR needs before it merges; its job, `claude-review`, is a required check next to `pytest`. It runs `anthropics/claude-code-action` with the checklist from `.claude/agents/code-reviewer.md`, which the action restores from the base branch along with the rest of `.claude/` and `CLAUDE.md`, so a PR cannot rewrite the rules it is reviewed by. Claude gets read-only tools (`Read`, `Grep`, `Glob`, `gh pr diff`, `gh pr view`) plus inline comments; nothing from the PR is executed.

- **Green** means a review ran on this head commit and found no BLOCKER. WARNINGS and SUGGESTIONS are left as comments and do not block.
- **Red** means one of: at least one BLOCKER; the review did not return a verdict (re-run the job); or no credential was visible. The last is always the case for a fork PR, because `pull_request` never hands secrets to a fork — push the branch into this repo instead.
- One sticky comment per PR (marked `<!-- claude-review -->`) is edited in place on each push, so the latest verdict is the one shown.
- A draft is skipped and cannot merge anyway; marking it ready starts the run the merge waits for.
- Dependabot is allowed to trigger it (`allowed_bots`), but Dependabot runs only see Dependabot secrets, so the credential has to be added there too.
- Prompt injection: the PR's text and code can try to talk the reviewer into passing. The prompt tells it to report such text as a finding, but the check is a review pass, not a substitute for the human who merges.

## Why they look like this

**Promote proves the deploy.** On 2026-08-22 a promotion was fast-forwarded three times; the ref moved every time and Railway picked it up once. PRO-128 is the same shape at length: production sat six weeks and 132 commits behind, crash-looping, while looking promoted. So a promotion that does not restart production fails. It deliberately never uses `railway up`, which uploads the runner's directory as a tarball that matches no commit.

**An unverified release cannot pass as a verified one (PRO-183).** `skip_deploy_check` was a checkbox, ticked on 9 of 10 promotions. Its replacement is a `skip_reason` of at least 10 characters, resolved in one unconditional step because a GitHub expression cannot trim a string. The run list shows only the run name and the conclusion, so `run-name` carries the warning; a `::warning::` never reaches the list. An unreachable health endpoint now records `unknown`, not 0: `uptime < 0` never comes true, so a real restart used to burn the full 10 minutes and report failure. Commit-SHA verification is a follow-up that needs `PRODUCTION_HEALTH_TOKEN`.

**Three reasons `pytest` can block a promotion.** On 2026-09-14 at 01:42 a run fired 45 seconds after a merge was annotated as an unverified commit when pytest was simply still going. Still running, never tested and completed without passing now read differently, and the first is tested first, because a re-run in flight also matches the last.

**Push runs of `tests.yml` are never cancelled.** Promote needs a `pytest` result on the dev SHA, and a concurrency group on the branch would lose one: GitHub drops a waiting run even with `cancel-in-progress: false`. So each push gets its own group.

**A stale environment looks like a quiet one (PRO-155).** Railway's deploy trigger for staging died and the dashboard kept showing SUCCESS, from variable-triggered restarts of a stale image. `verify-staging-deploy.yml` compares the pushed SHA with authenticated `/health`'s `commit`, the PRO-136-gated detail.

**The branch is the target.** The stop workflow's confirm box refused four runs in a row on 2026-09-08: a phone keyboard's trailing space, then the wrong word. A branch in the dropdown cannot be misspelled, and a feature branch can touch nothing. The service-areas check copies the shape, with a checkbox for the same reason.

**The E2E preparation has no production arm.** It resets chats and writes pros, so it reads only the `STAGING_*` secrets, runs from `dev` and from nowhere else, and checks the database name twice: in the workflow before the install, and in `scripts/prepare_staging_e2e.py`. The second check is the one that holds, because the workflow runs with `ENVIRONMENT=development` like its siblings and so cannot rely on the environment name. The handset numbers are masked in the job log first, since GitHub prints a step's `env:` values.

**A green stop must mean stopped.** A blanket `|| echo "no active deployment"` once swallowed a rejected token, then printed "All services stopped" (measured on staging 2026-09-22 and 2026-10-06). "No deployments found" is now the only failure that counts as success.

**The Railway CLI is pinned.** An unpinned install changed the failure between 2026-10-06 (5.63.3, "Invalid RAILWAY_TOKEN") and 2026-10-28 (12 releases later, "error decoding response body") with no change here, so nobody could say which of token, CLI or API was at fault. 5.63.3 is the last version with a readable run.

**The service-areas check needs no Railway.** On 2026-09-10 a new project token was refused as "Invalid RAILWAY_TOKEN" through four rotations while an older one kept working, with everything in this repo ruled out. Its settings now come from repo secrets. A missing `MONGO_URI` refuses, because `Settings` defaults to localhost and would report "0 pros" as a clean result; it only prints the database name. `ENVIRONMENT` is `development` because the other values would fail closed on `HEALTH_TOKEN`, which the script does not use. The report is the evidence PRO-27's `launch-readiness` gate asks for: a merged script is not proof it ran.

**The egress guards are ratchets (PRO-85, PRO-86).** They have found nothing since landing, which is the point: code pasted from history cannot reopen the bypass behind the yellowCard incident. `*.md` has been excluded since 2026-09-14: prose naming the retired vendor is not a bypass, and scanning it only tripped the docs explaining the rule.

**`refresh-test-baseline.yml` is gone.** It re-ran the suite on every merge and never opened its PR: `gh pr create` is refused while *Allow GitHub Actions to create and approve pull requests* is off, so it pushed a branch and went red. Its one bug worth remembering (`290e624`, PRO-186): pytest omits zero clauses, so compare counts, never summary strings.
