"""The event envelope shared by both hops (contracts/events.md) and its SSE framing."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

# Event types of contracts/events.md. Inbound: what the frontend sends to the BFF.
InboundType = Literal["user.message", "a2ui.action", "ui.surface_reopen", "a2ui.callAgentFunction", "confirm.response", "document.uploaded"]
# Outbound: what the hosted agent and the BFF send to the frontend.
OutboundType = Literal[
    "turn.started", "text.delta", "text.done", "suggestions", "a2ui.message", "tool.call", "tool.result",
    "memory.result", "state.delta", "signals", "confirm.required", "quote.created", "quote.updated",
    "session.preferences", "rules.hit", "handoff.created", "phase", "guardrail", "turn.completed", "error",
]
Actor = str  # customer | frontend | bff | main_agent | memory_agent | document_agent | tool:<name>


def now_iso() -> str:
    """The current UTC time as ISO 8601 with milliseconds and a ``Z`` suffix, the format of ``Envelope.ts``."""
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


class Envelope(BaseModel):
    """One event, in both directions and on both hops (contracts/events.md)."""

    type: str  # an InboundType or OutboundType value
    session_id: str = ""
    turn: int = 0  # the turn of the session the event belongs to
    seq: int = 0  # grows within a turn, so the frontend can drop duplicates when a stream reconnects
    ts: str = Field(default_factory=now_iso)  # UTC, ISO 8601, milliseconds
    actor: Actor = "bff"  # who produced the event
    data: dict[str, Any] = Field(default_factory=dict)  # the payload; its shape depends on ``type``
    form_spec: dict[str, Any] | None = None  # only on a2ui.message from a2ui.render_form; the BFF strips it

    def public(self) -> dict[str, Any]:
        """The envelope as forwarded to the frontend (never carries form_spec)."""
        return self.model_dump(exclude={"form_spec"}, exclude_none=True)


def sse_encode(event: Envelope | dict[str, Any]) -> bytes:
    """One SSE ``data:`` frame as UTF-8 bytes; an ``Envelope`` goes out as ``public()``, so without ``form_spec``."""
    payload = event.public() if isinstance(event, Envelope) else event
    return f"data: {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n\n".encode()


def sse_decode(chunk: str) -> list[dict[str, Any]]:
    """Decode every ``data:`` event in a text chunk (comments and blank lines are ignored)."""
    events: list[dict[str, Any]] = []
    for block in chunk.split("\n\n"):
        data_lines = [line[5:].lstrip() for line in block.splitlines() if line.startswith("data:")]
        if data_lines:
            events.append(json.loads("\n".join(data_lines)))
    return events
