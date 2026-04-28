"""
Tests for pro_flow.py: all professional text commands.
Covers: approve, reject, finish, active jobs, history, stats, reviews.
"""
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from bson import ObjectId
from datetime import datetime, timedelta, timezone
from app.core.constants import LeadStatus, UserStates, WorkerConstants
from app.core.messages import Messages
from app.services.pro_flow import handle_pro_text_command, _handle_search
import app.services.pro_flow

PRO_ID = ObjectId()
PRO_PHONE = "972500000000"


@pytest_asyncio.fixture
async def pro_setup(mock_db):
    """Create a pro and return (pro_doc, mock_db)."""
    pro_doc = {
        "_id": PRO_ID,
        "phone_number": PRO_PHONE,
        "role": "professional",
        "business_name": "יוסי אינסטלציה",
        "is_active": True,
        "social_proof": {"rating": 4.5, "review_count": 3},
        "created_at": datetime(2025, 1, 1, tzinfo=timezone.utc),
    }
    # Avoid duplicate key on re-run within same module scope
    existing = await mock_db.users.find_one({"_id": PRO_ID})
    if not existing:
        await mock_db.users.insert_one(pro_doc)
    return pro_doc, mock_db


@pytest.fixture
def mock_wa():
    wa = MagicMock()
    wa.send_message = AsyncMock()
    return wa


@pytest.fixture
def mock_lm():
    lm = MagicMock()
    lm.update_lead_status = AsyncMock()
    lm.create_lead = AsyncMock()
    return lm


# --- Approve ---

@pytest.mark.asyncio
async def test_approve_with_pending_lead(pro_setup, mock_wa, mock_lm, monkeypatch):
    pro_doc, db = pro_setup

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "תל אביב, הרצל 10",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    # Mock book_slot_for_lead
    import app.services.pro_flow
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=True))

    result = await handle_pro_text_command("972500000000@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_lm.update_lead_status.assert_called_once()
    # Customer should receive PRO_FOUND message
    mock_wa.send_message.assert_called_once()
    customer_msg = mock_wa.send_message.call_args.args[1]
    assert "יוסי אינסטלציה" in customer_msg


@pytest.mark.asyncio
async def test_approve_no_pending(mock_db, mock_wa, mock_lm):
    """Pro with no NEW leads -> NO_PENDING_APPROVE."""
    pro_id = ObjectId()
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "_id": pro_id, "phone_number": "972502222222",
            "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "אשר", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_APPROVALS


@pytest.mark.asyncio
async def test_approve_with_number_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """'1' is an alias for approve."""
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us", "issue_type": "חשמל",
        "full_address": "חיפה", "appointment_time": "14:00",
        "created_at": datetime.now(timezone.utc),
    })
    import app.services.pro_flow
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=False))

    result = await handle_pro_text_command("972500000000@c.us", "1", mock_wa, mock_lm)
    assert Messages.Pro.APPROVE_SUCCESS in result


# --- Reject ---

@pytest.mark.asyncio
async def test_reject_lead(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us", "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_called_once()


@pytest.mark.asyncio
async def test_reject_no_pending(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "דחה", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_PENDING_APPROVALS


# --- Finish ---

@pytest.mark.asyncio
async def test_finish_job_single(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id, "pro_id": pro_doc["_id"],
        "status": LeadStatus.BOOKED, "chat_id": "972501111111@c.us",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "סיימתי", mock_wa, mock_lm)
    assert result == Messages.Pro.FINISH_SUCCESS

    # Lead should be completed
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.COMPLETED

@pytest.mark.asyncio
async def test_finish_multiple_jobs_selection(pro_setup, mock_wa, mock_lm, monkeypatch):
    """If multiple BOOKED leads, pro enters selection state."""
    pro_doc, db = pro_setup
    chat_id = "972500000000@c.us"
    
    await db.leads.insert_many([
        {"pro_id": pro_doc["_id"], "status": LeadStatus.BOOKED, "customer_name": "A", "created_at": datetime.now(timezone.utc)},
        {"pro_id": pro_doc["_id"], "status": LeadStatus.BOOKED, "customer_name": "B", "created_at": datetime.now(timezone.utc) - timedelta(minutes=1)},
    ])

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    mock_state.set_metadata = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(chat_id, "סיימתי", mock_wa, mock_lm)
    
    assert "איזו עבודה סיימת?" in result
    mock_state.set_state.assert_called_with(chat_id, UserStates.PRO_SELECTING_JOB_TO_FINISH)
    mock_state.set_metadata.assert_called_once()


@pytest.mark.asyncio
async def test_finish_no_booked(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "סיימתי", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_ACTIVE_JOBS


# --- Active Jobs ---

@pytest.mark.asyncio
async def test_active_jobs_list(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.BOOKED,
        "issue_type": "נזילה", "full_address": "תל אביב",
        "appointment_time": "10:00", "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "עבודות", mock_wa, mock_lm)
    assert "עבודות פעילות" in result
    assert "נזילה" in result


@pytest.mark.asyncio
async def test_active_jobs_empty(mock_db, mock_wa, mock_lm):
    """Pro with no active leads."""
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    # Use the pro that has no leads assigned to it
    result = await handle_pro_text_command("972502222222@c.us", "עבודות", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_ACTIVE_JOBS_LIST


# --- History ---

@pytest.mark.asyncio
async def test_history(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "issue_type": "חשמל", "full_address": "חיפה",
        "completed_at": datetime(2026, 3, 15, tzinfo=timezone.utc),
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "היסטוריה", mock_wa, mock_lm)
    assert "עבודות אחרונות" in result
    assert "חשמל" in result


@pytest.mark.asyncio
async def test_history_empty(mock_db, mock_wa, mock_lm):
    existing = await mock_db.users.find_one({"phone_number": "972502222222"})
    if not existing:
        await mock_db.users.insert_one({
            "phone_number": "972502222222", "role": "professional", "is_active": True,
        })
    result = await handle_pro_text_command("972502222222@c.us", "היסטוריה", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_HISTORY


# --- Stats ---

@pytest.mark.asyncio
async def test_stats(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command("972500000000@c.us", "דוח", mock_wa, mock_lm)
    assert "סטטיסטיקות" in result
    assert "4.5" in result  # rating


# --- Reviews ---

@pytest.mark.asyncio
async def test_reviews_with_data(pro_setup, mock_wa, mock_lm):
    pro_doc, db = pro_setup
    # The _handle_reviews function matches reviews to leads via key = chat_id + rating
    chat_id = "972501234567@c.us"
    await db.leads.insert_one({
        "pro_id": pro_doc["_id"], "status": LeadStatus.COMPLETED,
        "rating_given": 5, "chat_id": chat_id,
        "completed_at": datetime.now(timezone.utc),
        "created_at": datetime.now(timezone.utc),
    })
    await db.reviews.insert_one({
        "pro_id": pro_doc["_id"],
        "customer_chat_id": chat_id,
        "rating": 5,
        "comment": "שירות מצוין",
    })

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "ביקורות", mock_wa, mock_lm)
    assert result is not None
    assert "דירוגים" in result or "ביקורות" in result.lower()
    assert "5" in result  # Rating value shown


@pytest.mark.asyncio
async def test_reviews_empty(pro_setup, mock_wa, mock_lm):
    # Pro has review_count=3 from fixture but let's set to 0
    pro_doc, db = pro_setup
    await db.users.update_one(
        {"_id": pro_doc["_id"]},
        {"$set": {"social_proof.review_count": 0}}
    )

    result = await handle_pro_text_command("972500000000@c.us", "ביקורות", mock_wa, mock_lm)
    assert result == Messages.Pro.NO_REVIEWS


# --- Unknown Command ---

@pytest.mark.asyncio
async def test_unknown_command_returns_dashboard(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Now returns dashboard instead of None."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command("972500000000@c.us", "שלום", mock_wa, mock_lm)
    assert "סטטוס: זמין" in result
    assert "יוסי אינסטלציה" in result


@pytest.mark.asyncio
async def test_non_pro_returns_none(mock_db, mock_wa, mock_lm):
    """Non-pro phone number -> returns None."""
    result = await handle_pro_text_command("972501111111@c.us", "אשר", mock_wa, mock_lm)
    assert result is None


# --- Text-Based Pro Approval Handlers ---

@pytest.mark.asyncio
async def test_approve_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'אשר' -> lead becomes BOOKED, customer state cleared."""
    pro_doc, db = pro_setup
    monkeypatch.setattr(app.services.pro_flow, "book_slot_for_lead", AsyncMock(return_value=True))

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "רחוב הרצל 5",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "אשר", mock_wa, mock_lm)

    assert Messages.Pro.APPROVE_SUCCESS in result
    mock_lm.update_lead_status.assert_called_once()
    # Customer state should be cleared
    mock_state.clear_state.assert_called_with("972501111111@c.us")


@pytest.mark.asyncio
async def test_pause_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'השהה' -> customer state set to PAUSED_FOR_HUMAN with TTL."""
    pro_doc, db = pro_setup

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "רחוב הרצל 5",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    # Note: "השהה" is now in BOT_PAUSE_COMMANDS
    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "השהה", mock_wa, mock_lm)

    assert result == Messages.Pro.PAUSE_ACK
    # Customer state should be set with TTL
    mock_state.set_state.assert_called_with(
        "972501111111@c.us",
        UserStates.PAUSED_FOR_HUMAN,
        ttl=WorkerConstants.PAUSE_TTL_SECONDS,
    )
    # Customer notified
    mock_wa.send_message.assert_called_with("972501111111@c.us", Messages.Customer.BOT_PAUSED_BY_PRO)



@pytest.mark.asyncio
async def test_reject_via_text_command(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro types 'דחה' -> lead rejected, customer state cleared."""
    pro_doc, db = pro_setup

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.NEW,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "רחוב הרצל 5",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "דחה", mock_wa, mock_lm)

    assert result == Messages.Pro.REJECT_SUCCESS
    mock_lm.update_lead_status.assert_called_once()
    mock_state.clear_state.assert_called_with("972501111111@c.us")


@pytest.mark.asyncio
async def test_resume_clears_pause(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Pro sends 'המשך' -> customer pause state cleared."""
    pro_doc, db = pro_setup

    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=UserStates.PAUSED_FOR_HUMAN)
    mock_state.clear_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "pro_id": pro_doc["_id"],
        "status": LeadStatus.BOOKED,
        "chat_id": "972501111111@c.us",
        "issue_type": "נזילה",
        "full_address": "רחוב הרצל 5",
        "appointment_time": "10:00",
        "created_at": datetime.now(timezone.utc),
    })

    result = await handle_pro_text_command(f"{PRO_PHONE}@c.us", "המשך", mock_wa, mock_lm)

    assert "חזר לפעולה" in result
    mock_state.clear_state.assert_called_with("972501111111@c.us")


# --- Zero-Touch Intent Detection ---

@pytest.mark.asyncio
async def test_intent_detected_prompts_switch(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Free-text service request from Pro -> sends INTENT_DETECTED message and sets AWAITING state."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    mock_state.set_state = AsyncMock()
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=True)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "המזגן שלי דולף", mock_wa, mock_lm, ai=mock_ai
    )

    # Returns empty sentinel
    assert result == ""
    # INTENT_DETECTED sent as text message
    mock_wa.send_message.assert_called_once()
    call_text = mock_wa.send_message.call_args[0][1]
    assert "השב *1*" in call_text
    # State set to AWAITING_INTENT_CONFIRMATION with 5-min TTL
    mock_state.set_state.assert_called_once_with(
        f"{PRO_PHONE}@c.us",
        UserStates.AWAITING_INTENT_CONFIRMATION,
        ttl=300,
    )


@pytest.mark.asyncio
async def test_intent_not_detected_returns_dashboard(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Classifier returns False -> function returns Dashboard."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=False)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "סתם הודעה", mock_wa, mock_lm, ai=mock_ai
    )

    assert "יוסי אינסטלציה" in result
    mock_ai.detect_service_intent.assert_called_once_with("סתם הודעה")



@pytest.mark.asyncio
async def test_known_command_skips_intent_detection(pro_setup, mock_wa, mock_lm, monkeypatch):
    """Text 'אשר' always matches APPROVE_COMMANDS -> detect_service_intent is never called."""
    mock_ai = MagicMock()
    mock_ai.detect_service_intent = AsyncMock(return_value=True)

    # Even if the result varies (depends on DB state), classifier must NOT be called
    await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "אשר", mock_wa, mock_lm, ai=mock_ai
    )

    mock_ai.detect_service_intent.assert_not_called()


@pytest.mark.asyncio
async def test_intent_detection_no_ai_returns_dashboard(pro_setup, mock_wa, mock_lm, monkeypatch):
    """When ai=None (default), unmatched text returns Dashboard."""
    mock_state = MagicMock()
    mock_state.get_state = AsyncMock(return_value=None)
    monkeypatch.setattr(app.services.pro_flow, "StateManager", mock_state)

    result = await handle_pro_text_command(
        f"{PRO_PHONE}@c.us", "שאלה כלשהי", mock_wa, mock_lm
    )
    assert "יוסי אינסטלציה" in result



# --- Proactive Search (rate-limited) ---

def _make_mock_redis():
    """Redis stub that simulates ttl / setex for _handle_search."""
    store = {}  # key -> (value, expires_at or None)

    def _now():
        return datetime.now(timezone.utc)

    async def ttl(key):
        entry = store.get(key)
        if entry is None:
            return -2
        _, expires_at = entry
        if expires_at is None:
            return -1
        remaining = int((expires_at - _now()).total_seconds())
        return remaining if remaining > 0 else -2

    async def setex(key, seconds, value):
        store[key] = (value, _now() + timedelta(seconds=seconds))

    redis = MagicMock()
    redis.ttl = AsyncMock(side_effect=ttl)
    redis.setex = AsyncMock(side_effect=setex)
    return redis, store


@pytest.mark.asyncio
async def test_search_no_stuck_leads_sets_cooldown(pro_setup, mock_wa):
    """First call with empty DB: returns NO_STUCK_LEADS and locks cool-down."""
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

    with patch("app.services.pro_flow.get_redis_client", new_callable=AsyncMock, return_value=redis):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert result == Messages.Pro.NO_STUCK_LEADS
    assert f"rate_limit:pro_search:{chat_id}" in store
    redis.setex.assert_called_once()


@pytest.mark.asyncio
async def test_search_rate_limited_sends_wait_message(pro_setup, mock_wa):
    """Second call within cool-down returns the rate-limited sentinel and sends formatted message."""
    pro_doc, _ = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()
    # Pre-seed an active cool-down with ~6 minutes remaining
    store[f"rate_limit:pro_search:{chat_id}"] = (
        "1",
        datetime.now(timezone.utc) + timedelta(seconds=360),
    )

    with patch("app.services.pro_flow.get_redis_client", new_callable=AsyncMock, return_value=redis):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert result == ""  # sentinel: handler sent message itself
    mock_wa.send_message.assert_called_once()
    sent_text = mock_wa.send_message.call_args.args[1]
    assert "6" in sent_text  # math.ceil(360 / 60) == 6
    assert "דקות" in sent_text
    # setex must NOT be refreshed when already rate-limited
    redis.setex.assert_not_called()


@pytest.mark.asyncio
async def test_search_finds_stuck_lead_and_assigns(pro_setup, mock_wa):
    """Pending-admin-review lead: assigned to pro as NEW, cool-down set."""
    pro_doc, db = pro_setup
    chat_id = f"{PRO_PHONE}@c.us"
    redis, store = _make_mock_redis()

    lead_id = ObjectId()
    await db.leads.insert_one({
        "_id": lead_id,
        "status": LeadStatus.PENDING_ADMIN_REVIEW,
        "issue_type": "נזילה",
        "city": "תל אביב",
        "created_at": datetime.now(timezone.utc) - timedelta(minutes=75),
    })

    with patch("app.services.pro_flow.get_redis_client", new_callable=AsyncMock, return_value=redis):
        result = await _handle_search(pro_doc, chat_id, mock_wa)

    assert "נזילה" in result
    assert "תל אביב" in result
    lead = await db.leads.find_one({"_id": lead_id})
    assert lead["status"] == LeadStatus.NEW
    assert lead["pro_id"] == pro_doc["_id"]
    assert f"rate_limit:pro_search:{chat_id}" in store
