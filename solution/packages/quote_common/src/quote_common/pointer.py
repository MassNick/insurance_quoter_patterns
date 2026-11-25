"""RFC 6901 JSON Pointer helpers shared by state, A2UI data models and events."""

from __future__ import annotations

from typing import Any

_MISSING = object()


def escape(token: str) -> str:
    """Escape one pointer token: ``~`` becomes ``~0`` and ``/`` becomes ``~1``."""
    return str(token).replace("~", "~0").replace("/", "~1")


def unescape(token: str) -> str:
    """The inverse of ``escape``."""
    return token.replace("~1", "/").replace("~0", "~")


def parts(pointer: str) -> list[str]:
    """The unescaped tokens of a pointer; ``""`` and ``"/"`` are the root (no tokens). Raises ``ValueError`` if it does
    not start with ``/``."""
    if pointer in ("", "/"):
        return []
    if not pointer.startswith("/"):
        msg = f"not a JSON Pointer: {pointer!r}"
        raise ValueError(msg)
    return [unescape(p) for p in pointer.split("/")[1:]]


def key_of(pointer: str) -> str:
    """The last token of a pointer (``""`` for the root)."""
    p = parts(pointer)
    return p[-1] if p else ""


def parent_of(pointer: str) -> str:
    """The pointer one level up (``"/"`` for a top-level token). Works on the text: tokens stay escaped."""
    idx = pointer.rfind("/")
    return pointer[:idx] or "/"


def provenance_path(pointer: str) -> str:
    """Where an A2UI data model keeps the provenance of a bound value: ``/_provenance/<escaped pointer>``."""
    return "/_provenance/" + escape(pointer)


def get(doc: Any, pointer: str, default: Any = None) -> Any:
    """The value at ``pointer`` in a document of nested dicts and lists, or ``default`` when any step is missing."""
    cur = doc
    for p in parts(pointer):
        if isinstance(cur, list):
            if not p.isdigit() or int(p) >= len(cur):
                return default
            cur = cur[int(p)]
        elif isinstance(cur, dict):
            if p not in cur:
                return default
            cur = cur[p]
        else:
            return default
    return cur


def set(doc: Any, pointer: str, value: Any) -> None:
    """Set a value, creating dicts or lists on the way (a numeric next token creates a list).

    A list grows with None to reach the index. Raises ``ValueError`` for the root, which cannot be replaced in place.
    """
    ps = parts(pointer)
    if not ps:
        msg = "cannot set the document root"
        raise ValueError(msg)
    cur = doc
    for i, p in enumerate(ps):
        last = i == len(ps) - 1
        nxt_is_index = not last and ps[i + 1].isdigit()
        if isinstance(cur, list):
            idx = int(p)
            while len(cur) <= idx:
                cur.append(None)
            if last:
                cur[idx] = value
                return
            if cur[idx] is None:
                cur[idx] = [] if nxt_is_index else {}
            cur = cur[idx]
        else:
            if last:
                cur[p] = value
                return
            if cur.get(p) is None:
                cur[p] = [] if nxt_is_index else {}
            cur = cur[p]


def delete(doc: Any, pointer: str) -> None:
    """Remove the value at ``pointer``: a dict key is dropped, a list item is set to None so the other indexes stay.
    A missing path, or the root, is left alone."""
    ps = parts(pointer)
    if not ps:
        return
    parent = get(doc, "/" + "/".join(escape(p) for p in ps[:-1])) if len(ps) > 1 else doc
    last = ps[-1]
    if isinstance(parent, dict):
        parent.pop(last, None)
    elif isinstance(parent, list) and last.isdigit() and int(last) < len(parent):
        parent[int(last)] = None
