"""D15 suggested-reply filter: a pure function run on every ``suggestions`` event before it is logged and relayed."""

from __future__ import annotations

import re
from typing import Any

from quote_common.logs import PII_PATTERNS

from quote_bff.state import CATALOG, STATE_KEYS

MAX_ITEMS, MAX_CHARS = 4, 40  # at most 4 bubbles of at most 40 characters (FR-CHAT-3)
ENUM_CODES = {str(code) for f in CATALOG.fields.values() for code, _ in f.get("enum") or []}  # codes, not answers
# Capitalized words allowed after the first word: approved UI vocabulary, not proper names.
ALLOWED = {"Desjardins", "Assurances", "Québec", "Canada", "OK", "Oui", "Non", "Je", "J’ai", "J'ai", "Formulaire"}
# Vehicle makes (case-folded): naming one is a value the customer has not given here (D15).
MAKES = {m.casefold() for m in (
    "Toyota", "Honda", "Ford", "Hyundai", "Kia", "Mazda", "Subaru", "Tesla", "Chevrolet", "Volkswagen", "Nissan",
    "Lamborghini", "Ferrari", "McLaren", "Bugatti", "Rolls-Royce", "Bentley", "Aston", "Porsche", "BMW", "Audi",
    "Mercedes", "Lexus", "Acura", "Jeep", "Dodge", "Ram", "GMC", "Volvo", "Mitsubishi", "Harley-Davidson")}
_WORD = re.compile(r"[\w’'-]+", re.UNICODE)  # a word; apostrophes and hyphens stay inside it
_LONG_NUMBER = re.compile(r"\d[\d ]{3,}")  # years, amounts and ids are values the customer has not given here


def _state_strings(state: dict[str, Any]) -> set[str]:
    """The case-folded values the session holds: strings and integers of 3+ characters, enum codes excluded."""
    out: set[str] = set()

    def walk(node: Any) -> None:
        """Collect the values of every field envelope under ``node``."""
        if isinstance(node, dict) and "value" in node and "source" in node:
            node = node["value"]
            values = node if isinstance(node, list) else [node]
            out.update(str(v).casefold() for v in values
                       if isinstance(v, str | int) and not isinstance(v, bool) and len(str(v)) >= 3
                       and str(v) not in ENUM_CODES)
        elif isinstance(node, dict):
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk({k: state.get(k) for k in STATE_KEYS})
    return out


def _reason(label: str, known: set[str]) -> str | None:
    """Why ``label`` must be dropped, or None when it may be shown (D15, FR-CHAT-7).

    Checked in this order: ``empty``, ``too_long``, ``pii_or_value`` (a PII pattern or a long number),
    ``state_or_catalog_value`` (a vehicle make, or a value of the session given in ``known``) and ``proper_name`` (a
    capitalized word that does not start a sentence and is not in ``ALLOWED``).
    """
    if not label.strip():
        return "empty"
    if len(label) > MAX_CHARS:
        return "too_long"
    if any(p.search(label) for p in PII_PATTERNS) or _LONG_NUMBER.search(label):
        return "pii_or_value"
    words = _WORD.findall(label)
    folded = label.casefold()
    if any(w.casefold() in MAKES for w in words) or any(re.search(rf"\b{re.escape(v)}\b", folded) for v in known):
        return "state_or_catalog_value"
    sentence_start = True
    for word in re.findall(r"[\w’'-]+|[.!?]", label):
        if word in ".!?":
            sentence_start = True
            continue
        if not sentence_start and word[:1].isupper() and word not in ALLOWED:
            return "proper_name"
        sentence_start = False
    return None


def filter_suggestions(items: list[Any], state: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Return (kept, dropped). Each dropped entry is ``{item, reason}``; log the reason, never the label.

    ``items`` are ``{label, kind, action_id?}`` dicts, or bare strings (read as ``reply`` items). The first
    ``MAX_ITEMS`` clean ones are kept in order; a clean item past the limit is dropped as ``over_limit``.
    ``state`` is the session document, so values the customer gave are never echoed back (D15, FR-CHAT-7).
    """
    known = _state_strings(state)
    kept, dropped = [], []
    for raw in items or []:
        item = raw if isinstance(raw, dict) else {"label": str(raw), "kind": "reply"}
        reason = _reason(str(item.get("label") or ""), known)
        if reason is None and len(kept) >= MAX_ITEMS:
            reason = "over_limit"
        (dropped.append({"item": item, "reason": reason}) if reason else kept.append(item))
    return kept, dropped
