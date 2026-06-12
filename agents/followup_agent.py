import logging
from services.twilio_service import send_whatsapp
from monitoring.langfuse_setup import get_langfuse

logger = logging.getLogger(__name__)
langfuse = get_langfuse()


# ─────────────────────────────────────────
# These functions receive a BookingProxy
# object (defined in pipeline.py).
# They just send WhatsApp messages.
# ─────────────────────────────────────────

def send_24hr_reminder(booking, business_name: str = "our clinic") -> bool:
    """Send reminder 24 hours before appointment"""
    trace = langfuse.trace(
        name="followup-24hr-reminder",
        input={
            "booking_id": booking.id,
            "customer": booking.customer_name
        }
    )
    try:
        message = (
            f"Hi {booking.customer_name}! 👋\n\n"
            f"Just a reminder about your appointment at "
            f"*{business_name}* tomorrow:\n\n"
            f"🩺 Service: *{booking.service}*\n"
            f"📅 Time: *{booking.scheduled_at.strftime('%A, %d %b at %I:%M %p')}*\n\n"
            f"Reply *CONFIRM* to confirm or *CANCEL* to cancel.\n"
            f"See you soon! 😊"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        logger.info(f"[FOLLOWUP] 24hr reminder → {booking.customer_phone} | sent={success}")
        return success

    except Exception as e:
        logger.error(f"[FOLLOWUP] 24hr reminder error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False


def send_1hr_reminder(booking, business_name: str = "our clinic") -> bool:
    """Send reminder 1 hour before appointment"""
    trace = langfuse.trace(
        name="followup-1hr-reminder",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"⏰ Your appointment at *{business_name}* "
            f"is in *1 hour!*\n\n"
            f"📅 {booking.scheduled_at.strftime('%I:%M %p')}\n"
            f"🩺 {booking.service}\n\n"
            f"Please arrive 5 minutes early. See you soon! 👋"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        logger.info(f"[FOLLOWUP] 1hr reminder → {booking.customer_phone} | sent={success}")
        return success

    except Exception as e:
        logger.error(f"[FOLLOWUP] 1hr reminder error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False


def send_review_request(booking, business_name: str = "our clinic") -> bool:
    """Send Google review request 2 hours after appointment"""
    trace = langfuse.trace(
        name="followup-review-request",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"Hi {booking.customer_name}! 😊\n\n"
            f"Hope your *{booking.service}* appointment went well!\n\n"
            f"We'd love your feedback. Could you take 30 seconds "
            f"to leave us a Google review? It helps us a lot! 🙏\n\n"
            f"⭐ https://g.page/r/YOUR_GOOGLE_REVIEW_LINK\n\n"
            f"Thank you! — Team {business_name}"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        logger.info(f"[FOLLOWUP] Review request → {booking.customer_phone} | sent={success}")
        return success

    except Exception as e:
        logger.error(f"[FOLLOWUP] Review request error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False


def send_noshow_followup(booking, business_name: str = "our clinic") -> bool:
    """Follow up if customer missed their appointment"""
    trace = langfuse.trace(
        name="followup-noshow",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"Hi {booking.customer_name}, we missed you today! 😢\n\n"
            f"Your *{booking.service}* appointment was at "
            f"{booking.scheduled_at.strftime('%I:%M %p')}.\n\n"
            f"Would you like to reschedule? "
            f"Just reply *YES* and I'll find you a new slot right away! 📅"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        logger.info(f"[FOLLOWUP] No-show followup → {booking.customer_phone} | sent={success}")
        return success

    except Exception as e:
        logger.error(f"[FOLLOWUP] No-show followup error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False