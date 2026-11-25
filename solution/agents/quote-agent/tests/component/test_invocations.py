"""The invocations ASGI app in-process: fake model, fake MCP servers, in-memory history."""

import json

import pytest
from agent_framework import Content
from conftest import of_type, post_turn
from fake_mcp import ARGS, REFUSE, SEEN

from quote_agent.fake.client import FakeMainClient, FakeMemoryClient

pytestmark = [pytest.mark.component, pytest.mark.anyio]
# the customer's first message
GREET = {"kind": "message", "text": "Bonjour, je veux une soumission auto"}
# committed state after consent: contact_consent and quote_goal
STATE_WITH_NAME = {
    "quote": {
        "contact_consent": {"value": True, "source": "chat", "status": "confirmed"},
        "quote_goal": {"value": "new_quote", "source": "chat", "status": "inferred"},
    }
}


def recording(monkeypatch, cls=FakeMainClient):
    """Record the messages every fake call receives (what a real model would see)."""
    calls = []
    original = cls.reply

    def reply(self, messages, options):
        """Record the call, then behave as the original ``reply``."""
        calls.append((list(messages), options))
        return original(self, messages, options)

    monkeypatch.setattr(cls, "reply", reply)
    return calls


async def test_message_turn_streams_the_expected_sequence_with_two_model_calls(http):
    events = await post_turn(http, GREET, phase="intent")
    types = [e["type"] for e in events]
    assert types[0] == "turn.started" and types[-1] == "turn.completed"
    assert {"text.delta", "text.done", "memory.result", "suggestions"} <= set(types)
    assert types.index("text.delta") < types.index("text.done")
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    assert {e["session_id"] for e in events} == {"s_test"} and {e["turn"] for e in events} == {1}
    done = events[-1]["data"]
    assert done["model_calls"] == 2 and done["ttft_ms"] is not None and done["usage"]["input"] > 0
    text = "".join(e["data"]["delta"] for e in of_type(events, "text.delta"))
    assert text == of_type(events, "text.done")[0]["data"]["text"]
    assert "puis-je les utiliser" in text
    memory = of_type(events, "memory.result")[0]
    assert memory["actor"] == "memory_agent"
    assert memory["data"]["state_changes"][0]["path"] == "/quote/quote_goal"
    assert all(e["actor"] in ("main_agent", "memory_agent") or e["actor"].startswith("tool:") for e in events)


async def test_render_form_turn_emits_a2ui_with_form_spec_and_the_model_sees_the_summary_only(http, runtime):
    state = {
        **STATE_WITH_NAME,
        "quote": {**STATE_WITH_NAME["quote"], "first_name": {"value": "Marie", "source": "chat", "status": "inferred"}},
    }
    events = await post_turn(
        http, {"kind": "confirmation_result", "accepted": True, "changed_paths": ["/quote/date_of_birth"]}, state=state
    )
    # docs not offered yet in this session → offer first; then a turn that opens the about form
    assert "avez-vous vos documents sous la main?" in of_type(events, "text.done")[0]["data"]["text"]
    events = await post_turn(http, {"kind": "message", "text": "Je ne les ai pas"}, turn=2, state=state)
    a2ui = of_type(events, "a2ui.message")
    assert len(a2ui) == 1 and a2ui[0]["data"]["createSurface"]["surfaceId"] == "a_propos"
    assert a2ui[0]["form_spec"]["form_id"] == "a_propos" and a2ui[0]["actor"] == "tool:a2ui.render_form"
    assert events[-1]["data"]["model_calls"] == 2
    history = runtime.sessions["s_test"].state[runtime.history.source_id]["messages"]
    results = [c.result for m in history for c in m.contents if c.type == "function_result"]
    assert '{"surface_id": "a_propos", "status": "shown"}' in results
    assert not any("createSurface" in str(r) for r in results)  # history stores the stub, not A2UI JSON
    assert ("render_form", "dev-only-session-ref") in SEEN
    assert ARGS["render_form"]["state"] == state and ARGS["render_form"]["form_spec"]["form_id"] == "a_propos"


async def test_warmup_makes_no_model_call(http, monkeypatch):
    calls = recording(monkeypatch)
    events = await post_turn(http, {"kind": "warmup"})
    assert [e["type"] for e in events] == ["turn.completed"]
    assert events[0]["data"]["model_calls"] == 0 and not calls


async def test_knowledge_question_costs_three_calls_and_cites_sources(http):
    events = await post_turn(http, {"kind": "message", "text": "C’est quoi la franchise?"}, phase="gather")
    assert events[-1]["data"]["model_calls"] == 3
    done = of_type(events, "text.done")[0]["data"]
    assert done["text"].startswith("La **franchise**")
    assert done["citations"] == [
        {"id": "kb_3_1", "title": "Guide des protections auto, section 2.4", "url": "kb://guide-protections-auto/3/1"}
    ]
    assert next(e["data"]["result_mode"] for e in of_type(events, "tool.call")) == "narrate"


async def test_system_notes_go_into_history_before_the_input_and_context_is_not_stored(http, runtime, monkeypatch):
    calls = recording(monkeypatch)
    await post_turn(http, GREET)
    note = "Form 'vehicle_0' edited outside the chat: /vehicles/0/usage_type pleasure -> commute"
    await post_turn(http, {"kind": "message", "text": "Oui, j’accepte"}, turn=2, system_notes=[note])
    messages, options = calls[-1]
    roles_texts = [(m.role, m.text) for m in messages]
    note_at = roles_texts.index(("system", f"SYSTEM NOTE: {note}"))
    context_at = next(i for i, (r, t) in enumerate(roles_texts) if r == "system" and t.startswith("STATE"))
    input_at = len(messages) - 1
    assert roles_texts[0] == ("user", "INPUT kind=message\n" + GREET["text"])  # history first
    assert note_at < context_at < input_at
    assert roles_texts[input_at] == ("user", "INPUT kind=message\nOui, j’accepte")
    assert options["instructions"].startswith("# Role") and options["prompt_cache_key"] == "quote-main:v1:s_test"
    stored = runtime.sessions["s_test"].state[runtime.history.source_id]["messages"]
    assert not any(m.text.startswith("STATE") for m in stored)  # volatile context is never stored
    assert [m.text for m in stored if m.role == "system"] == [f"SYSTEM NOTE: {note}"]


async def test_memory_agent_sees_last_assistant_message_input_and_state(http, monkeypatch):
    calls = recording(monkeypatch, FakeMemoryClient)
    await post_turn(http, GREET)
    await post_turn(http, {"kind": "message", "text": "Oui, j’accepte"}, turn=2, state=STATE_WITH_NAME)
    messages, options = calls[-1]
    assert len(messages) == 1 and messages[0].role == "user"
    assert messages[0].text.startswith("LAST_ASSISTANT_MESSAGE\nAvec plaisir.")
    assert "/quote/contact_consent=true~chat/confirmed" in messages[0].text
    assert options["response_format"]["json_schema"]["strict"] is False
    assert options["prompt_cache_key"] == "quote-memory:v1:s_test"


async def test_get_quote_is_pre_executed_before_the_model(http):
    events = await post_turn(http, {"kind": "action", "action_id": "get_quote"}, phase="gather")
    types = [e["type"] for e in events]
    assert types.index("quote.created") < types.index("text.done")
    assert events[-1]["data"]["model_calls"] == 2
    assert of_type(events, "quote.created")[0]["data"]["quote_id"] == "Q-1"
    offers = of_type(events, "a2ui.message")[0]
    assert "form_spec" not in offers and offers["actor"] == "tool:a2ui.render_offers"
    assert ("create_quote", "dev-only-session-ref") in SEEN
    assert of_type(events, "quote.created")[0]["data"]["input_hash"] == "sha256:dev-only"
    assert ARGS["render_offers"]["version"] == 1 and len(ARGS["render_offers"]["offers"]) == 3  # injected
    model_args = next(e for e in of_type(events, "tool.call") if e["data"]["name"] == "a2ui.render_offers")
    assert set(model_args["data"]["args_redacted"]) == {"quote_id", "highlight", "show_adjust"}


async def test_quote_refusal_is_narrated_without_quote_created(http):
    REFUSE["create_quote"] = True
    events = await post_turn(http, {"kind": "action", "action_id": "get_quote"}, phase="gather")
    assert not of_type(events, "quote.created") and not of_type(events, "a2ui.message")
    result = next(e for e in of_type(events, "tool.result") if e["data"]["name"] == "quoting.create_quote")
    assert result["data"]["error"] == "missing_fields"
    assert of_type(events, "text.done")[0]["data"]["text"].endswith("calculer votre soumission : Courriel.")
    assert events[-1]["data"]["model_calls"] == 2


async def test_modify_quote_action_is_pre_executed_with_the_quote_id(http):
    events = await post_turn(
        http,
        {"kind": "action", "action_id": "modify_quote", "quote_id": "Q-1", "changes": {"deductible": 1000}},
        phase="advise",
    )
    updated = of_type(events, "quote.updated")[0]["data"]
    assert updated["version"] == 2 and updated["previous_version"] == 1 and updated["input_hash"]
    assert ("modify_quote", "dev-only-session-ref") in SEEN and events[-1]["data"]["model_calls"] == 2


async def test_off_topic_message_emits_a_guardrail_event(http):
    events = await post_turn(http, {"kind": "message", "text": "Raconte-moi une blague"})
    assert of_type(events, "guardrail")[0]["data"] == {"category": "off_topic", "action": "redirect"}
    assert "Je ne peux pas vous aider avec ce sujet" in of_type(events, "text.done")[0]["data"]["text"]


async def test_main_agent_failure_becomes_an_error_event(http, monkeypatch):
    def boom(self, messages, options):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(FakeMainClient, "reply", boom)
    events = await post_turn(http, GREET)
    assert events[0]["type"] == "turn.started"
    assert events[-1]["type"] == "error"
    assert events[-1]["data"]["retryable"] is True and "model unavailable" not in json.dumps(events[-1])


async def test_memory_failure_is_reported_and_the_turn_continues(http, monkeypatch):
    monkeypatch.setattr(FakeMemoryClient, "reply", lambda self, messages, options: [Content.from_text("{not json")])
    events = await post_turn(http, GREET)
    memory = of_type(events, "memory.result")[0]["data"]
    assert memory["error"]["code"] == "JSONDecodeError"
    assert events[-1]["type"] == "turn.completed" and of_type(events, "text.done")


async def test_invalid_body_is_rejected(http):
    response = await http.post("/invocations", json={"turn": 1, "input": {"kind": "nope"}})
    assert response.status_code == 400


async def test_readiness(http):
    response = await http.get("/readiness")
    assert response.status_code == 200
