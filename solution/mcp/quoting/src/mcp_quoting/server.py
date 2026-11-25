"""The MCP server. Names, descriptions and input schemas come from contracts/tools.json, so the contract cannot
drift from the code; this module serves them, validates arguments, runs the handlers and shapes results and errors."""

from __future__ import annotations

import copy
import json
import logging
import os
import time
import traceback
from typing import Any

import httpx
import uvicorn
from jsonschema import Draft202012Validator
from jsonschema.exceptions import best_match
from mcp.server.lowlevel import Server
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool
from pydantic import ValidationError
from quote_common import logs
from quote_common.auth import ASGIApp, wrap_from_env
from quote_common.contracts import load_tools
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from . import __version__
from .adapters import Deps, SessionNotFound, from_env
from .tools import HANDLERS, ToolFailure

log = logging.getLogger("mcp_quoting")


def tool_specs() -> dict[str, tuple[str, dict[str, Any]]]:
    """`{name: (description, inputSchema)}` of the `quoting` server in tools.json. Every tool also accepts the
    optional string `session_ref`, which the hosted agent injects (the model never sees it)."""
    specs = {}
    for tool in load_tools()["tools"]:
        if tool["server"] == "quoting":
            schema = copy.deepcopy(tool["inputSchema"])
            schema.setdefault("properties", {})["session_ref"] = {"type": "string"}
            specs[tool["name"].split(".", 1)[1]] = (tool["description"], schema)
    return specs


def _result(payload: dict[str, Any], is_error: bool) -> CallToolResult:
    """The JSON the model reads (compact, accents kept), also as structured content for programmatic callers."""
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], structured_content=payload, is_error=is_error)


async def run_tool(
    deps: Deps, validators: dict[str, Draft202012Validator], name: str, args: dict[str, Any]
) -> CallToolResult:
    """Validate, run and log one call. Failures become error results; no argument or state value is ever logged."""
    started = time.perf_counter()
    is_error = True
    try:
        if name not in validators:
            raise ToolFailure("unknown_tool", f"No tool named {name}")
        if (problem := best_match(validators[name].iter_errors(args))) is not None:
            raise ToolFailure("invalid_arguments", f"{problem.json_path}: {problem.message}")
        payload, is_error = await HANDLERS[name](deps, args), False
    except ToolFailure as failure:
        payload = {"error": failure.code, "message": str(failure), **failure.extra}
    except SessionNotFound:
        payload = {"error": "session_not_found", "message": "No committed state for this session"}
    except (httpx.HTTPError, ValidationError) as exc:  # a failed call, or an answer that breaks the Offer contract
        log.warning("upstream failure in %s: %s", name, type(exc).__name__)
        payload = {
            "error": "upstream_error",
            "message": "A back-end service failed, timed out or answered unexpectedly",
        }
    except Exception as exc:  # the message may carry customer data: log the type and the code frames only
        log.error(
            "tool %s crashed with %s",
            name,
            type(exc).__name__,
            extra={"frames": traceback.format_tb(exc.__traceback__)},
        )
        payload = {"error": "internal_error", "message": "The tool failed unexpectedly"}
    log.info(
        "tool call",
        extra={
            "tool": name,
            "outcome": payload.get("error", "ok"),
            "ms": round((time.perf_counter() - started) * 1000),
        },
    )
    return _result(payload, is_error)


def build_server(deps: Deps) -> Server:
    """The MCP server for the tools of tools.json, each run by its handler in `HANDLERS` (see `run_tool`).
    Raises `RuntimeError` when the contract and the handlers do not list the same tools."""
    specs = tool_specs()
    if set(specs) != set(HANDLERS):
        msg = f"tools.json and the handlers disagree on: {sorted(set(specs) ^ set(HANDLERS))}"
        raise RuntimeError(msg)
    validators = {name: Draft202012Validator(schema) for name, (_, schema) in specs.items()}

    async def list_tools(ctx: Any, params: Any) -> ListToolsResult:
        """`tools/list`: every tool of the contract, with its description and input schema."""
        return ListToolsResult(
            tools=[Tool(name=n, description=desc, input_schema=schema) for n, (desc, schema) in specs.items()]
        )

    async def call_tool(ctx: Any, params: Any) -> CallToolResult:
        """`tools/call`: run the named tool with its arguments."""
        return await run_tool(deps, validators, params.name, params.arguments or {})

    return Server("mcp-quoting", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool)


def create_app(deps: Deps | None = None, host: str = "0.0.0.0") -> ASGIApp:
    """The ASGI app: MCP over stateless streamable HTTP at `/mcp`, and `GET /healthz`, behind the shared inbound Entra
    check (NFR-SEC-3), which is off unless `ENTRA_AUDIENCE` is set. `host` is the address the app is served on (the SDK
    checks Host headers when it is a loopback address)."""

    async def healthz(request: Request) -> JSONResponse:
        """Liveness probe: `{"status": "ok"}`, without touching any adapter."""
        return JSONResponse({"status": "ok"})

    app = build_server(deps or from_env()).streamable_http_app(
        stateless_http=True, json_response=True, host=host, custom_starlette_routes=[Route("/healthz", healthz)]
    )
    return wrap_from_env(
        app,
        default_roles={"Tools.Invoke", "Lookup.Read"},
        tool_roles={"create_quote": {"Tools.Invoke"}, "modify_quote": {"Tools.Invoke"}},
    )


def main() -> None:
    """`python -m mcp_quoting`: shared JSON logs (and Application Insights when configured), then serve on HOST:PORT."""
    logs.setup(os.environ.get("LOG_LEVEL", "INFO"), otel=bool(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")))
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        app = create_app(host=host)
    except (RuntimeError, ValueError) as exc:
        log.error("configuration error: %s", exc)
        raise SystemExit(1) from exc
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8101")), log_config=None)
