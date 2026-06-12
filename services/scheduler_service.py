import logging
import os
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from services.db_service import SessionLocal, get_upcoming_bookings
from models.booking import Booking

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../configs/dental_clinic.json")


def _get_business_config() -> dict:
    """Load business config from JSON"""
    import json
    with open(CONFIG_PATH) as f:
        return json.load(f)


def _booking_to_dict(booking: Booking) -> dict:
    """
    Serialize SQLAlchemy Booking object to plain dict.
    Needed because APScheduler passes args by value.
    """
    return {
        "id": booking.id,
        "customer_name": booking.customer_name,
        "customer_phone": booking.customer_phone,
        "service": booking.service,
        "scheduled_at": booking.scheduled_at.isoformat(),
        "status": booking.status,
        "calcom_booking_uid": booking.calcom_booking_uid
    }


def _run_followup_job(followup_type: str, booking_dict: dict):
    """
    Called by APScheduler at scheduled time.
    Routes through the full pipeline so everything is traced.
    """
    # Import here to avoid circular imports at module load
    from graph.pipeline import trigger_followup

    logger.info(f"[SCHEDULER] Running job | type={followup_type} | customer={booking_dict.get('customer_name')}")

    trigger_followup(
        followup_type=followup_type,
        phone_number=booking_dict["customer_phone"],
        business_config=_get_business_config(),
        booking_object=booking_dict
    )


def schedule_booking_reminders(booking: Booking):
    """
    Schedule all 4 follow-up jobs for a confirmed booking.
    Called from booking_node in pipeline immediately after
    booking is saved to DB.

    Jobs scheduled:
      24hr   → 24 hours before appointment
      1hr    → 1 hour before appointment
      review → 2 hours after appointment
      noshow → 30 minutes after appointment (check if showed up)
    """
    now = datetime.now()
    appt = booking.scheduled_at
    booking_dict = _booking_to_dict(booking)

    jobs = [
        ("24hr",   appt - timedelta(hours=24)),
        ("1hr",    appt - timedelta(hours=1)),
        ("review", appt + timedelta(hours=2)),
        ("noshow", appt + timedelta(minutes=30)),
    ]

    for followup_type, run_at in jobs:
        if run_at <= now:
            logger.info(f"[SCHEDULER] Skipping {followup_type} — time already passed ({run_at})")
            continue

        job_id = f"{followup_type}_{booking.id}"

        scheduler.add_job(
            func=_run_followup_job,
            trigger=DateTrigger(run_date=run_at),
            args=[followup_type, booking_dict],
            id=job_id,
            replace_existing=True
        )
        logger.info(
            f"[SCHEDULER] Scheduled {followup_type} for "
            f"{booking.customer_name} at {run_at}"
        )


def reschedule_all_pending():
    """
    On app startup — reload all reminders for upcoming bookings.
    This ensures reminders survive server restarts.
    """
    db = SessionLocal()
    try:
        upcoming = get_upcoming_bookings(db)
        count = 0
        for booking in upcoming:
            schedule_booking_reminders(booking)
            count += 1
        logger.info(f"[SCHEDULER] Restored reminders for {count} upcoming bookings")
    finally:
        db.close()


def start_scheduler():
    """Start APScheduler background thread"""
    scheduler.start()
    reschedule_all_pending()
    logger.info("✅ Scheduler started and running")


def stop_scheduler():
    """Graceful shutdown"""
    if scheduler.running:
        scheduler.shutdown(wait=False)
        logger.info("[SCHEDULER] Stopped")