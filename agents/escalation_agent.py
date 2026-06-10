import logging
import os
from services.twilio_service import send_whatsapp
from monitoring.langfuse_setup import get_langfuse
from models.config import BusinessConfig

logger = logging.getLogger(__name__)
langfuse = get_langfuse()

CONFIG_PATH = os.path.join(os.path.dirname(__file__), "../configs/dental_clinic.json")
business_config = BusinessConfig.from_json_file(CONFIG_PATH)

def notify_owner_escalation(
    phone_number: str,
    message: str,
    reason: str = "Emergency keyword detected"
) -> bool:
    """
    Notify business owner on WhatsApp when escalation triggered.
    """
    trace = langfuse.trace(
        name="escalation-owner-notify",
        input={"phone": phone_number, "reason": reason}
    )
    try:
        owner_message = (
            f"*URGENT — LeadFlow Alert*\n\n"
            f"Customer needs immediate attention!\n\n"
            f"Phone: {phone_number}\n"
            f"Message: \"{message}\"\n"
            f"Reason: {reason}\n\n"
            f"Please contact them immediately."
        )
        success = send_whatsapp(
            business_config.owner_whatsapp,
            owner_message
        )
        trace.update(output={"notified": success})
        logger.warning(f"Owner notified of escalation from {phone_number}")
        return success
    except Exception as e:
        logger.error(f"Owner escalation notification failed: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False