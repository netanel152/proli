class Messages:
    class Customer:
        # User-facing messages
        COMPLETION_CHECK = "היי! 👋 אנחנו ב-Proli רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. האם העבודה הסתיימה?"
        COMPLETION_ACK = "מעולה! שמחים לשמוע. איך היה השירות עם {pro_name}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."
        RATING_THANKS = "תודה רבה על הדירוג! ⭐"
        PRO_FOUND = "🎉 נמצא איש מקצוע! {pro_name} בדרך אליך. 📞 טלפון: {pro_phone}"
        RATE_SERVICE = "היי! 👋 איך היה השירות עם {pro_name}? נשמח לדירוג 1-5."
        REVIEW_REQUEST = "תודה על הדירוג! האם תרצה לכתוב ביקורת קצרה על החוויה? אם כן, פשוט כתוב אותה כעת."
        REVIEW_SAVED = "תודה רבה! הביקורת שלך נשמרה."
        ADDRESS_SAVED = "✅ הכתובת עודכנה בהצלחה!"
        ADDRESS_INVALID = "❌ לא הצלחתי לזהות את הכתובת. אנא נסה לשלוח מיקום (Location Pin) או הקלד עיר ורחוב בצורה ברורה."

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
        PRO_HELP_MENU = """👋 שלום איש מקצוע!
הנה הפקודות הזמינות לך:
• 'אשר' / '1' - אישור העבודה האחרונה
• 'דחה' / '2' - דחיית העבודה
• 'סיימתי' / '3' - דיווח על סיום עבודה
• 'תפריט' - הצגת עזרה זו"""

    class SOS:
        CUSTOMER_REASSIGNING = "מתנצלים על ההמתנה, אנו מאתרים עבורך איש מקצוע זמין יותר כעת... ⏳"
        PRO_LOST_LEAD = "העבודה הועברה לאיש מקצוע אחר עקב חוסר מענה."
        ADMIN_REPORT_HEADER = "🚨 *דו\"ח לידים תקועים (Proli)*"
        ADMIN_REPORT_BODY = "נמצאו {count} לידים ללא מענה (> {timeout} דק'):\n"
        ADMIN_REPORT_FOOTER = "\nהמערכת ניסתה להעביר אותם אך ללא הצלחה. נדרשת התערבות ידנית."
        
        # New additions
        TO_USER_WITH_PRO = "העברתי את הבקשה לאיש המקצוע שלך, הוא ייצור קשר בהקדם. 🛠️"
        TO_USER_NO_PRO = "העברתי את הפרטים לצוות התמיכה, נחזור אליך בהקדם. 👨‍💻"
        PRO_ALERT = "⚠️ Customer {chat_id} needs help. Msg: {last_message}"
        ADMIN_ALERT = "🚨 System SOS from {chat_id}. Msg: {last_message}"

    class Consent:
        REQUEST = (
            "שלום! 👋 ברוכים הבאים ל-Proli.\n\n"
            "לפני שנתחיל, חשוב לנו ליידע אותך:\n"
            "אנו שומרים את מספר הטלפון שלך, ההודעות והמיקום "
            "כדי לחבר אותך עם בעלי מקצוע מתאימים.\n\n"
            "המידע שלך מאובטח ולא ישותף עם צדדים שלישיים.\n"
            "בכל עת תוכל/י לבקש מחיקת המידע.\n\n"
            "השב/י *כן* או *אישור* להמשך, או *לא* לביטול."
        )
        ACCEPTED = "תודה! ✅ אפשר להתחיל. ספר/י לי במה אוכל לעזור?"
        DECLINED = "הבנו. 🙏 לא נשמור מידע עליך. אם תשנה את דעתך, שלח/י הודעה מתי שתרצה."
        ACCEPT_KEYWORDS = ["כן", "אישור", "yes", "ok", "אוקי", "בסדר", "מסכים", "מסכימה"]
        DECLINE_KEYWORDS = ["לא", "no", "ביטול", "cancel"]

    class Onboarding:
        WELCOME = (
            "👋 ברוכים הבאים להרשמה כאיש מקצוע ב-Proli!\n\n"
            "נשאל אותך כמה שאלות קצרות כדי ליצור את הפרופיל שלך.\n"
            "בסיום, מנהל המערכת יאשר את הפרופיל ותתחיל לקבל עבודות.\n\n"
            "מה *שם העסק* שלך?"
        )
        ASK_TYPE = (
            "מעולה! ✅\n"
            "מה *סוג המקצוע* שלך?\n\n"
            "1️⃣ אינסטלטור\n"
            "2️⃣ חשמלאי\n"
            "3️⃣ הנדימן\n"
            "4️⃣ מנעולן\n"
            "5️⃣ צבעי\n"
            "6️⃣ ניקיון\n"
            "7️⃣ כללי\n\n"
            "שלח את המספר או את שם המקצוע."
        )
        ASK_AREAS = (
            "👍 עכשיו, באילו *ערים/אזורים* אתה עובד?\n"
            "שלח רשימת ערים מופרדות בפסיקים.\n"
            "לדוגמה: תל אביב, רמת גן, חולון"
        )
        ASK_PRICES = (
            "💰 מה *המחירים* שלך? (אופציונלי)\n"
            "שלח רשימת שירותים ומחירים, או השב *דלג* לדילוג.\n"
            "לדוגמה:\n"
            "תיקון נזילה - 250₪\n"
            "החלפת ברז - 350₪"
        )
        CONFIRM = (
            "📋 *סיכום הפרופיל שלך:*\n\n"
            "🏢 שם: {name}\n"
            "🔧 מקצוע: {type}\n"
            "📍 אזורים: {areas}\n"
            "💰 מחירים: {prices}\n\n"
            "הכל נכון? השב *אשר* לשליחה או *ביטול* להתחלה מחדש."
        )
        SUCCESS = (
            "🎉 תודה! הפרופיל שלך נשלח לאישור.\n"
            "נעדכן אותך ברגע שהפרופיל יאושר ותתחיל לקבל עבודות. 👍"
        )
        CANCELLED = "❌ ההרשמה בוטלה. תוכל להתחיל מחדש בכל עת עם *הרשמה*."
        ALREADY_REGISTERED = "כבר יש לך פרופיל במערכת! 😊"
        PENDING_ALREADY = "הפרופיל שלך כבר בהמתנה לאישור. נעדכן אותך בקרוב! ⏳"
        APPROVED_NOTIFICATION = "✅ הפרופיל שלך אושר! מעכשיו תתחיל לקבל הצעות עבודה. בהצלחה! 🎉"
        REJECTED_NOTIFICATION = "לצערנו, הפרופיל שלך לא אושר בשלב זה. צור קשר לפרטים נוספים."
        INVALID_TYPE = "לא הבנתי. שלח מספר 1-7 או שם מקצוע (אינסטלטור, חשמלאי וכו')."

        TYPE_MAP = {
            "1": "plumber", "אינסטלטור": "plumber", "שרברב": "plumber",
            "2": "electrician", "חשמלאי": "electrician",
            "3": "handyman", "הנדימן": "handyman",
            "4": "locksmith", "מנעולן": "locksmith",
            "5": "painter", "צבעי": "painter",
            "6": "cleaner", "ניקיון": "cleaner",
            "7": "general", "כללי": "general",
        }
        TYPE_LABELS = {
            "plumber": "אינסטלטור", "electrician": "חשמלאי",
            "handyman": "הנדימן", "locksmith": "מנעולן",
            "painter": "צבעי", "cleaner": "ניקיון", "general": "כללי",
        }

    class System:
        RESET_SUCCESS = "🔄 השיחה אופסה בהצלחה. איך אפשר לעזור?"

    class Keywords:
        # Logic commands used in 'if' statements
        APPROVE_COMMANDS = ["אשר", "1", "approve"]
        REJECT_COMMANDS = ["דחה", "2", "reject"]
        FINISH_COMMANDS = ["סיימתי", "3", "finish", "done"]
        RESET_COMMANDS = ["תפריט", "reset", "menu", "התחלה"]
        SOS_COMMANDS = ["נציג", "אנושי", "עזרה", "מנהל", "human", "help", "admin", "sos"]
        REGISTER_COMMANDS = ["הרשמה", "להירשם", "register", "signup", "הצטרפות"]
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
        PROLI_SCHEDULER_ROLE = "You are Proli, an AI scheduler for {pro_name}."
