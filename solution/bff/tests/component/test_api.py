"""Component: HTTP surface details: SSE framing and seq, quotes, the internal state endpoint, cookie, problem+json."""

from __future__ import annotations

import json

from quote_common import session_ref

from quote_bff.fakes import event, reply
from tests.conftest import Api, chat_turn

# Exactly the keys of an envelope forwarded to the web: ``form_spec`` is never one of them.
ENVELOPE_KEYS = {"type", "session_id", "turn", "seq", "ts", "actor", "data"}


def test_sse_framing_and_monotonic_seq(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/quote/first_name", "Marie")))
    r = api.client.post(f"/api/sessions/{api.sid}/turns", json={"type": "user.message", "data": {"text": "Marie"}})
    blocks = r.text.split("\n\n")
    assert blocks[-1] == "" and all(b.startswith("data: ") and "\n" not in b for b in blocks[:-1])
    events = [json.loads(b[len("data: "):]) for b in blocks[:-1]]
    assert all(set(e) == ENVELOPE_KEYS for e in events)
    assert len({e["turn"] for e in events}) == 1 and all(e["session_id"] == api.sid for e in events)
    seqs = [e["seq"] for e in events]
    assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
    assert events[-1]["type"] == "turn.completed"
    # every forwarded event was persisted first, with the same turn and seq
    stored = {(row["turn"], row["seq"]) for row in api.rows("events") if row["forwarded"]}
    assert {(e["turn"], e["seq"]) for e in events} <= stored


def test_quotes_are_persisted_from_quote_events(api: Api) -> None:
    api.start()
    offers_v1 = [{"offer_type": "essential", "monthly": 120.0}, {"offer_type": "plus", "monthly": 140.0}]
    offers_v2 = [{"offer_type": "essential", "monthly": 110.5}, {"offer_type": "plus", "monthly": 131.0}]
    api.agent.script += [reply("Voici votre soumission.", event("quote.created", {
        "quote_id": "Q-1", "version": 1, "offers": offers_v1}, actor="tool:quoting.create_quote")),
        reply("Nouvelle version.", event("quote.updated", {"quote_id": "Q-1", "version": 2, "offers": offers_v2,
                                                           "previous_version": 1, "input_hash": "sha256:ab12"},
                                         actor="tool:quoting.modify_quote"))]
    api.say("Obtenir ma soumission", action_id="get_quote")
    assert api.agent.invocations[-1]["body"]["input"]["missing_fields"]  # readiness travels with the action
    api.say("Franchise de 1000")
    quotes = api.client.get(f"/api/sessions/{api.sid}/quotes").json()["quotes"]
    assert [(q["version"], q["reason"], q["previous_version"]) for q in quotes] == [(1, "created", None),
                                                                                     (2, "modified", 1)]
    assert quotes[1]["delta_monthly"] == -9.5
    assert [r["input_hash"] for r in sorted(api.rows("quotes"), key=lambda r: r["version"])] == [None, "sha256:ab12"]


def test_the_internal_state_endpoint_needs_a_session_ref_for_that_session(api: Api) -> None:
    api.start()
    key = api.bff.settings.session_ref_key.encode()
    url = f"/internal/sessions/{api.sid}/state"
    assert api.client.get(url).status_code == 401
    assert api.client.get(url, headers={"Authorization": "Bearer x"}).status_code == 401
    other = session_ref.sign("s_other", key)
    assert api.client.get(url, headers={"Authorization": f"SessionRef {other}"}).status_code == 401
    expired = session_ref.sign(api.sid, key, ttl_seconds=-1)
    assert api.client.get(url, headers={"Authorization": f"SessionRef {expired}"}).status_code == 401
    forged = session_ref.sign(api.sid, b"dev-only-wrong-key")
    assert api.client.get(url, headers={"Authorization": f"SessionRef {forged}"}).status_code == 401
    r = api.client.get(url, headers={"Authorization": f"SessionRef {session_ref.sign(api.sid, key)}"})
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"state", "preferences", "quote_blocked"} and body["quote_blocked"] is False
    assert set(body["state"]) == {"quote", "vehicles", "drivers", "underwriting", "household"}
    assert body["preferences"] == {"answer_mode": None}
    unknown = session_ref.sign("s_unknown", key)
    assert api.client.get("/internal/sessions/s_unknown/state",
                          headers={"Authorization": f"SessionRef {unknown}"}).status_code == 404


def test_session_routes_need_the_cookie_of_that_session(api: Api) -> None:
    api.start()
    sid, cookie = api.sid, api.client.cookies.get("qsid")
    api.client.cookies.clear()
    r = api.client.get(f"/api/sessions/{sid}/state")
    assert r.status_code == 403 and r.headers["content-type"] == "application/problem+json"
    forged = api.client.get(f"/api/sessions/{sid}/state", headers={"Cookie": f"qsid={sid}.forged"})
    assert forged.status_code == 403
    assert api.client.get(f"/api/sessions/{sid}/state", headers={"Cookie": f"qsid={cookie}"}).status_code == 200


def test_errors_are_problem_json_without_stack_traces(api: Api) -> None:
    api.start()
    r = api.client.post(f"/api/sessions/{api.sid}/turns", json={"type": "rm -rf", "data": {}})
    assert r.status_code == 422 and r.headers["content-type"] == "application/problem+json"
    assert set(r.json()) == {"type", "title", "status", "detail"} and "Traceback" not in r.text
    too_long = {"type": "user.message", "data": {"text": "x" * 2001}}
    long = api.client.post(f"/api/sessions/{api.sid}/turns", json=too_long)
    assert long.status_code == 422


def test_a_hosted_agent_failure_becomes_an_error_event(api: Api) -> None:
    api.start()

    def boom(body: dict) -> list:
        raise RuntimeError("agent crashed")

    api.agent.script.append(boom)
    events = api.say("Bonjour")
    assert events[-1]["type"] == "error" and events[-1]["data"]["retryable"] is True
    assert "agent crashed" not in json.dumps(events)


def test_health(api: Api) -> None:
    assert api.client.get("/api/health").json() == {"status": "ok"}
    r = api.client.get("/api/health/sse")
    assert r.text == 'data: {"type":"health"}\n\n' and r.headers["content-type"].startswith("text/event-stream")
