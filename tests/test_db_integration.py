import pytest
from app.services.lead_manager_service import LeadManager
from app.services.workflow_service import handle_pro_text_command, determine_best_pro, whatsapp
from bson import ObjectId
from datetime import datetime, timedelta, timezone

# Apply markers to all tests in this file
pytestmark = [pytest.mark.asyncio, pytest.mark.integration]

async def test_create_lead_persistence(integration_db):
    """
    Verify that calling create_lead with a [DEAL: ...] string correctly parses 
    the data and actually saves a document in the leads collection.
    """
    lm = LeadManager()
    chat_id = "972501234567@c.us"
    # Format: [DEAL: Time | Address | Issue]
    deal_string = "[DEAL: Tomorrow 10:00 AM | Rothschild Blvd 10, Tel Aviv | Broken Pipe]"
    
    # Act
    lead = await lm.create_lead(deal_string, chat_id)
    
    # Assert Return Value
    assert lead is not None
    assert isinstance(lead["_id"], ObjectId)
    assert lead["chat_id"] == chat_id
    assert lead["full_address"] == "Rothschild Blvd 10, Tel Aviv"
    assert lead["issue_type"] == "Broken Pipe"
    assert lead["appointment_time"] == "Tomorrow 10:00 AM"
    assert lead["status"] == "new"
    
    # Assert DB Persistence
    # Use integration_db fixture to query the real database directly
    db_lead = await integration_db.leads.find_one({"_id": lead["_id"]})
    
    assert db_lead is not None
    assert db_lead["chat_id"] == chat_id
    assert db_lead["full_address"] == "Rothschild Blvd 10, Tel Aviv"
    assert isinstance(db_lead["created_at"], datetime)

async def test_update_status_flow(integration_db):
    """
    Create a lead, update its status from 'new' to 'booked' using 
    update_lead_status, and verify the change in the DB.
    """
    lm = LeadManager()
    chat_id = "status_flow_user@c.us"
    deal_string = "[DEAL: Now | Test St | Test Issue]"
    
    # 1. Create Lead
    lead = await lm.create_lead(deal_string, chat_id)
    lead_id = str(lead["_id"])
    
    # Verify Initial Status
    assert lead["status"] == "new"
    
    # 2. Update Status
    pro_id = ObjectId()
    await lm.update_lead_status(lead_id, "booked", pro_id=pro_id)
    
    # 3. Verify DB Update
    updated_lead = await integration_db.leads.find_one({"_id": lead["_id"]})
    assert updated_lead["status"] == "booked"
    assert updated_lead["pro_id"] == pro_id

async def test_chat_history_integrity(integration_db):
    """
    Log multiple messages and verify chronological order and format.
    """
    lm = LeadManager()
    chat_id = "history_user@c.us"
    
    messages_to_log = [
        ("user", "Hello Fixi"),
        ("model", "Hello! How can I help?"),
        ("user", "I need a plumber"),
        ("model", "Where are you located?")
    ]
    
    # 1. Log Messages
    for role, text in messages_to_log:
        await lm.log_message(chat_id, role, text)
    
    # 2. Verify Raw DB Storage
    stored_msgs = await integration_db.messages.find({"chat_id": chat_id}).sort("timestamp", 1).to_list(None)
    assert len(stored_msgs) == 4
    assert stored_msgs[0]["text"] == "Hello Fixi"
    assert stored_msgs[3]["text"] == "Where are you located?"
    
    # 3. Verify get_chat_history (Service Layer)
    history = await lm.get_chat_history(chat_id)
    
    assert len(history) == 4
    # Check format required by Gemini (role, parts)
    assert history[0] == {"role": "user", "parts": ["Hello Fixi"]}
    assert history[1] == {"role": "model", "parts": ["Hello! How can I help?"]}
    assert history[2] == {"role": "user", "parts": ["I need a plumber"]}
    assert history[3] == {"role": "model", "parts": ["Where are you located?"]}

async def test_pro_lifecycle_text_commands(integration_db):
    """
    Tests the full text-command lifecycle for a Pro:
    1. Pro accepts a pending job ('אשר').
    2. Pro completes the job ('סיימתי').
    """
    # 1. Setup Pro
    pro_id = ObjectId()
    pro_phone = "972500000000"
    pro_chat_id = f"{pro_phone}@c.us"
    await integration_db.users.insert_one({
        "_id": pro_id,
        "phone_number": pro_phone,
        "business_name": "Test Pro",
        "is_active": True
    })

    # 2. Setup Pending Lead (assigned to Pro but status 'new')
    lead_id = ObjectId()
    customer_chat_id = "972509999999@c.us"
    await integration_db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_id,
        "status": "new",
        "chat_id": customer_chat_id,
        "created_at": datetime.now(timezone.utc)
    })

    # 3. Pro Sends "אשר" (Approve)
    response_text = await handle_pro_text_command(pro_chat_id, "אשר")
    
    # Verify Response and DB Update
    assert response_text is not None, "Pro text command returned None (Pro not found?)"
    assert "העבודה אושרה" in response_text
    
    # Verify Customer was notified via the patched whatsapp mock
    # Access the mock dynamically from the module to ensure we get the patched version
    from app.services import workflow_service
    workflow_service.whatsapp.send_message.assert_called()
    
    updated_lead = await integration_db.leads.find_one({"_id": lead_id})
    assert updated_lead["status"] == "booked"

    # Reset mock for next step
    workflow_service.whatsapp.send_message.reset_mock()

    # 4. Pro Sends "סיימתי" (Finish)
    response_text_finish = await handle_pro_text_command(pro_chat_id, "סיימתי")
    
    # Verify Response and DB Update
    assert response_text_finish is not None
    assert "העבודה הסתיימה" in response_text_finish
    
    final_lead = await integration_db.leads.find_one({"_id": lead_id})
    assert final_lead["status"] == "completed"
    assert final_lead["completed_at"] is not None
    assert final_lead["waiting_for_rating"] is True

async def test_pro_assignment_logic_db(integration_db):
    """
    Verifies that the database correctly finds the best pro based on:
    - Active status
    - Location match
    - Rating (High to Low)
    - Load Balancing (<3 active jobs)
    """
    # Create Geo Index
    await integration_db.users.create_index([("location", "2dsphere")])

    # Setup 3 Pros
    # Tel Aviv Coords: [34.7818, 32.0853]
    pro_good = {
        "business_name": "Pro Good",
        "is_active": True,
        "service_areas": ["Tel Aviv"],
        "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
        "social_proof": {"rating": 5.0},
        "phone_number": "972501111111",
        "role": "professional"
    }
    pro_avg = {
        "business_name": "Pro Avg",
        "is_active": True,
        "service_areas": ["Tel Aviv"],
        "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
        "social_proof": {"rating": 3.0},
        "phone_number": "972502222222",
        "role": "professional"
    }
    pro_busy = {
        "business_name": "Pro Busy",
        "is_active": True,
        "service_areas": ["Tel Aviv"],
        "location": {"type": "Point", "coordinates": [34.7818, 32.0853]},
        "social_proof": {"rating": 5.0},
        "phone_number": "972503333333",
        "role": "professional"
    }    
    # Insert Pros
    res_good = await integration_db.users.insert_one(pro_good)
    res_avg = await integration_db.users.insert_one(pro_avg)
    res_busy = await integration_db.users.insert_one(pro_busy)
    
    # Overload "Pro Busy" with 3 active jobs
    for _ in range(3):
        await integration_db.leads.insert_one({
            "pro_id": res_busy.inserted_id,
            "status": "booked"
        })

    # Test Logic
    selected_pro = await determine_best_pro(issue_type="Plumbing", location="Tel Aviv")
    
    # Should pick "Pro Good" (5.0 rating, not busy)
    # "Pro Busy" is skipped due to load.
    # "Pro Avg" is skipped due to lower rating (sort order).
    
    assert selected_pro is not None
    assert selected_pro["_id"] == res_good.inserted_id
    assert selected_pro["business_name"] == "Pro Good"

async def test_stale_lead_monitoring_queries(integration_db):
    """
    Simulates the DB queries used by the scheduler to find 'stale' jobs.
    """
    now_utc = datetime.now(timezone.utc)
    
    # Insert Leads with different ages
    # Lead 1: 5 hours old (Tier 1: 4-6h)
    id_t1 = ObjectId()
    await integration_db.leads.insert_one({
        "_id": id_t1,
        "status": "booked",
        "created_at": now_utc - timedelta(hours=5)
    })
    
    # Lead 2: 20 hours old (Tier 2: 6-24h)
    id_t2 = ObjectId()
    await integration_db.leads.insert_one({
        "_id": id_t2,
        "status": "booked",
        "created_at": now_utc - timedelta(hours=20)
    })
    
    # Lead 3: 2 hours old (Too new, ignored)
    id_new = ObjectId()
    await integration_db.leads.insert_one({
        "_id": id_new,
        "status": "booked",
        "created_at": now_utc - timedelta(hours=2)
    })

    # -- Verify Tier 1 Query --
    t1_start = now_utc - timedelta(hours=6)
    t1_end = now_utc - timedelta(hours=4)
    
    t1_results = await integration_db.leads.find({
        "status": "booked",
        "created_at": {"$gte": t1_start, "$lt": t1_end}
    }).to_list(None)
    
    assert len(t1_results) == 1
    assert t1_results[0]["_id"] == id_t1

    # -- Verify Tier 2 Query --
    t2_start = now_utc - timedelta(hours=24)
    t2_end = now_utc - timedelta(hours=6)
    
    t2_results = await integration_db.leads.find({
        "status": "booked",
        "created_at": {"$gte": t2_start, "$lt": t2_end}
    }).to_list(None)
    
    assert len(t2_results) == 1
    assert t2_results[0]["_id"] == id_t2
