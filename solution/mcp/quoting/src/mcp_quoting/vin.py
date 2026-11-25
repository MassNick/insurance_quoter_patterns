"""VIN check digit (North American weights), ported from `vinValid` in demo/src/a2ui.js."""

from __future__ import annotations

import re

PATTERN = re.compile(r"[A-HJ-NPR-Z0-9]{17}")  # same as tools.json: no I, O or Q
# Character -> its value in the check digit sum (letters are transliterated; there is no I, O or Q).
_VALUES = {
    **{str(d): d for d in range(10)},
    **dict(zip("ABCDEFGH", range(1, 9), strict=True)),
    **dict(zip("JKLMN", range(1, 6), strict=True)),
    "P": 7,
    "R": 9,
    **dict(zip("STUVWXYZ", range(2, 10), strict=True)),
}
# The weight of each of the 17 positions; the 9th, the check digit itself, weighs 0.
_WEIGHTS = (8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2)


def check_digit(vin: str) -> str:
    """The expected 9th character; the current 9th character has weight 0, so any placeholder works."""
    remainder = sum(_VALUES[c] * w for c, w in zip(vin, _WEIGHTS, strict=True)) % 11
    return "X" if remainder == 10 else str(remainder)


def is_valid(vin: str) -> bool:
    """True for 17 upper-case characters (no I, O or Q) whose 9th character is the right check digit."""
    return bool(PATTERN.fullmatch(vin)) and vin[8] == check_digit(vin)
