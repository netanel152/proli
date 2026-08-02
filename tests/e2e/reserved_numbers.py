"""Reserved, provably-unreachable phone numbers for the offline E2E harness (PRO-83).

An Israeli mobile number in international form is ``972`` followed by the national
number with the trunk ``0`` stripped, so the digit immediately after ``972`` is one
of 2,3,4,5,7,8,9 — never ``0``. Every number here has the shape ``9720________``,
which therefore cannot correspond to a real WhatsApp subscriber under any
allocation. That is a structural property of the numbering plan, not a naming
convention someone has to remember to respect.

Why this module exists at all: PRO-72's root cause was a QA script whose docstring
asserted a number was "virtual" when it was a real handset, and every simulated
inbound webhook produced a genuine outbound send to it. The harness must be
structurally incapable of repeating that, so the numbers it may use are enumerated
here and ``tests/e2e/test_e2e_safety.py`` scans the whole harness source tree for
the two numbers implicated in (or capable of repeating) that incident.
"""

RESERVED_PREFIX = "9720"

# --- The cast ---------------------------------------------------------------
CUSTOMER = "972000000001"
CUSTOMER_B = "972000000005"
PRO_PRIMARY = "972000000002"
PRO_SECONDARY = "972000000003"
PRO_THIRD = "972000000006"
PRO_FAR = "972000000004"  # seeded ~60 km out — never inside the 30 km max radius
PRO_UNAPPROVED = "972000000007"
ADMIN = "972000000009"
ONCALL = "972000000008"

ALL_RESERVED = (
    CUSTOMER,
    CUSTOMER_B,
    PRO_PRIMARY,
    PRO_SECONDARY,
    PRO_THIRD,
    PRO_FAR,
    PRO_UNAPPROVED,
    ADMIN,
    ONCALL,
)

# Numbers that must never appear anywhere under tests/e2e/:
#   * ...3651414 — the real handset PRO-72 was spamming.
#   * ...4828796 — the hardcoded ADMIN_PHONE default in app/core/config.py, which
#     becomes a recipient for every SOS / escalation alert unless overridden.
#
# Stored split so this module contains no contiguous copy of either number.
# The guard test scans *every* file under tests/e2e/ with no exclusions, and a
# denylist that spelled its own entries out would be the one file that had to be
# exempted — which is exactly the hole worth not having.
_FORBIDDEN_PARTS = (("97252", "3651414"), ("97252", "4828796"))
FORBIDDEN_NUMBERS = tuple(head + tail for head, tail in _FORBIDDEN_PARTS)


def chat(number: str) -> str:
    """Bare reserved number → WhatsApp chat id."""
    return f"{number}@c.us"


def is_reserved(number: str) -> bool:
    """True when a number is in the structurally-unreachable test range.

    Accepts a bare number or a ``@c.us`` chat id.
    """
    return str(number or "").replace("@c.us", "").startswith(RESERVED_PREFIX)
