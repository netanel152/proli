"""PRO-48 — Settings.admin_phone_unconfigured: a deployment that never set
ADMIN_PHONE has every SOS report, admin alert and on-call page routing to the
built-in default number, silently. Follows the direct-construction pattern in
tests/test_settings_secret_masking.py / tests/test_settings_meta_cloud_provider.py
(Settings() with the minimal required fields plus the case under test) rather
than monkeypatching the module-level settings singleton, since the property
reads ``model_fields_set`` — a fact about *this instance's* construction.

The property is provenance, not value: it must fire only when nothing (env var
or .env) supplied ADMIN_PHONE, and must stay silent when something did — even
when that something happens to equal the default. See the docstring on
``admin_phone_unconfigured`` for why: on 2026-09-16 production was measured set
explicitly to exactly the default, because the default *is* the operator's own
number. The obvious wrong implementation (comparing against the default value)
would fire on that healthy configuration.
"""

from pathlib import Path

from loguru import logger as loguru_logger

from app.core.config import Settings
from app.core.startup_checks import warn_if_admin_phone_unconfigured

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_REQUIRED = dict(
    GEMINI_API_KEY="k",
    CLOUDINARY_CLOUD_NAME="c",
    CLOUDINARY_API_KEY="k2",
    CLOUDINARY_API_SECRET="s",
)

# A prod-like construction additionally satisfies `require_webhook_auth_in_prod_like`
# (PRO-86). Kept separate from _BASE_REQUIRED so the development-environment
# cases stay minimal and it is obvious which field exists for which validator.
_PROD_LIKE_REQUIRED = dict(_BASE_REQUIRED, WEBHOOK_TOKEN="t")

# Read off the model rather than hardcoding it a second time here — if the
# default ever changes, this test moves with it instead of silently comparing
# against a stale literal.
_DEFAULT_ADMIN_PHONE = Settings.model_fields["ADMIN_PHONE"].default


def test_admin_phone_absent_from_environment_is_unconfigured(monkeypatch):
    """Nothing supplied ADMIN_PHONE: the built-in default is live and the
    guard must say so.

    ``Settings.model_config`` sets ``env_file=".env"``, so a stray local
    ``.env`` with ``ADMIN_PHONE=`` set would make this case pass or fail
    depending on the machine running it. Guarded two ways: ``monkeypatch``
    clears any ambient OS env var, and ``_env_file=None`` disables dotenv
    loading for this construction entirely.
    """
    monkeypatch.delenv("ADMIN_PHONE", raising=False)

    s = Settings(_env_file=None, **_BASE_REQUIRED)

    assert s.admin_phone_unconfigured is True
    # Sanity on the premise: the default really is what ends up live.
    assert s.ADMIN_PHONE == _DEFAULT_ADMIN_PHONE


def test_admin_phone_set_to_a_different_number_is_configured():
    s = Settings(_env_file=None, **_BASE_REQUIRED, ADMIN_PHONE="972501234567")

    assert s.admin_phone_unconfigured is False


def test_admin_phone_set_to_the_default_value_is_still_configured():
    """The case worth pinning hardest.

    An operator who explicitly sets ADMIN_PHONE to a number that happens to
    equal the built-in default (production's actual configuration as of
    2026-09-16, because the default *is* the operator's own number) is
    correctly configured and must not warn. If this property is ever
    "simplified" to a comparison against the default value, this is the test
    that must go red — that comparison fires on exactly this healthy state.
    """
    s = Settings(_env_file=None, **_BASE_REQUIRED, ADMIN_PHONE=_DEFAULT_ADMIN_PHONE)

    assert s.admin_phone_unconfigured is False


def test_admin_phone_from_a_real_env_var_is_configured(monkeypatch):
    """The path production actually uses.

    The three cases above supply ADMIN_PHONE as an ``__init__`` kwarg, and
    kwargs and environment variables are different pydantic-settings sources.
    They both land in ``model_fields_set`` today — but the whole design rests
    on the env-var half, so if a future pydantic-settings bump changed that,
    every other test here would stay green while every production boot emitted
    a false report. The value is the default on purpose: env-var provenance
    *and* the value-equals-default case, together, in the one shape that ships.
    """
    monkeypatch.setenv("ADMIN_PHONE", _DEFAULT_ADMIN_PHONE)

    s = Settings(_env_file=None, **_BASE_REQUIRED)

    assert s.admin_phone_unconfigured is False
    assert s.ADMIN_PHONE == _DEFAULT_ADMIN_PHONE


def test_an_explicitly_empty_admin_phone_counts_as_unconfigured(monkeypatch):
    """``ADMIN_PHONE=`` is worse than the default, not better.

    Railway allows an empty variable and ``docker-compose.prod.yml`` uses the
    bare ``VAR=`` idiom deliberately, so this is a shape the repo really
    produces. The field lands in ``model_fields_set``, so a provenance-only
    check would call it configured — while ``to_chat_id("")`` sends nowhere and
    the masked log line degrades to ``"***"``, which cannot even say who was
    missed. ``validate_environment`` rejects an empty ENVIRONMENT for the same
    reason.
    """
    for empty in ("", "   "):
        monkeypatch.setenv("ADMIN_PHONE", empty)
        assert (
            Settings(_env_file=None, **_BASE_REQUIRED).admin_phone_unconfigured is True
        )


def test_the_report_fires_only_in_a_prod_like_environment(caplog):
    """The helper itself, not the shape of the source that calls it.

    This replaces a scan that asserted the literal
    ``settings.is_prod_like and settings.admin_phone_unconfigured`` appeared in
    both boot files. That pin went red on reorderings and reformats that
    preserve behaviour, and stayed green on a report buried in dead code —
    wrong in both directions. Exercising the function covers what matters, and
    `test_both_boot_paths_run_the_check` below keeps the call sites honest.
    """
    unset = Settings(_env_file=None, ENVIRONMENT="development", **_BASE_REQUIRED)
    assert unset.admin_phone_unconfigured is True
    assert warn_if_admin_phone_unconfigured(unset) is False, (
        "development is not prod-like — an unset ADMIN_PHONE is normal locally "
        "and must not be reported"
    )

    for env in ("staging", "production"):
        s = Settings(_env_file=None, ENVIRONMENT=env, **_PROD_LIKE_REQUIRED)
        assert warn_if_admin_phone_unconfigured(s) is True, f"{env} must report"

    configured = Settings(
        _env_file=None,
        ENVIRONMENT="production",
        ADMIN_PHONE="972501234567",
        **_PROD_LIKE_REQUIRED,
    )
    assert warn_if_admin_phone_unconfigured(configured) is False


def test_the_report_names_the_number_by_its_last_four_only():
    """A full number in a log line is the thing PRO-191/PRO-195 spent two days
    removing; four digits answer "is that warning about me?" without it."""
    s = Settings(_env_file=None, ENVIRONMENT="production", **_PROD_LIKE_REQUIRED)

    records = []
    sink_id = loguru_logger.add(records.append, level="ERROR")
    try:
        assert warn_if_admin_phone_unconfigured(s) is True
    finally:
        loguru_logger.remove(sink_id)

    assert len(records) == 1
    message = records[0].record["message"]
    assert f"***{_DEFAULT_ADMIN_PHONE[-4:]}" in message
    assert _DEFAULT_ADMIN_PHONE not in message


def test_both_boot_paths_run_the_check():
    """The api and the worker are separate Railway services that boot
    independently and each read their own variable set, so either can be the
    misconfigured one and neither can observe the other. Names only — the
    behaviour is covered above."""
    for module in ("app/main.py", "app/core/arq_worker.py"):
        source = (REPO_ROOT / module).read_text(encoding="utf-8")
        assert (
            "warn_if_admin_phone_unconfigured(settings)" in source
        ), f"{module} no longer runs the PRO-48 boot check"


if __name__ == "__main__":  # pragma: no cover
    import sys

    import pytest

    sys.exit(pytest.main([__file__, "-q"]))
