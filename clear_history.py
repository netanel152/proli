from app.core.database import messages_collection
messages_collection.delete_many({})
print("🧹 History CLEANED!")