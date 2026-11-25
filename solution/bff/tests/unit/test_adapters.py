"""Unit: stores (in memory, Cosmos with a fake SDK), the MCP call helper, the hosted-agent SSE client, the document
extractor, the event emitter, the invocation body, document checks, the cookie and log redaction. No network."""

from __future__ import annotations

import json
import re
from typing import Any

import httpx
import pytest

from quote_bff import state
from quote_bff.app import cookie_session, cookie_value, sniff
from quote_bff.clients import (
    FoundryDocumentExtractor,
    HostedAgentClient,
    McpClient,
    UpstreamError,
    a2ui_messages,
    tool_value,
)
from quote_bff.fakes import CANNED_DOCUMENTS, FakeA2ui
from quote_bff.service import Turn, check_document, invocation_body, traceparent
from quote_bff.stores import Conflict, CosmosStore, InMemoryStore

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    """Run the anyio tests on asyncio only."""
    return "asyncio"


# -- stores -------------------------------------------------------------------------------------------------------


async def test_in_memory_store_enforces_etags_and_create_only() -> None:
    store = InMemoryStore()
    doc = await store.put("sessions", {"id": "s", "session_id": "s", "n": 1}, create=True)
    with pytest.raises(Conflict):
        await store.put("sessions", {"id": "s", "session_id": "s"}, create=True)
    updated = await store.put("sessions", {**doc, "n": 2}, etag=doc["_etag"])
    with pytest.raises(Conflict):
        await store.put("sessions", {**doc, "n": 3}, etag=doc["_etag"])  # stale etag
    assert (await store.get("sessions", "s", "s"))["n"] == 2 and updated["_etag"] != doc["_etag"]
    assert await store.get("sessions", "s", "nope") is None
    await store.put("quotes", {"id": "q:v1", "session_id": "s", "quote_id": "q", "version": 1})
    assert len(await store.query("quotes", session_id="s", version=1)) == 1
    assert await store.query("quotes", session_id="s", version=2) == []


class CosmosError(Exception):
    """Stands in for the SDK's HTTP error: only ``status_code`` matters to CosmosStore."""

    def __init__(self, status_code: int) -> None:
        """Carry the HTTP status the store looks at."""
        super().__init__(status_code)
        self.status_code = status_code


class FakeContainer:
    """An in-memory Cosmos container client: the SDK's method names and its 404, 409 and 412 answers."""

    def __init__(self) -> None:
        """``items`` by id; ``queries`` records each ``(query, parameters)``."""
        self.items: dict[str, dict[str, Any]] = {}
        self.queries: list[tuple[str, list]] = []

    async def read_item(self, item: str, partition_key: str) -> dict[str, Any]:
        """The item, or a 404."""
        if item not in self.items:
            raise CosmosError(404)
        return dict(self.items[item])

    async def create_item(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create; a 409 when the id exists."""
        if body["id"] in self.items:
            raise CosmosError(409)
        return self._save(body)

    async def replace_item(self, item: str, body: dict[str, Any], etag: str, match_condition: Any) -> dict[str, Any]:
        """Replace; a 412 when the etag differs."""
        if self.items[item]["_etag"] != etag:
            raise CosmosError(412)
        return self._save(body)

    async def upsert_item(self, body: dict[str, Any]) -> dict[str, Any]:
        """Create or replace, with no check."""
        return self._save(body)

    def query_items(self, query: str, parameters: list) -> Any:
        """Record the query and return an async iterator over every item (the filter itself is not run)."""
        self.queries.append((query, parameters))

        async def rows() -> Any:
            """An async generator over every item of the container."""
            for item in self.items.values():
                yield item

        return rows()

    def _save(self, body: dict[str, Any]) -> dict[str, Any]:
        """Store the body under a new ``_etag`` and return a copy."""
        self.items[body["id"]] = {**body, "_etag": f"e{len(self.items)}-{body.get('n')}"}
        return dict(self.items[body["id"]])


class FakeDatabase:
    """A database client that hands out one FakeContainer per name."""

    def __init__(self) -> None:
        """``containers`` by name, created on first use."""
        self.containers: dict[str, FakeContainer] = {}

    def get_container_client(self, name: str) -> FakeContainer:
        """The container of that name, created on first use."""
        return self.containers.setdefault(name, FakeContainer())


async def test_cosmos_store_maps_the_sdk_to_the_store_protocol() -> None:
    db = FakeDatabase()
    store = CosmosStore(db)
    assert await store.get("sessions", "s", "s") is None
    doc = await store.put("sessions", {"id": "s", "session_id": "s", "n": 1}, create=True)
    with pytest.raises(Conflict):
        await store.put("sessions", {"id": "s", "session_id": "s"}, create=True)
    await store.put("sessions", {**doc, "n": 2}, etag=doc["_etag"])
    with pytest.raises(Conflict):
        await store.put("sessions", {**doc, "n": 3}, etag=doc["_etag"])
    assert (await store.get("sessions", "s", "s"))["n"] == 2
    rows = await store.query("handoffs", id="CNS-123456")
    query, params = db.containers["handoffs"].queries[0]
    assert query == "SELECT * FROM c WHERE c.id = @id" and params == [{"name": "@id", "value": "CNS-123456"}]
    assert rows == []


async def test_cosmos_store_lets_other_errors_out() -> None:
    db = FakeDatabase()

    async def boom(**_: Any) -> None:
        raise CosmosError(503)

    db.get_container_client("events").create_item = boom
    with pytest.raises(CosmosError):
        await CosmosStore(db).put("events", {"id": "1", "session_id": "s"}, create=True)


# -- MCP call helper ------------------------------------------------------------------------------------------------


def mcp_transport(result: dict[str, Any], *, sse: bool) -> tuple[httpx.MockTransport, list[httpx.Request]]:
    """An httpx mock transport that plays an MCP server, with plain JSON or SSE replies, and the requests it saw."""
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        """Answer DELETE with 200 and a notification with 202.

        Initialize gets the protocol version; any other request gets ``result``.
        """
        seen.append(request)
        if request.method == "DELETE":
            return httpx.Response(200)
        msg = json.loads(request.content)
        if "id" not in msg:
            return httpx.Response(202)
        payload = {"protocolVersion": "2025-06-18", "capabilities": {}} if msg["method"] == "initialize" else result
        reply = {"jsonrpc": "2.0", "id": msg["id"], "result": payload}
        headers = {"mcp-session-id": "m-1"}
        if sse:
            return httpx.Response(200, text=f"event: message\ndata: {json.dumps(reply)}\n\n",
                                  headers={**headers, "content-type": "text/event-stream"})
        return httpx.Response(200, json=reply, headers=headers)

    return httpx.MockTransport(handler), seen


@pytest.mark.parametrize("sse", [False, True])
async def test_mcp_call_initializes_then_calls_the_tool(sse: bool) -> None:
    rendered = await FakeA2ui().call("render_form", {"form_spec": {"form_id": "x_form", "title": "T", "fields": [
        {"key": "/quote/email"}]}})
    transport, seen = mcp_transport(rendered, sse=sse)

    async def token() -> str:
        return "dev-only-token"

    client = McpClient("http://mcp.local/mcp", token=token, http=httpx.AsyncClient(transport=transport))
    result = await client.call("render_form", {"form_spec": {}})
    assert a2ui_messages(result)[0]["createSurface"]["surfaceId"] == "x_form"
    methods = [json.loads(r.content).get("method") if r.content else r.method for r in seen]
    assert methods == ["initialize", "notifications/initialized", "tools/call", "DELETE"]
    assert seen[2].headers["mcp-session-id"] == "m-1" and seen[2].headers["mcp-protocol-version"] == "2025-06-18"
    assert seen[0].headers["authorization"] == "Bearer dev-only-token"
    assert "text/event-stream" in seen[0].headers["accept"]


async def test_mcp_tool_errors_and_value_parsing() -> None:
    transport, _ = mcp_transport({"content": [{"type": "text", "text": "boom"}], "isError": True}, sse=False)
    with pytest.raises(UpstreamError):
        await McpClient("http://m/mcp", http=httpx.AsyncClient(transport=transport)).call("decode_vin", {})
    assert tool_value({"content": [{"type": "text", "text": '{"valid": true}'}]}) == {"valid": True}
    assert tool_value({"structuredContent": {"a": 1}, "content": []}) == {"a": 1}
    with pytest.raises(UpstreamError):
        a2ui_messages({"content": [{"type": "text", "text": "{}"}]})


# -- hosted agent -------------------------------------------------------------------------------------------------


async def test_hosted_agent_streams_sse_envelopes_and_retries_424() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(424, headers={"retry-after": "0"})
        body = ': keepalive\n\ndata: {"type":"turn.started","data":{}}\n\ndata: {"type":"text.delta",\n' \
               'data: "data":{"delta":"Bon"}}\n\ndata: {"type":"turn.completed","data":{}}'
        return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})

    client = HostedAgentClient("https://proj.example/agents/quote-agent", foundry=True, retry_delay=0,
                               http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    events = [e async for e in client.invoke({"input": {"kind": "message"}}, session_id="s_1", turn_id="s_1:3",
                                             traceparent="00-a-b-01")]
    assert [e["type"] for e in events] == ["turn.started", "text.delta", "turn.completed"]
    assert events[1]["data"] == {"delta": "Bon"}
    url = calls[-1].url
    assert url.path == "/agents/quote-agent/endpoint/protocols/invocations"
    assert dict(url.params) == {"api-version": "v1", "agent_session_id": "s_1"}
    assert calls[-1].headers["x-client-turn-id"] == "s_1:3" and calls[-1].headers["traceparent"] == "00-a-b-01"


async def test_hosted_agent_errors_become_upstream_errors() -> None:
    client = HostedAgentClient("http://localhost:8088", http=httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(500))))
    with pytest.raises(UpstreamError) as err:
        [e async for e in client.invoke({}, session_id="s", turn_id="s:1", traceparent="t")]
    assert err.value.code == "agent_http_500" and err.value.retryable


# -- document agent -------------------------------------------------------------------------------------------------


async def test_document_extractor_sends_the_agent_reference_and_reads_output_text() -> None:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"output": [{"content": [
            {"type": "output_text", "text": json.dumps(CANNED_DOCUMENTS["vin"])}]}]})

    async def token() -> str:
        return "dev-only-token"

    extractor = FoundryDocumentExtractor("https://proj.example", "document-agent", token=token,
                                         http=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    out = await extractor.extract(b"%PDF-1", "application/pdf", "vin")
    assert out["purpose"] == "vin"
    assert seen[0]["agent"] == {"type": "agent_reference", "name": "document-agent"}
    assert seen[0]["input"][0]["content"][1]["type"] == "input_file"


def test_document_checks_drop_nulls_before_validating() -> None:
    out = {"purpose": "vin", "document_ok": True, "reason": None,
           "fields": {"vin": {"value": "2T3P1RFV8MC123456", "confidence": 0.9, "page": None}, "plate": None}}
    assert check_document(out) == {"purpose": "vin", "document_ok": True,
                                   "fields": {"vin": {"value": "2T3P1RFV8MC123456", "confidence": 0.9}}}


def test_document_checks_redact_card_numbers_and_reject_off_schema_output() -> None:
    out = {"purpose": "declaration", "document_ok": True,
           "fields": {"current_insurer": {"value": "4111 1111 1111 1111", "confidence": 0.9}}}
    assert check_document(out)["fields"]["current_insurer"]["value"] == "redacted"
    with pytest.raises(UpstreamError):
        check_document({"purpose": "vin", "document_ok": True, "fields": {"iban": {"value": 1, "confidence": 1}}})
    for canned in CANNED_DOCUMENTS.values():
        check_document(canned)


# -- emitter, body, cookie, uploads, logs ---------------------------------------------------------------------------


async def test_the_emitter_assigns_seq_persists_then_forwards() -> None:
    store = InMemoryStore()
    t = Turn(store, "s_1", 4)
    await t.emit("text.done", {"text": "a"})
    await t.emit("memory.result", {}, "memory_agent", forward=False)
    await t.emit("text.done", {"text": "b"})
    t.close()
    frames = [f async for f in t.stream()]
    assert [json.loads(f[6:])["seq"] for f in frames] == [1, 3]
    rows = sorted(store.data["events"].values(), key=lambda r: r["seq"])
    assert [(r["id"], r["forwarded"]) for r in rows] == [("4:1", True), ("4:2", False), ("4:3", True)]


def test_invocation_body_carries_preferences_rules_notes_and_open_surfaces() -> None:
    d = state.new_session("s_1", "AUTO-1", "fr-CA")
    d["session_preferences"]["answer_mode"] = "forms"
    d["rule_hits"] = [{"rule_id": "X", "severity": "flag", "paths": [], "inferred": False}]
    d["system_notes"] = ["Form 'a' edited"]
    d["surfaces"] = {"a": {"status": "submitted"}, "b": {"status": "open"}}
    body = invocation_body(d, 7, {"kind": "message", "text": "hi"}, "ref")
    assert body == {"turn": 7, "input": {"kind": "message", "text": "hi"}, "state": state.state_of(d),
                    "phase": "intent", "open_surfaces": ["b"], "signals": [], "preferences": {"answer_mode": "forms"},
                    "rule_hits": d["rule_hits"], "system_notes": ["Form 'a' edited"], "session_ref": "ref"}
    body["system_notes"].append("x")
    assert d["system_notes"] == ["Form 'a' edited"]


def test_the_cookie_is_signed() -> None:
    value = cookie_value("s_1", "dev-only-key")
    assert value.startswith("s_1.") and cookie_session(value, "dev-only-key") == "s_1"
    assert cookie_session(value, "dev-only-other-key") is None
    assert cookie_session("s_1.forged", "dev-only-key") is None and cookie_session(None, "k") is None


def test_uploads_are_sniffed() -> None:
    assert sniff(b"%PDF-1.7") == "application/pdf" and sniff(b"\xff\xd8\xff\xe0") == "image/jpeg"
    assert sniff(b"\0\0\0\x18ftypheic") == "image/heic" and sniff(b"MZ") is None


def test_a_fresh_traceparent_without_an_sdk() -> None:  # redaction itself is tested in quote_common
    assert re.fullmatch(r"00-[0-9a-f]{32}-[0-9a-f]{16}-01", traceparent())
