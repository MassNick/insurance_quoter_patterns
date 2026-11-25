"""The MCP server in-process: an SDK client over memory and the ASGI app over HTTP, with stubs and fakes behind it."""

import copy
import json
import logging

import httpx
import pytest
from helpers import KEY, SESSION, env, make_deps, make_state, ref
from mcp import Client
from quote_common import session_ref
from quote_common.contracts import load_tools
from quote_common.logs import JsonFormatter
from starlette.testclient import TestClient

from mcp_quoting.adapters import CosmosStateReader, HttpApi, HttpBffStateReader, HttpRatingEngine
from mcp_quoting.server import build_server, create_app
from mcp_quoting.tools import HANDLERS

pytestmark = pytest.mark.component

CONTRACT = {t["name"].split(".", 1)[1]: t for t in load_tools()["tools"] if t["server"] == "quoting"}  # by tool name
VIN = "2T3W1RFV7MC123456"  # a VIN with a valid check digit
CUSTOMER_EMAIL = "marie@example.test"  # a customer value that must never reach the logs


def everything_logged(records):
    """The log records as they are before the shared formatter masks PII, so a leak cannot hide behind redaction."""
    return " ".join(f"{r.getMessage()} {vars(r)}" for r in records)


def body(result):
    """The JSON the model reads; it must equal the structured content."""
    payload = json.loads(result.content[0].text)
    assert payload == result.structured_content
    return payload


async def call(client, name, **args):
    """Call the tool `name` with `args` as its arguments."""
    return await client.call_tool(name, args)


# --------------------------------------------------------------------------- the tool list is the contract
@pytest.mark.anyio
async def test_list_tools_is_tools_json_plus_the_optional_session_ref():
    async with Client(build_server(make_deps())) as client:
        tools = (await client.list_tools()).tools
    assert (
        sorted(t.name for t in tools)
        == sorted(CONTRACT)
        == [
            "create_quote",
            "decode_vin",
            "identify_address",
            "identify_vehicle_type",
            "lookup_make_model_year",
            "modify_quote",
        ]
    )
    for tool in tools:
        schema = copy.deepcopy(tool.input_schema)
        assert schema["properties"].pop("session_ref") == {"type": "string"} and "session_ref" not in schema.get(
            "required", []
        )
        assert json.dumps(schema, sort_keys=True) == json.dumps(
            CONTRACT[tool.name]["inputSchema"], sort_keys=True
        )  # byte-equal
        assert tool.description == CONTRACT[tool.name]["description"]


def test_a_tool_without_a_handler_stops_the_server_at_startup(monkeypatch):
    from mcp_quoting import server

    monkeypatch.delitem(server.HANDLERS, "decode_vin")
    with pytest.raises(RuntimeError, match="decode_vin"):
        build_server(make_deps())


# --------------------------------------------------------------------------- every tool
@pytest.mark.anyio
async def test_every_tool_answers_with_json_text():
    async with Client(build_server(make_deps())) as client:
        address = await call(client, "identify_address", query="125 rue des Érables", postal_code="H7N 1A1")
        vehicle_type = await call(client, "identify_vehicle_type", make="Lamborghini", model="Urus", year=2021)
        lookup = await call(client, "lookup_make_model_year", year=2021, make="toyota", model="rav4")
        vin = await call(client, "decode_vin", vin=VIN)
        created = await call(client, "create_quote", session_ref=ref(), offer_types=["essential", "plus", "premium"])
        quote = body(created)
        modified = await call(
            client, "modify_quote", session_ref=ref(), quote_id=quote["quote_id"], changes={"deductible": 1000}
        )
    assert not any(r.is_error for r in (address, vehicle_type, lookup, vin, created, modified))
    assert body(address)["candidates"][0]["city"] == "Laval"
    assert body(vehicle_type) == {"vehicle_type": "suv", "body_style": "suv", "confidence": 0.98}
    assert body(lookup) == {"valid": True, "normalized": {"year": 2021, "make": "Toyota", "model": "RAV4"}}
    assert body(vin) == {"valid_checksum": True, "year": 2021, "make": "Toyota", "model": "RAV4", "trim": "Base"}
    assert quote["version"] == 1 and [o["offer_type"] for o in quote["offers"]] == ["essential", "plus", "premium"]
    again = body(modified)
    assert (again["quote_id"], again["version"], again["previous_version"], again["delta_monthly"]) == (
        quote["quote_id"],
        2,
        1,
        -7.75,
    )


@pytest.mark.anyio
async def test_the_model_reads_french_text_with_accents_kept():
    async with Client(build_server(make_deps())) as client:
        result = await call(client, "create_quote", session_ref=ref())
    text = result.content[0].text
    assert text == json.dumps(body(result), ensure_ascii=False, separators=(",", ":"))  # compact, accents literal
    assert "Responsabilité civile" in text and "Voiture de location jusqu’à 1 200 $" in text


# --------------------------------------------------------------------------- refusals
@pytest.mark.anyio
async def test_quote_tools_reject_forged_expired_and_missing_session_refs():
    async with Client(build_server(make_deps())) as client:
        forged = await call(client, "create_quote", session_ref=session_ref.sign(SESSION, b"another-key"))
        expired = await call(
            client, "create_quote", session_ref=session_ref.sign(SESSION, KEY, ttl_seconds=60, now=1000)
        )
        absent = await call(client, "modify_quote", quote_id="Q-1", changes={})
    for result in (forged, expired, absent):
        assert result.is_error and body(result)["error"] == "invalid_session_ref"
    assert "expired" in body(expired)["message"]


@pytest.mark.anyio
async def test_create_quote_refuses_with_the_missing_fields():
    deps = make_deps(
        make_state(
            {"/quote/email": None, "/underwriting/has_claims": env(True, status="pending_confirmation", source="chat")}
        )
    )
    async with Client(build_server(deps)) as client:
        result = await call(client, "create_quote", session_ref=ref())
    refusal = body(result)
    assert result.is_error and refusal["error"] == "missing_fields"
    assert [(m["path"], m["reason"]) for m in refusal["missing_fields"]] == [
        ("/quote/email", "missing"),
        ("/underwriting/has_claims", "pending_confirmation"),
    ]
    assert refusal["missing_fields"][0]["label"] == "Adresse courriel"


@pytest.mark.anyio
async def test_a_chat_value_is_priced_from_confidence_0_6_but_a_deterministic_one_never_is():
    def chat(value, confidence):
        """A field envelope inferred from chat at `confidence`."""
        return env(value, status="inferred", source="chat", confidence=confidence)

    states = {
        "sure": make_state({"/vehicles/0/usage_type": chat("commute", 0.9)}),
        "unsure": make_state({"/vehicles/0/usage_type": chat("commute", 0.5)}),
        "deterministic": make_state({"/underwriting/has_claims": chat(False, 0.99)}),
    }
    results = {}
    for name, state in states.items():
        async with Client(build_server(make_deps(state))) as client:
            results[name] = await call(client, "create_quote", session_ref=ref())
    assert not results["sure"].is_error and [o["annual"] for o in body(results["sure"])["offers"]] == [896, 1160, 1529]
    for name, key in (("unsure", "usage_type"), ("deterministic", "has_claims")):
        refusal = body(results[name])
        assert results[name].is_error and refusal["error"] == "missing_fields"
        assert [(m["key"], m["reason"]) for m in refusal["missing_fields"]] == [(key, "inferred")]


@pytest.mark.anyio
async def test_arguments_are_validated_against_the_contract_schema():
    async with Client(build_server(make_deps())) as client:
        cases = [
            ("decode_vin", {"vin": "not-a-vin"}),
            ("identify_address", {}),
            ("identify_vehicle_type", {"make": "Toyota"}),
            ("lookup_make_model_year", {"year": "2021"}),
            ("create_quote", {"session_ref": ref(), "offer_types": ["gold"]}),
            ("modify_quote", {"session_ref": ref(), "quote_id": "Q-1", "changes": {"deductible": "1000"}}),
            ("modify_quote", {"session_ref": ref(), "changes": {}}),
        ]
        results = [await client.call_tool(name, args) for name, args in cases]
        unknown = await client.call_tool("no_such_tool", {})
    for result in results:
        assert result.is_error and body(result)["error"] == "invalid_arguments"
    assert unknown.is_error and body(unknown)["error"] == "unknown_tool"


@pytest.mark.anyio
async def test_modify_quote_cannot_smuggle_field_values():
    deps = make_deps()
    async with Client(build_server(deps)) as client:
        quote = body(await call(client, "create_quote", session_ref=ref()))
        result = await call(
            client,
            "modify_quote",
            session_ref=ref(),
            quote_id=quote["quote_id"],
            changes={"vehicle_year": 1950, "deductible": 500},
        )
    assert result.is_error and body(result)["error"] == "invalid_changes"
    assert (await deps.quotes.latest(SESSION, quote["quote_id"]))["version"] == 1


@pytest.mark.anyio
async def test_unknown_quote_and_unknown_session():
    deps = make_deps()
    async with Client(build_server(deps)) as client:
        no_quote = await call(client, "modify_quote", session_ref=ref(), quote_id="Q-NOPE", changes={})
        no_session = await call(client, "create_quote", session_ref=ref("s_unknown"))
    assert body(no_quote)["error"] == "unknown_quote" and body(no_session)["error"] == "session_not_found"


# --------------------------------------------------------------------------- the BFF state reader behind the server
def bff_deps(handler):
    """Deps on stubs whose state comes from a BFF that `handler` plays (an httpx mock transport)."""
    api = HttpApi("http://bff.test", transport=httpx.MockTransport(handler), retries=1, backoff=0)
    return make_deps(state=HttpBffStateReader(api))


def bff_answer(state, quote_blocked=False):
    """What the BFF's internal state endpoint answers."""
    return {
        "session_id": SESSION,
        "phase": "gather",
        "state": state,
        "quote_blocked": quote_blocked,
        "missing_fields": [],
    }


@pytest.mark.anyio
async def test_create_quote_reads_the_state_from_the_bff_with_the_session_ref():
    seen = []

    def bff(request):
        """The BFF: record the path and the `Authorization` header, answer with the committed state."""
        seen.append((request.url.path, request.headers["Authorization"]))
        return httpx.Response(200, json=bff_answer(make_state()))

    token = ref()
    async with Client(build_server(bff_deps(bff))) as client:
        result = await call(client, "create_quote", session_ref=token)
    assert [o["annual"] for o in body(result)["offers"]] == [896, 1160, 1529]
    assert seen == [(f"/internal/sessions/{SESSION}/state", f"SessionRef {token}")]


@pytest.mark.anyio
async def test_a_session_the_bff_blocks_is_not_priced_until_the_stop_is_lifted():
    answers = iter(
        [
            bff_answer(make_state(), quote_blocked=True),
            bff_answer(make_state(), quote_blocked=True),
            bff_answer(make_state()),
        ]
    )

    def bff(request):
        """The BFF: answer each call with the next prepared answer."""
        return httpx.Response(200, json=next(answers))

    async with Client(build_server(bff_deps(bff))) as client:
        created = await call(client, "create_quote", session_ref=ref())
        modified = await call(client, "modify_quote", session_ref=ref(), quote_id="Q-1", changes={})
        priced = await call(client, "create_quote", session_ref=ref())
    for refused in (created, modified):
        assert refused.is_error and body(refused)["error"] == "quote_blocked" and "STOP" in body(refused)["message"]
    assert not priced.is_error and body(priced)["version"] == 1


@pytest.mark.anyio
async def test_bff_failures_become_readable_errors():
    async with Client(build_server(bff_deps(lambda request: httpx.Response(404)))) as client:
        missing = await call(client, "create_quote", session_ref=ref())
    async with Client(build_server(bff_deps(lambda request: httpx.Response(500)))) as client:
        broken = await call(client, "create_quote", session_ref=ref())
    assert body(missing)["error"] == "session_not_found"
    assert broken.is_error and body(broken)["error"] == "upstream_error" and "500" not in broken.content[0].text


# --------------------------------------------------------------------------- the Cosmos state reader behind the server
class Container:
    """The `sessions` container: one session document."""

    def __init__(self, document):
        """`document` is the one session document the container holds."""
        self.document = document

    async def read_item(self, item, partition_key):
        """The document, after checking that the session id is both the item id and the partition key."""
        assert item == partition_key == SESSION
        return self.document


def cosmos_deps(**fields):
    """Deps whose state reader is the Cosmos reader on a container holding the default state plus `fields`."""
    return make_deps(
        state=CosmosStateReader(Container({**make_state(), "session_id": SESSION, "phase": "gather", **fields}))
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "blocking",
    [
        {"stop": {"rule_id": "JEUNE_CONDUCTEUR_PRINCIPAL", "reason": "...", "paths": []}, "rules_error": False},
        {"rules_error": True},
    ],
)
async def test_a_cosmos_session_with_a_stop_or_a_rules_error_is_not_priced(blocking):
    async with Client(build_server(cosmos_deps(**blocking))) as client:
        result = await call(client, "create_quote", session_ref=ref())
    assert result.is_error and body(result)["error"] == "quote_blocked"


@pytest.mark.anyio
async def test_a_cosmos_session_without_a_stop_is_priced():
    async with Client(build_server(cosmos_deps(stop=None, rules_error=False))) as client:
        result = await call(client, "create_quote", session_ref=ref())
    assert not result.is_error and [o["annual"] for o in body(result)["offers"]] == [896, 1160, 1529]


@pytest.mark.anyio
async def test_a_rating_answer_that_breaks_the_offer_contract_is_an_upstream_error():
    api = HttpApi(
        "http://rating.test",
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"offers": [{"offer_type": "gold"}]})),
    )
    async with Client(build_server(make_deps(rating=HttpRatingEngine(api)))) as client:
        result = await call(client, "create_quote", session_ref=ref())
    assert result.is_error and body(result)["error"] == "upstream_error" and "gold" not in result.content[0].text


@pytest.mark.anyio
async def test_a_crashing_adapter_is_an_internal_error_and_its_message_is_not_logged(caplog):
    class Broken:
        """A state reader that crashes with a message that holds a customer value."""

        async def read(self, session_id, session_ref):
            """Always crash."""
            raise RuntimeError(f"cannot read the session of {CUSTOMER_EMAIL}")  # frames show this line, not the value

    async with Client(build_server(make_deps(state=Broken()))) as client:
        result = await call(client, "create_quote", session_ref=ref())
    assert result.is_error and body(result) == {"error": "internal_error", "message": "The tool failed unexpectedly"}
    crash = next(r for r in caplog.records if "crashed" in r.getMessage())
    assert "RuntimeError" in crash.getMessage() and "read" in "".join(
        crash.frames
    )  # type and frames, never the message
    assert CUSTOMER_EMAIL not in everything_logged(caplog.records)
    assert CUSTOMER_EMAIL not in " ".join(JsonFormatter().format(r) for r in caplog.records)


# --------------------------------------------------------------------------- logs
@pytest.mark.anyio
async def test_logs_name_the_tool_and_outcome_but_never_values(caplog):
    caplog.set_level(logging.INFO, logger="mcp_quoting")
    deps = make_deps(make_state({"/quote/email": None}))
    async with Client(build_server(deps)) as client:
        await call(client, "identify_address", query="777 Rue-Marqueur-Unique", postal_code="Z9Z 9Z9")
        await call(client, "create_quote", session_ref=ref())
    calls = [r for r in caplog.records if r.getMessage() == "tool call"]
    assert [(r.tool, r.outcome) for r in calls] == [("identify_address", "ok"), ("create_quote", "missing_fields")]
    logged = everything_logged(caplog.records)
    assert "Marqueur" not in logged and "Z9Z" not in logged and "Marie" not in logged
    line = json.loads(JsonFormatter().format(calls[0]))  # the shared format: ts, level, logger, msg, then the extras
    assert (line["level"], line["logger"], line["msg"]) == ("INFO", "mcp_quoting", "tool call")
    assert (line["tool"], line["outcome"]) == ("identify_address", "ok") and isinstance(line["ms"], int) and line["ts"]


# --------------------------------------------------------------------------- the ASGI app over HTTP
# The headers the streamable HTTP transport requires of a client.
HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def rpc(client, method, params, request_id=1):
    """POST one JSON-RPC request to `/mcp` and return its `result`, after checking for a 200 JSON response."""
    response = client.post(
        "/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": request_id, "method": method, "params": params}
    )
    assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
    return response.json()["result"]


def test_healthz():
    with TestClient(create_app(make_deps())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}


def test_streamable_http_serves_tools_statelessly():
    with TestClient(create_app(make_deps())) as client:
        init = rpc(
            client,
            "initialize",
            {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
        )
        assert init["serverInfo"]["name"] == "mcp-quoting"
        listed = rpc(client, "tools/list", {}, 2)["tools"]
        assert len(listed) == 6 and {t["name"] for t in listed} == set(CONTRACT)
        called = rpc(client, "tools/call", {"name": "create_quote", "arguments": {"session_ref": ref()}}, 3)
    assert called["isError"] is False and json.loads(called["content"][0]["text"])["version"] == 1
    assert called["structuredContent"]["offers"][1]["offer_type"] == "plus"


def test_http_tool_errors_are_results_not_protocol_errors():
    with TestClient(create_app(make_deps())) as client:
        called = rpc(client, "tools/call", {"name": "create_quote", "arguments": {"session_ref": "forged"}})
    assert called["isError"] is True and called["structuredContent"]["error"] == "invalid_session_ref"


def test_the_entra_check_is_off_by_default_and_answers_401_without_a_token_when_enabled(monkeypatch):
    monkeypatch.delenv("ENTRA_AUDIENCE", raising=False)
    with TestClient(create_app(make_deps())) as client:  # unset: the app serves as before
        assert len(rpc(client, "tools/list", {})["tools"]) == 6

    monkeypatch.setenv("ENTRA_TENANT_ID", "00000000-0000-0000-0000-000000000000")  # a fake tenant
    monkeypatch.setenv("ENTRA_AUDIENCE", "api://mcp-quoting")
    app = create_app(make_deps())
    assert app.default_roles == {"Tools.Invoke", "Lookup.Read"}  # lookups: either role
    assert app.tool_roles == {"create_quote": {"Tools.Invoke"}, "modify_quote": {"Tools.Invoke"}}
    assert set(app.tool_roles) <= set(HANDLERS)  # the roles name real tools
    with TestClient(app) as client:
        assert client.get("/healthz").status_code == 200  # never protected
        denied = client.post("/mcp", headers=HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert denied.status_code == 401 and denied.json()["error"] == "unauthorized"
