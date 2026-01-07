from datetime import datetime, timezone
from bson import ObjectId
from app.core.database import leads_collection, messages_collection
from app.core.logger import logger
from app.core.constants import LeadStatus
from app.core.config import settings
from app.services.context_manager_service import ContextManager

class LeadManager:
    async def create_lead(self, deal_string: str, chat_id: str, pro_id: ObjectId = None) -> dict:
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
                appointment_time = parts[0]
                full_address = parts[1]
                issue_type = parts[2]
            else:
                # Fallback
                appointment_time = "Not specified"
                full_address = parts[0] if len(parts) > 0 else "Unknown"
                issue_type = parts[1] if len(parts) > 1 else "Unknown"

            return await self.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=issue_type,
                full_address=full_address,
                appointment_time=appointment_time,
                status=LeadStatus.NEW,
                pro_id=pro_id
            )
            
        except Exception as e:
            logger.error(f"Failed to create lead from string '{deal_string}': {e}")
            return None

    async def create_lead_from_dict(self, chat_id: str, issue_type: str, full_address: str, appointment_time: str = "Pending", status: str = LeadStatus.NEW, pro_id: ObjectId = None) -> dict:
        """
        Creates a lead document directly from parameters.
        """
        try:
            lead_doc = {
                "chat_id": chat_id,
                "status": status,
                "appointment_time": appointment_time,
                "full_address": full_address,
                "issue_type": issue_type,
                "created_at": datetime.now(timezone.utc),
                "history": [],
                "pro_id": pro_id
            }
            
            result = await leads_collection.insert_one(lead_doc)
            lead_doc["_id"] = result.inserted_id
            logger.info(f"Lead created/inserted: {result.inserted_id} (Status: {status})")
            return lead_doc
        except Exception as e:
             logger.error(f"Error creating lead dict: {e}")
             return None

    async def log_message(self, chat_id: str, role: str, text: str):
        # 1. MongoDB Insert (Safety)
        msg_doc = {
            "chat_id": chat_id,
            "role": role,
            "text": text,
            "timestamp": datetime.now(timezone.utc)
        }
        await messages_collection.insert_one(msg_doc)
        
        # 2. Redis Cache Update (Performance)
        await ContextManager.update_history(chat_id, role, text)

    async def get_chat_history(self, chat_id: str, limit: int = settings.MAX_CHAT_HISTORY) -> list:
        # 1. Try fetching from ContextManager (Redis)
        cached_history = await ContextManager.get_history(chat_id)
        
        if cached_history is not None:
            # If cache hit, return it (slicing if needed to respect limit)
            # Assuming cache stores full relevant history or we manage size elsewhere.
            # If limit is smaller than cached history, we take the last 'limit' items.
            if len(cached_history) > limit:
                return cached_history[-limit:]
            return cached_history

        # 2. If empty/None, fetch from MongoDB
        cursor = messages_collection.find({"chat_id": chat_id}).sort("timestamp", 1).limit(limit)
        msgs = await cursor.to_list(length=limit)
        
        formatted = []
        for m in msgs:
            formatted.append({"role": "user" if m["role"] == "user" else "model", "parts": [m["text"]]})
        
        # 3. Save to ContextManager
        await ContextManager.set_history(chat_id, formatted)
        
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
        logger.info(f"Lead {lead_id} updated: Status -> {status}, Pro -> {pro_id or 'Unchanged'}")
