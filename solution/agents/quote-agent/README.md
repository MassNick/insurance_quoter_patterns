# quote-agent (Foundry hosted agent)

The hosted agent behind the quote chat: a **main agent** (talks to the customer, calls tools) and a **memory agent** (extracts facts and signals as strict JSON), both on Microsoft Agent Framework, served over the Foundry **invocations** protocol (`POST /invocations` → SSE, `GET /readiness`, port 8088). The BFF is its only caller (`../../contracts/interfaces.md` §2). Nothing here calls Azure unless you deploy and configure it.

## How a turn runs

`app.py` validates the body (`turn.py: TurnRequest`, extra keys tolerated), creates a `Turn` and streams its envelopes. `agent.py: Runtime.run_turn`:

1. `warmup` → only `turn.completed` with `model_calls: 0` (no model call).
2. `turn.started`; the BFF's `system_notes` are appended to the history as system messages **before** the new input (history stays append-only).
3. The **memory agent** starts (task): input = last assistant message + the input (a one-line summary for non-message inputs, `prompts.input_summary`) + compact STATE; `response_format` = the contract schema named by `MEMORY_OUTPUT_SCHEMA` (default `memory-output.schema.json`), strict. Its output is validated against the same schema and emitted as `memory.result` as soon as it returns (`{error}` instead after a failure or a 4 s timeout; `guardrail` when `intent.off_topic`).
4. Button actions are **pre-executed**: `get_quote` → `quoting.create_quote`, `modify_quote` (`quote_id`, `changes`) → `quoting.modify_quote`; the result goes into INPUT (`PRE_EXECUTED`), so the turn stays at 2 calls.
5. The **main agent** runs streamed. Messages: static prefix (system prompt + 13 tools + compact key list) → history (framework history provider) → volatile context (STATE, PHASE, OPEN_SURFACES, SIGNALS, PREFERENCES, RULES; `VolatileContext` provider, never stored) → INPUT. `prompt_cache_key = quote-main:<PROMPT_VERSION>:<session>` (memory: `quote-memory:…`).
6. Text → `text.delta`, then `text.done` (+ `citations` from Foundry IQ; a one-sentence fallback from the FormSpec title when the model only called tools). `turn.completed {model_calls, usage {input, cached_input, output}, ttft_ms, duration_ms}`. Any failure → `error {code, message, retryable: true}`.

Two middlewares enforce the call budget (`turn.py`):

| Middleware | What it does |
|---|---|
| `tool_policy` (function middleware) | Adds each tool's `injected_args` from `tools.json`, overwriting anything the model sent: `session_ref` (`create_quote`, `modify_quote`, `render_form`, `render_offers`), `state` (`render_form`), and `version` + `offers` from the turn's last quote result (`render_offers`). Emits `tool.call` (model-facing args only, redacted with `quote_common.logs.redact`) and `tool.result`. **Terminal** tools: result to the UI, a stub to the model, then `MiddlewareTermination` ends the loop (no follow-up completion) unless a narrate tool is in the same batch. **Narrate** tools (`create_quote`, `modify_quote`, `knowledge_base_retrieve`): result back to the model for one more completion; a narrate call in a later batch is refused (hard cap 3 calls). |
| `meter` (chat middleware) | Counts model calls and usage (incl. `cache_read_input_token_count`), records each completion's tool-call batch, and removes the injected args from the tool schemas the model sees. |

`Turn.emit` is the only envelope constructor (`quote_common.Envelope`, `sse_encode`); `Turn.run_tool` maps every tool result to events: `a2ui.message` (only `EmbeddedResource`s with `audience: ["user"]`; `form_spec` as a top-level envelope field next to `data` on the `createSurface` from `a2ui.render_form`; the model sees `{surface_id, status}`), `quote.created` / `quote.updated` (`quote_id, version, offers, previous_version, delta_monthly, input_hash`; not for a refusal), `suggestions`, `phase`, KB citations, lookup results (`tool.result.data.result`, model gets `{"status":"sent_to_ui"}`). An MCP `isError` result (for example a `missing_fields` refusal) is shown to the model so it can ask for what is missing.

## Files

| Path | What |
|---|---|
| `main.py` | Entry point (`python main.py`): shared JSON logging, then the host. |
| `src/quote_agent/app.py` | `InvocationAgentServerHost` + invoke handler. |
| `src/quote_agent/agent.py` | The two agents, history provider, `VolatileContext`, `Runtime.run_turn`. |
| `src/quote_agent/turn.py` | Request model, `Turn` (events), `tool_policy`, `meter`. |
| `src/quote_agent/tools.py` | `build_tools` (one function for Azure and local), result modes, logical names, MCP result parser. |
| `src/quote_agent/prompts.py` + `prompts/` | Prompt copies, static prefix, key list, context/input rendering, cache key, token budget. |
| `src/quote_agent/config.py` | Settings from the environment. |
| `src/quote_agent/fake/` | `QUOTE_AGENT_MODEL=fake`: `rules.py` (French parsing shared by both fakes) and `client.py` (fake memory and main chat clients). |
| `agent.yaml`, `deploy/deploy_quote_agent.py`, `requirements.txt` | Source-zip deploy. |
| `Dockerfile` (+ `Dockerfile.dockerignore`) | Local and e2e image. |

## Microsoft Agent Framework: versions and APIs used

Pinned in `pyproject.toml` (and `requirements.txt` for the deploy): `agent-framework-core 1.26.0`, `agent-framework-foundry 1.16.2`, `agent-framework-foundry-hosting 1.0.0b261112` (pre-release), `agent-framework-azure-cosmos 1.0.0b261112` (pre-release), `azure-ai-agentserver-invocations 1.3.0` (with `azure-ai-agentserver-core 2.3`), `mcp 2.3`, and for the deploy `azure-ai-projects 2.11`. APIs, all read in the installed source:

- `Agent(client, instructions, tools, middleware, context_providers, default_options, compaction_strategy, tokenizer)`, `Agent.run(..., session=AgentSession, stream=True, options={...})`.
- `@function_middleware` / `FunctionInvocationContext` / `MiddlewareTermination`; `@chat_middleware` / `ChatContext.stream_result_transforms`.
- `MCPStreamableHTTPTool(name, url | mcp_client, tool_name_prefix, parse_tool_results, allowed_tools, http_client, approval_mode)`; `FoundryToolbox(credential, url, name)` for the toolbox (Entra bearer + toolbox feature header).
- `FunctionTool(name, description, func, input_model=<JSON schema from tools.json>)` for `ui.*` and the local fake KB.
- `InMemoryHistoryProvider` (local), `CosmosHistoryProvider(endpoint, database_name, container_name, credential)` (Azure; partition key `/session_id`), `ContextProvider.before_run` + `SessionContext.extend_messages`.
- Structured output: `response_format={"type": "json_schema", "json_schema": {name, schema, strict: true}}`.
- `FoundryChatClient(project_endpoint, model, credential)`; options `prompt_cache_key`, `reasoning {effort}`, `temperature`, `max_tokens`, `store: false`.
- Fakes: `BaseChatClient` composed with `FunctionInvocationLayer`, `ChatMiddlewareLayer`, `ChatTelemetryLayer` (same layers as `FoundryChatClient`).
- History compaction: `TokenBudgetComposedStrategy` + `ToolResultCompactionStrategy` + `CharacterEstimatorTokenizer` (12k history tokens + static prefix; in-run only, stored history is never rewritten).
- Host: `azure.ai.agentserver.invocations.InvocationAgentServerHost` (`invoke_handler`, `shutdown_handler`, `/readiness`, `PORT` default 8088, OTel/App Insights via `APPLICATIONINSIGHTS_CONNECTION_STRING`).

### Differences from the design docs

| Design says | Installed version / what we did |
|---|---|
| `agent_framework_foundry_hosting.InvocationsHostServer` | It exists but hosts one agent and streams only `delta`/`done`/`error` text frames. We use the framework-agnostic `InvocationAgentServerHost` (the design's fallback) with our own handler, which can stream the custom envelopes. |
| Middleware ends the loop "when the last parallel call finishes" (`is_last_call_in_batch`) | No such API. In 1.26 parallel calls run concurrently; each `MiddlewareTermination` is caught per call and the loop stops if any call terminated, after **all** calls in the batch ran. To keep the loop going for narrate tools, `meter` records the batch from the finished completion and `tool_policy` terminates only when the batch has no narrate tool. |
| Injected args (`session_ref`, `state`, `version`, `offers`) hidden from the model | Done by `meter` editing each MCP `FunctionTool.parameters()` dict (cached by the framework); the MCP layer still forwards them because the server declares them. |
| Wire names `quoting__identify_address` | `tool_name_prefix="quoting"` gives `quoting_identify_address` (the framework strips leading `_`); the toolbox serves `quoting___identify_address` (verified on the dev deploy, 2026-11-25). `tools.logical_name` accepts every spelling. |
| A2UI resource read from the MCP result | The default MCP parser flattens an `EmbeddedResource` to text and drops its MIME type and audience, so we pass `parse_tool_results=parse_mcp_result`. MCP `isError` results arrive as `ToolExecutionException` carrying the tool text. |
| `build_context_message(...)` as run input; "developer" message | The volatile context is a `ContextProvider` message (sent, not stored). Framework roles are `system/user/assistant/tool`, so the context and system notes use `system`. |
| `KB_MCP_URL` | `FOUNDRY_IQ_MCP_URL` (+ `FOUNDRY_IQ_TOKEN_SCOPE`). |
| `create_version_from_code(code=("quote-agent.zip", bytes, ...))`, `rai_config=...` | `code` is a binary file object whose name ends in `.zip`; `HostedAgentDefinition` has no `rai_config` in `azure-ai-projects` 2.11, so the guardrail must sit on the model deployments (infra). |

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `QUOTE_AGENT_MODEL` | `azure` | `azure` (Foundry models) or `fake` (deterministic clients, no model). |
| `FOUNDRY_PROJECT_ENDPOINT` | — | Foundry project endpoint (required for `azure`). |
| `MAIN_MODEL_DEPLOYMENT`, `MEMORY_MODEL_DEPLOYMENT` | — | Model deployment names (required for `azure`). Never hard-coded: the deploy script sets them from `../../settings.yaml` (`agents.main.deployment`, `agents.memory.deployment`). |
| `MAIN_REASONING_EFFORT`, `MAIN_TEMPERATURE`, `MAIN_MAX_OUTPUT_TOKENS` | unset | Main agent generation options (unset = model default). The deploy script fills `MAIN_REASONING_EFFORT` (the thinking level) and `MAIN_MAX_OUTPUT_TOKENS` from `settings.yaml`. |
| `MEMORY_REASONING_EFFORT`, `MEMORY_TEMPERATURE`, `MEMORY_MAX_OUTPUT_TOKENS` | unset | Memory agent generation options, filled from `settings.yaml` like the main agent's. |
| `MEMORY_TIMEOUT_SECONDS` | `4` | After this the turn goes on without memory output (`memory.result {error}`). |
| `MEMORY_OUTPUT_SCHEMA` | `memory-output.schema.json` | The memory agent's structured output: a file name in `contracts/`. It is both the strict `response_format` and the schema the output is validated against. The BFF still validates `memory.result` against `memory-output.schema.json`, so another file must keep that shape or the BFF changes with it. |
| `PROMPT_VERSION` | `v1` | Part of `prompt_cache_key`. Set at deploy by `--prompt-version` (`deploy/post_deploy.sh` uses `main:<short git sha>`), so each release starts new cache keys. |
| `TOOLBOX_MCP_URL` | unset | Azure: toolbox consumer endpoint (`quoting.*`, `a2ui.*`). When set, the direct MCP URLs below are ignored. |
| `MCP_QUOTING_URL`, `MCP_A2UI_URL` | `http://localhost:8101/mcp`, `http://localhost:8102/mcp` | Local: direct MCP servers (namespaced client-side). |
| `FOUNDRY_IQ_MCP_URL` | unset | Azure: Foundry IQ knowledge base MCP endpoint (`knowledge_base_retrieve`). Unset → local fake knowledge base. |
| `FOUNDRY_IQ_TOKEN_SCOPE` | `https://search.azure.com/.default` | Entra scope for Foundry IQ. |
| `COSMOS_ENDPOINT`, `COSMOS_DATABASE`, `COSMOS_CONTAINER` | unset, `quoting`, `agent_history` | Azure history store. Unset endpoint → in-memory history. |
| `PORT` | `8088` | Read by the agent server host. |
| `LOG_LEVEL` | `INFO` | `main.py` calls `quote_common.logs.setup(LOG_LEVEL)` (JSON on stderr, PII redacted) before the host starts, so the host keeps its own OTel export and skips its plain console handler. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | injected by Foundry | Traces and logs. |
| `ENABLE_SENSITIVE_DATA`, `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | set `false` in deploy and image | Content recording off (architecture §13). |
| `QUOTE_CONTRACTS_DIR` | found by walking up | Contracts folder (see `quote_common.contracts`). |

The runtime reads these variables only, never `settings.yaml`: the hosted agent gets its environment from the deploy script, which takes the defaults of the model, thinking level, token limits, timeout and output schema from that file ([`../../docs/hosted-agent.md`](../../docs/hosted-agent.md) "Settings file").

Azure auth uses `azure.identity.aio.DefaultAzureCredential` (the agent identity in Foundry); no secret anywhere.

## Run locally (fake model)

```bash
cd agents/quote-agent
uv venv && uv pip install -e ../../packages/quote_common -e ".[test]"
# mcp-quoting on :8101 and mcp-a2ui on :8102 (see ../../mcp/*/README.md), then:
QUOTE_AGENT_MODEL=fake .venv/bin/python main.py          # serves :8088
curl -s localhost:8088/readiness
curl -sN -X POST 'localhost:8088/invocations?agent_session_id=s_dev1' -H 'content-type: application/json' \
  -H 'x-client-session-id: s_dev1' \
  -d '{"turn":1,"input":{"kind":"message","text":"Bonjour, je veux une soumission auto"},"phase":"intent","session_ref":"dev-only-session-ref"}'
```

Docker (from the solution root): `docker build -f agents/quote-agent/Dockerfile -t quote-agent .`, then `docker run --rm -p 8088:8088 -e MCP_QUOTING_URL=http://host.docker.internal:8101/mcp -e MCP_A2UI_URL=http://host.docker.internal:8102/mcp quote-agent` (non-root user, `QUOTE_AGENT_MODEL=fake` by default).

**Fake journey** (what the e2e suite can type; `tests/component/test_journey.py` runs it): "Bonjour, je veux une soumission auto" → consent question → "Oui, j’accepte" → (BFF double confirmation, `confirmation_result` with `/quote/contact_consent`) → document offer with the one-time "par écrit ou formulaire" note → "Je ne les ai pas" → name → "Marie Tremblay" → `a_propos` form (date of birth) → email → "Une Toyota RAV4 2021" → `vehicule_0` form (prefilled) → "Qui conduit la RAV4?" → "Moi et ma conjointe Julie, 45 ans" → "Non, c’est tout" → second-vehicle question → "Non, un seul véhicule" → `qui_conduit_quoi` assignment board → `antecedents` form → "Je calcule votre soumission?" with the `get_quote` action suggestion → offers (`a2ui.render_offers`). Also: "C’est quoi la franchise?" (Foundry IQ path, 3 calls, citations), "Raconte-moi une blague" (off-topic guardrail), "Je préfère répondre par écrit" (text mode: questions in chat), "Je veux assurer ma Lamborghini Urus 2023" (extracted for the BFF's STOP rule).

## Tests

```bash
cd agents/quote-agent
.venv/bin/python -m pytest              # unit + component, no network
.venv/bin/python -m pytest -m unit      # one level only
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

- `unit`: prompt assembly order and cache key, the 30k token budget (and that padding fails it), memory output schema validity, the French parsers, `tool_policy`/`meter` semantics with a scripted chat client (terminal ends the loop, one narrate round, second narrate refused, injected args override the model and are hidden), event mapping and redaction, FormSpecs and suggestions against the contracts, Azure vs local wiring (including `MEMORY_OUTPUT_SCHEMA`), the deploy definition and zip, and the deploy script's `settings.yaml` defaults (a temporary YAML file, a flag or an environment variable wins).
- `component`: the invocations ASGI app in-process with the fake model and in-process fake MCP servers (`tests/fake_mcp.py`): event sequence with 2 model calls, `render_form` with `form_spec` and the stub in history, warmup with 0 calls, a KB question with 3 calls and citations, system notes before the input, errors as `error` events, a quote refusal, `modify_quote`, and the whole journey.

## Change things

- **Prompts**: edit `../../contracts/prompts/*.md`, copy them into `src/quote_agent/prompts/`, run the tests (the token-budget test fails at 30,000 tokens), and deploy with a new `--prompt-version` (`deploy/post_deploy.sh` sets `main:<short git sha>` itself). `PROMPT_VERSION` is a runtime setting, not a constant in code.
- **Tools**: tools come from the MCP servers (or the toolbox) at run time; result modes and the 13 definitions come from `../../contracts/tools.json`. A new tool needs its `result_mode` there; a new local tool is one `_local(...)` line in `tools.build_tools`. Events for a tool live in `Turn._map_result`.
- **Models, thinking level, limits, output schema**: edit `../../settings.yaml` (`deployments`, `agents.main`, `agents.memory`), then deploy; or override one value with a deploy flag or the environment variable of the same name. No code change.

## Deploy (not run here)

`deploy/deploy_quote_agent.py` renders `agent.yaml`, builds a flat zip (`main.py`, `requirements.txt`, `quote_agent/`, `quote_common/`, `contracts/`) and calls `AIProjectClient.agents.create_version_from_code` with a `HostedAgentDefinition` (`python_3_13`, entry point `python main.py`, `remote_build`, 1 vCPU / 2 GiB, invocations protocol, environment variables without secrets). `--dry-run` prints the definition (every resolved value, and the settings file used) and builds the zip only.

For the options `settings.yaml` can fill (the deployments, reasoning efforts, token limits, timeout and output schema) the precedence is: **flag, then the environment variable of the same name** (`MAIN_REASONING_EFFORT`, ...), **then `settings.yaml`, then the default below**.

| Option | Default | Meaning |
|---|---|---|
| `--settings` | `../../settings.yaml` (the solution root) | Settings file read for the defaults below. |
| `--project-endpoint` | required | Foundry project endpoint (also set as `FOUNDRY_PROJECT_ENDPOINT`). |
| `--main-model-deployment`, `--memory-model-deployment` | `agents.main.deployment`, `agents.memory.deployment` (required if the file has none) | Model deployments. |
| `--main-reasoning-effort`, `--main-max-output-tokens` | `agents.main.reasoning_effort`, `agents.main.max_output_tokens`, else empty | Main agent thinking level and limit (empty = not set). |
| `--memory-reasoning-effort`, `--memory-max-output-tokens` | the same two keys under `agents.memory`, else empty | Memory agent thinking level and limit. |
| `--memory-timeout-seconds` | `agents.memory.timeout_seconds`, else `4` | `MEMORY_TIMEOUT_SECONDS`. |
| `--memory-output-schema` | `agents.memory.output_schema`, else `memory-output.schema.json` | `MEMORY_OUTPUT_SCHEMA`: a file in `contracts/` (the script refuses a name that is not one). |
| `--main-temperature`, `--memory-temperature` | empty | Not in `settings.yaml`; empty = not set. |
| `--prompt-version` | `v1` | `PROMPT_VERSION`; `deploy/post_deploy.sh` passes `main:<short git sha>`. |
| `--toolbox-mcp-url` | empty | `TOOLBOX_MCP_URL`, `{project}/toolboxes/auto-quoting-toolbox/mcp?api-version=v1`. |
| `--foundry-iq-mcp-url`, `--foundry-iq-token-scope` | empty, `https://search.azure.com/.default` | Foundry IQ endpoint and scope. |
| `--cosmos-endpoint`, `--cosmos-database`, `--cosmos-container` | empty, `quoting`, `agent_history` | History store. |
| `--cpu`, `--memory` | `1`, `2Gi` | Size per session. |
| `--protocol-version` | `2.0.0` | Invocations protocol version **[UNVERIFIED]** (spike S2). |
| `--agent-name` | `quote-agent` | Foundry agent name. |
| `--description` | from `agent.yaml` | Version description. |
| `--zip-out` | `dist/quote-agent.zip` | Where the zip is written. |
| `--wait` | off | Poll `get_version` until `active` or `failed` (exit 1 on failed). |
| `--dry-run` | off | No Azure call. |

After deploy (Phase 6.5): grant the agent identity Cosmos DB Built-in Data Contributor on `agent_history`, Search Index Data Reader on the search service, and the toolbox-calling role.

## Unverified

- Real-model behaviour: text and tool calls in one completion, prompt adherence, the 85 % two-call ratio. Only the fake path is tested.
- `prompt_cache_key` reaching the Foundry deployment and `cached_tokens` being reported (S6). The option is a first-class `FoundryChatOptions` key.
- Toolbox wire names and `EmbeddedResource` pass-through through the toolbox (S3, R11); `FoundryToolbox` with an async credential.
- Foundry IQ result shape (citations are taken from any `{title, url|ref_id|id}` objects) and its token scope (S8).
- Strict-mode acceptance of `memory-output.schema.json` (`value` allows untyped `array`/`object`; `pattern`, `maxItems`).
- `CosmosHistoryProvider` with the agent identity (it calls `create_container_if_not_exists`; data-plane RBAC cannot create containers, so the container must exist) (S5).
- That `x-client-session-id` reaches the container through the Foundry gateway; without it the session is `agent_session_id`.
- Invocations protocol version string; the deploy script and the Docker image were not run or built here (`--dry-run` and the zip were).
- Token counts use the framework's 4-characters-per-token estimator (4,464 for the static prefix, measured on 2026-11-25 with `prompts.static_prefix_tokens()`), not the deployed tokenizer.
- Not implemented: custom OTel span attributes (`session_id`, `phase`, `result_mode`, `model_calls` are in the envelopes and in the framework's GenAI spans), and the `x-consent` header to `mcp-a2ui` from the implementation plan.

## Requests to other components

- **BFF**: send `x-client-session-id` (the history partition key). Input fields we read: `form_submitted {surface_id, changed_paths}`, `action {action_id, quote_id?, changes?}` (`get_quote`, `modify_quote`, `rule_correct`), `document_extracted {purpose, changed_paths}`, `confirmation_result {accepted, changed_paths | paths}`. Lookup results from a model call are in `tool.result.data.result`. `memory.result` carries `{error: {code, message}}` when the memory agent failed (ignored by the BFF as agreed).
- **mcp-a2ui**: every tool declares its injected args (`session_ref`, `state`, `mode`, `version`, `offers`) as optional in its MCP `inputSchema`, otherwise the MCP client does not forward them; the A2UI messages come as an `EmbeddedResource` with `annotations.audience: ["user"]` and `mimeType: application/a2ui+json`, the model summary as JSON text `{surface_id, status}`.
- **Contracts**: `tools.json` wire names use `__`, which the framework prefix cannot produce (handled by `logical_name`). The fake memory agent writes `add_entity` as `/vehicles/N/<field>`; the schema accepts it and `/vehicles/N`.
- **Infra**: create the Cosmos container `agent_history` (partition key `/session_id`) ahead of the deploy; attach the guardrail to the model deployments (no `rai_config` on the hosted agent definition); inject `APPLICATIONINSIGHTS_CONNECTION_STRING`.
