"""Tests for the copy catalog itself (`app/core/messages.py`) — invariants that
span multiple `Messages.*` constants and so don't belong to any one service's
test file.
"""

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
