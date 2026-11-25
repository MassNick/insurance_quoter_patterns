"""The BFF core: one event emitter (``Turn.emit``), one commit path (``Bff.commit``), one surface-render helper
(``Bff.render``), and the inbound handlers (events.md). The BFF is the only writer of session state.
"""

from __future__ import annotations

import asyncio
import logging
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator, Callable
from typing import Any
from weakref import WeakValueDictionary

from jsonschema import Draft202012Validator
from opentelemetry import trace
from quote_common import pointer, session_ref
from quote_common.contracts import load_catalog, load_schema, load_tools
from quote_common.envelope import Envelope, now_iso, sse_encode
from quote_common.logs import redact

from quote_bff import forms, state
from quote_bff.clients import UpstreamError, a2ui_messages, tool_value
from quote_bff.config import Settings
from quote_bff.forms import END_OF_FORM, FULL_FORM, a2ui, text
from quote_bff.rules.end_of_form import RuleHit, get_envelope, get_value
from quote_bff.state import Change
from quote_bff.stores import Conflict, Store
from quote_bff.suggestions import filter_suggestions

log = logging.getLogger(__name__)
tracer = trace.get_tracer("quote_bff")  # a no-op tracer unless an OpenTelemetry SDK is configured
# Validate the output of the memory agent (a memory.result that fails is ignored) and of the document agent (FR-DOC-3).
MEMORY_SCHEMA = Draft202012Validator(load_schema("memory-output.schema.json"))
DOCUMENT_SCHEMA = Draft202012Validator(load_schema("document-output.schema.json"))
# Document field name -> state pointer, from document-output.schema.json; ``{i}`` stands for the row index.
FIELD_MAP: dict[str, str] = load_schema("document-output.schema.json")["field_map"]
# Seconds a double confirmation may wait before it expires (FR-DET-4), from the catalog's deterministic_policy.
PENDING_TTL = load_catalog()["deterministic_policy"]["pending_ttl_seconds"]
CONSENT = "/quote/contact_consent"  # the consent field (FR-INT-2)
# The main agent's reply: held until memory.result arrives, and dropped after a STOP or a rule question.
VISIBLE = {"text.delta", "text.done", "a2ui.message", "suggestions"}
# Button names that only close a form: no commit and no agent call.
CLOSE_ACTIONS = {"dismiss", "cancel", "back", "nodocs", "edit_answers"}
# A2UI renderer function name -> the mcp-quoting tool it calls.
FUNCTIONS = {"lookupMakeModelYear": "lookup_make_model_year", "identifyVehicleType": "identify_vehicle_type",
             "decodeVin": "decode_vin", "identifyAddress": "identify_address"}
# The arguments each quoting tool declares (tools.json): anything else the renderer sends is dropped.
TOOL_ARGS = {t["name"].split(".", 1)[1]: set(t["inputSchema"].get("properties", {})) for t in load_tools()["tools"]
             if t["name"].startswith("quoting.")}
# 13 to 19 digits, once spaces and dashes are removed: a payment card number when the Luhn check also passes.
CARD = re.compile(r"^\d{13,19}$")


def traceparent() -> str:
    """The W3C traceparent of the current span, or a fresh one when no SDK is recording."""
    ctx = trace.get_current_span().get_span_context()
    if ctx.is_valid:
        return f"00-{ctx.trace_id:032x}-{ctx.span_id:016x}-{ctx.trace_flags:02x}"
    return f"00-{secrets.token_hex(16)}-{secrets.token_hex(8)}-01"


def invocation_body(doc: dict[str, Any], turn: int, input_: dict[str, Any], ref: str) -> dict[str, Any]:
    """The body of one invocation (events.md "BFF → hosted agent").

    ``input_`` is what the turn is about and ``ref`` a fresh ``session_ref``. ``open_surfaces`` are the ids of the forms
    still open; ``system_notes`` are what happened since the agent last ran, which it reads before the input.
    """
    return {"turn": turn, "input": input_, "state": state.state_of(doc), "phase": doc["phase"],
            "open_surfaces": [k for k, s in doc["surfaces"].items() if s["status"] == "open"],
            "signals": doc["signals"], "preferences": dict(doc["session_preferences"]),
            "rule_hits": doc["rule_hits"], "system_notes": list(doc["system_notes"]), "session_ref": ref}


def public_state(doc: dict[str, Any]) -> dict[str, Any]:
    """What ``GET /state`` returns: state, preferences, signals, advisor signals, quote number, chips."""
    return {"state": state.state_of(doc), "preferences": doc["session_preferences"], "signals": doc["signals"],
            "advisor_signals": doc["advisor_signals"], "quote_number": doc["quote_number"],
            "surfaces": [{k: s[k] for k in ("surface_id", "title", "chip", "status")}
                         for s in doc["surfaces"].values()]}


def _luhn(digits: str) -> bool:
    """Luhn checksum of a digit string: how a payment card number is told from any other long number."""
    total = sum(int(d) if i % 2 == 0 else sum(divmod(int(d) * 2, 10)) for i, d in enumerate(reversed(digits)))
    return total % 10 == 0


def _strip_nulls(node: Any) -> Any:
    """``node`` without any dict entry whose value is None, at every depth: an unread field is absent, not null."""
    if isinstance(node, dict):
        return {k: _strip_nulls(v) for k, v in node.items() if v is not None}
    return [_strip_nulls(v) for v in node] if isinstance(node, list) else node


def check_document(out: Any) -> dict[str, Any]:
    """Check a document agent output: nulls dropped, card numbers redacted, schema enforced.

    Nulls are dropped (unread fields are absent), a payment card number (13 to 19 digits that pass the Luhn check)
    comes back as ``redacted`` (FR-DOC-6), and the output must match its schema (FR-DOC-3). Raises UpstreamError
    ``document_invalid`` when it does not.
    """
    out = _strip_nulls(out)
    for f in (out.get("fields") or {}).values() if isinstance(out, dict) else []:
        digits = re.sub(r"[ -]", "", str(f.get("value", "")))
        if isinstance(f.get("value"), str) and CARD.match(digits) and _luhn(digits):
            f["value"] = "redacted"
    if not isinstance(out, dict) or next(DOCUMENT_SCHEMA.iter_errors(out), None) is not None:
        raise UpstreamError("document_invalid", "document output does not match its schema", retryable=False)
    return out


def _register(doc: dict[str, Any], message: dict[str, Any], *, spec: dict[str, Any] | None, origin: str, mode: str,
              meta: dict[str, Any] | None = None) -> None:
    """Surface registry entry (§6.1): title, chip, origin, status, FormSpec, bound paths.

    Registers (or registers again) a surface as ``open`` from its compiled createSurface. The title is also the chip
    text; ``origin`` (agent or bff) is the first one seen; ``mode`` is new or edit; the FormSpec is kept for reopening
    and ``previous_status`` lets Cancel put the chip back.
    """
    cs = message["createSurface"]
    sid = cs["surfaceId"]
    prev = doc["surfaces"].get(sid, {})
    title = (spec or {}).get("title") or next(
        (c.get("text", "").lstrip("# ") for c in cs.get("components", []) if c.get("id") == "title"), sid)
    doc["surfaces"][sid] = {**prev, **(meta or {}), "surface_id": sid, "title": title, "chip": title,
                            "origin": prev.get("origin", origin), "status": "open", "mode": mode,
                            "previous_status": prev.get("status"), "form_spec": spec or prev.get("form_spec"),
                            "bound": forms.bound_paths(message)}


class Turn:
    """One inbound request: the turn number and the one emitter (assign seq, persist to ``events``, forward)."""

    def __init__(self, store: Store, session_id: str, turn: int) -> None:
        """A turn of session ``session_id`` with its number.

        ``sent`` collects what is forwarded to the customer; ``queue`` feeds the SSE response.
        """
        self.store, self.sid, self.turn = store, session_id, turn
        self.traceparent = traceparent()
        self.seq = 0
        self.sent: list[dict[str, Any]] = []
        self.queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()

    async def emit(self, type_: str, data: dict[str, Any], actor: str = "bff", *, forward: bool = True,
                   ts: str | None = None) -> None:
        """The one emitter: seq + 1, write the row to ``events``, then forward it (FR-LOG-1).

        ``forward=False`` stores the event only (the inbound event, ``memory.result``, a dropped reply). ``ts`` keeps
        the producer's timestamp.
        """
        self.seq += 1
        env = Envelope(type=type_, session_id=self.sid, turn=self.turn, seq=self.seq, actor=actor, data=data,
                       **({"ts": ts} if ts else {})).public()
        row = {**env, "id": f"{self.turn}:{self.seq}", "forwarded": forward, "trace_id": self.traceparent.split("-")[1]}
        await self.store.put("events", row, create=True)  # FR-LOG-1: persisted before it is forwarded
        if forward:
            self.sent.append(env)
            self.queue.put_nowait(env)

    async def stream(self) -> AsyncIterator[bytes]:
        """The SSE body: each forwarded envelope as a ``data:`` frame, until ``close``."""
        while (env := await self.queue.get()) is not None:
            yield sse_encode(env)

    def close(self) -> None:
        """End the stream; call it after the last event."""
        self.queue.put_nowait(None)


class Bff:
    """The BFF core: it owns the session state and handles one inbound event per ``Turn``.

    Every dependency is injected (stores, hosted agent, MCP clients, document extractor), so the tests and a local run
    use the fakes. The methods are grouped below: sessions, the one commit path, surfaces, inbound handlers, documents
    and renderer functions, the hosted-agent relay.
    """

    def __init__(self, settings: Settings, store: Store, blobs: Any, agent: Any, a2ui_mcp: Any, quoting: Any,
                 documents: Any) -> None:
        """Wire the adapters (see the README table "Adapters and how to swap them").

        ``store`` and ``blobs`` persist, ``agent`` is the hosted agent, ``a2ui_mcp`` and ``quoting`` are the MCP
        clients and ``documents`` reads uploads. ``locks`` serialises the writes of one session (an entry vanishes
        when unused); ``tasks`` keeps the running turns alive.
        """
        self.settings, self.store, self.blobs = settings, store, blobs
        self.agent, self.a2ui, self.quoting, self.documents = agent, a2ui_mcp, quoting, documents
        self.locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()  # dropped when unused
        self.tasks: set[asyncio.Task[None]] = set()

    # -- sessions ---------------------------------------------------------------------------------------------

    def session_ref(self, sid: str) -> str:
        """A signed ``session_ref`` for ``sid``, which mcp-quoting verifies; valid ``session_ref_ttl_seconds``."""
        return session_ref.sign(sid, self.settings.session_ref_key.encode(), self.settings.session_ref_ttl_seconds)

    async def load(self, sid: str) -> dict[str, Any]:
        """The session document, with its ``_etag``. Raises KeyError for an unknown session."""
        doc = await self.store.get("sessions", sid, sid)
        if doc is None:
            raise KeyError(sid)
        return doc

    async def mutate(self, sid: str, fn: Callable[[dict[str, Any]], Any]) -> tuple[dict[str, Any], Any]:
        """Load, change, write with the etag; on a conflict reload and re-apply (rule 5).

        ``fn`` edits the document in place and may run more than once, so it must not have side effects. Returns the
        stored document and ``fn``'s result. Writes of one session are serialised in this process by ``locks``. Raises
        Conflict after three failed writes.
        """
        async with self.locks.setdefault(sid, asyncio.Lock()):
            for _ in range(3):
                doc = await self.load(sid)
                result = fn(doc)
                try:
                    return await self.store.put("sessions", doc, etag=doc["_etag"]), result
                except Conflict:
                    log.warning("session etag conflict, retrying", extra={"session_id": sid})
        raise Conflict(sid)

    async def create_session(self, locale: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        """Create a session and its greeting (FR-INT-1): a templated ``text.done`` and generic suggestions.

        Returns the new document and the greeting events (turn 0), which the route sends in the response body.
        """
        sid = "s_" + secrets.token_hex(12)
        doc = state.new_session(sid, f"AUTO-{time.gmtime().tm_year}-{secrets.randbelow(10**6):06d}", locale)
        doc = await self.store.put("sessions", doc, create=True)
        t = Turn(self.store, sid, 0)
        items = [{"label": text(doc, k), "kind": "reply"} for k in ("sugg_quote", "sugg_compare", "sugg_question")]
        items.append({"label": text(doc, "sugg_advisor"), "kind": "action", "action_id": "talk_to_advisor"})
        await self.say(t, text(doc, "greeting"), filter_suggestions(items, doc)[0])
        return doc, t.sent

    async def warmup(self, sid: str) -> None:
        """Starts the hosted-agent session before the first message (input.kind=warmup, no model call)."""
        try:
            doc = await self.load(sid)
            body = invocation_body(doc, 0, {"kind": "warmup"}, self.session_ref(sid))
            async for _ in self.agent.invoke(body, session_id=sid, turn_id=f"{sid}:0", traceparent=traceparent()):
                pass
        except Exception:
            log.warning("warmup failed", exc_info=True, extra={"session_id": sid})

    async def open_turn(self, sid: str) -> Turn:
        """Allocate the next turn number (one per request, BFF-only ones too) and return its ``Turn``."""
        doc, _ = await self.mutate(sid, lambda d: d.update(turn=d["turn"] + 1))
        return Turn(self.store, sid, doc["turn"])

    def start(self, t: Turn, inbound: dict[str, Any]) -> None:
        """Run the inbound event as a background task, so the SSE response can stream while it works."""
        task = asyncio.create_task(self.run(t, inbound))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def run(self, t: Turn, inbound: dict[str, Any]) -> None:
        """Handle one inbound event: store it (not forwarded), expire old confirmations, then call its handler.

        An UpstreamError, or any other failure, becomes an ``error`` event with a generic message. The stream always
        closes.
        """
        kind, data = inbound["type"], inbound.get("data") or {}
        handlers = {"user.message": self.on_message, "a2ui.action": self.on_action, "ui.surface_reopen": self.on_reopen,
                    "confirm.response": self.on_confirm, "document.uploaded": self.on_document}
        try:
            with tracer.start_as_current_span("bff.turn", attributes={"turn": t.turn, "type": kind}):
                await t.emit(kind, data, "customer" if kind == "user.message" else "frontend", forward=False)
                await self.expire_confirmations(t)
                await handlers[kind](t, data)
        except UpstreamError as exc:
            log.warning("upstream error", extra={"session_id": t.sid, "code": exc.code})
            await self.fail(t, exc.code, retryable=exc.retryable)
        except Exception:
            log.exception("turn failed", extra={"session_id": t.sid, "turn": t.turn})
            await self.fail(t, "internal", retryable=True)
        finally:
            t.close()

    async def say(self, t: Turn, message: str, suggestions: list[dict[str, Any]] | None = None) -> None:
        """A templated BFF message: text.done (actor bff, no deltas), then its suggestions."""
        await t.emit("text.done", {"text": message})
        if suggestions is not None:
            await t.emit("suggestions", {"items": suggestions})

    async def fail(self, t: Turn, code: str, *, retryable: bool = False) -> None:
        """The ``error`` event: a code for the trace, a generic customer message, never internals."""
        await t.emit("error", {"code": code, "message": forms.STRINGS["fr"]["try_again"], "retryable": retryable})

    # -- the one commit path ------------------------------------------------------------------------------------

    async def commit(self, t: Turn, changes: list[Change], source: str, actor: str, *, reviewed: bool = False,
                     surface_id: str | None = None, rule_id: str | None = None,
                     extra: Callable[[dict[str, Any]], None] | None = None) -> str:
        """merge → derive → persist (sessions, state_changes) → rules → events, for every source.

        Returns ``go`` (the agent may run), ``ask`` (a rule confirmation question was asked) or ``stop``.
        ``extra`` changes the session document in the same write (bookkeeping that belongs to this batch).
        ``surface_id`` is the form the values came from: live autofill skips it, and ``END_OF_FORM`` lifts the consent
        gate for its contact fields. ``rule_id`` is recorded on the ``state_changes`` rows.

        Events.md rules 2, 5 and 6; FR-LOG-2, FR-RUL-1. Emits ``state.delta``, ``confirm.required``, live autofill and
        ``rules.hit``, then asks the rule question or renders end_of_form.
        """

        def apply(doc: dict[str, Any]) -> tuple[state.MergeResult, state.RuleOutcome, str | None]:
            """The session write: ``extra``, merge, notes, one confirmation hold, then the rules.

            The notes are for withheld values (FR-INT-2) and conflicts. It may run again after an etag conflict, so it
            has no side effect beyond the document it is given.
            """
            if extra:
                extra(doc)
            with tracer.start_as_current_span("bff.merge"):
                out = state.merge(doc, changes, source, t.turn, reviewed=reviewed, pii_ok=surface_id == END_OF_FORM)
            if out.withheld:  # FR-INT-2: nothing is stored or streamed for them; the agent asks again after consent
                doc["system_notes"].append("Not saved, the contact consent is not confirmed yet (ask again after "
                                           f"consent): {', '.join(out.withheld)}")
            cid = self._hold(doc, out.pending) if out.pending else None
            for c in out.conflicts:
                doc["conflicts"].append(c)
                doc["system_notes"].append(f"Conflict on {c['path']}: the confirmed value {c['current_value']!r} "
                                           f"was kept; {c['source']} said {c['stated_value']!r}")
            with tracer.start_as_current_span("bff.rules"):
                rules = state.apply_rules(doc, out.applied) if out.changed else state.RuleOutcome()
            return out, rules, cid

        doc, (out, rules, cid) = await self.mutate(t.sid, apply)
        for r in out.rejected:
            log.warning("change rejected", extra={"session_id": t.sid, "path": r["path"], "source": source})
        if not out.changed:
            return "go"
        for a in out.changes:
            await self.store.put("state_changes", {
                "id": uuid.uuid4().hex, "session_id": t.sid, "path": a.path, "old": (a.old or {}).get("value"),
                "new": a.new["value"], "source": a.new["source"], "status": a.new["status"], "turn": t.turn,
                "actor": actor, "trace_id": t.traceparent.split("-")[1], "surface_id": surface_id,
                "rule_id": rule_id, "ts": now_iso()}, create=True)
        await t.emit("state.delta", {"changes": [{"path": a.path, "value": a.new["value"], "source": a.new["source"],
                                                  "status": a.new["status"]} for a in out.changes]})
        if cid:
            c = doc["confirmations"][cid]
            first = c["paths"][0]
            await self.store.put("confirmations", {**c, "id": cid, "session_id": t.sid})
            await t.emit("confirm.required", {
                "confirmation_id": cid, "path": first["path"], "old_value": (first["old"] or {}).get("value"),
                "new_value": first["new_value"], "reason": text(doc, "confirm_reason"), "steps": 2,
                "changes": [{"path": p["path"], "old_value": (p["old"] or {}).get("value"),
                             "new_value": p["new_value"]} for p in c["paths"]]})
        await self.autofill(t, doc, out.changes, skip=surface_id)
        for hit in rules.hits:
            await t.emit("rules.hit", hit.event_data(doc["locale"]))
        if rules.action == "ask":
            await self.ask_rule_confirmation(t, doc, rules.stop)
        elif rules.action == "stop":
            await self.end_of_form(t, rules.stop.reason(doc["locale"]), rules.stop.rule_id)
        return rules.action

    def _hold(self, doc: dict[str, Any], pending: list[state.Applied]) -> str:
        """Rule 2: one confirmation for the deterministic fields of one batch; it supersedes older ones.

        Returns its id. It keeps the text shown and the old and new values (FR-DET-5); the caller stores the row once
        the session write has succeeded. An older pending confirmation on the same paths is marked ``superseded``.
        """
        paths = {a.path for a in pending}
        for c in doc["confirmations"].values():
            if c["status"] == "pending" and paths & {p["path"] for p in c["paths"]}:
                c["status"] = "superseded"
        cid = "cf_" + secrets.token_hex(6)
        lines = [" → ".join(forms.label_of(doc, a.path, v)[1] for v in ((a.old or {}).get("value"), a.new["value"]))
                 for a in pending]
        doc["confirmations"] = {k: v for k, v in doc["confirmations"].items() if v["status"] == "pending"}
        doc["confirmations"][cid] = {
            "confirmation_id": cid, "status": "pending", "created_at": now_iso(), "created_ts": time.time(),
            "step1_at": None, "step2_at": None, "text_shown": text(doc, "confirm_reason") + " " + "; ".join(lines),
            "source": pending[0].new["source"],
            "paths": [{"path": a.path, "old": a.old, "new_value": a.new["value"]} for a in pending]}
        return cid

    async def autofill(self, t: Turn, doc: dict[str, Any], changes: list[state.Applied], skip: str | None) -> None:
        """Live autofill: updateDataModel (value, then provenance) on every open surface bound to a changed path.

        ``skip`` is the surface the values came from, which already shows them. The provenance is only sent for the
        sources ``chat``, ``document`` and ``lookup`` (FR-A2UI-7).
        """
        for sid, s in doc["surfaces"].items():
            if s["status"] != "open" or sid == skip:
                continue
            for a in changes:
                if not forms.is_bound(a.path, s["bound"]):
                    continue
                await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=sid, path=a.path, value=a.new["value"]))
                if a.new["source"] in ("chat", "document", "lookup"):
                    prov = {"source": a.new["source"], "note": a.new.get("evidence")}
                    await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=sid,
                                                      path=pointer.provenance_path(a.path), value=prov))

    async def ask_rule_confirmation(self, t: Turn, doc: dict[str, Any], hit: RuleHit) -> None:
        """Ask the rule question (FR-RUL-3): a templated text naming the field and the value found.

        It comes with two action suggestions: ``rule_confirm`` and ``rule_correct``.
        """
        label, value = forms.label_of(doc, hit.paths[0], hit.values[hit.paths[0]])
        await self.say(t, text(doc, "rule_question", value=value, label=label), [
            {"label": text(doc, "rule_yes"), "kind": "action", "action_id": "rule_confirm"},
            {"label": text(doc, "rule_no"), "kind": "action", "action_id": "rule_correct"}])

    # -- surfaces -----------------------------------------------------------------------------------------------

    async def render(self, t: Turn, spec: dict[str, Any], *, mode: str = "new", stored: dict[str, Any] | None = None,
                     meta: dict[str, Any] | None = None) -> bool:
        """The one BFF render: mcp-a2ui render_form with the committed state, register, relay. False on failure.

        ``mode`` is ``new`` or ``edit``. ``stored`` is the FormSpec to remember when it differs from ``spec`` (a
        reopened chip keeps its original); ``meta`` adds fields to the surface's registry entry (the reason of
        end_of_form).
        """
        doc = await self.load(t.sid)
        try:
            result = await self.a2ui.call("render_form", {"form_spec": spec, "state": state.state_of(doc),
                                                          "mode": mode, "session_ref": self.session_ref(t.sid)})
            message = next(m for m in a2ui_messages(result) if "createSurface" in m)
        except (UpstreamError, StopIteration, OSError, ValueError):
            log.warning("mcp-a2ui render failed", exc_info=True, extra={"session_id": t.sid})
            return False
        await self.mutate(t.sid, lambda d: _register(d, message, spec=stored or spec, origin="bff", mode=mode,
                                                     meta=meta))
        await t.emit("a2ui.message", message)
        return True

    async def close(self, t: Turn, ids: list[str], status: str | None, note: str | None = None) -> None:
        """deleteSurface for each id; the chip becomes ``status`` (None: back to its status before the reopen).

        ``note`` is a system note the agent reads on its next turn.
        """

        def fn(d: dict[str, Any]) -> None:
            """Set the chips' status and add the note, in one session write."""
            for k in ids:
                s = d["surfaces"][k]
                s["status"] = status or (s["previous_status"] if s["previous_status"] in ("submitted", "closed")
                                         else "closed")
            if note:
                d["system_notes"].append(note)

        await self.mutate(t.sid, fn)
        for k in ids:
            await t.emit("a2ui.message", a2ui("deleteSurface", surfaceId=k))

    async def end_of_form(self, t: Turn, reason: str, rule_id: str | None) -> None:
        """Close every open surface, then render end_of_form (reason, rule id, summary, contact, consent).

        FR-RUL-4. ``rule_id`` is None for a customer request. If mcp-a2ui fails, the reason is said in chat with a
        ``talk_to_advisor`` suggestion instead. No model call (FR-RUL-6).
        """
        doc = await self.load(t.sid)
        await self.close(t, [k for k, s in doc["surfaces"].items() if s["status"] == "open"], "closed")
        if rule_id:
            await self.say(t, text(doc, "stop_intro"))
        doc = await self.load(t.sid)
        spec = forms.end_of_form_spec(doc, reason, rule_id)
        if not await self.render(t, spec, meta={"rule_id": rule_id, "reason": reason}):
            await self.say(t, reason, [{"label": text(doc, "sugg_advisor"), "kind": "action",
                                        "action_id": "talk_to_advisor"}])

    # -- inbound handlers ---------------------------------------------------------------------------------------

    async def on_message(self, t: Turn, data: dict[str, Any]) -> None:
        """Handle ``user.message``: typed text, a clicked suggestion or a button (``action_id``).

        The BFF answers alone to ``talk_to_advisor`` (FR-RUL-8), ``consent_yes``, the two rule answers (FR-RUL-3), a
        ``get_quote`` while a STOP stands (FR-RUL-4) and, after three off-topic turns in a row, a canned redirect
        (FR-GRD-5). Everything else goes to the hosted agent as a ``message`` or an ``action`` input; ``get_quote``
        carries the ``missing_fields``.
        """
        doc = await self.load(t.sid)
        action, said = data.get("action_id"), str(data.get("text") or "")
        if doc["handoff"] and action == "talk_to_advisor":  # already sent: repeat the reference
            await self.say(t, text(doc, "handoff_done", reference=doc["handoff"]["reference"]))
        elif action == "talk_to_advisor":
            stop = doc["stop"] or {}
            await self.end_of_form(t, stop.get("reason") or text(doc, "customer_request"), stop.get("rule_id"))
        elif action == "consent_yes":  # the BFF asked (full form): no agent turn, the answer goes to the commit path
            await self.commit(t, [Change(CONSENT, True, evidence=said)], "chat", "customer",
                              extra=lambda d: d.update(after_consent=FULL_FORM))
        elif action in ("rule_confirm", "rule_correct"):
            await self.on_rule_answer(t, doc, confirm=action == "rule_confirm", said=said)
        elif action == "get_quote" and (doc["stop"] or doc["rules_error"]):
            await self.say(t, text(doc, "not_ready"))
        elif doc["offtopic_streak"] >= 3 and not action:  # FR-GRD-5: canned redirect, no agent call
            await self.mutate(t.sid, lambda d: d.update(offtopic_streak=0))
            await self.say(t, text(doc, "off_topic"), [{"label": text(doc, "sugg_continue"), "kind": "reply"}])
        else:
            input_: dict[str, Any] = {"kind": "action", "action_id": action, "text": said} if action else {
                "kind": "message", "text": said}
            input_.update({k: data[k] for k in ("quote_id", "changes") if action and k in data})  # modify_quote
            if action == "get_quote":
                input_["missing_fields"] = state.missing_for_quote(doc)
            await self.agent_turn(t, input_)

    async def on_rule_answer(self, t: Turn, doc: dict[str, Any], *, confirm: bool, said: str) -> None:
        """Handle ``rule_confirm`` and ``rule_correct``, the two answers to the rule question (FR-RUL-3).

        Confirm commits the question's paths as confirmed, so the next check finds a firm STOP. Correct puts the old
        values back and, on ``go``, lets the agent ask for the right one. Fails with ``no_rule_question`` when no
        question is pending.
        """
        q = doc.get("rule_question")
        if not q:
            await self.fail(t, "no_rule_question")
            return
        if confirm:  # commits the value as confirmed; the next check finds a firm STOP
            changes = [Change(p, op="confirm") for p in q["paths"]]
        else:  # reverts it; the agent asks for the right value
            changes = [Change(p, op="revert", restore=q["restore"].get(p)) for p in q["paths"]]
        outcome = await self.commit(t, changes, "chat", "customer", rule_id=q["rule_id"],
                                    extra=lambda d: d.update(rule_question=None))
        if not confirm and outcome == "go":
            await self.agent_turn(t, {"kind": "action", "action_id": "rule_correct", "text": said,
                                      "rule_id": q["rule_id"], "changed_paths": q["paths"]})

    def _normalized(self, values: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        """Validate submitted values against the catalog: ``(valid values by path, fr-CA error message by path)``."""
        ok, errors = {}, {}
        for path, value in values.items():
            try:
                ok[path] = state.normalize(state.field_for(path)[0], value)
            except ValueError as exc:
                errors[path] = str(exc)
        return ok, errors

    async def on_action(self, t: Turn, data: dict[str, Any]) -> None:
        """Handle ``a2ui.action``, a button of a form.

        Closing buttons (``CLOSE_ACTIONS``) only close the form and never call the agent. On end_of_form, ``send``
        starts the hand-off. Otherwise the bound values are validated (errors go to ``/_errors`` and the form stays
        open, FR-A2UI-11), the form closes and the values are committed as ``form`` changes, or as reviewed
        ``document`` changes for a DocumentDrop form (FR-DOC-8). An edit-mode save commits only the changed paths and
        ends there (D16, D20); a normal submit runs the agent when the commit says ``go``. Fails with
        ``surface_not_open`` for an unknown or closed surface.
        """
        doc = await self.load(t.sid)
        sid, name = data.get("surfaceId"), data.get("name")
        s = doc["surfaces"].get(sid)
        if not s or s["status"] != "open":
            await self.fail(t, "surface_not_open")
            return
        edit = s["mode"] == "edit"
        if name in CLOSE_ACTIONS:
            note = None if edit or sid == END_OF_FORM else f"Form '{sid}' closed by the customer without a submit"
            await self.close(t, [sid], None if edit else "closed", note)
            return
        raw = {p: v for p, v in forms.values_from_context(data.get("context")).items() if forms.is_bound(p, s["bound"])}
        values, errors = self._normalized(raw)
        if sid == END_OF_FORM:
            await self.send_handoff(t, doc, s, values, errors)
            return
        if errors:  # FR-A2UI-11: the form stays open with the errors
            await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=sid, path="/_errors", value=errors))
            return
        if edit:  # edit-mode saves commit only the changed paths (D16, D20)
            values = {p: v for p, v in values.items() if get_value(doc, p) != v}
        await self.close(t, [sid], "submitted")
        drop = any(f.get("widget") == "document_drop" for f in (s.get("form_spec") or {}).get("fields", []))
        note = f"Form '{sid}' edited outside the chat: {', '.join(values)}" if edit and values else None
        outcome = await self.commit(t, [Change(p, v) for p, v in values.items()], "document" if drop else "form",
                                    "customer", reviewed=drop, surface_id=sid,
                                    extra=(lambda d: d["system_notes"].append(note)) if note else None)
        if edit:
            if outcome == "go":
                await self.say(t, text(doc, "saved", title=s["title"]))
            return
        if outcome == "go":
            submit = name in ("submit", "save")
            changes = {p.rsplit("/", 1)[-1]: v for p, v in values.items()}  # e.g. modify_quote from the offers form
            quote_id = {k: v for k, v in (data.get("context") or {}).items() if k == "quote_id"}
            action = {} if submit else {"action_id": name, "changes": changes, **quote_id}
            await self.agent_turn(t, {"kind": "form_submitted" if submit else "action", "surface_id": sid,
                                      "changed_paths": list(values), **action})

    async def send_handoff(self, t: Turn, doc: dict[str, Any], s: dict[str, Any], values: dict[str, Any],
                           errors: dict[str, str]) -> None:
        """The ``send`` button of end_of_form: validate, commit the contact, store the hand-off, say its reference.

        FR-RUL-5. A missing first name, last name or phone, or an unchecked consent box, comes back as ``/_errors``
        and nothing is created. The contact fields are committed under their own consent (FR-INT-2). The ``handoffs``
        row gets a ``CNS-`` reference, the summary as edited, the consent time and the advisor signals; then
        ``handoff.created`` is emitted, the reference is said in chat and the surface is marked ``sent``. No model
        call (FR-RUL-6).
        """
        for p in ("/quote/first_name", "/quote/last_name", "/quote/phone_number"):
            if not values.get(p, get_value(doc, p)) and p not in errors:
                errors[p] = text(doc, "required")
        if values.get("/quote/handoff_consent") is not True:
            errors["/quote/handoff_consent"] = text(doc, "consent_required")
        if errors:
            await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=END_OF_FORM, path="/_errors", value=errors))
            return
        await self.commit(t, [Change(p, v) for p, v in values.items()], "form", "customer", surface_id=END_OF_FORM)
        doc = await self.load(t.sid)
        reference = await self._new_reference()
        consent_at = (get_envelope(doc, "/quote/handoff_consent") or {}).get("updated_at", now_iso())
        await self.store.put("handoffs", {
            "id": reference, "session_id": t.sid, "reference": reference, "rule_id": s.get("rule_id"),
            "reason": s.get("reason"), "summary": get_value(doc, "/quote/handoff_summary"),
            "contact": {k: get_value(doc, f"/quote/{k}") for k in ("first_name", "last_name", "phone_number", "email")},
            "consent_at": consent_at, "advisor_signals": doc["advisor_signals"], "status": "new",
            "created_at": now_iso()}, create=True)
        note = f"Hand-off {reference} sent to an advisor"
        await self.mutate(t.sid, lambda d: (d.update(handoff={"reference": reference, "rule_id": s.get("rule_id")}),
                                            d["system_notes"].append(note)))
        await t.emit("handoff.created", {"reference": reference, "rule_id": s.get("rule_id")})
        await self.say(t, text(doc, "handoff_done", reference=reference))
        await self.close(t, [END_OF_FORM], "sent")

    async def _new_reference(self) -> str:
        """A ``CNS-`` plus six digits reference that no hand-off uses yet.

        Tries five times, then raises UpstreamError ``reference_exhausted``.
        """
        for _ in range(5):  # unique per environment: retry on a collision
            reference = f"CNS-{secrets.randbelow(900000) + 100000}"
            if not await self.store.query("handoffs", id=reference):
                return reference
        raise UpstreamError("reference_exhausted", "no free hand-off reference")

    async def on_reopen(self, t: Turn, data: dict[str, Any]) -> None:
        """Handle ``ui.surface_reopen`` (D16, D20): reopen a chip or the full form. No model call.

        A submitted or closed chip is rendered again in edit mode (FR-A2UI-15); end_of_form comes back as a new form.
        ``full_form`` (FR-A2UI-16) first needs the confirmed consent (FR-INT-2): without it the BFF asks for it in
        chat. Fails with ``chips_locked`` after a hand-off, ``surface_not_reopenable`` and ``a2ui_unavailable``
        (mcp-a2ui is down; retryable).
        """
        doc = await self.load(t.sid)
        sid = data.get("surface_id")
        s = doc["surfaces"].get(sid) or {}
        if doc["handoff"]:  # chips lock once a hand-off is sent
            await self.fail(t, "chips_locked")
            return
        if sid == FULL_FORM and not (get_value(doc, CONSENT) is True and (
                get_envelope(doc, CONSENT) or {}).get("status") == "confirmed"):  # FR-INT-2
            await self.say(t, text(doc, "consent_first"), [
                {"label": text(doc, "sugg_consent_yes"), "kind": "action", "action_id": "consent_yes"},
                {"label": text(doc, "sugg_consent_why"), "kind": "reply"}])
            return
        if sid == FULL_FORM:
            ok = await self.render(t, forms.full_form_spec(doc), mode="edit")
        elif s.get("status") in ("submitted", "closed") and s.get("form_spec"):
            if sid == END_OF_FORM:
                spec, mode = forms.end_of_form_spec(doc, s.get("reason") or "", s.get("rule_id")), "new"
            else:
                spec, mode = forms.edit_spec(s["form_spec"], doc), "edit"
            ok = await self.render(t, spec, mode=mode, stored=s["form_spec"])
        else:
            await self.fail(t, "surface_not_reopenable")
            return
        if not ok:
            await self.fail(t, "a2ui_unavailable", retryable=True)

    async def on_confirm(self, t: Turn, data: dict[str, Any]) -> None:
        """Handle ``confirm.response``, one step of the double confirmation (FR-DET-2).

        Step 1 accepted is only recorded. Step 2 accepted, after step 1, resolves the confirmation as accepted. A
        refusal at either step rejects it. Fails with ``unknown_confirmation`` or ``confirmation_step_out_of_order``.
        """
        doc = await self.load(t.sid)
        cid = data.get("confirmation_id")
        c = doc["confirmations"].get(cid)
        if not c or c["status"] != "pending":
            await self.fail(t, "unknown_confirmation")
            return
        if data.get("accepted") is not True:
            await self.resolve(t, c, "rejected")
        elif data.get("step") == 1:
            c["step1_at"] = now_iso()
            await self.mutate(t.sid, lambda d: d["confirmations"][cid].update(step1_at=c["step1_at"]))
            await self.store.put("confirmations", {**c, "id": cid, "session_id": t.sid})
        elif data.get("step") == 2 and c["step1_at"]:
            await self.resolve(t, c, "accepted")
        else:
            await self.fail(t, "confirmation_step_out_of_order")

    async def resolve(self, t: Turn, c: dict[str, Any], status: str) -> None:
        """Close a confirmation through the commit path: accepted → confirmed; rejected or expired → reverted.

        Only paths still pending with the same value are touched. After an acceptance the BFF opens the full form when
        that is what the consent was for, else tells the agent (``confirmation_result``) when the confirmation came from
        chat. A refusal or an expiry emits ``confirm.rejected``; a refusal also tells the agent (FR-DET-3). The agent is
        told only when the commit says ``go``.
        """
        doc = await self.load(t.sid)
        live = [p for p in c["paths"] if (get_envelope(doc, p["path"]) or {}).get("status") == "pending_confirmation"
                and get_value(doc, p["path"]) == p["new_value"]]
        if status == "accepted":
            changes = [Change(p["path"], op="confirm") for p in live]
        else:
            changes = [Change(p["path"], op="revert", restore=p["old"]) for p in live]
        record = {**c, "status": status, "step2_at": now_iso() if status == "accepted" else None}
        await self.store.put("confirmations", {**record, "id": c["confirmation_id"], "session_id": t.sid})
        then = doc.get("after_consent") if any(p["path"] == CONSENT for p in c["paths"]) else None

        def close(d: dict[str, Any]) -> None:
            """Drop the confirmation from the document, and clear ``after_consent`` when this confirmation used it."""
            d["confirmations"].pop(c["confirmation_id"], None)
            if then:
                d["after_consent"] = None

        outcome = await self.commit(t, changes, "chat", "customer", extra=close)
        if status == "accepted":
            if outcome == "go" and then:  # consent the BFF asked for the full form: open it now
                await self.on_reopen(t, {"surface_id": then})
            elif outcome == "go" and c.get("source") == "chat":  # the agent's last reply waited on this answer
                await self.agent_turn(t, {"kind": "confirmation_result", "confirmation_id": c["confirmation_id"],
                                          "accepted": True, "changed_paths": [p["path"] for p in c["paths"]]})
            return
        await t.emit("confirm.rejected", {"confirmation_id": c["confirmation_id"], "reason": status,
                                          "paths": [p["path"] for p in c["paths"]]})
        if status == "rejected" and outcome == "go":  # FR-DET-3: the agent acknowledges the refusal
            await self.agent_turn(t, {"kind": "confirmation_result", "confirmation_id": c["confirmation_id"],
                                      "accepted": False, "changed_paths": [p["path"] for p in c["paths"]]})

    async def expire_confirmations(self, t: Turn) -> None:
        """Resolve as expired every confirmation still pending after the TTL (FR-DET-4).

        Runs at the start of every turn.
        """
        doc = await self.load(t.sid)
        for c in list(doc["confirmations"].values()):
            if c["status"] == "pending" and time.time() - c["created_ts"] > PENDING_TTL:  # FR-DET-4
                log.info("confirmation expired", extra={"session_id": t.sid, "confirmation_id": c["confirmation_id"]})
                await self.resolve(t, c, "expired")

    # -- documents and renderer functions -----------------------------------------------------------------------

    async def upload(self, sid: str, data: bytes, content_type: str, purpose: str, surface_id: str | None,
                     field_path: str | None) -> str:
        """Store an upload (already checked by the route) and register it in the session.

        Returns the ``document_id``.
        """
        did = "d_" + secrets.token_hex(8)
        await self.blobs.put(f"{sid}/{did}", data, content_type)
        meta = {"blob": f"{sid}/{did}", "purpose": purpose, "content_type": content_type, "size": len(data),
                "surface_id": surface_id, "field_path": field_path, "uploaded_at": now_iso()}
        await self.mutate(sid, lambda d: d["documents"].__setitem__(did, meta))
        return did

    async def extract(self, t: Turn, document_id: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
        """Read one uploaded document with the document agent; logged as tool.call / tool.result (actor bff).

        Returns the checked output and the upload's metadata. ``policy`` is read as ``declaration``. Raises
        UpstreamError for an unknown document or an output that fails ``check_document``. The blob store may wait for
        the malware scan first (FR-DOC-2).
        """
        meta = (await self.load(t.sid))["documents"].get(document_id or "")
        if meta is None:
            raise UpstreamError("unknown_document", "unknown document", retryable=False)
        purpose = "declaration" if meta["purpose"] == "policy" else meta["purpose"]
        await t.emit("tool.call", {"name": "document.extract", "args_redacted": {"purpose": purpose}})
        started = time.perf_counter()
        with tracer.start_as_current_span("document.extract"):
            content = await self.blobs.get(meta["blob"])
            out = check_document(await self.documents.extract(content, meta["content_type"], purpose))
        await t.emit("tool.result", {"name": "document.extract", "ms": round((time.perf_counter() - started) * 1000),
                                     "result_mode": "terminal", "fields": len(out["fields"])})
        return out, meta

    async def on_document(self, t: Turn, data: dict[str, Any]) -> None:
        """Handle ``document.uploaded``: read the document and use its fields (FR-DOC-3 to FR-DOC-5, FR-DOC-8).

        An unreadable document asks for a new photo (FR-DOC-5). On an open DocumentDrop the values go to the surface
        for review and nothing is committed before the submit (FR-DOC-8). Otherwise they are committed as unreviewed
        ``document`` values, so ``inferred`` (FR-DOC-4), and the agent reads a ``document_extracted`` input.
        """
        doc = await self.load(t.sid)
        out, meta = await self.extract(t, data.get("document_id"))
        surface_id = data.get("surface_id") or meta.get("surface_id")
        s = doc["surfaces"].get(surface_id or "") or {}
        if not out["document_ok"]:  # FR-DOC-5: ask for another photo; manual entry stays available
            await self.say(t, text(doc, "retake"))
            return
        field_path = data.get("field_path") or meta.get("field_path") or ""
        drop = next((d.get("fills") or {} for f in (s.get("form_spec") or {}).get("fields", [])
                     if f.get("widget") == "document_drop" for d in (f.get("data") or {}).get("documents", [])
                     if d.get("purpose") == meta["purpose"]), None)
        m = re.match(r"^/(vehicles|drivers)/(\d+)/", field_path)
        fills: dict[str, tuple[Any, float]] = {}
        for key, f in out["fields"].items():
            own = field_path if field_path.endswith("/" + key) else None
            path = (drop or {}).get(key) or own or FIELD_MAP.get(key, "").replace("{i}", m[2] if m else "0")
            if path:
                fills[path] = (f["value"], f["confidence"])
        if drop is not None and s.get("status") == "open":  # D18: shown for review, committed only on submit
            for path, (value, confidence) in fills.items():
                await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=surface_id, path=path, value=value))
                await t.emit("a2ui.message", a2ui("updateDataModel", surfaceId=surface_id,
                                                  path=pointer.provenance_path(path),
                                                  value={"source": "document", "confidence": confidence}))
            return
        changes = [Change(p, v, confidence=c) for p, (v, c) in fills.items()]
        if await self.commit(t, changes, "document", "document_agent") == "go":
            await self.agent_turn(t, {"kind": "document_extracted", "document_id": data.get("document_id"),
                                      "purpose": meta["purpose"], "changed_paths": list(fills)})

    async def agent_function(self, sid: str, message: dict[str, Any]) -> dict[str, Any]:
        """A2UI callAgentFunction from the renderer: a lookup or a document read, no model call, no agent turn.

        The call takes a turn number for the log. A lookup passes on only the arguments its quoting tool declares (no
        ``session_ref``) and a VIN is upper-cased. Returns the ``agentFunctionResponse`` message; raises ValueError
        for an unknown function (the route answers 422).
        """
        call = message.get("callAgentFunction") or {}
        fn = call.get("callFunction") or {}
        name, args = fn.get("@call"), fn.get("args") or {}
        if name != "extractDocument" and name not in FUNCTIONS:
            raise ValueError(f"unknown function {name}")
        t = await self.open_turn(sid)
        try:
            await t.emit("a2ui.callAgentFunction", redact(call), "frontend", forward=False)
            if name == "extractDocument":
                value: Any = (await self.extract(t, args.get("document_id")))[0]
            else:
                tool = FUNCTIONS[name]
                tool_args = {k: v for k, v in args.items() if k in TOOL_ARGS[tool]}
                if isinstance(tool_args.get("vin"), str):
                    tool_args["vin"] = tool_args["vin"].strip().upper()
                await t.emit("tool.call", {"name": f"quoting.{tool}", "args_redacted": redact(tool_args)})
                started = time.perf_counter()
                value = tool_value(await self.quoting.call(tool, tool_args))  # lookups take no session_ref
                await t.emit("tool.result", {"name": f"quoting.{tool}", "result_mode": "terminal",
                                             "ms": round((time.perf_counter() - started) * 1000)})
        finally:
            t.close()
        return {"version": forms.A2UI_VERSION, "agentFunctionResponse": {"functionCallId": call.get("functionCallId"),
                                                                         "value": value}}

    # -- the hosted-agent relay ---------------------------------------------------------------------------------

    async def agent_turn(self, t: Turn, input_: dict[str, Any]) -> None:
        """Invoke the hosted agent and relay its stream to the customer.

        The reply (the ``VISIBLE`` events) is held until ``memory.result`` arrives, because the memory delta may raise
        a rule question or a STOP that must drop the whole reply; dropped events are stored, not forwarded. If no
        memory result comes, the reply is released before ``turn.completed`` or ``error``.
        """
        doc = await self.load(t.sid)
        body = invocation_body(doc, t.turn, input_, self.session_ref(t.sid))
        sent_notes = len(body["system_notes"])
        drop = False  # after a STOP or a rule question mid-turn, the main agent's reply is logged, not forwarded
        held: list[dict[str, Any]] | None = []  # the reply waits for memory.result, so a STOP can still drop it

        async def release() -> None:
            """Relay the held reply events now and stop holding."""
            nonlocal held
            for ev in held or ():
                await self.relay(t, ev, drop, sent_notes)
            held = None

        async for ev in self.agent.invoke(body, session_id=t.sid, turn_id=f"{t.sid}:{t.turn}",
                                          traceparent=t.traceparent):
            kind = ev.get("type", "")
            if kind == "memory.result":  # never forwarded raw
                await t.emit(kind, ev.get("data") or {}, ev.get("actor") or "memory_agent", forward=False)
                drop |= await self.on_memory(t, ev.get("data") or {}) != "go"
                await release()
            elif held is not None and kind in VISIBLE:
                held.append(ev)
            else:
                if kind in ("turn.completed", "error"):  # no memory result came: the reply goes out as it is
                    await release()
                await self.relay(t, ev, drop, sent_notes)
        await release()

    async def relay(self, t: Turn, ev: dict[str, Any], drop: bool, sent_notes: int) -> None:
        """One hosted-agent event to the browser, with the BFF's bookkeeping.

        ``drop`` stores the visible events without forwarding them; ``sent_notes`` is how many system notes this
        invocation carried. ``turn.started`` clears those notes, ``phase`` is stored, an agent surface is registered (or
        dropped after a STOP), suggestions are filtered (D15) and quotes are saved.
        """
        kind, data, actor = ev.get("type", ""), ev.get("data") or {}, ev.get("actor") or "main_agent"
        if drop and kind in VISIBLE:
            await t.emit(kind, data, actor, forward=False)
            return
        if kind == "turn.started":  # the hosted agent has the notes now
            await self.mutate(t.sid, lambda d: d.update(system_notes=d["system_notes"][sent_notes:]))
        elif kind == "phase" and data.get("phase"):
            await self.mutate(t.sid, lambda d, phase=data["phase"]: d.update(phase=phase))
        elif kind == "a2ui.message" and not await self.on_agent_surface(t, data, ev.get("form_spec")):
            await t.emit(kind, data, actor, forward=False)
            return
        elif kind == "suggestions":
            data = await self.on_suggestions(t, data)
        elif kind in ("quote.created", "quote.updated"):
            await self.save_quote(t, kind, data)
        await t.emit(kind, data, actor, ts=ev.get("ts"))

    async def on_memory(self, t: Turn, data: dict[str, Any]) -> str:
        """Apply a ``memory.result`` (FR-MEM-6) and return the outcome of its commit: ``go``, ``ask`` or ``stop``.

        A result that fails its schema is ignored. It stores the answer preference (events.md rule 7, FR-MEM-10), the
        signals, the off-topic streak (FR-GRD-5) and the conflicts, then commits its ``state_changes`` as ``chat``
        changes by ``memory_agent``.
        """
        if next(MEMORY_SCHEMA.iter_errors(data), None) is not None:
            log.warning("memory.result does not match its schema; ignored", extra={"session_id": t.sid})
            return "go"
        mode = (data.get("session_preferences") or {}).get("answer_mode")
        off_topic = data["intent"]["off_topic"]

        def fn(d: dict[str, Any]) -> None:
            """The session write: preference, signals, off-topic streak and conflict notes."""
            if mode is not None:  # rule 7: the last statement wins
                d["session_preferences"]["answer_mode"] = mode
            d["signals"] = list({**{s["code"]: s for s in d["signals"]}, **{s["code"]: s for s in data["signals"]}}
                                .values())
            d["offtopic_streak"] = d["offtopic_streak"] + 1 if off_topic else 0
            for c in data["conflicts"]:
                d["conflicts"].append(c)
                d["system_notes"].append(f"Conflict on {c['path']}: confirmed {c['current_value']!r}, "
                                         f"the customer said {c['stated_value']!r}")

        doc, _ = await self.mutate(t.sid, fn)
        if mode is not None:
            await t.emit("session.preferences", {"answer_mode": mode})
        if data["signals"]:
            await t.emit("signals", {"items": doc["signals"]})
        changes = [Change(c["path"], c["value"], c["op"], c["confidence"], c["evidence"])
                   for c in data["state_changes"]]
        return await self.commit(t, changes, "chat", "memory_agent") if changes else "go"

    async def on_agent_surface(self, t: Turn, data: dict[str, Any], form_spec: dict[str, Any] | None) -> bool:
        """Registry bookkeeping for an agent A2UI message.

        False drops it: after a STOP or a hand-off the agent opens no form (FR-RUL-4). A ``deleteSurface`` from the
        agent marks its chip closed.
        """
        doc = await self.load(t.sid)
        if "createSurface" in data:
            if doc["stop"] or doc["handoff"]:
                log.info("agent form dropped after a STOP", extra={"session_id": t.sid})
                return False
            await self.mutate(t.sid, lambda d: _register(d, data, spec=form_spec, origin="agent", mode="new"))
        elif "deleteSurface" in data:
            sid = data["deleteSurface"].get("surfaceId")
            if (doc["surfaces"].get(sid) or {}).get("status") == "open":
                await self.mutate(t.sid, lambda d: d["surfaces"][sid].update(status="closed"))
        return True

    async def on_suggestions(self, t: Turn, data: dict[str, Any]) -> dict[str, Any]:
        """Filter the agent's suggested replies (D15, FR-CHAT-7) and return the event data with the kept items.

        Drops are stored as ``suggestions.dropped`` (not forwarded) and logged by reason only, never by label.
        """
        doc = await self.load(t.sid)
        with tracer.start_as_current_span("bff.suggestions") as span:
            kept, dropped = filter_suggestions(data.get("items") or [], doc)
            span.set_attribute("dropped", len(dropped))
        for d in dropped:  # the reason only, never the label
            log.info("suggestion dropped", extra={"session_id": t.sid, "reason": d["reason"]})
        if dropped:
            await t.emit("suggestions.dropped", {"items": dropped}, forward=False)
        return {**data, "items": kept}

    async def save_quote(self, t: Turn, kind: str, data: dict[str, Any]) -> None:
        """Store a ``quote.created`` or ``quote.updated`` event as a row of ``quotes`` (FR-QUO-1).

        The row keeps the version, the offers, the reason, the previous version, the monthly delta against it
        (FR-QUO-2; a ``delta_monthly`` from mcp-quoting wins) and the ``input_hash`` (FR-QUO-3).
        """
        doc = await self.load(t.sid)
        version = int(data.get("version") or 1)
        previous = data.get("previous_version") or (version - 1 if version > 1 else None)
        old = await self.store.query("quotes", session_id=t.sid, quote_id=data.get("quote_id"), version=previous)

        def monthly(offers: list[dict[str, Any]]) -> float | None:
            """The monthly price of the offer type the customer chose, else of the first offer."""
            pick = get_value(doc, "/underwriting/offer_type")
            offer = next((o for o in offers if o.get("offer_type") == pick), offers[0] if offers else None)
            return (offer or {}).get("monthly")

        new_m, old_m = monthly(data.get("offers") or []), monthly(old[0]["offers"]) if old else None
        delta = data.get("delta_monthly")
        if delta is None and new_m is not None and old_m is not None:
            delta = round(new_m - old_m, 2)
        await self.store.put("quotes", {
            "id": f"{data.get('quote_id')}:v{version}", "session_id": t.sid, "quote_id": data.get("quote_id"),
            "version": version, "offers": data.get("offers") or [], "created_at": now_iso(),
            "reason": data.get("reason") or ("created" if kind == "quote.created" else "modified"),
            "previous_version": previous, "delta_monthly": delta, "input_hash": data.get("input_hash")})  # FR-QUO-3
