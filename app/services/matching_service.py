from app.core.database import users_collection, leads_collection
from app.core.logger import logger
from app.core.constants import LeadStatus, WorkerConstants

async def determine_best_pro(issue_type: str = None, location: str = None) -> dict:
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
        
        # 2. Location Filtering (Database Level Optimization)
        # We want to find pros where 'service_areas' contains the user's location.
        # Simple approach: If user says "Tel Aviv", we look for pros with "Tel Aviv" in their areas.
        # We use a regex for case-insensitive partial matching.
        if location:
            # Escaping regex characters in location is important if we want to be safe, 
            # but for city names it's usually fine.
            query["service_areas"] = {"$regex": location, "$options": "i"}
            
        cursor = users_collection.find(query)
        matching_pros = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
        
        # Fallback: If no pros match the specific location, try finding "All" or similar wide-net pros?
        # Or if the user's location was too specific (e.g. "Dizengoff St, Tel Aviv") and Pro just has "Tel Aviv".
        # The DB regex above does: ProArea LIKE %UserLoc%. 
        # So if Pro has "Tel Aviv" and User says "Tel Aviv", it matches.
        # If Pro has "Tel Aviv" and User says "Dizengoff, Tel Aviv", the regex "Dizengoff, Tel Aviv" won't match "Tel Aviv".
        # So we might need the Python fallback for the "Reverse" match (User string contains Pro area) if the DB query yields nothing.
        
        if not matching_pros and location:
             # Retry with looser query (just active pros) and filter in Python for the "Reverse" match
             # This handles: User="Dizengoff Tel Aviv", Pro="Tel Aviv"
             logger.info(f"No direct location match for '{location}', trying reverse match...")
             all_pros_cursor = users_collection.find({"is_active": True})
             all_pros = await all_pros_cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
             
             for pro in all_pros:
                 areas = pro.get("service_areas", [])
                 # Check if any area is inside the location string
                 if any(area in location for area in areas):
                     matching_pros.append(pro)
        
        if not matching_pros:
            # Final Fallback: All active pros? 
            # Original logic did: "if not matching_pros: matching_pros = pros" (all active)
            # We preserve this behavior for MVP stability.
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
