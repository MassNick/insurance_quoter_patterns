"""Component: form chips, reopen in edit mode, full-form mode (D16, D20). No hosted-agent call on any of these."""

from __future__ import annotations

from quote_bff.fakes import reply
from tests.conftest import Api, chat_turn, find, form_event, types


def _vehicle_form_submitted(api: Api) -> None:
    """A session where the agent showed the vehicle form and the customer submitted it (its chip is ``submitted``)."""
    api.start()
    api.agent.script.append(reply("Votre véhicule.", form_event(
        "vehicule_0", "Votre véhicule", [{"key": "vehicle_make", "prefill": {"value": "Honda", "source": "chat"}},
                                         {"key": "usage_type"}], entity={"type": "vehicle", "index": 0})))
    api.say("Mon auto")
    api.submit("vehicule_0", {"/vehicles/0/vehicle_make": "Honda", "/vehicles/0/usage_type": "pleasure"})


def test_reopening_a_submitted_chip_renders_it_in_edit_mode_and_saving_commits_only_changes(api: Api) -> None:
    _vehicle_form_submitted(api)
    calls = len(api.agent.invocations)
    events = api.send("ui.surface_reopen", surface_id="vehicule_0")
    assert types(events) == ["a2ui.message"]
    tool, args = api.bff.a2ui.calls[-1]
    assert tool == "render_form" and args["mode"] == "edit"
    assert all("prefill" not in f for f in args["form_spec"]["fields"])  # replaced by the committed state
    assert args["state"]["vehicles"][0]["usage_type"]["value"] == "pleasure"
    assert [a["id"] for a in args["form_spec"]["actions"]] == ["cancel", "submit"]
    data_model = events[0]["data"]["createSurface"]["dataModel"]
    assert data_model["vehicles"][0]["usage_type"] == "pleasure"
    assert api.state()["surfaces"][0]["status"] == "open"

    rows = len(api.rows("state_changes"))
    events = api.submit("vehicule_0", {"/vehicles/0/vehicle_make": "Honda", "/vehicles/0/usage_type": "commute"})
    assert "turn.started" not in types(events) and len(api.agent.invocations) == calls
    assert events[0]["data"]["deleteSurface"]["surfaceId"] == "vehicule_0"
    assert [c["path"] for c in find(events, "state.delta")[0]["data"]["changes"]] == ["/vehicles/0/usage_type"]
    new_rows = api.rows("state_changes")[rows:]
    assert [(r["path"], r["source"], r["surface_id"]) for r in new_rows] == [
        ("/vehicles/0/usage_type", "form", "vehicule_0")]
    assert find(events, "text.done")[0]["actor"] == "bff"
    assert api.state()["surfaces"][0]["status"] == "submitted"

    # the next invocation carries the system note, which the BFF clears once the turn has started
    api.say("Merci")
    notes = api.agent.invocations[-1]["body"]["system_notes"]
    assert notes == ["Form 'vehicule_0' edited outside the chat: /vehicles/0/usage_type"]
    assert api.doc()["system_notes"] == []


def test_cancel_in_edit_mode_restores_the_chip(api: Api) -> None:
    _vehicle_form_submitted(api)
    api.send("ui.surface_reopen", surface_id="vehicule_0")
    events = api.submit("vehicule_0", {}, name="cancel")
    assert events[0]["data"]["deleteSurface"]["surfaceId"] == "vehicule_0"
    assert api.state()["surfaces"][0]["status"] == "submitted"


def test_an_open_chip_cannot_be_reopened(api: Api) -> None:
    _vehicle_form_submitted(api)
    api.send("ui.surface_reopen", surface_id="vehicule_0")
    assert find(api.send("ui.surface_reopen", surface_id="vehicule_0"), "error")[0]["data"]["code"] == \
        "surface_not_reopenable"


def test_full_form_needs_the_confirmed_consent_first(api: Api) -> None:
    api.start()
    calls = len(api.bff.a2ui.calls)
    events = api.send("ui.surface_reopen", surface_id="full_form")
    assert types(events) == ["text.done", "suggestions"] and events[0]["actor"] == "bff"
    assert events[0]["data"]["text"].startswith("Avant d’afficher le formulaire complet")
    assert [i["label"] for i in events[1]["data"]["items"]] == ["Oui, j’accepte", "Pourquoi en avez-vous besoin?"]
    assert len(api.bff.a2ui.calls) == calls and api.state()["surfaces"] == []  # nothing rendered
    api.consent()
    assert find(api.send("ui.surface_reopen", surface_id="full_form"), "a2ui.message")


def test_full_form_shows_everything_known_and_a_save_commits_only_the_change(api: Api) -> None:
    api.start()
    api.consent()
    api.agent.script.append(chat_turn(("/quote/first_name", "Marie"), ("/vehicles/0/vehicle_make", "Toyota"),
                                      ("/drivers/0/license_year", 2010)))
    api.say("Marie, Toyota, permis en 2010")
    calls = len(api.agent.invocations)
    events = api.send("ui.surface_reopen", surface_id="full_form")
    surface = events[0]["data"]["createSurface"]
    assert surface["surfaceId"] == "full_form"
    args = api.bff.a2ui.calls[-1][1]
    assert args["mode"] == "edit" and args["state"]["quote"]["first_name"]["source"] == "chat"
    st = api.state()["state"]
    assert st["drivers"][0]["years_licensed"]["value"] == 16  # derived by the BFF (2026 - 2010)

    events = api.submit("full_form", {"/quote/first_name": "Marie", "/quote/last_name": "Tremblay",
                                      "/vehicles/0/vehicle_make": "Toyota"})
    delta = find(events, "state.delta")[0]["data"]["changes"]
    assert [c["path"] for c in delta if c["source"] == "form"] == ["/quote/last_name"]
    assert "turn.started" not in types(events) and len(api.agent.invocations) == calls
    assert api.state()["state"]["quote"]["first_name"]["source"] == "chat"  # unchanged values keep their source
    assert api.state()["state"]["drivers"][0]["driver_name"]["value"] == "Marie Tremblay"


def test_back_closes_the_full_form_without_committing(api: Api) -> None:
    api.start()
    api.consent()
    api.send("ui.surface_reopen", surface_id="full_form")
    rows = len(api.rows("state_changes"))
    events = api.submit("full_form", {"/quote/last_name": "Tremblay"}, name="back")
    assert events[0]["data"] == {"version": "1.0", "deleteSurface": {"surfaceId": "full_form"}}
    assert len(api.rows("state_changes")) == rows


def test_an_unavailable_compiler_keeps_the_chip_and_says_try_again(api: Api) -> None:
    _vehicle_form_submitted(api)

    async def down(tool: str, arguments: dict) -> dict:
        raise OSError("connection refused")

    api.bff.a2ui.call = down
    error = find(api.send("ui.surface_reopen", surface_id="vehicule_0"), "error")[0]["data"]
    assert error["code"] == "a2ui_unavailable" and error["retryable"] is True
    assert api.state()["surfaces"][0]["status"] == "submitted"
