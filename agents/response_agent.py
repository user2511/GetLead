import logging
from services.groq_service import call_groq
from monitoring.langfuse_setup import get_langfuse
from models.config import BusinessConfig

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

def generate_response(
    intent_result: dict,
    message: str,
    config: BusinessConfig,
    conversation_history: list = []
) -> str:
    """
    Generate WhatsApp reply based on classified intent.
    """

    services_list = "\n".join([f"- {s}" for s in config.services])
    hours = config.working_hours

    system_prompt = f"""You are a friendly WhatsApp assistant for {config.business_name}.
    
Business type: {config.business_type}
Available services:
{services_list}

Working hours:
Monday: {hours.monday or 'CLOSED'}
Tuesday: {hours.tuesday or 'CLOSED'}
Wednesday: {hours.wednesday or 'CLOSED'}
Thursday: {hours.thursday or 'CLOSED'}
Friday: {hours.friday or 'CLOSED'}
Saturday: {hours.saturday or 'CLOSED'}
Sunday: {hours.sunday or 'CLOSED'}

RULES:
- Be warm, friendly, and concise
- Use simple language, avoid jargon
- Use relevant emojis sparingly
- Never make up information
- For bookings: collect name, service, preferred time
- Keep responses under 100 words
- If asking for info, ask ONE question at a time
- Always end with a helpful next step

RESCHEDULE: Tell them you can help reschedule and ask them 
to share their booking reference or the date they booked for.
Then offer to start a fresh booking for a new slot.

CANCEL: Apologize and confirm their appointment has been 
noted for cancellation. Ask them to call directly to confirm
cancellation or offer to connect them with the team."""

    detected_intent = intent_result.get("intent", "UNKNOWN")
    customer_name = intent_result.get("customer_name")
    service = intent_result.get("service_requested")

    # Build context-aware user prompt
    context = f"Detected intent: {detected_intent}\n"
    if customer_name:
        context += f"Customer name: {customer_name}\n"
    if service:
        context += f"Service requested: {service}\n"
    context += f"Customer message: {message}"

    trace = langfuse.trace(
        name="response-generation",
        input={"intent": detected_intent, "message": message}
    )

    try:
        response = call_groq(
            system_prompt=system_prompt,
            user_message=context,
            temperature=0.7,  # Higher for natural responses
            max_tokens=300
        )

        trace.update(output={"response": response})
        return response

    except Exception as e:
        logger.error(f"Response generation failed: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return f"Hi! Thanks for reaching out to {config.business_name}. We'll get back to you shortly!"

def get_main_menu(business_name: str) -> str:
    return (
        f"Hi! Welcome to *{business_name}* 👋\n\n"
        f"How can I help you today?\n\n"
        f"1️⃣ Book Appointment\n"
        f"2️⃣ Reschedule Appointment\n"
        f"3️⃣ Cancel Appointment\n"
        f"4️⃣ Emergency / Urgent Help\n"
        f"5️⃣ FAQ / General Questions\n\n"
        f"_Reply with a number (1-5)_"
    )

def get_faq_menu() -> str:
    return (
        f"*Frequently Asked Questions* 📋\n\n"
        f"1️⃣ What are your timings?\n"
        f"2️⃣ What services do you offer?\n"
        f"3️⃣ Where are you located?\n"
        f"4️⃣ How much does it cost?\n"
        f"5️⃣ Do you accept walk-ins?\n"
        f"0️⃣ Back to main menu\n\n"
        f"_Reply with a number_"
    )        