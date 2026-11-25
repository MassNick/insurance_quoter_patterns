"""Configuration from environment variables only (README has the table). Model names are never hard-coded: the deploy
script sets them from the solution's settings.yaml, and this module never reads that file."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any


def _env(name: str, default: str = "") -> str:
    """Environment variable ``name`` with whitespace stripped; ``default`` when it is unset."""
    return os.environ.get(name, default).strip()


def _model_options(prefix: str) -> dict[str, Any]:
    """Per-agent generation options: ``<PREFIX>_REASONING_EFFORT``, ``_TEMPERATURE``, ``_MAX_OUTPUT_TOKENS``."""
    opts: dict[str, Any] = {}
    if effort := _env(f"{prefix}_REASONING_EFFORT"):
        opts["reasoning"] = {"effort": effort}
    if temperature := _env(f"{prefix}_TEMPERATURE"):
        opts["temperature"] = float(temperature)
    if max_tokens := _env(f"{prefix}_MAX_OUTPUT_TOKENS"):
        opts["max_tokens"] = int(max_tokens)
    return opts


@dataclass(frozen=True)
class Settings:
    """Runtime settings read from the environment by ``from_env`` (the README lists every variable). Frozen."""

    model: str = "azure"  # azure | fake
    project_endpoint: str = ""  # FOUNDRY_PROJECT_ENDPOINT; required with azure
    main_deployment: str = ""  # MAIN_MODEL_DEPLOYMENT; required with azure
    memory_deployment: str = ""  # MEMORY_MODEL_DEPLOYMENT; required with azure
    # Generation options from MAIN_* and MEMORY_* (reasoning, temperature, max_tokens); empty = the model's defaults
    main_options: dict[str, Any] = field(default_factory=dict)
    memory_options: dict[str, Any] = field(default_factory=dict)
    memory_output_schema: str = "memory-output.schema.json"  # a file name in contracts/
    prompt_version: str = "v1"  # PROMPT_VERSION: part of prompt_cache_key; set at each deploy
    toolbox_mcp_url: str = ""  # TOOLBOX_MCP_URL (Azure): when set, the two direct MCP URLs below are ignored
    foundry_iq_mcp_url: str = ""  # FOUNDRY_IQ_MCP_URL (Azure): the knowledge base; empty = a local fake one
    foundry_iq_scope: str = "https://search.azure.com/.default"  # FOUNDRY_IQ_TOKEN_SCOPE
    # Local and e2e: the MCP servers directly (MCP_QUOTING_URL, MCP_A2UI_URL)
    mcp_quoting_url: str = "http://localhost:8101/mcp"
    mcp_a2ui_url: str = "http://localhost:8102/mcp"
    cosmos_endpoint: str = ""  # COSMOS_ENDPOINT: the conversation history store; empty = in-memory history
    cosmos_database: str = "quoting"
    cosmos_container: str = "agent_history"  # COSMOS_CONTAINER; partition key /session_id
    memory_timeout_s: float = 4.0  # MEMORY_TIMEOUT_SECONDS: after this the turn goes on without memory output

    @classmethod
    def from_env(cls) -> Settings:
        """Read every setting from the environment; an unset variable keeps the default shown on its field."""
        return cls(
            model=_env("QUOTE_AGENT_MODEL", "azure").lower(),
            project_endpoint=_env("FOUNDRY_PROJECT_ENDPOINT"),
            main_deployment=_env("MAIN_MODEL_DEPLOYMENT"),
            memory_deployment=_env("MEMORY_MODEL_DEPLOYMENT"),
            main_options=_model_options("MAIN"),
            memory_options=_model_options("MEMORY"),
            memory_output_schema=_env("MEMORY_OUTPUT_SCHEMA", "memory-output.schema.json"),
            prompt_version=_env("PROMPT_VERSION", "v1"),
            toolbox_mcp_url=_env("TOOLBOX_MCP_URL"),
            foundry_iq_mcp_url=_env("FOUNDRY_IQ_MCP_URL"),
            foundry_iq_scope=_env("FOUNDRY_IQ_TOKEN_SCOPE", "https://search.azure.com/.default"),
            mcp_quoting_url=_env("MCP_QUOTING_URL", "http://localhost:8101/mcp"),
            mcp_a2ui_url=_env("MCP_A2UI_URL", "http://localhost:8102/mcp"),
            cosmos_endpoint=_env("COSMOS_ENDPOINT"),
            cosmos_database=_env("COSMOS_DATABASE", "quoting"),
            cosmos_container=_env("COSMOS_CONTAINER", "agent_history"),
            memory_timeout_s=float(_env("MEMORY_TIMEOUT_SECONDS", "4")),
        )

    def validate(self) -> None:
        """Raise ``ValueError`` for an unknown ``model`` or, with ``azure``, a missing endpoint or deployment name."""
        if self.model not in ("azure", "fake"):
            msg = f"QUOTE_AGENT_MODEL must be azure or fake, not {self.model!r}"
            raise ValueError(msg)
        if self.model == "azure":
            missing = [
                n
                for n, v in (
                    ("FOUNDRY_PROJECT_ENDPOINT", self.project_endpoint),
                    ("MAIN_MODEL_DEPLOYMENT", self.main_deployment),
                    ("MEMORY_MODEL_DEPLOYMENT", self.memory_deployment),
                )
                if not v
            ]
            if missing:
                msg = f"missing environment variables for QUOTE_AGENT_MODEL=azure: {', '.join(missing)}"
                raise ValueError(msg)
