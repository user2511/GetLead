import logging
import asyncio
from services.cal_service import get_available_slots, create_booking
from services.groq_service import call_groq
from monitoring.langfuse_setup import get_langfuse
from models.config import BusinessConfig
from services.db_service import SessionLocal, create_booking
from services.scheduler_service import schedule_booking_reminders
from agents.escalation_agent import notify_owner_escalation
from datetime import datetime

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

# In-memory conversation state
# In production → store in Redis or PostgreSQL
booking_sessions = {}

def get_or_create_session(phone_number: str) -> dict:
    if phone_number not in booking_sessions:
        booking_sessions[phone_number] = {
            "step": "ASK_NAME",       # ASK_NAME → ASK_SERVICE → SHOW_SLOTS → CONFIRM → DONE
            "name": None,
            "service": None,
            "email": None,
            "selected_slot": None,
            "available_slots": []
        }
    return booking_sessions[phone_number]

def clear_session(phone_number: str):
    if phone_number in booking_sessions:
        del booking_sessions[phone_number]

async def handle_booking_flow(
    message: str,
    phone_number: str,
    config: BusinessConfig,
    intent_result: dict
) -> str:
    """
    Multi-turn booking conversation handler.
    Guides customer through: name → service → slot → confirm
    """
    session = get_or_create_session(phone_number)
    step = session["step"]

    # Pre-fill from intent extraction if available
    if intent_result.get("customer_name") and not session["name"]:
        session["name"] = intent_result["customer_name"]
        session["step"] = "ASK_SERVICE"
        step = "ASK_SERVICE"

    if intent_result.get("service_requested") and not session["service"]:
        session["service"] = intent_result["service_requested"]

    trace = langfuse.trace(
        name="booking-flow",
        input={"step": step, "message": message, "phone": phone_number}
    )

    # ---- Step: Ask for name ----
    if step == "ASK_NAME":
        session["step"] = "ASK_SERVICE"
        response = f"I'd love to help you book an appointment! 😊\nCould I get your name first?"
        trace.update(output={"response": response, "next_step": "ASK_SERVICE"})
        return response

    # ---- Step: Got name, ask for service ----
    elif step == "ASK_SERVICE":
        # Extract name from message if not already set
        if not session["name"]:
            session["name"] = message.strip().split()[0].capitalize()

        services_text = "\n".join([f"{i+1}. {s}" for i, s in enumerate(config.services)])
        session["step"] = "SHOW_SLOTS"
        response = f"Nice to meet you, {session['name']}! 👋\n\nWhich service do you need?\n\n{services_text}\n\nJust reply with the number or name."
        trace.update(output={"response": response, "next_step": "SHOW_SLOTS"})
        return response

    # ---- Step: Got service, show slots ----
    elif step == "SHOW_SLOTS":
        # Save service selection
        if not session["service"]:
            # Try to match by number or name
            services = config.services
            try:
                idx = int(message.strip()) - 1
                session["service"] = services[idx]
            except (ValueError, IndexError):
                session["service"] = message.strip().capitalize()

        # Fetch real slots from Cal.com
        slots = await get_available_slots(
            event_type_id=config.calcom_event_type_id,
            days_ahead=4
        )

        if not slots:
            # Fallback if Cal.com has no slots
            response = (
                f"Let me check availability for {session['service']}. "
                f"Our team will confirm a slot for you shortly! "
                f"What time generally works best for you? Morning or afternoon?"
            )
            trace.update(output={"response": response, "slots": "none available"})
            return response

        session["available_slots"] = slots
        session["step"] = "CONFIRM_SLOT"

        slots_text = "\n".join([
            f"{i+1}. {slot['display']}"
            for i, slot in enumerate(slots)
        ])

        response = f"Great choice! Here are available slots for *{session['service']}*:\n\n{slots_text}\n\nWhich slot works for you? Reply with the number."
        trace.update(output={"response": response, "slots_shown": len(slots)})
        return response

    # ---- Step: Confirm slot ----
    elif step == "CONFIRM_SLOT":
        try:
            slot_idx = int(message.strip()) - 1
            selected = session["available_slots"][slot_idx]
            session["selected_slot"] = selected

            # Ask for email to complete Cal.com booking
            session["step"] = "GET_EMAIL"
            response = (
                f"Perfect! I'll book *{session['service']}* for you on:\n"
                f"📅 *{selected['display']}*\n\n"
                f"Could you share your email address for the confirmation?"
            )
            trace.update(output={"response": response, "selected_slot": selected['display']})
            return response

        except (ValueError, IndexError):
            response = "Please reply with just the number of the slot you'd like (e.g., 1, 2, or 3)"
            trace.update(output={"response": response, "error": "invalid slot selection"})
            return response

    # ---- Step: Get email and create booking ----
    elif step == "GET_EMAIL":
        email = message.strip()

        # Basic email validation
        if "@" not in email:
            return "Please share a valid email address (e.g., yourname@gmail.com)"

        session["email"] = email

        # Create actual booking on Cal.com
        booking_result = await create_booking(
            event_type_id=config.calcom_event_type_id,
            customer_name=session["name"],
            customer_email=email,
            customer_phone=phone_number,
            slot_time=session["selected_slot"]["iso"],
            service=session["service"]
        )

 

        # Replace the successful booking section in GET_EMAIL step:
        if booking_result["success"]:
            # Save to database
            db = SessionLocal()
            try:
                from datetime import datetime as dt
                scheduled_dt = dt.fromisoformat(
                session["selected_slot"]["iso"].replace("Z", "+00:00")
                ).replace(tzinfo=None)

                db_booking = create_booking(db, {
                "lead_id": "temp",  # Update with real lead_id from webhook
                "business_id": config.business_id,
                "customer_name": session["name"],
                "customer_phone": phone_number,
                "service": session["service"],
                "scheduled_at": scheduled_dt,
                "calcom_booking_uid": booking_result.get("booking_uid"),
                "status": "CONFIRMED"
            })

                # Schedule all reminders automatically
                schedule_booking_reminders(db_booking)

            finally:
                db.close()

            session["step"] = "DONE"
            clear_session(phone_number)

            response = (
        f"*Booking Confirmed!*\n\n"
        f"Name: {session['name']}\n"
        f"Service: {session['service']}\n"
        f"Date: {session['selected_slot']['display']}\n\n"
        f"Confirmation sent to {email}.\n"
        f"You'll get a reminder 24hrs and 1hr before. See you soon! "
    )
        else:
            response = (
                f"I've noted your preferred slot but had trouble confirming automatically. "
                f"Our team will call you within 30 minutes to confirm your {session['service']} "
                f"appointment. Sorry for the inconvenience!"
            )

        trace.update(output={"response": response, "booking_success": booking_result["success"]})
        return response

    # Fallback
    return f"I'm here to help you book at {config.business_name}. Would you like to schedule an appointment?"