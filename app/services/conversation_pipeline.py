"""The customer conversation pipeline — everything the guard chain falls through to.

PRO-139 slice B (PRO-182), the last one.

`dispatch_guards.GUARD_CHAIN` is a *decision table*: 25 ordered clauses, each of
which either answers the message or steps aside, and whose order is the whole
contract. What is left once the chain falls through is not a table at all — it
is a straight line. One message, top to bottom: log it, check whether it answers
an open completion/rating/review prompt, fetch media, find the active lead, run
the AI dispatcher, persist what it extracted, match a pro, send the reply, and
finalize the deal if the turn closes one. There is no ordering *choice* left to
express here; each step consumes what the one above it produced.

That difference is why this is a module and a function rather than more guards.
A guard that needed `history`, `media_data`, `extracted_city`, `lead_facts` and
`current_lead_id` from four guards above it would have to carry all of them on
`DispatchContext`, and the chain's one useful property — that any clause can be
read, moved or removed by looking only at its neighbours — would be gone.

How collaborators are reached
-----------------------------

Every one of them is resolved in the prologue of `run_customer_pipeline`, per
call, and never at import:

* five of `GuardDeps`' six fields come from `deps`, which is the
  dependency-injection seam `customer_flow` and `pro_flow` already use
  (`settings` is the sixth and has no reader here);
* everything else comes from `wf.<name>` through a call-time
  ``from app.services import workflow_service as wf``.

The call-time import is not only about the import cycle (`workflow_service`
imports this module at its top). It is what keeps `workflow_service` the single
patch point the suite monkeypatches: dozens of tests do
``monkeypatch.setattr(workflow_service, "whatsapp", fake)`` and expect the
message they send to land in `fake`. Binding these names at import time — or
snapshotting them into a dataclass built once — would silently detach every one
of those patch points, and the tests would keep passing against the real
objects. This is the A1 convention (PRO-179), for the same reason.

Binding them to locals rather than spelling `wf.leads_collection` at each of
fifteen call sites is deliberate: it let the body move here **verbatim**, which
is what makes a statement-level diff against the pre-image evidence rather than
decoration. The prologue is the only new code in this module.
"""

from app.core.constants import (
    Actor,
    Defaults,
    LeadStatus,
    UserStates,
    WorkerConstants,
)
from app.core.logger import logger
from app.providers.whatsapp.quality import non_essential_sends_paused
from app.core.messages import Messages
from app.core.phone import mask_chat_id, to_chat_id
from app.core.prompts import Prompts
from app.services.ai_engine_service import AIResponse, file_notice_in
from app.services.dispatch_guards import DispatchContext, GuardDeps
from app.services.matching_service import (
    customer_phones,
    issue_update,
    probable_trade,
    trade_fits,
    trade_named_in_chat,
)


async def _send_file_notice(response: AIResponse, chat_id, whatsapp, lead_manager):
    """For an exit that sends a fixed message instead of the reply carrying this.

    First line only: the state these exits lead to won't read a description, so
    the media-only notice's request for one would mislead.
    """
    notice = file_notice_in(response.reply_to_user)
    if notice:
        first_line = notice.split("\n")[0]
        await whatsapp.send_message(chat_id, first_line)
        await lead_manager.log_message(chat_id, "model", first_line)


async def run_customer_pipeline(ctx: DispatchContext, deps: GuardDeps) -> None:
    """Run steps 1–7 for one inbound customer message.

    Called by `workflow_service._process_incoming_message_inner` once
    `run_guard_chain` has fallen through — i.e. no guard claimed the message.

    Returns `None` in every case. Unlike a guard, this never reports "not mine":
    by the time control reaches here the message *is* a customer conversation
    turn, and every exit below has already answered the customer.
    """
    # Call-time, for the two reasons in the module docstring: the import cycle,
    # and keeping `workflow_service` the one place the suite patches.
    from app.services import workflow_service as wf

    # --- prologue: collaborators, resolved per call --------------------------
    # Five of `GuardDeps`' six fields (`settings` has no reader here), carried
    # in from the chain. One resolution-timing note: `deps` is built *before*
    # `run_guard_chain`, where the pre-image read these five as module globals
    # *after* it. Equivalent only while nothing rebinds them mid-turn, and
    # nothing does — neither this module nor `dispatch_guards` has a `global`
    # statement, and every test patch lands at setup, before the turn starts.
    whatsapp = deps.whatsapp
    StateManager = deps.state_manager
    ContextManager = deps.context_manager
    users_collection = deps.users_collection
    SecurityService = deps.security

    # The rest are read off `workflow_service` at call time.
    ai = wf.ai
    lead_manager = wf.lead_manager
    leads_collection = wf.leads_collection
    notification_service = wf.notification_service
    _handle_completion = wf._handle_completion
    handle_customer_rating_text = wf.handle_customer_rating_text
    handle_customer_review_comment = wf.handle_customer_review_comment
    detect_and_fetch_media = wf.detect_and_fetch_media
    determine_best_pro = wf.determine_best_pro
    set_lead_status = wf.set_lead_status
    is_address_complete = wf.is_address_complete
    _build_pro_response = wf._build_pro_response
    _finalize_deal = wf._finalize_deal
    _emergency_ack_for = wf._emergency_ack_for
    _inbound_log_text = wf._inbound_log_text
    _strip_deal_marker = wf._strip_deal_marker
    _clean_quoted_price = wf._clean_quoted_price
    DEAL_MARKER_RE = wf.DEAL_MARKER_RE

    # --- prologue: the turn's facts, read back off the context ---------------
    # The guards mutate these on the way past — the rate limiter resolves
    # `is_exempt` (still read at the two daily AI-cap call sites below), the
    # consent gate, zero-touch's second miss, the emergency hoist's "released"
    # path and loyalty's double-miss release all refresh `current_state` before
    # falling through, and the emergency hoist (PRO-180) and the consent gate's
    # replay set `emergency_inbound_logged` so step 1 does not log the same turn
    # twice — so this must take the post-chain values, not the pre-chain ones.
    #
    # `chat_id` alone is never rewritten. `user_text`, `media_url` and
    # `is_emergency_detected` are, by exactly one guard and deliberately: the
    # consent gate, which swaps in the messages held from before consent on an
    # accept and resolves `media_url` once a message is past it. Any other guard
    # assigning them would be changing what the customer said.
    chat_id = ctx.chat_id
    user_text = ctx.user_text
    media_url = ctx.media_url
    is_emergency_detected = ctx.is_emergency_detected
    current_state = ctx.current_state
    is_exempt = ctx.is_exempt
    emergency_inbound_logged = ctx.emergency_inbound_logged
    # 1. Log User Message — unless the emergency hoist above already did, in
    #    which case logging again would duplicate the turn (PRO-116 Q5).
    if not emergency_inbound_logged:
        await lead_manager.log_message(
            chat_id, "user", _inbound_log_text(user_text, media_url, ctx.file_lost)
        )

    # 2. Check for Customer Completion, Rating, or Review
    if user_text:
        completion_resp = await _handle_completion(chat_id, user_text, whatsapp)
        if completion_resp:
            await whatsapp.send_message(chat_id, completion_resp)
            await lead_manager.log_message(chat_id, "model", completion_resp)
            return

        # `has_media` so an unreadable *caption* on a photo doesn't earn a
        # re-prompt: media is fetched in step 3, below this block, and a
        # re-prompt here would return before the photo is ever downloaded.
        rating_resp = await handle_customer_rating_text(
            chat_id, user_text, has_media=bool(media_url)
        )
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
    media_fetch_failed = False
    if media_url:
        try:
            media_data, media_mime = await detect_and_fetch_media(media_url)
        except Exception as e:
            logger.warning(f"Media fetch failed for {mask_chat_id(chat_id)}: {e}")
            # PRO-124: tell the customer. Until now this failed in silence, and
            # silence here reads as "received".
            #
            # What failed is narrower than it looks, which is why the copy says
            # "לפתוח" and not "לקבל": `media_urls` is $addToSet-ed onto the lead
            # further down regardless of this exception, and the pro is sent
            # *links*, never bytes (notification_service.format_media_links), so
            # on a transient failure the file still reaches them. The casualty
            # is this turn's multimodal AI read.
            #
            # Deliberately additive: no early return even when there is no
            # caption to act on. Returning would also skip the sticky gate,
            # which creates the CONTACTED lead for a media-only first contact —
            # a flow change this issue did not ask for.
            await whatsapp.send_message(chat_id, Messages.Errors.MEDIA_FETCH_FAILED)
            media_fetch_failed = True  # logged after the history read, below
    # Already apologised for above; the engine would otherwise report it again.
    ai_media_url = None if media_fetch_failed else media_url

    if ctx.file_lost and not (user_text or "").strip():
        # Nothing to answer and no link to keep, so no AI call. Its type went with
        # it, so TEXT_ONLY. With text, the engine says so on the reply (media_lost).
        notice = Messages.Errors.MEDIA_UPLOAD_FAILED_TEXT_ONLY
        await whatsapp.send_message(chat_id, notice)
        await lead_manager.log_message(chat_id, "model", notice)
        return

    # 4. Check for existing active lead with assigned pro (skip dispatcher if so)
    active_lead = await leads_collection.find_one(
        {"chat_id": chat_id, "status": {"$in": [LeadStatus.NEW, LeadStatus.CONTACTED]}},
        sort=[("created_at", -1)],
    )

    existing_pro = None
    if active_lead and active_lead.get("pro_id"):
        existing_pro = await users_collection.find_one(
            {"_id": active_lead["pro_id"], "is_active": True}
        )

    # PRO-215: a pro whose trade doesn't fit is not an assigned pro — answering
    # in their name leaves the customer with "I'm a plumber, not an electrician".
    # A human's explicit assignment stands. An unoffered lead falls through to the
    # dispatcher and matching below; one the pro was already offered goes through
    # the reassignment path.
    rematch_notice_sent = False
    if existing_pro and not active_lead.get("assigned_by_admin_at"):
        issue = active_lead.get("issue_type") or ""
        wanted = probable_trade(issue)
        if not wanted:
            # The dispatcher is skipped from here on, so a vague issue can only
            # be refined from this message. `issue_type` reaches unapproved pros,
            # logs and pages, so it records the trade, never the words (PRO-59).
            wanted = trade_named_in_chat(user_text)
            if wanted:
                base = issue
                if base in ("", Defaults.UNKNOWN_ISSUE):
                    base = Messages.Fallbacks.ISSUE
                label = Messages.Onboarding.TYPE_LABELS[wanted]
                issue = f"{base} ({label})"
                await leads_collection.update_one(
                    {"_id": active_lead["_id"]}, {"$set": {"issue_type": issue}}
                )
                active_lead["issue_type"] = issue
        if not trade_fits(existing_pro, wanted, held=True):
            logger.warning(
                f"Lead {active_lead['_id']} is with pro {existing_pro['_id']} outside "
                f"its trade ({wanted}) — rematching"
            )
            offered = active_lead["status"] == LeadStatus.NEW
            dropped = await leads_collection.update_one(
                {
                    "_id": active_lead["_id"],
                    "pro_id": existing_pro["_id"],
                    "status": active_lead["status"],
                },
                {
                    "$set": {"pro_id": None},
                    # That pro's estimate, not the next one's.
                    "$unset": {"quoted_price": ""},
                },
            )
            if not dropped.modified_count:
                logger.info(
                    f"Lead {active_lead['_id']} moved before its wrong-trade rematch"
                )
                return
            active_lead.pop("quoted_price", None)

            # True on every outcome below, so it goes first. Without it a pro
            # holding the offer answers "אשר" and finds nothing, or books the
            # other NEW lead they hold.
            notice = (
                Messages.Pro.LEAD_OUTSIDE_TRADE_OFFER
                if offered
                else Messages.Pro.LEAD_OUTSIDE_TRADE
            )
            try:
                if existing_pro.get("phone_number"):
                    sent = await whatsapp.send_message(
                        to_chat_id(existing_pro["phone_number"]),
                        notice.format(issue=issue),
                    )
                    # None is the facade's "blocked": a closed 24h window, usually.
                    if not sent:
                        logger.warning(
                            f"Wrong-trade notice for lead {active_lead['_id']} did not "
                            f"reach pro {existing_pro['_id']}"
                        )
            except Exception as e:
                logger.error(f"Failed to tell pro about the wrong-trade rematch: {e}")

            if offered:
                from app.services import monitor_service

                try:
                    # This turn never reaches the pipeline's own recording below.
                    turn_ops = {}
                    if is_emergency_detected:
                        turn_ops["$set"] = {"is_emergency": True}
                        active_lead["is_emergency"] = True
                    if media_url:
                        turn_ops["$addToSet"] = {"media_urls": media_url}
                        urls = active_lead.get("media_urls") or []
                        if media_url not in urls:
                            active_lead["media_urls"] = [*urls, media_url]
                    if turn_ops:
                        await leads_collection.update_one(
                            {"_id": active_lead["_id"]}, turn_ops
                        )
                    await whatsapp.send_message(
                        chat_id, Messages.Customer.MOVING_TO_FITTING_PRO
                    )
                    await lead_manager.log_message(
                        chat_id, "model", Messages.Customer.MOVING_TO_FITTING_PRO
                    )
                    moved = await monitor_service.reassign_lead(
                        active_lead, notify_old_pro=False, announce=False
                    )
                except Exception as e:
                    logger.error(
                        f"Wrong-trade rematch failed for lead {active_lead['_id']}: {e}"
                    )
                    # Not when `reassign_lead` got as far as a new owner.
                    current = await leads_collection.find_one(
                        {"_id": active_lead["_id"]}
                    )
                    moved = bool(current and current.get("pro_id"))
                    if current and not current.get("pro_id"):
                        escalated = await set_lead_status(
                            active_lead["_id"],
                            LeadStatus.PENDING_ADMIN_REVIEW,
                            Actor.SYSTEM,
                            extra_set={
                                "escalation_reason": "wrong_trade_rematch_failed"
                            },
                            expected_status=LeadStatus.NEW,
                        )
                        if escalated:
                            await whatsapp.send_message(
                                chat_id, Messages.Customer.PENDING_REVIEW
                            )
                if not moved and (
                    is_emergency_detected or active_lead.get("is_emergency")
                ):
                    notification_service.page_operator(
                        f"EMERGENCY lead {active_lead['_id']} was with a pro outside "
                        "its trade and has no replacement — needs manual routing now"
                    )
                return

            await whatsapp.send_message(
                chat_id, Messages.Customer.MOVING_TO_FITTING_PRO
            )
            rematch_notice_sent = True
            existing_pro = None

    # PRO-116 Q3: a customer with a CONFIRMED (BOOKED) job who writes about
    # something else must not silently spawn a second parallel lead — that was
    # the root of the "3 leads / too many approvals" incident (BOOKED is absent
    # from the active_lead query above by design). Recognize the booked job and
    # ask whether this is a new request or about the existing one. Fires once per
    # booked lead (`new_request_prompted`), mirroring `loyalty_offered`.
    # Emergencies bypass — they must reach matching immediately.
    if (
        not active_lead
        and user_text
        and not is_emergency_detected
        and current_state != UserStates.PRO_MODE
    ):
        booked_lead = await leads_collection.find_one(
            {
                "chat_id": chat_id,
                "status": LeadStatus.BOOKED,
                "new_request_prompted": {"$ne": True},
            },
            sort=[("created_at", -1)],
        )
        if booked_lead:
            booked_pro = await users_collection.find_one(
                {"_id": booked_lead.get("pro_id")}
            )
            pro_name = (booked_pro or {}).get("business_name") or "איש המקצוע"
            await leads_collection.update_one(
                {"_id": booked_lead["_id"]},
                {"$set": {"new_request_prompted": True}},
            )
            await StateManager.set_metadata(
                chat_id, {"booked_lead_id": str(booked_lead["_id"])}
            )
            # PRO-193: bounded, like every other confirmation gate. Expiry
            # releases the customer to normal routing rather than trapping them
            # behind a question they could not phrase an answer to.
            await StateManager.set_state(
                chat_id,
                UserStates.AWAITING_NEW_OR_EXISTING,
                ttl=WorkerConstants.NEW_OR_EXISTING_TTL_SECONDS,
            )
            prompt = Messages.Customer.EXISTING_JOB_PROMPT.format(
                pro_name=pro_name,
                issue=booked_lead.get("issue_type") or "העבודה",
                appointment=booked_lead.get("appointment_time") or "בקרוב",
            )
            await whatsapp.send_message(chat_id, prompt)
            await lead_manager.log_message(chat_id, "model", prompt)
            return

    # Fresh-start guard: if there's no active lead, the user is starting a new
    # conversation. Drop any stale Redis context from a previously-closed lead
    # so the AI doesn't see turns that belong to a different request.
    if not active_lead:
        await ContextManager.clear_context(chat_id)
        logger.info(
            f"🧼 No active lead for {mask_chat_id(chat_id)} — cleared stale context before new dispatcher run"
        )

    history = await lead_manager.get_chat_history(chat_id)

    # PRO-124: logged here rather than at the send site one screen above.
    # `history` was just read and feeds this same turn's AI call, so logging the
    # apology any earlier puts it in the window as the model's last word — and
    # the dispatcher then reads the customer's caption as a reply *to* the
    # apology and apologises again. The emergency ack on the fast path below is
    # ordered after this read for the same reason.
    if media_fetch_failed:
        await lead_manager.log_message(
            chat_id, "model", Messages.Errors.MEDIA_FETCH_FAILED
        )
    if rematch_notice_sent:
        await lead_manager.log_message(
            chat_id, "model", Messages.Customer.MOVING_TO_FITTING_PRO
        )

    # --- OPTIMIZATION 1: Skip dispatcher if pro already assigned ---
    if existing_pro and active_lead:
        logger.info(
            f"⚡ Skipping dispatcher — pro already assigned for {mask_chat_id(chat_id)}"
        )
        # NOTE: the inbound was already logged once at the top of this function
        # (step 1). Do NOT log it again here — a second log_message duplicated
        # every user turn in history and in the AI context window (PRO-116 Q5).

        if is_emergency_detected and not active_lead.get("is_emergency"):
            await leads_collection.update_one(
                {"_id": active_lead["_id"]}, {"$set": {"is_emergency": True}}
            )
            ack = _emergency_ack_for(
                active_lead.get("city") or active_lead.get("full_address")
            )
            await whatsapp.send_message(chat_id, ack)
            await lead_manager.log_message(chat_id, "model", ack)
            active_lead["is_emergency"] = True

        if media_url:
            await leads_collection.update_one(
                {"_id": active_lead["_id"]}, {"$addToSet": {"media_urls": media_url}}
            )

        extracted_city = active_lead.get("full_address", "")
        extracted_issue = active_lead.get("issue_type", "")
        transcription = None

        # Daily AI cost cap also applies to the assigned-pro fast path — this is
        # the highest-volume conversation path and _build_pro_response makes a
        # Gemini call on every turn. Pros/admins are exempt (is_exempt above).
        if (
            not is_exempt
            and not await SecurityService.check_and_increment_daily_ai_cap(
                chat_id, WorkerConstants.DAILY_AI_CALL_CAP
            )
        ):
            logger.warning(f"⛔ Daily AI cap reached for {mask_chat_id(chat_id)}")
            await whatsapp.send_message(chat_id, Messages.Errors.DAILY_AI_CAP_REACHED)
            return

        try:
            pro_response_obj = await _build_pro_response(
                existing_pro,
                history,
                user_text,
                extracted_city,
                extracted_issue,
                transcription,
                media_data=media_data,
                media_mime=media_mime,
                media_url=ai_media_url,
                media_lost=ctx.file_lost,
            )
        except Exception as e:
            logger.error(f"Pro response failed for {mask_chat_id(chat_id)}: {e}")
            await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
            return

        # Check for deal on the raw text, then send/log the cleaned copy —
        # the [DEAL:...] marker must never reach the customer.
        is_deal = pro_response_obj.is_deal or bool(
            DEAL_MARKER_RE.search(pro_response_obj.reply_to_user)
        )
        # PRO-121: this is where an emergency released from the address gate
        # actually lands. Every route into AWAITING_ADDRESS writes `pro_id`
        # first — the matching block below does it before _finalize_deal runs,
        # and so does _accept_loyalty_offer — so `existing_pro` resolves and
        # this fast path returns long before the dispatcher's expedited branch.
        # Without the same widening here, releasing the gate only swapped one
        # unanswered question for another.
        emergency_expedite = bool(active_lead.get("is_emergency") and not is_deal)
        cleaned_reply = _strip_deal_marker(pro_response_obj.reply_to_user)
        if emergency_expedite:
            logger.info(
                f"🚑 Suppressing mid-intake reply for {mask_chat_id(chat_id)} — the "
                f"assigned pro is being asked to approve this turn "
                f"({len(cleaned_reply)} chars withheld)"
            )
        else:
            await whatsapp.send_message(chat_id, cleaned_reply)
            await lead_manager.log_message(chat_id, "model", cleaned_reply)

        if is_deal or emergency_expedite:
            try:
                await _finalize_deal(
                    chat_id,
                    existing_pro,
                    pro_response_obj,
                    extracted_city,
                    extracted_issue,
                    transcription,
                    active_lead["_id"],
                    media_url=media_url,
                    extracted_name=active_lead.get("customer_name"),
                )
            except Exception as e:
                logger.error(
                    f"Deal finalization failed for {mask_chat_id(chat_id)}: {e}"
                )
                # The suppressed reply was this turn's only customer-facing
                # message; finalization failing must not leave them with silence.
                if emergency_expedite:
                    await whatsapp.send_message(chat_id, cleaned_reply)
                    await lead_manager.log_message(chat_id, "model", cleaned_reply)
        return

    # 5. Smart Dispatcher Phase (only when no pro assigned yet)
    # Context window trimming is centralized in ai_engine_service.py
    # Inject sticky facts from the active lead so extractions survive the 10-message window.
    lead_facts = active_lead or {}
    # PRO-116 Q4: a returning customer whose prior lead is booked/closed has no
    # active_lead, so their name would be re-asked cold every time. Seed ONLY the
    # name (not city/issue — those are per-request) from their most recent prior
    # lead that captured one, so we greet them by name instead of re-interrogating.
    if not active_lead:
        prior_named = await leads_collection.find_one(
            {"chat_id": chat_id, "customer_name": {"$nin": [None, ""]}},
            sort=[("created_at", -1)],
        )
        if prior_named and prior_named.get("customer_name"):
            lead_facts = {"customer_name": prior_named["customer_name"]}
    sticky = {
        "customer_name": lead_facts.get("customer_name") or "none",
        "city": lead_facts.get("city") or lead_facts.get("full_address") or "none",
        "issue": lead_facts.get("issue_type") or "none",
        "street": lead_facts.get("street") or "none",
        "street_number": lead_facts.get("street_number") or "none",
        "floor": lead_facts.get("floor") or "none",
        "apartment": lead_facts.get("apartment") or "none",
    }
    # Which facts are known, not what they say. This line exists to debug the
    # sticky-facts mechanism — whether a field survived into the prompt — and
    # that question is answered by presence alone. The values are the
    # customer's name and home address, and since PRO-184 this line is indexed
    # and searchable (PRO-173's rule for pages, applied to logs).
    logger.info(
        f"📌 Sticky facts injected for {mask_chat_id(chat_id)}: "
        f"known={[k for k, v in sticky.items() if v and v != 'none']}, "
        # city/issue values kept deliberately: the sticky mechanism's
        # characteristic failure is a *stale* fact from a previous job
        # persisting, and presence alone cannot show that. Both are already
        # logged by value twice in this file, and PRO-173 rules city safe.
        f"city={sticky['city']!r}, issue={sticky['issue']!r}"
    )
    dispatcher_history = history
    dispatcher_prompt = Prompts.DISPATCHER_SYSTEM.format(
        known_customer_name=sticky["customer_name"],
        known_city=sticky["city"],
        known_issue=sticky["issue"],
        known_street=sticky["street"],
        known_street_number=sticky["street_number"],
        known_floor=sticky["floor"],
        known_apartment=sticky["apartment"],
    )

    if not is_exempt and not await SecurityService.check_and_increment_daily_ai_cap(
        chat_id, WorkerConstants.DAILY_AI_CALL_CAP
    ):
        logger.warning(f"⛔ Daily AI cap reached for {mask_chat_id(chat_id)}")
        await whatsapp.send_message(chat_id, Messages.Errors.DAILY_AI_CAP_REACHED)
        return
    try:
        dispatcher_response: AIResponse = await ai.analyze_conversation(
            history=dispatcher_history,
            user_text=user_text or "",
            custom_system_prompt=dispatcher_prompt,
            media_data=media_data,
            media_mime_type=media_mime,
            media_url=ai_media_url,
            media_lost=ctx.file_lost,
            require_json=True,
        )
    except Exception as e:
        logger.error(f"AI dispatcher failed for {mask_chat_id(chat_id)}: {e}")
        await whatsapp.send_message(chat_id, Messages.Errors.AI_OVERLOAD)
        return

    # Merge: prefer fresh AI output, fall back to stored lead facts so a trimmed
    # window or a silent parse-failure can't erase a previously-confirmed fact.
    ai_city = dispatcher_response.extracted_data.city
    ai_issue = issue_update(
        dispatcher_response.extracted_data.issue, lead_facts.get("issue_type")
    )
    ai_name = dispatcher_response.extracted_data.customer_name
    extracted_city = ai_city or lead_facts.get("city") or lead_facts.get("full_address")
    extracted_issue = ai_issue or lead_facts.get("issue_type")
    extracted_name = ai_name or lead_facts.get("customer_name")
    transcription = dispatcher_response.transcription

    if (not ai_city and extracted_city) or (not ai_issue and extracted_issue):
        logger.warning(
            f"🩹 Sticky-facts fallback used for {mask_chat_id(chat_id)}: "
            f"AI returned city={ai_city!r}/issue={ai_issue!r}, "
            f"lead facts filled in city={extracted_city!r}/issue={extracted_issue!r}"
        )

    logger.info(
        # Length, not content: a transcription is whatever the customer said,
        # which is routinely their name and street. `city`/`issue` are the
        # parsed fields and stay (PRO-173 rules city safe); the raw speech does not.
        f"Dispatcher analysis: City={extracted_city}, Issue={extracted_issue}, "
        f"transcription_chars={len(transcription or '')}"
    )

    # --- NEW: Sticky Persistence Gate ---
    # Create or update a "contacted" lead as soon as we have ANY info.
    # This ensures that if the AI forgets to repeat a field in the next turn,
    # it's still preserved in the DB and injected as a 'sticky' fact.
    #
    # Bound before the gate, not only inside it: every *later* reader used to
    # be nested under `if extracted_city and extracted_issue...`, which implied
    # the gate had run, but that is a fragile guarantee — a reader outside that
    # nesting (PRO-119's parts persistence below) would otherwise hit an
    # UnboundLocalError on any turn the gate skips.
    current_lead_id = active_lead["_id"] if active_lead else None
    if extracted_city or extracted_issue or media_url or is_emergency_detected:
        if not active_lead:
            active_lead = await lead_manager.create_lead_from_dict(
                chat_id=chat_id,
                issue_type=extracted_issue or Defaults.UNKNOWN_ISSUE,
                # full_address stays None until the address gate collects a
                # real address. Persisting the "Unknown Address" sentinel used
                # to confuse matching_service (see 2026-04-18 Unknown Address
                # incident) — see migration script migrate_unknown_address.py.
                full_address=extracted_city or None,
                status=LeadStatus.CONTACTED,
                appointment_time=Defaults.PENDING_TIME,
                media_url=media_url,
                customer_name=extracted_name,
                is_emergency=is_emergency_detected,
            )
            current_lead_id = active_lead["_id"] if active_lead else None
            if is_emergency_detected:
                ack = _emergency_ack_for(extracted_city)
                await whatsapp.send_message(chat_id, ack)
                await lead_manager.log_message(chat_id, "model", ack)
        else:
            current_lead_id = active_lead["_id"]
            update_data = {}
            if ai_city and ai_city != lead_facts.get("city"):
                update_data["city"] = ai_city
            if ai_issue and ai_issue != lead_facts.get("issue_type"):
                update_data["issue_type"] = ai_issue
            if ai_name and ai_name != lead_facts.get("customer_name"):
                update_data["customer_name"] = ai_name

            if is_emergency_detected and not lead_facts.get("is_emergency"):
                update_data["is_emergency"] = True
                ack = _emergency_ack_for(extracted_city)
                await whatsapp.send_message(chat_id, ack)
                await lead_manager.log_message(chat_id, "model", ack)

            mongo_ops = {}
            if update_data:
                mongo_ops["$set"] = update_data

            if media_url:
                mongo_ops["$addToSet"] = {"media_urls": media_url}

            if mongo_ops:
                await leads_collection.update_one({"_id": current_lead_id}, mongo_ops)
                # Refresh facts for the matching block below
                extracted_city = ai_city or extracted_city
                extracted_issue = ai_issue or extracted_issue

    # PRO-119: persist whatever address parts this turn's extraction produced.
    # The sticky gate above keeps only city/issue/name, so a customer who
    # front-loads their whole address ("נזילה ברחוב הרצל 10 קומה 2 דירה 5 בתל
    # אביב") had the parts dropped and got asked for them again — including
    # immediately after accepting the loyalty offer, which can only dispatch
    # when the parts are on the lead.
    if current_lead_id:
        extracted_parts = {
            field: getattr(dispatcher_response.extracted_data, field, None)
            for field in ("street", "street_number", "floor", "apartment")
        }
        extracted_parts = {k: v for k, v in extracted_parts.items() if v}
        if extracted_parts:
            await leads_collection.update_one(
                {"_id": current_lead_id}, {"$set": extracted_parts}
            )

    # PRO-121: is this turn acting on an emergency? Bound once here so the
    # loyalty gate, the PRO-116 Q1 early-notice suppression and the finalize
    # call site below all answer the question the same way.
    lead_is_emergency = bool(
        is_emergency_detected or (active_lead or {}).get("is_emergency")
    )

    # Loyalty Check: offer returning customers their previous pro before running
    # normal matching.
    #
    # PRO-121: never for an emergency. LOYALTY_OFFER parks the customer in
    # AWAITING_LOYALTY_CONFIRMATION — one of the holding states this issue is
    # about — to ask a question about *preference*. Escalating out of that state
    # afterwards (the hoisted branch above) is the cure; not entering it while
    # someone's home is flooding is the prevention.
    if (
        extracted_city
        and extracted_issue
        and extracted_issue != Defaults.UNKNOWN_ISSUE
        # PRO-215: an issue no trade can be named for goes to a human, not to
        # whoever this customer used last.
        and probable_trade(extracted_issue)
        and current_lead_id
        and not lead_is_emergency
        # PRO-90: no offer while WhatsApp quality is degraded. The lead carries
        # on into normal matching below, so dispatch is untouched.
        and not await non_essential_sends_paused()
    ):
        current_lead_doc = await leads_collection.find_one({"_id": current_lead_id})
        if current_lead_doc and not current_lead_doc.get("loyalty_offered"):
            # The offer has to name a pro who can do *this* job. It used to
            # take the single most recent COMPLETED lead whatever its trade, so
            # the plumber who fixed a leak got offered for an electrical short.
            # Walk back a bounded slice of completed jobs and take the most
            # recent pro whose trade fits; when the issue clearly names a trade
            # nobody in the history has, make no offer at all and let normal
            # matching find someone suitable.
            wanted_trade = probable_trade(extracted_issue)
            past_pro = None
            past_leads = (
                leads_collection.find(
                    {
                        "chat_id": chat_id,
                        "status": LeadStatus.COMPLETED,
                        "pro_id": {"$ne": None},
                    }
                )
                .sort("created_at", -1)
                .limit(WorkerConstants.LOYALTY_HISTORY_SCAN_LIMIT)
            )
            async for past_lead in past_leads:
                candidate = await users_collection.find_one(
                    {
                        "_id": past_lead["pro_id"],
                        "is_active": True,
                        "phone_number": {"$nin": customer_phones(chat_id)},
                    }
                )
                if candidate and trade_fits(candidate, wanted_trade):
                    past_pro = candidate
                    break
            if past_pro:
                await leads_collection.update_one(
                    {"_id": current_lead_id},
                    {"$set": {"loyalty_offered": True}},
                )
                await StateManager.set_metadata(
                    chat_id, {"past_pro_id": str(past_pro["_id"])}
                )
                # PRO-119: bounded TTL — without one this inherited the 4h
                # default and a customer whose reply we failed to parse
                # was trapped with no way out but a reset keyword.
                await StateManager.set_state(
                    chat_id,
                    UserStates.AWAITING_LOYALTY_CONFIRMATION,
                    ttl=WorkerConstants.LOYALTY_CONFIRM_TTL_SECONDS,
                )
                loyalty_msg = Messages.Customer.LOYALTY_OFFER.format(
                    pro_name=past_pro.get("business_name", "איש המקצוע")
                )
                await _send_file_notice(
                    dispatcher_response, chat_id, whatsapp, lead_manager
                )
                await whatsapp.send_message(chat_id, loyalty_msg)
                await lead_manager.log_message(chat_id, "model", loyalty_msg)
                return

    # 6. Logic Gate: Dispatcher vs Professional
    best_pro = None
    pro_response_obj = None

    # Note: the explicit `!= UNKNOWN_ADDRESS` check is gone because we no longer
    # persist that sentinel. `extracted_city` is either a real city string or None.
    #
    # An emergency doesn't wait for an issue. With none, matching finds nobody and
    # the block below pages the operator; otherwise the dispatcher could keep asking
    # with nobody told.
    if extracted_city and (
        lead_is_emergency
        or (extracted_issue and extracted_issue != Defaults.UNKNOWN_ISSUE)
    ):
        try:
            best_pro = await determine_best_pro(
                issue_type=extracted_issue,
                location=extracted_city,
                customer_chat_id=chat_id,
            )
        except Exception as e:
            logger.error(f"Pro matching failed for {mask_chat_id(chat_id)}: {e}")

        # If no pro found, escalate to admin review instead of closing.
        if not best_pro and current_lead_id:
            existing_lead = await leads_collection.find_one({"_id": current_lead_id})
            if existing_lead and not existing_lead.get("pro_id"):
                unknown_trade = not probable_trade(extracted_issue)
                await set_lead_status(
                    current_lead_id,
                    LeadStatus.PENDING_ADMIN_REVIEW,
                    Actor.SYSTEM,
                    extra_set=(
                        {"escalation_reason": "unknown_trade"}
                        if unknown_trade
                        else None
                    ),
                )
                why = (
                    "names no known trade" if unknown_trade else "has no available pro"
                )
                # WARNING, not CRITICAL: no-pro-available is a routine coverage gap
                # (admin handles it via PENDING_ADMIN_REVIEW), not an infra page. The
                # worker is Sentry CRITICAL-only, so this keeps no-pro leads out of the
                # operator's email (PRO-77). chat_id masked per PII convention.
                logger.warning(
                    f"Lead {current_lead_id} for {mask_chat_id(chat_id)} requires admin "
                    f"review — it {why}"
                )
                # PRO-121: a routine coverage gap is deliberately not a page
                # (see above), but an *emergency* with no pro is the one case
                # where nobody is coming and nobody has been told. The lead
                # would otherwise sit behind the 24h PENDING_ADMIN_REVIEW
                # short-circuit answering STILL_PENDING_REVIEW every 30 min.
                if lead_is_emergency:
                    notification_service.page_operator(
                        f"EMERGENCY lead {current_lead_id} {why} "
                        f"(city={extracted_city!r}, issue={extracted_issue!r}) — "
                        "PENDING_ADMIN_REVIEW, needs manual routing now"
                    )
                await _send_file_notice(
                    dispatcher_response, chat_id, whatsapp, lead_manager
                )
                await whatsapp.send_message(chat_id, Messages.Customer.PENDING_REVIEW)
                await lead_manager.log_message(
                    chat_id, "model", Messages.Customer.PENDING_REVIEW
                )
                return

        if best_pro:
            is_new_assignment = False
            if current_lead_id:
                existing_lead = await leads_collection.find_one(
                    {"_id": current_lead_id}
                )
                had_pro = existing_lead and existing_lead.get("pro_id")
                await leads_collection.update_one(
                    {"_id": current_lead_id}, {"$set": {"pro_id": best_pro["_id"]}}
                )
                if not had_pro:
                    is_new_assignment = True
            else:
                is_new_assignment = True

            # Build the persona response FIRST so we know whether this same turn
            # already closes the deal — PRO-116 Q1: if it does, sending the pro
            # the "שיחה בתהליך — אין צורך לפעול" early notice milliseconds before
            # the actual approval request is confusing noise. Suppress it then.
            try:
                pro_response_obj = await _build_pro_response(
                    best_pro,
                    history,
                    user_text,
                    extracted_city,
                    extracted_issue,
                    transcription,
                    media_data=media_data,
                    media_mime=media_mime,
                    media_url=ai_media_url,
                    media_lost=ctx.file_lost,
                )
            except Exception as e:
                logger.error(
                    f"Pro response build failed for {mask_chat_id(chat_id)}: {e}"
                )
                pro_response_obj = None

            # A [DEAL] marker alone is not enough — _finalize_deal's address gate
            # rejects an incomplete address, in which case the deal does NOT
            # finalize this turn and the pro still needs the EARLY_LEAD notice.
            # Only treat the turn as finalizing when the address is actually
            # complete (same gate helper), so we never suppress the notice on a
            # deal that will be rejected (PRO-116 Q1).
            _deal_flagged = bool(
                pro_response_obj
                and (
                    pro_response_obj.is_deal
                    or DEAL_MARKER_RE.search(pro_response_obj.reply_to_user)
                )
            )
            # PRO-121: an emergency finalizes on this turn with no [DEAL]
            # marker and with an incomplete address (_finalize_deal grants it a
            # city-only bypass), so deriving this from _deal_flagged alone sent
            # the pro "אין צורך לפעול עכשיו" milliseconds before the emergency
            # approval request — telling them to stand down on the one lead
            # that cannot wait.
            # The emergency arm is deliberately independent of
            # `pro_response_obj`: when _build_pro_response raises it is None,
            # yet the finalize call site below still dispatches on
            # `lead_is_emergency` — so gating both arms on it would send the
            # stand-down notice and the emergency approval request back to back.
            turn_finalizes = lead_is_emergency or (
                bool(pro_response_obj)
                and _deal_flagged
                and is_address_complete(pro_response_obj.extracted_data)[0]
            )

            if is_new_assignment and not turn_finalizes:
                try:
                    pro_phone = best_pro.get("phone_number")
                    if pro_phone:
                        pro_phone = to_chat_id(pro_phone)
                        notify_msg = (
                            Messages.Pro.EARLY_LEAD_HEADER
                            + "\n\n"
                            + Messages.Pro.EARLY_LEAD_DETAILS.format(
                                issue_type=extracted_issue, city=extracted_city
                            )
                            + Messages.Pro.EARLY_LEAD_FOOTER
                        )
                        # Send the CURRENT media only if it's new (to avoid duplicate spam)
                        if media_url:
                            await whatsapp.send_file_by_url(
                                pro_phone, media_url, caption=notify_msg
                            )
                        else:
                            await whatsapp.send_message(pro_phone, notify_msg)

                        logger.info(
                            f"📢 Notified pro {mask_chat_id(pro_phone)} about "
                            f"lead status from {mask_chat_id(chat_id)}"
                        )
                except Exception as e:
                    logger.error(f"Failed to notify pro about new lead: {e}")

    # Select which response to send
    final_response = (
        pro_response_obj if (best_pro and pro_response_obj) else dispatcher_response
    )

    # 7. Check for [DEAL] or Structured Booking — detect on the raw text
    # before stripping the marker for the customer-facing send.
    is_deal = final_response.is_deal

    deal_string_match = DEAL_MARKER_RE.search(final_response.reply_to_user)
    if deal_string_match:
        is_deal = True

    # PRO-121: this turn dispatches the emergency even though the AI did not
    # close the deal, so its reply is still mid-intake ("איזו קומה?"). Sending it
    # would contradict EMERGENCY_ACK's promise to ask only what is essential and
    # then be contradicted in turn by AWAITING_APPROVAL_TRANSPARENT a moment
    # later. The pro gets the full picture either way — _finalize_deal reads the
    # lead, not this reply.
    emergency_expedite = bool(best_pro and lead_is_emergency and not is_deal)

    # Send Message to User — cleaned copy, marker must never leak to the customer.
    cleaned_reply = _strip_deal_marker(final_response.reply_to_user)
    if emergency_expedite:
        logger.info(
            f"🚑 Suppressing mid-intake reply for {mask_chat_id(chat_id)} — emergency "
            f"dispatches this turn ({len(cleaned_reply)} chars withheld; the text "
            "is conversation content and stays out of the log)"
        )
    else:
        await whatsapp.send_message(chat_id, cleaned_reply)
        await lead_manager.log_message(chat_id, "model", cleaned_reply)

    # PRO-55: persist the AI's quoted price stickily the moment it's given (STEP 3),
    # so it reaches the pro approval request even though the estimate turn precedes
    # the deal close. Strip the ₪ symbol we re-add at display time.
    _qp_clean = _clean_quoted_price(
        getattr(final_response.extracted_data, "quoted_price", None)
    )
    if _qp_clean and current_lead_id:
        await leads_collection.update_one(
            {"_id": current_lead_id}, {"$set": {"quoted_price": _qp_clean}}
        )

    # PRO-121: an emergency does not wait for the AI to volunteer a [DEAL]
    # marker. Once a pro is actually matched, finalize on the spot — and only
    # then does the customer hear that someone has their call
    # (AWAITING_APPROVAL_TRANSPARENT, sent by _finalize_deal). Its address gate
    # already grants an emergency a city-only bypass, so the offer goes out with
    # whatever we have rather than stalling on floor and apartment.
    if best_pro and (is_deal or lead_is_emergency):
        if emergency_expedite:
            logger.warning(
                f"🚑 Emergency expedited dispatch for {mask_chat_id(chat_id)} — finalizing "
                f"without a [DEAL] marker (pro={best_pro['_id']})"
            )
        try:
            await _finalize_deal(
                chat_id,
                best_pro,
                final_response,
                extracted_city,
                extracted_issue,
                transcription,
                current_lead_id,
                media_url=media_url,
                extracted_name=extracted_name,
            )
        except Exception as e:
            logger.error(f"Deal finalization failed for {mask_chat_id(chat_id)}: {e}")
            # Same reasoning as the fast path above: on an expedited emergency
            # the AI's reply was withheld because _finalize_deal was going to
            # answer instead. It didn't, so send it rather than say nothing.
            if emergency_expedite:
                await whatsapp.send_message(chat_id, cleaned_reply)
                await lead_manager.log_message(chat_id, "model", cleaned_reply)
