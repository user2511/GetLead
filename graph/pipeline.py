import logging
import asyncio
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
    create_booking,
    get_upcoming_bookings
)
from services.scheduler_service import schedule_booking_reminders

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────
# STATE
# ──────────────────────────────────────────

class LeadFlowState(TypedDict):
    # Input
    message: str
    phone_number: str
    business_config: dict

    # Intent agent output
    intent: Optional[str]
    confidence: Optional[float]
    customer_name: Optional[str]
    service_requested: Optional[str]
    preferred_time: Optional[str]

    # Routing flags
    needs_escalation: bool
    is_booking_request: bool
    is_followup_trigger: bool   # True when scheduler calls pipeline directly
    followup_type: Optional[str]  # "24hr" | "1hr" | "review" | "noshow"

    # Booking result passed between nodes
    booking_confirmed: bool
    booking_object: Optional[dict]  # serialized Booking for scheduler

    # Final output
    response_text: Optional[str]

    # DB lead id (set after lead saved)
    lead_id: Optional[str]

# ──────────────────────────────────────────
# NODE 1 — INTENT CLASSIFIER
# ──────────────────────────────────────────

def intent_node(state: LeadFlowState) -> LeadFlowState:
    """
    Classifies every incoming message.
    Sets routing flags for downstream nodes.
    """
    logger.info(f"[INTENT NODE] phone={state['phone_number']} msg='{state['message'][:60]}'")

    config_dict = state["business_config"]

    result = classify_intent(
        message=state["message"],
        business_name=config_dict.get("business_name", "our business")
    )

    intent = result.get("intent", "UNKNOWN")
    escalation_keywords = config_dict.get("escalation_keywords", [])

    needs_escalation = (
        intent == "EMERGENCY" or
        any(kw.lower() in state["message"].lower() for kw in escalation_keywords)
    )

    # Save lead to DB
    db = SessionLocal()
    lead_id = None
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
            conversation_state="IN_PROGRESS"
        )
        lead_id = lead.id
    finally:
        db.close()

    logger.info(f"[INTENT NODE] intent={intent} escalation={needs_escalation} lead_id={lead_id}")

    return {
        **state,
        "intent": intent,
        "confidence": result.get("confidence"),
        "customer_name": result.get("customer_name"),
        "service_requested": result.get("service_requested"),
        "preferred_time": result.get("preferred_time"),
        "needs_escalation": needs_escalation,
        "is_booking_request": intent == "BOOK_APPOINTMENT" or intent == "RESCHEDULE",
        "lead_id": lead_id
    }

# ──────────────────────────────────────────
# NODE 2 — BOOKING AGENT
# ──────────────────────────────────────────


async def booking_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles full multi-turn booking conversation.
    When booking confirmed → saves to DB + schedules reminders.
    """
    logger.info(f"[BOOKING NODE] phone={state['phone_number']} service={state['service_requested']}")

    config = BusinessConfig(**state["business_config"])

    intent_result = {
        "intent": state["intent"],
        "customer_name": state["customer_name"],
        "service_requested": state["service_requested"],
        "preferred_time": state["preferred_time"]
    }

    # # Run async booking conversation
    # loop = asyncio.new_event_loop()
    # asyncio.set_event_loop(loop)
    # try:
    #     response, booking_data = loop.run_until_complete(
    #         handle_booking_flow(
    #             message=state["message"],
    #             phone_number=state["phone_number"],
    #             config=config,
    #             intent_result=intent_result,
    #             lead_id=state.get("lead_id")
    #         )
    #     )
    # finally:
    #     loop.close()

    

    response, booking_data = await handle_booking_flow(
                message=state["message"],
                phone_number=state["phone_number"],
                config=config,
                intent_result=intent_result,
                lead_id=state.get("lead_id")
            )

        
    booking_confirmed = booking_data is not None

    # If booking just confirmed → save to DB and schedule reminders
    if booking_confirmed and booking_data:
        db = SessionLocal()
        try:
            db_booking = create_booking(db, {
                "lead_id": state.get("lead_id", "unknown"),
                "business_id": config.business_id,
                "customer_name": booking_data["customer_name"],
                "customer_phone": state["phone_number"],
                "service": booking_data["service"],
                "scheduled_at": booking_data["scheduled_at"],
                "calcom_booking_uid": booking_data.get("calcom_booking_uid"),
                "status": "CONFIRMED"
            })

            # Schedule all follow-up reminders
            schedule_booking_reminders(db_booking)

            logger.info(f"[BOOKING NODE] Booking saved + reminders scheduled for {booking_data['customer_name']}")

        finally:
            db.close()

    logger.info(f"[BOOKING NODE] confirmed={booking_confirmed} response='{response[:60]}'")

    return {
        **state,
        "response_text": response,
        "booking_confirmed": booking_confirmed,
        "booking_object": booking_data
    }

# ──────────────────────────────────────────
# NODE 3 — GENERAL RESPONSE AGENT
# ──────────────────────────────────────────

def response_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles FAQ, greetings, cancellations, unknown intents.
    Anything that is NOT booking and NOT escalation.
    """
    logger.info(f"[RESPONSE NODE] intent={state['intent']} phone={state['phone_number']}")

    config = BusinessConfig(**state["business_config"])

    intent_result = {
        "intent": state["intent"],
        "customer_name": state["customer_name"],
        "service_requested": state["service_requested"],
        "preferred_time": state["preferred_time"]
    }

    response = generate_response(
        intent_result=intent_result,
        message=state["message"],
        config=config
    )

    logger.info(f"[RESPONSE NODE] response='{response[:60]}'")

    return {**state, "response_text": response}

# ──────────────────────────────────────────
# NODE 4 — ESCALATION AGENT
# ──────────────────────────────────────────

def escalation_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles emergency/urgent messages.
    1. Sends urgent reply to customer immediately
    2. Notifies business owner on WhatsApp
    Both happen in this node.
    """
    logger.warning(f"[ESCALATION NODE] EMERGENCY phone={state['phone_number']}")

    config_dict = state["business_config"]
    business_name = config_dict.get("business_name", "the clinic")

    # Step 1: Immediate reply to customer
    customer_response = (
        f"🚨 This sounds urgent and I'm treating it as a priority!\n\n"
        f"I've immediately alerted the *{business_name}* team. "
        f"Someone will contact you within the next few minutes.\n\n"
        f"For life-threatening emergencies please call *112* right away."
    )

    # Step 2: Notify business owner
    notify_owner_escalation(
        phone_number=state["phone_number"],
        message=state["message"],
        reason=(
            "EMERGENCY intent detected"
            if state.get("intent") == "EMERGENCY"
            else "Escalation keyword matched in message"
        )
    )

    # Step 3: Update lead status in DB
    if state.get("lead_id"):
        db = SessionLocal()
        try:
            update_lead(
                db=db,
                lead_id=state["lead_id"],
                is_escalated=True,
                conversation_state="ESCALATED"
            )
        finally:
            db.close()

    logger.warning(f"[ESCALATION NODE] Owner notified. Customer response sent.")

    return {**state, "response_text": customer_response}

# ──────────────────────────────────────────
# NODE 5 — FOLLOW-UP DISPATCHER
# ──────────────────────────────────────────

def followup_node(state: LeadFlowState) -> LeadFlowState:
    """
    Handles scheduled follow-up messages.
    Called by APScheduler — not triggered by customer message.
    followup_type determines which message to send:
      "24hr"   → appointment reminder 24hrs before
      "1hr"    → appointment reminder 1hr before
      "review" → review request after visit
      "noshow" → follow-up if customer didn't show
    """
    followup_type = state.get("followup_type")
    booking_object = state.get("booking_object")

    logger.info(f"[FOLLOWUP NODE] type={followup_type} phone={state['phone_number']}")

    if not booking_object:
        logger.error("[FOLLOWUP NODE] No booking_object in state — cannot send follow-up")
        return {**state, "response_text": None}

    # Reconstruct minimal booking-like object from dict
    class BookingProxy:
        def __init__(self, data):
            self.id = data.get("id")
            self.customer_name = data.get("customer_name")
            self.customer_phone = data.get("customer_phone")
            self.service = data.get("service")
            self.status = data.get("status")
            from datetime import datetime
            scheduled_raw = data.get("scheduled_at")
            if isinstance(scheduled_raw, str):
                self.scheduled_at = datetime.fromisoformat(scheduled_raw)
            else:
                self.scheduled_at = scheduled_raw

    booking = BookingProxy(booking_object)
    success = False

    if followup_type == "24hr":
        success = send_24hr_reminder(booking)

    elif followup_type == "1hr":
        success = send_1hr_reminder(booking)

    elif followup_type == "review":
        success = send_review_request(booking)

    elif followup_type == "noshow":
        # First mark booking as no-show in DB
        db = SessionLocal()
        try:
            from models.booking import Booking
            db_booking = db.query(Booking).filter(
                Booking.id == booking.id
            ).first()
            if db_booking and db_booking.status == "CONFIRMED":
                db_booking.status = "NO_SHOW"
                db.commit()
                success = send_noshow_followup(booking)
            else:
                logger.info(f"[FOLLOWUP NODE] Booking {booking.id} already updated — skipping noshow")
        finally:
            db.close()

    else:
        logger.error(f"[FOLLOWUP NODE] Unknown followup_type: {followup_type}")

    logger.info(f"[FOLLOWUP NODE] type={followup_type} success={success}")

    # Follow-up node doesn't send a WhatsApp reply via TwiML
    # It sends directly via Twilio — no response_text needed
    return {**state, "response_text": None}

# ──────────────────────────────────────────
# ROUTING FUNCTIONS
# ──────────────────────────────────────────

def route_entry(state: LeadFlowState) -> str:
    """
    First routing decision after pipeline starts.
    Scheduled follow-ups bypass intent entirely.
    """
    if state.get("is_followup_trigger"):
        return "followup"
    return "intent"

def route_after_intent(state: LeadFlowState) -> str:
    """
    After intent classification — decide which agent handles it.
    Priority: escalation > booking > general response
    """
    if state.get("needs_escalation"):
        return "escalation"
    if state.get("is_booking_request"):
        return "booking"
    return "response"

# ──────────────────────────────────────────
# BUILD GRAPH
# ──────────────────────────────────────────

def build_pipeline() -> StateGraph:
    """
    Full LangGraph pipeline with all 5 agents.

    Flow:
                        START
                          │
              ┌───────────▼───────────┐
              │     route_entry       │
              └──┬──────────────┬─────┘
                 │              │
           (normal)        (scheduled)
                 │              │
              ┌──▼──┐       ┌───▼────┐
              │INTENT│      │FOLLOWUP│──► END
              └──┬───┘       └────────┘
                 │
         ┌───────┼────────┐
         │       │        │
      ┌──▼──┐ ┌──▼───┐ ┌──▼────────┐
      │ESCAL│ │BOOK  │ │RESPONSE   │
      │ATION│ │AGENT │ │AGENT      │
      └──┬──┘ └──┬───┘ └──┬────────┘
         │       │        │
         └───────┴────────┘
                 │
                END
    """
    graph = StateGraph(LeadFlowState)

    # Add all 5 nodes
    graph.add_node("intent",     intent_node)
    graph.add_node("booking",    booking_node)
    graph.add_node("response",   response_node)
    graph.add_node("escalation", escalation_node)
    graph.add_node("followup",   followup_node)

    # Entry routing — followup bypasses intent
    graph.set_entry_point("intent")

    # Conditional routing after intent
    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "escalation": "escalation",
            "booking":    "booking",
            "response":   "response"
        }
    )

    # All terminal nodes go to END
    graph.add_edge("booking",    END)
    graph.add_edge("response",   END)
    graph.add_edge("escalation", END)
    graph.add_edge("followup",   END)

    compiled = graph.compile()
    logger.info("✅ LangGraph pipeline compiled — 5 nodes active")
    return compiled

# Single compiled instance — import this everywhere
pipeline = build_pipeline()

# ──────────────────────────────────────────
# PUBLIC ENTRY POINTS
# ──────────────────────────────────────────

async def process_message(
    message: str,
    phone_number: str,
    business_config: dict
) -> str:
    """
    Entry point for incoming WhatsApp messages.
    Called by webhook on every customer message.
    Returns response text to send back via TwiML.
    """
    initial_state: LeadFlowState = {
        "message": message,
        "phone_number": phone_number,
        "business_config": business_config,
        "intent": None,
        "confidence": None,
        "customer_name": None,
        "service_requested": None,
        "preferred_time": None,
        "response_text": None,
        "needs_escalation": False,
        "is_booking_request": False,
        "is_followup_trigger": False,
        "followup_type": None,
        "booking_confirmed": False,
        "booking_object": None,
        "lead_id": None
    }

    logger.info(f"[PIPELINE] process_message | phone={phone_number}")
    result = await pipeline.ainvoke(initial_state)
    response = result.get(
        "response_text",
        "Thanks for reaching out! We'll get back to you shortly. 😊"
    )
    logger.info(f"[PIPELINE] complete | intent={result.get('intent')} | response='{response[:60]}'")
    return response


async def trigger_followup(
    followup_type: str,
    phone_number: str,
    business_config: dict,
    booking_object: dict
) -> None:
    """
    Entry point for scheduled follow-up messages.
    Called by APScheduler — NOT by customer messages.
    Does not return a TwiML response (sends WhatsApp directly).

    followup_type: "24hr" | "1hr" | "review" | "noshow"
    booking_object: serialized booking dict from DB
    """
    initial_state: LeadFlowState = {
        "message": "",
        "phone_number": phone_number,
        "business_config": business_config,
        "intent": None,
        "confidence": None,
        "customer_name": None,
        "service_requested": None,
        "preferred_time": None,
        "response_text": None,
        "needs_escalation": False,
        "is_booking_request": False,
        "is_followup_trigger": True,       # ← bypasses intent node
        "followup_type": followup_type,    # ← tells followup_node what to send
        "booking_confirmed": False,
        "booking_object": booking_object,  # ← booking data for reminder
        "lead_id": None
    }

    logger.info(f"[PIPELINE] trigger_followup | type={followup_type} phone={phone_number}")
    await pipeline.ainvoke(initial_state)
    logger.info(f"[PIPELINE] followup complete | type={followup_type}")