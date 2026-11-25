# bff (`quote_bff`)

FastAPI backend-for-frontend of the auto quoting chat (port 8000). It owns the session state, relays the hosted agent's
SSE stream, applies the merge rules and the double confirmations, runs the end-of-form rules after every commit, renders
the surfaces it owns (reopened chips, `full_form`, `end_of_form`) through mcp-a2ui, filters suggested replies, and stores
the advisor hand-offs. Contracts: `../contracts/interfaces.md` §1–3 and `../contracts/events.md` (authoritative).

**Status:** code and tests; nothing deployed. With no URL or endpoint set, every dependency is an in-process fake or an
in-memory store, so the BFF runs and tests with no network and no Azure.

## Layout

| Module | What it holds |
|---|---|
| `app.py` | FastAPI routes, problem+json errors, the `qsid` cookie, CORS, adapter selection from the environment (`build_bff`) |
| `service.py` | `Turn.emit` (the one emitter: seq, persist, forward), `Bff.commit` (the one commit path), `Bff.render` (the one BFF render), the inbound handlers and the hosted-agent relay |
| `state.py` | Session document, catalog validation, merge rules 1–5, derived fields, quote readiness, end-of-form rule glue |
| `forms.py` | `full_form` and `end_of_form` FormSpecs, edit mode, surface bindings, hand-off summary, fr-CA/en string table |
| `suggestions.py` | D15 filter |
| `stores.py` | `Store` protocol with `InMemoryStore` and `CosmosStore`; `LocalBlobStore` and `AzureBlobStore` |
| `clients.py` | `HostedAgentClient` (invocations SSE), `McpClient` (one JSON-RPC call helper for both MCP servers), `FoundryDocumentExtractor`, Entra token provider |
| `fakes.py` | `FakeHostedAgent`, `FakeA2ui`, `FakeQuoting`, `FakeDocumentExtractor` (same methods as the real clients) |
| `rules/` | `end_of_form.py` copied unchanged from the design folder (`../../bff/rules`): the production `EndOfFormRules` |

Shared code comes from `quote_common` (catalog, JSON Pointer, envelope and `sse_encode`, `session_ref`, `state.is_usable`,
`logs`: JSON logs with PII redaction and the optional Azure Monitor setup) and from the
rules module (`get_envelope`, `get_value`, `age_on`, `today`), never re-implemented here.

## Endpoints

| Method and path | Notes |
|---|---|
| `POST /api/sessions` | `{locale?}` → `201 {session_id, quote_number, events}`: greeting `text.done` (actor `bff`) and its `suggestions`. Sets `qsid` (HttpOnly, Secure, SameSite=Strict, path `/api`). Sends the `warmup` invocation in the background. |
| `POST /api/sessions/{sid}/turns` | One inbound envelope `{type, data}` → `text/event-stream`. Types: `user.message`, `a2ui.action`, `ui.surface_reopen`, `confirm.response`, `document.uploaded`. BFF-only inputs stream BFF events only (no `turn.started`). |
| `POST /api/sessions/{sid}/a2ui/functions` | A2UI 1.0 `callAgentFunction` → `agentFunctionResponse`. `lookupMakeModelYear`, `identifyVehicleType`, `decodeVin`, `identifyAddress` (mcp-quoting; arguments filtered to the tool's `tools.json` schema, no `session_ref`, VIN upper-cased), `extractDocument` (document agent). No model call. |
| `POST /api/sessions/{sid}/documents` | Multipart `file`, `purpose`, `surface_id?`, `field_path?` → `201 {document_id}`. PDF/JPEG/PNG/HEIC (magic bytes checked), 10 MB. |
| `GET /api/sessions/{sid}/state` | `{state, preferences, signals, advisor_signals, surfaces, quote_number}` |
| `GET /api/sessions/{sid}/quotes` | `{quotes: [{quote_id, version, offers, reason, created_at, previous_version, delta_monthly}]}`. The stored row also keeps mcp-quoting's `input_hash` (FR-QUO-3). |
| `GET /api/health`, `GET /api/health/sse` | Liveness; a one-event SSE check |
| `GET /internal/sessions/{sid}/state` | mcp-quoting's local `StateReader`. `Authorization: SessionRef <token>` (HMAC with `SESSION_REF_KEY`, signed for this `{sid}`, not expired; else `401`) → `{state, preferences, quote_blocked}` (`state`: the entities with their envelopes; `quote_blocked`: additive, true while a STOP stands or the rules failed). `404` for an unknown session. |

Every `/api/sessions/{sid}/…` call needs the `qsid` cookie of that session (`403` otherwise). Errors are
`application/problem+json` `{type, title, status, detail}`, never a stack trace.

## How a turn works

- `open_turn` allocates the turn number; the inbound event is persisted first (not forwarded), then every outbound event
  goes through `Turn.emit`: seq + 1, write to `events`, then forward. Events the BFF does not forward (`memory.result`,
  a dropped main-agent reply, `suggestions.dropped`) are stored with `forwarded: false`. On a chat turn the main
  agent's reply (`text.*`, `a2ui.message`, `suggestions`) waits for `memory.result`, so a rule question or a STOP found in
  that result drops the whole reply (`Bff.agent_turn`, `Bff.relay`).
- `Bff.commit(changes, source, actor)` is the only way state changes, for every source: memory deltas, form submits,
  document reads and reviews, edit-mode and `full_form` saves, `rule_confirm` / `rule_correct`, confirmation steps and
  expiries. In one session write (etag, 3 retries) it merges, derives, holds deterministic fields, books conflicts and
  runs `DEFAULT_RULES.check`; then it writes `state_changes`, emits `state.delta`, `confirm.required`, live autofill
  `updateDataModel` (value + provenance) for open surfaces, `rules.hit`, and acts on the outcome: `go`, `ask` (one
  templated question with `rule_confirm` / `rule_correct` suggestions) or `stop` (close open surfaces, render
  `end_of_form`). For form, document and confirmation inputs the agent runs only on `go`; on the chat path an `ask` or
  `stop` drops the rest of the main agent's reply (logged, not forwarded).
- Agent `a2ui.message` `createSurface` registers the surface (its `form_spec` is kept, then stripped); after a STOP the
  agent's new forms are dropped. `suggestions` are filtered (D15). `quote.created` / `quote.updated` are stored.
  `turn.started` clears the system notes the invocation carried.
- Status by source: `form`, `system`, `lookup` and a reviewed document (DocumentDrop submit) are `confirmed`; `chat` and
  an unreviewed document are `inferred`, as is any change with confidence < 0.6. What may be priced is defined once,
  in `quote_common.state.is_usable` (rule 3, shared with mcp-quoting): `confirmed`, or `inferred` with confidence ≥ 0.6
  on a non-deterministic field; never `pending_confirmation`. `get_quote` carries `missing_fields`
  (`[{path, key, label, reason}]`, mcp-quoting's shape).
- FR-INT-2 is enforced once, in the commit path: a value for a catalog field marked `pii` (date of birth, email, phone,
  address, driver date of birth, licence number) is not written while `/quote/contact_consent` is not confirmed `true`.
  No `state.delta` or `state_changes` row is produced for it, and a system note asks the agent to ask again after
  consent. The `end_of_form` contact fields are exempt (their own `handoff_consent`).
- `full_form` also needs `/quote/contact_consent` confirmed (FR-INT-2): otherwise the BFF answers with a templated question
  and two suggestions and renders nothing. "Oui, j’accepte" there is the BFF action `consent_yes`: it goes to
  `Bff.commit`, the double confirmation follows, and step 2 opens the full form (no agent turn). `end_of_form` is the
  explicit exception: its own `handoff_consent` checkbox is the consent for that hand-off. mcp-a2ui also leaves
  personal-data fields out of other forms until the consent is confirmed.
- Hand-off references are `CNS-` plus 6 digits, checked for uniqueness, retried on a collision.

## Configuration

| Variable | Default | Meaning |
|---|---|---|
| `SESSION_REF_KEY` | `dev-only-local-key` | HMAC key for `session_ref`; must equal mcp-quoting's (same dev value as its README; Key Vault in Azure) |
| `SESSION_COOKIE_KEY` | `dev-only-session-cookie-key` | HMAC key that signs the `qsid` cookie (Key Vault in Azure) |
| `SESSION_REF_TTL_SECONDS` | `900` | `session_ref` lifetime |
| `COOKIE_SECURE` | `true` | Set `false` only for plain-http local dev |
| `CORS_ORIGINS` | `http://localhost:5173` | Comma-separated web origins (credentials allowed) |
| `HOSTED_AGENT_URL` | empty | Local hosted agent, e.g. `http://localhost:8088` (posts to `/invocations`) |
| `FOUNDRY_PROJECT_ENDPOINT` | empty | Foundry project endpoint: hosted agent through invocations, document agent through Responses (Entra token, `https://ai.azure.com/.default`) |
| `HOSTED_AGENT_NAME` / `DOCUMENT_AGENT_NAME` | `quote-agent` / `document-agent` | Agent names in the project |
| `A2UI_MCP_URL` / `A2UI_MCP_SCOPE` | empty | mcp-a2ui, e.g. `http://localhost:8102/mcp`; the scope (e.g. `api://<tenant-id>/<workload>-<env>-mcp-a2ui/.default`, as Terraform sets it) turns on Entra tokens |
| `QUOTING_MCP_URL` / `QUOTING_MCP_SCOPE` | empty | mcp-quoting, e.g. `http://localhost:8101/mcp` |
| `COSMOS_ENDPOINT` / `COSMOS_DATABASE` | empty / `quoting` | Cosmos DB (managed identity) |
| `BLOB_ACCOUNT_URL` / `BLOB_CONTAINER` | empty / `uploads` | Azure Blob (managed identity, Defender scan tag) |
| `LOCAL_UPLOAD_DIR` | temp folder | Upload folder when no Blob account is set |
| `MAX_MESSAGE_CHARS` / `MAX_UPLOAD_BYTES` | `2000` / `10485760` | Limits |
| `LOG_LEVEL` / `OTEL_ENABLED` | `INFO` / `false` | `OTEL_ENABLED=true` calls `configure_azure_monitor()` (reads `APPLICATIONINSIGHTS_CONNECTION_STRING`) |
| `QUOTE_CONTRACTS_DIR` | found by walking up | Contracts folder (`/app/contracts` in the image) |
| `PORT` | `8000` | Listen port |

## Adapters and how to swap them

| Port | Local / tests | Azure | Selected by |
|---|---|---|---|
| `Store` (`get`, `put`, `query`) | `InMemoryStore` | `CosmosStore` (azure-cosmos aio, `DefaultAzureCredential`) | `COSMOS_ENDPOINT` |
| Blob store (`put`, `get`) | `LocalBlobStore` | `AzureBlobStore` (waits for the Defender `Malware Scanning scan result` tag) | `BLOB_ACCOUNT_URL` |
| Hosted agent (`invoke`) | `FakeHostedAgent` | `HostedAgentClient` | `HOSTED_AGENT_URL`, else `FOUNDRY_PROJECT_ENDPOINT` |
| MCP (`call(tool, args)`) | `FakeA2ui`, `FakeQuoting` | `McpClient` | `A2UI_MCP_URL`, `QUOTING_MCP_URL` |
| Document extractor (`extract`) | `FakeDocumentExtractor` | `FoundryDocumentExtractor` | `FOUNDRY_PROJECT_ENDPOINT` + `DOCUMENT_AGENT_NAME` |

A new implementation only needs the same methods; wire it in `app.build_bff`, or pass a ready `Bff` to
`create_app(settings, bff)` (the tests do that).

## Run

```bash
cd solution/bff
uv venv && uv pip install -e ../packages/quote_common -e ".[test,azure]"
.venv/bin/quote-bff                       # fakes everywhere; COOKIE_SECURE=false for plain-http dev
HOSTED_AGENT_URL=http://localhost:8088 A2UI_MCP_URL=http://localhost:8102/mcp \
  QUOTING_MCP_URL=http://localhost:8101/mcp COOKIE_SECURE=false .venv/bin/quote-bff   # sibling services
# mcp-quoting then reads state from here: STATE_READER=bff BFF_BASE_URL=http://localhost:8000 SESSION_REF_KEY=dev-only-local-key
```

Image (build context is the solution folder): `docker build -f bff/Dockerfile -t quote-bff .` (python:3.13-slim,
non-root uid 10001, contracts at `/app/contracts`, `EXPOSE 8000`, `CMD quote-bff`).

## Test

```bash
cd solution/bff
.venv/bin/python -m pytest              # everything: unit (incl. the 95 copied rules tests) + component
.venv/bin/python -m pytest tests/unit   # one function or class, no I/O
.venv/bin/python -m pytest tests/component   # the BFF in-process (TestClient), every dependency faked
.venv/bin/ruff check .
```

Component tests drive the API with a scripted `FakeHostedAgent` (see `tests/conftest.py`: `chat_turn`, `memory`,
`form_event`), fake MCP clients and in-memory stores, and assert on the SSE stream, the stored rows and the invocation
bodies. No test opens a socket.

## Add an end-of-form rule

Follow "How to add a rule" in the docstring of `src/quote_bff/rules/end_of_form.py` (predicate, severity, fr/en
reasons, `@DEFAULT_RULES.rule(...)`, positive and negative fixtures in `tests/unit/rules/test_end_of_form.py`). Nothing
else changes: `Bff.commit` already runs every registered rule after every commit batch.

## Deviations from the contracts (and why)

- `confirm.required` carries `path`, `old_value`, `new_value` of the first change **plus** `changes[]`: the deterministic
  fields of one batch share one confirmation (implementation plan 8.5).
- `confirm.rejected` is forwarded as an event (`{confirmation_id, reason: rejected|expired, paths}`); events.md names it
  but its outbound table does not list it. `suggestions.dropped` is stored, never forwarded.
- A reverted value (refusal, expiry, `rule_correct`) restores the previous envelope, or writes a cleared envelope
  (`value: null`, status `inferred`) when there was none; `state.delta` shows it the same way.
- Upload purpose `policy` is read as `declaration`: document-output.schema.json has no `policy` purpose.
- The `qsid` cookie value is `<session_id>.<hmac>`: it carries the session id, signed, so a session id seen in a URL or a
  log cannot be replayed as a cookie.
- Invocation `input` uses the names quote-agent reads: `form_submitted {surface_id, changed_paths}`;
  `action {action_id, quote_id?, changes?}` (`get_quote` adds `missing_fields`, `rule_correct` adds `rule_id` and
  `changed_paths`, a form button adds `surface_id` and `changed_paths`); `document_extracted {purpose, changed_paths,
  document_id}`; `confirmation_result {accepted, changed_paths, confirmation_id}`.
- Model-triggered lookups (`tool.result.data.result`) are relayed unchanged and not committed: the customer's submit of
  the form the agent filled commits the values. The document agent's output has its nulls dropped before validation.
  An empty `driver_vehicle_assignments` list is never committed.
- Memory conflicts reach the main agent as `system_notes` (the invocation body has no `conflicts` key).
- Every request gets its own turn number (BFF-only events and function calls too), so agent turns are not contiguous.
  The inbound event is stored as seq 1 (not forwarded); outbound seq is strictly increasing within the turn.
- Chat values are always stored as `inferred` (the rules module, FR-RUL-3 and the process flow need that); rule 3's
  0.6 threshold decides what may be priced, through the shared `quote_common.state.is_usable`.
- Close actions (`dismiss`, `cancel`, `back`, `nodocs`, `edit_answers`) never call the agent; a system note tells it on
  the next turn. `a2ui.callAgentFunction` is accepted only on `/a2ui/functions`.
- Not implemented: `Last-Event-ID` replay (FR-CHAT-5; events are stored by `(turn, seq)` so it can be added), per-session
  rate limits (20 turns/min, 10 uploads/hour; Front Door WAF only), 250 ms batching of `text.delta` rows (each delta is
  one row), the Event Grid scan event (the Blob adapter polls the scan tag instead).

## Requests to other components

- **mcp-a2ui:** `render_form` with `state` should also fill repeater rows (`/vehicles/N/<key>`, `/drivers/N/<key>`) and
  DocumentDrop `data.values` from the committed state; in `mode: "edit"` keep the FormSpec's own `cancel` / `submit`
  actions (the BFF sends them). The BFF calls the server-local tool name `render_form` directly.
- **mcp-quoting:** `create_quote` / `modify_quote` refuse while a STOP stands (`quote_blocked` in the internal endpoint's
  answer locally; `stop` or `rules_error` on the Cosmos `sessions` document in Azure). The BFF also blocks the
  `get_quote` action itself.
- **quote-agent:** put `form_spec` next to `data` on every `createSurface` from `a2ui.render_form`; `memory.result.data`
  must validate against memory-output.schema.json (otherwise the BFF ignores it); append `system_notes` before the input.
- **web:** send `a2ui.action.context.values` as a flat `{pointer: value}` map (unbound pointers are ignored); use action
  names `submit`/`save` to commit, `cancel`/`dismiss`/`back`/`nodocs`/`edit_answers` to close, `send` on `end_of_form`;
  show `confirm.required.changes[]` when present; call the API with credentials (`qsid` is HttpOnly).
- **infra:** Key Vault secrets for `SESSION_REF_KEY` and `SESSION_COOKIE_KEY`; Cosmos containers `sessions`, `events`,
  `state_changes`, `quotes`, `confirmations`, `handoffs` (partition key `/session_id`); Defender for Storage malware
  scanning with index tags on `uploads`. The hand-off is not connected to any advisor system: the `handoffs` row is the
  record.
