"""Fixtures shared by every test."""

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """Run the `anyio` tests on asyncio only."""
    return "asyncio"
