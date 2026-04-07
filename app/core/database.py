from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient, uri_parser
from app.core.config import settings
import certifi

# Determine if SSL is needed (usually for Atlas/Cloud)
ca_file = certifi.where() if "+srv" in settings.MONGO_URI else None
kwargs = {"tlsCAFile": ca_file} if ca_file else {}

# Extract database name from URI (supports proli_db, proli_staging_db, etc.)
_parsed_uri = uri_parser.parse_uri(settings.MONGO_URI)
DB_NAME = _parsed_uri.get("database") or "proli_db"

# --- Async Client (Motor) for FastAPI ---
client = AsyncIOMotorClient(
    settings.MONGO_URI,
    maxPoolSize=settings.MONGO_MAX_POOL_SIZE,
    minPoolSize=settings.MONGO_MIN_POOL_SIZE,
    maxIdleTimeMS=settings.MONGO_MAX_IDLE_TIME_MS,
    **kwargs
)
db = client[DB_NAME]

# Async Collections
users_collection = db.users
messages_collection = db.messages
leads_collection = db.leads
slots_collection = db.slots
settings_collection = db.settings
reviews_collection = db.reviews
consent_collection = db.consent
audit_log_collection = db.audit_log
admins_collection = db.admins

# --- Sync Client (PyMongo) ---
# Kept strictly for synchronous scripts or legacy tools if needed.
# Use 'sync_client' explicitly if you need blocking calls.
sync_client = MongoClient(settings.MONGO_URI, **kwargs)
sync_db = sync_client[DB_NAME]

def check_db_connection():
    try:
        # Motor's ping is async, so we use the sync client for a quick health check function
        # if this function is called synchronously.
        sync_client.admin.command('ping')
        return True
    except Exception:
        return False
