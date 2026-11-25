# mcp-quoting

MCP server (`mcp_quoting`) with the six quoting tools of `contracts/tools.json`: `identify_address`, `identify_vehicle_type`, `lookup_make_model_year`, `decode_vin`, `create_quote`, `modify_quote`. Streamable HTTP at `/mcp`, `GET /healthz`, port 8101.

Everything outside the server sits behind a small interface (`mcp_quoting.adapters`) with a stub that runs the whole stack and an HTTP skeleton for the real quoting system. **The next owner connects the real system by implementing those interfaces; see [How to connect the real quoting system](#how-to-connect-the-real-quoting-system).**

Status: code and tests. Nothing here calls Azure unless you configure it to.

## Layout

```
src/mcp_quoting/
  server.py        tools.json -> MCP tools, argument validation, error results, app (behind the shared Entra check), main
  tools.py         the six handlers; one guard (session_ref + readiness) and one `_price` for create and modify
  state.py         session state -> usable snapshot, missing_fields, quote options, input hash
  vin.py           VIN check digit
  adapters/
    base.py        the six Protocols, `Offer`, `SessionNotFound`
    stubs.py       one stub per Protocol, ported from the design prototype
    http.py        `HttpApi` (base URL, timeout, retries, auth hook) + the HTTP adapters + the BFF state reader
    cosmos.py      the Azure state reader
    __init__.py    `Deps` and `from_env`, the one place implementations are chosen
tests/unit/        one function or class, no I/O
tests/component/   the server in-process (SDK client over memory, ASGI app, a real `python -m mcp_quoting` process)
```

## Run and test

```bash
cd solution/mcp/quoting
uv venv && uv pip install -e ../../packages/quote_common -e ".[test]"

.venv/bin/python -m pytest                    # everything
.venv/bin/python -m pytest tests/unit         # unit
.venv/bin/python -m pytest tests/component    # component
.venv/bin/ruff check . && .venv/bin/ruff format --check .

# serve with stubs, reading state from a BFF on :8000
SESSION_REF_KEY=dev-only-local-key .venv/bin/python -m mcp_quoting
curl -s localhost:8101/healthz
curl -s localhost:8101/mcp -H 'Accept: application/json, text/event-stream' -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"lookup_make_model_year","arguments":{"make":"lambo"}}}'
```

Docker (build context is `solution/`; the daemon was not available when this was written, see Not verified):

```bash
docker build -f mcp/quoting/Dockerfile -t mcp-quoting:dev solution
docker run --rm -p 8101:8101 -e SESSION_REF_KEY=dev-only-local-key -e BFF_BASE_URL=http://host.docker.internal:8000 mcp-quoting:dev
```

## Tools

Names, descriptions and input schemas are **read from `contracts/tools.json`** at startup (plus an optional string `session_ref` on every tool), so the contract cannot drift; the server refuses to start if a tool has no handler. Arguments are validated against that schema before a handler runs. The SDK is `mcp` 2.x, where `FastMCP` was renamed `MCPServer`; this server uses the SDK's public low-level `mcp.server.lowlevel.Server` instead, which serves the contract's schemas as they are.

| Tool | Mode | Result (JSON text, also `structuredContent`) |
|---|---|---|
| `identify_address` | terminal | `{candidates: [{address_line1, city, province_code, postal_code, confidence}]}`, at most 5 |
| `identify_vehicle_type` | terminal | `{vehicle_type, body_style, confidence}` |
| `lookup_make_model_year` | terminal | `{valid, normalized: {year, make, model}}` plus `years` (year omitted), `makes` (make missing or unknown) or `models` (model missing or unknown). Spelling is forgiving: case, accents, spaces and hyphens |
| `decode_vin` | terminal | `{valid_checksum: false}` or `{valid_checksum: true, year, make, model, trim}`. The VIN must be upper case (the schema's pattern) |
| `create_quote` | narrate | `{quote_id, version, offers, input_hash}` |
| `modify_quote` | narrate | `{quote_id, version, previous_version, delta_monthly, offers, input_hash}` |

An offer is `{offer_type, monthly, annual, deductible, coverages}` (`coverages` are fr-CA). `input_hash` (not in tools.json) is the SHA-256 of the input snapshot (FR-QUO-3); the BFF should store it with the quote.

**Errors** are tool results with `isError: true` and a body `{"error": code, "message": ..., ...}`, never protocol errors:

| `error` | When |
|---|---|
| `invalid_arguments`, `unknown_tool` | The arguments do not match the schema in tools.json; no such tool |
| `invalid_session_ref` | `session_ref` missing, malformed, forged (bad signature) or expired |
| `quote_blocked` | An end-of-form STOP rule (or a rules error) is active for the session: the customer goes to an advisor. Checked before `missing_fields`; do not retry or price |
| `missing_fields` | Carries `missing_fields: [{path, key, label, reason}]` with `reason` `missing` (absent or empty), or the envelope's `status` when it is present but not usable: `inferred` (confidence under 0.6, or a deterministic field), `pending_confirmation`, `unconfirmed` (no status) |
| `session_not_found` | The state reader has no state for the session |
| `unknown_quote` | `modify_quote` on a quote id this session does not have |
| `invalid_changes` | `changes` has a key other than `deductible`, `add_ons`, `offer_type`, `annual_kilometers`, or a value the catalog forbids |
| `no_offers` | The rating engine returned nothing |
| `upstream_error` | An HTTP adapter failed after its retries, or the rating engine's answer breaks the `Offer` contract (status, URL and values are not exposed) |
| `internal_error` | Anything else; details stay out of the result |

### How quotes behave

- **Session state only.** `create_quote` and `modify_quote` verify `session_ref` with `quote_common.session_ref.verify` and `SESSION_REF_KEY`, then read the session through the `StateReader`. The model cannot pass field values: unknown arguments are ignored and `changes` accepts only the four quote options.
- **Blocked sessions are never priced.** The state reader reports `quote_blocked`; while it is true both quote tools answer `quote_blocked`, before looking at any field. The flag is not part of the snapshot or the hash, and the state is read again on every call, so pricing resumes once the BFF lifts the STOP.
- **Quotable means every `required_for_quote` field is usable** (`state.missing_fields`). "Usable" is defined once, in `quote_common.state.is_usable` (events.md merge rule 3, FR-DET-6), and this server calls it, with no copy of the rule: a `confirmed` value is usable whatever its source (a deterministic field confirmed by the double confirmation keeps `source: chat`); an `inferred` value is usable at confidence 0.6 or more, except on a deterministic field (`has_claims`, `has_convictions_in_past_three_years`, `contact_consent`, ...), which must be confirmed first; a `pending_confirmation` value never is. The catalog's required keys apply to every vehicle and every driver in the state (at least one of each). One driver on one vehicle needs no `driver_vehicle_assignments`; two or more of either do.
- **The snapshot** handed to the rating engine holds the usable values only (the same `is_usable` test), unwrapped, in the shape of the session document: `{quote: {...}, vehicles: [{...}], drivers: [{...}], underwriting: {...}, household: {...}}`, with catalog keys. Optional keys may be absent. `deductible` is the catalog enum string.
- **Versions.** `create_quote` always starts a new quote (`Q-` and 8 hex digits, version 1). `modify_quote` adds the next version of a quote of the same session. Earlier versions stay in the `QuoteStore`. The quote options (`deductible`, `add_ons`, `offer_type`, `annual_kilometers`; the last goes to every vehicle) accumulate over versions and are applied on top of the state, which is read again each time, so a modification after the customer changed an answer prices the new answer.
- **`delta_monthly`** is the monthly price of the selected offer in this version minus the selected offer of the previous one. The selected offer is the `offer_type` option, else the usable `offer_type` of the state, else `plus`; it falls back to the first offer when it was not priced.
- **`input_hash`**: `sha256` of the snapshot after the options are applied, as canonical JSON (`sort_keys`, separators `,` and `:`, UTF-8 without ASCII escapes). Same function as `state.input_hash`.
- **Lookups ignore `session_ref`.** They need no session. Never pass the VIN in lower case.

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8101` | Listen port |
| `HOST` | `0.0.0.0` | Bind address. A loopback address turns on the SDK's Host-header check |
| `LOG_LEVEL` | `INFO` | Python log level, passed to `quote_common.logs.setup`. Logs are one JSON object per line in the shared format (`ts`, `level`, `logger`, `msg`, extra fields, `error`); this server logs tool name, outcome and duration, never arguments or state values |
| `SESSION_REF_KEY` | none, required | HMAC key of the `session_ref` tokens; the same value the BFF signs with (Key Vault secret in Azure; locally a `dev-only-...` value) |
| `QUOTE_CONTRACTS_DIR` | `<solution>/contracts` | Folder with `tools.json` and `state-catalog.json` (the image sets `/app/contracts`) |
| `STATE_READER` | `bff` | `bff` (local and end-to-end) or `cosmos` (Azure) |
| `BFF_BASE_URL` | `http://localhost:8000` | BFF for `STATE_READER=bff` |
| `COSMOS_ENDPOINT` | none | Required with `cosmos`, for example `https://<account>.documents.azure.com:443/` |
| `COSMOS_DATABASE`, `COSMOS_CONTAINER` | `quoting`, `sessions` | Where the session documents are |
| `RATING_ENGINE` | `stub` | `stub` or `http`. `http` needs `RATING_BASE_URL` |
| `VEHICLE_CATALOG` | `stub` | `stub` or `http`. `http` needs `CATALOG_BASE_URL` |
| `ADDRESS_SERVICE` | `stub` | `stub` or `http`. `http` needs `ADDRESS_BASE_URL` |
| `VIN_DECODER` | `stub` | `stub` or `http`. `http` needs `VIN_BASE_URL` |
| `RATING_SCOPE`, `CATALOG_SCOPE`, `ADDRESS_SCOPE`, `VIN_SCOPE` | unset | Entra scope of that back end, for example `api://<app-id>/.default`. When set, every call carries a bearer token from `DefaultAzureCredential` (managed identity). Unset: no token |
| `HTTP_TIMEOUT_SECONDS`, `HTTP_RETRIES` | `5`, `2` | Timeout per attempt and retries (transport errors, timeouts, 429, 502, 503, 504; exponential backoff from 0.2 s) for all HTTP adapters. NFR-PERF-5 wants `create_quote` under 3 s at p95: lower both for a real engine |
| `AZURE_CLIENT_ID` | unset | Picks the user-assigned managed identity for `DefaultAzureCredential` |
| `ENTRA_AUDIENCE` | unset | Turns on the inbound Entra check (NFR-SEC-3, `quote_common.auth`). Comma-separated accepted `aud` values: the `api://` URI and/or the client id of this API's app registration. Unset: no check, as in local runs and tests |
| `ENTRA_TENANT_ID` | none, required when the check is on | Tenant whose tokens are accepted (issuer v1 or v2, signing keys from the tenant's JWKS) |
| `ENTRA_ALLOWED_PRINCIPAL_IDS` | empty | Comma-separated object ids (`oid`) of the callers allowed in (the Foundry project's managed identity, which the toolbox calls as, and the BFF identity). Empty: any caller of the tenant that has a role |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset | When set (needs the `azure` extra), `quote_common.logs.setup(..., otel=True)` calls `configure_azure_monitor()`, which exports the logs and the spans the SDK already emits for every tool call (named `tools/call <tool>`, attributes `gen_ai.operation.name=execute_tool` and `gen_ai.tool.name`, parent trace context read from `_meta`). No exporter otherwise |

The `azure` extra (`pip install 'mcp-quoting[azure]'`, installed in the image) adds `azure-cosmos`, `azure-identity` and `azure-monitor-opentelemetry`. Nothing imports them unless `cosmos`, a `*_SCOPE` or the connection string is configured.

## Adapters

| Interface (`adapters/base.py`) | Stub (`adapters/stubs.py`) | Real system |
|---|---|---|
| `RatingEngine.price(snapshot, offer_types)` | `StubRatingEngine`: the prototype's `rate()` | `HttpRatingEngine` |
| `VehicleCatalog.years / makes / models / vehicle_type` | `StubVehicleCatalog`: 13 makes including Lamborghini Huracán and Urus, with first model years | `HttpVehicleCatalog` |
| `AddressService.candidates(query, postal_code)` | `StubAddressService`: the typed street under Laval, Montréal and Québec | `HttpAddressService` |
| `VinDecoder.decode(vin)` | `StubVinDecoder`: check digit, year from the 10th character, maker from the first three | `HttpVinDecoder` |
| `StateReader.read(session_id, session_ref)` | `StubStateReader`: a dict, for tests | `HttpBffStateReader` (local, end-to-end), `CosmosStateReader` (Azure) |
| `QuoteStore.latest / save` | `InMemoryQuoteStore` | none yet, see below |

The stubs are demo fixtures, not rules. `StubRatingEngine` is the prototype's formula (vehicle age, type and use; claims, convictions, a young additional driver, years with the insurer; offer, deductible and add-on factors) with one addition: kilometres per year scale the premium (0.9 at 0 km, neutral at 15 000, at most 1.15) so `modify_quote` visibly reacts to them. A deductible in the snapshot applies to every offer; without one the offers use 1 000 $, 500 $ and 250 $. Test prices are checked against the prototype's code run at 2026.

## How to connect the real quoting system

Each adapter is independent: switch them one at a time with the variables above. Start with the rating engine.

### 1. Rating engine (`RatingEngine`)

1. Set `RATING_ENGINE=http`, `RATING_BASE_URL=https://<rating api>` and, for an Entra-protected API, `RATING_SCOPE=api://<app id>/.default`. Give the container app's managed identity access to that API.
2. `HttpRatingEngine` (in `adapters/http.py`) calls `POST {RATING_BASE_URL}/rate` with `{"snapshot": ..., "offer_types": [...]}` and expects `{"offers": [{offer_type, monthly, annual, deductible, coverages}]}`. **Your system will differ: write the two mapping functions** and pass them in `from_env`:
   - `to_payload(snapshot, offer_types) -> dict`: the snapshot (see "How quotes behave") to the real request. Options are already applied: `snapshot["underwriting"]` holds `deductible` (string), `add_ons`, `offer_type`, and each vehicle has `annual_kilometers`.
   - `to_offers(body) -> list[dict]`: the real response to dicts that validate as `Offer`: `offer_type` is `essential`, `plus` or `premium`; `monthly` and `annual` are amounts in CAD; `deductible` an integer; `coverages` fr-CA customer-facing strings. A response that does not validate is an `upstream_error`; a mapping function that crashes is an `internal_error` (the code frames are in the log).
   - Change the path or method, or add headers, in `HttpRatingEngine.price` itself; it is two lines.
3. If the real system is not JSON over HTTP, write a class with `async def price(self, snapshot, offer_types) -> list[Offer]` and add it to the `RATING_ENGINE` options in `from_env`.
4. Check what the real engine needs against `required_for_quote` in `contracts/state-catalog.json`. That list, not code, decides what is "quotable"; add a field to the catalog (a contract change for every component), not a special case here. The only special cases in `state.missing_fields` are the per-vehicle and per-driver expansion and the single driver and vehicle exemption. If the real system issues its own quote ids and versions, see step 5.

### 2. Vehicle catalog, VIN, address

- `VEHICLE_CATALOG=http` + `CATALOG_BASE_URL`: GET `/years` is `[int]`, `/makes?year=` and `/models?make=&year=` are `[str]` (canonical spelling; models already filtered by year), `/vehicle-type?make=&model=&year=` is `{vehicle_type, body_style, confidence}` where `vehicle_type` is one of `car`, `suv`, `minivan`, `pickup`, `motorcycle`, `motorhome`. Pass `to_names` and `to_type` for other shapes. Matching of customer spelling is done once in `tools.lookup_make_model_year`, so the catalog only returns canonical names.
- `VIN_DECODER=http` + `VIN_BASE_URL`: GET `/vin/{vin}` answers `{year, make, model, trim}`; the check digit is verified before the call. Pass `to_vehicle` for another shape.
- `ADDRESS_SERVICE=http` + `ADDRESS_BASE_URL`: GET `/addresses?q=&postal_code=` answers `{"candidates": [...]}`, best first; the tool keeps 5. Pass `to_candidates` for another shape. Province codes are the catalog's `province_code` values.
- All three use `HttpApi`: the same base URL, `*_SCOPE` token, timeout and retries as the rating engine.

### 3. Session state (`StateReader`)

- Local and end-to-end: `STATE_READER=bff` (default). `GET {BFF_BASE_URL}/internal/sessions/{sid}/state` with `Authorization: SessionRef <session_ref>`; the BFF answers `{session_id, phase, state, quote_blocked, missing_fields}`, or 404 for an unknown session. Only `state` and `quote_blocked` are read; a missing `quote_blocked` means not blocked and any other key (the older `preferences`, the BFF's own `missing_fields`, which this server recomputes from the same state) is ignored.
- Azure: `STATE_READER=cosmos`, `COSMOS_ENDPOINT`. Reads item id = partition key = session id in `quoting/sessions` with `DefaultAzureCredential`. Grant the container app's identity the data-plane role **Cosmos DB Built-in Data Reader** (read-only, as architecture §6.3 requires). `quote_blocked` is true when the session document's `stop` (the active STOP rule hit) or `rules_error` is set, the same rule the BFF applies.

### 4. Quote ids and versions (`QuoteStore`)

`InMemoryQuoteStore` lives in one process. **With several replicas a `modify_quote` can reach a replica that does not know the quote** and answers `unknown_quote`. Until a persistent store exists, run one replica (the plan says min 1, max 10: set max to 1). To fix it, implement `latest(session_id, quote_id)` and `save(session_id, quote_id, record)` on a database (the record is a plain dict, JSON-ready; `save` assigns the id and `version = previous + 1` atomically) and replace `InMemoryQuoteStore()` in `from_env`. If the real system issues quote ids, wrap its API in the same two methods and keep `input_hash` in the record.

### 5. Tests to add

- **Wire format** (unit): for each HTTP adapter you enable, copy the pattern in `tests/unit/test_http.py`: an `httpx.MockTransport` answering with a recorded real response, asserting the request (path, params, body, headers) and the mapped result. Include the real error cases (404, 429, 5xx).
- **Offers**: each mapped offer passes `Offer.model_validate`.
- **Integration** (level `azure`, skipped unless `RUN_AZURE_TESTS=1`): a few states from `tests/helpers.py` (`make_state({...})` variations) priced by the real engine in its test environment, compared to the engine's own result; `create_quote` p95 under 3 s over 50 calls (NFR-PERF-5).
- **Persistent `QuoteStore`**: the semantics tested in `tests/unit/test_stubs.py::test_quote_store_numbers_versions_per_quote_and_session`, against the real database.
- **State reader in Azure**: one read of a seeded session document with the deployed identity.
- If you change the required-field rule: `tests/unit/test_state.py`.

### 6. Before go-live

`SESSION_REF_KEY` from Key Vault, equal to the BFF's. All `*_SCOPE` and role assignments in place. `HTTP_TIMEOUT_SECONDS` and `HTTP_RETRIES` sized so a failing back end still answers within the budget. Replicas decided (step 4). `ENTRA_*` set and the app roles assigned (see Security).

## Security and privacy

- `session_ref` is verified before anything is read. A forged, unsigned or expired token gets `invalid_session_ref`; the session id comes only from the verified token. The token is also what the BFF endpoint accepts as credential, so it is never logged.
- No argument or state value is logged (and the shared logger masks emails, phone numbers, postal codes, VINs and licence numbers in every log line anyway), and results to the model contain no state values. Crash logs carry the exception type and the code frames, not the message. Errors from back ends are replaced by a generic `upstream_error`.
- **Inbound caller authentication (NFR-SEC-3)** is the shared `quote_common.auth.wrap_from_env`, applied in `create_app()` and off unless `ENTRA_AUDIENCE` is set. When on, every request except `GET /healthz` needs a Bearer token signed by the tenant's keys, with the right issuer and audience, an allowed `oid` (if `ENTRA_ALLOWED_PRINCIPAL_IDS` is set) and an app role: `Tools.Invoke` to call `create_quote` or `modify_quote`, `Tools.Invoke` or `Lookup.Read` for everything else (lookups, `initialize`, `tools/list`). A missing or bad token is 401, a missing role or caller is 403. It sits in front of the `session_ref` check, which still applies to every quote call.

## Requests to other components

- **BFF** (`bff/`): `GET /internal/sessions/{sid}/state` must return `{session_id, phase, state: <session document with field envelopes>, quote_blocked, missing_fields}` for `Authorization: SessionRef <ref>` (as its README says; `quote_blocked` is what this server needs, the rest is ignored): verify the ref with the same key and require that its session id equals `{sid}`; answer 404 for an unknown session and 401 or 403 for a bad ref. Keep the `status` and `confidence` of every envelope: `quote_common.state.is_usable` decides what is priced, here and in the BFF's own completeness check.
- **BFF**: `create_quote` and `modify_quote` results carry two keys beyond `quote.created` and `quote.updated` in events.md: `delta_monthly` (modify) and `input_hash`. Store `input_hash` in `quotes` for FR-QUO-3; to recompute it from `state_changes`, hash the usable snapshot with `mcp_quoting.state.snapshot` and `input_hash` (canonical JSON rule above). Moving those two functions into `quote_common` is a decision for the orchestrator.
- **BFF**: lookups may carry `session_ref` (it is ignored); send the VIN in upper case.
- **Hosted agent** (`agents/quote-agent`): inject `session_ref` into `create_quote` and `modify_quote` only. Treat a result with `isError: true` as a refusal to narrate (`missing_fields` lists what to ask), not as a quote: emit no `quote.created`. `modify_quote` needs the `quote_id` returned by `create_quote` in the same session.
- **Infra** (`infra/terraform`): container app `mcp-quoting`, port 8101, probes on `/healthz`, max replicas 1 until the quote store is persistent, `SESSION_REF_KEY` from a Key Vault reference, the managed identity's Cosmos read role, `APPLICATIONINSIGHTS_CONNECTION_STRING`, and the variables above. For the Entra check (`infra/terraform/entra/` and `locals.tf` build it): an app registration `<workload>-<env>-mcp-quoting` whose identifier URI is `api://<tenant-id>/<workload>-<env>-mcp-quoting` (for example `api://aiq-dev-mcp-quoting`), with the app roles `Tools.Invoke` and `Lookup.Read`. `Tools.Invoke` is assigned to the Foundry project's managed identity (the toolbox calls as the project) and `Lookup.Read` to the BFF identity, which can then call the lookups, `initialize` and `tools/list` but not `create_quote` or `modify_quote`. `ENTRA_TENANT_ID`, `ENTRA_AUDIENCE` (that URI, then `,<client id>` once `mcp_client_ids` is set) and `ENTRA_ALLOWED_PRINCIPAL_IDS` (the BFF and project identities) are set on the container app.
- **End-to-end** (`e2e/`): give the BFF and `mcp-quoting` the same `SESSION_REF_KEY`. The stub decodes a VIN from the first three characters (`2T3` Toyota RAV4, `2T1` Corolla, `2HG` Civic, `ZHW` Huracán, `ZPB` Urus; any other maker: Toyota RAV4) and the model year from the 10th character.

## Not verified

- The Docker image was not built (no Docker daemon). Its install steps were run with plain `pip` in a scratch folder with the same layout: non-editable install with the `azure` extra, `QUOTE_CONTRACTS_DIR` pointing at a copy of `contracts/`, server started and called over HTTP.
- Nothing ran against Azure. `CosmosStateReader`, `entra_token` and the Application Insights hook were checked with fakes, and the `azure-cosmos`, `azure-identity` and `azure-monitor-opentelemetry` names and signatures they use were checked by import. A real Cosmos 404 raises `CosmosResourceNotFoundError` with `status_code` 404, which is what the reader tests for.
- How the Foundry toolbox treats `structuredContent` and `isError` results, and whether it needs SSE instead of JSON responses (`json_response=True` in `create_app`), is unverified.
- The Entra check: the token logic is tested in `quote_common`, and here one component test proves the app still serves with `ENTRA_AUDIENCE` unset, keeps `/healthz` open and answers 401 without a token when it is set, and that the role map names real tools. No real Entra token, app role or caller allow-list was exercised. With `ENTRA_AUDIENCE` set and no `ENTRA_TENANT_ID` the shared code raises a `RuntimeError` at startup (`ENTRA_TENANT_ID is required when ENTRA_AUDIENCE is set`); `main()` logs it as a configuration error and exits with code 1.
- Latency (NFR-PERF-5) was not measured against a real back end.
