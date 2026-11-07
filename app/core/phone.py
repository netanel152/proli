"""
Canonical phone-number ↔ WhatsApp chat_id conversion (PRO-49).

Historically these conversions were copy-pasted inline across ~13 files
(``f"{x}@c.us"`` suffixing, ``chat_id.replace("@c.us", "")`` stripping, and
``"0" + raw[3:]`` local formatting). An edge case fixed in one place but not the
others (a ``+972`` prefix, a number stored without a country code) caused
inconsistent chat_id resolution → undelivered messages. This module is the one
canonical place; fix conversion edge cases here.

Three operations:
  * ``to_chat_id(raw)``   → ``"972XXXXXXXXX@c.us"`` — for sending / keying.
  * ``strip_suffix(raw)`` → ``"972XXXXXXXXX"``      — bare intl digits, for DB lookups.
  * ``to_local_phone(raw)`` → ``"0XXXXXXXXX"``      — Israeli local display.
  * ``to_il_mobile(raw)``   → ``"9725XXXXXXXX"`` or ``""`` — a pro's stored phone
    (PRO-224); ``""`` when the input cannot be an Israeli mobile.

All are idempotent and None-safe (return ``""`` for falsy input).
"""

CHAT_SUFFIX = "@c.us"


def strip_suffix(raw) -> str:
    """Bare phone without the WhatsApp ``@c.us`` suffix.

    Behaviour-identical to the historical ``chat_id.replace("@c.us", "")`` for
    the values that flow through it (always a ``972...@c.us`` webhook chat_id),
    so DB ``phone_number`` lookups that match on this form are unchanged.
    """
    if not raw:
        return ""
    return str(raw).replace(CHAT_SUFFIX, "")


def _to_intl_digits(raw) -> str:
    """Normalize any accepted phone shape to bare international digits
    (``972XXXXXXXXX``): drops the ``@c.us`` suffix, a leading ``+``, separators,
    and converts an Israeli local leading ``0`` to the ``972`` country code."""
    s = strip_suffix(str(raw).strip())
    for ch in ("+", "-", " ", "(", ")"):
        s = s.replace(ch, "")
    if s.startswith("0"):
        s = "972" + s[1:]
    return s


def to_chat_id(raw) -> str:
    """Canonical WhatsApp chat id: ``<intl-digits>@c.us``. Idempotent.

    Handles an already-suffixed value, a leading ``+``, an Israeli local leading
    ``0`` (→ ``972``), and surrounding whitespace. For the values that already
    flowed (``972...`` or already-suffixed) the output is identical to the old
    ``f"{x}@c.us"`` / ``endswith`` guards; ``0...`` and ``+972...`` are now
    normalized instead of producing a broken id.
    """
    if not raw:
        return ""
    digits = _to_intl_digits(raw)
    return f"{digits}{CHAT_SUFFIX}" if digits else ""


def mask_chat_id(raw) -> str:
    """Last-4-digits display mask for logs and operator pages: ``...4567``.

    PRO-89 review finding: ``chat_id[-4:]`` on a ``@c.us``-suffixed id yields
    the literal string ``"c.us"`` — every recipient renders identically and
    the page tells the operator nothing. Strip the suffix first, here, once.
    """
    digits = strip_suffix(raw)
    return f"...{digits[-4:]}" if digits else "?"


def to_local_phone(raw) -> str:
    """Israeli local display form: ``0XXXXXXXXX``.

    Strips ``@c.us`` and ``+``, converts a ``972`` country code to a leading
    ``0``, and leaves an already-local number as-is. Matches the old
    ``"0" + raw[3:] if raw.startswith("972") else raw`` for clean inputs.
    """
    if not raw:
        return ""
    digits = _to_intl_digits(raw)
    if digits.startswith("972"):
        return "0" + digits[3:]
    return digits


def to_il_mobile(raw) -> str:
    """The bare ``9725XXXXXXXX`` form onboarding stores as a pro's
    ``phone_number``, or ``""`` when ``raw`` cannot be an Israeli mobile.

    Accepts every shape ``to_chat_id`` does, plus ``00972`` and ``+972 0…``
    (PRO-224: the admin panel stored whatever was typed, so the bot never
    found the pro).
    """
    if not raw:
        return ""
    s = str(raw).strip()
    if s.startswith("00"):
        s = s[2:]
    digits = _to_intl_digits(s)
    if digits.startswith("9720"):  # the trunk 0 typed after the country code
        digits = "972" + digits[4:]
    if digits.isdigit() and len(digits) == 12 and digits.startswith("9725"):
        return digits
    return ""
