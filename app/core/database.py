from pymongo import MongoClient
from app.core.config import settings

# Create a connection to MongoDB
client = MongoClient(settings.MONGO_URI)
db = client.fixi_db

# Collections
users_collection = db.users
messages_collection = db.messages
leads_collection = db.leads
slots_collection = db.slots

def check_db_connection():
    try:
        client.admin.command('ping')
        return True
    except Exception:
        return False