"""Shared by every script in deploy/: one HTTP client with Entra auth, --dry-run, and a few argument helpers.

Dry run prints each request as one JSON line on stdout ({"method", "url", "params", "body"}) and sends nothing, so a deployment
can be reviewed (and tested) without Azure. Nothing here runs at import time.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import httpx

FOUNDRY_SCOPE = "https://ai.azure.com/.default"  # Entra scope of the Foundry project endpoint
SEARCH_SCOPE = "https://search.azure.com/.default"  # Entra scope of Azure AI Search


def env(name: str, default: str | None = None) -> str | None:
    """Environment variable, treating the empty string as unset."""
    return os.environ.get(name) or default


def add_common_args(parser: argparse.ArgumentParser) -> None:
    """Add `--dry-run` to `parser`."""
    parser.add_argument("--dry-run", action="store_true", help="print the requests as JSON lines and send nothing")


def require(args: argparse.Namespace, *names: str) -> None:
    """argparse cannot require 'flag or environment variable', so check after parsing and exit with a usage error."""
    missing = [n for n in names if not getattr(args, n)]
    if missing:
        flags = ", ".join("--" + n.replace("_", "-") for n in missing)
        sys.exit(f"error: missing {flags} (or the matching environment variable)")


def decode_sse(text: str) -> list[Any]:
    """The JSON payload of every `data:` line of a text/event-stream body."""
    events = []
    for line in text.splitlines():
        if line.startswith("data:") and line[5:].strip():
            events.append(json.loads(line[5:].strip()))
    return events


class Client:
    """JSON over HTTP against one base URL. `scope=None` sends no token (public edge); otherwise an Entra bearer for `scope`."""

    def __init__(
        self,
        base_url: str,
        scope: str | None = None,
        *,
        dry_run: bool = False,
        credential: Any = None,
        transport: httpx.BaseTransport | None = None,
        timeout: float = 60.0,
    ) -> None:
        """`scope` is the Entra scope to ask a bearer token for (None: send no token). `credential` and `transport`
        replace the Azure credential and the HTTP transport in tests."""
        self.base_url = base_url.rstrip("/")
        self.scope = scope
        self.dry_run = dry_run
        self._credential = credential
        self._http = httpx.Client(transport=transport, timeout=timeout, follow_redirects=False)

    def _token(self) -> str:
        """A bearer token for `scope`; the Azure credential is created on first use."""
        if self._credential is None:
            from azure.identity import DefaultAzureCredential  # lazy: dry runs and tests need no Azure SDK

            self._credential = DefaultAzureCredential()
        return self._credential.get_token(self.scope).token

    def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        body: Any = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send one request. Returns parsed JSON, the list of SSE events, or {} on an empty body (and always {} in dry run)."""
        url = f"{self.base_url}{path}"
        if self.dry_run:
            print(
                json.dumps({"method": method, "url": url, "params": params or {}, "body": body}, ensure_ascii=False), flush=True
            )
            return {}
        hdrs = dict(headers or {})
        if self.scope:
            hdrs["Authorization"] = f"Bearer {self._token()}"
        response = self._http.request(method, url, params=params, json=body, headers=hdrs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {url} -> HTTP {response.status_code}: {response.text[:500]}")
        if response.headers.get("content-type", "").startswith("text/event-stream"):
            return decode_sse(response.text)
        return response.json() if response.content else {}
