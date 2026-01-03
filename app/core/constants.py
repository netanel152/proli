from enum import Enum

class LeadStatus(str, Enum):
    NEW = "new"
    CONTACTED = "contacted"
    BOOKED = "booked"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CLOSED = "closed"
    CANCELLED = "cancelled"

class WorkerConstants:
    MAX_PRO_LOAD = 3
    DB_QUERY_LIMIT = 100
    SLOT_DURATION_HOURS = 1
    SLOT_SEARCH_WINDOW_HOURS = 2
    DEFAULT_CURRENCY = "ILS"

class APIStatus:
    RUNNING = "Fixi is running! 🚀"
    PROCESSING = "processing_message"
    IGNORED_GROUP = "ignored_group"
    IGNORED_NO_DATA = "ignored_no_data"
    IGNORED_WRONG_INSTANCE = "ignored_wrong_instance"
    IGNORED_TYPE = "ignored_type"
    ERROR = "error"