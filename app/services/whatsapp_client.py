import httpx
from tenacity import retry, stop_after_attempt, wait_fixed
from app.core.config import settings
from app.core.logger import logger
import urllib.parse

class WhatsAppClient:
    def __init__(self):
        self.api_url = f"https://api.green-api.com/waInstance{settings.GREEN_API_ID}"
        self.api_token = settings.GREEN_API_TOKEN

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def send_message(self, chat_id: str, text: str):
        url = f"{self.api_url}/sendMessage/{self.api_token}"
        payload = {"chatId": chat_id, "message": text}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info(f"Message sent to {chat_id}")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def send_buttons(self, chat_id: str, text: str, buttons: list):
        """
        buttons payload example: [{"buttonId": "approve_lead_123", "buttonText": "Approve"}]
        """
        url = f"{self.api_url}/sendButtons/{self.api_token}"
        payload = {
            "chatId": chat_id,
            "message": text,
            "buttons": buttons
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, json=payload)
            resp.raise_for_status()
            logger.info(f"Buttons sent to {chat_id}")

    @retry(stop=stop_after_attempt(3), wait=wait_fixed(2))
    async def send_location_link(self, chat_id: str, address: str, text_prefix: str = "Navigate here:"):
        encoded_address = urllib.parse.quote(address)
        waze_url = f"https://waze.com/ul?q={encoded_address}"
        message = f"{text_prefix}\n{waze_url}"
        await self.send_message(chat_id, message)
