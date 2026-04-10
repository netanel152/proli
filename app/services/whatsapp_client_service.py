import httpx
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
from app.core.config import settings
from app.core.logger import logger
import urllib.parse

class WhatsAppClient:
    def __init__(self):
        self.api_url = f"https://api.green-api.com/waInstance{settings.GREEN_API_INSTANCE_ID}"
        self.api_token = settings.GREEN_API_TOKEN
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=30.0)
        return self._client

    async def close(self):
        if self._client and not self._client.is_closed:
            await self._client.close()
            self._client = None

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def _send_request(self, endpoint: str, payload: dict):
        url = f"{self.api_url}/{endpoint}/{self.api_token}"
        client = await self._get_client()
        resp = await client.post(url, json=payload)
        resp.raise_for_status()
        return resp.json()

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def send_message(self, chat_id: str, text: str):
        payload = {"chatId": chat_id, "message": text}
        try:
            await self._send_request("sendMessage", payload)
            logger.info(f"Message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            raise

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10)
    )
    async def send_location_link(self, chat_id: str, address: str, text_prefix: str = "Navigate here:"):
        encoded_address = urllib.parse.quote(address)
        waze_url = f"https://waze.com/ul?q={encoded_address}"
        message = f"{text_prefix}\n{waze_url}"
        await self.send_message(chat_id, message)

    async def send_interactive_buttons(self, to_number: str, text: str, buttons: list[dict]) -> dict:
        """
        Sends a message with buttons via sendButtons.
        Falls back to plain text if buttons are disabled in config or the plan doesn't support them.
        buttons: list of dicts with 'id' and 'title'.
        """
        chat_id = f"{to_number}@c.us" if not to_number.endswith("@c.us") else to_number

        if not settings.WHATSAPP_BUTTONS_ENABLED:
            options = "\n".join(f"• {b['title']}" for b in buttons)
            await self.send_message(chat_id, f"{text}\n\n{options}")
            return {}

        buttons_payload = [
            {"buttonId": b["id"], "buttonText": {"displayText": b["title"]}}
            for b in buttons
        ]
        payload = {"chatId": chat_id, "message": text, "buttons": buttons_payload}

        try:
            resp = await self._send_request("sendButtons", payload)
            logger.info(f"Interactive buttons sent to {chat_id}")
            return resp
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 403:
                logger.warning(
                    f"sendButtons returned 403 for {chat_id} — plan may not support buttons. "
                    "Set WHATSAPP_BUTTONS_ENABLED=false in .env to skip this call. Falling back to text."
                )
                options = "\n".join(f"• {b['title']}" for b in buttons)
                await self.send_message(chat_id, f"{text}\n\n{options}")
                return {}
            logger.error(f"Failed to send interactive buttons to {chat_id}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to send interactive buttons to {chat_id}: {e}")
            raise
