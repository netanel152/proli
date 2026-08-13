"""Tests for the canonical phone helpers (PRO-49): to_chat_id, strip_suffix, to_local_phone."""

import pytest
from app.core.phone import mask_chat_id, to_chat_id, strip_suffix, to_local_phone


# --- to_chat_id ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("972501234567", "972501234567@c.us"),  # intl digits (the common case)
        ("972501234567@c.us", "972501234567@c.us"),  # already suffixed → idempotent
        ("0501234567", "972501234567@c.us"),  # Israeli local leading 0 → 972
        ("+972501234567", "972501234567@c.us"),  # leading + stripped
        ("+972-50-123-4567", "972501234567@c.us"),  # separators stripped
        ("  972501234567  ", "972501234567@c.us"),  # surrounding whitespace
    ],
)
def test_to_chat_id(raw, expected):
    assert to_chat_id(raw) == expected


def test_to_chat_id_is_idempotent():
    once = to_chat_id("0501234567")
    assert to_chat_id(once) == once


@pytest.mark.parametrize("bad", [None, "", 0])
def test_to_chat_id_falsy_returns_empty(bad):
    assert to_chat_id(bad) == ""


# --- strip_suffix ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("972501234567@c.us", "972501234567"),  # the historical .replace behaviour
        ("972501234567", "972501234567"),  # no suffix → unchanged
        ("admin@c.us", "admin"),
    ],
)
def test_strip_suffix(raw, expected):
    assert strip_suffix(raw) == expected


@pytest.mark.parametrize("bad", [None, ""])
def test_strip_suffix_falsy_returns_empty(bad):
    assert strip_suffix(bad) == ""


# --- to_local_phone ---


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("972501234567", "0501234567"),  # 972 → 0
        ("972501234567@c.us", "0501234567"),  # strips suffix first
        ("0501234567", "0501234567"),  # already local → unchanged
        ("+972501234567", "0501234567"),  # + stripped
    ],
)
def test_to_local_phone(raw, expected):
    assert to_local_phone(raw) == expected


@pytest.mark.parametrize("bad", [None, ""])
def test_to_local_phone_falsy_returns_empty(bad):
    assert to_local_phone(bad) == ""


def test_round_trip_chat_id_local():
    # chat_id → local → chat_id is stable
    chat = "972501234567@c.us"
    assert to_chat_id(to_local_phone(chat)) == chat


# --- mask_chat_id (PRO-89 review finding) ---


def test_mask_chat_id_strips_suffix_before_taking_last_four_digits():
    """The bug this guards: chat_id[-4:] on a `@c.us`-suffixed id yields the
    literal string 'c.us' for every recipient — the operator page told them
    nothing. Stripping the suffix first is the fix."""
    assert mask_chat_id("972501234567@c.us") == "...4567"


def test_mask_chat_id_on_bare_digits():
    assert mask_chat_id("972501234567") == "...4567"


@pytest.mark.parametrize("bad", [None, "", "@c.us"])
def test_mask_chat_id_falsy_or_suffix_only_returns_placeholder(bad):
    assert mask_chat_id(bad) == "?"
