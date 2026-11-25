---
name: connect-quoting-systems
description: Replace the stub rating engine and lookups in mcp-quoting with the insurer's real quoting systems, and expose quoting tools through the Foundry toolbox. Use it for "connect the rating engine", "plug in the real quoting API", "use the insurer's vehicle catalog, address or VIN service", "map the rating response", "make quotes persistent", "add a quoting tool", "add an MCP server behind the toolbox", "which app role does this tool need". It changes the adapters behind the Protocols in `mcp/quoting/src/mcp_quoting/adapters/`, the env-driven selector `from_env`, Terraform's `quoting` variable, tool contracts, Entra roles and toolbox entries, with wire-format tests on recorded fixtures. It never calls a real system from unit, component or e2e tests, never lets the model pass field values, prices or `session_ref`, never bypasses the `quote_blocked` / `is_usable` gate, and never registers the toolbox or deploys unless the user asks for that step.
---

# Connect the insurer's quoting systems

This changes what sits behind mcp-quoting's adapter Protocols, not the tools: the model still sees the six `quoting.*`
tools of `contracts/tools.json`, and `create_quote` / `modify_quote` still price committed state only. Keep the one gate
(`tools.guard`), the one "priceable" rule (`quote_common.state.is_usable`), `stub` as the default for every offline
suite, and contracts first for any new tool or server.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Unit, component and e2e tests replay recorded responses (`httpx.MockTransport`) holding synthetic data only (`dev-only-...`, `example.test`). Real calls only in `azure`-level tests, run when the user asks.
- No adapter reads session state, and nothing prices a value `is_usable` refuses. Keep the stubs and the `stub` default.

## Read first

Paths are relative to the solution folder.

- `docs/toolbox.md` "Connect a new or real MCP server": checklists A (real systems) and B (another server).
- `mcp/quoting/README.md` "How to connect the real quoting system", "Environment variables", the error table.
- `mcp/quoting/src/mcp_quoting/adapters/base.py`: the Protocols (`RatingEngine`, `VehicleCatalog`, `AddressService`, `VinDecoder`, `StateReader`, `QuoteStore`) and `Offer`.
- `mcp/quoting/src/mcp_quoting/adapters/http.py`: `HttpApi` (base URL, timeout, retries, token hook), the `Http*` adapters and their default mappings.
- `mcp/quoting/src/mcp_quoting/adapters/__init__.py`: `Deps` and `from_env`, the one place implementations are chosen (`_choose`).
- `mcp/quoting/src/mcp_quoting/tools.py` (`guard`, `_price`, `HANDLERS`) and `state.py` (`missing_fields`, `snapshot`, `apply_options`, `input_hash`).
- `mcp/quoting/src/mcp_quoting/server.py`: tools served from `tools.json`, error results, `create_app` and its roles.
- `contracts/tools.json`: names, schemas, `result_mode`, `injected_args`.
- `mcp/quoting/tests/unit/test_http.py` and `tests/helpers.py` (`make_state`, `make_deps`): the test patterns.

## Steps

1. **Know the gate.** `create_quote` and `modify_quote` call `tools.guard`: verify `session_ref` (the session id comes
   from the token only), read state through the `StateReader`, refuse `quote_blocked` (a STOP or a rules error) first,
   then `missing_fields` (every `required_for_quote` key, per vehicle and driver, must pass `is_usable`). `_price` then
   gives the engine `apply_options(snapshot(state), options)`: `confirmed` values, and `inferred` ones at confidence
   ≥ 0.6 on non-deterministic fields. Pending values, inferred deterministic fields (claims, convictions, refusal,
   licence number, consent) and low-confidence chat values never reach it. If the insurer wants confirmed values only,
   change `QUOTABLE_MIN_CONFIDENCE` / `is_usable` in `packages/quote_common` with its tests (the BFF moves too), never
   a filter in an adapter.
2. **Get the facts from the user** before writing code: API spec and synthetic request/response samples per system,
   auth (Entra app id, so scope `api://<app-id>/.default`), network path, latency budget (NFR-PERF-5: `create_quote`
   under 3 s at p95), and whether the engine issues its own quote ids.
3. **Check the contract.** Compare the engine's required inputs with `required_for_quote` in
   `contracts/state-catalog.json`: a missing input is a new catalog field (skill `add-field-or-form`), never a special
   case in `state.py` or a mapping. Results must fit `Offer` (`offer_type` `essential|plus|premium`, `monthly`,
   `annual`, integer `deductible`, fr-CA `coverages`) and the tool's `output` in `tools.json`; otherwise change the
   contract first.
4. **Map the rating engine.** Write `to_payload(snapshot, offer_types) -> dict` and `to_offers(body) -> list[dict]`
   next to `_rating_payload` / `_rating_offers` in `http.py`, and pass them in `from_env`:
   `HttpRatingEngine(api("RATING"), to_payload=..., to_offers=...)`. Change path, method or headers in
   `HttpRatingEngine.price`. Coverage codes become fr-CA labels through a small table in the module (like `_OFFERS` in
   `stubs.py`). Not JSON over HTTP: a class with `async def price(self, snapshot, offer_types) -> list[Offer]`, added as
   an option of `_choose(env, "RATING_ENGINE", {...})`.
5. **Map the lookups.** `HttpVehicleCatalog(api("CATALOG"), to_names=..., to_type=...)`,
   `HttpAddressService(api("ADDRESS"), to_candidates=...)`, `HttpVinDecoder(api("VIN"), to_vehicle=...)`. Return
   canonical names (spelling matching stays in `tools.lookup_make_model_year`) and catalog enum values for
   `vehicle_type` and `province_code`. Keep the result shapes: the BFF relays them to forms (`FUNCTIONS` in
   `bff/src/quote_bff/service.py`).
6. **Quote ids and replicas** (only if needed). Implement `QuoteStore.latest` / `save` on a database (`save` assigns the
   id and `version = previous + 1` atomically; keep `input_hash` in the record) as a `_choose` option in `from_env`.
   Then raise `max` for `mcp-quoting` in `app_defs` (`infra/terraform/locals.tf`). An engine that issues ids is wrapped
   in the same two methods.
7. **Configure.** Locally: `RATING_ENGINE|VEHICLE_CATALOG|ADDRESS_SERVICE|VIN_DECODER=http`, `<PREFIX>_BASE_URL`,
   optional `<PREFIX>_SCOPE` (`RATING`, `CATALOG`, `ADDRESS`, `VIN`), `HTTP_TIMEOUT_SECONDS`, `HTTP_RETRIES`. In Azure:
   the Terraform variable `quoting` feeds `quoting_env` in `locals.tf`; a back end left out stays `stub`:

   ```hcl
   quoting = {
     backends             = { rating = { base_url = "https://<rating-api>", scope = "api://<rating-app-id>/.default" } }
     http_timeout_seconds = 2
     http_retries         = 1
   }
   ```

   With a scope, `entra_token` gets tokens as the mcp-quoting identity (`AZURE_CLIENT_ID`). The grant on the insurer's
   API is outside this repository. A fixed outbound IP: `enable_nat_gateway = true` (output `nat_public_ip`).
8. **Roles per tool.** `create_app` in `server.py` wraps the app with `wrap_from_env(app, default_roles={"Tools.Invoke",
   "Lookup.Read"}, tool_roles={"create_quote": {"Tools.Invoke"}, "modify_quote": {"Tools.Invoke"}})`. Toolbox calls
   arrive as the Foundry project identity (`Tools.Invoke`); the BFF holds `Lookup.Read` only. A tool that prices or
   writes goes in `tool_roles` with `{"Tools.Invoke"}`. App roles and grants: `local.apps`, `local.grants` in
   `infra/terraform/entra/main.tf`.
9. **Toolbox.** New adapters change no tool and no toolbox entry: release the image (skill `deploy-to-azure`). After a
   tool or server change, when the user asks for the toolbox step, show `uv run --project deploy python
   deploy/create_toolbox.py --dry-run --project-endpoint <project> --quoting-url https://<fqdn>/mcp --a2ui-url
   https://<fqdn>/mcp`; run `deploy/post_deploy.sh toolbox` (`--promote`) only on their go. Roll back: `--list`, `--pin <n>`.
10. **A new tool or MCP server, end to end.**
    - Contract: an entry in `contracts/tools.json` (`name` `<label>.<tool>`, `wire_name` `<label>__<tool>`, `server`,
      a one- or two-sentence `description`, `inputSchema`, `output`, `result_mode`, `injected_args`), copied to
      `../contracts/tools.json`. Update `TOOLBOX_TOOLS` (`deploy/check_contracts.py`), `EXPECTED_TOOLS`
      (`deploy/smoke_test.py`) and, for a server, `toolbox.servers` in `tools.json`.
    - Server: a handler named like the tool in `tools.py` and in `HANDLERS` (the server will not start without it). A
      new back end: Protocol in `base.py`, stub in `stubs.py`, adapter in `http.py`, a `Deps` field, a `_choose`. A new
      server: copy `create_app()` from `mcp/a2ui/src/mcp_a2ui/server.py`.
    - Injected arguments: optional on the server (as `tool_specs` adds `session_ref`), valued in `Turn.injected`
      (`agents/quote-agent/src/quote_agent/turn.py`), never in `inputSchema`.
    - Azure, new server only: `docs/toolbox.md` B steps 3 to 6 (`entra/main.tf`, `locals.tf`, `main.tf`
      `remote_tools`, `deploy-env.yml`, `build_version_body`, `post_deploy.sh`).
    - Prompt: name the tool where the agent should use it in `contracts/prompts/main-agent.md` (skill
      `edit-agent-prompts`). A form lookup the BFF calls also goes in `FUNCTIONS`.
    - Docs: `mcp/quoting/README.md` "Tools", `contracts/interfaces.md` §3, tool tables in `docs/toolbox.md`, `docs/hosted-agent.md`, `docs/patterns.md` §7.

## Tests to add or update

Test paths are under `mcp/quoting/` unless another component is named.

- **Wire format** (unit, per adapter enabled), in the style of `tests/unit/test_http.py`: recorded synthetic responses
  in a new `mcp/quoting/tests/fixtures/` folder; assert method, path, params, body and headers, and the mapped result;
  404, 429, 5xx and a timeout; every offer passes `Offer.model_validate`; a broken one raises `ValidationError`.
- **Selector**: `tests/unit/test_selector.py` for each new `_choose` option or variable. **Persistent store**: the
  semantics of `tests/unit/test_stubs.py::test_quote_store_numbers_versions_per_quote_and_session`.
- **Gate**: keep `tests/unit/test_tools.py` green (`test_a_pending_deterministic_field_blocks_the_quote`,
  `test_an_inferred_deterministic_field_is_refused_even_when_sure`, `test_the_block_comes_before_the_missing_fields`).
  Add one with `make_deps(rating=<your adapter over MockTransport>)` proving the request body holds no refused value.
- **Component**: `tests/component/test_server.py` through the server with your adapter (`upstream_error`, no values logged).
- **`azure` level** (none exists yet): a new `mcp/quoting/tests/azure/` with `pytestmark = [pytest.mark.azure,
  pytest.mark.skipif(os.environ.get("RUN_AZURE_TESTS") != "1", reason="real systems")]`, and the `azure` marker
  declared in `mcp/quoting/pyproject.toml`. Price `make_state({...})` variations on the insurer's test environment;
  `create_quote` p95 under 3 s over 50 calls.
- **New tool**: `test_list_tools_is_tools_json_plus_the_optional_session_ref`; quote-agent `tests/unit/test_prompts.py`
  (it pins 13 tools and the 30k budget), `tests/unit/test_events.py`, `tests/fake_mcp.py`; `deploy/tests/` counts.

## Verify

- `cd mcp/quoting && .venv/bin/python -m pytest && .venv/bin/ruff check .`: all pass.
- Tool or contract change: `uv run --project deploy --extra test python deploy/check_contracts.py` prints `contracts ok`;
  the `agents/quote-agent` and `deploy` suites pass. Terraform change: `init -backend=false`, then `validate`.
- `./test_all.sh`: `all suites passed` (the journeys still run on the stubs).
- Only when the user asks: `RUN_AZURE_TESTS=1 RATING_ENGINE=http RATING_BASE_URL=https://<test-api>
  RATING_SCOPE=api://<app-id>/.default .venv/bin/python -m pytest tests/azure`; after a release,
  `deploy/smoke_test.py --checks toolbox` lists 6 `quoting` and 4 `a2ui` tools.

## Done when

- [ ] Contract unchanged, or changed first (`tools.json`, its design copy, the tool counts).
- [ ] Every enabled adapter has its mapping and wire-format tests on synthetic recorded fixtures, error cases included.
- [ ] `stub` stays the default; the gate tests and `./test_all.sh` pass (or failures are reported as they are).
- [ ] Roles per tool (`tool_roles`, `infra/terraform/entra/main.tf`) and replicas (`QuoteStore`) decided; variables in
      `mcp/quoting/README.md`; the Terraform `quoting` value shown to the user with `<placeholders>`.

## Pitfalls

- The defaults `HTTP_TIMEOUT_SECONDS=5`, `HTTP_RETRIES=2` can exceed the 3 s budget. Lower them per environment.
- A crashing mapping is `internal_error`; an answer that breaks `Offer` is `upstream_error`. Never log arguments,
  state or back-end bodies, and never put back-end text in a result.
- `input_hash` is computed over the snapshot, not your payload: keep `to_payload` a pure function of the snapshot.
- The snapshot's `deductible` is the catalog's enum string; `Offer.deductible` is an integer. Lookups ignore
  `session_ref`; the VIN must be upper case and its check digit is verified before any call.
- Putting a lookup in `tool_roles` breaks the BFF's form lookups (403 `missing app role`): it holds `Lookup.Read` only.
- More than one replica with `InMemoryQuoteStore` answers `unknown_quote` to `modify_quote`.
- Toolbox wire names (dot or `__`) are **[UNVERIFIED]**: `tools.logical_name` accepts every spelling. Hard-code none.
- The agent treats a tool with no `tools.json` entry as `terminal` (the model never sees its result), and every new
  tool grows the static prefix: run the 30k test and deploy under a new `PROMPT_VERSION` (`post_deploy.sh` sets one per commit).
