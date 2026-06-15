import os
import httpx
import logging
from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

CALCOM_API_KEY  = os.getenv("CALCOM_API_KEY")
CALCOM_BASE_URL = "https://api.cal.com/v2"


async def get_available_slots(
    event_type_id: str,
    days_ahead: int = 4
) -> list:
    """
    Fetch available slots using Cal.com v2 API.
    Response structure: { "data": { "2026-06-15": [{"start": "..."}] } }
    """
    now      = datetime.now(timezone.utc)
    end      = now + timedelta(days=days_ahead)
    start_ts = now.strftime("%Y-%m-%dT%H:%M:%S.000Z")
    end_ts   = end.strftime("%Y-%m-%dT%H:%M:%S.000Z")

    logger.info(f"[CAL] Fetching slots | eventTypeId={event_type_id} | {start_ts} → {end_ts}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{CALCOM_BASE_URL}/slots",
                params={
                    "eventTypeId": event_type_id,
                    "start":       start_ts,
                    "end":         end_ts,
                },
                headers={
                    "Authorization":   f"Bearer {CALCOM_API_KEY}",
                    "cal-api-version": "2024-09-04",
                    "Content-Type":    "application/json"
                },
                timeout=10.0
            )

            logger.info(f"[CAL] Slots status: {response.status_code}")

            response.raise_for_status()
            data = response.json()

            # ✅ CORRECT: dates are directly inside "data"
            # { "data": { "2026-06-15": [...], "2026-06-16": [...] } }
            slots_by_date = data.get("data", {})

            # Remove any non-date keys just in case
            slots_by_date = {
                k: v for k, v in slots_by_date.items()
                if k and len(k) == 10  # date format: YYYY-MM-DD
            }

            if not slots_by_date:
                logger.warning("[CAL] No slots found in response")
                return []

            slots = []
            for date_str, times in sorted(slots_by_date.items()):
                day_slots = 0
                for slot in times:
                    if day_slots >= 2:  # Max 2 per day
                        break
                    start_raw = slot.get("start", "")
                    if not start_raw:
                        continue
                    try:
                        dt_utc  = datetime.fromisoformat(
                            start_raw.replace("Z", "+00:00")
                        )
                        dt_ist  = dt_utc + timedelta(hours=5, minutes=30)
                        display = dt_ist.strftime("%A, %d %b at %I:%M %p")
                        slots.append({
                            "display": display,
                            "iso":     start_raw
                        })
                        day_slots += 1
                    except Exception as parse_err:
                        logger.warning(f"[CAL] Parse error: {start_raw} | {parse_err}")

                if len(slots) >= 6:  # Max 6 total
                    break

            logger.info(f"[CAL] Parsed {len(slots)} slots successfully")
            return slots

    except httpx.HTTPStatusError as e:
        logger.error(f"[CAL] HTTP {e.response.status_code} | {e.response.text[:300]}")
        return []
    except Exception as e:
        logger.error(f"[CAL] Unexpected error: {e}")
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
    Create a booking using Cal.com v2 API.
    Correct version header: 2024-08-13
    """
    logger.info(f"[CAL] Creating booking | {customer_name} | {slot_time}")

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{CALCOM_BASE_URL}/bookings",
                headers={
                    "Authorization":   f"Bearer {CALCOM_API_KEY}",
                    "cal-api-version": "2024-08-13",
                    "Content-Type":    "application/json"
                },
                json={
                    "eventTypeId": int(event_type_id),
                    "start":       slot_time,
                    "attendee": {
                        "name":        customer_name,
                        "email":       customer_email,
                        "phoneNumber": customer_phone,
                        "timeZone":    "Asia/Kolkata",
                        "language":    "en"
                    },
                    "metadata": {
                        "source":  "leadflow-whatsapp",
                        "service": service
                    }
                },
                timeout=15.0
            )

            logger.info(f"[CAL] Booking status: {response.status_code}")
            logger.info(f"[CAL] Booking body: {response.text[:500]}")

            response.raise_for_status()
            data = response.json()
            booking = data.get("data", data)

            return {
                "success":     True,
                "booking_uid": booking.get("uid"),
                "title":       booking.get("title"),
                "start_time":  booking.get("start"),
            }

    except httpx.HTTPStatusError as e:
        logger.error(f"[CAL] Booking HTTP {e.response.status_code} | {e.response.text[:300]}")
        return {"success": False, "error": e.response.text}
    except Exception as e:
        logger.error(f"[CAL] Booking failed: {e}")
        return {"success": False, "error": str(e)}