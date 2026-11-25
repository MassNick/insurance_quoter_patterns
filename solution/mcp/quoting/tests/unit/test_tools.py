"""The tool handlers on stub adapters: lookups, the session_ref guard, the missing-field refusal, create and modify."""

import pytest
from helpers import KEY, SESSION, env, make_deps, make_state, ref
from quote_common import session_ref

from mcp_quoting.adapters import SessionNotFound
from mcp_quoting.state import apply_options, input_hash, snapshot
from mcp_quoting.tools import HANDLERS, OPTIONS, ToolFailure, create_quote

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


async def call(name, deps, **args):
    """Run the handler of tool `name` with `args` as its arguments."""
    return await HANDLERS[name](deps, args)


async def fails(name, deps, **args):
    """Run the handler, expect a `ToolFailure` and return it."""
    with pytest.raises(ToolFailure) as caught:
        await call(name, deps, **args)
    return caught.value


# --------------------------------------------------------------------------- lookups
async def test_lookup_validates_and_normalizes_whatever_spelling():
    out = await call("lookup_make_model_year", make_deps(), year=2020, make="lamborghini", model="HURACAN")
    assert out == {"valid": True, "normalized": {"year": 2020, "make": "Lamborghini", "model": "Huracán"}}


async def test_lookup_returns_options_for_what_is_missing():
    deps = make_deps()
    nothing = await call("lookup_make_model_year", deps)
    assert nothing["valid"] is False and nothing["years"][0] == 2027 and "Toyota" in nothing["makes"]
    assert nothing["normalized"] == {"year": None, "make": None, "model": None}
    only_make = await call("lookup_make_model_year", deps, make="Honda")
    assert only_make["models"] == ["Civic", "Accord", "CR-V", "HR-V", "Odyssey"] and "makes" not in only_make
    year_and_make = await call("lookup_make_model_year", deps, year=2010, make="Honda")
    assert year_and_make["models"] == ["Civic", "Accord", "CR-V", "Odyssey"]  # the HR-V starts in 2016
    unknown_make = await call("lookup_make_model_year", deps, year=2020, make="Lambo")
    assert unknown_make["normalized"]["make"] is None and "Lamborghini" in unknown_make["makes"]


async def test_lookup_rejects_a_model_that_did_not_exist_that_year():
    out = await call("lookup_make_model_year", make_deps(), year=2016, make="Lamborghini", model="Urus")
    assert out["valid"] is False and out["normalized"]["model"] is None and out["models"] == ["Huracán"]
    wrong_year = await call("lookup_make_model_year", make_deps(), year=1800, make="Toyota", model="Corolla")
    assert wrong_year["valid"] is False and wrong_year["normalized"]["year"] is None


async def test_vehicle_type_vin_and_address_pass_through_to_their_adapters():
    deps = make_deps()
    assert (await call("identify_vehicle_type", deps, make="Toyota", model="RAV4", year=2021))["vehicle_type"] == "suv"
    assert (await call("decode_vin", deps, vin="11111111111111111"))["valid_checksum"] is True
    assert await call("decode_vin", deps, vin="11111111211111111") == {"valid_checksum": False}
    found = await call("identify_address", deps, query="125 rue des érables")
    assert len(found["candidates"]) == 3


async def test_address_candidates_are_capped_at_five():
    class Many:
        """An address service that finds eight candidates."""

        async def candidates(self, query, postal_code=None):
            """Eight candidates, numbered from 0."""
            return [{"address_line1": str(i)} for i in range(8)]

    out = await call("identify_address", make_deps(addresses=Many()), query="x")
    assert [c["address_line1"] for c in out["candidates"]] == ["0", "1", "2", "3", "4"]


# --------------------------------------------------------------------------- session_ref guard
async def test_a_forged_unsigned_or_missing_session_ref_is_refused():
    deps = make_deps()
    forged = session_ref.sign(SESSION, b"another-key")
    payload, signature = ref().split(".")
    tampered = f"{payload}.{'B' if signature[0] != 'B' else 'C'}{signature[1:]}"
    for bad in (forged, tampered, "garbage", ""):
        failure = await fails("create_quote", deps, session_ref=bad)
        assert failure.code == "invalid_session_ref"
    assert (await fails("create_quote", deps)).code == "invalid_session_ref"  # not passed at all


async def test_an_expired_session_ref_is_refused():
    expired = session_ref.sign(SESSION, KEY, ttl_seconds=60, now=1000)
    failure = await fails("modify_quote", make_deps(), session_ref=expired, quote_id="Q-1", changes={})
    assert failure.code == "invalid_session_ref" and "expired" in str(failure)


async def test_the_session_comes_from_the_signature_not_the_arguments():
    deps = make_deps()
    deps.state.states["s_other"] = make_state({"/vehicles/0/vehicle_year": 2000})
    out = await create_quote(
        deps, {"session_ref": ref(), "session_id": "s_other", "state": {"x": 1}, "vehicle_year": 1999}
    )
    again = await create_quote(deps, {"session_ref": ref("s_other")})
    assert out["offers"] != again["offers"]  # each ref priced its own session; the extra arguments changed nothing
    plain = await create_quote(make_deps(), {"session_ref": ref()})
    assert out["offers"] == plain["offers"] and out["input_hash"] == plain["input_hash"]


async def test_an_unknown_session_is_reported():
    with pytest.raises(SessionNotFound):
        await create_quote(make_deps(), {"session_ref": ref("s_unknown")})


# --------------------------------------------------------------------------- the readiness refusal
async def test_create_quote_refuses_when_required_fields_are_missing():
    deps = make_deps(make_state({"/vehicles/0/vehicle_year": None, "/quote/email": None}))
    failure = await fails("create_quote", deps, session_ref=ref())
    assert failure.code == "missing_fields" and "2 required" in str(failure)
    assert [(m["path"], m["reason"]) for m in failure.extra["missing_fields"]] == [
        ("/quote/email", "missing"),
        ("/vehicles/0/vehicle_year", "missing"),
    ]
    assert await deps.quotes.latest(SESSION, "anything") is None  # nothing was stored


async def test_a_pending_deterministic_field_blocks_the_quote():
    deps = make_deps(make_state({"/underwriting/has_claims": env(True, status="pending_confirmation", source="chat")}))
    failure = await fails("create_quote", deps, session_ref=ref())
    assert [(m["key"], m["reason"]) for m in failure.extra["missing_fields"]] == [
        ("has_claims", "pending_confirmation")
    ]


def chat(value, confidence):
    """A field envelope inferred from chat at `confidence`."""
    return env(value, status="inferred", source="chat", confidence=confidence)


async def reasons(deps):
    """The `(key, reason)` pairs of the missing fields that make `create_quote` refuse."""
    failure = await fails("create_quote", deps, session_ref=ref())
    return [(m["key"], m["reason"]) for m in failure.extra["missing_fields"]]


async def test_a_chat_value_below_confidence_0_6_is_refused():
    deps = make_deps(make_state({"/vehicles/0/usage_type": chat("business", 0.5)}))
    assert await reasons(deps) == [("usage_type", "inferred")]


async def test_an_inferred_deterministic_field_is_refused_even_when_sure():
    deps = make_deps(make_state({"/underwriting/has_claims": chat(False, 0.95)}))
    assert await reasons(deps) == [("has_claims", "inferred")]


async def test_a_chat_value_at_confidence_0_9_is_priced_like_a_confirmed_one():
    inferred_state = make_state({"/vehicles/0/usage_type": chat("business", 0.9)})
    inferred = await call("create_quote", make_deps(inferred_state), session_ref=ref())
    confirmed = await call(
        "create_quote", make_deps(make_state({"/vehicles/0/usage_type": "business"})), session_ref=ref()
    )
    commute = await call("create_quote", make_deps(), session_ref=ref())
    assert inferred["offers"] == confirmed["offers"] != commute["offers"]  # the inferred value is the one priced
    assert inferred["input_hash"] == confirmed["input_hash"]  # the hash covers values, not status or confidence


# --------------------------------------------------------------------------- the end-of-form STOP block
def blocked_state(changes=None):
    """The default state, plus `changes`, marked as blocked by an end-of-form STOP rule."""
    return {**make_state(changes), "quote_blocked": True}


async def test_create_quote_refuses_while_the_session_is_blocked():
    deps = make_deps(blocked_state())  # a complete state: nothing is missing, a STOP rule is active
    failure = await fails("create_quote", deps, session_ref=ref())
    assert failure.code == "quote_blocked" and "STOP" in str(failure) and failure.extra == {}
    assert await deps.quotes.latest(SESSION, "anything") is None  # nothing was priced or stored


async def test_the_block_comes_before_the_missing_fields():
    deps = make_deps(blocked_state({"/quote/email": None}))
    assert (await fails("create_quote", deps, session_ref=ref())).code == "quote_blocked"


async def test_modify_quote_refuses_while_blocked_and_works_again_once_the_stop_is_lifted():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    deps.state.states[SESSION] = blocked_state()
    failure = await fails("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 500})
    assert failure.code == "quote_blocked"
    assert (await deps.quotes.latest(SESSION, v1["quote_id"]))["version"] == 1
    deps.state.states[SESSION] = {**make_state(), "quote_blocked": False}  # the commit that cleared the hit
    v2 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 500})
    assert v2["version"] == 2


async def test_the_flag_does_not_change_the_price_or_the_hash():
    plain = await call("create_quote", make_deps(), session_ref=ref())
    flagged = await call("create_quote", make_deps({**make_state(), "quote_blocked": False}), session_ref=ref())
    assert flagged["offers"] == plain["offers"] and flagged["input_hash"] == plain["input_hash"]


# --------------------------------------------------------------------------- create and modify
async def test_create_quote_prices_the_committed_state():
    deps = make_deps()
    out = await call("create_quote", deps, session_ref=ref())
    assert list(out) == ["quote_id", "version", "offers", "input_hash"]
    assert out["quote_id"].startswith("Q-") and out["version"] == 1
    assert [(o["offer_type"], o["annual"], o["monthly"], o["deductible"]) for o in out["offers"]] == [
        ("essential", 896, 74.67, 1000),
        ("plus", 1160, 96.67, 500),
        ("premium", 1529, 127.42, 250),
    ]
    assert set(out["offers"][0]) == {"offer_type", "monthly", "annual", "deductible", "coverages"}
    assert out["input_hash"] == input_hash(snapshot(make_state()))  # FR-QUO-3: recomputable from the committed state


async def test_create_quote_can_price_only_some_offers():
    out = await call("create_quote", make_deps(), session_ref=ref(), offer_types=["plus"])
    assert [o["offer_type"] for o in out["offers"]] == ["plus"]


async def test_modify_quote_adds_a_version_with_the_monthly_delta():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    v2 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 1000})
    assert list(v2) == ["quote_id", "version", "previous_version", "delta_monthly", "offers", "input_hash"]
    assert (v2["quote_id"], v2["version"], v2["previous_version"]) == (v1["quote_id"], 2, 1)
    assert v2["delta_monthly"] == -7.75  # plus, the default selection: 96.67 -> 88.92
    assert [o["deductible"] for o in v2["offers"]] == [1000, 1000, 1000]
    assert (
        v2["input_hash"] == input_hash(apply_options(snapshot(make_state()), {"deductible": 1000})) != v1["input_hash"]
    )
    assert (await deps.quotes.latest(SESSION, v1["quote_id"]))["version"] == 2


async def test_options_accumulate_across_versions_and_the_state_is_read_again():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 1000})
    v3 = await call(
        "modify_quote",
        deps,
        session_ref=ref(),
        quote_id=v1["quote_id"],
        changes={"add_ons": ["roadside"], "annual_kilometers": 5000},
    )
    assert v3["version"] == 3 and v3["previous_version"] == 2
    assert {o["deductible"] for o in v3["offers"]} == {1000}  # the deductible of version 2 is kept
    deps.state.states[SESSION] = make_state({"/vehicles/0/vehicle_year": 2012})
    v4 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={})
    assert v4["offers"] != v3["offers"] and {o["deductible"] for o in v4["offers"]} == {
        1000
    }  # an older vehicle, same options


async def test_switching_the_offer_type_compares_the_two_selected_offers():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    v2 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"offer_type": "premium"})
    plus_before = next(o for o in v1["offers"] if o["offer_type"] == "plus")["monthly"]
    premium_now = next(o for o in v2["offers"] if o["offer_type"] == "premium")["monthly"]
    assert v2["delta_monthly"] == round(premium_now - plus_before, 2) > 0
    v3 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 2500})
    assert v3["delta_monthly"] < 0  # still on premium: the selection is kept


async def test_the_delta_falls_back_to_the_first_offer_when_the_selection_was_not_priced():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref(), offer_types=["essential"])
    v2 = await call("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 2500})
    assert len(v2["offers"]) == 1 and v2["delta_monthly"] < 0


async def test_modify_quote_needs_a_quote_of_this_session():
    deps = make_deps()
    deps.state.states["s_2"] = make_state()
    first = await call("create_quote", deps, session_ref=ref())
    assert (await fails("modify_quote", deps, session_ref=ref(), quote_id="Q-NOPE", changes={})).code == "unknown_quote"
    assert (
        await fails("modify_quote", deps, session_ref=ref("s_2"), quote_id=first["quote_id"], changes={})
    ).code == "unknown_quote"


async def test_modify_quote_refuses_a_state_that_stopped_being_quotable():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    deps.state.states[SESSION] = make_state(
        {"/underwriting/has_claims": env(True, status="pending_confirmation", source="chat")}
    )
    failure = await fails("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes={"deductible": 500})
    assert failure.code == "missing_fields"
    assert (await deps.quotes.latest(SESSION, v1["quote_id"]))["version"] == 1


async def test_changes_cannot_carry_field_values_or_values_the_catalog_forbids():
    deps = make_deps()
    v1 = await call("create_quote", deps, session_ref=ref())
    for changes in (
        {"first_name": "Mallory"},
        {"vehicle_year": 1950},
        {"has_claims": False},
        {"deductible": 999},
        {"annual_kilometers": -1},
        {"annual_kilometers": 100001},
    ):
        failure = await fails("modify_quote", deps, session_ref=ref(), quote_id=v1["quote_id"], changes=changes)
        assert failure.code == "invalid_changes"
    assert (await deps.quotes.latest(SESSION, v1["quote_id"]))["version"] == 1


async def test_a_rating_engine_without_offers_is_an_error():
    class Empty:
        """A rating engine that returns no offers."""

        async def price(self, snapshot, offer_types):
            """No offers, whatever the snapshot."""
            return []

    assert (await fails("create_quote", make_deps(rating=Empty()), session_ref=ref())).code == "no_offers"


def test_the_accepted_options_are_the_ones_in_tools_json():
    from quote_common.contracts import load_tools

    modify = next(t for t in load_tools()["tools"] if t["name"] == "quoting.modify_quote")
    assert set(OPTIONS) == set(modify["inputSchema"]["properties"]["changes"]["properties"])
