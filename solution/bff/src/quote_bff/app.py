"""FastAPI app: the HTTP + SSE API (interfaces §1), the internal state endpoint, problem+json, the qsid cookie."""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import Any, Literal

from fastapi import FastAPI, File, Form, Header, HTTPException, Request, Response, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from quote_common import session_ref
from quote_common.logs import setup as setup_logs
from starlette.background import BackgroundTask
from starlette.exceptions import HTTPException as StarletteHTTPException

from quote_bff import state
from quote_bff.clients import FoundryDocumentExtractor, HostedAgentClient, McpClient, entra_token
from quote_bff.config import Settings
from quote_bff.fakes import FakeA2ui, FakeDocumentExtractor, FakeHostedAgent, FakeQuoting
from quote_bff.service import Bff, public_state
from quote_bff.stores import AzureBlobStore, CosmosStore, InMemoryStore, LocalBlobStore

log = logging.getLogger(__name__)
COOKIE = "qsid"  # name of the session cookie
UPLOAD_TYPES = {"application/pdf", "image/jpeg", "image/png", "image/heic"}  # what an upload may be (FR-DOC-1)
PURPOSES = {"licence", "registration", "policy", "vin", "declaration"}  # ``policy`` is read as ``declaration``
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}  # no caching, no proxy buffering of the stream


class NewSession(BaseModel):
    """Body of ``POST /api/sessions``; it may be left out."""

    locale: str = "fr-CA"  # session language: ``en...`` selects English, anything else French


class Inbound(BaseModel):
    """One inbound event of ``POST /api/sessions/{sid}/turns`` (events.md, "Inbound").

    ``a2ui.callAgentFunction`` and uploads have their own routes.
    """

    type: Literal["user.message", "a2ui.action", "ui.surface_reopen", "confirm.response", "document.uploaded"]
    data: dict[str, Any] = Field(default_factory=dict)  # handed to the handler of ``type`` as it is


def problem(status: int, title: str, detail: str = "") -> JSONResponse:
    """A ``problem+json`` error response, ``{type, title, status, detail}``; never a stack trace."""
    return JSONResponse({"type": "about:blank", "title": title, "status": status, "detail": detail},
                        status_code=status, media_type="application/problem+json")


def cookie_value(sid: str, key: str) -> str:
    """The ``qsid`` cookie value for session ``sid``: ``<sid>.<32 hex characters of HMAC-SHA256(key, sid)>``."""
    return f"{sid}.{hmac.new(key.encode(), sid.encode(), hashlib.sha256).hexdigest()[:32]}"


def cookie_session(value: str | None, key: str) -> str | None:
    """The session id a cookie value was signed for, or None when it is missing, malformed or forged."""
    sid = (value or "").rpartition(".")[0]
    return sid if sid and hmac.compare_digest(cookie_value(sid, key), value or "") else None


def sniff(data: bytes) -> str | None:
    """The content type of an upload from its first bytes (PDF, JPEG, PNG, HEIC), or None.

    The type the client declares is never trusted (FR-DOC-1).
    """
    if data.startswith(b"%PDF"):
        return "application/pdf"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftyphevc", b"ftypmif1", b"ftypmsf1"):
        return "image/heic"
    return None


def build_bff(settings: Settings) -> Bff:
    """Pick each adapter from the environment: an empty URL or endpoint means the fake or in-memory one.

    A ``hosted_agent_url`` wins over the Foundry endpoint for the hosted agent. The document agent needs the Foundry
    endpoint; without it the fake extractor reads documents. See the README table "Adapters and how to swap them".
    """
    s = settings
    store = CosmosStore.connect(s.cosmos_endpoint, s.cosmos_database) if s.cosmos_endpoint else InMemoryStore()
    blobs = AzureBlobStore(s.blob_account_url, s.blob_container) if s.blob_account_url else LocalBlobStore(
        s.local_upload_dir)
    foundry = s.foundry_project_endpoint.rstrip("/")
    if s.hosted_agent_url:
        agent: Any = HostedAgentClient(s.hosted_agent_url)
    elif foundry:
        agent = HostedAgentClient(f"{foundry}/agents/{s.hosted_agent_name}", foundry=True,
                                  token=entra_token("https://ai.azure.com/.default"))
    else:
        agent = FakeHostedAgent()

    def mcp(url: str, scope: str, fake: Any) -> Any:
        """An McpClient for ``url`` (with Entra tokens when ``scope`` is set), or ``fake`` when no URL is set."""
        return McpClient(url, token=entra_token(scope) if scope else None) if url else fake

    documents = (FoundryDocumentExtractor(foundry, s.document_agent_name, token=entra_token(
        "https://ai.azure.com/.default")) if foundry and s.document_agent_name else FakeDocumentExtractor())
    return Bff(s, store, blobs, agent, mcp(s.a2ui_mcp_url, s.a2ui_mcp_scope, FakeA2ui()),
               mcp(s.quoting_mcp_url, s.quoting_mcp_scope, FakeQuoting()), documents)


def create_app(settings: Settings | None = None, bff: Bff | None = None) -> FastAPI:
    """The FastAPI app for one Bff.

    Tests pass their own ``settings`` and ``bff``; otherwise both come from the environment.
    """
    settings = settings or Settings()
    bff = bff or build_bff(settings)
    app = FastAPI(title="quote-bff", version="0.1.0")
    app.state.bff = bff
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origin_list, allow_credentials=True,
                       allow_methods=["GET", "POST"], allow_headers=["Content-Type", "traceparent"])

    @app.exception_handler(StarletteHTTPException)
    async def http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        """HTTP errors, the router's own 404 and 405 included, as problem+json."""
        return problem(exc.status_code, str(exc.detail))

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        """An invalid body as a 422 problem+json that names the invalid fields, never their values."""
        fields = ", ".join(".".join(str(p) for p in e["loc"]) for e in exc.errors())
        return problem(422, "Invalid request", f"invalid fields: {fields}")

    @app.exception_handler(Exception)
    async def unexpected(_: Request, exc: Exception) -> JSONResponse:  # never a stack trace
        """Anything else: log it and answer a generic 500, never a stack trace."""
        log.exception("unhandled error")
        return problem(500, "Internal error")

    async def session(sid: str, request: Request) -> dict[str, Any]:
        """Authorize a ``/sessions/{sid}`` call and load the session.

        403 unless the ``qsid`` cookie was signed for ``sid``; 404 for an unknown session.
        """
        if cookie_session(request.cookies.get(COOKIE), settings.session_cookie_key) != sid:
            raise HTTPException(403, "This session belongs to another browser")
        try:
            return await bff.load(sid)
        except KeyError:
            raise HTTPException(404, "Unknown session") from None

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        """Liveness probe."""
        return {"status": "ok"}

    @app.get("/api/health/sse")
    async def health_sse() -> StreamingResponse:
        """Liveness of the SSE path: one ``health`` event, then the stream closes."""
        async def one() -> Any:
            """The single health event, already SSE-framed."""
            yield b'data: {"type":"health"}\n\n'

        return StreamingResponse(one(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/api/sessions", status_code=201)
    async def create_session(response: Response, body: NewSession | None = None) -> JSONResponse:
        """Create a session (interfaces §1).

        Answers 201 with the session id, the quote number and the greeting events, sets the ``qsid`` cookie and warms
        the hosted agent up in the background.
        """
        doc, events = await bff.create_session((body or NewSession()).locale)
        out = JSONResponse({"session_id": doc["session_id"], "quote_number": doc["quote_number"], "events": events},
                           status_code=201, background=BackgroundTask(bff.warmup, doc["session_id"]))
        out.set_cookie(COOKIE, cookie_value(doc["session_id"], settings.session_cookie_key), httponly=True,
                       secure=settings.cookie_secure, samesite="strict", path="/api")
        return out

    @app.post("/api/sessions/{sid}/turns")
    async def turns(sid: str, inbound: Inbound, request: Request) -> StreamingResponse:
        """Take one inbound event and stream the outbound events as SSE (interfaces §1).

        422 when a message is longer than ``max_message_chars``.
        """
        await session(sid, request)
        if inbound.type == "user.message" and len(str(inbound.data.get("text") or "")) > settings.max_message_chars:
            raise HTTPException(422, "Message too long")
        t = await bff.open_turn(sid)
        bff.start(t, inbound.model_dump())
        return StreamingResponse(t.stream(), media_type="text/event-stream", headers=SSE_HEADERS)

    @app.post("/api/sessions/{sid}/a2ui/functions")
    async def functions(sid: str, message: dict[str, Any], request: Request) -> dict[str, Any]:
        """An A2UI ``callAgentFunction`` from the renderer, answered with an ``agentFunctionResponse``.

        The function is a lookup or a document read. No model call. 422 for an unknown function.
        """
        await session(sid, request)
        try:
            return await bff.agent_function(sid, message)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from None

    @app.post("/api/sessions/{sid}/documents", status_code=201)
    async def documents(sid: str, request: Request, file: UploadFile = File(...),  # noqa: B008
                        purpose: str = Form(...), surface_id: str | None = Form(None),
                        field_path: str | None = Form(None)) -> dict[str, str]:
        """Store an upload after checking its purpose (422), size (413) and type (415); return its id.

        The type is read from the bytes. The size limit and the allowed types are FR-DOC-1.
        """
        await session(sid, request)
        if purpose not in PURPOSES:
            raise HTTPException(422, f"Unknown purpose {purpose}")
        data = await file.read(settings.max_upload_bytes + 1)
        if len(data) > settings.max_upload_bytes:
            raise HTTPException(413, "File larger than 10 MB")
        kind = sniff(data)
        if kind not in UPLOAD_TYPES or (file.content_type or kind) != kind:
            raise HTTPException(415, "PDF, JPEG, PNG or HEIC only")
        return {"document_id": await bff.upload(sid, data, kind, purpose, surface_id, field_path)}

    @app.get("/api/sessions/{sid}/state")
    async def get_state(sid: str, request: Request) -> dict[str, Any]:
        """What the web shows: state, preferences, signals, advisor signals, quote number and one chip per form."""
        return public_state(await session(sid, request))

    @app.get("/api/sessions/{sid}/quotes")
    async def quotes(sid: str, request: Request) -> dict[str, Any]:
        """The stored quote versions, oldest first.

        Each carries the monthly delta against the previous version (FR-QUO-1, FR-QUO-2).
        """
        await session(sid, request)
        rows = await bff.store.query("quotes", session_id=sid)
        keys = ("quote_id", "version", "offers", "reason", "created_at", "previous_version", "delta_monthly")
        return {"quotes": [{k: r.get(k) for k in keys} for r in sorted(rows, key=lambda r: (r["created_at"],
                                                                                           r["version"]))]}

    @app.get("/internal/sessions/{sid}/state")
    async def internal_state(sid: str, authorization: str = Header("")) -> dict[str, Any]:
        """mcp-quoting's StateReader (local): ``Authorization: SessionRef <token>`` signed for this session.

        401 for a missing, forged, expired or foreign ref; 404 for an unknown session. ``quote_blocked`` is true while a
        STOP stands or the rules failed, so mcp-quoting refuses to price.
        """
        scheme, _, token = authorization.partition(" ")
        try:
            owner = session_ref.verify(token, settings.session_ref_key.encode()) if scheme == "SessionRef" else None
        except session_ref.InvalidSessionRef:
            owner = None
        if owner != sid:
            raise HTTPException(401, "Invalid session_ref")
        try:
            doc = await bff.load(sid)
        except KeyError:
            raise HTTPException(404, "Unknown session") from None
        return {"state": state.state_of(doc), "preferences": doc["session_preferences"],
                "quote_blocked": bool(doc["stop"] or doc["rules_error"])}  # quote_blocked: additive (STOP, rules error)

    return app


def run() -> None:
    """Entry point of the ``quote-bff`` command.

    Sets up logging (and Azure Monitor when ``otel_enabled``) and serves the app with uvicorn on ``PORT`` (8000).
    """
    import uvicorn

    settings = Settings()
    setup_logs(settings.log_level, otel=settings.otel_enabled)
    uvicorn.run(create_app(settings), host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))  # noqa: S104 - container port
