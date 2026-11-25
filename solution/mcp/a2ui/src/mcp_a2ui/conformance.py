"""A2UI 1.0 conformance rules, ported from the JavaScript harness in ``skill/scripts/check_artifacts.py``.

``check`` returns the problems in a list of messages; an empty list means conformant. The tests run it on everything the
compiler and the tools emit, so a rule added here protects every widget.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

_ID = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")  # an identifier: surface and component ids must match it
# The "@" directives A2UI 1.0 defines.
_DIRECTIVES = {"@path", "@call", "@index", "@checks", "@catalogId", "@accessibility", "@metadata"}
# The message kinds; every message has exactly one.
_KINDS = ("createSurface", "updateComponents", "updateDataModel", "deleteSurface")


def _directives(node: Any, where: str) -> Iterator[str]:
    """Pre-1.0 spellings (``{path}``, ``call``, ``checks``) and unknown ``@`` directives, anywhere in a component."""
    if isinstance(node, list):
        for item in node:
            yield from _directives(item, where)
    elif isinstance(node, dict):
        if list(node) == ["path"]:
            yield f"{where}: {{path}} binding (use @path)"
        for old in ("call", "checks"):
            if old in node:
                yield f"{where}: {old} (use @{old})"
        for key, value in node.items():
            if key.startswith("@") and key not in _DIRECTIVES:
                yield f"{where}: unknown directive {key}"
            yield from _directives(value, where)


def _component(c: dict, ids: set[str] | None) -> Iterator[str]:
    """Rules for one component. ``ids`` is every component id of the surface, or ``None`` when it is not known."""
    cid = c.get("id", "")
    if not _ID.match(str(cid)):
        yield f"component id {cid}"
    if c.get("component") == "Text" and re.fullmatch(r"h[1-6]", str(c.get("variant"))):
        yield f"{cid}: heading variant (use Markdown)"
    refs = [r for key in ("children", "template") if isinstance(c.get(key), list) for r in c[key]] + [c.get("child")]
    for ref in refs:
        if ids is not None and isinstance(ref, str) and ref not in ids:
            yield f"{cid}: missing child {ref}"
    for check in c.get("@checks", []):
        if not (check.get("condition") or {}).get("@call"):
            yield f"{cid}: CheckRule without condition.@call"
    if c.get("component") == "Button" and not (((c.get("action") or {}).get("event")) or {}).get("name"):
        yield f"{cid}: Button without action.event.name"
    yield from _directives(c, cid)


def _message(body_kind: str, body: dict) -> Iterator[str]:
    """Rules for the body of one message: the surface id, and what its kind needs (components, data model or value)."""
    if not _ID.match(str(body.get("surfaceId", ""))):
        yield f"{body_kind}: surfaceId {body.get('surfaceId')}"
    if body_kind == "createSurface":
        if body.get("components") is None or body.get("dataModel") is None:
            yield "createSurface needs components and dataModel"
        if "theme" in body:
            yield "theme is invalid in 1.0"
        components = body.get("components") or []
        ids = {c.get("id") for c in components}
        if "root" not in ids:
            yield "no root"
        for c in components:
            yield from _component(c, ids)
    elif body_kind == "updateComponents":
        for c in body.get("components", []):
            yield from _component(c, None)
    elif body_kind == "updateDataModel" and "value" not in body:
        yield "updateDataModel needs value (null deletes the key)"


def check(messages: list[dict], *, opens_surface: bool = False) -> list[str]:
    """The conformance problems in ``messages``. ``opens_surface`` also requires the one-``createSurface`` form opening."""
    problems = []
    if opens_surface and not (len(messages) == 1 and "createSurface" in messages[0]):
        problems.append("form must open with one createSurface")
    for m in messages:
        if m.get("version") != "1.0":
            problems.append(f"version {m.get('version')}")
        kinds = [k for k in _KINDS if k in m]
        if len(kinds) != 1:
            problems.append(f"message needs exactly one of {', '.join(_KINDS)}")
        problems.extend(p for kind in kinds for p in _message(kind, m[kind]))
    return list(dict.fromkeys(problems))
