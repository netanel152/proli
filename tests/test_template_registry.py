"""Tests for `app/providers/whatsapp/template_registry.py` — the PRO-88 catalog
as code.

A `TemplateSpec` deliberately stores no body text — the copy has exactly one
home (`app/core/messages.py`) — so what it owes the catalog is `source`, a
pointer to the `Messages.*` constant the template must be worded from
(PRO-168). These tests are the anti-drift point of that field: every spec must
name a real constant, or the pointer itself has silently rotted.
"""

from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.core.messages import Messages
from app.providers.whatsapp import template_registry
from app.providers.whatsapp.template_registry import (
    DELIVERY_PROBE_KEY,
    TEMPLATES,
    TemplateSpec,
    TemplateStatus,
)


def _resolves(dotted_path: str) -> bool:
    """Walk a dotted ``Messages.X.Y`` path and confirm it resolves."""
    obj = Messages
    for part in dotted_path.split(".")[1:]:  # drop the leading "Messages"
        if not hasattr(obj, part):
            return False
        obj = getattr(obj, part)
    return True


def test_every_template_spec_has_a_non_empty_source():
    for key, spec in TEMPLATES.items():
        assert spec.source, f"template {key!r} has no `source` — a body with no pointer"


def test_every_template_source_names_a_real_messages_constant():
    """A `source` may combine more than one constant (``"A + B"``, when a
    template's body is composed of two call-site strings) — each named
    constant must resolve on its own."""
    for key, spec in TEMPLATES.items():
        for dotted_path in (p.strip() for p in spec.source.split("+")):
            assert dotted_path.startswith("Messages."), (
                f"template {key!r}'s source {dotted_path!r} doesn't name a "
                f"Messages.* constant"
            )
            assert _resolves(dotted_path), (
                f"template {key!r}'s source {dotted_path!r} does not resolve "
                f"on Messages — the pointer has rotted"
            )


# ---------------------------------------------------------------------------
# PRO-232 — arming: APPROVED in the registry *and* named by this environment
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _fresh_arming_warnings(monkeypatch):
    """The once-per-process warning set must not carry over between tests."""
    monkeypatch.setattr(template_registry, "_WARNED_ARMING_TOKENS", set())


@pytest.fixture
def registry(monkeypatch):
    """Four approved entries (a-d) and one DRAFT, so arming is tested on its own."""
    specs = {
        key: TemplateSpec(key, f"proli_{key}", status=TemplateStatus.APPROVED)
        for key in "abcd"
    }
    specs["draft"] = TemplateSpec("draft", "proli_draft")
    monkeypatch.setattr(template_registry, "TEMPLATES", specs)
    return specs


@pytest.mark.parametrize(
    "setting, armed",
    [
        ("", set()),
        (None, set()),
        ("a", {"a"}),
        ("a, b;c  d", {"a", "b", "c", "d"}),
        (" a,,;\nb\t", {"a", "b"}),
        ("draft", set()),
        ("nope", set()),
        ("*", set()),
        ("all", set()),
        ("a,*,nope,draft,b", {"a", "b"}),
    ],
    ids=[
        "unset",
        "none",
        "one",
        "mixed-separators",
        "stray-separators",
        "draft",
        "unknown",
        "star",
        "all",
        "only-the-valid-ones",
    ],
)
def test_armed_keys_holds_only_the_named_keys_that_are_approved(
    registry, monkeypatch, setting, armed
):
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", setting)

    assert template_registry.armed_keys() == armed
    assert [s.key for s in template_registry.armed_specs()] == sorted(armed)


def test_resolve_wants_the_key_approved_and_named(registry, monkeypatch):
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", "a,draft")

    assert template_registry.resolve("a") is registry["a"]
    assert template_registry.resolve("b") is None  # approved, not named here
    assert template_registry.resolve("draft") is None  # named, still DRAFT
    assert template_registry.resolve("nope") is None


def test_a_bad_token_is_warned_about_once_however_often_it_is_read(
    registry, monkeypatch
):
    warn = MagicMock()
    monkeypatch.setattr(template_registry, "logger", warn)
    monkeypatch.setattr(settings, "ENVIRONMENT", "staging")
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", "draft, nope, *, a")

    template_registry.armed_keys()
    template_registry.armed_keys()

    messages = [c.args[0] for c in warn.warning.call_args_list]
    for token in ("'draft'", "'nope'", "'*'"):
        assert len([m for m in messages if token in m]) == 1
    assert len(messages) == 3  # and the valid key `a` is not one of them


@pytest.mark.parametrize(
    "environment, level, other",
    [("production", "error", "warning"), ("staging", "warning", "error")],
)
def test_a_bad_token_is_an_error_in_production_and_a_warning_elsewhere(
    registry, monkeypatch, environment, level, other
):
    log = MagicMock()
    monkeypatch.setattr(template_registry, "logger", log)
    monkeypatch.setattr(settings, "ENVIRONMENT", environment)
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", "nope")

    assert template_registry.armed_keys() == frozenset()

    getattr(log, level).assert_called_once()
    getattr(log, other).assert_not_called()


def test_the_real_registry_arms_what_is_named_and_never_a_draft(monkeypatch):
    """`rate_service` is the one Proli template still DRAFT (never submitted),
    so naming it arms nothing; an approved key arms only once it is named."""
    assert TEMPLATES["rate_service"].status is TemplateStatus.DRAFT
    monkeypatch.setattr(settings, "WHATSAPP_ARMED_TEMPLATES", "lead_offer")
    assert template_registry.resolve(DELIVERY_PROBE_KEY) is None
    assert template_registry.resolve("lead_offer") is TEMPLATES["lead_offer"]

    monkeypatch.setattr(
        settings, "WHATSAPP_ARMED_TEMPLATES", f"{DELIVERY_PROBE_KEY},rate_service"
    )
    assert (
        template_registry.resolve(DELIVERY_PROBE_KEY) is TEMPLATES[DELIVERY_PROBE_KEY]
    )
    assert template_registry.resolve("rate_service") is None
    assert template_registry.resolve("lead_offer") is None
