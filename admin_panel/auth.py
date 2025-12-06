import streamlit as st
import os
import time
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import hashlib
from config import TRANS

# --- פונקציית עזר להצפנה (Hashing) ---
def make_hash(password):
    """מייצר טביעת אצבע ייחודית לסיסמה (SHA-256)"""
    return hashlib.sha256(password.encode()).hexdigest()

# --- מנהל הקוקיות (Singleton מתוקן) ---
def get_manager():
    # בדיקה אם המנהל כבר קיים בזיכרון כדי למנוע שגיאת מפתח כפול
    if "cookie_manager" not in st.session_state:
        st.session_state.cookie_manager = stx.CookieManager(key="fixi_auth_manager")
    return st.session_state.cookie_manager

def check_password():
    """
    מנהל את תהליך ההתחברות בצורה מאובטחת.
    """
    cookie_manager = get_manager()
    
    # קריאת כל הקוקיות
    cookies = cookie_manager.get_all()
    
    # קביעת שפה לפי קוקי (או ברירת מחדל HE)
    saved_lang = cookies.get("fixi_lang", "HE")
    T_auth = TRANS.get(saved_lang, TRANS["HE"])

    cookie_token = cookies.get("fixi_auth_token")
    
    real_password = os.getenv("ADMIN_PASSWORD", "admin123")
    real_password_hash = make_hash(real_password)
    
    # 1. בדיקה בזיכרון הרגעי (Session)
    if st.session_state.get("authenticated", False):
        return True

    # 2. בדיקה בקוקי (מוצפן!)
    if cookie_token == real_password_hash:
        st.session_state["authenticated"] = True
        return True

    # --- מסך התחברות ---
    col1, col2, col3 = st.columns([1, 2, 1])
    
    with col2:
        st.markdown("<br><br>", unsafe_allow_html=True)
        st.title("🔐 Fixi Admin")
        st.markdown(f"### {T_auth['welcome_message']}")
        
        with st.form("login_form"):
            password = st.text_input(T_auth["admin_password_label"], type="password", placeholder=T_auth["admin_password_placeholder"])
            remember_me = st.checkbox(T_auth["remember_me"])
            submitted = st.form_submit_button(T_auth["login_button"], type="primary")
            
            if submitted:
                if password == real_password:
                    st.session_state["authenticated"] = True
                    
                    if remember_me:
                        # שמירת ה-HASH
                        expires = datetime.now() + timedelta(days=7)
                        cookie_manager.set("fixi_auth_token", real_password_hash, expires_at=expires)
                    
                    st.success("התחברת בהצלחה! טוען...")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error(T_auth["wrong_password"])

    return False

def logout(cookie_manager, T):
    """כפתור התנתקות"""
    if st.sidebar.button(T["disconnect"]):
        st.toast("מתנתק...", icon="👋")

        st.session_state["authenticated"] = False
        
        try:
            cookie_manager.delete("fixi_auth_token")
        except KeyError:
            pass # הקוקי כבר לא שם, הכל טוב
        except Exception as e:
            print(f"Error deleting cookie: {e}")

        time.sleep(0.5)
        st.rerun()
        st.query_params.clear()
