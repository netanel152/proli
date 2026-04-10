from app.core.database import users_collection, leads_collection, slots_collection
from app.core.logger import logger
from app.core.constants import LeadStatus, WorkerConstants, ISRAEL_CITIES_COORDS
from app.services.scheduling_service import check_pro_availability
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
    Intelligent Routing Engine with Progressive Geo-Spatial Search:
    1. Active Status
    2. Location Match ($geoNear with expanding radius 10→20→30km, or Regex fallback)
    3. Load Balancing (Skip pros with >= MAX_PRO_LOAD active leads)
    4. Rating (High to Low)
    Returns None if no qualified pro is found (caller should set PENDING_ADMIN_REVIEW).
    """
    try:
        # 1. Base query filter — only fully approved, active professionals
        base_filter = {
            "is_active": True,
            "role": "professional",
            "pending_approval": {"$ne": True}
        }

        if excluded_pro_ids:
            base_filter["_id"] = {"$nin": [ObjectId(pid) for pid in excluded_pro_ids]}

        # 2. Location Filtering
        coordinates = get_coordinates(location)
        geo_enabled = False
        matching_pros = []

        if coordinates:
            geo_enabled = True
            # Progressive $geoNear — expand radius until we find candidates
            for radius in WorkerConstants.GEO_RADIUS_STEPS:
                logger.info(f"📍 Geo-Spatial search at {radius // 1000}km for '{location}' at {coordinates}")
                pipeline = [
                    {
                        "$geoNear": {
                            "near": {"type": "Point", "coordinates": coordinates},
                            "distanceField": "dist_meters",
                            "maxDistance": radius,
                            "spherical": True,
                            "query": base_filter,
                        }
                    },
                    {"$sort": {"social_proof.rating": -1}},
                    {"$limit": WorkerConstants.DB_QUERY_LIMIT},
                ]
                matching_pros = []
                async for doc in users_collection.aggregate(pipeline):
                    matching_pros.append(doc)

                if matching_pros:
                    logger.info(f"✅ Found {len(matching_pros)} pros within {radius // 1000}km")
                    break
                logger.info(f"No pros found within {radius // 1000}km, expanding radius...")

            if not matching_pros:
                logger.critical(
                    f"No professional found within {WorkerConstants.GEO_RADIUS_STEPS[-1] // 1000}km "
                    f"for '{location}'. Lead requires admin review."
                )
                return None
        elif location:
            # Fallback to Regex on service_areas
            query = {**base_filter, "service_areas": {"$regex": location, "$options": "i"}}
            logger.info(f"🔍 Text-based location query for '{location}'")
            cursor = users_collection.find(query)
            matching_pros = await cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

            # Reverse match (only if text-based search found nothing)
            if not matching_pros:
                logger.info(f"No direct location match for '{location}', trying reverse match...")
                all_pros_cursor = users_collection.find({"is_active": True, "role": "professional"})
                all_pros = await all_pros_cursor.to_list(length=WorkerConstants.DB_QUERY_LIMIT)

                for pro in all_pros:
                    areas = pro.get("service_areas", [])
                    if any(area.lower() in location.lower() for area in areas):
                        matching_pros.append(pro)
        else:
            logger.info("⚠️ No location provided for routing.")

        if not matching_pros:
            logger.warning(f"No pros found for location '{location}'. Lead requires admin review.")
            return None

        # 3. Load Balancing — batch-fetch active lead counts
        pro_ids = [pro["_id"] for pro in matching_pros]
        load_pipeline = [
            {"$match": {
                "pro_id": {"$in": pro_ids},
                "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]}
            }},
            {"$group": {"_id": "$pro_id", "count": {"$sum": 1}}}
        ]
        load_counts = {}
        async for doc in leads_collection.aggregate(load_pipeline):
            load_counts[doc["_id"]] = doc["count"]

        candidates = []

        for pro in matching_pros:
            current_load = load_counts.get(pro["_id"], 0)
            rating = pro.get("social_proof", {}).get("rating", 0)
            no_shows = pro.get("no_show_count", 0)

            if current_load < WorkerConstants.MAX_PRO_LOAD:
                has_slots = True
                try:
                    has_slots = await check_pro_availability(pro["_id"])
                except Exception:
                    pass

                candidates.append({
                    "pro": pro,
                    "load": current_load,
                    "rating": rating,
                    "has_slots": has_slots,
                    "no_shows": no_shows,
                })
            else:
                logger.debug(f"Skipping Pro '{pro.get('business_name')}' - Overloaded ({current_load} active leads)")

        if not candidates:
            logger.warning("All matching pros are overloaded. No qualified pro available.")
            return None

        # 4. Final Selection — sort by composite score (rating + slots - no-shows)
        def candidate_score(c):
            slot_bonus = 10 if c.get("has_slots", True) else 0
            no_show_penalty = c.get("no_shows", 0) * 0.5
            return slot_bonus + c["rating"] - no_show_penalty

        candidates.sort(key=candidate_score, reverse=True)
        selected = candidates[0]
        route_type = "Geo" if geo_enabled else "Text"
        logger.info(
            f"✅ {route_type}-Routing: Selected '{selected['pro'].get('business_name')}' "
            f"(Rating: {selected['rating']}, Load: {selected['load']})"
        )
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