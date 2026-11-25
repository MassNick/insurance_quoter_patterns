"""Unit tests for the end-of-form rules. Standard library only.

Run from solution/bff:  .venv/bin/python -m pytest tests/unit/rules
"""

# The BFF design folder is stdlib-only, so these are unittest tests on purpose (not pytest-style asserts), and
# the test-data builders take plain booleans (envelope(True)).

from __future__ import annotations

import asyncio
import copy
import re
import unittest
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest import mock

from quote_bff.rules import end_of_form as eof
from quote_bff.rules.end_of_form import DEFAULT_RULES, EndOfFormRules, Rule, RuleCheckResult, Severity

if TYPE_CHECKING:
    from collections.abc import Callable

TODAY = date(2026, 11, 24)  # pinned so the age rules do not depend on the day the tests run

# The ids of the five example rules under test.
EXOTIC = "VEHICULE_EXOTIQUE"
MODIFIED = "VEHICULE_MODIFIE_PERFORMANCE"
FARM = "USAGE_AGRICOLE"
DENIED = "REFUS_OU_RESILIATION"
YOUNG = "JEUNE_CONDUCTEUR_PRINCIPAL"
ASSIGNMENTS = "/household/driver_vehicle_assignments"  # who drives which vehicle, as what
HIGH_MILEAGE_KM = 40_000  # threshold of the worked example in RegistryTest


# ---------------------------------------------------------------------------------------------
# Builders for session states made of field envelopes
# ---------------------------------------------------------------------------------------------


def envelope(value: Any, *, status: str = "confirmed", source: str = "form") -> dict[str, Any]:
    """A field envelope as the BFF stores it; by default a confirmed value from a form."""
    return {
        "value": value,
        "source": source,
        "confidence": 1.0 if source == "form" else 0.9,
        "status": status,
        "turn": 1,
        "updated_at": "2026-11-24T15:04:05Z",
    }


def guessed(value: Any) -> dict[str, Any]:
    """What the memory agent writes from chat: a value the customer has not confirmed."""
    return envelope(value, status="inferred", source="chat")


def assign(driver: int, vehicle: int, role: str = "principal") -> dict[str, Any]:
    """One driver-to-vehicle assignment, by index (``principal`` unless told otherwise)."""
    return {"driver_index": driver, "vehicle_index": vehicle, "role": role, "percent_use": 100}


def assignments_field(items: list[dict[str, Any]], *, status: str = "confirmed") -> dict[str, Any]:
    """The household entity: just the assignments field."""
    return {"driver_vehicle_assignments": envelope(items, status=status)}


def make_state(
    *,
    quote: dict[str, Any] | None = None,
    vehicles: list[dict[str, Any]] | None = None,
    drivers: list[dict[str, Any]] | None = None,
    underwriting: dict[str, Any] | None = None,
    household: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """A session state from its five entities; any left out is empty."""
    return {
        "quote": quote or {},
        "vehicles": vehicles or [],
        "drivers": drivers or [],
        "underwriting": underwriting or {},
        "household": household or {},
    }


def clean_state() -> dict[str, Any]:
    """An ordinary family quote. Nothing in it should stop or flag."""
    return make_state(
        quote={"first_name": envelope("Marie"), "date_of_birth": envelope("1986-03-14")},
        vehicles=[
            {
                "vehicle_make": envelope("Honda"),
                "vehicle_model": envelope("Civic"),
                "usage_type": envelope("commute"),
                "modified_for_performance": envelope(False),
            }
        ],
        drivers=[
            {"relationship_code": envelope("self")},
            {
                "driver_name": envelope("Alex Tremblay"),
                "relationship_code": envelope("spouse"),
                "driver_date_of_birth": envelope("1984-07-02"),
            },
        ],
        underwriting={"was_denied_or_cancelled": envelope(False), "has_claims": envelope(False)},
        household=assignments_field([assign(0, 0, "principal"), assign(1, 0, "occasional")]),
    )


def vehicle_state(**fields: Any) -> dict[str, Any]:
    """A state with one vehicle whose fields are the given envelopes."""
    return make_state(vehicles=[fields])


def young_state(
    *,
    born: str = "2007-03-15",
    role: str = "principal",
    dob_status: str = "confirmed",
    assignments_status: str = "confirmed",
) -> dict[str, Any]:
    """An adult policyholder (driver 0) and a second driver (driver 1) born on `born`, one vehicle."""
    return make_state(
        quote={"date_of_birth": envelope("1986-03-14")},
        vehicles=[{"vehicle_make": envelope("Honda")}],
        drivers=[
            {"relationship_code": envelope("self")},
            {
                "relationship_code": envelope("child"),
                "driver_date_of_birth": envelope(born, status=dob_status),
            },
        ],
        household=assignments_field([assign(0, 0, "occasional"), assign(1, 0, role)], status=assignments_status),
    )


def simple_rule(rule_id: str, severity: Severity, *paths: str) -> Rule:
    """A throwaway rule that matches the given paths whatever the state."""
    return Rule(
        id=rule_id,
        description="test rule",
        severity=severity,
        reason_fr="raison",
        reason_en="reason",
        predicate=lambda _state: list(paths),
    )


# NFR-MNT-4: every registered rule needs a positive and a negative fixture. A rule added to DEFAULT_RULES without a
# pair here fails FixtureCoverageTest, so CI catches it. Each entry: rule id -> (state that fires it, near miss).
FIXTURES: dict[str, tuple[Callable[[], dict[str, Any]], Callable[[], dict[str, Any]]]] = {
    EXOTIC: (
        lambda: vehicle_state(vehicle_make=envelope("Ferrari")),
        lambda: vehicle_state(vehicle_make=envelope("Honda")),
    ),
    MODIFIED: (
        lambda: vehicle_state(modified_for_performance=envelope(True)),
        lambda: vehicle_state(modified_for_performance=envelope(False)),
    ),
    FARM: (
        lambda: vehicle_state(usage_type=envelope("farm")),
        lambda: vehicle_state(usage_type=envelope("business")),
    ),
    DENIED: (
        lambda: make_state(underwriting={"was_denied_or_cancelled": envelope(True)}),
        lambda: make_state(underwriting={"was_denied_or_cancelled": envelope(True, status="pending_confirmation")}),
    ),
    YOUNG: (
        lambda: young_state(born="2007-03-15"),
        lambda: young_state(born="1990-01-01"),
    ),
}


class RulesTestCase(unittest.TestCase):
    """Base class: pins today() and offers small assertions."""

    def setUp(self) -> None:
        """Pin ``today()`` to ``TODAY`` for the test."""
        patcher = mock.patch.object(eof, "today", return_value=TODAY)
        patcher.start()
        self.addCleanup(patcher.stop)

    def check(self, state: dict[str, Any], acknowledged: Any = frozenset()) -> RuleCheckResult:
        """Run the default rules on ``state``."""
        return DEFAULT_RULES.check(state, acknowledged)

    def ids(self, result: RuleCheckResult) -> list[str]:
        """The rule ids of a result's hits, in order."""
        return [hit.rule_id for hit in result.hits]


# ---------------------------------------------------------------------------------------------
# The default registry
# ---------------------------------------------------------------------------------------------


class DefaultRegistryTest(RulesTestCase):
    """The five example rules: their order, severities, French and English reasons, and id format."""

    def test_the_five_example_rules_are_registered_in_order(self) -> None:
        self.assertEqual(
            [(rule.id, rule.severity) for rule in DEFAULT_RULES.rules],
            [
                (EXOTIC, Severity.STOP),
                (MODIFIED, Severity.STOP),
                (FARM, Severity.STOP),
                (DENIED, Severity.STOP),
                (YOUNG, Severity.FLAG),
            ],
        )

    def test_every_rule_has_its_own_french_and_english_reason(self) -> None:
        for rule in DEFAULT_RULES.rules:
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.reason_fr.strip())
                self.assertTrue(rule.reason_en.strip())
                self.assertNotEqual(rule.reason_fr, rule.reason_en)
                self.assertTrue(rule.description.strip())

    def test_ids_are_upper_snake(self) -> None:
        for rule in DEFAULT_RULES.rules:
            self.assertRegex(rule.id, r"^[A-Z][A-Z0-9]*(_[A-Z0-9]+)*$")


class FixtureCoverageTest(RulesTestCase):
    """Every registered rule has a positive and a negative fixture (NFR-MNT-4).

    The positive one fires its rule and no other; the negative one fires nothing.
    """

    def test_every_registered_rule_has_a_positive_and_a_negative_fixture(self) -> None:
        self.assertEqual(sorted(FIXTURES), sorted(rule.id for rule in DEFAULT_RULES.rules))

    def test_the_positive_fixture_fires_its_rule_and_no_other(self) -> None:
        for rule in DEFAULT_RULES.rules:
            positive, _ = FIXTURES[rule.id]
            with self.subTest(rule=rule.id):
                self.assertEqual(self.ids(self.check(positive())), [rule.id])

    def test_the_negative_fixture_fires_nothing(self) -> None:
        for rule in DEFAULT_RULES.rules:
            _, negative = FIXTURES[rule.id]
            with self.subTest(rule=rule.id):
                self.assertEqual(self.check(negative()).hits, ())


# ---------------------------------------------------------------------------------------------
# No hit
# ---------------------------------------------------------------------------------------------


class NoHitTest(RulesTestCase):
    """No hit for an ordinary, empty, partial or malformed state; ``check`` neither changes the state nor varies."""

    def test_an_ordinary_quote_has_no_hit(self) -> None:
        result = self.check(clean_state())
        self.assertEqual(result.hits, ())
        self.assertIsNone(result.stop)
        self.assertEqual(result.flags, ())
        self.assertFalse(result.needs_confirmation)

    def test_an_empty_state_has_no_hit(self) -> None:
        self.assertEqual(self.check({}).hits, ())
        self.assertEqual(self.check(make_state()).hits, ())

    def test_an_empty_result_is_a_valid_result(self) -> None:
        result = RuleCheckResult()
        self.assertEqual((result.hits, result.stop, result.flags, result.needs_confirmation), ((), None, (), False))

    def test_partial_or_malformed_states_never_raise_and_never_match(self) -> None:
        malformed: list[Any] = [
            {"quote": None, "vehicles": None, "drivers": None, "underwriting": None, "household": None},
            {"vehicles": "oops", "drivers": 3, "household": []},
            {"vehicles": [None, 1, "x", {}, {"vehicle_make": "Ferrari"}, {"vehicle_make": {"value": None}}]},
            {"vehicles": [{"vehicle_make": {"value": 42, "status": 7}, "usage_type": {"value": ["farm"]}}]},
            {"underwriting": {"was_denied_or_cancelled": True}},  # a bare value, not an envelope
            {"household": {"driver_vehicle_assignments": {"value": "not a list"}}},
            {
                "household": {
                    "driver_vehicle_assignments": {
                        "value": [None, 3, "x", {"role": "principal"}, {"role": "principal", "driver_index": {}}]
                    }
                }
            },
            {
                "drivers": [{"driver_date_of_birth": {"value": ["2007"]}}],
                "vehicles": [{}],
                "household": {"driver_vehicle_assignments": {"value": [assign(0, 0)]}},
            },
        ]
        for state in malformed:
            with self.subTest(state=state):
                self.assertEqual(self.check(state).hits, ())

    def test_check_does_not_change_the_state(self) -> None:
        state = young_state()
        state["vehicles"][0]["vehicle_make"] = envelope("Ferrari")  # a STOP and a FLAG at the same time
        before = copy.deepcopy(state)
        self.assertEqual(len(self.check(state).hits), 2)
        self.assertEqual(state, before)

    def test_the_same_state_always_gives_the_same_result(self) -> None:
        state = young_state()
        state["vehicles"][0]["vehicle_make"] = guessed("Ferrari")
        result = self.check(state)
        self.assertEqual(len(result.hits), 2)
        self.assertEqual(result, self.check(copy.deepcopy(state)))


# ---------------------------------------------------------------------------------------------
# One class per example rule
# ---------------------------------------------------------------------------------------------


class VehiculeExotiqueTest(RulesTestCase):
    """VEHICULE_EXOTIQUE: an exotic make, however it is spelled, and nothing else."""

    def test_every_exotic_make_matches_whatever_its_spelling(self) -> None:
        spellings = [
            "Lamborghini", "ferrari", "McLaren", "BUGATTI", "Rolls-Royce", "rolls royce", "ROLLS-ROYCE",
            "Bentley", "Aston Martin", "aston-martin", "  Aston   Martin ",
        ]  # fmt: skip
        for make in spellings:
            with self.subTest(make=make):
                result = self.check(vehicle_state(vehicle_make=envelope(make)))
                self.assertEqual(self.ids(result), [EXOTIC])
                self.assertEqual(result.stop.severity, Severity.STOP)
                self.assertEqual(result.stop.paths, ("/vehicles/0/vehicle_make",))
                self.assertEqual(result.stop.values, {"/vehicles/0/vehicle_make": make})

    def test_other_makes_do_not_match(self) -> None:
        for make in ["Honda", "Toyota", "Fiat", "Ferrari Testa", "Rolls", "Martin", "", "   ", None, 42]:
            with self.subTest(make=make):
                self.assertEqual(self.check(vehicle_state(vehicle_make=envelope(make))).hits, ())

    def test_only_the_exotic_vehicle_of_a_fleet_is_reported(self) -> None:
        state = make_state(
            vehicles=[
                {"vehicle_make": envelope("Honda")},
                {"vehicle_make": envelope("Bentley")},
                {"vehicle_make": envelope("Mazda")},
                {"vehicle_make": envelope("Ferrari")},
            ]
        )
        self.assertEqual(self.check(state).stop.paths, ("/vehicles/1/vehicle_make", "/vehicles/3/vehicle_make"))

    def test_the_model_alone_does_not_match(self) -> None:
        self.assertEqual(self.check(vehicle_state(vehicle_model=envelope("Ferrari"))).hits, ())


class VehiculeModifiePerformanceTest(RulesTestCase):
    """VEHICULE_MODIFIE_PERFORMANCE: a vehicle modified for performance, only for a real ``True``."""

    def test_a_vehicle_modified_for_performance_stops_the_quote(self) -> None:
        result = self.check(vehicle_state(modified_for_performance=envelope(True)))
        self.assertEqual(self.ids(result), [MODIFIED])
        self.assertEqual(result.stop.severity, Severity.STOP)
        self.assertEqual(result.stop.paths, ("/vehicles/0/modified_for_performance",))

    def test_false_or_missing_does_not_match(self) -> None:
        self.assertEqual(self.check(vehicle_state(modified_for_performance=envelope(False))).hits, ())
        self.assertEqual(self.check(vehicle_state(vehicle_make=envelope("Honda"))).hits, ())

    def test_only_a_real_boolean_true_counts(self) -> None:
        for value in ["true", "false", "yes", 1, [True], {"a": 1}]:
            with self.subTest(value=value):
                self.assertEqual(self.check(vehicle_state(modified_for_performance=envelope(value))).hits, ())

    def test_it_reports_the_modified_vehicle_of_a_fleet(self) -> None:
        state = make_state(
            vehicles=[{"modified_for_performance": envelope(False)}, {"modified_for_performance": envelope(True)}]
        )
        self.assertEqual(self.check(state).stop.paths, ("/vehicles/1/modified_for_performance",))


class UsageAgricoleTest(RulesTestCase):
    """USAGE_AGRICOLE: farm use, whatever the case or spacing."""

    def test_farm_use_stops_the_quote(self) -> None:
        result = self.check(vehicle_state(usage_type=envelope("farm")))
        self.assertEqual(self.ids(result), [FARM])
        self.assertEqual(result.stop.severity, Severity.STOP)
        self.assertEqual(result.stop.paths, ("/vehicles/0/usage_type",))

    def test_other_uses_do_not_match(self) -> None:
        for usage in ["pleasure", "commute", "business", "farmhouse", "", None]:
            with self.subTest(usage=usage):
                self.assertEqual(self.check(vehicle_state(usage_type=envelope(usage))).hits, ())

    def test_case_and_spacing_are_ignored(self) -> None:
        self.assertEqual(self.ids(self.check(vehicle_state(usage_type=envelope(" Farm ")))), [FARM])

    def test_it_reports_the_farm_vehicle_of_a_fleet(self) -> None:
        state = make_state(vehicles=[{"usage_type": envelope("commute")}, {"usage_type": envelope("farm")}])
        self.assertEqual(self.check(state).stop.paths, ("/vehicles/1/usage_type",))


class RefusOuResiliationTest(RulesTestCase):
    """REFUS_OU_RESILIATION: a refused or cancelled policy, only once the customer has confirmed the answer."""

    path = "/underwriting/was_denied_or_cancelled"

    def state(self, value: Any, status: str) -> dict[str, Any]:
        """A state whose ``was_denied_or_cancelled`` has this value and status."""
        return make_state(underwriting={"was_denied_or_cancelled": envelope(value, status=status)})

    def test_a_confirmed_yes_stops_the_quote(self) -> None:
        result = self.check(self.state(True, "confirmed"))
        self.assertEqual(self.ids(result), [DENIED])
        self.assertEqual(result.stop.severity, Severity.STOP)
        self.assertEqual(result.stop.paths, (self.path,))
        self.assertFalse(result.stop.inferred)
        self.assertFalse(result.needs_confirmation)  # it already went through the double confirmation

    def test_a_yes_that_is_not_confirmed_yet_does_not_match(self) -> None:
        for status in ["pending_confirmation", "inferred"]:
            with self.subTest(status=status):
                self.assertEqual(self.check(self.state(True, status)).hits, ())

    def test_a_confirmed_no_or_a_missing_answer_does_not_match(self) -> None:
        self.assertEqual(self.check(self.state(False, "confirmed")).hits, ())
        self.assertEqual(self.check(self.state(None, "confirmed")).hits, ())
        self.assertEqual(self.check(make_state()).hits, ())

    def test_it_fires_when_the_double_confirmation_completes(self) -> None:
        self.assertEqual(self.check(self.state(True, "pending_confirmation")).hits, ())  # step 1: held as pending
        self.assertEqual(self.ids(self.check(self.state(True, "confirmed"))), [DENIED])  # step 2 accepted: committed

    def test_a_status_the_envelope_does_not_carry_does_not_match(self) -> None:
        bare = make_state(underwriting={"was_denied_or_cancelled": {"value": True}})
        self.assertEqual(self.check(bare).hits, ())


class JeuneConducteurPrincipalTest(RulesTestCase):
    """JEUNE_CONDUCTEUR_PRINCIPAL (a FLAG): a driver under 25 who is the principal driver of a vehicle."""

    def test_a_driver_under_25_who_is_principal_is_flagged(self) -> None:
        result = self.check(young_state(born="2007-03-15"))  # 19 years old
        self.assertEqual(self.ids(result), [YOUNG])
        hit = result.hits[0]
        self.assertEqual(hit.severity, Severity.FLAG)
        self.assertEqual(hit.paths, ("/drivers/1/driver_date_of_birth", ASSIGNMENTS))
        self.assertEqual(hit.values["/drivers/1/driver_date_of_birth"], "2007-03-15")
        self.assertIsNone(result.stop)  # a FLAG never ends the quote
        self.assertEqual(result.flags, (hit,))
        self.assertFalse(result.needs_confirmation)

    def test_other_roles_do_not_match(self) -> None:
        for role in ["occasional", "none"]:
            with self.subTest(role=role):
                self.assertEqual(self.check(young_state(role=role)).hits, ())

    def test_a_driver_aged_25_or_more_does_not_match(self) -> None:
        self.assertEqual(self.check(young_state(born="1990-01-01")).hits, ())

    def test_25_today_is_not_young_but_one_day_short_is(self) -> None:
        self.assertEqual(self.check(young_state(born="2001-11-24")).hits, ())  # turned 25 today
        self.assertEqual(self.ids(self.check(young_state(born="2001-11-25"))), [YOUNG])  # turns 25 tomorrow

    def test_the_age_follows_the_birthday_of_a_leap_day(self) -> None:
        state = young_state(born="2000-02-29")
        with mock.patch.object(eof, "today", return_value=date(2025, 2, 28)):
            self.assertEqual(self.ids(self.check(state)), [YOUNG])  # still 24
        with mock.patch.object(eof, "today", return_value=date(2025, 3, 1)):
            self.assertEqual(self.check(state).hits, ())  # 25

    def test_age_on_counts_whole_years(self) -> None:
        self.assertEqual(eof.age_on(date(2000, 2, 29), date(2025, 2, 28)), 24)
        self.assertEqual(eof.age_on(date(2000, 2, 29), date(2025, 3, 1)), 25)
        self.assertEqual(eof.age_on(date(2007, 3, 15), date(2026, 3, 15)), 19)
        self.assertEqual(eof.age_on(date(2007, 3, 15), date(2026, 3, 14)), 18)

    def test_the_policyholder_is_driver_0_and_their_birth_date_is_in_the_quote(self) -> None:
        state = make_state(
            quote={"date_of_birth": envelope("2005-06-01")},  # 21 years old
            vehicles=[{"vehicle_make": envelope("Honda")}],
            drivers=[{"relationship_code": envelope("self")}],
            household=assignments_field([assign(0, 0)]),
        )
        result = self.check(state)
        self.assertEqual(self.ids(result), [YOUNG])
        self.assertEqual(result.hits[0].paths, ("/quote/date_of_birth", ASSIGNMENTS))

    def test_driver_0_without_a_relationship_is_the_policyholder(self) -> None:
        state = make_state(
            quote={"date_of_birth": envelope("2005-06-01")},
            vehicles=[{}],
            drivers=[{}],
            household=assignments_field([assign(0, 0)]),
        )
        self.assertEqual(self.check(state).hits[0].paths, ("/quote/date_of_birth", ASSIGNMENTS))

    def test_a_spouse_does_not_borrow_the_policyholders_birth_date(self) -> None:
        state = young_state()
        state["drivers"][1] = {"relationship_code": envelope("spouse")}  # no date of birth of their own
        state["quote"]["date_of_birth"] = envelope("2005-06-01")  # a young policyholder, not the principal driver
        self.assertEqual(self.check(state).hits, ())

    def test_every_young_principal_driver_is_listed_once(self) -> None:
        state = make_state(
            vehicles=[{}, {}, {}],
            drivers=[
                {"relationship_code": envelope("self")},
                {"driver_date_of_birth": envelope("2006-01-10")},
                {"driver_date_of_birth": envelope("2004-02-02")},
                {"driver_date_of_birth": envelope("1980-02-02")},
            ],
            household=assignments_field([assign(1, 0), assign(1, 1), assign(2, 2), assign(3, 2, "occasional")]),
        )
        self.assertEqual(
            self.check(state).hits[0].paths,
            ("/drivers/1/driver_date_of_birth", "/drivers/2/driver_date_of_birth", ASSIGNMENTS),
        )

    def test_assignments_to_drivers_or_vehicles_that_do_not_exist_are_ignored(self) -> None:
        for item in [
            assign(5, 0),
            assign(1, 3),
            assign(-1, 0),
            {"driver_index": True, "vehicle_index": 0, "role": "principal"},
            {"driver_index": "1", "vehicle_index": 0, "role": "principal"},
            {"driver_index": 1, "role": "principal"},
        ]:
            with self.subTest(item=item):
                state = young_state()
                state["household"] = assignments_field([item])
                self.assertEqual(self.check(state).hits, ())

    def test_a_missing_future_or_unreadable_birth_date_does_not_match(self) -> None:
        for born in [None, "", "not a date", "15/03/2007", "2099-01-01", 2007]:
            with self.subTest(born=born):
                self.assertEqual(self.check(young_state(born=born)).hits, ())  # type: ignore[arg-type]

    def test_a_timestamp_birth_date_is_read(self) -> None:
        self.assertEqual(self.ids(self.check(young_state(born="2007-03-15T00:00:00Z"))), [YOUNG])


# ---------------------------------------------------------------------------------------------
# Inferred hits and the confirmation question
# ---------------------------------------------------------------------------------------------


class InferredConfirmationTest(RulesTestCase):
    """A STOP that is only inferred from chat needs one confirmation question before it stops the quote."""

    def test_a_stop_only_guessed_from_chat_needs_one_confirmation_question(self) -> None:
        result = self.check(vehicle_state(vehicle_make=guessed("Ferrari")))
        self.assertEqual(self.ids(result), [EXOTIC])
        self.assertTrue(result.stop.inferred)
        self.assertTrue(result.needs_confirmation)

    def test_a_confirmed_stop_does_not_ask_anything(self) -> None:
        result = self.check(vehicle_state(vehicle_make=envelope("Ferrari", status="confirmed")))
        self.assertFalse(result.stop.inferred)
        self.assertFalse(result.needs_confirmation)

    def test_other_statuses_are_not_inferred(self) -> None:
        for status in ["confirmed", "pending_confirmation", "something_else"]:
            with self.subTest(status=status):
                result = self.check(vehicle_state(vehicle_make=envelope("Ferrari", status=status)))
                self.assertFalse(result.stop.inferred)
                self.assertFalse(result.needs_confirmation)

    def test_a_value_without_a_status_is_not_inferred(self) -> None:
        bare = {"value": "Ferrari"}
        result = self.check(vehicle_state(vehicle_make=bare))
        self.assertFalse(result.stop.inferred)

    def test_a_multi_value_hit_is_inferred_only_when_every_value_is(self) -> None:
        cases = [
            ("confirmed", "confirmed", False),
            ("confirmed", "inferred", False),
            ("inferred", "confirmed", False),
            ("inferred", "inferred", True),
        ]
        for dob_status, assignments_status, expected in cases:
            with self.subTest(dob=dob_status, assignments=assignments_status):
                state = young_state(dob_status=dob_status, assignments_status=assignments_status)
                self.assertEqual(self.check(state).hits[0].inferred, expected)

    def test_a_flag_never_needs_confirmation_even_when_inferred(self) -> None:
        result = self.check(young_state(dob_status="inferred", assignments_status="inferred"))
        self.assertTrue(result.flags[0].inferred)
        self.assertIsNone(result.stop)
        self.assertFalse(result.needs_confirmation)

    def test_ask_once_then_stop_when_the_customer_confirms(self) -> None:
        acknowledged: set[str] = set()

        guess = self.check(vehicle_state(vehicle_make=guessed("Ferrari")), acknowledged)  # chat: "a Ferrari 488"
        self.assertTrue(guess.needs_confirmation)
        acknowledged.add(guess.stop.fingerprint)  # the BFF asked its question

        again = self.check(vehicle_state(vehicle_make=guessed("Ferrari")), acknowledged)  # next, unrelated commit
        self.assertEqual(again.hits, ())  # it is not asked a second time

        confirmed = self.check(vehicle_state(vehicle_make=envelope("Ferrari")), acknowledged)  # the customer said yes
        self.assertEqual(self.ids(confirmed), [EXOTIC])  # the acknowledged question does not swallow the stop
        self.assertFalse(confirmed.needs_confirmation)
        self.assertNotEqual(confirmed.stop.fingerprint, guess.stop.fingerprint)

    def test_a_corrected_value_leaves_no_hit(self) -> None:
        acknowledged = {self.check(vehicle_state(vehicle_make=guessed("Ferrari"))).stop.fingerprint}
        self.assertEqual(self.check(vehicle_state(vehicle_make=envelope("Fiat")), acknowledged).hits, ())
        self.assertEqual(self.check(vehicle_state(vehicle_make=envelope(None)), acknowledged).hits, ())


# ---------------------------------------------------------------------------------------------
# Acknowledged fingerprints
# ---------------------------------------------------------------------------------------------


class AcknowledgedTest(RulesTestCase):
    """Acknowledged fingerprints: a handled hit is not reported again, and the fingerprint recipe is pinned."""

    def test_an_acknowledged_fingerprint_is_skipped(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"))
        first = self.check(state)
        self.assertEqual(self.ids(first), [FARM])
        second = self.check(state, {first.stop.fingerprint})
        self.assertEqual(second.hits, ())
        self.assertIsNone(second.stop)

    def test_only_the_acknowledged_hit_is_skipped(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"), modified_for_performance=envelope(True))
        farm = next(hit for hit in self.check(state).hits if hit.rule_id == FARM)
        remaining = self.check(state, frozenset({farm.fingerprint}))
        self.assertEqual(self.ids(remaining), [MODIFIED])

    def test_a_flag_is_skipped_once_acknowledged(self) -> None:
        state = young_state()
        flag = self.check(state).flags[0]
        self.assertEqual(self.check(state, [flag.fingerprint]).hits, ())

    def test_any_collection_of_fingerprints_works(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"))
        fingerprint = self.check(state).stop.fingerprint
        for acknowledged in [{fingerprint}, frozenset({fingerprint}), [fingerprint], (fingerprint,)]:
            with self.subTest(kind=type(acknowledged).__name__):
                self.assertEqual(self.check(state, acknowledged).hits, ())

    def test_unrelated_fingerprints_change_nothing(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"))
        self.assertEqual(self.ids(self.check(state, {"0123456789abcdef"})), [FARM])

    def test_the_fingerprint_is_stable(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"))
        fingerprint = self.check(state).stop.fingerprint
        self.assertRegex(fingerprint, r"^[0-9a-f]{16}$")
        self.assertEqual(self.check(copy.deepcopy(state)).stop.fingerprint, fingerprint)
        reordered = {key: state[key] for key in reversed(list(state))}
        self.assertEqual(self.check(reordered).stop.fingerprint, fingerprint)

    def test_the_fingerprint_does_not_depend_on_unrelated_fields(self) -> None:
        state = vehicle_state(usage_type=envelope("farm"))
        fingerprint = self.check(state).stop.fingerprint
        state["quote"]["first_name"] = envelope("Marie")
        state["vehicles"][0]["annual_kilometers"] = envelope(12000)
        self.assertEqual(self.check(state).stop.fingerprint, fingerprint)

    def test_the_fingerprint_recipe_is_pinned(self) -> None:
        # sha256 of '["VEHICULE_EXOTIQUE",false,[["/vehicles/0/vehicle_make","Ferrari"]]]', first 16 hex characters.
        # Changing the recipe makes every acknowledged hit in a live session fire again: only do it on purpose.
        result = self.check(vehicle_state(vehicle_make=envelope("Ferrari")))
        self.assertEqual(result.stop.fingerprint, "41151eb2060bf2b0")

    def test_the_fingerprint_changes_with_the_value_the_path_the_evidence_and_the_rule(self) -> None:
        base = self.check(vehicle_state(vehicle_make=envelope("Ferrari"))).stop.fingerprint
        other_value = self.check(vehicle_state(vehicle_make=envelope("McLaren"))).stop.fingerprint
        other_path = self.check(make_state(vehicles=[{}, {"vehicle_make": envelope("Ferrari")}])).stop.fingerprint
        inferred = self.check(vehicle_state(vehicle_make=guessed("Ferrari"))).stop.fingerprint
        other_rule = self.check(vehicle_state(usage_type=envelope("farm"))).stop.fingerprint
        self.assertEqual(len({base, other_value, other_path, inferred, other_rule}), 5)

    def test_hit_values_are_a_snapshot_of_the_state(self) -> None:
        state = young_state()
        hit = self.check(state).hits[0]
        state["household"]["driver_vehicle_assignments"]["value"].append(assign(1, 0, "none"))
        self.assertEqual(len(hit.values[ASSIGNMENTS]), 2)


# ---------------------------------------------------------------------------------------------
# Ordering
# ---------------------------------------------------------------------------------------------


class OrderingTest(RulesTestCase):
    """Hit order: STOP before FLAG, firm before inferred, then registration order."""

    def test_a_stop_comes_before_a_flag(self) -> None:
        state = {**young_state(), "vehicles": [{"vehicle_make": envelope("Honda"), "usage_type": envelope("farm")}]}
        result = self.check(state)
        self.assertEqual(self.ids(result), [FARM, YOUNG])
        self.assertEqual(result.stop.rule_id, FARM)
        self.assertEqual([hit.rule_id for hit in result.flags], [YOUNG])

    def test_a_stop_comes_first_even_when_the_flag_rule_was_registered_first(self) -> None:
        rules = EndOfFormRules()
        rules.register(simple_rule("NOTE_CONSEILLER", Severity.FLAG, "/quote/first_name"))
        rules.register(simple_rule("ARRET_NET", Severity.STOP, "/quote/last_name"))
        result = rules.check({})
        self.assertEqual([hit.rule_id for hit in result.hits], ["ARRET_NET", "NOTE_CONSEILLER"])
        self.assertEqual(result.stop.rule_id, "ARRET_NET")
        self.assertEqual([hit.rule_id for hit in result.flags], ["NOTE_CONSEILLER"])

    def test_registration_order_breaks_ties_within_a_severity(self) -> None:
        rules = EndOfFormRules()
        for name in ["TROISIEME", "PREMIERE", "DEUXIEME"]:
            rules.register(simple_rule(name, Severity.STOP, f"/quote/{name.lower()}"))
        self.assertEqual([hit.rule_id for hit in rules.check({}).hits], ["TROISIEME", "PREMIERE", "DEUXIEME"])

    def test_a_firm_stop_ranks_before_an_inferred_stop(self) -> None:
        # The exotic make is registered first but only guessed; the farm use is confirmed.
        state = vehicle_state(vehicle_make=guessed("Ferrari"), usage_type=envelope("farm"))
        result = self.check(state)
        self.assertEqual(self.ids(result), [FARM, EXOTIC])
        self.assertEqual(result.stop.rule_id, FARM)
        self.assertFalse(result.needs_confirmation)  # the quote stops now; no question about the Ferrari

    def test_with_only_inferred_stops_the_first_one_asks_the_question(self) -> None:
        state = vehicle_state(vehicle_make=guessed("Ferrari"), modified_for_performance=guessed(True))
        result = self.check(state)
        self.assertEqual(self.ids(result), [EXOTIC, MODIFIED])
        self.assertTrue(result.needs_confirmation)


# ---------------------------------------------------------------------------------------------
# Registry: "How to add a rule" worked as code
# ---------------------------------------------------------------------------------------------


class RegistryTest(RulesTestCase):
    """Registering a new rule: the decorator, validation errors, loud predicate errors and independent registries."""

    def test_the_decorator_registers_the_rule_and_returns_the_predicate_unchanged(self) -> None:
        rules = EndOfFormRules()

        @rules.rule(
            id="VEHICULE_LOURD",
            description="Heavy vehicle.",
            severity=Severity.FLAG,
            reason_fr="Un véhicule lourd demande un suivi.",
            reason_en="A heavy vehicle needs a follow-up.",
        )
        def vehicule_lourd(state: dict[str, Any]) -> list[str]:
            return [path for path, value in eof.iter_fields(state, "vehicles", "vehicle_type") if value == "motorhome"]

        self.assertEqual([rule.id for rule in rules.rules], ["VEHICULE_LOURD"])
        self.assertIs(rules.get("VEHICULE_LOURD").predicate, vehicule_lourd)
        self.assertEqual(
            vehicule_lourd(vehicle_state(vehicle_type=envelope("motorhome"))), ["/vehicles/0/vehicle_type"]
        )

    def test_a_new_rule_works_end_to_end(self) -> None:
        # The six steps of the guide: predicate, severity, reasons, registration, then test it.
        rules = EndOfFormRules()

        @rules.rule(
            id="KILOMETRAGE_ELEVE",
            description="More than 40,000 km a year.",
            severity=Severity.FLAG,
            reason_fr="Un kilométrage annuel élevé sera revu par un membre de notre équipe.",
            reason_en="A high yearly mileage will be reviewed by a member of our team.",
        )
        def kilometrage_eleve(state: dict[str, Any]) -> list[str]:
            return [
                path
                for path, km in eof.iter_fields(state, "vehicles", "annual_kilometers")
                if isinstance(km, int) and km > HIGH_MILEAGE_KM
            ]

        match = rules.check(vehicle_state(annual_kilometers=envelope(55_000)))
        self.assertEqual([hit.rule_id for hit in match.flags], ["KILOMETRAGE_ELEVE"])
        self.assertEqual(match.flags[0].paths, ("/vehicles/0/annual_kilometers",))
        self.assertEqual(rules.check(vehicle_state(annual_kilometers=envelope(12_000))).hits, ())  # near miss
        self.assertEqual(rules.check({}).hits, ())  # partial state

    def test_a_duplicate_id_is_rejected(self) -> None:
        rules = EndOfFormRules()
        rules.register(simple_rule("DEJA_PRIS", Severity.FLAG))
        with self.assertRaisesRegex(ValueError, "already registered"):
            rules.register(simple_rule("DEJA_PRIS", Severity.STOP))

    def test_an_id_that_is_not_upper_snake_is_rejected(self) -> None:
        for bad in [
            "usage_agricole",
            "Usage_Agricole",
            "USAGE AGRICOLE",
            "USAGE-AGRICOLE",
            "1_USAGE",
            "USAGE__X",
            "_X",
            "",
        ]:
            with self.subTest(id=bad), self.assertRaisesRegex(ValueError, "UPPER_SNAKE"):
                simple_rule(bad, Severity.FLAG)

    def test_a_blank_reason_or_description_is_rejected(self) -> None:
        for field in ["reason_fr", "reason_en", "description"]:
            for blank in ["", "   "]:
                with self.subTest(field=field, blank=blank), self.assertRaisesRegex(ValueError, field):
                    Rule(
                        **{
                            "id": "SANS_RAISON",
                            "description": "d",
                            "severity": Severity.STOP,
                            "reason_fr": "r",
                            "reason_en": "r",
                            "predicate": lambda _state: [],
                            field: blank,
                        }
                    )

    def test_a_severity_that_is_not_a_severity_is_rejected(self) -> None:
        with self.assertRaises(TypeError):
            simple_rule("MAUVAISE_GRAVITE", "stop")  # type: ignore[arg-type]

    def test_a_predicate_that_returns_something_else_than_pointers_is_a_loud_error(self) -> None:
        # A bug in a rule must not pass as "no match": it is raised, naming the rule.
        for bad in [["vehicles/0/vehicle_make"], "/vehicles/0/vehicle_make", [None], [3]]:
            with self.subTest(returned=bad):
                rules = EndOfFormRules()
                rules.register(
                    Rule(
                        id="MAUVAIS_CHEMIN",
                        description="test rule",
                        severity=Severity.STOP,
                        reason_fr="raison",
                        reason_en="reason",
                        predicate=lambda _state, bad=bad: bad,  # type: ignore[misc]
                    )
                )
                with self.assertRaisesRegex(ValueError, "MAUVAIS_CHEMIN"):
                    rules.check({})

    def test_repeated_paths_are_reported_once_in_the_predicates_order(self) -> None:
        rules = EndOfFormRules()
        rules.register(simple_rule("DOUBLON", Severity.FLAG, "/quote/b", "/quote/a", "/quote/b"))
        self.assertEqual(rules.check({}).hits[0].paths, ("/quote/b", "/quote/a"))

    def test_a_rule_that_does_not_match_returns_no_hit(self) -> None:
        rules = EndOfFormRules()
        rules.register(simple_rule("JAMAIS", Severity.STOP))
        self.assertEqual(rules.check({"anything": 1}).hits, ())

    def test_registries_are_independent_of_the_default_one(self) -> None:
        before = [rule.id for rule in DEFAULT_RULES.rules]
        EndOfFormRules().register(simple_rule("AILLEURS", Severity.FLAG))
        self.assertEqual([rule.id for rule in DEFAULT_RULES.rules], before)


# ---------------------------------------------------------------------------------------------
# Reading the state
# ---------------------------------------------------------------------------------------------


class HelpersTest(RulesTestCase):
    """The state readers: ``get_value``, ``get_status``, ``get_envelope``, ``iter_fields`` and ``count_entities``."""

    def test_value_status_and_envelope_are_read_by_pointer(self) -> None:
        state = vehicle_state(vehicle_make=guessed("Honda"))
        self.assertEqual(eof.get_value(state, "/vehicles/0/vehicle_make"), "Honda")
        self.assertEqual(eof.get_status(state, "/vehicles/0/vehicle_make"), "inferred")
        self.assertEqual(eof.get_envelope(state, "/vehicles/0/vehicle_make")["source"], "chat")

    def test_anything_that_does_not_resolve_gives_none(self) -> None:
        state = vehicle_state(vehicle_make=envelope("Honda"))
        for pointer in [
            "/vehicles/1/vehicle_make",
            "/vehicles/0/nope",
            "/nope/0/vehicle_make",
            "/vehicles/0",  # an entity, not a field envelope
            "/vehicles",
            "vehicles/0/vehicle_make",  # no leading slash
            "/vehicles/00/vehicle_make",  # leading zero is not an array index
            "/vehicles/-1/vehicle_make",
            "/vehicles/x/vehicle_make",
            "/vehicles/0/vehicle_make/value/deeper",
            "",
            "/",
        ]:
            with self.subTest(pointer=pointer):
                self.assertIsNone(eof.get_envelope(state, pointer))
                self.assertIsNone(eof.get_value(state, pointer))
                self.assertIsNone(eof.get_status(state, pointer))

    def test_pointer_escapes_follow_rfc_6901(self) -> None:
        state = {"a/b": {"value": 1}, "c~d": {"value": 2}, "~1": {"value": 3}}
        self.assertEqual(eof.get_value(state, "/a~1b"), 1)
        self.assertEqual(eof.get_value(state, "/c~0d"), 2)
        self.assertEqual(eof.get_value(state, "/~01"), 3)

    def test_a_cleared_value_reads_as_none_but_keeps_its_status(self) -> None:
        state = vehicle_state(vehicle_make=envelope(None, status="inferred"))
        self.assertIsNone(eof.get_value(state, "/vehicles/0/vehicle_make"))
        self.assertEqual(eof.get_status(state, "/vehicles/0/vehicle_make"), "inferred")

    def test_a_status_that_is_not_a_string_reads_as_none(self) -> None:
        self.assertIsNone(
            eof.get_status(vehicle_state(vehicle_make={"value": "x", "status": 7}), "/vehicles/0/vehicle_make")
        )

    def test_iter_fields_walks_a_collection_and_skips_empty_fields(self) -> None:
        state = make_state(
            vehicles=[
                {"vehicle_make": envelope("Honda")},
                {},
                {"vehicle_make": envelope(None)},
                {"vehicle_make": envelope("Fiat")},
            ]
        )
        self.assertEqual(
            list(eof.iter_fields(state, "vehicles", "vehicle_make")),
            [("/vehicles/0/vehicle_make", "Honda"), ("/vehicles/3/vehicle_make", "Fiat")],
        )
        self.assertEqual(list(eof.iter_fields(state, "drivers", "driver_name")), [])
        self.assertEqual(list(eof.iter_fields({}, "vehicles", "vehicle_make")), [])

    def test_count_entities(self) -> None:
        self.assertEqual(eof.count_entities(make_state(vehicles=[{}, {}]), "vehicles"), 2)
        self.assertEqual(eof.count_entities({}, "vehicles"), 0)
        self.assertEqual(eof.count_entities({"vehicles": "ab"}, "vehicles"), 0)


# ---------------------------------------------------------------------------------------------
# RuleHit
# ---------------------------------------------------------------------------------------------


class RuleHitTest(RulesTestCase):
    """``RuleHit``: the reason by language and the ``rules.hit`` event payload, which leaves the values out."""

    def hit(self) -> eof.RuleHit:
        """The STOP hit of a vehicle with farm use."""
        return self.check(vehicle_state(usage_type=envelope("farm"))).stop

    def test_the_reason_follows_the_language(self) -> None:
        hit = self.hit()
        rule = DEFAULT_RULES.get(FARM)
        self.assertEqual(hit.reason("fr"), rule.reason_fr)
        self.assertEqual(hit.reason("en"), rule.reason_en)
        self.assertEqual(hit.reason("fr-CA"), rule.reason_fr)
        self.assertEqual(hit.reason("en_CA"), rule.reason_en)
        self.assertEqual(hit.reason("EN"), rule.reason_en)

    def test_an_unknown_language_gets_french(self) -> None:
        hit = self.hit()
        for language in ["es", "", "zz-ZZ"]:
            self.assertEqual(hit.reason(language), DEFAULT_RULES.get(FARM).reason_fr)
        self.assertEqual(hit.reason(), DEFAULT_RULES.get(FARM).reason_fr)

    def test_event_data_is_the_rules_hit_payload(self) -> None:
        data = self.hit().event_data("en")
        self.assertEqual(
            data,
            {
                "rule_id": FARM,
                "severity": "stop",
                "paths": ["/vehicles/0/usage_type"],
                "inferred": False,
                "reason": DEFAULT_RULES.get(FARM).reason_en,
            },
        )

    def test_event_data_leaves_the_matched_values_out(self) -> None:
        data = self.check(young_state()).hits[0].event_data("fr")
        self.assertNotIn("values", data)
        self.assertNotIn("2007-03-15", repr(data))
        self.assertEqual(data["severity"], "flag")

    def test_reasons_are_in_quebec_french_and_english(self) -> None:
        hit = self.hit()
        self.assertEqual(set(hit.reasons), {"fr", "en"})
        for rule in DEFAULT_RULES.rules:
            self.assertRegex(rule.reason_fr, r"[éèàùêôç]|soumission|équipe", msg=rule.id)  # reads as French
            self.assertIsNone(re.search(r"\b(the|your)\b", rule.reason_fr), msg=rule.id)  # no English left in it


# ---------------------------------------------------------------------------------------------
# The integration example (documentation that runs)
# ---------------------------------------------------------------------------------------------


class FakeSession:
    """The few BFF behaviours the example uses, recording what it was asked to do."""

    def __init__(self, language: str = "fr") -> None:
        """Start with nothing acknowledged, recorded or sent."""
        self.language = language
        self.acknowledged: set[str] = set()
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.system_notes: list[str] = []
        self.calls: list[str] = []
        self.signals: list[str] = []
        self.handoffs: list[dict[str, Any]] = []

    async def merge_and_persist(self, batch: Any) -> Any:
        """Return the batch as it is: in these tests it already is the merged state."""
        return batch  # in these tests the batch already is the merged state

    async def emit(self, event_type: str, data: dict[str, Any]) -> None:
        """Record the event."""
        self.events.append((event_type, data))

    async def ask_rule_confirmation(self, hit: eof.RuleHit) -> None:
        """Record the question."""
        self.calls.append(f"ask_rule_confirmation {hit.rule_id}")

    async def close_open_surfaces(self) -> None:
        """Record the call."""
        self.calls.append("close_open_surfaces")

    async def render_end_of_form(self, hit: eof.RuleHit) -> None:
        """Record the call."""
        self.calls.append(f"render_end_of_form {hit.rule_id}")

    def add_advisor_signal(self, hit: eof.RuleHit) -> None:
        """Record the advisor signal."""
        self.signals.append(hit.rule_id)

    async def create_handoff(self, **fields: Any) -> str:
        """Record the hand-off and return a fixed reference."""
        self.handoffs.append(fields)
        return "CNS-123456"


class IntegrationExampleTest(RulesTestCase):
    """The worked examples at the end of the rules module: ``example_commit`` and ``example_on_end_of_form_sent``."""

    def commit(self, session: FakeSession, state: dict[str, Any]) -> bool:
        """Run ``example_commit`` on a fake session and a state."""
        return asyncio.run(eof.example_commit(session, state))

    def test_a_clean_commit_lets_the_main_agent_run(self) -> None:
        session = FakeSession()
        self.assertTrue(self.commit(session, clean_state()))
        self.assertEqual((session.events, session.system_notes, session.calls, session.signals), ([], [], [], []))

    def test_a_flag_leaves_a_signal_once_and_the_quote_goes_on(self) -> None:
        session = FakeSession()
        self.assertTrue(self.commit(session, young_state()))
        self.assertEqual(
            [(kind, data["rule_id"], data["severity"]) for kind, data in session.events], [("rules.hit", YOUNG, "flag")]
        )
        self.assertEqual(session.system_notes, [f"Rule {YOUNG}: flag"])
        self.assertEqual(session.signals, [YOUNG])
        self.assertEqual(session.calls, [])  # no question, no end-of-form surface

        self.assertTrue(self.commit(session, young_state()))  # the next commit does not repeat it
        self.assertEqual(len(session.events), 1)
        self.assertEqual(len(session.system_notes), 1)
        self.assertEqual(session.signals, [YOUNG])

    def test_an_inferred_stop_asks_one_question_and_stops_once_confirmed(self) -> None:
        session = FakeSession()

        self.assertFalse(self.commit(session, vehicle_state(vehicle_make=guessed("Ferrari"))))
        self.assertEqual(session.calls, [f"ask_rule_confirmation {EXOTIC}"])
        self.assertTrue(session.events[0][1]["inferred"])

        self.assertTrue(self.commit(session, vehicle_state(vehicle_make=guessed("Ferrari"))))  # not asked twice
        self.assertEqual(session.calls, [f"ask_rule_confirmation {EXOTIC}"])

        self.assertFalse(self.commit(session, vehicle_state(vehicle_make=envelope("Ferrari"))))  # the customer said yes
        self.assertEqual(session.calls[1:], ["close_open_surfaces", f"render_end_of_form {EXOTIC}"])
        self.assertFalse(session.events[-1][1]["inferred"])

    def test_a_firm_stop_closes_the_surfaces_and_renders_the_end_of_form(self) -> None:
        session = FakeSession(language="en")
        self.assertFalse(self.commit(session, vehicle_state(usage_type=envelope("farm"))))
        self.assertEqual(session.calls, ["close_open_surfaces", f"render_end_of_form {FARM}"])
        kind, data = session.events[0]
        self.assertEqual(kind, "rules.hit")
        self.assertEqual(data["reason"], DEFAULT_RULES.get(FARM).reason_en)
        self.assertEqual(set(data), {"rule_id", "severity", "paths", "inferred", "reason"})
        self.assertEqual(session.system_notes, [f"Rule {FARM}: stop"])

    def test_a_stop_and_a_flag_in_one_batch_are_both_reported_stop_first(self) -> None:
        session = FakeSession()
        state = {**young_state(), "vehicles": [{"vehicle_make": envelope("Honda"), "usage_type": envelope("farm")}]}
        self.assertFalse(self.commit(session, state))
        self.assertEqual([data["rule_id"] for _, data in session.events], [FARM, YOUNG])
        self.assertEqual(session.signals, [YOUNG])  # the advisor still hears about the flag
        self.assertEqual(session.calls, ["close_open_surfaces", f"render_end_of_form {FARM}"])
        self.assertTrue(self.commit(session, state))  # nothing more to handle: edits on the end-of-form surface go on
        self.assertEqual(len(session.events), 2)

    def test_sending_the_end_of_form_creates_the_handoff(self) -> None:
        session = FakeSession()
        form = SimpleNamespace(
            rule_id=FARM,
            summary="Farm truck, 2 drivers.",
            contact={"email": "marie@example.com", "phone_number": "(514) 555-0142"},
            consent_to_transmit=True,
        )
        reference = asyncio.run(eof.example_on_end_of_form_sent(session, form))
        self.assertRegex(reference, r"^CNS-\d{6}$")
        self.assertEqual(session.events, [("handoff.created", {"reference": reference, "rule_id": FARM})])
        self.assertEqual(
            session.handoffs, [{"rule_id": FARM, "summary": "Farm truck, 2 drivers.", "contact": form.contact}]
        )

    def test_a_customer_request_has_no_rule_id(self) -> None:
        # "Parler à un conseiller": the same surface, the reason "customer request", no rule id.
        session = FakeSession()
        form = SimpleNamespace(
            rule_id=None, summary="s", contact={"email": "marie@example.com"}, consent_to_transmit=True
        )
        reference = asyncio.run(eof.example_on_end_of_form_sent(session, form))
        self.assertEqual(session.events, [("handoff.created", {"reference": reference, "rule_id": None})])

    def test_nothing_is_sent_without_the_consent_to_transmit(self) -> None:
        session = FakeSession()
        form = SimpleNamespace(rule_id=FARM, summary="s", contact={}, consent_to_transmit=False)
        self.assertIsNone(asyncio.run(eof.example_on_end_of_form_sent(session, form)))
        self.assertEqual((session.events, session.handoffs), ([], []))


if __name__ == "__main__":
    unittest.main()
