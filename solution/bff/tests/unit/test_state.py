"""Unit: catalog validation, merge rules 1-5, derived fields, quote readiness and the end-of-form rule glue."""

from __future__ import annotations

from typing import Any

import pytest

from quote_bff import state
from quote_bff.state import Change, merge


def doc(*, consent: bool = False) -> dict[str, Any]:
    """A new session document; with ``consent`` the contact consent is already confirmed."""
    d = state.new_session("s_1", "AUTO-1", "fr-CA")
    if consent:
        d["quote"]["contact_consent"] = state.envelope(True, "chat", status="confirmed", confidence=1.0, turn=0)
    return d


def env(d: dict[str, Any], path: str) -> dict[str, Any]:
    """The envelope at ``path`` of the session document ``d``, or None."""
    from quote_bff.rules.end_of_form import get_envelope

    return get_envelope(d, path)


# -- rule 1: source precedence and conflicts ----------------------------------------------------------------------


def test_a_lower_source_never_overwrites_a_confirmed_value_it_records_a_conflict() -> None:
    d = doc()
    merge(d, [Change("/vehicles/0/vehicle_year", 2019)], "form", 1)
    out = merge(d, [Change("/vehicles/0/vehicle_year", 2020, evidence="c'est une 2020")], "chat", 2)
    assert out.applied == [] and env(d, "/vehicles/0/vehicle_year")["value"] == 2019
    assert out.conflicts == [{"path": "/vehicles/0/vehicle_year", "current_value": 2019, "stated_value": 2020,
                              "source": "chat", "evidence": "c'est une 2020"}]


def test_a_higher_or_equal_source_overwrites() -> None:
    d = doc()
    merge(d, [Change("/vehicles/0/vehicle_year", 2019)], "chat", 1)
    merge(d, [Change("/vehicles/0/vehicle_year", 2020)], "chat", 2)
    assert env(d, "/vehicles/0/vehicle_year")["value"] == 2020
    merge(d, [Change("/vehicles/0/vehicle_year", 2021)], "form", 3)
    assert env(d, "/vehicles/0/vehicle_year")["source"] == "form"


def test_the_same_value_from_a_firmer_source_upgrades_the_envelope() -> None:
    d = doc()
    merge(d, [Change("/quote/first_name", "Marie")], "chat", 1)
    out = merge(d, [Change("/quote/first_name", "Marie")], "form", 2)
    assert [a.path for a in out.applied] == ["/quote/first_name"]
    assert (env(d, "/quote/first_name")["source"], env(d, "/quote/first_name")["status"]) == ("form", "confirmed")
    assert merge(d, [Change("/quote/first_name", "Marie")], "chat", 3).changed is False


def test_source_statuses() -> None:
    assert state.status_for("form", 1.0) == "confirmed"
    assert state.status_for("lookup", 1.0) == "confirmed"
    assert state.status_for("chat", 0.99) == "inferred"
    assert state.status_for("document", 0.95) == "inferred"
    assert state.status_for("document", 0.95, reviewed=True) == "confirmed"


# -- rule 2: deterministic fields ----------------------------------------------------------------------------------


@pytest.mark.parametrize("path", ["/quote/contact_consent", "/underwriting/has_claims",
                                  "/underwriting/was_denied_or_cancelled", "/drivers/0/driver_license_number"])
@pytest.mark.parametrize("source", ["chat", "form", "document"])
def test_deterministic_fields_never_commit_directly(path: str, source: str) -> None:
    d = doc(consent=path != "/quote/contact_consent")
    value = "T6512-180187-08" if path.endswith("number") else True
    out = merge(d, [Change(path, value)], source, 1)
    assert out.applied == [] or all(a.new["source"] == "system" for a in out.applied)
    assert [a.path for a in out.pending] == [path]
    assert env(d, path)["status"] == "pending_confirmation"


def test_confirm_and_revert_close_a_pending_value() -> None:
    d = doc()
    out = merge(d, [Change("/underwriting/has_claims", True)], "chat", 1)
    old = out.pending[0].old
    merge(d, [Change("/underwriting/has_claims", op="confirm")], "chat", 2)
    assert env(d, "/underwriting/has_claims")["status"] == "confirmed"
    merge(d, [Change("/underwriting/has_claims", False)], "form", 3)
    merge(d, [Change("/underwriting/has_claims", op="revert", restore=None)], "form", 4)
    assert env(d, "/underwriting/has_claims")["value"] is None and old is None


# -- rule 3: confidence ---------------------------------------------------------------------------------------------


def missing(d: dict[str, Any]) -> dict[str, str]:
    """``{path: reason}`` of the paths still missing for a quote."""
    return {m["path"]: m["reason"] for m in state.missing_for_quote(d)}


def test_rule_3_a_chat_value_is_quotable_from_confidence_0_6_and_stays_inferred() -> None:
    d = doc()
    merge(d, [Change("/drivers/0/marital_status_code", "married", confidence=0.5)], "chat", 1)
    e = env(d, "/drivers/0/marital_status_code")
    assert (e["status"], e["confidence"]) == ("inferred", 0.5)
    assert missing(d)["/drivers/0/marital_status_code"] == "inferred"  # below 0.6: never priced (FR-MEM-7)
    merge(d, [Change("/drivers/0/marital_status_code", "married", confidence=0.9)], "chat", 2)
    e = env(d, "/drivers/0/marital_status_code")
    assert (e["status"], e["confidence"]) == ("inferred", 0.9)  # still inferred: FR-RUL-3 and the rules need it
    assert "/drivers/0/marital_status_code" not in missing(d)  # but usable for pricing (quote_common is_usable)


def test_an_inferred_deterministic_value_is_never_quotable() -> None:
    d = doc()
    d["underwriting"]["has_claims"] = state.envelope(False, "chat", status="inferred", confidence=0.99, turn=1)
    assert missing(d)["/underwriting/has_claims"] == "inferred"


def test_missing_fields_match_mcp_quoting() -> None:
    d = doc()
    merge(d, [Change("/underwriting/has_claims", False)], "form", 1)
    first = state.missing_for_quote(d)[0]
    assert set(first) == {"path", "key", "label", "reason"}
    assert missing(d)["/underwriting/has_claims"] == "pending_confirmation"
    assert missing(d)["/vehicles/0/vehicle_make"] == "missing"
    assert "/household/driver_vehicle_assignments" not in missing(d)  # one driver on one vehicle
    merge(d, [Change("/vehicles/1/vehicle_make", "Honda")], "form", 2)
    assert "/household/driver_vehicle_assignments" in missing(d) and "/vehicles/1/vehicle_year" in missing(d)


# -- rule 4: derived fields ----------------------------------------------------------------------------------------


def test_derived_fields_are_computed_by_the_bff_and_rejected_from_models() -> None:
    d = doc(consent=True)
    out = merge(d, [Change("/drivers/0/years_licensed", 30)], "chat", 1)
    assert out.rejected and env(d, "/drivers/0/years_licensed") is None
    merge(d, [Change("/quote/date_of_birth", "1986-03-14"), Change("/drivers/0/license_year", 2005),
              Change("/drivers/0/age_became_principal_driver", 20)], "form", 2)
    assert env(d, "/drivers/0/years_licensed")["value"] == 21  # pinned today: 2026-11-25
    assert env(d, "/drivers/0/years_as_principal_driver")["value"] == 40 - 20
    assert env(d, "/drivers/0/years_licensed")["source"] == "system"
    merge(d, [Change("/drivers/0/license_year", 2010)], "form", 3)
    assert env(d, "/drivers/0/years_licensed")["value"] == 16


def test_driver_0_mirrors_the_policyholder() -> None:
    d = doc()
    merge(d, [Change("/quote/first_name", "Marie"), Change("/quote/last_name", "Tremblay")], "form", 1)
    assert env(d, "/drivers/0/driver_name")["value"] == "Marie Tremblay"
    assert env(d, "/drivers/0/relationship_code")["value"] == "self"


# -- FR-INT-2: personal data waits for the confirmed contact consent ---------------------------------------------


def test_pii_is_withheld_until_the_contact_consent_is_confirmed() -> None:
    d = doc()
    out = merge(d, [Change("/quote/email", "marie@example.com"),
                    Change("/drivers/1/driver_date_of_birth", "2000-01-01"), Change("/quote/first_name", "Marie")],
                "chat", 1)
    assert out.withheld == ["/quote/email", "/drivers/1/driver_date_of_birth"]
    assert env(d, "/quote/email") is None and len(d["drivers"]) == 1  # no entity for a withheld value
    assert "/quote/first_name" in [a.path for a in out.applied]
    merge(d, [Change("/quote/contact_consent", True)], "chat", 2)  # pending is not enough
    assert merge(d, [Change("/quote/email", "marie@example.com")], "chat", 3).withheld == ["/quote/email"]
    merge(d, [Change("/quote/contact_consent", op="confirm")], "chat", 4)
    out = merge(d, [Change("/quote/email", "marie@example.com")], "chat", 5)
    assert out.withheld == [] and env(d, "/quote/email")["value"] == "marie@example.com"


def test_the_handoff_contact_fields_carry_their_own_consent() -> None:
    d = doc()
    out = merge(d, [Change("/quote/phone_number", "514 555-0142")], "form", 1, pii_ok=True)
    assert out.withheld == [] and env(d, "/quote/phone_number")["value"] == "514 555-0142"


# -- validation -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(("path", "value", "expected"), [
    ("/quote/postal_code", "h2x1y4", "H2X 1Y4"),
    ("/vehicles/0/vin", "2t3p1rfv8mc123456", "2T3P1RFV8MC123456"),
    ("/drivers/0/driver_license_number", "t6512-180187-08", "T6512-180187-08"),
    ("/vehicles/0/vehicle_year", "2019", 2019),
    ("/vehicles/0/outside_quebec", "false", False),
    ("/underwriting/deductible", 500, "500"),
    ("/underwriting/add_ons", ["roadside"], ["roadside"]),
    ("/quote/date_of_birth", "1986-03-14T00:00:00Z", "1986-03-14"),
    ("/quote/email", "marie@example.com", "marie@example.com"),
])
def test_values_are_normalized(path: str, value: Any, expected: Any) -> None:
    assert state.normalize(state.field_for(path)[0], value) == expected


@pytest.mark.parametrize(("path", "value"), [
    ("/quote/postal_code", "12345"), ("/quote/email", "marie"), ("/quote/phone_number", "123"),
    ("/vehicles/0/vin", "IOQ"), ("/vehicles/0/usage_type", "space"), ("/vehicles/0/vehicle_year", 1900),
    ("/vehicles/0/vehicle_year", 2030), ("/quote/date_of_birth", "2020-01-01"), ("/quote/effective_date", "2020-01-01"),
    ("/vehicles/0/annual_kilometers", 2.5), ("/vehicles/0/antique_vehicle", "maybe"),
    ("/household/driver_vehicle_assignments", [{"driver_index": 0, "vehicle_index": 0, "role": "boss"}]),
])
def test_invalid_values_are_rejected_with_a_customer_message(path: str, value: Any) -> None:
    d = doc()
    out = merge(d, [Change(path, value)], "form", 1)
    assert out.rejected[0]["path"] == path and out.rejected[0]["reason"] in state.ERRORS.values()
    assert out.changed is False


@pytest.mark.parametrize("path", ["/quote/nope", "/vehicles/0/first_name", "/vehicles/x/vin", "/underwriting/"
                                  "claim_question_asked", "quote/first_name"])
def test_unknown_system_or_malformed_paths_are_rejected(path: str) -> None:
    assert merge(doc(), [Change(path, True)], "chat", 1).rejected


def test_entities_are_created_up_to_the_catalog_max() -> None:
    d = doc()
    out = merge(d, [Change("/vehicles/2/vehicle_make", "Honda")], "chat", 1)
    assert d["vehicles"][:2] == [{}, {}] and out.structural
    assert merge(d, [Change("/vehicles/25/vehicle_make", "Honda")], "chat", 1).rejected
    assert merge(d, [Change("/drivers/3/driver_name", op="add_entity")], "chat", 1).structural
    assert len(d["drivers"]) == 4


def test_clearing_a_field_that_has_no_value_is_a_no_op() -> None:
    d = doc()
    assert merge(d, [Change("/quote/first_name", op="clear"), Change("/quote/email", None)], "chat", 1).changed is False
    assert merge(d, [Change("/household/driver_vehicle_assignments", [])], "form", 1).changed is False


def test_every_committed_envelope_has_status_and_confidence() -> None:
    d = doc()
    out = merge(d, [Change("/quote/first_name", "Marie", confidence=0.8), Change("/underwriting/has_claims", True)],
                "chat", 1)
    out2 = merge(d, [Change("/underwriting/has_claims", op="confirm"), Change("/quote/first_name", op="revert")],
                 "chat", 2)
    for a in out.changes + out2.changes:
        assert {"status", "confidence"} <= set(a.new), a.path


def test_clear_sets_a_cleared_envelope() -> None:
    d = doc()
    merge(d, [Change("/quote/first_name", "Marie")], "chat", 1)
    merge(d, [Change("/quote/first_name", op="clear")], "chat", 2)
    assert env(d, "/quote/first_name")["value"] is None


# -- rule 6: the end-of-form rule glue ------------------------------------------------------------------------------


def test_an_inferred_stop_asks_once_and_records_how_to_revert() -> None:
    d = doc()
    out = merge(d, [Change("/vehicles/0/vehicle_make", "Ferrari")], "chat", 1)
    outcome = state.apply_rules(d, out.applied)
    assert outcome.action == "ask" and outcome.stop.rule_id == "VEHICULE_EXOTIQUE"
    assert d["rule_question"] == {"rule_id": "VEHICULE_EXOTIQUE", "paths": ["/vehicles/0/vehicle_make"],
                                  "restore": {"/vehicles/0/vehicle_make": None}}
    assert d["system_notes"] == ["Rule VEHICULE_EXOTIQUE: stop"] and d["stop"] is None
    assert state.apply_rules(d, []).action == "go"  # acknowledged: not asked twice
    merge(d, [Change("/vehicles/0/vehicle_make", op="confirm")], "chat", 2)
    firm = state.apply_rules(d, [])
    assert firm.action == "stop" and d["stop"]["rule_id"] == "VEHICULE_EXOTIQUE"
    assert d["rule_hits"] == [{"rule_id": "VEHICULE_EXOTIQUE", "severity": "stop",
                               "paths": ["/vehicles/0/vehicle_make"], "inferred": False}]


def test_a_commit_that_clears_the_hit_lifts_the_stop() -> None:
    d = doc()
    merge(d, [Change("/vehicles/0/usage_type", "farm")], "form", 1)
    assert state.apply_rules(d, []).action == "stop"
    merge(d, [Change("/vehicles/0/usage_type", "commute")], "form", 2)
    assert state.apply_rules(d, []).action == "go" and d["stop"] is None and d["rule_hits"] == []


def test_a_flag_adds_one_advisor_signal_and_is_acknowledged() -> None:
    d = doc(consent=True)
    merge(d, [Change("/quote/first_name", "Léa"), Change("/quote/date_of_birth", "2005-01-01"),
              Change("/vehicles/0/vehicle_make", "Honda"),
              Change("/household/driver_vehicle_assignments",
                     [{"driver_index": 0, "vehicle_index": 0, "role": "principal"}])], "form", 1)
    outcome = state.apply_rules(d, [])
    assert outcome.action == "go" and [h.rule_id for h in outcome.hits] == ["JEUNE_CONDUCTEUR_PRINCIPAL"]
    assert [s["rule_id"] for s in d["advisor_signals"]] == ["JEUNE_CONDUCTEUR_PRINCIPAL"]
    assert state.apply_rules(d, []).hits == []
    assert len(d["advisor_signals"]) == 1


def test_a_failing_rule_keeps_the_commit_and_blocks_pricing(monkeypatch: pytest.MonkeyPatch) -> None:
    from quote_bff.rules import end_of_form as eof

    def broken(*_: Any) -> None:
        raise RuntimeError("bad rule")

    monkeypatch.setattr(eof.DEFAULT_RULES, "check", broken)
    d = doc()
    assert state.apply_rules(d, []).action == "go" and d["rules_error"] is True
