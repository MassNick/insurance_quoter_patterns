"""Foundry invocations host: ``POST /invocations`` (SSE of envelopes) and ``GET /readiness`` on port 8088."""

from __future__ import annotations

import asyncio
from typing import Any

from azure.ai.agentserver.invocations import InvocationAgentServerHost
from pydantic import ValidationError
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse

from quote_agent.agent import Runtime
from quote_agent.config import Settings
from quote_agent.turn import Turn, TurnRequest, current_turn


def create_app(runtime: Runtime | None = None, **host_options: Any) -> InvocationAgentServerHost:
    """Build the invocations host with its ``POST /invocations`` handler.

    ``runtime`` is injected by tests; otherwise it is built from the environment (validated, with an Entra
    credential only when the models, the toolbox or Cosmos need one). ``host_options`` go to the host.
    The runtime stays on ``app.state.runtime`` and is closed on shutdown.
    """
    if runtime is None:
        settings = Settings.from_env()
        settings.validate()
        credential = None
        if settings.model == "azure" or settings.toolbox_mcp_url or settings.cosmos_endpoint:
            from azure.identity.aio import DefaultAzureCredential  # the agent identity in Foundry

            credential = DefaultAzureCredential()
        runtime = Runtime(settings, credential=credential)
    app = InvocationAgentServerHost(**host_options)
    tasks: set[asyncio.Task[None]] = set()  # strong references: a bare task can be collected before it ends

    @app.invoke_handler
    async def invoke(request: Request) -> Response:
        """``POST /invocations``: validate the body (400 on a bad one), start the turn in the background
        and stream its envelopes as SSE."""
        try:
            req = TurnRequest.model_validate(await request.json())
        except (ValidationError, ValueError) as exc:
            return JSONResponse({"error": "invalid_request", "detail": str(exc)[:500]}, status_code=400)
        session_id = request.headers.get("x-client-session-id") or request.state.session_id
        turn = Turn(session_id, req.turn, req.session_ref, req.state)
        token = current_turn.set(turn)
        task = asyncio.create_task(runtime.run_turn(turn, req))  # copies the context: middleware finds the turn
        current_turn.reset(token)
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return StreamingResponse(turn.sse(), media_type="text/event-stream", headers={"Cache-Control": "no-cache"})

    app.shutdown_handler(runtime.close)
    app.state.runtime = runtime
    return app
