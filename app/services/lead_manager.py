from datetime import datetime, timezone
from bson import ObjectId
from app.core.database import leads_collection, messages_collection
from app.core.logger import logger
import re

class LeadManager:
    async def create_lead(self, deal_string: str, chat_id: str) -> dict:
        """
        Parses [DEAL: time|city|address|issue] and saves to DB.
        Note: The prompt in AIEngine asks for [DEAL: Time | Full Address | Issue Summary]
        """
        try:
            # Remove brackets and split
            content = deal_string.replace("[DEAL:", "").replace("]", "").strip()
            parts = [p.strip() for p in content.split("|")]
            
            # Flexible parsing depending on how many parts returned
            if len(parts) >= 3:
                time_pref = parts[0]
                full_address = parts[1]
                issue = parts[2]
            else:
                # Fallback
                time_pref = "Not specified"
                full_address = parts[0] if len(parts) > 0 else "Unknown"
                issue = parts[1] if len(parts) > 1 else "Unknown"

            lead_doc = {
                "chat_id": chat_id,
                "status": "new",
                "time_preference": time_pref,
                "address": full_address,
                "issue": issue,
                "created_at": datetime.now(timezone.utc),
                "history": [] 
            }
            
            result = await leads_collection.insert_one(lead_doc)
            lead_doc["_id"] = result.inserted_id
            logger.info(f"Lead created: {result.inserted_id}")
            return lead_doc
            
        except Exception as e:
            logger.error(f"Failed to create lead from string '{deal_string}': {e}")
            return None

    async def log_message(self, chat_id: str, role: str, text: str):
        msg_doc = {
            "chat_id": chat_id,
            "role": role,
            "text": text,
            "timestamp": datetime.now(timezone.utc)
        }
        await messages_collection.insert_one(msg_doc)

    async def get_chat_history(self, chat_id: str, limit: int = 20) -> list:
        cursor = messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit)
        msgs = await cursor.to_list(length=limit)
        formatted = []
        for m in msgs:
            formatted.append({"role": "user" if m["role"] == "user" else "model", "parts": [m["text"]]})
        return formatted

    async def get_lead_by_id(self, lead_id: str):
        try:
            return await leads_collection.find_one({"_id": ObjectId(lead_id)})
        except:
            return None

    async def update_lead_status(self, lead_id: str, status: str, pro_id: str = None):
        update_fields = {"status": status}
        if pro_id:
            update_fields["pro_id"] = pro_id
        
        await leads_collection.update_one(
            {"_id": ObjectId(lead_id)},
            {"$set": update_fields}
        )
