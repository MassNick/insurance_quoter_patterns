"""End-to-end journeys through the real services (BFF, quote-agent with the fake model, mcp-quoting, mcp-a2ui).

    bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e   # starts the stack on ports +20000, runs, stops it

No browser here: the browser journeys are in web/e2e (mock BFF) and web/e2e-stack (this stack + the web app,
run with `cd web && npm run test:stack`).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
import pytest
from quote_common.envelope import sse_decode

from stack import Stack

pytestmark = pytest.mark.e2e


@pytest.fixture(scope="module")
def stack():
    """The running stack for this module, on ports shifted by ``E2E_PORT_OFFSET`` (default 20000)."""
    with Stack(int(os.environ.get("E2E_PORT_OFFSET", "20000"))) as s:
        yield s


class Session:
    """One customer: the BFF HTTP + SSE API exactly as the web app uses it."""

    def __init__(self, stack: Stack) -> None:
        """Open a session through the BFF; keep its id and the opening events."""
        self.http = httpx.Client(base_url=stack.url("bff"), timeout=60)
        body = self.http.post("/api/sessions", json={"locale": "fr-CA"}).raise_for_status().json()
        self.sid, self.events = body["session_id"], body["events"]

    def send(self, type_: str, **data: Any) -> list[dict[str, Any]]:
        """Send one inbound event as a turn and return the SSE events of the answer."""
        with self.http.stream("POST", f"/api/sessions/{self.sid}/turns", json={"type": type_, "data": data}) as r:
            r.raise_for_status()
            self.events = sse_decode(r.read().decode())
        return self.events

    def say(self, text: str, **extra: Any) -> list[dict[str, Any]]:
        """Send a customer message."""
        return self.send("user.message", text=text, **extra)

    def find(self, type_: str) -> list[dict[str, Any]]:
        """The events of the last turn with type ``type_``."""
        return [e for e in self.events if e["type"] == type_]

    def text(self) -> str:
        """The ``text.done`` texts of the last turn, joined."""
        return " ".join(e["data"].get("text", "") for e in self.find("text.done"))

    def surface(self) -> dict[str, Any]:
        """The createSurface the last turn opened."""
        (msg,) = [e["data"]["createSurface"] for e in self.find("a2ui.message") if "createSurface" in e["data"]]
        return msg

    def submit(self, surface_id: str, values: dict[str, Any], name: str = "submit") -> list[dict[str, Any]]:
        """Submit form ``values`` on a surface as an A2UI action (``name`` is the button)."""
        return self.send("a2ui.action", name=name, surfaceId=surface_id, sourceComponentId=f"action_{name}",
                         timestamp="2026-11-25T00:00:00Z", context={"values": values})

    def confirm(self) -> list[dict[str, Any]]:
        """Accept the pending double confirmation (both steps), as the popup does."""
        cid = self.find("confirm.required")[0]["data"]["confirmation_id"]
        self.send("confirm.response", confirmation_id=cid, step=1, accepted=True)
        return self.send("confirm.response", confirmation_id=cid, step=2, accepted=True)

    def state(self) -> dict[str, Any]:
        """The session state as the BFF serves it."""
        return self.http.get(f"/api/sessions/{self.sid}/state").raise_for_status().json()

    def value(self, path: str) -> Any:
        """The value at a state path (None when absent)."""
        node: Any = self.state()["state"]
        for part in path.strip("/").split("/"):
            node = node[int(part)] if isinstance(node, list) else node.get(part, {})
        return node.get("value") if isinstance(node, dict) else None

    def consented(self) -> Session:
        """The opening of most journeys: ask for a quote, give consent, confirm it twice and check that the agent
        then offers to read documents."""
        self.say("Obtenir une soumission auto")
        self.say("Oui, j’accepte")
        assert self.find("confirm.required"), "consent needs the double confirmation"
        self.confirm()
        assert "documents" in self.text(), "after consent the agent offers to read documents"
        assert self.text().count("documents sous la main") == 1, "the reply is streamed once (no repeated final text)"
        return self


def test_greeting_is_bff_templated_with_generic_suggestions(stack):
    s = Session(stack)
    assert [e["type"] for e in s.events] == ["text.done", "suggestions"]
    labels = [i["label"] for i in s.events[1]["data"]["items"]]
    assert labels and all(len(label) <= 40 and "@" not in label for label in labels)


def test_consent_then_document_offer_with_the_text_or_forms_note(stack):
    s = Session(stack).consented()
    assert "par écrit" in s.text() and "formulaire" in s.text()
    assert s.value("/quote/contact_consent") is True
    assert s.find("turn.completed")[-1]["data"]["model_calls"] == 2


def test_personal_data_waits_for_consent(stack):
    s = Session(stack)
    s.say("Obtenir une soumission auto")
    s.say("Mon courriel est marie@exemple.com")
    assert s.value("/quote/email") is None


def test_name_in_chat_then_a_small_form_and_its_chip(stack):
    s = Session(stack).consented()
    s.say("Je ne les ai pas")
    s.say("Je m’appelle Marie Tremblay")
    surface = s.surface()
    assert surface["surfaceId"] == "a_propos"
    assert s.value("/quote/first_name") == "Marie"
    s.submit("a_propos", {"/quote/date_of_birth": "1987-01-18"})
    assert any("deleteSurface" in e["data"] for e in s.find("a2ui.message"))
    assert s.value("/quote/date_of_birth") == "1987-01-18"
    chips = {x["surface_id"]: x["status"] for x in s.state()["surfaces"]}
    assert chips["a_propos"] == "submitted"


def test_lamborghini_stop_rule_asks_first_then_hands_off(stack):
    s = Session(stack).consented()
    s.say("Je conduis une Lamborghini Huracán 2023")
    assert "Lamborghini" in s.text() and [h for h in s.find("rules.hit") if h["data"]["rule_id"] == "VEHICULE_EXOTIQUE"]
    s.say("Oui, c’est bien ça", action_id="rule_confirm")
    end = s.surface()
    assert end["surfaceId"] == "end_of_form"
    s.submit("end_of_form", {"/quote/first_name": "Samir", "/quote/last_name": "Haddad", "/quote/phone_number": "514 555-0142",
                             "/quote/handoff_summary": "Lamborghini Huracán 2023", "/quote/handoff_consent": True}, name="send")
    (created,) = s.find("handoff.created")
    assert created["data"]["reference"].startswith("CNS-") and created["data"]["rule_id"] == "VEHICULE_EXOTIQUE"


def test_reopening_a_chip_renders_it_again_in_edit_mode_without_the_agent(stack):
    s = Session(stack).consented()
    s.say("Je ne les ai pas")
    s.say("Je m’appelle Marie Tremblay")
    s.submit("a_propos", {"/quote/date_of_birth": "1987-01-18"})
    events = s.send("ui.surface_reopen", surface_id="a_propos")
    assert "turn.started" not in [e["type"] for e in events]
    reopened = s.surface()
    assert reopened["surfaceId"] == "a_propos" and reopened["dataModel"]["quote"]["date_of_birth"] == "1987-01-18"


def test_a_coverage_question_is_answered_with_citations(stack):
    s = Session(stack).consented()
    s.say("C’est quoi la franchise?")
    (done,) = [e for e in s.find("text.done") if e["actor"] == "main_agent"]
    citations = done["data"].get("citations") or []
    assert citations and all(isinstance(c, dict) and c["title"] for c in citations)  # the shape the web renders
