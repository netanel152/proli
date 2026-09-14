"""Tests for admin_panel/core/labels.py — PRO-61.

`lead_status_label` / `profession_label` are the two places that used to
fall back to `x.capitalize()` on a miss. Pinned here: a miss now returns the
raw token (never capitalised) and `None` renders as "".

`test_admin_kanban.py` already covers the render helpers
(`render_status_pill` / `render_kanban_column`) that call into
`lead_status_label` — not repeated here.
"""

import pytest

from admin_panel.core.config import TRANS
from admin_panel.core.labels import (
    LEAD_STATUSES,
    PROFESSION_TYPES,
    lead_status_label,
    pro_option_label,
    pro_option_map,
    profession_label,
    status_by_label,
    status_label_map,
)
from app.core.constants import LeadStatus
from app.core.messages import Messages


@pytest.mark.parametrize("status", list(LeadStatus))
def test_lead_status_label_returns_the_he_label_for_every_status(status):
    assert lead_status_label(TRANS["HE"], status.value) == TRANS["HE"][status.value]


@pytest.mark.parametrize("bad_status", ["n/a", "some_legacy_value"])
def test_lead_status_label_unknown_status_returns_raw_token_not_capitalized(
    bad_status,
):
    # The bug this whole module exists to kill: the old call sites fell back
    # to `x.capitalize()`. Pin the honest raw value instead.
    assert lead_status_label(TRANS["HE"], bad_status) == bad_status
    assert lead_status_label(TRANS["HE"], bad_status) != bad_status.capitalize()


def test_lead_status_label_none_returns_empty_string():
    assert lead_status_label(TRANS["HE"], None) == ""


@pytest.mark.parametrize(
    "lang, expected",
    [("HE", "אינסטלטור"), ("EN", "Plumber")],
)
def test_profession_label_known_code(lang, expected):
    assert profession_label(TRANS[lang], "plumber") == expected


def test_profession_label_unknown_code_returns_raw_code():
    assert profession_label(TRANS["HE"], "astronaut") == "astronaut"


def test_profession_label_none_returns_empty_string():
    assert profession_label(TRANS["HE"], None) == ""


def test_status_by_label_is_exact_inverse_of_status_label_map_for_every_status():
    label_map = status_label_map(TRANS["HE"])
    inverse = status_by_label(TRANS["HE"])

    assert set(label_map) == set(LEAD_STATUSES)
    for status, label in label_map.items():
        assert inverse[label] == status
    assert len(inverse) == len(label_map)  # no two statuses collapsed onto one label


def test_profession_types_matches_catalog_keys_in_order():
    assert PROFESSION_TYPES == tuple(Messages.Onboarding.TYPE_LABELS)


# --- pro_option_label / pro_option_map -----------------------------------


@pytest.mark.parametrize("lang", ["HE", "EN"])
def test_pro_option_label_never_renders_an_empty_string(lang):
    """Self-onboarding writes ``business_name: ""``, and ``dict.get(k, default)``
    does not fire on an empty string — so every ``p.get("business_name",
    T["unnamed_pro"])`` in the panel rendered nothing at all."""
    T = TRANS[lang]
    assert pro_option_label(T, {"business_name": "אבי שרברב"}) == "אבי שרברב"
    for pro in ({"business_name": ""}, {"business_name": "  "}, {}):
        label = pro_option_label(T, {**pro, "phone_number": "972501234567"})
        assert label.startswith(T["unnamed_pro"]), label
        assert label.endswith("4567"), "the phone tail keeps two unnamed pros apart"
    assert pro_option_label(T, {}) == T["unnamed_pro"]


def test_pro_option_map_keeps_every_pro_reachable():
    """A name-keyed dict silently dropped every unnamed pro but one from the
    schedule selector, and would do the same to two pros sharing a name."""
    T = TRANS["HE"]
    pros = [
        {"_id": "a1", "business_name": "אבי שרברב"},
        {"_id": "b2", "business_name": "אבי שרברב"},
        {"_id": "c3", "business_name": "", "phone_number": "972500000001"},
        {"_id": "d4", "business_name": "", "phone_number": "972500000002"},
    ]
    out = pro_option_map(T, pros)
    assert len(out) == 4, out.keys()
    assert {p["_id"] for p in out.values()} == {"a1", "b2", "c3", "d4"}
    assert "אבי שרברב" in out and "אבי שרברב · b2" in out
