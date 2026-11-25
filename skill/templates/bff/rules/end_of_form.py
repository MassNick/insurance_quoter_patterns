"""End-of-form rules: when the online quote stops and a human advisor takes over.

What end-of-form rules are
==========================
An end-of-form rule is a small named condition on the quote session state. When it matches,
one of two things happens:

* STOP: the online quote ends and the customer is handed to a human advisor (an exotic car, a
  farm vehicle, a refused or cancelled policy, ...). The customer is told why, in plain words,
  and can send the conversation to an advisor.
* FLAG: the quote goes on, and the advisor gets a signal to look at later (for example, a
  driver under 25 is the principal driver of a vehicle).

A rule is an id, a severity, a customer-facing reason in French and in English, and a predicate:
a pure function that returns the JSON Pointer paths that matched (``/vehicles/0/vehicle_make``),
or an empty list when the rule does not apply.

When they run, and why
======================
The BFF evaluates the rules synchronously after EVERY state commit, whatever the source of the
commit (memory-agent deltas, form submits, document reviews, full-form saves, double
confirmations), once per commit batch, before the main agent runs. A commit batch is everything
written by one atomic state write: all the changes of one memory result, one form submit, one
document review, one full-form save or one confirmation step.

This is recommended over a callback on the memory agent's response:

* Forms, documents and full-form edits never pass through the memory agent. The BFF commits
  their values itself, and the memory agent only receives a one-line text summary of them, so a
  callback on its response would not see those commits (nor a double confirmation).
* The commit is the single choke point. The BFF is the only writer of session state, so one call
  site sees every change, whatever its source. A callback on the memory response is a second
  place that is skipped when the memory call fails or times out, and that every new input type
  would have to remember to call.
* It is deterministic and auditable. A predicate is a pure function of the committed state: the
  same state always gives the same hits, a stored state can be re-checked later to explain a
  decision, and each hit is written as a ``rules.hit`` event next to the ``state_changes`` rows.
* It costs no model call: plain Python, microseconds, no network, no tokens.

Rules read the whole merged state, never only the delta. They are therefore order-independent
(a date of birth and the assignment matrix can arrive in different batches: the rule fires when
the last piece lands) and self-healing (if the BFF crashes between persisting a commit and
handling a hit, the next commit finds the hit again, so it is never lost).

For every input except chat, the commit happens before the hosted agent is invoked, so a STOP
means the agent is not invoked at all. On the chat path the memory agent and the main agent run
concurrently inside one invocation (architecture.md, section 8.3), so the memory delta commits
mid-turn: on a STOP the BFF drops the main agent's in-flight reply instead of forwarding it (see
the integration example at the end of this module).

How to add a rule
=================
1. Write the predicate. A function ``def my_rule(state) -> list[str]`` that reads the merged
   session state and returns the paths that make the condition true, or ``[]``.

   * Read through ``get_value``, ``get_status``, ``get_envelope`` and ``iter_fields``. They never
     raise on a missing or partial state, and early in a session most fields do not exist yet.
   * Stay pure and fast: no I/O, no model call, no randomness. The only clock is ``today()``,
     which tests can pin.
   * Return field-level paths as in state-catalog.json (``/vehicles/1/usage_type``,
     ``/household/driver_vehicle_assignments``) and only the ones that matter: they are shown to
     the advisor and hashed into the fingerprint.
   * Compare booleans with ``is True`` and enum fields with the catalog code (``"farm"``).
   * When a condition must only count once the customer confirmed it (a deterministic field such
     as ``was_denied_or_cancelled``), test ``get_status(state, path) == "confirmed"`` yourself.
   * Never swallow errors. If a predicate raises anyway, ``check`` lets the error out instead of
     turning it into "no match": the commit stands, and the BFF logs, alerts and blocks
     ``get_quote`` until a later check passes (architecture.md, section 16).

2. Choose the severity. STOP when the quote must not go on online (the business cannot price it
   online, or a person must decide): the customer is handed off. FLAG when the quote can go on
   but the advisor should know. When unsure, start with FLAG: ending a customer's quote by
   mistake costs more than a late note.

3. Write the customer-facing reason in French and in English: ``reason_fr`` (Quebec French) and
   ``reason_en``. One or two short sentences, plain words, saying what happens next. No jargon,
   no rule id, no blame. They are shown as written on the end-of-form surface and in the
   ``rules.hit`` event. Registration fails if either one is blank.

4. Register it. Decorate the predicate with
   ``@DEFAULT_RULES.rule(id=..., description=..., severity=..., reason_fr=..., reason_en=...)``.
   The id is UPPER_SNAKE, French-ish like the others (``USAGE_AGRICOLE``) and unique. Never
   rename or reuse an id once released: it is stored in events, hand-offs and fingerprints.
   Registration checks the id format, its uniqueness and the reasons when this module is
   imported, so a mistake fails the tests and not a customer's quote. Registration order only
   breaks ties between hits of the same severity and the same firmness (see "Reading a result").

5. Add the tests in ``test_end_of_form.py``. First a positive and a negative fixture in
   ``FIXTURES``: a test fails when a registered rule has no pair, checks that the positive
   fixture fires this rule and no other, and that the negative one fires nothing. Then a test
   class for the rule: the paths and the severity on a match, near misses, an empty or partial
   state (no hit, no exception) and, when the predicate depends on ``status``, one case with
   ``inferred`` and one with ``confirmed``. Run ``cd bff && python3 -m unittest -v``.

6. Deploy. Rules are code and ship with the BFF: merge, let CI run the tests, release the BFF as
   usual. Nothing is configured at runtime. Because rules read the whole state, a new rule also
   applies to sessions already in progress, at their next commit.

Reading a result
================
``DEFAULT_RULES.check(state, session.acknowledged)`` returns a ``RuleCheckResult``: the hits that
are not acknowledged yet, STOP first (within a severity, firm hits before inferred ones, then
registration order).

* ``inferred`` is True when every matched value has status "inferred": the memory agent guessed
  it from chat and the customer has not confirmed it. ``RuleCheckResult.needs_confirmation`` is
  True when the first STOP hit is inferred; the BFF then asks the customer ONE confirmation
  question before it stops the quote. A hit backed by anything firmer (a form, a reviewed
  document, a lookup, a double confirmation) stops the quote right away.
* ``fingerprint`` is a stable hash of the rule id, the matched paths, their values and the
  ``inferred`` flag. The BFF adds it to ``session.acknowledged`` once it has handled the hit, so
  the same match is not reported again at every later commit. It changes when the customer
  corrects a value and when the evidence firms up, which is what lets one acknowledged set carry
  the sequence "inferred STOP, question asked, customer says yes, quote stops": asking
  acknowledges the inferred fingerprint, and the confirmed hit has a new one.

See ``example_commit`` and ``example_on_end_of_form_sent`` at the end of this module for how the
BFF uses a result.
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

__all__ = [
    "DEFAULT_LANGUAGE",
    "DEFAULT_RULES",
    "STATUS_CONFIRMED",
    "STATUS_INFERRED",
    "EndOfFormRules",
    "Predicate",
    "Rule",
    "RuleCheckResult",
    "RuleHit",
    "Severity",
    "State",
    "count_entities",
    "get_envelope",
    "get_status",
    "get_value",
    "iter_fields",
    "today",
]

# The session state document: {"quote": {...}, "vehicles": [{...}], "drivers": [{...}],
# "underwriting": {...}, "household": {...}}. Every leaf is a field envelope:
# {"value", "source", "confidence", "status", "turn", "updated_at"} (state-catalog.json).
State = Mapping[str, Any]
# A predicate returns the JSON Pointer paths that matched, or [] when the rule does not apply.
Predicate = Callable[[State], list[str]]

STATUS_INFERRED = "inferred"  # guessed by the memory agent from chat, not confirmed by the customer
STATUS_CONFIRMED = "confirmed"  # confirmed by the customer (form, reviewed document, double confirmation)
DEFAULT_LANGUAGE = "fr"  # Quebec is the home market
FINGERPRINT_LENGTH = 16  # hex characters kept from the SHA-256 digest


class Severity(str, Enum):
    """What a matching rule does."""

    STOP = "stop"  # end the online quote and hand the customer to a human advisor
    FLAG = "flag"  # keep quoting and add a signal for the advisor


_SEVERITY_RANK = {Severity.STOP: 0, Severity.FLAG: 1}  # sort key: STOP hits come before FLAG hits


# ---------------------------------------------------------------------------------------------
# Reading the state safely. None of these raise on a missing, partial or malformed state.
# ---------------------------------------------------------------------------------------------

_INDEX = re.compile(r"0|[1-9][0-9]*")  # an array index in a JSON Pointer: no sign, no leading zero (RFC 6901)


def _is_list(node: Any) -> bool:
    """True for a list, tuple or other sequence, but not for a string or bytes."""
    return isinstance(node, Sequence) and not isinstance(node, (str, bytes))


def _resolve(node: Any, pointer: str) -> Any:
    """Walk an RFC 6901 JSON Pointer through nested mappings and lists. None when it does not resolve."""
    if not isinstance(pointer, str) or (pointer and not pointer.startswith("/")):
        return None
    if not pointer:
        return node
    for raw in pointer[1:].split("/"):
        segment = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, Mapping):
            node = node.get(segment)
        elif _is_list(node) and _INDEX.fullmatch(segment):
            index = int(segment)
            node = node[index] if index < len(node) else None
        else:
            return None
    return node


def get_envelope(state: State, pointer: str) -> Mapping[str, Any] | None:
    """Return the field envelope at *pointer* (``/vehicles/0/vehicle_make``), or None."""
    node = _resolve(state, pointer)
    return node if isinstance(node, Mapping) and "value" in node else None


def get_value(state: State, pointer: str) -> Any:
    """Return the value held by the envelope at *pointer*, or None when absent or cleared."""
    envelope = get_envelope(state, pointer)
    return envelope.get("value") if envelope is not None else None


def get_status(state: State, pointer: str) -> str | None:
    """Return the envelope status ("inferred", "confirmed", "pending_confirmation"), or None."""
    envelope = get_envelope(state, pointer)
    status = envelope.get("status") if envelope is not None else None
    return status if isinstance(status, str) else None


def count_entities(state: State, collection: str) -> int:
    """Number of entries in a many-cardinality collection (``"vehicles"`` or ``"drivers"``)."""
    items = _resolve(state, f"/{collection}")
    return len(items) if _is_list(items) else 0


def iter_fields(state: State, collection: str, key: str) -> Iterator[tuple[str, Any]]:
    """Yield ``(pointer, value)`` for field *key* on every entry of *collection* that has a value."""
    for index in range(count_entities(state, collection)):
        pointer = f"/{collection}/{index}/{key}"
        value = get_value(state, pointer)
        if value is not None:
            yield pointer, value


def _normalize(value: Any) -> str:
    """Casefold, turn hyphens and underscores into spaces, collapse spaces: "Rolls-Royce " -> "rolls royce"."""
    if not isinstance(value, str):
        return ""
    return " ".join(value.casefold().replace("-", " ").replace("_", " ").split())


# ---------------------------------------------------------------------------------------------
# Rules, hits and the registry
# ---------------------------------------------------------------------------------------------

_RULE_ID = re.compile(r"[A-Z][A-Z0-9]*(?:_[A-Z0-9]+)*")


@dataclass(frozen=True)
class Rule:
    """One end-of-form rule: its metadata and the predicate that decides whether it matches."""

    id: str
    description: str  # for developers and the audit trail, not shown to the customer
    severity: Severity
    reason_fr: str  # customer-facing, Quebec French
    reason_en: str  # customer-facing, English
    predicate: Predicate

    def __post_init__(self) -> None:
        """Reject a bad id, severity or blank text when the rule is built, so a mistake fails at import time."""
        if not _RULE_ID.fullmatch(self.id):
            msg = f"Rule id {self.id!r} must be UPPER_SNAKE (letters, digits, single underscores)."
            raise ValueError(msg)
        if not isinstance(self.severity, Severity):
            msg = f"{self.id}: severity must be a Severity, got {self.severity!r}."
            raise TypeError(msg)
        for name in ("description", "reason_fr", "reason_en"):
            if not getattr(self, name).strip():
                msg = f"{self.id}: {name} must not be blank."
                raise ValueError(msg)

    @property
    def reasons(self) -> dict[str, str]:
        """The customer-facing reasons by language: ``{"fr": ..., "en": ...}``."""
        return {"fr": self.reason_fr, "en": self.reason_en}


@dataclass(frozen=True)
class RuleHit:
    """A rule that matched the state, with a snapshot of what matched it."""

    rule_id: str
    severity: Severity
    paths: tuple[str, ...]  # JSON Pointers that matched, in the predicate's order
    values: Mapping[str, Any]  # {path: value} at the time of the check (a copy)
    inferred: bool  # True when every matched value only has status "inferred"
    fingerprint: str  # stable hash of rule id + paths + values + inferred; what "acknowledged" stores
    reasons: Mapping[str, str]  # {"fr": ..., "en": ...}

    def reason(self, language: str = DEFAULT_LANGUAGE) -> str:
        """Customer-facing reason for *language* ("fr", "en", "fr-CA", ...). Unknown languages get French."""
        code = (language or DEFAULT_LANGUAGE).lower().replace("_", "-").split("-")[0]
        return self.reasons.get(code) or self.reasons[DEFAULT_LANGUAGE]

    def event_data(self, language: str = DEFAULT_LANGUAGE) -> dict[str, Any]:
        """The ``data`` of the ``rules.hit`` SSE event. Matched values stay out of it: they can be PII."""
        return {
            "rule_id": self.rule_id,
            "severity": self.severity.value,
            "paths": list(self.paths),
            "inferred": self.inferred,
            "reason": self.reason(language),
        }


@dataclass(frozen=True)
class RuleCheckResult:
    """What ``EndOfFormRules.check`` found: hits ordered STOP first, then FLAG."""

    hits: tuple[RuleHit, ...] = ()

    @property
    def stop(self) -> RuleHit | None:
        """The first STOP hit, or None."""
        return next((hit for hit in self.hits if hit.severity is Severity.STOP), None)

    @property
    def flags(self) -> tuple[RuleHit, ...]:
        """Every FLAG hit."""
        return tuple(hit for hit in self.hits if hit.severity is Severity.FLAG)

    @property
    def needs_confirmation(self) -> bool:
        """True when the STOP hit is only inferred: ask the customer one confirmation question first."""
        stop = self.stop
        return stop is not None and stop.inferred


def _fingerprint(rule_id: str, values: Mapping[str, Any], *, inferred: bool) -> str:
    """Stable hash of what matched.

    Changing this recipe makes every acknowledged hit in a live session fire again. Never use the
    built-in hash() here: it is salted per process, so it would change at every restart.
    """
    matches = [[path, values[path]] for path in sorted(values)]
    payload = json.dumps([rule_id, inferred, matches], sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:FINGERPRINT_LENGTH]


def _clean_paths(rule: Rule, raw: Iterable[str]) -> tuple[str, ...]:
    """The predicate's paths, without duplicates and in order.

    Raises ValueError for anything that is not a JSON Pointer.
    """
    paths = tuple(dict.fromkeys(raw))  # de-duplicate, keep the predicate's order
    for path in paths:
        if not isinstance(path, str) or not path.startswith("/"):
            msg = f"{rule.id}: the predicate must return JSON Pointer paths starting with '/', got {path!r}."
            raise ValueError(msg)
    return paths


def _build_hit(rule: Rule, state: State, paths: tuple[str, ...]) -> RuleHit:
    """A RuleHit for ``rule`` on ``state``, with a snapshot of the matched values.

    It records whether every matched value is only inferred, and carries the fingerprint.
    """
    values = {path: copy.deepcopy(get_value(state, path)) for path in paths}
    inferred = all(get_status(state, path) == STATUS_INFERRED for path in paths)
    return RuleHit(
        rule_id=rule.id,
        severity=rule.severity,
        paths=paths,
        values=values,
        inferred=inferred,
        fingerprint=_fingerprint(rule.id, values, inferred=inferred),
        reasons=rule.reasons,
    )


class EndOfFormRules:
    """A registry of rules. The BFF uses ``DEFAULT_RULES``; tests can build their own."""

    def __init__(self) -> None:
        """Start with no rules; ``register`` or the ``rule`` decorator adds them."""
        self._rules: dict[str, Rule] = {}

    @property
    def rules(self) -> tuple[Rule, ...]:
        """The registered rules, in registration order."""
        return tuple(self._rules.values())

    def get(self, rule_id: str) -> Rule:
        """The rule registered under ``rule_id``. Raises KeyError when there is none."""
        return self._rules[rule_id]

    def register(self, rule: Rule) -> Rule:
        """Add *rule*. Raises ValueError when its id is already taken."""
        if rule.id in self._rules:
            msg = f"Rule id {rule.id!r} is already registered."
            raise ValueError(msg)
        self._rules[rule.id] = rule
        return rule

    def rule(
        self,
        *,
        id: str,  # noqa: A002 (mirrors Rule.id)
        description: str,
        severity: Severity,
        reason_fr: str,
        reason_en: str,
    ) -> Callable[[Predicate], Predicate]:
        """Decorator that registers the decorated predicate as a rule and returns it unchanged."""

        def decorator(predicate: Predicate) -> Predicate:
            """Register ``predicate`` with the rule's metadata and return it unchanged."""
            self.register(
                Rule(
                    id=id,
                    description=description,
                    severity=severity,
                    reason_fr=reason_fr,
                    reason_en=reason_en,
                    predicate=predicate,
                )
            )
            return predicate

        return decorator

    def check(self, state: State, acknowledged: Collection[str] = frozenset()) -> RuleCheckResult:
        """Evaluate every rule on *state* and return the hits that are not acknowledged yet.

        Args:
            state: The merged session state document, after the commit.
            acknowledged: Fingerprints of the hits the BFF has already handled for this session.

        Returns:
            The hits, STOP first. Within a severity, firm hits (not inferred) come before inferred
            ones, so ``stop`` and ``needs_confirmation`` never ask a question when a firm STOP
            already ends the quote. Registration order breaks the remaining ties.

        Raises:
            ValueError: A predicate returned something that is not a JSON Pointer. That is a bug
                in the rule, not a property of the state, so it is raised and never hidden. An
                exception raised inside a predicate propagates the same way.
        """
        found: list[tuple[int, RuleHit]] = []
        for order, rule in enumerate(self._rules.values()):
            paths = _clean_paths(rule, rule.predicate(state))
            if not paths:
                continue
            hit = _build_hit(rule, state, paths)
            if hit.fingerprint not in acknowledged:
                found.append((order, hit))
        found.sort(key=lambda item: (_SEVERITY_RANK[item[1].severity], item[1].inferred, item[0]))
        return RuleCheckResult(tuple(hit for _, hit in found))


DEFAULT_RULES = EndOfFormRules()  # the registry the BFF uses; the example rules below register themselves on it


# ---------------------------------------------------------------------------------------------
# Example rules, registered on DEFAULT_RULES
# ---------------------------------------------------------------------------------------------

EXOTIC_MAKES = frozenset(
    _normalize(make)
    for make in ("Lamborghini", "Ferrari", "McLaren", "Bugatti", "Rolls-Royce", "Bentley", "Aston Martin")
)


@DEFAULT_RULES.rule(
    id="VEHICULE_EXOTIQUE",
    description="Exotic or ultra-luxury make (Lamborghini, Ferrari, ...): an advisor prices it, not the online quote.",
    severity=Severity.STOP,
    reason_fr=(
        "Ce type de véhicule de prestige demande une évaluation personnalisée. "
        "Un membre de notre équipe va préparer votre soumission avec vous."
    ),
    reason_en=(
        "This type of high-end vehicle needs a personal review. A member of our team will prepare your quote with you."
    ),
)
def vehicule_exotique(state: State) -> list[str]:
    """Any vehicle whose make is on the exotic list (case, hyphens and spacing are ignored)."""
    return [path for path, make in iter_fields(state, "vehicles", "vehicle_make") if _normalize(make) in EXOTIC_MAKES]


@DEFAULT_RULES.rule(
    id="VEHICULE_MODIFIE_PERFORMANCE",
    description="Vehicle modified for performance: an advisor must assess it.",
    severity=Severity.STOP,
    reason_fr=(
        "Un véhicule modifié pour la performance doit être évalué par un membre de notre équipe, "
        "qui préparera votre soumission avec vous."
    ),
    reason_en=(
        "A vehicle modified for performance has to be reviewed by a member of our team, "
        "who will prepare your quote with you."
    ),
)
def vehicule_modifie_performance(state: State) -> list[str]:
    """Any vehicle flagged ``modified_for_performance`` (a real boolean True, never a truthy string)."""
    return [path for path, value in iter_fields(state, "vehicles", "modified_for_performance") if value is True]


@DEFAULT_RULES.rule(
    id="USAGE_AGRICOLE",
    description="Farm use: farm vehicles go to the agricultural insurance team.",
    severity=Severity.STOP,
    reason_fr=(
        "Les véhicules à usage agricole sont pris en charge par notre équipe d'assurance agricole. "
        "Nous allons vous mettre en contact avec elle."
    ),
    reason_en="Farm vehicles are handled by our agricultural insurance team. We will put you in touch with them.",
)
def usage_agricole(state: State) -> list[str]:
    """Any vehicle whose main use is ``farm``."""
    return [path for path, usage in iter_fields(state, "vehicles", "usage_type") if _normalize(usage) == "farm"]


DENIED_OR_CANCELLED_PATH = "/underwriting/was_denied_or_cancelled"


@DEFAULT_RULES.rule(
    id="REFUS_OU_RESILIATION",
    description="Insurance refused, cancelled or not renewed: counts only once the customer confirmed it twice.",
    severity=Severity.STOP,
    reason_fr=(
        "Une assurance vous a été refusée, résiliée ou non renouvelée au cours des 3 dernières années. "
        "Un membre de notre équipe doit donc examiner votre dossier avec vous."
    ),
    reason_en=(
        "You were refused insurance, or a policy was cancelled or not renewed, in the past 3 years. "
        "A member of our team needs to review your file with you."
    ),
)
def refus_ou_resiliation(state: State) -> list[str]:
    """``was_denied_or_cancelled`` is true AND confirmed.

    It is a deterministic field: a value that is still inferred or pending the double confirmation
    never matches, so a misheard "refused" cannot end a quote.
    """
    answered_yes = get_value(state, DENIED_OR_CANCELLED_PATH) is True
    confirmed = get_status(state, DENIED_OR_CANCELLED_PATH) == STATUS_CONFIRMED
    return [DENIED_OR_CANCELLED_PATH] if answered_yes and confirmed else []


YOUNG_DRIVER_AGE_LIMIT = 25  # "younger than 25": the age must be below this
ASSIGNMENTS_PATH = "/household/driver_vehicle_assignments"
POLICYHOLDER_BIRTH_PATH = "/quote/date_of_birth"


def today() -> date:
    """The (UTC) date that age rules compare against. A function so tests can pin it."""
    return datetime.now(timezone.utc).date()


def age_on(born: date, on: date) -> int:
    """Whole years between *born* and *on*; the birthday counts on the day itself."""
    return on.year - born.year - int((on.month, on.day) < (born.month, born.day))


def _parse_date(value: Any) -> date | None:
    """A date from a date, a datetime or an ISO string (a longer timestamp is cut to its date), else None."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value.strip()[:10])  # "2007-03-15", or a longer RFC 3339 timestamp
        except ValueError:
            return None
    return None


def _is_index(value: Any, size: int) -> bool:
    """True when ``value`` is an int (not a bool) that is a valid index into ``size`` entries."""
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value < size


def _birth_path(state: State, driver_index: int) -> str | None:
    """Where the date of birth of driver *driver_index* lives.

    An additional driver has ``driver_date_of_birth``. The policyholder is driver 0 (relationship
    "self") and their date of birth is in the quote entity, not on the driver.
    """
    own = f"/drivers/{driver_index}/driver_date_of_birth"
    if get_value(state, own) is not None:
        return own
    relationship = get_value(state, f"/drivers/{driver_index}/relationship_code")
    if relationship == "self" or (relationship is None and driver_index == 0):
        return POLICYHOLDER_BIRTH_PATH
    return None


def _age_at(state: State, birth_path: str) -> int | None:
    """Age today for the date of birth at ``birth_path``, or None when it is missing, unreadable or in the future."""
    born = _parse_date(get_value(state, birth_path))
    now = today()
    return age_on(born, now) if born is not None and born <= now else None


@DEFAULT_RULES.rule(
    id="JEUNE_CONDUCTEUR_PRINCIPAL",
    description="A driver under 25 is the principal driver of a vehicle: the advisor should look at the file.",
    severity=Severity.FLAG,
    reason_fr=(
        "Un conducteur de moins de 25 ans est le conducteur principal d'un véhicule. "
        "Un membre de notre équipe pourra revoir ce point avec vous."
    ),
    reason_en="A driver under 25 is the main driver of a vehicle. A member of our team may go over this with you.",
)
def jeune_conducteur_principal(state: State) -> list[str]:
    """A driver younger than 25 holds the ``principal`` role on an existing vehicle.

    Matches the date-of-birth path of each such driver, then the assignments path.
    """
    assignments = get_value(state, ASSIGNMENTS_PATH)
    if not _is_list(assignments):
        return []
    driver_count, vehicle_count = count_entities(state, "drivers"), count_entities(state, "vehicles")
    young_birth_paths: list[str] = []
    for item in assignments:
        if not isinstance(item, Mapping) or _normalize(item.get("role")) != "principal":
            continue
        driver, vehicle = item.get("driver_index"), item.get("vehicle_index")
        if not (_is_index(driver, driver_count) and _is_index(vehicle, vehicle_count)):
            continue
        birth_path = _birth_path(state, driver)
        age = _age_at(state, birth_path) if birth_path else None
        if age is not None and age < YOUNG_DRIVER_AGE_LIMIT and birth_path not in young_birth_paths:
            young_birth_paths.append(birth_path)
    return [*young_birth_paths, ASSIGNMENTS_PATH] if young_birth_paths else []


# ---------------------------------------------------------------------------------------------
# Integration example. ILLUSTRATIVE ONLY: nothing above imports or calls it.
#
# `session` stands for the BFF's session object (duck-typed: its methods belong to the BFF, not
# to this module). `rules.hit` and `handoff.created` are SSE events on the BFF-to-frontend hop.
# ---------------------------------------------------------------------------------------------


async def example_commit(session: Any, batch: Any) -> bool:
    """Commit one batch of changes, then run the end-of-form rules on the merged state.

    Returns:
        True when the main agent may run (or keep streaming). False when this turn ends here: for a
        form, document or confirmation input the agent is not invoked; on the chat path the memory
        delta commits mid-turn, so the BFF stops forwarding the main agent's in-flight reply.
    """
    state = await session.merge_and_persist(batch)  # single writer: merge, etag write, state_changes
    result = DEFAULT_RULES.check(state, session.acknowledged)  # synchronous, pure, no model call

    for hit in result.hits:  # STOP first, then FLAG; only hits that are not acknowledged yet
        await session.emit("rules.hit", hit.event_data(session.language))  # no matched values: they can be PII
        session.system_notes.append(f"Rule {hit.rule_id}: {hit.severity.value}")  # carried by the next invocation

    for hit in result.flags:  # FLAG: leave a signal for the advisor and carry on
        session.add_advisor_signal(hit)  # one per rule id: a firmer hit (new fingerprint) updates it
        session.acknowledged.add(hit.fingerprint)  # saved with the session, so a restart does not repeat it

    stop = result.stop
    if stop is None:
        return True  # nothing to stop on: the main agent may run

    if result.needs_confirmation:  # only guessed from chat: ask ONE question before stopping
        await session.ask_rule_confirmation(stop)  # templated, two suggestions: confirm or correct
        session.acknowledged.add(stop.fingerprint)  # asked once; if confirmed, the new hit has a new fingerprint
        return False

    await session.close_open_surfaces()  # deleteSurface for every open A2UI surface
    await session.render_end_of_form(stop)  # reason, rule id, editable summary, contact fields, consent
    session.acknowledged.update(hit.fingerprint for hit in result.hits)  # the hand-off covers them all
    return False


async def example_on_end_of_form_sent(session: Any, form: Any) -> str | None:
    """The customer pressed Send on the end-of-form surface (an ``a2ui.action``).

    ``form`` carries the rule id (None when the customer asked for an advisor), the conversation
    summary as the customer edited it, the contact fields and the consent to transmit. Returns the
    hand-off reference, or None when the consent is missing.
    """
    if not form.consent_to_transmit:  # required: the surface shows an inline error and nothing is created
        return None
    reference = await session.create_handoff(  # a `handoffs` row with the consent timestamp, e.g. "CNS-123456"
        rule_id=form.rule_id,
        summary=form.summary,
        contact=form.contact,
    )  # it also carries the advisor signals collected from FLAG hits
    await session.emit("handoff.created", {"reference": reference, "rule_id": form.rule_id})
    return reference
