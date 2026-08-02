"""Outbound capture at the transport layer (PRO-83).

The harness does not mock ``WhatsAppClient``. It runs the real client and swaps
only the httpx transport underneath it, so every record here was produced by the
production code path: the same payload dict, the same URL, after the same PRO-71
circuit-breaker check and inside the same tenacity retry policy. The single
difference from a live send is that the bytes are handed to this recorder instead
of a socket.

That is what lets a test assert on the *fully rendered Hebrew a human would have
received* — emoji, line breaks, interpolated name/address/price — rather than on a
message key or a template name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

# Endpoints that carry content a person actually reads. `sendChatStateTyping`
# (the "typing…" indicator) and `getStateInstance` (the deauth probe) are real
# outbound traffic and are recorded, but they are not messages and would
# otherwise drown every assertion.
MESSAGE_ENDPOINTS = ("sendMessage", "sendFileByUrl")


@dataclass(frozen=True)
class Send:
    """One intercepted Green API call, in the order it was made."""

    index: int
    endpoint: str
    chat_id: str
    body: str
    payload: dict

    @property
    def is_message(self) -> bool:
        return self.endpoint in MESSAGE_ENDPOINTS

    @property
    def caption(self) -> str:
        return self.payload.get("caption", "")

    def __repr__(self) -> str:  # pragma: no cover - failure-message ergonomics
        return f"Send(#{self.index} {self.endpoint} → {self.chat_id}: {self.body!r})"


def _endpoint_of(request: httpx.Request) -> str:
    """Green API URLs are ``/waInstance<id>/<endpoint>/<token>``."""
    parts = [p for p in request.url.path.split("/") if p]
    return parts[-2] if len(parts) >= 2 else request.url.path


class OutboundRecorder:
    """httpx ``MockTransport`` handler that captures every Green API request.

    Also the harness's proof of the zero-real-sends acceptance criterion: it counts
    every request that reached it, and ``tests/e2e/conftest.py`` separately makes
    the *real* httpx transports raise, so a request either lands here or fails the
    run. There is no third outcome.
    """

    def __init__(self) -> None:
        self.all: list[Send] = []
        # What `getStateInstance` reports back. Tests flip this to drive the PRO-20
        # deauth monitor and the PRO-71 breaker off a realistic probe result.
        self.instance_state = "authorized"

    # -- transport ----------------------------------------------------------
    def handler(self, request: httpx.Request) -> httpx.Response:
        endpoint = _endpoint_of(request)

        if endpoint == "getStateInstance":
            self.all.append(Send(len(self.all), endpoint, "", "", {}))
            return httpx.Response(200, json={"stateInstance": self.instance_state})

        try:
            payload: dict[str, Any] = json.loads(request.content or b"{}")
        except ValueError:  # pragma: no cover - Green API is always JSON
            payload = {}

        # For a file send the "body" a recipient reads is the caption; the URL is
        # kept in the payload for tests that care which file went out.
        if endpoint == "sendFileByUrl":
            body = payload.get("caption", "") or payload.get("urlFile", "")
        else:
            body = payload.get("message", "")

        self.all.append(
            Send(
                index=len(self.all),
                endpoint=endpoint,
                chat_id=payload.get("chatId", ""),
                body=body,
                payload=payload,
            )
        )
        return httpx.Response(200, json={"idMessage": f"rec-{len(self.all)}"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    # -- queries ------------------------------------------------------------
    @property
    def sends(self) -> list[Send]:
        """Content-bearing sends only (excludes typing indicators / state probes)."""
        return [s for s in self.all if s.is_message]

    def to(self, chat_id: str) -> list[Send]:
        return [s for s in self.sends if s.chat_id == chat_id]

    def bodies_to(self, chat_id: str) -> list[str]:
        return [s.body for s in self.to(chat_id)]

    def last_to(self, chat_id: str) -> Send:
        got = self.to(chat_id)
        assert got, f"expected at least one message to {chat_id}\n{self.transcript()}"
        return got[-1]

    def recipients(self) -> list[str]:
        seen: list[str] = []
        for s in self.sends:
            if s.chat_id not in seen:
                seen.append(s.chat_id)
        return seen

    def clear(self) -> None:
        self.all.clear()

    # -- assertions ---------------------------------------------------------
    def assert_text_to(self, chat_id: str, *fragments: str) -> Send:
        """Assert some message to ``chat_id`` contains every fragment.

        Fragments are literal Hebrew (or interpolated values) as rendered — a
        template rename that changes what the customer reads fails here.
        """
        for send in self.to(chat_id):
            if all(f in send.body for f in fragments):
                return send
        raise AssertionError(
            f"no message to {chat_id} contained all of {list(fragments)}\n"
            f"{self.transcript()}"
        )

    def assert_silent(self, chat_id: str, why: str) -> None:
        """Assert nothing content-bearing was sent to ``chat_id``.

        The absence assertions matter as much as the presence ones: quiet hours, an
        engaged breaker and webhook dedupe are all defined by what does *not* go out.
        """
        got = self.to(chat_id)
        assert (
            not got
        ), f"expected no message to {chat_id} ({why}), got:\n{self.transcript()}"

    def assert_nothing_sent(self, why: str) -> None:
        assert (
            not self.sends
        ), f"expected no outbound message at all ({why}):\n{self.transcript()}"

    def assert_order(self, *chat_ids: str) -> None:
        """Assert the first message to each chat happened in this relative order."""
        firsts = []
        for chat_id in chat_ids:
            got = self.to(chat_id)
            assert got, f"expected a message to {chat_id}\n{self.transcript()}"
            firsts.append((chat_id, got[0].index))
        indexes = [i for _, i in firsts]
        assert indexes == sorted(
            indexes
        ), f"sends were out of order: {firsts}\n{self.transcript()}"

    def assert_never_contains(self, fragment: str, why: str) -> None:
        """Assert a string never leaked into *any* outbound message.

        Used for the internal ``[DEAL:]`` marker (PRO-44) and for the forbidden
        real phone numbers.
        """
        hits = [s for s in self.sends if fragment in s.body]
        assert (
            not hits
        ), f"{fragment!r} leaked outbound ({why}): {hits}\n{self.transcript()}"

    # -- diagnostics --------------------------------------------------------
    def transcript(self) -> str:
        if not self.all:
            return "  (no outbound traffic recorded)"
        lines = ["  --- outbound transcript ---"]
        for s in self.all:
            if s.endpoint == "getStateInstance":
                lines.append(f"  #{s.index} getStateInstance → {self.instance_state}")
                continue
            head = f"  #{s.index} {s.endpoint} → {s.chat_id}"
            if not s.is_message:
                lines.append(head)
                continue
            for i, line in enumerate((s.body or "").split("\n")):
                lines.append(
                    f"{head}: {line}" if i == 0 else f"{' ' * len(head)}  {line}"
                )
        return "\n".join(lines)
