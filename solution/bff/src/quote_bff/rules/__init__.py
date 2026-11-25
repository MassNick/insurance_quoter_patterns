"""Business rules the BFF evaluates on the quote session state.

Copied from the design folder (``bff/rules``); ``end_of_form`` is the production ``EndOfFormRules``.
"""

from .end_of_form import (
    DEFAULT_RULES,
    EndOfFormRules,
    Rule,
    RuleCheckResult,
    RuleHit,
    Severity,
)

__all__ = [
    "DEFAULT_RULES",
    "EndOfFormRules",
    "Rule",
    "RuleCheckResult",
    "RuleHit",
    "Severity",
]
