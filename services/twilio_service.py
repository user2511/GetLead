import os
from twilio.rest import Client
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

client = Client(
    os.getenv("TWILIO_ACCOUNT_SID"),
    os.getenv("TWILIO_AUTH_TOKEN")
)

TWILIO_FROM = os.getenv("TWILIO_WHATSAPP_FROM", "whatsapp:+14155238886")

def send_whatsapp(to_number: str, message: str) -> bool:
    """
    Send WhatsApp message via Twilio.
    to_number: just the phone number e.g. +919876543210
    """
    try:
        msg = client.messages.create(
            from_=TWILIO_FROM,
            to=f"whatsapp:{to_number}",
            body=message
        )
        logger.info(f"WhatsApp sent to {to_number} | SID: {msg.sid}")
        return True
    except Exception as e:
        logger.error(f"WhatsApp send failed to {to_number}: {e}")
        return False