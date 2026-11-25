"""session_ref: the short-lived, HMAC-signed token the hosted agent injects into tool calls.

Format: ``base64url("<session_id>.<expiry_epoch>") + "." + base64url(hmac_sha256(key, payload))``.
The signing key is ``SESSION_REF_KEY`` (Key Vault in Azure). The model never sees the token.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time


class InvalidSessionRef(ValueError):
    """The token is malformed, forged (bad signature) or expired."""


def _b64(data: bytes) -> str:
    """URL-safe base64 without padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    """The inverse of ``_b64``: restores the padding first."""
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign(session_id: str, key: bytes, ttl_seconds: int = 900, now: float | None = None) -> str:
    """A token for ``session_id`` that expires ``ttl_seconds`` after ``now`` (default: the current time).
    Raises ``ValueError`` for an empty key."""
    if not key:
        msg = "empty signing key"
        raise ValueError(msg)
    expiry = int((now if now is not None else time.time()) + ttl_seconds)
    payload = f"{session_id}.{expiry}".encode()
    sig = hmac.new(key, payload, hashlib.sha256).digest()
    return f"{_b64(payload)}.{_b64(sig)}"


def verify(token: str, key: bytes, now: float | None = None) -> str:
    """Return the session id, or raise InvalidSessionRef."""
    try:
        payload_b64, sig_b64 = token.split(".")
        payload = _unb64(payload_b64)
        sig = _unb64(sig_b64)
    except (ValueError, TypeError) as exc:
        msg = "malformed session_ref"
        raise InvalidSessionRef(msg) from exc
    expected = hmac.new(key, payload, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        msg = "bad session_ref signature"
        raise InvalidSessionRef(msg)
    session_id, _, expiry = payload.decode().rpartition(".")
    if not session_id or not expiry.isdigit():
        msg = "malformed session_ref payload"
        raise InvalidSessionRef(msg)
    if int(expiry) < (now if now is not None else time.time()):
        msg = "expired session_ref"
        raise InvalidSessionRef(msg)
    return session_id
