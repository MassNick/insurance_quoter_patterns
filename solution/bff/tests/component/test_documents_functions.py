"""Component: uploads, document reads (field-level and DocumentDrop) and renderer agent functions."""

from __future__ import annotations

from typing import Any

from quote_bff.fakes import reply
from tests.conftest import Api, find, form_event, types

# Minimal uploads: just the magic bytes the BFF sniffs, then padding.
PNG = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
PDF = b"%PDF-1.7\n" + b"\0" * 64


def upload(api: Api, purpose: str, content: bytes = PNG, content_type: str = "image/png", **form: str) -> Any:
    """POST an upload for ``purpose``; the default content is a valid PNG, and extra form fields pass through."""
    return api.client.post(f"/api/sessions/{api.sid}/documents", data={"purpose": purpose, **form},
                           files={"file": ("doc", content, content_type)})


def call(api: Api, name: str, **args: Any) -> Any:
    """POST an A2UI ``callAgentFunction`` for function ``name`` with ``args`` to ``/a2ui/functions``."""
    return api.client.post(f"/api/sessions/{api.sid}/a2ui/functions", json={"version": "1.0", "callAgentFunction": {
        "surfaceId": "vehicule_0", "functionCallId": "fc_1",
        "callFunction": {"@call": name, "catalogId": "insurance", "args": args}}})


def test_uploads_are_checked_for_purpose_type_and_size(api: Api) -> None:
    api.start()
    assert upload(api, "licence").status_code == 201
    assert upload(api, "passport").status_code == 422
    assert upload(api, "licence", b"MZ\x90\x00", "image/png").status_code == 415  # the bytes are not a PNG
    assert upload(api, "licence", PDF, "image/png").status_code == 415  # declared type differs from the content
    api.bff.settings.max_upload_bytes = 32
    assert upload(api, "licence").status_code == 413


def test_lookup_functions_call_mcp_quoting_with_the_session_ref_and_no_model(api: Api) -> None:
    api.start()
    calls = len(api.agent.invocations)
    r = call(api, "lookupMakeModelYear", year=2021, make="Toyota", model="RAV4", note="ignored")
    assert r.status_code == 200
    assert r.json() == {"version": "1.0", "agentFunctionResponse": {"functionCallId": "fc_1", "value": {
        "valid": True, "normalized": {"year": 2021, "make": "Toyota", "model": "RAV4"}, "makes": ["Honda",
        "Lamborghini", "Toyota"], "models": ["RAV4", "Corolla"]}}}
    tool, args = api.bff.quoting.calls[-1]
    assert tool == "lookup_make_model_year" and "note" not in args and "session_ref" not in args
    assert call(api, "identifyAddress", query="125 rue des Érables").json()["agentFunctionResponse"]["value"][
        "candidates"][0]["city"] == "Laval"
    assert call(api, "decodeVin", vin=" 2t3p1rfv8mc123456").json()["agentFunctionResponse"]["value"]["make"] == "Toyota"
    assert api.bff.quoting.calls[-1] == ("decode_vin", {"vin": "2T3P1RFV8MC123456"})
    assert call(api, "identifyVehicleType", make="Toyota", model="RAV4").status_code == 200
    assert call(api, "rmRf").status_code == 422
    assert len(api.agent.invocations) == calls
    logged = [(r["type"], r["actor"]) for r in api.rows("events") if r["type"].startswith("tool.")]
    assert ("tool.call", "bff") in logged and ("tool.result", "bff") in logged


def test_extract_document_returns_the_schema_valid_output_without_committing(api: Api) -> None:
    api.start()
    document_id = upload(api, "vin").json()["document_id"]
    r = call(api, "extractDocument", document_id=document_id, purpose="vin")
    value = r.json()["agentFunctionResponse"]["value"]
    assert value["purpose"] == "vin" and value["fields"]["vin"]["value"] == "2T3P1RFV8MC123456"
    assert api.rows("state_changes") == []


def test_policy_uploads_are_read_as_declarations(api: Api) -> None:
    api.start()
    document_id = upload(api, "policy", PDF, "application/pdf").json()["document_id"]
    value = call(api, "extractDocument", document_id=document_id).json()["agentFunctionResponse"]["value"]
    assert value["purpose"] == "declaration" and api.bff.documents.calls == ["declaration"]


def test_a_field_upload_commits_inferred_values_and_starts_a_document_turn(api: Api) -> None:
    api.start()
    document_id = upload(api, "vin", field_path="/vehicles/0/vin").json()["document_id"]
    events = api.send("document.uploaded", document_id=document_id, purpose="vin", field_path="/vehicles/0/vin")
    changes = {c["path"]: (c["source"], c["status"]) for c in find(events, "state.delta")[0]["data"]["changes"]}
    assert changes["/vehicles/0/vin"] == ("document", "inferred")  # FR-DOC-4: inferred until reviewed
    assert types(events)[:3] == ["tool.call", "tool.result", "state.delta"]
    body = api.agent.invocations[-1]["body"]
    assert body["input"]["kind"] == "document_extracted" and body["input"]["purpose"] == "vin"
    assert "/vehicles/0/vin" in body["input"]["changed_paths"]


def test_a_licence_upload_holds_the_licence_number_for_double_confirmation(api: Api) -> None:
    api.start()
    api.consent()  # licence number and date of birth are personal data (FR-INT-2)
    document_id = upload(api, "licence").json()["document_id"]
    events = api.send("document.uploaded", document_id=document_id, purpose="licence")
    assert find(events, "confirm.required")[0]["data"]["path"] == "/drivers/0/driver_license_number"  # FR-DOC-7


def test_documentdrop_values_go_to_the_surface_for_review_and_commit_on_submit(api: Api) -> None:
    api.start()
    documents = [{"purpose": "registration", "title": "Certificat", "fills": {
        "vehicle_make": "/vehicles/0/vehicle_make", "vehicle_model": "/vehicles/0/vehicle_model"}}]
    api.agent.script.append(reply("Vos documents.", form_event("documents", "Vos documents", [
        {"widget": "document_drop", "data": {"documents": documents}}])))
    api.say("Oui, j'ai mes documents")
    document_id = upload(api, "registration", surface_id="documents").json()["document_id"]
    events = api.send("document.uploaded", document_id=document_id, purpose="registration", surface_id="documents")
    updates = [e["data"]["updateDataModel"] for e in find(events, "a2ui.message")]
    assert {u["path"] for u in updates} >= {"/vehicles/0/vehicle_make", "/vehicles/0/vehicle_model"}
    assert api.rows("state_changes") == []  # FR-DOC-8: nothing reaches state before the submit
    calls = len(api.agent.invocations)

    reviewed = {"/vehicles/0/vehicle_make": "Toyota", "/vehicles/0/vehicle_model": "RAV4 Hybride"}
    events = api.submit("documents", reviewed)
    changes = {c["path"]: (c["source"], c["status"]) for c in find(events, "state.delta")[0]["data"]["changes"]}
    assert changes == {"/vehicles/0/vehicle_make": ("document", "confirmed"),
                       "/vehicles/0/vehicle_model": ("document", "confirmed")}
    assert len(api.agent.invocations) == calls + 1
    assert api.agent.invocations[-1]["body"]["input"]["kind"] == "form_submitted"


def test_an_unreadable_document_asks_for_another_photo(api: Api) -> None:
    api.start()
    api.bff.documents.outputs["vin"] = {"purpose": "vin", "document_ok": False, "reason": "flou", "fields": {}}
    document_id = upload(api, "vin").json()["document_id"]
    events = api.send("document.uploaded", document_id=document_id, purpose="vin")
    assert find(events, "text.done")[0]["actor"] == "bff" and "turn.started" not in types(events)


def test_an_output_that_breaks_its_schema_is_an_error(api: Api) -> None:
    api.start()
    api.bff.documents.outputs["vin"] = {"purpose": "vin", "document_ok": True, "fields": {"iban": {"value": "x"}}}
    document_id = upload(api, "vin").json()["document_id"]
    error = find(api.send("document.uploaded", document_id=document_id, purpose="vin"), "error")[0]["data"]
    assert error["code"] == "document_invalid"
