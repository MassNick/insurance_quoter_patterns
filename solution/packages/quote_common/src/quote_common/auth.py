"""Inbound Entra ID validation for the MCP servers (NFR-SEC-3), as one small ASGI middleware.

Enabled only when ``ENTRA_AUDIENCE`` is set (Azure). Locally and in tests it is off, so nothing needs Azure.

Checks, in order: a Bearer token signed by the tenant's keys; issuer of ``ENTRA_TENANT_ID`` (v2 or v1);
``aud`` equal to one of ``ENTRA_AUDIENCE`` (comma-separated: the ``api://`` URI and/or the client id); the
caller's ``oid`` in ``ENTRA_ALLOWED_PRINCIPAL_IDS`` (comma-separated; empty = any caller of the tenant with a
role); and an app role. ``tool_roles`` maps an MCP tool name to the roles that may call it; other requests need
any role in ``default_roles``. ``/healthz`` is never protected.

Requires the ``auth`` extra (``PyJWT[crypto]``).
"""

from __future__ import annotations

import json
import os
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

Scope = dict[str, Any]  # an ASGI connection scope
# An ASGI 3 application: awaited with (scope, receive, send).
ASGIApp = Callable[[Scope, Callable[[], Awaitable[dict]], Callable[[dict], Awaitable[None]]], Awaitable[None]]
KeyResolver = Callable[[str], Any]  # token -> verification key


class EntraAuth:
    """ASGI middleware: an HTTP request reaches ``app`` only with a valid Entra Bearer token, an allowed caller and an
    app role. Other scopes (lifespan) and ``open_paths`` are never checked; a refusal is a JSON 401 or 403."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        tenant_id: str,
        audiences: Iterable[str],
        allowed_principals: Iterable[str] = (),
        default_roles: Iterable[str] = ("Tools.Invoke",),
        tool_roles: dict[str, set[str]] | None = None,
        key_resolver: KeyResolver | None = None,
        open_paths: Iterable[str] = ("/healthz",),
    ) -> None:
        """``tool_roles`` maps an MCP tool name to the roles that may call it. ``key_resolver`` maps a token to its
        verification key (default: the tenant's JWKS; tests pass a fixed key). Empty ``audiences`` and
        ``allowed_principals`` entries are ignored; no principals means any caller of the tenant."""
        self.app = app
        self.tenant_id = tenant_id
        self.audiences = [a for a in audiences if a]
        self.allowed = {p for p in allowed_principals if p}
        self.default_roles = set(default_roles)
        self.tool_roles = tool_roles or {}
        self.open_paths = set(open_paths)
        self.issuers = [f"https://login.microsoftonline.com/{tenant_id}/v2.0", f"https://sts.windows.net/{tenant_id}/"]
        self._resolve = key_resolver or self._jwks_resolver()

    def _jwks_resolver(self) -> KeyResolver:
        """The default key resolver: the tenant's published signing keys (JWKS), cached by ``PyJWKClient``."""
        import jwt

        client = jwt.PyJWKClient(f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys", cache_keys=True)
        return lambda token: client.get_signing_key_from_jwt(token).key

    def claims(self, token: str) -> dict[str, Any]:
        """Verify ``token`` (signature, audience, issuer, expiry) and return its claims; raises a ``jwt`` error."""
        import jwt

        return jwt.decode(token, self._resolve(token), algorithms=["RS256"], audience=self.audiences, issuer=self.issuers)

    async def __call__(self, scope: Scope, receive, send) -> None:
        """Check an HTTP request, then pass it on with its (buffered) body replayed, or answer 401 or 403."""
        if scope["type"] != "http" or scope["path"] in self.open_paths:
            await self.app(scope, receive, send)
            return
        body, receive = await _buffer(receive)
        status, detail = self.check(dict(scope.get("headers") or []), body)
        if status:
            await _deny(send, status, detail)
            return
        await self.app(scope, receive, send)

    def check(self, headers: dict[bytes, bytes], body: bytes) -> tuple[int, str]:
        """Return (0, "") when allowed, else (401|403, reason)."""
        auth = headers.get(b"authorization", b"").decode()
        if not auth.lower().startswith("bearer "):
            return 401, "missing bearer token"
        try:
            claims = self.claims(auth[7:].strip())
        except Exception as exc:  # noqa: BLE001 - any validation failure is a 401
            return 401, f"invalid token: {type(exc).__name__}"
        if self.allowed and claims.get("oid") not in self.allowed:
            return 403, "caller not allowed"
        needed = self.tool_roles.get(_tool_name(body), self.default_roles)
        if not needed.intersection(claims.get("roles") or []):
            return 403, "missing app role"
        return 0, ""


def _tool_name(body: bytes) -> str | None:
    """The tool named by a JSON-RPC ``tools/call`` body; None for any other body, including invalid JSON."""
    try:
        msg = json.loads(body or b"{}")
    except ValueError:
        return None
    if isinstance(msg, dict) and msg.get("method") == "tools/call":
        return (msg.get("params") or {}).get("name")
    return None


async def _buffer(receive):
    """Read the whole request body. Returns it with a ``receive`` that replays it once, then defers to the real one."""
    chunks, more = [], True
    while more:
        message = await receive()
        chunks.append(message.get("body", b""))
        more = message.get("more_body", False)
    body = b"".join(chunks)
    sent = False

    async def replay() -> dict:
        """The buffered body on the first call; later calls (such as a disconnect) go to the real ``receive``."""
        nonlocal sent
        if not sent:
            sent = True
            return {"type": "http.request", "body": body, "more_body": False}
        return await receive()

    return body, replay


async def _deny(send, status: int, detail: str) -> None:
    """Answer with ``{"error": "unauthorized" | "forbidden", "message": detail}`` and the given status."""
    payload = json.dumps({"error": "unauthorized" if status == 401 else "forbidden", "message": detail}).encode()
    await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json")]})
    await send({"type": "http.response.body", "body": payload})


def wrap_from_env(app: ASGIApp, *, default_roles: Iterable[str] = ("Tools.Invoke",), tool_roles: dict[str, set[str]] | None = None) -> ASGIApp:
    """Wrap ``app`` with EntraAuth when ``ENTRA_AUDIENCE`` is set; otherwise return it unchanged.

    Reads ``ENTRA_AUDIENCE``, ``ENTRA_TENANT_ID`` and ``ENTRA_ALLOWED_PRINCIPAL_IDS`` (the audience and the principals
    are comma-separated). Raises ``RuntimeError`` when the audience is set without a tenant.
    """
    audience = os.environ.get("ENTRA_AUDIENCE", "")
    if not audience:
        return app
    tenant_id = os.environ.get("ENTRA_TENANT_ID", "")
    if not tenant_id:
        msg = "ENTRA_TENANT_ID is required when ENTRA_AUDIENCE is set"
        raise RuntimeError(msg)
    return EntraAuth(
        app,
        tenant_id=tenant_id,
        audiences=[a.strip() for a in audience.split(",")],
        allowed_principals=[p.strip() for p in os.environ.get("ENTRA_ALLOWED_PRINCIPAL_IDS", "").split(",")],
        default_roles=default_roles,
        tool_roles=tool_roles,
    )
