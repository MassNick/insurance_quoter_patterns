"""Component: the BFF in-process with a scripted FakeHostedAgent, fake MCP clients and in-memory stores."""

from __future__ import annotations

import re

from quote_common import session_ref

from quote_bff.fakes import event, reply
from tests.conftest import Api, chat_turn, find, form_event, types

LAMBO = "/vehicles/0/vehicle_make"  # the field whose value (Lamborghini) triggers the exotic-vehicle rule


def test_create_session_greets_sets_the_cookie_and_warms_the_agent(api: Api) -> None:
    body = api.start()
    assert body["quote_number"].startswith("AUTO-")
    assert types(body["events"]) == ["text.done", "suggestions"]
    greeting, suggestions = body["events"]
    assert greeting["actor"] == "bff" and greeting["data"]["text"].startswith("Bonjour")
    assert [i["label"] for i in suggestions["data"]["items"]][-1] == "Parler à un conseiller"
    assert suggestions["data"]["items"][-1]["action_id"] == "talk_to_advisor"
    cookie = api.client.cookies.jar
    assert any(c.name == "qsid" and c.secure and c.path == "/api" for c in cookie)
    assert [i["body"]["input"] for i in api.agent.invocations] == [{"kind": "warmup"}]


def test_session_cookie_attributes(api: Api) -> None:
    r = api.client.post("/api/sessions", json={})
    header = r.headers["set-cookie"]
    assert "HttpOnly" in header and "Secure" in header and "SameSite=strict" in header


def test_a_message_turn_commits_the_memory_result_and_relays_the_reply(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/quote/first_name", "Marie"), ("/vehicles/0/vehicle_year", 2019),
                                      signals=[{"code": "multi_vehicle_likely", "message": "2 autos", "confidence": 0.8,
                                                "related_paths": [], "suggested_follow_up": "Combien?"}],
                                      answer_mode="text", text="Merci Marie."))
    events = api.say("Je m'appelle Marie, j'ai une Civic 2019")
    assert "memory.result" not in types(events)
    assert types(events)[:5] == ["turn.started", "session.preferences", "signals", "state.delta", "text.delta"]
    delta = find(events, "state.delta")[0]["data"]["changes"]
    assert {c["path"]: (c["source"], c["status"]) for c in delta} == {
        "/quote/first_name": ("chat", "inferred"), "/vehicles/0/vehicle_year": ("chat", "inferred"),
        "/drivers/0/driver_name": ("system", "confirmed"), "/drivers/0/relationship_code": ("system", "confirmed")}
    assert find(events, "text.done")[0]["data"]["text"] == "Merci Marie."
    st = api.state()
    assert st["state"]["quote"]["first_name"]["value"] == "Marie"
    assert st["preferences"] == {"answer_mode": "text"}
    assert st["signals"][0]["code"] == "multi_vehicle_likely"
    assert len(api.rows("state_changes")) == len(delta)  # FR-LOG-2
    stored = sorted(api.rows("events"), key=lambda r: (r["turn"], r["seq"]))
    memory_row = next(r for r in stored if r["type"] == "memory.result")
    assert memory_row["forwarded"] is False
    assert stored[-1]["type"] == "turn.completed"


def test_the_invocation_body_and_headers(api: Api) -> None:
    api.start()
    api.say("Bonjour")
    call = api.agent.invocations[-1]
    body = call["body"]
    assert set(body) == {"turn", "input", "state", "phase", "open_surfaces", "signals", "preferences", "rule_hits",
                         "system_notes", "session_ref"}
    assert body["input"] == {"kind": "message", "text": "Bonjour"}
    assert session_ref.verify(body["session_ref"], api.bff.settings.session_ref_key.encode()) == api.sid
    assert call["headers"]["x-client-session-id"] == api.sid
    assert call["headers"]["x-client-turn-id"] == f"{api.sid}:{body['turn']}"
    assert call["headers"]["traceparent"].startswith("00-")


def test_deterministic_consent_needs_two_confirmation_steps(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/quote/contact_consent", True)))
    events = api.say("Oui, j'accepte")
    required = find(events, "confirm.required")[0]["data"]
    assert required["path"] == "/quote/contact_consent" and required["new_value"] is True and required["steps"] == 2
    assert api.state()["state"]["quote"]["contact_consent"]["status"] == "pending_confirmation"
    cid = required["confirmation_id"]

    assert api.send("confirm.response", confirmation_id=cid, step=1, accepted=True) == []
    events = api.send("confirm.response", confirmation_id=cid, step=2, accepted=True)
    change = find(events, "state.delta")[0]["data"]["changes"][0]
    assert (change["path"], change["status"]) == ("/quote/contact_consent", "confirmed")
    row = api.rows("confirmations")[0]
    assert row["step1_at"] and row["step2_at"] and row["status"] == "accepted" and row["text_shown"]
    # a confirmation raised from chat resumes the agent, whose last reply waited on the answer
    assert len(api.agent.invocations) == 3  # warmup + the message + confirmation_result
    assert {k: v for k, v in api.agent.invocations[-1]["body"]["input"].items() if k != "confirmation_id"} == {
        "kind": "confirmation_result", "accepted": True, "changed_paths": ["/quote/contact_consent"]}


def test_a_refused_confirmation_reverts_and_informs_the_agent(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/underwriting/has_claims", True)))
    cid = find(api.say("J'ai eu un accrochage"), "confirm.required")[0]["data"]["confirmation_id"]
    events = api.send("confirm.response", confirmation_id=cid, step=1, accepted=False)
    assert find(events, "confirm.rejected")[0]["data"]["reason"] == "rejected"
    assert api.state()["state"]["underwriting"]["has_claims"]["value"] is None
    assert {k: v for k, v in api.agent.invocations[-1]["body"]["input"].items() if k != "confirmation_id"} == {
        "kind": "confirmation_result", "accepted": False, "changed_paths": ["/underwriting/has_claims"]}
    assert api.rows("confirmations")[0]["status"] == "rejected"


def test_a_form_submit_closes_commits_checks_and_invokes_the_agent(api: Api) -> None:
    api.start()
    api.agent.script.append(reply("Voici un court formulaire.", form_event(
        "vehicule_0", "Votre véhicule", [{"key": "vehicle_make"}, {"key": "usage_type"}],
        entity={"type": "vehicle", "index": 0})))
    events = api.say("Je veux assurer mon auto")
    created = find(events, "a2ui.message")[0]
    assert "form_spec" not in created and created["data"]["createSurface"]["surfaceId"] == "vehicule_0"
    assert api.state()["surfaces"] == [{"surface_id": "vehicule_0", "title": "Votre véhicule",
                                        "chip": "Votre véhicule", "status": "open"}]

    bad = api.submit("vehicule_0", {"/vehicles/0/vehicle_make": "Honda", "/vehicles/0/usage_type": "space"})
    assert bad[0]["data"]["updateDataModel"]["path"] == "/_errors"
    assert "/vehicles/0/usage_type" in bad[0]["data"]["updateDataModel"]["value"]

    events = api.submit("vehicule_0", {"/vehicles/0/vehicle_make": "Honda", "/vehicles/0/usage_type": "commute",
                                       "/quote/email": "not-bound@example.com"})
    assert types(events)[:2] == ["a2ui.message", "state.delta"]
    assert events[0]["data"] == {"version": "1.0", "deleteSurface": {"surfaceId": "vehicule_0"}}
    assert {c["path"] for c in events[1]["data"]["changes"]} == {"/vehicles/0/vehicle_make", "/vehicles/0/usage_type"}
    assert "turn.started" in types(events)
    body = api.agent.invocations[-1]["body"]
    assert body["input"]["kind"] == "form_submitted" and body["input"]["surface_id"] == "vehicule_0"
    assert api.state()["surfaces"][0]["status"] == "submitted"
    assert api.state()["state"]["quote"] == {}  # an unbound path in the context is ignored


def test_lamborghini_inferred_stop_asks_then_confirm_opens_the_end_of_form_and_send_creates_a_handoff(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn((LAMBO, "Lamborghini"), text="Belle voiture! Quel modèle?"))
    events = api.say("J'ai une Lamborghini")
    hit = find(events, "rules.hit")[0]["data"]
    assert (hit["rule_id"], hit["severity"], hit["inferred"]) == ("VEHICULE_EXOTIQUE", "stop", True)
    question = [e for e in find(events, "text.done") if e["actor"] == "bff"]
    assert len(question) == 1 and "Lamborghini" in question[0]["data"]["text"]
    assert [i["action_id"] for i in find(events, "suggestions")[0]["data"]["items"]] == ["rule_confirm",
                                                                                          "rule_correct"]
    assert not [e for e in events if e["actor"] == "main_agent" and e["type"] in ("text.delta", "text.done")]
    assert "a2ui.message" not in types(events)  # no end-of-form surface yet
    assert any(r["type"] == "text.done" and r["actor"] == "main_agent" and not r["forwarded"]
               for r in api.rows("events"))  # the dropped reply is still logged

    calls = len(api.agent.invocations)
    events = api.say("Oui, c'est bien ça", action_id="rule_confirm", via="suggestion")
    assert "turn.started" not in types(events) and len(api.agent.invocations) == calls
    assert find(events, "rules.hit")[0]["data"]["inferred"] is False
    surface = find(events, "a2ui.message")[-1]["data"]["createSurface"]
    assert surface["surfaceId"] == "end_of_form"
    tool, args = api.bff.a2ui.calls[-1]
    assert tool == "render_form" and "VEHICULE_EXOTIQUE" in args["form_spec"]["fields"][0]["hint"]
    assert api.state()["state"]["vehicles"][0]["vehicle_make"]["status"] == "confirmed"
    assert api.doc()["stop"]["rule_id"] == "VEHICULE_EXOTIQUE"

    # get_quote is refused while the STOP stands (FR-RUL-4)
    events = api.say("Obtenir ma soumission", action_id="get_quote")
    assert find(events, "text.done")[0]["actor"] == "bff" and len(api.agent.invocations) == calls

    values = {"/quote/first_name": "Samir", "/quote/last_name": "Haddad", "/quote/phone_number": "514 555-0142",
              "/quote/handoff_summary": "Résumé modifié par le client."}
    refused = api.submit("end_of_form", values, name="send")
    assert "/quote/handoff_consent" in refused[0]["data"]["updateDataModel"]["value"]
    assert api.rows("handoffs") == []

    events = api.submit("end_of_form", {**values, "/quote/handoff_consent": True}, name="send")
    created = find(events, "handoff.created")[0]["data"]
    assert created["rule_id"] == "VEHICULE_EXOTIQUE" and re.fullmatch(r"CNS-\d{6}", created["reference"])
    row = api.rows("handoffs")[0]
    assert row["reference"] == created["reference"] and row["summary"] == "Résumé modifié par le client."
    assert row["contact"]["phone_number"] == "514 555-0142" and row["consent_at"] and row["status"] == "new"
    assert events[-1]["data"] == {"version": "1.0", "deleteSurface": {"surfaceId": "end_of_form"}}
    assert {s["surface_id"]: s["status"] for s in api.state()["surfaces"]}["end_of_form"] == "sent"
    assert find(api.send("ui.surface_reopen", surface_id="end_of_form"), "error")[0]["data"]["code"] == "chips_locked"


def test_rule_correct_reverts_the_value_and_starts_a_normal_turn(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn((LAMBO, "Lamborghini")))
    api.say("J'ai une Lamborghini")
    events = api.say("Non, je corrige", action_id="rule_correct")
    assert find(events, "state.delta")[0]["data"]["changes"][0] == {"path": LAMBO, "value": None, "source": "chat",
                                                                     "status": "inferred"}
    assert "turn.started" in types(events)
    assert api.agent.invocations[-1]["body"]["input"]["action_id"] == "rule_correct"
    assert api.agent.invocations[-1]["body"]["input"]["changed_paths"] == [LAMBO]
    assert api.doc()["rule_question"] is None and api.doc()["stop"] is None
    assert api.rows("state_changes")[-1]["rule_id"] == "VEHICULE_EXOTIQUE"


def test_a_firm_stop_from_a_form_closes_surfaces_and_does_not_invoke_the_agent(api: Api) -> None:
    api.start()
    api.agent.script.append(reply("Formulaire.", form_event("usage", "Utilisation", [{"key": "usage_type"}],
                                                            entity={"type": "vehicle", "index": 0})))
    api.say("Mon véhicule")
    calls = len(api.agent.invocations)
    events = api.submit("usage", {"/vehicles/0/usage_type": "farm"})
    assert "turn.started" not in types(events) and len(api.agent.invocations) == calls
    assert find(events, "rules.hit")[0]["data"]["rule_id"] == "USAGE_AGRICOLE"
    assert find(events, "a2ui.message")[-1]["data"]["createSurface"]["surfaceId"] == "end_of_form"


def test_a_rule_question_on_the_chat_path_drops_the_in_flight_reply(api: Api) -> None:
    api.start()
    api.agent.script.append(reply("Formulaire.", form_event(
        "vehicule_0", "Votre véhicule", [{"key": "modified_for_performance"}], entity={"type": "vehicle", "index": 0})))
    api.say("Mon véhicule")
    api.agent.script.append(chat_turn(("/vehicles/0/modified_for_performance", True), text="Parlons-en."))
    events = api.say("Il est modifié pour la performance")
    # the open form gets the live autofill (value, then provenance), then the question; the main reply is dropped
    autofill = [e["data"]["updateDataModel"] for e in find(events, "a2ui.message")]
    assert [(u["surfaceId"], u["path"]) for u in autofill] == [
        ("vehicule_0", "/vehicles/0/modified_for_performance"),
        ("vehicule_0", "/_provenance/~1vehicles~10~1modified_for_performance")]
    assert autofill[1]["value"] == {"source": "chat", "note": "dit en clavardage"}
    assert find(events, "rules.hit")[0]["data"]["rule_id"] == "VEHICULE_MODIFIE_PERFORMANCE"
    assert not [e for e in events if e["actor"] == "main_agent" and e["type"] == "text.done"]


def test_a_young_principal_driver_is_flagged_once_and_the_journey_continues(api: Api) -> None:
    api.start()
    api.consent()  # dates of birth are personal data (FR-INT-2)
    api.agent.script.append(reply("Qui conduit quoi?", form_event(
        "qui_conduit_quoi", "Qui conduit quoi?", [{"key": "/household/driver_vehicle_assignments",
                                                   "widget": "assignment_board"}])))
    api.agent.script.append(chat_turn(("/quote/date_of_birth", "1986-03-14"),
                                      ("/drivers/1/driver_date_of_birth", "2007-03-15"),
                                      ("/drivers/1/relationship_code", "child"), ("/vehicles/0/vehicle_make", "Honda")))
    api.say("Ma fille de 19 ans conduit la Honda")
    api.say("Qui conduit?")
    events = api.submit("qui_conduit_quoi", {"/household/driver_vehicle_assignments": [
        {"driver_index": 0, "vehicle_index": 0, "role": "occasional"},
        {"driver_index": 1, "vehicle_index": 0, "role": "principal"}]})
    hit = find(events, "rules.hit")[0]["data"]
    assert (hit["rule_id"], hit["severity"]) == ("JEUNE_CONDUCTEUR_PRINCIPAL", "flag")
    assert "turn.started" in types(events)  # the journey continues
    body = api.agent.invocations[-1]["body"]
    assert body["rule_hits"][0]["rule_id"] == "JEUNE_CONDUCTEUR_PRINCIPAL"
    assert "Rule JEUNE_CONDUCTEUR_PRINCIPAL: flag" in body["system_notes"]
    assert api.state()["advisor_signals"][0]["rule_id"] == "JEUNE_CONDUCTEUR_PRINCIPAL"
    api.agent.script.append(chat_turn(("/vehicles/0/usage_type", "commute")))
    assert "rules.hit" not in types(api.say("Pour le travail"))  # acknowledged: not reported again


def test_suggestions_are_filtered_before_they_are_relayed(api: Api) -> None:
    api.start()
    items = [{"label": "Oui, c'est marie@example.com", "kind": "reply"},
             {"label": "x" * 60, "kind": "reply"},
             {"label": "Continuer ma soumission", "kind": "reply"},
             {"label": "Parler à un conseiller", "kind": "action", "action_id": "talk_to_advisor"}]
    api.agent.script.append(reply("Bien.", event("suggestions", {"items": items})))
    relayed = find(api.say("ok"), "suggestions")[0]["data"]["items"]
    assert [i["label"] for i in relayed] == ["Continuer ma soumission", "Parler à un conseiller"]
    dropped = next(r for r in api.rows("events") if r["type"] == "suggestions.dropped")
    assert [d["reason"] for d in dropped["data"]["items"]] == ["pii_or_value", "too_long"]
    assert dropped["forwarded"] is False


def test_talk_to_advisor_opens_the_end_of_form_without_a_rule(api: Api) -> None:
    api.start()
    calls = len(api.agent.invocations)
    events = api.say("Parler à un conseiller", action_id="talk_to_advisor", via="header")
    assert types(events) == ["a2ui.message"] and len(api.agent.invocations) == calls
    spec = api.bff.a2ui.calls[-1][1]["form_spec"]
    assert spec["form_id"] == "end_of_form" and "conseiller" in spec["fields"][0]["hint"]
    assert "règle" not in spec["fields"][0]["hint"]


def test_after_three_off_topic_turns_the_bff_redirects_without_the_agent(api: Api) -> None:
    api.start()
    for _ in range(3):
        api.agent.script.append(chat_turn(off_topic=True))
        assert types(api.say("Quelle est la météo?")).count("guardrail") == 1  # the agent's, relayed once
    calls = len(api.agent.invocations)
    events = api.say("Et le hockey?")
    assert types(events) == ["text.done", "suggestions"] and len(api.agent.invocations) == calls


def test_a_reply_that_streams_before_the_memory_result_waits_for_it(api: Api) -> None:
    """events.md rule 6: a STOP found in this turn's memory result drops the reply, even if it streamed first."""
    api.start()
    early = reply("Belle voiture! Quel modèle?")
    api.agent.script.append([*early[:-1], chat_turn((LAMBO, "Lamborghini"))[1], early[-1]])
    events = api.say("J'ai une Lamborghini")
    assert find(events, "rules.hit") and types(events)[-1] == "turn.completed"
    assert not [e for e in events if e["actor"] == "main_agent" and e["type"] in ("text.delta", "text.done")]

    api.agent.script.append([*early[:-1], chat_turn()[1], early[-1]])  # nothing to stop: the reply goes out
    events = api.say("Elle est rouge")
    assert [e["data"]["text"] for e in find(events, "text.done")] == ["Belle voiture! Quel modèle?"]


def test_the_full_form_consent_question_goes_through_the_commit_path_then_opens_the_form(api: Api) -> None:
    api.start()
    items = find(api.send("ui.surface_reopen", surface_id="full_form"), "suggestions")[0]["data"]["items"]
    assert items[0] == {"label": "Oui, j’accepte", "kind": "action", "action_id": "consent_yes"}
    calls = len(api.agent.invocations)
    cid = find(api.say(items[0]["label"], action_id="consent_yes"), "confirm.required")[0]["data"]["confirmation_id"]
    api.send("confirm.response", confirmation_id=cid, step=1, accepted=True)
    events = api.send("confirm.response", confirmation_id=cid, step=2, accepted=True)
    assert find(events, "a2ui.message")[-1]["data"]["createSurface"]["surfaceId"] == "full_form"
    assert len(api.agent.invocations) == calls and api.doc()["after_consent"] is None


def test_an_agent_form_after_a_stop_is_not_relayed(api: Api) -> None:
    api.start()
    api.say("Parler", action_id="talk_to_advisor")
    api.doc()["stop"] = {"rule_id": "USAGE_AGRICOLE", "reason": "r", "paths": []}
    api.agent.script.append(reply("Formulaire.", form_event("autre", "Autre", [{"key": "/quote/email"}])))
    events = api.say("Bonjour")
    assert not [e for e in find(events, "a2ui.message") if "createSurface" in e["data"]]


def test_a_pending_confirmation_expires_after_its_ttl(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/quote/contact_consent", True)))
    cid = find(api.say("Oui"), "confirm.required")[0]["data"]["confirmation_id"]
    api.doc()["confirmations"][cid]["created_ts"] -= 901  # FR-DET-4: 900 s
    events = api.say("Bonjour")
    assert find(events, "confirm.rejected")[0]["data"] == {"confirmation_id": cid, "reason": "expired",
                                                           "paths": ["/quote/contact_consent"]}
    assert api.state()["state"]["quote"]["contact_consent"]["value"] is None
    assert api.rows("confirmations")[0]["status"] == "expired"
    assert find(api.send("confirm.response", confirmation_id=cid, step=1, accepted=True), "error")


def test_editing_the_answer_that_stopped_the_quote_lifts_the_stop(api: Api) -> None:
    api.start()
    api.agent.script.append(reply("Formulaire.", form_event("usage", "Utilisation", [{"key": "usage_type"}],
                                                            entity={"type": "vehicle", "index": 0})))
    api.say("Mon véhicule")
    api.submit("usage", {"/vehicles/0/usage_type": "farm"})
    assert api.doc()["stop"]["rule_id"] == "USAGE_AGRICOLE"
    closed = api.submit("end_of_form", {}, name="edit_answers")  # "Modifier mes réponses"
    assert closed[0]["data"]["deleteSurface"]["surfaceId"] == "end_of_form"
    api.send("ui.surface_reopen", surface_id="usage")
    events = api.submit("usage", {"/vehicles/0/usage_type": "commute"})
    assert "rules.hit" not in types(events) and api.doc()["stop"] is None and api.doc()["rule_hits"] == []
    assert any(r["type"] == "rules.hit" for r in api.rows("events"))  # the earlier hit stays in the log
    api.say("Obtenir ma soumission", action_id="get_quote")
    assert api.agent.invocations[-1]["body"]["input"]["action_id"] == "get_quote"


def test_a_chat_contradiction_of_a_form_value_is_a_conflict_for_the_next_turn(api: Api) -> None:
    api.start()
    api.agent.script.append(reply("Formulaire.", form_event("annee", "Année", [{"key": "vehicle_year"}],
                                                            entity={"type": "vehicle", "index": 0})))
    api.say("Mon auto")
    api.submit("annee", {"/vehicles/0/vehicle_year": 2019})
    api.agent.script.append(chat_turn(("/vehicles/0/vehicle_year", 2020)))
    events = api.say("En fait c'est une 2020")
    assert "state.delta" not in types(events)  # FR-MEM-4: no state change
    assert api.state()["state"]["vehicles"][0]["vehicle_year"]["value"] == 2019
    api.say("Et alors?")
    notes = api.agent.invocations[-1]["body"]["system_notes"]
    assert any(n.startswith("Conflict on /vehicles/0/vehicle_year") for n in notes)


def test_a_second_advisor_request_after_a_handoff_repeats_the_reference(api: Api) -> None:
    api.start()
    api.say("Parler", action_id="talk_to_advisor")
    api.submit("end_of_form", {"/quote/first_name": "Léa", "/quote/last_name": "Roy",
                               "/quote/phone_number": "418 555-0100", "/quote/handoff_consent": True}, name="send")
    reference = api.rows("handoffs")[0]["reference"]
    events = api.say("Parler", action_id="talk_to_advisor")
    assert types(events) == ["text.done"] and reference in events[0]["data"]["text"]
    assert len(api.rows("handoffs")) == 1


def test_modify_quote_actions_carry_the_quote_id_and_the_changes(api: Api) -> None:
    api.start()
    api.say("Réduire ma prime", action_id="modify_quote", quote_id="Q-1", changes={"deductible": "1000"})
    assert api.agent.invocations[-1]["body"]["input"] == {"kind": "action", "action_id": "modify_quote",
                                                          "text": "Réduire ma prime", "quote_id": "Q-1",
                                                          "changes": {"deductible": "1000"}}
    adjust = form_event("ajuster", "Ajuster", [{"key": "/underwriting/deductible"}])
    api.agent.script.append(reply("Ajustez.", adjust))
    api.say("Ajuster")
    api.send("a2ui.action", name="modify_quote", surfaceId="ajuster", sourceComponentId="b", timestamp="t",
             context={"values": {"/underwriting/deductible": 1000}, "quote_id": "Q-1"})
    sent = api.agent.invocations[-1]["body"]["input"]
    assert (sent["kind"], sent["action_id"], sent["quote_id"], sent["changes"]) == (
        "action", "modify_quote", "Q-1", {"deductible": "1000"})


def test_model_triggered_lookup_results_are_relayed_unchanged(api: Api) -> None:
    api.start()
    result = {"name": "quoting.decode_vin", "result": {"valid_checksum": True, "year": 2021, "make": "Toyota"},
              "ms": 40, "result_mode": "terminal"}
    api.agent.script.append(reply("Voici.", event("tool.call", {"name": "quoting.decode_vin"}, actor="tool:quoting"),
                                  event("tool.result", result, actor="tool:quoting")))
    events = api.say("Mon NIV")
    assert find(events, "tool.result")[0]["data"] == result
    assert "state.delta" not in types(events)  # the customer's submit of the filled form commits it


def test_an_empty_assignment_board_is_never_committed(api: Api) -> None:
    api.start()
    board = [{"key": "/household/driver_vehicle_assignments", "widget": "assignment_board"}]
    api.agent.script.append(reply("Qui conduit?", form_event("board", "Qui conduit quoi?", board)))
    api.say("Qui conduit?")
    events = api.submit("board", {"/household/driver_vehicle_assignments": []})
    assert "state.delta" not in types(events) and api.state()["state"]["household"] == {}


def test_personal_data_from_chat_waits_for_the_consent(api: Api) -> None:
    api.start()
    api.agent.script.append(chat_turn(("/quote/email", "marie@example.com"), ("/quote/first_name", "Marie")))
    events = api.say("Marie, marie@example.com")
    paths = [c["path"] for c in find(events, "state.delta")[0]["data"]["changes"]]
    assert "/quote/first_name" in paths and "/quote/email" not in paths
    assert "email" not in api.state()["state"]["quote"]
    assert not [r for r in api.rows("state_changes") if r["path"] == "/quote/email"]
    api.say("Et ensuite?")
    notes = api.agent.invocations[-1]["body"]["system_notes"]
    assert any("ask again after consent" in n and "/quote/email" in n for n in notes)

    api.consent()
    api.agent.script.append(chat_turn(("/quote/email", "marie@example.com")))
    events = api.say("marie@example.com")
    assert "/quote/email" in [c["path"] for c in find(events, "state.delta")[0]["data"]["changes"]]
    assert api.state()["state"]["quote"]["email"]["value"] == "marie@example.com"


def test_the_handoff_works_before_the_consent(api: Api) -> None:
    api.start()
    api.say("Parler", action_id="talk_to_advisor")
    events = api.submit("end_of_form", {"/quote/first_name": "Léa", "/quote/last_name": "Roy",
                                        "/quote/phone_number": "418 555-0100", "/quote/email": "lea@example.com",
                                        "/quote/handoff_consent": True}, name="send")
    assert find(events, "handoff.created")
    assert api.rows("handoffs")[0]["contact"]["phone_number"] == "418 555-0100"
    assert api.state()["state"]["quote"]["email"]["value"] == "lea@example.com"
