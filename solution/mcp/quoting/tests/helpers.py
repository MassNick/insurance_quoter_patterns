"""Shared test data: a complete committed session state, deps wired to stubs, and signed session refs."""

from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from quote_common import pointer, session_ref

from mcp_quoting.adapters import (
    Deps,
    InMemoryQuoteStore,
    StubAddressService,
    StubRatingEngine,
    StubStateReader,
    StubVehicleCatalog,
    StubVinDecoder,
)
from mcp_quoting.vin import check_digit

KEY = b"dev-only-test-key"  # signs the test session refs
SESSION = "s_1"  # the session that `make_deps` holds a state for


def today() -> date:
    """The fixed date of the tests: the clock of the stubs."""
    return date(2026, 11, 25)


def env(value: Any, status: str = "confirmed", source: str = "form", confidence: float | None = 1) -> dict[str, Any]:
    """A field envelope as the BFF stores it."""
    return {
        "value": value,
        "source": source,
        "confidence": confidence,
        "status": status,
        "turn": 1,
        "updated_at": "2026-11-25T12:00:00Z",
    }


# Every required_for_quote field of one driver on one vehicle (the prototype's single-vehicle journey).
BASE = {
    "/quote/first_name": "Marie",
    "/quote/last_name": "Tremblay",
    "/quote/date_of_birth": "1987-01-18",
    "/quote/email": "marie@example.test",
    "/quote/contact_consent": True,
    "/quote/effective_date": "2026-12-01",
    "/quote/address_line1": "125 Rue des Érables",
    "/quote/city": "Laval",
    "/quote/province_code": "QC",
    "/quote/postal_code": "H7N 1A1",
    "/vehicles/0/vehicle_type": "suv",
    "/vehicles/0/vehicle_make": "Toyota",
    "/vehicles/0/vehicle_model": "RAV4",
    "/vehicles/0/vehicle_year": 2021,
    "/vehicles/0/annual_kilometers": 15000,
    "/vehicles/0/usage_type": "commute",
    "/drivers/0/marital_status_code": "single",
    "/drivers/0/age_obtained_license": 18,
    "/underwriting/has_claims": False,
    "/underwriting/has_convictions_in_past_three_years": False,
}


def make_state(changes: dict[str, Any] | None = None) -> dict[str, Any]:
    """The session document: BASE plus `changes` (path -> value, wrapped as confirmed, or an `env(...)` envelope)."""
    state: dict[str, Any] = {}
    for path, value in {**BASE, **(changes or {})}.items():
        pointer.set(state, path, value if isinstance(value, dict) and "status" in value else env(value))
    return state


def make_deps(document: dict[str, Any] | None = None, **overrides: Any) -> Deps:
    """Deps on stubs; `document` is the committed state of SESSION, `overrides` replace Deps fields."""
    deps = Deps(
        rating=StubRatingEngine(today),
        catalog=StubVehicleCatalog(today),
        addresses=StubAddressService(),
        vins=StubVinDecoder(today),
        state=StubStateReader({SESSION: make_state() if document is None else document}),
        quotes=InMemoryQuoteStore(),
        key=KEY,
    )
    return dataclasses.replace(deps, **overrides)


def with_check_digit(vin: str) -> str:
    """Fix the 9th character of a 17-character VIN."""
    return vin[:8] + check_digit(vin) + vin[9:]


def ref(session_id: str = SESSION, **kwargs: Any) -> str:
    """A signed `session_ref` for `session_id`; `kwargs` go to `session_ref.sign` (`ttl_seconds`, `now`)."""
    return session_ref.sign(session_id, KEY, **kwargs)
