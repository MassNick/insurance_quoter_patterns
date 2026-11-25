"""The ``azure`` test level for every service (loaded automatically wherever quote_common is installed).

Tests marked ``@pytest.mark.azure`` call a real deployment; they are skipped unless ``RUN_AZURE_TESTS=1``.
"""

from __future__ import annotations

import os

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register the ``azure`` marker."""
    config.addinivalue_line("markers", "azure: integration test against a real deployment (needs RUN_AZURE_TESTS=1)")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """Skip the tests marked ``azure`` unless ``RUN_AZURE_TESTS=1``."""
    if os.environ.get("RUN_AZURE_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="azure test: set RUN_AZURE_TESTS=1 to run it against a deployment")
    for item in items:
        if "azure" in item.keywords:
            item.add_marker(skip)
