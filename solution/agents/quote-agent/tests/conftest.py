"""Shared fixtures: the invocations app in-process, fake model, fake MCP servers (no network)."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
from fake_mcp import ARGS, REFUSE, SEEN, a2ui_server, quoting_server
from mcp import Client
from quote_common import sse_decode

from quote_agent.agent import Runtime
from quote_agent.app import create_app
from quote_agent.config import Settings


@pytest.fixture
def anyio_backend() -> str:
    """Run async tests and fixtures on asyncio."""
    return "asyncio"  # anyio runs async fixtures and tests in one task (MCP clients use cancel scopes)


@pytest.fixture
async def runtime() -> AsyncIterator[Runtime]:
    """A ``Runtime`` with the fake model, wired to in-process fake MCP servers; clears what they recorded."""
    SEEN.clear()
    ARGS.clear()
    REFUSE["create_quote"] = False
    async with Client(quoting_server()) as quoting, Client(a2ui_server()) as a2ui:
        yield Runtime(Settings(model="fake"), mcp_clients={"quoting": quoting, "a2ui": a2ui})


@pytest.fixture
async def http(runtime: Runtime) -> AsyncIterator[httpx.AsyncClient]:
    """An HTTP client that talks to the invocations app in-process."""
    app = create_app(runtime, configure_observability=None)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://agent") as client:
        yield client


async def post_turn(
    http: httpx.AsyncClient, inp: dict[str, Any], *, turn: int = 1, session: str = "s_test", **body: Any
) -> list[dict[str, Any]]:
    """POST one turn to ``/invocations``, check the SSE answer and return its decoded events."""
    payload = {"turn": turn, "input": inp, "session_ref": "dev-only-session-ref", **body}
    response = await http.post(
        "/invocations?agent_session_id=" + session, json=payload, headers={"x-client-session-id": session}
    )
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    return sse_decode(response.text)


def of_type(events: list[dict[str, Any]], kind: str) -> list[dict[str, Any]]:
    """The events of one type."""
    return [e for e in events if e["type"] == kind]
