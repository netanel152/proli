"""Tests for the copy catalog itself (`app/core/messages.py`) — invariants that
span multiple `Messages.*` constants and so don't belong to any one service's
test file.
"""

import re

from app.core.messages import Messages


def test_rating_prompts_share_the_same_scale_line():
    """PRO-122/PRO-168: COMPLETION_ACK, RATE_SERVICE and RATING_REPROMPT are the
    three messages that open the `waiting_for_rating` window, and all three must
    advertise the exact same answer set — including the *דלג* skip token
    `Keywords.SKIP_TOKENS` already handles — so a customer who saw one prompt
    isn't surprised by what a different one accepts."""
    for prompt in (
        Messages.Customer.COMPLETION_ACK,
        Messages.Customer.RATE_SERVICE,
        Messages.Customer.RATING_REPROMPT,
    ):
        assert Messages.Customer.RATING_SCALE_LINE in prompt


# §2 of docs/COPY_STYLE_GUIDE.md: "Never the bare masculine form in
# customer-facing copy." Pro- and onboarding-facing copy is explicitly exempt
# ("may keep the direct imperative register it has today"), so this checks
# Messages.Customer only.
_BARE_MASCULINE = re.compile(
    r"(?<![\w/])(אתה|שמור|שלח|השב|נסה|בחר|כתוב|לחץ|תרצה|הזן|ודא)(?![\w/])"
)


def test_customer_copy_uses_no_bare_masculine_forms():
    """PRO-168 cleared the whole Customer namespace of bare masculine verbs and
    pronouns per style guide §2. Nothing enforced it, so PRO-121 immediately
    reintroduced three (``אנא שמור על בטיחות`` ×3, ``באיזו עיר אתה?``) simply by
    branching before the guide landed — and the full suite stayed green.

    The negative lookbehind/lookahead on ``/`` is what makes this useful rather
    than annoying: it matches the bare ``שמור`` but not the inclusive ``שמור/י``
    the guide asks for, so rule 2 (``השב/י``) passes and only rule 3 fails.
    """
    offenders = {}
    for name in dir(Messages.Customer):
        if name.startswith("_"):
            continue
        value = getattr(Messages.Customer, name)
        if not isinstance(value, str):
            continue
        found = sorted(set(_BARE_MASCULINE.findall(value)))
        if found:
            offenders[name] = found

    assert not offenders, (
        "Customer-facing copy must not use bare masculine forms (style guide "
        f"§2). Rephrase to an infinitive, or use the inclusive form: {offenders}"
    )
