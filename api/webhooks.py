import json
import logging
from fastapi import APIRouter, Request, Form
from fastapi.responses import PlainTextResponse
from services.db_service import get_or_create_lead, update_lead, SessionLocal
from graph.pipeline import process_message
from models.config import BusinessConfig

logger = logging.getLogger(__name__)
router = APIRouter()

# Load business config once at startup
# In production this would be dynamic per business
import os
CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../configs/dental_clinic.json")
business_config = BusinessConfig.from_json_file(CONFIG_PATH)
business_config_dict = business_config.model_dump()

@router.post("/whatsapp")
async def whatsapp_webhook(
    request: Request,
    From: str = Form(...),
    Body: str = Form(...),
    To: str = Form(...)
):
    """Receive WhatsApp message → run pipeline → reply"""

    phone_number = From.replace("whatsapp:", "").strip()
    message = Body.strip()

    logger.info(f"From: {phone_number} | Message: {message}")

    # Save lead to database
    db = SessionLocal()
    try:
        lead = get_or_create_lead(
            db=db,
            phone_number=phone_number,
            business_id=business_config.business_id
        )

        # Run through LangGraph pipeline
        response_text = await process_message(
            message=message,
            phone_number=phone_number,
            business_config=business_config_dict
        )

        # Update lead state
        update_lead(
            db=db,
            lead_id=lead.id,
            last_message=message,
            conversation_state="IN_PROGRESS"
        )

    finally:
        db.close()

    # Return TwiML
    twiml = f"""<?xml version="1.0" encoding="UTF-8"?>
<Response>
    <Message>{response_text}</Message>
</Response>"""

    return PlainTextResponse(content=twiml, media_type="application/xml")

@router.get("/whatsapp")
async def webhook_verify():
    return {"status": "LeadFlow webhook active"}