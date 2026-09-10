"""PRO-61 drift guard: every translation key a view reads must exist in both
languages, and every key defined in ``TRANS`` must be read by something.

This is what makes the bug class the ticket describes structurally unable to
come back: the old code read missing keys through ``T.get(key, "English
fallback")`` / ``T.get(x, x.capitalize())``, so a missing key never raised —
it silently rendered capitalised English on the Hebrew panel. A test that
only checks specific keys (as ``test_admin_kanban.py`` does for the Kanban
statuses) cannot catch the *next* missing key; this one walks every call site
under ``admin_panel/`` and pins the whole vocabulary at once.

Two languages are read through three call shapes: ``T.get("key"...)`` /
``T["key"]``, the login-page's separate ``T_auth`` / ``T_logout`` dicts (same
shapes), and the ``_t(T, "key", fallback, **subs)`` helper in
``views/home.py``. Keys built from a variable (``T.get(f"type_{ptype}")``,
``T[f"day_{x}"]``, ``T.get(f"role_{role}")``, ``lead_status_label``'s
``T.get(status)``) are deliberately not literal and are exempted by prefix /
by ``LeadStatus`` membership instead.
"""

import re
from pathlib import Path

from admin_panel.core.config import TRANS
from app.core.constants import LeadStatus
from app.core.messages import Messages

ADMIN_DIR = Path(__file__).resolve().parent.parent / "admin_panel"

# `\s*` already matches newlines without DOTALL (there is no `.` in the class
# that needs to cross lines), but re.S is harmless and documents the intent:
# several real call sites (views/analytics.py, views/home.py) wrap the key
# and its fallback across lines.
_KEY_PATTERN = re.compile(
    r'\bT(?:_[a-z]+)?(?:\.get\(|\[)\s*(["\'])([A-Za-z0-9_]+)\1', re.S
)
_HELPER_PATTERN = re.compile(r'\b_t\(\s*T,\s*(["\'])([A-Za-z0-9_]+)\1', re.S)

#: home.py resolves a skip reason to a T key through this table
#: (`T.get(_SKIP_REASON_KEYS.get(row.get("reason"), ""), ...)`), so the three
#: values never appear as a literal argument to T.get/T[] — they are read,
#: just not statically-literally. Recorded here rather than pattern-matched
#: because the indirection is a one-off, not a convention worth a third regex.
_INDIRECTLY_READ_KEYS = {
    "leads_skip_lead_gone",
    "leads_skip_unresolved",
    "leads_skip_no_change",
}


def _admin_py_files():
    return sorted(
        p
        for p in ADMIN_DIR.rglob("*.py")
        if not (p.name == "config.py" and p.parent.name == "core")
    )


def _collect_read_keys():
    """``{key: ["file:line", ...]}`` for every literal key read anywhere
    under admin_panel/ (outside core/config.py)."""
    found = {}
    for path in _admin_py_files():
        text = path.read_text(encoding="utf-8")
        for pattern in (_KEY_PATTERN, _HELPER_PATTERN):
            for m in pattern.finditer(text):
                key = m.group(2)
                line = text.count("\n", 0, m.start()) + 1
                found.setdefault(key, []).append(
                    f"{path.relative_to(ADMIN_DIR.parent)}:{line}"
                )
    for key in _INDIRECTLY_READ_KEYS:
        found.setdefault(key, []).append("indirect via home.py:_SKIP_REASON_KEYS")
    return found


def _is_dynamic_key(key):
    if key.startswith(("type_", "day_", "role_")):
        return True
    return key in {s.value for s in LeadStatus}


def test_every_read_key_exists_in_both_languages():
    read_keys = _collect_read_keys()
    he, en = TRANS["HE"], TRANS["EN"]

    missing = []
    for key, sites in sorted(read_keys.items()):
        if key not in he or key not in en:
            missing.append(f"{key!r} (read at {', '.join(sites)})")

    assert not missing, (
        "Keys read by admin_panel/ views but missing from TRANS — these used "
        "to render as a capitalised English fallback on the Hebrew panel:\n"
        + "\n".join(missing)
    )


def test_he_en_key_parity():
    he_only = set(TRANS["HE"]) - set(TRANS["EN"])
    en_only = set(TRANS["EN"]) - set(TRANS["HE"])
    assert not he_only, f"Keys only in HE: {sorted(he_only)}"
    assert not en_only, f"Keys only in EN: {sorted(en_only)}"


def test_every_lead_status_has_a_label_in_both_languages():
    he, en = TRANS["HE"], TRANS["EN"]
    for status in LeadStatus:
        assert status.value in he, f"LeadStatus {status.value!r} missing from TRANS[HE]"
        assert status.value in en, f"LeadStatus {status.value!r} missing from TRANS[EN]"


def test_every_profession_type_has_a_derived_label_matching_the_catalog():
    he, en = TRANS["HE"], TRANS["EN"]
    for code, name in Messages.Onboarding.TYPE_LABELS.items():
        key = f"type_{code}"
        assert key in he, f"{key!r} missing from TRANS[HE]"
        assert key in en, f"{key!r} missing from TRANS[EN]"
        # The HE entries are derived from the catalog at import — pin the
        # derivation, not a second copy of the Hebrew text.
        assert he[key] == name


def test_no_orphaned_keys_read_by_nothing():
    """Every key in TRANS should be read by something, or it's dead weight
    that will drift silently. Only the dynamically-built prefixes
    (`type_`/`day_`/`role_`) and the flat `LeadStatus` keys are exempt —
    everything else must be reachable through a literal `T.get`/`T[...]`
    call (or the recorded `_SKIP_REASON_KEYS` indirection) somewhere under
    admin_panel/.
    """
    read_keys = _collect_read_keys()
    he = TRANS["HE"]

    orphans = [key for key in he if key not in read_keys and not _is_dynamic_key(key)]
    assert not orphans, (
        "Keys defined in TRANS but read by nothing — delete them or read "
        f"them: {sorted(orphans)}"
    )


def test_no_capitalize_fallback_remains_outside_the_module_docstring():
    """PRO-61's whole point: `.capitalize()` fallbacks are gone. The only
    surviving mention is the historical explanation in labels.py's module
    docstring (`T.get(x, x.capitalize())`), which documents the *old* bug —
    excluded by skipping the leading triple-quoted block, not by excluding
    the whole file, so a real `.capitalize()` call added later to labels.py
    would still be caught.
    """
    hits = []
    for path in _admin_py_files():
        lines = path.read_text(encoding="utf-8").splitlines()
        in_leading_docstring = False
        docstring_seen = False
        for lineno, line in enumerate(lines, start=1):
            stripped = line.strip()
            if not docstring_seen and lineno == 1 and stripped.startswith('"""'):
                docstring_seen = True
                # single-line docstring: """...""" closes on the same line
                if stripped.count('"""') >= 2 and len(stripped) > 3:
                    continue
                in_leading_docstring = True
                continue
            if in_leading_docstring:
                if '"""' in line:
                    in_leading_docstring = False
                continue
            if stripped.startswith("#"):
                continue
            if ".capitalize()" in line:
                hits.append(
                    f"{path.relative_to(ADMIN_DIR.parent)}:{lineno}: {stripped}"
                )

    assert not hits, (
        "'.capitalize()' fallback found outside a module docstring — PRO-61 "
        "removed these on purpose:\n" + "\n".join(hits)
    )


def test_day_labels_follow_python_weekday_order():
    # views/schedule.py indexes T[f"day_{x}"] by `date.weekday()` (Monday=0).
    # Before PRO-61 these were Sunday-first, so every day label was off by
    # one against both readers.
    assert TRANS["EN"]["day_0"] == "Monday"
    assert TRANS["EN"]["day_6"] == "Sunday"
    assert TRANS["HE"]["day_6"] == "ראשון"
