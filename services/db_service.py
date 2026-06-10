import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from models.lead import Base, Lead
from models.booking import Booking
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./leadflow.db")

# SQLite for local dev, PostgreSQL for production
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    """Create all tables"""
    Base.metadata.create_all(bind=engine)
    logger.info("Database tables created")

def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- Lead Operations ---

def get_or_create_lead(db: Session, phone_number: str, business_id: str) -> Lead:
    """Get existing lead or create new one"""
    lead = db.query(Lead).filter(
        Lead.phone_number == phone_number,
        Lead.business_id == business_id
    ).first()

    if not lead:
        lead = Lead(
            phone_number=phone_number,
            business_id=business_id,
            conversation_state="NEW"
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        logger.info(f"New lead created: {phone_number}")
    
    return lead

def update_lead(db: Session, lead_id: str, **kwargs) -> Lead:
    """Update lead fields"""
    db.query(Lead).filter(Lead.id == lead_id).update(kwargs)
    db.commit()
    return db.query(Lead).filter(Lead.id == lead_id).first()

# --- Booking Operations ---

def create_booking(db: Session, booking_data: dict) -> Booking:
    """Create a new booking"""
    booking = Booking(**booking_data)
    db.add(booking)
    db.commit()
    db.refresh(booking)
    logger.info(f"New booking: {booking.customer_name} at {booking.scheduled_at}")
    return booking

def get_upcoming_bookings(db: Session):
    """Get all upcoming confirmed bookings"""
    from datetime import datetime
    return db.query(Booking).filter(
        Booking.status == "CONFIRMED",
        Booking.scheduled_at > datetime.now()
    ).all()