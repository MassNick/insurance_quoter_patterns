"""The fake main policy's FormSpecs and suggestions respect the contracts (D15)."""

import re

import pytest
from jsonschema import Draft202012Validator
from quote_common import load_schema

from quote_agent.fake.client import SUGG, _Main

pytestmark = pytest.mark.unit
# what every form of the fake policy must satisfy
FORM_SPEC = Draft202012Validator(load_schema("form-spec.schema.json"))
# the input schema of ``ui.suggest_replies`` in tools.json
SUGGEST = Draft202012Validator(
    next(t for t in __import__("quote_common").load_tools()["tools"] if t["name"] == "ui.suggest_replies")[
        "inputSchema"
    ]
)


def main(state_lines=(), text="Bonjour"):
    """A fake main policy (``_Main``) reading a context with the given STATE lines and one customer message."""
    from agent_framework import Message

    context = "\n".join(
        ["STATE", *state_lines, "PHASE gather", "OPEN_SURFACES []", "SIGNALS []", "PREFERENCES {}", "RULES []"]
    )
    return _Main(
        [Message(role="system", contents=[context]), Message(role="user", contents=[f"INPUT kind=message\n{text}"])]
    )


def test_every_fake_form_spec_is_valid():
    m = main(
        [
            '/quote/first_name="Marie"',
            '/vehicles/0/vehicle_make="Toyota"',
            '/vehicles/0/vehicle_model="RAV4"',
            "/vehicles/0/vehicle_year=2021",
            '/drivers/1/driver_name="Julie"',
            '/drivers/1/relationship_code="spouse"',
        ]
    )
    for spec in (
        m.about(True, True),
        m.about(False, False),
        m.vehicle(0),
        m.vehicle(1),
        m.assignment(),
        m.history_form(),
        m.documents(),
    ):
        FORM_SPEC.validate(spec)
    assert m.vehicle(0)["fields"][0]["prefill"]["value"] == {
        "vehicle_year": 2021,
        "vehicle_make": "Toyota",
        "vehicle_model": "RAV4",
    }


def test_suggestions_are_generic_and_within_limits():
    pii = re.compile(r"@|\d{3}|[A-Z]\d[A-Z]")
    for items in SUGG.values():
        SUGGEST.validate({"items": [{"label": s, "kind": "reply"} for s in items]})
        assert not any(pii.search(s) for s in items)


def test_first_message_asks_for_consent_and_sets_the_intent_phase():
    plan = main(text="Je veux une soumission auto").decide()
    assert "puis-je les utiliser pour préparer votre soumission?" in plan.text
    assert plan.phase == "intent" and plan.suggestions == SUGG["consent"]
