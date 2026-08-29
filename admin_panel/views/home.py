import streamlit as st
import pandas as pd
from bson.objectid import ObjectId
from datetime import datetime
from admin_panel.core.utils import (
    users_collection,
    leads_collection,
    messages_collection,
    send_completion_check_sync,
)
from admin_panel.ui.components import (
    render_chat_bubble,
    render_kanban_column,
    render_status_pill,
    STATUS_COLORS,
)
from admin_panel.core.auth import log_audit, get_current_role
from admin_panel.core.lead_queries import (
    EDITOR_COLUMNS,
    SKIP_LEAD_GONE,
    SKIP_NO_CHANGE,
    SKIP_UNRESOLVED,
    build_edit_form_payload,
    build_lead_row,
    save_lead_edits,
)
from admin_panel.core.rbac import can_edit, has_permission
import pytz
import os
import sys
from app.core.logger import logger
from app.core.constants import AdminDefaults, Defaults, LeadStatus, Actor
from app.core.phone import to_chat_id, strip_suffix
from app.core.lead_history import status_history_entry

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

KANBAN_STATUSES = [
    # First in reading order — the human-intervention queue (PRO-46). These leads
    # (e.g. exhausted reassignments, PRO-63) need an operator to act, so they lead
    # the board instead of being invisible off the end of it. In the RTL layout
    # this renders as the right-most (leading) column; the flex container below
    # sets `dir` explicitly so that mapping doesn't depend on inherited direction.
    "pending_admin_review",
    "new",
    "contacted",
    "booked",
    "completed",
    "rejected",
    "closed",
    "cancelled",
]
ALL_STATUSES = [s.value for s in LeadStatus]


#: Why a row was skipped -> the T key explaining it to the operator.
_SKIP_REASON_KEYS = {
    SKIP_LEAD_GONE: "leads_skip_lead_gone",
    SKIP_UNRESOLVED: "leads_skip_unresolved",
    SKIP_NO_CHANGE: "leads_skip_no_change",
}


def _t(T, key, fallback, **subs):
    """Localized string with `{placeholder}` substitution that cannot raise.

    `str.format` on translator-supplied text turns one mistyped placeholder
    into a red traceback rendered on top of the success message. `.replace`
    degrades to leaving the placeholder visible instead — the panel already
    does this in views/settings.py.
    """
    text = T.get(key, fallback)
    for name, value in subs.items():
        text = text.replace("{" + name + "}", str(value))
    return text


def _render_leads_flash(T):
    """Render the confirmation stashed by the previous run's mutation."""
    flash = st.session_state.pop("leads_flash", None)
    if not flash:
        return

    if flash.get("deleted"):
        msg = T.get("success_delete", "Lead deleted.")
    else:
        msg = _t(T, "leads_msg_saved", "{n} changes saved.", n=flash.get("updated", 0))

    st.toast(msg, icon="✅")
    st.success(msg)

    _render_skipped(T, flash.get("skipped_rows") or [])


def _render_skipped(T, skipped_rows):
    """Name the rows that were not written, and why."""
    if not skipped_rows:
        return

    st.warning(
        _t(
            T,
            "leads_msg_skipped",
            "Rows not saved: {n}. Their edits were discarded — re-enter them.",
            n=len(skipped_rows),
        )
    )
    # Built with explicit `columns=` rather than from dict keys: the two
    # headers are localized strings, and if a translation ever made them equal
    # the dict would collapse to one key and the Reason column would silently
    # vanish — the wrong failure mode for a table whose whole job is explaining
    # a failure.
    st.dataframe(
        pd.DataFrame(
            [
                (
                    row.get("client") or "—",
                    T.get(
                        _SKIP_REASON_KEYS.get(row.get("reason"), ""),
                        row.get("reason", ""),
                    ),
                )
                for row in skipped_rows
            ],
            columns=[
                T.get("col_client", "Client"),
                T.get("leads_col_reason", "Reason"),
            ],
        ),
        hide_index=True,
        use_container_width=True,
    )


def view_leads_dashboard(T):
    st.title(T["title_dashboard"])
    st.caption(T.get("page_desc_dashboard", "View and manage incoming leads."))

    # PRO-161: every mutation in this view reruns to refresh the table, which
    # throws away the element tree — so a confirmation rendered after the write
    # was never actually readable. Stash it and render it on the next run
    # (same pattern as `sch_flash` in views/schedule.py, PRO-158).
    #
    # Popped here, above the tabs, on purpose. Rendering it inside the Table
    # tab's non-empty branch would strand the flash whenever the post-save
    # re-query came back empty, to resurface at some unrelated later moment;
    # and delete/edit can both be triggered from the Kanban tab, whose
    # confirmation would then never appear. The toast is what the admin
    # actually sees: the Save button sits under a ~400px scroll region, so an
    # inline alert at the top of the page renders off-screen.
    _render_leads_flash(T)

    # Tabs: Kanban | Table | Create
    tab_kanban, tab_table, tab_create = st.tabs(
        [
            T.get("tab_kanban", "Board"),
            T.get("tab_dashboard", "Table"),
            T.get("tab_create_lead", "Create"),
        ]
    )

    # --- Shared Data ---
    all_pros = list(users_collection.find())
    pro_map_id_to_name = {
        p["_id"]: p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros
    }
    pro_map_name_to_id = {
        p.get("business_name", AdminDefaults.UNKNOWN_PRO): p["_id"] for p in all_pros
    }
    pro_names = [p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros]
    pro_names.insert(0, T["unknown_pro"])

    @st.cache_data(ttl=30)
    def get_leads_data():
        leads = list(leads_collection.find().sort("created_at", -1).limit(100))
        if not leads:
            # PRO-161/PRO-158: a bare `pd.DataFrame()` has zero columns, which
            # is what made the schedule editor crash on an empty day.
            # `EDITOR_COLUMNS` is the schema the save routine reads from, so
            # the frame carries it even when there is nothing to show.
            return pd.DataFrame(columns=EDITOR_COLUMNS)

        # PRO-163: the per-lead row build moved to `lead_queries` so it has a
        # real test. It is where this ticket's own regression lived — a hidden
        # column the Edit form prefills from — and it was unreachable from a
        # test while it sat inside this @st.cache_data closure. Same move as
        # save_lead_edits (PRO-161) and the PRO-140/158 query extractions.
        data = [
            build_lead_row(
                l,
                pro_map_id_to_name=pro_map_id_to_name,
                unknown_pro_label=T["unknown_pro"],
            )
            for l in leads
        ]

        # `pd.DataFrame(rows, columns=...)` is a *reindex*, not a validation: a
        # renamed producer key yields a silent all-NaN column. That degrades
        # safely on the save path (every row skips, visibly) but NOT on the
        # delete path — a NaN `id` collapses every `lead_labels` key to "nan",
        # the lookup below then matches every row, and `.iloc[0]` hands the
        # delete button row 0. That is this ticket's own bug, in the one
        # section that deletes. So assert rather than reindex-and-hope.
        missing = set(EDITOR_COLUMNS) - set(data[0])
        if missing:
            raise KeyError(f"leads editor frame is missing {sorted(missing)}")
        return pd.DataFrame(data, columns=EDITOR_COLUMNS)

    leads_df = get_leads_data()

    # --- Metrics Row (shared across tabs) ---
    total_count = leads_collection.count_documents({})
    new_count = leads_collection.count_documents({"status": "new"})
    booked_count = leads_collection.count_documents({"status": "booked"})
    # PRO-46: mirrors /health/leads `pending_review_count` — the human-intervention queue.
    pending_review_count = leads_collection.count_documents(
        {"status": LeadStatus.PENDING_ADMIN_REVIEW}
    )
    active_pros = users_collection.count_documents({"is_active": True})

    # ==========================================
    # TAB 1: KANBAN BOARD
    # ==========================================
    with tab_kanban:
        # Metrics — the pending-review tile gets extra width for its longer label.
        c1, c2, c3, c4, c5 = st.columns([1, 1.4, 1, 1, 1])
        c1.metric(T.get("metric_total", "Total"), total_count)
        c2.metric(T.get("metric_pending_review", "Needs Review"), pending_review_count)
        c3.metric(T.get("metric_new", "New"), new_count)
        c4.metric(T.get("metric_booked", "Booked"), booked_count)
        c5.metric(T.get("metric_pros", "Staff"), active_pros)

        st.markdown("")

        if leads_df.empty:
            st.info(T["no_leads_found"])
        else:
            # Group leads by status
            grouped = {}
            for status in KANBAN_STATUSES:
                mask = leads_df["status"] == status
                grouped[status] = leads_df[mask].to_dict("records")

            # Render Kanban columns as horizontal scrollable row
            all_columns_html = ""
            for status in KANBAN_STATUSES:
                all_columns_html += render_kanban_column(
                    status, grouped.get(status, []), T
                )

            # dir is set explicitly (not left to inherited direction) so the
            # column reading order — pending_admin_review first/leading — is
            # stable in RTL regardless of any ancestor's direction (PRO-46).
            st.markdown(
                f"""<div dir="{T['dir']}" style="display: flex; gap: 12px; overflow-x: auto; padding-bottom: 12px; direction: {T['dir']};">
{all_columns_html}
</div>""",
                unsafe_allow_html=True,
            )

        st.markdown("")

        # Quick actions on selected lead (below Kanban)
        if not leads_df.empty:
            _render_lead_detail_section(
                leads_df, T, all_pros, pro_map_name_to_id, tab_key="kanban"
            )

    # ==========================================
    # TAB 2: TABLE VIEW
    # ==========================================
    with tab_table:
        # Metrics — the pending-review tile gets extra width for its longer label.
        c1, c2, c3, c4, c5 = st.columns([1, 1.4, 1, 1, 1])
        c1.metric(T.get("metric_total", "Total"), total_count)
        c2.metric(T.get("metric_pending_review", "Needs Review"), pending_review_count)
        c3.metric(T.get("metric_new", "New"), new_count)
        c4.metric(T.get("metric_booked", "Booked"), booked_count)
        c5.metric(T.get("metric_pros", "Staff"), active_pros)

        st.markdown("")

        if not leads_df.empty:
            # Export CSV
            # PRO-163: `_`-prefixed columns are internal carriers for the
            # editor and the Edit form (`_chat_id`, `_display_name`,
            # `_details`), not operator-facing data — they duplicate
            # visible columns in the export.
            csv = (
                leads_df.drop(
                    columns=[c for c in leads_df.columns if c.startswith("_")]
                )
                .to_csv(index=False)
                .encode("utf-8-sig")
            )
            st.download_button(
                label=T.get("export_csv", "Export CSV"),
                data=csv,
                file_name=f'proli_leads_{datetime.now().strftime("%Y-%m-%d")}.csv',
                mime="text/csv",
                key="export_leads_csv",
            )

        if leads_df.empty:
            st.info(T["no_leads_found"])
        else:
            st.subheader(T["table_title"])

            status_options = ALL_STATUSES

            edited_df = st.data_editor(
                leads_df,
                key="leads_editor",
                column_config={
                    "id": None,
                    "_chat_id": None,
                    "_display_name": None,
                    "date": st.column_config.DatetimeColumn(
                        T["col_date"],
                        format="D MMM YYYY, h:mm a",
                        width="medium",
                        disabled=True,
                    ),
                    "client": st.column_config.TextColumn(
                        # PRO-163: "medium", because this column held 12 Latin
                        # digits before and can now hold a Hebrew name. The
                        # grid CSS forces LTR, so an over-long RTL string clips
                        # from its *start* — the wrong end for a name.
                        T["col_client"],
                        width="medium",
                        disabled=True,
                    ),
                    "professional": st.column_config.SelectboxColumn(
                        T["col_pro"], width="medium", options=pro_names, required=False
                    ),
                    "details_summary": st.column_config.TextColumn(
                        T["col_details"], width="large"
                    ),
                    "status": st.column_config.SelectboxColumn(
                        T["col_status"],
                        options=status_options,
                        width="small",
                        required=True,
                    ),
                },
                use_container_width=True,
                hide_index=True,
                # PRO-161: "dynamic" let the admin add and delete rows here,
                # but Save only ever read `edited_rows` — added and deleted
                # rows were discarded while the save still reported success.
                # Leads arrive from the bot; the supported hand-made path is
                # the Create tab in this same view, which validates the phone
                # and writes a whole document.
                num_rows="fixed",
            )

            # The "+" row is gone; both replacements live in this same view but
            # nothing on screen pointed at them.
            st.caption(
                T.get(
                    "leads_editor_hint",
                    "To add a lead use the Create tab; to delete one use the "
                    "lead details below the table.",
                )
            )

            # Save changes button
            if can_edit(get_current_role()):
                if st.button(
                    T.get("save_btn", "Save Changes"),
                    type="primary",
                    key="save_dashboard_changes",
                ):
                    changes = st.session_state.leads_editor.get("edited_rows", {})
                    if not changes:
                        st.toast(T.get("no_changes", "No changes."))
                        # The data editor's widget id hashes the frame itself,
                        # so a new inbound lead (or the 30s cache expiring)
                        # between typing and clicking Save builds a *new*
                        # widget with empty `edited_rows` — the admin's work is
                        # gone and "No changes" is a lie by omission. Same
                        # class as the bug this ticket fixes, so say it.
                        st.caption(
                            T.get(
                                "leads_msg_refresh_discarded",
                                "If you had unsaved edits, the table refreshed "
                                "underneath them and they were discarded — "
                                "re-apply them and save again.",
                            )
                        )
                    else:
                        # PRO-161: `edited_df` is the frame the editor returned
                        # *this run*, and `changes` keys are positions into that
                        # same frame — so the id read out of it is always the row
                        # the admin actually edited. The old code resolved it
                        # against a session snapshot taken once, hours earlier.
                        result = save_lead_edits(
                            edited_df,
                            changes,
                            leads_collection=leads_collection,
                            pro_map_name_to_id=pro_map_name_to_id,
                            unknown_pro_label=T["unknown_pro"],
                            audit=log_audit,
                        )

                        if result["updated"]:
                            st.session_state["leads_flash"] = result
                            st.cache_data.clear()
                            st.rerun()
                        else:
                            # Nothing was written — a success here would be the
                            # exact lie this ticket exists to kill. No rerun, so
                            # the admin's edits stay on screen for a second try.
                            st.warning(
                                T.get(
                                    "leads_msg_nothing_saved",
                                    "Nothing was saved. The leads you edited "
                                    "may no longer exist — refresh the table "
                                    "and try again.",
                                )
                            )
                            # The skipped rows carry the *reason* nothing
                            # landed; dropping them here would leave the admin
                            # with a warning and no diagnosis.
                            _render_skipped(T, result.get("skipped_rows") or [])

            st.markdown("")

            # Lead selection and actions (below table)
            _render_lead_detail_section(
                leads_df, T, all_pros, pro_map_name_to_id, tab_key="table"
            )

    # ==========================================
    # TAB 3: CREATE LEAD
    # ==========================================
    with tab_create:
        if not can_edit(get_current_role()):
            st.warning(
                T.get(
                    "no_permission_create", "You don't have permission to create leads."
                )
            )
            return

        st.header(T.get("create_lead_title", "Create a New Lead"))

        with st.form("create_lead_form"):
            c1, c2 = st.columns(2)
            with c1:
                new_phone = st.text_input(
                    T.get("input_client_phone", "Phone (WhatsApp)"),
                    placeholder="972501234567",
                )
                new_status = st.selectbox(
                    T.get("input_status", "Initial Status"),
                    options=["new", "contacted", "booked", "closed"],
                    index=0,
                )
            with c2:
                pro_names_create = [
                    p.get("business_name", AdminDefaults.UNKNOWN_PRO) for p in all_pros
                ]
                pro_names_create.insert(0, T["unknown_pro"])
                selected_pro_name = st.selectbox(
                    T.get("input_pro", "Assign Professional"), options=pro_names_create
                )

            new_details = st.text_area(
                T.get("input_issue", "Issue / Details"),
                placeholder="e.g., Leaking faucet in the kitchen...",
            )

            submitted = st.form_submit_button(
                T.get("submit_create_lead", "Create Lead"), type="primary"
            )

            if submitted:
                if not new_phone:
                    st.error(T.get("error_phone_required", "Phone number is required."))
                else:
                    try:
                        clean_phone = "".join(filter(str.isdigit, new_phone))
                        chat_id = to_chat_id(clean_phone)

                        assigned_pro_id = None
                        if selected_pro_name != T["unknown_pro"]:
                            assigned_pro_id = pro_map_name_to_id.get(selected_pro_name)

                        new_lead_doc = {
                            "chat_id": chat_id,
                            "details": new_details,
                            "issue_type": new_details,
                            "status": new_status,
                            "status_history": [
                                status_history_entry(new_status, Actor.ADMIN)
                            ],
                            "pro_id": assigned_pro_id,
                            "created_at": datetime.now(pytz.utc),
                            "full_address": AdminDefaults.MANUAL_LABEL,
                            "appointment_time": AdminDefaults.MANUAL_LABEL,
                            "source": AdminDefaults.MANUAL_SOURCE,
                        }

                        leads_collection.insert_one(new_lead_doc)
                        log_audit(
                            "create_lead", {"chat_id": chat_id, "status": new_status}
                        )
                        logger.info(f"Admin manually created lead for {chat_id}")
                        st.success(
                            T.get("create_lead_success", "Lead created successfully!")
                        )
                        st.cache_data.clear()
                    except Exception as e:
                        st.error(f"Error creating lead: {e}")


def _render_lead_detail_section(
    leads_df, T, all_pros, pro_map_name_to_id, tab_key="kanban"
):
    """Render the lead detail / quick-action section below the Kanban board."""
    st.markdown("---")
    st.subheader(T.get("lead_quick_actions", "Lead Details"))

    # PRO-161: options are lead ids, resolved by an explicit lookup rather than
    # `.iloc` on a row position.
    #
    # To be accurate about what this does and does not fix: `st.selectbox`
    # hashes its *formatted labels* into the widget id, so changing the lead
    # list already rebuilt the widget and reset the selection — the old
    # `range(len(...))` form was therefore not reachable as a wrong-row bug.
    # This is not a live-bug fix. It is here because this section owns the
    # delete button, and identity-by-id removes the whole question instead of
    # leaving it resting on an undocumented Streamlit implementation detail.
    #
    # The status is localized: it renders as a Hebrew pill everywhere else in
    # this view, and a raw `pending_admin_review` here read as a different
    # system. The RLM pins the base direction so the Latin phone number and
    # the direction-neutral separators can't reorder under bidi.
    lead_labels = {
        str(row["id"]): (
            f"‏{T.get(row['status'], str(row['status']).capitalize())} · "
            f"{row['client']} · {str(row.get('details_summary', ''))[:40]}"
        )
        for _, row in leads_df.iterrows()
    }
    if not lead_labels:
        st.info(T.get("no_leads_found", "No leads found."))
        return

    no_longer_listed = T.get("lead_no_longer_listed", "That lead is no longer listed.")
    selected_id = st.selectbox(
        T.get("select_lead", "Select Lead"),
        list(lead_labels.keys()),
        # Never fall through to the raw id: a 24-char ObjectId hex is not a
        # thing to show an operator.
        format_func=lambda lid: lead_labels.get(lid, no_longer_listed),
        key=f"{tab_key}_lead_select",
    )

    matches = leads_df[leads_df["id"].astype(str) == str(selected_id)]
    if matches.empty:
        # Defence in depth, not a live path: `selected_id` is drawn from
        # `lead_labels`, which was built from `leads_df` in this same run, so
        # the lookup should always match. Kept because the alternative on a
        # miss is acting on whatever now sits at that position — and this
        # section deletes leads.
        st.info(no_longer_listed)
        return

    _render_selected_lead_actions(
        matches.iloc[0], T, pro_map_name_to_id, tab_key=tab_key
    )


def _render_selected_lead_actions(
    selected_lead, T, pro_map_name_to_id, tab_key="kanban"
):
    """Render actions for a selected lead (shared between Kanban and Table views)."""
    lid = selected_lead["id"]
    k = f"{tab_key}_{lid}"
    c1, c2 = st.columns([1, 3])

    with c1:
        # Status pill
        status = selected_lead.get("status", "new")
        pill_html = render_status_pill(status, T)
        st.markdown(pill_html, unsafe_allow_html=True)
        st.markdown("")

        # Delete action
        if has_permission(get_current_role(), "delete_leads"):
            if st.button(
                T.get("delete_btn", "Delete Lead"), key=f"delete_{k}", type="secondary"
            ):
                st.session_state[f"confirm_delete_{k}"] = True

        # Manual customer check
        if can_edit(get_current_role()):
            if st.button(
                T.get("check_customer_btn", "Customer Check"), key=f"check_{k}"
            ):
                try:
                    # PRO-86: False means the facade suppressed the send (circuit
                    # breaker or operator kill switch). Before the facade existed
                    # this path raised on an HTTP error; a suppressed send is not
                    # an error, but it is also not a delivery, so it must not show
                    # a success toast.
                    sent = send_completion_check_sync(lid)
                    log_audit("send_completion_check", {"lead_id": lid, "sent": sent})
                    if sent:
                        st.success(T.get("check_sent", "Check sent!"))
                    else:
                        st.warning(
                            T.get(
                                "check_not_sent",
                                "Outbound is halted — the check was not sent.",
                            )
                        )
                except Exception as e:
                    st.error(f"Failed: {e}")

        # Edit Lead Form
        if can_edit(get_current_role()):
            with st.expander(T.get("edit_lead_btn", "Edit Lead")):
                with st.form(key=f"edit_lead_form_{k}"):
                    current_status = selected_lead.get("status", "new")
                    status_options = (
                        ALL_STATUSES
                        if current_status in ALL_STATUSES
                        else ALL_STATUSES + [current_status]
                    )
                    new_status = st.selectbox(
                        T.get("status_label", "Status"),
                        status_options,
                        index=status_options.index(current_status),
                        format_func=lambda x: T.get(x, x.capitalize()),
                    )
                    # PRO-163: a real, optional label for the customer.
                    # Prefilled from the *raw* `_display_name`, never from the
                    # composed `client` — that falls back to the customer's
                    # own name or the phone, so prefilling from it would save
                    # one of those back as an admin-authored name on the first
                    # submit. pd.isna, not `or`: a missing key reindexes to
                    # NaN, NaN is truthy, so `or ""` would put the literal
                    # "nan" in the box and then save it.
                    raw_name = selected_lead.get("_display_name")
                    current_name = (
                        "" if raw_name is None or pd.isna(raw_name) else str(raw_name)
                    )
                    new_client_name = st.text_input(
                        T.get("client_name_label", "שם לקוח"),
                        value=current_name,
                        max_chars=40,
                        # Describes the fallback rather than restating the
                        # number: a greyed phone number inside an empty box
                        # reads as content, and the number is shown just below.
                        placeholder=T.get(
                            "client_name_placeholder",
                            "לא הוגדר שם — יוצג השם מהשיחה או הטלפון",
                        ),
                        help=T.get(
                            "client_name_help",
                            "שם לתצוגה בפאנל בלבד.",
                        ),
                    )
                    # Read-only by decision, not by omission (PRO-163): the
                    # chat_id is the lead's identity and the key the FSM, the
                    # Redis context and wa_delivery are all keyed by. Re-keying
                    # a conversation is a migration, not a text input.
                    #
                    # Static text rather than a disabled input: a disabled
                    # <input> cannot be selected in Chrome, so the operator
                    # could not copy the number they are being shown in order
                    # to dial it. st.code carries a copy button, and nothing
                    # about it invites typing — which documents "read-only"
                    # more honestly than a greyed box with a hover tooltip.
                    st.caption(T.get("phone_number_label", "מספר טלפון"))
                    st.code(
                        strip_suffix(selected_lead.get("_chat_id", "")),
                        language=None,
                    )
                    st.caption(
                        T.get(
                            "phone_readonly_help",
                            "מספר זה הוא מזהה השיחה ואינו ניתן לעריכה.",
                        )
                    )
                    current_details = str(selected_lead.get("details_summary") or "")
                    new_details = st.text_area(
                        T.get("details_label", "פרטי הבקשה"),
                        value=current_details,
                    )

                    unassigned_label = T["unknown_pro"]
                    pro_names = [unassigned_label] + list(pro_map_name_to_id.keys())
                    current_pro_name = selected_lead.get(
                        "professional", unassigned_label
                    )
                    if current_pro_name not in pro_names:
                        pro_names.append(current_pro_name)
                    new_pro = st.selectbox(
                        T.get("professional_label", "Professional"),
                        pro_names,
                        index=pro_names.index(current_pro_name),
                    )

                    if st.form_submit_button(T.get("save_changes_btn", "Save Changes")):
                        # PRO-161: these used to be the *editor's column names*
                        # — `client`, `phone_number`, `details_summary`,
                        # `professional` — none of which any reader looks for
                        # on a lead (`phone_number` is a pros field). Only
                        # `status` and `pro_id` ever took effect, so an admin
                        # retyping the details got a success and no change.
                        # Harmless while the confirmation was being discarded
                        # by the rerun; this PR makes it a toast, so it gets
                        # fixed rather than amplified. Writes the same pair the
                        # table editor writes (`lead_queries._build_update_payload`).
                        # PRO-163: built by a streamlit-free helper so the
                        # payload has a real test — this form has already
                        # shipped one that wrote editor column names onto the
                        # lead and discarded every field but `status`.
                        # Unassigning clears pro_id so matching/healer/pro-flow
                        # stop treating the lead as owned, via the localized
                        # T["unknown_pro"] sentinel every other view uses.
                        update_data, unset_data = build_edit_form_payload(
                            status=new_status,
                            details=new_details,
                            # The box is prefilled with the *composed*
                            # summary, so an untouched submit used to stamp
                            # "<issue> | <time> | <address>" into the
                            # pro-facing `issue_type` on every save (PRO-163).
                            details_touched=(new_details != current_details),
                            display_name=new_client_name,
                            pro_name=new_pro,
                            pro_map_name_to_id=pro_map_name_to_id,
                            unknown_pro_label=unassigned_label,
                        )

                        update_op = {"$set": update_data}
                        if unset_data:
                            update_op["$unset"] = unset_data
                        if new_status != selected_lead.get("status"):
                            update_op["$push"] = {
                                "status_history": status_history_entry(
                                    new_status, Actor.ADMIN
                                )
                            }
                        res = leads_collection.update_one(
                            {"_id": ObjectId(lid)}, update_op
                        )
                        if getattr(res, "matched_count", 0) == 0:
                            # The lead was deleted inside the 30s cache
                            # window: nothing was written, so neither the
                            # audit entry nor the success toast may claim
                            # otherwise. The table path already refuses this
                            # (SKIP_LEAD_GONE); this one used to report
                            # "1 saved" over a no-op, and PRO-163 adds a
                            # $unset to the same call.
                            st.error(
                                T.get(
                                    "lead_no_longer_listed",
                                    "That lead is no longer listed.",
                                )
                            )
                            st.stop()
                        # Which fields, not just that an edit happened — a
                        # display_name is operator-authored content about a
                        # customer. Names only; values stay out of the log.
                        log_audit(
                            "edit_lead",
                            {
                                "lead_id": lid,
                                "fields": sorted(list(update_data) + list(unset_data)),
                            },
                        )
                        # PRO-161: the st.success here was discarded by the
                        # rerun below — the admin saw a flicker and nothing
                        # else. Route it through the same flash the table Save
                        # uses so one lead-editing path isn't silently worse
                        # than the other.
                        st.session_state["leads_flash"] = {
                            "updated": 1,
                            "skipped": 0,
                            "skipped_rows": [],
                        }
                        st.cache_data.clear()
                        st.rerun()

        # Delete confirmation
        if st.session_state.get(f"confirm_delete_{k}"):
            st.warning(T.get("confirm_delete", "Are you sure?"))
            cy, cn = st.columns(2)
            if cy.button(T["confirm_yes"], key=f"yes_del_{k}"):
                leads_collection.delete_one({"_id": ObjectId(lid)})
                log_audit("delete_lead", {"lead_id": lid})
                logger.info(f"Admin deleted lead {lid}")
                # PRO-161: an irreversible delete whose only confirmation was
                # thrown away by the rerun — the admin's sole evidence was a
                # row vanishing from a 100-row table. Same flash as everything
                # else that mutates here.
                st.session_state["leads_flash"] = {"deleted": lid}
                del st.session_state[f"confirm_delete_{k}"]
                st.cache_data.clear()
                st.rerun()
            if cn.button(T["confirm_no"], key=f"no_del_{k}"):
                del st.session_state[f"confirm_delete_{k}"]
                st.rerun()

    with c2:
        # Chat History
        chat_id = selected_lead["_chat_id"]
        msgs = list(messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1))

        with st.expander(f"{T['chat_history']} ({len(msgs)})", expanded=True):
            if msgs:
                with st.container(height=350, border=False):
                    html_chat = "".join(
                        render_chat_bubble(m["text"], m["role"], m.get("timestamp"), T)
                        for m in msgs
                    )
                    st.markdown(
                        f'<div class="chat-container">{html_chat}</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.info(T["no_chat"])
