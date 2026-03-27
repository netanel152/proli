"""
SMS Service - Fallback notification channel when WhatsApp fails.

Supports Israeli SMS providers via HTTP API.
Currently implements InforUMobile API format.
Configure via SMS_API_KEY and SMS_SENDER_ID env vars.
"""

import httpx
from app.core.config import settings
from app.core.logger import logger


class SMSClient:
    """HTTP client for sending SMS via Israeli SMS provider."""

    def __init__(self):
        self.api_key = getattr(settings, "SMS_API_KEY", None)
        self.sender_id = getattr(settings, "SMS_SENDER_ID", "Proli")
        self.base_url = getattr(settings, "SMS_API_URL", "https://api.inforu.co.il/SendSMS/SendSMS")
        self._enabled = bool(self.api_key)

    @property
    def is_configured(self) -> bool:
        return self._enabled

    async def send_sms(self, phone: str, message: str) -> bool:
        """
        Send an SMS message.

        Args:
            phone: Phone number (e.g., "972501234567" or "972501234567@c.us")
            message: Message text

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._enabled:
            logger.debug("SMS not configured, skipping.")
            return False

        # Normalize phone number
        clean_phone = phone.replace("@c.us", "").strip()
        if not clean_phone.startswith("972"):
            clean_phone = f"972{clean_phone.lstrip('0')}"

        payload = {
            "api_key": self.api_key,
            "sender": self.sender_id,
            "to": clean_phone,
            "message": message,
        }

        try:
            async with httpx.AsyncClient(timeout=10) as client:
                response = await client.post(self.base_url, json=payload)
                response.raise_for_status()

                result = response.json()
                if result.get("status") == "ok" or result.get("success"):
                    logger.info(f"SMS sent to {clean_phone[:6]}***")
                    return True
                else:
                    logger.warning(f"SMS API returned error: {result}")
                    return False

        except httpx.HTTPStatusError as e:
            logger.error(f"SMS HTTP error: {e.response.status_code}")
            return False
        except Exception as e:
            logger.error(f"SMS send failed: {e}")
            return False


# Module-level instance
sms_client = SMSClient()
