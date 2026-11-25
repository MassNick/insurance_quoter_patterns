"""`CosmosStateReader`: the session document, `quote_blocked`, and `from_env` wired to stand-ins for the Azure SDKs."""

import sys
import types

import pytest

from mcp_quoting.adapters import CosmosStateReader, SessionNotFound

pytestmark = [pytest.mark.unit, pytest.mark.anyio]


class CosmosError(Exception):
    """Like azure.cosmos.exceptions.CosmosHttpResponseError: the HTTP status is an attribute."""

    def __init__(self, status_code):
        """`status_code` is the HTTP status of the failed call."""
        super().__init__(f"status {status_code}")
        self.status_code = status_code


class FakeContainer:
    """A Cosmos container with canned items. It records every read, and raises `error` instead when one is set."""

    def __init__(self, items=None, error=None):
        """`items` maps an id to its document; `error` is raised by every read."""
        self.items, self.error, self.calls = items or {}, error, []

    async def read_item(self, item, partition_key):
        """Record the call, then raise `error` or return the item."""
        self.calls.append((item, partition_key))
        if self.error:
            raise self.error
        return self.items[item]


async def test_reads_the_session_document_by_id_and_partition_key():
    container = FakeContainer({"s_1": {"session_id": "s_1", "quote": {}, "_etag": "e1"}})
    state = await CosmosStateReader(container).read("s_1", "ignored-session-ref")
    assert state["_etag"] == "e1" and container.calls == [("s_1", "s_1")]


@pytest.mark.parametrize(
    ("blocking", "expected"),
    [
        ({}, False),  # a document that never had the fields
        ({"stop": None, "rules_error": False}, False),  # the BFF's defaults
        ({"stop": {"rule_id": "JEUNE_CONDUCTEUR_PRINCIPAL", "reason": "...", "paths": ["/quote/date_of_birth"]}}, True),
        ({"stop": None, "rules_error": True}, True),
        ({"stop": {"rule_id": "X"}, "rules_error": True}, True),
    ],
)
async def test_quote_blocked_comes_from_stop_and_rules_error(blocking, expected):
    document = {"session_id": "s_1", "quote": {}, **blocking}
    container = FakeContainer({"s_1": document})
    state = await CosmosStateReader(container).read("s_1", "r")
    assert state["quote_blocked"] is expected
    assert state == {**document, "quote_blocked": expected}  # the document is returned whole
    assert "quote_blocked" not in document  # and not modified


async def test_a_missing_document_is_session_not_found():
    with pytest.raises(SessionNotFound):
        await CosmosStateReader(FakeContainer(error=CosmosError(404))).read("s_9", "r")


async def test_other_cosmos_errors_surface_unchanged():
    with pytest.raises(CosmosError) as caught:
        await CosmosStateReader(FakeContainer(error=CosmosError(429))).read("s_1", "r")
    assert caught.value.status_code == 429


def fake_azure(monkeypatch):
    """Stand-ins for azure.cosmos.aio and azure.identity.aio, so the wiring is tested without the SDKs."""
    seen = {}

    class Credential:
        """Stands in for `DefaultAzureCredential`."""

    class Client:
        """Stands in for `CosmosClient`: remembers what it was built and asked with."""

        def __init__(self, endpoint, credential):
            """Remember the endpoint and the credential."""
            seen["endpoint"], seen["credential"] = endpoint, credential

        def get_database_client(self, name):
            """Remember the database name; the container client is the container name itself."""
            seen["database"] = name
            return types.SimpleNamespace(get_container_client=lambda container: seen.setdefault("container", container))

    modules = {
        "azure": types.ModuleType("azure"),
        "azure.cosmos": types.ModuleType("azure.cosmos"),
        "azure.cosmos.aio": types.SimpleNamespace(CosmosClient=Client),
        "azure.identity": types.ModuleType("azure.identity"),
        "azure.identity.aio": types.SimpleNamespace(DefaultAzureCredential=Credential),
    }
    for name, module in modules.items():
        monkeypatch.setitem(sys.modules, name, module)
    return seen, Credential


def test_from_env_connects_with_the_default_azure_credential(monkeypatch):
    seen, credential = fake_azure(monkeypatch)
    reader = CosmosStateReader.from_env({"COSMOS_ENDPOINT": "https://cosmos.test:443/"})
    assert isinstance(reader, CosmosStateReader)
    assert seen["endpoint"] == "https://cosmos.test:443/" and isinstance(seen["credential"], credential)
    assert (seen["database"], seen["container"]) == ("quoting", "sessions")


def test_from_env_database_and_container_are_configurable(monkeypatch):
    seen, _ = fake_azure(monkeypatch)
    CosmosStateReader.from_env({"COSMOS_ENDPOINT": "https://c/", "COSMOS_DATABASE": "db", "COSMOS_CONTAINER": "docs"})
    assert (seen["database"], seen["container"]) == ("db", "docs")
