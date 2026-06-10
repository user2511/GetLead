from sqlalchemy import Column, String, DateTime, Boolean, Text, Integer
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.sql import func
import uuid

Base = declarative_base()

class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Contact info
    phone_number = Column(String, nullable=False, index=True)
    name = Column(String, nullable=True)
    
    # Business context
    business_id = Column(String, nullable=False)  # which business this lead came from
    service_requested = Column(String, nullable=True)
    
    # Conversation state
    intent = Column(String, nullable=True)         # BOOK / RESCHEDULE / FAQ / UNKNOWN
    conversation_state = Column(String, default="NEW")  # NEW / IN_PROGRESS / BOOKED / ESCALATED
    last_message = Column(Text, nullable=True)
    
    # Flags
    is_escalated = Column(Boolean, default=False)
    booking_confirmed = Column(Boolean, default=False)
    
    # Timestamps
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    def __repr__(self):
        return f"<Lead {self.phone_number} | {self.intent} | {self.conversation_state}>"