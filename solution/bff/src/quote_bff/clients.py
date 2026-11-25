"""Outbound clients: the hosted agent (invocations over SSE), MCP servers (one JSON-RPC call helper) and the document
agent (Foundry Responses with an agent reference). Each has an in-process fake in ``fakes.py`` with the same methods.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any

import httpx
from quote_common.envelope import sse_decode

TokenProvider = Callable[[], Awaitable[str]]  # an async function that returns a bearer token
A2UI_MIME = "application/a2ui+json"  # MIME type of the EmbeddedResource that carries A2UI messages
MCP_PROTOCOL = "2025-06-18"  # MCP protocol version offered in ``initialize``; the server's answer is then used


def entra_token(scope: str) -> TokenProvider:
    """A token provider for ``scope``: an async function that returns an Entra access token.

    The token comes from the managed identity (DefaultAzureCredential) and is cached until a minute before it expires.
    """
    cache: dict[str, Any] = {"token": None, "expires": 0.0}

    async def get() -> str:
        """The cached token, or a fresh one when it expires within a minute."""
        if cache["expires"] - 60 < time.time():
            from azure.identity.aio import DefaultAzureCredential

            async with DefaultAzureCredential() as cred:
                tok = await cred.get_token(scope)
            cache.update(token=tok.token, expires=float(tok.expires_on))
        return cache["token"]

    return get


class UpstreamError(Exception):
    """A dependency (hosted agent, MCP server, document agent) failed, or its output is unusable.

    ``code`` goes to the log and to the ``error`` event; ``retryable`` tells the web whether Retry can help.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = True) -> None:
        """``message`` is the exception text; ``code`` and ``retryable`` are kept as attributes."""
        super().__init__(message)
        self.code, self.retryable = code, retryable


async def _headers(token: TokenProvider | None, extra: dict[str, str] | None = None) -> dict[str, str]:
    """``extra`` plus ``Authorization: Bearer <token>`` when a token provider is set."""
    headers = dict(extra or {})
    if token is not None:
        headers["Authorization"] = f"Bearer {await token()}"
    return headers


# ---------------------------------------------------------------------------------------------------------------
# Hosted agent (interfaces §2)
# ---------------------------------------------------------------------------------------------------------------


class HostedAgentClient:
    """POST the invocation body and yield the envelopes of the SSE response.

    Local: ``{url}/invocations``. Azure: ``{project}/agents/{name}/endpoint/protocols/invocations?api-version=v1
    &agent_session_id=...`` with an Entra token for ``https://ai.azure.com/.default``. Retries ``424`` (session not
    ready) and ``429`` (honours Retry-After) up to 3 times (architecture §16).
    """

    def __init__(self, url: str, *, token: TokenProvider | None = None, foundry: bool = False,
                 http: httpx.AsyncClient | None = None, retry_delay: float = 0.5) -> None:
        """``url`` is the local agent URL, or the Foundry agent endpoint when ``foundry`` is true.

        ``retry_delay`` is the first back-off, in seconds, when a 424 or 429 comes without a Retry-After header.
        """
        self.url, self.token, self.foundry, self.retry_delay = url.rstrip("/"), token, foundry, retry_delay
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(120.0, connect=5.0))

    async def invoke(self, body: dict[str, Any], *, session_id: str, turn_id: str,
                     traceparent: str) -> AsyncIterator[dict[str, Any]]:
        """Stream the agent's envelopes for one turn.

        Raises UpstreamError on a 4xx or 5xx once the retries are spent (retryable for 5xx, 424 and 429).
        """
        if self.foundry:
            url, params = self.url + "/endpoint/protocols/invocations", {"api-version": "v1",
                                                                         "agent_session_id": session_id}
        else:
            url, params = self.url + "/invocations", {}
        extra = {"x-client-session-id": session_id, "x-client-turn-id": turn_id, "traceparent": traceparent,
                 "Accept": "text/event-stream"}
        for attempt in range(4):
            headers = await _headers(self.token, extra)
            async with self.http.stream("POST", url, params=params, json=body, headers=headers) as resp:
                if resp.status_code in (424, 429) and attempt < 3:
                    wait = float(resp.headers.get("retry-after", self.retry_delay * 2**attempt))
                    await asyncio.sleep(min(wait, 5.0))
                    continue
                if resp.status_code >= 400:
                    raise UpstreamError(f"agent_http_{resp.status_code}", "The assistant is unavailable.",
                                        retryable=resp.status_code >= 500 or resp.status_code in (424, 429))
                block: list[str] = []
                async for line in resp.aiter_lines():
                    if line:
                        block.append(line)
                        continue
                    for event in sse_decode("\n".join(block) + "\n\n"):
                        yield event
                    block = []
                for event in sse_decode("\n".join(block) + "\n\n"):
                    yield event
                return


# ---------------------------------------------------------------------------------------------------------------
# MCP (streamable HTTP, JSON-RPC): initialize, notifications/initialized, tools/call
# ---------------------------------------------------------------------------------------------------------------


class McpClient:
    """One call helper for both MCP servers. ``call`` returns the ``CallToolResult`` as a dict.

    Each call is its own short MCP session: initialize, initialized, ``tools/call``, then a DELETE of the session
    when the server issued an ``mcp-session-id``.
    """

    def __init__(self, url: str, *, token: TokenProvider | None = None, http: httpx.AsyncClient | None = None) -> None:
        """``url`` is the server's ``/mcp`` endpoint; ``token`` is set only when Entra tokens are required (Azure)."""
        self.url, self.token = url, token
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=5.0))

    async def _post(self, headers: dict[str, str], message: dict[str, Any]) -> tuple[httpx.Response, dict | None]:
        """POST one JSON-RPC message; return the response and its ``result`` (None for a notification).

        A request has an ``id``; its reply may be plain JSON or an SSE stream. Raises UpstreamError on an HTTP error
        or a JSON-RPC error.
        """
        resp = await self.http.post(self.url, json=message, headers=headers)
        if resp.status_code >= 400:
            raise UpstreamError(f"mcp_http_{resp.status_code}", f"MCP {message.get('method')} failed")
        if "id" not in message:
            return resp, None
        if resp.headers.get("content-type", "").startswith("text/event-stream"):
            reply = next((m for m in sse_decode(resp.text) if m.get("id") == message["id"]), None)
        else:
            reply = resp.json()
        if not reply or "error" in (reply or {}):
            raise UpstreamError("mcp_error", str((reply or {}).get("error", "no reply")))
        return resp, reply["result"]

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Call one tool: initialize, send ``notifications/initialized``, ``tools/call``, then close the MCP session.

        Raises UpstreamError when the server fails or the tool reports ``isError`` (not retryable).
        """
        headers = await _headers(self.token, {"Accept": "application/json, text/event-stream"})
        hello = {"protocolVersion": MCP_PROTOCOL, "capabilities": {},
                 "clientInfo": {"name": "quote-bff", "version": "1"}}
        resp, init = await self._post(headers, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello})
        if resp.headers.get("mcp-session-id"):
            headers["mcp-session-id"] = resp.headers["mcp-session-id"]
        headers["mcp-protocol-version"] = (init or {}).get("protocolVersion", MCP_PROTOCOL)
        await self._post(headers, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _, result = await self._post(headers, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                                               "params": {"name": tool, "arguments": arguments}})
        if "mcp-session-id" in headers:
            await self.http.delete(self.url, headers=headers)
        if result.get("isError"):
            raise UpstreamError("tool_error", f"{tool} returned an error", retryable=False)
        return result


def a2ui_messages(result: dict[str, Any]) -> list[dict[str, Any]]:
    """The A2UI messages of a CallToolResult: the EmbeddedResource with mimeType application/a2ui+json."""
    for item in result.get("content", []):
        resource = item.get("resource") or {}
        if item.get("type") == "resource" and resource.get("mimeType") == A2UI_MIME:
            return json.loads(resource["text"])
    raise UpstreamError("a2ui_no_resource", "render result has no A2UI resource", retryable=False)


def tool_value(result: dict[str, Any]) -> Any:
    """The structured value of a CallToolResult (structuredContent, else the first JSON text content)."""
    if result.get("structuredContent") is not None:
        return result["structuredContent"]
    text = next((c["text"] for c in result.get("content", []) if c.get("type") == "text"), "null")
    return json.loads(text)


# ---------------------------------------------------------------------------------------------------------------
# Document agent (interfaces §3.3)
# ---------------------------------------------------------------------------------------------------------------


class FoundryDocumentExtractor:
    """Foundry prompt agent ``document-agent`` through the Responses API with an agent reference. [UNVERIFIED body
    shape: ``agent: {type: agent_reference, name}`` on ``{project}/openai/v1/responses``.] The BFF validates the
    output against document-output.schema.json."""

    def __init__(self, project_endpoint: str, agent: str, *, token: TokenProvider,
                 http: httpx.AsyncClient | None = None) -> None:
        """``project_endpoint`` is the Foundry project; ``agent`` is the document agent's name.

        ``token`` provides the Entra bearer token.
        """
        self.url, self.agent, self.token = project_endpoint.rstrip("/") + "/openai/v1/responses", agent, token
        self.http = http or httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=5.0))

    async def extract(self, content: bytes, content_type: str, purpose: str) -> dict[str, Any]:
        """Send the document and its ``purpose`` to the agent and return its JSON output.

        The output is not validated here: ``service.check_document`` does that. A PDF goes as ``input_file``, an image
        as ``input_image``. Raises UpstreamError on an HTTP error.
        """
        data_url = f"data:{content_type};base64,{base64.b64encode(content).decode()}"
        part = ({"type": "input_file", "filename": "document.pdf", "file_data": data_url}
                if content_type == "application/pdf" else {"type": "input_image", "image_url": data_url})
        body = {"agent": {"type": "agent_reference", "name": self.agent},
                "input": [{"role": "user", "content": [{"type": "input_text", "text": f"purpose: {purpose}"}, part]}]}
        resp = await self.http.post(self.url, json=body, headers=await _headers(self.token))
        if resp.status_code >= 400:
            raise UpstreamError(f"document_http_{resp.status_code}", "document agent failed")
        out = resp.json()
        text = out.get("output_text") or next(
            (c["text"] for item in out.get("output", []) for c in item.get("content", []) if c.get("type") ==
             "output_text"), "{}")
        return json.loads(text)
