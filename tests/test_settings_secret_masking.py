"""PRO-94: secrets on ``Settings`` must be structurally unprintable.

The incident these tests exist for: on 2026-08-09 a pytest ``AttributeError``
on a removed ``Settings`` field rendered the whole object, and pydantic's
default ``__repr__`` prints every field value — so the entire secret set landed
in a transcript in plaintext, including a token rotated an hour earlier. The
same repr reaches a Sentry exception context or a Railway deploy log on any
traceback that touches the object, so this was never an AI-tooling problem.

Two layers are asserted here:
  1. Type-level — every credential field is a ``SecretStr``, so repr/str/format
     mask it. This is the fix.
  2. Source-level — no secret is interpolated into an f-string, a log call or a
     print. SecretStr would mask it anyway, but a masked value in a log line is
     a bug waiting to be "fixed" by someone adding ``.get_secret_value()`` to
     make the log useful again.
"""

import re
import traceback
import types
from pathlib import Path
from typing import Union, get_args, get_origin

import pytest
from pydantic import SecretStr

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]

# Distinctive values — a substring match on these is a leak, and they cannot
# collide with anything else pytest prints.
SENTINELS = {
    "GEMINI_API_KEY": "sentinel-gemini-6f1a2b",
    "MONGO_URI": "mongodb+srv://u:sentinel-atlas-pw-9c3d@cluster0.example.net/proli_db",
    "MONGO_TEST_URI": "mongodb+srv://u:sentinel-test-pw-4e7f@cluster0.example.net/t",
    "ADMIN_PASSWORD": "sentinel-admin-pw-11b8",
    "REDIS_URL": "redis://:sentinel-redis-pw-22c9@redis.example.net:6379/0",
    "CLOUDINARY_API_KEY": "sentinel-cloudinary-key-33da",
    "CLOUDINARY_API_SECRET": "sentinel-cloudinary-secret-44eb",
    "WEBHOOK_TOKEN": "sentinel-webhook-token-55fc",
    "GOOGLE_MAPS_API_KEY": "sentinel-maps-key-66ad",
    "SENTRY_DSN": "https://sentinel-sentry-key-77be@o1.ingest.sentry.io/1",
}


def _is_secret_annotation(annotation) -> bool:
    """True for ``SecretStr`` and ``SecretStr | None``.

    Both union spellings must be handled: ``typing.Optional[SecretStr]`` gives
    ``typing.Union``, while the PEP 604 ``SecretStr | None`` this codebase uses
    gives ``types.UnionType``. Checking only the former silently classifies
    every optional secret as non-secret — which is exactly the set of fields
    this test exists to protect.
    """
    if annotation is SecretStr:
        return True
    if get_origin(annotation) in (Union, types.UnionType):
        return any(arg is SecretStr for arg in get_args(annotation))
    return False


SECRET_FIELDS = sorted(
    name
    for name, field in Settings.model_fields.items()
    if _is_secret_annotation(field.annotation)
)


@pytest.fixture
def loaded_settings() -> Settings:
    """A Settings instance holding every sentinel.

    ``CLOUDINARY_CLOUD_NAME`` is required but deliberately *not* secret — it
    appears in every delivery URL — so it is stubbed with a plain value.
    """
    return Settings(CLOUDINARY_CLOUD_NAME="sentinel-cloud-name", **SENTINELS)


def _assert_no_sentinels(rendered: str, *, context: str):
    leaked = [name for name, value in SENTINELS.items() if value in rendered]
    assert not leaked, f"{context} leaked: {', '.join(leaked)}"


# ---------------------------------------------------------------------------
# 1. Type-level: the fields are SecretStr
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", sorted(SENTINELS))
def test_every_credential_field_is_a_secretstr(field_name):
    """The closed list from the ticket, asserted field by field.

    Parametrized rather than a set comparison so a regression names the field
    that lost its wrapper instead of printing two sets to diff by eye.
    """
    assert field_name in SECRET_FIELDS, (
        f"{field_name} is credential-bearing but is not typed SecretStr — "
        "any traceback touching Settings will print it in plaintext."
    )


# Naming convention → secrecy. Every field whose name ends this way is a
# credential today, and there are no false positives in Settings: MONGO_MAX_POOL_SIZE,
# REDIS_HOST, BACKEND_CORS_ORIGINS and friends all miss it. That makes it a usable
# forward guard rather than an allowlist that rots.
_CREDENTIAL_NAME = re.compile(r"(TOKEN|KEY|SECRET|PASSWORD|DSN|_URI|_URL)$")


def test_new_credential_shaped_fields_must_be_secretstr():
    """The guard for fields that do not exist yet.

    PRO-89 adds ``META_ACCESS_TOKEN``, ``META_APP_SECRET`` and
    ``META_VERIFY_TOKEN``; PRO-87's checklist lists two more. Declaring any of
    them as a bare ``str`` re-arms the exact landmine this ticket defused, and
    a reviewer will not catch it in a 40-line config diff. This will.

    If a genuinely public value ever ends in one of these suffixes, name it so
    it does not (``CLOUDINARY_CLOUD_NAME`` already shows the pattern) rather
    than widening this test.
    """
    offenders = [
        name
        for name in Settings.model_fields
        if _CREDENTIAL_NAME.search(name) and name not in SECRET_FIELDS
    ]
    assert not offenders, (
        "Credential-shaped Settings field(s) not typed SecretStr: "
        f"{', '.join(sorted(offenders))}. pydantic's repr prints every field "
        "value, so any traceback touching Settings leaks these in plaintext "
        "(PRO-94). Type them SecretStr and unwrap with .get_secret_value() at "
        "the point of use."
    )


def test_secret_fields_are_discovered_by_annotation(loaded_settings):
    """The runtime values are SecretStr too, not just the annotations.

    A ``str`` default on a ``SecretStr`` field slips through validation
    (pydantic does not validate defaults), which would leave the field
    annotated correctly and still leaking — the exact trap ``MONGO_URI``'s
    localhost default sets.
    """
    for name in SECRET_FIELDS:
        value = getattr(loaded_settings, name)
        assert value is None or isinstance(
            value, SecretStr
        ), f"{name} holds {type(value).__name__}, not SecretStr"


def test_mongo_uri_default_is_wrapped():
    """The unset-MONGO_URI path must not hand back a bare str.

    pydantic skips validators on defaults, so a plain-string default would make
    ``settings.MONGO_URI.get_secret_value()`` an AttributeError in every
    consumer the moment a developer runs without MONGO_URI set.
    """
    s = Settings(
        GEMINI_API_KEY="k",
        CLOUDINARY_CLOUD_NAME="c",
        CLOUDINARY_API_KEY="k",
        CLOUDINARY_API_SECRET="s",
        MONGO_URI=None,
    )
    assert isinstance(s.MONGO_URI, SecretStr)
    assert s.MONGO_URI.get_secret_value().startswith("mongodb://")


# ---------------------------------------------------------------------------
# 2. The original failure mode
# ---------------------------------------------------------------------------


def test_repr_masks_every_secret(loaded_settings):
    """The headline regression: this is the string that leaked."""
    _assert_no_sentinels(repr(loaded_settings), context="repr(settings)")
    assert "**********" in repr(loaded_settings)


def test_str_and_format_mask_every_secret(loaded_settings):
    _assert_no_sentinels(str(loaded_settings), context="str(settings)")
    _assert_no_sentinels(f"{loaded_settings}", context="f-string interpolation")
    _assert_no_sentinels(
        f"{loaded_settings.WEBHOOK_TOKEN}", context="f-string on a single field"
    )


def test_attribute_error_traceback_does_not_leak(loaded_settings):
    """Reproduce the incident: touch a field that no longer exists.

    pytest renders locals on failure (and Sentry attaches them), so the
    rendered traceback plus a locals dump is what an operator actually sees.
    """
    with pytest.raises(AttributeError) as exc_info:
        loaded_settings.GREEN_API_INSTANCE_ID  # removed in PRO-86

    rendered = "".join(
        traceback.format_exception(
            type(exc_info.value), exc_info.value, exc_info.value.__traceback__
        )
    )
    # Emulate pytest's --showlocals / Sentry's local-variable capture, which is
    # how the object's repr reached the transcript in the first place.
    rendered += "\n".join(f"{k} = {v!r}" for k, v in locals().items())
    _assert_no_sentinels(rendered, context="AttributeError traceback + locals")


def test_model_dump_and_json_mask_every_secret(loaded_settings):
    """Serialization is the other route out — health payloads, debug endpoints."""
    _assert_no_sentinels(repr(loaded_settings.model_dump()), context="model_dump()")
    _assert_no_sentinels(loaded_settings.model_dump_json(), context="model_dump_json()")


def test_get_secret_value_still_returns_the_real_value(loaded_settings):
    """Masking must not have broken the consumers — the point is a wrapper, not
    a shredder."""
    for name, expected in SENTINELS.items():
        assert getattr(loaded_settings, name).get_secret_value() == expected


# ---------------------------------------------------------------------------
# 3. iter_secret_values — the logger's redaction source
# ---------------------------------------------------------------------------


def test_iter_secret_values_returns_the_raw_values(loaded_settings):
    values = loaded_settings.iter_secret_values()
    for expected in SENTINELS.values():
        assert expected in values


def test_iter_secret_values_skips_unset_fields():
    """An unset optional secret must not enter the redaction list — an empty
    string matches every message and would redact all output."""
    s = Settings(
        GEMINI_API_KEY="a-real-looking-key",
        CLOUDINARY_CLOUD_NAME="c",
        CLOUDINARY_API_KEY="a-real-looking-key-2",
        CLOUDINARY_API_SECRET="a-real-looking-secret",
        WEBHOOK_TOKEN=None,
    )
    values = s.iter_secret_values()
    assert all(values), "empty value entered the redaction list"
    assert None not in values


def test_iter_secret_values_skips_short_values():
    """A short secret would corrupt unrelated log lines by substring match,
    while protecting something already too guessable to protect."""
    s = Settings(
        GEMINI_API_KEY="short",
        CLOUDINARY_CLOUD_NAME="c",
        CLOUDINARY_API_KEY="also-long-enough-key",
        CLOUDINARY_API_SECRET="also-long-enough-secret",
    )
    assert "short" not in s.iter_secret_values()
    assert "also-long-enough-key" in s.iter_secret_values()


def test_logger_redaction_list_is_built_from_settings():
    """PRO-80's filter is kept as the second layer and must now source its
    values from the SecretStr fields, so PRO-89's Meta credential is covered
    the moment it is declared rather than when someone remembers a second list.
    """
    import app.core.logger as logmod

    source = (REPO_ROOT / "app" / "core" / "logger.py").read_text(encoding="utf-8")
    assert "settings.iter_secret_values()" in source
    assert isinstance(logmod._SECRET_VALUES, list)


# ---------------------------------------------------------------------------
# 4. Source-level: no secret reaches a log line, f-string or print
# ---------------------------------------------------------------------------

_SCANNED_DIRS = ("app", "admin_panel", "scripts")
# config.py declares the fields; this test file names them on purpose.
_EXEMPT = {Path("app") / "core" / "config.py"}

_SECRET_REF = re.compile(
    r"settings\.(" + "|".join(re.escape(f) for f in SECRET_FIELDS) + r")\b"
)
_FSTRING = re.compile(r"""\bf["']""")
_LOGGING_CALL = re.compile(r"\b(logger\.\w+|print)\s*\(")


def _python_sources():
    for directory in _SCANNED_DIRS:
        for path in (REPO_ROOT / directory).rglob("*.py"):
            if "__pycache__" in path.parts:
                continue
            relative = path.relative_to(REPO_ROOT)
            if relative in _EXEMPT:
                continue
            yield relative, path.read_text(encoding="utf-8")


def test_no_secret_field_is_interpolated_into_an_fstring():
    offenders = []
    for relative, source in _python_sources():
        for lineno, line in enumerate(source.splitlines(), 1):
            if _SECRET_REF.search(line) and _FSTRING.search(line):
                offenders.append(f"{relative}:{lineno}: {line.strip()}")
    assert not offenders, (
        "A Settings secret is interpolated into an f-string. SecretStr masks it "
        "today, which means the log line is useless and the next reader is "
        "tempted to 'fix' it with .get_secret_value(). Log an identifier "
        "instead:\n" + "\n".join(offenders)
    )


def test_no_secret_field_is_passed_to_a_log_or_print_call():
    offenders = []
    for relative, source in _python_sources():
        for lineno, line in enumerate(source.splitlines(), 1):
            if _SECRET_REF.search(line) and _LOGGING_CALL.search(line):
                offenders.append(f"{relative}:{lineno}: {line.strip()}")
    assert (
        not offenders
    ), "A Settings secret is passed to a logger/print call:\n" + "\n".join(offenders)


def test_get_secret_value_is_not_assigned_to_a_module_level_name_in_services():
    """Unwrapping at the point of use is the rule; an unwrapped module-level
    constant is a plain str that any later ``logger.info(f"...{X}")`` will
    happily print.

    The three infrastructure modules that build a client at import time are
    exempt — they hand the value straight to a driver constructor and hold no
    logging of their own.
    """
    exempt = {
        Path("app") / "core" / "database.py",
        Path("admin_panel") / "core" / "auth.py",
        Path("admin_panel") / "views" / "analytics.py",
    }
    offenders = []
    for relative, source in _python_sources():
        if relative in exempt or relative.parts[0] == "scripts":
            continue
        for lineno, line in enumerate(source.splitlines(), 1):
            stripped = line.rstrip()
            if not stripped or stripped[0].isspace():
                continue  # indented → function scope, not module level
            if ".get_secret_value()" in stripped and "=" in stripped.split("(")[0]:
                offenders.append(f"{relative}:{lineno}: {stripped}")
    assert not offenders, "Secret unwrapped into a module-level name:\n" + "\n".join(
        offenders
    )
