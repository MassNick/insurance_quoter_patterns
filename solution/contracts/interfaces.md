# Component interfaces

This file pins the boundaries between the solution's components. `events.md` (the event envelope and the merge rules), `state-catalog.json`, `form-spec.schema.json`, `memory-output.schema.json`, `document-output.schema.json` and `tools.json` are the other contracts. When this file and the design docs disagree, this file wins for the code.

Local ports (dev and end-to-end tests):

| Component | Port | Health |
|---|---|---|
| `web` (Vite dev / preview) | 5173 | `/` |
| `bff` | 8000 | `GET /api/health` |
| `quote-agent` (hosted agent, invocations) | 8088 | `GET /readiness` |
| `mcp-quoting` | 8101 | `GET /healthz`, MCP at `/mcp` |
| `mcp-a2ui` | 8102 | `GET /healthz`, MCP at `/mcp` |

All services read their configuration from environment variables only. No secret is ever committed: local and test values are generated or are obviously fake (`dev-only-...`).

---

## 1. Frontend ⇄ BFF (HTTP + SSE)

Base path `/api`. JSON bodies are UTF-8. Every streamed event is one envelope (`events.md`): `{ type, session_id, turn, seq, ts, actor, data }`.

SSE framing: each event is sent as `data: <envelope JSON>\n\n`. The server may send `: keepalive\n\n` comments. A stream always ends after `turn.completed`, `error`, or the last BFF-only event; the server then closes it.

| Method and path | Body | Response |
|---|---|---|
| `POST /api/sessions` | `{ "locale": "fr-CA" }` (optional) | `201 { session_id, quote_number, events: [envelope...] }`. The events are the BFF greeting (`text.done`, `actor: "bff"`) and its `suggestions`. The BFF also sends the hosted agent a `warmup` input in the background. |
| `POST /api/sessions/{sid}/turns` | One inbound envelope without server fields: `{ "type": "user.message" \| "a2ui.action" \| "ui.surface_reopen" \| "confirm.response" \| "document.uploaded", "data": {...} }` | `200 text/event-stream` of outbound envelopes. BFF-only inbound events (see `events.md`) stream only BFF events and close, with no `turn.started`. |
| `POST /api/sessions/{sid}/a2ui/functions` | An A2UI 1.0 `callAgentFunction` message: `{ "version": "1.0", "callAgentFunction": { surfaceId, functionCallId, callFunction: { "@call", catalogId, args } } }` | `200` an A2UI 1.0 `agentFunctionResponse` message: `{ "version": "1.0", "agentFunctionResponse": { functionCallId, value } }`. Functions: `lookupMakeModelYear`, `identifyVehicleType`, `decodeVin`, `identifyAddress`, `extractDocument` (`args: { document_id, purpose }`). No model call. |
| `POST /api/sessions/{sid}/documents` | `multipart/form-data`: `file`, `purpose` (`licence` \| `registration` \| `policy` \| `vin` \| `declaration`), optional `surface_id`, `field_path` | `201 { document_id }`. Stores the file (Blob in Azure, a temp folder locally). Size limit 10 MB; PDF, JPEG, PNG, HEIC only. |
| `GET /api/sessions/{sid}/state` | | `200 { state, preferences, signals, advisor_signals, surfaces: [{ surface_id, title, chip, status }], quote_number }`. `state` uses the field envelope from the catalog. |
| `GET /api/sessions/{sid}/quotes` | | `200 { quotes: [{ quote_id, version, offers, reason, created_at, previous_version?, delta_monthly? }] }` |
| `GET /api/health` | | `200 { status: "ok" }` |
| `GET /api/health/sse` | | A short SSE stream: one `data: {"type":"health"}` event, then close. |

Errors use `application/problem+json` (`{ type, title, status, detail }`). The BFF never returns a stack trace.

Session identity: the BFF sets an HttpOnly, Secure, SameSite=Strict cookie `qsid` with the session id on `POST /api/sessions` and checks it on every `/api/sessions/{sid}/...` call (anonymous sessions; Entra External ID sign-in is optional, Q4).

### Surface status (form chips)

`surfaces[].status` is `open`, `submitted`, `closed` or `sent`. The frontend draws a chip per surface (`Formulaire : <chip>`). Clicking a `submitted` or `closed` chip sends `ui.surface_reopen { surface_id }`. The reserved surface ids are `full_form` (full-form mode, D20) and `end_of_form` (the hand-off surface, D19).

---

## 2. BFF ⇄ hosted agent `quote-agent` (invocations protocol)

In Azure: `POST {project_endpoint}/agents/quote-agent/endpoint/protocols/invocations?api-version=v1&agent_session_id={asid}` with an Entra token for `https://ai.azure.com/.default`. Locally the same body is posted to `http://localhost:8088/invocations`.

Request headers: `x-client-session-id`, `x-client-turn-id`, `traceparent`.

Request body: exactly the JSON in `events.md` § "BFF → hosted agent": `{ turn, input, state, phase, open_surfaces, signals, preferences, rule_hits, system_notes, session_ref }`.

Response: `text/event-stream`, same framing as § 1, envelopes with `actor` in `main_agent`, `memory_agent`, `tool:<name>`. Types the agent may emit: `turn.started`, `text.delta`, `text.done`, `a2ui.message` (plus `form_spec` next to `data` for `createSurface` produced by `a2ui.render_form`), `tool.call`, `tool.result`, `memory.result` (data = MemoryAgentOutput), `suggestions` (raw items from `ui.suggest_replies`; the BFF filters them), `phase`, `guardrail`, `quote.created`, `quote.updated`, `turn.completed`, `error`.

The BFF turns `memory.result` into commits (`state.delta`, `signals`, `session.preferences`, `confirm.required`, `rules.hit`) and never forwards it raw.

`GET /readiness` returns 200 when the agent can serve.

---

## 3. MCP servers (streamable HTTP at `/mcp`)

Server-local tool names below. The toolbox namespaces them as `quoting.*` and `a2ui.*` (wire names `quoting__*`, `a2ui__*`), matching `tools.json`. Every tool also accepts `session_ref` (string). The hosted agent injects it; the model never sees it.

### 3.1 `mcp-a2ui`

| Tool | Arguments | Result |
|---|---|---|
| `render_form` | `{ form_spec: FormSpec, state?: SessionState, mode?: "new" \| "edit", session_ref }` | `CallToolResult` with (1) a `TextContent` JSON summary for the model: `{ "surface_id", "status": "shown" }`; (2) an `EmbeddedResource` with `mimeType: "application/a2ui+json"`, `annotations.audience: ["user"]`, whose text is a JSON array with exactly one A2UI 1.0 `createSurface` message (components + dataModel). When `state` is given, every bound path without an explicit `prefill` is filled from the committed state, with provenance `{ source, note }` for `chat`, `document` and `lookup` values. `mode: "edit"` changes the primary button label to "Enregistrer". |
| `update_form` | `{ surface_id, set?: {path: value}, errors?: {path: message}, session_ref? }` | Text summary + embedded resource with `updateDataModel` messages. To add or remove fields, call `render_form` again. |
| `close_form` | `{ surface_id, session_ref }` | Text summary + embedded resource with one `deleteSurface`. |
| `render_offers` | `{ quote_id, version, offers, highlight?, show_adjust?, session_ref }` | Like `render_form`, with an `offer_cards` surface. |

Model-facing versus wire arguments: the model sees `tools.json` (for `render_form`, only `{ form_spec }`; for `render_offers`, `{ quote_id, highlight?, show_adjust? }`). The caller adds the rest before the MCP call: the hosted agent injects `state` and `session_ref` (and `version` and `offers` from the turn's last quote result); the BFF injects `state` and `mode: "edit"` for reopened chips. Every tool declares these injected properties as optional in its MCP input schema so MCP clients forward them; the hosted agent hides them from the model.

The compiler is a Python port of `compile()` in the design demo (`demo/src/a2ui.js`). Its output for every FormSpec in `demo/src/forms.js` must be byte-for-byte equal (after JSON canonicalization) to the JavaScript compiler's output. The golden test runs both.

### 3.2 `mcp-quoting`

Tools and arguments exactly as `tools.json` (`identify_address`, `identify_vehicle_type`, `lookup_make_model_year`, `decode_vin`, `create_quote`, `modify_quote`). `create_quote` and `modify_quote` read the committed session state through a `StateReader` (Cosmos DB in Azure; the BFF's internal endpoint `GET /internal/sessions/{sid}/state` locally), never from model arguments. Lookups and pricing go through adapter interfaces (`RatingEngine`, `VehicleCatalog`, `AddressService`) with stub implementations; the real quoting system plugs in behind those interfaces.

`session_ref` is an HMAC-SHA256 token `base64url(session_id.expiry).base64url(sig)` signed with `SESSION_REF_KEY` (Key Vault in Azure). `mcp-quoting` verifies it and rejects expired or unsigned refs.

### 3.3 Document agent

The BFF calls the Foundry prompt agent `document-agent` (model `gpt-5.6-luna`) through the Responses API with the agent reference, and validates the output against `document-output.schema.json`. Locally and in tests a `FakeDocumentExtractor` returns canned, schema-valid output per purpose.

---

## 4. Shared Python package `quote_common`

`packages/quote_common` holds what every Python service needs and must stay identical across them:

- `quote_common.contracts`: paths to the contract files and loaders (`load_catalog()`, `load_tools()`, `load_schema(name)`).
- `quote_common.catalog`: `Catalog` with `field(key)`, `deterministic_keys`, `required_for_quote`, `entity_path(entity, index)`, `resolve_path(key, entity)`, `label(key, lang)`, `enum_labels(key, lang)`.
- `quote_common.pointer`: RFC 6901 `get`, `set`, `delete`, `escape`, `provenance_path`.
- `quote_common.envelope`: the `Envelope` model, the inbound and outbound type literals, and `sse_encode()` / `sse_decode()`.
- `quote_common.session_ref`: `sign(session_id, key, ttl)` and `verify(token, key)`.
- `quote_common.auth`: `wrap_from_env(app, default_roles, tool_roles)`, the inbound Entra check for both MCP servers (NFR-SEC-3): issuer, audience, allowed caller ids, app roles per tool. Off unless `ENTRA_AUDIENCE` is set. Needs the `auth` extra.
- `quote_common.logs`: `setup(level, otel=False)`, the one JSON log formatter with PII redaction (`redact()`), used by every service.
- `quote_common.state`: `is_usable(envelope, key)`, the one definition of a value that may reach `create_quote` (merge rule 3): `confirmed`, or `inferred` with confidence ≥ 0.6 on a non-deterministic field. The BFF and `mcp-quoting` both call it.

Components depend on it as a path dependency. Change it only with its tests, and never per component.
