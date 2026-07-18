"""
Agent-pack drift guard (PRO-67).

The Proli sub-agent definitions under ``.claude/agents/`` embed facts that are
owned by ``app/core/constants.py``:

* ``flow-tracer.md`` hardcodes the full ``UserStates`` list, the ``LeadStatus``
  lifecycle, and four ``WorkerConstants`` TTL/threshold values.
* ``code-reviewer.md`` hardcodes the ``LeadStatus`` lifecycle.

Those are exactly the facts ``docs-syncer`` keeps fresh in ``docs/`` — but
``.claude/`` was historically outside every sync path, so the first new state or
bumped TTL would rot the agents silently (the same way docs rotted before
docs-syncer existed).

These tests are the anti-drift mechanism. They read ``constants.py`` as ground
truth and assert the embedded facts still match. Bump a TTL or add a
``UserStates`` member without updating the agent markdown and the relevant test
goes red — drift is caught deterministically in the normal ``pytest`` run, with
no agent invocation required. ``docs-syncer.md`` / ``full-sync-docs.md`` are
also extended to allow fixing these specific embeds when a failure points at
them.

The checks are scoped and word-boundaried on purpose: LeadStatus values are
matched only inside the lifecycle line (the one with the ``→`` arrows) so a
value reused in unrelated prose can't mask a deletion, and an added status that
is a substring of an existing one (e.g. ``pending`` vs ``pending_admin_review``)
is still caught. TTL values are matched with a trailing non-digit boundary so a
constant shortened ``600 → 60`` against a stale doc is not a false pass.

Skills decision (PRO-67): the three never-built skills — ``whatsapp-message``,
``fsm-transition``, ``add-scheduler-job`` — were **dropped**, not committed.
They existed only in a past planning conversation, nothing in the repo
referenced them, and the existing ``.claude/commands/`` (``add-pro``,
``simulate``, etc.) already cover the quick-action need. Adding three
speculative skills would be more surface area to keep from drifting — the exact
problem this issue exists to prevent. Recorded here and on the Linear issue so
the inventory question is closed; no ``.claude/skills/`` directory is created.
"""

import re
from pathlib import Path

from app.core.constants import LeadStatus, UserStates, WorkerConstants

_REPO_ROOT = Path(__file__).resolve().parents[1]
_AGENTS_DIR = _REPO_ROOT / ".claude" / "agents"
FLOW_TRACER = _AGENTS_DIR / "flow-tracer.md"
CODE_REVIEWER = _AGENTS_DIR / "code-reviewer.md"

# The WorkerConstants attributes that flow-tracer.md embeds by literal value.
# Extend this list only if the agent starts embedding more constants.
_FLOW_TRACER_EMBEDDED_TTLS = [
    "PAUSE_TTL_SECONDS",
    "PRO_SEARCH_RATE_LIMIT_SECONDS",
    "SOS_TIMEOUT_MINUTES",
    "STALE_BOOKED_LEAD_HOURS",
]

# ALL-CAPS enum-style token wrapped in backticks, e.g. `AWAITING_ADDRESS` or the
# `ONBOARDING_*` wildcard shorthand.
_TOKEN_RE = re.compile(r"`([A-Z][A-Z0-9_*]+)`")


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _section(md: str, header: str) -> str:
    """Return the body between a ``## <header>`` line and the next ``## `` header.

    The start match is a prefix match so an annotated header (e.g.
    ``## UserStates (FSM)``) still resolves.
    """
    out = []
    capturing = False
    for line in md.splitlines():
        if line.strip().startswith("## "):
            if capturing:
                break
            capturing = line.strip().startswith(f"## {header}")
            continue
        if capturing:
            out.append(line)
    return "\n".join(out)


def _lifecycle_text(md: str) -> str:
    """Return only the lifecycle line(s) — those carrying the ``→`` arrow.

    This scopes LeadStatus checks to the actual lifecycle, not to any prose
    that happens to reuse a status word (``new`` user, ``closed`` lead, ...).
    """
    return "\n".join(line for line in md.splitlines() if "→" in line)


def _present(word: str, haystack: str) -> bool:
    """Whole-word presence test (so ``pending`` != ``pending_admin_review``)."""
    return re.search(rf"\b{re.escape(word)}\b", haystack) is not None


def test_flow_tracer_lists_every_userstate():
    """Every UserStates member is documented in flow-tracer.md (catches new states)."""
    section = _section(_read(FLOW_TRACER), "UserStates")
    assert section.strip(), "flow-tracer.md is missing its '## UserStates' section"
    tokens = set(_TOKEN_RE.findall(section))

    missing = []
    for member in UserStates:
        name = member.name
        if name.startswith("ONBOARDING_"):
            # The five onboarding states are intentionally collapsed to ONBOARDING_*.
            if "ONBOARDING_*" not in tokens and name not in tokens:
                missing.append(name)
        elif name not in tokens:
            missing.append(name)

    assert not missing, (
        "flow-tracer.md '## UserStates' section is missing states that exist in "
        f"app/core/constants.py: {missing}. Update the agent file to match."
    )


def test_flow_tracer_has_no_stale_userstates():
    """flow-tracer.md lists no state that was renamed/removed from UserStates."""
    section = _section(_read(FLOW_TRACER), "UserStates")
    assert section.strip(), "flow-tracer.md is missing its '## UserStates' section"
    tokens = set(_TOKEN_RE.findall(section))

    valid = {member.name for member in UserStates}
    valid.add("ONBOARDING_*")  # allowed wildcard shorthand for the onboarding states

    stale = sorted(t for t in tokens if t not in valid)
    assert not stale, (
        "flow-tracer.md '## UserStates' section lists tokens that are not real "
        f"UserStates members (stale/renamed): {stale}."
    )


def test_flow_tracer_lead_lifecycle_matches_constants():
    """flow-tracer.md's lifecycle line covers every LeadStatus value (whole-word)."""
    lifecycle = _lifecycle_text(_read(FLOW_TRACER))
    assert lifecycle.strip(), "flow-tracer.md has no LeadStatus lifecycle line (→)"
    missing = [s.value for s in LeadStatus if not _present(s.value, lifecycle)]
    assert not missing, (
        f"flow-tracer.md lifecycle is missing LeadStatus values from constants.py: "
        f"{missing}."
    )


def test_code_reviewer_lead_lifecycle_matches_constants():
    """code-reviewer.md's lifecycle line covers every LeadStatus value (whole-word)."""
    lifecycle = _lifecycle_text(_read(CODE_REVIEWER))
    assert lifecycle.strip(), "code-reviewer.md has no LeadStatus lifecycle line (→)"
    missing = [s.value for s in LeadStatus if not _present(s.value, lifecycle)]
    assert not missing, (
        f"code-reviewer.md lifecycle is missing LeadStatus values from constants.py: "
        f"{missing}."
    )


def test_flow_tracer_embedded_ttls_match_constants():
    """The four TTL/threshold values embedded in flow-tracer.md match WorkerConstants."""
    md = _read(FLOW_TRACER)
    mismatches = []
    for name in _FLOW_TRACER_EMBEDDED_TTLS:
        value = getattr(WorkerConstants, name)
        # Anchor the trailing edge with (?!\d) so `= 60` does not match a stale
        # `= 600`; tolerate incidental spacing around `=`.
        if not re.search(rf"{re.escape(name)}\s*=\s*{value}(?!\d)", md):
            mismatches.append(f"{name} = {value}")
    assert not mismatches, (
        "flow-tracer.md '## TTL Constants' is out of sync with WorkerConstants; "
        f"expected 'NAME = VALUE' not found: {mismatches}."
    )


# ---------------------------------------------------------------------------
# Dispatch-order drift guard (PRO-76 item 9)
#
# flow-tracer.md's "## Dispatch Order" section enumerates the first-match-wins
# branches of ``workflow_service._process_incoming_message_inner`` in order.
# That prose historically drifted from the code (branches reversed, gates
# omitted) with nothing to catch it. These two tests are the guard.
#
# Approach: re-deriving branch *order* from source by scanning every ``if`` is
# too brittle (many branches share ``current_state == ...`` fragments). Instead
# each branch is pinned to a **unique** anchor string that appears exactly once
# in ``workflow_service.py`` inside that branch. The tests assert (a) each
# anchor is still unique and the anchors appear in the listed order in source —
# catching a real reorder or refactor — and (b) flow-tracer.md lists the same
# branch labels in the same order — catching doc drift. If a future refactor
# makes an anchor ambiguous, test (a) fails loudly and points at the anchor to
# re-pick; this is the documented limitation of the anchor approach.
# ---------------------------------------------------------------------------

_WORKFLOW_SERVICE = _REPO_ROOT / "app" / "services" / "workflow_service.py"

# (branch label as it appears bolded in flow-tracer.md, unique source anchor).
# Anchors are chosen to occur exactly once in workflow_service.py inside their
# branch; the label is the first bold span on the matching numbered doc line
# (a trailing *(conditional)* / *(↩ falls through)* marker is not part of it).
_DISPATCH_SEQUENCE = [
    ("Admin routing wizard", "handle_admin_message"),
    ("Global reset", "RESET_SUCCESS"),
    ("Help / menu", "HELP_INFO"),
    ("Inbound rate-limit gate", "check_sliding_window"),
    ("AWAITING_INTENT_CONFIRMATION", "INTENT_REPROMPT"),
    ("Consent gate", "Consent.ACCEPTED"),
    ("Politeness interceptor", "YOU_ARE_WELCOME"),
    ("Customer status pull", "reply = await _handle_status_query"),
    ("SOS / human handoff", "BOT_PAUSED_BY_CUSTOMER"),
    ("AWAITING_PRO_APPROVAL soft hold", "STILL_WAITING"),
    ("PAUSED_FOR_HUMAN", "current_state == UserStates.PAUSED_FOR_HUMAN"),
    (
        "AWAITING_RESCHEDULE_TIME",
        "current_state == UserStates.AWAITING_RESCHEDULE_TIME",
    ),
    (
        "AWAITING_LOYALTY_CONFIRMATION",
        "current_state == UserStates.AWAITING_LOYALTY_CONFIRMATION",
    ),
    ("BOOKED cancel / reschedule interceptor", "CANCELLED_ACTIVE_LEAD"),
    ("Explicit customer-mode switch", "CUSTOMER_MODE_COMMANDS"),
    ("Pro safety-bypass", "normalized_text in PRO_BUSINESS_KEYWORDS"),
    ("PRO_MODE", "current_state == UserStates.PRO_MODE:"),
    ("Pro onboarding", "current_state in ONBOARDING_STATES"),
    ("AWAITING_ADDRESS", "current_state == UserStates.AWAITING_ADDRESS"),
    ("Pro registration", "REGISTER_COMMANDS"),
    ("Auto-detect professional", "Auto-detect Professional on first contact"),
    ("Smart Dispatcher", "Smart Dispatcher Phase"),
]


def test_dispatch_anchors_unique_and_ordered_in_source():
    """Each dispatch anchor occurs exactly once and in the listed order in code."""
    lines = _WORKFLOW_SERVICE.read_text(encoding="utf-8").splitlines()
    positions = []
    for label, anchor in _DISPATCH_SEQUENCE:
        hits = [i for i, line in enumerate(lines) if anchor in line]
        assert len(hits) == 1, (
            f"dispatch anchor for {label!r} ({anchor!r}) must occur exactly once "
            f"in workflow_service.py; found {len(hits)}. Pick a more specific anchor "
            f"in _DISPATCH_SEQUENCE."
        )
        positions.append((label, hits[0]))

    source_order = [label for label, _ in sorted(positions, key=lambda p: p[1])]
    expected_order = [label for label, _ in _DISPATCH_SEQUENCE]
    assert source_order == expected_order, (
        "dispatch branches appear in a different order in workflow_service.py than "
        f"_DISPATCH_SEQUENCE declares.\n  source: {source_order}\n  expected: "
        f"{expected_order}\nUpdate flow-tracer.md and _DISPATCH_SEQUENCE to match code."
    )


def test_flow_tracer_dispatch_order_matches_code():
    """flow-tracer.md '## Dispatch Order' labels + order match the code sequence."""
    section = _section(_read(FLOW_TRACER), "Dispatch Order")
    assert section.strip(), "flow-tracer.md is missing its '## Dispatch Order' section"

    doc_labels = re.findall(r"^\d+\.\s+\*\*(.+?)\*\*", section, re.MULTILINE)
    expected_labels = [label for label, _ in _DISPATCH_SEQUENCE]
    assert doc_labels == expected_labels, (
        "flow-tracer.md '## Dispatch Order' branch labels/order are out of sync with "
        f"workflow_service.py.\n  doc:      {doc_labels}\n  expected: {expected_labels}"
    )
