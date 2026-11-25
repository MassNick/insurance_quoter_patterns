"""Test doubles for the deploy scripts: a fake credential, a routed fake HTTP backend and a client factory."""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest

from common import Client


class FakeCredential:
    """Stands in for azure.identity: records the scopes asked for, returns a fixed token."""

    def __init__(self) -> None:
        """Start with no scope recorded."""
        self.scopes: list[str] = []

    def get_token(self, scope: str):
        """Record `scope` and return a fixed token."""
        self.scopes.append(scope)
        return SimpleNamespace(token="fake-token")


class Fake:
    """An HTTP backend for Client: routes (method, path) to a response and records every request."""

    def __init__(self, routes: dict[tuple[str, str], object]) -> None:
        """`routes` maps (method, path) to a response, a JSON body or a function of the request."""
        self.routes = routes
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        """Answer one request (404 for an unknown route) and record it."""
        self.requests.append(request)
        route = self.routes.get((request.method, request.url.path))
        if route is None:
            return httpx.Response(404, json={"error": f"no route for {request.method} {request.url.path}"})
        return route(request) if callable(route) else _as_response(route)

    def bodies(self) -> list:
        """The JSON body of every recorded request (None when it had none)."""
        return [json.loads(r.content) if r.content else None for r in self.requests]


def _as_response(route: object) -> httpx.Response:
    """A route given as a JSON body becomes a 200 response."""
    if isinstance(route, httpx.Response):
        return route
    return httpx.Response(200, json=route)


def sse(*events: dict) -> httpx.Response:
    """A 200 event-stream response with one `data:` line per event."""
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events)
    return httpx.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})


@pytest.fixture
def credential() -> FakeCredential:
    """A fake credential that records the scopes asked for."""
    return FakeCredential()


@pytest.fixture
def make_client(credential):
    """A factory of `Client` objects over the fake backend; each call returns (client, fake)."""

    def build(
        routes: dict, base_url: str = "https://host.example/base", scope: str | None = "scope/.default", dry_run: bool = False
    ):
        """Build one client for `routes`."""
        fake = Fake(routes)
        return Client(base_url, scope, dry_run=dry_run, credential=credential, transport=httpx.MockTransport(fake)), fake

    return build
