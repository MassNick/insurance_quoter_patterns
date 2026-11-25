"""Shared fixtures: a BFF wired to in-memory stores and in-process fakes, a TestClient, and event builders."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest
from fastapi.testclient import TestClient
from quote_common.envelope import sse_decode

from quote_bff.app import create_app
from quote_bff.config import Settings
from quote_bff.fakes import FakeA2ui, FakeDocumentExtractor, FakeHostedAgent, FakeQuoting, event, reply
from quote_bff.rules import end_of_form as eof
from quote_bff.service import Bff
from quote_bff.stores import InMemoryStore, LocalBlobStore

TODAY = date(2026, 11, 25)  # the day every test runs on (see pinned_today)


@pytest.fixture(autouse=True)
def pinned_today(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the rules module's clock, so ages and years licensed do not depend on the day the tests run."""
    monkeypatch.setattr(eof, "today", lambda: TODAY)


def memory(*changes: tuple, signals: list | None = None, conflicts: list | None = None, off_topic: bool = False,
           answer_mode: str | None = None) -> dict[str, Any]:
    """A memory.result event. Each change: (path, value) or (path, value, confidence) or (path, value, conf, op)."""
    items = []
    for c in changes:
        path, value, confidence, op = [*c, *[0.95, "set"][len(c) - 2:]]
        items.append({"path": path, "op": op, "value": value, "confidence": confidence,
                      "evidence": "dit en clavardage"})
    return event("memory.result", {
        "state_changes": items, "signals": signals or [], "conflicts": conflicts or [],
        "intent": {"phase_hint": "gather", "off_topic": off_topic},
        "session_preferences": {"answer_mode": answer_mode} if answer_mode else None}, actor="memory_agent")


def chat_turn(*changes: tuple, text: str = "D’accord.", **kw: Any) -> list[dict[str, Any]]:
    """A chat turn where the memory result lands before the main agent's reply (the reply streams after it)."""
    guardrail = [event("guardrail", {"category": "off_topic", "action": "redirect"}, actor="memory_agent")]
    return [event("turn.started", {"phase": "gather"}), memory(*changes, **kw),
            *(guardrail if kw.get("off_topic") else []), *reply(text)[1:]]  # the hosted agent's guardrail event


def form_event(form_id: str, title: str, fields: list[dict[str, Any]], **spec: Any) -> dict[str, Any]:
    """The hosted agent's a2ui.message for a render_form call, with the FormSpec next to the data."""
    form_spec = {"form_id": form_id, "title": title, "fields": fields, **spec}
    message = {"version": "1.0", "createSurface": {"surfaceId": form_id, "components": [
        {"id": "title", "component": "Text", "text": "## " + title}]}}
    from quote_bff.state import CATALOG

    for i, f in enumerate(fields):
        if f.get("widget") == "document_drop":
            message["createSurface"]["components"].append({"id": f"f_{i}", "component": "DocumentDrop",
                                                           "documents": f["data"]["documents"]})
        elif f.get("key"):
            path = CATALOG.resolve_path(f["key"], spec.get("entity"))
            message["createSurface"]["components"].append({"id": f"f_{i}", "component": "TextField",
                                                           "value": {"@path": path}})
    return event("a2ui.message", message, actor="tool:a2ui.render_form", form_spec=form_spec)


class Api:
    """Thin helpers over the TestClient."""

    def __init__(self, client: TestClient, bff: Bff, agent: FakeHostedAgent) -> None:
        """``bff`` and ``agent`` give tests the stores and the scripted agent; ``sid`` is the current session id."""
        self.client, self.bff, self.agent = client, bff, agent
        self.sid = ""

    def start(self, locale: str = "fr-CA") -> dict[str, Any]:
        """POST ``/api/sessions`` and remember the new session id."""
        r = self.client.post("/api/sessions", json={"locale": locale})
        assert r.status_code == 201, r.text
        self.sid = r.json()["session_id"]
        return r.json()

    def send(self, type_: str, **data: Any) -> list[dict[str, Any]]:
        """POST one inbound event to ``/turns`` and return the decoded SSE envelopes."""
        r = self.client.post(f"/api/sessions/{self.sid}/turns", json={"type": type_, "data": data})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith("text/event-stream")
        return sse_decode(r.text)

    def say(self, text: str, **data: Any) -> list[dict[str, Any]]:
        """Send a ``user.message``; extra keys (``action_id``, ``via``...) go into its data."""
        return self.send("user.message", text=text, **data)

    def submit(self, surface_id: str, values: dict[str, Any], name: str = "submit") -> list[dict[str, Any]]:
        """Send an ``a2ui.action`` (``submit`` by default) with ``values`` as the bound-value map of the form."""
        return self.send("a2ui.action", name=name, surfaceId=surface_id, sourceComponentId=f"action_{name}",
                         timestamp="2026-11-25T10:00:00Z", context={"values": values})

    def consent(self) -> None:
        """The real consent flow: chat says yes, then both confirmation steps."""
        self.agent.script.append(chat_turn(("/quote/contact_consent", True)))
        cid = find(self.say("Oui, j’accepte"), "confirm.required")[0]["data"]["confirmation_id"]
        self.send("confirm.response", confirmation_id=cid, step=1, accepted=True)
        self.send("confirm.response", confirmation_id=cid, step=2, accepted=True)

    def state(self) -> dict[str, Any]:
        """GET ``/state``: what the web sees."""
        return self.client.get(f"/api/sessions/{self.sid}/state").json()

    def doc(self) -> dict[str, Any]:
        """The stored session document, read straight from the in-memory store."""
        return self.bff.store.data["sessions"][(self.sid, self.sid)]

    def rows(self, container: str) -> list[dict[str, Any]]:
        """The stored rows of one container for this session."""
        return [r for (sid, _), r in self.bff.store.data[container].items() if sid == self.sid]


@pytest.fixture
def agent() -> FakeHostedAgent:
    """The scripted fake hosted agent: tests append replies to its ``script``."""
    return FakeHostedAgent()


@pytest.fixture
def bff(agent: FakeHostedAgent, tmp_path: Any) -> Bff:
    """A Bff on in-memory stores and a temp-folder blob store, with every dependency faked."""
    return Bff(Settings(), InMemoryStore(), LocalBlobStore(str(tmp_path)), agent, FakeA2ui(), FakeQuoting(),
               FakeDocumentExtractor())


@pytest.fixture
def api(bff: Bff, agent: FakeHostedAgent) -> Api:
    """A TestClient on that Bff (https, so the Secure cookie is sent), wrapped in the ``Api`` helper."""
    with TestClient(create_app(bff.settings, bff), base_url="https://testserver") as client:
        yield Api(client, bff, agent)


def types(events: list[dict[str, Any]]) -> list[str]:
    """The ``type`` of each event, in order."""
    return [e["type"] for e in events]


def find(events: list[dict[str, Any]], type_: str) -> list[dict[str, Any]]:
    """The events of one type."""
    return [e for e in events if e["type"] == type_]
