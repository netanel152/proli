from app.services.whatsapp_client_service import WhatsAppClient
from app.services.ai_engine_service import AIEngine, AIResponse
from app.services.lead_manager_service import LeadManager, is_address_complete, compose_full_address
from app.services.state_manager_service import StateManager
from app.services.context_manager_service import ContextManager
from app.core.logger import logger
from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.messages import Messages
from app.core.prompts import Prompts
from app.core.constants import LeadStatus, Defaults, UserStates, WorkerConstants
from app.services.matching_service import determine_best_pro
from app.services.notification_service import send_sos_alert
from app.services.data_management_service import has_consent, record_consent
from app.services.customer_flow import (
    send_customer_completion_check as _send_completion_check,
    handle_customer_completion_text as _handle_completion,
    handle_customer_rating_text,
    handle_customer_review_comment,
)
from app.services.pro_flow import handle_pro_text_command as _handle_pro_cmd
from app.services.pro_onboarding_service import (
    start_onboarding, handle_onboarding_step, ONBOARDING_STATES,
)
from app.services.media_handler import detect_and_fetch_media
import re
from datetime import datetime, timezone

# Initialize services
whatsapp = WhatsAppClient()
ai = AIEngine()
lead_manager = LeadManager()

# Pro business keywords that must always route to pro_flow, even mid-CUSTOMER_MODE
PRO_BUSINESS_KEYWORDS = (
    set(Messages.Keywords.APPROVE_COMMANDS)
    | set(Messages.Keywords.REJECT_COMMANDS)
    | set(Messages.Keywords.FINISH_COMMANDS)
    | set(Messages.Keywords.ACTIVE_JOBS_COMMANDS)
    | set(Messages.Keywords.HISTORY_COMMANDS)
    | set(Messages.Keywords.STATS_COMMANDS)
    | set(Messages.Keywords.REVIEWS_COMMANDS)
    | set(Messages.Keywords.RESUME_COMMANDS)
    | set(Messages.Keywords.PAUSE_COMMANDS)
)


# --- Public API (used by scheduler, admin panel, arq_worker) ---

async def send_customer_completion_check(lead_id: str, triggered_by: str = "auto"):
    """Public wrapper — delegates to customer_flow with shared whatsapp instance."""
    await _send_completion_check(lead_id, whatsapp, triggered_by)


async def send_pro_reminder(lead_id: str, triggered_by: str = "auto"):
    """Re-export from notification_service for scheduler compatibility."""
    from app.services.notification_service import send_pro_reminder as _reminder
    await _reminder(lead_id, triggered_by)


# --- Main Orchestrator ---

async def process_incoming_message(chat_id: str, user_text: str, media_url: str = None):
    normalized_text = (user_text or "").strip().lower()

    # Get state early — needed to skip global checks for pros
    current_state = await StateManager.get_state(chat_id)

    # Global Reset Check (skip for pros — they use "תפריט" to show their menu)
    if normalized_text in Messages.Keywords.RESET_COMMANDS and current_state != UserStates.PRO_MODE:
        await StateManager.clear_state(chat_id)
        await ContextManager.clear_context(chat_id)
        await whatsapp.send_message(chat_id, Messages.System.RESET_SUCCESS)
        return

    # Zero-Touch: transient confirmation after intent was detected in pro_flow
    if current_state == UserStates.AWAITING_INTENT_CONFIRMATION:
        if normalized_text == "1" or normalized_text in ("כן", "yes"):
            await StateManager.set_state(chat_id, UserStates.CUSTOMER_MODE)
            await ContextManager.clear_context(chat_id)
            await whatsapp.send_message(chat_id, Messages.Pro.SWITCHED_TO_CUSTOMER)
            return
        if normalized_text == "2" or normalized_text in ("לא", "no"):
            await StateManager.clear_state(chat_id)
            await whatsapp.send_message(chat_id, Messages.Pro.SWITCH_CANCELLED)
            return
        # Any other reply: clear transient state and fall through to normal routing
        await StateManager.clear_state(chat_id)
        current_state = await StateManager.get_state(chat_id)

    # Consent Check (skip for professionals — they're added by admin)

    if current_state != UserStates.PRO_MODE:
        phone = chat_id.replace("@c.us", "")
        is_pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}, "role": "professional"})

        if not is_pro:
            consent_status = await has_consent(chat_id)

            # Handle consent response first (state takes priority over DB status)
            if current_state == UserStates.AWAITING_CONSENT:
                if normalized_text in Messages.Consent.ACCEPT_KEYWORDS:
                    await record_consent(chat_id, accepted=True)
                    await StateManager.clear_state(chat_id)
                    await whatsapp.send_message(chat_id, Messages.Consent.ACCEPTED)
                    return
                elif normalized_text in Messages.Consent.DECLINE_KEYWORDS:
                    await record_consent(chat_id, accepted=False)
                    await StateManager.clear_state(chat_id)
                    await whatsapp.send_message(chat_id, Messages.Consent.DECLINED)
                    return
                else:
                    # Repeat consent request if unclear response
                    await whatsapp.send_message(chat_id, Messages.Consent.REQUEST)
                    return

            if consent_status is None:
                # First contact — send consent request
                await whatsapp.send_message(chat_id, Messages.Consent.REQUEST)
                await StateManager.set_state(chat_id, UserStates.AWAITING_CONSENT)
                return

            if consent_status is False:
                # User previously declined — re-ask on new contact
                await whatsapp.send_message(chat_id, Messages.Consent.REQUEST)
                await StateManager.set_state(chat_id, UserStates.AWAITING_CONSENT)
                return

    # Refresh state after potential consent state changes
    current_state = await StateManager.get_state(chat_id)
    logger.info(f"🚦 User {chat_id} is in State: {current_state}")

    # SOS / Human Handoff Check (customers only — pros have their own help menu)
    sos_keywords = Messages.Keywords.SOS_COMMANDS
    if user_text and current_state != UserStates.PRO_MODE and any(k in normalized_text for k in sos_keywords):
        # Pause bot with 15-minute auto-expiry (Task 1 updated constants)
        await StateManager.set_state(chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS)
        logger.info(f"Bot paused for {chat_id} (triggered by: customer_sos, TTL: {WorkerConstants.PAUSE_TTL_SECONDS}s)")

        active_lead = await leads_collection.find_one({
            "chat_id": chat_id,
            "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]}
        }, sort=[("created_at", -1)])

        if active_lead:
            await leads_collection.update_one(
                {"_id": active_lead["_id"]},
                {"$set": {"is_paused": True, "paused_at": datetime.now(timezone.utc)}}
            )

        pro_id = active_lead["pro_id"] if active_lead and "pro_id" in active_lead else None
        await send_sos_alert(chat_id, user_text, pro_id)

        # Notify the pro about the pause (if assigned)
        if pro_id:
            pro = await users_collection.find_one({"_id": pro_id})
            if pro and pro.get("phone_number"):
                pro_phone = pro["phone_number"]
                if not pro_phone.endswith("@c.us"):
                    pro_phone = f"{pro_phone}@c.us"
                await whatsapp.send_message(pro_phone, Messages.Pro.PAUSE_NOTIFICATION)

        await whatsapp.send_message(chat_id, Messages.Customer.BOT_PAUSED_BY_CUSTOMER)
        return

    # Soft Hold — customer is waiting for pro approval
    if current_state == UserStates.AWAITING_PRO_APPROVAL:
        await whatsapp.send_message(chat_id, Messages.Customer.STILL_WAITING)
        return

    # Bot Paused — pro or customer triggered human handoff (auto-expires via Redis TTL)
    if current_state == UserStates.PAUSED_FOR_HUMAN:
        await lead_manager.log_message(chat_id, "user", user_text or "")
        # Task 2: Reset 15-minute rolling window
        await StateManager.set_state(chat_id, UserStates.PAUSED_FOR_HUMAN, ttl=WorkerConstants.PAUSE_TTL_SECONDS)

        # Update paused_at in lead doc to track activity for SLA monitor
        await leads_collection.update_one(
            {"chat_id": chat_id, "is_paused": True, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]}},
            {"$set": {"paused_at": datetime.now(timezone.utc)}},
        )

        logger.info(f"Bot paused for {chat_id} — message logged and timeout reset to {WorkerConstants.PAUSE_TTL_SECONDS}s")
        return

    # Safety Bypass: a registered pro typing a business keyword always routes to pro_flow,
    # even if they're currently in CUSTOMER_MODE — snap them back to PRO_MODE first.
    if normalized_text in PRO_BUSINESS_KEYWORDS:
        phone = chat_id.replace("@c.us", "")
        is_pro_doc = await users_collection.find_one(
            {"phone_number": {"$in": [phone, chat_id]}, "role": "professional"}
        )
        if is_pro_doc and current_state != UserStates.PRO_MODE:
            await StateManager.set_state(chat_id, UserStates.PRO_MODE)
            current_state = UserStates.PRO_MODE

    # Handle Pro Mode
    if current_state == UserStates.PRO_MODE:
        pro_resp = await _handle_pro_cmd(chat_id, user_text, whatsapp, lead_manager, ai=ai)
        if pro_resp:
            await whatsapp.send_message(chat_id, pro_resp)
        elif pro_resp is None:
            await whatsapp.send_message(chat_id, Messages.Pro.PRO_HELP_MENU)
        # empty string "" means pro_flow already sent everything internally
        return

    # Handle Pro Onboarding Flow
    if current_state in ONBOARDING_STATES:
        await handle_onboarding_step(chat_id, user_text or "", current_state, whatsapp)
        return

    # Handle Awaiting Address — re-entry after the finalization gate rejected an
    # incomplete address. Re-run extraction on the customer's reply, merge with
    # whatever we already stored, and only clear the state when all five fields
    # (street, number, city, floor, apartment) are present.
    if current_state == UserStates.AWAITING_ADDRESS:
        if not user_text or len(user_text) <= 3:
            await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_INVALID)
            return

        active_lead_await = await leads_collection.find_one(
            {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}},
            sort=[("created_at", -1)]
        )
        if not active_lead_await:
            await StateManager.clear_state(chat_id)
            # Fall through to normal routing below
        else:
            lead_facts = active_lead_await
            await lead_manager.log_message(chat_id, "user", user_text)
            follow_up_prompt = Prompts.DISPATCHER_SYSTEM.format(
                known_city=lead_facts.get("city") or "none",
                known_issue=lead_facts.get("issue_type") or "none",
                known_street=lead_facts.get("street") or "none",
                known_street_number=lead_facts.get("street_number") or "none",
                known_floor=lead_facts.get("floor") or "none",
                known_apartment=lead_facts.get("apartment") or "none",
            )
            try:
                follow_up = await ai.analyze_conversation(
                    history=await lead_manager.get_chat_history(chat_id),
                    user_text=user_text,
                    custom_system_prompt=follow_up_prompt,
                    require_json=True,
                )
            except Exception as e:
                logger.error(f"AWAITING_ADDRESS re-extraction failed for {chat_id}: {e}")
                await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
                return

            merged = {
                "street": follow_up.extracted_data.street or lead_facts.get("street"),
                "street_number": follow_up.extracted_data.street_number or lead_facts.get("street_number"),
                "city": follow_up.extracted_data.city or lead_facts.get("city"),
                "floor": follow_up.extracted_data.floor or lead_facts.get("floor"),
                "apartment": follow_up.extracted_data.apartment or lead_facts.get("apartment"),
            }
            logger.info(
                f"🔍 AWAITING_ADDRESS re-extraction for {chat_id}: "
                f"new_from_ai={[k for k, v in merged.items() if v and not lead_facts.get(k)]}, "
                f"merged={ {k: v for k, v in merged.items() if v} }"
            )
            non_empty = {k: v for k, v in merged.items() if v}
            if non_empty:
                await leads_collection.update_one(
                    {"_id": active_lead_await["_id"]}, {"$set": non_empty}
                )

            class _AddrProbe:
                pass
            probe = _AddrProbe()
            probe.street = merged.get("street")
            probe.street_number = merged.get("street_number")
            probe.city = merged.get("city")
            probe.floor = merged.get("floor")
            probe.apartment = merged.get("apartment")

            ok, reason = is_address_complete(probe)
            if ok:
                full = compose_full_address(probe)
                await leads_collection.update_one(
                    {"_id": active_lead_await["_id"]}, {"$set": {"full_address": full}}
                )
                await StateManager.clear_state(chat_id)
                await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_SAVED)
                logger.info(f"✅ AWAITING_ADDRESS complete for {chat_id}, full_address={full!r}")
                return
            else:
                await whatsapp.send_message(chat_id, reason)
                logger.info(f"⏳ AWAITING_ADDRESS still missing parts for {chat_id}: {reason}")
                return

    # Pro Registration keyword check (before auto-detect)
    if current_state == UserStates.IDLE and normalized_text in Messages.Keywords.REGISTER_COMMANDS:
        await start_onboarding(chat_id, whatsapp)
        return

    # Auto-detect Professional on first contact (only active/approved pros)
    if current_state == UserStates.IDLE:
        phone = chat_id.replace("@c.us", "")
        is_pro = await users_collection.find_one({"phone_number": {"$in": [phone, chat_id]}, "role": "professional", "is_active": True})
        if is_pro:
            await StateManager.set_state(chat_id, UserStates.PRO_MODE)
            pro_resp = await _handle_pro_cmd(chat_id, user_text, whatsapp, lead_manager, ai=ai)
            if pro_resp:
                await whatsapp.send_message(chat_id, pro_resp)
            elif pro_resp is None:
                await whatsapp.send_message(chat_id, Messages.Pro.PRO_HELP_MENU)
            # empty string "" means pro_flow already sent everything internally
            return

    # 1. Log User Message
    log_text = user_text
    if media_url:
        log_text = f"{user_text or ''} [MEDIA: {media_url}]"
    await lead_manager.log_message(chat_id, "user", log_text)

    # 2. Check for Customer Completion, Rating, or Review
    if user_text:
        completion_resp = await _handle_completion(chat_id, user_text, whatsapp)
        if completion_resp:
            await whatsapp.send_message(chat_id, completion_resp)
            await lead_manager.log_message(chat_id, "model", completion_resp)
            return

        rating_resp = await handle_customer_rating_text(chat_id, user_text)
        if rating_resp:
            await whatsapp.send_message(chat_id, rating_resp)
            await lead_manager.log_message(chat_id, "model", rating_resp)
            return

        review_resp = await handle_customer_review_comment(chat_id, user_text)
        if review_resp:
            await whatsapp.send_message(chat_id, review_resp)
            await lead_manager.log_message(chat_id, "model", review_resp)
            return

    # 3. Handle Media
    media_data = None
    media_mime = None
    if media_url:
        try:
            media_data, media_mime = await detect_and_fetch_media(media_url)
        except Exception as e:
            logger.warning(f"Media fetch failed for {chat_id}: {e}")

    # 4. Check for existing active lead with assigned pro (skip dispatcher if so)
    active_lead = await leads_collection.find_one({
        "chat_id": chat_id,
        "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}
    }, sort=[("created_at", -1)])

    existing_pro = None
    if active_lead and active_lead.get("pro_id"):
        existing_pro = await users_collection.find_one({"_id": active_lead["pro_id"], "is_active": True})

    history = await lead_manager.get_chat_history(chat_id)

    # --- OPTIMIZATION 1: Skip dispatcher if pro already assigned ---
    if existing_pro and active_lead:
        logger.info(f"⚡ Skipping dispatcher — pro already assigned for {chat_id}")
        await lead_manager.log_message(chat_id, "user", user_text or (f"[MEDIA: {media_url}]" if media_url else ""))

        extracted_city = active_lead.get("full_address", "")
        extracted_issue = active_lead.get("issue_type", "")
        transcription = None

        try:
            pro_response_obj = await _build_pro_response(
                existing_pro, history, user_text,
                extracted_city, extracted_issue, transcription,
                media_data=media_data, media_mime=media_mime, media_url=media_url,
            )
        except Exception as e:
            logger.error(f"Pro response failed for {chat_id}: {e}")
            await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
            return

        await whatsapp.send_message(chat_id, pro_response_obj.reply_to_user)
        await lead_manager.log_message(chat_id, "model", pro_response_obj.reply_to_user)

        # Check for deal
        is_deal = pro_response_obj.is_deal or bool(re.search(r"\[DEAL:.*?\]", pro_response_obj.reply_to_user))
        if is_deal:
            try:
                await _finalize_deal(
                    chat_id, existing_pro, pro_response_obj,
                    extracted_city, extracted_issue, transcription,
                    active_lead["_id"], media_url=media_url
                )
            except Exception as e:
                logger.error(f"Deal finalization failed for {chat_id}: {e}")
        return

    # 5. Smart Dispatcher Phase (only when no pro assigned yet)
    # Context window trimming is centralized in ai_engine_service.py
    # Inject sticky facts from the active lead so extractions survive the 10-message window.
    lead_facts = active_lead or {}
    sticky = {
        "city": lead_facts.get("city") or lead_facts.get("full_address") or "none",
        "issue": lead_facts.get("issue_type") or "none",
        "street": lead_facts.get("street") or "none",
        "street_number": lead_facts.get("street_number") or "none",
        "floor": lead_facts.get("floor") or "none",
        "apartment": lead_facts.get("apartment") or "none",
    }
    logger.info(
        f"📌 Sticky facts injected for {chat_id}: "
        f"city={sticky['city']}, issue={sticky['issue']}, "
        f"street={sticky['street']} {sticky['street_number']}, "
        f"floor={sticky['floor']}, apt={sticky['apartment']}"
    )
    dispatcher_history = history
    dispatcher_prompt = Prompts.DISPATCHER_SYSTEM.format(
        known_city=sticky["city"],
        known_issue=sticky["issue"],
        known_street=sticky["street"],
        known_street_number=sticky["street_number"],
        known_floor=sticky["floor"],
        known_apartment=sticky["apartment"],
    )

    try:
        dispatcher_response: AIResponse = await ai.analyze_conversation(
            history=dispatcher_history,
            user_text=user_text or "",
            custom_system_prompt=dispatcher_prompt,
            media_data=media_data,
            media_mime_type=media_mime,
            media_url=media_url,
            require_json=True
        )
    except Exception as e:
        logger.error(f"AI dispatcher failed for {chat_id}: {e}")
        await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
        return

    # Merge: prefer fresh AI output, fall back to stored lead facts so a trimmed
    # window or a silent parse-failure can't erase a previously-confirmed fact.
    ai_city = dispatcher_response.extracted_data.city
    ai_issue = dispatcher_response.extracted_data.issue
    extracted_city = ai_city or lead_facts.get("city") or lead_facts.get("full_address")
    extracted_issue = ai_issue or lead_facts.get("issue_type")
    transcription = dispatcher_response.transcription

    if (not ai_city and extracted_city) or (not ai_issue and extracted_issue):
        logger.warning(
            f"🩹 Sticky-facts fallback used for {chat_id}: "
            f"AI returned city={ai_city!r}/issue={ai_issue!r}, "
            f"lead facts filled in city={extracted_city!r}/issue={extracted_issue!r}"
        )

    logger.info(f"Dispatcher analysis: City={extracted_city}, Issue={extracted_issue}, Transcr={transcription}")

    # 6. Logic Gate: Dispatcher vs Professional
    best_pro = None
    pro_response_obj = None
    current_lead_id = None

    if extracted_city and extracted_issue:
        if active_lead:
            current_lead_id = active_lead["_id"]
        else:
            new_lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=extracted_issue,
                full_address=extracted_city,
                status=LeadStatus.CONTACTED,
                appointment_time=Defaults.PENDING_TIME
            )
            if new_lead:
                current_lead_id = new_lead["_id"]

        try:
            best_pro = await determine_best_pro(issue_type=extracted_issue, location=extracted_city)
        except Exception as e:
            logger.error(f"Pro matching failed for {chat_id}: {e}")

        # If no pro found, escalate to admin review instead of closing.
        if not best_pro and current_lead_id:
            existing_lead = await leads_collection.find_one({"_id": current_lead_id})
            if existing_lead and not existing_lead.get("pro_id"):
                await leads_collection.update_one(
                    {"_id": current_lead_id},
                    {"$set": {"status": LeadStatus.PENDING_ADMIN_REVIEW}}
                )
                logger.critical(f"Lead {current_lead_id} for {chat_id} requires admin review — no pro available")
                await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
                await lead_manager.log_message(chat_id, "model", Messages.Customer.PENDING_REVIEW)
                return

        if best_pro:
            is_new_assignment = False
            if current_lead_id:
                existing_lead = await leads_collection.find_one({"_id": current_lead_id})
                had_pro = existing_lead and existing_lead.get("pro_id")
                await leads_collection.update_one(
                    {"_id": current_lead_id},
                    {"$set": {"pro_id": best_pro["_id"]}}
                )
                if not had_pro:
                    is_new_assignment = True
            else:
                is_new_assignment = True

            if is_new_assignment:
                try:
                    pro_phone = best_pro.get("phone_number")
                    if pro_phone:
                        if not pro_phone.endswith("@c.us"):
                            pro_phone = f"{pro_phone}@c.us"
                        notify_msg = (
                            Messages.Pro.EARLY_LEAD_HEADER + "\n\n"
                            + Messages.Pro.EARLY_LEAD_DETAILS.format(
                                issue_type=extracted_issue,
                                city=extracted_city
                            )
                            + Messages.Pro.EARLY_LEAD_FOOTER
                        )
                        await whatsapp.send_message(pro_phone, notify_msg)
                        logger.info(f"📢 Notified pro {pro_phone} about new lead from {chat_id}")
                except Exception as e:
                    logger.error(f"Failed to notify pro about new lead: {e}")

            try:
                pro_response_obj = await _build_pro_response(
                    best_pro, history, user_text,
                    extracted_city, extracted_issue, transcription,
                    media_data=media_data, media_mime=media_mime, media_url=media_url,
                )
            except Exception as e:
                logger.error(f"Pro response build failed for {chat_id}: {e}")
                pro_response_obj = None

    # Select which response to send
    final_response = pro_response_obj if (best_pro and pro_response_obj) else dispatcher_response

    # Send Message to User
    await whatsapp.send_message(chat_id, final_response.reply_to_user)
    await lead_manager.log_message(chat_id, "model", final_response.reply_to_user)

    # 7. Check for [DEAL] or Structured Booking
    is_deal = final_response.is_deal

    deal_string_match = re.search(r"\[DEAL:.*?\]", final_response.reply_to_user)
    if deal_string_match:
        is_deal = True

    if is_deal and best_pro:
        try:
            await _finalize_deal(
                chat_id, best_pro, final_response,
                extracted_city, extracted_issue, transcription,
                current_lead_id, media_url=media_url
            )
        except Exception as e:
            logger.error(f"Deal finalization failed for {chat_id}: {e}")


# --- Private Helpers ---

async def _build_pro_response(best_pro, history, user_text, extracted_city, extracted_issue, transcription,
                               media_data=None, media_mime=None, media_url=None):
    """Build the pro persona AI response."""
    pro_name = best_pro.get("business_name", Defaults.PROLI_PRO_NAME)
    raw_price_list = best_pro.get("price_list", "")
    if isinstance(raw_price_list, dict):
        price_list = ", ".join(f"{k}: {v} ILS" for k, v in raw_price_list.items())
    else:
        price_list = str(raw_price_list) if raw_price_list else ""
    base_system_prompt = best_pro.get("system_prompt", Messages.AISystemPrompts.PROLI_SCHEDULER_ROLE.format(pro_name=pro_name))

    rating = best_pro.get("social_proof", {}).get("rating", 5.0)
    count = best_pro.get("social_proof", {}).get("review_count", 0)
    social_proof_text = f"{rating} stars based on {count} reviews"

    full_system_prompt = Prompts.PRO_BASE_SYSTEM.format(
        base_system_prompt=base_system_prompt,
        pro_name=pro_name,
        price_list=price_list,
        social_proof_text=social_proof_text,
        extracted_city=extracted_city,
        extracted_issue=extracted_issue,
        transcription=transcription or Defaults.DEFAULT_TRANSCRIPTION
    )

    return await ai.analyze_conversation(
        history=history,
        user_text=user_text or "",
        custom_system_prompt=full_system_prompt,
        media_data=media_data,
        media_mime_type=media_mime,
        media_url=media_url,
        require_json=True,
        pro_id=str(best_pro["_id"]),
    )


async def _finalize_deal(chat_id, best_pro, final_response, extracted_city, extracted_issue, transcription, current_lead_id, media_url=None):
    """Finalize a deal: create/update lead, set customer to AWAITING_PRO_APPROVAL, send pro interactive buttons."""
    # Hard address gate: never dispatch a pro without street+number+city+floor+apartment.
    ed = final_response.extracted_data
    logger.info(
        f"🚧 Address gate check for {chat_id}: "
        f"street={ed.street!r}, number={ed.street_number!r}, city={ed.city!r}, "
        f"floor={ed.floor!r}, apt={ed.apartment!r}, time={ed.appointment_time!r}"
    )
    ok, reason = is_address_complete(ed)
    if not ok:
        # Persist whatever partial address parts we already have so the sticky
        # facts survive the next turn and the customer doesn't re-state them.
        partial_update = {
            "street": ed.street,
            "street_number": ed.street_number,
            "city": ed.city or extracted_city,
            "floor": ed.floor,
            "apartment": ed.apartment,
            "issue_type": ed.issue or extracted_issue,
        }
        partial_update = {k: v for k, v in partial_update.items() if v}
        if current_lead_id and partial_update:
            await leads_collection.update_one({"_id": current_lead_id}, {"$set": partial_update})
            logger.info(
                f"💾 Persisted partial address parts for {chat_id} (lead={current_lead_id}): {list(partial_update.keys())}"
            )

        await StateManager.set_state(chat_id, UserStates.AWAITING_ADDRESS)
        await whatsapp.send_message(chat_id, reason)
        logger.warning(f"🚫 Address gate REJECTED finalization for {chat_id}: {reason}")
        return
    logger.info(f"✅ Address gate PASSED for {chat_id}")

    d_time = final_response.extracted_data.appointment_time or Defaults.ASAP_TIME
    d_address = compose_full_address(final_response.extracted_data)
    d_issue = final_response.extracted_data.issue or extracted_issue or Defaults.UNKNOWN_ISSUE

    lead_update = {
        "status": LeadStatus.NEW,
        "appointment_time": d_time,
        "full_address": d_address,
        "street": final_response.extracted_data.street,
        "street_number": final_response.extracted_data.street_number,
        "city": final_response.extracted_data.city,
        "floor": final_response.extracted_data.floor,
        "apartment": final_response.extracted_data.apartment,
        "issue_type": d_issue,
        "pro_id": best_pro["_id"],
    }
    if media_url:
        lead_update["media_url"] = media_url

    if current_lead_id:
        await leads_collection.update_one(
            {"_id": current_lead_id},
            {"$set": lead_update}
        )
        lead = await leads_collection.find_one({"_id": current_lead_id})
    else:
        lead = await lead_manager.create_lead_from_dict(
            chat_id=chat_id,
            issue_type=d_issue,
            full_address=d_address,
            appointment_time=d_time,
            status=LeadStatus.NEW,
            pro_id=best_pro["_id"]
        )

    if lead:
        # 1. Set customer state to AWAITING_PRO_APPROVAL with a bounded TTL so
        #    the customer is never silently stuck if the pro misses the notification.
        await StateManager.set_state(
            chat_id,
            UserStates.AWAITING_PRO_APPROVAL,
            ttl=WorkerConstants.PRO_APPROVAL_TTL_SECONDS,
        )
        await whatsapp.send_message(chat_id, Messages.Customer.AWAITING_APPROVAL)
        logger.info(f"Customer {chat_id} entered AWAITING_PRO_APPROVAL state")

        # 2. Send pro approval request with interactive buttons
        pro_phone = best_pro.get("phone_number")
        if pro_phone:
            if not pro_phone.endswith("@c.us"):
                pro_phone = f"{pro_phone}@c.us"

            customer_phone = chat_id.replace("@c.us", "")
            approval_msg = Messages.Pro.APPROVAL_REQUEST.format(
                customer_phone=customer_phone,
                full_address=lead['full_address'],
                issue_type=lead['issue_type'],
                appointment_time=lead['appointment_time'],
            )

            if transcription:
                approval_msg += Messages.Pro.NEW_LEAD_TRANSCRIPTION.format(transcription=transcription)

            lead_media_url = lead.get("media_url") or media_url
            if lead_media_url:
                approval_msg += Messages.Pro.APPROVAL_MEDIA.format(media_url=lead_media_url)

            await whatsapp.send_message(pro_phone, approval_msg)

            await whatsapp.send_location_link(pro_phone, lead['full_address'], Messages.Pro.NAVIGATE_TO)

    # Zero-Touch: if the "customer" is actually a registered pro (came via CUSTOMER_MODE),
    # snap them back to PRO_MODE so they can keep running their own business.
    phone = chat_id.replace("@c.us", "")
    customer_is_pro = await users_collection.find_one(
        {"phone_number": {"$in": [phone, chat_id]}, "role": "professional"}
    )
    if customer_is_pro:
        await StateManager.set_state(chat_id, UserStates.PRO_MODE)
        await whatsapp.send_message(chat_id, Messages.Pro.AUTO_RETURNED_TO_PRO)
        logger.info(f"Auto-returned pro-as-customer {chat_id} to PRO_MODE after lead dispatched")
