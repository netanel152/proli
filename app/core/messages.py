class Messages:
    class Customer:
        # User-facing messages
        COMPLETION_CHECK = (
            "היי! 👋 אנחנו ב-Proli רוצים לוודא שהכל תקין עם השירות מ-{pro_name}. "
            "האם העבודה הסתיימה?\n\n"
            "השב *1* — כן, הסתיים ✅\n"
            "השב *2* — עדיין לא ❌"
        )
        COMPLETION_ACK = "מעולה! שמחים לשמוע. איך היה השירות עם {pro_name}? נשמח אם תדרגו אותו מ-1 (גרוע) עד 5 (מצוין)."
        RATING_THANKS = "תודה רבה על הדירוג! ⭐"
        PRO_FOUND = (
            "🎉 *נמצא לך איש מקצוע!*\n\n"
            "👷 *שם:* {pro_name}\n"
            "📞 *טלפון:* {pro_phone}\n"
            "{profession_line}"
            "\n"
            "📋 *פרטי העבודה:*\n"
            "🛠️ תקלה: {issue_type}\n"
            "📍 כתובת: {full_address}\n"
            "⏰ תאריך ושעה: {appointment_time}\n"
            "{price_line}"
            "{rating_line}"
            "\n{pro_name} יצור איתך קשר בקרוב! 👍"
        )
        RATE_SERVICE = "היי! 👋 איך היה השירות עם {pro_name}? נשמח לדירוג 1-5."
        REVIEW_REQUEST = "תודה על הדירוג! האם תרצה לכתוב ביקורת קצרה על החוויה? אם כן, פשוט כתוב אותה כעת."
        REVIEW_SAVED = "תודה רבה! הביקורת שלך נשמרה."
        ADDRESS_SAVED = "✅ הכתובת עודכנה בהצלחה!"
        ADDRESS_INVALID = "❌ לא הצלחתי לזהות את הכתובת. אנא נסה לשלוח מיקום (Location Pin) או הקלד עיר ורחוב בצורה ברורה."
        REQUEST_CANCELLED = "👍 הבקשה בוטלה. אם תרצה/י לפתוח פנייה חדשה, פשוט שלח/י הודעה."
        CANCELLED_ACTIVE_LEAD = "✅ ביטלתי את העבודה כבקשתך. עדכנתי את איש המקצוע."
        RESCHEDULE_OFFER = (
            "אין בעיה! בוא נתאם מועד חדש. הנה הזמנים הפנויים של איש המקצוע:\n"
            "{slots}\n\n"
            "השב עם מספר התור הרצוי, או 'ביטול' כדי להשאיר את המועד הנוכחי."
        )
        RESCHEDULE_SUCCESS = "✅ מעולה! המועד שונה בהצלחה ל-{new_time}. איש המקצוע עודכן."
        RESCHEDULE_NO_SLOTS = (
            "מצטערים, לא מצאתי תורים פנויים כרגע אצל איש המקצוע. "
            "האם תרצה לבטל את העבודה או להמתין?"
        )
        RESCHEDULE_INVALID_CHOICE = "אנא בחר מספר תור חוקי מהרשימה."
        RESCHEDULE_CANCELLED = "המועד נשאר כפי שהיה."
        HELP_INFO = (
            "אני המערכת החכמה של Proli! 🛠️\n"
            "אני כאן כדי לעזור לך למצוא את איש המקצוע המתאים ביותר. "
            "פשוט תאר/י לי את התקלה והמיקום שלך, ואני אדאג להשאר."
        )
        PENDING_REVIEW = (
            "קיבלתי את הפנייה שלך! 👍\n"
            "כרגע אנחנו מחפשים את איש המקצוע המתאים ביותר.\n"
            "צוות Proli יחזור אליך בהקדם עם עדכון."
        )
        STILL_PENDING_REVIEW = (
            "הפנייה שלך עדיין בבדיקה אצל צוות Proli. "
            "נחזור אליך ברגע שנוכל לשבץ איש מקצוע מתאים. 🙏"
        )
        AWAITING_APPROVAL = (
            "מעולה, העברתי את הפרטים והמדיה לאיש המקצוע לאישור. "
            "אעדכן אותך ממש בקרוב. 👍"
        )
        AWAITING_APPROVAL_TRANSPARENT = "✅ העברתי את הפנייה שלך ל-{pro_name}. \nזמן המענה הממוצע שלו הוא כ-10 דקות.\nאעדכן אותך כאן ברגע שהוא יאשר את העבודה!"
        YOU_ARE_WELCOME = "בכיף! אני כאן אם תצטרך עוד משהו. 🛠️"
        STILL_WAITING = "הפנייה שלך נמצאת עכשיו אצל איש המקצוע לאישור. נעדכן אותך ברגע שנקבל תשובה! 🙏"
        BOT_PAUSED_BY_PRO = (
            "איש המקצוע ביקש לדבר איתך ישירות. 📞\n"
            "הבוט מושהה כרגע — איש המקצוע ידבר איתך בהודעות."
        )
        BOT_PAUSED_BY_CUSTOMER = (
            "✅ קיבלתי! מעביר אותך לנציג אנושי.\n"
        )
        EMERGENCY_ACK = "🚨 *זיהיתי מצב חירום.* \nאני מדלג על שאר הפרטים ומזעיק עכשיו אנשי מקצוע פנויים באזור שלך! בינתיים, אנא שמור על בטיחות."
        SLA_DEFLECTION_MESSAGE = (
            "אני רואה שאיש המקצוע שלנו כרגע באמצע עבודה מורכבת ולא התפנה לענות.\n"
            "תרצה שאקבע לך שיחה טלפונית איתו להמשך היום?\n\n"
            "השב *כן* - לקביעת שיחה\n"
            "השב *לא* - להמשך המתנה בוואטסאפ"
        )
        LOYALTY_OFFER = (
            "איזה כיף שחזרת אלינו! 🏠\n"
            "ראיתי שבעבר {pro_name} טיפל בך. תרצה שאבדוק קודם אם הוא פנוי לקחת את העבודה הזו?\n\n"
            "השב *1* - כן, אשמח שזה יהיה הוא.\n"
            "השב *2* - לא, חפש לי מישהו אחר."
        )
    class Pro:
        # Messages sent to professionals
        REMINDER = """👋 היי, רק מוודא לגבי העבודה האחרונה. האם סיימת? 
השב 'סיימתי' לאישור או 'עדיין עובד' לעדכון."""
        STALE_LEAD_REMINDER = "היי {pro_name}! 👋\nשמתי לב שהעבודה שנקבעה אצל {customer_name} עדיין מסומנת כפתוחה.\nאם סיימת אותה, אנא השב *סיימתי* כדי לשחרר את המערכת ולקבל עבודות חדשות!"
        CUSTOMER_REPORTED_COMPLETION = "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן."
        APPROVE_SUCCESS = "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך."
        CALENDAR_UPDATE_SUCCESS = "\n📅 היומן עודכן בהצלחה!"
        NO_PENDING_APPROVE = "לא מצאתי עבודה חדשה לאישור."
        ALREADY_RESPONDED = "כבר הגבת לקריאה זו. לא ניתן לשנות את בחירתך כעת."
        REJECT_SUCCESS = "העבודה נדחתה. נחפש איש מקצוע אחר."
        NO_PENDING_REJECT = "לא מצאתי עבודה חדשה לדחייה."
        FINISH_SUCCESS = "✅ עודכן שהעבודה הסתיימה. תודה!"
        NO_ACTIVE_FINISH = "לא מצאתי עבודה פעילה לסיום."
        STATUS_PAUSED = "☕ *הסטטוס שלך שונה ל'בהפסקה'.* לא תקבל הצעות עבודה חדשות עד שתכתוב 'זמין'."
        STATUS_RESUMED = "🚀 *הסטטוס שלך שונה ל'זמין'.* חזרת לקבל הצעות עבודה!"
        NO_PENDING_APPROVALS = "❌ אין לך כרגע עבודות שממתינות לאישור."
        NO_ACTIVE_JOBS = "❌ אין לך עבודות פעילות כרגע שניתן לסיים."
        SELECT_JOB_TO_FINISH = "איזו עבודה סיימת?\n{jobs_list}\nהשב עם מספר העבודה, או 'ביטול'."
        # Sent when pro is first matched — conversation still in progress, no action needed yet
        EARLY_LEAD_HEADER = "👀 *שיחה בתהליך*"
        LOYALTY_LEAD_HEADER = "🌟 *לקוח חוזר שלך ביקש אותך!*"
        EARLY_LEAD_DETAILS = "🛠️ *תקלה:* {issue_type}\n📍 *עיר:* {city}"
        EARLY_LEAD_FOOTER = "\n\nהבוט אוסף פרטים מהלקוח (כתובת + תאריך ושעה).\nתקבל הודעה עם כל הפרטים לאישורך — *אין צורך לפעול עכשיו.*"
        # Sent when deal closes — ready for approval
        DEAL_CONFIRMED_HEADER = "✅ *הלקוח אישר! פרטי העבודה:*"
        EMERGENCY_LEAD_HEADER = "🚨 *קריאת חירום דחופה!* 🚨"
        NEW_LEAD_HEADER = "📢 *הצעת עבודה חדשה*"
        NEW_LEAD_DETAILS = "👤 *לקוח:* {customer_name}\n📍 *כתובת:* {full_address}\nℹ️ *פרטים נוספים:* {extra_info}\n🛠️ *תקלה:* {issue_type}\n⏰ *תאריך ושעה מועדפים:* {appointment_time}"
        NEW_LEAD_TRANSCRIPTION = "\n🎙️ *תמליל:* {transcription}"
        NEW_LEAD_FOOTER = "\n\nהשב 'אשר' לקבלת העבודה או 'דחה' לדחייה."
        APPROVAL_REQUEST = (
            "📋 *פרטי עבודה חדשה לאישורך:*\n\n"
            "👤 *לקוח:* {customer_name} ({customer_phone})\n"
            "📍 *כתובת:* {full_address}\n"
            "ℹ️ *פרטים נוספים:* {extra_info}\n"
            "🛠️ *תקלה:* {issue_type}\n"
            "⏰ *תאריך ושעה:* {appointment_time}\n\n"
            "כדי לאשר השב: *אשר*\n"
            "כדי לדחות השב: *דחה*"
        )
        PAUSE_ACK = (
            "⏸️ הבוט הושהה. תוכל לדבר עם הלקוח ישירות.\n"
            "הבוט יחזור לפעולה אוטומטית בעוד שעתיים או כשתשלח 'המשך'."
        )
        PAUSE_NOTIFICATION = (
            "🚨 הלקוח מבקש מענה אנושי.\n"
            "כנס לשיחה בוואטסאפ. הבוט יחזור לפעולה אוטומטית בעוד שעתיים."
        )
        CUSTOMER_CANCELLED = (
            "⚠️ *עדכון חשוב:* הלקוח/ה {customer_name} ביטל/ה את העבודה "
            "שנקבעה לכתובת {address}."
        )
        CUSTOMER_RESCHEDULED_SUCCESS = (
            "📅 *עדכון יומן:* הלקוח/ה {customer_name} בכתובת {address} "
            "שינה/תה את מועד העבודה.\n"
            "מועד ישן: {old_time}\n"
            "מועד חדש: {new_time}\n"
            "היומן שלך עודכן אוטומטית."
        )
        NAVIGATE_TO = "🚗 נווט לכתובת:"
        NO_ACTIVE_JOBS_LIST = "אין לך עבודות פעילות כרגע. 👍"
        NO_HISTORY = "עדיין אין לך עבודות מושלמות."
        NO_REVIEWS = "עדיין אין לך ביקורות."
        ACTIVE_JOB_ROW = "  {num}. [{status}] {issue} — {address} | {time}"
        HISTORY_ROW = "  {num}. {issue} — {address} | {date}"
        REVIEW_ROW = "  ⭐{rating} — \"{comment}\""
        STATS_HEADER = "📊 *הסטטיסטיקות שלך:*\n"
        STATS_BODY = (
            "✅ עבודות שהושלמו: {completed}\n"
            "🔄 עבודות פעילות: {active}\n"
            "⭐ דירוג ממוצע: {rating}\n"
            "💬 ביקורות: {reviews}\n"
            "📅 הצטרפת: {joined}"
        )
        PRO_DASHBOARD = (
            "שלום {pro_name}! 🛠️\n"
            "⭐ דירוג: {rating}\n"
            "{status_emoji} סטטוס: {status_text}\n"
            "💼 עבודות פעילות: {active_jobs}/{max_jobs}\n\n"
            "*פקודות המערכת:*\n"
            "✅ 'אשר' / ❌ 'דחה' - לעבודות שממתינות לתשובתך\n"
            "🏁 'סיימתי' - לסיום עבודה פעילה\n"
            "🔍 'חפש' - לאיתור לידים פנויים\n"
            "☕ 'הפסקה' / 🚀 'זמין' - לשליטה בקבלת עבודות"
        )
        INTENT_DETECTED = (
            "🛠️ זיהיתי שאתה מדווח על תקלה. האם תרצה לעבור למצב לקוח כדי שאזמין לך איש מקצוע?\n\n"
            "השב *1* — כן, עבור למצב לקוח 👤\n"
            "השב *2* — לא, אני ממשיך כטכנאי 🛠️"
        )
        SWITCHED_TO_CUSTOMER = "מעולה, עברת למצב לקוח 👤. כעת אטפל בך כמו בכל לקוח שלנו. ספר לי שוב, מה התקלה?"
        SWITCH_CANCELLED = "👍 ממשיכים כרגיל במצב טכנאי."
        AUTO_RETURNED_TO_PRO = (
            "הקריאה שלך הועברה לאיש המקצוע לאישור. בינתיים, החזרתי אותך למצב טכנאי 🛠️ "
            "כדי שתוכל להמשיך לנהל את העסק כרגיל."
        )
        SEARCH_RATE_LIMITED = "⏳ חיפשת לאחרונה. אנא המתן {minutes} דקות לפני החיפוש הבא."
        NO_STUCK_LEADS = "אין לידים תקועים זמינים כרגע. ננסה שוב מאוחר יותר 👍"
        STUCK_LEAD_FOUND = (
            "📢 *נמצא ליד תקוע:*\n\n"
            "🛠️ *תקלה:* {issue}\n"
            "📍 *עיר:* {city}\n"
            "⏰ *ממתין:* {wait_minutes} דק'\n\n"
            "השב 'אשר' כדי לקחת את העבודה."
        )

    class SOS:
        CUSTOMER_REASSIGNING = "מתנצלים על ההמתנה, אנו מאתרים עבורך איש מקצוע זמין יותר כעת... ⏳"
        NO_PRO_AVAILABLE = (
            "מצטערים 😔 לא הצלחנו למצוא איש מקצוע זמין לבקשתך כרגע.\n"
            "אנא נסה שוב מאוחר יותר או פנה אלינו ישירות לקבלת עזרה."
        )
        MAX_REASSIGNMENTS_REACHED = (
            "מצטערים מאוד 😔 ניסינו למצוא לך מספר אנשי מקצוע ולא הצלחנו.\n"
            "הפנייה שלך הועברה לצוות שלנו ונחזור אליך בהקדם."
        )
        PRO_LOST_LEAD = "העבודה הועברה לאיש מקצוע אחר עקב חוסר מענה."
        ADMIN_REPORT_HEADER = "🚨 *דו\"ח לידים תקועים (Proli)*"
        ADMIN_REPORT_BODY = "נמצאו {count} לידים ללא מענה (> {timeout} דק'):\n"
        ADMIN_REPORT_FOOTER = (
            "\nהמערכת ניסתה להעביר אותם אך ללא הצלחה. נדרשת התערבות ידנית."
            "\n💡 לטיפול בלידים והעברה לאיש מקצוע, השב את המילה: ניהול"
        )

        TO_USER_WITH_PRO = (
            "✅ קיבלתי! העברתי את בקשתך לאיש המקצוע שלך.\n"
            "הוא ייצור איתך קשר בהקדם האפשרי. 🛠️\n\n"
            "אם לא קיבלת מענה תוך זמן קצר, תוכל/י לפנות אלינו שוב."
        )
        TO_USER_NO_PRO = (
            "✅ קיבלתי! העברתי את פנייתך לצוות התמיכה שלנו.\n"
            "נחזור אליך בהקדם האפשרי. 👨‍💻\n\n"
            "אנחנו כאן בשבילך ונטפל בעניין בהקדם!"
        )
        PRO_ALERT = (
            "⚠️ *הלקוח שלך צריך עזרה!*\n\n"
            "📞 *טלפון:* {phone}\n"
            "💬 *הודעה:* {last_message}\n\n"
            "פנה/י אליו בהקדם האפשרי."
        )
        ADMIN_ALERT = (
            "🚨 *קריאת SOS מלקוח — Proli*\n\n"
            "📞 *טלפון:* {phone}\n"
            "💬 *הודעה:* \"{last_message}\"\n\n"
            "{lead_details}"
        )

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
        ACTIVE_JOBS_COMMANDS = ["עבודות", "4", "jobs", "active"]
        HISTORY_COMMANDS = ["היסטוריה", "5", "history"]
        STATS_COMMANDS = ["דוח", 'דו"ח', "6", "stats", "report"]
        REVIEWS_COMMANDS = ["ביקורות", "7", "reviews", "ratings"]
        SEARCH_COMMANDS = ["מצא", "search", "find"]
        RESET_COMMANDS = ["reset", "התחלה"]
        HELP_COMMANDS = ["תפריט", "עזרה", "help", "menu"]
        SOS_COMMANDS = ["נציג", "אנושי", "מנהל", "admin", "sos"]
        CANCEL_KEYWORDS = ["בטל", "בטלי", "עזוב", "עזבי", "לא משנה", "טעות", "cancel", "nevermind"]
        RESCHEDULE_KEYWORDS = [
            "לשנות שעה", "לשנות תאריך", "מועד אחר", "זמן אחר",
            "תאריך אחר", "reschedule", "לדחות את", "מועד חדש",
        ]
        REGISTER_COMMANDS = ["הרשמה", "להירשם", "register", "signup", "הצטרפות"]
        RESUME_COMMANDS = ["זמין", "חזרתי", "פעיל"]
        PAUSE_COMMANDS = ["חופשה", "הפסקה", "לא זמין"]
        BOT_RESUME_COMMANDS = ["המשך", "resume", "חזור"]
        BOT_PAUSE_COMMANDS = ["השהה", "pause", "hold"]
        EMERGENCY_KEYWORDS = ["דחוף", "פיצוץ", "הצפה", "שריפה", "קצר", "סכנה", "מים בכל הבית", "חירום", "emergency", "urgent"]
        RATING_OPTIONS = ["1", "2", "3", "4", "5"]
        THANKS_KEYWORDS = ["תודה", "תודה רבה", "אחלה", "מעולה תודה", "thanks", "thank you", "תודה אחי", "מעולה", "בסדר גמור"]

        # Completion check text tokens (used in handle_customer_completion_text)
        CUSTOMER_COMPLETION_INDICATOR = "כן, הסתיים"

    class Errors:
        AI_OVERLOAD = "סליחה, אני חווה עומס כרגע. נסה שוב עוד רגע."
        GENERIC_ERROR = "משהו השתבש. אנא נסה שוב."

    class AISystemPrompts:
        ANALYZE_IMAGE = "[System: Analyze the image to identify the issue.]"
        TRANSCRIBE_AUDIO = "[System: Transcribe the audio verbatim and analyze the intent.]"
        ANALYZE_VIDEO = "[System: Watch the video to identify the issue and describe what you see.]"
        DEFAULT_SYSTEM = "You are a helpful assistant."
        PROLI_SCHEDULER_ROLE = "You are Proli, an AI scheduler for {pro_name}."
