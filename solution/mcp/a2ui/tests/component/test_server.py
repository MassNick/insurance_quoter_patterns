"""The MCP server in-process: the SDK client over memory for the tools, the ASGI app over HTTP for the transport."""

import copy
import json
from contextlib import asynccontextmanager

import httpx2
import pytest
from jsonschema import Draft202012Validator
from mcp import Client
from mcp.client.session import ClientSession
from mcp.client.streamable_http import streamable_http_client
from mcp.types import EmbeddedResource, TextContent
from quote_common.contracts import load_tools

from mcp_a2ui import conformance
from mcp_a2ui.server import build_server, create_app

pytestmark = [pytest.mark.component, pytest.mark.anyio]
SESSION_REF = "dev-only-session-ref-never-echoed"
FORM_SPEC = {
    "form_id": "about_you",
    "title": "À propos de vous",
    "layout": "single",
    "fields": [{"key": "first_name"}, {"key": "email"}],
}
STATE = {
    "quote": {
        "contact_consent": {"value": True, "source": "chat", "status": "confirmed"},
        "first_name": {"value": "Marie", "source": "chat", "status": "confirmed", "evidence": "Je m’appelle Marie"},
        "email": {"value": "m@example.com", "source": "form", "status": "confirmed"},
    }
}
OFFERS = [
    {
        "offer_type": "essential",
        "monthly": 118.4,
        "annual": 1420.8,
        "deductible": 1000,
        "coverages": ["Responsabilité"],
    },
    {"offer_type": "plus", "monthly": 142.75},
]
CONTRACT = {t["name"].removeprefix("a2ui."): t for t in load_tools()["tools"] if t["server"] == "a2ui"}


async def call(tool, **arguments):
    """One tool call; the hosted agent's ``session_ref`` rides along on every call."""
    async with Client(build_server()) as client:
        return await client.call_tool(tool, {"session_ref": SESSION_REF, **arguments})


def a2ui(result, surface_id, status):
    """Check what every tool returns and give back the A2UI messages: a summary for the model, the messages for the user."""
    assert not result.is_error
    summary, resource = result.content
    assert isinstance(summary, TextContent) and json.loads(summary.text) == {"surface_id": surface_id, "status": status}
    assert isinstance(resource, EmbeddedResource)
    assert resource.annotations.audience == ["user"]
    assert resource.resource.mime_type == "application/a2ui+json"
    assert resource.resource.uri == f"a2ui://surfaces/{surface_id}"
    assert "version" not in summary.text and "components" not in summary.text  # the A2UI payload stays out of the text
    assert SESSION_REF not in result.model_dump_json()
    messages = json.loads(resource.resource.text)
    assert conformance.check(messages) == []
    return messages


def refused(result, code="invalid_arguments"):
    """The message of an error result, ``{"error": code, "message": ...}``: no resource, and session_ref is not echoed."""
    assert result.is_error and len(result.content) == 1 and SESSION_REF not in result.model_dump_json()
    body = json.loads(result.content[0].text)
    assert body["error"] == code
    return body["message"]


# --- the tool list -------------------------------------------------------------------------------------------------


async def test_the_tool_list_is_tools_json_plus_the_injected_arguments_as_optional_properties():
    async with Client(build_server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == set(CONTRACT) == {"render_form", "update_form", "close_form", "render_offers"}
    for name, tool in tools.items():
        schema, contract = copy.deepcopy(tool.input_schema), CONTRACT[name]
        Draft202012Validator.check_schema(schema)
        assert tool.description == contract["description"]
        injected = {"session_ref", *contract.get("injected_args", {})}
        assert injected <= set(schema["properties"]) and not injected & set(
            schema["required"]
        )  # optional: clients forward them
        for prop in injected:
            del schema["properties"][prop]
        schema.pop("$defs", None)
        if "form_spec" in schema["properties"]:
            schema["properties"]["form_spec"] = {
                "$ref": "form-spec.schema.json"
            }  # in the tool list it is the FormSpec schema
        assert schema == contract["inputSchema"]


async def test_the_model_learns_the_form_spec_format_from_the_render_form_schema():
    async with Client(build_server()) as client:
        schema = next(t for t in (await client.list_tools()).tools if t.name == "render_form").input_schema
    assert schema["properties"]["form_spec"]["required"] == ["form_id", "title", "fields"]
    assert "section" in schema["$defs"]["field"]["properties"]["widget"]["enum"]


# --- the transport -------------------------------------------------------------------------------------------------


@asynccontextmanager
async def running():
    """An HTTP client on the ASGI app, which runs in-process with its lifespan."""
    app = create_app()
    transport = httpx2.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http,
    ):
        yield http


async def test_healthz():
    async with running() as http:
        response = await http.get("/healthz")
    assert (response.status_code, response.json()) == (200, {"status": "ok"})


async def test_the_bffs_json_rpc_sequence_gets_json_responses():
    headers = {"Accept": "application/json, text/event-stream"}
    hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "quote-bff", "version": "1"}}
    arguments = {"form_spec": FORM_SPEC, "state": STATE, "mode": "edit", "session_ref": SESSION_REF}
    call_tool = {
        "jsonrpc": "2.0",
        "id": 2,
        "method": "tools/call",
        "params": {"name": "render_form", "arguments": arguments},
    }
    async with running() as http:
        init = await http.post(
            "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello}
        )
        headers["mcp-protocol-version"] = init.json()["result"]["protocolVersion"]
        await http.post("/mcp", headers=headers, json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        reply = await http.post("/mcp", headers=headers, json=call_tool)
    assert all(r.headers["content-type"].startswith("application/json") for r in (init, reply))
    assert "mcp-session-id" not in reply.headers  # stateless
    result = reply.json()["result"]
    resource = next(c["resource"] for c in result["content"] if c["type"] == "resource")
    assert (resource["mimeType"], result.get("isError")) == ("application/a2ui+json", False)
    (message,) = json.loads(resource["text"])
    assert message["createSurface"]["dataModel"]["quote"]["first_name"] == "Marie"
    assert [c["text"] for c in message["createSurface"]["components"] if c["id"] == "action_submit_label"] == [
        "Enregistrer"
    ]


async def test_the_entra_check_is_off_unless_the_audience_is_set_and_then_a_token_is_required(monkeypatch):
    headers = {"Accept": "application/json, text/event-stream"}
    hello = {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"}}
    initialize = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": hello}
    async with running() as http:  # ENTRA_AUDIENCE is unset: open
        assert (await http.post("/mcp", headers=headers, json=initialize)).status_code == 200
    monkeypatch.setenv("ENTRA_TENANT_ID", "dev-only-tenant")
    monkeypatch.setenv("ENTRA_AUDIENCE", "api://dev-only-mcp-a2ui")
    transport = httpx2.ASGITransport(app=create_app())
    async with httpx2.AsyncClient(transport=transport, base_url="http://testserver") as http:
        assert (await http.post("/mcp", headers=headers, json=initialize)).status_code == 401
        assert (await http.get("/healthz")).status_code == 200  # the platform's probe has no token
        denied = await http.post("/mcp", headers={**headers, "Authorization": "Bearer not-a-token"}, json=initialize)
    assert denied.status_code == 401


async def test_the_sdks_streamable_http_client_works_over_the_app():
    async with running() as http, streamable_http_client("http://testserver/mcp", http_client=http) as streams:
        async with ClientSession(*streams) as session:
            await session.initialize()
            result = await session.call_tool("render_form", {"form_spec": FORM_SPEC, "session_ref": SESSION_REF})
    assert a2ui(result, "about_you", "shown")[0]["createSurface"]["surfaceId"] == "about_you"


# --- render_form ---------------------------------------------------------------------------------------------------


async def test_render_form_opens_one_prefilled_surface():
    (message,) = a2ui(await call("render_form", form_spec=FORM_SPEC, state=STATE), "about_you", "shown")
    surface = message["createSurface"]
    assert conformance.check([message], opens_surface=True) == []
    assert surface["dataModel"] == {
        "quote": {"first_name": "Marie", "email": "m@example.com"},
        "_provenance": {"/quote/first_name": {"source": "chat", "note": "Je m’appelle Marie"}},
    }
    assert {c["id"] for c in surface["components"]} >= {"root", "title", "text_1", "email_2", "actions"}


async def test_render_form_edit_mode_relabels_the_primary_button_and_keeps_the_others():
    actions = [
        {"id": "cancel", "label": "Annuler", "style": "secondary"},
        {"id": "submit", "label": "Go", "style": "primary"},
    ]
    result = await call("render_form", form_spec={**FORM_SPEC, "actions": actions}, mode="edit")
    (message,) = a2ui(result, "about_you", "shown")
    labels = {c["id"]: c["text"] for c in message["createSurface"]["components"] if c["id"].endswith("_label")}
    assert labels == {"action_cancel_label": "Annuler", "action_submit_label": "Enregistrer"}


async def test_render_form_names_every_problem_of_the_arguments_and_of_the_form_spec():
    bad = {
        "form_id": "about_you",
        "title": "T",
        "fields": [{"key": "email", "widget": "carousel"}, {"key": "x", "nope": 1}],
    }
    message = refused(await call("render_form", form_spec=bad))
    assert "form_spec.fields[0].widget: 'carousel' is not one of" in message
    assert "form_spec.fields[1]: Additional properties" in message
    assert "'form_spec' is a required property" in refused(await call("render_form"))
    assert "mode: 'draft' is not one of" in refused(await call("render_form", form_spec=FORM_SPEC, mode="draft"))
    unknown = {**FORM_SPEC, "fields": [{"key": "vehicle_yr"}, {"key": "email", "visible_if": "age > 3"}]}
    message = refused(await call("render_form", form_spec=unknown))
    assert "fields[0].key: unknown catalog key 'vehicle_yr'" in message
    assert "fields[1].visible_if: 'age > 3' is not supported" in message


async def test_an_unknown_tool_is_a_tool_error_not_a_protocol_error():
    assert refused(await call("render_everything"), "unknown_tool") == "No tool named render_everything"


# --- update_form ---------------------------------------------------------------------------------------------------


async def test_update_form_sets_values_and_errors_in_the_data_model():
    result = await call(
        "update_form",
        surface_id="about_you",
        set={"/quote/first_name": "Marie", "/quote/email": None},
        errors={"/quote/email": "Courriel invalide"},
    )
    messages = a2ui(result, "about_you", "updated")
    assert [m["updateDataModel"] for m in messages] == [
        {"surfaceId": "about_you", "path": "/quote/first_name", "value": "Marie"},
        {"surfaceId": "about_you", "path": "/quote/email", "value": None},  # null deletes the key
        {"surfaceId": "about_you", "path": "/_errors", "value": {"/quote/email": "Courriel invalide"}},
    ]


async def test_update_form_refuses_a_call_that_changes_nothing_and_the_old_spellings():
    assert "needs set or errors" in refused(await call("update_form", surface_id="about_you"))
    old = await call("update_form", surface_id="about_you", prefill={"/quote/email": "x"})
    assert "'prefill' was unexpected" in refused(old)


# --- close_form and render_offers ----------------------------------------------------------------------------------


async def test_close_form_deletes_the_surface_and_accepts_the_reason_the_contract_lists():
    result = await call("close_form", surface_id="about_you", reason="the customer chose chat")
    assert a2ui(result, "about_you", "closed") == [{"version": "1.0", "deleteSurface": {"surfaceId": "about_you"}}]


async def test_render_offers_opens_an_offer_cards_surface_for_one_quote_version():
    result = await call(
        "render_offers", quote_id="q_1042", version=2, offers=OFFERS, highlight="plus", show_adjust=True
    )
    (message,) = a2ui(result, "offers_q_1042_v2", "shown")
    comps = {c["id"]: c for c in message["createSurface"]["components"]}
    assert conformance.check([message], opens_surface=True) == []
    assert comps["title"]["text"] == "## Votre soumission\u00a0: 2 options"
    cards = comps["offer_cards_1"]
    assert (cards["offers"], cards["recommended"], cards["showAdjust"]) == (OFFERS, "plus", True)
    assert cards["value"] == {"@path": "/underwriting/offer_type"}
    assert comps["action_select_label"]["text"] == "Choisir cette offre"
    plain = (await call("render_offers", quote_id="Q-7", version=1, offers=OFFERS)).content[1].resource.text
    assert "showAdjust" not in plain and "recommended" not in plain and '"surfaceId":"offers_q_7_v1"' in plain


async def test_render_offers_needs_the_version_and_offers_the_agent_adds_and_a_valid_highlight():
    assert "version and offers are required" in refused(await call("render_offers", quote_id="q_1"))
    gold = await call("render_offers", quote_id="q_1", version=1, offers=OFFERS, highlight="gold")
    assert "highlight: 'gold' is not one of" in refused(gold)


async def test_the_customer_language_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("A2UI_LANG", "en")
    (message,) = a2ui(await call("render_form", form_spec=FORM_SPEC), "about_you", "shown")
    assert {c["id"]: c.get("text") for c in message["createSurface"]["components"]}["action_submit_label"] == "Continue"
    offers = (await call("render_offers", quote_id="q_1", version=1, offers=OFFERS)).content[1].resource.text
    assert "Your quote: 2 options" in offers
