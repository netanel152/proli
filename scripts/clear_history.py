import sys
import os
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.database import messages_collection, leads_collection

def clear_history():
    print("⚠️  WARNING: This will delete ALL chat history and leads.")
    print("   Your Professionals (Users) and Schedules (Slots) will remain intact.")
    
    confirm = input("Are you sure? (y/n): ")
    if confirm.lower() != 'y':
        print("❌ Aborted.")
        return

    # Delete Messages
    msg_res = messages_collection.delete_many({})
    print(f"🗑️  Deleted {msg_res.deleted_count} messages.")

    # Delete Leads
    lead_res = leads_collection.delete_many({}) 
    print(f"🗑️  Deleted {lead_res.deleted_count} leads.")
    
    print("✅ Chat & Lead History Cleaned!")

if __name__ == "__main__":
    clear_history()
