"""The last pass over a model reply before a customer reads it (PRO-292).

The prompts (`app/core/prompts.py`) state the copy rules of
`docs/COPY_STYLE_GUIDE.md`, and PRO-245 rewrote them, but a prompt only asks.
The first staging chat showed what the model still writes:

* the customer's name twice in a row ("נעים להכיר, נתקסנתקס"),
* emoji ("👋 שלום", "🙏 מעולה"),
* an em dash ("היום ב-18:00 — תיקון נזילה"),
* פרולי speaking as the professional who arrives ("ומתי נוח שאגיע?").

None of these needs judgement to fix, so they are fixed here, in code, on every
reply the pipeline sends: the dispatcher's and the pro phase's. The rules are
deliberately narrow, so a correct reply passes through unchanged.

`copy_violations` names what a text still breaks; the tests use it to scan the
recorded model replies.
"""

from __future__ import annotations

import re

from app.core.logger import logger
from app.core.phone import mask_chat_id

# Pictographs, dingbats and the emoji modifiers: variation selector 16, the
# zero-width joiner and the keycap combiner. The ranges match
# `tests/test_copy_rules.py`, which pins the catalog; ₪ (U+20AA) is outside them.
EMOJI_RE = re.compile("[" "\U0001f000-\U0001faff" "⌀-⏿" "☀-➿" "⬀-⯿" "️" "‍" "⃣" "]")
# The modifiers alone: removed outright, where a pictograph becomes a space so
# "תודה🙏רבה" does not come out as one word.
_EMOJI_JOINERS_RE = re.compile("[\ufe0f\u200d\u20e3]")

DASH_RE = re.compile("[–—]")

# פרולי never arrives; the professional does. "אני אגיע", "שאגיע", "ואבוא",
# "אני לא אגיע", "וכשאגיע"... Group 1 is a joined prefix (ו, ש, כש, וכש),
# group 2 an adverb that stays with the verb, group 3 the verb.
_ARRIVAL_RE = re.compile(
    r"(?<!\w)(ו?כש|ו|ש)?(?:אני\s+)?(?:(לא|כבר|מיד|גם|תמיד)\s+)?(אגיע|אבוא)(?!\w)"
)
_THIRD_PERSON = {"אגיע": "יגיע", "אבוא": "יבוא"}
_PROFESSIONAL = "איש המקצוע"


def _strip_emoji(text: str) -> str:
    return EMOJI_RE.sub(" ", _EMOJI_JOINERS_RE.sub("", text))


_RANGE_RE = re.compile(
    r"(\d[\d:.,]*)[ \t]*[–—][ \t]*(\d[\d:.,]*)(?=([ \t]*₪)?)", flags=re.M
)


def _range(match: re.Match) -> str:
    """'A-B' when the two numbers read as one range, else the match unchanged."""
    first, second, shekel = match.group(1), match.group(2), match.group(3)
    both_times = ":" in first and ":" in second
    neither_time = ":" not in first and ":" not in second
    rest = match.string[match.end() :]
    ends_here = not rest.strip() or rest.lstrip(" \t")[:1] in "\n.,;!?)"
    if both_times or (neither_time and (shekel or ends_here)):
        return f"{first}-{second}"
    return match.group(0)


def _replace_dashes(text: str) -> str:
    # A range between two numbers keeps a plain hyphen: "300–450 ₪" → "300-450 ₪",
    # "10:00–12:00" → "10:00-12:00". "18:00 — 2 חדרים" is not a range.
    text = _RANGE_RE.sub(_range, text)
    # A dash opening or closing a line carries nothing a comma would.
    text = re.sub(r"^[ \t]*[–—][ \t]*", "", text, flags=re.M)
    text = re.sub(r"[ \t]*[–—][ \t]*$", "", text, flags=re.M)
    # After punctuation the dash is redundant; elsewhere it becomes a comma.
    text = re.sub(r"([.,:;!?])[ \t]*[–—][ \t]*", r"\1 ", text)
    return re.sub(r"[ \t]*[–—][ \t]*", ", ", text)


def _third_person_arrival(text: str) -> str:
    def _sub(match: re.Match) -> str:
        prefix, adverb, verb = match.group(1) or "", match.group(2), match.group(3)
        middle = f" {adverb}" if adverb else ""
        return f"{prefix}{_PROFESSIONAL}{middle} {_THIRD_PERSON[verb]}"

    return _ARRIVAL_RE.sub(_sub, text)


def _collapse_repeated_name(text: str, name: str) -> str:
    """'נתקסנתקס' or 'דנה, דנה' → the name once. Whole words only."""
    name = (name or "").strip()
    if len(name) < 2:
        return text
    n = re.escape(name)
    # "דנה, דנה", "שי שי": a separated repeat, whatever the name's length.
    text = re.sub(rf"(?<!\w){n}(?:[\s,]+{n})+(?!\w)", name, text)
    # "נתקסנתקס": glued only from three letters up, since a short name doubled
    # is often a real word ("שי" → "שישי", "גל" → "גלגל").
    if len(name) >= 3:
        text = re.sub(rf"(?<!\w){n}(?:{n})+(?!\w)", name, text)
    return text


def _tidy(text: str) -> str:
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"\n{3,}", "\n\n", text)
    # A removed emoji or dash can leave ", ," or a space before punctuation.
    text = re.sub(r",[ \t]*,", ",", text)
    text = re.sub(r"[ \t]+([.,:;!?])", r"\1", text)
    return text.strip()


def clean_customer_reply(
    text: str, *customer_names: str | None, chat_id: str | None = None
) -> str:
    """The reply the customer gets: `text` with the four fixes above applied.

    `customer_names` are the names the conversation knows (this turn's
    extraction, the stored lead); each is collapsed where it repeats.
    `chat_id` only ties the log line to a conversation, masked.
    """
    if not isinstance(text, str):
        return text
    fixed = []
    cleaned = text
    for label, step in (
        ("emoji", _strip_emoji),
        ("dash", _replace_dashes),
        ("first_person_arrival", _third_person_arrival),
    ):
        after = step(cleaned)
        if after != cleaned:
            fixed.append(label)
        cleaned = after
    for name in dict.fromkeys(n for n in customer_names if isinstance(n, str) and n):
        after = _collapse_repeated_name(cleaned, name)
        if after != cleaned:
            fixed.append("repeated_name")
        cleaned = after
    cleaned = _tidy(cleaned) if fixed else cleaned
    if fixed:
        # Which rules, never the text: the reply carries the customer's name
        # and address. A rising count says the model has drifted from the prompt.
        where = f" for {mask_chat_id(chat_id)}" if chat_id else ""
        logger.info(
            f"✂️ Model reply cleaned before sending{where}: {sorted(set(fixed))}"
        )
    return cleaned


def copy_violations(text: str) -> list[str]:
    """Which of the rules above `text` still breaks (the name aside)."""
    found = []
    if EMOJI_RE.search(text or ""):
        found.append("emoji")
    if DASH_RE.search(text or ""):
        found.append("dash")
    if _ARRIVAL_RE.search(text or ""):
        found.append("first_person_arrival")
    return found
