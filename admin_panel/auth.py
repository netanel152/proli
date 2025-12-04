import streamlit as st
import os
import time
import extra_streamlit_components as stx
from datetime import datetime, timedelta
import hashlib

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
        st.markdown("### ברוך הבא למרכז השליטה")
        
        with st.form("login_form"):
            password = st.text_input("סיסמת מנהל", type="password", placeholder="הכנס סיסמה כאן...")
            remember_me = st.checkbox("זכור אותי (Keep me logged in)")
            submitted = st.form_submit_button("כניסה למערכת", type="primary")
            
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
                    st.error("❌ סיסמה שגויה. נסה שוב.")

    return False

def logout():
    """כפתור התנתקות"""
    if st.sidebar.button("🔒 התנתק"):
        st.toast("מתנתק...", icon="👋")

        st.session_state["authenticated"] = False
        
        try:
            cookie_manager = get_manager()
            cookie_manager.delete("fixi_auth_token")
        except KeyError:
            pass # הקוקי כבר לא שם, הכל טוב
        except Exception as e:
            print(f"Error deleting cookie: {e}")

        time.sleep(0.5)
        st.rerun()
        st.experimental_set_query_params(page="login")
