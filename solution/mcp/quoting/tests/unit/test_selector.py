"""`from_env`: the one place implementations are chosen from environment variables."""

import sys
import types

import pytest

from mcp_quoting.adapters import (
    CosmosStateReader,
    HttpAddressService,
    HttpBffStateReader,
    HttpRatingEngine,
    HttpVehicleCatalog,
    HttpVinDecoder,
    InMemoryQuoteStore,
    StubAddressService,
    StubRatingEngine,
    StubVehicleCatalog,
    StubVinDecoder,
    from_env,
)

pytestmark = pytest.mark.unit

BASE = {"SESSION_REF_KEY": "dev-only-test-key"}  # the one variable without a default


def test_defaults_are_the_stubs_and_the_bff_reader():
    deps = from_env(BASE)
    assert isinstance(deps.rating, StubRatingEngine) and isinstance(deps.catalog, StubVehicleCatalog)
    assert isinstance(deps.addresses, StubAddressService) and isinstance(deps.vins, StubVinDecoder)
    assert isinstance(deps.state, HttpBffStateReader) and isinstance(deps.quotes, InMemoryQuoteStore)
    assert deps.key == b"dev-only-test-key"


def test_the_session_ref_key_is_required_and_has_no_default():
    for env in ({}, {"SESSION_REF_KEY": ""}):
        with pytest.raises(RuntimeError, match="SESSION_REF_KEY"):
            from_env(env)


def test_each_adapter_switches_to_http_with_its_own_base_url():
    env = {
        **BASE,
        "RATING_ENGINE": "http",
        "RATING_BASE_URL": "https://rating.test",
        "VEHICLE_CATALOG": "http",
        "CATALOG_BASE_URL": "https://catalog.test",
        "ADDRESS_SERVICE": "http",
        "ADDRESS_BASE_URL": "https://address.test",
        "VIN_DECODER": "http",
        "VIN_BASE_URL": "https://vin.test",
    }
    deps = from_env(env)
    assert isinstance(deps.rating, HttpRatingEngine) and isinstance(deps.catalog, HttpVehicleCatalog)
    assert isinstance(deps.addresses, HttpAddressService) and isinstance(deps.vins, HttpVinDecoder)


@pytest.mark.parametrize(
    ("var", "prefix"),
    [
        ("RATING_ENGINE", "RATING"),
        ("VEHICLE_CATALOG", "CATALOG"),
        ("ADDRESS_SERVICE", "ADDRESS"),
        ("VIN_DECODER", "VIN"),
    ],
)
def test_http_without_a_base_url_is_a_configuration_error(var, prefix):
    with pytest.raises(RuntimeError, match=f"{prefix}_BASE_URL"):
        from_env({**BASE, var: "http"})


def test_an_unknown_choice_names_the_variable_and_the_options():
    with pytest.raises(ValueError, match=r"STATE_READER='redis' is not one of \['bff', 'cosmos'\]"):
        from_env({**BASE, "STATE_READER": "redis"})


def test_the_bff_reader_uses_bff_base_url():
    reader = from_env({**BASE, "BFF_BASE_URL": "http://bff.test:9000"}).state
    assert str(reader._api._client.base_url).rstrip("/") == "http://bff.test:9000"


def test_an_entra_scope_adds_the_token_hook_and_timeouts_are_configurable(monkeypatch):
    class Credential:
        """A managed identity that issues a token for any scope."""

        async def get_token(self, scope):
            """A token that names the scope it was asked for."""
            return types.SimpleNamespace(token=f"token-for-{scope}")

    for name in ("azure", "azure.identity"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.identity.aio", types.SimpleNamespace(DefaultAzureCredential=Credential))
    env = {
        **BASE,
        "RATING_ENGINE": "http",
        "RATING_BASE_URL": "https://r.test",
        "RATING_SCOPE": "api://rating/.default",
        "HTTP_TIMEOUT_SECONDS": "2.5",
        "HTTP_RETRIES": "0",
    }
    api = from_env(env).rating._api
    assert api._token is not None and api._retries == 0 and api._client.timeout.read == 2.5


@pytest.mark.anyio
async def test_the_token_hook_asks_the_managed_identity_for_the_scope(monkeypatch):
    calls = []

    class Credential:
        """A managed identity that records the scopes it is asked for."""

        async def get_token(self, scope):
            """Record the scope and issue a fixed token."""
            calls.append(scope)
            return types.SimpleNamespace(token="abc")

    for name in ("azure", "azure.identity"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.identity.aio", types.SimpleNamespace(DefaultAzureCredential=Credential))
    from mcp_quoting.adapters import entra_token

    assert await entra_token("api://rating/.default")() == "abc" and calls == ["api://rating/.default"]


def test_cosmos_is_selected_by_state_reader_and_needs_its_endpoint(monkeypatch):
    class Client:
        """A stand-in Cosmos client."""

        def __init__(self, endpoint, credential):
            """Ignore the endpoint and the credential."""

        def get_database_client(self, name):
            """A database client whose container client is a plain object."""
            return types.SimpleNamespace(get_container_client=lambda container: object())

    for name in ("azure", "azure.cosmos", "azure.identity"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    monkeypatch.setitem(sys.modules, "azure.cosmos.aio", types.SimpleNamespace(CosmosClient=Client))
    monkeypatch.setitem(sys.modules, "azure.identity.aio", types.SimpleNamespace(DefaultAzureCredential=object))
    assert isinstance(
        from_env({**BASE, "STATE_READER": "cosmos", "COSMOS_ENDPOINT": "https://c/"}).state, CosmosStateReader
    )
    with pytest.raises(RuntimeError, match="COSMOS_ENDPOINT"):
        from_env({**BASE, "STATE_READER": "cosmos"})


@pytest.mark.parametrize(
    "env",
    [
        {"STATE_READER": "cosmos", "COSMOS_ENDPOINT": "https://c/"},
        {"RATING_ENGINE": "http", "RATING_BASE_URL": "https://r.test", "RATING_SCOPE": "api://rating/.default"},
    ],
)
def test_azure_features_without_the_azure_extra_say_how_to_install_it(monkeypatch, env):
    monkeypatch.setitem(sys.modules, "azure", None)  # makes `import azure...` raise ImportError
    with pytest.raises(RuntimeError, match=r"mcp-quoting\[azure\]"):
        from_env({**BASE, **env})
