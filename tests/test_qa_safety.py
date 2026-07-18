"""Unit tests for tests/qa_safety.py — the production guard for manual QA scripts
(PRO-72). Plain sync functions, no asyncio needed. No I/O — _input/_print are
stubbed so tests never block on real stdin or spam stdout.
"""

import pytest

try:
    from tests.qa_safety import is_production_instance, preflight_or_abort
except ImportError:  # pragma: no cover - fallback mirroring simulate_test.py
    from qa_safety import is_production_instance, preflight_or_abort


# ---------------------------------------------------------------------------
# is_production_instance
# ---------------------------------------------------------------------------


def test_is_production_instance_true_when_matches_env(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")
    assert is_production_instance("1234567890") is True


def test_is_production_instance_false_when_differs_from_env(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")
    assert is_production_instance("9999999999") is False


def test_is_production_instance_fail_safe_true_when_env_unset(monkeypatch):
    monkeypatch.delenv("GREEN_API_INSTANCE_ID", raising=False)
    assert is_production_instance("anything") is True


def test_is_production_instance_fail_safe_true_when_env_empty_string(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "")
    assert is_production_instance("anything") is True


def test_is_production_instance_numeric_vs_string_equality(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "7105567180")
    assert is_production_instance(7105567180) is True


def test_is_production_instance_whitespace_handling(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "  1234567890  ")
    assert is_production_instance(" 1234567890 ") is True


def test_is_production_instance_whitespace_mismatch_still_false(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")
    assert is_production_instance(" 999 ") is False


# ---------------------------------------------------------------------------
# preflight_or_abort
# ---------------------------------------------------------------------------


def _silent_print(*args, **kwargs):
    pass


def test_preflight_aborts_on_production_without_allow_production(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    with pytest.raises(SystemExit) as exc_info:
        preflight_or_abort(
            instance_id="1234567890",
            base_url="http://localhost:8000",
            db_name="proli_db",
            recipients=["972500000000"],
            allow_production=False,
            _print=_silent_print,
        )

    assert exc_info.value.code == 3


def test_preflight_passes_on_production_with_allow_production(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    # Should not raise.
    preflight_or_abort(
        instance_id="1234567890",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        allow_production=True,
        _print=_silent_print,
    )


def test_preflight_passes_on_non_production_instance(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    # Non-production target, allow_production defaults to False — should not raise.
    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        _print=_silent_print,
    )


def test_preflight_fail_safe_aborts_when_env_unset(monkeypatch):
    """No live instance id known -> everything is treated as production."""
    monkeypatch.delenv("GREEN_API_INSTANCE_ID", raising=False)

    with pytest.raises(SystemExit) as exc_info:
        preflight_or_abort(
            instance_id="whatever-qa-instance",
            base_url="http://localhost:8000",
            db_name="proli_db",
            recipients=["972500000000"],
            allow_production=False,
            _print=_silent_print,
        )

    assert exc_info.value.code == 3


def test_preflight_destructive_confirmed_with_yes_passes(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    called = {}

    def fake_input(prompt):
        called["prompt"] = prompt
        return "yes"

    # Non-production instance so we get past the production gate.
    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        destructive=True,
        assume_yes=False,
        _input=fake_input,
        _print=_silent_print,
    )

    assert "prompt" in called


def test_preflight_destructive_confirmed_case_and_whitespace_insensitive(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        destructive=True,
        assume_yes=False,
        _input=lambda prompt: "  YES  ",
        _print=_silent_print,
    )


def test_preflight_destructive_declined_aborts(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    with pytest.raises(SystemExit) as exc_info:
        preflight_or_abort(
            instance_id="9999999999",
            base_url="http://localhost:8000",
            db_name="proli_db",
            recipients=["972500000000"],
            destructive=True,
            assume_yes=False,
            _input=lambda prompt: "no",
            _print=_silent_print,
        )

    assert exc_info.value.code == 3


def test_preflight_destructive_assume_yes_skips_input(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    def fail_input(prompt):
        raise AssertionError("_input should not be called when assume_yes=True")

    # Should not raise and should never touch _input.
    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        destructive=True,
        assume_yes=True,
        _input=fail_input,
        _print=_silent_print,
    )


def test_preflight_prints_banner_via_injected_print(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    printed = []

    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000", "972511111111"],
        _print=printed.append,
    )

    joined = "\n".join(printed)
    assert "9999999999" in joined
    assert "http://localhost:8000" in joined
    assert "proli_db" in joined
    assert "972500000000" in joined
    assert "972511111111" in joined


def test_preflight_aborts_on_production_before_destructive_prompt(monkeypatch):
    """Production gate must be checked before the destructive confirmation prompt —
    _input must never be reached when aborting on production, even if
    destructive=True."""
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    def fail_input(prompt):
        raise AssertionError(
            "_input should not be called — production gate aborts first"
        )

    with pytest.raises(SystemExit) as exc_info:
        preflight_or_abort(
            instance_id="1234567890",
            base_url="http://localhost:8000",
            db_name="proli_db",
            recipients=["972500000000"],
            destructive=True,
            allow_production=False,
            _input=fail_input,
            _print=_silent_print,
        )

    assert exc_info.value.code == 3


def test_preflight_non_destructive_never_calls_input(monkeypatch):
    monkeypatch.setenv("GREEN_API_INSTANCE_ID", "1234567890")

    def fail_input(prompt):
        raise AssertionError("_input should not be called when destructive=False")

    preflight_or_abort(
        instance_id="9999999999",
        base_url="http://localhost:8000",
        db_name="proli_db",
        recipients=["972500000000"],
        destructive=False,
        _input=fail_input,
        _print=_silent_print,
    )
