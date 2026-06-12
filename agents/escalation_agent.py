import logging
import os
from services.twilio_service import send_whatsapp
from monitoring.langfuse_setup import get_langfuse

logger = logging.getLogger(__name__)
langfuse = get_langfuse()


def notify_owner_escalation(
    phone_number: str,
    message: str,
    reason: str = "Emergency keyword detected"
) -> bool:
    """
    Notify business owner on WhatsApp when escalation triggered.
    Called from escalation_node inside pipeline.
    """
    trace = langfuse.trace(
        name="escalation-owner-notify",
        input={
            "customer_phone": phone_number,
            "reason": reason,
            "message": message
        }
    )

    try:
        # Get owner number from env (set per business)
        owner_number = os.getenv("OWNER_WHATSAPP_NUMBER", "")

        if not owner_number:
            logger.error("[ESCALATION] OWNER_WHATSAPP_NUMBER not set in .env")
            trace.update(output={"error": "owner number not configured"}, level="ERROR")
            return False

        owner_message = (
            f"🚨 *URGENT — LeadFlow Alert*\n\n"
            f"A customer needs immediate attention!\n\n"
            f"📱 Customer: {phone_number}\n"
            f"💬 Message: \"{message}\"\n"
            f"⚠️ Reason: {reason}\n\n"
            f"Please contact them immediately."
        )

        success = send_whatsapp(owner_number, owner_message)

        trace.update(output={"notified": success, "owner": owner_number})
        logger.warning(f"[ESCALATION] Owner notified: {success}")
        return success

    except Exception as e:
        logger.error(f"[ESCALATION] Owner notification failed: {e}")
        trace.update(output={"error": str(e)}, level="ERROR")
        return False