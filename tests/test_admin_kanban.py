"""Tests for PRO-46: surfacing pending_admin_review on the admin Kanban board.

Covers:
  - pending_admin_review is a Kanban column
  - color entries exist and are well-formed (STATUS_COLORS / STATUS_COLORS_DARK)
  - no drift: every Kanban status has a color
  - no drift: every Kanban status has a label in both HE and EN translation dicts
  - render helpers use the localized label, not the capitalized fallback
"""

import admin_panel.views.home as home
from admin_panel.ui.components import (
    STATUS_COLORS,
    STATUS_COLORS_DARK,
    render_kanban_column,
    render_status_pill,
)
from admin_panel.core.config import TRANS


def _find_translation_dicts():
    """Locate the Hebrew (RTL) and English (LTR) translation dicts.

    Both dicts contain the key "metric_total"; they're distinguished by their
    "dir" key (rtl vs ltr).
    """
    he = None
    en = None
    for lang_dict in TRANS.values():
        assert "metric_total" in lang_dict
        if lang_dict.get("dir") == "rtl":
            he = lang_dict
        elif lang_dict.get("dir") == "ltr":
            en = lang_dict
    assert he is not None, "Could not locate Hebrew (rtl) translation dict"
    assert en is not None, "Could not locate English (ltr) translation dict"
    return he, en


def test_pending_admin_review_is_a_kanban_column():
    assert "pending_admin_review" in home.KANBAN_STATUSES


def test_pending_admin_review_color_entries_exist_and_well_formed():
    assert "pending_admin_review" in STATUS_COLORS
    assert "pending_admin_review" in STATUS_COLORS_DARK

    light = STATUS_COLORS["pending_admin_review"]
    for key in ("bg", "text", "border", "icon"):
        assert key in light, f"STATUS_COLORS['pending_admin_review'] missing '{key}'"
        assert light[key], f"STATUS_COLORS['pending_admin_review']['{key}'] is empty"


def test_every_kanban_status_has_a_color():
    for status in home.KANBAN_STATUSES:
        assert status in STATUS_COLORS, (
            f"Status '{status}' is on the Kanban board but has no entry in "
            "STATUS_COLORS - it will silently fall back to the 'new' palette."
        )


def test_every_kanban_status_has_a_label_in_both_languages():
    he, en = _find_translation_dicts()
    for status in home.KANBAN_STATUSES:
        assert status in he, (
            f"Status '{status}' has no Hebrew label - it will render via "
            "T.get(status, status.capitalize()) as an ugly capitalized fallback."
        )
        assert status in en, (
            f"Status '{status}' has no English label - it will render via "
            "T.get(status, status.capitalize()) as an ugly capitalized fallback."
        )


def test_render_kanban_column_uses_localized_label_not_capitalized_fallback():
    he, _ = _find_translation_dicts()
    html_out = render_kanban_column("pending_admin_review", [], he)

    assert he["pending_admin_review"] in html_out
    # Guard against the ugly default fallback ever leaking through. The real
    # fallback is `status.capitalize()` → "Pending_admin_review" (only the first
    # char is upper-cased), so that exact string is what must be absent.
    assert "pending_admin_review".capitalize() not in html_out


def test_render_status_pill_uses_localized_label_and_correct_css_class():
    he, _ = _find_translation_dicts()
    html_out = render_status_pill("pending_admin_review", he)

    assert he["pending_admin_review"] in html_out
    assert "status-pill-pending_admin_review" in html_out
    assert "pending_admin_review".capitalize() not in html_out


def test_pending_review_count_query_matches_stored_status():
    """The dashboard metric counts with `LeadStatus.PENDING_ADMIN_REVIEW`
    (`home.py`), while leads are stored with the plain string the app writes.
    Guard that the str-enum matches the wire value so the tile can't silently
    read 0 — the one real behaviour behind this presentation change.
    """
    import mongomock
    from app.core.constants import LeadStatus

    col = mongomock.MongoClient().db.leads
    col.insert_one({"status": "pending_admin_review"})
    col.insert_one({"status": "new"})

    assert col.count_documents({"status": LeadStatus.PENDING_ADMIN_REVIEW}) == 1
