import streamlit as st

def load_css(lang_code, T):
    direction = T["dir"]
    align = T["align"]
    
    # Import Google Fonts (Rubik + Heebo) and Material Symbols Rounded
    st.markdown("""
        <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Rubik:wght@300;400;500;600;700&family=Heebo:wght@300;400;500;600;700;800&display=swap">
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
            font-family: 'Rubik', 'Heebo', sans-serif !important;
            color: var(--text-main);
            background-color: var(--bg-app);
            font-size: 18px !important; /* Base Font Size */
            line-height: 1.6;
            direction: {direction} !important;
            text-align: {align} !important;
        }}
        
        .stApp {{
            background-color: var(--bg-app);
        }}

        h1, h2, h3, h4, h5, h6 {{
            color: var(--text-main) !important;
            font-family: 'Rubik', sans-serif !important;
            font-weight: 700 !important;
            letter-spacing: -0.5px;
            text-align: {align} !important;
        }}
        
        h1 {{ font-size: 2.2rem !important; margin-bottom: 1rem !important; }}
        h2 {{ font-size: 1.6rem !important; margin-top: 1.4rem !important; }}
        h3 {{ font-size: 1.3rem !important; color: var(--text-secondary) !important; }}
        
        /* Body Text */
        p, div, span, label, li {{ 
            color: var(--text-main);
            font-size: 1rem;
            text-align: {align};
        }}
        
        /* Form Labels */
        label {{
            font-weight: 500 !important;
            opacity: 0.9;
            display: block !important;
            text-align: {align} !important;
            direction: {direction} !important;
        }}

        /* --- Main Container Layout --- */
        .block-container {{
            padding-top: 2.5rem !important;
            padding-bottom: 4rem !important;
            max-width: 100% !important;
            padding-left: 1.5rem !important;
            padding-right: 1.5rem !important;
        }}

        /* --- Sidebar Styling --- */
        section[data-testid="stSidebar"] {{
            background-color: var(--bg-card);
            border-{'left' if direction == 'rtl' else 'right'}: 1px solid var(--border-color);
            box-shadow: var(--shadow-sm);
        }}
        
        section[data-testid="stSidebar"] .block-container {{
            padding-top: 1.5rem;
            padding-left: 0.8rem !important;
            padding-right: 0.8rem !important;
            direction: {direction} !important;
            text-align: {align} !important;
        }}
        
        /* Force Sidebar Elements Alignment */
        section[data-testid="stSidebar"] .stRadio div,
        section[data-testid="stSidebar"] .stSelectbox div {{
            direction: {direction};
            text-align: {align};
        }}

        /* --- Metrics Cards --- */
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
            direction: {direction};
            text-align: {align};
        }}
        
        div[data-testid="stMetricLabel"] {{
            width: 100%;
            text-align: {align} !important;
        }}
        
        div[data-testid="stMetricValue"] {{
            width: 100%;
            text-align: {align} !important;
        }}

        /* --- Expanders --- */
        .streamlit-expanderHeader {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: var(--radius-md);
            color: var(--text-main) !important;
            font-family: 'Rubik', sans-serif !important;
            font-weight: 500;
            font-size: 1.2rem;
            padding: 1rem;
            margin-bottom: 0.5rem;
            transition: all 0.2s;
            direction: {direction} !important;
            text-align: {align} !important;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        
        /* RTL Fix for Expander Arrow */
        .streamlit-expanderHeader p {{
            margin: 0 !important;
            flex-grow: 1;
            text-align: {align} !important;
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
            direction: {direction} !important;
            text-align: {align} !important;
        }}
        
        /* --- Buttons --- */
        .stButton button {{
            border-radius: 10px;
            font-family: 'Rubik', sans-serif !important;
            font-weight: 500;
            padding: 0.6rem 1.4rem;
            font-size: 1.1rem !important;
            transition: all 0.2s;
            border: 1px solid transparent;
            width: 100%;
        }}

        /* --- Tables --- */
        div[data-testid="stDataFrame"] {{
            border: 1px solid var(--border-color);
            border-radius: var(--radius-md);
            overflow: hidden;
            box-shadow: var(--shadow-sm);
            background-color: var(--bg-card);
            direction: {direction} !important;
        }}
        
        thead tr th {{
            background-color: var(--bg-secondary) !important;
            color: var(--text-secondary) !important;
            font-weight: 600 !important;
            font-size: 1.05rem !important;
            padding: 14px !important;
            text-align: {align} !important;
        }}
        
        tbody tr td {{
            font-size: 1.05rem !important;
            color: var(--text-main) !important;
            padding: 14px !important;
            background-color: var(--bg-card) !important;
            text-align: {align} !important;
        }}

        /* --- Form Inputs --- */
        .stTextInput input, .stNumberInput input, .stTextArea textarea, .stDateInput input, .stTimeInput input {{
            background-color: var(--bg-card) !important;
            border: 1px solid var(--border-color) !important;
            border-radius: 10px;
            color: var(--text-main) !important;
            padding: 10px 14px;
            font-size: 1.05rem;
            font-family: 'Rubik', sans-serif !important;
            direction: {direction} !important;
            text-align: {align} !important;
        }}

        /* --- Selectbox Fixes --- */
        div[data-baseweb="select"] > div {{
            background-color: var(--bg-card) !important;
            border-color: var(--border-color) !important;
            color: var(--text-main) !important;
            border-radius: 10px !important;
            direction: {direction} !important;
        }}
        
        div[data-baseweb="select"] span {{
            color: var(--text-main) !important;
            font-size: 1.05rem;
            font-family: 'Rubik', sans-serif !important;
            text-align: {align} !important;
        }}
        
        /* Dropdown Options */
        li[role="option"] {{
            direction: {direction} !important;
            text-align: {align} !important;
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
            direction: {direction};
        }}
        
        .chat-bubble {{
            padding: 14px 20px;
            border-radius: 18px;
            max-width: 80%;
            font-size: 1rem;
            line-height: 1.6;
            position: relative;
            box-shadow: 0 1px 2px rgba(0,0,0,0.05);
            font-family: 'Rubik', sans-serif !important;
            display: inline-block;
            text-align: {align};
        }}
        
        /* User Message (Right in RTL, Left in LTR) */
        .user-msg {{
            background-color: var(--chat-user-bg);
            color: var(--chat-user-text);
            align-self: {'flex-start' if direction == 'rtl' else 'flex-end'};
            border-bottom-{'right' if direction == 'rtl' else 'left'}-radius: 4px;
            margin-{'right' if direction == 'rtl' else 'left'}: auto;
        }}
        
        /* Bot Message (Left in RTL, Right in LTR) */
        .bot-msg {{
            background-color: var(--chat-bot-bg);
            color: var(--chat-bot-text);
            align-self: {'flex-end' if direction == 'rtl' else 'flex-start'};
            border-bottom-{'left' if direction == 'rtl' else 'right'}-radius: 4px;
            margin-{'left' if direction == 'rtl' else 'right'}: auto;
        }}
        
        .chat-meta {{
            font-size: 0.8rem;
            font-weight: 600;
            margin-bottom: 4px;
            display: flex;
            align-items: center;
            opacity: 0.7;
            flex-direction: {'row' if direction == 'ltr' else 'row-reverse'};
            gap: 4px;
        }}
        
        .material-symbols-rounded {{
            font-size: 1.2em;
        }}

        /* Global RTL Overrides for Streamlit Widgets */
        .stMarkdown, p, span, div, input, textarea {{
            direction: {direction} !important;
            text-align: {align} !important;
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
    
    # In RTL, we might want the icon on the right for user, left for bot? 
    # Or just consistent. Let's keep it simple: Icon Name Time
    
    return f"<div class='chat-bubble {cls}'><span class='chat-meta'>{icon_html} {name} • {time_str}</span>{text}</div>"