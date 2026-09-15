"""PRO-191: a phone number or an address interpolated straight into a log
f-string is not scrubbed by anything.

`app/core/logger.py`'s redaction list is built from `Settings` field *names*
(PRO-94) — that catches a leaked secret, not a customer's chat id or street
address, neither of which is a `Settings` field. PRO-173 already settled the
rule for *paging*: mask the phone to its last digits, never send the street
(city only). Since PRO-184 every log line is flat, indexed JSON in Railway's
Log Explorer, so the same leak class applies to a plain `logger.info(f"...")`
call — it is now exactly as searchable as the page was.

PRO-195 took this from a ratchet to a floor. PRO-191 fixed the 31 log lines
it scoped (`dispatch_guards.py`, `workflow_service.py`) and carried the other
38, across 12 files, as an enumerated `KNOWN_VIOLATIONS` allowlist that could
only shrink. PRO-195 paid all 38 down, so the allowlist is gone and the rule is
now unconditional: **zero violations anywhere under `app/` and `admin_panel/`.**

The allowlist was deleted rather than left empty on purpose. With no entries,
its two companion assertions — *no file above its count*, *none below it
either* — iterate over nothing and pass forever without testing anything, and a
rule that cannot fire is worse than no rule (the PRO-179 dead `60vh` cap). What
replaces them is `test_the_repo_scan_is_not_vacuous`: the failure mode of an
unconditional scan is that it silently walks nothing — a wrong `SCAN_ROOTS`, a
tree that moved — and stays green while proving nothing.

The review of PRO-191 found two ways its first cut miscounted, both still worth
remembering because both are properties of the detector this file still uses:
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


def test_no_unmasked_pii_log_interpolations_anywhere():
    """The floor, and since PRO-195 the whole contract: not one `logger.*`
    f-string slot under `app/` or `admin_panel/` may render an unmasked chat id
    or a structural reference to an address, a name or somebody else's number.

    There is no allowlist to add a file to. That is the point — the escape
    hatch is what let 38 lines sit for two days after the ticket that named
    them, and this repo already runs `black --check` and `flake8 --count` at
    zero with no per-file exemptions."""
    scanned = _scan_repo()
    counts = {path: len(v) for path, v in scanned.items()}
    assert not counts, (
        f"Unmasked chat_id/address/name interpolation(s) in logger calls: {counts}. "
        "Mask the chat id with `mask_chat_id()` from `app/core/phone.py`, or drop "
        "the address/name field from the log line — city is fine, the street/name "
        "is not. Offending expressions: "
        f"{ {path: [e for _, e in v] for path, v in scanned.items()} }"
    )


def test_the_repo_scan_is_not_vacuous():
    """An unconditional 'zero violations' assertion has exactly one silent
    failure mode: a scan that walks nothing passes it.

    While `KNOWN_VIOLATIONS` existed, assertion 3 covered this incidentally —
    a scan returning nothing made every recorded count look stale and went
    red. With the allowlist gone that cover is gone with it, so the scan's own
    reach is asserted directly: both roots exist, both hold modules, and the
    walk parses a realistic number of them rather than zero."""
    for root in SCAN_ROOTS:
        assert (
            root.is_dir()
        ), f"SCAN_ROOTS names something that is not a directory: {root}"
        assert list(
            root.rglob("*.py")
        ), f"No Python files under {root} — the scan is blind there"

    # 79 modules across the two roots at PRO-195. The bound sits far below that
    # deliberately: it is here to catch a tree that moved out from under the
    # scan, not to track the file count, which would make it a chore.
    scanned_files = [path for root in SCAN_ROOTS for path in root.rglob("*.py")]
    assert len(scanned_files) >= 50, (
        f"Only {len(scanned_files)} Python files under {[str(r) for r in SCAN_ROOTS]} — "
        "the scan has lost its tree, and the zero-violations assertion above is "
        "passing because it looked at nothing."
    )

    # And the detector still fires on the shape it exists for, through the same
    # entry point the repo scan uses.
    assert find_violations('logger.info(f"x {chat_id}")')


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


def test_detector_catches_a_name_that_merely_contains_chat_id():
    """`customer_chat_id` is somebody else's number and reads as plainly as the
    sender's. The rule is a substring of the expression source rather than an
    exact name match, which is what catches this — three of the lines PRO-195
    fixed in `pro_flow.py` were this shape, and an exact-name rule would have
    certified all three as clean."""
    src = textwrap.dedent(
        """
        def handler():
            logger.info(f"Pro {pro['_id']} paused bot for customer {customer_chat_id}")
        """
    )
    assert len(find_violations(src)) == 1

    masked = textwrap.dedent(
        """
        def handler():
            logger.info(
                f"Pro {pro['_id']} paused bot for customer {mask_chat_id(customer_chat_id)}"
            )
        """
    )
    assert find_violations(masked) == []


def test_detector_accepts_mask_chat_id_on_a_pro_phone():
    """`mask_chat_id` is not only for chat ids: it strips `@c.us` if present and
    takes the last four digits either way, so it is also the right treatment for
    a bare `pro_phone`. `monitor_service.py`'s stale-lead nudger — the one
    non-chat-id line in the PRO-195 batch — logs exactly this."""
    raw = textwrap.dedent(
        """
        def handler():
            logger.error(f"Failed to send reminder to {pro_phone}: {e}")
        """
    )
    assert len(find_violations(raw)) == 1

    masked = textwrap.dedent(
        """
        def handler():
            logger.error(f"Failed to send reminder to {mask_chat_id(pro_phone)}: {e}")
        """
    )
    assert find_violations(masked) == []


def test_detector_catches_the_fsm_transition_line():
    """The highest-traffic line in the PRO-195 batch, and the one that makes the
    case: `state_manager_service` logs a transition on every state write, so a
    single unmasked slot there puts the number beside a searchable state name on
    every turn of every conversation. The masked form keeps the diagnostic value
    — which user, which transition — and drops the identity."""
    raw = textwrap.dedent(
        """
        def handler():
            logger.info(f"FSM {chat_id}: {prev} -> {state_value} (ttl={ttl}s)")
        """
    )
    assert len(find_violations(raw)) == 1

    masked = textwrap.dedent(
        """
        def handler():
            logger.info(f"FSM {mask_chat_id(chat_id)}: {prev} -> {state_value} (ttl={ttl}s)")
        """
    )
    assert find_violations(masked) == []


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
