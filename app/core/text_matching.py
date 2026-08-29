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


# Hebrew clitics that attach directly to a noun with no space: ו־ ה־ ב־ ל־ מ־
# ש־ כ־. Up to four stack ("וכשההצפה" = ו+כ+ש+ה+הצפה), so a token is retried
# with 0-4 leading clitics removed. Every stem is at least four letters, so
# stripping that many cannot expose an unrelated short word.
_HEBREW_CLITICS = "ובהלמשכ"
_MAX_CLITICS = 4


def contains_keyword_prefixed(text, keywords, exclude_phrases=()) -> bool:
    """Like :func:`contains_keyword`, but a token also matches a keyword when it
    carries leading Hebrew clitics — ``"מהצפה"`` matches ``"הצפה"`` (PRO-121).

    Whole-token matching is the right default (PRO-118), but Hebrew glues its
    conjunction, article and prepositions onto the following word with no space,
    so ``"ההצפה"`` and ``"מהשריפה"`` are *not* the token ``"הצפה"``/``"שריפה"``.
    Substring matching used to catch them by accident; for a safety detector,
    losing them is the worse trade. Only single-word keywords are prefixable —
    a multi-word keyword falls back to plain whole-token matching.
    """
    if not text:
        return False
    haystack = f" {_tokens(text)} "
    for phrase in exclude_phrases:
        phrase_tokens = _tokens(phrase)
        if phrase_tokens:
            padded = f" {phrase_tokens} "
            while padded in haystack:
                haystack = haystack.replace(padded, " ")
    stems = set()
    multiword = []
    for kw in keywords:
        kw_tokens = _tokens(kw)
        if not kw_tokens:
            continue
        if " " in kw_tokens:
            multiword.append(kw_tokens)
        else:
            stems.add(kw_tokens)
    for kw_tokens in multiword:
        if f" {kw_tokens} " in haystack:
            return True
    for token in haystack.split():
        for cut in range(_MAX_CLITICS + 1):
            if cut and (len(token) <= cut or token[cut - 1] not in _HEBREW_CLITICS):
                break
            if token[cut:] in stems:
                return True
    return False


def is_emergency_text(text) -> bool:
    """True when ``text`` declares an emergency (PRO-121).

    The single detector shared by ``workflow_service``'s dispatch hoist and
    ``customer_flow``'s rating interceptor, so the two can never disagree about
    what counts as an emergency. Exact keywords and clitic-prefixable stems are
    two separate lists on purpose — see the comments beside them in
    ``Messages.Keywords``.
    """
    from app.core.messages import Messages

    keywords = Messages.Keywords
    excludes = keywords.EMERGENCY_EXCLUDE_PHRASES
    return contains_keyword(
        text, keywords.EMERGENCY_KEYWORDS, excludes
    ) or contains_keyword_prefixed(text, keywords.EMERGENCY_PREFIXABLE, excludes)
