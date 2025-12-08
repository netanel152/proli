import streamlit as st

def load_css(lang_code, T):
    direction = T["dir"]
    align = T["align"]
    
    # Import Google Fonts (Heebo) and Material Symbols Rounded
    st.markdown("""
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Heebo:wght@300;400;500;600;700;800&display=swap">
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Material+Symbols+Rounded:opsz,wght,FILL,GRAD@24,400,1,0" />
    """, unsafe_allow_html=True)
    
    st.markdown(f"""
    <style>
        :root {{
            /* --- Light Mode Variables --- */
            --primary: #2563EB;
            --primary-hover: #1D4ED8;
            
            --bg-app: #F8FAFC;
            --bg-card: #FFFFFF;
            --bg-secondary: #F1F5F9;
            
            --text-main: #0F172A;
            --text-secondary: #64748B;
            --text-inverse: #FFFFFF;
            
            --border-color: #E2E8F0;
            
            /* Chat Specific */
            --chat-bg: #F1F5F9;
            --chat-user-bg: #DBEAFE;
            --chat-user-text: #1E40AF;
            --chat-bot-bg: #FFFFFF;
            --chat-bot-text: #334155;
            
            --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.05);
            --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.1), 0 2px 4px -1px rgb(0 0 0 / 0.06);
            --radius-md: 12px;
            --radius-lg: 16px;
        }}

        @media (prefers-color-scheme: dark) {{
            :root {{
                /* --- Dark Mode Variables --- */
                --primary: #3B82F6;
                --primary-hover: #60A5FA;
                
                --bg-app: #0E1117;
                --bg-card: #1E293B;
                --bg-secondary: #334155;
                
                --text-main: #E2E8F0;
                --text-secondary: #94A3B8;
                --text-inverse: #0F172A;
                
                --border-color: #334155;
                
                /* Chat Specific */
                --chat-bg: #111827;
                --chat-user-bg: #1E3A8A;
                --chat-user-text: #DBEAFE;
                --chat-bot-bg: #1E293B;
                --chat-bot-text: #E2E8F0;
                
                --shadow-sm: 0 1px 2px 0 rgb(0 0 0 / 0.3);
                --shadow-md: 0 4px 6px -1px rgb(0 0 0 / 0.4);
            }}
        }}

        /* --- Global Reset & Typography --- */
        html, body, [class*="css"] {{
            font-family: 'Heebo', sans-serif;
            color: var(--text-main);
            background-color: var(--bg-app);
        }}
        
        .stApp {{
            background-color: var(--bg-app);
        }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--text-main) !important;
            font-weight: 800 !important;
            letter-spacing: -0.5px;
        }}
        
        h1 {{ font-size: 2.2rem !important; margin-bottom: 1rem !important; }}
        h2 {{ font-size: 1.5rem !important; margin-top: 1.5rem !important; }}
        h3 {{ font-size: 1.2rem !important; color: var(--text-secondary) !important; }}
        p, div, span, label {{ color: var(--text-main); }}

        /* --- Main Container Layout (Responsive Stretch) --- */
        .block-container {{
            padding-top: 3rem !important;
            padding-bottom: 5rem !important;
            max-width: 100% !important;
            padding-left: 2rem !important;
            padding-right: 2rem !important;
        }}

        /* --- Sidebar Styling --- */
        section[data-testid="stSidebar"] {{
            background-color: var(--bg-card);
            border-{'left' if direction == 'rtl' else 'right'}: 1px solid var(--border-color);
            box-shadow: var(--shadow-sm);
        }}
        
        section[data-testid="stSidebar"] .block-container {{
            padding-top: 2rem;
            padding-left: 1rem !important;
            padding-right: 1rem !important;
        }}

        /* --- Hide Sidebar Collapse Button --- */
        button[data-testid="collapsed-sidebar-button"] {{
            display: none !important;
        }}

        /* --- Metrics Cards (Flex Layout) --- */
        div[data-testid="stMetric"] {{
            background-color: var(--bg-card);
            border: 1px solid var(--border-color);
            padding: 20px;
            border-radius: var(--radius-lg);
            box-shadow: var(--shadow-sm);
            transition: all 0.2s ease;
            height: 100%;
            display: flex;
            flex-direction: column;
            justify-content: center;
        }}
        
        div[data-testid="stMetric"]:hover {{
            box-shadow: var(--shadow-md);
            transform: translateY(-2px);
            border-color: var(--primary);
        }}
        
        div[data-testid="stMetricLabel"] p {{ color: var(--text-secondary) !important; font-size: 0.9rem !important; font-weight: 600 !important; }}
        div[data-testid="stMetricValue"] div {{ color: var(--primary) !important; font-size: 2.2rem !important; font-weight: 800 !important; }}

        /* --- Expanders (Professional Cards) --- */
        .streamlit-expanderHeader {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: var(--radius-md);
            color: var(--text-main) !important;
            font-weight: 600;
            font-size: 1.1rem;
            padding: 1rem;
            margin-bottom: 0.5rem;
            transition: all 0.2s;
        }}
        
        .streamlit-expanderHeader:hover {{
            border-color: var(--primary) !important;
            color: var(--primary) !important;
        }}
        
        .streamlit-expanderContent {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-top: none !important;
            border-bottom-left-radius: var(--radius-md);
            border-bottom-right-radius: var(--radius-md);
            padding: 1.5rem;
            margin-top: -0.5rem;
            margin-bottom: 1rem;
            box-shadow: var(--shadow-sm);
            color: var(--text-main) !important;
        }}
        
        /* --- Buttons --- */
        .stButton button {{
            border-radius: 10px;
            font-weight: 600;
            padding: 0.5rem 1.2rem;
            transition: all 0.2s;
            border: 1px solid transparent;
            width: 100%;
        }}
        
        /* Primary Button Override */
        .stButton button[kind="primary"] {{
            background: var(--primary) !important;
            color: var(--text-inverse) !important;
            border: none !important;
        }}
        
        .stButton button[kind="primary"]:hover {{
            background: var(--primary-hover) !important;
            box-shadow: 0 4px 12px rgba(37, 99, 235, 0.2);
            transform: translateY(-1px);
        }}

        /* Secondary Button */
        .stButton button[kind="secondary"] {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            color: var(--text-main) !important;
        }}
        
        .stButton button[kind="secondary"]:hover {{
            border-color: var(--text-secondary) !important;
            background-color: var(--bg-secondary) !important;
        }}

        /* --- Tables (DataFrames) --- */
        div[data-testid="stDataFrame"] {{
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            overflow: hidden;
            box-shadow: var(--shadow-sm);
            background-color: var(--bg-card);
        }}
        
        thead tr th {{
            background-color: var(--bg-secondary) !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            font-size: 0.95rem !important;
            padding: 12px !important;
        }}
        
        tbody tr td {{
            font-size: 0.95rem !important;
            color: var(--text-main) !important;
            padding: 12px !important;
            background-color: var(--bg-card) !important;
        }}

        /* --- Form Inputs (Text, Number, Date, Time) --- */
        .stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input, .stTimeInput input {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 10px;
            color: var(--text-main) !important;
            padding: 8px 12px;
            font-size: 1rem;
        }}
        
        .stTextInput input:focus, .stTextArea textarea:focus {{
            border-color: var(--primary) !important;
            box-shadow: 0 0 0 1px var(--primary);
        }}

        /* --- Selectbox / Dropdown Fixes --- */
        
        /* Target the main box */
        div[data-baseweb="select"] > div {{
            background-color: var(--bg-card) !important;
            border-color: var(--border-color) !important;
            color: var(--text-main) !important;
            border-radius: 10px !important;
        }}
        
        /* Text inside the select box */
        div[data-baseweb="select"] span {{
            color: var(--text-main) !important;
        }}
        
        /* Dropdown Menu Container */
        ul[data-testid="stSelectboxVirtualDropdown"] {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
        }}
        
        /* Dropdown Options */
        li[role="option"] {{
            color: var(--text-main) !important;
            background-color: var(--bg-card) !important;
        }}
        
        /* Hovered/Selected Option */
        li[role="option"][aria-selected="true"], li[role="option"]:hover {{
            background-color: var(--bg-secondary) !important;
            color: var(--primary) !important;
            font-weight: 600;
        }}
        
        /* Fix SVG Icon Color */
        div[data-baseweb="select"] svg {{
            fill: var(--text-secondary) !important;
        }}

        /* --- Chat Bubbles (Custom HTML) --- */
        .chat-container {{
            display: flex;
            flex-direction: column;
            gap: 12px;
            padding: 20px;
            background-color: var(--chat-bg);
            border-radius: var(--radius-lg);
            max-height: 500px;
            overflow-y: auto;
            border: 1px solid var(--border-color);
        }}
        
        .chat-bubble {{
            padding: 12px 18px;
            border-radius: 18px;
            max-width: 80%;
            font-size: 1rem;
            line-height: 1.5;
            position: relative;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
        }}
        
        .user-msg {{
            background-color: var(--chat-user-bg);
            color: var(--chat-user-text);
            align-self: {'flex-start' if direction == 'rtl' else 'flex-end'};
            border-bottom-{'right' if direction == 'rtl' else 'left'}-radius: 4px;
        }}
        
        .bot-msg {{
            background-color: var(--chat-bot-bg);
            color: var(--chat-bot-text);
            align-self: {'flex-end' if direction == 'rtl' else 'flex-start'};
            border-bottom-{'left' if direction == 'rtl' else 'right'}-radius: 4px;
        }}
        
        .chat-meta {{
            font-size: 0.75rem;
            font-weight: 600;
            margin-bottom: 4px;
            display: block;
            opacity: 0.7;
        }}
        
        .material-symbols-rounded {{
            vertical-align: middle;
            font-size: 1.2em;
            margin-{'left' if direction == 'rtl' else 'right'}: 4px;
        }}

        /* --- RTL/LTR Direction Overrides --- */
        .stMarkdown, p, span, div, input, textarea {{
            direction: {direction};
            text-align: {align};
        }}
        
    </style>
    """, unsafe_allow_html=True)

def render_chat_bubble(text, role, timestamp, T):
    is_user = role == 'user'
    cls = "user-msg" if is_user else "bot-msg"
    name = T["role_user"] if is_user else T["role_bot"]
    
    # Use Material Symbols with conditional class
    icon_name = "person" if is_user else "smart_toy"
    icon_html = f'<span class="material-symbols-rounded">{icon_name}</span>'
    
    time_str = timestamp.strftime("%H:%M")
    return f"<div class='chat-bubble {cls}'><span class='chat-meta'>{icon_html} {name} • {time_str}</span>{text}</div>"