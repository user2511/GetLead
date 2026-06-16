import logging
import asyncio
from datetime import datetime
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from models.config import BusinessConfig
from agents.intent_agent import classify_intent
from agents.response_agent import generate_response
from agents.booking_agent import handle_booking_flow, booking_sessions
from agents.escalation_agent import notify_owner_escalation
from agents.followup_agent import (
    send_24hr_reminder,
    send_1hr_reminder,
    send_review_request,
    send_noshow_followup
)
from services.db_service import (
    SessionLocal,
    get_or_create_lead,
    update_lead,
    create_booking
)

logger = logging.getLogger(__name__)


# ══════════════════════════════════════════════
# STATE
# ══════════════════════════════════════════════

class LeadFlowState(TypedDict):
    # Input
    message:          str
    phone_number:     str
    business_config:  dict

    # Set by intent_node
    intent:            Optional[str]
    confidence:        Optional[float]
    customer_name:     Optional[str]
    service_requested: Optional[str]
    preferred_time:    Optional[str]
    lead_id:           Optional[str]

    # Routing flags
    needs_escalation:    bool
    is_booking_request:  bool
    is_followup_trigger: bool
    followup_type:       Optional[str]

    # Set by booking_node
    booking_confirmed: bool
    booking_object:    Optional[dict]

    # Final output
    response_text: Optional[str]


# ══════════════════════════════════════════════
# HELPER — BookingProxy
# ══════════════════════════════════════════════

class BookingProxy:
    """
    Wraps a plain dict so followup functions
    can access fields like a real DB object.
    """
    def __init__(self, data: dict):
        self.id                 = data.get("id")
        self.customer_name      = data.get("customer_name")
        self.customer_phone     = data.get("customer_phone")
        self.service            = data.get("service")
        self.status             = data.get("status", "CONFIRMED")
        self.calcom_booking_uid = data.get("calcom_booking_uid")

        raw = data.get("scheduled_at")
        if isinstance(raw, str):
            self.scheduled_at = datetime.fromisoformat(raw)
        elif isinstance(raw, datetime):
            self.scheduled_at = raw
        else:
            self.scheduled_at = datetime.now()


# ══════════════════════════════════════════════
# NODE 1 — INTENT CLASSIFIER
# ══════════════════════════════════════════════

def intent_node(state: LeadFlowState) -> LeadFlowState:
    phone       = state["phone_number"]
    config_dict = state["business_config"]
    message     = state["message"].strip()
    message_lower = message.lower()

    logger.info(f"[INTENT] phone={phone} msg='{message[:60]}'")

    # ── Get or create lead ──
    db = SessionLocal()
    try:
        lead = get_or_create_lead(
            db=db,
            phone_number=phone,
            business_id=config_dict.get("business_id", "default")
        )
        lead_id = lead.id
    except Exception as e:
        logger.error(f"[INTENT] DB error: {e}")
        lead_id = None
    finally:
        db.close()

    # ── Check active booking session ──
    active_session = booking_sessions.get(phone)
    current_step = active_session.get("step") if active_session else None

    # ── If mid-booking AND message is "0" or "menu" → show menu ──
    if active_session and current_step not in (None, "DONE"):
        if message in ("0", "00", "menu", "main menu", "back"):
            from agents.booking_agent import clear_session
            clear_session(phone)
            logger.info("[INTENT] User requested menu mid-booking — clearing session")
            return {
                **state,
                "intent":             "GREETING",
                "confidence":         1.0,
                "customer_name":      None,
                "service_requested":  None,
                "preferred_time":     None,
                "lead_id":            lead_id,
                "needs_escalation":   False,
                "is_booking_request": False,
            }

        # Mid-booking → continue booking flow
        logger.info(f"[INTENT] Continuing booking step={current_step}")
        return {
            **state,
            "intent":             "BOOK_APPOINTMENT",
            "confidence":         1.0,
            "customer_name":      state.get("customer_name"),
            "service_requested":  state.get("service_requested"),
            "preferred_time":     state.get("preferred_time"),
            "lead_id":            lead_id,
            "needs_escalation":   False,
            "is_booking_request": True,
        }

    # ── Menu-driven routing ──
    # Map number choices to intents directly
    menu_map = {
        "1": "BOOK_APPOINTMENT",
        "2": "RESCHEDULE",
        "3": "CANCEL",
        "4": "EMERGENCY",
        "5": "FAQ",
    }

    if message in menu_map:
        intent = menu_map[message]
        logger.info(f"[INTENT] Menu choice {message} → {intent}")

        needs_escalation   = intent == "EMERGENCY"
        is_booking_request = intent == "BOOK_APPOINTMENT"

        if needs_escalation:
            db = SessionLocal()
            try:
                update_lead(db=db, lead_id=lead_id,
                    last_message=message, intent=intent,
                    conversation_state="ESCALATED")
            except Exception as e:
                logger.error(f"[INTENT] DB error: {e}")
            finally:
                db.close()

        return {
            **state,
            "intent":             intent,
            "confidence":         1.0,
            "customer_name":      None,
            "service_requested":  None,
            "preferred_time":     None,
            "lead_id":            lead_id,
            "needs_escalation":   needs_escalation,
            "is_booking_request": is_booking_request,
        }

    # ── Not a menu choice → classify with LLM ──
    # (handles "hi", "hello", or anything typed freely)
    result = classify_intent(
        message=message,
        business_name=config_dict.get("business_name", "our business")
    )
    intent = result.get("intent", "UNKNOWN")

    needs_escalation   = intent == "EMERGENCY"
    is_booking_request = intent in ("BOOK_APPOINTMENT", "RESCHEDULE")

    db = SessionLocal()
    try:
        update_lead(db=db, lead_id=lead_id,
            last_message=message, intent=intent,
            conversation_state="ESCALATED" if needs_escalation else "IN_PROGRESS")
    except Exception as e:
        logger.error(f"[INTENT] DB error: {e}")
    finally:
        db.close()

    logger.info(f"[INTENT] LLM classified: {intent}")

    return {
        **state,
        "intent":             intent,
        "confidence":         result.get("confidence"),
        "customer_name":      result.get("customer_name"),
        "service_requested":  result.get("service_requested"),
        "preferred_time":     result.get("preferred_time"),
        "lead_id":            lead_id,
        "needs_escalation":   needs_escalation,
        "is_booking_request": is_booking_request,
    }

# ══════════════════════════════════════════════
# NODE 2 — BOOKING AGENT
# ══════════════════════════════════════════════

async def booking_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles full multi-turn appointment booking conversation.
    When booking confirmed:
      → saves booking to DB
      → schedules all 4 follow-up reminders
    """
    logger.info(
        f"[BOOKING] phone={state['phone_number']} "
        f"service={state['service_requested']}"
    )

    config = BusinessConfig(**state["business_config"])

    intent_result = {
        "intent":             state["intent"],
        "customer_name":      state["customer_name"],
        "service_requested":  state["service_requested"],
        "preferred_time":     state["preferred_time"],
    }

    # Run async booking conversation
    response, booking_data = await handle_booking_flow(
        message=state["message"],
        phone_number=state["phone_number"],
        config=config,
        intent_result=intent_result,
        lead_id=state.get("lead_id")
    )

    booking_confirmed = booking_data is not None

    # If booking just confirmed → save to DB + schedule reminders
    if booking_confirmed and booking_data:
        db = SessionLocal()
        try:
            db_booking = create_booking(db, {
                "lead_id":            state.get("lead_id", "unknown"),
                "business_id":        config.business_id,
                "customer_name":      booking_data["customer_name"],
                "customer_phone":     state["phone_number"],
                "service":            booking_data["service"],
                "scheduled_at":       booking_data["scheduled_at"],
                "calcom_booking_uid": booking_data.get("calcom_booking_uid"),
                "status":             "CONFIRMED",
            })

            from services.scheduler_service import schedule_booking_reminders
            schedule_booking_reminders(db_booking)

            logger.info(
                f"[BOOKING] Saved + reminders scheduled "
                f"for {booking_data['customer_name']}"
            )
        except Exception as e:
            logger.error(f"[BOOKING] DB/scheduler error: {e}")
        finally:
            db.close()

    logger.info(
        f"[BOOKING] confirmed={booking_confirmed} "
        f"response='{response[:60]}'"
    )

    return {
        **state,
        "response_text":    response,
        "booking_confirmed": booking_confirmed,
        "booking_object":   booking_data,
    }


# ══════════════════════════════════════════════
# NODE 3 — GENERAL RESPONSE AGENT
# ══════════════════════════════════════════════

def response_node(state: LeadFlowState) -> LeadFlowState:
    config      = BusinessConfig(**state["business_config"])
    intent      = state["intent"]

    logger.info(f"[RESPONSE] intent={intent} phone={state['phone_number']}")

    # ── Show main menu for greeting or unknown ──
    if intent in ("GREETING", "UNKNOWN"):
        from agents.response_agent import get_main_menu
        response = get_main_menu(config.business_name)

    # ── Show FAQ menu ──
    elif intent == "FAQ":
        from agents.response_agent import get_faq_menu
        response = get_faq_menu()

    # ── Reschedule ──
    elif intent == "RESCHEDULE":
        response = (
            f"No problem! Let's get you rescheduled. 😊\n\n"
            f"I'll start a fresh booking for you.\n"
            f"Just reply *1* to begin or *0* for main menu."
        )

    # ── Cancel ──
    elif intent == "CANCEL":
        response = (
            f"Sorry to hear that! 🙏\n\n"
            f"Your cancellation request has been noted.\n"
            f"Please call us directly to confirm:\n"
            f"📞 {config.business_name}\n\n"
            f"Reply *1* if you'd like to rebook instead, "
            f"or *0* for main menu."
        )

    # ── Everything else — use LLM ──
    else:
        intent_result = {
            "intent":             intent,
            "customer_name":      state["customer_name"],
            "service_requested":  state["service_requested"],
            "preferred_time":     state["preferred_time"],
        }
        response = generate_response(
            intent_result=intent_result,
            message=state["message"],
            config=config
        )
        # Always add menu at end
        response += "\n\nReply *0* to go back to main menu."

    logger.info(f"[RESPONSE] response='{response[:60]}'")
    return {**state, "response_text": response}

# ══════════════════════════════════════════════
# NODE 4 — ESCALATION AGENT
# ══════════════════════════════════════════════

def escalation_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles emergency/urgent messages.
    1. Sends immediate reply to customer
    2. Notifies business owner on WhatsApp
    3. Updates lead status in DB
    """
    logger.warning(f"[ESCALATION] EMERGENCY phone={state['phone_number']}")

    config_dict   = state["business_config"]
    business_name = config_dict.get("business_name", "the clinic")

    # 1. Reply to customer
    customer_response = (
        f"🚨 This sounds urgent — treating it as a priority!\n\n"
        f"I've immediately alerted the *{business_name}* team. "
        f"Someone will contact you within the next few minutes.\n\n"
        f"For life-threatening emergencies please call *112* right away. 🙏"
    )

    # 2. Notify owner
    try:
        notify_owner_escalation(
            phone_number=state["phone_number"],
            message=state["message"],
            reason=(
                "EMERGENCY intent detected by AI"
                if state.get("intent") == "EMERGENCY"
                else "Escalation keyword matched in message"
            )
        )
    except Exception as e:
        logger.error(f"[ESCALATION] Owner notify failed: {e}")

    # 3. Update lead in DB
    if state.get("lead_id"):
        db = SessionLocal()
        try:
            update_lead(
                db=db,
                lead_id=state["lead_id"],
                is_escalated=True,
                conversation_state="ESCALATED"
            )
        except Exception as e:
            logger.error(f"[ESCALATION] DB update failed: {e}")
        finally:
            db.close()

    logger.warning("[ESCALATION] Owner notified. Customer response ready.")
    return {**state, "response_text": customer_response}


# ══════════════════════════════════════════════
# NODE 5 — FOLLOW-UP DISPATCHER
# ══════════════════════════════════════════════

def followup_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles ALL scheduled follow-up messages.
    Called by trigger_followup() from APScheduler.
    NOT triggered by customer messages.

    followup_type:
      "24hr"   → reminder 24hrs before appointment
      "1hr"    → reminder 1hr before appointment
      "review" → review request 2hrs after visit
      "noshow" → follow-up if customer no-showed
    """
    followup_type  = state.get("followup_type")
    booking_object = state.get("booking_object")

    logger.info(f"[FOLLOWUP] type={followup_type} phone={state['phone_number']}")

    if not booking_object:
        logger.error("[FOLLOWUP] booking_object missing")
        return {**state, "response_text": None}

    if not followup_type:
        logger.error("[FOLLOWUP] followup_type missing")
        return {**state, "response_text": None}

    booking       = BookingProxy(booking_object)
    business_name = state["business_config"].get("business_name", "our clinic")
    success       = False

    try:
        if followup_type == "24hr":
            success = send_24hr_reminder(booking, business_name)

        elif followup_type == "1hr":
            success = send_1hr_reminder(booking, business_name)

        elif followup_type == "review":
            success = send_review_request(booking, business_name)

        elif followup_type == "noshow":
            db = SessionLocal()
            try:
                from models.booking import Booking as BookingModel
                db_booking = db.query(BookingModel).filter(
                    BookingModel.id == booking.id
                ).first()

                if db_booking and db_booking.status == "CONFIRMED":
                    db_booking.status = "NO_SHOW"
                    db.commit()
                    success = send_noshow_followup(booking, business_name)
                    logger.info(f"[FOLLOWUP] Marked NO_SHOW: {booking.id}")
                else:
                    logger.info(
                        f"[FOLLOWUP] Booking {booking.id} "
                        f"status={db_booking.status if db_booking else 'not found'} "
                        f"— skipping noshow"
                    )
                    success = True
            finally:
                db.close()

        else:
            logger.error(f"[FOLLOWUP] Unknown type: {followup_type}")

    except Exception as e:
        logger.error(f"[FOLLOWUP] type={followup_type} error: {e}")

    logger.info(f"[FOLLOWUP] type={followup_type} success={success}")

    # Sends directly via Twilio — no TwiML response needed
    return {**state, "response_text": None}


# ══════════════════════════════════════════════
# ROUTING
# ══════════════════════════════════════════════

def route_after_intent(state: LeadFlowState) -> str:
    """
    Priority: escalation > booking > response
    """
    if state.get("needs_escalation"):
        return "escalation"
    if state.get("is_booking_request"):
        return "booking"
    return "response"


# ══════════════════════════════════════════════
# BUILD GRAPH
# ══════════════════════════════════════════════

def build_pipeline() -> StateGraph:
    graph = StateGraph(LeadFlowState)

    # ── Rename nodes to avoid conflict with state keys ──
    graph.add_node("intent_classifier",  intent_node)
    graph.add_node("booking_handler",    booking_node)
    graph.add_node("response_handler",   response_node)
    graph.add_node("escalation_handler", escalation_node)
    graph.add_node("followup_handler",   followup_node)

    graph.set_entry_point("intent_classifier")

    graph.add_conditional_edges(
        "intent_classifier",
        route_after_intent,
        {
            "escalation": "escalation_handler",
            "booking":    "booking_handler",
            "response":   "response_handler",
        }
    )

    graph.add_edge("booking_handler",    END)
    graph.add_edge("response_handler",   END)
    graph.add_edge("escalation_handler", END)
    graph.add_edge("followup_handler",   END)

    compiled = graph.compile()
    logger.info("✅ LangGraph pipeline compiled — 5 nodes active")
    return compiled

pipeline = build_pipeline()
# ══════════════════════════════════════════════
# PUBLIC ENTRY POINTS
# ══════════════════════════════════════════════

async def process_message(
    message: str,
    phone_number: str,
    business_config: dict
) -> str:
    """
    Entry point for incoming WhatsApp messages.
    Called by Twilio webhook on every customer message.
    Returns response text for TwiML reply.
    """
    initial_state: LeadFlowState = {
        "message":             message,
        "phone_number":        phone_number,
        "business_config":     business_config,
        "intent":              None,
        "confidence":          None,
        "customer_name":       None,
        "service_requested":   None,
        "preferred_time":      None,
        "lead_id":             None,
        "needs_escalation":    False,
        "is_booking_request":  False,
        "is_followup_trigger": False,
        "followup_type":       None,
        "booking_confirmed":   False,
        "booking_object":      None,
        "response_text":       None,
    }

    logger.info(f"[PIPELINE] process_message | phone={phone_number}")

    result = await pipeline.ainvoke(initial_state)

    response = result.get(
        "response_text",
        "Thanks for reaching out! We'll get back to you shortly. 😊"
    )

    logger.info(
        f"[PIPELINE] complete | "
        f"intent={result.get('intent')} | "
        f"response='{response[:60]}'"
    )

    return response


def trigger_followup(
    followup_type: str,
    phone_number: str,
    business_config: dict,
    booking_object: dict
) -> None:
    """
    Entry point for scheduled follow-up messages.
    Called by APScheduler — NOT by customer messages.
    Calls followup_node directly — bypasses graph entirely.

    followup_type: "24hr" | "1hr" | "review" | "noshow"
    booking_object: serialized booking dict from DB
    """
    state: LeadFlowState = {
        "message":             "",
        "phone_number":        phone_number,
        "business_config":     business_config,
        "intent":              None,
        "confidence":          None,
        "customer_name":       None,
        "service_requested":   None,
        "preferred_time":      None,
        "lead_id":             None,
        "needs_escalation":    False,
        "is_booking_request":  False,
        "is_followup_trigger": True,
        "followup_type":       followup_type,
        "booking_confirmed":   False,
        "booking_object":      booking_object,
        "response_text":       None,
    }

    logger.info(
        f"[PIPELINE] trigger_followup | "
        f"type={followup_type} | phone={phone_number}"
    )

    # Call followup_node directly — no need to go through full graph
    followup_node(state)

    logger.info(f"[PIPELINE] trigger_followup complete | type={followup_type}")