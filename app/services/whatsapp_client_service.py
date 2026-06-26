import asyncio
import httpx
from tenacity import (
    retry,
    stop_after_attempt,
    wait_exponential,
    retry_if_exception_type,
)
from app.core.config import settings
from app.core.logger import logger
import urllib.parse


class WhatsAppClient:
    def __init__(self):
        self.api_url = (
            f"https://api.green-api.com/waInstance{settings.GREEN_API_INSTANCE_ID}"
        )
        self.api_token = settings.GREEN_API_TOKEN
        self._client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    async def _get_client(self) -> httpx.AsyncClient:
        # Double-check locking: fast path avoids lock when client is already live;
        # slow path re-checks inside the lock so only one coroutine ever creates it.
        if self._client is not None and not self._client.is_closed:
            return self._client

        async with self._client_lock:
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
        wait=wait_exponential(multiplier=1, min=2, max=10),
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
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_message(self, chat_id: str, text: str):
        payload = {"chatId": chat_id, "message": text}
        try:
            await self._send_request("sendMessage", payload)
            logger.info(f"Message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            raise

    async def get_state_instance(self) -> str | None:
        """Return the Green API instance authorization state (e.g. "authorized",
        "notAuthorized", "starting", "yellowCard", "blocked") via getStateInstance.

        Best-effort and read-only: returns None on any network/HTTP error so
        callers (the deauth monitor) can treat "unreachable" the same as
        "not authorized" without raising. Not wrapped in tenacity — the monitor
        polls on its own interval, so a single failed probe is fine."""
        try:
            client = await self._get_client()
            url = f"{self.api_url}/getStateInstance/{self.api_token}"
            resp = await client.get(url)
            resp.raise_for_status()
            return resp.json().get("stateInstance")
        except Exception as e:
            logger.warning(f"getStateInstance probe failed: {e}")
            return None

    async def send_chat_state_typing(self, chat_id: str) -> None:
        """Show 'typing...' indicator via Green API sendChatStateTyping. Best-effort: failures are
        logged and swallowed so they cannot block real message processing."""
        try:
            await self._send_request("sendChatStateTyping", {"chatId": chat_id})
            logger.debug(f"Typing indicator sent to {chat_id}")
        except Exception as e:
            logger.warning(f"Failed to send typing indicator to {chat_id}: {e}")

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_location_link(
        self, chat_id: str, address: str, text_prefix: str = "Navigate here:"
    ):
        encoded_address = urllib.parse.quote(address)
        waze_url = f"https://waze.com/ul?q={encoded_address}"
        message = f"{text_prefix}\n{waze_url}"
        await self.send_message(chat_id, message)

    @retry(
        retry=retry_if_exception_type((httpx.NetworkError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def send_file_by_url(
        self, chat_id: str, url: str, caption: str = "", file_name: str = "media.jpg"
    ):
        payload = {
            "chatId": chat_id,
            "urlFile": url,
            "fileName": file_name,
            "caption": caption,
        }
        try:
            await self._send_request("sendFileByUrl", payload)
            logger.info(f"File sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send file to {chat_id}: {e}")
            raise
