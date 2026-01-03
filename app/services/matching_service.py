from app.core.database import users_collection, leads_collection, slots_collection
from app.core.logger import logger
from app.core.constants import LeadStatus, WorkerConstants
from datetime import datetime, timedelta, timezone
from bson import ObjectId

async def determine_best_pro(issue_type: str = None, location: str = None, excluded_pro_ids: list = None) -> dict:
    """
    Intelligent Routing Engine:
    1. Active Status
    2. Location Match (if applicable)
    3. Rating (High to Low)
    4. Availability (Load Balancing)
    """
    try:
        # 1. Build Query
        query = {"is_active": True}
        
        if excluded_pro_ids:
            # Filter out IDs provided in the exclusion list
            query["_id"] = {"$nin": [ObjectId(pid) for pid in excluded_pro_ids]}
        
        # 2. Location Filtering (Database Level Optimization)
        if location:
            query["service_areas"] = {"$regex": location, "$options": "i"}
            
        cursor = users_collection.find(query)
        matching_pros = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
        
        # Fallback: Reverse match
        if not matching_pros and location:
             logger.info(f"No direct location match for '{location}', trying reverse match...")
             all_pros_cursor = users_collection.find({"is_active": True})
             all_pros = await all_pros_cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
             
             for pro in all_pros:
                 areas = pro.get("service_areas", [])
                 if any(area in location for area in areas):
                     matching_pros.append(pro)
        
        if not matching_pros:
            # Final Fallback: All active pros
            matching_pros = await users_collection.find({"is_active": True}).to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        # 3. Sort by Rating (Descending)
        def get_rating(p):
            return p.get("social_proof", {}).get("rating", 0)
        
        matching_pros.sort(key=get_rating, reverse=True)

        # 4. Load Balancing
        selected_pro = None
        
        for pro in matching_pros:
            current_load = await leads_collection.count_documents({
                "pro_id": pro["_id"],
                "status": LeadStatus.BOOKED
            })
            
            if current_load < WorkerConstants.MAX_PRO_LOAD:
                selected_pro = pro
                logger.info(f"Routing Decision: Selected Pro '{pro.get('business_name')}' (Load: {current_load}/{WorkerConstants.MAX_PRO_LOAD}, Rating: {get_rating(pro)})")
                break
        
        if not selected_pro and matching_pros:
            selected_pro = matching_pros[0]
            logger.warning(f"Routing Decision: All pros busy. Fallback to highest rated '{selected_pro.get('business_name')}' despite load.")

        return selected_pro

    except Exception as e:
        logger.error(f"Error in determine_best_pro: {e}")
        return None

async def book_slot_for_lead(pro_id: str, lead_created_at: datetime) -> bool:
    """
    Attempts to book a slot for the pro around the lead creation time.
    """
    try:
        if not lead_created_at:
            return False

        # Ensure UTC
        if lead_created_at.tzinfo is None:
            lead_created_at = lead_created_at.replace(tzinfo=timezone.utc)

        # 1. Calculate Estimated Slot Time (Round up to next hour)
        # e.g., 14:15 -> 15:00
        estimated_time = lead_created_at.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

        # 2. Define Search Window (+/- 2 hours)
        start_window = estimated_time - timedelta(hours=2)
        end_window = estimated_time + timedelta(hours=2)

        # 3. Find and Book Slot (Atomic)
        slot = await slots_collection.find_one_and_update(
            {
                "pro_id": ObjectId(pro_id) if isinstance(pro_id, str) else pro_id,
                "is_taken": False,
                "start_time": {"$gte": start_window, "$lte": end_window}
            },
            {"$set": {"is_taken": True}},
            sort=[("start_time", 1)]
        )

        if slot:
            logger.info(f"📅 Booked slot {slot['_id']} for Pro {pro_id} at {slot['start_time']}")
            return True
        else:
            logger.info(f"⚠️ No available slot found for Pro {pro_id} near {estimated_time}")
            return False

    except Exception as e:
        logger.error(f"Error in book_slot_for_lead: {e}")
        return False