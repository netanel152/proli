from app.core.database import users_collection, leads_collection, slots_collection
from app.core.logger import logger
from app.core.constants import LeadStatus, WorkerConstants, ISRAEL_CITIES_COORDS
from datetime import datetime, timedelta, timezone
from bson import ObjectId

def get_coordinates(city_name: str):
    """
    Returns [Longitude, Latitude] for a given city name if found in the static dictionary.
    Normalizes input to lowercase.
    """
    if not city_name:
        return None
    return ISRAEL_CITIES_COORDS.get(city_name.lower().strip())

async def determine_best_pro(issue_type: str = None, location: str = None, excluded_pro_ids: list = None) -> dict:
    """
    Intelligent Routing Engine with Geo-Spatial Support:
    1. Active Status
    2. Location Match (Geo-Spatial $near or Fallback Regex)
    3. Load Balancing (Check active leads)
    4. Rating (High to Low)
    """
    try:
        # 1. Base Query
        query = {
            "is_active": True,
            "role": "professional" # Ensure we only get pros
        }
        
        if excluded_pro_ids:
            query["_id"] = {"$nin": [ObjectId(pid) for pid in excluded_pro_ids]}
        
        # 2. Location Filtering
        coordinates = get_coordinates(location)
        geo_enabled = False

        if coordinates:
            # Use MongoDB $near operator (Legacy Coordinate Pairs)
            # Finds pros within 10km (10000 meters)
            query["location"] = {
                "$near": {
                    "$geometry": {
                        "type": "Point",
                        "coordinates": coordinates
                    },
                    "$maxDistance": 10000 
                }
            }
            geo_enabled = True
            logger.info(f"📍 Geo-Spatial query enabled for '{location}' at {coordinates}")
        elif location:
            # Fallback to Regex on service_areas
            query["service_areas"] = {"$regex": location, "$options": "i"}
            logger.info(f"🔍 Text-based location query for '{location}'")
        else:
            logger.info("⚠️ No location provided for routing.")

        # Execute Query
        # Note: $near sorts by distance automatically.
        cursor = users_collection.find(query)
        matching_pros = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
        
        # Fallback: Reverse match (only if using text-based search and no results)
        if not matching_pros and location and not geo_enabled:
             logger.info(f"No direct location match for '{location}', trying reverse match...")
             # Warning: This is expensive, use sparingly
             all_pros_cursor = users_collection.find({"is_active": True, "role": "professional"})
             all_pros = await all_pros_cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)
             
             for pro in all_pros:
                 areas = pro.get("service_areas", [])
                 # Check if any of the pro's areas are substrings of the location or vice versa
                 if any(area.lower() in location.lower() for area in areas):
                     matching_pros.append(pro)
        
        if not matching_pros:
             # Final Fallback: All active pros (Emergency Catch-all)
            logger.warning("No pros found matching location. Fallback to all active pros.")
            fallback_query = {"is_active": True, "role": "professional"}
            if excluded_pro_ids:
                fallback_query["_id"] = {"$nin": [ObjectId(pid) for pid in excluded_pro_ids]}
            
            matching_pros = await users_collection.find(fallback_query).to_list(length=WorkerConstants.DB_QUERY_LIMIT)

        # 3. Load Balancing & Sorting
        # If $near was used, they are sorted by distance. 
        # If not, we should sort by rating.
        # However, we must filter by Load first.

        candidates = []
        
        for pro in matching_pros:
            # Check Active Load
            current_load = await leads_collection.count_documents({
                "pro_id": pro["_id"],
                "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]} # Check all active states
            })
            
            # Helper to get rating safely
            rating = pro.get("social_proof", {}).get("rating", 0)

            if current_load < WorkerConstants.MAX_PRO_LOAD:
                candidates.append({
                    "pro": pro,
                    "load": current_load,
                    "rating": rating
                })
            else:
                logger.debug(f"Skipping Pro '{pro.get('business_name')}' - Overloaded ({current_load} active leads)")

        if not candidates:
             logger.warning("All matching pros are overloaded. Returning highest rated from original matches.")
             # Fallback to just rating if everyone is busy
             def get_rating_simple(p):
                return p.get("social_proof", {}).get("rating", 0)
             matching_pros.sort(key=get_rating_simple, reverse=True)
             return matching_pros[0] if matching_pros else None

        # 4. Final Selection Logic
        # If Geo-Spatial was used, candidates are already sorted by distance (roughly).
        # We might want to pick the highest rated among the top closest, or just the closest.
        # Let's prioritize:
        # - If Geo used: Keep order (Distance), but maybe boost if rating is significantly higher? 
        # For now, let's keep it simple:
        # If Geo used -> Return closest qualified candidate.
        # If Text used -> Sort by Rating.
        
        if geo_enabled:
            # Already sorted by distance by MongoDB
            selected = candidates[0]
            logger.info(f"✅ Geo-Routing: Selected '{selected['pro'].get('business_name')}' (Dist: Closest, Load: {selected['load']})")
            return selected['pro']
        else:
            # Sort by Rating Descending
            candidates.sort(key=lambda x: x["rating"], reverse=True)
            selected = candidates[0]
            logger.info(f"✅ Text-Routing: Selected '{selected['pro'].get('business_name')}' (Rating: {selected['rating']}, Load: {selected['load']})")
            return selected['pro']

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