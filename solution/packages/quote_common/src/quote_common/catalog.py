"""The state catalog (contracts/state-catalog.json) as a typed helper."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from quote_common.contracts import load_catalog

# Entity -> JSON Pointer of its part of the session document.
ENTITY_BASE = {"quote": "/quote", "vehicle": "/vehicles", "driver": "/drivers", "underwriting": "/underwriting", "household": "/household"}
MANY = {"vehicle", "driver"}  # the entities held as lists: a base pointer plus an index


class Catalog:
    """The fields of the state catalog by key, with the lookups the services share: paths, labels and enum options."""

    def __init__(self, raw: dict[str, Any]) -> None:
        """``raw`` is the parsed state-catalog.json; ``fields`` indexes its field definitions by ``key``."""
        self.raw = raw
        self.fields: dict[str, dict[str, Any]] = {f["key"]: f for f in raw["fields"]}

    def field(self, key: str) -> dict[str, Any] | None:
        """The definition of a catalog field, or None for an unknown key."""
        return self.fields.get(key)

    def has(self, key: str) -> bool:
        """Whether ``key`` is a catalog field."""
        return key in self.fields

    @property
    def deterministic_keys(self) -> set[str]:
        """Keys of the deterministic fields: a value counts only after the customer has confirmed it twice."""
        return {k for k, f in self.fields.items() if f.get("deterministic")}

    @property
    def required_for_quote(self) -> set[str]:
        """Keys of the fields that must hold a usable value before a quote can be priced."""
        return {k for k, f in self.fields.items() if f.get("required_for_quote")}

    @staticmethod
    def entity_path(entity: str, index: int | None = None) -> str:
        """JSON Pointer of an entity in the session document; a vehicle or a driver takes an ``index`` (default 0)."""
        base = ENTITY_BASE[entity]
        return f"{base}/{index or 0}" if entity in MANY else base

    def resolve_path(self, key: str, entity: dict[str, Any] | None = None) -> str:
        """Catalog key → JSON Pointer, using the FormSpec's default entity scope for repeated entities.

        A key that already starts with ``/`` is returned as it is. Raises ``KeyError`` for an unknown key.
        """
        if key.startswith("/"):
            return key
        f = self.fields.get(key)
        if f is None:
            msg = f"unknown catalog key: {key}"
            raise KeyError(msg)
        idx = (entity or {}).get("index", 0) if entity and entity.get("type") == f["entity"] else 0
        return f"{self.entity_path(f['entity'], idx)}/{key}"

    def label(self, key: str, lang: str = "fr") -> str:
        """The customer-facing label: ``label_fr`` for French when it exists, else ``label``, else the key itself."""
        f = self.fields.get(key) or {}
        return (f.get("label_fr") if lang == "fr" else None) or f.get("label", key)

    def help(self, key: str, lang: str = "fr") -> str:
        """The help text (``help_fr`` for French when it exists, else ``help``), or an empty string."""
        f = self.fields.get(key) or {}
        return (f.get("help_fr") if lang == "fr" else None) or f.get("help", "")

    def enum_labels(self, key: str, lang: str = "fr") -> list[list[str]] | None:
        """The ``[value, label]`` options of an enum field (``enum_fr`` for French when it exists); None without any."""
        f = self.fields.get(key) or {}
        return (f.get("enum_fr") if lang == "fr" else None) or f.get("enum")

    def is_deterministic(self, key: str) -> bool:
        """Whether the field is deterministic (see ``deterministic_keys``); an unknown key is not."""
        return bool((self.fields.get(key) or {}).get("deterministic"))


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    """The shared catalog, loaded once from the contracts folder."""
    return Catalog(load_catalog())
