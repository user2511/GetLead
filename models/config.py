from pydantic import BaseModel
from typing import List, Optional, Dict

class WorkingHours(BaseModel):
    monday: Optional[str] = None     # "9:00-18:00" or "CLOSED"
    tuesday: Optional[str] = None
    wednesday: Optional[str] = None
    thursday: Optional[str] = None
    friday: Optional[str] = None
    saturday: Optional[str] = None
    sunday: Optional[str] = None

class ReminderConfig(BaseModel):
    reminder_24hr: bool = True
    reminder_1hr: bool = True
    post_visit_review: bool = True
    no_show_followup: bool = True

class BusinessConfig(BaseModel):
    business_id: str
    business_name: str
    business_type: str               # dental / plumber / salon / medical / gym
    owner_whatsapp: str              # owner's WhatsApp to notify on escalation
    services: List[str]
    working_hours: WorkingHours
    calcom_username: str
    calcom_event_type_id: str
    greeting_message: str
    escalation_keywords: List[str] = ["emergency", "urgent", "pain", "bleeding"]
    reminders: ReminderConfig = ReminderConfig()
    language: str = "en"

    @classmethod
    def from_json_file(cls, path: str) -> "BusinessConfig":
        import json
        with open(path, "r") as f:
            data = json.load(f)
        return cls(**data)