"""In-process fakes with the same methods as the real clients. Used by the tests and by a local BFF with no URLs set.

None of them makes a network call. The fake compiler is deliberately small: it produces a valid A2UI 1.0
``createSurface`` with one bound component per field, enough for the BFF's registry, autofill and edit mode. The
real compiler (mcp-a2ui) is the reference for components and checks.
"""

from __future__ import annotations

import copy
import json
from collections.abc import AsyncIterator, Callable
from typing import Any

from quote_common import pointer

from quote_bff.clients import A2UI_MIME
from quote_bff.forms import a2ui
from quote_bff.rules.end_of_form import get_envelope
from quote_bff.state import CATALOG

CATALOG_ID = "https://desjardins.example/a2ui/insurance-catalog/v1"  # catalogId of the insurance components
# One scripted reply: a list of envelopes, or a function of the invocation body that returns them.
Script = list[dict[str, Any]] | Callable[[dict[str, Any]], list[dict[str, Any]]]


def event(type_: str, data: dict[str, Any] | None = None, actor: str = "main_agent", **extra: Any) -> dict[str, Any]:
    """An envelope as the hosted agent streams it; ``extra`` keys (e.g. ``form_spec``) sit next to ``data``."""
    return {"type": type_, "actor": actor, "data": data or {}, **extra}


def reply(text: str, *more: dict[str, Any]) -> list[dict[str, Any]]:
    """A typical turn: started, one delta, done, then ``more`` events, then completed."""
    return [event("turn.started", {"phase": "gather"}), event("text.delta", {"delta": text}),
            event("text.done", {"text": text}), *more,
            event("turn.completed", {"model_calls": 2, "usage": {"input": 0, "cached_input": 0, "output": 0}})]


class FakeHostedAgent:
    """Streams one scripted list of envelopes per invocation (a list, or a callable of the invocation body).

    Warm-up invocations never consume the script. With no script left, it replies with a short default text.
    """

    def __init__(self, script: list[Script] | None = None) -> None:
        """One scripted reply per expected invocation goes in ``script``.

        ``invocations`` records the body and the headers of each call.
        """
        self.script = list(script or [])
        self.invocations: list[dict[str, Any]] = []

    async def invoke(self, body: dict[str, Any], *, session_id: str, turn_id: str,
                     traceparent: str) -> AsyncIterator[dict[str, Any]]:
        """Record the call, then stream the next scripted reply (a bare ``turn.completed`` for a warm-up)."""
        headers = {"x-client-session-id": session_id, "x-client-turn-id": turn_id, "traceparent": traceparent}
        self.invocations.append({"body": copy.deepcopy(body), "headers": headers})
        if body["input"]["kind"] == "warmup":
            events = [event("turn.completed", {"model_calls": 0})]
        elif self.script:
            step = self.script.pop(0)
            events = step(body) if callable(step) else step
        else:
            events = reply("Merci. (Agent de démonstration local.)")
        for e in events:
            yield copy.deepcopy(e)


def _tool_result(value: Any) -> dict[str, Any]:
    """A CallToolResult that carries ``value`` both as text and as structured content."""
    return {"content": [{"type": "text", "text": json.dumps(value)}], "structuredContent": value, "isError": False}


class FakeA2ui:
    """``render_form`` → a CallToolResult with one createSurface in an application/a2ui+json EmbeddedResource."""

    def __init__(self) -> None:
        """``calls`` records each ``(tool, arguments)`` it receives."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Build a createSurface from the FormSpec in ``arguments``.

        One component per field, values and provenance filled from ``state``, one button per action (the primary one
        reads "Enregistrer" in edit mode).
        """
        self.calls.append((tool, copy.deepcopy(arguments)))
        spec, state, mode = arguments["form_spec"], arguments.get("state") or {}, arguments.get("mode", "new")
        comps: list[dict[str, Any]] = [{"id": "title", "component": "Text", "text": "## " + spec["title"]}]
        data: dict[str, Any] = {}
        for i, f in enumerate(spec["fields"]):
            widget, key = f.get("widget", "auto"), f.get("key")
            if widget == "repeater":
                tids = [f"tpl_{i}_{j}" for j in range(len(f["repeat"]["fields"]))]
                comps += [{"id": t, "component": "TextField", "value": {"@path": sub["key"]}}
                          for t, sub in zip(tids, f["repeat"]["fields"], strict=True)]
                comps.append({"id": f"field_{i}", "component": "Repeater", "collection": key, "template": tids})
            elif widget == "document_drop":
                comps.append({"id": f"field_{i}", "component": "DocumentDrop",
                              "documents": (f.get("data") or {}).get("documents", [])})
            elif not key:
                comps.append({"id": f"note_{i}", "component": "Text", "text": f.get("hint") or f.get("label", "")})
            else:
                path = CATALOG.resolve_path(key, spec.get("entity"))
                comps.append({"id": f"field_{i}", "component": "TextField", "value": {"@path": path}})
                env = get_envelope(state, path) or {}
                value = (f.get("prefill") or {}).get("value", env.get("value"))
                if value is not None:
                    pointer.set(data, path, value)
                if "prefill" not in f and env.get("source") in ("chat", "document", "lookup"):
                    pointer.set(data, pointer.provenance_path(path), {"source": env["source"]})
        for a in spec.get("actions") or [{"id": "submit", "label": "Continuer", "style": "primary"}]:
            label = "Enregistrer" if mode == "edit" and a.get("style") == "primary" else a["label"]
            comps.append({"id": f"action_{a['id']}", "component": "Button", "child": label,
                          "action": {"event": {"name": a["id"]}}})
        message = a2ui("createSurface", surfaceId=spec["form_id"], catalogId=CATALOG_ID, dataModel=data,
                       components=comps)
        return {"content": [
            {"type": "text", "text": json.dumps({"surface_id": spec["form_id"], "status": "shown"})},
            {"type": "resource", "annotations": {"audience": ["user"]},
             "resource": {"uri": f"a2ui://surface/{spec['form_id']}", "mimeType": A2UI_MIME,
                          "text": json.dumps([message])}}], "isError": False}


# The makes and models the fake lookup knows; Lamborghini is there for the exotic-vehicle rule.
MAKES = {"Toyota": ["RAV4", "Corolla"], "Honda": ["Civic", "CR-V"], "Lamborghini": ["Huracán", "Urus"]}


class FakeQuoting:
    """The four lookup tools of mcp-quoting with canned answers (tools.json output shapes)."""

    def __init__(self) -> None:
        """``calls`` records each ``(tool, arguments)`` it receives."""
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Answer one lookup tool; an unknown tool returns an ``isError`` result."""
        self.calls.append((tool, copy.deepcopy(arguments)))
        a = arguments
        if tool == "lookup_make_model_year":
            models = MAKES.get(a.get("make") or "", [])
            valid = bool(a.get("year") and a.get("model") in models)
            value = {"valid": valid, "normalized": {k: a.get(k) for k in ("year", "make", "model")} if valid else None,
                     "makes": sorted(MAKES), "models": models}
        elif tool == "identify_vehicle_type":
            value = {"vehicle_type": "suv" if a.get("model") in ("RAV4", "CR-V", "Urus") else "car",
                     "body_style": "4 portes", "confidence": 0.95}
        elif tool == "decode_vin":
            value = {"valid_checksum": True, "year": 2021, "make": "Toyota", "model": "RAV4", "trim": "LE"}
        elif tool == "identify_address":
            value = {"candidates": [{"address_line1": "125 Rue des Érables", "city": "Laval", "province_code": "QC",
                                     "postal_code": "H7N 1A1", "confidence": 0.93}]}
        else:
            return {"content": [{"type": "text", "text": f"unknown tool {tool}"}], "isError": True}
        return _tool_result(value)


def _f(value: Any, confidence: float) -> dict[str, Any]:
    """A field of a document output: ``{value, confidence}``."""
    return {"value": value, "confidence": confidence}


CANNED_DOCUMENTS: dict[str, dict[str, Any]] = {  # schema-valid (document-output.schema.json)
    "licence": {"purpose": "licence", "document_ok": True, "fields": {
        "first_name": _f("Marie", 0.99), "last_name": _f("Tremblay", 0.99), "date_of_birth": _f("1987-01-18", 0.96),
        "driver_license_number": _f("T6512-180187-08", 0.95), "license_class": _f("5", 0.88),
        "issuing_province": _f("QC", 0.97)}},
    "registration": {"purpose": "registration", "document_ok": True, "fields": {
        "vin": _f("2T3P1RFV8MC123456", 0.95), "vehicle_year": _f(2021, 0.96), "vehicle_make": _f("Toyota", 0.97),
        "vehicle_model": _f("RAV4", 0.94), "plate": _f("ABC 123", 0.9)}},
    "vin": {"purpose": "vin", "document_ok": True, "fields": {
        "vin": _f("2T3P1RFV8MC123456", 0.97), "vehicle_year": _f(2021, 0.9), "vehicle_make": _f("Toyota", 0.92)}},
    "declaration": {"purpose": "declaration", "document_ok": True, "fields": {
        "current_insurer": _f("Assureur ABC", 0.9), "policy_expiry": _f("2027-03-31", 0.9),
        "years_with_current_insurer": _f(6, 0.86), "has_claims": _f(False, 0.74)}},
}


class FakeDocumentExtractor:
    """Canned output per purpose (``policy`` is read as ``declaration``); ``outputs`` overrides it in tests."""

    def __init__(self, outputs: dict[str, dict[str, Any]] | None = None) -> None:
        """``outputs`` replaces the canned output of the purposes it names; ``calls`` records the purposes asked."""
        self.outputs = {**CANNED_DOCUMENTS, **(outputs or {})}
        self.calls: list[str] = []

    async def extract(self, content: bytes, content_type: str, purpose: str) -> dict[str, Any]:
        """A copy of the canned output for ``purpose``; the content itself is ignored."""
        self.calls.append(purpose)
        return copy.deepcopy(self.outputs[purpose])
