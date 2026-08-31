import sys
import os
import re
import json
import logging
import traceback
import uuid
from loguru import logger
from app.core.config import settings

# PII masking pattern: Israeli phone numbers (972XXXXXXXXX)
# Keeps country code + first 2 digits + last 3 digits, masks the middle.
# Example: 972521234567 → 97252****567
_PHONE_PATTERN = re.compile(r"(972\d{2})(\d+)(\d{3})")


def mask_pii(message: str) -> str:
    """Mask Israeli phone numbers in log messages."""
    return _PHONE_PATTERN.sub(r"\1****\3", message)


# PRO-80: secret values that must never appear in logs, redacted wherever they
# occur — a URL query string (uvicorn access log: `/webhook?token=<WEBHOOK_TOKEN>`),
# a URL path, or an exception string. Built once at import from settings;
# empty/unset secrets are skipped so nothing over-redacts.
#
# PRO-94 replaced the hand-maintained tuple (WEBHOOK_TOKEN only, after PRO-86
# dropped the Green token) with every SecretStr field on Settings. The old list
# carried a standing "remember to append the next credential here" comment —
# exactly the kind of instruction that gets missed. Typing a new field
# SecretStr now enrolls it in redaction automatically.
#
# Second layer by design: SecretStr makes the *repr* leak impossible, this
# catches a value that reached a log line by some other route (an httpx
# exception echoing a URL, a uvicorn access line). Neither alone is enough.
_SECRET_VALUES = settings.iter_secret_values()


def redact_secrets(message: str) -> str:
    """Replace any known secret value with a placeholder. Complements PRO-79
    (which suppressed httpx INFO request logs at the source): this is
    defense-in-depth for any *other* path that echoes a secret — the uvicorn
    access log line, an httpx exception string reaching logger.error, etc."""
    for secret in _SECRET_VALUES:
        if secret in message:
            message = message.replace(secret, "***REDACTED***")
    return message


# PRO-174: street-address masking. PRO-173 established that a customer's
# street address is at least as identifying as the phone number `mask_pii`
# already removes — it fixed the leak for operator pages by narrowing them to
# the city. The same leak class applies to any log line that carries a
# `full_address`: the webhook's "Location message from …: <address>", the
# lead-parse failure that echoes the whole `[DEAL: …]` string, the escalation
# warning that logs `full_address=<repr>`. This closes it at the sink, so it
# holds for the lines nobody thought about — and it lands *before* the
# external drain in PRO-175 rather than after.
#
# Hebrew alphabet only (א–ת). A Latin-script address the customer typed —
# "Herzl 15, Tel Aviv" — is NOT masked, and that is a known gap, not a
# covered case: the geocoder is asked for `language=he` so anything it
# returns is Hebrew, but `full_address` is an AI extraction of whatever the
# customer wrote. Latin has no delimiter this side of a real parser, so the
# honest statement is "Hebrew is covered", not "addresses are covered".
_HEB = "א-ת"
# Israeli street names carry all four apostrophe glyphs interchangeably
# (רח' / רח׳ / צה"ל / צה״ל) — users type the ASCII pair, Hebrew keyboards
# produce the geresh/gershayim.
_GERESH = "'\"׳״"

# One Hebrew token: 2+ letters with an optional geresh tail, OR a single
# letter that carries one. That second alternative is not an edge case —
# Israeli neighbourhood addresses are full of it ("רמת אביב ג' 12",
# "נווה שאנן ב'"), and requiring two letters let the whole address through:
# branch B needs the house number to follow the name directly, so one
# unmatchable token ahead of it drops the entire match.
_HEB_WORD = rf"(?:[{_HEB}]{{2,}}(?:[{_GERESH}][{_HEB}]*)?|[{_HEB}][{_GERESH}])"

# A house number: 1–3 digits standing alone. The lookarounds are load-bearing
# — without the `:` guard "מחר 10:00" (an appointment time, which every lead
# carries) reads as a street number and a genuinely useful log line is
# destroyed; without the `\d` guard a masked phone's trailing digits qualify.
_HOUSE_NUMBER = r"(?<![\d:.])\d{1,3}(?![\d:.])"

# Deliberately excludes דרך and מעלה: both are ordinary Hebrew words far more
# often than street prefixes, and "דרך מנחם בגין 12" is still caught by the
# bare-street branch below. Longest alternative first so רחוב is not eaten
# by רח.
_STREET_KEYWORD = (
    rf"רחוב|רח[{_GERESH}]|שדרות|שדרת|שד[{_GERESH}]|סמטת|סמטה|שכונת|כיכר|ככר"
)

# Horizontal whitespace only — never a newline. A bare `\s+` would let a
# Hebrew word ending one line and a number opening the next read as a
# street address, and multi-line records are routine here (the SOS report
# joins its lines with a newline): "stuck in חיפה" / "2 pros available"
# would have collapsed into a single line with the city, the count and the
# break between them all gone.
_WS = r"[^\S\r\n]+"

# Two branches, tried in order:
#   A. an explicit street prefix + 1–3 name words + an optional house number
#      ("רחוב הרצל 15", "שד' רוטשילד")
#   B. a bare 1–3 word street name immediately followed by a house number
#      ("הרצל 15", "בן גוריון 42", "רמת אביב ג' 12") — the form
#      `compose_full_address` emits.
# `(?<![HEB])` on both so a keyword is never matched mid-word (מדרך).
#
# The trailing ", <city>" is intentionally left alone. That is PRO-173's line
# exactly: the city is genuine triage context (it says which pro pool is
# short), the street is not, and `lead=<id>` remains the real lookup key.
_ADDRESS_PATTERN = re.compile(
    rf"(?<![{_HEB}]){{}}".format(
        rf"(?:(?:{_STREET_KEYWORD}){_WS}{_HEB_WORD}(?:{_WS}{_HEB_WORD}){{0,2}}"
        rf"(?:{_WS}{_HOUSE_NUMBER})?"
        rf"|{_HEB_WORD}(?:{_WS}{_HEB_WORD}){{0,2}}{_WS}{_HOUSE_NUMBER})"
    )
)


def mask_address(message: str) -> str:
    """Mask the identifying part of a Hebrew street address (PRO-174).

    Errs toward over-masking, on purpose. A false positive costs one mangled
    Hebrew fragment in a log line; a false negative ships a customer's front
    door to whatever the logs drain into. PRO-173 made the same call and wrote
    it down: degrading is "the right way to fail here".
    """
    return _ADDRESS_PATTERN.sub("***ADDRESS***", message)


def scrub(message: str) -> str:
    """Every scrubber, in the one order that is correct — the single
    definition ``_pii_filter`` and ``page_critical`` both call, so the two
    egresses cannot drift apart as scrubbers are added.

    Secrets first: a secret containing a ``972…`` digit run would otherwise be
    mangled by ``mask_pii`` and no longer match ``redact_secrets``. Addresses
    last, and unable to touch a masked phone either way — ``_HOUSE_NUMBER``
    refuses any digit run longer than three.
    """
    return mask_address(mask_pii(redact_secrets(message)))


def _scrub_value(value, _depth: int = 0):
    """Scrub every string reachable inside a bound extra, container included.

    PRO-184 widened this from the flat `isinstance(value, str)` check the
    original filter used. That check was true of every extra `app/` actually
    binds today — `trace_id` (str), `_stdlib`/`sentry_skip` (bool) — but a
    single `logger.bind(lead=lead_doc)` puts a raw phone number and a Hebrew
    street address past every scrubber in this module, and the prod sink now
    hoists extras to the top level, which is exactly where Railway indexes
    them as queryable attributes. The gap was latent under `serialize=True`
    too; hoisting is what raises the stakes.

    Numbers, bools and `None` are returned unchanged so a numeric extra stays
    numeric and Railway's numeric comparisons (`@duration_ms:>500`) keep
    working. Anything else — an arbitrary object that `json.dumps(default=str)`
    would render — is rendered here instead, so the rendering is scrubbed
    rather than the object slipping past as a string nobody looked at.

    `_depth` bounds the walk: a self-referential extra would otherwise recurse
    until the interpreter gives up, and a filter that raises takes the log
    line with it. Past the limit the value is rendered and scrubbed flat.
    """
    if isinstance(value, str):
        return scrub(value)
    if isinstance(value, bool) or isinstance(value, (int, float)) or value is None:
        return value
    if _depth >= _MAX_SCRUB_DEPTH:
        return scrub(str(value))
    if isinstance(value, dict):
        return {k: _scrub_value(v, _depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_scrub_value(v, _depth + 1) for v in value]
    return scrub(str(value))


# Deep enough for a lead document, shallow enough that a cyclic extra cannot
# outrun it. Cycles are also caught by the sink's serialization fallback; this
# is the first of the two guards, not the only one.
_MAX_SCRUB_DEPTH = 6


def _pii_filter(record):
    """Loguru sink filter: scrub every record before any sink writes it.

    Bound extras are scrubbed too, not just the message. The prod sink emits
    `record["extra"]` into the JSON line, and PRO-174 makes
    `logger.contextualize` the house pattern — so the next person to bind
    `chat_id=chat_id` would route a raw phone number straight past every
    scrubber in this module. `_stdlib` and `sentry_skip` are bools and pass
    through untouched; see `_scrub_value` for the non-string cases.
    """
    record["message"] = scrub(record["message"])
    # `.get`, not `record["extra"]`: a sink filter is handed whatever the
    # caller built, and a bare `{"message": ...}` record must scrub rather
    # than raise — a filter that throws takes the log line with it.
    for key, value in (record.get("extra") or {}).items():
        scrubbed = _scrub_value(value)
        # Assign only on change: `_stdlib`/`sentry_skip` and numeric extras
        # come back identical, and rewriting them would churn the dict every
        # sink shares.
        if scrubbed is not value:
            record["extra"][key] = scrubbed
    return True


# PRO-174: the correlation id. `logger.py` has emitted a `trace_id` field
# since the beginning and nothing ever bound one, so the 344 log calls in
# `app/` could not be grouped by conversation, lead or request — the single
# question this product generates ("what happened with this pro, on this
# lead?") was unanswerable from the logs.
def new_trace_id() -> str:
    """A correlation id for one conversation turn. Random, by construction.

    It is tempting to derive this from the chat id and the provider's message
    id so the two processes can each compute it. Do not: an unsalted digest
    of the chat id is a *decryption key for* ``mask_pii``, published on every
    line that carries it. The message id is logged verbatim (the idempotency
    lines on both routes, the `wamid` Sentry tag), and the masked phone beside
    it — ``97250****567`` — leaves 10⁴ candidates, so the seed is recovered in
    milliseconds; the whole Israeli mobile space falls in minutes. The field
    would undo, in public, exactly what the rest of this module exists to do.

    Determinism buys nothing anyway. The API mints the id, binds it, and
    forwards it to the worker as an explicit job kwarg, so nothing needs to
    recompute it — and the one path that derives its own (a job enqueued
    before that kwarg existed) is not required to match.

    12 hex chars is ~48 bits: nowhere near collision range for the handful of
    turns in flight at once, and short enough to read off a terminal and paste
    into a log search.
    """
    return uuid.uuid4().hex[:12]


# PRO-113 — the ONLY way to page the operator. sentry_sdk's LoggingIntegration
# hooks the *stdlib* logging module; loguru emits no stdlib LogRecord, so a
# loguru `logger.critical` is stdout-only and has never created a Sentry
# issue. This primitive emits through stdlib (InterceptHandler routes the
# record back into loguru, so the stdout/file sinks still apply), which is
# what actually reaches Sentry's event_level=CRITICAL threshold.
_PAGING_LOGGER = logging.getLogger("proli.paging")


def page_critical(message: str) -> None:
    """Emit a CRITICAL that actually pages (Sentry issue → operator email).

    Scrubbing happens inline, not only at the sink: ``_pii_filter`` mutates
    loguru's own record dict and never touches the stdlib ``LogRecord`` that
    Sentry reads, so sink-side masking can never cover the Sentry payload —
    regardless of handler ordering. ``stacklevel=2`` makes the record (and
    the Sentry culprit) point at the caller, not this helper.

    Deliberately no ``exc_info``: a rendered exception *value* (e.g. a
    pymongo auth error echoing a credentialed URI) would reach Sentry as
    frames/vars that ``redact_secrets`` never sees. Callers interpolate the
    scrubbed ``str(exc)`` into the message instead.

    PRO-174: ``scrub``, not the two scrubbers by hand — Sentry is an egress
    like any sink, and the address masking has to reach it too.
    """
    safe = scrub(message)
    try:
        _PAGING_LOGGER.critical(safe, stacklevel=2)
    except Exception:
        # Paging must never become the failure it reports: the loguru call
        # this replaced could not raise (sinks default to catch=True), and
        # several call sites are documented fail-open paths. `safe`, not
        # `message`: a bare test/aux sink without _pii_filter must never
        # see the unscrubbed text either.
        logger.opt(depth=1).critical(safe)


# Development-only file sink target (see setup_logging). Just a path here —
# the directory is created inside the dev branch, so a prod-like boot on
# Railway does not leave an empty `logs/` on the container filesystem.
log_dir = os.path.join(os.getcwd(), "logs")


def _is_logging_machinery_frame(frame) -> bool:
    """True for frames the InterceptHandler walk must step over: stdlib
    logging itself, plus sentry_sdk's logging integration — its
    ``sentry_patched_callhandlers`` wraps ``Logger.callHandlers``, so when
    Sentry is active one of its frames sits mid-walk and, unskipped, every
    intercepted line renders as sentry_sdk.integrations.logging (PRO-113)."""
    filename = frame.f_code.co_filename
    # Cheap short-circuit before the per-frame normalization allocation —
    # this runs for every frame of every intercepted record.
    return filename == logging.__file__ or (
        "sentry_sdk" in filename
        and filename.replace("\\", "/").endswith("sentry_sdk/integrations/logging.py")
    )


class InterceptHandler(logging.Handler):
    """
    Redirect standard logging to Loguru.
    """

    def emit(self, record):
        try:
            level = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        # On Python 3.12 logging.currentframe() returns *this* frame (not a
        # logging-internal one), so the original loguru recipe's loop never
        # ran and every intercepted line rendered as logging:callHandlers.
        # Force the first step, then walk out of the logging machinery.
        frame, depth = logging.currentframe(), 0
        while frame and (depth == 0 or _is_logging_machinery_frame(frame)):
            frame = frame.f_back
            depth += 1
        # PRO-113: page_critical is a one-line shim in this module; skip it so
        # the rendered location is the real caller (matching stacklevel=2).
        # Identity check, not name — another function merely named
        # page_critical must not be skipped.
        if frame and frame.f_code is page_critical.__code__:
            frame, depth = frame.f_back, depth + 1
        if frame is None:  # walked off the top — depth is now meaningless
            depth = 1

        # _stdlib marks the record's origin so the Sentry bridge sink
        # (app/core/sentry.py) can skip stdlib-origin ERRORs — uvicorn/arq/
        # apscheduler either reach Sentry via their integration already or
        # are third-party noise the bridge must not re-report.
        logger.bind(_stdlib=True).opt(depth=depth, exception=record.exc_info).log(
            level, record.getMessage()
        )


# PRO-184: extras that exist to steer this module's own plumbing and mean
# nothing to an operator reading logs. `_stdlib` and `sentry_skip` are dropped
# from the emitted payload only — they stay on `record["extra"]`, which is
# where `app/core/sentry.py` reads them to decide what reaches Sentry.
_INTERNAL_EXTRAS = frozenset({"_stdlib", "sentry_skip"})

# The keys Railway reads plus the two this sink adds. Extras never overwrite
# them (a stray `logger.bind(level=…)` must not displace the severity field),
# and they are what the serialization fallback keeps when the extras cannot be
# encoded.
_RESERVED_PAYLOAD_KEYS = ("message", "level", "level_name", "logger", "time")


def _railway_json_sink(message) -> None:
    """Emit one flat JSON line in the shape Railway's log pipeline reads.

    PRO-174 shipped `serialize=True` for this sink, which was the reasonable
    choice and the wrong one. Loguru's serializer emits ``{"text": …,
    "record": {…}}`` — the message lives at ``record.message`` and the level at
    ``record.level.name``, with **no** top-level ``message``, ``msg`` or
    ``level``. Railway parses any valid single-line JSON and reads exactly
    those keys: ``message`` becomes the body shown in the Log Explorer,
    ``level`` drives severity, everything else becomes an ``@name:value``
    attribute. Finding neither, it rendered every one of our lines with an
    **empty body** and matched nothing on ``@level:`` or on a substring
    search — including, provably, the ``[Scheduler] First runs:`` boot line
    that `app/scheduler.py` emits at WARNING once per boot precisely so an
    operator can see it.

    The consequence worth naming: PRO-174's whole purpose was the `trace_id`
    correlation id, and nesting put it at ``record.extra.trace_id``, so the
    one query it existed to enable — ``@trace_id:<id>`` across a conversation
    turn — matched zero rows. Hoisting `extra` to the top level is what makes
    that work, and is why this is a flat object rather than a tidier nested
    one.

    Both level fields are deliberate. Railway normalizes ``level`` to the
    closest of debug/info/warn/error, which collapses CRITICAL into ``error``
    — and CRITICAL is not a synonym for ERROR here, it is the level
    `page_critical` uses to page a human (PRO-113). ``level`` keeps
    ``@level:error`` working; ``level_name`` keeps the distinction the
    normalization destroys.

    Emitting a line is unconditional: every serialization failure degrades to
    a smaller line rather than raising, because loguru's `catch=True` rescue
    both loses the line and prints the whole record — `extra` included — to
    stderr as unparseable text. See the fallback at the bottom.
    """
    record = message.record

    payload = {
        # Scrubbed already: `_pii_filter` runs before any sink and rewrites
        # `record["message"]` and every bound extra in place, so both this and
        # the hoisted extras below are redacted text. The traceback is the one
        # thing it does not reach, and is scrubbed here.
        "message": record["message"],
        "level": record["level"].name.lower(),
        "level_name": record["level"].name,
        "logger": f"{record['name']}:{record['function']}:{record['line']}",
        "time": record["time"].isoformat(),
    }

    # Bound extras become queryable attributes — `trace_id` above all. Written
    # after the fixed keys and filtered to skip them, so a stray
    # `logger.bind(level=…)` cannot displace the field Railway reads.
    for key, value in (record.get("extra") or {}).items():
        if key in _INTERNAL_EXTRAS or key in payload:
            continue
        payload[key] = value

    # The traceback rode in serialize's `text` field before, where nothing
    # scrubbed it — the filter covers the message and bound extras, not this.
    # This sink is what puts it in the payload explicitly, so it goes through
    # the same scrubber on the way rather than arriving as the one unredacted
    # string on the line.
    #
    # `exc.value is not None`, not `exc is not None`: for a `logger.exception()`
    # with no active exception — reachable through `InterceptHandler` too, since
    # stdlib `exc_info=True` outside a handler yields `(None, None, None)` —
    # loguru sets a RecordException of three Nones rather than None itself, and
    # formatting that produces the string "NoneType: None" on every such line.
    exc = record.get("exception")
    if exc is not None and exc.value is not None:
        payload["exception"] = scrub(
            "".join(traceback.format_exception(exc.type, exc.value, exc.traceback))
        )

    # `allow_nan=False`: the default emits bare `NaN`/`Infinity`, which are not
    # JSON. A strict parser rejects the whole line, and Railway falls back to
    # treating it as plain text — the blank-body failure this sink exists to
    # fix, arriving through a different door.
    #
    # The fallback exists because `default=str` only rescues unserializable
    # *leaves*. A cyclic extra, a non-str dict key or a deep nest still raises,
    # and an unrescued raise here is worse than a dropped line: loguru's
    # `catch=True` prints its own multi-line report to stderr with the record —
    # `extra` included — dumped verbatim, which is both unparseable and the one
    # place an extra appears without passing through the filter. Better to emit
    # the five fields Railway actually reads and say the extras were lost.
    try:
        line = json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False)
    except (TypeError, ValueError, RecursionError):
        line = json.dumps(
            {key: payload[key] for key in _RESERVED_PAYLOAD_KEYS if key in payload}
            | {"extras_error": "unserializable"},
            ensure_ascii=False,
            default=str,
        )

    # loguru's StreamSink flushes after every write; a callable sink does not,
    # so this restores it. `PYTHONUNBUFFERED=1` in the Dockerfile makes it a
    # no-op for the deployed image, but that leaves the sink silently depending
    # on an env var set two files away — outside the image (a bare
    # `python -m app.worker`, a non-Docker runner) stdout to a pipe is
    # block-buffered and the tail of the log dies with the process.
    sys.stdout.write(line + "\n")
    sys.stdout.flush()


def _dev_format(record) -> str:
    """Format template for the development sinks, as a callable.

    A callable rather than a plain string because the human-readable format
    has to render ``{extra[trace_id]}``, and that placeholder raises
    ``KeyError`` on every line emitted outside a ``contextualize`` block —
    a scheduler tick, a startup line, anything no entry point minted an id
    for. The prod sink needs none of this: `_railway_json_sink` builds its
    payload from the record and simply omits an extra that was never bound.

    Without this the correlation id existed only in staging and production:
    the dev stdout format named no extras and the dev file sink used
    loguru's default, which also drops them — so PRO-174's whole point was
    invisible in the one environment where `/logs` tells an operator to
    grep for it.

    The default must NOT be written into the record. Loguru hands one
    record object to every active sink for a single log call, so a
    ``setdefault`` here would stamp ``trace_id="-"`` onto the shared
    ``extra`` and every later sink — the prod JSON one included — would
    then report an id that was never bound. The unbound case substitutes a
    literal dash into the *template* instead, leaving the placeholder (and
    loguru's own handling of the value) for when the key really is there.

    Returns the template loguru then renders, so it owns the trailing
    newline. Colour markup is stripped by the non-colorized file sink.
    """
    trace = "{extra[trace_id]}" if "trace_id" in record["extra"] else "-"
    return (
        "<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | "
        "<cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> | "
        "trace=<magenta>" + trace + "</magenta> - <level>{message}</level>\n"
    )


def setup_logging():
    """
    Configure Loguru and intercept standard logging.
    """
    logger.remove()

    # Determine format based on environment. PRO-34: staging is prod-like —
    # it gets the same structured JSON + PII filter + diagnose=False as
    # production, so staging logs are a faithful rehearsal of prod logs.
    is_prod_like = settings.is_prod_like

    if is_prod_like:
        # Structured JSON logging for staging/production, through an explicit
        # sink rather than `serialize=True` (PRO-184). Loguru's serializer
        # nests everything under `record`, which is valid JSON that Railway
        # parses and cannot read — no top-level `message` or `level` means a
        # blank body in the Log Explorer and zero matches on `@level:`,
        # substring search, or PRO-174's `@trace_id:`. `_railway_json_sink`
        # emits the flat shape those queries need; its docstring carries the
        # detail.
        #
        # `format` is unused by a callable sink that reads `.record` directly,
        # but loguru still renders it, so it stays minimal — and must not be
        # `_dev_format`, whose `{extra[trace_id]}` placeholder is exactly the
        # KeyError that template exists to avoid.
        logger.add(
            _railway_json_sink,
            format="{message}",
            level=settings.LOG_LEVEL,
            filter=_pii_filter,
        )
    else:
        # Human-readable for development
        logger.add(
            sys.stdout,
            format=_dev_format,
            level=settings.LOG_LEVEL,
            filter=_pii_filter,
            colorize=True,
        )

        # File handler — development only (PRO-174). On Railway this wrote to
        # an ephemeral container filesystem: discarded on every deploy and
        # restart, unreachable while running, and `logs/` is gitignored, so it
        # was pure I/O cost buying an archive nobody could ever read. In
        # staging/production the log of record is the stdout JSON stream the
        # platform captures (and, once PRO-175 lands, drains off-platform).
        # Locally it is still the thing `/logs` reads, so it stays.
        os.makedirs(log_dir, exist_ok=True)
        logger.add(
            os.path.join(log_dir, "proli.log"),
            format=_dev_format,
            filter=_pii_filter,
            rotation="10 MB",
            retention="10 days",
            level="DEBUG",
            compression="zip",
            enqueue=True,
            backtrace=True,
            diagnose=True,
        )

    logging.basicConfig(handlers=[InterceptHandler()], level=0, force=True)

    for log_name in ["uvicorn", "uvicorn.error", "uvicorn.access", "fastapi"]:
        logging_logger = logging.getLogger(log_name)
        logging_logger.handlers = [InterceptHandler()]
        logging_logger.propagate = False

    # PRO-79: httpx/httpcore log "HTTP Request: GET <url>" at INFO. The legacy
    # WhatsApp vendor put its auth token in the URL path; keep these at WARNING
    # so no credential a provider ever puts in a URL reaches the logs.
    for noisy in ["httpx", "httpcore"]:
        logging.getLogger(noisy).setLevel(logging.WARNING)


setup_logging()
__all__ = [
    "logger",
    "setup_logging",
    "page_critical",
    "mask_pii",
    "redact_secrets",
    "mask_address",
    "scrub",
    "new_trace_id",
]
