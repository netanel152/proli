class Messages:
    class Customer:
        # User-facing messages
        COMPLETION_CHECK = "היי! 👋 אנחנו ב-Fixi רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. האם העבודה הסתיימה?"
        COMPLETION_ACK = "מעולה! שמחים לשמוע. איך היה השירות עם {pro_name}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."
        RATING_THANKS = "תודה רבה על הדירוג! ⭐"
        PRO_FOUND = "🎉 נמצא איש מקצוע! {pro_name} בדרך אליך. 📞 טלפון: {pro_phone}"
        RATE_SERVICE = "היי! 👋 איך היה השירות עם {pro_name}? נשמח לדירוג 1-5."
        REVIEW_REQUEST = "תודה על הדירוג! האם תרצה לכתוב ביקורת קצרה על החוויה? אם כן, פשוט כתוב אותה כעת."
        REVIEW_SAVED = "תודה רבה! הביקורת שלך נשמרה."

    class Pro:
        # Messages sent to professionals
        REMINDER = """👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת? 
השב 'סיימתי' לאישור או 'עדיין עובד' לעדכון."""
        CUSTOMER_REPORTED_COMPLETION = "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן."
        APPROVE_SUCCESS = "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך."
        CALENDAR_UPDATE_SUCCESS = "\n📅 היומן עודכן בהצלחה!"
        NO_PENDING_APPROVE = "לא מצאתי עבודה חדשה לאישור."
        REJECT_SUCCESS = "העבודה נדחתה. נחפש איש מקצוע אחר."
        NO_PENDING_REJECT = "לא מצאתי עבודה חדשה לדחייה."
        FINISH_SUCCESS = "✅ עודכן שהעבודה הסתיימה. תודה!"
        NO_ACTIVE_FINISH = "לא מצאתי עבודה פעילה לסיום."
        NEW_LEAD_HEADER = "📢 *הצעת עבודה חדשה*"
        NEW_LEAD_DETAILS = "📍 *כתובת:* {full_address}\n🛠️ *תקלה:* {issue_type}\n⏰ *זמן מועדף:* {appointment_time}"
        NEW_LEAD_TRANSCRIPTION = "\n🎙️ *תמליל:* {transcription}"
        NEW_LEAD_FOOTER = "\n\nהשב 'אשר' לקבלת העבודה או 'דחה' לדחייה."
        NAVIGATE_TO = "🚗 נווט לכתובת:"

    class SOS:
        CUSTOMER_REASSIGNING = "מתנצלים על ההמתנה, אנו מאתרים עבורך איש מקצוע זמין יותר כעת... ⏳"
        PRO_LOST_LEAD = "העבודה הועברה לאיש מקצוע אחר עקב חוסר מענה."
        ADMIN_REPORT_HEADER = "🚨 *דו\"ח לידים תקועים (Fixi)*"
        ADMIN_REPORT_BODY = "נמצאו {count} לידים ללא מענה (> {timeout} דק'):\n"
        ADMIN_REPORT_FOOTER = "\nהמערכת ניסתה להעביר אותם אך ללא הצלחה. נדרשת התערבות ידנית."
        
        # New additions
        TO_USER_WITH_PRO = "העברתי את הבקשה לאיש המקצוע שלך, הוא ייצור קשר בהקדם. 🛠️"
        TO_USER_NO_PRO = "העברתי את הפרטים לצוות התמיכה, נחזור אליך בהקדם. 👨‍💻"
        PRO_ALERT = "⚠️ Customer {chat_id} needs help. Msg: {last_message}"
        ADMIN_ALERT = "🚨 System SOS from {chat_id}. Msg: {last_message}"

    class System:
        RESET_SUCCESS = "Reset successful"

    class Keywords:
        # Logic commands used in 'if' statements
        APPROVE_COMMANDS = ["אשר", "1", "approve"]
        REJECT_COMMANDS = ["דחה", "2", "reject"]
        FINISH_COMMANDS = ["סיימתי", "3", "finish", "done"]
        RESET_COMMANDS = ["תפריט", "reset", "menu", "התחלה"]
        SOS_COMMANDS = ["נציג", "אנושי", "עזרה", "מנהל", "human", "help", "admin", "sos"]
        RATING_OPTIONS = ["1", "2", "3", "4", "5"]
        
        # Interactive Button IDs & Titles
        CUSTOMER_COMPLETION_INDICATOR = "כן, הסתיים"
        BUTTON_CONFIRM_FINISH = "confirm_finish"
        BUTTON_NOT_FINISHED = "not_finished"
        TEXT_YES_FINISHED = "כן, הסתיים"
        BUTTON_TITLE_YES_FINISHED = "✅ כן, הסתיים"
        BUTTON_TITLE_NO_NOT_YET = "❌ עדיין לא"

    class Errors:
        AI_OVERLOAD = "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."

    class AISystemPrompts:
        ANALYZE_IMAGE = "[System: Analyze the image to identify the issue.]"
        TRANSCRIBE_AUDIO = "[System: Transcribe the audio verbatim and analyze the intent.]"
        ANALYZE_VIDEO = "[System: Watch the video to identify the issue and describe what you see.]"
        DEFAULT_SYSTEM = "You are a helpful assistant."
        FIXI_SCHEDULER_ROLE = "You are Fixi, an AI scheduler for {pro_name}."
