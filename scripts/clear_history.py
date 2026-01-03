import sys
import os
from dotenv import load_dotenv
import asyncio

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import messages_collection, leads_collection, reviews_collection

async def clear_history():
    print("⚠️  WARNING: This will delete ALL chat history, leads, and reviews.")
    print("   Your Professionals (Users) and Schedules (Slots) will remain intact.")
    
    confirm = input("Are you sure? (y/n): ")
    if confirm.lower() != 'y':
        print("❌ Aborted.")
        return

    # Delete Messages
    msg_res = await messages_collection.delete_many({})
    print(f"🗑️  Deleted {msg_res.deleted_count} messages.")

    # Delete Leads
    lead_res = await leads_collection.delete_many({}) 
    print(f"🗑️  Deleted {lead_res.deleted_count} leads.")

    # Delete Reviews
    review_res = await reviews_collection.delete_many({})
    print(f"🗑️  Deleted {review_res.deleted_count} reviews.")
    
    print("✅ Chat, Lead & Review History Cleaned!")

if __name__ == "__main__":
    if os.name == 'nt':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(clear_history())
