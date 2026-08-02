import asyncio
import json
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


def _endpoint_of(request: httpx.Request) -> str:
    """Green API URLs are ``/waInstance<id>/<endpoint>/<token>`` — pull out the
    endpoint without ever putting the token in a log line.

    A path too short to have an endpoint segment returns a constant rather than the
    path itself: on a single-segment URL that segment *is* the token.
    """
    parts = [p for p in request.url.path.split("/") if p]
    return parts[-2] if len(parts) >= 2 else "unknown"


def _dry_run_handler(request: httpx.Request) -> httpx.Response:
    """PRO-79/PRO-83 transport for ``WHATSAPP_DRY_RUN``: absorbs a fully-built
    Green API request and answers with a synthetic success, so nothing leaves the
    process.

    This is the *only* point at which dry-run diverges from production. The
    payload, the URL, the circuit-breaker check and the retry policy above it are
    all the real code path, which is what lets the offline E2E harness (PRO-83)
    assert on the exact bytes a real recipient would have received.
    """
    endpoint = _endpoint_of(request)
    chat_id = ""
    preview = ""
    try:
        data = json.loads(request.content) if request.content else {}
        chat_id = str(data.get("chatId", ""))
        preview = str(data.get("message") or data.get("urlFile") or "")[:100]
    except Exception:  # pragma: no cover — logging must never break a send
        pass
    # debug, not info: the preview is the rendered message body, which routinely
    # contains a customer's or pro's phone number (PRO-80).
    logger.debug(
        f"🧪 [DRY-RUN] {endpoint} to ...{chat_id[-8:]}: {preview!r} — not transmitted"
    )
    return httpx.Response(200, json={"idMessage": "dry-run"})


class WhatsAppClient:
    def __init__(self):
        self.api_url = (
            f"https://api.green-api.com/waInstance{settings.GREEN_API_INSTANCE_ID}"
        )
        self.api_token = settings.GREEN_API_TOKEN
        self._client: httpx.AsyncClient | None = None
        # Which mode the cached client was built for, so a runtime flip of
        # WHATSAPP_DRY_RUN can never be served by a stale real-network client
        # (or vice versa).
        self._client_dry_run: bool | None = None
        # Separate client for the read-only getStateInstance probe. Dry-run means
        # "send nothing to anybody", not "stop looking at our own instance": the
        # WhatsApp outage runbook tells the operator to set WHATSAPP_DRY_RUN=true
        # in production during an incident, and the PRO-20 deauth monitor has to
        # keep seeing the true state through that window — both to avoid a
        # permanently-green watchdog and because it is what detects recovery.
        self._probe_client: httpx.AsyncClient | None = None
        self._client_lock = asyncio.Lock()

    def _build_client(self, dry_run: bool) -> httpx.AsyncClient:
        transport = httpx.MockTransport(_dry_run_handler) if dry_run else None
        return httpx.AsyncClient(timeout=30.0, transport=transport)

    async def _get_client(self) -> httpx.AsyncClient:
        # Double-check locking: fast path avoids lock when client is already live;
        # slow path re-checks inside the lock so only one coroutine ever creates it.
        dry_run = bool(settings.WHATSAPP_DRY_RUN)
        if (
            self._client is not None
            and not self._client.is_closed
            and self._client_dry_run == dry_run
        ):
            return self._client

        async with self._client_lock:
            stale = (
                self._client is None
                or self._client.is_closed
                or self._client_dry_run != dry_run
            )
            if stale:
                # Deliberately not closing the outgoing client: a coroutine that
                # took it off the fast path a moment ago may still be mid-request,
                # and closing underneath it raises a RuntimeError that tenacity
                # does not retry. Drop the reference and let the pool be reclaimed.
                self._client = self._build_client(dry_run)
                self._client_dry_run = dry_run
            return self._client

    async def _get_probe_client(self) -> httpx.AsyncClient:
        """Always-real client for ``getStateInstance`` — see ``_probe_client``."""
        if self._probe_client is None or self._probe_client.is_closed:
            self._probe_client = httpx.AsyncClient(timeout=30.0)
        return self._probe_client

    async def close(self):
        for attr in ("_client", "_probe_client"):
            client = getattr(self, attr)
            if client and not client.is_closed:
                # aclose(), not close() — httpx defines the latter on the sync
                # Client only, so the old call raised AttributeError.
                await client.aclose()
            setattr(self, attr, None)
        self._client_dry_run = None

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
        polls on its own interval, so a single failed probe is fine.

        Uses the probe client, so this stays a real read even under
        ``WHATSAPP_DRY_RUN`` — a fabricated "authorized" would leave the deauth
        monitor permanently green and would clear the PRO-71 breaker keys on every
        tick, exactly when the outage runbook has dry-run switched on."""
        try:
            client = await self._get_probe_client()
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
