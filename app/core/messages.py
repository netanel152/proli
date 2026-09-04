"""The copy catalog — every sentence a customer, professional or operator reads.

PRO-168 rewrote this file against `docs/COPY_STYLE_GUIDE.md`. The rules that
shape what you see below, in the guide's numbering:

* §2 neutral-first gender — rephrase so no gendered verb is needed; `השב/י`
  only when a verb is unavoidable; never bare masculine in customer copy.
* §3 one menu format — a lead-in ending `?`/`:`, then `*token* — text`, one
  option per line. Every advertised digit/keyword has a handler (§7).
* §4 at most one emoji per message, at the start of the headline; sibling
  messages agree. Structured cards use bold field labels (§5), not one emoji
  per row.
* §6 RTL safety — Latin/numeric placeholders end-of-line or on their own line.
* §8 the product is **פרולי** in Hebrew copy; no trailing whitespace.

Menus are text-only by hard product rule (see CLAUDE.md): nothing here is ever
rendered through `send_interactive`.
"""


class Messages:
    class Customer:
        # --- The 1-5 rating ask -------------------------------------------
        # §7: three messages open the rating window — COMPLETION_ACK (the
        # customer confirmed), RATE_SERVICE (the pro finished) and
        # RATING_REPROMPT (an unreadable answer) — and all three set the same
        # `waiting_for_rating` flag. They share this line so they cannot
        # advertise three different answer sets, and so every one of them
        # mentions the *דלג* skip that `Keywords.SKIP_TOKENS` already handles.
        RATING_SCALE_LINE = (
            "אפשר לענות במספר בין *1* (גרוע) ל-*5* (מצוין), או *דלג* לדילוג."
        )

        COMPLETION_CHECK = (
            "היי 👋 רק לוודא שהכל תקין עם השירות מ{pro_name}.\n"
            "העבודה הסתיימה?\n"
            "*1* — כן, הסתיים\n"
            "*2* — עדיין לא\n"
            "*3* — איש המקצוע לא הגיע"
        )
        COMPLETION_ACK = (
            "🙌 מעולה, שמחתי לשמוע.\n"
            "איך היה השירות עם {pro_name}?\n" + RATING_SCALE_LINE
        )
        COMPLETION_NOT_YET_ACK = (
            "👍 אין בעיה, לא אטריד יותר בינתיים.\n"
            "כשהעבודה תסתיים אפשר לכתוב לי כאן *סיימתי* ונמשיך משם."
        )
        # PRO-45: answer to option *3*. It is a receipt for the report and
        # nothing else. `reassign_lead` runs before this reaches the customer
        # and messages them itself on every branch — a replacement search, a
        # pending-review notice, or the exhausted-attempts message — and the
        # dispatcher sends this one *last*, so any disposition claimed here
        # would overwrite whichever of those just landed.
        NO_SHOW_ACK = "🙏 מצטערים שאיש המקצוע לא הגיע. רשמנו את הדיווח."
        RATING_THANKS = "⭐ תודה רבה על הדירוג!"
        # PRO-122: the closing question used to accept only an exact "1"-"5".
        # Anything else fell through to the dispatcher, which — context already
        # cleared on completion — greeted the customer anew and asked their name.
        RATING_REPROMPT = "🙂 לא הצלחתי לקרוא את הדירוג.\n" + RATING_SCALE_LINE
        RATING_SKIPPED = "🙏 אין בעיה, תודה רבה על העדכון!"
        # §4/§5: one headline emoji, bold field labels; §6: the phone number
        # and every other Latin/numeric value sits at the end of its line.
        PRO_FOUND = (
            "🎉 *נמצא לך איש מקצוע!*\n\n"
            "*שם:* {pro_name}\n"
            "*טלפון:* {pro_phone}\n"
            "{profession_line}"
            "\n"
            "*פרטי העבודה*\n"
            "*תקלה:* {issue_type}\n"
            "*כתובת:* {full_address}\n"
            "*מועד:* {appointment_time}\n"
            "{price_line}"
            "{rating_line}"
            "\n{pro_name} ייצור איתך קשר בקרוב."
        )
        RATE_SERVICE = (
            "🙌 העבודה עם {pro_name} סומנה כהושלמה.\n"
            "איך היה השירות?\n" + RATING_SCALE_LINE
        )
        REVIEW_REQUEST = (
            "תודה על הדירוג! אפשר גם לכתוב ביקורת קצרה על החוויה —\n"
            "פשוט לכתוב אותה כאן, או להשיב *דלג* לדילוג."
        )
        REVIEW_SAVED = "תודה רבה! הביקורת שלך נשמרה."
        # PRO-122: REVIEW_REQUEST is framed as optional but had no skip path, so
        # "לא" / "לא תודה" was stored verbatim as the pro's public review.
        REVIEW_DECLINED = "🙏 תודה רבה!"
        ADDRESS_SAVED = "✅ הכתובת עודכנה בהצלחה."
        # §8: the English aside "(Location Pin)" is gone; §2: no bare masculine.
        ADDRESS_INVALID = (
            "❌ לא הצלחתי לזהות את הכתובת.\n"
            "אפשר לשתף מיקום, או לכתוב עיר ורחוב בצורה ברורה."
        )
        REQUEST_CANCELLED = "👍 הבקשה בוטלה. לפתיחת פנייה חדשה — פשוט לשלוח לי הודעה."
        CANCELLED_ACTIVE_LEAD = "✅ ביטלתי את העבודה כבקשתך. איש המקצוע עודכן."
        # PRO-118: a cancel keyword on a BOOKED job asks before acting —
        # text-only menu, destructive action only on an explicit '1'.
        CANCEL_CONFIRM_PROMPT = (
            "⚠️ רק לוודא — לבטל את העבודה שנקבעה?\n"
            "*1* — כן, לבטל\n"
            "*2* — לא, להשאיר אותה"
        )
        CANCEL_ABORTED = "👍 העבודה נשארת כמתוכנן."
        CANCEL_NO_ACTIVE = "לא מצאתי עבודה פעילה לביטול — ייתכן שהיא כבר עודכנה."
        RESCHEDULE_OFFER = (
            "אין בעיה, נתאם מועד חדש. הנה הזמנים הפנויים של איש המקצוע:\n"
            "{slots}\n\n"
            "אפשר להשיב במספר התור הרצוי, או *ביטול* כדי להשאיר את המועד הנוכחי."
        )
        RESCHEDULE_SUCCESS = (
            "✅ המועד שונה בהצלחה. איש המקצוע עודכן.\nמועד חדש: {new_time}"
        )
        RESCHEDULE_NO_SLOTS = (
            "לא מצאתי תורים פנויים כרגע אצל איש המקצוע.\n"
            "אפשר להשיב *ביטול* כדי לבטל את העבודה, או *נציג* כדי לתאם מול הצוות."
        )
        RESCHEDULE_INVALID_CHOICE = "אנא לבחור מספר תור מהרשימה."
        RESCHEDULE_CANCELLED = "המועד נשאר כפי שהיה."
        HELP_INFO = (
            "🛠️ אני העוזר החכם של פרולי.\n"
            "אני כאן כדי למצוא לך את איש המקצוע המתאים ביותר. "
            "אפשר לתאר לי את התקלה ואת המיקום, ואני אדאג לשאר."
        )
        # §7: PENDING_REVIEW and STATUS_PENDING_ADMIN_REVIEW describe the same
        # state — a lead sitting in PENDING_ADMIN_REVIEW waiting for a human to
        # route it — so they say the same thing. PENDING_REVIEW announces it,
        # STATUS_* answers a status pull about it, STILL_PENDING_REVIEW repeats
        # it when the customer writes again inside the short-circuit window.
        PENDING_REVIEW = (
            "👍 קיבלתי את הפנייה שלך.\n"
            "נציג/ה מצוות פרולי בוחן/ת אותה ידנית ומשבץ/ת איש מקצוע מתאים.\n"
            "אעדכן אותך כאן ברגע שיש חדש."
        )
        STILL_PENDING_REVIEW = (
            "🙏 הפנייה שלך עדיין נבחנת ידנית אצל צוות פרולי.\n"
            "אעדכן אותך כאן ברגע שיש איש מקצוע מתאים."
        )
        AWAITING_APPROVAL = (
            "👍 העברתי את הפרטים ואת המדיה לאיש המקצוע לאישור.\n"
            "אעדכן אותך ממש בקרוב."
        )
        AWAITING_APPROVAL_TRANSPARENT = (
            "✅ העברתי את הפנייה שלך ל{pro_name}.\n"
            "זמן המענה הממוצע שלו הוא כ-10 דקות.\n"
            "אעדכן אותך כאן ברגע שהעבודה תאושר."
        )
        YOU_ARE_WELCOME = "🛠️ בכיף! אני כאן אם צריך עוד משהו."
        STILL_WAITING = (
            "🙏 הפנייה שלך נמצאת עכשיו אצל איש המקצוע לאישור.\n"
            "אעדכן אותך ברגע שתתקבל תשובה."
        )
        # PRO-56 approval-SLA: offered to the customer when the pro stays silent.
        REASSIGN_OFFER = (
            "🕐 איש המקצוע עדיין לא אישר את הפנייה. מה עושים?\n"
            "*1* — לחפש איש מקצוע אחר\n"
            "*2* — להמתין עוד קצת"
        )
        REASSIGN_WAIT_ACK = (
            "🙏 אין בעיה, ממשיכים להמתין לאישור. אעדכן אותך ברגע שיש תשובה."
        )
        BOT_PAUSED_BY_PRO = (
            "📞 איש המקצוע ביקש לדבר איתך ישירות.\n"
            "אני מושהה כרגע — ההודעות הבאות יגיעו ממנו."
        )
        BOT_PAUSED_BY_CUSTOMER = "✅ קיבלתי, מעביר אותך לנציג/ה אנושי/ת."
        # PRO-121: this fires on a keyword, before any pro is matched — and
        # matching cannot even start without a city. The old copy ("I'm skipping
        # the rest of the details and summoning available pros right now")
        # promised both, then the AI carried on asking questions. It now claims
        # only what is true at this point: the request is flagged urgent and the
        # intake is being shortened. The "a pro has your call" claim lives in
        # AWAITING_APPROVAL_TRANSPARENT, which is sent after a real match.
        EMERGENCY_ACK = (
            "🚨 *זיהיתי מצב חירום.*\n"
            "סימנתי את הפנייה כדחופה ואני מקצר את התהליך — אשאל רק את מה שהכרחי "
            "כדי לאתר איש מקצוע פנוי בהקדם.\n"
            "בינתיים, חשוב לשמור על בטיחות."
        )
        # PRO-121: an emergency with no city cannot be matched at all, so ask
        # for the city alone — never the full five-part address gate.
        EMERGENCY_NEED_CITY = (
            "🚨 *זיהיתי מצב חירום.*\n"
            "כדי לאתר עבורך איש מקצוע עכשיו חסר לי רק דבר אחד — *באיזו עיר?*\n"
            "את שאר הפרטים נשלים אחר כך. בינתיים, חשוב לשמור על בטיחות."
        )
        # PRO-121: the emergency was declared while already holding for a pro's
        # approval. The offer is already out, and flagging the lead halves the
        # PRO-56 approval SLA — so say what actually happens next instead of
        # answering with the generic STILL_WAITING.
        EMERGENCY_WHILE_WAITING = (
            "🚨 *סימנתי את הפנייה שלך כדחופה.*\n"
            "איש המקצוע שקיבל את הקריאה מתבקש להגיב מיד, ואם לא תתקבל תשובה "
            "בדקות הקרובות אחפש עבורך מישהו אחר.\n"
            "בינתיים, חשוב לשמור על בטיחות."
        )
        # §7: `monitor_service.check_sla_deflection` clears the pause state and
        # books nothing at all. The old copy offered "אקבע לך שיחה טלפונית"
        # with a כן/לא menu whose replies fell straight through to the AI. This
        # says only what the code does: the handoff timed out and the bot is
        # back, so the customer can simply keep writing here.
        SLA_DEFLECTION_MESSAGE = (
            "🕐 איש המקצוע עדיין לא התפנה לענות, ואני חוזר לטפל בפנייה שלך.\n"
            "אפשר פשוט לכתוב לי כאן ואמשיך מכאן — או לכתוב *נציג* כדי לחזור "
            "ולהמתין למענה אנושי."
        )
        LOYALTY_OFFER = (
            "🏠 איזה כיף שחזרת אלינו!\n"
            "בעבר {pro_name} טיפל בך — לבדוק קודם אם הוא פנוי לעבודה הזו?\n"
            "*1* — כן, אשמח שזה יהיה הוא\n"
            "*2* — לא, לחפש מישהו אחר"
        )
        # PRO-119: the accept ack must match what actually happened. Sent only
        # when the lead was complete enough to dispatch and the pro really was
        # contacted this turn.
        LOYALTY_ACCEPTED_NOTIFYING = (
            "🙌 מעולה! פניתי ל{pro_name} עם הפרטים, ואעדכן אותך ברגע שיאשר."
        )
        # Sent when the address gate is not satisfied yet: the preference is
        # saved, but no pro was contacted — so the copy promises nothing and
        # asks for what is still missing ({missing} is the gate's own reason).
        LOYALTY_ACCEPTED_NEED_DETAILS = (
            "👌 מעולה, רשמתי ש{pro_name} יטפל בעבודה הזו.\n" "{missing}"
        )
        # PRO-119: the lead changed hands while the loyalty prompt was open —
        # neutral, promises nothing, since the race winner owns it now.
        LOYALTY_ALREADY_UPDATED = (
            "🙏 הפנייה שלך כבר עודכנה במערכת — ממשיכים לטפל בה ואעדכן אותך."
        )
        LOYALTY_DECLINED = "בסדר גמור, אחפש עבורך את איש המקצוע הפנוי והמתאים ביותר."
        LOYALTY_REPROMPT = (
            "🙂 לא בטוח שהבנתי. לבדוק מול איש המקצוע הקודם?\n"
            "*1* — כן, לבדוק מולו\n"
            "*2* — לא, לחפש מישהו אחר"
        )
        # §7: `pro_flow._execute_cancel` writes LeadStatus.CANCELLED — a
        # terminal status. It frees the slot, clears state and context, and
        # stops; `cancel_reason: "pro_cancelled"` is read nowhere and no
        # scheduler job looks at CANCELLED leads. Nothing is searching for a
        # replacement, so the copy must not say that it is — it offers the two
        # routes that do work instead.
        PRO_CANCELLED_BOOKING = (
            "⚠️ *עדכון:* איש המקצוע ביטל את העבודה שנקבעה.\n"
            "אפשר לכתוב לי כאן ואפתח פנייה חדשה מיד, "
            "או *נציג* כדי לדבר עם מישהו מהצוות."
        )
        # PRO-116: shown when a customer with a confirmed BOOKED job writes about
        # something else — so we don't silently open a second parallel lead.
        EXISTING_JOB_PROMPT = (
            "🛠️ יש לך כבר עבודה מאושרת עם {pro_name} ({issue} — {appointment}).\n"
            "זו פנייה חדשה?\n"
            "*1* — כן, בעיה חדשה\n"
            "*2* — זה לגבי העבודה הקיימת"
        )
        NEW_REQUEST_ACK = "🙂 בסדר גמור. מה הבעיה החדשה? אדאג לך לאיש מקצוע."
        EXISTING_JOB_HANDOFF = (
            "🛠️ אין בעיה! העברתי את ההודעה ל{pro_name} והוא יחזור אליך בהקדם."
        )

        # --- Status pull responses ---
        # §4: the sibling set agrees — every one of these opens with its own
        # status emoji and carries no second emoji.
        STATUS_NO_ACTIVE_LEAD = (
            "👋 אין לך כרגע פנייה פעילה אצלנו.\n"
            "לפתיחת פנייה חדשה — פשוט לתאר את הבעיה ואטפל בה."
        )
        STATUS_NEW = (
            "📨 *סטטוס הפנייה שלך*\n\n"
            "קיבלנו את הפנייה ואנחנו מאתרים איש מקצוע מתאים.\n"
            "*בעיה:* {issue}\n"
            "*עודכן לאחרונה:* {updated_at}"
        )
        STATUS_CONTACTED = (
            "📤 *סטטוס הפנייה שלך*\n\n"
            "הפנייה נשלחה לאיש מקצוע ואנחנו ממתינים לאישורו.\n"
            "*בעיה:* {issue}\n"
            "*עודכן לאחרונה:* {updated_at}"
        )
        STATUS_BOOKED = (
            "✅ *סטטוס הפנייה שלך*\n\n"
            "הפנייה אושרה ומשובצת.\n"
            "*איש מקצוע:* {pro_name}\n"
            "*בעיה:* {issue}\n"
            "*מועד:* {appointment_time}"
        )
        STATUS_PENDING_ADMIN_REVIEW = (
            "🕒 *סטטוס הפנייה שלך*\n\n"
            "נציג/ה מצוות פרולי בוחן/ת את הפנייה ידנית ומשבץ/ת איש מקצוע מתאים.\n"
            "אעדכן אותך כאן ברגע שיש חדש."
        )
        STATUS_COMPLETED = (
            "✔️ *סטטוס הפנייה שלך*\n\n"
            "העבודה הסתיימה. אם עוד לא דירגת את איש המקצוע, נשמח לדירוג."
        )
        STATUS_CANCELLED = "❌ *סטטוס הפנייה שלך*\n\nהפנייה בוטלה."
        STATUS_REJECTED_OR_CLOSED = (
            "ℹ️ *סטטוס הפנייה שלך*\n\nהפנייה נסגרה. אפשר לפתוח פנייה חדשה בכל עת."
        )

        # --- PRO-166: migrated from call sites ---
        # Optional lines composed into PRO_FOUND (built in pro_flow.py).
        PROFESSION_LINE = "*מקצוע:* {label}\n"
        QUOTED_PRICE_LINE = "\n*הערכת המחיר שקיבלת:* {quoted_price}₪\n"
        PRO_RATING_LINE = "\n*דירוג:* {rating:.1f} ({review_count} ביקורות)"
        # workflow_service's AWAITING_NEW_OR_EXISTING re-prompt. §3: same menu
        # shape and same option wording as EXISTING_JOB_PROMPT, which is the
        # question this re-asks.
        NEW_OR_EXISTING_REPROMPT = (
            "🙂 לא בטוח שהבנתי. זו פנייה חדשה?\n"
            "*1* — כן, בעיה חדשה\n"
            "*2* — זה לגבי העבודה הקיימת"
        )
        # lead_manager_service's address gate: the sentence plus the field
        # vocabulary it lists (attribute name -> Hebrew label, in gate order).
        ADDRESS_MISSING_PARTS = (
            "כדי שאיש המקצוע יגיע למקום המדויק נשאר רק להשלים לכתובת: "
            "{missing_fields}"
        )
        ADDRESS_FIELD_LABELS = {
            "street": "רחוב",
            "street_number": "מספר בית",
            "city": "עיר",
            "floor": "קומה",
            "apartment": "מספר דירה",
        }

    class Pro:
        # --- The canonical command list (§3) ------------------------------
        # One row per keyword, in the one menu format. `HELP_MENU` renders all
        # of them, grouped; `pro_flow._show_pro_dashboard` renders the subset
        # that applies right now. Both read these rows, so the dashboard and
        # the help menu can no longer advertise different keywords — which is
        # exactly what the three separate hand-written menus here used to do.
        # Every keyword below is matched by a `Messages.Keywords` list that
        # `handle_pro_text_command` dispatches on (§7).
        CMD_APPROVE = "*אשר* — קבלת העבודה שממתינה לך"
        CMD_REJECT = "*דחה* — דחיית העבודה שממתינה לך"
        CMD_DETAILS = "*פרטים* — פרטי העבודות הפעילות וקישורי ניווט"
        CMD_ACTIVE_JOBS = "*עבודות* — רשימת העבודות הפעילות"
        CMD_FINISH = "*סיימתי* — סיום עבודה פעילה"
        CMD_CANCEL = "*ביטול* — ביטול עבודה פעילה"
        CMD_SEARCH = "*חפש* — איתור לידים פנויים"
        CMD_RESUME = "*זמין* — חזרה לקבלת עבודות"
        CMD_PAUSE = "*הפסקה* — עצירה זמנית של קבלת עבודות"
        CMD_SUMMARY = "*סיכום* — סיכום הביצועים שלך"
        CMD_REVIEWS = "*ביקורות* — הביקורות האחרונות שלך"
        CMD_HISTORY = "*היסטוריה* — העבודות האחרונות שהושלמו"
        CMD_MENU = "*תפריט* — חזרה ללוח הבקרה"

        HELP_MENU = "\n".join(
            (
                "📖 *הפקודות של פרולי*",
                "",
                "*קבלת עבודות:*",
                CMD_APPROVE,
                CMD_REJECT,
                "",
                "*ניהול העבודה:*",
                CMD_DETAILS,
                CMD_ACTIVE_JOBS,
                CMD_FINISH,
                CMD_CANCEL,
                "",
                "*יוזמה וזמינות:*",
                CMD_SEARCH,
                CMD_RESUME,
                CMD_PAUSE,
                "",
                "*העסק שלך:*",
                CMD_SUMMARY,
                CMD_REVIEWS,
                CMD_HISTORY,
                "",
                CMD_MENU,
            )
        )

        # --- Messages sent to professionals -------------------------------
        # §7: this used to advertise 'עדיין עובד', a keyword no Keywords list
        # contained, so the reply fell through to the dashboard. Both options
        # below are now real commands (FINISH_COMMANDS / STILL_WORKING_COMMANDS).
        REMINDER = (
            "👋 רק מוודא לגבי העבודות הפתוחות שלך. סיימת?\n"
            "*סיימתי* — לסגירת עבודה\n"
            "*עדיין עובד* — להפסקת התזכורות על כולן"
        )
        # The answer to 'עדיין עובד': the finish nudges stop for this pro's
        # open jobs, which is exactly what the handler does.
        STILL_WORKING_ACK = (
            "👍 עודכן, לא אזכיר לך שוב על העבודות הפתוחות.\n"
            "כשתסיים — אפשר לכתוב *סיימתי* ואסגור אותן."
        )
        STALE_LEAD_REMINDER = (
            "👋 היי {pro_name}, העבודה אצל {customer_name} עדיין מסומנת כפתוחה.\n"
            "אם סיימת אותה — אפשר לכתוב *סיימתי* כדי לשחרר מקום לעבודות חדשות."
        )
        CUSTOMER_REPORTED_COMPLETION = "👍 הלקוח דיווח שהעבודה הסתיימה. הסטטוס עודכן."
        # PRO-45: replaces `SOS.PRO_LOST_LEAD` ("הועברה עקב חוסר מענה") for this
        # path — the lead is taken away for a reason the pro is entitled to be
        # told accurately, and "no response" is not that reason.
        CUSTOMER_REPORTED_NO_SHOW = (
            "⚠️ הלקוח דיווח שלא הגעת לעבודה שנקבעה.\n"
            "הבקשה הועברה להמשך טיפול והדיווח נרשם."
        )
        APPROVE_SUCCESS = "✅ העבודה אושרה! שלחתי ללקוח את הפרטים שלך."
        CALENDAR_UPDATE_SUCCESS = "\nהיומן עודכן בהצלחה."
        NO_PENDING_APPROVE = "לא מצאתי עבודה חדשה לאישור."
        ALREADY_RESPONDED = "כבר הגבת לקריאה זו, ולא ניתן לשנות את הבחירה כעת."
        # PRO-117: sent only after the rematch actually happened, so the copy
        # states a done fact rather than promising a future search.
        REJECT_SUCCESS = "העבודה נדחתה. הפנייה הועברה לאיש מקצוע אחר."
        # PRO-117: sent when reject → rematch found no replacement and the lead
        # was escalated to admin review — no "we'll find someone else" promise.
        REJECT_SUCCESS_ESCALATED = (
            "העבודה נדחתה. הפנייה הועברה לצוות פרולי לשיבוץ ידני."
        )
        NO_PENDING_REJECT = "לא מצאתי עבודה חדשה לדחייה."
        FINISH_SUCCESS = "✅ עודכן שהעבודה הסתיימה. תודה!"
        # PRO-33: the job is already COMPLETED when this is sent — the price is a
        # non-blocking follow-up, always skippable.
        FINISH_SUCCESS_ASK_PRICE = (
            "✅ עודכן שהעבודה הסתיימה. תודה!\n\n"
            "כמה גבית על העבודה?\n"
            "אפשר לשלוח מספר בשקלים, או *דלג* לדילוג."
        )
        FINAL_PRICE_RECORDED = "💰 נרשם: {price}₪. תודה!"
        FINAL_PRICE_SKIPPED = "👍 דילגתי על רישום המחיר."
        FINAL_PRICE_INVALID = "לא זוהה סכום — דילגתי על רישום המחיר. ממשיכים כרגיל."
        NO_ACTIVE_FINISH = "לא מצאתי עבודה פעילה לסיום."
        STATUS_PAUSED = (
            "☕ *הסטטוס שלך שונה ל'בהפסקה'.*\n"
            "לא יגיעו אליך הצעות עבודה חדשות עד שתכתוב *זמין*."
        )
        STATUS_RESUMED = "🚀 *הסטטוס שלך שונה ל'זמין'.*\nחזרת לקבל הצעות עבודה!"
        NO_PENDING_APPROVALS = "אין לך כרגע עבודות שממתינות לאישור."
        NO_ACTIVE_JOBS = "אין לך עבודות פעילות כרגע שניתן לסיים."
        SELECT_JOB_TO_FINISH = (
            "📋 איזו עבודה סיימת?\n"
            "{jobs_list}\n"
            "אפשר להשיב במספר העבודה, או *ביטול* ליציאה."
        )
        # Sent when pro is first matched — conversation still in progress, no action needed yet
        EARLY_LEAD_HEADER = "👀 *שיחה בתהליך*"
        LOYALTY_LEAD_HEADER = "🌟 *לקוח חוזר שלך ביקש אותך!*"
        EARLY_LEAD_DETAILS = "*תקלה:* {issue_type}\n*עיר:* {city}"
        EARLY_LEAD_FOOTER = (
            "\n\nאני אוסף מהלקוח את שאר הפרטים (כתובת, תאריך ושעה).\n"
            "תקבל הודעה עם כל הפרטים לאישורך — *אין צורך לפעול עכשיו.*"
        )
        # Sent when deal closes — ready for approval
        DEAL_CONFIRMED_HEADER = "✅ *הלקוח אישר! פרטי העבודה:*"
        EMERGENCY_LEAD_HEADER = "🚨 *קריאת חירום דחופה!*"
        NEW_LEAD_HEADER = "📢 *הצעת עבודה חדשה*"
        NEW_LEAD_DETAILS = (
            "*לקוח:* {customer_name}\n"
            "*כתובת:* {full_address}\n"
            "*פרטים נוספים:* {extra_info}\n"
            "*תקלה:* {issue_type}\n"
            "*מועד מועדף:* {appointment_time}"
        )
        NEW_LEAD_TRANSCRIPTION = "\n*תמליל:* {transcription}"
        NEW_LEAD_FOOTER = "\n\n*אשר* — לקבלת העבודה\n*דחה* — לדחייה"
        # Floor/apartment line rendered into {extra_info} of NEW_LEAD_DETAILS and
        # APPROVAL_REQUEST. '-' placeholders keep the line shape stable when a
        # field is missing, so the pro sees the same layout on every offer.
        EXTRA_INFO_LINE = "קומה {floor}, דירה {apartment}"
        # Header for the numbered media-links block appended to a lead offer.
        # Media is always sent as text links, never re-sent as files — see
        # notification_service.format_media_links for the policy.
        MEDIA_ATTACHED_HEADER = "*מדיה מצורפת:*"
        # §6: the customer's phone is Latin/numeric, so it ends its own line.
        APPROVAL_REQUEST = (
            "📋 *פרטי עבודה חדשה לאישורך*\n\n"
            "*לקוח:* {customer_name}\n"
            "*טלפון:* {customer_phone}\n"
            "*כתובת:* {full_address}\n"
            "*פרטים נוספים:* {extra_info}\n"
            "*תקלה:* {issue_type}\n"
            "*מועד:* {appointment_time}\n"
            "{price_line}"
            "\n*אשר* — לאישור העבודה\n"
            "*דחה* — לדחייה"
        )
        # PRO-55: the AI-quoted price the customer was promised, shown to the pro
        # before approval. Appended to APPROVAL_REQUEST only when a quote exists.
        APPROVAL_PRICE_LINE = "*הערכת מחיר שניתנה ללקוח:* {quoted_price}₪\n"
        # PRO-56: nudge a pro who hasn't approved within the SLA window.
        APPROVAL_NUDGE = (
            "⏰ ליד ממתין לאישורך כבר {minutes} דק'.\n"
            "*אשר* — לאישור\n"
            "*דחה* — לדחייה"
        )
        PAUSE_ACK = (
            "⏸️ הבוט הושהה ואפשר לדבר עם הלקוח ישירות.\n"
            "הבוט יחזור לפעולה אוטומטית בעוד שעתיים, או מיד עם *המשך*."
        )
        PAUSE_NOTIFICATION = (
            "🚨 הלקוח מבקש מענה אנושי.\n"
            "אפשר להיכנס לשיחה בוואטסאפ. הבוט יחזור לפעולה אוטומטית בעוד שעתיים."
        )
        CUSTOMER_CANCELLED = (
            "⚠️ *עדכון חשוב:* הלקוח/ה {customer_name} ביטל/ה את העבודה "
            "שנקבעה לכתובת {address}."
        )
        CUSTOMER_RESCHEDULED_SUCCESS = (
            "📅 *עדכון יומן:* הלקוח/ה {customer_name} בכתובת {address} "
            "שינה/תה את מועד העבודה.\n"
            "*מועד ישן:* {old_time}\n"
            "*מועד חדש:* {new_time}\n"
            "היומן שלך עודכן אוטומטית."
        )
        # PRO-116: the customer has a confirmed job with this pro and wants to
        # talk about it (not open a new request) — nudge the pro to reach out.
        CUSTOMER_EXISTING_JOB_QUERY = (
            "💬 *הלקוח/ה {customer_name} רוצה לעדכן או לשאול לגבי העבודה הקיימת*\n"
            "*תקלה:* {issue}\n"
            "*טלפון:* {customer_phone}\n\n"
            "אנא לפנות אליו בהקדם."
        )
        NAVIGATE_TO = "🚗 ניווט לכתובת:"
        NO_ACTIVE_JOBS_LIST = "👍 אין לך עבודות פעילות כרגע."
        NO_HISTORY = "עדיין אין לך עבודות שהושלמו."
        NO_REVIEWS = "עדיין אין לך ביקורות."
        # §6: no ' | '-separated mixed-direction rows — the address and the
        # numeric date/time each get their own line, ending in their value.
        ACTIVE_JOB_ROW = "{num}. [{status}] {issue}\n*כתובת:* {address}\n*מועד:* {time}"
        HISTORY_ROW = "{num}. {issue}\n*כתובת:* {address}\n*תאריך:* {date}"
        STATS_HEADER = "📊 *הסטטיסטיקות שלך*\n"
        STATS_BODY = (
            "*עבודות שהושלמו:* {completed}\n"
            "*עבודות פעילות:* {active}\n"
            "*דירוג ממוצע:* {rating}\n"
            "*ביקורות:* {reviews}\n"
            "*הצטרפת:* {joined}"
        )
        # §6: every numeric value sits at the end of its line.
        PRO_DASHBOARD_HEADER = (
            "🛠️ *שלום {pro_name}*\n"
            "*דירוג:* {rating}\n"
            "*סטטוס:* {status_text} {status_emoji}\n"
            "*עבודות פעילות:* {active_jobs}/{max_jobs}\n\n"
            "*מה אפשר לעשות עכשיו:*"
        )
        INTENT_DETECTED = (
            "🛠️ זיהיתי שאתה מדווח על תקלה. לעבור למצב לקוח כדי שאזמין לך איש מקצוע?\n"
            "*1* — כן, לעבור למצב לקוח\n"
            "*2* — לא, להמשיך כטכנאי"
        )
        INTENT_REPROMPT = (
            "🤔 לא הבנתי. לעבור למצב לקוח?\n"
            "*1* — כן, לעבור למצב לקוח\n"
            "*2* — לא, להמשיך כטכנאי"
        )
        SWITCHED_TO_CUSTOMER = (
            "👤 מעולה, עברת למצב לקוח. מכאן אטפל בך כמו בכל לקוח שלנו.\n"
            "אז ספר לי שוב — מה התקלה?"
        )
        SWITCH_CANCELLED = "👍 ממשיכים כרגיל במצב טכנאי."
        # Currently unsent: PRO-69 removed the auto-return at dispatch time (it fired
        # while the pro's own request was still live). Kept for the follow-up that
        # announces the return when their lead actually closes.
        AUTO_RETURNED_TO_PRO = (
            "🛠️ הקריאה שלך הועברה לאיש מקצוע לאישור, והחזרתי אותך למצב טכנאי "
            "כדי שתוכל להמשיך לנהל את העסק כרגיל."
        )
        SEARCH_RATE_LIMITED = "⏳ חיפשת לאחרונה. אפשר לחפש שוב בעוד {minutes} דקות."
        NO_STUCK_LEADS = "👍 אין לידים תקועים זמינים כרגע. אפשר לנסות שוב מאוחר יותר."
        # PRO-123: the `מצא` search now applies the same gates as routing, so it
        # has to be able to say *why* it refused instead of returning nothing.
        SEARCH_WHILE_PAUSED = (
            "☕ אתה כרגע במצב 'בהפסקה', ולכן החיפוש מושבת.\n"
            "אפשר לכתוב *זמין* כדי לחזור לקבל עבודות ואז לחפש שוב."
        )
        SEARCH_LOAD_FULL = (
            "🔧 יש לך כבר {active} עבודות פעילות, והמקסימום הוא {max_jobs}.\n"
            "אחרי סיום אחת מהן אפשר לחפש עבודה נוספת."
        )
        STUCK_LEAD_FOUND = (
            "📢 *נמצא ליד תקוע*\n\n"
            "*תקלה:* {issue}\n"
            "*עיר:* {city}\n"
            "*ממתין:* {wait_minutes} דק'\n\n"
            "*אשר* — לקחת את העבודה"
        )
        DETAILS_HEADER = "📋 *עבודות פעילות (מאושרות)*\n"
        # §6: no ' | '-separated mixed-direction rows — the phone number and the
        # two links each get their own line, ending in the Latin value.
        DETAILS_ROW = (
            "{num}. {issue}\n"
            "*מועד:* {appointment_time}\n"
            "*עיר:* {city}\n"
            "*טלפון:* {customer_phone}\n"
            "*צ'אט:* https://wa.me/{customer_phone_intl}\n"
            "*ניווט:* https://waze.com/ul?q={address_encoded}"
        )
        SELECT_JOB_TO_CANCEL = (
            "🚫 איזו עבודה לבטל?\n"
            "{jobs_list}\n"
            "אפשר להשיב במספר העבודה, או *ביטול* ליציאה."
        )
        CANCEL_SUCCESS = "✅ העבודה בוטלה. הלקוח עודכן."
        SUMMARY_BODY = (
            "📊 *סיכום ביצועים*\n\n"
            "*עבודות שהושלמו החודש:* {this_month}\n"
            '*סה"כ עבודות:* {total_completed}\n'
            "*עבודות פעילות כרגע:* {active}\n"
            "*דירוג ממוצע:* {rating}\n\n"
            "{motivation}"
        )
        SUMMARY_MOTIVATION_GREAT = "כל הכבוד! אתה מהטובים שלנו. המשך כך."
        SUMMARY_MOTIVATION_GOOD = "עבודה טובה! כל עבודה מוסיפה לשם הטוב שלך."
        SUMMARY_MOTIVATION_START = "תחילת דרך! ביצועים מצוינים מתחילים מהצעד הראשון."
        NO_REVIEWS_WITH_TEXT = (
            "💬 עדיין אין ביקורות כתובות. אחרי כל עבודה לקוחות יכולים להשאיר לך ביקורת."
        )
        # §4 one emoji, §6 no ' | ' row: the two numbers move onto their own
        # line, each at the end of a Hebrew phrase.
        REVIEWS_HEADER = (
            "💬 *הביקורות האחרונות שלך*\n" "ממוצע {rating:.1f} מתוך {count} דירוגים\n"
        )
        REVIEW_TEXT_ROW = '  ⭐{rating} — "{comment}"'
        DASHBOARD_TIP = "\nלרשימת הפקודות המלאה — *עזרה*."

        # --- PRO-166: migrated verbatim from app/services/pro_flow.py ---
        # Status vocabulary rendered into ACTIVE_JOB_ROW and the dashboard.
        STATUS_LABELS = {
            "new": "ממתין",
            "contacted": "ממתין",
            "booked": "מאושר",
            "completed": "הושלם",
            "rejected": "נדחה",
            "cancelled": "בוטל",
            "closed": "סגור",
            "pending_admin_review": "ממתין לבדיקת מנהל",
        }
        STATUS_AVAILABLE = "זמין"
        STATUS_ON_BREAK = "בהפסקה"
        ACTION_CANCELLED = "👍 הפעולה בוטלה."
        INVALID_JOB_SELECTION = "אנא לבחור מספר מהרשימה, או לכתוב *ביטול* ליציאה."
        NO_PAUSED_CONVERSATION = "אין שיחה מושהית כרגע."
        BOT_RESUMED = "✅ הבוט חזר לפעולה."
        BOT_ALREADY_ACTIVE = "הבוט כבר פעיל."
        JOB_SELECT_ROW = "{num}. {name} — {city} ({issue})"
        ACTIVE_JOBS_HEADER = "🔄 *עבודות פעילות*\n"
        ACTIVE_JOBS_TOTAL = '\n*סה"כ:* {count} עבודות'
        HISTORY_HEADER = "📋 *10 העבודות האחרונות שהושלמו*\n"
        RATING_NONE = "אין עדיין"

        # --- PRO-166: migrated from app/scheduler.py (daily agenda) ---
        DAILY_AGENDA_HEADER = (
            "☀️ *בוקר טוב {pro_name}!*\nהנה העבודות שלך להיום ({date}):"
        )
        # PRO-168 §7: this row used to render `job.get("details")` — a field
        # the bot never writes. Only the admin panel does
        # (`admin_panel/core/lead_queries.py`), and it mirrors the same value
        # into `issue_type` anyway, so every agenda line for a bot-created lead
        # rendered the literal fallback "פרטים חסרים". `issue_type` is the
        # strict superset. §6: the time and the phone are numeric, so each
        # ends its own labelled line rather than opening one.
        DAILY_AGENDA_ROW = "\n{issue}\n*שעה:* {time}\n*טלפון:* {phone}\n"
        DAILY_AGENDA_FOOTER = "\nשיהיה יום מוצלח!"

    class Admin:
        # PRO-166 migrated the ניהול wizard's copy here; PRO-168 brought it into
        # the standard voice — the bare "בוטל.", the transliteration
        # "פרופסיונלי" and the un-emoji'd error strings are gone, and the whole
        # register now agrees on one emoji convention (§4).
        # Sent to the ADMIN's own WhatsApp chat, never to customers or pros.
        NO_STUCK_LEADS = "✅ אין לידים תקועים כרגע."
        STUCK_LEADS_HEADER = "📋 *לידים הממתינים לטיפול:*\n"
        WAIT_MINUTES = "{wait_minutes}ד'"
        STUCK_LEAD_ROW = "{num}. {city} — {issue} (ממתין {wait})"
        SELECT_PROMPT = "\nאפשר להשיב במספר לבחירה, או *ביטול* ליציאה."
        CANCELLED = "👍 בוטל. אפשר להתחיל מחדש עם *ניהול*."
        INVALID_NUMBER = "⚠️ מספר לא חוקי. אפשר לנסות שוב, או *ביטול* ליציאה."
        ACTION_MENU = (
            "📋 בחרת בליד. למי להעביר אותו?\n"
            "*1* — קח את הליד לעצמך\n"
            "*2* — הצג רשימת אנשי מקצוע פנויים"
        )
        NO_ADMIN_PRO_PROFILE = (
            "⚠️ לא נמצא פרופיל איש מקצוע למנהל. אפשר לבחור באפשרות *2*."
        )
        LEAD_NOT_FOUND = "⚠️ הליד לא נמצא. אפשר להתחיל מחדש עם *ניהול*."
        NO_AVAILABLE_PROS = "⚠️ לא נמצאו אנשי מקצוע פנויים לליד זה."
        AVAILABLE_PROS_HEADER = "👷 *אנשי מקצוע פנויים:*\n"
        PRO_ROW = "{num}. {name} (דירוג: {rating})"
        INVALID_OPTION = "⚠️ אפשרות לא חוקית. אפשר להשיב *1* או *2*."
        PRO_NOT_FOUND = "⚠️ איש המקצוע לא נמצא. אפשר להתחיל מחדש עם *ניהול*."
        ASSIGN_SUCCESS = "✅ הליד הועבר ל-{pro_name}."
        ASSIGN_LEAD_LOOKUP_MISSED = (
            "⚠️ הליד לא נמצא לאחר העדכון — יש לבדוק בפאנל הניהול וליצור "
            "קשר ידני עם {pro_name} ועם הלקוח."
        )
        ASSIGN_OFFER_FAILED = (
            "⚠️ הליד שויך ל-{pro_name}, אבל שליחת ההצעה אליו נכשלה "
            "(ייתכן שחלון 24 השעות שלו סגור). יש ליצור איתו קשר ידנית."
        )

    class SOS:
        CUSTOMER_REASSIGNING = (
            "⏳ מתנצל על ההמתנה — אני מאתר עבורך איש מקצוע זמין יותר כעת."
        )
        NO_PRO_AVAILABLE = (
            "😔 לא הצלחתי למצוא איש מקצוע זמין לבקשתך כרגע.\n"
            "אפשר לנסות שוב מאוחר יותר, או לכתוב *נציג* כדי לדבר עם מישהו מהצוות."
        )
        # PRO-63: sent when a lead exhausts MAX_REASSIGNMENTS. This is the worst
        # moment a customer can have with Proli — they have been failed three
        # times — so the copy hands them to a human instead of dismissing them.
        # Deliberately: no apology-spiral, no "try again later" dead end, and a
        # concrete commitment ("תוך שעה") that the immediate admin alert in
        # reassign_lead actually backs. Hedged with "בשעות הפעילות" so the promise
        # stays honest for a lead that escalates at 02:00.
        MAX_REASSIGNMENTS_REACHED = (
            "לא הצלחתי למצוא זמינות מיידית, מעביר אותך לנציג — "
            "נחזור אליך תוך שעה בשעות הפעילות."
        )
        # The operator's WhatsApp alerts (ADMIN_MAX_REASSIGNMENTS,
        # ADMIN_REPORT_*, ADMIN_ALERT) were retired by PRO-88 and deleted by
        # PRO-166 — the admin never messages the bot, so under Meta Cloud API
        # their 24h window is permanently closed. Operator alerts page via
        # notification_service.page_operator() → Sentry → email (PRO-75). See
        # docs/WHATSAPP_TEMPLATE_CATALOG.md before adding any operator alert.
        PRO_LOST_LEAD = "העבודה הועברה לאיש מקצוע אחר עקב חוסר מענה."

        TO_USER_WITH_PRO = (
            "✅ קיבלתי! העברתי את בקשתך לאיש המקצוע שלך.\n"
            "הוא ייצור איתך קשר בהקדם האפשרי.\n"
            "אם לא תקבל/י מענה תוך זמן קצר, אפשר לפנות אלינו שוב."
        )
        TO_USER_NO_PRO = (
            "✅ קיבלתי! העברתי את פנייתך לצוות התמיכה של פרולי.\n"
            "נחזור אליך בהקדם האפשרי."
        )
        PRO_ALERT = (
            "⚠️ *הלקוח שלך צריך עזרה*\n\n"
            "*טלפון:* {phone}\n"
            "*הודעה:* {last_message}\n\n"
            "אנא לפנות אליו בהקדם האפשרי."
        )

    class Alerts:
        # PRO-20 — infra paging for WhatsApp account deauth (SPOF).
        # The WA-down page is now an out-of-band page_critical → Sentry email
        # (PRO-75); we never send a WA-down alert over WhatsApp. Only the recovery
        # notice (instance authorized again) goes over WhatsApp.
        WHATSAPP_RECOVERED = (
            "✅ *מערכת פרולי התאוששה*\n\n"
            "חיבור הוואטסאפ חזר למצב 'authorized'.\n"
            "ההודעות מעובדות כרגיל."
        )

    class Consent:
        REQUEST = (
            "👋 שלום וברוכים הבאים לפרולי.\n\n"
            "לפני שמתחילים, חשוב לנו ליידע אותך:\n"
            "אנחנו שומרים את מספר הטלפון, ההודעות והמיקום שלך "
            "כדי לחבר אותך עם אנשי מקצוע מתאימים.\n\n"
            "המידע מאובטח ומשותף רק עם איש המקצוע שמטפל בפנייה שלך, "
            "ובכל עת אפשר לבקש למחוק אותו.\n\n"
            "*כן* — להמשיך\n"
            "*לא* — לביטול"
        )
        ACCEPTED = "✅ תודה! אפשר להתחיל. במה אוכל לעזור?"
        DECLINED = "🙏 הבנתי, לא נשמור מידע עליך. אם תשנה/י את דעתך — פשוט לשלוח הודעה."
        ACCEPT_KEYWORDS = [
            "כן",
            "אישור",
            "yes",
            "ok",
            "אוקי",
            "בסדר",
            "מסכים",
            "מסכימה",
        ]
        DECLINE_KEYWORDS = ["לא", "no", "ביטול", "cancel"]

    class Onboarding:
        WELCOME = (
            "👋 ברוכים הבאים להרשמה כאיש מקצוע בפרולי!\n\n"
            "כמה שאלות קצרות ונבנה את הפרופיל שלך.\n"
            "בסיום, מנהל המערכת יאשר את הפרופיל ותתחיל לקבל עבודות.\n\n"
            "מה *שם העסק* שלך?"
        )
        # §3/§4: the seven 1️⃣-7️⃣ emoji digits became the canonical menu format.
        # TYPE_MAP still matches the plain digits "1"-"7" the pro replies with.
        ASK_TYPE = (
            "✅ מעולה! מה *סוג המקצוע* שלך?\n"
            "*1* — אינסטלטור\n"
            "*2* — חשמלאי\n"
            "*3* — הנדימן\n"
            "*4* — מנעולן\n"
            "*5* — צבעי\n"
            "*6* — ניקיון\n"
            "*7* — כללי\n\n"
            "אפשר לשלוח את המספר או את שם המקצוע."
        )
        ASK_AREAS = (
            "👍 באילו *ערים או אזורים* אתה עובד?\n"
            "אפשר לשלוח רשימת ערים מופרדות בפסיקים.\n"
            "לדוגמה: תל אביב, רמת גן, חולון"
        )
        ASK_PRICES = (
            "💰 מה *המחירים* שלך? (אופציונלי)\n"
            "אפשר לשלוח רשימת שירותים ומחירים, או להשיב *דלג* לדילוג.\n"
            "לדוגמה:\n"
            "תיקון נזילה - 250₪\n"
            "החלפת ברז - 350₪"
        )
        CONFIRM = (
            "📋 *סיכום הפרופיל שלך*\n\n"
            "*שם:* {name}\n"
            "*מקצוע:* {type}\n"
            "*אזורים:* {areas}\n"
            "*מחירים:* {prices}\n\n"
            "הכל נכון?\n"
            "*אשר* — לשליחה\n"
            "*ביטול* — להתחלה מחדש"
        )
        SUCCESS = (
            "🎉 תודה! הפרופיל שלך נשלח לאישור.\n"
            "נעדכן אותך ברגע שהפרופיל יאושר ותתחיל לקבל עבודות."
        )
        CANCELLED = "❌ ההרשמה בוטלה. אפשר להתחיל מחדש בכל עת עם *הרשמה*."
        ALREADY_REGISTERED = "😊 כבר יש לך פרופיל במערכת!"
        PENDING_ALREADY = "⏳ הפרופיל שלך כבר ממתין לאישור. נעדכן אותך בקרוב!"
        APPROVED_NOTIFICATION = (
            "🎉 הפרופיל שלך אושר! מעכשיו יגיעו אליך הצעות עבודה. בהצלחה!"
        )
        REJECTED_NOTIFICATION = (
            "לצערנו הפרופיל שלך לא אושר בשלב זה. אפשר לפנות אלינו לפרטים נוספים."
        )
        INVALID_TYPE = (
            "לא הבנתי. אפשר לשלוח מספר בין *1* ל-*7*, או שם מקצוע "
            "(אינסטלטור, חשמלאי וכו')."
        )

        TYPE_MAP = {
            "1": "plumber",
            "אינסטלטור": "plumber",
            "שרברב": "plumber",
            "2": "electrician",
            "חשמלאי": "electrician",
            "3": "handyman",
            "הנדימן": "handyman",
            "4": "locksmith",
            "מנעולן": "locksmith",
            "5": "painter",
            "צבעי": "painter",
            "6": "cleaner",
            "ניקיון": "cleaner",
            "7": "general",
            "כללי": "general",
        }
        TYPE_LABELS = {
            "plumber": "אינסטלטור",
            "electrician": "חשמלאי",
            "handyman": "הנדימן",
            "locksmith": "מנעולן",
            "painter": "צבעי",
            "cleaner": "ניקיון",
            "general": "כללי",
        }

        # --- PRO-166: migrated from app/services/pro_onboarding_service.py ---
        NAME_LENGTH_ERROR = "שם העסק חייב להיות באורך 2 עד 100 תווים. אפשר לנסות שוב:"
        CITIES_PARSE_ERROR = "לא זיהיתי ערים. אפשר לשלוח רשימת ערים מופרדות בפסיקים:"
        CONFIRM_REPROMPT = "*אשר* — לשליחה\n*ביטול* — להתחלה מחדש"

    class System:
        # RESET_SUCCESS was removed 2026-08-27 (operator decision): a global
        # reset clears state/context silently, with no confirmation message.
        # PRO-166: the textual rendering of an inbound location message, shared
        # by app/api/routes/webhook.py and the Cloud API inbound parser so the
        # two cannot drift.
        LOCATION_AS_TEXT = "מיקום: {latitude}, {longitude}"
        # PRO-166: the list-picker button label on the (unused — see CLAUDE.md)
        # interactive-list transport path in cloud_api.py.
        LIST_PICKER_BUTTON = "בחירה"

    class Keywords:
        # Logic commands used in 'if' statements
        APPROVE_COMMANDS = ["אשר", "1", "approve"]
        REJECT_COMMANDS = ["דחה", "2", "reject"]
        FINISH_COMMANDS = ["סיימתי", "3", "finish", "done"]
        # PRO-168 §7: `Pro.REMINDER` advertises 'עדיין עובד' as the answer that
        # stops the finish nudges. It matched no list at all, so the reply fell
        # through to the dashboard and the reminders kept coming. Listed with
        # the forms a pro actually types, matched like every other command
        # (exact, after `_normalize`).
        STILL_WORKING_COMMANDS = [
            "עדיין עובד",
            "עדיין עובדת",
            "עוד עובד",
            "עוד עובדת",
            "עדיין בעבודה",
            "still working",
        ]
        # PRO-33: skip the optional "how much did you charge?" price prompt.
        SKIP_COMMANDS = ["דלג", "דילוג", "skip", "-"]
        ACTIVE_JOBS_COMMANDS = ["עבודות", "4", "jobs", "active"]
        HISTORY_COMMANDS = ["היסטוריה", "5", "history"]
        STATS_COMMANDS = ["דוח", 'דו"ח', "6", "stats", "report"]
        REVIEWS_COMMANDS = ["ביקורות", "פידבק", "7", "reviews", "ratings"]
        SEARCH_COMMANDS = ["מצא", "חפש", "search", "find"]
        DETAILS_COMMANDS = ["פרטים", "details"]
        CANCEL_BOOKED_COMMANDS = ["ביטול"]
        SUMMARY_COMMANDS = ["סיכום", "סטטיסטיקה"]
        # Explicit "I need service myself" switch for a registered pro.
        # Deterministic — never routed through the AI intent detector.
        CUSTOMER_MODE_COMMANDS = ["לקוח", "אני לקוח", "מצב לקוח", "customer"]
        RESET_COMMANDS = ["reset", "התחלה"]
        MENU_COMMANDS = ["תפריט", "menu"]
        HELP_COMMANDS = ["עזרה", "help"]
        # PRO-118: whole-token matched, so inflected/prefixed forms substring
        # matching caught by accident are listed explicitly — "לנציג"/"למנהל"
        # ("תעבירו אותי לנציג"), definite "הנציג", and the feminine forms.
        # A missed SOS means a customer asking for a human gets the AI instead,
        # so this list errs generous; "מנהל עבודה" stays excluded.
        SOS_COMMANDS = [
            "נציג",
            "לנציג",
            "הנציג",
            "נציגה",
            "אנושי",
            "מנהל",
            "למנהל",
            "מנהלת",
            "למנהלת",
            "admin",
            "sos",
        ]
        # PRO-118: word sequences that contain an SOS token but are not a
        # request for a human — "מנהל עבודה" is a construction foreman, a
        # profession a customer plausibly mentions when describing the job.
        SOS_EXCLUDE_PHRASES = ["מנהל עבודה"]
        # PRO-119: natural yes/no for the loyalty confirmation, matched as
        # whole tokens (app/core/text_matching), so "כן בבקשה" / "לא תודה"
        # work instead of being rejected into an unbounded re-prompt loop.
        AFFIRMATIVE_KEYWORDS = [
            "כן",
            "בטח",
            "אשמח",
            "סבבה",
            "מעולה",
            "בהחלט",
            "yes",
            "ok",
            "אוקי",
        ]
        NEGATIVE_KEYWORDS = [
            "לא",
            "לאו",
            "no",
            "nope",
            "אחר",
            "מישהו אחר",
        ]
        # PRO-118: matched as whole tokens/phrases only (app/core/text_matching).
        # The bare "טעות" was dropped — it cancelled BOOKED jobs from innocent
        # sentences like "שלחתי בטעות את הכתובת הלא נכונה". Because whole-token
        # matching no longer catches inflections by substring accident, the
        # common inflected forms are listed explicitly: "לבטל" ("אני רוצה
        # לבטל"), "מבטל"/"מבטלת" ("אני מבטל"), and "ביטול" — the reply the
        # RESCHEDULE_OFFER menu itself advertises. A false hit is cheap now:
        # a BOOKED cancel only asks for confirmation, never acts directly.
        CANCEL_KEYWORDS = [
            "בטל",
            "בטלי",
            "לבטל",
            "ביטול",
            "מבטל",
            "מבטלת",
            "אבטל",
            "תבטל",
            "בטלו",
            "עזוב",
            "עזבי",
            "לא משנה",
            "cancel",
            "nevermind",
        ]
        RESCHEDULE_KEYWORDS = [
            "לשנות שעה",
            "לשנות תאריך",
            "מועד אחר",
            "זמן אחר",
            "תאריך אחר",
            "reschedule",
            "לדחות את",
            "מועד חדש",
        ]
        REGISTER_COMMANDS = ["הרשמה", "להירשם", "register", "signup", "הצטרפות"]
        RESUME_COMMANDS = ["זמין", "חזרתי", "פעיל"]
        PAUSE_COMMANDS = ["חופשה", "הפסקה", "לא זמין"]
        BOT_RESUME_COMMANDS = ["המשך", "resume", "חזור"]
        BOT_PAUSE_COMMANDS = ["השהה", "pause", "hold"]
        # PRO-121 — emergency detection is now whole-token (`contains_keyword`),
        # not substring, because the flag short-circuits a holding state rather
        # than merely tagging a lead: a false positive costs a dropped menu.
        #
        # This list is matched *exactly*. Bare "קצר" used to live here and was
        # deliberately dropped — as a whole token it is the everyday adjective
        # ("תיקון קצר", "הסבר קצר", "לזמן קצר"), and under the prefix-tolerant
        # matching below "לדחוף"-style false positives multiply. The unambiguous
        # electrical phrases replace it.
        EMERGENCY_KEYWORDS = [
            "מים בכל הבית",
            "קצר חשמלי",
            "קצר בחשמל",
            "קצר בלוח",
            "emergency",
            "urgent",
        ]
        # PRO-121 — stems matched through up to three leading Hebrew clitics
        # (ו/ה/ב/ל/מ/ש/כ), so "ההצפה", "מהצפה", "וכשהשריפה" all land. Substring
        # matching caught these for free; plain whole-token matching would not,
        # and a false *negative* in a safety detector is the worse failure.
        EMERGENCY_PREFIXABLE = [
            "דחוף",
            "דחופה",
            "דחופים",
            "דחיפות",
            "פיצוץ",
            "הצפה",
            "הצפות",
            "שריפה",
            "שריפות",
            "סכנה",
            "חירום",
        ]
        # PRO-121 — removed before matching, the way SOS uses "מנהל עבודה".
        # Negations ("זה לא דחוף, אני יכול לחכות") must not flag a lead and
        # halve its SLA; "לדחוף" is the everyday verb "to push", which the
        # clitic-tolerant match above would otherwise read as "דחוף".
        EMERGENCY_EXCLUDE_PHRASES = [
            "לא דחוף",
            "לא בהול",
            "לא חירום",
            "אין סכנה",
            "לא בסכנה",
            "לדחוף",
            "לדחוף את",
        ]
        RATING_OPTIONS = ["1", "2", "3", "4", "5"]
        # PRO-122: an opt-out of the rating / review prompts. Matched by *exact*
        # equality after strip+lower, never by `contains_keyword`: "לא" on its own
        # is a decline, but "לא היה טוב" is a genuine negative review and must be
        # saved rather than thrown away as a skip.
        SKIP_TOKENS = (
            "דלג",
            "דלגי",
            "לדלג",
            "לא",
            "לא תודה",
            "לא, תודה",
            "אין צורך",
            "לא רוצה",
            "skip",
            "no",
            "no thanks",
            "nope",
        )
        THANKS_KEYWORDS = [
            "תודה",
            "תודה רבה",
            "אחלה",
            "מעולה תודה",
            "thanks",
            "thank you",
            "תודה אחי",
            "מעולה",
            "בסדר גמור",
        ]

        # Completion check text tokens (used in handle_customer_completion_text).
        # Must stay in step with the "*1* — כן, הסתיים" option in
        # Customer.COMPLETION_CHECK: a customer who types the option back
        # verbatim is confirming completion.
        CUSTOMER_COMPLETION_INDICATOR = "כן, הסתיים"

        # Customer status pull — '?' must be exact match; words matched after .strip().lower()
        STATUS_COMMANDS_EXACT = ("?",)
        STATUS_COMMANDS_WORDS = ("סטטוס", "status")

    class Fallbacks:
        # Substitutes for missing lead fields in pro-facing messages. One home,
        # one language: three call sites used to hand-roll these, and the
        # monitor path showed English ("Unknown", "Pending") inside a Hebrew
        # message. All lead-offer fallbacks come from here.
        CUSTOMER_NAME = "לקוח"
        UNKNOWN = "לא ידוע"
        TIME_ASAP = "בהקדם"
        # PRO-168: the pro-facing job lists and the admin wizard hand-rolled
        # these inline. §9 — a value that can be empty formats through a
        # Fallback, never as an empty hole in a sentence.
        TIME_UNSET = "לא נקבע"
        CITY_UNKNOWN = "עיר לא ידועה"
        ISSUE_UNKNOWN = "בעיה לא ידועה"
        PRO_NAME_MISSING = "ללא שם"

    class Errors:
        AI_OVERLOAD = "סליחה, אני חווה עומס כרגע. אפשר לנסות שוב בעוד רגע."
        GENERIC_ERROR = "משהו השתבש. אפשר לנסות שוב."
        # PRO-21 — graceful throttle messages
        RATE_LIMITED = "⏳ הגיעו ממך הרבה הודעות ברצף. אפשר לנסות שוב בעוד רגע."
        DAILY_AI_CAP_REACHED = (
            "🙏 הגעת למכסת הפניות היומית. אפשר לנסות שוב מחר — "
            "ואם זה דחוף, לכתוב *נציג*."
        )

    class AISystemPrompts:
        ANALYZE_IMAGE = "[System: Analyze the image to identify the issue.]"
        TRANSCRIBE_AUDIO = (
            "[System: Transcribe the audio verbatim and analyze the intent.]"
        )
        ANALYZE_VIDEO = (
            "[System: Watch the video to identify the issue and describe what you see.]"
        )
        DEFAULT_SYSTEM = "You are a helpful assistant."
        PROLI_SCHEDULER_ROLE = "You are Proli, an AI scheduler for {pro_name}."
