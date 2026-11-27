"""PRO-245 — the owner's 2026-11-17 copy rules, pinned over the whole catalog.

`docs/COPY_STYLE_GUIDE.md` §1-§5: פרולי speaks as "אנחנו", with no em dash, no
emoji, no `*bold*` and no slashed gender form. `tests/test_template_bodies.py`
already holds `Messages.Templates` to these rules; this holds every other
user-facing string a customer, pro or operator reads, free-form copy included,
so a retired pattern fails here instead of reaching a phone.

`Keywords` is input vocabulary, not copy, and is skipped. So is `AISystemPrompts`,
which is model-facing English; the AI prompts are pinned by
`tests/test_prompt_copy_style.py`.
"""

import re

import pytest

from app.core.messages import Messages
from tests.copy_util import (
    command_row_keyword,
    unread_after_write_verb,
    written_keywords,
)

COPY_CLASSES = (
    "Customer",
    "Pro",
    "Admin",
    "SOS",
    "Alerts",
    "Consent",
    "Onboarding",
    "System",
    "Errors",
    "Fallbacks",
    "Templates",
)

# Input vocabulary that happens to live beside the copy.
_NOT_COPY = {"Consent.ACCEPT_KEYWORDS", "Consent.DECLINE_KEYWORDS"}

# The one bold the guide allows: the emergency numbers in the owner-approved
# life-threatening message (PRO-222), and its headline.
BOLD_ALLOWED = {"Customer.LIFE_THREATENING"}

EM_DASH = "—"
EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"  # pictographs, emoticons, transport, symbols
    "⌀-⏿"  # ⏰ ⏳ ⏸ and friends
    "☀-➿"  # ☀ ☕ ✅ ❌ ⚠ ✔
    "⬀-⯿"  # ⭐
    "️"  # emoji variation selector
    "]"
)
SLASHED_GENDER_RE = re.compile(r"[א-ת]/[א-ת]")
BOLD_RE = re.compile(r"\*[^*\n]+\*")
# §1: first person singular. "אני" alone, and the verb forms the catalog used
# before PRO-245. Keywords such as סיימתי and חזרתי are what the pro types, so
# they are not on this list.
FIRST_PERSON_RE = re.compile(
    r"(?<!\w)(אני|הבנתי|רשמתי|מצאתי|הצלחתי|העברתי|פניתי|ביטלתי|דילגתי|"
    r"התייחסתי|אעדכן|אחפש|אעביר|אדאג|אטפל|אזכיר|אטריד|מעביר|מקצר)(?!\w)"
)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _strings(v)
    elif isinstance(value, (list, tuple)):
        for v in value:
            yield from _strings(v)


def _catalog():
    """(qualified name, string) for every user-facing string in the catalog."""
    out = []

    def walk(cls, prefix):
        for name, value in vars(cls).items():
            if name.startswith("__"):
                continue
            qualified = f"{prefix}.{name}"
            if isinstance(value, type):
                walk(value, qualified)
            elif qualified not in _NOT_COPY:
                out.extend((qualified, s) for s in _strings(value))

    for cls_name in COPY_CLASSES:
        walk(getattr(Messages, cls_name), cls_name)
    return out


CATALOG = _catalog()


def test_the_catalog_walk_finds_the_copy():
    # A guard against the walk matching nothing and every check turning vacuous.
    names = {name for name, _ in CATALOG}
    assert len(CATALOG) > 300
    assert "Customer.PRO_FOUND" in names
    assert "Pro.HELP_MENU" in names
    assert "Templates.Buttons.APPROVE" in names


def _offenders(predicate):
    return sorted({name for name, text in CATALOG if predicate(name, text)})


def test_no_em_dash_in_user_facing_copy():
    assert not _offenders(
        lambda n, t: EM_DASH in t
    ), "§1: no em dash in user-facing copy, a comma or a full stop does the job"


def test_no_emoji_in_user_facing_copy():
    found = _offenders(lambda n, t: EMOJI_RE.search(t))
    assert not found, f"§4: no emoji, at the head of a message or anywhere: {found}"


def test_no_slashed_gender_form_in_user_facing_copy():
    found = _offenders(lambda n, t: SLASHED_GENDER_RE.search(t))
    assert not found, f"§2: rephrase instead of a slashed form (את/ה): {found}"


def test_no_bold_outside_the_allowlist():
    found = _offenders(lambda n, t: n not in BOLD_ALLOWED and BOLD_RE.search(t))
    assert not found, f"§5: no *bold*, labels are plain `label: value`: {found}"


def test_the_bold_allowlist_is_still_needed():
    # An entry that no longer carries bold is a stale exception: drop it.
    for name in BOLD_ALLOWED:
        texts = [t for n, t in CATALOG if n == name]
        assert texts and any(BOLD_RE.search(t) for t in texts), name


def test_voice_is_we_not_i():
    found = {
        name: sorted(set(FIRST_PERSON_RE.findall(text)))
        for name, text in CATALOG
        if FIRST_PERSON_RE.search(text) and not name.startswith("Templates.PARAM")
    }
    assert not found, f'§1: פרולי speaks as "אנחנו", never "אני": {found}'


def test_life_threatening_still_names_all_three_emergency_numbers():
    # PRO-245 restyled the message and must not have softened it.
    text = Messages.Customer.LIFE_THREATENING
    for number in ("*102*", "*101*", "*100*"):
        assert number in text
    assert "להתרחק מהמקום" in text and "להתקשר עכשיו" in text


# --- §7: every keyword a message asks for has a handler ---------------------

_VOCABULARY = (
    {
        word
        for name, value in vars(Messages.Keywords).items()
        if not name.startswith("_")
        for word in _strings(value)
    }
    | set(Messages.Consent.ACCEPT_KEYWORDS)
    | set(Messages.Consent.DECLINE_KEYWORDS)
    # The admin wizard's entry word is matched as a literal in
    # `admin_flow.handle_admin_message` and `dispatch_guards`, not through a list.
    | {"ניהול"}
)

# Free text that may follow "כתוב"/"לכתוב" without being a command.
_FREE_TEXT_AFTER_WRITE = {"לנו", "כאן", "אותן", "עיר", "מה", "את"}


@pytest.mark.parametrize("name,text", CATALOG, ids=[n for n, _ in CATALOG])
def test_every_word_asked_for_is_a_keyword(name, text):
    for word in written_keywords(text, _VOCABULARY):
        assert word in _VOCABULARY
    unread = set(unread_after_write_verb(text, _VOCABULARY)) - _FREE_TEXT_AFTER_WRITE
    assert not unread, (
        f"{name} asks the reader to write {sorted(unread)}, which no "
        "Messages.Keywords list matches (§7)"
    )


def test_pro_command_rows_start_with_their_keyword():
    rows = [n for n in vars(Messages.Pro) if n.startswith("CMD_")]
    assert rows
    for name in rows:
        keyword = command_row_keyword(getattr(Messages.Pro, name))
        assert keyword in _VOCABULARY, f"Messages.Pro.{name}"
