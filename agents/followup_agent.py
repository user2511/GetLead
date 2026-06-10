import logging
from datetime import datetime, timedelta
from services.twilio_service import send_whatsapp
from services.db_service import SessionLocal, get_upcoming_bookings
from monitoring.langfuse_setup import get_langfuse
from models.booking import Booking
from models.config import BusinessConfig
import json
import os

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

# Load config
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../configs/dental_clinic.json")
business_config = BusinessConfig.from_json_file(CONFIG_PATH)

def send_24hr_reminder(booking: Booking) -> bool:
    """Send reminder 24 hours before appointment"""
    trace = langfuse.trace(
        name="followup-24hr-reminder",
        input={"booking_id": booking.id, "customer": booking.customer_name}
    )
    try:
        message = (
            f"Hi {booking.customer_name}! \n\n"
            f"Just a reminder about your appointment at "
            f"*{business_config.business_name}* tomorrow:\n\n"
            f"Service: {booking.service}\n"
            f"Time: {booking.scheduled_at.strftime('%A, %d %b at %I:%M %p')}\n\n"
            f"Reply *CONFIRM* to confirm or *CANCEL* to cancel.\n"
            f"See you soon! "
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        logger.info(f"24hr reminder {'sent' if success else 'failed'} → {booking.customer_phone}")
        return success
    except Exception as e:
        logger.error(f"24hr reminder error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False

def send_1hr_reminder(booking: Booking) -> bool:
    """Send reminder 1 hour before appointment"""
    trace = langfuse.trace(
        name="followup-1hr-reminder",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"Your appointment at *{business_config.business_name}* "
            f"is in 1 hour!\n\n"
            f"{booking.scheduled_at.strftime('%I:%M %p')}\n"
            f"{booking.service}\n\n"
            f"Please arrive 5 minutes early. See you soon! "
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        return success
    except Exception as e:
        logger.error(f"1hr reminder error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False

def send_review_request(booking: Booking) -> bool:
    """Send review request 2 hours after appointment"""
    trace = langfuse.trace(
        name="followup-review-request",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"Hi {booking.customer_name}! \n\n"
            f"Hope your {booking.service} appointment went well!\n\n"
            f"We'd love to hear your feedback. "
            f"Could you take 30 seconds to leave us a Google review? "
            f"It helps us a lot! 🙏\n\n"
            f"Review us: https://g.page/r/YOUR_GOOGLE_REVIEW_LINK\n\n"
            f"Thank you! — Team {business_config.business_name}"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        return success
    except Exception as e:
        logger.error(f"Review request error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False

def send_noshow_followup(booking: Booking) -> bool:
    """Follow up if customer missed appointment"""
    trace = langfuse.trace(
        name="followup-noshow",
        input={"booking_id": booking.id}
    )
    try:
        message = (
            f"Hi {booking.customer_name}, we missed you today! \n\n"
            f"Your {booking.service} appointment was at "
            f"{booking.scheduled_at.strftime('%I:%M %p')}.\n\n"
            f"Would you like to reschedule? Just reply *YES* "
            f"and we'll find you a new slot right away! 📅"
        )
        success = send_whatsapp(booking.customer_phone, message)
        trace.update(output={"sent": success})
        return success
    except Exception as e:
        logger.error(f"No-show followup error: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False