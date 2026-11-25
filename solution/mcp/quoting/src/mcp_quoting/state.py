"""Session state to quote inputs: what is quotable, what the rating engine sees, and the audit hash.

The session document holds every field as an envelope `{value, source, confidence, status, ...}` (state-catalog.json).
Only usable values reach pricing, as `quote_common.state.is_usable` defines them (events.md merge rule 3): confirmed
values, and inferred ones with confidence 0.6 or more on a non-deterministic field; never a pending value.
"""

from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

from quote_common import pointer
from quote_common.catalog import ENTITY_BASE, MANY, get_catalog
from quote_common.state import is_usable


def _section(state: dict[str, Any], entity: str) -> Any:
    """The entity's part of the document: a dict, or a list for vehicles and drivers."""
    return pointer.get(state, ENTITY_BASE[entity]) or ([] if entity in MANY else {})


def _usable(envelope: Any, key: str) -> bool:
    """`is_usable`, also false for a value that is not an envelope at all (a malformed document fails closed)."""
    return isinstance(envelope, dict) and is_usable(envelope, key)


def missing_fields(state: dict[str, Any]) -> list[dict[str, str]]:
    """Required-for-quote paths whose value is absent or not usable, in catalog order. Empty means quotable.

    Each `required_for_quote` key of the catalog is needed on every vehicle and every driver of the state (and at
    least one of each), except `driver_vehicle_assignments` when one driver is on one vehicle: nothing to assign."""
    cat = get_catalog()
    counts = {entity: max(1, len(_section(state, entity))) for entity in MANY}
    missing = []
    for key, field in cat.fields.items():
        if not field.get("required_for_quote") or (key == "driver_vehicle_assignments" and set(counts.values()) == {1}):
            continue
        for index in range(counts.get(field["entity"], 1)):
            path = cat.resolve_path(key, {"type": field["entity"], "index": index})
            envelope = pointer.get(state, path)
            if not _usable(envelope, key):
                present = isinstance(envelope, dict) and envelope.get("value") not in (None, "")
                reason = (envelope.get("status") or "unconfirmed") if present else "missing"  # only names the cause
                missing.append({"path": path, "key": key, "label": cat.label(key), "reason": reason})
    return missing


def snapshot(state: dict[str, Any]) -> dict[str, Any]:
    """The usable values, unwrapped, in the shape of the document (`quote`, `vehicles`, `drivers`, ...)."""

    def usable(fields: Any) -> dict[str, Any]:
        """The usable values of one entity's fields, without their envelopes."""
        return {key: e["value"] for key, e in (fields or {}).items() if _usable(e, key)}

    out: dict[str, Any] = {}
    for entity, base in ENTITY_BASE.items():
        section = _section(state, entity)
        out[base.lstrip("/")] = [usable(item) for item in section] if entity in MANY else usable(section)
    return out


def apply_options(snap: dict[str, Any], options: dict[str, Any]) -> dict[str, Any]:
    """A copy of the snapshot with the quote options applied. Options are catalog keys (`deductible`, `add_ons`,
    `offer_type`, `annual_kilometers`); a vehicle option goes to every vehicle."""
    cat = get_catalog()
    out = copy.deepcopy(snap)
    for key, value in options.items():
        entity = cat.field(key)["entity"]
        for index in range(len(out["vehicles"]) if entity == "vehicle" else 1):
            pointer.set(
                out,
                cat.resolve_path(key, {"type": entity, "index": index}),
                str(value) if key == "deductible" else value,
            )
    return out


def input_hash(snap: dict[str, Any]) -> str:
    """SHA-256 of the snapshot as canonical JSON (sorted keys, no spaces, UTF-8, no ASCII escapes). FR-QUO-3."""
    return hashlib.sha256(
        json.dumps(snap, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()
