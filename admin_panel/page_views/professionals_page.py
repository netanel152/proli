import streamlit as st
from bson.objectid import ObjectId
from datetime import datetime, timezone
from admin_panel.utils import users_collection, slots_collection, create_initial_schedule, generate_system_prompt
import re

# Constants for Prompt Markers
PROMPT_AREA_MARKER_HE = "אזורי שירות"
PROMPT_AREA_MARKER_EN = "Service Areas"

@st.cache_data(ttl=60)
def get_professionals():
    """Cached function to fetch all professionals."""
    return list(users_collection.find())

def view_professionals(T):
    st.title(T["pros_title"])
    st.caption(T.get("page_desc_professionals", "Manage the list of professionals, add new ones, edit their details, and set their availability."))

    if 'pro_view_mode' not in st.session_state:
        st.session_state.pro_view_mode = 'list'
    if 'pro_to_edit' not in st.session_state:
        st.session_state.pro_to_edit = None

    if st.session_state.pro_view_mode == 'list':
        render_pro_list(T)
    elif st.session_state.pro_view_mode == 'add':
        render_pro_form(T)
    elif st.session_state.pro_view_mode == 'edit':
        render_pro_form(T, pro_data=st.session_state.pro_to_edit)

def render_pro_list(T):
    """Renders the list of professionals with editing and deletion capabilities."""
    
    c1, c2, c3 = st.columns([2, 2, 1])
    pros = get_professionals()
    
    with c1:
        st.metric(T["metric_pros"], len(pros))
    with c2:
        st.metric(T["status_active"], len([p for p in pros if p.get("is_active", True)]))
    with c3:
        if st.button(f"＋ {T.get('action_add_new', 'Add New')}", type="primary", width='stretch'):
            st.session_state.pro_view_mode = 'add'
            st.rerun()
            
    st.markdown("---")

    for p in pros:
        pro_id = str(p["_id"])
        is_active = p.get('is_active', True)
        status_icon = "🟢" if is_active else "🔴"
        
        with st.container(border=True):
            c_info, c_actions = st.columns([4, 1])
            with c_info:
                st.subheader(f"{status_icon} {p['business_name']}")
                st.caption(f"{T.get(f'type_{p.get("type", "general")}', p.get('type', 'general')).capitalize()} | {p.get('phone_number')}")
                st.write(f"**{T['new_areas']}:** {', '.join(p.get('service_areas', []))}")
            
            with c_actions:
                st.write(" ") # Spacer
                if st.button(T.get("edit_btn", "Edit"), key=f"edit_{pro_id}", width='stretch'):
                    st.session_state.pro_view_mode = 'edit'
                    st.session_state.pro_to_edit = p
                    st.rerun()
                
                if st.button(T.get("delete_btn", "Delete"), key=f"del_{pro_id}", width='stretch', type="secondary"):
                    st.session_state[f"confirm_del_{pro_id}"] = True
            
            if st.session_state.get(f"confirm_del_{pro_id}"):
                st.warning(T.get("confirm_delete_pro", "Are you sure you want to delete this professional? This is irreversible."))
                c_yes, c_no, c_spacer = st.columns([1, 1, 4])
                if c_yes.button(T.get("confirm_yes", "Yes, Delete"), key=f"yes_del_{pro_id}"):
                    slots_collection.delete_many({"pro_id": p["_id"]})
                    users_collection.delete_one({"_id": p["_id"]})
                    del st.session_state[f"confirm_del_{pro_id}"]
                    st.cache_data.clear() # Clear cache after deletion
                    st.rerun()
                if c_no.button(T.get("confirm_no", "No"), key=f"no_del_{pro_id}"):
                    del st.session_state[f"confirm_del_{pro_id}"]
                    st.rerun()

def render_pro_form(T, pro_data=None):
    """Renders the form for adding or editing a professional."""
    is_edit = pro_data is not None
    pro_id = str(pro_data["_id"]) if is_edit else None
    
    header = T["add_pro_title"] if not is_edit else f"{T.get('edit_pro_title', 'Edit Professional')}: {pro_data.get('business_name')}"
    st.header(header)

    if st.button(f"← {T.get('back_to_list', 'Back to list')}"):
        st.session_state.pro_view_mode = 'list'
        st.session_state.pro_to_edit = None
        st.rerun()

    with st.form(key=f"pro_form_{pro_id or 'new'}"):
        c1, c2 = st.columns(2)
        with c1:
            name = st.text_input(T["new_name"], value=pro_data.get("business_name", "") if is_edit else "")
            phone = st.text_input(T["new_phone"], value=pro_data.get("phone_number", "") if is_edit else "")
        with c2:
            ptype_options = ["plumber", "electrician", "handyman", "locksmith", "painter", "cleaner", "general"]
            default_type = pro_data.get("type", "general") if is_edit else "general"
            ptype_index = ptype_options.index(default_type) if default_type in ptype_options else ptype_options.index("general")
            ptype = st.selectbox(T["new_type"], ptype_options, index=ptype_index, format_func=lambda x: T.get(f"type_{x}", x.capitalize()))
            active = st.checkbox(T["active"], value=pro_data.get("is_active", True) if is_edit else True)

        st.text_input(T["new_areas"], value=", ".join(pro_data.get("service_areas", [])) if is_edit else "", key="pro_areas", help=T.get("areas_help", "Comma-separated list of cities"))
        st.text_area(T["new_prices"], height=100, help=T.get("prices_help", "Optional. One item per line, e.g., 'Service call: $100'"), value=pro_data.get("prices_for_prompt", "") if is_edit else "", key="pro_prices")
        
        st.subheader(T.get("ai_settings_title", "AI Settings"))
        st.text_area(T["prompt_title"], value=pro_data.get("system_prompt", "") if is_edit else "", height=250, key="pro_prompt", help=T.get("prompt_help", "Service areas will be added automatically from the list above."))
        st.text_input(T["keywords"], value=", ".join(pro_data.get("keywords", [])) if is_edit else "", key="pro_keywords")

        submit_label = T["btn_create"] if not is_edit else T["save_btn"]
        if st.form_submit_button(submit_label, type="primary"):
            if name and phone and st.session_state.pro_areas:
                
                # Auto-update service areas in the prompt
                current_prompt = st.session_state.pro_prompt
                service_areas_str = st.session_state.pro_areas
                areas_line = f"{PROMPT_AREA_MARKER_HE}: {service_areas_str}"
                
                # Regex to find and replace the service areas line in Hebrew or English
                # Use the constants in the regex
                regex_pattern = f"({PROMPT_AREA_MARKER_HE}|{PROMPT_AREA_MARKER_EN}):.*"
                if re.search(regex_pattern, current_prompt, re.IGNORECASE):
                    updated_prompt = re.sub(regex_pattern, areas_line, current_prompt, flags=re.IGNORECASE)
                else:
                    updated_prompt = current_prompt + "\n" + areas_line
                
                # If the prompt was empty, generate a new one
                if not updated_prompt.strip():
                    updated_prompt, final_keywords = generate_system_prompt(name, ptype, service_areas_str, st.session_state.pro_prices)
                else:
                    _, final_keywords = generate_system_prompt(name, ptype, service_areas_str, st.session_state.pro_prices)

                pro_payload = {
                    "business_name": name, "phone_number": phone, "type": ptype, "is_active": active,
                    "service_areas": [x.strip() for x in service_areas_str.split(",")],
                    "prices_for_prompt": st.session_state.pro_prices,
                    "system_prompt": updated_prompt,
                    "keywords": [x.strip() for x in st.session_state.pro_keywords.split(",")] if st.session_state.pro_keywords else final_keywords,
                }

                if is_edit:
                    users_collection.update_one({"_id": ObjectId(pro_id)}, {"$set": pro_payload})
                    st.success(T["success_update"])
                else:
                    pro_payload.update({
                        "plan": "basic", "created_at": datetime.now(timezone.utc),
                        "social_proof": {"rating": 5.0}, "is_verified": True
                    })
                    res = users_collection.insert_one(pro_payload)
                    create_initial_schedule(res.inserted_id)
                    st.success(T["success_create"])
                
                st.session_state.pro_view_mode = 'list'
                st.session_state.pro_to_edit = None
                st.rerun()
            else:
                st.error(T["error_fill_fields"])