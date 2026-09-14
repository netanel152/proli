"""Source-level pins for the admin-panel sweep after S6.

Streamlit view bodies never execute under pytest (`.claude/rules/admin-panel.md`),
so each of these is a static read of the source — the same shape as the arity
and action-row guards — pinning a defect that was found by reading and would
be invisible to every other gate if it came back:

* the weekly-template widgets keyed without the pro, so switching pro showed
  and *saved* the previous pro's hours;
* the assign options built from a bare ``users_collection.find()`` — customers
  and pros still awaiting approval offered a waiting customer's address;
* ``p['business_name']`` rendered raw where self-onboarding leaves it blank;
* the logout button sent to the sidebar root instead of its column;
* a full second of ``time.sleep`` on every login for a message the rerun threw
  away.
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL = REPO_ROOT / "admin_panel"


def _source(relpath):
    return (PANEL / relpath).read_text(encoding="utf-8")


def _code(relpath):
    """The source with `#` comments dropped: the comments quote the very
    shapes these tests forbid, which is exactly why they were written."""
    lines = _source(relpath).splitlines()
    return "\n".join(line.split("#", 1)[0] for line in lines)


def test_weekly_template_widget_keys_carry_the_pro_id():
    """A keyed widget ignores ``value=`` once its key exists in session state.
    Every template widget is rendered on every run (tab bodies always run), so
    a key without the pro in it kept showing pro A's hours after the selector
    moved to pro B — and Save wrote A's hours onto B."""
    source = _source("views/schedule.py")
    keys = re.findall(r'key=f?"(tmpl_[a-z]+_[^"]+|template_duration[^"]*)"', source)
    assert len(keys) == 4, keys
    for key in keys:
        assert (
            "{pro['_id']}" in key
        ), f"template widget key not scoped to the pro: {key}"


def test_assign_options_come_from_the_eligibility_filter():
    """The table's Professional column and the Edit Lead form write ``pro_id``
    from ``pro_map_name_to_id``; its candidates must be the same set matching
    would consider, not every document in ``users``."""
    code = _code("views/home.py")
    assert "users_collection.find(APPROVED_PRO_FILTER" in code
    assert "users_collection.find()" not in code, "a bare find() offers customers"
    # Display names still cover every professional, so a lead held by a
    # paused pro keeps showing who holds it.
    assert 'users_collection.find({"role": "professional"})' in code


def test_no_view_renders_business_name_without_a_fallback():
    """``p['business_name']`` in an f-string paints a bold empty string on the
    pro card; ``p.get("business_name", default)`` is the same bug, because the
    default never fires on the empty string self-onboarding writes."""
    for relpath in ("views/home.py", "views/schedule.py", "views/professionals.py"):
        source = _source(relpath)
        assert not re.search(r"\{p\['business_name'\]\}", source), relpath
        assert not re.search(r'\.get\("business_name", T\[', source), relpath
        assert '{p["business_name"]:' not in source, relpath


def test_logout_button_lands_in_its_column():
    """`main.py` calls ``logout`` inside the brand/logout column; an explicit
    ``st.sidebar.button`` ignores that and appends to the sidebar root."""
    source = _code("core/auth.py")
    assert "st.sidebar.button" not in source
    assert 'if st.button(T["disconnect"])' in source


def test_login_does_not_sleep_for_a_message_the_rerun_discards():
    source = _code("core/auth.py")
    login = source[source.index("def check_password") : source.index("def logout")]
    assert "time.sleep(1)" not in login
    assert "st.success(" not in login


def test_admin_mutations_are_gated_on_the_write():
    """``update_admin_role`` / ``delete_admin`` return whether a document
    changed; the flash used to say "updated" and "deleted" regardless, and
    Delete wrote on the first click."""
    source = _source("views/settings.py")
    assert 'if update_admin_role(admin["username"], new_role):' in source
    assert 'if delete_admin(admin["username"]):' in source
    assert "confirm_deladmin_" in source
