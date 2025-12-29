from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import MongoClient
from app.core.config import settings
import certifi

# Determine if SSL is needed (usually for Atlas/Cloud)
ca_file = certifi.where() if "+srv" in settings.MONGO_URI else None
kwargs = {"tlsCAFile": ca_file} if ca_file else {}

# --- Async Client (Motor) for FastAPI ---
client = AsyncIOMotorClient(settings.MONGO_URI, **kwargs)
db = client.fixi_db

# Async Collections
users_collection = db.users
messages_collection = db.messages
leads_collection = db.leads
slots_collection = db.slots
settings_collection = db.settings

# --- Sync Client (PyMongo) ---
# Kept strictly for synchronous scripts or legacy tools if needed.
# Use 'sync_client' explicitly if you need blocking calls.
sync_client = MongoClient(settings.MONGO_URI, **kwargs)
sync_db = sync_client.fixi_db

def check_db_connection():
    try:
        # Motor's ping is async, so we use the sync client for a quick health check function
        # if this function is called synchronously.
        sync_client.admin.command('ping')
        return True
    except Exception:
        return False
