"""Prompt assembly order, cache key, structured output and the 30k token budget."""

import json

import pytest
from jsonschema import Draft202012Validator
from quote_common import load_schema

from quote_agent import prompts
from quote_agent.config import Settings
from quote_agent.prompts import TOKEN_LIMIT, TOKENIZER
from quote_agent.turn import TurnInput, TurnRequest

pytestmark = pytest.mark.unit

# a committed state: one inferred field, one empty field and one confirmed vehicle field
STATE = {
    "quote": {
        "first_name": {"value": "Marie", "source": "chat", "status": "inferred"},
        "email": {"value": None, "source": "chat", "status": "inferred"},
    },
    "vehicles": [{"vehicle_year": {"value": 2021, "source": "form", "status": "confirmed"}}],
    "drivers": [],
    "underwriting": {},
    "household": {},
}


def request(**kw):
    """A ``TurnRequest`` with state, a phase, an open form, a preference and a rule hit."""
    return TurnRequest(
        turn=3,
        input=TurnInput(kind="message", text="Bonjour"),
        state=STATE,
        phase="gather",
        open_surfaces=["vehicule_0"],
        preferences={"answer_mode": "text"},
        rule_hits=[{"rule_id": "R1", "severity": "flag"}],
        **kw,
    )


def test_static_prefix_is_prompt_then_key_list_without_comments():
    text = prompts.instructions("main")
    assert text.startswith("# Role")
    assert "<!--" not in text
    assert text.index("# Scope and guardrails") < text.index("# State keys") < text.index("quote.quote_goal:enum[")
    assert prompts.instructions("main") is text  # byte-identical across turns (cached)


def test_key_list_has_every_catalog_key_with_codes_and_no_labels():
    lines = prompts.key_list().splitlines()
    assert len(lines) == 68
    assert "vehicle.usage_type:enum[pleasure|commute|business|farm]" in lines
    assert "Pleasure" not in prompts.key_list()


def test_token_budget_under_30k_and_counts_tools():
    total = prompts.static_prefix_tokens()
    prompt_only = TOKENIZER.count_tokens(prompts.instructions("main"))
    assert 1_500 < prompt_only < total < TOKEN_LIMIT
    assert len(prompts.tool_definitions()) == 13
    render = next(t for t in prompts.tool_definitions() if t["name"] == "a2ui__render_form")
    assert "fields" in render["parameters"]["properties"]["form_spec"]["properties"]  # FormSpec schema inlined
    assert "form-spec.schema.json" not in json.dumps(prompts.tool_definitions())
    spec = {"form_id": "a_propos", "title": "Vous", "fields": [{"key": "/quote/date_of_birth", "widget": "date"}]}
    Draft202012Validator(render["parameters"]).validate({"form_spec": spec})  # inlined refs resolve
    offers = next(t for t in prompts.tool_definitions() if t["name"] == "a2ui__render_offers")
    assert set(offers["parameters"]["properties"]) == {"quote_id", "highlight", "show_adjust"}


def test_token_budget_check_fails_when_padded(monkeypatch):
    padding = "x" * (4 * TOKEN_LIMIT)
    monkeypatch.setattr(prompts, "instructions", lambda agent: padding)
    assert prompts.static_prefix_tokens() >= TOKEN_LIMIT


def test_cache_key_layout():
    assert prompts.cache_key("main", "v13", "s_8f2c") == "quote-main:v13:s_8f2c"


def test_compact_state_skips_empty_and_keeps_source_status():
    lines = prompts.compact_state(STATE)
    assert lines == ['/quote/first_name="Marie"~chat/inferred', "/vehicles/0/vehicle_year=2021~form/confirmed"]


def test_context_message_order_is_state_phase_surfaces_signals_preferences_rules():
    text = prompts.context_message(request())
    order = [text.index(h) for h in ("STATE", "PHASE gather", "OPEN_SURFACES", "SIGNALS", "PREFERENCES", "RULES")]
    assert order == sorted(order)
    assert 'PREFERENCES {"answer_mode":"text"}' in text


def test_main_input_for_structured_inputs_carries_summary_and_pre_executed_result():
    inp = TurnInput(kind="form_submitted", surface_id="a_propos", changed_paths=["/quote/date_of_birth"])
    assert prompts.main_input(inp).splitlines()[:2] == [
        "INPUT kind=form_submitted",
        "Form a_propos submitted: 1 fields",
    ]
    action = TurnInput(kind="action", action_id="get_quote")
    text = prompts.main_input(action, ("quoting.create_quote", {"quote_id": "Q-1"}))
    assert text.endswith('PRE_EXECUTED quoting.create_quote\n{"quote_id": "Q-1"}')


def test_inputs_tolerate_extra_keys_and_paths_alias():
    inp = TurnInput.model_validate(
        {
            "kind": "confirmation_result",
            "accepted": True,
            "paths": ["/quote/contact_consent"],
            "missing_fields": [],
            "confirmation_id": "cf_1",
        }
    )
    assert inp.changed_paths == ["/quote/contact_consent"]
    assert prompts.input_summary(inp) == "Confirmation accepted: /quote/contact_consent"
    assert '"confirmation_id": "cf_1"' in prompts.main_input(inp)


def test_memory_input_uses_one_line_summary_for_non_message_inputs():
    req = TurnRequest(
        turn=1,
        input=TurnInput(
            kind="document_extracted", purpose="licence", changed_paths=["/quote/first_name", "/quote/last_name"]
        ),
    )
    text = prompts.memory_input(req, "Téléversez vos documents.")
    assert "INPUT kind=document_extracted\nDocument licence read: 2 fields" in text
    assert text.startswith("LAST_ASSISTANT_MESSAGE\nTéléversez vos documents.")


def test_memory_response_format_is_the_strict_contract_schema():
    fmt = prompts.memory_response_format(Settings().memory_output_schema)[
        "json_schema"
    ]  # the default: the contract file
    assert fmt["strict"] is False and fmt["name"] == "MemoryAgentOutput"  # value is any JSON: not strict-expressible
    contract = load_schema("memory-output.schema.json")
    assert fmt["schema"]["properties"] == contract["properties"]
    assert "$schema" not in fmt["schema"] and "$id" not in fmt["schema"]
    json.dumps(fmt)  # serializable as response_format


def test_memory_response_format_follows_the_schema_file_it_is_given():
    fmt = prompts.memory_response_format("document-output.schema.json")["json_schema"]
    assert fmt["schema"]["properties"] == load_schema("document-output.schema.json")["properties"]
