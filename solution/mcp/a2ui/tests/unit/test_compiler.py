"""The FormSpec compiler: each widget, check, layout rule, prefill path and the edit mode. No I/O."""

import json

import pytest
from quote_common.catalog import get_catalog

from mcp_a2ui import conformance
from mcp_a2ui.compiler import compile, parse_visible_if, validate

pytestmark = pytest.mark.unit
YEAR = 2026  # the pinned clock of every compile
CAT = get_catalog()


def build(*fields, state=None, mode="new", lang="fr", **spec):
    """Compile a small FormSpec. Returns the components by id and the data model; every build is checked for conformance."""
    spec = {"form_id": "t_form", "title": "T", "layout": "single", **spec, "fields": list(fields)}
    messages = compile(spec, state, mode, lang, year=YEAR)
    assert conformance.check(messages, opens_surface=True) == []
    surface = messages[0]["createSurface"]
    return {c["id"]: c for c in surface["components"]}, surface["dataModel"]


def one(comps, component):
    """The only component of that kind (``TextField``, ``Repeater``, ...); fails when there are none or several."""
    (found,) = (c for c in comps.values() if c["component"] == component)
    return found


def rules(comp):
    """The CheckRules of a component as (function, arguments other than value, message)."""
    return [
        (r["condition"]["@call"], {k: v for k, v in r["condition"]["args"].items() if k != "value"}, r["message"])
        for r in comp["@checks"]
    ]


def AT(path):
    """A binding to a data model path: ``{"@path": path}``."""
    return {"@path": path}


def envelope(value, source="chat", **extra):
    """A confirmed field envelope as the BFF stores it; ``extra`` adds keys such as ``evidence``."""
    return {"value": value, "source": source, "status": "confirmed", **extra}


# --- the surface ---------------------------------------------------------------------------------------------------


def test_one_create_surface_with_title_intro_fields_and_action_bar():
    spec = {
        "form_id": "about-you",
        "title": "À propos",
        "intro": "Deux détails.",
        "intro_tone": "warn",
        "fields": [{"key": "first_name"}],
    }
    (message,) = compile(spec, year=YEAR)
    surface = message["createSurface"]
    comps = {c["id"]: c for c in surface["components"]}
    assert message["version"] == "1.0"
    assert surface["surfaceId"] == "about_you"  # an identifier: no hyphen
    assert surface["catalogId"] == "https://catalog.example.com/insurance-catalog/v1/catalog.json"
    assert surface["components"][0]["id"] == "root"
    assert comps["root"] == {"id": "root", "component": "Column", "children": ["title", "intro", "text_1", "actions"]}
    assert comps["title"] == {"id": "title", "component": "Text", "text": "## À propos"}
    assert comps["intro"] == {"id": "intro", "component": "InPlaceNote", "tone": "warn", "text": "Deux détails."}
    assert comps["actions"] == {"id": "actions", "component": "ActionBar", "children": ["action_submit"]}
    assert comps["action_submit"] == {
        "id": "action_submit", "component": "Button", "child": "action_submit_label", "variant": "primary",
        "action": {"event": {"name": "submit", "context": {"surfaceId": "about_you"}}},
    }  # fmt: skip
    assert comps["action_submit_label"]["text"] == "Continuer"


def test_action_styles_and_english_default():
    actions = [
        {"id": "edit", "label": "Back", "style": "secondary"},
        {"id": "skip", "label": "Skip", "style": "link"},
        {"id": "go", "label": "Go", "style": "primary"},
    ]
    comps, _ = build({"key": "first_name"}, actions=actions)
    assert [comps[f"action_{a}"].get("variant") for a in ("edit", "skip", "go")] == [None, "borderless", "primary"]
    assert build({"key": "first_name"}, lang="en")[0]["action_submit_label"]["text"] == "Continue"
    assert build({"key": "first_name"}, actions=[])[0]["actions"]["children"] == []


# --- widgets -------------------------------------------------------------------------------------------------------

YES_NO = [{"value": "true", "label": "Oui"}, {"value": "false", "label": "Non"}]
USAGE = [{"value": e[0], "label": e[1]} for e in CAT.enum_labels("usage_type", "fr")]
WIDGETS = [  # (field, component, expected props)
    ({"key": "first_name"}, "TextField", {"textFieldType": "shortText", "label": "Prénom", "required": True, "value": AT("/quote/first_name"), "provenance": AT("/_provenance/~1quote~1first_name")}),
    ({"key": "email", "hint": "Pour la soumission"}, "TextField", {"textFieldType": "email", "hint": "Pour la soumission"}),
    ({"key": "phone_number"}, "TextField", {"textFieldType": "phone"}),
    ({"key": "postal_code"}, "TextField", {"textFieldType": "postal_code"}),
    ({"key": "weeks_outside_quebec"}, "TextField", {"textFieldType": "number"}),
    ({"key": "vehicle_value"}, "TextField", {"textFieldType": "currency"}),
    ({"key": "driver_license_number", "widget": "text"}, "TextField", {"textFieldType": "licence", "deterministic": True}),
    ({"key": "/quote/handoff_summary", "widget": "textarea", "label": "Résumé"}, "TextField", {"textFieldType": "longText", "label": "Résumé"}),
    ({"key": "occupation_label", "placeholder": "Infirmière", "computed": "x = 1", "readonly": True}, "TextField", {"placeholder": "Infirmière", "computed": "x = 1", "readonly": True}),
    ({"key": "date_of_birth"}, "DateTimeInput", {"enableDate": True, "enableTime": False}),
    ({"key": "usage_type"}, "ChoicePicker", {"variant": "chips", "options": USAGE}),
    ({"key": "province_code"}, "ChoicePicker", {"variant": "select"}),
    ({"key": "gender", "widget": "select"}, "ChoicePicker", {"variant": "select"}),
    ({"key": "quote_goal", "widget": "radio"}, "ChoicePicker", {"variant": "radio"}),
    ({"key": "add_ons"}, "ChoicePicker", {"variant": "chips", "multiple": True, "maxAllowedSelections": 4}),
    ({"key": "callback_slot", "widget": "time_slots", "options": [["a", "Lundi"], ["b", "Mardi"]]}, "ChoicePicker", {"variant": "slots", "options": [{"value": "a", "label": "Lundi"}, {"value": "b", "label": "Mardi"}]}),
    ({"key": "has_claims"}, "ChoicePicker", {"variant": "segmented", "valueType": "boolean", "options": YES_NO, "deterministic": True}),
    ({"key": "contact_consent"}, "CheckBox", {"value": AT("/quote/contact_consent")}),
    ({"key": "annual_kilometers", "widget": "slider", "max": 60000}, "Slider", {"minValue": 0, "maxValue": 60000, "step": 1000, "unit": "km"}),
    ({"key": "vin"}, "VinField", {"decode": True, "value": AT("/vehicles/0/vin")}),
    ({"key": "vehicle_year", "widget": "ymm"}, "YmmPicker", {"label": "Année, marque et modèle", "required": True, "year": AT("/vehicles/0/vehicle_year"), "make": AT("/vehicles/0/vehicle_make"), "model": AT("/vehicles/0/vehicle_model"), "vehicleType": AT("/vehicles/0/vehicle_type")}),
    ({"widget": "address_lookup"}, "AddressLookup", {"label": "Adresse du domicile", "line1": AT("/quote/address_line1"), "city": AT("/quote/city"), "province": AT("/quote/province_code"), "postal": AT("/quote/postal_code")}),
    ({"widget": "assignment_board", "data": {"drivers": [{"index": 0}], "vehicles": [{"index": 0}]}}, "AssignmentBoard", {"label": "Qui conduit quoi", "value": AT("/household/driver_vehicle_assignments"), "drivers": [{"index": 0}], "vehicles": [{"index": 0}]}),
    ({"key": "driver_vehicle_assignments"}, "AssignmentBoard", {"value": AT("/household/driver_vehicle_assignments")}),  # auto, from the catalog type assignment_matrix
    ({"widget": "matrix", "data": {"rows": ["r"], "cols": ["c"]}}, "AssignmentMatrix", {"rows": ["r"], "cols": ["c"], "choices": [{"value": v, "label": label} for v, label in CAT.field("driver_vehicle_assignments")["item_role_fr"]]}),
    ({"key": "offer_type", "widget": "offer_cards", "data": {"offers": [{"offer_type": "plus"}], "recommended": "plus", "show_adjust": True}}, "OfferCards", {"value": AT("/underwriting/offer_type"), "offers": [{"offer_type": "plus"}], "recommended": "plus", "showAdjust": True}),
    ({"widget": "summary", "data": {"items": [{"label": "A", "value": "B"}]}}, "Summary", {"items": [{"label": "A", "value": "B"}]}),
    ({"widget": "file", "data": {"purpose": "registration", "fills": {"vin": "/vehicles/0/vin"}}}, "FileDrop", {"purpose": "registration", "fills": {"vin": "/vehicles/0/vin"}}),
    ({"widget": "document_drop", "data": {"documents": [{"purpose": "licence"}], "read": ["licence"]}}, "DocumentDrop", {"documents": [{"purpose": "licence"}], "read": ["licence"]}),
]  # fmt: skip


@pytest.mark.parametrize(
    ("field", "component", "expected"), WIDGETS, ids=[f"{w[1]}-{w[0].get('widget') or w[0]['key']}" for w in WIDGETS]
)
def test_widget(field, component, expected):
    comp = one(build(field)[0], component)
    assert {k: comp.get(k) for k in expected} == expected


def test_matrix_roles_in_english():
    assert [c["label"] for c in one(build({"widget": "matrix"}, lang="en")[0], "AssignmentMatrix")["choices"]] == [
        "Principal",
        "Occasional",
        "Does not drive",
    ]


def test_info_and_section_headings():
    comps, _ = build(
        {"widget": "section", "label": "Votre résumé"},
        {"widget": "info", "tone": "warn", "label": "Pourquoi", "hint": "règle"},
    )
    assert comps["section_title_1"] == {"id": "section_title_1", "component": "Text", "text": "### Votre résumé"}
    assert one(comps, "InPlaceNote") == {
        "id": "info_2",
        "component": "InPlaceNote",
        "tone": "warn",
        "text": "Pourquoi\u00a0: règle",
    }
    english, _ = build({"widget": "info", "label": "Why", "hint": "rule"}, lang="en")
    assert one(english, "InPlaceNote") == {
        "id": "info_1",
        "component": "InPlaceNote",
        "tone": "info",
        "text": "Why: rule",
    }


def test_help_and_deterministic_come_from_the_catalog_and_required_false_is_dropped():
    comps, _ = build({"key": "driver_license_number"}, {"key": "email", "required": False})
    assert (
        comps["text_1"]["help"] == CAT.help("driver_license_number", "fr") and comps["text_1"]["deterministic"] is True
    )
    assert "required" not in comps["text_1"] and "required" not in comps["email_2"]
    assert build({"key": "email", "help": "Aide"})[0]["email_1"]["help"] == "Aide"


def test_field_actions_and_visible_if():
    action = {"id": "upload_vin", "label": "Photo du NIV", "kind": "upload_photo"}
    comps, _ = build(
        {"key": "vin", "field_actions": [action]},
        {"key": "weeks_outside_quebec", "visible_if": "outside_quebec == true"},
    )
    assert comps["vin_1"]["fieldActions"] == [action]
    assert comps["number_2"]["visibleIf"] == {
        "@call": "equals",
        "args": {"left": AT("/vehicles/0/outside_quebec"), "right": True},
    }


# --- repeater ------------------------------------------------------------------------------------------------------


def test_repeater_template_has_relative_paths_and_no_auto_required():
    repeat = {
        "count": 2,
        "start": 1,
        "min": 0,
        "max": 6,
        "item_label": "Conducteur {n}",
        "add_label": "Ajouter un conducteur",
        "fields": [
            {"key": "driver_name"},
            {"key": "relationship_code", "widget": "select"},
            {"key": "age_obtained_license"},
        ],
    }
    comps, data = build({"key": "/drivers", "widget": "repeater", "label": "Autres", "repeat": repeat})
    rep = one(comps, "Repeater")
    assert (rep["collection"], rep["start"], rep["count"], rep["min"], rep["max"], rep["layout"]) == (
        "/drivers",
        1,
        2,
        0,
        6,
        "grid",
    )
    assert (rep["itemLabel"], rep["addLabel"]) == ("Conducteur {n}", "Ajouter un conducteur")
    assert [comps[t]["value"] for t in rep["template"]] == [
        AT("driver_name"),
        AT("relationship_code"),
        AT("age_obtained_license"),
    ]
    assert not any(
        "provenance" in comps[t] or "required" in comps[t] for t in rep["template"]
    )  # age_obtained_license is required_for_quote
    assert data == {}


def test_repeater_defaults_follow_the_language_and_one_vin_field_is_a_list():
    field = {
        "key": "/vehicles",
        "widget": "repeater",
        "repeat": {"count": 10, "fields": [{"key": "vin", "widget": "vin"}]},
    }
    fr, en = one(build(field)[0], "Repeater"), one(build(field, lang="en")[0], "Repeater")
    assert (fr["itemLabel"], fr["addLabel"], fr["layout"], fr["count"], fr["max"]) == (
        "Élément {n}",
        "Ajouter",
        "list",
        10,
        25,
    )
    assert (en["itemLabel"], en["addLabel"]) == ("Item {n}", "Add another")


def test_repeater_without_a_pointer_key_uses_the_drivers_collection():
    assert (
        one(build({"widget": "repeater", "repeat": {"fields": [{"key": "driver_name"}]}})[0], "Repeater")["collection"]
        == "/drivers"
    )


# --- checks --------------------------------------------------------------------------------------------------------


def test_required_email_regex_and_vin_rules_in_french():
    comps, _ = build(
        {"key": "email", "required": True}, {"key": "phone_number"}, {"key": "postal_code"}, {"key": "vin"}
    )
    assert rules(comps["email_1"]) == [
        ("required", {}, "Ce champ est obligatoire"),
        ("email", {}, "Entrez une adresse courriel valide"),
    ]
    assert rules(comps["phone_2"])[0][0::2] == ("regex", "Entrez un numéro à 10 chiffres")
    assert rules(comps["postal_code_3"])[-1][2] == "Entrez un code postal canadien, comme H2X 1Y4"
    assert rules(comps["vin_4"]) == [
        ("vinCheckDigit", {}, "Ce NIV n’est pas valide (chiffre de contrôle)")
    ]  # the pattern is not repeated as a regex


def test_required_email_regex_and_vin_rules_in_english():
    comps, _ = build({"key": "email", "required": True}, {"key": "phone_number"}, {"key": "vin"}, lang="en")
    assert [r[2] for r in rules(comps["email_1"])] == ["This field is required", "Enter a valid email address"]
    assert rules(comps["phone_2"])[0][2] == "Enter a 10-digit phone number"
    assert rules(comps["vin_3"])[0][2] == "This VIN is not valid (check digit)"


def test_regex_carries_the_catalog_pattern():
    ((call, args, message),) = rules(build({"key": "driver_license_number"})[0]["text_1"])
    assert (call, args["pattern"], message) == (
        "regex",
        CAT.field("driver_license_number")["pattern"],
        "Format du permis du Québec : A1234-123456-12",
    )


def test_numeric_range_uses_the_year_for_offsets_and_either_bound():
    comps, _ = build({"key": "vehicle_year"}, {"key": "weeks_outside_quebec"})
    assert rules(comps["number_1"]) == [
        ("required", {}, "Ce champ est obligatoire"),
        ("numericRange", {"min": 1950, "max": YEAR + 1}, f"Entrez une valeur de 1950 à {YEAR + 1}"),
    ]
    assert rules(comps["number_2"]) == [("numericRange", {"min": 0, "max": 52}, "Entrez une valeur de 0 à 52")]
    assert rules(build({"key": "weeks_outside_quebec"}, lang="en")[0]["number_1"])[0][2] == "Enter a value from 0 to 52"


def test_age_and_date_window_rules():
    comps, _ = build({"key": "date_of_birth"}, {"key": "effective_date"})
    assert rules(comps["date_1"])[1:] == [("ageBetween", {"min": 16, "max": 110}, "Vous devez avoir 16 ans ou plus")]
    assert rules(comps["date_2"])[1:] == [
        ("dateWithin", {"from_days": 0, "to_days": 90}, "Choisissez une date dans les 90 prochains jours")
    ]
    english, _ = build({"key": "date_of_birth"}, {"key": "effective_date"}, lang="en")
    assert (rules(english["date_1"])[1][2], rules(english["date_2"])[1][2]) == (
        "Must be 16 or older",
        "Pick a date in the next 90 days",
    )


def test_ymm_and_assignment_rules_replace_the_key_rules():
    comps, _ = build(
        {"key": "/vehicles/1/vehicle_year", "widget": "ymm", "required": False}, {"widget": "assignment_board"}
    )
    ymm = one(comps, "YmmPicker")
    expected = (
        "ymmValid",
        {"make": AT("/vehicles/1/vehicle_make"), "model": AT("/vehicles/1/vehicle_model")},
        "Choisissez une année, une marque et un modèle valides",
    )
    assert rules(ymm) == [expected] and ymm["required"] is False
    assert ymm["@checks"][0]["condition"]["args"]["value"] == AT("/vehicles/1/vehicle_year")
    assert rules(one(comps, "AssignmentBoard")) == [
        ("onePrincipalPerVehicle", {}, "Chaque véhicule a besoin d’un conducteur principal")
    ]


# --- visible_if ----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("expr", "right"),
    [
        ("a == true", True),
        ("a == false", False),
        ("a == TRUE", True),
        ("a == 3", 3),
        ("a == 'x y'", "x y"),
        ('a == "z"', "z"),
    ],
)
def test_parse_visible_if(expr, right):
    assert parse_visible_if(expr, "/vehicles/0") == {
        "@call": "equals",
        "args": {"left": AT("/vehicles/0/a"), "right": right},
    }


def test_parse_visible_if_pointer_and_unsupported_expressions():
    assert parse_visible_if("/quote/x == true", "/vehicles/0")["args"]["left"] == AT("/quote/x")
    assert parse_visible_if("age > 3", "/x") is None and parse_visible_if("", "/x") is None


# --- layout --------------------------------------------------------------------------------------------------------


def top_level(*fields, **spec):
    """The root's children, with each row replaced by the list of its children."""
    comps, _ = build(*fields, **spec)
    return [comps[c]["children"] if c.startswith("row_") else c for c in comps["root"]["children"]]


def test_two_column_packs_narrow_fields_in_pairs_and_leaves_a_lone_one_unwrapped():
    narrow = [{"key": "first_name"}, {"key": "last_name"}, {"key": "email"}]
    assert top_level(*narrow, layout="two_column") == [
        "title",
        ["text_1", "text_2"],
        "email_4",
        "actions",
    ]  # row_3 takes the id after its two fields
    assert top_level(*narrow, layout="single") == ["title", "text_1", "text_2", "email_3", "actions"]


def test_wide_fields_and_sections_take_a_full_row_and_flush_the_pending_one():
    fields = [
        {"key": "first_name"},
        {"key": "usage_type"},
        {"key": "last_name"},
        {"widget": "section", "label": "S"},
        {"key": "email"},
        {"key": "phone_number"},
    ]
    assert top_level(*fields, layout="two_column") == [
        "title",
        "text_1",
        "chips_2",
        "text_3",
        "section_title_4",
        ["email_5", "phone_6"],
        "actions",
    ]


def test_an_auto_select_with_many_options_is_wide_but_an_explicit_select_is_narrow():
    assert top_level({"key": "province_code"}, {"key": "city"}, layout="two_column") == [
        "title",
        "select_1",
        "text_2",
        "actions",
    ]
    assert top_level({"key": "province_code", "widget": "select"}, {"key": "city"}, layout="two_column") == [
        "title",
        ["select_1", "text_2"],
        "actions",
    ]


# --- prefill from the FormSpec -------------------------------------------------------------------------------------


def test_explicit_prefill_value_and_provenance():
    _, data = build(
        {"key": "first_name", "prefill": {"value": "Marie", "source": "chat", "note": "« Marie Tremblay »"}},
        {"key": "last_name", "prefill": {"value": "Tremblay"}},
        {"key": "email", "prefill": {"note": "vu dans le courriel"}},
    )  # fmt: skip
    assert data == {
        "quote": {"first_name": "Marie", "last_name": "Tremblay"},
        "_provenance": {"/quote/first_name": {"source": "chat", "note": "« Marie Tremblay »"}, "/quote/email": {"source": "chat", "note": "vu dans le courriel"}},
    }  # fmt: skip


def test_explicit_prefill_of_composite_widgets():
    board = [{"driver_index": 0, "vehicle_index": 0, "role": "principal"}]
    repeat = {
        "start": 1,
        "fields": [{"key": "driver_name"}],
        "prefill": [{"driver_name": "Alex"}, {"driver_name": "Léo", "relationship_code": "child"}],
    }
    _, data = build(
        {"widget": "ymm", "key": "vehicle_year", "prefill": {"value": {"vehicle_year": 2021, "vehicle_make": "Toyota"}, "source": "chat"}},
        {"widget": "address_lookup", "prefill": {"value": {"city": "Laval", "postal_code": "H7N 1A1"}}},
        {"key": "driver_vehicle_assignments", "widget": "assignment_board", "prefill": {"value": board}},
        {"key": "offer_type", "widget": "offer_cards", "prefill": {"value": "plus"}},
        {"widget": "document_drop", "data": {"documents": [], "values": {"/quote/first_name": "Marie"}}},
        {"key": "/drivers", "widget": "repeater", "repeat": repeat},
    )  # fmt: skip
    assert data["vehicles"] == [{"vehicle_year": 2021, "vehicle_make": "Toyota"}]
    assert data["quote"] == {"city": "Laval", "postal_code": "H7N 1A1", "first_name": "Marie"}
    assert data["household"] == {"driver_vehicle_assignments": board} and data["underwriting"] == {"offer_type": "plus"}
    assert data["drivers"] == [None, {"driver_name": "Alex"}, {"driver_name": "Léo", "relationship_code": "child"}]
    assert data["_provenance"] == {"/vehicles/0/vehicle_year": {"source": "chat"}}


def test_explicit_prefill_keeps_falsy_values():
    # The JavaScript compiler skips a falsy prefill value on offer_cards, matrix and assignment_board; this one does not.
    _, data = build(
        {"key": "offer_type", "widget": "offer_cards", "prefill": {"value": ""}},
        {"key": "has_claims", "prefill": {"value": False}},
    )
    assert data == {"underwriting": {"offer_type": "", "has_claims": False}}


# --- prefill from the committed state ------------------------------------------------------------------------------

STATE = {
    "quote": {
        "contact_consent": envelope(True),  # FR-INT-2: personal data is shown only after consent
        "first_name": envelope("Marie", evidence="Je m’appelle Marie"),
        "last_name": envelope("Tremblay", "form"),
        "date_of_birth": envelope("1986-03-14", "document"),
        "email": envelope("marie@example.com", "system"),
        "phone_number": envelope(None),
        "address_line1": envelope("125 Rue des Érables", "lookup"),
        "city": envelope("Laval", "lookup"),
        "province_code": envelope("QC", "lookup"),
    },
    "vehicles": [
        {"vehicle_year": envelope(2021), "vehicle_make": envelope("Toyota"), "vehicle_model": envelope("RAV4"), "vin": envelope("2T3W1RFV0LW000001", "document")},
        {"vehicle_make": envelope("Honda")},
    ],
    "drivers": [{}, {"driver_name": envelope("Alex Tremblay"), "relationship_code": envelope("spouse")}, {"driver_name": envelope("Léo Tremblay")}],
    "underwriting": {
        "has_claims": envelope(True, status="pending_confirmation"),
        "claims_detail": envelope([{"date": "2023-06-12", "type": "collision", "amount": 2400, "at_fault": False}]),
    },
    "household": {"driver_vehicle_assignments": envelope([{"driver_index": 0, "vehicle_index": 0, "role": "principal"}], "form")},
    "surfaces": {"vehicle_0": {"form_spec": {"fields": [{"key": "nope"}]}}},  # outside the five entities: ignored
}  # fmt: skip


def test_personal_data_fields_wait_for_confirmed_consent_except_on_the_end_of_form():
    fields = [{"key": "first_name"}, {"key": "email"}, {"key": "/drivers", "widget": "repeater", "repeat": {"count": 1, "fields": [
        {"key": "driver_name"}, {"key": "driver_license_number"}]}}]  # fmt: skip
    for consent in (None, envelope(True, status="pending_confirmation"), envelope(False)):
        state = {"quote": {"contact_consent": consent} if consent else {}}
        paths = {p for m in compile(spec_with(*fields), state) for p in _bound_paths(m)}
        assert "/quote/first_name" in paths and "/quote/email" not in paths
        assert not any(p.endswith("driver_license_number") for p in paths)
    consented = {"quote": {"contact_consent": envelope(True)}}
    paths = {p for m in compile(spec_with(*fields), consented) for p in _bound_paths(m)}
    assert "/quote/email" in paths and any(p.endswith("driver_license_number") for p in paths)
    end = spec_with({"key": "phone_number"}, form_id="end_of_form")
    assert "/quote/phone_number" in {p for m in compile(end, {"quote": {}}) for p in _bound_paths(m)}


def _bound_paths(node):
    """Every ``@path`` binding found anywhere inside a message."""
    if isinstance(node, dict):
        if isinstance(node.get("@path"), str):
            yield node["@path"]
        for v in node.values():
            yield from _bound_paths(v)
    elif isinstance(node, list):
        for v in node:
            yield from _bound_paths(v)


def test_state_fills_bound_paths_and_adds_provenance_for_chat_document_and_lookup_only():
    _, data = build(
        *({"key": k} for k in ("first_name", "last_name", "date_of_birth", "email", "phone_number")), state=STATE
    )
    assert data["quote"] == {
        "first_name": "Marie",
        "last_name": "Tremblay",
        "date_of_birth": "1986-03-14",
        "email": "marie@example.com",
    }  # a cleared value is not filled
    assert data["_provenance"] == {
        "/quote/first_name": {"source": "chat", "note": "Je m’appelle Marie"},
        "/quote/date_of_birth": {"source": "document"},
    }  # none for form and system


def test_a_state_value_without_an_envelope_is_filled_without_provenance():
    assert build({"key": "first_name"}, state={"quote": {"first_name": "Marie"}})[1] == {
        "quote": {"first_name": "Marie"}
    }


def test_a_pending_confirmation_value_still_prefills():
    _, data = build({"key": "has_claims"}, state=STATE)
    assert data["underwriting"]["has_claims"] is True and data["_provenance"]["/underwriting/has_claims"] == {
        "source": "chat"
    }


def test_explicit_prefill_wins_over_the_state_but_other_paths_still_fill():
    _, data = build(
        {"key": "first_name", "prefill": {"value": "Marion"}},
        {"widget": "ymm", "key": "vehicle_year", "prefill": {"value": {"vehicle_make": "Honda"}}},
        state=STATE,
    )
    assert data["quote"] == {"first_name": "Marion"}
    assert data["vehicles"] == [{"vehicle_make": "Honda", "vehicle_year": 2021, "vehicle_model": "RAV4"}]


def test_state_fills_each_ymm_path_and_the_entity_index_selects_the_vehicle():
    _, first = build({"widget": "ymm", "key": "vehicle_year"}, entity={"type": "vehicle", "index": 0}, state=STATE)
    assert first["vehicles"] == [{"vehicle_year": 2021, "vehicle_make": "Toyota", "vehicle_model": "RAV4"}]
    assert first["_provenance"] == {
        f"/vehicles/0/{k}": {"source": "chat"} for k in ("vehicle_year", "vehicle_make", "vehicle_model")
    }
    _, second = build({"widget": "ymm", "key": "vehicle_year"}, entity={"type": "vehicle", "index": 1}, state=STATE)
    assert second["vehicles"] == [None, {"vehicle_make": "Honda"}]


def test_state_fills_the_address_the_vin_and_the_assignment_board():
    _, data = build(
        {"widget": "address_lookup"},
        {"key": "vin"},
        {"key": "driver_vehicle_assignments", "widget": "assignment_board"},
        state=STATE,
    )
    assert data["quote"] == {"address_line1": "125 Rue des Érables", "city": "Laval", "province_code": "QC"}
    assert data["vehicles"] == [{"vin": "2T3W1RFV0LW000001"}]
    assert data["household"]["driver_vehicle_assignments"] == [
        {"driver_index": 0, "vehicle_index": 0, "role": "principal"}
    ]
    assert (
        data["_provenance"]["/vehicles/0/vin"] == {"source": "document"}
        and "/household/driver_vehicle_assignments" not in data["_provenance"]
    )


def test_state_fills_repeater_rows_and_grows_the_row_count_to_hold_them():
    drivers = {"key": "/drivers", "widget": "repeater"}
    repeat = {
        "count": 1,
        "start": 1,
        "max": 6,
        "fields": [{"key": "driver_name"}, {"key": "relationship_code", "widget": "select"}],
    }
    comps, data = build({**drivers, "repeat": repeat}, state=STATE)
    assert one(comps, "Repeater")["count"] == 2
    assert data["drivers"] == [
        None,
        {"driver_name": "Alex Tremblay", "relationship_code": "spouse"},
        {"driver_name": "Léo Tremblay"},
    ]
    assert data["_provenance"]["/drivers/2/driver_name"] == {"source": "chat"}
    capped = build({**drivers, "repeat": {**repeat, "max": 1}}, state=STATE)[0]
    without_state = build({**drivers, "repeat": repeat})[0]
    assert (one(capped, "Repeater")["count"], one(without_state, "Repeater")["count"]) == (
        1,
        1,
    )  # capped by max; no state, no change


def test_state_fills_the_vehicle_rows_of_a_fleet_form():
    vins = [f"2T3W1RFV0LW00000{i}" for i in range(4)]
    state = {"vehicles": [{"vin": envelope(v, "document" if i < 2 else "form")} for i, v in enumerate(vins)]}
    field = {
        "key": "/vehicles",
        "widget": "repeater",
        "repeat": {"count": 1, "fields": [{"key": "vin", "widget": "vin"}]},
    }
    comps, data = build(field, state=state)
    assert one(comps, "Repeater")["count"] == 4
    assert [v["vin"] for v in data["vehicles"]] == vins
    assert sorted(data["_provenance"]) == [
        "/vehicles/0/vin",
        "/vehicles/1/vin",
    ]  # read from a document; the typed ones have no badge


def test_state_fills_the_items_of_a_list_field():
    fields = [
        {"key": "date", "widget": "date"},
        {"key": "type", "widget": "select", "options": [["collision", "Collision"]]},
        {"key": "amount", "widget": "currency"},
        {"key": "at_fault", "widget": "toggle"},
    ]
    _, data = build(
        {"key": "/underwriting/claims_detail", "widget": "repeater", "repeat": {"fields": fields}}, state=STATE
    )
    assert data["underwriting"]["claims_detail"] == [
        {"date": "2023-06-12", "type": "collision", "amount": 2400, "at_fault": False}
    ]
    assert data["_provenance"]["/underwriting/claims_detail/0/amount"] == {"source": "chat"}


def test_state_fills_the_fills_paths_of_a_document_drop_unless_the_spec_gives_values():
    fills = {"first_name": "/quote/first_name", "date_of_birth": "/quote/date_of_birth", "vin": "/vehicles/0/vin"}
    field = {"widget": "document_drop", "data": {"documents": [{"purpose": "licence", "fills": fills}]}}
    _, data = build(field, state=STATE)
    assert data["quote"] == {"first_name": "Marie", "date_of_birth": "1986-03-14"}
    assert data["vehicles"] == [{"vin": "2T3W1RFV0LW000001"}]
    assert data["_provenance"] == {
        "/quote/first_name": {"source": "chat", "note": "Je m’appelle Marie"},
        "/quote/date_of_birth": {"source": "document"},
        "/vehicles/0/vin": {"source": "document"},
    }  # fmt: skip
    given = {"widget": "document_drop", "data": {**field["data"], "values": {"/quote/first_name": "Marion"}}}
    assert build(given, state=STATE)[1]["quote"]["first_name"] == "Marion"


def test_an_empty_state_changes_nothing():
    spec = {
        "form_id": "t_form",
        "title": "T",
        "fields": [{"key": "first_name"}, {"widget": "ymm", "key": "vehicle_year"}],
    }
    assert (
        compile(spec, None, year=YEAR)
        == compile(spec, {}, year=YEAR)
        == compile(spec, {"quote": None, "vehicles": []}, year=YEAR)
    )


# --- edit mode -----------------------------------------------------------------------------------------------------


def test_edit_mode_keeps_the_specs_actions_and_relabels_only_the_primary_one():
    actions = [
        {"id": "cancel", "label": "Annuler", "style": "secondary"},
        {"id": "submit", "label": "Continuer", "style": "primary"},
    ]
    new, _ = build({"key": "first_name"}, actions=actions)
    edit, _ = build({"key": "first_name"}, actions=actions, mode="edit")
    assert edit["actions"]["children"] == new["actions"]["children"] == ["action_cancel", "action_submit"]
    assert (new["action_submit_label"]["text"], edit["action_submit_label"]["text"]) == ("Continuer", "Enregistrer")
    assert edit["action_cancel_label"]["text"] == "Annuler" and edit["action_cancel"].get("variant") is None


def test_edit_mode_in_english_on_the_default_action_and_on_a_spec_without_a_primary_action():
    assert (
        build({"key": "first_name"}, mode="edit")[0]["action_submit_label"]["text"] == "Enregistrer"
    )  # the default action is primary
    assert build({"key": "first_name"}, mode="edit", lang="en")[0]["action_submit_label"]["text"] == "Save"
    plain = build({"key": "first_name"}, mode="edit", actions=[{"id": "go", "label": "Go"}])[0]
    assert plain["action_go_label"]["text"] == "Go"


# --- every catalog key (FR-A2UI-2) ---------------------------------------------------------------------------------


@pytest.mark.parametrize("entity", ["quote", "vehicle", "driver", "underwriting", "household"])
def test_one_form_holds_every_key_of_an_entity(entity):
    keys = [f["key"] for f in CAT.fields.values() if f["entity"] == entity]
    comps, _ = build(*({"key": k} for k in keys), entity={"type": entity})
    bound = json.dumps(list(comps.values()), ensure_ascii=False)
    assert [k for k in keys if f'"@path": "{CAT.resolve_path(k, {"type": entity})}"' not in bound] == []


# --- validation ----------------------------------------------------------------------------------------------------


def test_a_valid_spec_has_no_problems():
    assert (
        validate(
            {
                "form_id": "t_form",
                "title": "T",
                "intro_tone": "warn",
                "fields": [{"key": "first_name"}, {"widget": "section", "label": "S"}],
            }
        )
        == []
    )


def spec_with(*fields, **spec):
    """A small FormSpec with these ``fields``; ``spec`` overrides its form id, title and other keys."""
    return {"form_id": "t_form", "title": "T", "fields": list(fields), **spec}


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"form_id": "t_form", "fields": [{"key": "first_name"}]}, "$: 'title' is a required property"),
        (spec_with({"key": "first_name"}, form_id="Bad-Id"), "form_id: 'Bad-Id' does not match"),
        (spec_with(), "fields: [] should be non-empty"),
        (spec_with({"key": "first_name", "widget": "carousel"}), "fields[0].widget: 'carousel' is not one of"),
        (
            spec_with({"key": "first_name", "colour": "red"}),
            "fields[0]: Additional properties are not allowed ('colour' was unexpected)",
        ),
        (spec_with({"key": "vehicle_yr"}), "fields[0].key: unknown catalog key 'vehicle_yr'"),
        (spec_with({"key": "/cars/0/x"}), "fields[0].key: '/cars/0/x' must start with /quote"),
        (spec_with({"key": "first_name", "visible_if": "age > 3"}), "fields[0].visible_if: 'age > 3' is not supported"),
        (
            spec_with({"key": "/drivers", "widget": "repeater", "repeat": {"fields": [{"key": "nope"}]}}),
            "fields[0].repeat.fields[0].key: unknown catalog key 'nope'",
        ),
        (
            spec_with({"key": "first_name"}, actions=[{"id": "get-quote", "label": "Go"}]),
            "actions[0].id: 'get-quote' does not match",
        ),
    ],
)
def test_problems_name_the_bad_field(spec, message):
    assert any(message in problem for problem in validate(spec)), validate(spec)


def test_item_keys_of_a_list_field_are_accepted_and_problems_are_capped():
    repeat = {"fields": [{"key": "date", "widget": "date"}, {"key": "amount", "widget": "currency"}]}
    assert validate(spec_with({"key": "/underwriting/claims_detail", "widget": "repeater", "repeat": repeat})) == []
    assert len(validate(spec_with(*[{"widget": "nope"}] * 40))) == 10
