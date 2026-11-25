"""Unit: the D15 suggestion filter, the BFF FormSpecs (full_form, end_of_form, edit mode) and surface bindings."""

from __future__ import annotations

from typing import Any

import pytest
from jsonschema import Draft202012Validator
from quote_common.contracts import load_schema

from quote_bff import forms, state
from quote_bff.state import Change
from quote_bff.suggestions import filter_suggestions


def session_with(*changes: tuple[str, Any], source: str = "form") -> dict[str, Any]:
    """A session document with the given ``(path, value)`` changes committed from ``source``.

    The source is a form by default.
    """
    d = state.new_session("s_1", "AUTO-1", "fr-CA")
    state.merge(d, [Change(p, v) for p, v in changes], source, 1)
    return d


def labels(items: list[dict[str, Any]]) -> list[str]:
    """The labels of a list of suggestion items."""
    return [i["label"] for i in items]


# -- D15 ----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("label", "reason"), [
    ("Oui, c'est marie@example.com", "pii_or_value"),
    ("Appelez le 514 555-0142", "pii_or_value"),
    ("Mon code postal H2X 1Y4", "pii_or_value"),
    ("VIN 2T3P1RFV8MC123456", "pii_or_value"),
    ("Permis T6512-180187-08", "pii_or_value"),
    ("Mon auto de 2019", "pii_or_value"),
    ("C’est bien une Toyota", "state_or_catalog_value"),
    ("Oui, avec Alex", "proper_name"),
    ("Yes, with Jordan", "proper_name"),
    ("x" * 41, "too_long"),
    ("   ", "empty"),
])
def test_bad_labels_are_dropped_with_a_reason(label: str, reason: str) -> None:
    kept, dropped = filter_suggestions([{"label": label, "kind": "reply"}], session_with())
    assert kept == [] and dropped[0]["reason"] == reason


@pytest.mark.parametrize("label", ["Obtenir une soumission auto", "Oui, j’accepte", "Parler à un conseiller",
                                   "Je préfère un formulaire", "Pourquoi? Je ne sais pas", "Seulement moi",
                                   "Desjardins Assurances, c’est quoi?", "Get a car quote"])
def test_generic_labels_are_kept(label: str) -> None:
    kept, dropped = filter_suggestions([{"label": label, "kind": "reply"}], session_with())
    assert labels(kept) == [label] and dropped == []


def test_values_from_the_session_are_dropped_but_enum_codes_are_not() -> None:
    d = session_with(("/quote/first_name", "Marie"), ("/underwriting/offer_type", "plus"))
    kept, dropped = filter_suggestions(["Merci marie", "Ajouter plus de protections"], d)
    assert labels(kept) == ["Ajouter plus de protections"] and dropped[0]["reason"] == "state_or_catalog_value"


def test_at_most_four_items_and_strings_become_replies() -> None:
    kept, dropped = filter_suggestions(["Un", "Deux", "Trois", "Quatre", "Cinq"], session_with())
    assert labels(kept) == ["Un", "Deux", "Trois", "Quatre"] and dropped[0]["reason"] == "over_limit"
    assert kept[0] == {"label": "Un", "kind": "reply"}


def test_an_all_invalid_list_keeps_nothing() -> None:
    assert filter_suggestions([{"label": "a@b.ca"}, {"label": "y" * 50}], session_with())[0] == []


# -- FormSpecs ------------------------------------------------------------------------------------------------------


def form_spec_validator() -> Draft202012Validator:
    """A validator for FormSpecs, from form-spec.schema.json."""
    return Draft202012Validator(load_schema("form-spec.schema.json"))


def test_full_form_is_built_from_the_catalog() -> None:
    d = session_with(("/quote/first_name", "Marie"), ("/vehicles/1/vehicle_make", "Honda"),
                     ("/drivers/0/license_year", 2010))
    spec = forms.full_form_spec(d)
    assert list(form_spec_validator().iter_errors(spec)) == []
    assert spec["form_id"] == "full_form" and len(spec["fields"]) <= 40
    sections = [f["label"] for f in spec["fields"] if f.get("widget") == "section"]
    assert sections == ["Vous", "Véhicules", "Conducteurs", "Antécédents et options", "Qui conduit quoi"]
    keys = [f.get("key") for f in spec["fields"]]
    assert "/underwriting/claim_question_asked" not in keys and "/quote/handoff_consent" not in keys  # system flags
    vehicles = next(f for f in spec["fields"] if f.get("key") == "/vehicles")
    assert vehicles["widget"] == "repeater" and vehicles["repeat"]["count"] == 2
    drivers = next(f for f in spec["fields"] if f.get("key") == "/drivers")
    readonly = {f["key"] for f in drivers["repeat"]["fields"] if f.get("readonly")}
    assert readonly == {"years_licensed", "years_as_principal_driver"}
    board = next(f for f in spec["fields"] if f.get("widget") == "assignment_board")
    assert board["data"]["drivers"][0]["label"] == "Marie" and board["data"]["vehicles"][1]["label"] == "Honda"
    assert all(f.get("required") is False for f in spec["fields"] if f.get("key") and f.get("widget") != "repeater")
    assert next(f for f in spec["fields"] if f.get("key") == "/quote/address_line1")["widget"] == "address_lookup"


def test_end_of_form_has_the_reason_the_rule_the_summary_contact_and_consent() -> None:
    d = session_with(("/quote/first_name", "Samir"), ("/quote/last_name", "Haddad"),
                     ("/vehicles/0/vehicle_year", 2023), ("/vehicles/0/vehicle_make", "Lamborghini"))
    spec = forms.end_of_form_spec(d, "Évaluation personnalisée.", "VEHICULE_EXOTIQUE")
    assert list(form_spec_validator().iter_errors(spec)) == []
    assert spec["form_id"] == "end_of_form" and [a["id"] for a in spec["actions"]] == ["edit_answers", "send"]
    assert spec["fields"][0]["hint"] == "**Pourquoi?** Évaluation personnalisée. (règle VEHICULE_EXOTIQUE)"
    summary = next(f for f in spec["fields"] if f.get("key") == "handoff_summary")["prefill"]["value"]
    assert summary.splitlines()[:3] == ["Samir Haddad souhaite une soumission d’assurance auto.",
                                        "Véhicules : 2023 Lamborghini.", "Conducteurs : Samir Haddad (titulaire)."]
    consent = next(f for f in spec["fields"] if f.get("key") == "handoff_consent")
    assert consent == {"key": "handoff_consent", "widget": "checkbox", "required": True}
    assert "règle" not in forms.end_of_form_spec(d, "Demande du client.", None)["fields"][0]["hint"]


def test_english_sessions_get_english_templates() -> None:
    d = state.new_session("s_1", "AUTO-1", "en-CA")
    assert forms.end_of_form_spec(d, "Why.", None)["title"] == "Let’s continue with an advisor"


def test_edit_spec_drops_prefills_and_switches_to_save_and_cancel() -> None:
    d = session_with(("/vehicles/0/vehicle_make", "Honda"))
    spec = {"form_id": "documents", "title": "Docs", "fields": [
        {"key": "vehicle_make", "prefill": {"value": "Toyota", "source": "chat"}},
        {"key": "/drivers", "widget": "repeater", "repeat": {"fields": [{"key": "driver_name", "prefill": {}}]}},
        {"widget": "document_drop", "data": {"documents": [{"purpose": "registration",
                                                            "fills": {"vehicle_make": "/vehicles/0/vehicle_make"}}]}}]}
    out = forms.edit_spec(spec, d)
    assert "prefill" not in out["fields"][0] and "prefill" not in out["fields"][1]["repeat"]["fields"][0]
    assert out["fields"][2]["data"]["values"] == {"/vehicles/0/vehicle_make": "Honda"}
    assert [(a["id"], a["label"]) for a in out["actions"]] == [("cancel", "Annuler"), ("submit", "Enregistrer")]
    assert "prefill" in spec["fields"][0]  # the stored spec is not changed


def test_bound_paths_come_from_the_compiled_surface() -> None:
    message = {"version": "1.0", "createSurface": {"surfaceId": "x", "components": [
        {"id": "a", "component": "TextField", "value": {"@path": "/quote/email"},
         "provenance": {"@path": "/_provenance/~1quote~1email"}},
        {"id": "y", "component": "YmmPicker", "year": {"@path": "/vehicles/0/vehicle_year"},
         "make": {"@path": "/vehicles/0/vehicle_make"}},
        {"id": "t", "component": "TextField", "value": {"@path": "driver_name"}},
        {"id": "r", "component": "Repeater", "collection": "/drivers", "template": ["t"]},
        {"id": "d", "component": "DocumentDrop", "documents": [{"fills": {"vin": "/vehicles/0/vin"}}]}]}}
    bound = forms.bound_paths(message)
    assert bound == ["/drivers/*/driver_name", "/quote/email", "/vehicles/0/vehicle_make", "/vehicles/0/vehicle_year",
                     "/vehicles/0/vin"]
    assert forms.is_bound("/drivers/3/driver_name", bound) and not forms.is_bound("/drivers/x/driver_name", bound)
    assert not forms.is_bound("/quote/first_name", bound)


def test_values_from_context() -> None:
    assert forms.values_from_context({"values": {"/quote/email": "a", "/_errors": {}, "x": 1}}) == {"/quote/email": "a"}
    assert forms.values_from_context({"/quote/email": "a", "surfaceId": "s"}) == {"/quote/email": "a"}
    assert forms.values_from_context(None) == {}
