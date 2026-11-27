"""PRO-167: derive assertion fragments from Messages catalog templates.

Tests must not retype product copy as literal Hebrew — a copy edit in
``app/core/messages.py`` (the PRO-168 rewrite in particular) would then fail
dozens of tests that never cared about wording, only about *which* message was
chosen. Instead, derive the fragment from the constant itself:

- exact message, no placeholders     -> assert ``Messages.X == sent`` /
  ``Messages.X in sent``
- known format values in the test    -> assert ``Messages.X.format(...) == sent``
- placeholders, meaningful prefix    -> assert ``static_prefix(Messages.X) in sent``
- placeholders mid-and-start         -> assert ``longest_static_chunk(Messages.X) in sent``

Both helpers raise rather than return a fragment too short to be meaningful,
so a template refactor can never silently turn an assertion vacuous.
"""

import re

_PLACEHOLDER = re.compile(r"\{[^{}]*\}")


def static_prefix(template: str, min_len: int = 4) -> str:
    """The template's literal text up to its first placeholder."""
    frag = _PLACEHOLDER.split(template)[0].strip()
    if len(frag) < min_len:
        raise ValueError(
            f"static_prefix too short ({frag!r}) — use longest_static_chunk or "
            f".format() with test values instead"
        )
    return frag


def longest_static_chunk(template: str, min_len: int = 4) -> str:
    """The longest literal run between placeholders."""
    frag = max((c.strip() for c in _PLACEHOLDER.split(template)), key=len)
    if len(frag) < min_len:
        raise ValueError(f"no meaningful static chunk in {template!r}")
    return frag


# PRO-245 §3: the two ways a free-form message asks for a keyword. A sentence
# asks with the verb ("לאישור כתוב אשר", "אפשר לכתוב ביטול"); a pro command
# list puts the keyword first on its line ("אשר - לקבל את העבודה"). A keyword
# may be two words ("עדיין עובד"), so the caller passes the vocabulary and the
# longest match wins.
_WRITE_VERB = re.compile(r"(?<![\w])ל?כתוב ")
_COMMAND_ROW = re.compile(r"^(\S+(?: \S+)?) - ", flags=re.M)


def written_keywords(text: str, vocabulary) -> list[str]:
    """Every keyword ``text`` asks to be written after "כתוב"/"לכתוב", in order.

    Only words in ``vocabulary`` count. What else may follow the verb ("לכתוב
    לנו", "לכתוב מה קרה") is free text, not a command, and is skipped here;
    ``unread_after_write_verb`` lists it so a test can pin what is allowed.
    """
    words = sorted(vocabulary, key=len, reverse=True)
    found = []
    for m in _WRITE_VERB.finditer(text):
        rest = text[m.end() :]
        for word in words:
            if rest.startswith(word) and not re.match(r"\w", rest[len(word) :]):
                found.append(word)
                break
    return found


def unread_after_write_verb(text: str, vocabulary) -> list[str]:
    """The first word after each "כתוב"/"לכתוב" that is not a keyword."""
    known = set(written_keywords(text, vocabulary))
    out = []
    for m in _WRITE_VERB.finditer(text):
        rest = text[m.end() :]
        if not any(
            rest.startswith(w) and not re.match(r"\w", rest[len(w) :]) for w in known
        ):
            out.append(re.split(r"[\s,.:!?]", rest, maxsplit=1)[0])
    return out


def command_row_keyword(row: str) -> str | None:
    """The keyword a pro command-list row (``"אשר - ..."``) starts with."""
    m = _COMMAND_ROW.match(row)
    return m.group(1) if m else None
