"""mcp-a2ui: the MCP server, with the four A2UI tools, over stateless streamable HTTP at ``/mcp`` (JSON responses) and
``GET /healthz``. Built like mcp-quoting: names, descriptions and input schemas come from contracts/tools.json, arguments
are validated with jsonschema, and failures are tool results with ``isError`` rather than protocol errors.

The server is stateless: a result depends only on the call's arguments. ``session_ref`` is accepted because the hosted
agent injects it into every call, and is neither read nor returned. A result has two parts: a short JSON summary for the
model, and the A2UI messages in an embedded resource for the user only (``audience: ["user"]``), which keeps them out of
the model's context.
"""

import copy
import json
import logging
import os
import re
import traceback
from collections.abc import Callable
from typing import Any

import uvicorn
from jsonschema import Draft202012Validator
from mcp.server.lowlevel import Server
from mcp.types import (
    Annotations,
    CallToolResult,
    EmbeddedResource,
    ListToolsResult,
    TextContent,
    TextResourceContents,
    Tool,
)
from quote_common import logs
from quote_common.auth import ASGIApp, wrap_from_env
from quote_common.contracts import load_tools
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from mcp_a2ui import __version__
from mcp_a2ui.compiler import FORM_SPEC_SCHEMA, compile, message, schema_problems, validate

log = logging.getLogger("mcp_a2ui")
MIME = "application/a2ui+json"  # the media type of the embedded resource that carries the A2UI messages
_OFFERS_TEXT = {  # (title, field label, button label)
    "fr": ("Votre soumission\u00a0: {n} options", "Choisissez une offre", "Choisir cette offre"),
    "en": ("Your quote: {n} options", "Pick an offer", "Select this offer"),
}
# What a caller adds before the MCP call (``injected_args`` in tools.json). Declared as optional properties so that MCP
# clients forward them; the hosted agent hides them from the model.
INJECTED = {
    "state": {"type": "object"},
    "mode": {"enum": ["new", "edit"]},
    "version": {"type": "integer"},
    "offers": {"type": "array", "items": {"type": "object"}},
    "session_ref": {"type": "string"},
}


class Refused(Exception):
    """A refusal the caller can act on. It reaches the caller as ``{"error": code, "message": ...}``."""

    def __init__(self, code: str, reason: str) -> None:
        """``code`` is the machine-readable error and ``reason`` the explanation."""
        super().__init__(reason)
        self.code = code


def _lang() -> str:
    """Language of the customer UI (env ``A2UI_LANG``, ``fr`` or ``en``; fr-CA by default)."""
    return os.environ.get("A2UI_LANG", "fr")[:2]


# --------------------------------------------------------------------------- the four tools
# A handler takes the schema-validated arguments and returns (surface id, status, A2UI messages).


def render_form(args: dict[str, Any]) -> tuple[str, str, list[dict]]:
    """Open a form: one ``createSurface`` with components and data model, prefilled from ``state`` when given."""
    if problems := validate(args["form_spec"]):
        raise Refused("invalid_arguments", "Invalid FormSpec: " + "; ".join(problems))
    messages = compile(args["form_spec"], args.get("state"), args.get("mode", "new"), _lang())
    return messages[0]["createSurface"]["surfaceId"], "shown", messages


def update_form(args: dict[str, Any]) -> tuple[str, str, list[dict]]:
    """Change the data model of an open form: ``set`` values, and ``errors`` (shown at ``/_errors``). To add or remove
    fields the caller renders the form again."""
    sid = args["surface_id"]
    messages = [message("updateDataModel", surfaceId=sid, path=p, value=v) for p, v in args.get("set", {}).items()]
    if "errors" in args:
        messages.append(message("updateDataModel", surfaceId=sid, path="/_errors", value=args["errors"]))
    if not messages:
        raise Refused("invalid_arguments", "update_form needs set or errors")
    return sid, "updated", messages


def close_form(args: dict[str, Any]) -> tuple[str, str, list[dict]]:
    """Close a form without a submit."""
    return args["surface_id"], "closed", [message("deleteSurface", surfaceId=args["surface_id"])]


def render_offers(args: dict[str, Any]) -> tuple[str, str, list[dict]]:
    """Open the offer cards of one quote version: a FormSpec with one ``offer_cards`` field, like any other form."""
    if "version" not in args or "offers" not in args:
        # both are optional in the schema so that clients forward them; the hosted agent adds them from the last quote
        raise Refused("invalid_arguments", "version and offers are required: the hosted agent adds them")
    title, label, button = _OFFERS_TEXT.get(_lang(), _OFFERS_TEXT["fr"])
    data = {"offers": args["offers"], "recommended": args.get("highlight"), "show_adjust": args.get("show_adjust")}
    spec = {
        "form_id": re.sub(r"[^a-z0-9_]", "_", f"offers_{args['quote_id']}_v{args['version']}".lower()),
        "title": title.format(n=len(args["offers"])),
        "layout": "single",
        "entity": {"type": "underwriting"},
        "actions": [{"id": "select", "label": button, "style": "primary"}],
        "fields": [{"key": "offer_type", "widget": "offer_cards", "label": label, "data": data}],
    }
    return render_form({"form_spec": spec})


# Tool name -> handler. The server refuses to start unless these are exactly the tools of tools.json.
HANDLERS: dict[str, Callable[[dict[str, Any]], tuple[str, str, list[dict]]]] = {
    fn.__name__: fn for fn in (render_form, update_form, close_form, render_offers)
}


# --------------------------------------------------------------------------- tool list and results
def tool_specs() -> dict[str, tuple[str, dict[str, Any]]]:
    """``{name: (description, inputSchema)}`` of the ``a2ui`` server in tools.json, plus the injected arguments as optional
    properties. ``form_spec`` carries the FormSpec schema (and its ``$defs``): the model learns the format from it."""
    specs = {}
    for tool in load_tools()["tools"]:
        if tool["server"] == "a2ui":
            schema = copy.deepcopy(tool["inputSchema"])
            properties = schema["properties"]
            properties.update({name: INJECTED[name] for name in ("session_ref", *tool.get("injected_args", ()))})
            if "form_spec" in properties:  # {"$ref": "form-spec.schema.json"} in tools.json
                form_spec = {k: v for k, v in FORM_SPEC_SCHEMA.items() if k not in ("$schema", "$id", "$defs")}
                properties["form_spec"], schema["$defs"] = form_spec, FORM_SPEC_SCHEMA["$defs"]
            specs[tool["name"].split(".", 1)[1]] = (tool["description"], schema)
    return specs


def _success(surface_id: str, status: str, messages: list[dict]) -> CallToolResult:
    """The result of every tool: a summary for the model, and the messages for the user only."""
    summary = TextContent(type="text", text=json.dumps({"surface_id": surface_id, "status": status}))
    payload = json.dumps(messages, ensure_ascii=False, separators=(",", ":"))
    surface = TextResourceContents(uri=f"a2ui://surfaces/{surface_id}", mime_type=MIME, text=payload)
    resource = EmbeddedResource(type="resource", annotations=Annotations(audience=["user"]), resource=surface)
    return CallToolResult(content=[summary, resource])


def _failure(code: str, reason: str) -> CallToolResult:
    """An error result: ``{"error": code, "message": reason}`` as text, with ``isError`` set and no resource."""
    text = json.dumps({"error": code, "message": reason}, ensure_ascii=False, separators=(",", ":"))
    return CallToolResult(content=[TextContent(type="text", text=text)], is_error=True)


def run_tool(validators: dict[str, Draft202012Validator], name: str, args: dict[str, Any]) -> CallToolResult:
    """Validate and run one call. Failures become error results; nothing from the arguments is logged."""
    try:
        if name not in HANDLERS:
            raise Refused("unknown_tool", f"No tool named {name}")
        if problems := schema_problems(validators[name], args):
            raise Refused("invalid_arguments", "; ".join(problems))
        return _success(*HANDLERS[name](args))
    except Refused as refusal:
        return _failure(refusal.code, str(refusal))
    except Exception as exc:  # the message may carry customer data: log the type and the code frames only
        extra = {"frames": traceback.format_tb(exc.__traceback__)}
        log.error("tool %s crashed with %s", name, type(exc).__name__, extra=extra)
        return _failure("internal_error", "The tool failed unexpectedly")


# --------------------------------------------------------------------------- server and app
def build_server() -> Server:
    """The MCP server for the tools of tools.json, each run by its handler in ``HANDLERS`` (see ``run_tool``).
    Raises ``RuntimeError`` when the contract and the handlers do not list the same tools."""
    specs = tool_specs()
    if set(specs) != set(HANDLERS):
        raise RuntimeError(f"tools.json and the handlers disagree on: {sorted(set(specs) ^ set(HANDLERS))}")
    validators = {name: Draft202012Validator(schema) for name, (_, schema) in specs.items()}

    async def list_tools(ctx: Any, params: Any) -> ListToolsResult:
        """``tools/list``: every tool of the contract, with its description and input schema."""
        return ListToolsResult(tools=[Tool(name=n, description=d, input_schema=s) for n, (d, s) in specs.items()])

    async def call_tool(ctx: Any, params: Any) -> CallToolResult:
        """``tools/call``: run the named tool with its arguments."""
        return run_tool(validators, params.name, params.arguments or {})

    return Server("mcp-a2ui", version=__version__, on_list_tools=list_tools, on_call_tool=call_tool)


def create_app(host: str = "0.0.0.0") -> ASGIApp:
    """The ASGI app: MCP over stateless streamable HTTP with JSON responses at ``/mcp``, and ``GET /healthz``. ``host`` is
    the address the app is served on (the SDK checks Host headers when it is a loopback address). With ``ENTRA_AUDIENCE``
    set the app is behind the shared Entra check (NFR-SEC-3); ``/healthz`` stays open."""

    async def healthz(request: Request) -> JSONResponse:
        """Liveness probe: ``{"status": "ok"}``."""
        return JSONResponse({"status": "ok"})

    app = build_server().streamable_http_app(
        stateless_http=True, json_response=True, host=host, custom_starlette_routes=[Route("/healthz", healthz)]
    )
    return wrap_from_env(app, default_roles={"Tools.Invoke"})


def main() -> None:
    """``python -m mcp_a2ui``: shared JSON logs (and Application Insights when configured), then serve on HOST:PORT. A
    configuration error (for example ``ENTRA_AUDIENCE`` without ``ENTRA_TENANT_ID``) is logged and exits with code 1."""
    logs.setup(os.environ.get("LOG_LEVEL", "INFO"), otel=bool(os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")))
    host = os.environ.get("HOST", "0.0.0.0")
    try:
        app = create_app(host)
    except (RuntimeError, ValueError) as exc:
        log.error("configuration error: %s", exc)
        raise SystemExit(1) from exc
    uvicorn.run(app, host=host, port=int(os.environ.get("PORT", "8102")), log_config=None)
