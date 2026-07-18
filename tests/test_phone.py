"""Tests for the canonical phone helpers (PRO-49): to_chat_id, strip_suffix, to_local_phone."""

import pytest
from app.core.phone import to_chat_id, strip_suffix, to_local_phone


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
