from pymongo import MongoClient
from app.core.config import settings
import certifi

# Create a connection to MongoDB
# tlsCAFile=certifi.where() פותר את בעיות ה-Handshake בווינדוס
client = MongoClient(settings.MONGO_URI, tlsCAFile=certifi.where())
db = client.fixi_db

# Collections
users_collection = db.users
messages_collection = db.messages
leads_collection = db.leads
slots_collection = db.slots
settings_collection = db.settings

def check_db_connection():
    try:
        client.admin.command('ping')
        return True
    except Exception:
        return False