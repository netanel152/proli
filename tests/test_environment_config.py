"""
Tests for PRO-34 (ENVIRONMENT=staging as a first-class environment):

  - app/core/constants.py: normalize_environment / is_prod_like_env pure helpers
  - app/core/config.py: Settings.ENVIRONMENT validator + is_prod_like / is_production
  - app/core/logger.py: setup_logging() branches on is_prod_like (not a raw
    ENVIRONMENT string compare)
  - admin_panel/core/utils.py: the "MONGO_URI required" guard rule
    (is_prod_like_env(os.getenv("ENVIRONMENT"))) — tested without importing the
    module itself, since it opens a real MongoClient at import time.
  - scripts/seed_db.py: assert_seed_allowed() refuses production, allows
    staging/development.
"""

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import ValidationError

from app.core.constants import (
    DEVELOPMENT_ENV,
    PRODUCTION_ENV,
    STAGING_ENV,
    VALID_ENVIRONMENTS,
    is_prod_like_env,
    normalize_environment,
)
from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_REQUIRED_SETTINGS_FIELDS = dict(
    GREEN_API_INSTANCE_ID="test-instance",
    GREEN_API_TOKEN="test-token",
    GEMINI_API_KEY="test-gemini-key",
    CLOUDINARY_CLOUD_NAME="test-cloud",
    CLOUDINARY_API_KEY="test-cloud-key",
    CLOUDINARY_API_SECRET="test-cloud-secret",
)


def make_settings(**overrides) -> Settings:
    """Construct a Settings instance with the required fields stubbed out,
    so tests never depend on the developer's real .env / os.environ values
    (env vars/kwargs always take priority over the .env file, but explicit
    kwargs here make that independence obvious and deterministic)."""
    kwargs = {**_REQUIRED_SETTINGS_FIELDS, **overrides}
    return Settings(**kwargs)


# ---------------------------------------------------------------------------
# app/core/constants.py: normalize_environment / is_prod_like_env
# ---------------------------------------------------------------------------


def test_normalize_environment_none_is_development():
    assert normalize_environment(None) == DEVELOPMENT_ENV


@pytest.mark.parametrize("value", ["", "   ", "\t"])
def test_normalize_environment_empty_or_whitespace_is_development(value):
    assert normalize_environment(value) == DEVELOPMENT_ENV


def test_normalize_environment_strips_and_lowercases():
    assert normalize_environment("  Staging ") == STAGING_ENV
    assert normalize_environment("PRODUCTION") == PRODUCTION_ENV
    assert normalize_environment("Development") == DEVELOPMENT_ENV


def test_normalize_environment_does_not_validate_unknown_values():
    # Pure helper — no rejection of unrecognized values; that's the job of
    # the Settings validator, not this function.
    assert normalize_environment("prod") == "prod"
    assert normalize_environment("some-nonsense") == "some-nonsense"


def test_is_prod_like_env_true_for_staging_and_production():
    assert is_prod_like_env(STAGING_ENV) is True
    assert is_prod_like_env(PRODUCTION_ENV) is True
    assert is_prod_like_env("  Staging ") is True
    assert is_prod_like_env("PRODUCTION") is True


def test_is_prod_like_env_false_for_development_and_none():
    assert is_prod_like_env(DEVELOPMENT_ENV) is False
    assert is_prod_like_env(None) is False
    assert is_prod_like_env("") is False


# ---------------------------------------------------------------------------
# app/core/config.py: Settings.ENVIRONMENT validator
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [DEVELOPMENT_ENV, STAGING_ENV, PRODUCTION_ENV])
def test_settings_environment_accepts_each_valid_value(value):
    s = make_settings(ENVIRONMENT=value)
    assert s.ENVIRONMENT == value


@pytest.mark.parametrize("value", ["prod", "PRODUCTION_", "test", "stage", "devx"])
def test_settings_environment_rejects_unknown_values(value):
    with pytest.raises(ValidationError) as exc_info:
        make_settings(ENVIRONMENT=value)
    message = str(exc_info.value)
    # Message must name all three valid values so an operator can fix the typo.
    for valid in VALID_ENVIRONMENTS:
        assert valid in message


def test_settings_environment_rejection_message_mentions_offending_value():
    with pytest.raises(ValidationError) as exc_info:
        make_settings(ENVIRONMENT="prod")
    assert "prod" in str(exc_info.value)


def test_settings_environment_normalizes_case_and_whitespace():
    s = make_settings(ENVIRONMENT="  Staging ")
    assert s.ENVIRONMENT == STAGING_ENV


@pytest.mark.parametrize("value", ["", "   "])
def test_settings_environment_empty_or_whitespace_raises(value):
    # An *unset* ENVIRONMENT never reaches the validator (pydantic's
    # validate_default=False uses the field default as-is), so an empty
    # string here can only mean an operator explicitly set ENVIRONMENT="" —
    # a misconfiguration that must fail fast, not silently become "development".
    with pytest.raises(ValidationError) as exc_info:
        make_settings(ENVIRONMENT=value)
    message = str(exc_info.value)
    assert "empty" in message
    for valid in VALID_ENVIRONMENTS:
        assert valid in message


# ---------------------------------------------------------------------------
# is_prod_like / is_production truth table
# ---------------------------------------------------------------------------


def test_is_prod_like_and_is_production_development():
    s = make_settings(ENVIRONMENT=DEVELOPMENT_ENV)
    assert s.is_prod_like is False
    assert s.is_production is False


def test_is_prod_like_and_is_production_staging():
    s = make_settings(ENVIRONMENT=STAGING_ENV)
    assert s.is_prod_like is True
    assert s.is_production is False


def test_is_prod_like_and_is_production_production():
    s = make_settings(ENVIRONMENT=PRODUCTION_ENV)
    assert s.is_prod_like is True
    assert s.is_production is True


# ---------------------------------------------------------------------------
# app/core/logger.py: setup_logging() branches on is_prod_like
# ---------------------------------------------------------------------------


def _invoke_setup_logging(monkeypatch, is_prod_like: bool) -> MagicMock:
    """Run setup_logging() with settings/logger.add/logging.basicConfig
    replaced by test doubles, so nothing real is installed and no global
    logging state is left mutated for the rest of the session."""
    import app.core.logger as logmod

    fake_settings = SimpleNamespace(is_prod_like=is_prod_like, LOG_LEVEL="INFO")
    monkeypatch.setattr(logmod, "settings", fake_settings)
    monkeypatch.setattr(logmod.logger, "remove", MagicMock())
    add_mock = MagicMock()
    monkeypatch.setattr(logmod.logger, "add", add_mock)
    monkeypatch.setattr(logmod.logging, "basicConfig", MagicMock())

    logmod.setup_logging()
    return add_mock


def test_setup_logging_prod_like_branch_for_staging(monkeypatch):
    add_mock = _invoke_setup_logging(monkeypatch, is_prod_like=True)

    assert add_mock.call_count == 2
    stdout_call, file_call = add_mock.call_args_list

    assert stdout_call.kwargs.get("serialize") is True
    assert "colorize" not in stdout_call.kwargs

    assert file_call.kwargs.get("diagnose") is False


def test_setup_logging_prod_like_branch_for_production(monkeypatch):
    add_mock = _invoke_setup_logging(monkeypatch, is_prod_like=True)

    stdout_call, file_call = add_mock.call_args_list
    assert stdout_call.kwargs.get("serialize") is True
    assert file_call.kwargs.get("diagnose") is False


def test_setup_logging_dev_branch_not_taken_for_prod_like(monkeypatch):
    add_mock = _invoke_setup_logging(monkeypatch, is_prod_like=False)

    stdout_call, file_call = add_mock.call_args_list
    # Dev branch: colorize=True, no serialize kwarg at all.
    assert stdout_call.kwargs.get("colorize") is True
    assert "serialize" not in stdout_call.kwargs

    assert file_call.kwargs.get("diagnose") is True


# ---------------------------------------------------------------------------
# admin_panel/core/utils.py: the MONGO_URI guard *rule*
#
# We deliberately do NOT import admin_panel.core.utils — it opens a real
# MongoClient at module scope. Instead we assert the exact predicate the
# module applies: `is_prod_like_env(os.getenv("ENVIRONMENT"))`.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("env_value", [STAGING_ENV, PRODUCTION_ENV])
def test_admin_panel_guard_fires_for_staging_and_production(env_value):
    assert is_prod_like_env(env_value) is True


@pytest.mark.parametrize("env_value", [DEVELOPMENT_ENV, None, "", "  "])
def test_admin_panel_guard_falls_back_to_localhost_for_dev_or_unset(env_value):
    assert is_prod_like_env(env_value) is False


# ---------------------------------------------------------------------------
# scripts/seed_db.py: assert_seed_allowed()
#
# assert_seed_allowed() is an allow-list, not a production check: it only
# lets DEVELOPMENT_ENV / STAGING_ENV through, so a monkeypatched settings
# stub only needs an ENVIRONMENT attribute (no is_production).
# ---------------------------------------------------------------------------


def test_assert_seed_allowed_raises_systemexit_under_production(monkeypatch):
    import scripts.seed_db as seed_db

    fake_settings = SimpleNamespace(ENVIRONMENT=PRODUCTION_ENV)
    monkeypatch.setattr(seed_db, "settings", fake_settings)

    with pytest.raises(SystemExit):
        seed_db.assert_seed_allowed()


@pytest.mark.parametrize("env_value", [STAGING_ENV, DEVELOPMENT_ENV])
def test_assert_seed_allowed_is_noop_under_staging_and_development(
    monkeypatch, env_value
):
    import scripts.seed_db as seed_db

    fake_settings = SimpleNamespace(ENVIRONMENT=env_value)
    monkeypatch.setattr(seed_db, "settings", fake_settings)

    # Should not raise.
    seed_db.assert_seed_allowed()


def test_assert_seed_allowed_fails_closed_for_unrecognized_value(monkeypatch):
    # Allow-list semantics: anything that isn't explicitly development/staging
    # is refused, including a value that isn't even a valid ENVIRONMENT at all
    # (e.g. it slipped past Settings validation via a raw stub in a test, or a
    # future environment name nobody has added to the allow-list yet).
    import scripts.seed_db as seed_db

    fake_settings = SimpleNamespace(ENVIRONMENT="qa")
    monkeypatch.setattr(seed_db, "settings", fake_settings)

    with pytest.raises(SystemExit):
        seed_db.assert_seed_allowed()


@pytest.mark.asyncio
async def test_seed_aborts_before_any_destructive_call(monkeypatch):
    # The entire safety guarantee of assert_seed_allowed() is ordering: it
    # must run before clear_db() (or anything else destructive) is invoked.
    import scripts.seed_db as seed_db

    monkeypatch.setattr(
        seed_db, "settings", SimpleNamespace(ENVIRONMENT=PRODUCTION_ENV)
    )
    clear_mock = AsyncMock()
    monkeypatch.setattr(seed_db, "clear_db", clear_mock)

    with pytest.raises(SystemExit):
        await seed_db.seed()

    clear_mock.assert_not_called()


# ---------------------------------------------------------------------------
# admin_panel/core/utils.py: source-level regression test
#
# We can't import the module (it opens a real MongoClient at import time), so
# assert the source still routes the MONGO_URI guard through the shared
# is_prod_like_env() helper and hasn't regressed back to a raw string compare.
# ---------------------------------------------------------------------------


def test_admin_panel_utils_uses_the_prod_like_helper():
    src = (REPO_ROOT / "admin_panel" / "core" / "utils.py").read_text(encoding="utf-8")
    assert 'is_prod_like_env(os.getenv("ENVIRONMENT"))' in src
    assert '== "production"' not in src
