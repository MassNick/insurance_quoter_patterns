"""Session state to quote inputs: missing fields, the usable snapshot, the quote options and the input hash."""

import hashlib
import json

import pytest
from helpers import env, make_state

from mcp_quoting.state import apply_options, input_hash, missing_fields, snapshot

pytestmark = pytest.mark.unit


def paths(state):
    """The missing fields of `state` as `{path: reason}`."""
    return {m["path"]: m["reason"] for m in missing_fields(state)}


def test_a_complete_state_is_quotable():
    assert missing_fields(make_state()) == []


def test_an_empty_state_lists_every_required_field_in_catalog_order():
    missing = missing_fields({})
    assert [m["key"] for m in missing][:3] == ["first_name", "last_name", "date_of_birth"]
    assert {m["reason"] for m in missing} == {"missing"}
    assert (
        "/vehicles/0/vehicle_year" in paths({})
        and "/drivers/0/age_obtained_license" in paths({})
        and "/underwriting/has_claims" in paths({})
    )
    assert "/household/driver_vehicle_assignments" not in paths({})  # one driver on one vehicle: nothing to assign
    assert missing[0] == {"path": "/quote/first_name", "key": "first_name", "label": "Prénom", "reason": "missing"}


def chat(value, confidence, status="inferred"):
    """A field envelope from chat with the given `confidence` and `status`."""
    return env(value, status=status, source="chat", confidence=confidence)


def test_a_pending_value_never_counts():
    state = make_state(
        {
            "/vehicles/0/vehicle_year": chat(2021, 0.99, status="pending_confirmation"),
            "/underwriting/has_claims": chat(False, 1, status="pending_confirmation"),  # FR-DET-6
        }
    )
    assert paths(state) == {
        "/vehicles/0/vehicle_year": "pending_confirmation",
        "/underwriting/has_claims": "pending_confirmation",
    }


def test_a_chat_value_counts_from_confidence_0_6_on_a_non_deterministic_field():
    year = "/vehicles/0/vehicle_year"
    assert missing_fields(make_state({year: chat(2021, 0.9)})) == []
    assert missing_fields(make_state({year: chat(2021, 0.6)})) == []  # merge rule 3: below 0.6 is not usable
    assert paths(make_state({year: chat(2021, 0.59)})) == {year: "inferred"}
    assert paths(make_state({year: chat(2021, 0.5)})) == {year: "inferred"}
    assert paths(make_state({year: chat(2021, None)})) == {year: "inferred"}  # no confidence: not usable


def test_an_inferred_deterministic_field_never_counts_however_sure_the_model_is():
    state = make_state({"/underwriting/has_claims": chat(False, 0.95), "/quote/contact_consent": chat(True, 1)})
    assert paths(state) == {"/underwriting/has_claims": "inferred", "/quote/contact_consent": "inferred"}


def test_a_confirmed_value_counts_whatever_its_source():
    state = make_state(
        {
            "/underwriting/has_claims": env(False, source="chat"),
            "/vehicles/0/vehicle_year": env(2021, source="document"),
        }
    )
    assert missing_fields(state) == []  # a deterministic field confirmed by the double confirmation keeps source chat


def test_false_and_zero_are_values_but_none_and_empty_text_are_not():
    assert missing_fields(make_state({"/underwriting/has_claims": False, "/drivers/0/age_obtained_license": 0})) == []
    for empty in (None, ""):
        assert paths(make_state({"/underwriting/has_claims": empty})) == {"/underwriting/has_claims": "missing"}


def test_a_malformed_envelope_fails_closed():
    state = make_state()
    state["quote"]["email"] = "marie@example.test"  # a bare value, not an envelope
    state["quote"]["city"] = {"value": "Laval"}  # no status
    assert paths(state) == {"/quote/email": "missing", "/quote/city": "unconfirmed"}


def test_every_vehicle_and_driver_needs_its_required_fields_and_assignments():
    second_vehicle = make_state(
        {
            "/vehicles/1/vehicle_make": "Honda",
            "/vehicles/1/vehicle_model": "Civic",
            "/vehicles/1/vehicle_year": 2015,
        }
    )
    assert paths(second_vehicle) == {
        "/vehicles/1/vehicle_type": "missing",
        "/vehicles/1/annual_kilometers": "missing",
        "/vehicles/1/usage_type": "missing",
        "/household/driver_vehicle_assignments": "missing",
    }
    second_driver = make_state({"/drivers/1/driver_name": "Alex"})
    assert paths(second_driver) == {
        "/drivers/1/marital_status_code": "missing",
        "/drivers/1/age_obtained_license": "missing",
        "/household/driver_vehicle_assignments": "missing",
    }


def test_snapshot_keeps_usable_values_only_in_the_document_shape():
    state = make_state(
        {
            "/vehicles/0/vehicle_value": chat(30000, 0.5),  # too uncertain
            "/vehicles/0/modified_for_performance": chat(False, 0.9),  # usable
            "/underwriting/was_denied_or_cancelled": chat(False, 0.9),  # deterministic: must be confirmed
            "/underwriting/offer_type": chat("plus", 0.9, status="pending_confirmation"),
            "/underwriting/deductible": "1000",
            "/vehicles/1/vehicle_make": "Honda",
        }
    )
    snap = snapshot(state)
    assert list(snap) == ["quote", "vehicles", "drivers", "underwriting", "household"]
    assert snap["quote"]["first_name"] == "Marie" and snap["vehicles"][0]["vehicle_year"] == 2021
    assert "vehicle_value" not in snap["vehicles"][0] and snap["vehicles"][0]["modified_for_performance"] is False
    assert "was_denied_or_cancelled" not in snap["underwriting"] and "offer_type" not in snap["underwriting"]
    assert snap["underwriting"]["deductible"] == "1000"
    assert snap["vehicles"][1] == {"vehicle_make": "Honda"} and snap["household"] == {}
    blocked = {**state, "quote_blocked": True, "stop": {"rule_id": "X"}, "phase": "gather"}  # not part of the quote
    assert snapshot(blocked) == snap and input_hash(snapshot(blocked)) == input_hash(snap)
    assert snapshot({}) == {"quote": {}, "vehicles": [], "drivers": [], "underwriting": {}, "household": {}}


def test_apply_options_overrides_without_touching_the_original():
    snap = snapshot(make_state({"/vehicles/1/vehicle_make": "Honda", "/underwriting/deductible": "500"}))
    out = apply_options(
        snap, {"deductible": 1000, "add_ons": ["roadside"], "offer_type": "premium", "annual_kilometers": 9000}
    )
    assert out["underwriting"] == {
        **snap["underwriting"],
        "deductible": "1000",
        "add_ons": ["roadside"],
        "offer_type": "premium",
    }
    assert [v["annual_kilometers"] for v in out["vehicles"]] == [9000, 9000]  # every vehicle
    assert snap["underwriting"]["deductible"] == "500" and snap["vehicles"][0]["annual_kilometers"] == 15000
    assert apply_options(snap, {}) == snap


def test_input_hash_is_the_sha256_of_canonical_json():
    snap = snapshot(make_state())
    canonical = json.dumps(snap, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    assert input_hash(snap) == hashlib.sha256(canonical).hexdigest() and len(input_hash(snap)) == 64
    assert input_hash({"b": 1, "a": {"y": 1, "x": 2}}) == input_hash({"a": {"x": 2, "y": 1}, "b": 1})
    assert input_hash(apply_options(snap, {"deductible": 1000})) != input_hash(snap)
    assert "Érables" in canonical.decode()  # accents are hashed as UTF-8, not escaped
