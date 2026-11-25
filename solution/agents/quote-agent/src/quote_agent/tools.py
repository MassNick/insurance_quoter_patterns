"""Tool wiring for the main agent: one function for Azure (toolbox + Foundry IQ) and local (direct MCP URLs).

The model-visible names depend on who namespaces them (toolbox, or ``tool_name_prefix`` locally), so every
policy lookup goes through ``logical_name`` (risk R11 in the implementation plan).
"""

from __future__ import annotations

import json
from collections.abc import AsyncGenerator, Mapping
from functools import lru_cache
from typing import Any

import httpx2
from agent_framework import Content, FunctionTool, MCPStreamableHTTPTool
from mcp import types
from quote_common import load_tools

from quote_agent.config import Settings
from quote_agent.fake.rules import kb_search

# tools.json definitions by logical name, for example ``quoting.create_quote``
TOOLS: dict[str, dict[str, Any]] = {t["name"]: t for t in load_tools()["tools"]}
# logical name -> terminal | narrate: what the turn does with the result (tools.json)
RESULT_MODE: dict[str, str] = {name: t["result_mode"] for name, t in TOOLS.items()}


@lru_cache(maxsize=1)
def _aliases() -> dict[str, str]:
    """Every served spelling of each tool (logical, wire, toolbox ``server___tool``, ``server_tool``, ``tool``)
    → its logical name."""
    out: dict[str, str] = {}
    for name, t in TOOLS.items():
        server, local = name.split(".", 1)
        # ___ is the Foundry toolbox's spelling (quoting___create_quote); __ the local MCP client's
        for alias in (name, t["wire_name"], f"{server}___{local}", f"{server}_{local}", local):
            out[alias] = name
    return out


def logical_name(function_name: str) -> str:
    """Any served spelling (``quoting___x`` from the toolbox, ``quoting__x``, ``quoting_x``, ``quoting.x``, ``x``).

    Returns the logical name (``quoting.x``); an unknown name comes back unchanged.
    """
    return _aliases().get(function_name, function_name)


def injected_args(name: str) -> tuple[str, ...]:
    """Arguments the hosted agent adds after the model's call and hides from the model (tools.json)."""
    return tuple(TOOLS.get(name, {}).get("injected_args", {}))


def parse_mcp_result(result: types.CallToolResult) -> list[Content]:
    """Keep text and embedded resources apart, with the resource's MIME type and audience (A2UI pass-through)."""
    out: list[Content] = []
    for item in result.content:
        if isinstance(item, types.TextContent):
            out.append(Content.from_text(item.text))
        elif isinstance(item, types.EmbeddedResource) and isinstance(item.resource, types.TextResourceContents):
            audience = list((item.annotations.audience if item.annotations else None) or [])
            out.append(
                Content.from_text(
                    item.resource.text,
                    additional_properties={"mime_type": item.resource.mime_type, "audience": audience},
                )
            )
    if result.structured_content is not None and not out:
        out.append(Content.from_text(json.dumps(result.structured_content, ensure_ascii=False)))
    return out


async def _ack(**_: Any) -> str:
    """Handler of the local ``ui.*`` tools: just acknowledge (``tool_policy`` emits their events)."""
    return "ok"  # ui.* events are emitted by the tool policy from the arguments


async def _fake_kb(query: str, **_: Any) -> str:
    """Local stand-in for the knowledge base tool: the passages of ``fake.rules.kb_search`` as JSON."""
    return json.dumps({"passages": kb_search(query)}, ensure_ascii=False)


def _local(name: str, func: Any) -> FunctionTool:
    """A ``FunctionTool`` that runs in this process; its name, description and schema come from tools.json."""
    t = TOOLS[name]
    return FunctionTool(name=t["wire_name"], description=t["description"], func=func, input_model=t["inputSchema"])


class _EntraAuth(httpx2.Auth):
    """Bearer token from an async Entra credential on every request (Foundry IQ endpoint)."""

    def __init__(self, credential: Any, scope: str) -> None:
        """Keep the async credential and the Entra scope to request tokens for."""
        self._credential, self._scope = credential, scope

    async def async_auth_flow(self, request: httpx2.Request) -> AsyncGenerator[httpx2.Request, httpx2.Response]:
        """Get a token for the scope and set the bearer header (httpx calls this for every request)."""
        token = await self._credential.get_token(self._scope)
        request.headers["Authorization"] = f"Bearer {token.token}"
        yield request


def build_tools(settings: Settings, credential: Any = None, mcp_clients: Mapping[str, Any] | None = None) -> list[Any]:
    """Main agent tools: the local ``ui.*`` tools, the toolbox or the two MCP servers, and the knowledge base.

    The knowledge base is Foundry IQ when its URL is set, otherwise a local fake. ``mcp_clients``
    (server label → entered ``mcp.Client``) replaces the MCP URLs in tests.
    """
    # Options of every MCP tool: our result parser (keeps A2UI resources), no approval prompts, no MCP prompts
    common: dict[str, Any] = {
        "parse_tool_results": parse_mcp_result,
        "approval_mode": "never_require",
        "load_prompts": False,
    }
    tools: list[Any] = [_local("ui.suggest_replies", _ack), _local("ui.set_phase", _ack)]
    if settings.toolbox_mcp_url:
        from agent_framework_foundry_hosting import FoundryToolbox

        tools.append(FoundryToolbox(credential, url=settings.toolbox_mcp_url, name="toolbox", **common))
    else:
        for server, url in (("quoting", settings.mcp_quoting_url), ("a2ui", settings.mcp_a2ui_url)):
            conn = {"mcp_client": mcp_clients[server]} if mcp_clients else {"url": url}
            tools.append(MCPStreamableHTTPTool(server, tool_name_prefix=server, **conn, **common))
    if settings.foundry_iq_mcp_url:
        tools.append(
            MCPStreamableHTTPTool(
                "knowledge",
                url=settings.foundry_iq_mcp_url,
                tool_name_prefix="knowledge",
                allowed_tools=["knowledge_base_retrieve"],
                http_client=httpx2.AsyncClient(auth=_EntraAuth(credential, settings.foundry_iq_scope)),
                **common,
            )
        )
    else:
        tools.append(_local("knowledge.knowledge_base_retrieve", _fake_kb))
    return tools
