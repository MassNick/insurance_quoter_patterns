"""One tool-wiring function: Azure (toolbox + Foundry IQ + Cosmos) and local differ only by configuration."""

import pytest
from agent_framework import FunctionTool, InMemoryHistoryProvider, MCPStreamableHTTPTool
from quote_common import load_schema

from quote_agent.agent import Runtime
from quote_agent.config import Settings
from quote_agent.fake.rules import extract

pytestmark = pytest.mark.unit


class NoNetworkCredential:
    """A credential that fails when asked for a token: building the agents must not need one."""

    async def get_token(self, *scopes, **kwargs):
        """Always fail."""
        raise AssertionError("construction must not request a token")


# Azure settings with dev-only values: toolbox, Foundry IQ and Cosmos
AZURE = Settings(
    model="azure",
    project_endpoint="https://dev-only.services.ai.azure.com/api/projects/dev-only",
    main_deployment="dev-only-main",
    memory_deployment="dev-only-memory",
    main_options={"reasoning": {"effort": "low"}, "max_tokens": 800},
    toolbox_mcp_url="https://dev-only.services.ai.azure.com/api/projects/dev-only/toolboxes/t/mcp?api-version=v1",
    foundry_iq_mcp_url="https://dev-only.search.windows.net/knowledgebases/kb/mcp?api-version=2026-11-01-preview",
    cosmos_endpoint="https://dev-only.documents.azure.com:443/",
)


def test_azure_wiring_uses_toolbox_foundry_iq_and_cosmos():
    rt = Runtime(AZURE, credential=NoNetworkCredential())
    assert type(rt.main.client).__name__ == type(rt.memory.client).__name__ == "FoundryChatClient"
    assert type(rt.history).__name__ == "CosmosHistoryProvider"
    assert [(type(t).__name__, t.name) for t in rt.main.mcp_tools] == [
        ("FoundryToolbox", "toolbox"),
        ("MCPStreamableHTTPTool", "knowledge"),
    ]
    assert (
        rt.main.default_options["model"] == "dev-only-main" and rt.memory.default_options["model"] == "dev-only-memory"
    )
    assert rt.main.default_options["reasoning"] == {"effort": "low"} and rt.main.default_options["max_tokens"] == 800
    assert rt.main.default_options["store"] is False


def test_local_wiring_uses_direct_mcp_urls_and_a_fake_knowledge_base():
    rt = Runtime(Settings(model="fake"))
    assert isinstance(rt.history, InMemoryHistoryProvider)
    mcp = {t.name: t for t in rt.main.mcp_tools}
    assert set(mcp) == {"quoting", "a2ui"} and all(isinstance(t, MCPStreamableHTTPTool) for t in mcp.values())
    local = [t.name for t in rt.main.default_options["tools"] if isinstance(t, FunctionTool)]
    assert local == ["ui__suggest_replies", "ui__set_phase", "knowledge__knowledge_base_retrieve"]


def test_settings_from_env(monkeypatch):
    monkeypatch.setenv("QUOTE_AGENT_MODEL", "azure")
    monkeypatch.setenv("MEMORY_TEMPERATURE", "0")
    monkeypatch.setenv("MAIN_REASONING_EFFORT", "medium")
    settings = Settings.from_env()
    assert settings.memory_options == {"temperature": 0.0} and settings.main_options == {
        "reasoning": {"effort": "medium"}
    }
    with pytest.raises(ValueError, match="MAIN_MODEL_DEPLOYMENT"):
        settings.validate()


def test_memory_output_schema_is_an_environment_setting(monkeypatch):
    monkeypatch.delenv("MEMORY_OUTPUT_SCHEMA", raising=False)
    assert Settings.from_env().memory_output_schema == "memory-output.schema.json"
    monkeypatch.setenv("MEMORY_OUTPUT_SCHEMA", "document-output.schema.json")
    assert Settings.from_env().memory_output_schema == "document-output.schema.json"


def test_memory_validator_and_response_format_use_the_configured_schema():
    output = extract("Bonjour", {}, "other")  # a valid memory output
    for name, valid in (("memory-output.schema.json", True), ("document-output.schema.json", False)):
        rt = Runtime(
            Settings(model="fake", memory_output_schema=name)
        )  # document-output stands in for another contract
        contract = load_schema(name)
        assert rt.memory_schema.schema["title"] == contract["title"] and rt.memory_schema.is_valid(output) is valid
        fmt = rt.memory.default_options["response_format"]["json_schema"]
        assert fmt["strict"] is False and fmt["schema"]["properties"] == contract["properties"]
