"""PRO-184 — app.core.logger._railway_json_sink: the flat JSON shape Railway's
log pipeline actually reads.

`serialize=True` (the sink this replaced) emitted `{"text": ..., "record":
{...}}` — no top-level `message` or `level`, so Railway rendered every line
with a blank body and `@level:`/`@trace_id:` matched nothing. These tests pin
the flat shape so a future refactor that re-nests fails here instead of
silently going dark in the Log Explorer again.

Each test adds `_railway_json_sink` (with the real `_pii_filter`) to a fresh
loguru handler via the `add_sink` fixture and reads it back off `capsys`; the
fixture removes every handler it added on teardown so nothing leaks into
other test modules — same pattern as `tests/test_logger_dev_format.py`'s
`_rendered_lines` helper and `tests/test_logger_redaction.py`.
"""

import json

import pytest
from loguru import logger

from app.core.logger import (
    _INTERNAL_EXTRAS,
    _RESERVED_PAYLOAD_KEYS,
    _pii_filter,
    _railway_json_sink,
)


@pytest.fixture
def add_sink():
    """Register a loguru sink for the duration of one test, removed after."""
    sink_ids = []

    def _add(sink_fn=_railway_json_sink, level="DEBUG"):
        sink_id = logger.add(
            sink_fn, format="{message}", filter=_pii_filter, level=level
        )
        sink_ids.append(sink_id)
        return sink_id

    yield _add

    for sink_id in sink_ids:
        logger.remove(sink_id)


def _lines(capsys):
    """Non-empty stdout lines from the current capture, in emission order."""
    return [line for line in capsys.readouterr().out.splitlines() if line.strip()]


def test_top_level_key_set_is_pinned(add_sink, capsys):
    add_sink()
    logger.info("hello world")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["message"] == "hello world"
    assert payload["level"] == "info"
    # The exact acceptance criterion: Railway reads `message` and `level` at
    # the top level, nothing nested under a `record` key.
    assert set(payload.keys()) == {"message", "level", "level_name", "logger", "time"}


@pytest.mark.parametrize(
    "log_call, expected_level, expected_level_name",
    [
        (lambda: logger.debug("d"), "debug", "DEBUG"),
        (lambda: logger.info("i"), "info", "INFO"),
        (lambda: logger.warning("w"), "warning", "WARNING"),
        (lambda: logger.error("e"), "error", "ERROR"),
        # CRITICAL is the page_critical (PRO-113) level — Railway normalizes
        # `level` down to error/warn/info/debug, which would collapse it into
        # "error" if level_name did not carry the distinction separately.
        (lambda: logger.critical("c"), "critical", "CRITICAL"),
    ],
)
def test_level_mapping(add_sink, capsys, log_call, expected_level, expected_level_name):
    add_sink()
    log_call()

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["level"] == expected_level
    assert payload["level_name"] == expected_level_name


def test_trace_id_is_hoisted_to_top_level(add_sink, capsys):
    add_sink()
    with logger.contextualize(trace_id="abc123def456"):
        logger.info("turn started")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["trace_id"] == "abc123def456"
    assert "record" not in payload


def test_internal_extras_absent_from_payload_but_kept_on_record(add_sink, capsys):
    # The Sentry bridge (app/core/sentry.py) reads _stdlib/sentry_skip off
    # record["extra"] to decide what reaches Sentry, so they must survive
    # there even though Railway must never see them as filter attributes.
    captured = {}

    def probe(message):
        captured["record"] = message.record

    add_sink(_railway_json_sink)
    add_sink(probe)

    bound = {key: True for key in _INTERNAL_EXTRAS}
    logger.bind(**bound).info("internal plumbing")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    for key in _INTERNAL_EXTRAS:
        assert key not in payload
        assert captured["record"]["extra"][key] is True


def test_hebrew_emitted_unescaped(add_sink, capsys):
    add_sink()
    logger.info("שלום עולם")

    raw = capsys.readouterr().out
    assert "שלום עולם" in raw
    assert "\\u05e9" not in raw


def test_scrubbing_applies_to_message_and_bound_string_extra(add_sink, capsys):
    add_sink()
    logger.bind(chat_id="972521234567").info("call 972521234567 now")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["message"] == "call 97252****567 now"
    assert payload["chat_id"] == "97252****567"
    assert "972521234567" not in line


def test_exception_line_carries_scrubbed_traceback(add_sink, capsys):
    add_sink()
    try:
        raise ValueError("contact 972521234567 about this")
    except ValueError:
        logger.exception("boom")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert "97252****567" in payload["exception"]
    assert "972521234567" not in payload["exception"]


def test_bare_exception_call_without_active_exception_omits_exception_key(
    add_sink, capsys
):
    # PRO-184 tightened the guard to `exc.value is not None`: loguru's
    # RecordException for a logger.exception() call with no active exception
    # (or InterceptHandler forwarding stdlib exc_info=True outside a handler)
    # is three Nones, not None itself — formatting that used to render the
    # literal string "NoneType: None" onto every such line.
    add_sink()
    logger.exception("no active exception here")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert "exception" not in payload


def test_multiline_message_stays_one_physical_line_and_round_trips(add_sink, capsys):
    # Multi-line records are routine here (the SOS report joins lines with
    # "\n") — a naive sink could split on the embedded newline and break "one
    # JSON object per stdout line". json.dumps encodes it as the two-char
    # escape `\n`, not a literal line break, so the line count must stay 1.
    add_sink()
    logger.info("line1\nline2")

    lines = _lines(capsys)
    assert len(lines) == 1

    payload = json.loads(lines[0])
    assert payload["message"] == "line1\nline2"


@pytest.mark.parametrize(
    "extra_value, expected",
    [
        # dict: string leaves scrubbed, recursively.
        (
            {"phone": "972521234567", "addr": "רחוב הרצל 15, תל אביב"},
            {"phone": "97252****567", "addr": "***ADDRESS***, תל אביב"},
        ),
        # list: string leaves scrubbed, non-string leaves (int/bool/None)
        # pass through unchanged so they stay JSON numbers/booleans/null —
        # Railway's numeric filters (`@duration_ms:>500`) depend on that.
        (["972521234567", 5, True, None], ["97252****567", 5, True, None]),
        # bare scalar: unchanged and still a JSON number, not stringified.
        (123, 123),
    ],
)
def test_structural_scrubbing_walks_containers_and_passes_scalars_through(
    add_sink, capsys, extra_value, expected
):
    add_sink()
    logger.bind(extra=extra_value).info("bound extra")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["extra"] == expected
    assert "972521234567" not in line


def test_structural_scrubbing_renders_and_scrubs_arbitrary_object(add_sink, capsys):
    # Anything that is not a str/bool/int/float/None/dict/list/tuple/set is
    # rendered with str() and scrubbed — so the rendering is masked rather
    # than the raw object slipping past every scrubber in this module.
    class LeadRef:
        def __str__(self):
            return "lead for 972521234567"

    add_sink()
    logger.bind(who=LeadRef()).info("odd extra")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["who"] == "lead for 97252****567"
    assert "972521234567" not in line


@pytest.mark.parametrize(
    "extra_value",
    [
        float("nan"),  # allow_nan=False rejects this — bare NaN is not JSON
        {(1, 2): "v"},  # a non-str dict key also isn't JSON-encodable
    ],
)
def test_unserializable_extra_falls_back_to_reserved_keys_without_raising(
    add_sink, capsys, extra_value
):
    add_sink()
    logger.bind(bad=extra_value).info("trouble")

    lines = _lines(capsys)
    assert len(lines) == 1  # the line survives — it is never dropped

    payload = json.loads(lines[0])  # still one valid JSON line
    assert payload["extras_error"] == "unserializable"
    assert payload["message"] == "trouble"
    assert "bad" not in payload
    assert set(payload.keys()) == set(_RESERVED_PAYLOAD_KEYS) | {"extras_error"}


def test_bound_extra_cannot_displace_reserved_level_key(add_sink, capsys):
    add_sink()
    logger.bind(level="nonsense").warning("careful")

    (line,) = _lines(capsys)
    payload = json.loads(line)

    assert payload["level"] == "warning"
    assert "nonsense" not in line
