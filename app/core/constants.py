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

class WorkerConstants:
    MAX_PRO_LOAD = 3
    DB_QUERY_LIMIT = 100
    SLOT_DURATION_HOURS = 1
    SLOT_SEARCH_WINDOW_HOURS = 2
    DEFAULT_CURRENCY = "ILS"
    SOS_TIMEOUT_MINUTES = 60
    ADMIN_PHONE = "972524828796"

class APIStatus:
    RUNNING = "Fixi is running! 🚀"
    PROCESSING = "processing_message"
    IGNORED_GROUP = "ignored_group"
    IGNORED_NO_DATA = "ignored_no_data"
    IGNORED_WRONG_INSTANCE = "ignored_wrong_instance"
    IGNORED_TYPE = "ignored_type"
    ERROR = "error"

class Defaults:
    PENDING_TIME = "Pending"
    ASAP_TIME = "As soon as possible"
    UNKNOWN_ADDRESS = "Unknown Address"
    UNKNOWN_ISSUE = "Issue"
    GENERIC_PRO_NAME = "איש המקצוע"
    FIXI_PRO_NAME = "Fixi Pro"
    EXPERT_NAME = "מומחה"
    DEFAULT_TRANSCRIPTION = "None"

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