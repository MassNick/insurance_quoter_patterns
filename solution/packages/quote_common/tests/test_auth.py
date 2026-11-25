"""The inbound Entra check (``EntraAuth``, ``wrap_from_env``) against locally signed RS256 tokens: no network."""

import asyncio
import json
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from quote_common.auth import EntraAuth, wrap_from_env

pytestmark = pytest.mark.unit
TENANT = "00000000-0000-0000-0000-000000000001"
AUD = "api://dev-only-mcp-quoting"
# Signs the test tokens; the middleware under test verifies them with the public half.
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def token(**over):
    """A signed token for the test tenant, audience and caller; ``over`` replaces or adds claims."""
    claims = {"iss": f"https://login.microsoftonline.com/{TENANT}/v2.0", "aud": AUD, "oid": "caller-1", "roles": ["Tools.Invoke"], "exp": int(time.time()) + 300}
    claims.update(over)
    return jwt.encode(claims, KEY, algorithm="RS256")


async def app(scope, receive, send):
    """The protected app: answers 200 with the body it received, so a test can see the body was replayed."""
    body = (await receive())["body"]
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def call(mw, path="/mcp", auth=None, body=b"{}"):
    """Send one request through ``mw`` (with a Bearer ``auth`` token, if any); returns (status, response body)."""
    headers = [(b"authorization", f"Bearer {auth}".encode())] if auth else []
    sent = []

    async def receive():
        """The request, its body in one chunk."""
        return {"type": "http.request", "body": body, "more_body": False}

    async def send(message):
        """Keep what the middleware sends."""
        sent.append(message)

    asyncio.run(mw({"type": "http", "path": path, "headers": headers}, receive, send))
    return sent[0]["status"], sent[1]["body"]


def middleware(**kw):
    """An ``EntraAuth`` for the test tenant and key that allows only caller ``caller-1``; ``kw`` adds options."""
    return EntraAuth(app, tenant_id=TENANT, audiences=[AUD], allowed_principals=["caller-1"], key_resolver=lambda _t: KEY.public_key(), **kw)


def test_valid_token_passes_and_body_is_replayed():
    body = json.dumps({"method": "tools/call", "params": {"name": "create_quote"}}).encode()
    assert call(middleware(), auth=token(), body=body) == (200, body)


@pytest.mark.parametrize(
    ("auth", "status"),
    [(None, 401), ("not-a-jwt", 401), ("WRONG_AUD", 401), ("WRONG_ISS", 401), ("OTHER_CALLER", 403), ("NO_ROLE", 403)],
)
def test_rejections(auth, status):
    tokens = {"WRONG_AUD": token(aud="api://other"), "WRONG_ISS": token(iss="https://login.microsoftonline.com/other/v2.0"), "OTHER_CALLER": token(oid="intruder"), "NO_ROLE": token(roles=[])}
    assert call(middleware(), auth=tokens.get(auth, auth))[0] == status


def test_per_tool_roles():
    mw = middleware(default_roles={"Tools.Invoke", "Lookup.Read"}, tool_roles={"create_quote": {"Tools.Invoke"}})
    lookup_only = token(roles=["Lookup.Read"])
    create = json.dumps({"method": "tools/call", "params": {"name": "create_quote"}}).encode()
    lookup = json.dumps({"method": "tools/call", "params": {"name": "decode_vin"}}).encode()
    assert call(mw, auth=lookup_only, body=lookup)[0] == 200
    assert call(mw, auth=lookup_only, body=create)[0] == 403


def test_healthz_is_open_and_env_switch(monkeypatch):
    assert call(middleware(), path="/healthz")[0] == 200
    monkeypatch.delenv("ENTRA_AUDIENCE", raising=False)
    assert wrap_from_env(app) is app
