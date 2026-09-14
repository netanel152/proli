# Admin panel — full mobile + desktop responsive layout (plan)

Status: **all six slices landed (S1, S2, S3, S4, S5, S6).** (S6 was not in the
original five — see the slice for why it had to exist.) This document is the
implementation plan behind the ticket "Admin panel: full mobile + desktop
responsive layout". It is the spec the implementing PR is measured against; delete or fold it
into `docs/ARCHITECTURE.md` once all slices have merged.

## Why

The Streamlit admin panel (`admin_panel/`) was built desktop-first and has **no
responsive rules at all**: `load_css` in `admin_panel/ui/components.py` is ~850 lines
of CSS and its only `@media` query is `prefers-color-scheme: dark`. The operator's
real usage is the opposite of that assumption — the pending-review queue (PRO-188)
and the SOS pages are things you act on from a phone, in the field, in Hebrew.

What actually breaks today at phone width (375 px), from reading the code:

| Symptom | Cause |
|---|---|
| Sidebar covers the whole screen on load, must be dismissed before any view is usable | `initial_sidebar_state="expanded"` (`admin_panel/main.py`) plus `width: 280px !important` on `section[data-testid="stSidebar"]`, which fights Streamlit's own mobile overlay sizing |
| Five full-width metric cards stacked before the board — a screen and a half of scrolling before the operator sees a lead | Streamlit stacks every `st.columns` cell to 100 % width below 640 px; the 5-wide metric rows in `views/home.py` (twice) and `views/analytics.py` become 5 tall cards with `padding: 20px 24px` and a 2 rem value |
| Kanban board is an 8-column, ~1,500 px wide horizontal scroller | `.kanban-column { flex: 1 0 180px; min-width: 180px }` inside an inline-styled `display:flex; overflow-x:auto` wrapper in `views/home.py` — there is no narrow-viewport layout, and empty columns still take their 180 px |
| Confirm buttons (Yes / No) become two stacked full-width buttons, destructive one on top | Every `[1, 1, 4]` confirm row (`views/professionals.py` ×2, `views/schedule.py`, the audit-log pagination in `views/settings.py`) stacks on mobile; the empty spacer column is hidden but the two buttons still stack |
| Six analytics tabs and the settings tabs clip / wrap | `.stTabs [data-baseweb="tab"]` has fixed padding and no `overflow-x` on the tab list |
| Login form floats in a narrow band with dead space either side | `st.columns([1, 2, 1])` in `admin_panel/core/auth.py` |
| Content edges touch the screen | `.block-container` padding is a fixed `2rem` on each side at every width |
| Inputs zoom the page on iOS when focused | Input font sizes are set below 16 px |

Nothing here is a Streamlit limitation. It is missing CSS plus a handful of layout
calls that assume a wide screen.

## Constraints that shape the approach

- **Streamlit is pinned to 1.31.1** (`requirements.txt`). No `st.html`, no
  `st.dialog`, no `key=` on `st.columns`/`st.container`, no `st.columns(...,
  vertical_alignment=)`. Everything below works on 1.31.1; the pin is not moved by
  this work.
- **Server-side code cannot see the viewport.** Streamlit has no window-width API
  without a JS component, and this plan adds **no new dependency**. Layout is
  therefore **CSS-first**: media queries do all the work, and where the *structure*
  must change (board, metric row) the Python renders one markup that CSS re-flows.
- **RTL is the default.** Every rule is written with `direction`/`text-align`
  derived from `T["dir"]` exactly as `load_css` does today; nothing may regress
  the PRO-46 column reading order or the data-grid "always LTR" island.
- **Text-only menus / provider facade rules are untouched** — this is CSS and view
  layout only; no outbound WhatsApp path changes.
- **Dark mode** (`prefers-color-scheme: dark` block) must keep working; the new
  rules only touch layout, never colour tokens.

## Breakpoints

Three widths, defined once and referenced everywhere (constants in a new
`admin_panel/ui/responsive.py`, interpolated into the CSS):

| token | range | design target |
|---|---|---|
| `MOBILE_MAX = 640` | ≤ 640 px | phones, portrait (375 / 390 / 430) — matches Streamlit's own column-stacking breakpoint so our rules and its rules flip together |
| `TABLET_MAX = 1024` | 641 – 1024 px | tablets, phones landscape, narrow laptop windows |
| desktop | > 1024 px | today's layout, unchanged |

Streamlit's sidebar overlay breakpoint is 768 px; the sidebar rules use that value
explicitly so they agree with Streamlit's own toggle behaviour.

## The work, in slices

Each slice is independently mergeable and leaves the desktop layout pixel-identical.

### S1 — Foundation: sidebar, container, tokens (`main.py`, `components.py`, new `responsive.py`) — done

1. `initial_sidebar_state="expanded"` → `"auto"`: Streamlit then expands on desktop
   and collapses to the hamburger on mobile, which is the behaviour operators expect.
2. Sidebar `width: 280px !important` moves inside `@media (min-width: 769px)`. Below
   that Streamlit's overlay owns the width.
3. `.block-container` side padding: `2rem` desktop → `1.25rem` tablet → `1rem` mobile;
   top padding `2rem` → `1rem` on mobile (the hamburger bar already costs height).
4. Base type: `html { font-size }` stays; inputs/selects/textarea get
   `font-size: 16px` at ≤ 640 px so iOS Safari stops zooming on focus; `h1`
   `1.875rem` → `1.5rem`, `h2` `1.375rem` → `1.2rem` at ≤ 640 px.
5. Touch targets: `.stButton > button`, sidebar radio labels, tab buttons get
   `min-height: 44px` at ≤ 640 px.
6. All new media-query blocks live in **one** `responsive_css(direction)` function in
   `responsive.py`, appended **last** inside `load_css`'s `<style>` so they win the
   cascade against the desktop rules above them without more `!important`.

### S2 — Metric rows and the Kanban board (`components.py`, `views/home.py`, `views/analytics.py`) — done

7. **Metric grid helper.** Add `render_metric_grid(items, T)` to `components.py`
   returning a `<div class="metric-grid">` of `.metric-tile` cards, styled with the
   existing `stMetric` look. Grid is `grid-template-columns: repeat(auto-fit,
   minmax(140px, 1fr))` — 5-up on desktop, 3-up on tablet, 2-up on phones, with no
   media query at all. Replace the three hand-rolled 5-column `st.metric` rows
   (`home.py` board tab, `home.py` table tab, `analytics.py` overview) with it. The
   helper is a pure string function like `render_kanban_column`, so it is unit-tested
   the same way. `st.metric` stays in use where a delta is shown (analytics revenue
   tiles) — those rows are ≤ 2 wide and stack acceptably.
8. **Kanban board.** Move the inline `style="display:flex; gap:12px; overflow-x:auto"`
   wrapper in `home.py` into a `.kanban-board` class in `components.py` (the `dir`
   attribute stays inline — PRO-46). Add `kanban-column--empty` to a column with no
   leads. Then:
   - ≤ 640 px: `.kanban-board { flex-direction: column; overflow: visible }`,
     `.kanban-column { min-width: 0; flex: none }`, `.kanban-column--empty
     { display: none }`, cards full width.
   - 641–1024 px: `flex-wrap: wrap`, `.kanban-column { flex: 1 1 calc(50% - 12px) }`.
   - desktop: unchanged 8-across scroller.
   - The status ordering (pending-review first, `KANBAN_STATUSES`) is preserved in all
     three because the DOM order never changes.
9. **Pending-review strip** (`_render_pending_review_strip`): the `[3, 1]`
   select + Assign row stacks on mobile by Streamlit's default, which is the right
   outcome (full-width picker over a full-width primary button). Only change:
   `st.columns([3, 1], gap="small")` so the desktop row tightens; verify, don't rewrite.

### S3 — Button rows, forms, login (`components.py`, `views/*.py`, `core/auth.py`) — done

10. **Inline-row marker.** Streamlit 1.31 cannot tag a column, so add
    `mark_row_inline()` to `components.py`: it emits a zero-height
    `<span class="row-inline" hidden></span>` into the *first* cell of a row, and the
    CSS `[data-testid="stHorizontalBlock"]:has(.row-inline) > [data-testid="column"]
    { min-width: 0 !important; flex: 1 1 0 !important }` at ≤ 640 px keeps that row
    horizontal. Apply it to every confirm pair (`professionals.py` ×2, `schedule.py`
    delete-day confirm, `home.py` delete-lead confirm `cy, cn`) and to the audit-log
    prev/next pagination. `:has()` is already relied on by the sidebar radio styling,
    so this adds no new browser requirement.
11. **Confirm rows drop the spacer column**: `[1, 1, 4]` → `[1, 1]` inside a
    `st.container()` whose width the CSS caps at `max-width: 320px` on desktop via
    the same marker. Yes/No then sit side by side at every width and the destructive
    button is never full-width.
12. **Login** (`auth.py`): replace the `[1, 2, 1]` column trick with a single
    `.login-container` wrapper that already exists in the CSS (`max-width: 400px`), so the
    form is full-width on phones and centred at 400 px on desktop. No behaviour change
    to `check_password`; the lockout / cookie logic is not touched.
13. **Forms**: `[data-testid="stForm"]` padding `24px` → `16px` at ≤ 640 px; the
    professional-edit two-column pairs (`c1, c2 = st.columns(2)`, `c_active,
    c_verified`) are allowed to stack — they are inputs, not actions. The
    Save / Save-anyway / Cancel row (`professionals.py` `[2, 2, 1]`) gets the inline
    marker so the three actions stay on one line, Cancel narrowest.
14. **Chat bubbles** (lead detail): `.chat-bubble max-width: 75%` → `92%` at ≤ 640 px.

### S4 — Tabs, tables, analytics (`components.py`, `views/*.py`) — done

15. **Tabs**: `.stTabs [data-baseweb="tab-list"] { overflow-x: auto; flex-wrap:
    nowrap; scrollbar-width: thin; -webkit-overflow-scrolling: touch }` and
    `[data-baseweb="tab"] { white-space: nowrap; padding: 8px 12px }` at ≤ 640 px.
    Six analytics tabs then scroll instead of wrapping; the active tab is scrolled
    into view by the browser because it holds focus.
16. **Data grids** (`st.dataframe` / `st.data_editor`): they are canvas widgets with
    their own horizontal scroll, so they are left as grids. Two adjustments only:
    `height` capped at `min(existing, 60vh)` on the leads editor so the Save button is
    reachable without scrolling the page past the grid, and the "always LTR" island
    rules are re-asserted *after* the responsive block so nothing above re-orders
    cells. No card-list re-rendering of tables — the Board tab is the phone path,
    the Table tab is the desktop path, and the tab labels will say so in `TRANS`
    (one new key per language, guarded by `tests/test_admin_translations.py`).
17. **Analytics filter/date rows** (`c1..c5` in `analytics.py`) stack on mobile by
    default and that is acceptable — they are inputs. The `[2, 1]` chart + table pair
    in the FinOps tab stacks so the chart gets the full width.
18. **Settings audit filters**: the `[1, 1, 1, 1]` since/until/page-size row keeps
    the trailing spacer only on desktop (spacer columns are hidden by Streamlit
    on mobile anyway); the prev/next row is covered by item 10.

### S5 — Verification: tests, screenshot script, checklist — done

19. **Unit tests** — new `tests/test_admin_responsive.py` (no browser, no Streamlit
    server), in the style of `tests/test_admin_kanban.py`:
    - `load_css` output contains exactly the three breakpoint media queries, in the
      last `<style>` position, for both `HE` and `EN`;
    - `render_metric_grid` emits one `.metric-tile` per item, escapes labels
      (`html.escape`, like the kanban card), and carries `dir`;
    - the kanban board wrapper has `class="kanban-board"` and an empty status gets
      `kanban-column--empty`;
    - `mark_row_inline()` output is stable and hidden;
    - a guard that no `width: <n>px !important` survives outside a `@media` block in
      `load_css` (the sidebar bug, prevented for good).
    Bump the `docs/TESTING.md` "Current status" floor accordingly.
20. **Screenshot script** — `scripts/admin_panel_screenshots.py`: Playwright +
    Chromium, logs in against a running local panel (`docker-compose up`), and captures
    each of the 5 views at 375×812, 768×1024, 1024×768, 1440×900 in HE and EN into
    `artifacts/admin-panel/`. Optional dev dependency (`requirements-dev.txt` entry,
    not `requirements.txt`); the script exits with a clear message when Playwright is
    absent. It is a review aid, not CI — the panel needs live Mongo/Redis, which the
    unit suite deliberately never has.
21. **Manual checklist** — new section in `docs/MANUAL_TEST_PLAN.md`, "Admin panel
    viewport checklist", listing the acceptance criteria below as tick boxes per
    width, so the PR can attach a ticked copy as evidence.
22. `docs/ARCHITECTURE.md` / `CLAUDE.md` Process 3 paragraph: one sentence pointing at
    `responsive.py` as the single home of breakpoints (docs-syncer pass).

### S6 — RTL correctness, the sidebar, and cross-cutting polish — done

Not in the original five. It exists because S5 put the panel in a browser for the
first time and the Hebrew rendering did not survive the look — and because
several of the defects were in chrome no slice had been aimed at, so no slice
would have found them.

The distinction that shapes the fixes, and where each one goes:

- rules that are wrong at a **width** live in `responsive.py`;
- rules that are wrong in a **language** live in the new `rtl.py`, which
  overrides Streamlit's and BaseWeb's own sheets where a side is baked in.
  In LTR it emits nothing at all, which is the claim the module makes about
  itself and the thing its test pins;
- anything wrong in **our own** markup is fixed in place in `components.py`.

23. **Streamlit chrome that ignores `direction`** (`rtl.py`): the hamburger
    (Streamlit pins it `left`, so Hebrew opened a right-hand sidebar from the
    opposite corner); Streamlit's own top-right toolbar, which has to move the
    other way or the two overlap exactly; the sidebar's collapse button, pinned
    to the sidebar's right edge — the content edge in LTR, the screen edge in
    RTL; and the slider, whose BaseWeb thumb is positioned from a track
    measurement taken as if the page were LTR and lands off the end of its own
    track. The slider becomes an LTR island, the same answer the Glide data grid
    already gets.
24. **The sidebar itself** (`components.py`): nav rows span the sidebar rather
    than their own text, so the selected pill stops having five different widths
    and the tap target is the row; the row is `flex` at *every* width, not only
    below 640px — under the base sheet's `label { display: block !important }`
    the radio marker sat on its own line above the label, which is why every
    desktop nav row measured 61px instead of 39px. The mobile drawer is capped so
    a real edge of page shows behind it (Streamlit's 336px on a 375px screen
    leaves a 39px sliver).
25. **Our own direction-aware rules that were wrong** (`components.py`): HTML
    tables forced `text-align: left` on Hebrew inside a right-aligned box — only
    the Glide grid is a deliberate LTR island, and for a reason a markdown table
    does not share; `.chat-meta` set `row-reverse` *and* inherited `direction:
    rtl`, flipping twice and putting the speaker icon back on the left; chat
    bubbles set `align-self` and an auto margin, which disagreed silently (the
    margin wins); `text-transform: uppercase` and 0.03–0.05em tracking were
    applied to Hebrew, which has no case and is drawn to sit close.
26. **Dark mode** (`components.py`): `STATUS_COLORS_DARK` had existed since
    PRO-46 and was read by nothing, so every pill and Kanban header stayed a pale
    pastel on a `#0F172A` page. Both palettes are now emitted as classes with the
    dark set behind `prefers-color-scheme`, which also required the header
    colours to leave the inline `style` attribute they were in — an inline
    declaration outranks any media query.
27. **Polish that is not direction-specific** (`components.py`, `responsive.py`):
    a `:focus-visible` ring, since the sheet styled `:hover` on everything and
    `:focus` on nothing; `min-height` on the download and form-submit buttons,
    the two the `.stButton` touch-target rule does not reach (both measured 38px
    on a phone); `box-sizing` and a floor on buttons, which measured 39px and
    41px side by side depending on whether they carried a border.
28. **The preview grew a second page** (`scripts/admin_panel_layout_preview.py`
    `--section widgets`): alerts, expander, form, date/number/multiselect/slider,
    chat, HTML table, data editor, download. Every defect above is in chrome the
    Dashboard happens not to render — a defect you cannot render is a defect you
    cannot see.

### What S3 and S4 changed against the plan above

Three items were written before anything had been measured, and measuring
changed two of them and killed a third.

- **The marker goes *before* the row, not inside its first cell** (item 10).
  A marker inside a cell earns that cell the vertical block's `1rem` gap, so
  the two buttons of a confirm pair end up a line apart. As a preceding
  sibling the rule reaches the row with `+`, and the marker's own container is
  `display: none` — which also has to sit **outside** the phone media query,
  because a zero-height flex item still earns the gap: scoped to the phone it
  pushed the marked desktop rows down by 15, 30 and 45px.
- **`min-width: 0` alone, not `flex: 1 1 0`** (item 10). The plan's pair would
  have flattened `[2, 2, 1]` into thirds; clearing `min-width` and leaving
  `flex-basis` alone keeps Cancel the narrow one. Measured at 375px: 128 /
  128 / 59.
- **The login page is capped on `.block-container`, not wrapped in a `<div>`**
  (item 12). `st.markdown('<div class="login-container">')` wraps nothing —
  Streamlit parses each markdown call on its own, so the browser closes the
  div immediately. Measured: the container rendered 400px wide with
  `children.length == 0` while the form sat beside it at 1100px.
- **No `60vh` cap on the leads editor** (item 16). `st.data_editor` self-caps
  at 402px whether it is given 30 rows or 200 — 49vh on a 375x812 phone — so
  the planned rule could never fire. A rule that cannot fire is the shape this
  project has already been bitten by (S6's alert accent), so there is none,
  and `tests/test_admin_action_rows.py` records the measurement.
- **The fixed-width guard was over-broad.** Its regex ended in `width:`, so it
  also caught `max-width` — the opposite shape, since a cap can only reduce a
  box and is inert below itself. Narrowed to `width` and `min-width`, with a
  test beside it proving both forcing shapes are still caught.

### The sweep after S6 (2026-09-14)

A second full look at the rendered panel — all four pages, four widths, both
languages, both colour schemes — after everything above had landed. Two real
defects, one root cause, and a review tool that had been lying.

- **Every checkbox and toggle stacked its box above its text** (46px tall,
  measured, against 24px for the row), and the English checkbox text was in
  uppercase that a later `span` rule could not undo because the text lives in
  a `p`. The cause was the same one S6 had already met on the sidebar radio and
  had beaten with a second `!important`: an unscoped `label {{ display: block
  !important }}` written for the captions above inputs, reaching every label
  BaseWeb lays out as a *row*. Fixed at the root — the rule is now
  `label[data-testid="stWidgetLabel"]`, captions only — rather than with a
  third override. `tests/test_admin_rtl.py` walks every rule in the sheet and
  fails if any selector containing `label` forces `display: block` outside that
  one.
- **The login button was Streamlit's default red** (`rgb(255, 75, 75)`,
  measured) while every other primary in the panel was the blue gradient:
  `st.form_submit_button` renders under `stFormSubmitButton` with
  `kind="primaryFormSubmit"`, which `.stButton button[kind="primary"]` never
  matched. The base, primary and secondary button rules now name both.
- **The preview app was wrong about the panel in two ways**, and every
  screenshot taken from it since S5 carried both. Seven of its translation
  keys were near-misses of real ones (`tab_table` for `tab_dashboard`,
  `input_client` for `client_name_label`…), so the Hebrew dashboard's tabs
  rendered "Table / New" and its forms "Client / Save" in English — a
  localisation defect that did not exist. And its copy of the dashboard's
  delete confirm was not marked with `mark_row_inline()` while the real one
  is, so the phone screenshot showed the two buttons stacked — a regression
  that was not there. Both fixed, both pinned: every key the preview reads must
  exist in `TRANS`, and its confirm row must be marked like the view's.
- **A false positive worth recording.** With the Material Symbols font
  unreachable (a sandboxed browser behind an egress proxy), every icon renders
  as its ligature *name* — `support_agent`, `event_available` — and those words
  wrapped the Kanban headers onto three lines and pushed the count badge
  outside its column. Measured with the real font embedded, every header fits
  and nothing clips. `scripts/admin_panel_screenshots.py --icon-font
  <file.woff2>` embeds a local copy so the evidence shows the real thing.

## Acceptance criteria

Checked in both `HE` (RTL) and `EN` (LTR), light and dark, at 375×812, 768×1024,
1024×768 and 1440×900, on all five views (Dashboard, Professionals, Schedule,
Analytics, Settings):

- [ ] The page body never scrolls horizontally. Only the data grids, the tab strip and
      the desktop kanban board scroll sideways, each inside its own container.
- [ ] On a phone the sidebar starts collapsed; the hamburger opens it; language,
      navigation and auto-refresh controls are all reachable and the overlay closes
      after picking a page.
- [ ] Metric tiles: 2 per row on a phone, 3 on a tablet, 5 on desktop, same order.
- [ ] Kanban: single stacked column on a phone with empty statuses hidden; two-up on a
      tablet; unchanged 8-across scroller on desktop; pending-review leads always first.
- [ ] Every Yes/No, Save/Cancel and Prev/Next pair stays on one row at every width;
      no destructive button is ever full-width.
- [ ] All buttons, nav items and tab headers are at least 44 px tall on a phone;
      focusing an input on iOS does not zoom the page.
- [ ] Login form is full-width on a phone and centred at 400 px on desktop; lockout and
      remember-me behave exactly as before (`tests/test_admin_auth.py` green).
- [ ] Tabs scroll horizontally, never wrap or clip; the selected tab is visible.
- [ ] Data grids remain LTR, editable and scrollable; the leads editor's Save button is
      reachable without scrolling past a full-height grid.
- [ ] The desktop layout at 1440 px is visually unchanged from `dev` (before/after
      screenshots in the PR).
- [ ] `black`, `flake8`, full `pytest` green; `docs/TESTING.md` floor bumped; the
      **ux-reviewer** subagent run on the diff with no open findings.
- [ ] PR carries the screenshot set (at minimum HE 375 and 1440 for each view) and the
      ticked viewport checklist.

## Out of scope

- Upgrading Streamlit past 1.31.1 (own ticket; several pins depend on it).
- Rewriting the panel in React / a component library, or a PWA / native shell.
- Re-rendering data grids as card lists on mobile (revisit only if the Board tab
  proves insufficient for phone triage).
- Any change to WhatsApp flows, the provider facade or the text-only menu rule.

## Risks and how they are contained

- **Streamlit DOM `data-testid` hooks change between versions.** Every selector the
  responsive block depends on is listed in one comment at the top of `responsive.py`
  with the pinned version; a Streamlit bump reviews that list first.
- **`!important` cascade fights.** New rules are appended last and avoid `!important`
  except where Streamlit itself uses it (column `min-width` on mobile). The unit guard
  in item 19 stops fixed pixel widths from creeping back outside media queries.
- **`:has()` support.** Chrome 105+, Safari 15.4+, Firefox 121+ — all current; the
  sidebar already depends on it. Without it, rows fall back to Streamlit's stacking,
  which is degraded, not broken.
- **Double-rendering cost.** None: the board and metric grid render once; only CSS
  re-flows them.

## Estimate

S1 ½ day · S2 1 day · S3 1 day · S4 ½ day · S5 1 day — about 4 working days including
the screenshot pass, in one PR or two (S1–S2 first, since the board and sidebar are
where a phone operator hurts most today).
