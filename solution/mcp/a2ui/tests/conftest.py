"""Fixtures shared by every test."""

import pytest


@pytest.fixture
def anyio_backend():
    """Run the ``anyio`` tests on asyncio only."""
    return "asyncio"


@pytest.fixture(autouse=True)
def entra_off(monkeypatch):
    """The Entra check is on when ENTRA_AUDIENCE is set; a test turns it on itself."""
    monkeypatch.delenv("ENTRA_AUDIENCE", raising=False)
