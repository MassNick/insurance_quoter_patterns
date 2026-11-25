"""The two agents and the turn orchestration (architecture §4.1, §8).

Per turn: system notes go into the history; the memory agent starts in the background while button actions
are pre-executed; then the main agent runs (2 model calls in all), its text streamed as ``text.delta``,
and ``memory.result`` is emitted as soon as it is ready.
"""

from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

from agent_framework import (
    Agent,
    AgentSession,
    CharacterEstimatorTokenizer,
    ContextProvider,
    HistoryProvider,
    InMemoryHistoryProvider,
    Message,
    TokenBudgetComposedStrategy,
    ToolResultCompactionStrategy,
)
from jsonschema import Draft202012Validator
from quote_common import load_schema

from quote_agent import prompts
from quote_agent.config import Settings
from quote_agent.tools import build_tools, logical_name
from quote_agent.turn import Turn, TurnRequest, current_turn, meter, tool_policy

HISTORY_BUDGET = 12_000  # tokens of history before compaction (token-budget.md)


class VolatileContext(ContextProvider):
    """STATE, PHASE, OPEN_SURFACES, SIGNALS, PREFERENCES, RULES: sent after the history, never stored in it."""

    def __init__(self) -> None:
        """Register the provider under the name ``volatile_context``."""
        super().__init__("volatile_context")

    async def before_run(self, *, agent: Any, session: AgentSession, context: Any, state: dict[str, Any]) -> None:
        """Add the turn's context (``Turn.context``) as a system message after the history: sent, never stored."""
        context.extend_messages(self, [Message(role="system", contents=[current_turn.get().context])])


def chat_clients(settings: Settings, credential: Any) -> tuple[Any, Any]:
    """The (main, memory) chat clients: the deterministic fakes for ``QUOTE_AGENT_MODEL=fake``,
    otherwise one ``FoundryChatClient`` per deployment."""
    if settings.model == "fake":
        from quote_agent.fake.client import FakeMainClient, FakeMemoryClient

        return FakeMainClient(), FakeMemoryClient()
    from agent_framework.foundry import FoundryChatClient

    return tuple(
        FoundryChatClient(project_endpoint=settings.project_endpoint, model=model, credential=credential)
        for model in (settings.main_deployment, settings.memory_deployment)
    )


def history_provider(settings: Settings, credential: Any) -> HistoryProvider:
    """The conversation history store: Cosmos DB when ``COSMOS_ENDPOINT`` is set, otherwise in memory."""
    if not settings.cosmos_endpoint:
        return InMemoryHistoryProvider()
    from agent_framework_azure_cosmos import CosmosHistoryProvider

    return CosmosHistoryProvider(
        endpoint=settings.cosmos_endpoint,
        database_name=settings.cosmos_database,
        container_name=settings.cosmos_container,
        credential=credential,
    )


class Runtime:
    """The two agents (main with tools, memory with a strict output schema), their history and the per-session
    state. ``run_turn`` serves one invocation."""

    def __init__(self, settings: Settings, *, credential: Any = None, mcp_clients: Mapping[str, Any] | None = None):
        """Build both agents from ``settings``. ``mcp_clients`` (server label → entered ``mcp.Client``)
        replaces the MCP URLs in tests."""
        self.settings = settings
        main_client, memory_client = chat_clients(settings, credential)
        self.history = history_provider(settings, credential)
        tokenizer = CharacterEstimatorTokenizer()
        self.main = Agent(
            client=main_client,
            name="main",
            instructions=prompts.instructions("main"),
            tools=build_tools(settings, credential, mcp_clients),
            middleware=[tool_policy, meter],
            context_providers=[self.history, VolatileContext()],
            default_options={"store": False, **settings.main_options},
            compaction_strategy=TokenBudgetComposedStrategy(
                token_budget=HISTORY_BUDGET + prompts.static_prefix_tokens(),
                tokenizer=tokenizer,
                strategies=[ToolResultCompactionStrategy(keep_last_tool_call_groups=2)],
            ),
            tokenizer=tokenizer,
        )
        self.memory_schema = Draft202012Validator(load_schema(settings.memory_output_schema))
        self.memory = Agent(
            client=memory_client,
            name="memory",
            instructions=prompts.instructions("memory"),
            middleware=[meter],
            default_options={
                "store": False,
                "response_format": prompts.memory_response_format(settings.memory_output_schema),
                **settings.memory_options,
            },
        )
        self.sessions: dict[str, AgentSession] = {}  # one agent session per session id
        self.locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)  # one turn at a time per session

    async def close(self) -> None:
        """Close MCP connections opened by turns (on server shutdown)."""
        for tool in self.main.mcp_tools:
            await tool.close()

    def options(self, agent: str, session_id: str) -> dict[str, Any]:
        """Chat options of one model call: the ``prompt_cache_key`` of ``agent`` (main or memory) for the session."""
        return {"prompt_cache_key": prompts.cache_key(agent, self.settings.prompt_version, session_id)}

    async def call_tool(self, t: Turn, name: str, args: dict[str, Any]) -> Any:
        """Run a model tool from code (pre-executed button actions), with the same events and injection."""
        for mcp in self.main.mcp_tools:
            if not mcp.is_connected:
                await mcp.connect()
            for fn in mcp.functions:
                if logical_name(fn.name) == name:
                    call_args = {**args, **t.injected(name)}
                    return await t.run_tool(name, args, lambda fn=fn, a=call_args: fn.invoke(arguments=a))
        msg = f"tool {name} is not available"
        raise LookupError(msg)

    async def pre_execute(self, t: Turn, req: TurnRequest) -> tuple[str, Any] | None:
        """get_quote → quoting.create_quote, modify_quote → quoting.modify_quote, before the model (§8.2 step 4)."""
        inp = req.input
        if inp.kind != "action" or inp.action_id not in ("get_quote", "modify_quote"):
            return None
        name = f"quoting.{'create' if inp.action_id == 'get_quote' else 'modify'}_quote"
        args = {} if inp.action_id == "get_quote" else {"quote_id": inp.quote_id, "changes": inp.changes or {}}
        visible = await self.call_tool(t, name, args)
        try:
            return name, json.loads(visible)
        except (TypeError, ValueError):
            return name, visible

    async def run_memory(self, t: Turn, req: TurnRequest, last_assistant: str) -> dict[str, Any] | None:
        """Run the memory agent, validate its JSON and emit it as ``memory.result`` (``guardrail`` too when off topic).

        A failure or a timeout emits ``memory.result`` with an ``error`` instead and returns None:
        the turn goes on without memory output.
        """
        try:
            response = await asyncio.wait_for(
                self.memory.run(
                    prompts.memory_input(req, last_assistant), options=self.options("memory", t.session_id)
                ),
                timeout=self.settings.memory_timeout_s,
            )
            output = json.loads(response.text)
            self.memory_schema.validate(output)
        except Exception as exc:  # §16: the turn continues without memory output
            t.emit(
                "memory.result",
                {"error": {"code": type(exc).__name__, "message": "memory agent unavailable"}},
                actor="memory_agent",
            )
            return None
        t.emit("memory.result", output, actor="memory_agent")
        if output["intent"]["off_topic"]:
            t.emit("guardrail", {"category": "off_topic", "action": "redirect"}, actor="memory_agent")
        return output

    async def run_turn(self, t: Turn, req: TurnRequest) -> None:
        """Serve one invocation: stream the main agent's text, tool events and the memory result into ``t``.

        System notes go into the history before the input; ``warmup`` makes no model call. The turns of one session
        run one at a time. ``t`` ends with ``turn.completed``, or with ``error`` when the turn fails.
        """
        memory_task: asyncio.Task[Any] | None = None
        try:
            if req.input.kind == "warmup":  # starts the sandbox; no model call, only turn.completed
                t.complete()
                return
            t.emit("turn.started", {"phase": req.phase})
            async with self.locks[t.session_id]:
                session = self.sessions.setdefault(t.session_id, AgentSession(session_id=t.session_id))
                if req.system_notes:  # history stays append-only: notes go in before the new input
                    await self.history.save_messages(
                        t.session_id,
                        [Message(role="system", contents=[f"SYSTEM NOTE: {n}"]) for n in req.system_notes],
                        state=session.state.setdefault(self.history.source_id, {}),
                    )
                memory_task = asyncio.create_task(self.run_memory(t, req, session.state.get("last_assistant", "")))
                pre = await self.pre_execute(t, req)
                t.context = prompts.context_message(req)
                text = []
                stream = self.main.run(
                    Message(role="user", contents=[prompts.main_input(req.input, pre)]),
                    session=session,
                    stream=True,
                    options=self.options("main", t.session_id),
                )
                async for update in stream:
                    for c in update.contents:
                        if c.type != "text" or not c.text:
                            continue
                        if getattr(c, "merge_mode", None) == "replace":  # output_text.done repeats the whole text
                            text = [c.text]
                        else:
                            text.append(c.text)
                            t.emit("text.delta", {"delta": c.text})
                final = "".join(text) or t.fallback_text
                if final:
                    t.emit("text.done", {"text": final, **({"citations": t.citations} if t.citations else {})})
                session.state["last_assistant"] = final
                await memory_task
            t.complete()
        except Exception as exc:
            if memory_task:
                memory_task.cancel()
            t.fail(exc)
