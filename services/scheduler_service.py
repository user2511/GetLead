import logging
from datetime import datetime, timedelta
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from services.db_service import SessionLocal, get_upcoming_bookings
from agents.followup_agent import (
    send_24hr_reminder,
    send_1hr_reminder,
    send_review_request,
    send_noshow_followup
)
from models.booking import Booking

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(timezone="Asia/Kolkata")

def schedule_booking_reminders(booking: Booking):
    """
    Schedule all reminders for a confirmed booking.
    Called immediately after booking is created.
    """
    now = datetime.now()
    appt_time = booking.scheduled_at

    # 24hr reminder
    reminder_24hr = appt_time - timedelta(hours=24)
    if reminder_24hr > now:
        scheduler.add_job(
            func=send_24hr_reminder,
            trigger=DateTrigger(run_date=reminder_24hr),
            args=[booking],
            id=f"24hr_{booking.id}",
            replace_existing=True
        )
        logger.info(f"Scheduled 24hr reminder for {booking.customer_name} at {reminder_24hr}")

    # 1hr reminder
    reminder_1hr = appt_time - timedelta(hours=1)
    if reminder_1hr > now:
        scheduler.add_job(
            func=send_1hr_reminder,
            trigger=DateTrigger(run_date=reminder_1hr),
            args=[booking],
            id=f"1hr_{booking.id}",
            replace_existing=True
        )
        logger.info(f"Scheduled 1hr reminder for {booking.customer_name} at {reminder_1hr}")

    # Review request (2hrs after appointment)
    review_time = appt_time + timedelta(hours=2)
    scheduler.add_job(
        func=send_review_request,
        trigger=DateTrigger(run_date=review_time),
        args=[booking],
        id=f"review_{booking.id}",
        replace_existing=True
    )

    # No-show check (30 mins after appointment)
    noshow_time = appt_time + timedelta(minutes=30)
    scheduler.add_job(
        func=check_and_send_noshow,
        trigger=DateTrigger(run_date=noshow_time),
        args=[booking.id],
        id=f"noshow_{booking.id}",
        replace_existing=True
    )

def check_and_send_noshow(booking_id: str):
    """Check if customer showed up — if not, send follow-up"""
    db = SessionLocal()
    try:
        booking = db.query(Booking).filter(Booking.id == booking_id).first()
        if booking and booking.status == "CONFIRMED":
            # Still confirmed = no-show (not marked COMPLETED)
            booking.status = "NO_SHOW"
            db.commit()
            send_noshow_followup(booking)
    finally:
        db.close()

def reschedule_all_pending():
    """
    On startup — reschedule reminders for all upcoming bookings.
    Handles server restarts gracefully.
    """
    db = SessionLocal()
    try:
        upcoming = get_upcoming_bookings(db)
        for booking in upcoming:
            schedule_booking_reminders(booking)
        logger.info(f"Rescheduled reminders for {len(upcoming)} upcoming bookings")
    finally:
        db.close()

def start_scheduler():
    """Start the background scheduler"""
    scheduler.start()
    # Reschedule any pending reminders from DB
    reschedule_all_pending()
    logger.info("✅ APScheduler started")

def stop_scheduler():
    """Graceful shutdown"""
    if scheduler.running:
        scheduler.shutdown()
        logger.info("Scheduler stopped")