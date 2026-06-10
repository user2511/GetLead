import os
import httpx
import logging
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

CALCOM_API_KEY = os.getenv("CALCOM_API_KEY")
CALCOM_BASE_URL = "https://api.cal.com/v1"

async def get_available_slots(
    event_type_id: str,
    days_ahead: int = 3
) -> list:
    """
    Fetch available appointment slots from Cal.com.
    Returns list of formatted slot strings.
    """
    start_date = datetime.now().strftime("%Y-%m-%d")
    end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CALCOM_BASE_URL}/slots",
                params={
                    "apiKey": CALCOM_API_KEY,
                    "eventTypeId": event_type_id,
                    "startTime": start_date,
                    "endTime": end_date,
                    "timeZone": "Asia/Kolkata"
                },
                timeout=10.0
            )
            response.raise_for_status()
            data = response.json()

            # Parse slots into readable format
            slots = []
            slot_data = data.get("slots", {})

            for date, times in slot_data.items():
                for slot in times[:3]:  # Max 3 per day to keep WhatsApp clean
                    dt = datetime.fromisoformat(slot["time"].replace("Z", "+00:00"))
                    # Convert to IST
                    ist_time = dt + timedelta(hours=5, minutes=30)
                    formatted = ist_time.strftime("%A, %d %b at %I:%M %p")
                    slots.append({
                        "display": formatted,
                        "iso": slot["time"]
                    })

            logger.info(f"Found {len(slots)} available slots")
            return slots[:6]  # Max 6 slots total

    except Exception as e:
        logger.error(f"Cal.com slots fetch failed: {e}")
        return []

async def create_booking(
    event_type_id: str,
    customer_name: str,
    customer_email: str,
    customer_phone: str,
    slot_time: str,
    service: str = "General Appointment"
) -> dict:
    """
    Create a booking on Cal.com.
    Returns booking confirmation details.
    """
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CALCOM_BASE_URL}/bookings",
                params={"apiKey": CALCOM_API_KEY},
                json={
                    "eventTypeId": int(event_type_id),
                    "start": slot_time,
                    "responses": {
                        "name": customer_name,
                        "email": customer_email,
                        "phone": customer_phone,
                        "notes": f"Service requested: {service}"
                    },
                    "timeZone": "Asia/Kolkata",
                    "language": "en",
                    "metadata": {
                        "source": "leadflow-whatsapp"
                    }
                },
                timeout=15.0
            )
            response.raise_for_status()
            booking = response.json()

            logger.info(f"Booking created: {booking.get('uid')}")
            return {
                "success": True,
                "booking_uid": booking.get("uid"),
                "title": booking.get("title"),
                "start_time": booking.get("startTime"),
                "meeting_link": booking.get("meetingUrl")
            }

    except Exception as e:
        logger.error(f"Cal.com booking creation failed: {e}")
        return {"success": False, "error": str(e)}