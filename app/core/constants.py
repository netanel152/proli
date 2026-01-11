from enum import Enum

class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    BOOKED = "booked"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CLOSED = "closed"
    CANCELLED = "cancelled"

class UserStates(str, Enum):
    IDLE = "idle"                      # Default state
    PRO_MODE = "pro_mode"              # User is acting as a Professional
    CUSTOMER_FLOW = "customer_flow"    # User is in a booking flow
    AWAITING_ADDRESS = "awaiting_address"
    AWAITING_MEDIA = "awaiting_media"
    AWAITING_TIME = "awaiting_time"
    SOS = "sos"

class WorkerConstants:
    MAX_PRO_LOAD = 3
    DB_QUERY_LIMIT = 100
    SLOT_DURATION_HOURS = 1
    SLOT_SEARCH_WINDOW_HOURS = 2
    DEFAULT_CURRENCY = "ILS"
    SOS_TIMEOUT_MINUTES = 60
    ADMIN_PHONE = "972524828796"

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
    UNKNOWN_ADDRESS = "Unknown Address"
    UNKNOWN_ISSUE = "Issue"
    GENERIC_PRO_NAME = "איש המקצוע"
    PROLI_PRO_NAME = "Proli Pro"
    EXPERT_NAME = "מומחה"
    DEFAULT_TRANSCRIPTION = "None"
    DEFAULT_MIME_TYPE = "image/jpeg"

class ProType(str, Enum):
    PLUMBER = "plumber"
    ELECTRICIAN = "electrician"
    HANDYMAN = "handyman"
    LOCKSMITH = "locksmith"
    PAINTER = "painter"
    CLEANER = "cleaner"
    GENERAL = "general"

class AdminDefaults:
    UNKNOWN_PRO = "Unknown"
    MANUAL_SOURCE = "manual_admin"
    MANUAL_LABEL = "Manual"
    DEFAULT_PLAN = "basic"
    DEFAULT_RATING = 5.0

ISRAEL_CITIES_COORDS = {
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