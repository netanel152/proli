---
paths:
  - "admin_panel/**"
  - "scripts/admin_panel_*.py"
  - "tests/test_admin_*.py"
---

# Streamlit admin panel (`admin_panel/`)

Loaded when a file under `admin_panel/` (or its preview/screenshot scripts and tests)
is read. View bodies are Streamlit and never run under pytest — only the injectable
query/label/refresh/assignment seams do — so `tests/test_admin_view_call_arity.py`
statically checks every call in `admin_panel/` against its target's signature. Hebrew
is the primary language: every string goes through `TRANS` / `labels.py`, never a
silent `.capitalize()` fallback (`tests/test_admin_translations.py`). Synchronous
WhatsApp sends use `app.providers.whatsapp.sync.send_text_sync`, never a provider.

### Process 3: Streamlit Admin Panel (`admin_panel/`)

Protected by bcrypt cookie-based auth. Views for lead management, professional profiles, and schedule management. Auto-refresh (PRO-141) is a client-side timer (`streamlit-autorefresh`, armed in the sidebar above the view dispatch) rather than the old `time.sleep(interval); st.rerun()` at the end of the script run, which blocked the page for up to 120s per tick; it pauses (and says so) while a `st.data_editor` holds unsaved rows, per `admin_panel/core/refresh.py`. Hebrew/English copy lives in the `TRANS` dict (`admin_panel/core/config.py`); lead-status and profession-type labels are resolved through the single `admin_panel/core/labels.py` (PRO-61) rather than at each call site, and `tests/test_admin_translations.py` guards HE/EN key parity and forbids the old silent `.capitalize()` fallback. `admin_panel/core/assignment.py` (PRO-188) is the same injectable-collaborator seam as `lead_queries`/`schedule_queries`/`analytics_queries`, crossing sync→async on the shared bridge loop to call `admin_flow.assign_lead_to_pro`; it backs the dashboard's one-click assignment strip for `PENDING_ADMIN_REVIEW` leads (`home.py`'s `_render_pending_review_strip`), cutting that assignment from six clicks through the generic lead-edit form down to three. Mobile/tablet layout (`docs/ADMIN_PANEL_RESPONSIVE_PLAN.md`, all six slices landed) lives entirely in `admin_panel/ui/responsive.py`, appended last inside `load_css`'s `<style>` — breakpoints `MOBILE_MAX=640`/`SIDEBAR_OVERLAY_MAX=768`/`TABLET_MAX=1024`, plus `render_metric_grid` and the `.kanban-board` CSS in `components.py`. Its sibling `admin_panel/ui/rtl.py` is the same shape for the other axis: `responsive.py` holds what changes with the *viewport*, `rtl.py` what changes with the *language* — and only where Streamlit's or BaseWeb's own sheet bakes a side in (the hamburger and toolbar, both pinned top-right; the sidebar's collapse button; the slider thumb, which BaseWeb positions from an LTR track measurement and drops off the end of its own track). `rtl_css("ltr", …)` emits nothing at all, so a rule that is not direction-specific cannot hide there; anything wrong in our *own* markup is fixed in place in `components.py` instead. Action rows that must stay horizontal carry `mark_row_inline()` immediately before their `st.columns` — Streamlit stacks *every* row below 640px and nothing in the DOM tells a Yes/No pair from a row of inputs, so the marker is the only handle; the CSS reaches the row with `+`, which is why the adjacency is pinned in `tests/test_admin_action_rows.py` rather than left to a reader. `mark_login_page()` is the same mechanism scoped to a page, capping `.block-container` — an unclosed `<div>` in one `st.markdown` wraps nothing. `scripts/admin_panel_layout_preview.py` renders `dashboard`, `widgets`, `forms` and `login` pages through real Streamlit with fake data (no Mongo, no password), and `scripts/admin_panel_screenshots.py` shoots them all at four widths × two languages × either colour scheme — the S6 defects were all in chrome the Dashboard never renders.
