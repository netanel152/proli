"""PRO-86 — the single outbound egress.

Every WhatsApp message Proli sends passes through this object. That is the whole
point of the ticket: the yellowCard incident was caused by traffic that went
around the guarded client (raw ``httpx`` in the admin panel), which made the
circuit breaker decoration rather than protection.

Three guarantees live here, deliberately above the provider so they hold for any
transport:

* **Circuit breaker / kill switch** — PRO-71, now fail-closed (PRO-82).
* **Dry-run** — expressed as provider *selection* rather than a transport swap;
  see :class:`~app.providers.whatsapp.dry_run.DryRunProvider`.
* **One "not sent" answer** — every send that was blocked rather than attempted
  returns ``None``, whatever blocked it. PRO-159: a closed 24h service window
  with no approved fallback template used to be the exception to that, and the
  raise crashed ``process_message_task`` mid-flow. An unregistered fallback
  template is translated the same way, since it reaches callers through the
  same free-form sends. A vendor *rejection* still propagates — that
  distinction is the yellowCard lesson.

Method names match the client this replaces so the ~177 existing call sites and
their test doubles keep working unchanged.
"""

import urllib.parse
from typing import Any

from app.core.constants import WorkerConstants
from app.core.logger import logger
from app.core.phone import mask_chat_id as _mask
from app.core.redis_client import get_redis_client
from app.providers.whatsapp.base import (
    NormalizedMessage,
    ServiceWindowClosedError,
    TemplateNotRegisteredError,
    WhatsAppProvider,
)

# The two ways a send can be *blocked* by policy rather than fail in transit.
# Both are already reported by the provider before they are raised, and both
# must end as a ``None`` return rather than an exception — see PRO-159 and
# ``_note_blocked`` below.
_BLOCKED_BY_POLICY = (ServiceWindowClosedError, TemplateNotRegisteredError)

# PRO-71: presence of EITHER key halts all outbound.
#   * wa:instance:paused        — auto, set by the deauth monitor while the
#     account is non-authorized, cleared on recovery.
#   * wa:instance:paused:manual — operator kill switch, never touched by the
#     monitor, so a manual halt survives instance recovery.
_PAUSE_KEY = "wa:instance:paused"
_PAUSE_MANUAL_KEY = "wa:instance:paused:manual"

# PRO-82: positive confirmation that the account was probed and found healthy.
# Written by the deauth monitor every tick with a TTL, so "absent" means either
# "never probed" (worker boot window) or "the monitor stopped running" — both of
# which are states in which we must NOT send.
_STATE_KEY = "wa:instance:state"


def _as_text(value: Any) -> str:
    return value.decode() if isinstance(value, bytes) else str(value)


class WhatsAppFacade:
    """The chokepoint. Holds a provider; nothing else may hold one."""

    def __init__(self, provider: WhatsAppProvider):
        self._provider = provider

    @property
    def provider(self) -> WhatsAppProvider:
        return self._provider

    # ------------------------------------------------------------------
    # Circuit breaker
    # ------------------------------------------------------------------

    async def _outbound_blocked_reason(self) -> str | None:
        """``None`` when sending is permitted, else a human-readable reason.

        Fail-**closed** on an absent state key (PRO-82): before PRO-86 an absent
        key was indistinguishable from a healthy account, so every worker boot
        opened a window in which a flagged number could be sent from. Fail-open
        survives only for an actual Redis *error* — PRO-82 is explicit that a
        monitoring dependency going down must never take the send path with it,
        and that the bug is the absent key, not an unreachable Redis.
        """
        if not self._provider.transmits:
            # Cannot reach a handset — there is no number to protect.
            return None

        try:
            redis = await get_redis_client()
            if await redis.exists(_PAUSE_KEY, _PAUSE_MANUAL_KEY):
                return "circuit breaker engaged (instance paused)"
            state = await redis.get(_STATE_KEY)
        except Exception as e:
            logger.warning(
                f"Outbound pause check failed — sending anyway (fail-open): {e}"
            )
            return None

        if state is None:
            return (
                "account state unconfirmed — no successful probe on record "
                "(worker boot window or monitor not running)"
            )
        state_text = _as_text(state)
        if state_text != "authorized":
            return f"account state={state_text}"
        return None

    async def _guard(self, what: str, chat_id: str) -> bool:
        """True when the send may proceed; logs and returns False otherwise."""
        reason = await self._outbound_blocked_reason()
        if reason is None:
            return True
        logger.warning(
            f"⛔ Outbound halted ({reason}) — {what} to ...{chat_id[-4:]} "
            "suppressed, not sent."
        )
        return False

    # ------------------------------------------------------------------
    # Blocked by policy → the same "not sent" answer the breaker already gives
    # ------------------------------------------------------------------

    @staticmethod
    def _note_blocked(what: str, chat_id: str, error: Exception) -> None:
        """PRO-159: report a policy-blocked send without re-reporting it.

        The provider always logs *before* raising, so this line is a breadcrumb
        naming the send that was lost, not the alert. WARNING rather than ERROR
        because an ERROR here would open a second Sentry issue for a single
        drop the provider has already reported.

        Know what that provider-side report actually is, because it is thinner
        than "already paged" suggests: only the **first** window block per
        recipient per day is a ``page_critical``. Every later one is a lone
        ``logger.error``, and the loguru→Sentry bridge throttles per
        ``module:function:line`` — so all subsequent window drops, for all
        recipients, collapse into roughly one Sentry event per hour per
        replica. Before PRO-159 each one also surfaced as an ArqIntegration
        crash event; removing that crash was the point, but it means
        closed-window *volume* has to be read from the logs rather than counted
        from Sentry issues (SANDBOX_E2E_RUNSHEET.md step 4.6).

        The two causes are logged distinctly because they need different
        remedies: a closed window waits for an approved re-engagement template
        (PRO-87/PRO-150), while an unregistered template is a registry or
        rollout mistake that an operator can fix directly.
        """
        if isinstance(error, TemplateNotRegisteredError):
            reason = "fallback template is not registered as approved"
            hint = "Check the PRO-88 registry — an entry is still DRAFT."
        else:
            reason = "24h service window closed, no approved fallback template"
            hint = "Reported by the provider; see PRO-87/PRO-150 for templates."
        logger.warning(
            f"⛔ Outbound dropped ({reason}) — {what} to {_mask(chat_id)} "
            f"not sent. {hint}"
        )

    # ------------------------------------------------------------------
    # Outbound — legacy names, unchanged signatures
    # ------------------------------------------------------------------

    async def send_message(self, chat_id: str, text: str):
        if not await self._guard("message", chat_id):
            return None
        try:
            result = await self._provider.send_text(chat_id, text)
            logger.info(f"Message sent to ...{chat_id[-4:]}")
            return result
        except _BLOCKED_BY_POLICY as e:
            self._note_blocked("message", chat_id, e)
            return None
        except Exception as e:
            logger.error(f"Failed to send message to ...{chat_id[-4:]}: {e}")
            raise

    async def send_file_by_url(
        self,
        chat_id: str,
        url: str,
        caption: str = "",
        file_name: str = "media.jpg",
    ):
        if not await self._guard("file", chat_id):
            return None
        try:
            result = await self._provider.send_file(chat_id, url, caption, file_name)
            logger.info(f"File sent to ...{chat_id[-4:]}")
            return result
        except _BLOCKED_BY_POLICY as e:
            self._note_blocked("file", chat_id, e)
            return None
        except Exception as e:
            logger.error(f"Failed to send file to ...{chat_id[-4:]}: {e}")
            raise

    async def send_location_link(
        self, chat_id: str, address: str, text_prefix: str = "Navigate here:"
    ):
        """Provider-neutral: a Waze deep link inside an ordinary text message."""
        encoded_address = urllib.parse.quote(address)
        waze_url = f"https://waze.com/ul?q={encoded_address}"
        return await self.send_message(chat_id, f"{text_prefix}\n{waze_url}")

    async def send_chat_state_typing(self, chat_id: str) -> None:
        """Best-effort typing indicator — never blocks real message processing."""
        if not self._provider.transmits:
            return
        if await self._outbound_blocked_reason() is not None:
            return  # no point showing typing when outbound is halted
        try:
            await self._provider.send_typing(chat_id)
        except NotImplementedError:
            pass
        except Exception as e:
            logger.warning(f"Failed to send typing indicator to ...{chat_id[-4:]}: {e}")

    # ------------------------------------------------------------------
    # Outbound — provider-contract names
    # ------------------------------------------------------------------

    async def send_text(self, chat_id: str, text: str):
        return await self.send_message(chat_id, text)

    async def send_template(
        self,
        chat_id: str,
        template_name: str,
        params: dict[str, Any] | None = None,
    ):
        if not await self._guard(f"template {template_name}", chat_id):
            return None
        return await self._provider.send_template(chat_id, template_name, params)

    async def send_interactive(self, chat_id: str, body: str, options: list[str]):
        if not await self._guard("interactive message", chat_id):
            return None
        try:
            return await self._provider.send_interactive(chat_id, body, options)
        except _BLOCKED_BY_POLICY as e:
            self._note_blocked("interactive message", chat_id, e)
            return None

    # ------------------------------------------------------------------
    # State + inbound
    # ------------------------------------------------------------------

    async def get_state_instance(self) -> str | None:
        """Account authorization state.

        Deliberately *not* gated by the breaker: dry-run means "send nothing to
        anybody", not "stop looking at our own account". The outage runbook has
        the operator switch to dry-run during an incident, and the PRO-20
        watchdog has to keep seeing true state through that window — it is what
        detects recovery.
        """
        try:
            return await self._provider.get_state()
        except NotImplementedError:
            logger.warning(
                f"Provider {self._provider.name!r} does not implement a state probe."
            )
            return None
        except Exception as e:
            logger.warning(f"State probe failed: {e}")
            return None

    async def get_state(self) -> str | None:
        return await self.get_state_instance()

    def parse_webhook(self, payload: Any) -> NormalizedMessage | None:
        return self._provider.parse_webhook(payload)

    async def close(self):
        await self._provider.close()


async def record_account_state(state: str | None, *, transmits: bool = True) -> None:
    """Publish the outcome of a state probe for the breaker to read.

    Called by the PRO-20 deauth monitor every tick. The TTL is what makes the
    breaker fail closed when the monitor dies: the key simply stops being
    refreshed, and the next send is blocked rather than being waved through on
    stale good news.

    ``transmits=False`` refuses to write at all. A transport that cannot reach a
    handset cannot vouch for the account: ``DryRunProvider.get_state()`` reports
    ``authorized`` because a provider that never sends can never be
    deauthorized, and letting that answer into shared Redis would forge exactly
    the confirmation the breaker fails closed without. The outage runbook has the
    operator set ``WHATSAPP_DRY_RUN=true`` *during* an incident, so the forged
    key would be waiting for whichever process comes back up transmitting. This
    replaces the always-real probe client PRO-83 used for the same purpose.

    Non-authorized states actively **clear** the key rather than letting it age
    out, so the confirmation and the pause key cannot disagree during the TTL
    window if one of the two writes fails.
    """
    if not transmits:
        return
    try:
        redis = await get_redis_client()
        if state != "authorized":
            await redis.delete(_STATE_KEY)
            return
        await redis.set(
            _STATE_KEY, state, ex=WorkerConstants.WA_STATE_CONFIRM_TTL_SECONDS
        )
    except Exception as e:
        logger.warning(f"Could not record account state: {e}")
