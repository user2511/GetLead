import logging
import asyncio
from typing import TypedDict, Optional
from langgraph.graph import StateGraph, END
from models.config import BusinessConfig
from agents.intent_agent import classify_intent
from agents.response_agent import generate_response
from agents.booking_agent import handle_booking_flow

logger = logging.getLogger(__name__)

class LeadFlowState(TypedDict):
    message: str
    phone_number: str
    business_config: dict
    intent: Optional[str]
    confidence: Optional[float]
    customer_name: Optional[str]
    service_requested: Optional[str]
    preferred_time: Optional[str]
    response_text: Optional[str]
    needs_escalation: bool
    is_booking_request: bool

def intent_node(state: LeadFlowState) -> LeadFlowState:
    config_dict = state["business_config"]
    result = classify_intent(
        message=state["message"],
        business_name=config_dict.get("business_name", "our business")
    )
    escalation_keywords = config_dict.get("escalation_keywords", [])
    needs_escalation = (
        result.get("intent") == "EMERGENCY" or
        any(kw.lower() in state["message"].lower() for kw in escalation_keywords)
    )
    return {
        **state,
        "intent": result.get("intent"),
        "confidence": result.get("confidence"),
        "customer_name": result.get("customer_name"),
        "service_requested": result.get("service_requested"),
        "preferred_time": result.get("preferred_time"),
        "needs_escalation": needs_escalation,
        "is_booking_request": result.get("intent") == "BOOK_APPOINTMENT"
    }

def booking_node(state: LeadFlowState) -> LeadFlowState:
    logger.info(f"Booking node | phone: {state['phone_number']}")
    config = BusinessConfig(**state["business_config"])
    intent_result = {
        "intent": state["intent"],
        "customer_name": state["customer_name"],
        "service_requested": state["service_requested"]
    }
    # Run async booking flow
    response = asyncio.get_event_loop().run_until_complete(
        handle_booking_flow(
            message=state["message"],
            phone_number=state["phone_number"],
            config=config,
            intent_result=intent_result
        )
    )
    return {**state, "response_text": response}

def response_node(state: LeadFlowState) -> LeadFlowState:
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
    return {**state, "response_text": response}

def escalation_node(state: LeadFlowState) -> LeadFlowState:
    logger.warning(f"🚨 Escalation | phone: {state['phone_number']}")
    config_dict = state["business_config"]
    response = (
        f"This sounds urgent! I'm alerting the "
        f"{config_dict['business_name']} team right now. "
        f"Someone will contact you within minutes. "
        f"For emergencies please call 112."
    )
    return {**state, "response_text": response}

def route_after_intent(state: LeadFlowState) -> str:
    if state.get("needs_escalation"):
        return "escalation"
    if state.get("is_booking_request"):
        return "booking"
    return "response"

def build_pipeline() -> StateGraph:
    graph = StateGraph(LeadFlowState)
    graph.add_node("intent", intent_node)
    graph.add_node("booking", booking_node)
    graph.add_node("response", response_node)
    graph.add_node("escalation", escalation_node)
    graph.set_entry_point("intent")
    graph.add_conditional_edges(
        "intent",
        route_after_intent,
        {
            "booking": "booking",
            "response": "response",
            "escalation": "escalation"
        }
    )
    graph.add_edge("booking", END)
    graph.add_edge("response", END)
    graph.add_edge("escalation", END)
    return graph.compile()

pipeline = build_pipeline()

def process_message(message: str, phone_number: str, business_config: dict) -> str:
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
        "is_booking_request": False
    }
    result = pipeline.invoke(initial_state)
    return result.get("response_text", "Thanks for reaching out! We'll be in touch shortly.")