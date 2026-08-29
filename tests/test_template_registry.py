"""Tests for `app/providers/whatsapp/template_registry.py` — the PRO-88 catalog
as code.

A `TemplateSpec` deliberately stores no body text — the copy has exactly one
home (`app/core/messages.py`) — so what it owes the catalog is `source`, a
pointer to the `Messages.*` constant the template must be worded from
(PRO-168). These tests are the anti-drift point of that field: every spec must
name a real constant, or the pointer itself has silently rotted.
"""

from app.core.messages import Messages
from app.providers.whatsapp.template_registry import TEMPLATES


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
