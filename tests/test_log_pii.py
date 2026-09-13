"""PRO-191: a phone number or an address interpolated straight into a log
f-string is not scrubbed by anything.

`app/core/logger.py`'s redaction list is built from `Settings` field *names*
(PRO-94) — that catches a leaked secret, not a customer's chat id or street
address, neither of which is a `Settings` field. PRO-173 already settled the
rule for *paging*: mask the phone to its last digits, never send the street
(city only). Since PRO-184 every log line is flat, indexed JSON in Railway's
Log Explorer, so the same leak class applies to a plain `logger.info(f"...")`
call — it is now exactly as searchable as the page was.

This file is a **ratchet**, not a blanket check. PRO-191 fixed the 31 log
lines the issue scoped (`dispatch_guards.py`, `workflow_service.py`) — those
two files must NOT appear in `KNOWN_VIOLATIONS` below, because their absence is
exactly what makes assertion 1 mean something. The remaining 38, across 12
files in `app/` and `admin_panel/`, are recorded debt: the three assertions
below make that number payable-down only — never inflated, and never left stale
once somebody *does* pay it down.

Counts to trust are the ones in the dict, not any figure in prose. The review
of this PR found two ways the first cut miscounted, both worth remembering:
`mask_chat_id` is imported aliased (`as _mask`) in four modules, so a substring
rule booked eight already-masked lines as debt and left three files unable to
regress visibly; and chained calls (`logger.bind(...).error(...)`) were excluded
"to match the original scan", which made wrapping a violation a way to lower
the count — the ratchet endorsing the leak.

The detector itself (`find_violations`) is the one thing both the repo scan and
its own unit tests exercise — a scanner that silently matched nothing would
make the whole ratchet vacuous while staying green, so its rules are pinned by
unit tests on in-process source snippets at the bottom of this file.

Detection rules, applied to every `{...}` slot (`ast.FormattedValue`) inside
any call that bottoms out at `logger`:

  1. chat id — the expression mentions `chat_id` and is not rendered through a
     masking slice (`[-4:]`, `[-8:]`) or a masking callee (`mask_chat_id` and
     its aliases). `strip_suffix` is NOT accepted: it returns the whole number.
  2. address / name / speech — the expression structurally references
     `full_address`, `street`, `street_number`, `customer_name`,
     `display_name`, `transcription`, `pro_phone` or `phone_number`, as a
     `Name`, an `Attribute.attr`, a dict-subscript key, or a `.get("field")`
     argument. `city` is never flagged: PRO-173 rules it safe enough to send
     off-platform, and it is what makes a failed address gate diagnosable.
     A reference wrapped in a masker or in `len()` does not count — neither
     discloses what it wraps.
"""

import ast
import functools
import textwrap
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
#: Both trees are scanned. The admin panel logs too, and leaving it out would
#: have meant a guard that stops at the boundary of the code it was written
#: from — `admin_panel/views/home.py` has a bare `{chat_id}` today.
SCAN_ROOTS = (REPO_ROOT / "app", REPO_ROOT / "admin_panel")

#: Slice forms that mask a chat id. Tolerated because they predate
#: `mask_chat_id`; prefer the function in new code — it strips the `@c.us`
#: suffix before slicing, so it yields real digits rather than a constant, and
#: it is None-safe.
#:
#: `strip_suffix` is deliberately NOT here. It returns `972501234567` — the
#: whole number, minus the suffix — so accepting it would certify a full-phone
#: leak as clean. No log line uses it today; it is exactly the thing someone
#: reaches for next.
_SAFE_CHAT_ID_SLICES = ("[-8:]", "[-4:]")

#: Callables that render a chat id safely. Matched on the callee *name*, so an
#: aliased import counts: four modules do `from app.core.phone import
#: mask_chat_id as _mask`, and a substring rule missed every one of them —
#: recording 8 already-masked lines as debt and leaving three files (the
#: outbound egress among them) with counts that could never go up.
_SAFE_MASKING_CALLEES = {"mask_chat_id", "_mask", "mask_pii"}

#: Callables whose *return value* is PII regardless of how it is spelled.
_UNSAFE_CALLEES = {"compose_full_address"}

#: Calls whose argument is not disclosed by their result, so an unsafe field
#: inside one is safe: the maskers, plus `len` — a character count of a
#: transcription tells you it was long, not what it said.
_SAFE_WRAPPER_CALLEES = _SAFE_MASKING_CALLEES | {"len"}

_UNSAFE_FIELD_NAMES = {
    "full_address",
    "street",
    "street_number",
    "customer_name",
    "display_name",
    # Free-form customer speech routinely *is* the name and the address.
    "transcription",
    # Other people's numbers are no less personal than the sender's.
    "pro_phone",
    "phone_number",
}

# Exact known debt, file -> violation count, as of PRO-191. `dispatch_guards.py`
# and `workflow_service.py` are the two files this PR cleaned and must never
# reappear here — that omission is what makes assertion 1 below mean anything.
KNOWN_VIOLATIONS = {
    "admin_panel/views/home.py": 1,
    "app/api/routes/webhook.py": 1,
    "app/core/arq_worker.py": 4,
    "app/core/redis_client.py": 2,
    "app/services/context_manager_service.py": 5,
    "app/services/customer_flow.py": 2,
    "app/services/data_management_service.py": 2,
    "app/services/monitor_service.py": 5,
    "app/services/notification_service.py": 2,
    "app/services/pro_flow.py": 6,
    "app/services/security_service.py": 1,
    "app/services/state_manager_service.py": 7,
}


# ---------------------------------------------------------------------------
# The detector. Shared by the repo scan below and by its own unit tests —
# otherwise the unit tests would prove nothing about what the scan does.
# ---------------------------------------------------------------------------


def _is_logger_call(call: ast.Call) -> bool:
    """True for any call that bottoms out at the `logger` name — `logger.info(...)`
    and equally `logger.bind(...).error(...)` or `logger.opt(...).critical(...)`.

    Chained forms were out of scope in the first cut of this guard, to match the
    scan the counts came from. That was a **laundering path**, not a counting
    detail: wrapping a violating call as `logger.bind(...).error(f"...{chat_id}")`
    made the count *drop*, and the below-count assertion would then have told the
    developer to lower the allowlist — the ratchet ratifying the leak it exists
    to catch. Closing it costs one number (`arq_worker.py` 3 -> 4, a line that
    was always exposed and merely uncounted).

    Still uncovered, and recorded rather than hidden: a *bound field*
    (`logger.bind(chat_id=chat_id)`) is invisible to an f-string scan, and since
    PRO-184 it lands as its own top-level JSON key — more indexed, not less.
    """
    func = call.func
    while isinstance(func, ast.Attribute):
        func = func.value
        if isinstance(func, ast.Call):
            func = func.func
    return isinstance(func, ast.Name) and func.id == "logger"


def _calls_in(node: ast.AST):
    """Every call appearing anywhere inside `node`."""
    return [n for n in ast.walk(node) if isinstance(n, ast.Call)]


def _callee_name(call: ast.Call):
    """`f(x)` -> 'f'; `mod.f(x)` -> 'f'; anything else -> None."""
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _subscript_const_str_key(node: ast.Subscript):
    """The string key of a dict-style subscript (`lead['full_address']`),
    or None. Handles both the 3.9+ shape (slice is the expression directly)
    and the pre-3.9 `ast.Index` wrapper."""
    sl = node.slice
    if hasattr(ast, "Index") and isinstance(sl, ast.Index):  # pragma: no cover
        sl = sl.value
    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
        return sl.value
    return None


def _references_unsafe_field(expr: ast.AST) -> bool:
    """Structural match only — a bare string literal sitting in a tuple/list
    (e.g. `('street', 'street_number')` used to report which field *names*
    are present) is not a reference to field *content* and must not trip
    this. Only a `Name`, an `Attribute.attr`, a dict-subscript key, a
    `.get("field")` argument, or an unsafe callee count.

    `.get()` is not an afterthought: `lead.get("customer_name")` outnumbers the
    subscript form across `app/services/`, so a rule that handled only
    `lead["customer_name"]` would miss the dominant access style in this
    codebase and pass most of what it exists to catch.
    """
    # Walked by hand rather than with `ast.walk`, so a subtree wrapped in a safe
    # call can be skipped whole. `mask_chat_id(pro_phone)` and
    # `len(transcription or "")` both *reference* an unsafe field and neither
    # discloses it — a flat walk flags both, which would have made this guard
    # reject the very lines written to satisfy it.
    if isinstance(expr, ast.Call) and _callee_name(expr) in _SAFE_WRAPPER_CALLEES:
        return False

    if isinstance(expr, ast.Name) and expr.id in _UNSAFE_FIELD_NAMES:
        return True
    if isinstance(expr, ast.Attribute) and expr.attr in _UNSAFE_FIELD_NAMES:
        return True
    if isinstance(expr, ast.Subscript):
        if _subscript_const_str_key(expr) in _UNSAFE_FIELD_NAMES:
            return True
    if isinstance(expr, ast.Call):
        if _callee_name(expr) in _UNSAFE_CALLEES:
            return True
        # `x.get("street")` / `x.get("street", default)`
        if isinstance(expr.func, ast.Attribute) and expr.func.attr == "get":
            for arg in expr.args:
                if (
                    isinstance(arg, ast.Constant)
                    and isinstance(arg.value, str)
                    and arg.value in _UNSAFE_FIELD_NAMES
                ):
                    return True

    return any(_references_unsafe_field(child) for child in ast.iter_child_nodes(expr))


def _chat_id_is_masked(expr: ast.AST, expr_src: str) -> bool:
    """Whether a chat-id-bearing expression renders it safely.

    The callee check is structural rather than a substring of the source,
    because four modules import the masker aliased (`mask_chat_id as _mask`).
    A substring rule saw `_mask(chat_id)` as unmasked and booked eight
    already-safe lines as debt — which, in a ratchet, is the direction that
    goes blind rather than red: those files' counts could never rise, so a real
    regression in them would have passed.
    """
    if any(slice_form in expr_src for slice_form in _SAFE_CHAT_ID_SLICES):
        return True
    return any(_callee_name(c) in _SAFE_MASKING_CALLEES for c in _calls_in(expr))


def _is_violation(expr: ast.AST, expr_src: str) -> bool:
    if "chat_id" in expr_src and not _chat_id_is_masked(expr, expr_src):
        return True
    return _references_unsafe_field(expr)


def find_violations(source: str) -> list[tuple[int, str]]:
    """Every `(lineno, expr_source)` PII violation inside f-string slots of
    direct `logger.*` calls in `source`."""
    tree = ast.parse(source)
    violations: list[tuple[int, str]] = []
    seen: set[int] = set()
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and _is_logger_call(node)):
            continue
        for sub in ast.walk(node):
            if not isinstance(sub, ast.FormattedValue):
                continue
            if id(sub) in seen:
                continue
            seen.add(id(sub))
            expr_src = ast.get_source_segment(source, sub.value) or ast.unparse(
                sub.value
            )
            if _is_violation(sub.value, expr_src):
                violations.append((sub.lineno, expr_src))
    return violations


@functools.lru_cache(maxsize=1)
def _scan_repo() -> dict:
    """`{relative/posix/path.py: [(lineno, expr), ...]}` for every file under
    `SCAN_ROOTS` that has at least one violation. Cached — three tests below
    all want the same scan and a fresh `ast.parse` of the whole tree per test
    would be pure waste."""
    results = {}
    for root in SCAN_ROOTS:
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            violations = find_violations(source)
            if violations:
                results[path.relative_to(REPO_ROOT).as_posix()] = violations
    return results


# ---------------------------------------------------------------------------
# The ratchet, against the real tree.
# ---------------------------------------------------------------------------


def test_no_new_unmasked_pii_log_interpolations_outside_known_violations():
    """Assertion 1: a file not already carrying recorded debt must have zero
    violations. This is what stops a new one appearing anywhere — including
    back in `dispatch_guards.py`/`workflow_service.py`, which this PR just
    cleaned and which must therefore never gain an entry in
    `KNOWN_VIOLATIONS`."""
    scanned = _scan_repo()
    unexpected = {
        path: len(v) for path, v in scanned.items() if path not in KNOWN_VIOLATIONS
    }
    assert not unexpected, (
        "Unmasked chat_id/address/name interpolation(s) in logger calls, in "
        f"files with no recorded ratchet debt: {unexpected}. Mask the chat id "
        "(chat_id[-8:], mask_chat_id(), strip_suffix()) or drop the address/"
        "name field from the log line — city is fine, the street/name is not."
    )


def test_known_violation_files_do_not_exceed_recorded_count():
    """Assertion 2: no backsliding in a file that already has debt."""
    scanned = _scan_repo()
    regressed = {
        path: (expected, len(scanned.get(path, [])))
        for path, expected in KNOWN_VIOLATIONS.items()
        if len(scanned.get(path, [])) > expected
    }
    assert not regressed, (
        "Violation count increased in file(s) already carrying ratchet debt "
        f"(expected, actual): {regressed}. Revert the new interpolation(s) or "
        "mask them before raising the recorded count."
    )


def test_known_violation_files_are_not_below_recorded_count():
    """Assertion 3: `KNOWN_VIOLATIONS` must track reality exactly, not just
    bound it — a count that is only ever a ceiling silently rots into a
    blanket exemption for that file. When a fix lowers the real count, this
    test fails and says exactly which number to write (or that the entry can
    be deleted)."""
    scanned = _scan_repo()
    stale = {
        path: len(scanned.get(path, []))
        for path, expected in KNOWN_VIOLATIONS.items()
        if len(scanned.get(path, [])) < expected
    }
    assert not stale, (
        "KNOWN_VIOLATIONS is stale — these files now have fewer violations "
        "than recorded. Lower KNOWN_VIOLATIONS to match (or delete the entry "
        f"if it reached 0): {stale}"
    )


# ---------------------------------------------------------------------------
# Unit tests for the detector itself, on in-process source snippets.
# ---------------------------------------------------------------------------


def test_detector_allows_the_accepted_chat_id_mask():
    src = textwrap.dedent(
        """
        def handler():
            logger.info(f"🚦 User ...{ctx.chat_id[-8:]} is in State: {state}")
        """
    )
    assert find_violations(src) == []


def test_detector_catches_bare_chat_id_interpolation():
    src = textwrap.dedent(
        """
        def handler():
            logger.info(f"Task started: processing message for {chat_id}")
        """
    )
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_catches_attribute_chat_id_interpolation():
    src = textwrap.dedent(
        """
        def handler():
            logger.warning(f"stuck lead for pro ...{ctx.chat_id}")
        """
    )
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_catches_dict_style_full_address():
    src = textwrap.dedent(
        """
        def handler():
            logger.error(f"lead parse failed for {lead['full_address']}")
        """
    )
    violations = find_violations(src)
    assert len(violations) == 1


def test_detector_allows_city_interpolation():
    src = textwrap.dedent(
        """
        def handler():
            logger.info(f"no pro available near {city}")
        """
    )
    assert find_violations(src) == []


def test_detector_ignores_chat_id_in_a_non_logger_call():
    """The rule is about log lines, not every f-string in the codebase — a
    provider send call that happens to interpolate chat_id is not this
    ticket's concern."""
    src = textwrap.dedent(
        """
        async def handler():
            await whatsapp.send_message(chat_id, f"hello {chat_id}")
        """
    )
    assert find_violations(src) == []


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
