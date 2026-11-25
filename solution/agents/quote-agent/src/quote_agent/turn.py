"""One invocation: the request body, the event stream, and the two middlewares that enforce the call budget.

* ``Turn.emit`` is the only place an envelope is built (contracts/events.md); ``Turn.run_tool`` maps every
  tool result to its events and returns what the model may see.
* ``tool_policy`` (function middleware): injected args (tools.json), terminal vs narrate (architecture §8.2).
* ``meter`` (chat middleware): counts model calls, usage and the tool-call batch, and hides session_ref
  (and the other injected args) from the tool schemas the model sees.
"""

from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextvars import ContextVar
from typing import Any, Literal

from agent_framework import (
    ChatContext,
    ChatResponse,
    FunctionInvocationContext,
    FunctionTool,
    MiddlewareTermination,
    chat_middleware,
    function_middleware,
)
from agent_framework.exceptions import ToolExecutionException
from pydantic import AliasChoices, BaseModel, ConfigDict, Field
from quote_common import Envelope, sse_encode
from quote_common.logs import redact

from quote_agent.tools import RESULT_MODE, injected_args, logical_name

# quote tool -> the event its result produces
log = logging.getLogger("quote_agent")
BATCH_WAIT_S = 30.0  # longest a finished call waits for the rest of its batch
QUOTE_EVENTS = {"quoting.create_quote": "quote.created", "quoting.modify_quote": "quote.updated"}


class TurnInput(BaseModel):
    """The customer's input as the BFF sends it; ``kind`` says which fields are set.

    message: ``text``. form_submitted: ``surface_id`` and ``changed_paths``. action: ``action_id``
    (``get_quote``, ``modify_quote`` with ``quote_id`` and ``changes``, ``rule_correct``).
    document_extracted: ``purpose`` and ``changed_paths``. confirmation_result: ``accepted`` and
    ``changed_paths`` (``paths`` is accepted too). warmup: nothing else, and no model call.
    Extra keys are tolerated.
    """

    model_config = ConfigDict(extra="allow")
    kind: Literal["message", "form_submitted", "action", "document_extracted", "confirmation_result", "warmup"]
    text: str = ""
    surface_id: str | None = None
    action_id: str | None = None
    purpose: str | None = None
    accepted: bool | None = None
    changed_paths: list[str] = Field(default_factory=list, validation_alias=AliasChoices("changed_paths", "paths"))
    quote_id: str | None = None
    changes: dict[str, Any] | None = None


class TurnRequest(BaseModel):
    """BFF → hosted agent body (contracts/events.md)."""

    turn: int  # turn number within the session
    input: TurnInput
    state: dict[str, Any] = Field(default_factory=dict)  # committed state; each field is {value, source, status}
    phase: str = "intent"  # intent | gather | advise
    open_surfaces: list[str] = Field(default_factory=list)  # ids of the forms open in the UI (OPEN_SURFACES)
    signals: list[Any] = Field(default_factory=list)  # what the memory agent concluded earlier (SIGNALS)
    preferences: dict[str, Any] = Field(default_factory=dict)  # session preferences such as answer_mode (PREFERENCES)
    rule_hits: list[Any] = Field(default_factory=list)  # end-of-form rule results still active (RULES)
    system_notes: list[str] = Field(default_factory=list)  # between-turn events; go into the history before the input
    session_ref: str = ""  # signed session token for the tools; never shown to the model


def _json(text: str) -> Any:
    """``text`` parsed as JSON, or None when it is not JSON."""
    try:
        return json.loads(text)
    except (TypeError, ValueError):
        return None


def _failed(result: Any) -> bool:
    """True when a tool result is an error body (``{"error": ...}``): a server refusal or a failed call."""
    data = _json(result) if isinstance(result, str) else None
    return isinstance(data, dict) and "error" in data


def _citations(node: Any) -> list[dict[str, Any]]:
    """Passages with a title and a reference anywhere in a knowledge-base result."""
    if isinstance(node, list):
        return [c for item in node for c in _citations(item)]
    if not isinstance(node, dict):
        return []
    if "title" in node and ({"url", "ref_id", "id"} & node.keys()):
        return [{k: node[k] for k in ("id", "ref_id", "title", "url") if k in node}]
    return _citations(list(node.values()))


class Turn:
    """One invocation: the envelopes queued for the SSE response, the counters behind ``turn.completed`` and
    the state the two middlewares share. The invoke handler creates it; middlewares reach it by ``current_turn``."""

    def __init__(self, session_id: str, turn: int, session_ref: str = "", state: dict[str, Any] | None = None) -> None:
        """``state`` is the committed state the BFF sent; ``session_ref`` goes to the tools, never to the model."""
        self.session_id, self.turn, self.session_ref, self.state = session_id, turn, session_ref, state or {}
        self.last_quote: dict[str, Any] = {}  # the turn's last create/modify_quote result (render_offers input)
        self.started = time.monotonic()
        self.queue: asyncio.Queue[Envelope | None] = asyncio.Queue()  # envelopes to stream; None ends the stream
        self.seq = 0  # last envelope number: monotonic within the turn
        self.ttft_ms: int | None = None
        self.model_calls = 0
        self.usage = {"input": 0, "cached_input": 0, "output": 0}
        self.batch: set[str] = set()  # logical names of the tool calls in the latest main completion
        self.batch_no = 0  # main completions so far
        self.narrate_batch: int | None = None  # batch of the narrate round (a later narrate call is refused)
        self.batch_left = 0  # calls of the latest batch still running: the batch decides together once all finish
        self.batch_failed = False  # a call of the latest batch failed (a refusal or an error)
        self.batch_settled = asyncio.Event()  # set when every call of the latest batch has finished
        self.retry_batch: int | None = None  # the batch that used the turn's one corrective completion
        self.citations: list[dict[str, Any]] = []  # knowledge base passages cited this turn
        self.fallback_text = ""  # what to say when the model only called tools (from a form title)
        self.context = ""  # the volatile context message for the main agent (prompts.context_message)

    def _ms(self) -> int:
        """Milliseconds since the turn started."""
        return int((time.monotonic() - self.started) * 1000)

    # ---------------------------------------------------------------- the only envelope constructor
    def emit(
        self, type_: str, data: dict[str, Any], actor: str = "main_agent", form_spec: dict[str, Any] | None = None
    ) -> None:
        """Queue one envelope for the stream: the only place an envelope is built (contracts/events.md).

        The first ``text.delta`` fixes ``ttft_ms``. ``form_spec`` rides on an ``a2ui.message`` for the BFF,
        which strips it.
        """
        if type_ == "text.delta" and self.ttft_ms is None:
            self.ttft_ms = self._ms()
        self.seq += 1
        self.queue.put_nowait(
            Envelope(
                type=type_,
                session_id=self.session_id,
                turn=self.turn,
                seq=self.seq,
                actor=actor,
                data=data,
                form_spec=form_spec,
            )
        )

    def complete(self) -> None:
        """Emit ``turn.completed`` (model calls, token usage, time to first token, duration) and end the stream."""
        self.emit(
            "turn.completed",
            {"model_calls": self.model_calls, "usage": self.usage, "ttft_ms": self.ttft_ms, "duration_ms": self._ms()},
        )
        self.queue.put_nowait(None)

    def fail(self, exc: BaseException) -> None:
        """Emit a retryable ``error`` that carries only the exception class name, then end the stream."""
        self.emit("error", {"code": type(exc).__name__, "message": "The turn failed. Please retry.", "retryable": True})
        self.queue.put_nowait(None)

    async def sse(self) -> AsyncIterator[bytes]:
        """The queued envelopes as SSE bytes until the turn ends; ``form_spec`` is kept for the BFF."""
        while (env := await self.queue.get()) is not None:
            yield sse_encode(env.model_dump(exclude_none=True))  # keeps form_spec for the BFF

    # ---------------------------------------------------------------- model calls
    def record(self, response: ChatResponse, *, main: bool) -> ChatResponse:
        """Add a completion's token usage to the turn; for a main-agent completion also remember which tools it
        called (``batch``). Returns the response unchanged."""
        u = response.usage_details or {}
        self.usage["input"] += u.get("input_token_count") or 0
        self.usage["cached_input"] += u.get("cache_read_input_token_count") or 0
        self.usage["output"] += u.get("output_token_count") or 0
        if main:
            self.batch_no += 1
            calls = [c for m in response.messages for c in m.contents if c.type == "function_call"]
            self.batch = {logical_name(c.name or "") for c in calls}
            self.batch_left, self.batch_failed, self.batch_settled = len(calls), False, asyncio.Event()
        return response

    async def settle(self, *, failed: bool) -> bool:
        """Count one finished call of the batch and wait for the others; returns True when the turn ends here.

        The batch decides together because its calls run in parallel and a fast one (``ui.suggest_replies``) would
        otherwise end the turn before a slow one fails. The turn goes on when the batch holds a narrate tool, or when
        a call failed and the turn has not used its one corrective completion: the model then sees the error (for
        example an invalid FormSpec) and fixes its call. A second failure ends the turn as usual.
        """
        self.batch_failed |= failed
        self.batch_left -= 1
        if self.batch_left <= 0:
            if self.batch_failed and self.retry_batch is None:
                self.retry_batch = self.batch_no
            self.batch_settled.set()
        else:
            with contextlib.suppress(TimeoutError):  # a hung call must not hold the others forever
                await asyncio.wait_for(self.batch_settled.wait(), BATCH_WAIT_S)
        narrate = any(RESULT_MODE.get(n) == "narrate" for n in self.batch)
        return not narrate and self.retry_batch != self.batch_no

    # ---------------------------------------------------------------- tools
    def injected(self, name: str) -> dict[str, Any]:
        """Values for the tool's injected_args (tools.json) that this turn can provide."""
        values = {"session_ref": self.session_ref, "state": self.state}
        values |= {k: self.last_quote[k] for k in ("version", "offers") if k in self.last_quote}
        return {k: values[k] for k in injected_args(name) if k in values}

    async def run_tool(self, name: str, args: dict[str, Any], invoke: Callable[[], Awaitable[Any]]) -> Any:
        """Run a tool call through ``invoke``: emit ``tool.call``, the events of its result, then ``tool.result``.

        Returns what the model may see (``_map_result``). A failed call returns the tool's JSON refusal body, so
        the model can narrate it, or ``{"error": "tool_failed"}``.
        """
        mode = RESULT_MODE.get(name, "terminal")
        actor = f"tool:{name}"
        self.emit("tool.call", {"name": name, "args_redacted": redact(args), "result_mode": mode}, actor)
        start = time.monotonic()
        try:
            result = await invoke()
        except Exception as exc:
            # An MCP isError result (for example a create_quote refusal with missing_fields) arrives as a
            # ToolExecutionException carrying the tool's JSON body: the model sees that body and narrates it.
            body = _json(str(exc)) if isinstance(exc, ToolExecutionException) else None
            refusal = isinstance(body, dict)
            # The event carries only a code; the reason (a server refusal such as an invalid FormSpec, or the client's
            # own validation error) goes to the logs, redacted, so a failed call can be traced in Application Insights.
            log.warning("tool %s failed: %s: %s", name, type(exc).__name__, redact(str(exc))[:500])
            self.emit(
                "tool.result",
                {
                    "name": name,
                    "ms": int((time.monotonic() - start) * 1000),
                    "result_mode": mode,
                    "error": body.get("error", "tool_error") if refusal else type(exc).__name__,
                },
                actor,
            )
            return str(exc) if refusal else json.dumps({"error": "tool_failed"})
        visible, extra = self._map_result(name, args, result, actor)
        self.emit(
            "tool.result",
            {"name": name, "ms": int((time.monotonic() - start) * 1000), "result_mode": mode, **extra},
            actor,
        )
        return visible

    def _map_result(self, name: str, args: dict[str, Any], result: Any, actor: str) -> tuple[Any, dict[str, Any]]:
        """Emit the events a tool result produces; return (model-visible result, extra tool.result fields)."""
        contents = [c for c in result if c.type == "text"] if isinstance(result, list) else []
        is_user = ["user" in ((c.additional_properties or {}).get("audience") or []) for c in contents]
        for_user = [c for c, u in zip(contents, is_user, strict=True) if u]
        visible = "\n".join(c.text for c, u in zip(contents, is_user, strict=True) if not u)
        visible = visible or (result if isinstance(result, str) else "")
        if name == "ui.suggest_replies":
            self.emit("suggestions", {"items": args.get("items", [])})
            return "ok", {}
        if name == "ui.set_phase":
            self.emit("phase", {"phase": args.get("phase")})
            return "ok", {}
        if name.startswith("a2ui."):
            spec = args.get("form_spec") if name == "a2ui.render_form" else None
            for content in for_user:
                for message in _json(content.text) or []:
                    with_spec = spec if spec and "createSurface" in message else None
                    self.emit("a2ui.message", message, actor, form_spec=with_spec)
            if spec and spec.get("title"):
                self.fallback_text = f"Voici un court formulaire : {spec['title']}."
            return visible, {}
        data = _json(visible)
        if name in QUOTE_EVENTS and isinstance(data, dict) and data.get("quote_id") and not data.get("missing_fields"):
            keys = ("quote_id", "version", "offers", "previous_version", "delta_monthly", "input_hash")
            self.emit(QUOTE_EVENTS[name], {k: data[k] for k in keys if k in data}, actor)
            self.last_quote = data
        if name == "knowledge.knowledge_base_retrieve":
            self.citations += _citations(data)
        if RESULT_MODE.get(name) == "terminal":  # lookups: the result goes to the UI, the model gets a stub
            return json.dumps({"status": "sent_to_ui"}), {"result": data if data is not None else visible}
        return visible, {}


current_turn: ContextVar[Turn] = ContextVar("current_turn")


@function_middleware
async def tool_policy(ctx: FunctionInvocationContext, call_next: Callable[[], Awaitable[None]]) -> None:
    """Terminal tools end the loop; one narrate round per turn; a failed call gets one corrective completion per
    turn (``Turn.settle``); injected args (session_ref, state, quote version and offers) are added here and override
    anything the model sent."""
    t = current_turn.get()
    name = logical_name(ctx.function.name)
    narrate = RESULT_MODE.get(name) == "narrate"
    if narrate and t.narrate_batch not in (None, t.batch_no):
        ctx.result = json.dumps({"error": "only one narrate tool round per turn"})
        await t.settle(failed=False)  # counted, so the other calls of the batch do not wait for it
        raise MiddlewareTermination
    hidden = injected_args(name)
    model_args = {k: v for k, v in ctx.arguments.items() if k not in hidden}
    ctx.arguments.update(t.injected(name))

    async def invoke() -> Any:
        """Run the real tool call and return its result."""
        await call_next()
        return ctx.result

    ctx.result = await t.run_tool(name, model_args, invoke)
    if narrate:
        t.narrate_batch = t.batch_no
    if await t.settle(failed=_failed(ctx.result)):
        raise MiddlewareTermination  # no follow-up completion


def hide_injected(tool: Any) -> None:
    """Remove injected args from the schema the model sees (the server still declares them, so they are forwarded).

    Works on a deep copy given to this tool object only. Mutating the cached schema in place broke on Azure: the
    toolbox rebuilds its tools while sharing the nested ``properties`` dict, so a fresh tool lost the injected
    properties but kept ``additionalProperties: false`` and rejected them ("Unexpected argument(s) ... session_ref,
    state"). A rebuilt tool now keeps the full schema, which accepts the injected args; this one accepts them too.
    """
    hidden = injected_args(logical_name(tool.name)) if isinstance(tool, FunctionTool) else ()
    if not hidden or getattr(tool, "_injected_hidden", False):
        return
    schema = copy.deepcopy(tool.parameters())
    for name in hidden:
        schema.get("properties", {}).pop(name, None)
    if "required" in schema:
        schema["required"] = [r for r in schema["required"] if r not in hidden]
    schema.pop("additionalProperties", None)  # the hidden args come back at call time; the server still validates
    # A fresh copy per call: the OpenAI client makes function schemas strict in place (additionalProperties false),
    # which would otherwise flow back into the schema used to validate the call.
    tool.parameters = lambda: copy.deepcopy(schema)
    tool._injected_hidden = True


@chat_middleware
async def meter(ctx: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
    """Count the model call and its usage, record a main completion's tool-call batch and hide the injected
    args from the tool schemas."""
    t = current_turn.get()
    t.model_calls += 1
    tools = (ctx.options or {}).get("tools") or []
    for tool in tools:
        hide_injected(tool)
    if ctx.stream:
        ctx.stream_result_transforms.append(lambda r: t.record(r, main=bool(tools)))
    await call_next()
    if not ctx.stream and isinstance(ctx.result, ChatResponse):
        t.record(ctx.result, main=bool(tools))
