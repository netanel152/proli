import streamlit as st

def load_css(lang_code, T):
    direction = T["dir"]
    align = T["align"]
    
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700;900&display=swap');
        </style>
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
    <style>
        /* הגדרות פונט גלובליות */
        html, body, p, h1, h2, h3, h4, h5, h6, input, textarea, button, .stMarkdown, span {{
            font-family: 'Heebo', sans-serif !important;
            direction: {direction};
            text-align: {align};
        }}
        
        /* כותרות */
        h1 {{ font-size: 3rem !important; font-weight: 900 !important; opacity: 0.9; }}
        h2 {{ font-size: 2.2rem !important; font-weight: 700 !important; opacity: 0.8; }}
        
        /* כרטיסי מטריקות */
        div[data-testid="stMetric"] {{
            background-color: var(--secondary-background-color);
            border: 1px solid rgba(128, 128, 128, 0.2);
            padding: 20px;
            border-radius: 16px;
            text-align: center !important;
        }}
        div[data-testid="stMetricLabel"] p {{ font-size: 1.3rem !important; font-weight: 700 !important; color: var(--text-color) !important; }}
        div[data-testid="stMetricValue"] {{ font-size: 3.5rem !important; font-weight: 900 !important; color: #4F8BF9 !important; }}
        
        /* טבלאות */
        .stDataFrame {{ direction: {direction} !important; }}
        .stDataFrame th, .stDataFrame td {{ text-align: {align} !important; }}

        /* צ'אט */
        .chat-container {{ display: flex; flex-direction: column; gap: 15px; padding: 20px; background-color: rgba(0,0,0,0.02); border-radius: 12px; }}
        .chat-bubble {{ padding: 12px 18px; border-radius: 18px; max-width: 80%; font-size: 1.1rem; line-height: 1.5; box-shadow: 0 1px 2px rgba(0,0,0,0.1); position: relative; }}
        
        .user-msg {{ background-color: rgba(37, 99, 235, 0.15); color: var(--text-color); align-self: {'flex-start' if direction == 'rtl' else 'flex-end'}; margin-{'left' if direction == 'rtl' else 'right'}: auto; text-align: {align}; }}
        .bot-msg {{ background-color: var(--secondary-background-color); border: 1px solid rgba(128, 128, 128, 0.2); color: var(--text-color); align-self: {'flex-end' if direction == 'rtl' else 'flex-start'}; margin-{'right' if direction == 'rtl' else 'left'}: auto; text-align: {align}; }}
        
        .chat-meta {{ font-size: 0.85rem; font-weight: 700; margin-bottom: 6px; opacity: 0.6; display: block; }}
        
        /* כפתורים */
        .stButton button {{ font-size: 1.2rem !important; font-weight: 700; border-radius: 10px; }}
        
        /* Expander - שמירה על אייקונים */
        .streamlit-expanderHeader {{ font-family: 'Heebo', sans-serif !important; font-size: 1.2rem !important; font-weight: 600 !important; background-color: transparent !important; color: var(--text-color) !important; direction: {direction} !important; }}
        .streamlit-expanderHeader svg {{ font-family: 'Material Icons' !important; }}
    </style>
    """, unsafe_allow_html=True)

def render_chat_bubble(text, role, timestamp, T):
    is_user = role == 'user'
    cls = "user-msg" if is_user else "bot-msg"
    name = T["role_user"] if is_user else T["role_bot"]
    icon = "👤" if is_user else "🤖"
    time_str = timestamp.strftime("%H:%M")
    return f"<div class='chat-bubble {cls}'><span class='chat-meta'>{icon} {name} • {time_str}</span>{text}</div>"