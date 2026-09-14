"""Corrections to Streamlit chrome that is hard-coded left-to-right.

This is the sibling of `responsive.py`, and the split is the same idea: a
coherent concern, in one pure function, appended inside `load_css`'s single
`<style>` block, so it can be asserted on without a running panel.

The distinction from the direction-aware rules already in `components.py` is
worth keeping straight, because it decides where a fix belongs:

* `components.py` styles **our own** markup and Streamlit's, threading
  `direction` / `align` through. Those rules are already mirrored, and a bug
  in one is fixed in place.
* This module overrides **Streamlit's and BaseWeb's own** stylesheets, where a
  side is baked in and no amount of `direction: rtl` moves it: an element
  positioned with a literal `left`, a thumb whose offset BaseWeb computes in
  JavaScript from the wrong edge.

So in LTR there is, by construction, nothing here to correct, and
`rtl_css("ltr", "left")` emits no declarations at all. That is not an
oversight — it is the claim this module makes, and
`tests/test_admin_rtl.py` pins it.

**Every selector below is a Streamlit 1.31.1 internal** (`requirements.txt`),
and each is listed here so a Streamlit upgrade has one place to re-check:

* ``[data-testid="collapsedControl"]`` — the hamburger shown when the sidebar
  is collapsed. Streamlit positions it ``position: fixed; left: …``.
* ``[data-testid="stToolbar"]`` — Streamlit's own header menu (Rerun,
  Settings, About). It is pinned to the top *right*, which is where the
  hamburger has to go in RTL: on a 375px screen the two overlapped exactly.
  Mirroring both is the whole fix — in LTR the hamburger is left and the
  toolbar right, so in RTL they simply swap.
* ``[data-testid="stSidebarContent"] > div:has(> [data-testid="baseButton-header"])``
  — the absolutely positioned wrapper holding the sidebar's own collapse
  button. Streamlit sets both ``left`` and ``right`` on it, pinning the button
  to the sidebar's right edge — the inner edge in LTR, the screen edge in RTL.
* ``[data-testid="stSlider"]`` — BaseWeb computes the thumb's offset from the
  track's left edge in JavaScript and writes it to ``left``. Under
  ``direction: rtl`` the track is mirrored but that number is not, so the
  thumb lands outside the track entirely (measured ~400px past the end of a
  1050px slider). The whole widget becomes an LTR island, the same treatment
  and for the same class of reason as the Glide data grid in `components.py`.

`:has()` is used once, for the collapse-button wrapper, whose only other
handle is a hashed emotion class that changes between Streamlit builds. The
base sheet already relies on `:has(input:checked)` for the nav highlight, so
this adds no new browser requirement.
"""


def rtl_css(direction, align):
    """Return the RTL-correction block for `load_css`, already interpolated.

    `direction` / `align` come from the language dict exactly as they do in
    `load_css` and `responsive_css`.
    """
    if direction != "rtl":
        # Not a stub: everything below corrects a side that Streamlit hard-codes
        # to the left, which is the correct side in LTR. See the module docstring.
        return """
        /* =========================================================
           RTL CORRECTIONS — admin_panel/ui/rtl.py
           Nothing to correct: this language reads left-to-right, which
           is the direction Streamlit's own chrome is built for.
           ========================================================= */
"""

    return f"""
        /* =========================================================
           RTL CORRECTIONS — admin_panel/ui/rtl.py
           Overrides for Streamlit/BaseWeb chrome with a hard-coded side.
           ========================================================= */

        /* The hamburger that opens a collapsed sidebar. Streamlit pins it
           `position: fixed` at the viewport's left edge — so in Hebrew the
           sidebar slides in from the right while the only control that opens
           it sits in the opposite corner, on top of the page title. */
        [data-testid="collapsedControl"] {{
            left: auto !important;
            right: 0.5rem !important;
        }}

        /* Streamlit's header menu, pinned top-right like the hamburger now
           is. Measured on a 375px screen the two occupied the same 34px:
           the toolbar ran x=275..371 and the hamburger x=334..368, so the
           menu sat under the button that opens the sidebar. Its wrapper uses
           the same both-edges trick as the collapse button below, so the fix
           is the same: clear the far edge and let it shrink to fit. */
        [data-testid="stToolbar"] {{
            left: 0.25rem !important;
            right: auto !important;
        }}

        /* The sidebar's own collapse button. Its wrapper is absolutely
           positioned with both `left` and `right` set, which puts the button
           against the sidebar's right edge: the edge facing the content in
           LTR, and the edge facing the screen in RTL. Setting `right: auto`
           lets the box shrink-to-fit against `left`. */
        section[data-testid="stSidebar"]
            [data-testid="stSidebarContent"]
            > div:has(> [data-testid="baseButton-header"]) {{
            left: 0.25rem !important;
            right: auto !important;
        }}

        /* BaseWeb writes the slider thumb's offset into `left` from a track
           measurement it takes as if the page were LTR, so under
           `direction: rtl` the thumb renders off the end of its own track.
           An LTR island is the same answer `components.py` gives the Glide
           data grid: the control keeps working, and only its internal
           reading order stays left-to-right. The label above it is ours and
           stays {align}-aligned. */
        [data-testid="stSlider"] > div {{
            direction: ltr !important;
        }}
"""
