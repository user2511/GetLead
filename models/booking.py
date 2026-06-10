from sqlalchemy import Column, String, DateTime, Boolean, ForeignKey, Text
from sqlalchemy.sql import func
from models.lead import Base
import uuid

class Booking(Base):
    __tablename__ = "bookings"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Relations
    lead_id = Column(String, ForeignKey("leads.id"), nullable=False)
    business_id = Column(String, nullable=False)
    
    # Booking details
    customer_name = Column(String, nullable=False)
    customer_phone = Column(String, nullable=False)
    service = Column(String, nullable=False)
    scheduled_at = Column(DateTime, nullable=False)
    calcom_booking_uid = Column(String, nullable=True)  # Cal.com reference
    
    # Status
    status = Column(String, default="CONFIRMED")  # CONFIRMED / CANCELLED / NO_SHOW / COMPLETED
    
    # Reminders sent
    reminder_24hr_sent = Column(Boolean, default=False)
    reminder_1hr_sent = Column(Boolean, default=False)
    review_request_sent = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Booking {self.customer_name} | {self.service} | {self.scheduled_at}>"