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
#: from — `admin_panel/views/home.py` carried a bare `{chat_id}` when this file
#: was written, and PRO-195 masked it.
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
    # PRO-195 review: the inbound location payload. `user_text` on that path is
    # the place name, the street and the exact lat/long, and none of it is
    # reachable by the sink filters — `_HOUSE_NUMBER` refuses digit runs
    # containing `.`, so the GPS pair survives every one of them, and a
    # Latin-script street survives `_ADDRESS_PATTERN`. Costs nothing: no line
    # in either tree logs any of the three today.
    "user_text",
    "latitude",
    "longitude",
}

#: Matched **only** as a record-field access — `doc["name"]` or
#: `doc.get("name")` — never as a bare `name` or an `obj.name`.
#:
#: The distinction is the whole reason this set exists separately. Putting
#: `name` in `_UNSAFE_FIELD_NAMES` flags 17 lines, and 16 of them are a
#: provider's name, a city's name or an enum member's `.name`; the seventeenth
#: is a person. A rule that cries wolf sixteen times out of seventeen gets
#: switched off, so it is narrowed to the access shape a *record field* takes.
_UNSAFE_RECORD_FIELD_NAMES = {"name"}


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
        key = _subscript_const_str_key(expr)
        if key in _UNSAFE_FIELD_NAMES or key in _UNSAFE_RECORD_FIELD_NAMES:
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
                    and (
                        arg.value in _UNSAFE_FIELD_NAMES
                        or arg.value in _UNSAFE_RECORD_FIELD_NAMES
                    )
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
def _scan_repo() -> tuple[dict, dict]:
    """`({relative/posix/path.py: [(lineno, expr), ...]}, {root_name: parsed})`.

    The second half is the non-vacuity evidence and is deliberately counted
    *here*, per root, rather than re-derived by the test with its own
    `rglob`. A test that measures its own walk proves nothing about this
    function: add an exclusion in the loop below (`if "migrations" in
    path.parts: continue`) and a self-measuring test stays green while the
    floor quietly stops covering that subtree. This number is what was
    actually parsed.

    Cached — both tests below want the same scan, and a fresh `ast.parse` of
    the whole tree per test would be pure waste."""
    results = {}
    parsed = {}
    for root in SCAN_ROOTS:
        count = 0
        for path in sorted(root.rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            count += 1
            violations = find_violations(source)
            if violations:
                results[path.relative_to(REPO_ROOT).as_posix()] = violations
        parsed[root.name] = count
    return results, parsed


# ---------------------------------------------------------------------------
# The floor, against the real tree.
# ---------------------------------------------------------------------------


def test_no_unmasked_pii_log_interpolations_anywhere():
    """The floor, and since PRO-195 the whole contract: not one `logger.*`
    f-string slot under `app/` or `admin_panel/` may render an unmasked chat id
    or a structural reference to an address, a name or somebody else's number.

    There is no allowlist to add a file to. That is the point — the escape
    hatch is what let 38 lines sit for two days after the ticket that named
    them, and this repo already runs `black --check` and `flake8 --count` at
    zero with no per-file exemptions."""
    scanned, _ = _scan_repo()
    found = {path: [f"{ln}: {expr}" for ln, expr in v] for path, v in scanned.items()}
    assert not found, (
        f"Unmasked chat_id/address/name interpolation(s) in logger calls: {found}. "
        "Mask the chat id with `mask_chat_id()` from `app/core/phone.py`, or drop "
        "the address/name field from the log line — city is fine, the street/name "
        "is not."
    )


#: Both trees, by name, and a floor on each. Checked per root rather than in
#: total: `app/` alone holds 58 modules, so a total-only bound of 50 passes
#: happily with `admin_panel/` — 21 modules, and the tree the guard was
#: extended to cover on purpose — dropped from `SCAN_ROOTS` entirely. The
#: first cut of this test had exactly that hole, and it was demonstrated
#: rather than argued: deleting `admin_panel` from `SCAN_ROOTS` left the file
#: at 11 passed.
_EXPECTED_ROOTS = {"app": 15, "admin_panel": 15}


def test_the_repo_scan_is_not_vacuous():
    """An unconditional 'zero violations' assertion has one silent failure
    mode: a scan that walks less than it claims passes it.

    While `KNOWN_VIOLATIONS` existed, that was covered incidentally — a scan
    returning nothing made every recorded count look stale and went red. With
    the allowlist gone the cover goes with it, so the scan's reach is asserted
    directly, in the two ways it can shrink: a root can disappear, or a root
    can stop being walked.

    The per-root counts come from `_scan_repo` itself, not from a second
    `rglob` here — see its docstring for why a self-measuring test proves
    nothing about the function it is guarding."""
    assert {root.name for root in SCAN_ROOTS} == set(_EXPECTED_ROOTS), (
        "SCAN_ROOTS no longer covers both trees — the floor above is only as "
        f"wide as this list: {[str(r) for r in SCAN_ROOTS]}"
    )
    for root in SCAN_ROOTS:
        assert (
            root.is_dir()
        ), f"SCAN_ROOTS names something that is not a directory: {root}"

    _, parsed = _scan_repo()
    for root in SCAN_ROOTS:
        # 58 and 21 modules at PRO-195. The bounds sit far below that on
        # purpose: they catch a tree that moved out from under the scan, and
        # are not meant to track the file count, which would make this a chore.
        minimum = _EXPECTED_ROOTS[root.name]
        assert parsed.get(root.name, 0) >= minimum, (
            f"`_scan_repo` parsed only {parsed.get(root.name, 0)} modules under "
            f"{root.name}/ (expected at least {minimum}) — the floor is passing "
            "because it looked at almost nothing."
        )

        # And it parsed *everything* there, which a floor cannot express: an
        # exclusion added inside `_scan_repo`'s loop (`if "migrations" in
        # path.parts: continue`) leaves a subtree uncovered while every bound
        # above still passes — `app/services/` alone is ~20 modules, well
        # inside the slack of any bound loose enough not to be a chore. This
        # walk is the independent reference the equality is taken against;
        # that is what makes measuring it here sound rather than circular.
        on_disk = len(list(root.rglob("*.py")))
        assert parsed.get(root.name, 0) == on_disk, (
            f"`_scan_repo` parsed {parsed.get(root.name, 0)} of the {on_disk} "
            f"modules under {root.name}/ — something in it is skipping files, "
            "so the floor does not cover the tree it claims to."
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


def test_detector_flags_a_record_name_and_not_a_things_name():
    """`name` is the one field where the access shape carries the meaning, so
    the rule is narrowed to the record form rather than dropped.

    Flagging bare `name` too would have cost 16 false positives against 1 real
    hit — a provider's name, a city's name from the geocoder, an enum member's
    `.name` — and a guard that is wrong sixteen times out of seventeen is a
    guard somebody turns off."""
    person = textwrap.dedent(
        """
        def handler():
            logger.info(f"New pending pro created: {result.inserted_id} ({data.get('name')})")
        """
    )
    assert len(find_violations(person)) == 1

    also_person = textwrap.dedent(
        """
        def handler():
            logger.info(f"pro {pro['name']} approved")
        """
    )
    assert len(find_violations(also_person)) == 1

    not_a_person = textwrap.dedent(
        """
        def handler():
            logger.info(f"WhatsApp egress using provider '{provider.name}'")
            logger.info(f"Geocoded {name} to {lat},{lon}")
            logger.info(f"File is {file_status.state.name}")
        """
    )
    assert find_violations(not_a_person) == []


def test_detector_flags_the_inbound_location_payload():
    """The sink filters cannot reach this one, which is why it is in the field
    list rather than left to them: `_HOUSE_NUMBER` refuses digit runs
    containing `.`, so a lat/long pair survives every scrubber, and
    `_ADDRESS_PATTERN` is Hebrew-shaped, so a Latin-script street does too."""
    for src in (
        'logger.info(f"Location message from {chat} ({user_text})")',
        'logger.info(f"at {latitude}, {longitude}")',
    ):
        assert find_violations(src), f"not flagged: {src}"

    assert (
        find_violations('logger.info(f"Location message ({len(user_text)} chars)")')
        == []
    )


if __name__ == "__main__":  # pragma: no cover
    import sys

    sys.exit(pytest.main([__file__, "-q"]))
