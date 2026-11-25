"""Tool results → envelopes (Turn.run_tool), logical names and redaction."""

import json

import pytest
from agent_framework import Content
from mcp import types

from quote_agent.tools import logical_name, parse_mcp_result
from quote_agent.turn import Turn

pytestmark = [pytest.mark.unit, pytest.mark.anyio]
# a minimal FormSpec
SPEC = {"form_id": "a_propos", "title": "Deux détails sur vous", "fields": [{"key": "/quote/date_of_birth"}]}


def events(t):
    """Drain the turn's queue as plain dicts (None marks the end of the stream)."""
    out = []
    while not t.queue.empty():
        env = t.queue.get_nowait()
        out.append(env.model_dump(exclude_none=True) if env else None)
    return out


async def run_tool(name, args, result):
    """Run one tool call that returns ``result``; return the turn, what the model sees and the emitted events."""
    t = Turn("s_1", 4, "dev-only-ref")

    async def invoke():
        """The tool call: return the canned result."""
        return result

    visible = await t.run_tool(name, args, invoke)
    return t, visible, events(t)


def a2ui_result(message):
    """An mcp-a2ui result (summary text plus a user-audience resource carrying ``message``) as the agent parses it."""
    return parse_mcp_result(
        types.CallToolResult(
            content=[
                types.TextContent(type="text", text='{"surface_id":"a_propos","status":"shown"}'),
                types.EmbeddedResource(
                    type="resource",
                    annotations=types.Annotations(audience=["user"]),
                    resource=types.TextResourceContents(
                        uri="a2ui://a", mime_type="application/a2ui+json", text=json.dumps([message])
                    ),
                ),
            ]
        )
    )


async def test_render_form_forwards_the_user_resource_with_form_spec_and_model_sees_summary_only():
    msg = {"version": "1.0", "createSurface": {"surfaceId": "a_propos"}}
    t, visible, evs = await run_tool("a2ui.render_form", {"form_spec": SPEC, "session_ref": "x"}, a2ui_result(msg))
    assert visible == '{"surface_id":"a_propos","status":"shown"}'
    assert [e["type"] for e in evs] == ["tool.call", "a2ui.message", "tool.result"]
    a2ui = evs[1]
    assert a2ui["data"] == msg and a2ui["form_spec"] == SPEC and a2ui["actor"] == "tool:a2ui.render_form"
    assert t.fallback_text == "Voici un court formulaire : Deux détails sur vous."
    assert [e["seq"] for e in evs] == [1, 2, 3]


async def test_update_messages_do_not_carry_form_spec():
    msg = {"version": "1.0", "deleteSurface": {"surfaceId": "a_propos"}}
    _, _, evs = await run_tool("a2ui.close_form", {"surface_id": "a_propos"}, a2ui_result(msg))
    assert "form_spec" not in evs[1]


async def test_quote_results_become_quote_events_and_stay_visible_for_narration():
    quote = {"quote_id": "Q-1", "version": 2, "previous_version": 1, "delta_monthly": -9, "offers": []}
    t, visible, evs = await run_tool("quoting.modify_quote", {}, [Content.from_text(json.dumps(quote))])
    assert json.loads(visible) == quote
    assert evs[1]["type"] == "quote.updated" and evs[1]["data"] == quote
    assert t.last_quote == quote  # feeds render_offers' injected version and offers
    _, _, evs = await run_tool("quoting.create_quote", {}, [Content.from_text('{"missing_fields":["/quote/email"]}')])
    assert [e["type"] for e in evs] == ["tool.call", "tool.result"]


def test_injected_values_follow_tools_json():
    t = Turn("s_1", 1, "dev-only-ref", state={"quote": {}})
    assert t.injected("a2ui.render_form") == {"state": {"quote": {}}, "session_ref": "dev-only-ref"}
    assert t.injected("a2ui.render_offers") == {"session_ref": "dev-only-ref"}  # no quote yet in this turn
    assert t.injected("quoting.identify_address") == {}
    t.last_quote = {"quote_id": "Q-1", "version": 3, "offers": []}
    assert t.injected("a2ui.render_offers") == {"version": 3, "offers": [], "session_ref": "dev-only-ref"}


async def test_lookup_result_goes_to_the_ui_and_the_model_gets_a_stub():
    data = {"candidates": [{"city": "Lévis"}]}
    _, visible, evs = await run_tool(
        "quoting.identify_address", {"query": "G6V"}, [Content.from_text(json.dumps(data))]
    )
    assert json.loads(visible) == {"status": "sent_to_ui"}
    assert evs[-1]["data"]["result"] == data


async def test_local_ui_tools_emit_suggestions_and_phase():
    items = [{"label": "Oui, j’accepte", "kind": "reply"}]
    _, visible, evs = await run_tool("ui.suggest_replies", {"items": items}, [Content.from_text("ok")])
    assert visible == "ok" and evs[1] == {
        **evs[1],
        "type": "suggestions",
        "data": {"items": items},
        "actor": "main_agent",
    }
    _, _, evs = await run_tool("ui.set_phase", {"phase": "advise"}, "ok")
    assert evs[1]["type"] == "phase" and evs[1]["data"] == {"phase": "advise"}


async def test_tool_failure_is_reported_not_raised():
    t = Turn("s_1", 1)

    async def boom():
        raise RuntimeError("down")

    visible = await t.run_tool("quoting.create_quote", {}, boom)
    assert json.loads(visible) == {"error": "tool_failed"}
    assert events(t)[-1]["data"]["error"] == "RuntimeError"


def test_turn_completed_and_error_envelopes():
    t = Turn("s_9", 7)
    t.model_calls = 2
    t.complete()
    done = events(t)
    assert done[0]["type"] == "turn.completed" and done[-1] is None
    assert set(done[0]["data"]) == {"model_calls", "usage", "ttft_ms", "duration_ms"}
    t = Turn("s_9", 8)
    t.fail(ValueError("x"))
    assert events(t)[0]["data"] == {
        "code": "ValueError",
        "message": "The turn failed. Please retry.",
        "retryable": True,
    }


def test_logical_names_accept_every_separator():
    assert logical_name("quoting__identify_address") == "quoting.identify_address"
    assert logical_name("quoting___create_quote") == "quoting.create_quote"  # the Foundry toolbox's spelling
    assert logical_name("a2ui___render_form") == "a2ui.render_form"
    assert logical_name("quoting_identify_address") == "quoting.identify_address"
    assert logical_name("quoting.identify_address") == "quoting.identify_address"
    assert logical_name("knowledge_knowledge_base_retrieve") == "knowledge.knowledge_base_retrieve"
    assert logical_name("ui__suggest_replies") == "ui.suggest_replies"
    assert logical_name("render_form") == "a2ui.render_form"


async def test_tool_call_args_are_redacted_with_the_shared_pii_masking():
    args = {"query": "G6V 0A1 ou 514 555-0142", "email": "marie@exemple.com", "vin": ["1HGCM82633A004352"]}
    _, _, evs = await run_tool("quoting.identify_address", args, [Content.from_text("{}")])
    assert evs[0]["data"]["args_redacted"] == {
        "query": "[redacted] ou [redacted]",
        "email": "[redacted]",
        "vin": ["[redacted]"],
    }
