# תוכנית סידור מבנה — Proli

> **סטטוס:** הצעה, 2026-11-17. נכתבה אחרי סקירה מלאה של הריפו (commit `f9b7d81`): `app/`, `admin_panel/`, `scripts/`, `tests/`, `docs/`, CI, `.claude/` וקבצי הדיפלוי.
> כל ממצא כאן אומת מול הקוד (grep / AST). מספרי שורות נכונים ל-commit הנ"ל.
>
> **הכלל המנחה: אף שלב לא משנה התנהגות.** הזזות קוד נפרדות מתיקוני באגים, כל PR קטן, וה-suite (כולל `tests/e2e/`) ירוק לפני ואחרי כל צעד.
> המסמך הזה מחליף את טבלת "Phase 3" ב-[`ARCHITECTURE_STANDARD.md`](./ARCHITECTURE_STANDARD.md) §16, שהמספרים בה כבר לא נכונים: `workflow_service.py` ירד מ-1,674 ל-1,210 שורות, ו-`dispatch_guards.py` (1,655) לא מופיע בה בכלל.

> **החלטת בעלים, 2026-11-17:** לפני ההשקה רק שלבים 0–3. שלבים 4–9 (שכבת נתונים, פירוק ה-dispatcher וה-services, פאנל האדמין, סקריפטים, קונפיגורציה) מחכים לאחרי הפיילוט. ה-epic ב-Linear: PRO-246.
>
> **מה כבר נכנס ל-`dev`:**
>
> | שלב | מה | PR |
> |---|---|---|
> | 0 | `tests/test_architecture_layers.py`: טסט AST לכיוון התלויות, עם טבלאות חריגים שרק מצטמצמות | #245 |
> | 1 | מחיקת `seed_analytics_data.py`, `docker-compose.prod.yml` + `nginx.prod.conf`, ה-override ושני סקריפטי ה-shell של Railway; guard סביבה ל-`clear_history.py` ול-`reset_test.py`; הסרת `gunicorn` | #246 |
> | 2 | B1 = PRO-247 (#247), B2 = PRO-248 (#248), B5 = PRO-251 (#249), B3 = PRO-249 (#250), B4 = PRO-250 (#251), B8 = PRO-252 (#252); וגם: אין `/docs`, `/redoc`, `/openapi.json` ב-production (#253). B9 (PRO-253) ו-B6/B7 (PRO-254) עדיין פתוחים | ראו ליד כל באג |
> | 3 | `create_indexes` ו-`backup` עברו ל-`app/db/` (הגיבוי רץ כ-`python -m app.db.backup`); `app/` כבר לא מייבא ולא מריץ את `scripts/`. האריזה של `app/worker/` (סעיף 3.3 למטה) נדחתה לאחרי הפיילוט | #254 |
>
> **סקירות read-only (2026-11-17):** אבטחה (`[DEAL]` prompt injection, חתימת Meta, הרשאות ונעילה בפאנל) ו-races (double-booking של slots, ליד אצל שני מקצוענים). כל ממצא מאומת נפתח כ-issue נפרד: PRO-267 עד PRO-285, ו-PRO-266 (כפתורי Scheduler מתים, נמצא בזמן PRO-249). שום תיקון לא נכנס בתוך הסקירה.
>
> מספרי השורות למטה נכונים ל-`f9b7d81`, כלומר לפני השינויים האלה.

---

## תוכן

1. [תמונת מצב — מה עובד טוב](#1-תמונת-מצב--מה-עובד-טוב)
2. [הבעיות, לפי השפעה](#2-הבעיות-לפי-השפעה)
3. [באגים שנמצאו בדרך (מחוץ לתוכנית המבנה)](#3-באגים-שנמצאו-בדרך)
4. [מבנה היעד](#4-מבנה-היעד)
5. [מה **לא** להזיז, ולמה](#5-מה-לא-להזיז-ולמה)
6. [כללי ביצוע — איך לא לשבור כלום](#6-כללי-ביצוע--איך-לא-לשבור-כלום)
7. [השלבים](#7-השלבים)
8. [נספח: כל מה שמקבע את המבנה הנוכחי](#8-נספח-כל-מה-שמקבע-את-המבנה-הנוכחי)

---

## 1. תמונת מצב — מה עובד טוב

לפני הבעיות: השלד נכון, ואין סיבה לשבור אותו.

- **שלושה תהליכים מופרדים היטב:** API שרק מקבל ומכניס לתור, Worker, ופאנל אדמין.
- **יציאה אחת לכל תעבורת WhatsApp** (`app/providers/whatsapp/`), עם guards ב-CI.
- **כל הטקסטים במקום אחד** (`app/core/messages.py`), וה-FSM במקום אחד (`state_manager_service`).
- **טסטים בלי שירותים חיצוניים** (mongomock + fakeredis), ו-CI שנכשל אם מספר הטסטים יורד.
- **כמעט אין קוד מת ברמת פונקציות.** סריקת AST מצאה רק קומץ (בסעיף 2.8).

כלומר, הבעיה היא לא "ארכיטקטורה שגויה". הבעיה היא **שחיקה**: קבצים שגדלו, תלויות שהתהפכו, ולוגיקה שהועתקה בין האפליקציה לפאנל.

| מדד | ערך |
|---|---|
| שורות Python ב-`app/` + `admin_panel/` + `scripts/` | ~37,000 |
| שורות טסטים | ~63,000 (136 קבצים) |
| קבצים מעל 1,000 שורות | 10 |
| קבצים מעל 1,200 שורות (סף "קריטי" של הסטנדרט) | 7 |

הקבצים הגדולים:

| קובץ | שורות | מה הוא באמת |
|---|---|---|
| `app/services/dispatch_guards.py` | 1,655 | 4 קבוצות guards + guard כתובת של 185 שורות |
| `app/core/messages.py` | 1,603 | טקסטים + `Keywords` (אוצר מילים לפענוח קלט) + `Templates` + פרומפטים |
| `app/services/pro_flow.py` | 1,558 | 7 תחומים: דשבורד, אישור/דחייה, השהיה, סיום/מחיר, רשימות, דוחות, חיפוש |
| `admin_panel/views/home.py` | 1,548 | פונקציה אחת של ~550 שורות (`view_leads_dashboard`) |
| `app/services/monitor_service.py` | 1,441 | `reassign_lead` (350 שורות) + 5 מוניטורים לא קשורים |
| `admin_panel/ui/components.py` | 1,426 | ~1,130 שורות CSS בתוך מחרוזת Python |
| `app/services/workflow_service.py` | 1,210 | entry point + loyalty + חירום + `_finalize_deal` + 25 re-exports לטסטים |
| `app/services/conversation_pipeline.py` | 1,122 | **פונקציה אחת של 1,035 שורות** (`run_customer_pipeline`) |
| `app/services/customer_flow.py` | 1,120 | השלמה/no-show, דירוג, תזמון מחדש, סטטוס |
| `app/scheduler.py` | 1,021 | "חיווט בלבד" לפי הסטנדרט — בפועל: לוגיקת agenda, מוניטור, ~350 שורות גיבוי |
| `app/providers/whatsapp/cloud_api.py` | 927 | שליחה יוצאת **ו**פענוח נכנס |
| `admin_panel/core/config.py` | 841 | **אין בו קונפיגורציה בכלל.** מילון תרגומים `TRANS` אחד |

---

## 2. הבעיות, לפי השפעה

### 2.1 תלויות הפוכות — קוד production שתלוי ב-`scripts/`

- `app/main.py:20` עושה `from scripts.create_indexes import create_all_indexes` בכל עליית API.
- `app/scheduler.py:402-404` מריץ את `scripts/backup.py` לפי נתיב קובץ, בגיבוי הלילי.

לכן `scripts/` הוא לא תיקיית כלים: הזזת הקובץ הלא-נכון שם שוברת את production. זה **הדבר הראשון** שצריך לתקן לפני כל סידור של `scripts/`.

### 2.2 מעגל import ב-dispatcher, ו-`workflow_service` כ-service locator

- `workflow_service` מייבא את `dispatch_guards` ואת `conversation_pipeline`.
- שניהם מייבאים בחזרה `workflow_service as wf` **בתוך פונקציות**: 22 פעמים ב-`dispatch_guards` ופעם אחת ב-`conversation_pipeline:100`.
- דרך `wf.*` ה-guards ניגשים לכ-50 שמות, כולל פונקציות פרטיות: `wf._finalize_deal`, `wf._escalate_emergency`, `wf._is_registered_pro`.

בפועל יש **שלושה מנגנונים שונים להזרקת תלויות**, ובכל guard לא ברור מאיפה מגיעה כל תלות:

1. `GuardDeps`, שמזריק חלק מהתלויות;
2. `wf.X` בתוך פונקציות;
3. ~25 re-exports עם `# noqa: F401` ב-`workflow_service:40-89`, שקיימים רק כדי שטסטים יוכלו לעשות `monkeypatch.setattr(workflow_service, "ai", ...)`.

### 2.3 קבצים שעושים יותר מדי

ראו את הטבלה למעלה. הדוגמה הקשה ביותר היא `run_customer_pipeline`: פונקציה אחת של 1,035 שורות. היא כבר מחולקת בהערות לשלבים, וזה התפר הטבעי לפיצול:

> log → השלמה/דירוג → מדיה → ליד פעיל + טרייד שגוי → BOOKED gate → מסלול מהיר למקצוען משויך → Smart Dispatcher → sticky → חלקי כתובת → loyalty → matching gate → `[DEAL]`

### 2.4 שכבת נתונים מפוזרת

- **אין שכבת repository.** קריאות ישירות ל-collections:

  | קובץ | קריאות ישירות |
  |---|---|
  | `customer_flow` | 34 |
  | `pro_flow` | 29 |
  | `conversation_pipeline` | 23 |
  | `dispatch_guards` | 22 |
  | `monitor_service` | 18 |
  | `workflow_service` | 17 |
  | `scheduler.py` | 13 |
  | עוד 7 קבצים | ... |

  `LeadManager` עם 6 מתודות הוא "repository" שכמעט לא משתמשים בו.
- **כל מודול עושה `from app.core.database import leads_collection`.** לכן `tests/conftest.py` צריך **74** קריאות `monkeypatch.setattr` כדי להחליף DB, מודול אחרי מודול. כל מודול חדש שוכחים להוסיף שם.
- **עד 4 לקוחות Mongo בתהליך האדמין:**
  - `admin_panel/core/utils.py:42`, שקורא `os.getenv` בעצמו ועם סדר עדיפויות אחר ל-URI;
  - `admin_panel/core/auth.py:93`;
  - `admin_panel/views/analytics.py:21`;
  - ה-Motor וה-`sync_client` של `app.core.database`, שנוצרים בזמן import.
- **אין רישום מרכזי לשמות:**
  - `"scheduler_config"` מופיע ~13 פעמים כמחרוזת בשני התהליכים, עם **שתי צורות ברירת מחדל שונות** (באג, סעיף 3).
  - ~35 משפחות מפתחות Redis מפוזרות ב-14 מודולים, בשמות לא עקביים: `rl:` מול `rate_limit:`, `lock:` מול `lock_busy:`, `state_meta:`.
- **אין מודלים למסמכי DB.** הכל `dict`, ולכן נוצרות סטיות כמו:
  - `customer_chat_id` מול `chat_id` ב-reviews;
  - `reminder_sent_count` מול `reminders_sent` (שני מונים לאותה תזכורת, ו-`pro_flow.py:887` מעדכן את שניהם);
  - `customer_phone` נקרא (`pro_flow.py:538`) אבל אף פעם לא נכתב.
- **אינדקסים:**

  | בעיה | אינדקסים |
  |---|---|
  | חסרים | `reviews.pro_id`, `reviews.customer_chat_id`, `messages.{chat_id, timestamp}`, `slots.{pro_id, is_taken, start_time}` |
  | מיותרים (prefix של compound) | `leads.chat_id`, `leads.status`, `slots.pro_id` |
  | לא בשימוש | `users.business_name` TEXT (אין `$text` בשום מקום) |

- **אין מנגנון מיגרציות.** יש 3–4 סקריפטי backfill חד-פעמיים, ושום רישום של מה כבר רץ ב-production. ה-shims בקוד (`$in: [phone, chat_id]`, `profession_type`, `handles_electrical`, `_END_STAMPS`) נשארים לנצח, כי אף אחד לא יודע מתי מותר להסיר אותם.

### 2.5 לוגיקה כפולה

| מה | איפה |
|---|---|
| חיפוש מקצוען לפי טלפון: `{"phone_number": {"$in": [phone, chat_id]}, "role": "professional"}` | 8 מקומות: `workflow_service:234,254`, `dispatch_guards:245,430,1483`, `pro_flow:129`, `pro_onboarding_service:34`, `data_management_service:59`. `matching_service.customer_phones` בודק **3** צורות במקום 2 |
| שחרור slot | `customer_flow:305,980`, `pro_flow:1285`, `workflow_service:566`. וריאציה אחת מוסיפה תנאי `is_taken: True` והשאר לא |
| ביטול ליד BOOKED | `workflow_service._execute_customer_cancel` ו-`pro_flow._execute_cancel` |
| הסלמה ל-`PENDING_ADMIN_REVIEW` | 9 מקומות ב-4 קבצים. `pro_flow:731` קורא לפונקציה **פרטית** של `monitor_service` |
| שתי תזכורות "סיים עבודה" עם שני מונים | `scheduler.monitor_unfinished_jobs` ו-`monitor_service.remind_stale_booked_leads` |
| אזור זמן ישראל | מוגדר מחדש ב-6 מקומות ב-`app/` ו-5 ב-`admin_panel/`. `settings.TIMEZONE` קיים ונקרא רק פעמיים. pytz ו-ZoneInfo מעורבבים |
| `ObjectId(x) if isinstance(x, str) else x` | 8+ מקומות |
| מחולל slots | `scheduling_service.py:115` ו-`admin_panel/core/utils.py:150` (צורה אחרת, בלי בדיקת כפילויות) |
| שליחת בדיקת השלמה | `customer_flow.send_customer_completion_check` ו-`admin_panel/core/utils.py:181` (עותק סינכרוני) |
| כתיבת סטטוס ליד | `lead_manager_service.set_lead_status` ו-`admin_panel/core/lead_queries.py:452` (בלי `updated_at` — באג, סעיף 3) |
| יצירת ליד | `lead_manager_service` ו-`admin_panel/views/home.py:1162` (צורת מסמך אחרת) |
| בניית Redis URL / `RedisSettings` | `redis_client.py:165,198`, `auth.py:27`, `arq_worker.py:27` |
| קליטת webhook (dedup, מגן DDoS, enqueue) | `api/routes/webhook.py` ו-`api/routes/meta_webhook.py` |
| עיר → קואורדינטות סטטי | `matching_service.get_coordinates` ו-`geocoding_service._static_lookup` |
| פרומפטים ל-LLM | `app/core/prompts.py`, `Messages.AISystemPrompts`, `admin_panel/core/utils.py:52-146` |

### 2.6 שכבות שנחצות

- `app/core/arq_worker.py` מייבא את `app.services.workflow_service` ואת `app.scheduler`, כלומר core תלוי ב-services.
- `scheduler.py` ו-`arq_worker.py` מייבאים את `whatsapp` **מתוך `workflow_service`**.
- הפאנל מייבא `app.services.*` ב-6 מקומות (`admin_flow`, `lead_manager`, `matching`, `geocoding`, `cloudinary`, `state/context`), בניגוד ל-`ARCHITECTURE_STANDARD.md` §4. לא כל הייבואים האלה שגויים: שימוש חוזר ב-`assign_lead_to_pro` הוא בדיוק מה שמונע כפילות. הבעיה היא שאין כלל שמגדיר מה מותר.
- לוגיקה עסקית בתוך views של הפאנל, בלי seam שאפשר לבדוק:
  - אישור מקצוען: `professionals.py:596-641`;
  - מחיקת מקצוען עם cascade: `professionals.py:180-188`;
  - יצירת ליד ומחיקתו: `home.py:1162,1516`.
- פענוח הודעות נכנסות יושב בתוך ה-transport היוצא (`cloud_api.py:724-927`).
- 7 ייבואים בתוך פונקציות **שלא שוברים שום מעגל**: `pro_flow:686,721,572,1266`, `customer_flow:233,488`, `admin_flow:157`. ההערה ב-`dispatch_guards:144` ("admin_flow imports from workflow_service") פשוט לא נכונה.

### 2.7 שמות לא עקביים

- **סיומות קבצים ב-`app/services/`:** `*_service.py` (15), `*_flow.py` (3), ושלושה בלי שום סיומת: `media_handler.py`, `dispatch_guards.py`, `conversation_pipeline.py`.
  - `agenda_service` הוא בעצם פורמט טקסט.
  - `cloudinary_client_service` הוא client של 40 שורות.
- **סגנונות מעורבבים:** classes עם classmethods בלבד (`StateManager`, `ContextManager`), staticmethods (`SecurityService`), instances בלי state (`LeadManager()`, `AIEngine()`) ופונקציות חופשיות. וזה למרות שהסטנדרט אומר "service הוא מודול של פונקציות async".
- **אותו שם, שתי חתימות:**
  - `send_customer_completion_check` ב-`customer_flow` וב-`workflow_service`;
  - `send_pro_reminder` ב-`notification_service` וב-`workflow_service`.
- **פונקציות ציבוריות שמיובאות בשם שנראה פרטי:** `handle_pro_text_command as _handle_pro_cmd`.
- **פעלים לא אחידים ב-jobs:**
  - ב-`monitor_service`: `check_*`, `send_*`, `remind_*`, `auto_reject_*`;
  - ב-scheduler: `run_*` לצד `monitor_unfinished_jobs` ו-`daily_reminders_job`.
- **בפאנל:**
  - `core/config.py` הוא קובץ תרגומים;
  - `core/utils.py` הוא שק: חיבור Mongo + persona prompts + מחולל slots + שליחת WhatsApp;
  - `*_queries.py` הם בעצם services.
- **בטסטים:**
  - `test_admin_flow.py` בודק את `app/services/admin_flow`, אבל נתפס ב-glob של חוקי הפאנל (`tests/test_admin_*.py`).
  - 4 טסטים של הפאנל **לא** נתפסים בו.
  - `test_unit_*` ו-`test_integration_*` מופיעים לצד `test_db_integration`.

### 2.8 דברים שלא בשימוש

| פריט | מצב |
|---|---|
| `scripts/seed_analytics_data.py` | **מסוכן.** `delete_many({})` על leads, reviews ו-pros, בלי guard סביבה, ואף אחד לא מפנה אליו |
| `scripts/start_railway_services.sh`, `stop_railway_services.sh` | הוחלפו ע"י `stop_railway_services.yml` |
| `docker-compose.prod.yml`, `nginx/nginx.prod.conf` | דיפלוי VPS שלא קיים. production רץ על Railway. ה-compose הזה גם **לא יכול לעלות** (`MONGO_URI=` מגדיר מחרוזת ריקה, ולא "יורש מ-.env") |
| `docker-compose.override.yml` | חוזר על ערכים שכבר קיימים בקובץ הבסיס |
| `scripts/start.sh` | "לא בשימוש" לפי `RAILWAY_SETUP.md:318`, אבל טסט מקבע אותו |
| `scripts/migrate_unknown_address.py` | מיגרציה חד-פעמית שכבר רצה (2026-04-18) |
| `LeadManager.get_lead_by_id`, `database.sync_db` | 0 הפניות |
| `matching_service.get_coordinates`, `scheduling_service.save_schedule_template` / `get_no_show_count`, `facade.send_location_link`, נתיב `parse_webhook` דרך ה-ABC | בשימוש של טסטים בלבד |
| מפתחות `Messages` בלי קורא | `Customer.RATING_THANKS`, `Pro.NO_PENDING_REJECT`, `Pro.FINISH_SUCCESS`, `Pro.NO_ACTIVE_FINISH`, `Pro.DEAL_CONFIRMED_HEADER`, `Pro.NAVIGATE_TO`, `Pro.NO_REVIEWS`, `SOS.TO_USER_WITH_PRO`, `SOS.TO_USER_NO_PRO` |
| קבועים | `WorkerConstants.SLOT_DURATION_HOURS`, `SLOT_SEARCH_WINDOW_HOURS`, `DEFAULT_CURRENCY`, `APIStatus.RUNNING`, `APIStatus.IGNORED_WRONG_INSTANCE`, `AdminDefaults.DEFAULT_PLAN`, `AdminDefaults.DEFAULT_RATING` |
| תלויות | `gunicorn` לא מיובא בשום מקום. `pytest-xdist` מופיע גם ב-`requirements.txt` וגם ב-`requirements-dev.txt` |
| `POST /webhook` (מעטפת הספק הישן) + `app/schemas/whatsapp.py` | אף ספק חי לא שולח אליו. הוא חי רק כ-ingress של ה-e2e harness, אבל **חשוף לאינטרנט ב-production** |

### 2.9 קונפיגורציה ומשתני סביבה

- **משתנים שנקראים מחוץ ל-`Settings`:**

  | איפה | מה |
  |---|---|
  | `admin_panel/core/utils.py` | `MONGO_URI` / `MONGODB_URI` / `MONGO_URL`, `ENVIRONMENT` |
  | `admin_panel/core/auth.py` | `ADMIN_PASSWORD`, `ADMIN_PASSWORD_HASH` |
  | `health.py` | `RAILWAY_*` |
  | `main.py` | `PORT` |
  | סקריפטים | המשתנים שלהם |

  התוצאות:
  - `Settings.ADMIN_PASSWORD` לא נקרא אף פעם בזמן ריצה.
  - `ADMIN_PASSWORD_HASH` לא נמצא ב-`Settings`, ולכן ה-redactor של הלוג לא מכסה אותו.
- **שדות לא מתועדים בשום מקום:** `BACKUP_S3_BUCKET` (שמתואר כ"נדרש"), `BACKUP_S3_ENDPOINT`, `SENTRY_TRACES_SAMPLE_RATE`, `MONGO_*_POOL*`, `AI_MODELS`, `MAX_CHAT_HISTORY`, `GEOCODING_*_TTL_SECONDS`, `TIMEZONE`.
- **אין `.env.example`.**
- **הערות שהתיישנו:**
  - `WHATSAPP_PROVIDER` "stub until PRO-89";
  - Sentry "worker only";
  - ה-README מציג את `ADMIN_PASSWORD` ליד "generate hash", אבל הסקריפט מדפיס `ADMIN_PASSWORD_HASH=`.
- **כלי טסט ופיתוח נכנסים לכל image של production:** pytest, black, flake8, Faker ועוד יושבים ב-`requirements.txt`, כי CI מתקין רק את הקובץ הזה.
- **בעיות ב-Dockerfile:**
  - `PATH` ו-`PYTHONPATH` מצביעים ל-`/install/...`, שלא קיים אחרי ה-COPY;
  - `HEALTHCHECK` משותף מסמן את ה-worker ואת הפאנל כ-unhealthy;
  - `--workers 4` שונה מ-Railway, שרץ עם worker אחד.

### 2.10 תיעוד

יש 23 מסמכים ב-`docs/`, עם חפיפות:

- **ארכיטקטורה:** `ARCHITECTURE.md`, `ARCHITECTURE_STANDARD.md` ו-`DOCUMENTATION_FLOW.md` מתארים כולם את מחזור החיים של ליד ואת ה-FSM.
- **בדיקות ידניות:** `MANUAL_TEST_PLAN.md` ("אין ספק חי"), `SANDBOX_E2E_RUNSHEET.md` ו-`PILOT_E2E_CHECKLIST.md` סותרים זה את זה.
- **מסמכים היסטוריים** (כולם מגדירים את עצמם כ-snapshot או כתוכנית שהסתיימה): `PRODUCTION_READINESS.md`, `ADMIN_PANEL_RESPONSIVE_PLAN.md`, וסעיף 5 של `SCALING_GUIDE.md`.
- **`SCRIPTS.md`:** חסרים בו 11 סקריפטים, והוא מתעד את `simulate_webhook.py`, שבכלל יושב ב-`tests/`.

---

## 3. באגים שנמצאו בדרך

אלה **לא** חלק מסידור המבנה, כי כל אחד מהם משנה התנהגות. אני ממליץ לתקן אותם **קודם**, כל אחד ב-PR נפרד עם טסט שמשחזר אותו. סדר העדיפות לפי חומרה:

| # | באג | ראיה | השפעה |
|---|---|---|---|
| B1 | **ייצוא ומחיקת נתוני משתמש (DSAR) לא מוצאים ביקורות** | נכתב `customer_chat_id` (`customer_flow.py:892,918`), נשאל `chat_id` (`data_management_service.py:62,98`). גם `wa_delivery`, `state_meta:` ו-`consent_hold:` לא מנוקים | "זכות למחיקה" משאירה מידע. הפונקציות עדיין לא מחוברות ל-endpoint, וזה הזמן לתקן |
| B2 | **שינוי סטטוס מהפאנל לא כותב `updated_at`** | `admin_panel/core/lead_queries.py:452,483` | ה-nudger (`monitor_service.py:1363`) רואה ליד שהאדמין העביר ל-BOOKED כ"ישן" ושולח תזכורת מיד. ה-short-circuit של `PENDING_ADMIN_REVIEW` (`dispatch_guards.py:1544`) לא נדרך |
| B3 | **`scheduler_config`: הפאנל ו-ה-worker לא מסכימים על ברירות מחדל** | הפאנל מציג `sos_healer_active` כ**דלוק** כשהמפתח חסר (`settings.py:186`). ה-worker מתייחס אליו כ**כבוי** (`scheduler.py:304,770`). הפאנל מכניס מסמך חלקי, ולכן ה-`$setOnInsert` של ה-worker לא חל | האדמין רואה "פעיל" על משהו שלא רץ |
| B4 | **כשל ביצירת אינדקס בעלייה לא נראה** | `create_all_indexes(silent=True)` משתיק גם את השגיאות (`create_indexes.py:122-148`), ו-`main.py:113` מדפיס "✅ verified" | אינדקס unique שנכשל על כפילויות (בדיוק התרחיש של `normalize_pro_phones`) עובר בשקט |
| B5 | **`/health` חוסם את ה-event loop** | `async def` שקורא ל-`sync_client.admin.command("ping")` (`health.py:97`, `database.py:55`), בלי `serverSelectionTimeoutMS` | כש-Mongo נופל, כל probe יכול לעצור את ה-API עד 30 שניות, כולל קליטת webhooks |
| B6 | **ה-webhook הישן יכול לאבד הודעה** | תופס את מפתח ה-idempotency, ואם ה-enqueue נכשל מחזיר 200 בלי לשחרר אותו (`webhook.py:51,151`). ה-route של Meta עושה את זה נכון (`meta_webhook.py:178-186`) | רלוונטי רק כל עוד ה-route קיים (ראו שלב 8) |
| B7 | **השוואת token לא constant-time ב-webhook הישן** | `webhook.py:31` משתמש ב-`!=`. ב-`meta_webhook` וב-`health` משתמשים ב-`hmac.compare_digest` | timing side-channel על `WEBHOOK_TOKEN` |
| B8 | **`admin_sessions.expiry` נכתב בזמן מקומי naive** | `auth.py:257,332,426` משתמשים ב-`datetime.now()`. ל-collection יש אינדקס TTL, ו-Mongo מפרש את הזמן כ-UTC | נכון רק כל עוד הקונטיינר רץ ב-UTC |
| B9 | **לבדיקה:** fallback של היסטוריית צ'אט מחזיר את ה-N **הישנות** | `lead_manager_service.py:327`: `.sort("timestamp", 1).limit(limit)` | אם יש יותר מ-N הודעות מאז ה-cutoff, ה-AI מקבל את תחילת השיחה ולא את סופה. צריך לבדוק אם זה מכוון |
| B10 | `docker-compose.prod.yml` לא יכול לעלות | `MONGO_URI=` מגדיר מחרוזת ריקה | נפתר ממילא במחיקה (שלב 1) |

---

## 4. מבנה היעד

תיקיות השורש **נשארות**: `app/`, `admin_panel/`, `scripts/`, `tests/`, `docs/`. Railway, Netlify, CI וה-hooks כולם תלויים בהן, ואין סיבה טובה לגעת בהן. הסידור הוא **בתוך** התיקיות.

```
app/
├── main.py                      # ללא שינוי (נקודת הכניסה של uvicorn)
├── api/
│   ├── ingest.py                # חדש: dedup + מגן DDoS + enqueue, משותף לשני ה-routes
│   └── routes/                  # meta_webhook, health, legal (privacy+terms), [webhook הישן עד שלב 8]
├── worker/                      # היה: app/worker.py + app/core/arq_worker.py + app/scheduler.py
│   ├── __main__.py              # `python -m app.worker` ממשיך לעבוד בלי לשנות את Railway
│   ├── arq_settings.py          # היה app/core/arq_worker.py
│   ├── scheduler.py             # חיווט בלבד: רישום jobs + stagger בעלייה
│   └── jobs/                    # daily_agenda, stale_jobs, backup, mongo_auth, ...
├── db/                          # חדש: כל שכבת הנתונים
│   ├── client.py                # יצירה lazy של async ו-sync client, URI resolver אחד, serverSelectionTimeoutMS
│   ├── collections.py           # רישום שמות + מזהי settings (SCHEDULER_CONFIG, ...) + SCHEDULER_CONFIG_DEFAULTS
│   ├── documents.py             # TypedDicts: LeadDoc, ProDoc, SlotDoc, ReviewDoc, ... (טיפוסים בלבד, אפס עלות ריצה)
│   ├── indexes.py               # היה scripts/create_indexes.py (INDEX_SPECS)
│   ├── repositories/            # leads, pros, slots, reviews, messages, settings
│   └── migrations/              # NNNN_<slug>.py + runner + collection בשם schema_migrations
├── core/                        # תשתית בלבד (בלי ידע על לידים/מקצוענים)
│   ├── config.py                # נשאר במקום (37 importers)
│   ├── logger.py                # נשאר; scrub/PII יוצא ל-pii.py, page_critical ל-paging.py
│   ├── sentry.py, startup_checks.py, http_client.py, sync_bridge.py, background_tasks.py
│   ├── redis_client.py
│   ├── redis_keys.py            # חדש: פונקציה לכל משפחת מפתחות, שמחזירה את אותן מחרוזות בדיוק
│   ├── constants.py             # נשאר במקום (39 importers); ISRAEL_CITIES_COORDS יוצא ל-geo_data.py
│   ├── messages.py              # נשאר במקום (טקסטים בלבד); Keywords → keywords.py, Templates → template_bodies.py
│   ├── datetime_utils.py        # IL_TZ אחד מתוך settings.TIMEZONE + now_israel/to_israel (מ-agenda_service)
│   └── phone.py, text_matching.py, pro_trades.py, lead_history.py, legal_links.py, prompts.py
├── providers/whatsapp/          # ללא שינוי, חוץ מ-inbound.py (פענוח Meta יוצא מ-cloud_api.py)
├── schemas/                     # נמחק בשלב 8 יחד עם ה-webhook הישן
└── services/
    ├── dispatch/                # היה workflow_service + dispatch_guards
    │   ├── entry.py             # process_incoming_message + chat lock
    │   ├── context.py           # DispatchContext, GuardDeps (מנגנון ההזרקה היחיד)
    │   ├── chain.py             # GUARD_CHAIN: הבעלים היחיד של הסדר
    │   ├── guards_head.py, guards_holding.py, guards_pro.py, guard_address.py
    │   └── keywords.py          # טבלאות state/keyword של ה-dispatcher
    ├── customer/                # pipeline.py (שלבי _step_*), completion, rating, reschedule, status,
    │                            #   loyalty, emergency, deal
    ├── pro/                     # commands, offers, jobs, lists, reports, search, onboarding
    ├── admin/                   # wizard, assignment (היה admin_flow)
    ├── leads/                   # lifecycle: cancel_booked_lead, escalate_to_review, reassign_lead
    ├── matching/                # matcher, trades (טקסונומיה), [book_slot → scheduling]
    ├── monitors/                # leads (stale/janitor/SLA/nudger/SOS), whatsapp_health
    ├── notification_service.py  # + page_safe_city, תזכורת "סיים עבודה" אחת
    ├── scheduling_service.py    # + book_slot_for_lead + release_slot
    ├── ai_engine_service.py, geocoding_service.py, security_service.py
    ├── state_manager_service.py, context_manager_service.py   # ה-API לא משתנה
    ├── data_management_service.py
    └── media/                   # media_handler + cloudinary client

admin_panel/
├── main.py                      # ללא שינוי (docker-compose, Railway ו-.flake8 מפנים אליו)
├── core/
│   ├── db.py                    # חדש: לקוח sync אחד מ-app.db.client (במקום 3)
│   ├── i18n.py                  # היה core/config.py (TRANS)
│   ├── labels.py, auth.py, rbac.py, refresh.py, runtime_config.py, pro_phone.py
│   └── services/                # היה *_queries.py + assignment.py, + leads/pros/slots actions
│                                #   שיוצאות מה-views (אישור מקצוען, cascade delete, יצירת ליד)
├── ui/
│   ├── components.py            # בלי ה-CSS
│   ├── styles.css               # היה מחרוזת של 1,130 שורות בתוך load_css
│   └── responsive.py, rtl.py
└── views/                       # home.py מפוצל: leads_dashboard, pending_review_strip, flash_report

scripts/
├── _common.py                   # path shim + guard סביבה + parser של --apply (במקום 21 עותקים)
├── ops/                         # backup (CLI עוטף), restore, check_health_leads, meta_healthcheck,
│                                #   submit_whatsapp_templates, probe_template_delivery, finops_report,
│                                #   generate_admin_hash, init_production, fire_test_page, create_indexes (עוטף)
├── maintenance/                 # check_pro_service_areas, normalize_pro_phones, backfill_pro_trades
├── dev/                         # seed_db, seed_coverage_matrix, reset_test, clear_history, simulate_*,
│                                #   admin_panel_*, verify_analytics, simulate_webhook (מ-tests/)
└── cleanup_worktrees.sh

docs/
├── archive/                     # PRODUCTION_READINESS, ADMIN_PANEL_RESPONSIVE_PLAN, MANUAL_TEST_PLAN, ...
└── ...                          # ARCHITECTURE מקבל את DOCUMENTATION_FLOW; REFACTORING_EXAMPLES הופך לנספח של STANDARD
```

### הכללים שהמבנה הזה אוכף

1. **כיוון התלויות:** `api`/`worker` → `services` → `db`/`providers` → `core`. אף פעם לא בכיוון ההפוך. `core` לא מייבא כלום מ-`app.services` או מ-`app.worker`.
2. **`app/` לא מייבא את `scripts/` לעולם.** `scripts/` מייבא את `app/`, ולא להפך.
3. **גישה ל-Mongo רק דרך `app/db/`.** ה-repositories מחזיקים את השאילתות, וה-services מחזיקים את ההחלטות. פונקציות repository נבנות כ-**builders טהורים** (filter/update), כך שגם העטיפה ה-async וגם הסינכרונית של הפאנל מריצות את **אותו** builder. זה הפתרון ל-B2 ולכל הכפילויות בין הפאנל לאפליקציה.
4. **הזרקת תלויות ב-dispatcher רק דרך `GuardDeps`.** בלי `wf.X` בתוך פונקציות ובלי re-exports לטסטים.
5. **הפאנל מייבא שירותים רק דרך רשימת מותרים מוגדרת** (ראו שלב 0): `admin.assignment`, `leads.lifecycle`, `geocoding`, `media`, ה-seams הסינכרוניים של WhatsApp ו-`app.db`.

---

## 5. מה **לא** להזיז, ולמה

שינוי מבנה שמזיז הכל הוא שינוי מבנה שלא נגמר. הדברים הבאים נשארים במקומם בכוונה:

| לא לגעת | למה |
|---|---|
| `app/core/logger.py`, `config.py`, `constants.py`, `messages.py`, `database.py` **כנתיבים** | 52, 37, 39, 26 ו-32 importers בהתאמה. הזזה תייצר מאות שורות diff בלי שום ערך. מפצלים **מתוכם** החוצה, והשם נשאר. (`database.py` הופך לעטיפה דקה מעל `app/db/client.py`) |
| `app/main.py`, `admin_panel/main.py`, `python -m app.worker` | פקודות ההרצה מוגדרות ב-dashboard של Railway, לא בריפו. אם `app/worker.py` הופך לחבילה, `__main__.py` שומר על אותה פקודה |
| `app/providers/whatsapp/` | השכבה הכי נקייה בריפו, וה-guards ב-CI מקבעים את הנתיב. רק מוציאים את `inbound.py` |
| `tests/` **כמבנה שטוח** | 39 קבצים מחשבים את שורש הריפו עם `Path(__file__).parent.parent`, ו-`test_background_task_isolation` קורא קבצים שכנים. מעבר ל-`tests/unit/<area>/` הוא סיכון גבוה עם ערך נמוך. במקום זה: קובץ fixtures/factories משותף ותיקון שמות (שלב 9) |
| `site/` + `netlify.toml`, `.streamlit/`, `railway.json` | חיים ונעולים ע"י טסטים |
| חלוקת `Messages` לקבצים לפי קהל | ה-catalog האחד הוא נכס (ARCHITECTURE_STANDARD §17 Q5). מוציאים ממנו רק את מה ש**אינו** טקסט: `Keywords` ו-`Templates` |

---

## 6. כללי ביצוע — איך לא לשבור כלום

1. **PR אחד = הזזה אחת.** לעולם לא הזזה ושינוי התנהגות באותו PR. התיקונים מסעיף 3 נכנסים ב-PRs נפרדים.
2. **לפני ואחרי כל PR:** `pytest` מלא, כולל `tests/e2e/test_e2e_state_matrix.py`, שהוא רשת הביטחון של ה-dispatcher. CI ממילא נכשל אם מספר הטסטים יורד.
3. **Shim בנתיב הישן** (`from app.new.place import X  # noqa: F401  # moved, remove in <phase>`) **רק לקוד מייבא**, ונמחק בסוף השלב.
4. **⚠️ המלכודת העיקרית: `monkeypatch` על shim לא משפיע על הקוד שזז.** אם טסט עושה `monkeypatch.setattr(workflow_service, "ai", fake)` והקוד שמשתמש ב-`ai` עבר ל-`customer/deal.py`, הטסט יעבור **ירוק** בלי לבדוק כלום. לכן:
   - נקודות ה-patch של הטסטים עוברות **באותו PR** עם הקוד;
   - לפני כל הזזה: `grep -rn "setattr(<module>" tests/` כדי למפות את נקודות ה-patch;
   - עדיף להזיז קודם ל-`GuardDeps` (שלב 5א), כי אז הטסטים מזריקים תלות במקום לעשות patch לשם במודול.
5. **כל הקבצים שמקבעים נתיב זזים יחד עם הקוד** (נספח, סעיף 8):
   - `.github/workflows/tests.yml` (ה-guards);
   - `.flake8` (`scripts/*.py:E402`);
   - `.claude/rules/*` (`paths:`) ו-`.claude/hooks/stop-docs-guard.py`;
   - הנתיבים ב-`CLAUDE.md` (`test_claude_config.py` נכשל על נתיב שלא קיים);
   - ה-workflows שמריצים סקריפטים לפי נתיב.
6. **שמות branch ו-PR בלי מפתח Linear**, אלא אם ה-PR סוגר issue שלם (ראו CLAUDE.md). רוב ה-PRs כאן הם חלקים של מאמץ גדול.
7. **docs-syncer בסוף כל PR** (ה-Stop hook דורש את זה ממילא).

---

## 7. השלבים

הערכה גסה: ~30 PRs קטנים. השלבים מסודרים כך שכל אחד מוריד סיכון לפני הבא. שלבים 1, 2 ו-9 בלתי תלויים, ואפשר להריץ אותם במקביל לשאר.

### שלב 0 — רשת ביטחון (PR אחד, אפס שינוי בקוד)

- **`tests/test_architecture_layers.py`**: טסט AST שמקבע את כללי סעיף 4 עם **רשימת חריגים של המצב הנוכחי**. הרשימה רק מצטמצמת לאורך התוכנית, וכל חריגה חדשה מכשילה את ה-build:
  - `app.core` לא מייבא את `app.services` או את `app.scheduler` (חריג היום: `arq_worker`);
  - `app` לא מייבא את `scripts` (חריג היום: `main.py`);
  - `admin_panel` מייבא מ-`app.services` רק מתוך allowlist (היום: 6 מודולים);
  - ייבוא בתוך פונקציה רק עם הערת `# cycle:` (היום: הרשימה מסעיף 2.6).
- **בסיס מספרי:** לרשום את מספר הטסטים ואת זמן הריצה של ה-e2e.

### שלב 1 — ניקוי בטוח (2–3 PRs, אפס סיכון)

- למחוק את `scripts/seed_analytics_data.py`, `start_railway_services.sh`, `stop_railway_services.sh`, `docker-compose.prod.yml`, `nginx/nginx.prod.conf` ו-`docker-compose.override.yml`, ולעדכן את `README.md:103` ואת ההערות ב-`config.py:205,257`.
- למחוק את מפתחות `Messages` ואת הקבועים שאין להם קורא (סעיף 2.8). מפתח שמשמש רק טסט נבדק אחד-אחד: האם הטסט מגן על משהו?
- למחוק את `LeadManager.get_lead_by_id` ואת `database.sync_db`, ואת הייבואים שקיימים "רק בשביל conftest" (`data_management_service.py:14`, `scheduling_service`). במקביל לעדכן את `conftest`.
- להעביר את `migrate_unknown_address.py` ל-`scripts/maintenance/done/`, או למחוק אותו ולתעד ב-git.
- להסיר את `gunicorn` ואת הכפילות של `pytest-xdist`.
- להוסיף guard סביבה ל-`clear_history.py` ול-`reset_test.py`, כמו `assert_seed_allowed` שכבר קיים ב-`seed_db`.

### שלב 2 — תיקוני הבאגים (PR לכל באג; משנים התנהגות, ולכן נפרדים)

לפי הסדר בטבלה של סעיף 3: B1 → B5 → B2 → B3 → B4 → B8 → B9, ו-B6/B7 אם ה-webhook הישן לא נמחק בקרוב. **שלב זה נפרד מהסידור, וההחלטה עליו היא שלך.** הוא מופיע כאן כי חלק מהסידור (repositories, `SCHEDULER_CONFIG_DEFAULTS`) יפתור ממילא את B2 ו-B3. אם מתקנים אותם קודם, הסידור רק שומר על התיקון.

### שלב 3 — לשחרר את production מ-`scripts/` ולסדר את ה-worker (3 PRs)

1. `scripts/create_indexes.py` → `app/db/indexes.py`. ב-`scripts/create_indexes.py` נשארת עטיפת CLI של 5 שורות. מעדכנים את `app/main.py`.
2. לוגיקת הגיבוי → `app/worker/jobs/backup.py`. `scripts/backup.py` הופך לעטיפת CLI. ה-scheduler ממשיך להריץ subprocess (כדי לא לשנות התנהגות), אבל דרך `python -m app.worker.jobs.backup` ולא לפי נתיב קובץ.
3. `app/worker.py` + `app/core/arq_worker.py` → חבילת `app/worker/` (`__main__.py`, `arq_settings.py`).
   - **לוודא ש-`python -m app.worker` עובד:** זה הפקודה ב-Railway וב-`docker-compose.yml:37`.
   - מעדכנים את 12 הטסטים שמייבאים `arq_worker` (רשימה בסעיף 8).
   - `whatsapp` מגיע מ-`get_whatsapp()` ולא מתוך `workflow_service`.

### שלב 4 — שכבת נתונים (5–6 PRs)

1. **`app/db/client.py`:** URI resolver אחד ו-`serverSelectionTimeoutMS`. `app/core/database.py` הופך לעטיפה דקה. הפאנל (`utils.py`, `auth.py`, `analytics.py`) ו-`reset_test.py` / `verify_analytics.py` עוברים ללקוח האחד.
   - **אזהרה:** זה מאחד את **סדר העדיפויות של משתני ה-URI**. היום `admin_panel/core/utils.py` מעדיף `MONGO_URI` מה-env, ו-`Settings` מנתב ערך ריק דרך ה-aliases. צריך לוודא ש-`MONGO_URI` מוגדר בכל סביבה ב-Railway לפני ה-merge.
2. **`app/db/collections.py`:** שמות + `SCHEDULER_CONFIG_DEFAULTS` אחד ששני התהליכים משתמשים בו. כל ~13 המופעים של `"scheduler_config"` עוברים לקבוע.
3. **`app/core/redis_keys.py`:** פונקציה לכל משפחת מפתחות, שמחזירה את **אותה מחרוזת בדיוק**. בלי שינוי שמות: שינוי שם של מפתח Redis חי = אובדן state.
   - טסט שמשווה כל פונקציה למחרוזת הישנה.
   - `reset_test` ו-DSAR עוברים על הרישום.
4. **Repositories, אחד לכל PR,** לפי סדר הכפילות:
   - `pros.find_by_chat_id` (8 מקומות): **שומר על ההבדל** של `customer_phones` עם 3 צורות. מחליטים אם לאחד רק אחרי בדיקה;
   - `slots.release` / `claim`;
   - `leads.set_status`: builder משותף לאפליקציה ולפאנל;
   - `settings`;
   - `reviews`.

   ה-repositories פותרים את הצורך ב-74 קריאות `setattr`: ה-collection נשלף בזמן קריאה (`repo.leads.collection()`), ו-`conftest` עושה `set_test_db(mock_db)` פעם אחת. **אבל** את `conftest` מעבירים רק אחרי שכל המודולים עברו. עד אז שני המנגנונים חיים זה לצד זה.
5. **`app/db/documents.py`:** TypedDicts לפי השדות שנכתבים בפועל, + טסט AST שבודק שכל מפתח `$set` מוכרז. הטסט ייכשל על `reminder_sent_count`/`reminders_sent` ועל `customer_phone`. **מתעדים** את הסטיות כחריגים, ולא מתקנים אותן כאן.
6. **מנגנון מיגרציות:**
   - `app/db/migrations/NNNN_<slug>.py` עם `async def up(db, apply: bool)`;
   - runner שרושם `{_id, applied_at, counts}` ב-`schema_migrations`;
   - כל shim בקוד מקבל תגית `# MIGRATION NNNN`;
   - ה-backfills הקיימים (`normalize_pro_phones`, `backfill_pro_trades`) נרשמים כמיגרציות 0001–0002. ה-workflows שלהם ממשיכים לעבוד דרך עטיפה;
   - הסרת shims נעשית **רק** אחרי ש-`schema_migrations` ב-production מראה שהמיגרציה רצה.
7. **(נפרד, משנה ביצועים)** האינדקסים החסרים והמיותרים מסעיף 2.4, ולוג ERROR לכל כשל אינדקס (B4).

### שלב 5 — פירוק ה-dispatcher וה-services (הכי גדול, 8–10 PRs)

**סדר הפעולות כאן הוא העיקר:**

- **5א. קודם שוברים את המעגל, בלי להזיז קבצים.**
  1. מוסיפים ל-`GuardDeps` את `whatsapp`, `ai`, `lead_manager` ו-`leads_collection`.
  2. מעבירים את ה-guards לקרוא ל-`deps.X` במקום ל-`wf.X`.
  3. מעבירים את נקודות ה-patch בטסטים להזרקה.

  בסוף השלב 22 הייבואים בתוך פונקציות וה-25 re-exports נעלמים. **זה ה-PR המסוכן ביותר בתוכנית:** להריץ את ה-state matrix לפני ואחרי, ולפרק אותו ל-2–3 PRs לפי קבוצות guards.
- **5ב. מוציאים את ה-helpers ש-guards קוראים להם** מ-`workflow_service` למודולים משלהם: `customer/loyalty.py`, `customer/emergency.py`, `customer/deal.py`, ו-`pros.find_by_chat_id` משלב 4. אחרי זה ה-guards מייבאים אותם ב-top-level.
- **5ג. חבילת `services/dispatch/`.** `chain.py` מחזיק את `GUARD_CHAIN` בסדר **זהה**, ו-guards מקובצים לפי A1/A2/A3/address. טסט מקבע את רשימת שמות ה-guards ואת הסדר שלהם.
- **5ד. `run_customer_pipeline` → `customer/pipeline.py`:** רצף של `_step_*(ctx, deps, turn)` לפי ההערות הקיימות, עם dataclass `TurnState`. הפונקציה הראשית הופכת לרשימה של ~12 קריאות.
- **5ה. פיצול `pro_flow`, `customer_flow`, `monitor_service`** לחבילות `pro/`, `customer/`, `monitors/`. לוגיקה משותפת עוברת ל-`leads/lifecycle.py`: `cancel_booked_lead` (מאחד את שני הביטולים), `escalate_to_admin_review` (מאחד 9 מופעים) ו-`reassign_lead`.
  - **אזהרה:** איחוד שני מסלולי ביטול הוא שינוי התנהגות אם הם שונים. קודם בודקים diff מדויק ביניהם. אם יש הבדל, הוא נשמר כפרמטר.
- **5ו. `scheduler.py` → `app/worker/scheduler.py` (חיווט) + `app/worker/jobs/*`.**
  - `job_enabled(key)` אחד במקום 5 בדיקות `scheduler_config` פזורות.
  - `within_business_hours()` במקום `8 <= hour < 21`.
- **5ז. `matching_service`:**
  - `book_slot_for_lead` עובר ל-`scheduling_service`;
  - הטקסונומיה של הטריידים עוברת ל-`matching/trades.py`;
  - `get_coordinates` נמחק (טסטים בלבד, וכפול עם `geocoding._static_lookup`).
- **5ח. שמות:**
  - `media_handler` + `cloudinary_client_service` → `media/`;
  - `agenda_service` → `notification/agenda.py` או `core/formatting`;
  - `_handle_pro_cmd` → השם הציבורי;
  - פעלי jobs אחידים: `run_*` ב-scheduler, `check_*` / `remind_*` ב-monitors, לפי הסטנדרט §7.
- **5ט. אזור זמן:** `IL_TZ` אחד ב-`core/datetime_utils.py` מתוך `settings.TIMEZONE`, ו-`now_israel` / `to_israel` עוברים לשם. מחליפים את 11 ההגדרות **בלי** לעבור בין pytz ל-ZoneInfo באותו PR.

**לא נוגעים** ב-classes של `StateManager` / `ContextManager` / `SecurityService`: 9 ו-7 importers, וה-API עובד. האחדה לסגנון "פונקציות" היא קוסמטית ולא שווה את הסיכון.

### שלב 6 — פאנל האדמין (4–5 PRs)

1. `core/config.py` → `core/i18n.py`, עם shim. מעדכנים `skills/whatsapp-copy` ו-`rules/admin-panel.md`.
2. ה-CSS מ-`ui/components.py:70-1202` עובר ל-`ui/styles.css`, ש-`load_css` קורא מהדיסק.
   - **לוודא** ש-`.dockerignore` לא מסנן אותו, ושהטסטים שבודקים את ה-CSS (`test_admin_responsive`) קוראים מהמקום החדש.
3. `core/utils.py` מתפרק:
   - חיבור Mongo → `core/db.py` (משלב 4);
   - `PROFESSION_CONFIG` / `generate_system_prompt` → `app/core/prompts.py`;
   - `create_initial_schedule` → `scheduling` דרך עטיפה סינכרונית. **שימו לב:** שני המחוללים מייצרים slots בצורות שונות (שעתיים מול תבנית). האיחוד הוא שינוי התנהגות, ולכן קודם מעבירים, ורק אחר כך מחליטים אם לאחד;
   - `send_completion_check_sync` → עטיפה מעל הגרסה ה-async.
4. לוגיקה עסקית יוצאת מה-views ל-`core/services/`: אישור מקצוען, מחיקה עם cascade, יצירת ליד ומחיקתו. יצירת ליד עוברת ל-builder המשותף מ-`app/db/repositories/leads`. (גם זה שינוי בצורת המסמך, ולכן ב-PR נפרד.)
5. פיצול `views/home.py`: `view_leads_dashboard` מתפרק לרכיבים, ו-flash/report ו-pending-strip עוברים לקבצים משלהם.

### שלב 7 — סקריפטים (2 PRs)

1. `scripts/_common.py`: path shim + `assert_env_allowed()` + parser אחיד של `--apply`/`--dry-run`. ברירת המחדל תמיד dry-run. **לשים לב:** ב-`migrate_unknown_address` הלוגיקה הפוכה, וזו עוד סיבה להעביר אותו ל-done.
2. מעבר ל-`ops/`, `maintenance/` ו-`dev/`. באותו PR:
   - שלושת ה-workflows שמריצים סקריפטים לפי נתיב;
   - `.flake8` (`scripts/**/*.py:E402`);
   - `.claude/commands/*`;
   - `.claude/agents/docs-syncer.md`;
   - `docs/SCRIPTS.md` (כולל 11 החסרים);
   - הנתיבים ב-`CLAUDE.md`;
   - 12 הטסטים שמייבאים או טוענים סקריפטים.

   את `tests/simulate_webhook.py` מעבירים ל-`scripts/dev/`.

### שלב 8 — קונפיגורציה, API ודיפלוי (4 PRs)

1. **`.env.example`** עם כל שדות `Settings`, מקובצים ומתועדים. מוסיפים ל-`Settings` את `ADMIN_PASSWORD_HASH` (כ-`SecretStr`), ואת `RAILWAY_*` / `PORT` כשדות. הפאנל מפסיק לקרוא `os.getenv` ישירות. מתקנים את ההערות שהתיישנו ואת ה-README.
2. **`app/api/ingest.py`:** dedup, מגן DDoS ו-enqueue משותפים לשני ה-routes. פענוח הודעות נכנסות של Meta עובר ל-`providers/whatsapp/inbound.py`. `privacy` ו-`terms` מתאחדים ל-`routes/legal.py`, יחד עם `legal_links`.
3. **הסרת ה-webhook הישן** (החלטה שלך):
   1. קודם מעבירים את ה-e2e harness (`tests/e2e/world.py:317`) ואת `/simulate` למעטפת של Meta;
   2. אחר כך מוחקים את ה-route, את `app/schemas/`, את הדרישה ל-`WEBHOOK_TOKEN` ואת בלוק `/webhook` ב-nginx.

   **מה ייסגר:** endpoint חשוף ב-production, ושני הבאגים B6 ו-B7.
4. **דיפלוי ותלויות:**
   - ב-Dockerfile: מוחקים את `PATH` / `PYTHONPATH` השבורים, ומעבירים את `HEALTHCHECK` ל-compose לפי שירות;
   - `--workers` אחיד עם Railway;
   - פיצול `requirements.txt` (runtime) מול `requirements-dev.txt` (טסטים ולינט). **שינוי ב-CI:** `tests.yml` צריך להתקין את שני הקבצים. `test_requirements_pins.py` ו-`deploy-check` מתעדכנים באותו PR.

### שלב 9 — תיעוד, טסטים ו-`.claude` (רץ במקביל לכל השאר)

- **`docs/archive/`:** `PRODUCTION_READINESS`, `ADMIN_PANEL_RESPONSIVE_PLAN`, `MANUAL_TEST_PLAN`, וסעיף 5 של `SCALING_GUIDE`.
- **איחודים:**
  - `DOCUMENTATION_FLOW` נכנס לתוך `ARCHITECTURE`;
  - `REFACTORING_EXAMPLES` הופך לנספח של `ARCHITECTURE_STANDARD`;
  - טבלת משתני הסביבה נשארת במקום אחד (`RAILWAY_SETUP`), ושאר המסמכים מפנים אליה.
- **עדכונים:**
  - `ARCHITECTURE_STANDARD` §2 ו-§16: מספרים עדכניים + הפניה למסמך הזה;
  - `CLAUDE.md`: רשימת ה-collections (חסר בה `admin_sessions`);
  - `.claude/rules/services.md` לפי המבנה החדש.
- **טסטים, בלי להזיז קבצים:**
  - `tests/factories.py` עם `_pro` / `_lead` (מוגדרים היום 8 ו-4 פעמים) ו-`mock_whatsapp` (7 פעמים) ל-`conftest`;
  - `resolve_script` (משוכפל בשלושה טסטים של workflows) ל-helper משותף;
  - `test_ai_media_failure` מפסיק לייבא fixture מקובץ טסט אחר.
- **שמות טסטים:**
  - `test_admin_flow.py` → `test_admin_flow_service.py`, כדי שלא ייתפס ב-glob של הפאנל;
  - ארבעת טסטי הפאנל שלא תואמים את ה-glob מקבלים קידומת `test_admin_`;
  - `test_unit_*` ו-`test_integration_*` מתיישרים ל-`test_<subject>`.

### סדר מומלץ

```
שלב 0 ──► שלב 1 ──► שלב 3 ──► שלב 4 ──► שלב 5 ──► שלב 6 ──► שלב 7 ──► שלב 8
             │                                                    ▲
             └─► שלב 2 (באגים, בקצב שלך) ─────────────────────────┘
שלב 9 ─── לאורך כל הדרך
```

שלב 3 בא לפני 7, כי אי אפשר להזיז את `scripts/` כל עוד production תלוי בו. שלב 4 בא לפני 5 ו-6, כי ה-repositories הם מה שמאפשר לפרק את ה-flows ולאחד את הכפילויות עם הפאנל.

---

## 8. נספח: כל מה שמקבע את המבנה הנוכחי

כל שינוי נתיב חייב לעדכן את אלה **באותו PR**:

**Production runtime**
- `app/main.py:20` → `scripts.create_indexes`
- `app/scheduler.py:403` → `scripts/backup.py`
- Railway dashboard: `uvicorn app.main:app`, `python -m app.worker`, `streamlit run admin_panel/main.py`
- `docker-compose.yml:37,65`

**CI ולינט**
- `.github/workflows/tests.yml:45` (guard ל-vendor), `:66-67` (`app/services/`, `geocoding_service.py`), `:76-78` (`app/`, `app/providers/whatsapp/`)
- `backfill_pro_trades.yml:116`, `check_pro_service_areas.yml:113`, `health-leads-monitor.yml:64`
- `.flake8:28-34` (`admin_panel/main.py`, `admin_panel/core/auth.py`, `scripts/*.py`)
- `pytest.ini` (`testpaths = tests`)
- `netlify.toml` (`base = "site"`), `.dockerignore`

**`.claude/`**
- `hooks/stop-docs-guard.py:54`: `app/`, `admin_panel/`, `scripts/`
- `hooks/pre-edit-protect.py:27-45`
- `rules/*.md` `paths:` (admin-panel, config-secrets, testing, services, constants)
- `skills/whatsapp-copy` (`admin_panel/core/utils.py`), `skills/dispatch-guard` (3 קבצים ב-`app/services`), `skills/scheduler-job`
- `commands/finops|sla-test|simulate|deploy-check|db-status|cleanup-worktrees|full-sync-docs.md`
- `agents/docs-syncer.md:31`, `agents/test-writer.md`
- `CLAUDE.md`: כל נתיב בתוך backticks נבדק ע"י `tests/test_claude_config.py:353`

**טסטים שקוראים קבצי מקור לפי נתיב**
- **פאנל:**
  - `test_admin_panel_invariants.py:8`
  - `test_admin_translations.py:9`
  - `test_admin_view_call_arity.py:47,276,397`
  - `test_admin_action_rows.py:40,262,274,353`
  - `test_admin_set_pro.py:182-206,637`
  - `test_admin_muted_send.py:655`
  - `test_admin_refresh.py:38`
  - `test_environment_config.py:340`
  - `test_template_bodies.py:535-645`
  - `test_settings_secret_masking.py:249-317` (allowlist: `app/core/database.py`, `admin_panel/core/auth.py`, `admin_panel/views/analytics.py`)
  - `test_log_pii.py:13`
  - `test_whatsapp_facade.py:754`
  - `test_admin_responsive.py:511`
  - `test_admin_fast_reruns.py:295-327`
- **סקריפטים (import):** `test_backup_script.py:20`, `test_fire_test_page.py:20`, `test_check_pro_service_areas.py:20`, `test_pro_phone_normalisation.py:19`, `test_pro_trades.py:20`, `test_environment_config.py:276-316`, `test_seed_coverage_matrix.py:25`
- **סקריפטים (path):** `test_probe_template_delivery.py:21`, `test_submit_whatsapp_templates.py:16`, `test_backfill_pro_trades_workflow.py:36`, `test_check_health_leads.py:20`
- **`app/`:**
  - `test_status_history.py:355`
  - `test_agent_pack_drift.py:50-224` (`workflow_service`, `dispatch_guards`, `conversation_pipeline`)
  - `test_ai_media_upload.py:39`
  - `test_whatsapp_quality.py:1013`
  - `test_page_critical.py:105`
  - `test_settings_admin_phone.py:180`
  - `test_terms_route.py:299` (`app/main.py`, `app/core/arq_worker.py`)
  - `e2e/test_e2e_state_matrix.py:1344`
- **שמייבאים את `arq_worker`:**
  - `test_arq_worker_message_id`, `test_arq_worker_meta_media`, `test_arq_worker_trace_id`
  - `test_sentry_init`, `test_sentry_capture`
  - `test_log_pii`, `test_terms_route`, `test_settings_admin_phone`
  - `e2e/world.py`, `e2e/conftest.py`, `e2e/test_e2e_consent_hold.py`, `e2e/test_e2e_chat_lock.py`
- **workflows ו-config:**
  - `test_promote_workflow`, `test_stop_workflow`, `test_check_pro_service_areas_workflow`, `test_backfill_pro_trades_workflow`, `test_ci_workflows`
  - `test_vendor_guard_workflow:33,165`: מוודא ש-`app`, `scripts`, `tests` ו-`admin_panel` לא מוחרגים מה-guard
  - `test_claude_config`, `test_pre_bash_guard`, `test_post_edit_format`, `test_stop_docs_guard`, `test_agent_least_privilege`
  - `test_public_site`, `test_requirements_pins`
- **מבנה `tests/` עצמו:** 39 קבצים עם `Path(__file__).parent.parent`, `test_background_task_isolation.py:122-147`, וכ-30 ייבואים של `tests.e2e` / `tests.copy_util`.
