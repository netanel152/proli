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

from app.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parents[1]

_BASE_REQUIRED = dict(
    GEMINI_API_KEY="k",
    CLOUDINARY_CLOUD_NAME="c",
    CLOUDINARY_API_KEY="k2",
    CLOUDINARY_API_SECRET="s",
)

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
    "simplified" to `self.ADMIN_PHONE == "972524828796"`, this is the test
    that must go red — that comparison fires on exactly this healthy state.
    """
    s = Settings(_env_file=None, **_BASE_REQUIRED, ADMIN_PHONE=_DEFAULT_ADMIN_PHONE)

    assert s.admin_phone_unconfigured is False


def test_boot_paths_consult_the_guard_and_gate_on_is_prod_like():
    """Both app/main.py's lifespan and app/core/arq_worker.py's startup must
    warn exactly when a prod-like deploy has an unconfigured ADMIN_PHONE.

    Source-level rather than exercising the real lifespan/startup: both pull
    in Mongo/Redis connect-with-retry, index creation, the APScheduler start
    and the heartbeat loop for behaviour that is a single `if` plus a log
    call — mocking all of that to observe one warning is disproportionate to
    what PRO-48 added. Mirrors the repo-scan style of
    tests/test_log_pii.py and tests/test_admin_view_call_arity.py.
    """
    guarded_files = (
        Path("app") / "main.py",
        Path("app") / "core" / "arq_worker.py",
    )
    for relative in guarded_files:
        source = (REPO_ROOT / relative).read_text(encoding="utf-8")
        idx = source.find("settings.is_prod_like and settings.admin_phone_unconfigured")
        assert idx != -1, (
            f"{relative} no longer gates a boot warning on "
            "`settings.is_prod_like and settings.admin_phone_unconfigured` "
            "(PRO-48) — an unconfigured ADMIN_PHONE in a prod-like "
            "deployment would go unnoticed."
        )
        # Not just a truthy guard — it has to actually warn somebody.
        tail = source[idx : idx + 400]
        assert "logger.warning" in tail, (
            f"{relative}: the PRO-48 guard is present but no logger.warning "
            "follows it within range — the check would be silent."
        )
