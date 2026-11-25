"""Stub adapters ported from the design prototype (demo/src/a2ui.js `makeServices`, demo/src/journey.js `rate`).

They make the whole stack run with no real system behind it. Their data and prices are demo fixtures, not rules.
`today` is injectable so tests and prices are deterministic.
"""

from __future__ import annotations

import math
import re
import secrets
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any, NamedTuple

from mcp_quoting.vin import is_valid

from .base import Offer, SessionNotFound, norm

Today = Callable[[], date]  # the clock: returns today's date, so tests can fix it


# --------------------------------------------------------------------------- vehicle catalog
class Row(NamedTuple):
    """One model of the stub catalog."""

    model: str
    vehicle_type: str  # a `vehicle_type` enum value of the state catalog
    body_style: str
    since: int  # first model year


# Make -> its models: demo fixtures, not real data.
CATALOG: dict[str, list[Row]] = {
    "Toyota": [
        Row("Corolla", "car", "sedan", 2000),
        Row("Camry", "car", "sedan", 2000),
        Row("RAV4", "suv", "suv", 2000),
        Row("Highlander", "suv", "suv", 2001),
        Row("Sienna", "minivan", "minivan", 2000),
        Row("Tacoma", "pickup", "pickup", 2000),
    ],
    "Honda": [
        Row("Civic", "car", "sedan", 2000),
        Row("Accord", "car", "sedan", 2000),
        Row("CR-V", "suv", "suv", 2000),
        Row("HR-V", "suv", "suv", 2016),
        Row("Odyssey", "minivan", "minivan", 2000),
    ],
    "Ford": [
        Row("F-150", "pickup", "pickup", 2000),
        Row("Escape", "suv", "suv", 2001),
        Row("Mustang", "car", "coupe", 2000),
        Row("Explorer", "suv", "suv", 2000),
    ],
    "Hyundai": [
        Row("Elantra", "car", "sedan", 2000),
        Row("Tucson", "suv", "suv", 2005),
        Row("Kona", "suv", "suv", 2018),
        Row("Ioniq 5", "suv", "suv", 2022),
    ],
    "Kia": [
        Row("Forte", "car", "sedan", 2010),
        Row("Sportage", "suv", "suv", 2000),
        Row("Soul", "car", "hatchback", 2010),
        Row("Carnival", "minivan", "minivan", 2022),
    ],
    "Mazda": [
        Row("Mazda3", "car", "sedan", 2004),
        Row("CX-5", "suv", "suv", 2013),
        Row("CX-30", "suv", "suv", 2020),
        Row("MX-5", "car", "roadster", 2000),
    ],
    "Subaru": [
        Row("Impreza", "car", "sedan", 2000),
        Row("Outback", "suv", "wagon", 2000),
        Row("Forester", "suv", "suv", 2000),
        Row("Crosstrek", "suv", "suv", 2013),
    ],
    "Tesla": [Row("Model 3", "car", "sedan", 2017), Row("Model Y", "suv", "suv", 2020)],
    "Chevrolet": [
        Row("Silverado", "pickup", "pickup", 2000),
        Row("Equinox", "suv", "suv", 2005),
        Row("Bolt EV", "car", "hatchback", 2017),
    ],
    "Volkswagen": [
        Row("Jetta", "car", "sedan", 2000),
        Row("Golf", "car", "hatchback", 2000),
        Row("Tiguan", "suv", "suv", 2009),
    ],
    "Nissan": [
        Row("Rogue", "suv", "suv", 2008),
        Row("Sentra", "car", "sedan", 2000),
        Row("Leaf", "car", "hatchback", 2011),
    ],
    "Lamborghini": [Row("Huracán", "car", "coupe", 2014), Row("Urus", "suv", "suv", 2018)],
    "Harley-Davidson": [
        Row("Street Glide", "motorcycle", "touring", 2006),
        Row("Sportster", "motorcycle", "cruiser", 2000),
    ],
}


class StubVehicleCatalog:
    """The `CATALOG` fixtures: a model is on sale from its first year to next year."""

    def __init__(self, today: Today = date.today) -> None:
        """`today` is the clock (tests fix it)."""
        self._today = today

    def _rows(self, make: str, year: int | None) -> list[Row]:
        """The models of `make` on sale in `year` (all of them when `year` is None); none for an unknown make."""
        latest = self._today().year + 1
        return [r for r in CATALOG.get(make, []) if year is None or r.since <= year <= latest]

    async def years(self) -> list[int]:
        """The model years from next year back to 1990."""
        return list(range(self._today().year + 1, 1989, -1))

    async def makes(self, year: int | None = None) -> list[str]:
        """The makes with a model on sale in `year`."""
        return [make for make in CATALOG if self._rows(make, year)]

    async def models(self, make: str, year: int | None = None) -> list[str]:
        """The models of `make` on sale in `year`."""
        return [r.model for r in self._rows(make, year)]

    async def vehicle_type(self, make: str, model: str, year: int | None = None) -> dict[str, Any]:
        """The type of a known make and model (any spelling), else a low-confidence `car`."""
        row = next(
            (r for m, rows in CATALOG.items() if norm(m) == norm(make) for r in rows if norm(r.model) == norm(model)),
            None,
        )
        if row is None:  # the prototype's low-confidence guess
            return {"vehicle_type": "car", "body_style": None, "confidence": 0.6}
        return {"vehicle_type": row.vehicle_type, "body_style": row.body_style, "confidence": 0.98}


# --------------------------------------------------------------------------- address
_POSTAL = re.compile(r"[A-Za-z]\d[A-Za-z][ -]?\d[A-Za-z]\d")  # a Canadian postal code, with a space, a hyphen or none
# The canned candidates: (city, postal code, confidence).
_CITIES = (("Laval", "H7N 1A1", 0.93), ("Montréal", "H2X 1Y4", 0.71), ("Québec", "G1R 2T3", 0.58))


class StubAddressService:
    """The prototype's `identifyAddress`: the typed street under three canned Quebec cities.

    A postal code, given or typed in the query, moves the candidates of the same forward sortation area first."""

    async def candidates(self, query: str, postal_code: str | None = None) -> list[dict[str, Any]]:
        """One candidate per canned city for the typed street (a default number and street when it has none)."""
        typed = _POSTAL.search(query)
        postal = re.sub(r"[ -]", "", (postal_code or (typed.group() if typed else "")).upper())
        rest = _POSTAL.sub(" ", query)
        number = (re.search(r"\d+", rest) or ["125"])[0]
        street = re.sub(",", " ", rest.replace(number, "", 1)).strip() or "Rue des Érables"
        street = " ".join(word[:1].upper() + word[1:] for word in street.split())
        if not re.match(r"(rue|avenue|boulevard|chemin|av|boul)\b", street, re.IGNORECASE):
            street = f"Rue {street}"
        found = [
            {
                "address_line1": f"{number} {street}",
                "city": city,
                "province_code": "QC",
                "postal_code": code,
                "confidence": confidence,
            }
            for city, code, confidence in _CITIES
        ]
        return sorted(found, key=lambda c: c["postal_code"].replace(" ", "")[:3] != postal[:3])


# --------------------------------------------------------------------------- VIN
# World manufacturer identifier (first 3 characters) -> (make, model) of the stub catalog.
_WMI = {
    "2T3": ("Toyota", "RAV4"),
    "2T1": ("Toyota", "Corolla"),
    "4T1": ("Toyota", "Camry"),
    "2HG": ("Honda", "Civic"),
    "2HK": ("Honda", "CR-V"),
    "1FA": ("Ford", "Mustang"),
    "1FT": ("Ford", "F-150"),
    "5YJ": ("Tesla", "Model 3"),
    "ZHW": ("Lamborghini", "Huracán"),
    "ZPB": ("Lamborghini", "Urus"),
}
_YEAR_CODES = "ABCDEFGHJKLMNPRSTVWXY123456789"  # 10th character: 1980 + index, repeating every 30 years


class StubVinDecoder:
    """Check digit, then year from the 10th character and make and model from the first three.

    Like the prototype, a valid VIN of an unknown maker decodes as a Toyota RAV4."""

    def __init__(self, today: Today = date.today) -> None:
        """`today` is the clock (tests fix it)."""
        self._today = today

    async def decode(self, vin: str) -> dict[str, Any]:
        """`{"valid_checksum": False}` for a bad check digit, else the decoded year, make, model and trim."""
        if not is_valid(vin):
            return {"valid_checksum": False}
        make, model = _WMI.get(vin[:3], ("Toyota", "RAV4"))
        year = None
        if vin[9] in _YEAR_CODES:
            first = 1980 + _YEAR_CODES.index(vin[9])
            year = next((y for y in (first + 60, first + 30, first) if y <= self._today().year + 1), None)
        return {
            "valid_checksum": True,
            "year": year or 2019 + sum(map(ord, vin)) % 5,
            "make": make,
            "model": model,
            "trim": "Base",
        }


# --------------------------------------------------------------------------- rating
# offer type -> (price factor, default deductible, coverages)
_OFFERS = {
    "essential": (
        0.84,
        1000,
        ["Responsabilité civile 1 M$", "Collision ou versement", "Risques autres que la collision"],
    ),
    "plus": (
        1.0,
        500,
        [
            "Responsabilité civile 2 M$",
            "Collision et autres risques",
            "Assistance routière",
            "Voiture de location jusqu’à 1 200 $",
        ],
    ),
    "premium": (
        1.21,
        250,
        [
            "Responsabilité civile 2 M$",
            "Remplacement à neuf (5 ans)",
            "Pardon d’accident",
            "Voiture de location jusqu’à 2 000 $",
        ],
    ),
}
_TYPE = {"car": 1, "suv": 1.06, "minivan": 1.02, "pickup": 1.1}  # price factor by vehicle type
_USAGE = {"pleasure": 0.95, "commute": 1.06, "business": 1.22, "farm": 0.9}  # price factor by usage
_DEDUCTIBLE = {250: 1.09, 500: 1, 1000: 0.92, 2500: 0.85}  # price factor by deductible amount
# Add-on -> what it adds to the monthly price, on every offer.
_ADD_ON_MONTHLY = {"roadside": 4.5, "rental_car": 6, "accident_forgiveness": 9, "new_vehicle_replacement": 11}
_KM_NEUTRAL = 15_000  # the kilometres per year that leave the price unchanged


def _age(iso: Any, today: date) -> int | None:
    """Whole years from the date in `iso` (its first ten characters) to `today`; None when it is not a date."""
    try:
        born = date.fromisoformat(str(iso)[:10])
    except ValueError:
        return None
    return today.year - born.year - ((today.month, today.day) < (born.month, born.day))


class StubRatingEngine:
    """The prototype's `rate()`: a base premium per vehicle (age, type, use), surcharges for young additional drivers,
    claims and convictions, a loyalty discount, then a factor per offer, deductible and add-ons.

    One addition: kilometres per year scale the premium (0.9 at 0 km, neutral at 15 000, at most 1.15), so changing
    them through `modify_quote` visibly moves the price. A deductible in the snapshot applies to all offers; without
    one each offer uses its own default. Every selected add-on is charged on every offer."""

    def __init__(self, today: Today = date.today) -> None:
        """`today` is the clock (tests fix it)."""
        self._today = today

    async def price(self, snapshot: dict[str, Any], offer_types: Sequence[str]) -> list[Offer]:
        """One offer per requested type, in catalog order (`_OFFERS`), priced from the snapshot as the class says."""
        today = self._today()
        underwriting = snapshot.get("underwriting") or {}
        vehicles = snapshot.get("vehicles") or [{}]
        drivers = snapshot.get("drivers") or [{}]
        total = 0.0
        for i, vehicle in enumerate(vehicles):
            age = today.year - (vehicle.get("vehicle_year") or 2018)
            premium = 780 + max(4000, 34000 - age * 2600) * 0.012
            km = vehicle.get("annual_kilometers")
            km_factor = 1.0 if km is None else min(1.15, max(0.9, 0.9 + 0.1 * km / _KM_NEUTRAL))
            premium *= (
                _TYPE.get(vehicle.get("vehicle_type"), 1)
                * _USAGE.get(vehicle.get("usage_type") or "commute", 1)
                * km_factor
            )
            total += premium if i == 0 else premium * 0.85
        for driver in drivers[1:]:
            age = _age(driver.get("driver_date_of_birth"), today)
            if age is not None and age < 25:
                total *= 1.28
        if underwriting.get("has_claims"):
            total *= 1.18
        if underwriting.get("has_convictions_in_past_three_years"):
            total *= 1.12
        total *= max(0.9, 1 - 0.01 * (drivers[0].get("years_with_current_insurer") or 0))
        chosen = int(underwriting["deductible"]) if underwriting.get("deductible") else None
        add_ons = sum(_ADD_ON_MONTHLY.get(a, 0) for a in underwriting.get("add_ons") or [])
        offers = []
        for offer_type, (factor, default_deductible, coverages) in _OFFERS.items():
            if offer_type in offer_types:
                deductible = chosen or default_deductible
                annual = math.floor(
                    total * factor * _DEDUCTIBLE.get(deductible, 1) + add_ons * 12 + 0.5
                )  # JS Math.round
                offers.append(
                    Offer(
                        offer_type=offer_type,
                        monthly=round(annual / 12, 2),
                        annual=annual,
                        deductible=deductible,
                        coverages=coverages,
                    )
                )
        return offers


# --------------------------------------------------------------------------- state and quotes
class StubStateReader:
    """Committed states held in memory, keyed by session id (tests and demos)."""

    def __init__(self, states: dict[str, dict[str, Any]] | None = None) -> None:
        """`states` maps a session id to its state; the dict is kept, not copied, so a test can change it later."""
        self.states = {} if states is None else states

    async def read(self, session_id: str, session_ref: str) -> dict[str, Any]:
        """The state of `session_id`, or `SessionNotFound`; `session_ref` is not used."""
        if session_id not in self.states:
            raise SessionNotFound(session_id)
        return self.states[session_id]


class InMemoryQuoteStore:
    """Quotes of this process only. Replicas do not share it: run one, or implement `QuoteStore` on a database."""

    def __init__(self) -> None:
        """Start empty: the versions of a quote are kept per (session id, quote id), oldest first."""
        self._versions: dict[tuple[str, str], list[dict[str, Any]]] = {}

    async def latest(self, session_id: str, quote_id: str) -> dict[str, Any] | None:
        """The newest version of the quote, or None."""
        versions = self._versions.get((session_id, quote_id))
        return versions[-1] if versions else None

    async def save(self, session_id: str, quote_id: str | None, record: dict[str, Any]) -> dict[str, Any]:
        """Store `record` as version 1 of a new quote (id `Q-` and 8 hex digits) or the next version of `quote_id`."""
        quote_id = quote_id or f"Q-{secrets.token_hex(4).upper()}"
        versions = self._versions.setdefault((session_id, quote_id), [])
        saved = {**record, "quote_id": quote_id, "version": len(versions) + 1}
        versions.append(saved)
        return saved
