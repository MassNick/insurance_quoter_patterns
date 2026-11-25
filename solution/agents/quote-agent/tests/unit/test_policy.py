"""Function/chat middleware semantics: terminal ends the loop, one narrate round, session_ref injection."""

import json

import pytest
from agent_framework import Agent, Content, FunctionTool

from quote_agent.fake.client import _FakeClient
from quote_agent.turn import Turn, current_turn, meter, tool_policy

pytestmark = [pytest.mark.unit, pytest.mark.anyio]
invoked: list[tuple[str, dict]] = []  # (tool name, arguments) of every tool call that ran


class Scripted(_FakeClient):
    """Replies from a script; records the tool schemas each model call saw."""

    def __init__(self, *script):
        """``script`` holds one list of contents per model call."""
        super().__init__()
        self.script, self.seen = list(script), []

    def reply(self, messages, options):
        """Record the tool schemas this call saw and return the next scripted completion."""
        self.seen.append({t.name: t.parameters() for t in options.get("tools") or []})
        return self.script.pop(0)


def call(name, n=0, **args):
    """A function-call content for the tool ``name`` with ``args``; ``n`` makes the call id unique."""
    return Content.from_function_call(call_id=f"c{n}_{name}", name=name, arguments=json.dumps(args))


def tool(name, schema, result="ok"):
    """A local tool ``name`` with a JSON ``schema``: it records its arguments in ``invoked`` and returns ``result``."""

    async def run(**kwargs):
        """The tool body: record the call and return the result."""
        invoked.append((name, kwargs))
        return result

    return FunctionTool(name=name, description=name, func=run, input_model=schema)


# the tools of the scripted turns, named as the framework serves them (server__tool)
TOOLS = [
    tool(
        "quoting__create_quote",
        {
            "type": "object",
            "properties": {"offer_types": {"type": "array"}, "session_ref": {"type": "string"}},
            "required": ["session_ref"],
        },
        json.dumps({"quote_id": "Q-1", "offers": []}),
    ),
    tool(
        "a2ui__render_offers",
        {
            "type": "object",
            "properties": {k: {} for k in ("quote_id", "highlight", "show_adjust", "version", "offers", "session_ref")},
            "required": ["quote_id", "version", "offers"],
        },
        json.dumps({"surface_id": "offers_q_1_v2", "status": "shown"}),
    ),
    tool(
        "knowledge__knowledge_base_retrieve",
        {"type": "object", "properties": {"query": {"type": "string"}}},
        json.dumps({"passages": [{"id": "kb_1", "title": "Guide", "url": "kb://g", "content": "..."}]}),
    ),
    tool("ui__suggest_replies", {"type": "object", "properties": {"items": {"type": "array"}}}),
    tool("ui__set_phase", {"type": "object", "properties": {"phase": {"type": "string"}}}),
]


async def run(client, last_quote=None):
    """Run one turn of an agent with both middlewares over ``client``; return the turn and the response."""
    invoked.clear()
    t = Turn("s_1", 1, "dev-only-ref")
    t.last_quote = last_quote or {}
    token = current_turn.set(t)
    try:
        agent = Agent(client=client, tools=TOOLS, middleware=[tool_policy, meter], default_options={"store": False})
        response = await agent.run("hello")
    finally:
        current_turn.reset(token)
    return t, response


async def test_terminal_tools_end_the_loop_after_one_completion_and_parallel_calls_all_run():
    client = Scripted(
        [
            Content.from_text("Bonjour"),
            call("ui__set_phase", phase="gather"),
            call("ui__suggest_replies", items=[{"label": "Oui"}]),
        ],
        [Content.from_text("never")],
    )
    t, response = await run(client)
    assert t.model_calls == 1 and len(client.script) == 1
    assert {name for name, _ in invoked} == {"ui__set_phase", "ui__suggest_replies"}
    assert response.text == "Bonjour"


async def test_narrate_tool_allows_exactly_one_follow_up_completion():
    client = Scripted(
        [call("knowledge__knowledge_base_retrieve", query="franchise")],
        [Content.from_text("La franchise..."), call("ui__suggest_replies", n=1, items=[])],
    )
    t, _ = await run(client)
    assert t.model_calls == 2 and not client.script
    assert t.citations == [{"id": "kb_1", "title": "Guide", "url": "kb://g"}]


async def test_terminal_with_narrate_in_same_batch_still_gets_the_follow_up():
    client = Scripted(
        [call("knowledge__knowledge_base_retrieve", query="x"), call("ui__set_phase", phase="advise")],
        [Content.from_text("done")],
    )
    t, _ = await run(client)
    assert t.model_calls == 2


async def test_second_narrate_round_is_refused():
    client = Scripted(
        [call("knowledge__knowledge_base_retrieve", query="a")],
        [call("quoting__create_quote", n=1)],
        [Content.from_text("never")],
    )
    t, _ = await run(client)
    assert t.model_calls == 2 and len(client.script) == 1
    assert [name for name, _ in invoked] == ["knowledge__knowledge_base_retrieve"]


async def test_injected_args_override_the_model_and_are_hidden_from_it():
    offers = [{"offer_type": "plus", "monthly": 118}]
    client = Scripted([call("a2ui__render_offers", quote_id="Q-1", highlight="plus", session_ref="EVIL", version=9)])
    t, _ = await run(client, last_quote={"quote_id": "Q-1", "version": 2, "offers": offers})
    expected = {"quote_id": "Q-1", "highlight": "plus", "session_ref": "dev-only-ref", "version": 2, "offers": offers}
    assert invoked == [("a2ui__render_offers", expected)]
    seen = client.seen[0]
    assert set(seen["a2ui__render_offers"]["properties"]) == {"quote_id", "highlight", "show_adjust"}
    assert seen["a2ui__render_offers"]["required"] == ["quote_id"]
    assert set(seen["quoting__create_quote"]["properties"]) == {"offer_types"}
    assert "required" not in seen["quoting__create_quote"] or not seen["quoting__create_quote"]["required"]
    assert "query" in seen["knowledge__knowledge_base_retrieve"]["properties"]
    call_event = next(e for e in drain(t) if e.type == "tool.call")
    assert call_event.data["args_redacted"] == {"quote_id": "Q-1", "highlight": "plus"}  # model-facing args only


def drain(t):
    """Everything in the turn's event queue."""
    events = []
    while not t.queue.empty():
        events.append(t.queue.get_nowait())
    return events


def test_injected_args_still_validate_after_the_openai_client_makes_the_shown_schema_strict():
    """Azure regression (2026-11-25): the Responses client sets ``additionalProperties: false`` on the schema it sends,
    in place (agent_framework_openai ``_chat_client``). With a shared schema, validation then rejected the injected
    ``session_ref`` and ``state`` as unexpected arguments and no form ever opened."""
    from quote_agent.turn import hide_injected

    schema = {"type": "object", "required": ["form_spec"], "additionalProperties": False, "properties": {
        "form_spec": {"type": "object"}, "session_ref": {"type": "string"}, "state": {"type": "object"}}}
    tool = FunctionTool(name="a2ui___render_form", description="d", func=lambda **_: None, input_model=schema)
    hide_injected(tool)
    sent = tool.parameters()
    assert set(sent["properties"]) == {"form_spec"}  # the model never sees the injected args
    sent["additionalProperties"] = False  # what the OpenAI client does to the schema it sends
    args = {"form_spec": {}, "session_ref": "sr.x", "state": {}}
    assert tool._prepare_arguments(args) == args


def flaky_render_form(failures: int):
    """``a2ui__render_form`` that refuses its first ``failures`` calls like mcp-a2ui does, slower than the UI tools."""
    import asyncio

    from agent_framework.exceptions import ToolExecutionException

    calls = {"n": 0}

    async def render(**kwargs):
        """Refuse (after a delay, so the fast suggest_replies finishes first) until ``failures`` calls have run."""
        invoked.append(("a2ui__render_form", kwargs))
        calls["n"] += 1
        await asyncio.sleep(0.05)
        if calls["n"] <= failures:
            raise ToolExecutionException(json.dumps({"error": "invalid_arguments", "message": "Invalid FormSpec"}))
        return json.dumps({"surface_id": "a_propos", "status": "shown"})

    schema = {"type": "object", "properties": {"form_spec": {"type": "object"}}}
    return FunctionTool(name="a2ui__render_form", description="render_form", func=render, input_model=schema)


async def test_a_failed_form_gets_one_corrective_completion_even_when_suggestions_finish_first():
    TOOLS.append(flaky_render_form(failures=1))
    try:
        client = Scripted(
            [Content.from_text("Voici"), call("a2ui__render_form", form_spec={}),
             call("ui__suggest_replies", items=[])],
            [Content.from_text("Voici le formulaire."), call("a2ui__render_form", n=1, form_spec={"x": 1}),
             call("ui__suggest_replies", n=1, items=[])],
            [Content.from_text("never")],
        )  # fmt: skip
        t, response = await run(client)
    finally:
        TOOLS.pop()
    assert t.model_calls == 2 and len(client.script) == 1  # one retry, then the turn ends
    assert [name for name, _ in invoked].count("a2ui__render_form") == 2
    assert "Voici le formulaire." in response.text


async def test_only_one_corrective_completion_per_turn():
    TOOLS.append(flaky_render_form(failures=5))
    try:
        client = Scripted(
            [call("a2ui__render_form", form_spec={}), call("ui__suggest_replies", items=[])],
            [call("a2ui__render_form", n=1, form_spec={}), call("ui__suggest_replies", n=1, items=[])],
            [Content.from_text("never")],
        )
        t, _ = await run(client)
    finally:
        TOOLS.pop()
    assert t.model_calls == 2 and len(client.script) == 1  # the second failure ends the turn
