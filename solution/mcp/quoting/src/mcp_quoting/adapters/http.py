"""Adapters for the real quoting system, and the BFF state reader.

One small helper, `HttpApi`, gives all of them the base URL, a timeout, retries and the auth hook. Every adapter
documents its default wire format and takes mapping functions for the parts the real system shapes differently, so
connecting a system means choosing paths and mappings here, not new classes.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from typing import Any

import httpx

from mcp_quoting.vin import is_valid

from .base import Offer, SessionNotFound

TokenProvider = Callable[[], Awaitable[str]]  # an async function that returns a bearer token
RETRY_STATUS = {429, 502, 503, 504}  # transient statuses: the request is tried again


class HttpApi:
    """JSON over HTTP with a base URL, a timeout, retries and an auth hook (`token` returns a bearer token)."""

    def __init__(
        self,
        base_url: str,
        *,
        token: TokenProvider | None = None,
        timeout: float = 5.0,
        retries: int = 2,
        backoff: float = 0.2,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """`retries` is the number of attempts after the first; `backoff` is the first wait in seconds, doubled each
        time. `transport` replaces the network (tests)."""
        self._client = httpx.AsyncClient(base_url=base_url, timeout=timeout, transport=transport)
        self._token, self._retries, self._backoff = token, retries, backoff

    async def request(self, method: str, path: str, *, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        """Return the decoded JSON body. Transport errors, timeouts and 429/502/503/504 are retried with backoff;
        other errors are not. Raises `httpx.HTTPError` once the last attempt fails. An explicit `Authorization`
        header replaces the bearer token."""
        for attempt in range(self._retries + 1):
            sent = dict(headers or {})
            if self._token and "Authorization" not in sent:
                sent["Authorization"] = f"Bearer {await self._token()}"
            try:
                response = await self._client.request(method, path, headers=sent, **kwargs)
                if response.status_code not in RETRY_STATUS or attempt == self._retries:
                    response.raise_for_status()
                    return response.json()
            except httpx.TransportError:
                if attempt == self._retries:
                    raise
            await asyncio.sleep(self._backoff * 2**attempt)
        raise AssertionError("unreachable")  # pragma: no cover


def entra_token(scope: str) -> TokenProvider:
    """Bearer tokens for `scope` from the container's managed identity (DefaultAzureCredential); `azure` extra."""
    from azure.identity.aio import DefaultAzureCredential  # lazy: stub-only setups do not install it

    credential = DefaultAzureCredential()

    async def token() -> str:
        """A bearer token for `scope`, from the managed identity."""
        return (await credential.get_token(scope)).token

    return token


def _params(**values: Any) -> dict[str, Any]:
    """The query parameters that are set (not None)."""
    return {k: v for k, v in values.items() if v is not None}


# --------------------------------------------------------------------------- rating
def _rating_payload(snapshot: dict[str, Any], offer_types: Sequence[str]) -> dict[str, Any]:
    """The default request body of `POST /rate`."""
    return {"snapshot": snapshot, "offer_types": list(offer_types)}


def _rating_offers(body: Any) -> list[dict[str, Any]]:
    """The default reading of the response: its `offers` list."""
    return body["offers"]


class HttpRatingEngine:
    """`POST /rate` with `to_payload(snapshot, offer_types)`; `to_offers(body)` must return dicts shaped like `Offer`.

    Default wire format: request `{"snapshot": {...}, "offer_types": [...]}`; response
    `{"offers": [{"offer_type", "monthly", "annual", "deductible", "coverages"}]}`."""

    def __init__(
        self,
        api: HttpApi,
        to_payload: Callable[..., Any] = _rating_payload,
        to_offers: Callable[[Any], Any] = _rating_offers,
    ) -> None:
        """`to_payload` builds the request body and `to_offers` reads the offers from the response; the defaults are
        the wire format above."""
        self._api, self._to_payload, self._to_offers = api, to_payload, to_offers

    async def price(self, snapshot: dict[str, Any], offer_types: Sequence[str]) -> list[Offer]:
        """Post the snapshot to `/rate` and check each offer as an `Offer` (`ValidationError` if one is not)."""
        body = await self._api.request("POST", "/rate", json=self._to_payload(snapshot, offer_types))
        return [Offer.model_validate(offer) for offer in self._to_offers(body)]


# --------------------------------------------------------------------------- vehicle catalog
class HttpVehicleCatalog:
    """Default wire format (GET, JSON): `/years` is `[int]`; `/makes?year=` and `/models?make=&year=` are `[str]`;
    `/vehicle-type?make=&model=&year=` is `{vehicle_type, body_style, confidence}`. `to_names` maps a names body
    to the list, `to_type` a vehicle-type body to the result."""

    def __init__(
        self, api: HttpApi, to_names: Callable[[Any], list[str]] = list, to_type: Callable[[Any], dict[str, Any]] = dict
    ) -> None:
        """`to_names` maps a makes or models response to the list of names; `to_type` maps a vehicle-type response to
        the result."""
        self._api, self._to_names, self._to_type = api, to_names, to_type

    async def years(self) -> list[int]:
        """The years from `GET /years`."""
        return [int(year) for year in await self._api.request("GET", "/years")]

    async def makes(self, year: int | None = None) -> list[str]:
        """The makes from `GET /makes`, for `year` when given."""
        return self._to_names(await self._api.request("GET", "/makes", params=_params(year=year)))

    async def models(self, make: str, year: int | None = None) -> list[str]:
        """The models of `make` from `GET /models`, for `year` when given."""
        return self._to_names(await self._api.request("GET", "/models", params=_params(make=make, year=year)))

    async def vehicle_type(self, make: str, model: str, year: int | None = None) -> dict[str, Any]:
        """The vehicle type, body style and confidence from `GET /vehicle-type`."""
        return self._to_type(
            await self._api.request("GET", "/vehicle-type", params=_params(make=make, model=model, year=year))
        )


# --------------------------------------------------------------------------- address
def _candidates_of(body: Any) -> list[dict[str, Any]]:
    """The default reading of the response: its `candidates` list."""
    return body["candidates"]


class HttpAddressService:
    """Default: `GET /addresses?q=&postal_code=` answers `{"candidates": [{address_line1, city, province_code,
    postal_code, confidence}]}`. `to_candidates(body)` maps another shape."""

    def __init__(self, api: HttpApi, to_candidates: Callable[[Any], list[dict[str, Any]]] = _candidates_of) -> None:
        """`to_candidates` maps the response to the list of candidates."""
        self._api, self._to_candidates = api, to_candidates

    async def candidates(self, query: str, postal_code: str | None = None) -> list[dict[str, Any]]:
        """The candidates from `GET /addresses` for the typed query, narrowed by `postal_code` when given."""
        return self._to_candidates(
            await self._api.request("GET", "/addresses", params=_params(q=query, postal_code=postal_code))
        )


# --------------------------------------------------------------------------- VIN
def _vehicle_of(body: Any) -> dict[str, Any]:
    """The default reading of the response: its `year`, `make`, `model` and `trim`."""
    return {key: body.get(key) for key in ("year", "make", "model", "trim")}


class HttpVinDecoder:
    """Default: `GET /vin/{vin}` answers `{year, make, model, trim}`. The check digit is verified here first, so a bad
    VIN costs no call. `to_vehicle(body)` maps another shape."""

    def __init__(self, api: HttpApi, to_vehicle: Callable[[Any], dict[str, Any]] = _vehicle_of) -> None:
        """`to_vehicle` maps the response to `{year, make, model, trim}`."""
        self._api, self._to_vehicle = api, to_vehicle

    async def decode(self, vin: str) -> dict[str, Any]:
        """`{"valid_checksum": False}` without a call for a bad check digit, else the vehicle from `GET /vin/{vin}`."""
        if not is_valid(vin):
            return {"valid_checksum": False}
        return {"valid_checksum": True, **self._to_vehicle(await self._api.request("GET", f"/vin/{vin}"))}


# --------------------------------------------------------------------------- state
class HttpBffStateReader:
    """Local and end-to-end runs: `GET /internal/sessions/{sid}/state` on the BFF, authorized by
    `Authorization: SessionRef <session_ref>`. The BFF answers `{session_id, phase, state, quote_blocked,
    missing_fields}`; only `state` and `quote_blocked` are read (a missing `quote_blocked` means not blocked), so
    other keys, such as the older `preferences`, are harmless."""

    def __init__(self, api: HttpApi) -> None:
        """`api` is an `HttpApi` on the BFF's base URL."""
        self._api = api

    async def read(self, session_id: str, session_ref: str) -> dict[str, Any]:
        """The committed state from the BFF, plus `quote_blocked`. A 404 raises `SessionNotFound`."""
        try:
            body = await self._api.request(
                "GET", f"/internal/sessions/{session_id}/state", headers={"Authorization": f"SessionRef {session_ref}"}
            )
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                raise SessionNotFound(session_id) from exc
            raise
        return {**body["state"], "quote_blocked": bool(body.get("quote_blocked"))}
