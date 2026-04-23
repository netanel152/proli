from enum import Enum

class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    BOOKED = "booked"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CLOSED = "closed"
    CANCELLED = "cancelled"
    PENDING_ADMIN_REVIEW = "pending_admin_review"

class UserStates(str, Enum):
    IDLE = "idle"                      # Default state
    PRO_MODE = "pro_mode"              # User is acting as a Professional
    CUSTOMER_MODE = "customer_mode"    # Pro temporarily acting as a customer
    AWAITING_INTENT_CONFIRMATION = "awaiting_intent_confirmation"  # Transient: waiting for 1/2 after intent detected
    CUSTOMER_FLOW = "customer_flow"    # User is in a booking flow
    AWAITING_ADDRESS = "awaiting_address"
    AWAITING_MEDIA = "awaiting_media"
    AWAITING_TIME = "awaiting_time"
    AWAITING_CONSENT = "awaiting_consent"  # Waiting for privacy consent
    SOS = "sos"
    AWAITING_PRO_APPROVAL = "awaiting_pro_approval"
    PAUSED_FOR_HUMAN = "paused_for_human"
    # Pro onboarding flow
    ONBOARDING_NAME = "onboarding_name"
    ONBOARDING_TYPE = "onboarding_type"
    ONBOARDING_AREAS = "onboarding_areas"
    ONBOARDING_PRICES = "onboarding_prices"
    ONBOARDING_CONFIRM = "onboarding_confirm"

class WorkerConstants:
    MAX_PRO_LOAD = 3
    DB_QUERY_LIMIT = 100
    SLOT_DURATION_HOURS = 1
    SLOT_SEARCH_WINDOW_HOURS = 2
    DEFAULT_CURRENCY = "ILS"
    SOS_TIMEOUT_MINUTES = 60
    MAX_REASSIGNMENTS = 3          # Max times a lead can be reassigned before closing
    UNASSIGNED_LEAD_TIMEOUT_HOURS = 24  # Auto-reject CONTACTED leads with no pro after this
    MAX_PRO_REMINDERS = 3          # Max reminder messages sent to a pro for a stale booked lead
    GEO_RADIUS_STEPS = [10000, 20000, 30000]  # Progressive search radius in meters (10km, 20km, 30km)
    PAUSE_TTL_SECONDS = 900        # 15 minutes — auto-expiry for PAUSED_FOR_HUMAN state
    PRO_APPROVAL_TTL_SECONDS = 3600  # 60 min — pro must approve a finalized deal within this window
    # ADMIN_PHONE moved to config.py / env var

class APIStatus:
    RUNNING = "Proli is running! 🚀"
    PROCESSING = "processing_message"
    IGNORED_GROUP = "ignored_group"
    IGNORED_NO_DATA = "ignored_no_data"
    IGNORED_WRONG_INSTANCE = "ignored_wrong_instance"
    IGNORED_TYPE = "ignored_type"
    IGNORED_RATE_LIMIT = "ignored_rate_limit"
    ERROR = "error"

class Defaults:
    PENDING_TIME = "Pending"
    ASAP_TIME = "As soon as possible"
    # DEPRECATED — kept only so the migration script and monitor_service.py
    # backward-compat skip can reference the legacy sentinel. NEW CODE MUST
    # NOT WRITE THIS VALUE. full_address is now nullable; a missing address
    # should be represented as None. Scheduled for removal after the migration
    # sweep clears all historical docs (see scripts/migrate_unknown_address.py).
    UNKNOWN_ADDRESS = "Unknown Address"
    UNKNOWN_ISSUE = "Issue"
    GENERIC_PRO_NAME = "איש המקצוע"
    PROLI_PRO_NAME = "Proli Pro"
    EXPERT_NAME = "מומחה"
    DEFAULT_TRANSCRIPTION = "None"
    DEFAULT_MIME_TYPE = "image/jpeg"

class AdminDefaults:
    UNKNOWN_PRO = "Unknown"
    MANUAL_SOURCE = "manual_admin"
    MANUAL_LABEL = "Manual"
    DEFAULT_PLAN = "basic"
    DEFAULT_RATING = 5.0

ISRAEL_CITIES_COORDS = {
    # Hebrew names (primary - AI returns Hebrew)
    "תל אביב": [34.7818, 32.0853],
    "תל-אביב": [34.7818, 32.0853],
    "תל אביב יפו": [34.7818, 32.0853],
    "ת\"א": [34.7818, 32.0853],
    "ירושלים": [35.2137, 31.7683],
    "חיפה": [34.9896, 32.7940],
    "ראשון לציון": [34.7925, 31.9730],
    "ראשלצ": [34.7925, 31.9730],
    "פתח תקווה": [34.8878, 32.0840],
    "פתח תקוה": [34.8878, 32.0840],
    "אשדוד": [34.6553, 31.8044],
    "נתניה": [34.8532, 32.3215],
    "באר שבע": [34.7913, 31.2518],
    "חולון": [34.7742, 32.0158],
    "בני ברק": [34.8254, 32.0849],
    "רמת גן": [34.8115, 32.0684],
    "רחובות": [34.8113, 31.8928],
    "בת ים": [34.7515, 32.0162],
    "אשקלון": [34.5715, 31.6690],
    "הרצליה": [34.8254, 32.1624],
    "כפר סבא": [34.9079, 32.1750],
    "חדרה": [34.9197, 32.4340],
    "מודיעין": [35.0145, 31.8903],
    "רעננה": [34.8674, 32.1848],
    "לוד": [34.8951, 31.9530],
    "רמלה": [34.8625, 31.9279],
    "נס ציונה": [34.7983, 31.9293],
    "הוד השרון": [34.8883, 32.1537],
    "גבעתיים": [34.8101, 32.0717],
    "קריית אתא": [35.1064, 32.8049],
    "קריית גת": [34.7715, 31.6064],
    "אילת": [34.9519, 29.5569],
    "עפולה": [35.2894, 32.6100],
    "טבריה": [35.5327, 32.7922],
    "נצרת": [35.3039, 32.6996],
    "עכו": [35.0736, 32.9276],
    "כרמיאל": [35.2961, 32.9114],
    "רהט": [34.7589, 31.3886],
    "יבנה": [34.7388, 31.8782],
    "אור יהודה": [34.8525, 32.0296],
    "צפת": [35.4975, 32.9648],
    "דימונה": [35.0335, 31.0683],
    "טירה": [34.9495, 32.2341],
    "קלנסווה": [34.9741, 32.2833],
    # English names (fallback)
    "tel aviv": [34.7818, 32.0853],
    "tel-aviv": [34.7818, 32.0853],
    "tlv": [34.7818, 32.0853],
    "jerusalem": [35.2137, 31.7683],
    "haifa": [34.9896, 32.7940],
    "rishon letsiyon": [34.7925, 31.9730],
    "rishon": [34.7925, 31.9730],
    "petah tikva": [34.8878, 32.0840],
    "ashdod": [34.6553, 31.8044],
    "netanya": [34.8532, 32.3215],
    "beersheba": [34.7913, 31.2518],
    "beer sheva": [34.7913, 31.2518],
    "holon": [34.7742, 32.0158],
    "bnei brak": [34.8254, 32.0849],
    "ramat gan": [34.8115, 32.0684],
    "rehovot": [34.8113, 31.8928],
    "bat yam": [34.7515, 32.0162],
    "ashkelon": [34.5715, 31.6690],
    "herzliya": [34.8254, 32.1624],
    "kfar saba": [34.9079, 32.1750],
    "hadera": [34.9197, 32.4340],
    "modiin": [35.0145, 31.8903],
    "raanana": [34.8674, 32.1848],
}