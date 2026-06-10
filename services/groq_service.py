import os
from groq import Groq
from dotenv import load_dotenv
import logging

load_dotenv()
logger = logging.getLogger(__name__)

# Single shared client
groq_client = Groq(api_key=os.getenv("GROQ_API_KEY"))

def call_groq(
    system_prompt: str,
    user_message: str,
    model: str = "llama-3.1-70b-versatile",
    temperature: float = 0.3,
    max_tokens: int = 500
) -> str:
    """
    Single function for all Groq calls.
    Keep it simple — one place to change model/settings.
    """
    try:
        response = groq_client.chat.completions.create(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ]
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"Groq call successful | tokens: {response.usage.total_tokens}")
        return result

    except Exception as e:
        logger.error(f"Groq call failed: {e}")
        raise