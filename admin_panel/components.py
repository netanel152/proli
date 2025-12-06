import streamlit as st

def load_css(lang_code, T):
    direction = T["dir"]
    align = T["align"]
    
    st.markdown("""
        <style>
        @import url('https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;700;900&display=swap');
        
        :root {
            --bg-color: #ffffff;
            --text-color: #0F172A;
            --heading-color-primary: #1E3A8A;
            --heading-color-secondary: #2563EB;
            --card-bg: #FFFFFF;
            --card-border: #E5E7EB;
            --metric-label: #6B7280;
            --metric-value: #2563EB;
            --sidebar-bg: #F1F5F9;
            --sidebar-border: #E2E8F0;
            --chat-user-bg: #EFF6FF;
            --chat-user-border: #BFDBFE;
            --chat-user-text: #1E40AF;
            --chat-bot-bg: #FFFFFF;
            --chat-bot-border: #E5E7EB;
            --chat-bot-text: #374151;
            --input-bg: #FFFFFF;
            --input-border: #D1D5DB;
            --tab-bg: #F3F4F6;
            --tab-active-bg: #FFFFFF;
            --tab-active-text: #2563EB;
            --table-header-bg: #F3F4F6;
            --table-header-text: #374151;
            --table-row-text: #1F2937;
        }

        /* Dark Mode Overrides */
        @media (prefers-color-scheme: dark) {
            :root {
                --bg-color: #0E1117;
                --text-color: #E2E8F0;
                --heading-color-primary: #60A5FA;
                --heading-color-secondary: #3B82F6;
                --card-bg: #1E293B;
                --card-border: #334155;
                --metric-label: #94A3B8;
                --metric-value: #60A5FA;
                --sidebar-bg: #1E293B; /* Matches card bg for seamless look */
                --sidebar-border: #334155;
                --chat-user-bg: #172554;
                --chat-user-border: #1E3A8A;
                --chat-user-text: #BFDBFE;
                --chat-bot-bg: #1E293B;
                --chat-bot-border: #334155;
                --chat-bot-text: #E2E8F0;
                --input-bg: #1E293B;
                --input-border: #475569;
                --tab-bg: #1E293B;
                --tab-active-bg: #0E1117;
                --tab-active-text: #60A5FA;
                --table-header-bg: #1E293B;
                --table-header-text: #E2E8F0;
                --table-row-text: #CBD5E1;
            }
            /* Fix Streamlit specific elements in dark mode */
            .stApp {
                background-color: var(--bg-color);
                color: var(--text-color);
            }
            .stMarkdown, p, span, div {
                color: var(--text-color);
            }
        }
        
        </style>
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
    <style>
        /* הגדרות פונט גלובליות */
        html, body, p, h1, h2, h3, h4, h5, h6, input, textarea, button, .stMarkdown, span, div {{
            font-family: 'Heebo', sans-serif !important;
            direction: {direction};
            text-align: {align};
        }}
        
        /* כותרות */
        h1 {{ font-size: 3.5rem !important; font-weight: 900 !important; opacity: 1; color: var(--heading-color-primary); padding-bottom: 0.5rem; }}
        h2 {{ font-size: 2.5rem !important; font-weight: 800 !important; opacity: 0.9; color: var(--heading-color-secondary); }}
        h3 {{ font-size: 1.8rem !important; font-weight: 700 !important; opacity: 0.85; margin-top: 1.5rem !important; color: var(--text-color); }}
        
        /* טקסט כללי */
        p, label, .stSelectbox, .stTextInput, .stDateInput, .stTimeInput, .stNumberInput {{
            font-size: 1.2rem !important;
            color: var(--text-color) !important;
        }}

        /* כרטיסי מטריקות */
        div[data-testid="stMetric"] {{
            background-color: var(--card-bg);
            border: 1px solid var(--card-border);
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
            padding: 24px;
            border-radius: 16px;
            text-align: center !important;
            transition: transform 0.2s;
        }}
        div[data-testid="stMetric"]:hover {{
            transform: translateY(-5px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
        }}
        div[data-testid="stMetricLabel"] p {{ font-size: 1.4rem !important; font-weight: 600 !important; color: var(--metric-label) !important; }}
        div[data-testid="stMetricValue"] {{ font-size: 3.8rem !important; font-weight: 900 !important; color: var(--metric-value) !important; }}
        
        /* טבלאות */
        .stDataFrame {{ direction: {direction} !important; width: 100%; }}
        .stDataFrame th {{ text-align: {align} !important; font-size: 1.2rem !important; font-weight: 700 !important; background-color: var(--table-header-bg); color: var(--table-header-text) !important; }}
        .stDataFrame td {{ text-align: {align} !important; font-size: 1.1rem !important; color: var(--table-row-text) !important; direction: {direction} !important; unicode-bidi: embed; }}
        
        /* Tabs */
        .stTabs [data-baseweb="tab-list"] {{ gap: 10px; }}
        .stTabs [data-baseweb="tab"] {{
            height: 50px;
            white-space: pre-wrap;
            background-color: var(--tab-bg);
            color: var(--text-color);
            border-radius: 8px 8px 0 0;
            gap: 1px;
            padding: 0px 20px;
            font-size: 1.2rem !important;
            font-weight: 600;
        }}
        .stTabs [aria-selected="true"] {{
            background-color: var(--tab-active-bg);
            color: var(--tab-active-text);
            border-top: 3px solid var(--tab-active-text);
        }}

        /* צ'אט */
        .chat-container {{ display: flex; flex-direction: column; gap: 15px; padding: 20px; background-color: var(--card-bg); border-radius: 16px; border: 1px solid var(--card-border); }}
        .chat-bubble {{ padding: 16px 22px; border-radius: 20px; max-width: 85%; font-size: 1.15rem; line-height: 1.6; box-shadow: 0 2px 4px rgba(0,0,0,0.05); position: relative; }}
        
        .user-msg {{ background-color: var(--chat-user-bg); border: 1px solid var(--chat-user-border); color: var(--chat-user-text); align-self: {'flex-start' if direction == 'rtl' else 'flex-end'}; margin-{'left' if direction == 'rtl' else 'right'}: auto; text-align: {align}; border-bottom-{'right' if direction == 'rtl' else 'left'}-radius: 4px; }}
        .bot-msg {{ background-color: var(--chat-bot-bg); border: 1px solid var(--chat-bot-border); color: var(--chat-bot-text); align-self: {'flex-end' if direction == 'rtl' else 'flex-start'}; margin-{'right' if direction == 'rtl' else 'left'}: auto; text-align: {align}; border-bottom-{'left' if direction == 'rtl' else 'right'}-radius: 4px; }}
        
        .chat-meta {{ font-size: 0.9rem; font-weight: 700; margin-bottom: 6px; opacity: 0.7; display: block; color: var(--metric-label); }}
        
        /* כפתורים */
        .stButton button {{
            font-size: 1.3rem !important;
            font-weight: 700;
            padding: 0.6rem 1.5rem;
            border-radius: 12px;
            transition: all 0.3s ease;
            box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
        }}
        .stButton button:hover {{
            transform: translateY(-2px);
            box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1);
        }}
        
        /* Expander */
        .streamlit-expanderHeader {{ 
            font-family: 'Heebo', sans-serif !important; 
            font-size: 1.3rem !important; 
            font-weight: 600 !important; 
            background-color: var(--card-bg) !important; 
            color: var(--text-color) !important; 
            border-radius: 12px;
            margin-bottom: 10px;
            border: 1px solid var(--card-border);
        }}
        .streamlit-expanderContent {{
            border: 1px solid var(--card-border);
            border-top: none;
            border-radius: 0 0 12px 12px;
            padding: 20px;
            background-color: var(--card-bg);
            color: var(--text-color);
        }}
        
        /* קלטים */
        input[type="text"], input[type="number"], textarea {{
            background-color: var(--input-bg) !important;
            border: 1px solid var(--input-border) !important;
            color: var(--text-color) !important;
            border-radius: 10px;
            padding: 10px 15px;
            font-size: 1.1rem !important;
        }}
        input:focus, textarea:focus {{
            border-color: var(--heading-color-secondary) !important;
            box-shadow: 0 0 0 3px rgba(37, 99, 235, 0.2);
            outline: none;
        }}
        
        /* סרגל צד */
        section[data-testid="stSidebar"] {{
            background-color: var(--sidebar-bg);
            border-{'left' if direction == 'rtl' else 'right'}: 1px solid var(--sidebar-border);
        }}
        section[data-testid="stSidebar"] h1 {{
            font-size: 2.5rem !important;
            color: var(--text-color);
            text-align: center;
        }}
        .stRadio label {{
            font-size: 1.25rem !important;
            font-weight: 500;
            padding: 10px 15px;
            margin-bottom: 5px;
            border-radius: 8px;
            transition: background-color 0.2s;
            width: 100%;
            display: block;
            cursor: pointer;
            color: var(--text-color);
        }}
        .stRadio label:hover {{
            background-color: var(--card-border);
        }}
        
    </style>
    """, unsafe_allow_html=True)

def render_chat_bubble(text, role, timestamp, T):
    is_user = role == 'user'
    cls = "user-msg" if is_user else "bot-msg"
    name = T["role_user"] if is_user else T["role_bot"]
    icon = "👤" if is_user else "🤖"
    time_str = timestamp.strftime("%H:%M")
    return f"<div class='chat-bubble {cls}'><span class='chat-meta'>{icon} {name} • {time_str}</span>{text}</div>"