import streamlit as st
from bson.objectid import ObjectId
from datetime import datetime, timezone
from admin_panel.core.utils import (
    users_collection,
    leads_collection,
    slots_collection,
    create_initial_schedule,
    generate_system_prompt,
)
from admin_panel.core.auth import log_audit, get_current_role
from admin_panel.core.labels import PROFESSION_TYPES, profession_label
from admin_panel.ui.components import render_flash, set_flash
from admin_panel.core.rbac import can_edit, has_permission
from app.services.geocoding_service import (
    ServiceAreaResolution,
    parse_service_areas,
    resolve_service_areas_sync,
)
import re

# Constants for Prompt Markers
PROMPT_AREA_MARKER_HE = "אזורי שירות"
PROMPT_AREA_MARKER_EN = "Service Areas"


@st.cache_data(ttl=60)
def get_professionals():
    return list(users_collection.find({"pending_approval": {"$ne": True}}))


def view_professionals(T):
    st.title(T["pros_title"])
    st.caption(T.get("page_desc_professionals", "Manage the list of professionals."))
    # Confirmation of the previous run's create/edit/delete/approve/reject
    # (each ends in st.rerun(), which would otherwise discard it).
    render_flash("pro_flash")

    if "pro_view_mode" not in st.session_state:
        st.session_state.pro_view_mode = "list"
    if "pro_to_edit" not in st.session_state:
        st.session_state.pro_to_edit = None

    pending_count = users_collection.count_documents({"pending_approval": True})

    tab_list, tab_pending = st.tabs(
        [
            T.get("tab_professionals", "Professionals"),
            f"{T.get('tab_pending_approval', 'Pending Approval')} ({pending_count})",
        ]
    )

    with tab_list:
        if st.session_state.pro_view_mode == "list":
            render_pro_list(T)
        elif st.session_state.pro_view_mode == "add":
            render_pro_form(T)
        elif st.session_state.pro_view_mode == "edit":
            render_pro_form(T, pro_data=st.session_state.pro_to_edit)

    with tab_pending:
        render_pending_approvals(T)


def render_pro_list(T):
    pros = get_professionals()

    # Metrics row
    c1, c2, c3 = st.columns([2, 2, 1])
    with c1:
        st.metric(T["metric_pros"], len(pros))
    with c2:
        st.metric(
            T["status_active"], len([p for p in pros if p.get("is_active", True)])
        )
    with c3:
        if can_edit(get_current_role()):
            if st.button(
                T.get("action_add_new", "Add New"),
                type="primary",
                use_container_width=True,
            ):
                st.session_state.pro_view_mode = "add"
                st.rerun()

    st.markdown("")

    if not pros:
        st.info(T.get("no_pros", "No professionals found."))
        return

    for p in pros:
        pro_id = str(p["_id"])
        is_active = p.get("is_active", True)

        with st.container(border=True):
            c_img, c_info, c_actions = st.columns([1, 4, 1])

            with c_img:
                image_url = p.get("profile_image_url")
                if image_url:
                    st.markdown(
                        f'<img src="{image_url}" class="pro-circle-img">',
                        unsafe_allow_html=True,
                    )
                else:
                    st.markdown(
                        """
                        <div style="width:72px; height:72px; border-radius:50%; background:var(--bg-secondary); display:flex; align-items:center; justify-content:center; border:2px solid var(--border-color);">
                            <span class="material-symbols-rounded" style="font-size:36px; color:var(--text-muted);">person</span>
                        </div>
                    """,
                        unsafe_allow_html=True,
                    )

            with c_info:
                # Status + Name + Verified
                # Icon + text, never colour alone (PRO-61).
                status_txt = (
                    f"🟢 {T['status_active']}"
                    if is_active
                    else f"🔴 {T['status_inactive']}"
                )
                verified_badge = (
                    f" · ✅ {T['verified']}" if p.get("is_verified") else ""
                )
                st.markdown(f"**{p['business_name']}** · {status_txt}{verified_badge}")

                pro_type = profession_label(T, p.get("type", "general"))
                st.caption(f"{pro_type} · {p.get('phone_number', '')}")

                areas = ", ".join(p.get("service_areas", []))
                if areas:
                    st.markdown(
                        f"<span style='font-size:0.85rem; color:var(--text-secondary);'>{T['new_areas']}: {areas}</span>",
                        unsafe_allow_html=True,
                    )

            with c_actions:
                if st.button(
                    T.get("edit_btn", "Edit"),
                    key=f"edit_{pro_id}",
                    use_container_width=True,
                ):
                    st.session_state.pro_view_mode = "edit"
                    st.session_state.pro_to_edit = p
                    st.rerun()

                if has_permission(get_current_role(), "delete_pros"):
                    if st.button(
                        T.get("delete_btn", "Delete"),
                        key=f"del_{pro_id}",
                        type="secondary",
                        use_container_width=True,
                    ):
                        st.session_state[f"confirm_del_{pro_id}"] = True

            if st.session_state.get(f"confirm_del_{pro_id}"):
                st.warning(
                    T.get(
                        "confirm_delete_pro",
                        "Are you sure you want to delete this professional?",
                    )
                )
                c_yes, c_no, _ = st.columns([1, 1, 4])
                if c_yes.button(T.get("confirm_yes", "Yes"), key=f"yes_del_{pro_id}"):
                    # Cascade: unassign open leads so healer/janitor can re-route them
                    leads_collection.update_many(
                        {
                            "pro_id": p["_id"],
                            "status": {"$in": ["new", "contacted", "booked"]},
                        },
                        {"$set": {"pro_id": None}},
                    )
                    slots_collection.delete_many({"pro_id": p["_id"]})
                    users_collection.delete_one({"_id": p["_id"]})
                    log_audit(
                        "delete_pro", {"pro_id": pro_id, "name": p.get("business_name")}
                    )
                    set_flash(
                        "pro_flash",
                        T["pro_deleted"].replace(
                            "{name}", str(p.get("business_name") or T["unnamed_pro"])
                        ),
                    )
                    del st.session_state[f"confirm_del_{pro_id}"]
                    st.cache_data.clear()
                    st.rerun()
                if c_no.button(T.get("confirm_no", "No"), key=f"no_del_{pro_id}"):
                    del st.session_state[f"confirm_del_{pro_id}"]
                    st.rerun()


def render_pro_form(T, pro_data=None):
    is_edit = pro_data is not None
    pro_id = str(pro_data["_id"]) if is_edit else None

    header = (
        T["add_pro_title"]
        if not is_edit
        else f"{T.get('edit_pro_title', 'Edit Professional')}: {pro_data.get('business_name')}"
    )
    st.header(header)

    # The arrow lives in the translation: → is "back" in RTL, ← in LTR.
    if st.button(T["back_to_list"]):
        st.session_state.pro_view_mode = "list"
        st.session_state.pro_to_edit = None
        st.rerun()

    with st.form(key=f"pro_form_{pro_id or 'new'}"):
        # Basic Info Section
        st.markdown(f"##### {T.get('basic_info', 'Basic Information')}")
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(
                T["new_name"],
                value=pro_data.get("business_name", "") if is_edit else "",
            )
            phone = st.text_input(
                T["new_phone"],
                value=pro_data.get("phone_number", "") if is_edit else "",
            )
            license_number = st.text_input(
                T.get("license_number", "License / Business ID"),
                value=pro_data.get("license_number", "") if is_edit else "",
            )
        with c2:
            # PRO-61: the onboarding catalog's codes, so the panel cannot
            # offer a profession the bot cannot name (or vice versa).
            ptype_options = list(PROFESSION_TYPES)
            default_type = pro_data.get("type", "general") if is_edit else "general"
            ptype_index = (
                ptype_options.index(default_type)
                if default_type in ptype_options
                else ptype_options.index("general")
            )
            ptype = st.selectbox(
                T["new_type"],
                ptype_options,
                index=ptype_index,
                format_func=lambda x: profession_label(T, x),
            )

            c_active, c_verified = st.columns(2)
            with c_active:
                active = st.checkbox(
                    T["active"],
                    value=pro_data.get("is_active", True) if is_edit else True,
                )
            with c_verified:
                is_verified = st.checkbox(
                    T.get("verified", "Verified"),
                    value=pro_data.get("is_verified", False) if is_edit else False,
                )

        # Image Upload
        existing_image_url = pro_data.get("profile_image_url", "") if is_edit else ""
        if existing_image_url:
            st.image(
                existing_image_url, width=80, caption=T.get("current_image", "Current")
            )

        uploaded_file = st.file_uploader(
            T.get("upload_image", "Profile Image"), type=["png", "jpg", "jpeg"]
        )

        # Service Details
        st.markdown(f"##### {T.get('service_details', 'Service Details')}")
        st.text_input(
            T["new_areas"],
            value=", ".join(pro_data.get("service_areas", [])) if is_edit else "",
            key="pro_areas",
            help=T.get("areas_help", "Comma-separated list of cities"),
        )
        st.text_area(
            T["new_prices"],
            height=80,
            help=T.get("prices_help", "Optional."),
            value=pro_data.get("prices_for_prompt", "") if is_edit else "",
            key="pro_prices",
        )

        # AI Settings
        st.markdown(f"##### {T.get('ai_settings_title', 'AI Settings')}")
        st.text_area(
            T["prompt_title"],
            value=pro_data.get("system_prompt", "") if is_edit else "",
            height=200,
            key="pro_prompt",
            help=T.get("prompt_help", "Service areas will be added automatically."),
        )
        st.text_input(
            T["keywords"],
            value=", ".join(pro_data.get("keywords", [])) if is_edit else "",
            key="pro_keywords",
        )

        submit_label = T["btn_create"] if not is_edit else T["save_btn"]
        if st.form_submit_button(submit_label, type="primary"):
            if name and phone and st.session_state.pro_areas:

                # Handle Image Upload
                final_image_url = existing_image_url
                if uploaded_file:
                    try:
                        from app.services.cloudinary_client_service import upload_image

                        uploaded_url = upload_image(uploaded_file)
                        if uploaded_url:
                            final_image_url = uploaded_url
                        else:
                            st.error(T.get("upload_failed", "Failed to upload image."))
                            return
                    except ImportError:
                        st.error(
                            T.get("cloudinary_error", "Cloudinary module not found.")
                        )
                        return
                    except Exception as e:
                        st.error(f"{T.get('upload_error', 'Upload error')}: {e}")
                        return

                # Auto-update service areas in the prompt
                current_prompt = st.session_state.pro_prompt
                service_areas_str = st.session_state.pro_areas
                areas_line = f"{PROMPT_AREA_MARKER_HE}: {service_areas_str}"

                regex_pattern = f"({PROMPT_AREA_MARKER_HE}|{PROMPT_AREA_MARKER_EN}):.*"
                if re.search(regex_pattern, current_prompt, re.IGNORECASE):
                    updated_prompt = re.sub(
                        regex_pattern, areas_line, current_prompt, flags=re.IGNORECASE
                    )
                else:
                    updated_prompt = current_prompt + "\n" + areas_line

                if not updated_prompt.strip():
                    updated_prompt, final_keywords = generate_system_prompt(
                        name, ptype, service_areas_str, st.session_state.pro_prices
                    )
                else:
                    _, final_keywords = generate_system_prompt(
                        name, ptype, service_areas_str, st.session_state.pro_prices
                    )

                pro_payload = {
                    "business_name": name,
                    "phone_number": phone,
                    "type": ptype,
                    "is_active": active,
                    "is_verified": is_verified,
                    "license_number": license_number,
                    "profile_image_url": final_image_url,
                    "service_areas": [x.strip() for x in service_areas_str.split(",")],
                    "prices_for_prompt": st.session_state.pro_prices,
                    "system_prompt": updated_prompt,
                    "keywords": (
                        [x.strip() for x in st.session_state.pro_keywords.split(",")]
                        if st.session_state.pro_keywords
                        else final_keywords
                    ),
                }

                if is_edit:
                    users_collection.update_one(
                        {"_id": ObjectId(pro_id)}, {"$set": pro_payload}
                    )
                    log_audit("edit_pro", {"pro_id": pro_id, "name": name})
                    set_flash("pro_flash", T["success_update"])
                else:
                    pro_payload.update(
                        {
                            "role": "professional",
                            "plan": "basic",
                            "created_at": datetime.now(timezone.utc),
                            "social_proof": {"rating": 5.0, "review_count": 0},
                        }
                    )
                    res = users_collection.insert_one(pro_payload)
                    create_initial_schedule(res.inserted_id)
                    log_audit(
                        "create_pro", {"pro_id": str(res.inserted_id), "name": name}
                    )
                    set_flash("pro_flash", T["success_create"])

                st.session_state.pro_view_mode = "list"
                st.session_state.pro_to_edit = None
                st.rerun()
            else:
                st.error(T["error_fill_fields"])


# Session-state key for a message that must survive the `st.rerun()` an
# approval ends with — anything rendered before the rerun is discarded.
def _geo_check_key(pro_id: str) -> str:
    return f"geo_check_{pro_id}"


def render_pending_approvals(T):
    # No flash render here: `view_professionals` already renders "pro_flash"
    # at the top of the page, and this section is inside it.
    pending = list(
        users_collection.find({"pending_approval": True}).sort("created_at", -1)
    )

    if not pending:
        st.info(T.get("no_pending_pros", "No professionals pending approval."))
        return

    for p in pending:
        pro_id = str(p["_id"])
        pro_type_label = profession_label(T, p.get("type", "general"))

        with st.container(border=True):
            c_info, c_actions = st.columns([3, 1])

            with c_info:
                st.markdown(f"**🟡 {p.get('business_name') or T['unnamed_pro']}**")
                st.caption(f"{pro_type_label} · {p.get('phone_number', '')}")
                st.markdown(
                    f"<span style='font-size:0.85rem;'>{T.get('new_areas', 'Areas')}: {', '.join(p.get('service_areas', []))}</span>",
                    unsafe_allow_html=True,
                )
                if p.get("prices_for_prompt"):
                    st.caption(
                        f"{T.get('new_prices', 'Prices')}: {p['prices_for_prompt']}"
                    )
                if p.get("created_at"):
                    # LRM pins the date-then-time order under RTL bidi.
                    st.caption(
                        f"{T.get('registered_at', 'Registered')}: \u200e{p['created_at'].strftime('%Y-%m-%d %H:%M')}"
                    )

            with c_actions:
                if can_edit(get_current_role()):
                    if st.button(
                        T.get("approve_btn", "Approve"),
                        key=f"approve_{pro_id}",
                        type="primary",
                        use_container_width=True,
                    ):
                        try:
                            _check_then_approve(p, T)
                        except Exception as e:
                            st.error(T["error_approve_pro"].replace("{error}", str(e)))

                    if st.button(
                        T.get("reject_btn", "Reject"),
                        key=f"reject_{pro_id}",
                        type="secondary",
                        use_container_width=True,
                    ):
                        st.session_state[f"confirm_reject_{pro_id}"] = True

            if st.session_state.get(f"confirm_reject_{pro_id}"):
                st.warning(T.get("confirm_reject_pro", "Reject this professional?"))
                c_yes, c_no, _ = st.columns([1, 1, 4])
                if c_yes.button(
                    T.get("confirm_yes", "Yes"), key=f"yes_reject_{pro_id}"
                ):
                    users_collection.delete_one({"_id": p["_id"]})
                    log_audit(
                        "reject_pro", {"pro_id": pro_id, "name": p.get("business_name")}
                    )
                    _notify_pro_rejected(p.get("phone_number"))
                    set_flash(
                        "pro_flash",
                        T["pro_rejected"].replace(
                            "{name}", str(p.get("business_name") or T["unnamed_pro"])
                        ),
                    )
                    del st.session_state[f"confirm_reject_{pro_id}"]
                    st.session_state.pop(_geo_check_key(pro_id), None)
                    st.cache_data.clear()
                    st.rerun()
                if c_no.button(T.get("confirm_no", "No"), key=f"no_reject_{pro_id}"):
                    del st.session_state[f"confirm_reject_{pro_id}"]
                    st.rerun()

            check = st.session_state.get(_geo_check_key(pro_id))
            if check is not None:
                _render_service_area_correction(p, T, check)


def _check_then_approve(p: dict, T) -> None:
    """The approve click. Geocode every service area first: a pro is matched
    through `$geoNear` on `location`, so one nobody can place on the map is
    approved into silence — the 2026-04-18 failure class.

    * every area resolves → approve, write `location`.
    * geocoder down for some (`unavailable`) and nothing definitively wrong →
      approve anyway (a Google outage must not block the operator), write
      what resolved, flag the pro for a re-check.
    * any definitive miss → stop and show the correction box. "Approve
      anyway" is offered there only when at least one area did resolve.
    """
    pro_id = str(p["_id"])
    resolution = resolve_service_areas_sync(p.get("service_areas", []))
    if resolution.unresolved or resolution.blocks_approval:
        st.session_state[_geo_check_key(pro_id)] = resolution
        return
    _approve_pending_pro(p, T, resolution)


def _approve_pending_pro(p: dict, T, resolution: ServiceAreaResolution) -> None:
    pro_id = str(p["_id"])
    name = p.get("business_name", "")
    areas_str = ", ".join(p.get("service_areas", []))
    prompt, keywords = generate_system_prompt(
        name,
        p.get("type", "general"),
        areas_str,
        p.get("prices_for_prompt", ""),
    )
    update = resolution.mongo_update(
        now=datetime.now(timezone.utc), include_location=True
    )
    update["$set"].update(
        {
            "is_active": True,
            "pending_approval": False,
            "system_prompt": prompt,
            "keywords": keywords,
        }
    )
    users_collection.update_one({"_id": p["_id"]}, update)
    create_initial_schedule(p["_id"])
    log_audit(
        "approve_pro",
        {
            "pro_id": pro_id,
            "name": name,
            "service_areas_unresolved": list(resolution.unresolved),
            "service_areas_geocode_pending": resolution.needs_recheck,
        },
    )
    _notify_pro_approved(p.get("phone_number"))

    shown_name = name or T["unnamed_pro"]
    if resolution.unresolved:
        set_flash(
            "pro_flash",
            T["geo_approved_flagged"].format(
                name=shown_name, areas=", ".join(resolution.unresolved)
            ),
            "warning",
        )
    elif resolution.needs_recheck:
        set_flash(
            "pro_flash", T["geo_approved_recheck"].format(name=shown_name), "warning"
        )
    else:
        set_flash("pro_flash", T["approved_ok"].format(name=shown_name))
    st.session_state.pop(_geo_check_key(pro_id), None)
    st.cache_data.clear()
    st.rerun()


def _render_service_area_correction(
    p: dict, T, resolution: ServiceAreaResolution
) -> None:
    """The warning + fix-it box shown under a pending pro whose areas did not
    all resolve. Text-only, like every other admin mutation here."""
    pro_id = str(p["_id"])
    key = _geo_check_key(pro_id)

    if resolution.unresolved:
        st.warning(
            T["geo_unresolved_warning"].format(areas=", ".join(resolution.unresolved))
        )
    if resolution.unavailable:
        st.warning(
            T["geo_unavailable_warning"].format(areas=", ".join(resolution.unavailable))
        )
    if resolution.blocks_approval and not resolution.unresolved:
        st.warning(T["geo_no_areas"])
    if resolution.resolved:
        st.caption(
            T["geo_resolved_caption"].format(
                areas=", ".join(name for name, _ in resolution.resolved)
            )
        )

    edited = st.text_input(
        T["geo_fix_areas_label"],
        value=", ".join(p.get("service_areas", [])),
        key=f"geo_fix_{pro_id}",
        help=T["geo_fix_areas_help"],
    )
    c_save, c_anyway, c_cancel = st.columns([2, 2, 1])

    if c_save.button(T["geo_recheck_btn"], key=f"geo_recheck_{pro_id}"):
        areas = parse_service_areas(edited)
        users_collection.update_one(
            {"_id": p["_id"]}, {"$set": {"service_areas": areas}}
        )
        log_audit(
            "edit_pro_service_areas",
            {"pro_id": pro_id, "name": p.get("business_name"), "areas": areas},
        )
        p["service_areas"] = areas
        del st.session_state[key]
        try:
            _check_then_approve(p, T)
        except Exception as e:
            st.error(T["error_approve_pro"].replace("{error}", str(e)))
            return
        # Not approved — the re-check stashed a fresh verdict and the redrawn
        # warning is byte-identical to the one already on screen, so say that
        # the write landed. Without this the click is indistinguishable from a
        # no-op even though `service_areas` and an audit entry were committed.
        set_flash(
            "pro_flash",
            T["geo_areas_saved"].replace(
                "{name}", p.get("business_name") or T["unnamed_pro"]
            ),
            "warning",
        )
        st.rerun()

    # Offered only when something resolved: approving with no placeable area
    # would write no `location` — the silent pro this check exists to prevent.
    if resolution.resolved and c_anyway.button(
        T["geo_approve_anyway_btn"], key=f"geo_anyway_{pro_id}"
    ):
        _approve_pending_pro(p, T, resolution)

    if c_cancel.button(T["cancel_btn"], key=f"geo_cancel_{pro_id}"):
        del st.session_state[key]
        st.rerun()


def _notify_pro_approved(phone_number: str):
    # PRO-86: was a raw httpx.post straight at the legacy vendor, bypassing the circuit
    # breaker and kill switch entirely. Now goes through the one egress.
    if not phone_number:
        return
    from app.core.messages import Messages
    from app.core.phone import to_chat_id
    from app.providers.whatsapp.sync import send_text_sync

    send_text_sync(to_chat_id(phone_number), Messages.Onboarding.APPROVED_NOTIFICATION)


def _notify_pro_rejected(phone_number: str):
    # PRO-86: see _notify_pro_approved — same bypass, same fix.
    if not phone_number:
        return
    from app.core.messages import Messages
    from app.core.phone import to_chat_id
    from app.providers.whatsapp.sync import send_text_sync

    send_text_sync(to_chat_id(phone_number), Messages.Onboarding.REJECTED_NOTIFICATION)
