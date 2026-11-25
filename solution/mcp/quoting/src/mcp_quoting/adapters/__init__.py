"""Adapter interfaces, their implementations, and the one env-driven selector (`from_env`)."""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass

from .base import (
    OFFER_TYPES,
    AddressService,
    Offer,
    QuoteStore,
    RatingEngine,
    SessionNotFound,
    StateReader,
    VehicleCatalog,
    VinDecoder,
    norm,
)
from .cosmos import CosmosStateReader
from .http import (
    HttpAddressService,
    HttpApi,
    HttpBffStateReader,
    HttpRatingEngine,
    HttpVehicleCatalog,
    HttpVinDecoder,
    entra_token,
)
from .stubs import (
    InMemoryQuoteStore,
    StubAddressService,
    StubRatingEngine,
    StubStateReader,
    StubVehicleCatalog,
    StubVinDecoder,
)

__all__ = [
    "OFFER_TYPES",
    "AddressService",
    "CosmosStateReader",
    "Deps",
    "HttpAddressService",
    "HttpApi",
    "HttpBffStateReader",
    "HttpRatingEngine",
    "HttpVehicleCatalog",
    "HttpVinDecoder",
    "InMemoryQuoteStore",
    "Offer",
    "QuoteStore",
    "RatingEngine",
    "SessionNotFound",
    "StateReader",
    "StubAddressService",
    "StubRatingEngine",
    "StubStateReader",
    "StubVehicleCatalog",
    "StubVinDecoder",
    "VehicleCatalog",
    "VinDecoder",
    "entra_token",
    "from_env",
    "norm",
]


@dataclass(frozen=True)
class Deps:
    """Everything the tools depend on. Build it with `from_env`, or directly in tests."""

    rating: RatingEngine
    catalog: VehicleCatalog
    addresses: AddressService
    vins: VinDecoder
    state: StateReader
    quotes: QuoteStore
    key: bytes  # SESSION_REF_KEY


def _choose[T](env: Mapping[str, str], var: str, options: dict[str, Callable[[], T]], default: str = "stub") -> T:
    """Build what `env[var]` (or `default`) names from `options`, a `{name: constructor}` map.
    A name that is not in `options` raises `ValueError` listing the valid ones."""
    kind = env.get(var, default)
    if kind not in options:
        msg = f"{var}={kind!r} is not one of {sorted(options)}"
        raise ValueError(msg)
    return options[kind]()


def from_env(env: Mapping[str, str] | None = None) -> Deps:
    """Pick every implementation from environment variables (README: environment variables).

    `RATING_ENGINE`, `VEHICLE_CATALOG`, `ADDRESS_SERVICE`, `VIN_DECODER` are `stub` (default) or `http`; `http` reads
    `<PREFIX>_BASE_URL` and the optional `<PREFIX>_SCOPE` (Entra token scope) with PREFIX `RATING`, `CATALOG`,
    `ADDRESS` or `VIN`. `STATE_READER` is `bff` (default, `BFF_BASE_URL`) or `cosmos` (`COSMOS_*`)."""
    env = os.environ if env is None else env

    def required(name: str) -> str:
        """The value of a variable that must be set and not empty; `RuntimeError` otherwise."""
        if not env.get(name):
            msg = f"{name} is required"
            raise RuntimeError(msg)
        return env[name]

    def api(prefix: str) -> HttpApi:
        """An `HttpApi` on `<prefix>_BASE_URL`, with the Entra token hook when `<prefix>_SCOPE` is set."""
        scope = env.get(f"{prefix}_SCOPE")
        return HttpApi(
            required(f"{prefix}_BASE_URL"),
            token=entra_token(scope) if scope else None,
            timeout=float(env.get("HTTP_TIMEOUT_SECONDS", "5")),
            retries=int(env.get("HTTP_RETRIES", "2")),
        )

    def cosmos() -> CosmosStateReader:
        """The Cosmos state reader; `COSMOS_ENDPOINT` is required."""
        required("COSMOS_ENDPOINT")
        return CosmosStateReader.from_env(env)

    try:
        return Deps(
            rating=_choose(
                env, "RATING_ENGINE", {"stub": StubRatingEngine, "http": lambda: HttpRatingEngine(api("RATING"))}
            ),
            catalog=_choose(
                env, "VEHICLE_CATALOG", {"stub": StubVehicleCatalog, "http": lambda: HttpVehicleCatalog(api("CATALOG"))}
            ),
            addresses=_choose(
                env, "ADDRESS_SERVICE", {"stub": StubAddressService, "http": lambda: HttpAddressService(api("ADDRESS"))}
            ),
            vins=_choose(env, "VIN_DECODER", {"stub": StubVinDecoder, "http": lambda: HttpVinDecoder(api("VIN"))}),
            state=_choose(
                env,
                "STATE_READER",
                {
                    "bff": lambda: HttpBffStateReader(HttpApi(env.get("BFF_BASE_URL", "http://localhost:8000"))),
                    "cosmos": cosmos,
                },
                default="bff",
            ),
            quotes=InMemoryQuoteStore(),
            key=required("SESSION_REF_KEY").encode(),
        )
    except ImportError as exc:  # only the Azure imports are lazy
        msg = f"{exc}: this configuration needs the azure extra, pip install 'mcp-quoting[azure]'"
        raise RuntimeError(msg) from exc
