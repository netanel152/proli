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
from app.core.redis_client import get_redis_client
import urllib.parse


# PRO-71 circuit breaker: presence of EITHER key halts all outbound sends.
#   * wa:instance:paused        — auto, managed by the deauth monitor
#     (`check_whatsapp_instance_state`): set the moment the instance is
#     non-authorized, cleared on recovery.
#   * wa:instance:paused:manual — operator kill switch, set/cleared by hand and
#     NEVER touched by the monitor, so a manual halt survives instance recovery.
# Two keys (not one) so an overlapping real outage can't wipe a manual pause.
_OUTBOUND_PAUSE_KEY = "wa:instance:paused"
_OUTBOUND_PAUSE_MANUAL_KEY = "wa:instance:paused:manual"


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
        if settings.WHATSAPP_DRY_RUN:
            logger.info(
                f"🧪 [DRY-RUN] send_message to ...{chat_id[-8:]}: {text[:100]!r}"
            )
            return
        if await self._is_outbound_paused():
            logger.warning(
                f"⛔ Outbound halted (WhatsApp instance not authorized) — "
                f"message to ...{chat_id[-8:]} suppressed, not sent."
            )
            return
        payload = {"chatId": chat_id, "message": text}
        try:
            await self._send_request("sendMessage", payload)
            logger.info(f"Message sent to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send message to {chat_id}: {e}")
            raise

    async def _is_outbound_paused(self) -> bool:
        """Circuit breaker (PRO-71): True when outbound sending is halted — either
        the deauth monitor tripped the auto breaker (``wa:instance:paused``) or an
        operator set the manual kill switch (``wa:instance:paused:manual``).

        Note: a suppressed send returns ``None`` like a successful one — callers do
        not distinguish delivery from suppression. During an outage that is the
        intended degradation (halt, don't silently vanish); delivery-gated state
        transitions are out of scope for this stop-the-bleeding change.

        Fail-open: any Redis error returns False so a monitoring dependency can
        never take down the send path."""
        try:
            redis = await get_redis_client()
            return bool(
                await redis.exists(_OUTBOUND_PAUSE_KEY, _OUTBOUND_PAUSE_MANUAL_KEY)
            )
        except Exception as e:
            logger.warning(
                f"Outbound pause check failed — sending anyway (fail-open): {e}"
            )
            return False

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
        if settings.WHATSAPP_DRY_RUN:
            return
        if await self._is_outbound_paused():
            return  # no point showing typing when outbound is halted
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
        if settings.WHATSAPP_DRY_RUN:
            logger.info(f"🧪 [DRY-RUN] send_file_by_url to ...{chat_id[-8:]}: {url}")
            return
        if await self._is_outbound_paused():
            logger.warning(
                f"⛔ Outbound halted (WhatsApp instance not authorized) — "
                f"file to ...{chat_id[-8:]} suppressed, not sent."
            )
            return
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
