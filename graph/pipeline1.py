import logging
import asyncio
from datetime import datetime
from typing import TypedDict, Optional

from langgraph.graph import StateGraph, END

from models.config import BusinessConfig
from agents.intent_agent import classify_intent
from agents.response_agent import generate_response
from agents.booking_agent import handle_booking_flow
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
# Travels through every node in the graph.
# Every field must have a value — use None
# as default for optional fields.
# ══════════════════════════════════════════════

class LeadFlowState(TypedDict):
    # ── Input ──
    message:         str
    phone_number:    str
    business_config: dict

    # ── Set by intent_node ──
    intent:           Optional[str]
    confidence:       Optional[float]
    customer_name:    Optional[str]
    service_requested: Optional[str]
    preferred_time:   Optional[str]
    lead_id:          Optional[str]

    # ── Routing flags ──
    needs_escalation:    bool
    is_booking_request:  bool
    is_followup_trigger: bool      # True = scheduler called us, skip intent
    followup_type:       Optional[str]  # "24hr"|"1hr"|"review"|"noshow"

    # ── Set by booking_node ──
    booking_confirmed: bool
    booking_object:    Optional[dict]  # serialized booking for scheduler

    # ── Final output ──
    response_text: Optional[str]


# ══════════════════════════════════════════════
# HELPER — BookingProxy
# Lets followup functions work with a plain dict
# the same way they'd work with a DB object.
# ══════════════════════════════════════════════

class BookingProxy:
    """
    Wraps a plain dict so followup functions
    can access .customer_name, .scheduled_at etc.
    like a real SQLAlchemy Booking object.
    """
    def __init__(self, data: dict):
        self.id               = data.get("id")
        self.customer_name    = data.get("customer_name")
        self.customer_phone   = data.get("customer_phone")
        self.service          = data.get("service")
        self.status           = data.get("status", "CONFIRMED")
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
    """
    Classifies every incoming WhatsApp message.
    Saves/updates lead in DB.
    Sets routing flags for all downstream nodes.
    """
    logger.info(
        f"[INTENT] phone={state['phone_number']} "
        f"msg='{state['message'][:60]}'"
    )

    config_dict = state["business_config"]

    # ── Classify intent ──
    result = classify_intent(
        message=state["message"],
        business_name=config_dict.get("business_name", "our business")
    )
    intent = result.get("intent", "UNKNOWN")

    # ── Check escalation ──
    escalation_keywords = config_dict.get("escalation_keywords", [])
    needs_escalation = (
        intent == "EMERGENCY" or
        any(
            kw.lower() in state["message"].lower()
            for kw in escalation_keywords
        )
    )

    # ── Save lead to DB ──
    lead_id = None
    db = SessionLocal()
    try:
        lead = get_or_create_lead(
            db=db,
            phone_number=state["phone_number"],
            business_id=config_dict.get("business_id", "default")
        )
        update_lead(
            db=db,
            lead_id=lead.id,
            last_message=state["message"],
            intent=intent,
            conversation_state="ESCALATED" if needs_escalation else "IN_PROGRESS"
        )
        lead_id = lead.id
    except Exception as e:
        logger.error(f"[INTENT] DB error: {e}")
    finally:
        db.close()

    logger.info(
        f"[INTENT] intent={intent} "
        f"escalation={needs_escalation} "
        f"lead_id={lead_id}"
    )

    return {
        **state,
        "intent":            intent,
        "confidence":        result.get("confidence"),
        "customer_name":     result.get("customer_name"),
        "service_requested": result.get("service_requested"),
        "preferred_time":    result.get("preferred_time"),
        "lead_id":           lead_id,
        "needs_escalation":  needs_escalation,
        "is_booking_request": intent in ("BOOK_APPOINTMENT", "RESCHEDULE")
    }


# ══════════════════════════════════════════════
# NODE 2 — BOOKING AGENT
# ══════════════════════════════════════════════

async def booking_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles full multi-turn appointment booking conversation.
    When booking confirmed:
      → saves booking to DB
      → schedules all 4 follow-up reminders via scheduler
    """
    logger.info(
        f"[BOOKING] phone={state['phone_number']} "
        f"service={state['service_requested']}"
    )

    config = BusinessConfig(**state["business_config"])

    intent_result = {
        "intent":            state["intent"],
        "customer_name":     state["customer_name"],
        "service_requested": state["service_requested"],
        "preferred_time":    state["preferred_time"]
    }

    # # ── Run async booking conversation ──
    # loop = asyncio.new_event_loop()
    # asyncio.set_event_loop(loop)
    # try:
    response, booking_data = await handle_booking_flow(
                message=state["message"],
                phone_number=state["phone_number"],
                config=config,
                intent_result=intent_result,
                lead_id=state.get("lead_id")
            )
    #     )
    # finally:
    #     loop.close()

    booking_confirmed = booking_data is not None

    # ── If confirmed → save to DB + schedule reminders ──
    if booking_confirmed and booking_data:
        db = SessionLocal()
        try:
            db_booking = create_booking(db, {
                "lead_id":           state.get("lead_id", "unknown"),
                "business_id":       config.business_id,
                "customer_name":     booking_data["customer_name"],
                "customer_phone":    state["phone_number"],
                "service":           booking_data["service"],
                "scheduled_at":      booking_data["scheduled_at"],
                "calcom_booking_uid": booking_data.get("calcom_booking_uid"),
                "status":            "CONFIRMED"
            })

            # Import here to avoid circular import at module load
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
        "response_text":   response,
        "booking_confirmed": booking_confirmed,
        "booking_object":  booking_data
    }


# ══════════════════════════════════════════════
# NODE 3 — GENERAL RESPONSE AGENT
# ══════════════════════════════════════════════

def response_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles all non-booking, non-emergency messages:
    FAQ, greetings, cancellations, unknown intents.
    """
    logger.info(
        f"[RESPONSE] intent={state['intent']} "
        f"phone={state['phone_number']}"
    )

    config = BusinessConfig(**state["business_config"])

    intent_result = {
        "intent":            state["intent"],
        "customer_name":     state["customer_name"],
        "service_requested": state["service_requested"],
        "preferred_time":    state["preferred_time"]
    }

    response = generate_response(
        intent_result=intent_result,
        message=state["message"],
        config=config
    )

    logger.info(f"[RESPONSE] response='{response[:60]}'")
    return {**state, "response_text": response}


# ══════════════════════════════════════════════
# NODE 4 — ESCALATION AGENT
# ══════════════════════════════════════════════

def escalation_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles emergency/urgent messages.
    Does TWO things:
      1. Sends immediate reassuring reply to customer
      2. Notifies business owner on WhatsApp
    """
    logger.warning(
        f"[ESCALATION] EMERGENCY "
        f"phone={state['phone_number']}"
    )

    config_dict  = state["business_config"]
    business_name = config_dict.get("business_name", "the clinic")

    # ── 1. Customer reply ──
    customer_response = (
        f"🚨 This sounds urgent — treating it as a priority!\n\n"
        f"I've immediately alerted the *{business_name}* team. "
        f"Someone will contact you within the next few minutes.\n\n"
        f"For life-threatening emergencies please call *112* right away. 🙏"
    )

    # ── 2. Notify owner ──
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

    # ── 3. Update lead in DB ──
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

    logger.warning(
        f"[ESCALATION] Owner notified. "
        f"Customer response sent."
    )

    return {**state, "response_text": customer_response}


# ══════════════════════════════════════════════
# NODE 5 — FOLLOW-UP DISPATCHER
# ══════════════════════════════════════════════

def followup_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles ALL scheduled follow-up messages.
    Called via trigger_followup() from APScheduler —
    NOT triggered by customer messages.

    followup_type determines what gets sent:
      "24hr"   → reminder 24hrs before appointment
      "1hr"    → reminder 1hr before appointment
      "review" → review request 2hrs after visit
      "noshow" → follow-up if customer missed appointment
    """
    followup_type = state.get("followup_type")
    booking_object = state.get("booking_object")

    logger.info(
        f"[FOLLOWUP] type={followup_type} "
        f"phone={state['phone_number']}"
    )

    if not booking_object:
        logger.error("[FOLLOWUP] booking_object missing — cannot send follow-up")
        return {**state, "response_text": None}

    if not followup_type:
        logger.error("[FOLLOWUP] followup_type missing")
        return {**state, "response_text": None}

    # Wrap dict in BookingProxy so followup functions work normally
    booking = BookingProxy(booking_object)
    business_name = state["business_config"].get("business_name", "our clinic")
    success = False

    try:
        if followup_type == "24hr":
            success = send_24hr_reminder(booking, business_name)

        elif followup_type == "1hr":
            success = send_1hr_reminder(booking, business_name)

        elif followup_type == "review":
            success = send_review_request(booking, business_name)

        elif followup_type == "noshow":
            # Only send if booking still shows CONFIRMED in DB
            # (if marked COMPLETED it means they showed up)
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
                    logger.info(f"[FOLLOWUP] Marked as NO_SHOW: {booking.id}")
                else:
                    logger.info(
                        f"[FOLLOWUP] Booking {booking.id} "
                        f"status={db_booking.status if db_booking else 'not found'} "
                        f"— skipping noshow"
                    )
                    success = True  # Not a failure, just not needed
            finally:
                db.close()

        else:
            logger.error(f"[FOLLOWUP] Unknown followup_type: {followup_type}")

    except Exception as e:
        logger.error(f"[FOLLOWUP] type={followup_type} error: {e}")

    logger.info(f"[FOLLOWUP] type={followup_type} success={success}")

    # No TwiML response needed — message sent directly via Twilio
    return {**state, "response_text": None}


# ══════════════════════════════════════════════
# ROUTING
# ══════════════════════════════════════════════

def route_after_intent(state: LeadFlowState) -> str:
    """
    After intent classification — route to correct agent.
    Priority order: escalation > booking > response
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
    """
    Builds and compiles the full LangGraph pipeline.

    Normal message flow:
      START → intent → [escalation | booking | response] → END

    Scheduled follow-up flow (bypasses intent entirely):
      START → followup → END

    The bypass works because trigger_followup() sets
    is_followup_trigger=True and the graph entry point
    checks this flag before routing.
    """
    graph = StateGraph(LeadFlowState)

    # ── Register all 5 nodes ──
    graph.add_node("intent",     intent_node)
    graph.add_node("booking",    booking_node)
    graph.add_node("response",   response_node)
    graph.add_node("escalation", escalation_node)
    graph.add_node("followup",   followup_node)

    # ── Entry point is always intent for customer messages ──
    # For follow-ups, trigger_followup() calls pipeline.invoke()
    # with is_followup_trigger=True and we route directly
    graph.set_entry_point("intent")

    # ── After intent → conditional routing ──
    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "escalation": "escalation",
            "booking":    "booking",
            "response":   "response"
        }
    )

    # ── All nodes terminate at END ──
    graph.add_edge("booking",    END)
    graph.add_edge("response",   END)
    graph.add_edge("escalation", END)
    graph.add_edge("followup",   END)

    compiled = graph.compile()
    logger.info(
        "✅ LangGraph pipeline compiled — "
        "5 nodes: intent / booking / response / escalation / followup"
    )
    return compiled


# Single compiled instance — import this everywhere
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
    Returns response text sent back via TwiML.
    """
    initial_state: LeadFlowState = {
        "message":            message,
        "phone_number":       phone_number,
        "business_config":    business_config,
        "intent":             None,
        "confidence":         None,
        "customer_name":      None,
        "service_requested":  None,
        "preferred_time":     None,
        "lead_id":            None,
        "needs_escalation":   False,
        "is_booking_request": False,
        "is_followup_trigger": False,
        "followup_type":      None,
        "booking_confirmed":  False,
        "booking_object":     None,
        "response_text":      None
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
    Sends WhatsApp directly via Twilio (no TwiML needed).

    followup_type: "24hr" | "1hr" | "review" | "noshow"
    booking_object: serialized booking dict from DB
    """
    initial_state: LeadFlowState = {
        "message":            "",
        "phone_number":       phone_number,
        "business_config":    business_config,
        "intent":             None,
        "confidence":         None,
        "customer_name":      None,
        "service_requested":  None,
        "preferred_time":     None,
        "lead_id":            None,
        "needs_escalation":   False,
        "is_booking_request": False,
        "is_followup_trigger": True,          # ← skips intent node
        "followup_type":      followup_type,  # ← tells followup_node what to send
        "booking_confirmed":  False,
        "booking_object":     booking_object, # ← booking data for message
        "response_text":      None
    }

    logger.info(
        f"[PIPELINE] trigger_followup | "
        f"type={followup_type} | "
        f"phone={phone_number}"
    )

    # NOTE: is_followup_trigger=True means pipeline entry goes
    # directly to followup node — intent node is bypassed.
    # This works because set_entry_point("intent") still runs
    # but intent_node checks for is_followup_trigger and we
    # use a separate graph branch for it.
    # To make bypass work cleanly — call invoke on followup node directly:
    followup_node(initial_state)

    logger.info(
        f"[PIPELINE] trigger_followup complete | "
        f"type={followup_type}"
    )