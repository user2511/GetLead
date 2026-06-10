import json
import logging
from services.groq_service import call_groq
from monitoring.langfuse_setup import get_langfuse

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

INTENT_SYSTEM_PROMPT = """You are an intent classifier for a local business WhatsApp assistant.

Classify the customer's message into EXACTLY one of these intents:

BOOK_APPOINTMENT   - Customer wants to book, schedule, or make an appointment
RESCHEDULE         - Customer wants to change or reschedule existing appointment  
CANCEL             - Customer wants to cancel their appointment
FAQ                - Customer asking about services, prices, timings, location
GREETING           - Just saying hi, hello, starting conversation
EMERGENCY          - Urgent situation requiring immediate human attention
UNKNOWN            - Cannot determine intent clearly

Also extract:
- customer_name: if mentioned, else null
- service_requested: specific service mentioned, else null
- preferred_time: any time/date preference mentioned, else null

Respond ONLY with valid JSON. No explanation. Example:
{
    "intent": "BOOK_APPOINTMENT",
    "confidence": 0.95,
    "customer_name": "Rahul",
    "service_requested": "teeth cleaning",
    "preferred_time": "tomorrow morning"
}"""

def classify_intent(message: str, business_name: str = "our clinic") -> dict:
    """
    Classify intent of incoming WhatsApp message.
    Returns dict with intent + extracted entities.
    """
    # Start Langfuse trace
    trace = langfuse.trace(
        name="intent-classification",
        input={"message": message, "business": business_name}
    )

    try:
        span = trace.span(name="groq-intent-call")

        raw_response = call_groq(
            system_prompt=INTENT_SYSTEM_PROMPT,
            user_message=f"Business: {business_name}\nCustomer message: {message}",
            temperature=0.1,  # Low temp for classification
            max_tokens=200
        )

        span.end(output=raw_response)

        # Parse JSON response
        result = json.loads(raw_response)

        trace.update(
            output=result,
            metadata={"intent": result.get("intent"), "confidence": result.get("confidence")}
        )

        logger.info(f"Intent: {result.get('intent')} | Confidence: {result.get('confidence')}")
        return result

    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse intent JSON: {e} | Raw: {raw_response}")
        trace.update(output={"error": str(e)}, level="ERROR")
        # Safe fallback
        return {
            "intent": "UNKNOWN",
            "confidence": 0.0,
            "customer_name": None,
            "service_requested": None,
            "preferred_time": None
        }

    except Exception as e:
        logger.error(f"Intent classification failed: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return {
            "intent": "UNKNOWN",
            "confidence": 0.0,
            "customer_name": None,
            "service_requested": None,
            "preferred_time": None
        }