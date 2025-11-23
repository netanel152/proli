from pymongo import MongoClient
from app.core.config import settings

# Create a connection to MongoDB
client = MongoClient(settings.MONGO_URI)

# Select the database
db = client.fixi_db

# Select the collections
users_collection = db.users
messages_collection = db.messages

def check_db_connection():
    """Check if the database connection is successful"""
    try:
        client.admin.command('ping')
        return True
    except Exception:
        return False