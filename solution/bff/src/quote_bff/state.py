"""Session state: the session document, catalog validation, the merge rules and the end-of-form rule glue.

Pure functions over the session document (architecture §6.1): the state entities sit at the root of the document
(``quote``, ``vehicles``, ``drivers``, ``underwriting``, ``household``), every leaf is a field envelope
``{value, source, confidence, status, turn, updated_at[, evidence]}``. Reads go through the rules module helpers
(``get_envelope``, ``get_value``), writes through ``quote_common.pointer``. ``service.Bff.commit`` is the only caller
that persists what these functions change.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from quote_common import pointer
from quote_common.catalog import get_catalog
from quote_common.envelope import now_iso
from quote_common.state import QUOTABLE_MIN_CONFIDENCE, is_usable

from quote_bff.rules import end_of_form as eof

log = logging.getLogger(__name__)
CATALOG = get_catalog()  # the state catalog (contracts/state-catalog.json), loaded once

STATE_KEYS = ("quote", "vehicles", "drivers", "underwriting", "household")  # the entities at the root of the document
# Document key -> catalog entity: ``vehicles`` holds ``vehicle`` entities, ``drivers`` holds ``driver`` entities.
COLLECTION_ENTITY = {"quote": "quote", "vehicles": "vehicle", "drivers": "driver", "underwriting": "underwriting",
                     "household": "household"}
ENTITY_COLLECTION = {v: k for k, v in COLLECTION_ENTITY.items()}  # the reverse map
# Rank of each source, low to high: a lower source never overwrites a confirmed value (events.md rule 1).
PRECEDENCE = {"system": 0, "chat": 1, "lookup": 2, "document": 3, "form": 4}
# Fields only the BFF computes, never a model or a form (events.md rule 4).
DERIVED = frozenset({"years_licensed", "years_as_principal_driver"})
CONSENT = "/quote/contact_consent"  # the consent field (FR-INT-2)
# A state pointer: /<entity>/<key> for quote, underwriting and household, /<collection>/<index>/<key> for the others.
_PATH = re.compile(r"^/(quote|underwriting|household)/([a-z0-9_]+)$|^/(vehicles|drivers)/(\d+)/([a-z0-9_]+)$")
_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")  # deliberately simple: one @, a dot, no spaces
ERRORS = {  # customer-facing (fr-CA), shown under /_errors; keyed by field type or kind of failure
    "email": "Adresse courriel invalide.",
    "phone": "Numéro de téléphone invalide (10 chiffres).",
    "postal_code": "Code postal canadien invalide (ex. H2X 1Y4).",
    "vin": "Le NIV compte 17 caractères, sans I, O ni Q.",
    "date": "Date invalide.",
    "age": "Âge hors des limites permises.",
    "range": "Valeur hors des limites permises.",
    "choice": "Choix invalide.",
    "value": "Valeur invalide.",
}


def new_session(session_id: str, quote_number: str, locale: str) -> dict[str, Any]:
    """A new session document (architecture §6.1).

    The five state entities (``quote``, ``vehicles``, ``drivers``, ``underwriting``, ``household``) hold field
    envelopes. ``id`` and ``session_id`` are the same value (the Cosmos id and its partition key) and ``turn`` counts
    the requests so far. The other keys are the BFF's bookkeeping: ``surfaces`` (the form registry), ``confirmations``
    (pending double confirmations), ``rule_hits``, ``acknowledged``, ``advisor_signals``, ``rule_question``, ``stop``
    and ``rules_error`` (end-of-form rules), ``handoff`` (the sent hand-off), ``signals``, ``conflicts``,
    ``system_notes`` and ``session_preferences`` (what the agents are told), ``offtopic_streak`` (FR-GRD-5),
    ``after_consent`` (the surface to open once the consent is confirmed) and ``documents`` (uploads).
    """
    return {
        "id": session_id, "session_id": session_id, "quote_number": quote_number, "locale": locale,
        "created_at": now_iso(), "phase": "intent", "turn": 0,
        "quote": {}, "vehicles": [], "drivers": [], "underwriting": {}, "household": {},
        "signals": [], "conflicts": [], "session_preferences": {"answer_mode": None}, "surfaces": {},
        "rule_hits": [], "acknowledged": [], "advisor_signals": [], "system_notes": [], "confirmations": {},
        "rule_question": None, "stop": None, "rules_error": False, "handoff": None, "offtopic_streak": 0,
        "after_consent": None, "documents": {},
    }


def state_of(doc: dict[str, Any]) -> dict[str, Any]:
    """The five state entities of a session document: what the agent, the web and mcp-quoting get to see."""
    return {k: doc[k] for k in STATE_KEYS}


def envelope(value: Any, source: str, *, status: str, confidence: float, turn: int,
             evidence: str | None = None) -> dict[str, Any]:
    """A field envelope (events.md): a value with its source, confidence, status, turn and time.

    ``evidence`` is what the value came from: the customer's words, or the document.
    """
    env = {"value": value, "source": source, "confidence": confidence, "status": status, "turn": turn,
           "updated_at": now_iso()}
    if evidence:
        env["evidence"] = evidence
    return env


# ---------------------------------------------------------------------------------------------------------------
# Catalog validation
# ---------------------------------------------------------------------------------------------------------------


def field_for(path: str) -> tuple[dict[str, Any], str, int | None]:
    """Catalog field, collection and index of a state pointer. ValueError when the path is not in the catalog.

    The index is None for ``quote``, ``underwriting`` and ``household``, which have no rows.
    """
    m = _PATH.match(path or "")
    if not m:
        raise ValueError(ERRORS["value"])
    coll, key, idx = (m[1], m[2], None) if m[1] else (m[3], m[5], int(m[4]))
    f = CATALOG.field(key)
    if f is None or f["entity"] != COLLECTION_ENTITY[coll]:
        raise ValueError(ERRORS["value"])
    return f, coll, idx


def _bad(kind: str) -> ValueError:
    """A ValueError that carries the fr-CA message for ``kind``, a key of ``ERRORS``.

    An unknown kind gets the generic message.
    """
    return ValueError(ERRORS.get(kind, ERRORS["value"]))


def _number(value: Any, f: dict[str, Any]) -> int | float:
    """Parse a number for an integer or currency field, within the catalog's ``min`` and ``max``.

    Spaces and a decimal comma are accepted. ``max_offset_years`` makes the maximum relative to the current year.
    """
    if isinstance(value, bool):
        raise _bad("value")
    try:
        n = float(str(value).replace(" ", "").replace(",", "."))
    except ValueError:
        raise _bad("value") from None
    if f["type"] == "integer":
        if n != int(n):
            raise _bad("value")
        n = int(n)
    lo, hi = f.get("min"), f.get("max")
    if "max_offset_years" in f:
        hi = eof.today().year + f["max_offset_years"]
    if (lo is not None and n < lo) or (hi is not None and n > hi):
        raise _bad("range")
    return n


def _date(value: Any, f: dict[str, Any]) -> str:
    """Parse an ISO date (its first 10 characters) and check the catalog limits.

    Limits: the age (``min_age``, ``max_age``, never born in the future) and the offset in days from today.
    """
    try:
        d = date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        raise _bad("date") from None
    today = eof.today()
    if "min_age" in f or "max_age" in f:
        age = eof.age_on(d, today)
        if age < f.get("min_age", 0) or age > f.get("max_age", 200) or d > today:
            raise _bad("age")
    days = (d - today).days
    if days < f.get("min_offset_days", -10**6) or days > f.get("max_offset_days", 10**6):
        raise _bad("range")
    return d.isoformat()


def _codes(f: dict[str, Any], key: str = "enum") -> list[str]:
    """The codes of a coded list of the field: its ``enum`` by default, or another one such as ``role``."""
    return [str(code) for code, _ in f.get(key) or []]


def normalize(f: dict[str, Any], value: Any) -> Any:
    """Validate and normalize one value against its catalog field (FR-MEM-3). ValueError carries the fr-CA message.

    None or an empty string means no value and gives None. Whitespace is collapsed, postal codes get the ``A1A 1A1``
    form, VINs and licence numbers are upper-cased, enums are checked against the catalog codes, numbers and dates
    against their limits.
    """
    if value is None or value == "":
        return None
    t = f["type"]
    if t in ("string", "email", "phone", "postal_code", "vin"):
        if isinstance(value, bool) or not isinstance(value, str | int | float):
            raise _bad(t)
        v = " ".join(str(value).split())
        if t == "postal_code":
            v = v.upper().replace(" ", "").replace("-", "")
            v = f"{v[:3]} {v[3:]}" if len(v) == 6 else v
        if t == "vin" or f["key"] == "driver_license_number":
            v = v.upper()
        if (t == "email" and not _EMAIL.match(v)) or (f.get("pattern") and not re.fullmatch(f["pattern"], v)):
            raise _bad(t)
        if len(v) > f.get("max_length", 500):
            raise _bad("value")
        return v
    if t == "enum":
        if str(value) not in _codes(f):
            raise _bad("choice")
        return str(value)
    if t == "multi_enum":
        if not isinstance(value, list) or any(str(v) not in _codes(f) for v in value):
            raise _bad("choice")
        return [str(v) for v in value]
    if t == "boolean":
        if isinstance(value, bool):
            return value
        if str(value).lower() in ("true", "false"):
            return str(value).lower() == "true"
        raise _bad("choice")
    if t in ("integer", "currency"):
        return _number(value, f)
    if t == "date":
        return _date(value, f)
    if t == "assignment_matrix":
        if value == []:
            return None  # an empty board is no answer (quote_common is_usable treats [] as missing)
        roles = _codes(f["item"], "role")
        ok = isinstance(value, list) and all(
            isinstance(a, dict) and isinstance(a.get("driver_index"), int) and isinstance(a.get("vehicle_index"), int)
            and str(a.get("role")) in roles for a in value)
        if not ok:
            raise _bad("value")
        return value
    if t == "list" and not isinstance(value, list):
        raise _bad("value")
    return value


# ---------------------------------------------------------------------------------------------------------------
# Merge (events.md rules 1-5)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class Change:
    """One proposed change. ``op``: set, clear, add_entity (memory agent); confirm, revert (BFF only)."""

    path: str  # JSON Pointer of the field
    value: Any = None
    op: str = "set"
    confidence: float = 1.0  # from the memory or document agent; merge counts form, lookup and system as 1.0
    evidence: str | None = None  # the customer's words or the document, kept in the envelope
    restore: dict[str, Any] | None = None  # revert: the envelope to put back (None clears the field)


@dataclass
class Applied:
    """A change that was written: ``old`` and ``new`` are the envelopes before and after.

    ``old`` is None when the field was empty.
    """

    path: str
    old: dict[str, Any] | None
    new: dict[str, Any]


@dataclass
class MergeResult:
    """What one ``merge`` call did, sorted by outcome."""

    applied: list[Applied] = field(default_factory=list)  # written to the document, derived fields included
    pending: list[Applied] = field(default_factory=list)  # deterministic: held as pending_confirmation
    conflicts: list[dict[str, Any]] = field(default_factory=list)  # a lower source met a confirmed value (rule 1)
    rejected: list[dict[str, Any]] = field(default_factory=list)  # failed catalog validation: {path, reason}
    withheld: list[str] = field(default_factory=list)  # FR-INT-2: PII before the contact consent is confirmed
    structural: bool = False  # an entity was added

    @property
    def changes(self) -> list[Applied]:
        """Everything written: the applied and the pending changes."""
        return self.applied + self.pending

    @property
    def changed(self) -> bool:
        """True when the merge changed the document at all, a new entity included."""
        return bool(self.applied or self.pending or self.structural)


def status_for(source: str, confidence: float, *, reviewed: bool = False) -> str:
    """form, system, lookup and a reviewed document are confirmed; chat, an unreviewed document and any change under
    the rule 3 threshold stay inferred."""
    firm = source in ("form", "system", "lookup") or (source == "document" and reviewed)
    return "confirmed" if firm and confidence >= QUOTABLE_MIN_CONFIDENCE else "inferred"


def ensure_entity(doc: dict[str, Any], collection: str, index: int) -> bool:
    """Make sure ``doc[collection][index]`` exists, padding with empty entities; True when it had to be created.

    Raises ValueError (``range``) at or beyond the entity's ``max`` in the catalog.
    """
    limit = CATALOG.raw["entities"][COLLECTION_ENTITY[collection]].get("max", 25)
    if index >= limit:
        raise _bad("range")
    items = doc[collection]
    created = len(items) <= index
    while len(items) <= index:
        items.append({})
    if items[index] is None:
        items[index] = {}
    return created


def has_consent(doc: dict[str, Any]) -> bool:
    """True once ``/quote/contact_consent`` is true and confirmed, after the double confirmation (FR-INT-2)."""
    return eof.get_value(doc, CONSENT) is True and eof.get_status(doc, CONSENT) == "confirmed"


def merge(doc: dict[str, Any], changes: list[Change], source: str, turn: int, *, reviewed: bool = False,
          pii_ok: bool = False) -> MergeResult:
    """Apply one batch from one source to the session document, then recompute the derived fields.

    Per change (events.md rules 1 to 4): a bad path or value is ``rejected``; only the ``system`` source may write
    ``system`` and derived fields; a lower source never overwrites a confirmed value (it becomes a ``conflict``); the
    same value from a firmer source upgrades the envelope; a deterministic field from any source but ``system`` is
    held as ``pending_confirmation``. The ops ``confirm`` and ``revert`` close such a pending value. ``reviewed``
    marks a document the customer checked, so it counts as confirmed.

    FR-INT-2: a value for a ``pii`` catalog field is withheld (not written) until the contact consent is confirmed,
    unless ``pii_ok`` (the end_of_form hand-off, which carries its own consent).

    Edits ``doc`` in place; writing it to the store is the caller's job (``Bff.commit``).
    """
    out = MergeResult()
    consent = pii_ok or has_consent(doc)
    for ch in changes:
        try:
            f, coll, idx = field_for(ch.path)
            model_written = source != "system" and ch.op in ("set", "clear", "add_entity")
            if model_written and (f.get("system") or f["key"] in DERIVED):
                raise _bad("value")
            value = ch.value if ch.op in ("confirm", "revert") else None if ch.op == "clear" else normalize(f, ch.value)
            if f.get("pii") and not consent and value is not None and ch.op in ("set", "add_entity"):
                out.withheld.append(ch.path)
                continue
            if idx is not None:
                out.structural |= ensure_entity(doc, coll, idx)
        except ValueError as exc:
            out.rejected.append({"path": ch.path, "reason": str(exc)})
            continue
        cur = eof.get_envelope(doc, ch.path)
        cur = dict(cur) if cur is not None else None
        confidence = 1.0 if source in ("form", "lookup", "system") else ch.confidence
        status = status_for(source, confidence, reviewed=reviewed)
        if value is None and ch.op in ("set", "clear", "add_entity") and (cur or {}).get("value") is None:
            continue  # nothing to clear
        if ch.op == "revert":
            new = ch.restore or envelope(None, source, status="inferred", confidence=1.0, turn=turn)
        elif ch.op == "confirm":
            if cur is None:
                continue
            new = {**cur, "status": "confirmed", "confidence": 1.0, "turn": turn, "updated_at": now_iso()}
        elif cur is not None and cur.get("value") == value:
            upgrade = PRECEDENCE[source] > PRECEDENCE.get(cur.get("source"), 0) or (
                cur.get("status") == "inferred" and status == "confirmed") or (
                cur.get("source") == source and confidence > cur.get("confidence", 1.0))
            if cur.get("status") == "pending_confirmation" or not upgrade:
                continue
            new = envelope(value, source, status=status, confidence=confidence, turn=turn, evidence=ch.evidence)
        elif cur and cur.get("status") == "confirmed" and PRECEDENCE[source] < PRECEDENCE.get(cur.get("source"), 0):
            out.conflicts.append({"path": ch.path, "current_value": cur.get("value"), "stated_value": value,
                                  "source": source, "evidence": ch.evidence})
            continue
        else:
            new = envelope(value, source, status=status, confidence=confidence, turn=turn, evidence=ch.evidence)
            if f.get("deterministic") and source != "system":
                new["status"] = "pending_confirmation"
                pointer.set(doc, ch.path, new)
                out.pending.append(Applied(ch.path, cur, new))
                continue
        pointer.set(doc, ch.path, new)
        out.applied.append(Applied(ch.path, cur, new))
    if out.changed:
        out.applied += derive(doc, turn)
    return out


def derive(doc: dict[str, Any], turn: int) -> list[Applied]:
    """Rule 4: derived fields, and driver 0 mirroring the policyholder. Computed here, never by a model."""
    want: dict[str, Any] = {}
    names = [eof.get_value(doc, f"/quote/{k}") for k in ("first_name", "last_name")]
    if any(names):
        ensure_entity(doc, "drivers", 0)
        want["/drivers/0/driver_name"] = " ".join(n for n in names if n)
        if eof.get_value(doc, "/drivers/0/relationship_code") is None:
            want["/drivers/0/relationship_code"] = "self"
    for i in range(len(doc["drivers"])):
        licensed = eof.get_value(doc, f"/drivers/{i}/license_year")
        want[f"/drivers/{i}/years_licensed"] = eof.today().year - licensed if isinstance(licensed, int) else None
        became = eof.get_value(doc, f"/drivers/{i}/age_became_principal_driver")
        birth = eof._birth_path(doc, i)  # one definition of "whose birth date"
        age = eof._age_at(doc, birth) if birth else None
        principal = isinstance(became, int) and age is not None and age >= became
        want[f"/drivers/{i}/years_as_principal_driver"] = age - became if principal else None
    out = []
    for path, value in want.items():
        cur = eof.get_envelope(doc, path)
        if (cur or {}).get("value") == value:
            continue
        new = envelope(value, "system", status="confirmed", confidence=1.0, turn=turn)
        out.append(Applied(path, dict(cur) if cur else None, new))
        pointer.set(doc, path, new)
    return out


def missing_for_quote(doc: dict[str, Any]) -> list[dict[str, str]]:
    """Required-for-quote paths whose value is not usable for pricing (``quote_common.state.is_usable``, rule 3), in
    catalog order, in mcp-quoting's shape. The assignments are not needed for one driver on one vehicle."""
    counts = {c: max(1, len(doc[c])) for c in ("vehicles", "drivers")}
    missing = []
    for key, f in CATALOG.fields.items():
        if not f.get("required_for_quote") or (key == "driver_vehicle_assignments" and set(counts.values()) == {1}):
            continue
        coll = ENTITY_COLLECTION[f["entity"]]
        for i in range(counts.get(coll, 1)):
            path = f"/{coll}/{i}/{key}" if coll in counts else f"/{coll}/{key}"
            env = eof.get_envelope(doc, path)
            if is_usable(dict(env) if env else None, key):
                continue
            empty = not env or env.get("value") in (None, "", [], {})
            reason = "missing" if empty else env.get("status") or "unconfirmed"
            missing.append({"path": path, "key": key, "label": CATALOG.label(key), "reason": reason})
    return missing


# ---------------------------------------------------------------------------------------------------------------
# End-of-form rules (events.md rule 6; the rules module docstring and example_commit)
# ---------------------------------------------------------------------------------------------------------------


@dataclass
class RuleOutcome:
    """What ``apply_rules`` decided for one batch; ``Bff.commit`` acts on it."""

    action: str = "go"  # go: the agent may run; ask: one confirmation question; stop: end_of_form
    hits: list[eof.RuleHit] = field(default_factory=list)  # new hits, STOP first
    stop: eof.RuleHit | None = None  # the STOP hit behind ``ask`` or ``stop``


def hit_summary(hit: eof.RuleHit) -> dict[str, Any]:
    """A hit as stored in the session (``rule_hits``, advisor signals).

    The matched values are left out: they can be personal data.
    """
    return {"rule_id": hit.rule_id, "severity": hit.severity.value, "paths": list(hit.paths), "inferred": hit.inferred}


def apply_rules(doc: dict[str, Any], applied: list[Applied]) -> RuleOutcome:
    """Check the merged state once for this batch and book the result in the session document.

    Runs every end-of-form rule (FR-RUL-1, events.md rule 6) and returns ``go`` (the agent may run), ``ask`` (the
    STOP is only inferred: ask one confirmation question first, FR-RUL-3) or ``stop`` (a firm STOP: render
    end_of_form, FR-RUL-4). FLAG hits become advisor signals and never stop the journey. ``applied`` gives the old
    values that a "no" to the question puts back. If a rule raises, the commit stands, ``rules_error`` is set and
    pricing stays blocked until a later check passes.
    """
    try:
        result = eof.DEFAULT_RULES.check(doc, frozenset(doc["acknowledged"]))
        active = eof.DEFAULT_RULES.check(doc)
    except Exception:  # architecture §16: the commit stands, get_quote is blocked until a later check passes
        log.exception("end-of-form rules failed", extra={"session_id": doc["session_id"]})
        doc["rules_error"] = True
        return RuleOutcome()
    doc["rules_error"] = False
    doc["rule_hits"] = [hit_summary(h) for h in active.hits]
    ack = doc["acknowledged"]
    for hit in result.hits:
        doc["system_notes"].append(f"Rule {hit.rule_id}: {hit.severity.value}")
    for hit in result.flags:  # one advisor signal per rule id; a firmer hit replaces it
        doc["advisor_signals"] = [s for s in doc["advisor_signals"] if s["rule_id"] != hit.rule_id]
        doc["advisor_signals"].append({**hit_summary(hit), "reason": hit.reason(doc["locale"])})
        ack.append(hit.fingerprint)
    if doc["stop"] and not any(h.severity is eof.Severity.STOP and not h.inferred for h in active.hits):
        doc["stop"] = None  # a commit that clears the hit lifts the STOP
    stop = result.stop
    if stop is None:
        return RuleOutcome("go", list(result.hits))
    if result.needs_confirmation:
        ack.append(stop.fingerprint)
        olds = {a.path: a.old for a in applied}
        doc["rule_question"] = {"rule_id": stop.rule_id, "paths": list(stop.paths),
                                "restore": {p: olds.get(p) for p in stop.paths}}
        return RuleOutcome("ask", list(result.hits), stop)
    ack.extend(h.fingerprint for h in result.hits if h.fingerprint not in ack)
    doc["stop"] = {"rule_id": stop.rule_id, "reason": stop.reason(doc["locale"]), "paths": list(stop.paths)}
    doc["rule_question"] = None
    return RuleOutcome("stop", list(result.hits), stop)
