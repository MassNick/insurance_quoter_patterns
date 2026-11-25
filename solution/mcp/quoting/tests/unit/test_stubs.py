"""The stub adapters. Prices are checked against demo/src/journey.js `rate()` run at 2026 (see GOLDEN)."""

import pytest
from helpers import today, with_check_digit

from mcp_quoting.adapters import (
    InMemoryQuoteStore,
    StubAddressService,
    StubRatingEngine,
    StubStateReader,
    StubVehicleCatalog,
    StubVinDecoder,
)
from mcp_quoting.adapters.base import SessionNotFound, norm

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

ALL = ("essential", "plus", "premium")
SUV = {"vehicle_type": "suv", "vehicle_year": 2021, "usage_type": "commute", "annual_kilometers": 15000}
NO_HISTORY = {"has_claims": False, "has_convictions_in_past_three_years": False}


def offers(result):
    """The offers as `(type, annual, monthly, deductible)` tuples, so a test compares them in one line."""
    return [(o.offer_type, o.annual, o.monthly, o.deductible) for o in result]


# --------------------------------------------------------------------------- vehicle catalog
async def test_catalog_has_the_lamborghinis_and_filters_by_year():
    catalog = StubVehicleCatalog(today)
    assert "Lamborghini" in await catalog.makes()
    assert await catalog.models("Lamborghini", 2020) == ["Huracán", "Urus"]
    assert await catalog.models("Lamborghini", 2016) == ["Huracán"]  # the Urus starts in 2018
    assert "Tesla" not in await catalog.makes(2010) and "Toyota" in await catalog.makes(2010)
    assert await catalog.makes(2028) == [] and await catalog.makes(1995) == []  # after next year, before the catalog


async def test_catalog_years_run_from_next_year_to_1990():
    years = await StubVehicleCatalog(today).years()
    assert years[0] == 2027 and years[-1] == 1990 and years == sorted(years, reverse=True)


async def test_vehicle_type_is_forgiving_and_guesses_low_for_unknown_models():
    catalog = StubVehicleCatalog(today)
    assert await catalog.vehicle_type("lamborghini", "HURACAN", 2020) == {
        "vehicle_type": "car",
        "body_style": "coupe",
        "confidence": 0.98,
    }
    assert (await catalog.vehicle_type("Toyota", "Sienna"))["vehicle_type"] == "minivan"
    assert (await catalog.vehicle_type("Harley-Davidson", "Sportster"))["vehicle_type"] == "motorcycle"
    assert await catalog.vehicle_type("Toyota", "Zoomer") == {
        "vehicle_type": "car",
        "body_style": None,
        "confidence": 0.6,
    }


def test_norm_ignores_case_accents_and_punctuation():
    assert norm("Huracán") == norm("huracan") == norm("HURA-CAN") and norm("CR-V") == norm("crv")


# --------------------------------------------------------------------------- address
async def test_address_candidates_are_the_street_under_three_cities():
    found = await StubAddressService().candidates("125, avenue des pins")
    assert [c["city"] for c in found] == ["Laval", "Montréal", "Québec"]
    assert found[0] == {
        "address_line1": "125 Avenue Des Pins",
        "city": "Laval",
        "province_code": "QC",
        "postal_code": "H7N 1A1",
        "confidence": 0.93,
    }


async def test_address_adds_a_street_type_and_a_default_street():
    assert (await StubAddressService().candidates("12 érables"))[0]["address_line1"] == "12 Rue Érables"
    assert (await StubAddressService().candidates("H2X 1Y4"))[0]["address_line1"] == "125 Rue Des Érables"


async def test_a_postal_code_moves_its_area_first():
    for kwargs, query in (({"postal_code": "h2x1y4"}, "5 rue x"), ({}, "5 rue x H2X 1Y4")):
        found = await StubAddressService().candidates(query, **kwargs)
        assert found[0]["city"] == "Montréal" and "H2X" not in found[0]["address_line1"]
    assert (await StubAddressService().candidates("5 rue x", "G1R 2T3"))[0]["city"] == "Québec"


# --------------------------------------------------------------------------- VIN
async def test_vin_decode_reads_year_and_maker():
    vin = with_check_digit("2T3W1RFV0MC123456")
    assert await StubVinDecoder(today).decode(vin) == {
        "valid_checksum": True,
        "year": 2021,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "Base",
    }
    huracan = await StubVinDecoder(today).decode(with_check_digit("ZHWUC1ZF0LLA12345"))
    assert (huracan["year"], huracan["make"], huracan["model"]) == (2020, "Lamborghini", "Huracán")


async def test_vin_decode_rejects_a_bad_check_digit():
    vin = with_check_digit("2T3W1RFV0MC123456")
    bad = vin[:8] + ("0" if vin[8] != "0" else "1") + vin[9:]
    assert await StubVinDecoder(today).decode(bad) == {"valid_checksum": False}


async def test_vin_decode_falls_back_like_the_prototype():
    unknown_maker_and_year_code = with_check_digit("9ZZ000000U0000000")  # no such maker, and 'U' is no model year code
    out = await StubVinDecoder(today).decode(unknown_maker_and_year_code)
    assert out["valid_checksum"] and (out["make"], out["model"]) == ("Toyota", "RAV4") and 2019 <= out["year"] <= 2023


# --------------------------------------------------------------------------- rating
GOLDEN = {  # (offer type, annual, monthly, deductible), from rate() in demo/src/journey.js with the year pinned to 2026
    "one vehicle": [("essential", 896, 74.67, 1000), ("plus", 1160, 96.67, 500), ("premium", 1529, 127.42, 250)],
    "one vehicle, deductible 1000": [
        ("essential", 896, 74.67, 1000),
        ("plus", 1067, 88.92, 1000),
        ("premium", 1291, 107.58, 1000),
    ],
    "two vehicles, young driver, claims, six years with insurer": [
        ("essential", 2263, 188.58, 1000),
        ("plus", 2929, 244.08, 500),
        ("premium", 3863, 321.92, 250),
    ],
    "same, deductible 1000 and two add-ons": [
        ("essential", 2425, 202.08, 1000),
        ("plus", 2856, 238.0, 1000),
        ("premium", 3422, 285.17, 1000),
    ],
}
TWO_VEHICLES = {
    "vehicles": [
        SUV,
        {"vehicle_type": "car", "vehicle_year": 2015, "usage_type": "pleasure", "annual_kilometers": 15000},
    ],
    "drivers": [{"years_with_current_insurer": 6}, {"driver_date_of_birth": "2003-05-02"}],
    "underwriting": {"has_claims": True, "has_convictions_in_past_three_years": True},
}


async def test_prices_match_the_prototype():
    engine = StubRatingEngine(today)
    one = {"vehicles": [SUV], "drivers": [{}], "underwriting": NO_HISTORY}
    assert offers(await engine.price(one, ALL)) == GOLDEN["one vehicle"]
    chosen = {**one, "underwriting": {**NO_HISTORY, "deductible": "1000"}}
    assert offers(await engine.price(chosen, ALL)) == GOLDEN["one vehicle, deductible 1000"]
    assert (
        offers(await engine.price(TWO_VEHICLES, ALL))
        == GOLDEN["two vehicles, young driver, claims, six years with insurer"]
    )
    options = {
        **TWO_VEHICLES,
        "underwriting": {
            **TWO_VEHICLES["underwriting"],
            "deductible": "1000",
            "add_ons": ["roadside", "accident_forgiveness"],
        },
    }
    assert offers(await engine.price(options, ALL)) == GOLDEN["same, deductible 1000 and two add-ons"]


async def test_offers_carry_the_french_coverages_and_honour_the_requested_types():
    engine = StubRatingEngine(today)
    plus, premium = await engine.price({"vehicles": [SUV], "drivers": [{}]}, ["premium", "plus"])
    assert plus.offer_type == "plus" and premium.offer_type == "premium"  # catalog order, not request order
    assert "Assistance routière" in plus.coverages and "Pardon d’accident" in premium.coverages
    assert len(await engine.price({}, ["essential"])) == 1


async def test_each_pricing_factor_moves_the_price_the_expected_way():
    engine = StubRatingEngine(today)

    async def plus(vehicle=None, drivers=None, underwriting=None):
        """The annual price of the plus offer for the base case, with the given parts of it replaced."""
        snapshot = {
            "vehicles": [{**SUV, **(vehicle or {})}],
            "drivers": drivers or [{}],
            "underwriting": {**NO_HISTORY, **(underwriting or {})},
        }
        return (await engine.price(snapshot, ["plus"]))[0].annual

    base = await plus()
    assert await plus({"vehicle_type": "car"}) < base < await plus({"vehicle_type": "pickup"})
    assert await plus({"usage_type": "pleasure"}) < base < await plus({"usage_type": "business"})
    assert await plus({"vehicle_year": 2010}) < base  # an older vehicle is worth less to insure
    assert abs(await plus(underwriting={"has_claims": True}) - base * 1.18) <= 1
    assert abs(await plus(underwriting={"has_convictions_in_past_three_years": True}) - base * 1.12) <= 1
    assert await plus(drivers=[{"years_with_current_insurer": 20}]) == pytest.approx(
        base * 0.9, abs=1
    )  # the discount stops at 10 %
    assert await plus(drivers=[{}, {"driver_date_of_birth": "2003-05-02"}]) == pytest.approx(base * 1.28, abs=1)
    assert await plus(drivers=[{}, {"driver_date_of_birth": "1990-05-02"}]) == base  # only under 25 counts
    assert await plus(underwriting={"deductible": "2500"}) < await plus(underwriting={"deductible": "250"})
    assert await plus(underwriting={"add_ons": ["rental_car"]}) == base + 72  # 6 $ a month, charged on every offer


async def test_kilometres_scale_the_price_with_a_cap():
    engine = StubRatingEngine(today)

    async def annual(km):
        """The annual price of the plus offer for a vehicle driven `km` kilometres a year."""
        return (await engine.price({"vehicles": [{**SUV, "annual_kilometers": km}], "drivers": [{}]}, ["plus"]))[
            0
        ].annual

    assert await annual(0) < await annual(15000) < await annual(30000) < await annual(37500)
    assert await annual(37500) == await annual(100000)  # capped at +15 %
    no_km = {key: value for key, value in SUV.items() if key != "annual_kilometers"}
    assert (await engine.price({"vehicles": [no_km], "drivers": [{}]}, ["plus"]))[0].annual == await annual(
        15000
    )  # neutral


# --------------------------------------------------------------------------- state reader and quote store
async def test_stub_state_reader_returns_states_and_raises_for_unknown_sessions():
    reader = StubStateReader({"s_1": {"quote": {}}})
    assert await reader.read("s_1", "ref") == {"quote": {}}
    with pytest.raises(SessionNotFound):
        await reader.read("s_2", "ref")


async def test_quote_store_numbers_versions_per_quote_and_session():
    store = InMemoryQuoteStore()
    first = await store.save("s_1", None, {"offers": ["v1"]})
    assert first["quote_id"].startswith("Q-") and first["version"] == 1
    second = await store.save("s_1", first["quote_id"], {"offers": ["v2"]})
    assert (second["quote_id"], second["version"]) == (first["quote_id"], 2)
    assert (await store.latest("s_1", first["quote_id"]))["offers"] == ["v2"]
    assert await store.latest("s_2", first["quote_id"]) is None  # another session cannot see it
    assert await store.latest("s_1", "Q-NOPE") is None
    other = await store.save("s_1", None, {"offers": []})
    assert other["quote_id"] != first["quote_id"] and other["version"] == 1
