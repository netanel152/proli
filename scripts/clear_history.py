from app.core.database import messages_collection, leads_collection
messages_collection.delete_many({})
print("Chat History Cleaned!")

leads_collection.delete_many({}) 
print("✅ Leads Cleaned!")