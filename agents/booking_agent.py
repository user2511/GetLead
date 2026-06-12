import logging
from typing import Optional, Tuple
from services.cal_service import get_available_slots, create_booking
from monitoring.langfuse_setup import get_langfuse
from models.config import BusinessConfig

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

# ─────────────────────────────────────────
# In-memory session store
# Tracks multi-turn conversation per phone
# In production → replace with Redis
# ─────────────────────────────────────────
booking_sessions: dict = {}


def get_or_create_session(phone_number: str) -> dict:
    if phone_number not in booking_sessions:
        booking_sessions[phone_number] = {
            "step": "ASK_NAME",
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
    intent_result: dict,
    lead_id: str = None
) -> Tuple[str, Optional[dict]]:
    """
    Multi-turn booking conversation.

    Returns:
        (response_text, booking_data_dict)
        booking_data_dict is only set when booking is JUST confirmed.
        All other steps return (response_text, None)

    Steps:
        ASK_NAME → ASK_SERVICE → SHOW_SLOTS → CONFIRM_SLOT → GET_EMAIL → DONE
    """
    session = get_or_create_session(phone_number)
    step = session["step"]

    # ── Pre-fill from intent extraction if available ──
    if intent_result.get("customer_name") and not session["name"]:
        session["name"] = intent_result["customer_name"]
        if step == "ASK_NAME":
            step = "ASK_SERVICE"
            session["step"] = "ASK_SERVICE"

    if intent_result.get("service_requested") and not session["service"]:
        session["service"] = intent_result["service_requested"]

    trace = langfuse.trace(
        name="booking-flow",
        input={
            "step": step,
            "message": message,
            "phone": phone_number
        }
    )

    # ────────────────────────────
    # STEP 1 — Ask for name
    # ────────────────────────────
    if step == "ASK_NAME":
        session["step"] = "ASK_SERVICE"
        response = (
            f"I'd love to help you book an appointment! 😊\n"
            f"Could I get your name first?"
        )
        trace.update(output={"response": response, "next_step": "ASK_SERVICE"})
        return response, None

    # ────────────────────────────
    # STEP 2 — Got name, ask service
    # ────────────────────────────
    elif step == "ASK_SERVICE":
        if not session["name"]:
            # Extract name from current message
            session["name"] = message.strip().split()[0].capitalize()

        services_text = "\n".join([
            f"{i+1}. {s}"
            for i, s in enumerate(config.services)
        ])
        session["step"] = "SHOW_SLOTS"
        response = (
            f"Nice to meet you, {session['name']}! 👋\n\n"
            f"Which service do you need?\n\n"
            f"{services_text}\n\n"
            f"Just reply with the number or name."
        )
        trace.update(output={"response": response, "next_step": "SHOW_SLOTS"})
        return response, None

    # ────────────────────────────
    # STEP 3 — Got service, show slots
    # ────────────────────────────
    elif step == "SHOW_SLOTS":
        # Match service by number or text
        if not session["service"]:
            services = config.services
            try:
                idx = int(message.strip()) - 1
                if 0 <= idx < len(services):
                    session["service"] = services[idx]
                else:
                    session["service"] = message.strip().capitalize()
            except ValueError:
                session["service"] = message.strip().capitalize()

        # Fetch real slots from Cal.com
        slots = await get_available_slots(
            event_type_id=config.calcom_event_type_id,
            days_ahead=4
        )

        if not slots:
            response = (
                f"Let me check availability for *{session['service']}*.\n\n"
                f"Our team will confirm a slot for you shortly! "
                f"What time generally works best — morning or afternoon?"
            )
            trace.update(output={"response": response, "slots": "none"})
            return response, None

        session["available_slots"] = slots
        session["step"] = "CONFIRM_SLOT"

        slots_text = "\n".join([
            f"{i+1}. {slot['display']}"
            for i, slot in enumerate(slots)
        ])

        response = (
            f"Great! Here are available slots for "
            f"*{session['service']}*:\n\n"
            f"{slots_text}\n\n"
            f"Which slot works for you? Reply with the number."
        )
        trace.update(output={"response": response, "slots_shown": len(slots)})
        return response, None

    # ────────────────────────────
    # STEP 4 — Confirm slot
    # ────────────────────────────
    elif step == "CONFIRM_SLOT":
        try:
            slot_idx = int(message.strip()) - 1
            selected = session["available_slots"][slot_idx]
            session["selected_slot"] = selected
            session["step"] = "GET_EMAIL"

            response = (
                f"Perfect! I'll book *{session['service']}* on:\n"
                f"📅 *{selected['display']}*\n\n"
                f"Could you share your email for the confirmation?"
            )
            trace.update(output={"response": response, "slot": selected["display"]})
            return response, None

        except (ValueError, IndexError):
            response = (
                f"Please reply with just the slot number "
                f"(e.g. 1, 2 or 3) 😊"
            )
            trace.update(output={"response": response, "error": "invalid slot"})
            return response, None

    # ────────────────────────────
    # STEP 5 — Get email + confirm
    # ────────────────────────────
    elif step == "GET_EMAIL":
        email = message.strip()

        if "@" not in email or "." not in email:
            return "Please share a valid email (e.g. yourname@gmail.com) 😊", None

        session["email"] = email

        # Create booking on Cal.com
        booking_result = await create_booking(
            event_type_id=config.calcom_event_type_id,
            customer_name=session["name"],
            customer_email=email,
            customer_phone=phone_number,
            slot_time=session["selected_slot"]["iso"],
            service=session["service"]
        )

        if booking_result["success"]:
            # Build booking data dict for pipeline to save
            from datetime import datetime
            scheduled_dt = datetime.fromisoformat(
                session["selected_slot"]["iso"].replace("Z", "+00:00")
            ).replace(tzinfo=None)

            booking_data = {
                "customer_name": session["name"],
                "service": session["service"],
                "scheduled_at": scheduled_dt,
                "calcom_booking_uid": booking_result.get("booking_uid")
            }

            response = (
                f"✅ *Booking Confirmed!*\n\n"
                f"👤 Name: {session['name']}\n"
                f"🩺 Service: {session['service']}\n"
                f"📅 {session['selected_slot']['display']}\n\n"
                f"Confirmation sent to {email}.\n"
                f"You'll get reminders before your appointment. "
                f"See you soon! 😊"
            )

            clear_session(phone_number)
            trace.update(output={"response": response, "booking": "confirmed"})
            return response, booking_data   # ← booking_data triggers DB save

        else:
            response = (
                f"I noted your slot but had trouble confirming automatically.\n"
                f"Our team will call you within 30 minutes to confirm. "
                f"Sorry for the inconvenience!"
            )
            trace.update(output={"response": response, "booking": "failed"})
            return response, None

    # ── Fallback ──
    response = (
        f"I'm here to help you book at {config.business_name}. "
        f"Would you like to schedule an appointment?"
    )
    return response, None