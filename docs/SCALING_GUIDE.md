# מדריך להרחבת המערכת (Scaling Guide)

Proli בנויה כפלטפורמה מולטי-מודאלית ומבוזרת. מנוע הניתוב החכם מאפשר למערכת לגדול לאלפי אנשי מקצוע.

## 1. מנוע הניתוב והרחבה (Routing Engine)

המערכת משתמשת בלוגיקה חכמה כדי לבזר עומסים:
*   **Geo-Spatial Routing:** שימוש ב-MongoDB `$near` (10 ק"מ רדיוס) למציאת אנשי מקצוע קרובים.
*   **Fallback:** אם אין קורדינטות, המערכת עוברת ל-Regex על `service_areas`.
*   **Load Balancing:** המערכת בודקת כמה עבודות פתוחות (`new`/`contacted`/`booked`) יש לכל איש מקצוע.
*   **סף עומס (Threshold):** כרגע מוגדר ל-3 עבודות במקביל (`WorkerConstants.MAX_PRO_LOAD`). אם איש מקצוע עמוס, המערכת תעבור אוטומטית לבא בתור בדירוג.
*   **משמעות:** ניתן להוסיף עוד ועוד אנשי מקצוע לאותו אזור, והמערכת תחלק ביניהם את העבודה אוטומטית.

**ביצועים:** Load Balancing מבוצע באמצעות `$group` Aggregation Pipeline יחיד (שאילתה אחת לכל אנשי המקצוע, במקום N+1 שאילתות).

## 2. הגדרת מקצוע חדש

כדי להוסיף תחום חדש (למשל: טכנאי מזגנים):

1.  **הגדרת הפרופיל ב-DB:**
    *   צור משתמש חדש דרך ה-Admin Panel (לשונית "אנשי מקצוע").
    *   **`service_areas`:** חשוב מאוד להגדיר ערים מדויקות לשיפור הניתוב.
    *   **`location`:** להוסיף GeoJSON Point עם קורדינטות למיקום הבסיס של איש המקצוע.

2.  **הנדסת הפרומפט (System Prompt):**
    *   המערכת תומכת ב-Prompts דינמיים — לכל איש מקצוע יש שדה `system_prompt` משלו.
    *   בעת הוספת מקצוע, יש לכתוב הנחיות ספציפיות (למשל: "שאל על סוג הגז במזגן").
    *   **הערה:** הפונקציה `generate_system_prompt` ב-Admin Panel תומכת בכל 7 סוגי המקצועות: אינסטלטור, חשמלאי, הנדימן, מנעולן, צבעי, מנקה, וכללי.

## 3. מולטימדיה (Vision/Video/Audio)

אין צורך בשינוי קוד. המודל (Gemini 2.5) יודע לנתח תמונות וסרטונים של כל סוגי התקלות באופן גנרי. המערכת תומכת כיום ב:
*   **תמונות:** הורדה לזיכרון ושליחה כ-bytes ל-Gemini.
*   **וידאו:** סטרימינג לקובץ זמני, העלאה ל-Gemini File API, המתנה לעיבוד (timeout: 120s).
*   **הודעות קוליות:** אותו תהליך כמו וידאו — File API + תמלול.

## 4. סקיילינג אופקי (Horizontal Scaling)

### API (FastAPI)
*   Stateless לחלוטין — ניתן להוסיף replicas ללא הגבלה.
*   כל ה-state נשמר ב-Redis/MongoDB.

### Worker (ARQ)
*   ניתן להרחיב ל-2+ workers שמצביעים על אותו Redis.
*   **בעיה ידועה:** ה-Scheduler (APScheduler) רץ בתוך ה-Worker. עם מספר workers, כל אחד יריץ את ה-Scheduler בנפרד — מה שגורם לכפילויות (SOS alerts כפולים, reminders כפולים).
*   **פתרון נדרש:** הוספת מנגנון נעילה מבוזר (Redis `SET NX`) לפני כל ריצת job תזמון. או הפרדת ה-Scheduler לתהליך נפרד.

### Admin Panel (Streamlit)
*   רץ בנפרד ומשתמש ב-PyMongo (sync). לא צריך scaling — מיועד למנהל אחד.

## 5. Railway Deployment

### מצב נוכחי (בעייתי)
קונטיינר יחיד (`start.sh`) מריץ את שלושת השירותים. בעיות:
- פורט 8501 (Admin) לא נגיש מבחוץ.
- Worker רץ ב-background ללא restart.
- `numReplicas > 1` גורם לכפילויות ב-Scheduler.

### מצב מומלץ
פיצול ל-3 Railway Services:

| Service | Command | Port |
|---|---|---|
| API | `uvicorn app.main:app --host 0.0.0.0 --port 8000` | 8000 |
| Worker | `python -m app.worker` | - |
| Admin | `streamlit run admin_panel/main.py --server.port 8501 --server.address 0.0.0.0` | 8501 |

כל השלושה משתפים את אותם env vars (MongoDB Atlas, Redis, Green API).

## 6. התאמות בקוד (Advanced)

אם נדרש שינוי בלוגיקת הניתוב:
*   קובץ: `app/services/matching_service.py`
*   פונקציה: `determine_best_pro`
*   ניתן להוסיף סינון לפי שדה `type` (ProType enum) ב-User Object.
*   להוספת עיר חדשה לניתוב Geo: הוסיפו את הקורדינטות ל-`ISRAEL_CITIES_COORDS` ב-`constants.py`.
