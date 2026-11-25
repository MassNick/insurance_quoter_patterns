"""Shared rules about committed state values (contracts/events.md, merge rule 3).

One definition of "usable for pricing" so the BFF and mcp-quoting never disagree.
"""

from __future__ import annotations

from typing import Any

from quote_common.catalog import get_catalog

QUOTABLE_MIN_CONFIDENCE = 0.6  # lowest confidence at which an inferred (chat) value may be priced


def is_usable(envelope: dict[str, Any] | None, key: str) -> bool:
    """True when a field envelope may reach create_quote.

    - ``confirmed`` values are always usable;
    - ``pending_confirmation`` never is;
    - ``inferred`` (chat) values are usable at confidence >= 0.6, except deterministic fields,
      which must go through the double confirmation first.
    """
    if not envelope or envelope.get("value") in (None, "", [], {}):
        return False  # an empty list (e.g. no driver assignments yet) is not an answer
    status = envelope.get("status")
    if status == "confirmed":
        return True
    if status == "inferred":
        confidence = envelope.get("confidence")
        return (
            not get_catalog().is_deterministic(key)
            and confidence is not None
            and float(confidence) >= QUOTABLE_MIN_CONFIDENCE
        )
    return False
