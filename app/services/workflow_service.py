from app.services.whatsapp_client_service import WhatsAppClient
from app.services.ai_engine_service import AIEngine, AIResponse
from app.services.lead_manager_service import LeadManager
from app.services.state_manager_service import StateManager
from app.services.context_manager_service import ContextManager
from app.core.logger import logger
from app.core.database import users_collection, leads_collection, reviews_collection
from app.core.messages import Messages
from app.core.prompts import Prompts
from app.core.constants import LeadStatus, Defaults, UserStates
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

# Initialize services
whatsapp = WhatsAppClient()
ai = AIEngine()
lead_manager = LeadManager()


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
        await StateManager.set_state(chat_id, UserStates.SOS)

        active_lead = await leads_collection.find_one({
            "chat_id": chat_id,
            "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED, LeadStatus.BOOKED]}
        }, sort=[("created_at", -1)])

        pro_id = active_lead["pro_id"] if active_lead and "pro_id" in active_lead else None
        await send_sos_alert(chat_id, user_text, pro_id)

        if pro_id:
            await whatsapp.send_message(chat_id, Messages.SOS.TO_USER_WITH_PRO)
        else:
            await whatsapp.send_message(chat_id, Messages.SOS.TO_USER_NO_PRO)
        return

    # Handle Pro Mode
    if current_state == UserStates.PRO_MODE:
        pro_resp = await _handle_pro_cmd(chat_id, user_text, whatsapp, lead_manager)
        if pro_resp:
            await whatsapp.send_message(chat_id, pro_resp)
        else:
            await whatsapp.send_message(chat_id, Messages.Pro.PRO_HELP_MENU)
        return

    # Handle Pro Onboarding Flow
    if current_state in ONBOARDING_STATES:
        await handle_onboarding_step(chat_id, user_text or "", current_state, whatsapp)
        return

    # Handle Awaiting Address
    if current_state == UserStates.AWAITING_ADDRESS:
        if user_text and len(user_text) > 3:
            active_lead = await leads_collection.find_one(
                {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}},
                sort=[("created_at", -1)]
            )
            if active_lead:
                await leads_collection.update_one(
                    {"_id": active_lead["_id"]},
                    {"$set": {"full_address": user_text}}
                )
                await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_SAVED)
                await StateManager.clear_state(chat_id)
                return
            else:
                await StateManager.clear_state(chat_id)
        else:
            await whatsapp.send_message(chat_id, Messages.Customer.ADDRESS_INVALID)
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
            pro_resp = await _handle_pro_cmd(chat_id, user_text, whatsapp, lead_manager)
            if pro_resp:
                await whatsapp.send_message(chat_id, pro_resp)
            else:
                await whatsapp.send_message(chat_id, Messages.Pro.PRO_HELP_MENU)
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
                    active_lead["_id"]
                )
            except Exception as e:
                logger.error(f"Deal finalization failed for {chat_id}: {e}")
        return

    # 5. Smart Dispatcher Phase (only when no pro assigned yet)
    # OPTIMIZATION 2: Trim history — dispatcher only needs last 8 messages
    dispatcher_history = history[-8:] if len(history) > 8 else history
    dispatcher_prompt = Prompts.DISPATCHER_SYSTEM

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

    extracted_city = dispatcher_response.extracted_data.city
    extracted_issue = dispatcher_response.extracted_data.issue
    transcription = dispatcher_response.transcription

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
                current_lead_id
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

    recent_history = history[-4:] if len(history) > 4 else history
    return await ai.analyze_conversation(
        history=recent_history,
        user_text=user_text or "",
        custom_system_prompt=full_system_prompt,
        media_data=media_data,
        media_mime_type=media_mime,
        media_url=media_url,
        require_json=True
    )


async def _finalize_deal(chat_id, best_pro, final_response, extracted_city, extracted_issue, transcription, current_lead_id):
    """Finalize a deal: update/create lead and notify pro."""
    d_time = final_response.extracted_data.appointment_time or Defaults.ASAP_TIME
    d_address = final_response.extracted_data.full_address or extracted_city or Defaults.UNKNOWN_ADDRESS
    d_issue = final_response.extracted_data.issue or extracted_issue or Defaults.UNKNOWN_ISSUE

    if current_lead_id:
        await leads_collection.update_one(
            {"_id": current_lead_id},
            {"$set": {
                "status": LeadStatus.NEW,
                "appointment_time": d_time,
                "full_address": d_address,
                "issue_type": d_issue,
                "pro_id": best_pro["_id"]
            }}
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
        pro_phone = best_pro.get("phone_number")
        if pro_phone:
            if not pro_phone.endswith("@c.us"):
                pro_phone = f"{pro_phone}@c.us"

            msg_to_pro = Messages.Pro.DEAL_CONFIRMED_HEADER + "\n\n"
            msg_to_pro += Messages.Pro.NEW_LEAD_DETAILS.format(
                full_address=lead['full_address'],
                issue_type=lead['issue_type'],
                appointment_time=lead['appointment_time']
            )

            if transcription:
                msg_to_pro += Messages.Pro.NEW_LEAD_TRANSCRIPTION.format(transcription=transcription)

            msg_to_pro += Messages.Pro.NEW_LEAD_FOOTER
            await whatsapp.send_message(pro_phone, msg_to_pro)

            await whatsapp.send_location_link(pro_phone, lead['full_address'], Messages.Pro.NAVIGATE_TO)

        # Clear customer context so stale city/issue don't leak into future messages
        await ContextManager.clear_context(chat_id)
