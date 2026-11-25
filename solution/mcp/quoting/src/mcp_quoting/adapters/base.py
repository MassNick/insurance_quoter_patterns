"""The seams to the real quoting system: one small Protocol per adapter.

Every tool talks to the outside world only through these. Each has a `Stub...` (this package, ported from the
design prototype) and, except `QuoteStore`, an `Http...` skeleton for the real system. See the README section
"How to connect the real quoting system".

Plain dicts cross the seams so a mapping function is all a new back end needs. `snapshot` is the usable state: the
values `quote_common.state.is_usable` accepts, unwrapped, shaped like the session document (`quote`, `vehicles`,
`drivers`, `underwriting`, `household`).
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from typing import Any, Literal, Protocol

from pydantic import BaseModel

OFFER_TYPES = ("essential", "plus", "premium")  # every offer type; create_quote prices all of them by default


class Offer(BaseModel):
    """One priced offer, as in contracts/tools.json. `coverages` are fr-CA customer-facing text."""

    offer_type: Literal["essential", "plus", "premium"]
    monthly: float  # price per month
    annual: int | float  # price per year
    deductible: int  # the deductible this offer is priced with
    coverages: list[str]


class SessionNotFound(LookupError):
    """The state reader has no committed state for this session."""


def norm(text: str) -> str:
    """Compare vehicle names the way a customer types them: no case, accents, spaces or punctuation."""
    ascii_text = unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", ascii_text.lower())


class RatingEngine(Protocol):
    """Prices a committed state."""

    async def price(self, snapshot: dict[str, Any], offer_types: Sequence[str]) -> list[Offer]:
        """Price one offer per requested type for this committed state."""
        ...


class VehicleCatalog(Protocol):
    """Which years, makes and models exist, and what type of vehicle a make and model is."""

    async def years(self) -> list[int]:
        """The model years the catalog covers."""
        ...

    async def makes(self, year: int | None = None) -> list[str]:
        """Makes with at least one model on sale in `year` (all makes when None)."""
        ...

    async def models(self, make: str, year: int | None = None) -> list[str]:
        """Canonical model names of `make` available in `year` (all when None)."""
        ...

    async def vehicle_type(self, make: str, model: str, year: int | None = None) -> dict[str, Any]:
        """`{vehicle_type, body_style, confidence}`; `vehicle_type` is a catalog enum value."""
        ...


class AddressService(Protocol):
    """Finds the postal address a customer typed."""

    async def candidates(self, query: str, postal_code: str | None = None) -> list[dict[str, Any]]:
        """Candidates as `{address_line1, city, province_code, postal_code, confidence}`, best first."""
        ...


class VinDecoder(Protocol):
    """Decodes a VIN into the vehicle it names."""

    async def decode(self, vin: str) -> dict[str, Any]:
        """`{valid_checksum: False}` for a bad check digit, else `{valid_checksum: True, year, make, model, trim}`."""
        ...


class StateReader(Protocol):
    """Reads the committed state of a session."""

    async def read(self, session_id: str, session_ref: str) -> dict[str, Any]:
        """The committed session document, with field envelopes, plus `quote_blocked` (bool): true while an
        end-of-form STOP rule or a rules error forbids pricing. Raises `SessionNotFound`.

        `session_ref` is the already verified credential: readers that call another service for the customer
        (the BFF endpoint) send it, readers with their own identity (Cosmos) ignore it.
        """
        ...


class QuoteStore(Protocol):
    """Quote ids and versions per session. A record is a dict: `offers`, `input_hash`, `options` (the quote
    options applied on top of the state), `offer_types`, `selected` (offer type used for the delta), `created_at`."""

    async def latest(self, session_id: str, quote_id: str) -> dict[str, Any] | None:
        """The newest version of the quote, with its `quote_id` and `version`, or None."""
        ...

    async def save(self, session_id: str, quote_id: str | None, record: dict[str, Any]) -> dict[str, Any]:
        """Store a new quote (`quote_id` None: new id, version 1) or the next version of one. Returns the record
        with `quote_id` and `version`."""
        ...
