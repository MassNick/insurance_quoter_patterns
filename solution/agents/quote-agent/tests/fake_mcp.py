"""In-process fake MCP servers for mcp-quoting and mcp-a2ui (shapes from contracts/interfaces.md §3)."""

from __future__ import annotations

import json
from typing import Any

from mcp import types
from mcp.server.mcpserver import MCPServer

SEEN: list[tuple[str, str]] = []  # (tool, session_ref) as received by the servers
ARGS: dict[str, dict[str, Any]] = {}  # last arguments each a2ui tool received
REFUSE: dict[str, bool] = {"create_quote": False}  # set True to get mcp-quoting's missing_fields refusal
# the missing_fields of the refusal
MISSING = [{"path": "/quote/email", "key": "email", "label": "Courriel", "reason": "required_for_quote"}]

# three fixed offers: essential, plus, premium
OFFERS = [
    {"offer_type": t, "monthly": m, "annual": m * 12, "deductible": 500, "coverages": []}
    for t, m in (("essential", 92), ("plus", 118), ("premium", 141))
]


def _a2ui(surface_id: str, messages: list[dict[str, Any]]) -> types.CallToolResult:
    """A tool result in mcp-a2ui's shape: a JSON summary for the model, then the A2UI messages as an embedded
    resource for the user."""
    return types.CallToolResult(
        content=[
            types.TextContent(type="text", text=json.dumps({"surface_id": surface_id, "status": "shown"})),
            types.EmbeddedResource(
                type="resource",
                annotations=types.Annotations(audience=["user"]),
                resource=types.TextResourceContents(
                    uri=f"a2ui://{surface_id}", mime_type="application/a2ui+json", text=json.dumps(messages)
                ),
            ),
        ]
    )


def a2ui_server() -> MCPServer:
    """A fake mcp-a2ui with ``render_form``, ``close_form`` and ``render_offers``; calls are recorded in ``SEEN``.

    The tools have no docstring on purpose: an MCP server publishes it as the tool description the model sees.
    """
    server = MCPServer("mcp-a2ui")

    # records the call (form_spec, state, mode) and returns a surface for the form
    @server.tool()
    def render_form(
        form_spec: dict[str, Any], state: dict[str, Any] | None = None, mode: str = "new", session_ref: str = ""
    ) -> types.CallToolResult:
        SEEN.append(("render_form", session_ref))
        ARGS["render_form"] = {"form_spec": form_spec, "state": state, "mode": mode}
        sid = form_spec["form_id"]
        return _a2ui(sid, [{"version": "1.0", "createSurface": {"surfaceId": sid, "components": [], "dataModel": {}}}])

    # returns a deleteSurface message for the form
    @server.tool()
    def close_form(surface_id: str, reason: str = "", session_ref: str = "") -> types.CallToolResult:
        SEEN.append(("close_form", session_ref))
        return _a2ui(surface_id, [{"version": "1.0", "deleteSurface": {"surfaceId": surface_id}}])

    # records the injected version and offers and returns the offers surface
    @server.tool()
    def render_offers(
        quote_id: str,
        version: int = 1,
        offers: list[dict[str, Any]] | None = None,
        highlight: str | None = None,
        show_adjust: bool = False,
        session_ref: str = "",
    ) -> types.CallToolResult:
        SEEN.append(("render_offers", session_ref))
        ARGS["render_offers"] = {"quote_id": quote_id, "version": version, "offers": offers}
        sid = f"offers_{quote_id}_v{version}".lower().replace("-", "_")
        return _a2ui(sid, [{"version": "1.0", "createSurface": {"surfaceId": sid, "components": [], "dataModel": {}}}])

    return server


def quoting_server() -> MCPServer:
    """A fake mcp-quoting with ``create_quote`` (or its missing_fields refusal), ``modify_quote`` and
    ``identify_address``.

    The tools have no docstring on purpose: an MCP server publishes it as the tool description the model sees.
    """
    server = MCPServer("mcp-quoting")

    # returns a quote with three offers, or the missing_fields refusal when REFUSE is set
    @server.tool()
    def create_quote(offer_types: list[str] | None = None, session_ref: str = "") -> types.CallToolResult:
        SEEN.append(("create_quote", session_ref))
        if REFUSE["create_quote"]:
            body = {"error": "missing_fields", "message": "Required fields are missing.", "missing_fields": MISSING}
            return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(body))], is_error=True)
        quote = {"quote_id": "Q-1", "version": 1, "offers": OFFERS, "input_hash": "sha256:dev-only"}
        return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(quote))])

    # returns version 2 of the quote
    @server.tool()
    def modify_quote(quote_id: str, changes: dict[str, Any], session_ref: str = "") -> str:
        SEEN.append(("modify_quote", session_ref))
        return json.dumps(
            {
                "quote_id": quote_id,
                "version": 2,
                "previous_version": 1,
                "delta_monthly": -9,
                "offers": OFFERS,
                "input_hash": "sha256:dev-only-2",
            }
        )

    # returns one address candidate
    @server.tool()
    def identify_address(query: str, postal_code: str | None = None, session_ref: str = "") -> str:
        SEEN.append(("identify_address", session_ref))
        return json.dumps(
            {
                "candidates": [
                    {
                        "address_line1": "1 rue Exemple",
                        "city": "Lévis",
                        "province_code": "QC",
                        "postal_code": "G6V 0A1",
                        "confidence": 0.9,
                    }
                ]
            }
        )

    return server
