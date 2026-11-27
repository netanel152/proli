"""The copy catalog — every sentence a customer, professional or operator reads.

PRO-168 rewrote this file against `docs/COPY_STYLE_GUIDE.md`, and PRO-245
brought the free-form copy to the owner's 2026-11-17 rules, the ones the
`Templates` bodies were already written to. In the guide's numbering:

* §1 voice — פרולי speaks as "אנחנו" ("קיבלנו", "נעדכן אותך"), never "אני".
  No em dash anywhere: a comma or a full stop does the job.
* §2 gender — rephrase so no gendered verb is needed (an infinitive after
  "אפשר"); never a slashed form; never bare masculine in customer copy. Pro
  copy addresses the pro in the direct masculine imperative ("כתוב אשר").
* §3 replies — a keyword asked for in the sentence ("לאישור כתוב אשר",
  "אפשר לכתוב ביטול"), or a lead-in ending `?`/`:` followed by `1 - text`
  lines. A pro command list is the same line shape with the keyword in place
  of the digit (`אשר - ...`). Every advertised digit/keyword has a handler (§7).
* §4 no emoji, §5 no `*bold*`. A structured card uses plain `label: value`
  lines. The one exception is `Customer.LIFE_THREATENING`, whose emergency
  numbers stay bold (owner-approved wording, PRO-222).
* §6 RTL safety — Latin/numeric placeholders end-of-line or on their own line.
* §7 no unmeasured claims — never state a number or a timeframe the code does
  not produce. Say what the system will do instead (PRO-58).
* §8 the product is **פרולי** in Hebrew copy; no trailing whitespace.

`tests/test_copy_rules.py` pins §1, §2, §4 and §5 over every user-facing class
here, so a retired pattern fails the build instead of reaching a phone.

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
        # mentions the דלג skip that `Keywords.SKIP_TOKENS` already handles.
        # Same wording as `Templates.RATE_SERVICE`.
        RATING_SCALE_LINE = "אפשר לענות במספר מ-1 (גרוע) עד 5 (מצוין), או לכתוב דלג."

        # "כן, הסתיימה" matches the template. A customer who types the option
        # back still contains `Keywords.CUSTOMER_COMPLETION_INDICATOR`.
        COMPLETION_CHECK = (
            "היי, רצינו לוודא שהכל הסתדר עם {pro_name}.\n"
            "העבודה הסתיימה? אפשר לענות במספר:\n"
            "1 - כן, הסתיימה\n"
            "2 - עדיין לא\n"
            "3 - איש המקצוע לא הגיע"
        )
        COMPLETION_ACK = (
            "איזה כיף לשמוע.\n" "איך היה השירות של {pro_name}?\n" + RATING_SCALE_LINE
        )
        COMPLETION_NOT_YET_ACK = "אין בעיה.\nכשהעבודה תסתיים, אפשר לכתוב כאן סיימתי."
        # PRO-45: answer to option 3. It is a receipt for the report and
        # nothing else. `reassign_lead` runs before this reaches the customer
        # and messages them itself on every branch — a replacement search, a
        # pending-review notice, or the exhausted-attempts message — and the
        # dispatcher sends this one *last*, so any disposition claimed here
        # would overwrite whichever of those just landed.
        NO_SHOW_ACK = "מצטערים שאיש המקצוע לא הגיע. רשמנו את הדיווח."
        RATING_THANKS = "תודה רבה על הדירוג!"
        # PRO-122: the closing question used to accept only an exact "1"-"5".
        # Anything else fell through to the dispatcher, which — context already
        # cleared on completion — greeted the customer anew and asked their name.
        RATING_REPROMPT = "לא הצלחנו להבין את הדירוג.\n" + RATING_SCALE_LINE
        RATING_SKIPPED = "אין בעיה, תודה על העדכון!"
        # §5: plain `label: value` lines; §6: the phone number and every other
        # Latin/numeric value sits at the end of its line. The closing line is
        # `Templates.PRO_FOUND`'s.
        PRO_FOUND = (
            "מצאנו לך איש מקצוע!\n\n"
            "שם: {pro_name}\n"
            "טלפון: {pro_phone}\n"
            "{profession_line}"
            "\n"
            "פרטי העבודה:\n"
            "תקלה: {issue_type}\n"
            "כתובת: {full_address}\n"
            "מועד: {appointment_time}\n"
            "{price_line}"
            "{rating_line}"
            "\nאיש המקצוע יתקשר אליך בקרוב כדי לתאם הגעה."
        )
        # The first line is sent alone, as a plain notice, while WhatsApp quality
        # is tightened (`pro_flow`), so it states a fact and asks nothing.
        RATE_SERVICE = (
            "העבודה עם {pro_name} הסתיימה.\n" "איך היה השירות?\n" + RATING_SCALE_LINE
        )
        REVIEW_REQUEST = (
            "תודה על הדירוג!\n"
            "אם בא לך להוסיף כמה מילים על החוויה, אפשר לכתוב אותן כאן, "
            "או לכתוב דלג."
        )
        REVIEW_SAVED = "תודה רבה! הביקורת נשמרה."
        # PRO-122: REVIEW_REQUEST is framed as optional but had no skip path, so
        # "לא" / "לא תודה" was stored verbatim as the pro's public review.
        REVIEW_DECLINED = "תודה רבה!"
        ADDRESS_SAVED = "הכתובת עודכנה."
        # §8: the English aside "(Location Pin)" is gone; §2: no bare masculine.
        # Asks for what the gate needs first (street, number, city), in the
        # gate's order, so following it does not cost another turn.
        ADDRESS_INVALID = (
            "לא הצלחנו לזהות את הכתובת.\n"
            "אפשר לכתוב רחוב, מספר בית ועיר, או לשתף מיקום."
        )
        REQUEST_CANCELLED = "הבקשה בוטלה. כשצריך משהו חדש, אפשר פשוט לכתוב לנו כאן."
        CANCELLED_ACTIVE_LEAD = "ביטלנו את העבודה, ועדכנו את איש המקצוע."
        # PRO-118: a cancel keyword on a BOOKED job asks before acting —
        # text-only menu, destructive action only on an explicit '1'.
        CANCEL_CONFIRM_PROMPT = (
            "רק לוודא, לבטל את העבודה שנקבעה?\n" "1 - כן, לבטל\n" "2 - לא, להשאיר אותה"
        )
        CANCEL_ABORTED = "בסדר, העבודה נשארת כמו שתוכננה."
        CANCEL_NO_ACTIVE = "לא מצאנו עבודה פעילה לביטול. ייתכן שהיא כבר עודכנה."
        # A cancel keyword on a request still awaiting the pro's approval (a NEW
        # lead) asks the same way PRO-118 asks for a BOOKED job, and is handled
        # by the same AWAITING_CANCEL_CONFIRMATION guard: 1, כן or a cancel
        # keyword confirms; anything else keeps the request with the pro.
        CANCEL_REQUEST_CONFIRM_PROMPT = (
            "רק לוודא, לבטל את הבקשה שמחכה לאישור של איש המקצוע?\n"
            "1 - כן, לבטל\n"
            "2 - לא, להשאיר אותה"
        )
        # PRO-105: both "really cancel?" prompts above also go out with two
        # reply buttons. Their ids are the 1 and 2 the text advertises, and each
        # label is its line's own words (at most 20 characters).
        CANCEL_YES_BUTTON = "כן, לבטל"
        CANCEL_NO_BUTTON = "לא, להשאיר אותה"
        # PRO-105: a customer's reply buttons and list rows name the prompt they
        # answer. A tap on an older one changes nothing and says so: the first
        # while a newer question of the same kind is open, the second when none is.
        STALE_BUTTON_TAP = (
            "הכפתור הזה שייך להודעה קודמת, אז לא שינינו כלום.\n"
            "אפשר לענות על השאלה האחרונה ששלחנו."
        )
        STALE_BUTTON_TAP_EXPIRED = (
            "הבחירה הזו שייכת להודעה שכבר לא בתוקף, אז לא שינינו כלום.\n"
            "אפשר לכתוב לנו כאן מה צריך עכשיו."
        )
        REQUEST_WITHDRAWN = (
            "הבקשה בוטלה, ועדכנו את איש המקצוע.\n"
            "אם צריך עוד משהו, אפשר לכתוב לנו כאן."
        )
        CANCEL_REQUEST_KEPT = (
            "הבקשה נשארת אצל איש המקצוע לאישור.\n" "נעדכן אותך כאן ברגע שתהיה תשובה."
        )
        # The pro approved while the prompt was open: the request is a booked
        # job now, and cancelling that is the BOOKED path, which asks again.
        CANCEL_REQUEST_ALREADY_BOOKED = (
            "איש המקצוע כבר אישר את הבקשה, והעבודה נקבעה.\n"
            "אם עדיין צריך לבטל אותה, אפשר לכתוב ביטול."
        )
        CANCEL_REQUEST_MOVED = (
            "הבקשה כבר עברה לאיש מקצוע אחר, והיא מחכה לאישור שלו.\n"
            "אם עדיין צריך לבטל אותה, אפשר לכתוב ביטול."
        )
        # Answer to התחלה while a request is with a pro. The reset still clears
        # the conversation, but it does not withdraw the offer, so say so.
        RESET_REQUEST_STILL_OPEN = (
            "הבקשה שלך עדיין אצל איש המקצוע לאישור.\n"
            "אם צריך לבטל אותה, אפשר לכתוב ביטול."
        )
        RESCHEDULE_OFFER = (
            "אין בעיה, נקבע מועד חדש. אלה הזמנים הפנויים של איש המקצוע:\n"
            "{slots}\n\n"
            "אפשר לענות במספר של המועד שמתאים, "
            "או לכתוב ביטול כדי להשאיר את המועד הנוכחי."
        )
        RESCHEDULE_SUCCESS = (
            "המועד עודכן, ועדכנו את איש המקצוע.\n" "המועד החדש: {new_time}"
        )
        RESCHEDULE_NO_SLOTS = (
            "כרגע אין לאיש המקצוע זמנים פנויים אחרים.\n"
            "אפשר לכתוב ביטול כדי לבטל את העבודה, או לכתוב נציג כדי לתאם מול הצוות."
        )
        # The picker's own exit is ביטול (handle_reschedule_selection): it keeps
        # the current time and closes the list, so every re-prompt names it.
        RESCHEDULE_INVALID_CHOICE = (
            "המספר הזה לא ברשימה. אפשר לענות במספר מהרשימה, "
            "או לכתוב ביטול כדי להשאיר את המועד הנוכחי."
        )
        # A listed slot was taken by someone else before the customer's pick
        # reached us; the list below is rebuilt from what is free now.
        RESCHEDULE_SLOT_TAKEN = (
            "המועד הזה נתפס בינתיים. אלה הזמנים שפנויים עכשיו:\n"
            "{slots}\n\n"
            "אפשר לענות במספר של המועד שמתאים, "
            "או לכתוב ביטול כדי להשאיר את המועד הנוכחי."
        )
        # Same moment, nothing left to offer: the picker closes, the booking stays.
        # After it, ביטול reaches the BOOKED cancel prompt and נציג the human handoff.
        RESCHEDULE_SLOT_TAKEN_NO_SLOTS = (
            "המועד הזה נתפס בינתיים, וכרגע אין לאיש המקצוע זמנים פנויים אחרים.\n"
            "המועד הנוכחי נשאר כמו שהיה.\n"
            "כדי לבטל את העבודה אפשר לכתוב ביטול, ולתיאום מול הצוות אפשר לכתוב נציג."
        )
        RESCHEDULE_CANCELLED = "בסדר, המועד נשאר כמו שהיה."
        # PRO-105: RESCHEDULE_OFFER and RESCHEDULE_SLOT_TAKEN also open as an
        # interactive list, one row per numbered line, each row's id its number
        # so a tap is the typed digit. Meta's limits: opener at most 20
        # characters, section title and row title at most 24, description 72.
        RESCHEDULE_LIST_BUTTON = "בחירת מועד"
        RESCHEDULE_LIST_SECTION = "זמנים פנויים"
        # The row title is compact ("1. ה׳ 29.11 · 10:00", at most 19) so it
        # never reaches 24, and opens with the number the text asks for; the
        # description under it spells the slot out.
        RESCHEDULE_ROW_TITLE = "{number}. {day_letter} {date} · {time}"
        RESCHEDULE_ROW_DESCRIPTION = "יום {weekday}, {date} בשעה {time}"
        # Monday first, as datetime.weekday() counts and Pro.WEEKDAY_NAMES lists.
        WEEKDAY_LETTERS = ("ב׳", "ג׳", "ד׳", "ה׳", "ו׳", "ש׳", "א׳")
        # A tap on a row of an earlier list while a newer one is open: its
        # number may name another slot now, so nothing is booked.
        RESCHEDULE_STALE_CHOICE = (
            "הבחירה הזו הגיעה מרשימה קודמת, אז לא שינינו את המועד.\n"
            "אפשר לבחור מהרשימה האחרונה, "
            "או לכתוב ביטול כדי להשאיר את המועד הנוכחי."
        )
        # PRO-105: RESCHEDULE_NO_SLOTS and RESCHEDULE_SLOT_TAKEN_NO_SLOTS offer
        # two words, ביטול and נציג; these buttons send exactly those words.
        CANCEL_JOB_BUTTON = "ביטול העבודה"
        HUMAN_BUTTON = "תיאום עם נציג"
        HELP_INFO = (
            "כאן פרולי. אנחנו מוצאים לך את איש המקצוע שמתאים לתקלה, "
            "כמו אינסטלטור, חשמלאי או מנעולן.\n"
            "אפשר לכתוב לנו מה קרה ואיפה, ואנחנו נדאג לשאר."
        )
        # §7: PENDING_REVIEW and STATUS_PENDING_ADMIN_REVIEW describe the same
        # state — a lead sitting in PENDING_ADMIN_REVIEW waiting for a human to
        # route it — so they say the same thing. PENDING_REVIEW announces it
        # (same words as `Templates.PENDING_REVIEW`), STATUS_* answers a status
        # pull about it, STILL_PENDING_REVIEW repeats it when the customer
        # writes again inside the short-circuit window.
        PENDING_REVIEW = (
            "קיבלנו את הפנייה שלך.\n"
            "הצוות שלנו יעבור עליה וימצא לך איש מקצוע מתאים. נעדכן אותך כאן."
        )
        STILL_PENDING_REVIEW = (
            "הצוות שלנו עדיין עובר על הפנייה שלך.\n"
            "נעדכן אותך כאן ברגע שנמצא איש מקצוע מתאים."
        )
        AWAITING_APPROVAL = (
            "העברנו את הפרטים ואת הקבצים לאיש המקצוע לאישור.\n"
            "נעדכן אותך כאן ברגע שתהיה תשובה."
        )
        # PRO-58 §7: the middle line used to read "זמן המענה הממוצע שלו הוא
        # כ-10 דקות" — a per-pro statistic, stated as fact, that nothing
        # measures. It is simply gone.
        #
        # The obvious replacement was the PRO-56 SLA's own promise ("if no
        # answer comes I will ask whether to look for someone else"), and it was
        # written that way first. Review found that only one of the three
        # senders arms the state that promise depends on:
        # admin_flow.assign_lead_to_pro never sets the customer's state at all,
        # and monitor_service.reassign_lead clears it for a CONTACTED lead, so
        # check_pro_approval_sla skips both on every tick. That would have been
        # this issue's own defect, one sentence further along. What is left is
        # true from all three senders: we will say when it is approved.
        AWAITING_APPROVAL_TRANSPARENT = (
            "העברנו את הפנייה שלך אל {pro_name}.\n" "נעדכן אותך כאן ברגע שהעבודה תאושר."
        )
        YOU_ARE_WELCOME = "בכיף! אם צריך עוד משהו, אנחנו כאן."
        STILL_WAITING = (
            "הפנייה שלך עדיין אצל איש המקצוע לאישור.\n"
            "נעדכן אותך כאן ברגע שתהיה תשובה."
        )
        # PRO-56 approval-SLA: offered to the customer when the pro stays silent.
        REASSIGN_OFFER = (
            "איש המקצוע עוד לא אישר את הפנייה. מה עושים? אפשר לענות במספר:\n"
            "1 - לחפש איש מקצוע אחר\n"
            "2 - להמתין עוד קצת"
        )
        REASSIGN_WAIT_ACK = (
            "אין בעיה, ממשיכים לחכות לאישור. נעדכן אותך כאן ברגע שתהיה תשובה."
        )
        # PRO-238: a "1"/"2" answering REASSIGN_OFFER after the situation it
        # asked about has moved on. Each says what is true now; none changes
        # anything.
        REASSIGN_REPLY_PRO_APPROVED = (
            "איש המקצוע כבר אישר את הפנייה, אז אין צורך לחפש מישהו אחר.\n"
            "כדי לראות את פרטי העבודה, אפשר לכתוב סטטוס."
        )
        REASSIGN_REPLY_ALREADY_MOVED = (
            "הפנייה כבר עברה לאיש מקצוע אחר, והיא מחכה לאישור שלו.\n"
            "נעדכן אותך כאן ברגע שתהיה תשובה."
        )
        REASSIGN_REPLY_REQUEST_CLOSED = (
            "הפנייה הזו כבר סגורה.\n"
            "אם צריך עוד משהו, אפשר לכתוב לנו מה קרה ונפתח פנייה חדשה."
        )
        BOT_PAUSED_BY_PRO = (
            "איש המקצוע רוצה לדבר איתך ישירות, והוא יחזור אליך מהמספר שלו.\n"
            "בינתיים המענה האוטומטי בשיחה הזו מושהה."
        )
        # The pause pages the operator and, on a booked job, tells the pro.
        # Nothing schedules a human reply, so this promises none.
        BOT_PAUSED_BY_CUSTOMER = (
            "קיבלנו, והעברנו את השיחה למענה אנושי.\n"
            "בינתיים המענה האוטומטי בשיחה הזו מושהה."
        )
        # PRO-121: this fires on a keyword, before any pro is matched — and
        # matching cannot even start without a city. The old copy ("I'm skipping
        # the rest of the details and summoning available pros right now")
        # promised both, then the AI carried on asking questions. It now claims
        # only what is true at this point: the request is flagged urgent and the
        # intake is being shortened. The "a pro has your call" claim lives in
        # AWAITING_APPROVAL_TRANSPARENT, which is sent after a real match.
        # PRO-222: urgent, not life-threatening (that is LIFE_THREATENING, which
        # sends nobody), so the copy no longer says "חירום".
        EMERGENCY_ACK = (
            "סימנו את הפנייה כדחופה.\n"
            "נשאל רק את מה שחייבים כדי למצוא איש מקצוע פנוי כמה שיותר מהר.\n"
            "בינתיים, חשוב לשמור על בטיחות."
        )
        # PRO-121: an emergency with no city cannot be matched at all, so ask
        # for the city alone — never the full five-part address gate.
        EMERGENCY_NEED_CITY = (
            "סימנו את הפנייה כדחופה.\n"
            "כדי למצוא איש מקצוע עכשיו, חסר לנו רק דבר אחד: באיזו עיר זה?\n"
            "את שאר הפרטים נשלים אחר כך. בינתיים, חשוב לשמור על בטיחות."
        )
        # PRO-222 — life-threatening: no pro, no address, no lead. Wording
        # approved by the owner 2026-11-17 (gas line extended the same day).
        # Numbers: מד"א 101, כבאות והצלה 102, משטרה 100 (Israel's national
        # emergency numbers). PRO-245 took out the emoji and the em dash and
        # left every instruction as it was: the headline and the three numbers
        # stay bold, the one bold `tests/test_copy_rules.py` allows.
        LIFE_THREATENING = (
            "*אם יש סכנה, קודם כל להתרחק מהמקום ולהתקשר עכשיו:*\n"
            "כבאות והצלה *102* (שריפה, עשן, ריח גז, פיצוץ)\n"
            'מד"א *101* (פגיעה בגוף או התחשמלות)\n'
            "משטרה *100*\n"
            "בריח גז: לא להדליק אש, אור או מכשיר חשמלי, "
            "לסגור את ברז הגז הראשי אם אפשר לעשות את זה בלי סיכון, "
            "לפתוח חלונות, לצאת החוצה ולהתקשר מחוץ לבית.\n"
            "פרולי אינה שירות חירום ולא תשלח איש מקצוע לקריאה הזו. "
            "הצוות שלנו עודכן ויחזור אליך כשהמצב בטוח."
        )
        # PRO-121: the emergency was declared while already holding for a pro's
        # approval. The offer is already out, and flagging the lead halves the
        # PRO-56 approval SLA — so say what actually happens next instead of
        # answering with the generic STILL_WAITING.
        EMERGENCY_WHILE_WAITING = (
            "סימנו את הפנייה שלך כדחופה.\n"
            "איש המקצוע שקיבל אותה צריך לענות מהר. אם התשובה תתעכב, "
            "נשאל אותך כאן אם לחפש איש מקצוע אחר.\n"
            "בינתיים, חשוב לשמור על בטיחות."
        )
        # §7: `monitor_service.check_sla_deflection` clears the pause state and
        # books nothing at all. The old copy offered "אקבע לך שיחה טלפונית"
        # with a כן/לא menu whose replies fell straight through to the AI. This
        # says only what the code does: the handoff timed out and the bot is
        # back in the chat, so the customer can simply keep writing here. It
        # names nobody and promises no call: the pause may be a customer SOS
        # (the operator was paged) or a pro's השהה (the pro paused it), and
        # nothing schedules anyone's reply.
        SLA_DEFLECTION_MESSAGE = (
            "סליחה על ההמתנה. המענה האוטומטי חזר לפעול בשיחה הזו, "
            "אז אפשר להמשיך לכתוב לנו כאן.\n"
            "כדי לבקש שוב מענה אנושי, אפשר לכתוב נציג."
        )
        LOYALTY_OFFER = (
            "איזה כיף שחזרת אלינו!\n"
            "בפעם הקודמת עבדת עם {pro_name}\n"
            "לבדוק קודם אם איש המקצוע פנוי גם הפעם?\n"
            "1 - כן, לבדוק מולו\n"
            "2 - לא, לחפש מישהו אחר"
        )
        # PRO-105: LOYALTY_OFFER and LOYALTY_REPROMPT go out with two reply
        # buttons, ids 1 and 2, each label its line's own words.
        LOYALTY_YES_BUTTON = "כן, לבדוק מולו"
        LOYALTY_NO_BUTTON = "לא, לחפש מישהו אחר"
        # PRO-119: the accept ack must match what actually happened. Sent only
        # when the lead was complete enough to dispatch and the pro really was
        # contacted this turn.
        LOYALTY_ACCEPTED_NOTIFYING = (
            "מעולה! פנינו אל {pro_name} עם הפרטים, ונעדכן אותך ברגע שיאשר."
        )
        # Sent when the address gate is not satisfied yet: the preference is
        # saved, but no pro was contacted — so the copy promises nothing and
        # asks for what is still missing ({missing} is the gate's own reason).
        LOYALTY_ACCEPTED_NEED_DETAILS = (
            "מעולה, נבדוק קודם מול {pro_name}.\n" "{missing}"
        )
        # PRO-119: the lead changed hands while the loyalty prompt was open —
        # neutral, promises nothing, since the race winner owns it now.
        LOYALTY_ALREADY_UPDATED = (
            "הפנייה שלך כבר עודכנה אצלנו. אנחנו ממשיכים לטפל בה ונעדכן אותך."
        )
        # PRO-215: the lead left a pro outside its trade. "נעביר" stays true when
        # nobody fits: PENDING_REVIEW then says the team assigns a suitable pro.
        MOVING_TO_FITTING_PRO = "נעביר את הפנייה לאיש מקצוע שמתאים לתקלה הזו."
        LOYALTY_DECLINED = "בסדר גמור, נחפש לך את איש המקצוע הפנוי שהכי מתאים."
        LOYALTY_REPROMPT = (
            "לא בטוחים שהבנו. לבדוק מול איש המקצוע הקודם?\n"
            "1 - כן, לבדוק מולו\n"
            "2 - לא, לחפש מישהו אחר"
        )
        # §7: `pro_flow._execute_cancel` writes LeadStatus.CANCELLED — a
        # terminal status. It frees the slot, clears state and context, and
        # stops; `cancel_reason: "pro_cancelled"` is read nowhere and no
        # scheduler job looks at CANCELLED leads. Nothing is searching for a
        # replacement, so the copy must not say that it is — it offers the two
        # routes that do work instead. Same words as the template.
        PRO_CANCELLED_BOOKING = (
            "עדכון מפרולי: איש המקצוע ביטל את העבודה שנקבעה.\n"
            "אם עדיין צריך, אפשר לכתוב לנו כאן ונפתח פנייה חדשה. "
            "לשיחה עם הצוות אפשר לכתוב נציג."
        )
        # PRO-116: shown when a customer with a confirmed BOOKED job writes about
        # something else — so we don't silently open a second parallel lead.
        # §6: the appointment is numeric, so it ends the line.
        EXISTING_JOB_PROMPT = (
            "יש לך כבר עבודה מאושרת עם {pro_name}: {issue}, {appointment}\n"
            "זו פנייה חדשה? אפשר לענות במספר:\n"
            "1 - כן, בעיה חדשה\n"
            "2 - לגבי העבודה הקיימת"
        )
        # PRO-105: EXISTING_JOB_PROMPT and NEW_OR_EXISTING_REPROMPT go out with
        # two reply buttons, ids 1 and 2, each label its line's own words (the
        # line lost its "זה" so the label fits Meta's 20 characters).
        NEW_REQUEST_BUTTON = "כן, בעיה חדשה"
        EXISTING_JOB_BUTTON = "לגבי העבודה הקיימת"
        NEW_REQUEST_ACK = "בסדר גמור. מה הבעיה החדשה?"
        # The pro gets CUSTOMER_EXISTING_JOB_QUERY, which asks them to call back;
        # nothing measures when they do, so no timeframe here.
        EXISTING_JOB_HANDOFF = (
            "אין בעיה, העברנו את ההודעה אל {pro_name} וביקשנו שיחזור אליך."
        )

        # --- Status pull responses ---
        # §4/§5: one plain headline shared by the sibling set, no emoji, no bold.
        # PRO-245 §7: NEW is the lead offered to a pro and waiting for their
        # approval; CONTACTED is the conversation still collecting details. The
        # two texts used to describe each other's state.
        STATUS_NO_ACTIVE_LEAD = (
            "אין לך כרגע פנייה פתוחה אצלנו.\n"
            "כדי לפתוח פנייה חדשה, אפשר פשוט לכתוב מה קרה."
        )
        STATUS_NEW = (
            "הסטטוס של הפנייה שלך:\n\n"
            "הפנייה אצל איש מקצוע, ואנחנו מחכים לאישור שלו.\n"
            "בעיה: {issue}\n"
            "עודכן לאחרונה: {updated_at}"
        )
        STATUS_CONTACTED = (
            "הסטטוס של הפנייה שלך:\n\n"
            "קיבלנו את הפנייה, ואנחנו משלימים פרטים ומחפשים איש מקצוע מתאים.\n"
            "בעיה: {issue}\n"
            "עודכן לאחרונה: {updated_at}"
        )
        STATUS_BOOKED = (
            "הסטטוס של הפנייה שלך:\n\n"
            "העבודה אושרה ונקבעה.\n"
            "איש מקצוע: {pro_name}\n"
            "בעיה: {issue}\n"
            "מועד: {appointment_time}"
        )
        STATUS_PENDING_ADMIN_REVIEW = (
            "הסטטוס של הפנייה שלך:\n\n"
            "הצוות שלנו עובר על הפנייה וימצא לך איש מקצוע מתאים.\n"
            "נעדכן אותך כאן ברגע שנמצא איש מקצוע מתאים."
        )
        STATUS_COMPLETED = (
            "הסטטוס של הפנייה שלך:\n\n" "העבודה הסתיימה. תודה שבחרת בפרולי."
        )
        STATUS_CANCELLED = "הסטטוס של הפנייה שלך:\n\nהפנייה בוטלה."
        STATUS_REJECTED_OR_CLOSED = (
            "הסטטוס של הפנייה שלך:\n\n" "הפנייה נסגרה. אפשר לפתוח פנייה חדשה מתי שצריך."
        )

        # --- PRO-166: migrated from call sites ---
        # Optional lines composed into PRO_FOUND (built in pro_flow.py).
        PROFESSION_LINE = "מקצוע: {label}\n"
        QUOTED_PRICE_LINE = "\nהערכת המחיר שקיבלת: {quoted_price}₪\n"
        PRO_RATING_LINE = "\nדירוג: {rating:.1f}\nמספר ביקורות: {review_count}"
        # workflow_service's AWAITING_NEW_OR_EXISTING re-prompt. §3: same menu
        # shape and same option wording as EXISTING_JOB_PROMPT, which is the
        # question this re-asks.
        NEW_OR_EXISTING_REPROMPT = (
            "לא בטוחים שהבנו. זו פנייה חדשה? אפשר לענות במספר:\n"
            "1 - כן, בעיה חדשה\n"
            "2 - לגבי העבודה הקיימת"
        )
        # lead_manager_service's address gate: the sentence plus the field
        # vocabulary it lists (attribute name -> Hebrew label, in gate order).
        # One sentence naming only what is still missing, the labels joined the
        # way Hebrew joins a list ("קומה ומספר דירה"), never a checklist.
        ADDRESS_MISSING_PARTS = "כדי לתאם, נשאר לקבל {missing_fields}."
        # Appended when floor and apartment are both missing: a private home has
        # neither, and without this line the customer has no way out of the gate
        # but to invent a number. "בית פרטי" is PRIVATE_HOME_PHRASES[0], read by
        # lead_manager_service.fill_private_home. PRO_BASE_SYSTEM quotes it.
        ADDRESS_PRIVATE_HOME_HINT = "\nאם זה בית פרטי, אפשר פשוט לכתוב בית פרטי."
        ADDRESS_FIELD_LABELS = {
            "street": "רחוב",
            "street_number": "מספר בית",
            "city": "עיר",
            "floor": "קומה",
            "apartment": "מספר דירה",
        }

    class Pro:
        # --- The canonical command list (§3) ------------------------------
        # One row per keyword, in the one command-list shape: the keyword, a
        # spaced hyphen, what it does. `HELP_MENU` renders all of them,
        # grouped; `pro_flow._show_pro_dashboard` renders the subset that
        # applies right now. Both read these rows, so the dashboard and the
        # help menu can no longer advertise different keywords — which is
        # exactly what the three separate hand-written menus here used to do.
        # Every keyword below is matched by a `Messages.Keywords` list that
        # `handle_pro_text_command` dispatches on (§7).
        CMD_APPROVE = "אשר - לקבל את העבודה שמחכה לך"
        CMD_REJECT = "דחה - לדחות את העבודה שמחכה לך"
        CMD_DETAILS = "פרטים - לראות את העבודות שאישרת, עם טלפון וניווט"
        CMD_ACTIVE_JOBS = "עבודות - לראות את העבודות הפעילות"
        CMD_FINISH = "סיימתי - לסגור עבודה שסיימת"
        CMD_CANCEL = "ביטול - לבטל עבודה פעילה"
        CMD_SEARCH = "חפש - לחפש פניות שמחכות לאיש מקצוע"
        CMD_RESUME = "זמין - לחזור לקבל עבודות"
        CMD_PAUSE = "הפסקה - להפסיק זמנית לקבל עבודות"
        CMD_SUMMARY = "סיכום - לראות את סיכום הביצועים שלך"
        CMD_REVIEWS = "ביקורות - לקרוא את הביקורות האחרונות שלך"
        CMD_HISTORY = "היסטוריה - לראות את העבודות האחרונות שסיימת"
        CMD_MENU = "תפריט - לחזור ללוח הבקרה"

        HELP_MENU = "\n".join(
            (
                "הפקודות של פרולי:",
                "",
                "קבלת עבודות:",
                CMD_APPROVE,
                CMD_REJECT,
                "",
                "ניהול העבודה:",
                CMD_DETAILS,
                CMD_ACTIVE_JOBS,
                CMD_FINISH,
                CMD_CANCEL,
                "",
                "חיפוש וזמינות:",
                CMD_SEARCH,
                CMD_RESUME,
                CMD_PAUSE,
                "",
                "העסק שלך:",
                CMD_SUMMARY,
                CMD_REVIEWS,
                CMD_HISTORY,
                "",
                CMD_MENU,
            )
        )

        # --- Messages sent to professionals -------------------------------
        # §7: this used to advertise 'עדיין עובד', a keyword no Keywords list
        # contained, so the reply fell through to the dashboard. Both replies
        # below are now real commands (FINISH_COMMANDS / STILL_WORKING_COMMANDS),
        # asked for the way `Templates.FINISH_REMINDER` asks.
        REMINDER = (
            "היי, רק בודקים לגבי העבודות הפתוחות שלך.\n"
            "אם סיימת עבודה, כתוב סיימתי. "
            "אם אתה עדיין באמצע, כתוב עדיין עובד ונפסיק להזכיר."
        )
        # The answer to 'עדיין עובד': the finish nudges stop for this pro's
        # open jobs, which is exactly what the handler does.
        STILL_WORKING_ACK = (
            "עודכן, לא נזכיר לך שוב על העבודות הפתוחות.\n"
            "כשתסיים, כתוב סיימתי ונסגור את העבודה."
        )
        STALE_LEAD_REMINDER = (
            "היי {pro_name}, העבודה אצל {customer_name} עדיין פתוחה אצלנו.\n"
            "אם סיימת, כתוב סיימתי ונסגור אותה כדי לפנות מקום לעבודות חדשות."
        )
        CUSTOMER_REPORTED_COMPLETION = (
            "עדכון: הלקוח דיווח שהעבודה הסתיימה.\n"
            "עדכנו את הסטטוס, אין צורך לעשות כלום."
        )
        # PRO-215: also not `SOS.PRO_LOST_LEAD` — nobody failed to answer. Says
        # nothing about where the lead went, since that may be a human's queue.
        LEAD_OUTSIDE_TRADE = (
            "עדכון: הפנייה בנושא {issue} כבר לא אצלך, כי היא לא בתחום המקצוע שלך."
        )
        LEAD_OUTSIDE_TRADE_OFFER = LEAD_OUTSIDE_TRADE + "\nאין צורך לאשר או לדחות אותה."
        # PRO-45: replaces `SOS.PRO_LOST_LEAD` ("הועברה עקב חוסר מענה") for this
        # path — the lead is taken away for a reason the pro is entitled to be
        # told accurately, and "no response" is not that reason.
        CUSTOMER_REPORTED_NO_SHOW = (
            "עדכון: הלקוח דיווח שלא הגעת לעבודה שנקבעה.\n"
            "הדיווח נרשם והפנייה עברה להמשך טיפול."
        )
        APPROVE_SUCCESS = "העבודה אושרה! שלחנו ללקוח את הפרטים שלך."
        CALENDAR_UPDATE_SUCCESS = "\nהיומן עודכן."
        # The approval stands with no hour reserved: no free slot lay near the
        # requested time. Said in place of CALENDAR_UPDATE_SUCCESS, never left
        # out silently, and the customer is not given an hour either (§7).
        APPROVE_NO_SLOT = (
            "\nאין ביומן שעה פנויה קרובה למועד שהלקוח ביקש, אז לא שריינו שעה.\n"
            "צריך לתאם את מועד ההגעה ישירות עם הלקוח."
        )
        # The requested hour was taken and the nearest free slot was reserved
        # instead. The lead and the customer's message carry this hour (§6:
        # the time ends its own line).
        CALENDAR_UPDATE_MOVED = (
            "\nהמועד שהלקוח ביקש תפוס ביומן, אז שריינו את המועד הפנוי הקרוב:\n"
            "{appointment_time}\n"
            "שלחנו ללקוח את המועד הזה."
        )
        # PRO-59: everything APPROVAL_REQUEST withholds, sent the moment the
        # approval is *claimed* — after the atomic status write, never before,
        # so a pro who loses the PRO-123 race gets ALREADY_RESPONDED and no
        # contact details at all. Field order and the wa.me/waze lines mirror
        # DETAILS_ROW, so the pro reads the same layout here and under פרטים.
        # The navigation link is inlined rather than sent separately: a second
        # send arrives *before* the handler's own return value, so the pro read
        # a context-free waze URL and then the approval. One message, and one
        # fewer egress call to fail.
        # §6: every Latin/numeric value ends its own line.
        CONTACT_CARD = (
            "\n\nפרטי הלקוח:\n"
            "טלפון: {customer_phone}\n"
            "צ'אט: https://wa.me/{customer_phone_intl}\n"
            "כתובת מלאה: {full_address}\n"
            "ניווט: https://waze.com/ul?q={address_encoded}"
        )
        # Appended to CONTACT_CARD only when the lead carries a floor or an
        # apartment; '-' placeholders would otherwise render an empty promise.
        CONTACT_CARD_EXTRA = "\nפרטים נוספים: {extra_info}"
        # Last in the reply, whatever else the card carried: this is what makes
        # the "יתקשר אליך" that PRO_FOUND tells the customer more than a hope.
        CONTACT_CARD_CLOSING = (
            "\n\nכדאי ליצור קשר עם הלקוח כמה שיותר מהר כדי לתאם הגעה."
        )
        NO_PENDING_APPROVE = "לא מצאנו עבודה חדשה שמחכה לאישור."
        ALREADY_RESPONDED = "כבר ענית על הפנייה הזו, ואי אפשר לשנות את הבחירה עכשיו."
        # PRO-117: sent only after the rematch actually happened, so the copy
        # states a done fact rather than promising a future search.
        REJECT_SUCCESS = "העבודה נדחתה, והפנייה עברה לאיש מקצוע אחר."
        # PRO-117: sent when reject → rematch found no replacement and the lead
        # was escalated to admin review — no "we'll find someone else" promise.
        REJECT_SUCCESS_ESCALATED = (
            "העבודה נדחתה, והפנייה עברה לצוות של פרולי לשיבוץ ידני."
        )
        NO_PENDING_REJECT = "לא מצאנו עבודה חדשה לדחייה."
        FINISH_SUCCESS = "עודכן שהעבודה הסתיימה. תודה!"
        # PRO-33: the job is already COMPLETED when this is sent — the price is a
        # non-blocking follow-up, always skippable.
        FINISH_SUCCESS_ASK_PRICE = (
            "עודכן שהעבודה הסתיימה. תודה!\n\n"
            "כמה גבית על העבודה?\n"
            "שלח את הסכום בשקלים, או כתוב דלג."
        )
        FINAL_PRICE_RECORDED = "תודה! רשמנו את הסכום: {price}₪"
        FINAL_PRICE_SKIPPED = "בסדר, לא רשמנו מחיר."
        FINAL_PRICE_INVALID = "לא זיהינו סכום, אז לא רשמנו מחיר. ממשיכים כרגיל."
        NO_ACTIVE_FINISH = "לא מצאנו עבודה פעילה לסיום."
        STATUS_PAUSED = (
            "עברת למצב הפסקה.\n" "לא יגיעו אליך הצעות עבודה חדשות עד שתכתוב זמין."
        )
        STATUS_RESUMED = "עברת למצב זמין.\nמעכשיו יגיעו אליך שוב הצעות עבודה."
        # PRO-212: is_active False without self_paused is not a break the pro took.
        # PRO-245: nothing the pro can type here reactivates the account, so the
        # copy names the mailbox, as REJECTED_NOTIFICATION does. §6: the address
        # is Latin, so it sits on its own line.
        RESUME_NEEDS_ADMIN = (
            "החשבון שלך לא פעיל כרגע, ורק הצוות של פרולי יכול להפעיל אותו מחדש.\n"
            "אפשר לכתוב לנו למייל:\n"
            "info@proli.me"
        )
        NO_PENDING_APPROVALS = "אין לך כרגע עבודות שמחכות לאישור."
        NO_ACTIVE_JOBS = "אין לך כרגע עבודות פעילות לסיום."
        SELECT_JOB_TO_FINISH = (
            "איזו עבודה סיימת?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        # Sent when pro is first matched — conversation still in progress, no action needed yet
        EARLY_LEAD_HEADER = "לקוח מדבר איתנו עכשיו על עבודה שמתאימה לך:"
        LOYALTY_LEAD_HEADER = "לקוח חוזר שלך ביקש אותך!"
        EARLY_LEAD_DETAILS = "תקלה: {issue_type}\nעיר: {city}"
        EARLY_LEAD_FOOTER = (
            "\n\nאנחנו משלימים עם הלקוח את שאר הפרטים: כתובת, יום ושעה.\n"
            "כשהכל יהיה מוכן, תקבל הודעה עם הפרטים לאישור. "
            "אין צורך לעשות כלום עכשיו."
        )
        # Sent when deal closes — ready for approval
        DEAL_CONFIRMED_HEADER = "הלקוח השלים את הפרטים. זו העבודה:"
        EMERGENCY_LEAD_HEADER = "קריאת חירום דחופה!"
        NEW_LEAD_HEADER = "הצעת עבודה חדשה מפרולי:"
        # PRO-59: the reassignment/admin offer is the same pre-approval moment
        # as APPROVAL_REQUEST, so it withholds the same things. It never
        # carried the phone; the floor/apartment line goes for the same reason
        # the phone does, and both arrive in CONTACT_CARD on approval.
        NEW_LEAD_DETAILS = (
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n"
            "תקלה: {issue_type}\n"
            "מועד מועדף: {appointment_time}"
        )
        NEW_LEAD_TRANSCRIPTION = "\nתמליל: {transcription}"
        NEW_LEAD_FOOTER = "\n\nלאישור כתוב אשר, לדחייה כתוב דחה."
        # Floor/apartment line rendered into {extra_info} of CONTACT_CARD_EXTRA.
        # PRO-59 took it out of both pre-approval offers, so this is now shown
        # only after the pro has approved. The '-' placeholders are why the
        # caller checks for a floor or apartment first: "קומה -, דירה -" is a
        # line that says nothing while looking like information.
        EXTRA_INFO_LINE = "קומה {floor}, דירה {apartment}"
        # The same line for a private home (apartment is
        # Fallbacks.PRIVATE_HOME_APARTMENT): "קומה קרקע, דירה בית פרטי" reads wrong.
        EXTRA_INFO_PRIVATE_HOME = "בית פרטי"
        # A private home whose floor the customer did give ("קומה 2", then "בית פרטי").
        EXTRA_INFO_PRIVATE_HOME_FLOOR = "בית פרטי, קומה {floor}"
        # Header for the numbered media-links block appended to a lead offer.
        # Media is always sent as text links, never re-sent as files — see
        # notification_service.format_media_links for the policy.
        MEDIA_ATTACHED_HEADER = "קבצים מצורפים:"
        # PRO-59: no phone and no floor/apartment before the pro approves. A
        # pro who can call the customer from the offer can reject in-bot and
        # take the job off-platform, which is the disintermediation leak the
        # commission model (PRO-33) cannot survive. The street and city stay,
        # because a pro cannot judge whether to accept without knowing where
        # it is. Everything withheld here is sent by CONTACT_CARD the moment
        # the approval is claimed — this delays the contact details, it does
        # not deny them.
        APPROVAL_REQUEST = (
            "עבודה חדשה מחכה לאישור שלך.\n\n"
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n"
            "תקלה: {issue_type}\n"
            "מועד: {appointment_time}\n"
            "{price_line}"
            "\nלאישור כתוב אשר, לדחייה כתוב דחה."
        )
        # PRO-55: the AI-quoted price the customer was promised, shown to the pro
        # before approval. Appended to APPROVAL_REQUEST only when a quote exists.
        APPROVAL_PRICE_LINE = "הערכת המחיר שהלקוח קיבל: {quoted_price}₪\n"
        # PRO-56: nudge a pro who hasn't approved within the SLA window.
        APPROVAL_NUDGE = (
            "העבודה מחכה לאישור שלך כבר {minutes} דקות.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        # "רבע שעה" is WorkerConstants.PAUSE_TTL_SECONDS, as in
        # PAUSE_NOTIFICATION and Templates.BOT_PAUSED. A test pins all three.
        PAUSE_ACK = (
            "השהינו את המענה האוטומטי בשיחה הזו, "
            "ואפשר לדבר ישירות עם {customer_name}.\n"
            "המענה האוטומטי יחזור לבד בעוד רבע שעה, או מיד כשתכתוב המשך."
        )
        # PRO-212: the pause needs a customer the pro can actually reach.
        PAUSE_NEEDS_APPROVAL = (
            "אפשר להשהות את המענה האוטומטי רק בעבודה שאישרת, "
            "כי את פרטי הלקוח אנחנו שולחים אחרי האישור.\n"
            + CMD_APPROVE
            + "\n"
            + CMD_REJECT
        )
        PAUSE_NOTIFICATION = (
            "הלקוח ביקש לדבר עם נציג אנושי.\n"
            "המענה האוטומטי בשיחה הזו מושהה לרבע שעה."
        )
        CUSTOMER_CANCELLED = (
            "עדכון: הלקוח {customer_name} ביטל את העבודה שנקבעה בכתובת {address}\n"
            "אין צורך להגיע."
        )
        # The customer withdrew a request this pro was offered and had not yet
        # approved. Nothing here the offer did not already show; sent with the
        # `customer_cancelled` template for a pro whose window is closed.
        CUSTOMER_WITHDREW_OFFER = (
            "הלקוח ביטל את הפנייה בנושא {issue_type} לפני שאישרת אותה.\n"
            "אין צורך לאשר או לדחות אותה."
        )
        CUSTOMER_RESCHEDULED_SUCCESS = (
            "עדכון ביומן: הלקוח {customer_name} שינה את מועד העבודה.\n"
            "כתובת: {address}\n"
            "מועד קודם: {old_time}\n"
            "מועד חדש: {new_time}\n"
            "היומן שלך כבר עודכן."
        )
        # PRO-116: the customer has a confirmed job with this pro and wants to
        # talk about it (not open a new request) — nudge the pro to reach out.
        CUSTOMER_EXISTING_JOB_QUERY = (
            "הלקוח {customer_name} רוצה לשאול או לעדכן משהו לגבי העבודה.\n"
            "תקלה: {issue}\n"
            "טלפון: {customer_phone}\n\n"
            "כדאי לחזור אליו כמה שיותר מהר."
        )
        NAVIGATE_TO = "ניווט לכתובת:"
        NO_ACTIVE_JOBS_LIST = "אין לך כרגע עבודות פעילות."
        NO_HISTORY = "עדיין אין לך עבודות שסיימת."
        NO_REVIEWS = "עדיין אין לך ביקורות."
        # §6: no ' | '-separated mixed-direction rows — the address and the
        # numeric date/time each get their own line, ending in their value.
        ACTIVE_JOB_ROW = "{num}. [{status}] {issue}\nכתובת: {address}\nמועד: {time}"
        HISTORY_ROW = "{num}. {issue}\nכתובת: {address}\nתאריך: {date}"
        STATS_HEADER = "הסטטיסטיקות שלך:\n"
        STATS_BODY = (
            "עבודות שסיימת: {completed}\n"
            "עבודות פעילות: {active}\n"
            "דירוג ממוצע: {rating}\n"
            "ביקורות: {reviews}\n"
            "הצטרפת: {joined}"
        )
        # §6: every numeric value sits at the end of its line. PRO-245: the
        # status is said in words only; `pro_flow` still passes status_emoji,
        # and str.format ignores it.
        PRO_DASHBOARD_HEADER = (
            "שלום {pro_name}\n"
            "דירוג: {rating}\n"
            "סטטוס: {status_text}\n"
            "עבודות פעילות: {active_jobs}/{max_jobs}\n\n"
            "מה אפשר לעשות עכשיו:"
        )
        INTENT_DETECTED = (
            "נראה שאתה מדווח על תקלה. לעבור למצב לקוח כדי שנמצא לך איש מקצוע?\n"
            "1 - כן, לעבור למצב לקוח\n"
            "2 - לא, להמשיך כאיש מקצוע"
        )
        INTENT_REPROMPT = (
            "לא הבנו. לעבור למצב לקוח?\n"
            "1 - כן, לעבור למצב לקוח\n"
            "2 - לא, להמשיך כאיש מקצוע"
        )
        SWITCHED_TO_CUSTOMER = (
            "מעולה, עברת למצב לקוח. מכאן נטפל בך כמו בכל לקוח שלנו.\n" "אז מה התקלה?"
        )
        SWITCH_CANCELLED = "בסדר, ממשיכים כרגיל במצב איש מקצוע."
        # Currently unsent: PRO-69 removed the auto-return at dispatch time (it fired
        # while the pro's own request was still live). Kept for the follow-up that
        # announces the return when their lead actually closes.
        AUTO_RETURNED_TO_PRO = (
            "הפנייה שלך עברה לאיש מקצוע לאישור, והחזרנו אותך למצב איש מקצוע "
            "כדי שתוכל להמשיך לנהל את העסק כרגיל."
        )
        SEARCH_RATE_LIMITED = "חיפשת לפני זמן קצר. אפשר לחפש שוב בעוד {minutes} דקות."
        NO_STUCK_LEADS = "אין כרגע פניות שמחכות לאיש מקצוע. אפשר לנסות שוב מאוחר יותר."
        # PRO-123: the `מצא` search now applies the same gates as routing, so it
        # has to be able to say *why* it refused instead of returning nothing.
        SEARCH_WHILE_PAUSED = (
            "אתה כרגע במצב הפסקה, אז החיפוש כבוי.\n"
            "כדי לחזור לקבל עבודות ולחפש שוב, כתוב זמין."
        )
        SEARCH_LOAD_FULL = (
            "יש לך כבר {active} עבודות פעילות, והמקסימום הוא {max_jobs}.\n"
            "אחרי שתסיים אחת מהן, אפשר לחפש עבודה נוספת."
        )
        STUCK_LEAD_FOUND = (
            "מצאנו פנייה שמחכה לאיש מקצוע:\n\n"
            "תקלה: {issue}\n"
            "עיר: {city}\n"
            "מחכה כבר {wait_minutes} דקות\n\n"
            "כדי לקחת את העבודה, כתוב אשר."
        )
        DETAILS_HEADER = "העבודות הפעילות שאישרת:\n"
        # §6: no ' | '-separated mixed-direction rows — the phone number and the
        # two links each get their own line, ending in the Latin value.
        DETAILS_ROW = (
            "{num}. {issue}\n"
            "מועד: {appointment_time}\n"
            "עיר: {city}\n"
            "טלפון: {customer_phone}\n"
            "צ'אט: https://wa.me/{customer_phone_intl}\n"
            "ניווט: https://waze.com/ul?q={address_encoded}"
        )
        SELECT_JOB_TO_CANCEL = (
            "איזו עבודה לבטל?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה לביטול, או כתוב ביטול כדי לצאת בלי לבטל."
        )
        # PRO-212: אשר/דחה with more than one NEW lead. Rows are JOB_SELECT_ROW,
        # which carries nothing the offer itself did not (PRO-59).
        SELECT_JOB_TO_APPROVE = (
            "איזו עבודה לאשר?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        SELECT_JOB_TO_REJECT = (
            "איזו עבודה לדחות?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        SELECT_JOB_TO_RESUME = (
            "באיזו עבודה להחזיר את המענה האוטומטי?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        SELECT_JOB_TO_PAUSE = (
            "באיזו עבודה להשהות את המענה האוטומטי?\n"
            "{jobs_list}\n"
            "שלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        CANCEL_SUCCESS = "העבודה בוטלה, ועדכנו את הלקוח."
        SUMMARY_BODY = (
            "סיכום הביצועים שלך:\n\n"
            "עבודות שסיימת החודש: {this_month}\n"
            'סה"כ עבודות: {total_completed}\n'
            "עבודות פעילות כרגע: {active}\n"
            "דירוג ממוצע: {rating}\n\n"
            "{motivation}"
        )
        SUMMARY_MOTIVATION_GREAT = "כל הכבוד! אתה מהטובים שלנו, תמשיך ככה."
        SUMMARY_MOTIVATION_GOOD = "עבודה טובה! כל עבודה מוסיפה לשם הטוב שלך."
        SUMMARY_MOTIVATION_START = "רק התחלת, וכל עבודה בונה לך שם."
        NO_REVIEWS_WITH_TEXT = (
            "עדיין אין ביקורות כתובות. אחרי כל עבודה, הלקוחות יכולים להשאיר לך ביקורת."
        )
        # §6 no ' | ' row: the two numbers each close a labelled line.
        REVIEWS_HEADER = (
            "הביקורות האחרונות שלך:\n"
            "דירוג ממוצע: {rating:.1f}\n"
            "מספר דירוגים: {count}\n"
        )
        REVIEW_TEXT_ROW = 'דירוג {rating}: "{comment}"'
        DASHBOARD_TIP = "\nלרשימת כל הפקודות, כתוב עזרה."

        # --- PRO-166: migrated verbatim from app/services/pro_flow.py ---
        # Status vocabulary rendered into ACTIVE_JOB_ROW and the dashboard.
        STATUS_LABELS = {
            "new": "מחכה לאישור",
            "contacted": "בשיחה עם הלקוח",
            "booked": "מאושר",
            "completed": "הושלם",
            "rejected": "נדחה",
            "cancelled": "בוטל",
            "closed": "סגור",
            "pending_admin_review": "אצל הצוות",
        }
        STATUS_AVAILABLE = "זמין"
        STATUS_ON_BREAK = "בהפסקה"
        ACTION_CANCELLED = "בסדר, הפעולה בוטלה."
        INVALID_JOB_SELECTION = "שלח מספר מהרשימה, או כתוב ביטול כדי לצאת."
        NO_PAUSED_CONVERSATION = "אין כרגע שיחה מושהית."
        BOT_RESUMED = "המענה האוטומטי חזר לפעול."
        BOT_ALREADY_ACTIVE = "המענה האוטומטי כבר פועל."
        # PRO-147 §6: the appointment is numeric-heavy, so it closes its own
        # line instead of joining the Hebrew row.
        JOB_SELECT_ROW = "{num}. {name}, {city} ({issue})\nמועד: {time}"
        ACTIVE_JOBS_HEADER = "העבודות הפעילות שלך:\n"
        ACTIVE_JOBS_TOTAL = '\nסה"כ עבודות: {count}'

        # --- PRO-147: chronological job lists ------------------------------
        # Every pro-facing job list (עבודות, פרטים, the סיימתי / ביטול prompts)
        # is ordered by when the work happens and grouped under one header per
        # day. Jobs with no resolved `appointment_datetime` (open-ended
        # "בהקדם" requests) sit last under their own header, so an ASAP job
        # can never hide a dated one. §5: a day header is a plain line ending
        # in a colon. §6: the date is numeric, so it is the last thing on its
        # line before the colon.
        DAY_HEADER_TODAY = "היום:"
        DAY_HEADER_TOMORROW = "מחר:"
        DAY_HEADER_DATE = "{weekday} {date}:"
        DAY_HEADER_UNSCHEDULED = "בלי מועד קבוע:"
        # Absolute Israel-time rendering of `appointment_datetime` inside a
        # row. The raw `appointment_time` string ("מחר בבוקר") is shown only
        # when no datetime was resolved — it stops being true a day later.
        TIME_TODAY = "היום {time}"
        TIME_TOMORROW = "מחר {time}"
        TIME_DATE = "{weekday} {date} {time}"
        # Python weekday() order: Monday first, Sunday last.
        WEEKDAY_NAMES = ("שני", "שלישי", "רביעי", "חמישי", "שישי", "שבת", "ראשון")
        # Pagination. A list is never cut silently: when more rows exist the
        # footer says how many are shown out of how many and how to continue.
        # §6: each numeric value closes its own labelled line.
        LIST_PAGE_MORE = (
            '\nסה"כ עבודות: {total}\n'
            "מוצגות עבודות: {first} עד {last}\n"
            "להמשך הרשימה כתוב עוד."
        )
        LIST_PAGE_END = '\nסה"כ עבודות: {total}\nמוצגות עבודות: {first} עד {last}'
        NO_MORE_ROWS = (
            "אין המשך לרשימה.\n"
            "לרשימה מעודכנת כתוב עבודות, ולפרטי העבודות שאישרת כתוב פרטים."
        )
        # Inside a סיימתי / ביטול prompt the pro is still choosing, so the
        # reply keeps the prompt open instead of pointing at other commands.
        SELECTION_NO_MORE_ROWS = (
            "זה סוף הרשימה.\nשלח את מספר העבודה, או כתוב ביטול כדי לצאת."
        )
        # A listed job left this pro while the prompt was open, so the numbers
        # already shown no longer mean what they meant. The prompt is dropped
        # rather than renumbered — a shifted number here cancels the wrong job.
        SELECTION_LIST_CHANGED = (
            "הרשימה השתנתה מאז שהצגנו אותה, אז ביטלנו את הבחירה.\n"
            "לרשימה מעודכנת לסיום עבודה כתוב סיימתי, ולביטול עבודה כתוב ביטול."
        )
        # The same, in an אשר/דחה prompt, where other offers are still waiting.
        SELECTION_OFFER_LIST_CHANGED = (
            "הרשימה השתנתה מאז שהצגנו אותה, אז ביטלנו את הבחירה.\n"
            "לאישור עבודה שמחכה לך כתוב אשר, לדחייה כתוב דחה."
        )
        SELECTION_RESUME_LIST_CHANGED = (
            "הרשימה השתנתה מאז שהצגנו אותה, אז ביטלנו את הבחירה.\n"
            "לרשימה מעודכנת כתוב המשך."
        )
        SELECTION_PAUSE_LIST_CHANGED = (
            "הרשימה השתנתה מאז שהצגנו אותה, אז ביטלנו את הבחירה.\n"
            "לרשימה מעודכנת כתוב השהה."
        )
        HISTORY_HEADER = "העבודות האחרונות שסיימת:\n"
        RATING_NONE = "אין עדיין"

        # --- PRO-166: migrated from app/scheduler.py (daily agenda) ---
        DAILY_AGENDA_HEADER = (
            "בוקר טוב {pro_name}!\nתאריך: {date}\nאלה העבודות שלך להיום:"
        )
        # PRO-168 §7: this row used to render `job.get("details")` — a field
        # the bot never writes. Only the admin panel does
        # (`admin_panel/core/lead_queries.py`), and it mirrors the same value
        # into `issue_type` anyway, so every agenda line for a bot-created lead
        # rendered the literal fallback "פרטים חסרים". `issue_type` is the
        # strict superset. §6: the time and the phone are numeric, so each
        # ends its own labelled line rather than opening one.
        DAILY_AGENDA_ROW = "\n{issue}\nשעה: {time}\nטלפון: {phone}\n"
        DAILY_AGENDA_FOOTER = "\nשיהיה יום מוצלח!"

    class Admin:
        # PRO-166 migrated the ניהול wizard's copy here; PRO-168 brought it into
        # the standard voice, and PRO-245 to the 2026-11-17 rules: no emoji,
        # no bold, no em dash.
        # Sent to the ADMIN's own WhatsApp chat, never to customers or pros.
        NO_STUCK_LEADS = "אין כרגע לידים תקועים."
        STUCK_LEADS_HEADER = "לידים שמחכים לטיפול:\n"
        WAIT_MINUTES = "{wait_minutes} דק'"
        STUCK_LEAD_ROW = "{num}. {city}, {issue} (מחכה {wait})"
        SELECT_PROMPT = "\nשלח את המספר לבחירה, או כתוב ביטול כדי לצאת."
        CANCELLED = "בוטל. כדי להתחיל מחדש, כתוב ניהול."
        INVALID_NUMBER = "המספר הזה לא ברשימה. נסה שוב, או כתוב ביטול כדי לצאת."
        ACTION_MENU = (
            "בחרת ליד. למי להעביר אותו?\n"
            "1 - לקחת את הליד בעצמך\n"
            "2 - להציג רשימה של אנשי מקצוע פנויים"
        )
        NO_ADMIN_PRO_PROFILE = (
            "לא מצאנו פרופיל איש מקצוע של המנהל. אפשר לבחור באפשרות 2."
        )
        LEAD_NOT_FOUND = "הליד לא נמצא. כדי להתחיל מחדש, כתוב ניהול."
        NO_AVAILABLE_PROS = "לא מצאנו אנשי מקצוע פנויים לליד הזה."
        AVAILABLE_PROS_HEADER = "אנשי מקצוע פנויים:\n"
        PRO_ROW = "{num}. {name}\nדירוג: {rating}"
        INVALID_OPTION = "האפשרות הזו לא קיימת. אפשר לענות 1 או 2."
        PRO_NOT_FOUND = "איש המקצוע לא נמצא. כדי להתחיל מחדש, כתוב ניהול."
        ASSIGN_SUCCESS = "הליד הועבר אל {pro_name}."
        ASSIGN_LEAD_LOOKUP_MISSED = (
            "לא מצאנו את הליד אחרי העדכון. צריך לבדוק בפאנל הניהול וליצור "
            "קשר ידני עם {pro_name} ועם הלקוח."
        )
        ASSIGN_OFFER_FAILED = (
            "הליד שויך אל {pro_name}, אבל ההצעה לא הגיעה אליו "
            "(ייתכן שחלון 24 השעות שלו סגור). צריך ליצור איתו קשר ידנית."
        )
        # PRO-282: the lead left PENDING_ADMIN_REVIEW while the admin was
        # choosing (a pro's מצא claimed it, say). Nothing was written.
        ASSIGN_LEAD_TAKEN = (
            "הליד כבר לא מחכה לשיבוץ, מישהו טיפל בו בינתיים. "
            "הוא לא שויך אל {pro_name}. כדי לפתוח את הרשימה מחדש, כתוב ניהול."
        )

    class SOS:
        # Same words as `Templates.REASSIGNING`, without the issue.
        CUSTOMER_REASSIGNING = (
            "סליחה על העיכוב. אנחנו מחפשים לך איש מקצוע אחר ונעדכן אותך כאן."
        )
        NO_PRO_AVAILABLE = (
            "לא מצאנו כרגע איש מקצוע פנוי לפנייה שלך.\n"
            "אפשר לנסות שוב מאוחר יותר, או לכתוב נציג כדי להעביר את השיחה לצוות שלנו."
        )
        # PRO-63: sent when a lead exhausts MAX_REASSIGNMENTS. This is the worst
        # moment a customer can have with Proli — they have been failed three
        # times — so the copy hands them to a human instead of dismissing them.
        # Deliberately: no apology-spiral and no "try again later" dead end. No
        # number of minutes either: the immediate admin page in reassign_lead is
        # all that backs this, and nothing measures how fast a human answers it.
        MAX_REASSIGNMENTS_REACHED = (
            "עדיין לא מצאנו איש מקצוע פנוי לפנייה שלך.\n"
            "העברנו אותה לצוות שלנו, ונחזור אליך בהקדם בשעות הפעילות."
        )
        # The operator's WhatsApp alerts (ADMIN_MAX_REASSIGNMENTS,
        # ADMIN_REPORT_*, ADMIN_ALERT) were retired by PRO-88 and deleted by
        # PRO-166 — the admin never messages the bot, so under Meta Cloud API
        # their 24h window is permanently closed. Operator alerts page via
        # notification_service.page_operator() → Sentry → email (PRO-75). See
        # docs/WHATSAPP_TEMPLATE_CATALOG.md before adding any operator alert.
        PRO_LOST_LEAD = "עדכון: העבודה עברה לאיש מקצוע אחר, כי לא הגיעה תשובה בזמן."

        TO_USER_WITH_PRO = (
            "קיבלנו! העברנו את הבקשה שלך לאיש המקצוע, וביקשנו שיחזור אליך.\n"
            "אם התשובה מתעכבת, אפשר לכתוב לנו כאן שוב."
        )
        TO_USER_NO_PRO = (
            "קיבלנו! העברנו את הפנייה לצוות של פרולי.\n"
            "נחזור אליך בהקדם בשעות הפעילות."
        )
        # PRO-59: the SOS pro-alert is the fourth path to the customer's phone,
        # and the only one not gated on the lead being taken. `guard_sos` fires
        # on a lead in NEW or CONTACTED too — "offered, not yet approved" and
        # "early lead", where the pro has been shown no contact details at all
        # — so a customer typing נציג while waiting on approval used to hand
        # their number to a pro who could then reject in-bot and call them.
        # This variant carries the cry for help without the number, and says
        # plainly how to earn it. PRO-212: without the customer's words too, since
        # they can type their number. A CONTACTED lead gets no alert at all: it
        # was never offered, so אשר would find nothing.
        PRO_ALERT_PENDING = (
            "לקוח שמחכה לתשובה שלך ביקש עזרה.\n"
            "את פרטי הקשר שלו נשלח אחרי שתאשר.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        PRO_ALERT = (
            "הלקוח שלך ביקש עזרה.\n\n"
            "טלפון: {phone}\n"
            "ההודעה שלו: {last_message}\n\n"
            "כדאי לחזור אליו כמה שיותר מהר."
        )

    class Alerts:
        # PRO-20 — infra paging for WhatsApp account deauth (SPOF).
        # The WA-down page is now an out-of-band page_critical → Sentry email
        # (PRO-75); we never send a WA-down alert over WhatsApp. Only the recovery
        # notice (instance authorized again) goes over WhatsApp.
        WHATSAPP_RECOVERED = (
            "מערכת פרולי חזרה לפעול.\n\n" "החיבור לוואטסאפ חזר, וההודעות מעובדות כרגיל."
        )

    class Consent:
        # PRO-105: sent with two reply buttons (ACCEPT_BUTTON / DECLINE_BUTTON)
        # that mirror the two words this text asks for.
        REQUEST = (
            "שלום וברוכים הבאים לפרולי.\n\n"
            "לפני שמתחילים, חשוב לנו שיהיה ברור:\n"
            "אנחנו שומרים את מספר הטלפון, ההודעות והמיקום שלך "
            "כדי לחבר אותך לאנשי מקצוע מתאימים.\n\n"
            "המידע מאובטח, ואנחנו משתפים אותו רק עם איש המקצוע שמטפל בפנייה שלך. "
            "אפשר לבקש למחוק אותו בכל זמן.\n\n"
            "כדי להמשיך אפשר לכתוב כן, ואם לא מתאים אפשר לכתוב לא."
        )
        # Appended by legal_links.consent_request_text, one URL per line (§6).
        PRIVACY_LINK = "המדיניות המלאה: {privacy_url}"
        TERMS_LINK = "תנאי השימוש: {terms_url}"
        # PRO-124: a bare ack. It used to end "במה אוכל לעזור?", which the
        # dispatcher's own opening question then repeated on the very next
        # turn — two invitations to describe the same problem, back to back.
        # The greeting and the question belong to the dispatcher; this line
        # only confirms the consent was recorded.
        ACCEPTED = "תודה! אפשר להתחיל."
        DECLINED = (
            "הבנו, לא נשמור את תוכן ההודעות שלך. "
            "אם משנים את הדעה, אפשר פשוט לשלוח לנו הודעה."
        )
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
        # PRO-105: reply-button titles on REQUEST, the two lines it advertises.
        # A tap sends ACCEPT_KEYWORDS[0] / DECLINE_KEYWORDS[0], the words typed
        # (dispatch_guards.CONSENT_BUTTONS), so the label may say more. Meta
        # caps a reply-button title at 20 characters.
        ACCEPT_BUTTON = "כן, להמשיך"
        DECLINE_BUTTON = "לא, תודה"

    class Onboarding:
        _WELCOME_INTRO = (
            "ברוכים הבאים להרשמה כאיש מקצוע בפרולי!\n\n"
            "נשאל כמה שאלות קצרות ונבנה את הפרופיל שלך.\n"
            "בסוף הצוות שלנו יעבור על הפרופיל, ואחרי האישור תתחיל לקבל עבודות.\n\n"
        )
        _WELCOME_QUESTION = "מה שם העסק שלך?"
        WELCOME = _WELCOME_INTRO + _WELCOME_QUESTION
        # Sent instead of WELCOME once terms are published (legal_links).
        # §6: the URL sits on its own line.
        WELCOME_WITH_TERMS = (
            _WELCOME_INTRO
            + "ההרשמה מהווה הסכמה לתנאי השימוש:\n{terms_url}\n\n"
            + _WELCOME_QUESTION
        )
        # §3: a numbered choice. TYPE_MAP matches the plain digits "1"-"7" the
        # pro replies with, and the trade names too.
        ASK_TYPE = (
            "מעולה! מה המקצוע שלך? אפשר לענות במספר:\n"
            "1 - אינסטלטור\n"
            "2 - חשמלאי\n"
            "3 - הנדימן\n"
            "4 - מנעולן\n"
            "5 - צבעי\n"
            "6 - ניקיון\n"
            "7 - כללי\n\n"
            "אפשר גם לכתוב את שם המקצוע."
        )
        ASK_AREAS = (
            "באילו ערים או אזורים אתה עובד?\n"
            "אפשר לשלוח כמה ערים, מופרדות בפסיקים.\n"
            "לדוגמה: תל אביב, רמת גן, חולון"
        )
        ASK_PRICES = (
            "מה המחירים שלך? זה לא חובה.\n"
            "אפשר לשלוח רשימה של שירותים ומחירים, או לכתוב דלג.\n"
            "לדוגמה:\n"
            "תיקון נזילה - 250₪\n"
            "החלפת ברז - 350₪"
        )
        CONFIRM = (
            "זה הסיכום של הפרופיל שלך:\n\n"
            "שם: {name}\n"
            "מקצוע: {type}\n"
            "אזורים: {areas}\n"
            "מחירים: {prices}\n\n"
            "הכל נכון? לשליחה כתוב אשר, ולביטול ההרשמה כתוב ביטול."
        )
        SUCCESS = (
            "תודה! שלחנו את הפרופיל שלך לצוות שלנו.\n"
            "נעדכן אותך כאן כשנעבור עליו.\n"
            "לשאלות אפשר לכתוב לנו למייל:\n"
            "info@proli.me"
        )
        CANCELLED = "ההרשמה בוטלה. כדי להתחיל מחדש בכל זמן, כתוב הרשמה."
        ALREADY_REGISTERED = "כבר יש לך פרופיל אצלנו!"
        # PRO-245: a pro waiting for approval has no command that does anything
        # yet, so the copy names the mailbox (§6: on its own line).
        PENDING_ALREADY = (
            "הפרופיל שלך כבר מחכה לאישור, ונעדכן אותך כשיאושר.\n"
            "לשאלות אפשר לכתוב לנו למייל:\n"
            "info@proli.me"
        )
        # Same words as `Templates.ONBOARDING_APPROVED`.
        APPROVED_NOTIFICATION = (
            "הפרופיל שלך בפרולי אושר והוא פעיל.\n"
            "מעכשיו פניות של לקוחות יגיעו אליך לכאן, לוואטסאפ."
        )
        # The rejected pro's profile is deleted, so a WhatsApp reply reaches no pro
        # flow at all. Hence the mailbox, on its own line (§6).
        REJECTED_NOTIFICATION = (
            "הפרופיל שלך בפרולי לא אושר בשלב הזה.\n"
            "לפרטים אפשר לכתוב לנו למייל:\n"
            "info@proli.me\n"
            "תודה שנרשמת."
        )
        INVALID_TYPE = (
            "לא הבנו. אפשר לשלוח מספר מ-1 עד 7, או את שם המקצוע, "
            "למשל אינסטלטור או חשמלאי."
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
        NAME_LENGTH_ERROR = "שם העסק צריך להיות באורך של 2 עד 100 תווים. נסה שוב:"
        CITIES_PARSE_ERROR = (
            "לא זיהינו ערים. אפשר לשלוח רשימה של ערים, מופרדות בפסיקים:"
        )
        CONFIRM_REPROMPT = "לשליחה כתוב אשר, ולביטול ההרשמה כתוב ביטול."

    class System:
        # RESET_SUCCESS was removed 2026-08-27 (operator decision): a global
        # reset clears state/context silently, with no confirmation message.
        # PRO-166: the textual rendering of an inbound location message, used
        # by the Cloud API inbound parser (it was shared with the legacy
        # webhook route until that was removed, PRO-254).
        LOCATION_AS_TEXT = "מיקום: {latitude}, {longitude}"
        # PRO-166: the list-picker button label on the (unused — see CLAUDE.md)
        # interactive-list transport path in cloud_api.py.
        LIST_PICKER_BUTTON = "בחירה"
        # PRO-232: what scripts/probe_template_delivery.py sends a staging test
        # device whose window is open. Read only by the operator running it; a
        # closed window gets Meta's `hello_world` (`delivery_probe`) instead.
        DELIVERY_PROBE = "בדיקת מסירה של פרולי: חלון 24 השעות היה פתוח."

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
        # PRO-147: next page of a paged job list (עבודות / פרטים / the סיימתי
        # and ביטול selection prompts). Matched exactly, like every command;
        # inside a selection prompt it is checked *before* the PRO-186
        # abandon-on-command rule, so asking for the next page never drops
        # the prompt.
        MORE_COMMANDS = ["עוד", "more", "הבא"]
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
        # PRO-222 — life-threatening: answered with LIFE_THREATENING, never a pro.
        # Checked before every holding state and before consent, so a miss here
        # is the worse failure: these lean wide. Exact phrases (whole-token):
        LIFE_THREATENING_KEYWORDS = [
            "ריח גז",
            "ריח של גז",
            "מריח גז",
            "מריחים גז",
            "דליפת גז",
            "דליפה של גז",
            "סכנת חיים",
            "מכת חשמל",
            "קיבל חשמל",
            "קיבלתי חשמל",
            "gas leak",
            "smell gas",
            "fire",
            "explosion",
        ]
        # Stems through up to three leading clitics, as EMERGENCY_PREFIXABLE.
        LIFE_THREATENING_PREFIXABLE = [
            "שריפה",
            "שריפות",
            "שרפה",
            "אש",
            "עשן",
            "נשרף",
            "נשרפה",
            "בוער",
            "בוערת",
            "פיצוץ",
            "התפוצץ",
            "התפוצצה",
            "התפוצצות",
            "התחשמל",
            "התחשמלה",
            "התחשמלתי",
            "התחשמלות",
        ]
        # Removed before matching. Negations, and a burst pipe or water heater:
        # in a plumber's chat "פיצוץ" is usually water, which keeps the urgent
        # fast path (EMERGENCY_PREFIXABLE still matches it there).
        LIFE_THREATENING_EXCLUDE_PHRASES = [
            "אין ריח גז",
            "אין ריח של גז",
            "לא מריח גז",
            "אין דליפת גז",
            "אין שריפה",
            "אין אש",
            "אין עשן",
            "אין סכנת חיים",
            "לא התחשמל",
            "לא התחשמלתי",
            "במחיר שריפה",
            "פיצוץ צינור",
            "פיצוץ בצינור",
            "פיצוץ של צינור",
            "פיצוץ צנרת",
            "פיצוץ בצנרת",
            "פיצוץ מים",
            "פיצוץ בדוד",
            "צינור התפוצץ",
            "הצינור התפוצץ",
            "התפוצץ צינור",
            "התפוצץ הצינור",
            "התפוצץ לי צינור",
            "דוד התפוצץ",
            "הדוד התפוצץ",
            "התפוצץ הדוד",
            "אש בגריל",
            "על האש",
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
        # A reply that settles floor and apartment with no number: a private
        # home has neither. Whole-token matched (contains_keyword, no clitic
        # stripping, so the ב־ forms are listed), and a phrase after "לא" never
        # counts ("זה לא בית פרטי"). "קוטג'" tokenizes to "קוטג". The first
        # entry is what Customer.ADDRESS_PRIVATE_HOME_HINT tells the customer
        # to type.
        PRIVATE_HOME_PHRASES = [
            "בית פרטי",
            "בבית פרטי",
            "צמוד קרקע",
            "צמודת קרקע",
            "בצמוד קרקע",
            "וילה",
            "ווילה",
            "בוילה",
            "בווילה",
            "קוטג",
            "בקוטג",
            "אין קומה",
            "אין דירה",
            "אין מספר דירה",
            "private house",
        ]
        # Private home only as the WHOLE reply: inside a longer message "קרקע"
        # is as likely a ground floor ("קרקע, דירה 2") or the soil ("קרקע
        # רטובה"), and "פרטי" a first name ("שם פרטי").
        PRIVATE_HOME_WHOLE_REPLIES = ["קרקע", "פרטי"]
        # Says it is a building: never read as a private home.
        NOT_PRIVATE_HOME_PHRASES = ["בניין", "בבניין", "בנין", "בבנין"]
        # A ground floor in a building: settles the floor only.
        GROUND_FLOOR_PHRASES = [
            "קומת קרקע",
            "בקומת קרקע",
            "קומה קרקע",
            "ground floor",
        ]
        # Precision: guard_politeness matches the WHOLE message
        # against this list (normalized_text ==), never a substring, so "תודה,
        # מתי הוא מגיע?" or "תודה, שכחתי לציין שזה קומה 3" reach the flow.
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
        # Must stay a substring of the "1 - כן, הסתיימה" option in
        # Customer.COMPLETION_CHECK: a customer who types the option back
        # verbatim is confirming completion (customer_flow matches with `in`).
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
        ISSUE = "תקלה"
        TIME_ASAP = "בהקדם"
        # PRO-168: the pro-facing job lists and the admin wizard hand-rolled
        # these inline. §9 — a value that can be empty formats through a
        # Fallback, never as an empty hole in a sentence.
        TIME_UNSET = "לא נקבע"
        CITY_UNKNOWN = "עיר לא ידועה"
        ISSUE_UNKNOWN = "בעיה לא ידועה"
        PRO_NAME_MISSING = "ללא שם"
        # What a private home stores for the two address parts it does not have,
        # so the five-field gate passes (lead_manager_service.fill_private_home).
        PRIVATE_HOME_FLOOR = "קרקע"
        PRIVATE_HOME_APARTMENT = "בית פרטי"
        GROUND_FLOOR = "קרקע"
        # Template variables cannot be empty, so an optional line in a free-form
        # message becomes a variable with a value that says "nothing here".
        PRICE_NOT_QUOTED = "לא ניתנה הערכה"
        # PRO_FOUND's time when approval reserved no slot: the customer is not
        # told an hour nobody holds. Fits the approved template's "מועד: {...}"
        # line, which is why it is a value and not a dropped line.
        TIME_TO_COORDINATE = "יתואם בטלפון עם איש המקצוע"
        URGENCY_REGULAR = "רגילה"

    class Templates:
        # The fixed bodies submitted to Meta for business-initiated sends: what a
        # recipient whose 24h service window is closed reads instead of the
        # free-form message. `template_registry.meta_body` turns each `{name}` into
        # Meta's `{{n}}`, numbered by `TemplateSpec.params`. Nothing `str.format`s
        # these. tests/test_template_bodies.py holds them to Meta's review rules
        # and pins every keyword they advertise against `Keywords`.
        #
        # These are worded for review, so they name פרולי and stand alone, and
        # they are not the free-form wording with holes in it. A send's optional
        # price and urgency become values. Media links and a voice transcript
        # cannot be a variable at all: they arrive with the approval
        # (`pro_flow._approve_lead`). The free-form loyalty header has no
        # counterpart here.
        LEAD_OFFER = (
            "עבודה חדשה מפרולי מחכה לאישור שלך.\n\n"
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n"
            "תקלה: {issue_type}\n"
            "מועד: {appointment_time}\n"
            "מחיר משוער: {price_estimate}\n"
            "דחיפות: {urgency}\n\n"
            "את הטלפון של הלקוח נשלח מיד אחרי שתאשר.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        LEAD_OFFER_REASSIGNED = (
            "יש פנייה חדשה מפרולי שמחכה לאישור שלך.\n\n"
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n"
            "תקלה: {issue_type}\n"
            "מועד מועדף: {appointment_time}\n\n"
            "את הטלפון של הלקוח נשלח מיד אחרי שתאשר.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        PRICE_ESTIMATE = "{quoted_price}₪"
        URGENCY_EMERGENCY = "קריאת חירום דחופה"
        APPROVAL_NUDGE = (
            "העבודה בנושא {issue_type} מחכה לאישור שלך כבר {minutes} דקות.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        DAILY_AGENDA = (
            "בוקר טוב {pro_name}, זה סדר היום שלך בפרולי.\n"
            "תאריך: {date}\n"
            "עבודות להיום: {job_count}\n\n"
            "כדי לראות אותן כתוב עבודות."
        )
        STALE_LEAD_REMINDER = (
            "היי {pro_name}, העבודה אצל {customer_name} עדיין פתוחה אצלנו.\n"
            "אם סיימת, כתוב סיימתי ונסגור אותה כדי לפנות מקום לעבודות חדשות."
        )
        FINISH_REMINDER = (
            "תזכורת מפרולי: יש לך עבודות שעדיין פתוחות.\n"
            "אם סיימת עבודה, כתוב סיימתי. "
            "אם אתה עדיין באמצע, כתוב עדיין עובד ונפסיק להזכיר."
        )
        PRO_LOST_LEAD = (
            "עדכון מפרולי: העבודה בנושא {issue_type} עברה לאיש מקצוע אחר, "
            "כי לא הגיעה תשובה בזמן."
        )
        # "רבע שעה" is WorkerConstants.PAUSE_TTL_SECONDS, as in the free-form
        # Pro.PAUSE_NOTIFICATION and Pro.PAUSE_ACK. A test pins all three.
        BOT_PAUSED = (
            "לקוח ביקש לדבר עם נציג אנושי לגבי העבודה בנושא {issue_type}.\n"
            "המענה האוטומטי בשיחה הזו מושהה לרבע שעה."
        )
        SOS_ALERT = (
            "הלקוח שלך ביקש עזרה.\n\n"
            "טלפון: {phone}\n"
            "ההודעה שלו: {last_message}\n\n"
            "כדאי לחזור אליו כמה שיותר מהר."
        )
        # A body of its own because the pro has not approved yet and must not get
        # the phone number (PRO-59), nor the customer's words, where they can type
        # that number (PRO-212). So it has no variable at all.
        SOS_ALERT_PENDING = (
            "לקוח שמחכה לתשובה שלך ביקש עזרה.\n"
            "את פרטי הקשר שלו נשלח אחרי שתאשר.\n"
            "לאישור כתוב אשר, לדחייה כתוב דחה."
        )
        # One body for a lead the pro was offered and one they only heard of
        # early: "אין צורך לעשות כלום" is true of both, and nothing here says where
        # the lead went, since that may be a human's queue (PRO-215).
        PRO_LEAD_OUTSIDE_TRADE = (
            "עדכון מפרולי: הפנייה בנושא {issue_type} כבר לא אצלך, "
            "כי היא לא בתחום המקצוע שלך.\n"
            "אין צורך לעשות כלום."
        )
        CUSTOMER_CANCELLED = (
            "הלקוח ביטל את העבודה.\n\n"
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n\n"
            "אין צורך להגיע."
        )
        ONBOARDING_APPROVED = (
            "הפרופיל שלך בפרולי אושר והוא פעיל.\n"
            "מעכשיו פניות של לקוחות יגיעו אליך לכאן, לוואטסאפ."
        )
        ONBOARDING_REJECTED = (
            "הפרופיל שלך בפרולי לא אושר בשלב הזה.\n"
            "לפרטים אפשר לכתוב לנו ל-info@proli.me\n"
            "תודה שנרשמת."
        )
        # Customer.PRO_FOUND without its optional profession and rating lines. The
        # phone is the last field and a fixed sentence follows it, because a body
        # may not end on a variable.
        PRO_FOUND = (
            "מצאנו לך איש מקצוע לפנייה שלך בפרולי.\n\n"
            "שם: {pro_name}\n"
            "תקלה: {issue_type}\n"
            "כתובת: {full_address}\n"
            "מועד: {appointment_time}\n"
            "הערכת מחיר: {price_estimate}\n"
            "טלפון: {pro_phone}\n\n"
            "איש המקצוע יתקשר אליך בקרוב כדי לתאם הגעה."
        )
        NO_PRO_AVAILABLE = (
            "לא מצאנו איש מקצוע פנוי לפנייה שלך בנושא {issue_type}.\n"
            "אפשר לנסות שוב מאוחר יותר, או לכתוב נציג ומישהו מהצוות יחזור אליך."
        )
        COMPLETION_CHECK = (
            "עדכון מפרולי לגבי העבודה שלך עם {pro_name}.\n"
            "העבודה הסתיימה? אפשר לענות במספר:\n"
            "1 - כן, הסתיימה\n"
            "2 - עדיין לא\n"
            "3 - איש המקצוע לא הגיע"
        )
        REASSIGNING = (
            "סליחה על העיכוב בפנייה שלך בנושא {issue_type}.\n"
            "אנחנו מחפשים לך איש מקצוע אחר ונעדכן אותך כאן."
        )
        MAX_REASSIGNMENTS = (
            "עדיין לא מצאנו איש מקצוע פנוי לפנייה שלך בנושא {issue_type}.\n"
            "העברנו אותה לצוות שלנו, ונחזור אליך בשעות הפעילות."
        )
        PENDING_REVIEW = (
            "קיבלנו את הפנייה שלך בנושא {issue_type}.\n"
            "הצוות שלנו יעבור עליה וימצא לך איש מקצוע מתאים. נעדכן אותך כאן."
        )
        # Nine notices one party's message or a scheduler sends the *other* party,
        # whose own 24h window may be closed (approved 2026-11-14). Each free-form
        # original keeps its wording for an open window.
        PRO_CANCELLED_BOOKING = (
            "עדכון מפרולי: איש המקצוע ביטל את העבודה שנקבעה.\n"
            "אם עדיין צריך, אפשר לכתוב לנו כאן ונפתח פנייה חדשה. "
            "לשיחה עם הצוות אפשר לכתוב נציג."
        )
        CUSTOMER_RESCHEDULED = (
            "הלקוח שינה את מועד העבודה.\n\n"
            "לקוח: {customer_name}\n"
            "כתובת: {full_address}\n"
            "מועד קודם: {old_time}\n"
            "מועד חדש: {new_time}\n\n"
            "היומן שלך בפרולי כבר עודכן."
        )
        # Sent on a BOOKED lead only, so this pro already holds the number (PRO-59).
        CUSTOMER_EXISTING_JOB_QUERY = (
            "לקוח שלך רוצה לשאול או לעדכן משהו לגבי העבודה.\n\n"
            "לקוח: {customer_name}\n"
            "תקלה: {issue_type}\n"
            "טלפון: {phone}\n\n"
            "כדאי לחזור אליו כמה שיותר מהר."
        )
        # Not submitted (owner, 2026-11-17): a rating request reads as a survey,
        # which Meta is likely to hold as MARKETING. Worded anyway, so submitting
        # it later is a decision about the category, not about the copy.
        RATE_SERVICE = (
            "העבודה עם {pro_name} הסתיימה. איך היה?\n"
            "אפשר לענות במספר מ-1 (גרוע) עד 5 (מצוין), או לכתוב דלג."
        )
        CUSTOMER_REPORTED_NO_SHOW = (
            "עדכון מפרולי: הלקוח דיווח שלא הגעת לעבודה שנקבעה.\n"
            "הדיווח נרשם והפנייה עברה להמשך טיפול."
        )
        REASSIGN_OFFER = (
            "עדכון מפרולי: איש המקצוע עוד לא אישר את הפנייה שלך.\n"
            "מה עושים? אפשר לענות במספר:\n"
            "1 - לחפש איש מקצוע אחר\n"
            "2 - להמתין עוד קצת"
        )
        AWAITING_APPROVAL = (
            "עדכון מפרולי: העברנו את הפנייה שלך אל {pro_name}.\n"
            "נעדכן אותך כאן ברגע שהעבודה תאושר."
        )
        CUSTOMER_REPORTED_COMPLETION = (
            "עדכון מפרולי: {customer_name} דיווח שהעבודה הסתיימה.\n"
            "עדכנו את הסטטוס, אין צורך לעשות כלום."
        )
        BOT_PAUSED_BY_PRO = (
            "עדכון מפרולי: איש המקצוע רוצה לדבר איתך ישירות, "
            "והוא יחזור אליך מהמספר שלו.\n"
            "בינתיים המענה האוטומטי בשיחה הזו מושהה."
        )

        class Buttons:
            # PRO-230: quick-reply button labels, submitted with the templates
            # whose bodies advertise a reply. A tap sends the button's payload
            # (`template_registry`), which is the exact string the flow already
            # accepts typed, so the label is free to be friendlier than the
            # keyword. Meta, read 2026-11-15: at most 25 characters, and the
            # utility-template page says "Alphanumeric characters only", hence
            # no comma in JOB_DONE where the body has one.
            APPROVE = "אשר"
            REJECT = "דחה"
            FINISHED = "סיימתי"
            STILL_WORKING = "עדיין עובד"
            ACTIVE_JOBS = "עבודות"
            HUMAN = "נציג"
            JOB_DONE = "כן הסתיים"
            NOT_YET = "עדיין לא"
            PRO_NO_SHOW = "לא הגיע"
            FIND_ANOTHER_PRO = "לחפש איש מקצוע אחר"
            KEEP_WAITING = "להמתין עוד קצת"

        # One sample per variable name, sent with the submission and never to a
        # recipient. Meta requires a sample for every variable. All invented: no
        # real person, business, address or phone number.
        PARAM_SAMPLES = {
            "customer_name": "ישראל ישראלי",
            "full_address": "רחוב הדוגמה 1, תל אביב",
            "issue_type": "נזילה בכיור המטבח",
            "appointment_time": "מחר ב-09:00",
            "price_estimate": "350₪",
            "urgency": "רגילה",
            "minutes": "10",
            "pro_name": "שירותי אינסטלציה לדוגמה",
            "date": "15/11",
            "job_count": "3",
            "phone": "050-0000000",
            "pro_phone": "050-0000001",
            "last_message": "יש נזילה חזקה ואני צריך עזרה",
            "old_time": "15/11/2026 09:00",
            "new_time": "16/11/2026 14:00",
        }

    class Errors:
        AI_OVERLOAD = "סליחה, יש לנו עומס כרגע. אפשר לנסות שוב בעוד רגע."
        GENERIC_ERROR = "משהו השתבש אצלנו. אפשר לנסות שוב."
        # PRO-21 — graceful throttle messages
        RATE_LIMITED = "קיבלנו ממך הרבה הודעות ברצף. אפשר לנסות שוב בעוד רגע."
        # PRO-124. Two word choices, both deliberate. "הקובץ" rather than
        # "התמונה", because the fetch raises before the mime type is known and
        # it is as likely to have been a voice note. And "לפתוח" rather than
        # "לקבל", because the file did arrive: `media_urls` is written to the
        # lead regardless of this failure and the pro is sent links, not bytes
        # — so the honest claim is that we could not read it, not that it was
        # lost. Infinitives, no gendered verb (style guide §2.1).
        MEDIA_FETCH_FAILED = (
            "לא הצלחנו לפתוח את הקובץ ששלחת.\n" "אפשר לשלוח שוב, או פשוט לתאר במילים."
        )
        # The model never saw the file and there was no text to answer. Infinitives,
        # as above. TEXT_ONLY is for a failed recording, or a file of unknown type:
        # offering a voice note after one failed can send them round again.
        MEDIA_UPLOAD_FAILED = (
            "לא הצלחנו לעבד את הקובץ ששלחת.\n"
            "אפשר לתאר את הבעיה בטקסט או בהודעה קולית?"
        )
        MEDIA_UPLOAD_FAILED_TEXT_ONLY = (
            "לא הצלחנו לעבד את הקובץ ששלחת.\nאפשר לתאר את הבעיה בטקסט?"
        )
        # The model never saw the file but there was text, which they already wrote,
        # so this asks for nothing. Also sent ahead of LOYALTY_OFFER and
        # PENDING_REVIEW, so it says "התייחסנו", not "ענינו".
        MEDIA_FAILED_TEXT_ANSWERED = (
            "לא הצלחנו לעבד את הקובץ ששלחת, אז התייחסנו רק למה שכתבת."
        )
        DAILY_AI_CAP_REACHED = (
            "הגענו למכסת ההודעות היומית בשיחה הזו. אפשר להמשיך מחר, "
            "ואם זה דחוף, אפשר לכתוב נציג."
        )

    class AISystemPrompts:
        ANALYZE_IMAGE = "[System: Analyze the image to identify the issue.]"
        TRANSCRIBE_AUDIO = (
            "[System: Transcribe the audio verbatim and analyze the intent.]"
        )
        ANALYZE_VIDEO = (
            "[System: Watch the video to identify the issue and describe what you see.]"
        )
        MEDIA_NOT_SEEN = (
            "[System: The customer attached a file that could not be processed. "
            "You have NOT seen or heard it, so do not describe it or guess what it "
            "shows. They are told separately. Answer only their text.]"
        )
        DEFAULT_SYSTEM = "You are a helpful assistant."
        PROLI_SCHEDULER_ROLE = "You are Proli, an AI scheduler for {pro_name}."
