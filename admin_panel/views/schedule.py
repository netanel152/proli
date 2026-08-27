import streamlit as st
import pandas as pd
from datetime import datetime, timedelta, time
from admin_panel.core.utils import users_collection, slots_collection
from admin_panel.core.schedule_queries import EDITOR_COLUMNS, save_daily_schedule
from app.core.config import settings
import pytz


@st.cache_data(ttl=60)
def get_active_professionals():
    return list(users_collection.find({"is_active": True}))


def view_schedule_editor(T):
    st.title(T["title_schedule"])
    st.caption(
        T.get(
            "page_desc_schedule",
            "Edit the daily and weekly work schedule for each professional.",
        )
    )

    pros = get_active_professionals()
    if not pros:
        st.warning(
            T.get("no_active_pros_for_schedule", "No active professionals found.")
        )
        st.stop()

    pro_map = {p["business_name"]: p for p in pros}
    selected_pro_name = st.selectbox(T["sch_select_pro"], list(pro_map.keys()))

    if selected_pro_name:
        pro = pro_map[selected_pro_name]
        tz = pytz.timezone(settings.TIMEZONE)

        tab_daily, tab_bulk, tab_template = st.tabs(
            [T["tab_daily"], T["tab_bulk"], T.get("tab_template", "Weekly Template")]
        )

        # --- TAB 1: DAILY EDITOR ---
        with tab_daily:
            st.subheader(T["tab_daily"])

            # PRO-158: st.success followed by st.rerun() is discarded with the
            # element tree, so confirmations were never actually readable.
            # Stash the counts across the rerun and render them here instead.
            flash = st.session_state.pop("sch_flash", None)
            if flash:
                st.success(
                    f"{T.get('sch_success', 'Schedule updated!')} "
                    f"(+{flash['inserted']} / ~{flash['updated']} / -{flash['deleted']})"
                )
                if flash.get("skipped"):
                    # A partial save destroys the skipped rows (the editor
                    # resets from DB on rerun) — say so next to the success,
                    # or the admin never learns a row was lost.
                    st.warning(
                        T.get(
                            "sch_msg_rows_skipped",
                            "{n} row(s) skipped — missing or invalid time.",
                        ).format(n=flash["skipped"])
                    )

            selected_date = st.date_input(T["sch_date"], value=datetime.now(tz).date())

            day_start_local = tz.localize(datetime.combine(selected_date, time.min))
            day_end_local = tz.localize(datetime.combine(selected_date, time.max))
            day_start_utc = day_start_local.astimezone(pytz.utc)
            day_end_utc = day_end_local.astimezone(pytz.utc)

            slots_cursor = slots_collection.find(
                {
                    "pro_id": pro["_id"],
                    "start_time": {"$gte": day_start_utc, "$lte": day_end_utc},
                }
            ).sort("start_time", 1)

            original_slots = list(slots_cursor)
            original_ids = {str(s["_id"]) for s in original_slots}

            editor_data = []
            for s in original_slots:
                local_start = s["start_time"].replace(tzinfo=pytz.utc).astimezone(tz)
                local_end = s["end_time"].replace(tzinfo=pytz.utc).astimezone(tz)
                editor_data.append(
                    {
                        "_id": str(s["_id"]),
                        "start_time": local_start.time(),
                        "end_time": local_end.time(),
                        "is_taken": s["is_taken"],
                    }
                )

            if not original_slots:
                # PRO-158: an empty grid with bare headers reads as "loading",
                # not "this day is free — add a row". Say it, and point at the
                # Bulk tab (filling a week by hand is ~6 clicks per slot).
                st.info(T.get("sch_msg_no_slots_today", T.get("no_slots", "")))

            # PRO-158: explicit schema — pd.DataFrame([]) has ZERO columns
            # (column_config styles columns, it does not create them), so on a
            # day with no slots yet the Save handler crashed on edited_df["_id"]
            # and the first slot of a date could never be created.
            df = pd.DataFrame(editor_data, columns=EDITOR_COLUMNS)

            edited_df = st.data_editor(
                df,
                column_config={
                    "_id": None,
                    "start_time": st.column_config.TimeColumn(
                        T.get("sch_start_time", "Start"), format="HH:mm", step=60
                    ),
                    "end_time": st.column_config.TimeColumn(
                        T.get("sch_end_time", "End"), format="HH:mm", step=60
                    ),
                    "is_taken": st.column_config.CheckboxColumn(
                        T.get("sch_taken", "Taken"), default=False
                    ),
                },
                num_rows="dynamic",
                use_container_width=True,
                # Belt-and-braces: Streamlit already hashes the frame's arrow
                # bytes into the widget id, so any real change resets the
                # editor. The suffix only covers hypothetical equal-bytes cases.
                key=f"editor_{pro['_id']}_{selected_date}_{len(original_ids)}",
            )

            if st.button(T.get("sch_save", "Save"), type="primary"):
                # PRO-158: the delete/update/insert routine lives in
                # admin_panel/core/schedule_queries.py (streamlit-free,
                # collection injected) so the empty-day path has a real test.
                result = save_daily_schedule(
                    slots_collection,
                    edited_df,
                    original_ids,
                    pro["_id"],
                    selected_date,
                    tz,
                )

                if result["deleted"] or result["updated"] or result["inserted"]:
                    st.session_state["sch_flash"] = result
                    st.rerun()
                else:
                    # Nothing was written — success here would repeat the exact
                    # lie this ticket exists to kill. No rerun: the warning and
                    # the admin's typed rows stay on screen for a second try.
                    st.warning(
                        T.get(
                            "sch_msg_nothing_saved",
                            "Nothing was saved. Make sure every row has both "
                            "a start and an end time.",
                        )
                    )

        # --- TAB 2: BULK GENERATOR ---
        with tab_bulk:
            st.subheader(T["sch_config_title"])

            with st.container(border=True):
                c1, c2 = st.columns(2)
                with c1:
                    start_date = st.date_input(
                        T["sch_start_date"],
                        value=datetime.now().date() + timedelta(days=1),
                    )
                    start_hour = st.number_input(T["sch_start_hour"], 0, 23, 8)
                    duration = st.number_input(T["sch_duration"], 15, 120, 60, step=15)
                with c2:
                    end_date = st.date_input(
                        T["sch_end_date"],
                        value=datetime.now().date() + timedelta(days=7),
                    )
                    end_hour = st.number_input(T["sch_end_hour"], 0, 23, 18)

                weekdays_opts = [6, 0, 1, 2, 3, 4, 5]
                selected_days = st.multiselect(
                    T["sch_workdays"],
                    weekdays_opts,
                    default=[0, 1, 2, 3, 4],
                    format_func=lambda x: T.get(
                        f"day_{x}", ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][x]
                    ),
                )

                st.markdown("")
                c_gen, c_clear = st.columns(2)

                if c_gen.button(
                    T["sch_gen_btn"], type="primary", use_container_width=True
                ):
                    new_slots = []
                    curr_d = start_date
                    while curr_d <= end_date:
                        if curr_d.weekday() in selected_days:
                            try:
                                day_start = tz.localize(
                                    datetime.combine(curr_d, time(start_hour, 0))
                                )
                                day_end = tz.localize(
                                    datetime.combine(curr_d, time(end_hour, 0))
                                )
                            except (
                                pytz.exceptions.AmbiguousTimeError,
                                pytz.exceptions.NonExistentTimeError,
                            ):
                                curr_d += timedelta(days=1)
                                continue

                            curr_slot = day_start
                            while curr_slot + timedelta(minutes=duration) <= day_end:
                                slot_end = curr_slot + timedelta(minutes=duration)
                                new_slots.append(
                                    {
                                        "pro_id": pro["_id"],
                                        "start_time": curr_slot.astimezone(pytz.utc),
                                        "end_time": slot_end.astimezone(pytz.utc),
                                        "is_taken": False,
                                    }
                                )
                                curr_slot = slot_end
                        curr_d += timedelta(days=1)

                    if new_slots:
                        slots_collection.insert_many(new_slots)
                        st.success(f"{T['sch_msg_generated']} ({len(new_slots)})")
                        st.rerun()
                    else:
                        st.warning(T.get("sch_msg_no_slots", "No slots generated."))

                if c_clear.button(
                    T["sch_clear_btn"], type="secondary", use_container_width=True
                ):
                    st.session_state.confirm_clear_slots = True

                if st.session_state.get("confirm_clear_slots"):
                    st.warning(
                        T.get(
                            "confirm_clear_slots_body",
                            "Delete all future non-booked slots in range?",
                        )
                    )
                    cy, cn, _ = st.columns([1, 1, 4])
                    if cy.button(T["confirm_yes"], key="confirm_clear_yes"):
                        res = slots_collection.delete_many(
                            {
                                "pro_id": pro["_id"],
                                "is_taken": False,
                                "start_time": {
                                    "$gte": tz.localize(
                                        datetime.combine(start_date, time(0, 0))
                                    ).astimezone(pytz.utc),
                                    "$lte": tz.localize(
                                        datetime.combine(end_date, time(23, 59))
                                    ).astimezone(pytz.utc),
                                },
                            }
                        )
                        st.success(f"{T['sch_msg_cleared']} ({res.deleted_count})")
                        del st.session_state.confirm_clear_slots
                        st.rerun()
                    if cn.button(T["confirm_no"], key="confirm_clear_no"):
                        del st.session_state.confirm_clear_slots
                        st.rerun()

        # --- TAB 3: WEEKLY TEMPLATE ---
        with tab_template:
            st.subheader(T.get("template_title", "Recurring Weekly Schedule"))
            st.caption(
                T.get(
                    "template_desc",
                    "Set a recurring weekly template. Slots will be auto-generated every Sunday for the next 2 weeks.",
                )
            )

            existing_template = pro.get("schedule_template", {})
            slot_duration = existing_template.get("slot_duration_minutes", 60)

            day_names = [
                "sunday",
                "monday",
                "tuesday",
                "wednesday",
                "thursday",
                "friday",
                "saturday",
            ]
            day_labels = {
                "sunday": T.get("day_6", "Sun"),
                "monday": T.get("day_0", "Mon"),
                "tuesday": T.get("day_1", "Tue"),
                "wednesday": T.get("day_2", "Wed"),
                "thursday": T.get("day_3", "Thu"),
                "friday": T.get("day_4", "Fri"),
                "saturday": T.get("day_5", "Sat"),
            }

            slot_dur = st.number_input(
                T.get("template_slot_dur", "Slot Duration (minutes)"),
                min_value=15,
                max_value=120,
                value=slot_duration,
                step=15,
                key="template_duration",
            )

            template_data = {}
            for day in day_names:
                day_config = existing_template.get(
                    day,
                    {
                        "start": "08:00",
                        "end": "18:00",
                        "enabled": day not in ["friday", "saturday"],
                    },
                )

                with st.container(border=True):
                    c_check, c_start, c_end = st.columns([1, 2, 2])

                    enabled = c_check.checkbox(
                        day_labels.get(day, day.capitalize()),
                        value=day_config.get("enabled", False),
                        key=f"tmpl_en_{day}",
                    )

                    try:
                        start_t = datetime.strptime(
                            day_config.get("start", "08:00"), "%H:%M"
                        ).time()
                        end_t = datetime.strptime(
                            day_config.get("end", "18:00"), "%H:%M"
                        ).time()
                    except ValueError:
                        start_t = time(8, 0)
                        end_t = time(18, 0)

                    start_val = c_start.time_input(
                        T.get("sch_start_time", "Start"),
                        value=start_t,
                        key=f"tmpl_s_{day}",
                    )
                    end_val = c_end.time_input(
                        T.get("sch_end_time", "End"),
                        value=end_t,
                        key=f"tmpl_e_{day}",
                    )

                    template_data[day] = {
                        "enabled": enabled,
                        "start": start_val.strftime("%H:%M"),
                        "end": end_val.strftime("%H:%M"),
                    }

            if st.button(
                T.get("template_save", "Save Template"),
                type="primary",
                key="save_template",
            ):
                template_data["slot_duration_minutes"] = slot_dur
                users_collection.update_one(
                    {"_id": pro["_id"]},
                    {"$set": {"schedule_template": template_data}},
                )
                st.success(T.get("template_saved", "Template saved!"))
