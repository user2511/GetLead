from fastapi import APIRouter
from services.db_service import SessionLocal
from models.lead import Lead
from models.booking import Booking
from sqlalchemy import func

router = APIRouter()

@router.get("/dashboard")
async def get_dashboard():
    """Simple metrics dashboard"""
    db = SessionLocal()
    try:
        total_leads    = db.query(Lead).count()
        total_bookings = db.query(Booking).count()
        confirmed      = db.query(Booking).filter(
            Booking.status == "CONFIRMED"
        ).count()
        no_shows       = db.query(Booking).filter(
            Booking.status == "NO_SHOW"
        ).count()
        escalations    = db.query(Lead).filter(
            Lead.is_escalated == True
        ).count()

        return {
            "total_leads":       total_leads,
            "total_bookings":    total_bookings,
            "confirmed_bookings": confirmed,
            "no_shows":          no_shows,
            "escalations":       escalations,
            "conversion_rate":   f"{(total_bookings/total_leads*100):.1f}%" if total_leads > 0 else "0%"
        }
    finally:
        db.close()