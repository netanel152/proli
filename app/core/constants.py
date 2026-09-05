from enum import Enum

# PRO-34: the deployment environments the project recognizes. `staging` is a
# first-class value — it behaves like production for anything observability- or
# safety-related (structured logs, PII filter, MONGO_URI required), while still
# being a legitimate target for destructive tooling like scripts/seed_db.py.
#
# Defined here rather than in app/core/config.py on purpose: constants.py has no
# dependencies, so admin_panel/ and scripts/ can import these without
# instantiating Settings() (which requires the full set of required env vars).
DEVELOPMENT_ENV = "development"
STAGING_ENV = "staging"
PRODUCTION_ENV = "production"

VALID_ENVIRONMENTS = (DEVELOPMENT_ENV, STAGING_ENV, PRODUCTION_ENV)

# PRO-86: the WhatsApp transports the provider facade can select. Declared here
# rather than in app/providers/whatsapp/ because app/core/config.py validates the
# value at boot and the providers package imports config — putting the names in
# the provider package would close that loop.
DRYRUN_PROVIDER = "dryrun"
CLOUD_PROVIDER = "cloud"
VALID_WHATSAPP_PROVIDERS = (DRYRUN_PROVIDER, CLOUD_PROVIDER)
# Environments that must behave production-like: JSON logs, PII masking,
# no local-DB fallback.
PROD_LIKE_ENVIRONMENTS = (STAGING_ENV, PRODUCTION_ENV)


def normalize_environment(value: str | None) -> str:
    """Lower-case and strip an ENVIRONMENT value. Empty/unset → ``development``.

    Does not validate — callers that need rejection of unknown values use the
    ENVIRONMENT validator in app/core/config.py. This keeps the raw-``os.getenv``
    call-sites (admin panel) reading the same normalized value the app does.
    """
    if value is None:
        return DEVELOPMENT_ENV
    return str(value).strip().lower() or DEVELOPMENT_ENV


def is_prod_like_env(value: str | None) -> bool:
    """True for ``staging`` and ``production``. Pure counterpart to
    ``settings.is_prod_like`` for code that cannot import Settings."""
    return normalize_environment(value) in PROD_LIKE_ENVIRONMENTS


class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    BOOKED = "booked"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PENDING_ADMIN_REVIEW = "pending_admin_review"


class Actor(str, Enum):
    """Who caused a lead status transition — recorded in each status_history entry."""

    SYSTEM = "system"  # scheduler / monitor / automated healing
    PRO = "pro"
    CUSTOMER = "customer"
    ADMIN = "admin"


class UserStates(str, Enum):
    IDLE = "idle"  # Default state
    PRO_MODE = "pro_mode"  # User is acting as a Professional
    CUSTOMER_MODE = "customer_mode"  # Pro temporarily acting as a customer
    AWAITING_INTENT_CONFIRMATION = "awaiting_intent_confirmation"  # Transient: waiting for 1/2 after intent detected
    AWAITING_ADDRESS = "awaiting_address"
    AWAITING_CONSENT = "awaiting_consent"  # Waiting for privacy consent
    AWAITING_PRO_APPROVAL = "awaiting_pro_approval"
    PAUSED_FOR_HUMAN = "paused_for_human"
    AWAITING_RESCHEDULE_TIME = "awaiting_reschedule_time"
    # Pro onboarding flow
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_TYPE = "onboarding_type"
    ONBOARDING_AREAS = "onboarding_areas"
    ONBOARDING_PRICES = "onboarding_prices"
    ONBOARDING_CONFIRM = "onboarding_confirm"
    # Admin routing wizard
    ADMIN_SELECTING_LEAD = "admin_selecting_lead"
    ADMIN_SELECTING_ACTION = "admin_selecting_action"
    ADMIN_SELECTING_PRO = "admin_selecting_pro"
    # Pro flow
    PRO_SELECTING_JOB_TO_FINISH = "pro_selecting_job_to_finish"
    PRO_SELECTING_JOB_TO_CANCEL = "pro_selecting_job_to_cancel"
    # PRO-33: after a job is marked COMPLETED, the pro is asked what they charged.
    # Skippable and never gates completion — the lead is already COMPLETED when
    # this state is set, so a missed/garbage reply just leaves final_price null.
    PRO_AWAITING_FINAL_PRICE = "pro_awaiting_final_price"
    # Loyalty flow
    AWAITING_LOYALTY_CONFIRMATION = "awaiting_loyalty_confirmation"
    # PRO-116: a customer with a confirmed BOOKED job messages about something
    # else — ask whether it's a new request or about the existing job, instead
    # of silently spawning a second parallel lead.
    AWAITING_NEW_OR_EXISTING = "awaiting_new_or_existing"
    # PRO-118: a cancel keyword on a confirmed BOOKED job asks for explicit
    # confirmation ('1'/'2') instead of cancelling on the first keyword hit —
    # the destructive action never fires off a possibly-misread word.
    AWAITING_CANCEL_CONFIRMATION = "awaiting_cancel_confirmation"


# PRO-185 — unix timestamp of the last successful nightly backup. Written by
# `scheduler.run_daily_backup` on a zero exit, read by the worker watchdog and
# by the authenticated `/health` `checks.backup` block. Lives here rather than
# in scheduler.py so the API never has to import the scheduler to read it.
BACKUP_LAST_SUCCESS_KEY = "backup:last_success"


class WorkerConstants:
    MAX_PRO_LOAD = 3
    DB_QUERY_LIMIT = 100
    SLOT_DURATION_HOURS = 1
    SLOT_SEARCH_WINDOW_HOURS = 2
    DEFAULT_CURRENCY = "ILS"
    SOS_TIMEOUT_MINUTES = 60
    # PRO-162: how long a stuck lead stays quiet after the SOS Reporter has
    # paged the operator about it once. The Reporter fires every 4 hours and
    # used to page CRITICAL for *every* lead over SOS_TIMEOUT_MINUTES on every
    # tick, so a single unresolvable lead produced an unbounded stream of
    # pages (Sentry PYTHON-Y: 20 fatals over 4 days for one staging lead).
    # A lead is paged once, then again only after this interval, so a genuinely
    # forgotten lead still resurfaces without drowning the one guaranteed
    # out-of-band operator channel.
    SOS_REPORT_REPAGE_HOURS = 24
    # Max reassignment attempts before the lead is escalated to
    # PENDING_ADMIN_REVIEW for a human to take over (PRO-63 — it is not closed).
    MAX_REASSIGNMENTS = 3
    # PRO-63: how long a PENDING_ADMIN_REVIEW lead keeps short-circuiting the
    # customer's chat. The short-circuit exists to stop a waiting customer
    # spawning duplicate leads, but it has no natural end — if no admin ever
    # acts, the customer can never open a new request. After this window their
    # next message starts a fresh request; the stale lead stays in
    # PENDING_ADMIN_REVIEW for the admin (it is never auto-closed).
    PENDING_REVIEW_SHORTCIRCUIT_HOURS = 24
    # PRO-56 approval-SLA: chase a silent pro fast instead of waiting for the
    # 60-min Healer. Emergency leads use half these thresholds (// 2 → 5 / 12).
    APPROVAL_NUDGE_MINUTES = 10  # T+10 → nudge the pro (once per lead)
    APPROVAL_REASSIGN_OFFER_MINUTES = 25  # T+25 → offer the customer a reassignment
    APPROVAL_SLA_CHECK_INTERVAL_MINUTES = 5  # how often the SLA job runs
    UNASSIGNED_LEAD_TIMEOUT_HOURS = (
        24  # Auto-reject CONTACTED leads with no pro after this
    )
    MAX_PRO_REMINDERS = 3  # Max reminder messages sent to a pro for a stale booked lead
    # PRO-176 — the nudger also runs once shortly after every worker boot, so
    # the count cap alone no longer bounds *rate*: on a deploy-heavy day three
    # boots minutes apart would burn all MAX_PRO_REMINDERS at once. A lead is
    # re-nudged only once this many hours have passed since its last reminder
    # (= the job's own interval, so steady-state cadence is unchanged).
    STALE_LEAD_REMINDER_COOLDOWN_HOURS = 4
    # Customer-side equivalent of MAX_PRO_REMINDERS. The stale-job monitor re-runs
    # every 30 min and a BOOKED lead stays selectable for the whole 6–24h Tier-2
    # window, so without a cap + cooldown every open lead re-sent the completion
    # check on every tick (once per open lead, per tick).
    # Per *booking*, not per lead: `reassign_lead` clears the counters when the
    # lead gets a fresh owner (PRO-45), so a lead reassigned MAX_REASSIGNMENTS
    # times can be nudged this many times about each pro in turn.
    MAX_CUSTOMER_COMPLETION_CHECKS = 2
    CUSTOMER_COMPLETION_CHECK_COOLDOWN_HOURS = 6  # Min gap between two auto checks
    # PRO-122: how many times an unparseable reply to the 1-5 rating prompt is
    # re-prompted before `waiting_for_rating` is released. The flag never clears
    # on its own, so an unbounded re-prompt would trap a customer who answers the
    # closing question with a new request instead of a number.
    MAX_RATING_REPROMPTS = 2
    # PRO-122: how long the 1-5 rating prompt stays live. `waiting_for_rating`
    # has no other expiry, and the ordinary outcome is silence, so without a
    # window a prompt ignored months ago still captures the next bare digit the
    # customer types -- landing a rating on the wrong (long-closed) job and
    # leaving the current one uncompleted.
    RATING_PROMPT_MAX_AGE_HOURS = 48
    # PRO-45: how long the completion check stays answerable by the bare "3"
    # ("איש המקצוע לא הגיע"). Exactly the reasoning above, and it matters more
    # here than it does for a rating: `completion_check_sent_at` is stamped
    # 6-24h after the lead is created, never expires and is never unset, so
    # without a window a job booked for next week would have every stray "3"
    # from that chat read as "the pro never arrived" — releasing the slot,
    # taking the job away and permanently penalising a pro who has not yet
    # been given a chance to show up. `record_no_show` only ever increments.
    # Note the "2 / עדיין לא" decline restarts `completion_check_sent_at` to
    # push the cooldown forward, so answering "not yet" also keeps "3" live for
    # another window — the customer did just answer that menu.
    NO_SHOW_REPORT_MAX_AGE_HOURS = 48
    STALE_BOOKED_LEAD_HOURS = 24  # Threshold for considering a booked lead "stale"
    GEO_RADIUS_STEPS = [
        10000,
        20000,
        30000,
    ]  # Progressive search radius in meters (10km, 20km, 30km)
    PAUSE_TTL_SECONDS = 900  # 15 minutes — auto-expiry for PAUSED_FOR_HUMAN state
    # PRO-118: window for the customer to answer the "really cancel?" prompt
    # (AWAITING_CANCEL_CONFIRMATION). Expiry = the job silently stays booked.
    CANCEL_CONFIRM_TTL_SECONDS = 300
    # PRO-119: window for the customer to answer the loyalty offer ("want your
    # previous pro?"). Without it the state inherited the 4h default TTL and a
    # customer whose reply we failed to parse was trapped for hours.
    LOYALTY_CONFIRM_TTL_SECONDS = 300
    PRO_APPROVAL_TTL_SECONDS = (
        3600  # 60 min — pro must approve a finalized deal within this window
    )
    PRO_SEARCH_RATE_LIMIT_SECONDS = (
        600  # 10 min — per-pro cool-down on proactive "מצא" command
    )
    # PRO-33 monetization: platform take-rate applied to a recorded final_price to
    # derive commission_amount. GMV = sum(final_price); commission = sum(commission_amount).
    COMMISSION_RATE = 0.10  # 10% — configurable take-rate for unit economics
    FINAL_PRICE_TTL_SECONDS = (
        600  # 10 min — window for the pro to answer "how much did you charge?"
    )
    # PRO-21 — abuse / cost protection (customers only; pros & admin exempt)
    INBOUND_RATE_LIMIT_MAX = 20  # max inbound messages per sliding window
    INBOUND_RATE_LIMIT_WINDOW_SECONDS = 60  # sliding-window size for the inbound limit
    DAILY_AI_CALL_CAP = 40  # max Gemini/multimodal calls per chat per day (Israel-time)
    RATE_LIMIT_ABUSE_TRIP_THRESHOLD = (
        3  # trips within a window → escalate to logger.error (stdout/file
        # only — loguru does not reach Sentry, PRO-113; not an operator page)
    )
    # PRO-20 — WhatsApp account deauth monitor (SPOF). The WhatsApp instance
    # is a single point of failure: if it loses authorization (phone offline,
    # ban, session drop) no message is processed. A worker job polls
    # getStateInstance and pages on sustained deauth.
    WA_STATE_CHECK_INTERVAL_MINUTES = 2  # how often the worker polls getStateInstance
    WA_STATE_ALERT_THRESHOLD_MINUTES = (
        5  # page only after the instance has been non-authorized > this long
    )
    WA_STATE_REALERT_MINUTES = 60  # re-page interval while the instance stays down
    # PRO-71 circuit breaker: TTL on the `wa:instance:paused` outbound-halt key.
    # Refreshed every WA_STATE_CHECK_INTERVAL_MINUTES tick while the instance is
    # non-authorized; if the monitor stops running the breaker auto-releases after
    # this window so a dead monitor never halts outbound forever (fail-open).
    WA_STATE_PAUSE_TTL_SECONDS = 360  # 6 min (3× the 2-min poll interval)
    # PRO-82/PRO-86: TTL on `wa:instance:state`, the *positive* confirmation that
    # the account was probed and found authorized. The outbound facade fails
    # CLOSED when this key is absent, so the value is the maximum time a send may
    # ride on a past probe. Same 6-minute window as the pause key, but the
    # polarity is inverted: an expired pause key releases the breaker, an expired
    # confirmation key engages it.
    WA_STATE_CONFIRM_TTL_SECONDS = 360  # 6 min (3× the 2-min poll interval)
    # PRO-89 — Meta Cloud API 24h customer-service window. Free-form messages
    # are deliverable only inside the 24 hours following the recipient's most
    # recent inbound message; outside it only a pre-approved template can be
    # sent. `wa:window:{chat_id}` is refreshed with this TTL on every inbound
    # message, so key-present == window-open by construction.
    SERVICE_WINDOW_TTL_SECONDS = 86400  # 24h — Meta's rule, not ours to tune
    # PRO-89 — cap on inbound Meta media the worker will download and re-host.
    # Meta allows documents/video up to 100MB; buffering that three times over
    # (download → BytesIO → Cloudinary) in a worker with max_jobs=10 is an OOM
    # waiting to happen, and nothing downstream (Gemini, the pro's offer
    # message) benefits from an asset that large.
    MAX_INBOUND_MEDIA_BYTES = 25 * 1024 * 1024  # 25MB
    # PRO-111 — nightly backup failure escalation. A single failed run stays
    # ERROR (transient: Mongo hiccup, S3 blip); this many CONSECUTIVE failures
    # logs CRITICAL, which is the Sentry paging threshold. Counter lives in
    # Redis (`backup:consecutive_failures`), cleared on the first success.
    BACKUP_FAILURE_ESCALATION_THRESHOLD = 2
    # PRO-185 — backup *freshness* watchdog, the other half of PRO-111. That
    # escalation is failure-triggered: it needs the job to run and fail. Every
    # path where the job never runs at all — worker down at 02:00, a misfired
    # cron tick, a stale scheduler lock — is silent, and silence is also what
    # a healthy night looks like. The watchdog pages when the last recorded
    # success (`backup:last_success`, written with NO TTL so an expired key
    # can never masquerade as "never ran") is older than this.
    BACKUP_MAX_AGE_HOURS = 48
    # How often the watchdog looks. Hourly is ample against a 48h threshold,
    # and it also runs once shortly after boot (PRO-176 boot slot).
    BACKUP_WATCHDOG_INTERVAL_MINUTES = 60
    # Re-page cadence while the condition stands: once per window via
    # `backup:stale_alert` SET NX EX, not once per tick — the PRO-162 lesson
    # and the WA_STATE_REALERT_MINUTES precedent.
    BACKUP_STALE_REALERT_HOURS = 24
    # A `backup:last_success` written by the worker is read by the API in
    # another container; sub-second NTP skew right after a backup would
    # otherwise read as "in the future" and flip /health to "unknown" (and
    # the watchdog to an ERROR line) until the clocks agreed again.
    BACKUP_CLOCK_SKEW_TOLERANCE_SECONDS = 300
    # PRO-112 — Mongo auth failures on scheduler jobs are a *config* failure
    # (rotated/deleted DB user), not a data condition: left at ERROR they
    # accumulate silently while the worker is effectively dead. This many auth
    # failures within the rolling window logs CRITICAL (the Sentry paging
    # threshold). A rolling window rather than a consecutive-failure streak:
    # outside business hours the PRO-73-gated jobs return early without
    # touching Mongo, and their "successes" would keep resetting a streak.
    SCHEDULER_MONGO_AUTH_TRIP_THRESHOLD = 3
    SCHEDULER_MONGO_AUTH_WINDOW_SECONDS = 1800  # 30 min rolling count window
    SCHEDULER_MONGO_AUTH_REALERT_SECONDS = 3600  # re-page hourly while broken
    # PRO-176 — an IntervalTrigger job's first run is one full interval after
    # scheduler.start(), and the in-memory job store forgets the countdown on
    # every restart. On a deploy-heavy day (17 merges on 2026-08-29, longest
    # gap 3h30) the 4h/6h jobs therefore never ran at all. The long-interval
    # jobs now run once shortly after boot: DELAY leaves room for the boot
    # Mongo/Redis probes (PRO-153) to settle; STAGGER spaces the long jobs'
    # first runs so a boot does not fire all of them in the same second.
    # MISFIRE_GRACE lets a tick that came due while the event loop was busy
    # run late rather than be dropped (APScheduler's default grace is 1s).
    SCHEDULER_BOOT_RUN_DELAY_SECONDS = 60
    SCHEDULER_BOOT_RUN_STAGGER_SECONDS = 45
    SCHEDULER_LONG_JOB_MISFIRE_GRACE_SECONDS = 600
    # ADMIN_PHONE moved to config.py / env var


# PRO-89 — Graph API error code on a send Meta rejected because the 24h
# customer-service window was closed. A `failed` delivery status carrying this
# code is re-routed through the template registry rather than merely logged.
META_ERROR_WINDOW_CLOSED = 131047


class APIStatus:
    RUNNING = "Proli is running! 🚀"
    PROCESSING = "processing_message"
    IGNORED_GROUP = "ignored_group"
    IGNORED_NO_DATA = "ignored_no_data"
    IGNORED_WRONG_INSTANCE = "ignored_wrong_instance"
    IGNORED_TYPE = "ignored_type"
    IGNORED_RATE_LIMIT = "ignored_rate_limit"
    ERROR = "error"


class Defaults:
    PENDING_TIME = "Pending"
    ASAP_TIME = "As soon as possible"
    # DEPRECATED — kept only so the migration script and monitor_service.py
    # backward-compat skip can reference the legacy sentinel. NEW CODE MUST
    # NOT WRITE THIS VALUE. full_address is now nullable; a missing address
    # should be represented as None. Scheduled for removal after the migration
    # sweep clears all historical docs (see scripts/migrate_unknown_address.py).
    UNKNOWN_ADDRESS = "Unknown Address"
    UNKNOWN_ISSUE = "Issue"
    GENERIC_PRO_NAME = "איש המקצוע"
    PROLI_PRO_NAME = "Proli Pro"
    EXPERT_NAME = "מומחה"
    DEFAULT_TRANSCRIPTION = "None"
    DEFAULT_MIME_TYPE = "image/jpeg"


class AdminDefaults:
    UNKNOWN_PRO = "Unknown"
    MANUAL_SOURCE = "manual_admin"
    MANUAL_LABEL = "Manual"
    DEFAULT_PLAN = "basic"
    DEFAULT_RATING = 5.0


ISRAEL_CITIES_COORDS = {
    # Hebrew names (primary - AI returns Hebrew)
    "תל אביב": [34.7818, 32.0853],
    "תל-אביב": [34.7818, 32.0853],
    "תל אביב יפו": [34.7818, 32.0853],
    'ת"א': [34.7818, 32.0853],
    "ירושלים": [35.2137, 31.7683],
    "חיפה": [34.9896, 32.7940],
    "ראשון לציון": [34.7925, 31.9730],
    "ראשלצ": [34.7925, 31.9730],
    "פתח תקווה": [34.8878, 32.0840],
    "פתח תקוה": [34.8878, 32.0840],
    "אשדוד": [34.6553, 31.8044],
    "נתניה": [34.8532, 32.3215],
    "באר שבע": [34.7913, 31.2518],
    "חולון": [34.7742, 32.0158],
    "בני ברק": [34.8254, 32.0849],
    "רמת גן": [34.8115, 32.0684],
    "רחובות": [34.8113, 31.8928],
    "בת ים": [34.7515, 32.0162],
    "אשקלון": [34.5715, 31.6690],
    "הרצליה": [34.8254, 32.1624],
    "כפר סבא": [34.9079, 32.1750],
    "חדרה": [34.9197, 32.4340],
    "מודיעין": [35.0145, 31.8903],
    "רעננה": [34.8674, 32.1848],
    "לוד": [34.8951, 31.9530],
    "רמלה": [34.8625, 31.9279],
    "נס ציונה": [34.7983, 31.9293],
    "הוד השרון": [34.8883, 32.1537],
    "גבעתיים": [34.8101, 32.0717],
    "קריית אתא": [35.1064, 32.8049],
    "קריית גת": [34.7715, 31.6064],
    "אילת": [34.9519, 29.5569],
    "עפולה": [35.2894, 32.6100],
    "טבריה": [35.5327, 32.7922],
    "נצרת": [35.3039, 32.6996],
    "עכו": [35.0736, 32.9276],
    "כרמיאל": [35.2961, 32.9114],
    "רהט": [34.7589, 31.3886],
    "יבנה": [34.7388, 31.8782],
    "אור יהודה": [34.8525, 32.0296],
    "צפת": [35.4975, 32.9648],
    "דימונה": [35.0335, 31.0683],
    "טירה": [34.9495, 32.2341],
    "קלנסווה": [34.9741, 32.2833],
    # English names (fallback)
    "tel aviv": [34.7818, 32.0853],
    "tel-aviv": [34.7818, 32.0853],
    "tlv": [34.7818, 32.0853],
    "jerusalem": [35.2137, 31.7683],
    "haifa": [34.9896, 32.7940],
    "rishon letsiyon": [34.7925, 31.9730],
    "rishon": [34.7925, 31.9730],
    "petah tikva": [34.8878, 32.0840],
    "ashdod": [34.6553, 31.8044],
    "netanya": [34.8532, 32.3215],
    "beersheba": [34.7913, 31.2518],
    "beer sheva": [34.7913, 31.2518],
    "holon": [34.7742, 32.0158],
    "bnei brak": [34.8254, 32.0849],
    "ramat gan": [34.8115, 32.0684],
    "rehovot": [34.8113, 31.8928],
    "bat yam": [34.7515, 32.0162],
    "ashkelon": [34.5715, 31.6690],
    "herzliya": [34.8254, 32.1624],
    "kfar saba": [34.9079, 32.1750],
    "hadera": [34.9197, 32.4340],
    "modiin": [35.0145, 31.8903],
    "raanana": [34.8674, 32.1848],
}
