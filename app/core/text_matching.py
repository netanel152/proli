"""Whole-token keyword matching (PRO-118).

Substring matching (``kw in text``) tripped destructive flows on innocent
words: ``"טעות" ⊂ "בטעות"`` cancelled a confirmed BOOKED job, and any
``"admin"``/``"מנהל"`` fragment paused the bot for 15 minutes. This module
matches a keyword only when it appears as a complete word — or, for
multi-word keywords like ``"לא משנה"``, as a complete word sequence.
"""

import re

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokens(text: str) -> str:
    """Lowercase ``text`` and collapse it to its space-joined word tokens."""
    return " ".join(_TOKEN_RE.findall(text.lower()))


def contains_keyword(text, keywords, exclude_phrases=()) -> bool:
    """True iff any keyword appears in ``text`` as a whole token / phrase.

    ``exclude_phrases`` are known-innocent word sequences removed before
    matching — e.g. ``"מנהל עבודה"`` (a construction foreman, a profession a
    customer plausibly mentions) must not trip the SOS keyword ``"מנהל"``
    even though it contains it as a whole token. Both sides are compared
    space-padded so every match is token-aligned, never mid-word.
    """
    if not text:
        return False
    haystack = f" {_tokens(text)} "
    for phrase in exclude_phrases:
        phrase_tokens = _tokens(phrase)
        if phrase_tokens:
            padded = f" {phrase_tokens} "
            # Loop until stable: one replace consumes the trailing space, so
            # back-to-back repetitions of the phrase need another pass.
            while padded in haystack:
                haystack = haystack.replace(padded, " ")
    for kw in keywords:
        kw_tokens = _tokens(kw)
        if kw_tokens and f" {kw_tokens} " in haystack:
            return True
    return False
