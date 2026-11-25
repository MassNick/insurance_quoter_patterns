"""The six tools of contracts/tools.json. A handler takes `Deps` and the schema-validated arguments and returns a
JSON-able dict, or raises `ToolFailure` for a refusal the caller should read."""

from __future__ import annotations

from typing import Any

from quote_common import session_ref
from quote_common.catalog import get_catalog
from quote_common.envelope import now_iso

from .adapters import OFFER_TYPES, Deps, norm
from .state import apply_options, input_hash, missing_fields, snapshot

OPTIONS = ("deductible", "add_ons", "offer_type", "annual_kilometers")  # the `changes` of modify_quote in tools.json


class ToolFailure(Exception):
    """A refusal the caller can act on. Reaches the caller as `{"error": code, "message": ..., **extra}`."""

    def __init__(self, code: str, message: str, **extra: Any) -> None:
        """`code` is the machine-readable error; `extra` adds keys to the result (for example `missing_fields`)."""
        super().__init__(message)
        self.code, self.extra = code, extra


# --------------------------------------------------------------------------- lookups (terminal tools)
def _pick(value: str | None, names: list[str]) -> str | None:
    """The name in `names` that `value` matches the way a customer types it (see `norm`), or None."""
    return next((name for name in names if value and norm(name) == norm(value)), None)


async def identify_address(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """The address service's candidates for the typed address, best first, at most five."""
    return {"candidates": (await d.addresses.candidates(args["query"], args.get("postal_code")))[:5]}


async def identify_vehicle_type(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """The vehicle type, body style and confidence for a make, a model and an optional year."""
    return await d.catalog.vehicle_type(args["make"], args["model"], args.get("year"))


async def lookup_make_model_year(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """Validate year, make and model, spelled any way a customer types them; list the options for what is missing."""
    year, options = args.get("year"), {}
    if year is None:
        options["years"] = await d.catalog.years()
    makes = await d.catalog.makes(year)
    make = _pick(args.get("make"), makes)
    models = await d.catalog.models(make, year) if make else []
    model = _pick(args.get("model"), models)
    if make is None:
        options["makes"] = makes
    elif model is None:
        options["models"] = models
    normalized = {"year": year if makes else None, "make": make, "model": model}
    return {"valid": all(normalized.values()), "normalized": normalized, **options}


async def decode_vin(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """Decode a VIN: `{"valid_checksum": False}` for a bad check digit, else the vehicle it names."""
    return await d.vins.decode(args["vin"])


# --------------------------------------------------------------------------- quotes (narrate tools)
async def guard(d: Deps, args: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """The one gate of the quote tools. Verify `session_ref` (signed, not expired), read that session's committed
    state through the state reader, and require it to be quotable: not blocked by an end-of-form STOP rule, and with
    every required field usable (`is_usable`). Returns the session id and the state."""
    ref = args.get("session_ref") or ""
    try:
        session_id = session_ref.verify(ref, d.key)
    except session_ref.InvalidSessionRef as exc:
        raise ToolFailure("invalid_session_ref", str(exc)) from exc
    state = await d.state.read(session_id, ref)
    if state.get("quote_blocked"):
        raise ToolFailure("quote_blocked", "An end-of-form STOP rule is active: this session cannot be priced online")
    if missing := missing_fields(state):
        message = f"{len(missing)} required field(s) are missing or not usable for pricing"
        raise ToolFailure("missing_fields", message, missing_fields=missing)
    return session_id, state


def _monthly(offers: list[dict[str, Any]], selected: str) -> float:
    """The monthly price of the `selected` offer type (of the first offer when it is not among them)."""
    return next((o for o in offers if o["offer_type"] == selected), offers[0])["monthly"]


async def _price(
    d: Deps,
    session_id: str,
    state: dict[str, Any],
    quote_id: str | None,
    offer_types: list[str],
    options: dict[str, Any],
    prev: dict[str, Any] | None,
) -> dict[str, Any]:
    """Price the usable state with the quote options applied, store the new version, build the result.
    Used by `create_quote` (a new quote) and `modify_quote` (the next version of `prev`)."""
    snap = apply_options(snapshot(state), options)
    offers = [offer.model_dump() for offer in await d.rating.price(snap, offer_types)]
    if not offers:
        raise ToolFailure("no_offers", "The rating engine returned no offers")
    selected = snap["underwriting"].get("offer_type", "plus")
    saved = await d.quotes.save(
        session_id,
        quote_id,
        {
            "offers": offers,
            "input_hash": input_hash(snap),
            "options": options,
            "offer_types": offer_types,
            "selected": selected,
            "created_at": now_iso(),
        },
    )
    result: dict[str, Any] = {"quote_id": saved["quote_id"], "version": saved["version"]}
    if prev:
        delta = _monthly(offers, selected) - _monthly(prev["offers"], prev["selected"])
        result |= {"previous_version": prev["version"], "delta_monthly": round(delta, 2)}
    return result | {"offers": offers, "input_hash": saved["input_hash"]}


async def create_quote(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """Price version 1 of a new quote from the session's state, for `offer_types` (all three by default)."""
    session_id, state = await guard(d, args)
    return await _price(d, session_id, state, None, args.get("offer_types") or list(OFFER_TYPES), {}, None)


def _checked(changes: dict[str, Any]) -> dict[str, Any]:
    """The model may pass quote options only, never field values, and only values the catalog allows.
    Returns `changes`, or raises `ToolFailure("invalid_changes")`."""
    if unknown := sorted(set(changes) - set(OPTIONS)):
        raise ToolFailure(
            "invalid_changes", f"Unsupported change(s): {', '.join(unknown)}. Allowed: {', '.join(OPTIONS)}"
        )
    cat = get_catalog()
    deductibles = [int(value) for value, _ in cat.field("deductible")["enum"]]
    if "deductible" in changes and changes["deductible"] not in deductibles:
        raise ToolFailure("invalid_changes", f"deductible must be one of {deductibles}")
    km = cat.field("annual_kilometers")
    if "annual_kilometers" in changes and not km["min"] <= changes["annual_kilometers"] <= km["max"]:
        raise ToolFailure("invalid_changes", f"annual_kilometers must be between {km['min']} and {km['max']}")
    return changes


async def modify_quote(d: Deps, args: dict[str, Any]) -> dict[str, Any]:
    """Next version of a quote of this session: options accumulate over earlier versions, state is read afresh."""
    session_id, state = await guard(d, args)
    prev = await d.quotes.latest(session_id, args["quote_id"])
    if prev is None:
        raise ToolFailure("unknown_quote", f"No quote {args['quote_id']} in this session: call create_quote first")
    options = {**prev["options"], **_checked(args["changes"])}
    return await _price(d, session_id, state, prev["quote_id"], prev["offer_types"], options, prev)


# Tool name -> handler. The server refuses to start unless these are exactly the tools of tools.json.
HANDLERS = {
    fn.__name__: fn
    for fn in (identify_address, identify_vehicle_type, lookup_make_model_year, decode_vin, create_quote, modify_quote)
}
