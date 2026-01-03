class Messages:
    class Customer:
        # User-facing messages
        COMPLETION_CHECK = "היי! 👋 אנחנו ב-Fixi רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. האם העבודה הסתיימה לשביעות רצונך?\nהשב 'כן' לאישור או 'לא' אם טרם הסתיים."
        COMPLETION_ACK = "מעולה! שמחים לשמוע. איך היה השירות עם {pro_name}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מעולה)."
        RATING_THANKS = "תודה רבה על הדירוג! ⭐"
        PRO_FOUND = "🎉 נמצא איש מקצוע! {pro_name} בדרך אליך. 📞 טלפון: {pro_phone}"
        RATE_SERVICE = "היי! 👋 איך היה השירות עם {pro_name}? נשמח לדירוג 1-5."
        REVIEW_REQUEST = "תודה על הדירוג! האם תרצה לכתוב ביקורת קצרה על החוויה? אם כן, פשוט כתוב אותה כעת."
        REVIEW_SAVED = "תודה רבה! הביקורת שלך נשמרה."

    class Pro:
        # Messages sent to professionals
        REMINDER = "👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת? \nהשב 'סיימתי' לאישור או 'עדיין עובד' לעדכון."
        CUSTOMER_REPORTED_COMPLETION = "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן."
        APPROVE_SUCCESS = "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך."
        NO_PENDING_APPROVE = "לא מצאתי עבודה חדשה לאישור."
        REJECT_SUCCESS = "העבודה נדחתה. נחפש איש מקצוע אחר."
        NO_PENDING_REJECT = "לא מצאתי עבודה חדשה לדחייה."
        FINISH_SUCCESS = "✅ עודכן שהעבודה הסתיימה. תודה!"
        NO_ACTIVE_FINISH = "לא מצאתי עבודה פעילה לסיום."
        NEW_LEAD_HEADER = "📢 *הצעת עבודה חדשהфі*"
        NEW_LEAD_DETAILS = "📍 *כתובת:* {full_address}\n🛠️ *תקלה:* {issue_type}\n⏰ *זמן מועדף:* {appointment_time}"
        NEW_LEAD_TRANSCRIPTION = "\n🎙️ *תמליל:* {transcription}"
        NEW_LEAD_FOOTER = "\n\nהשב 'אשר' לקבלת העבודה או 'דחה' לדחייה."
        NAVIGATE_TO = "🚗 נווט לכתובת:"

    class Keywords:
        # Logic commands used in 'if' statements
        APPROVE_COMMANDS = ["אשר", "1", "approve"]
        REJECT_COMMANDS = ["דחה", "2", "reject"]
        FINISH_COMMANDS = ["סיימתי", "3", "finish", "done"]
        RATING_OPTIONS = ["1", "2", "3", "4", "5"]
        CUSTOMER_COMPLETION_INDICATOR = "כן, הסתיים"
