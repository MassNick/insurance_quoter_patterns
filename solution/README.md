# Auto quote solution (Azure AI Foundry, Desjardins demo, Variation C)

The buildable solution for the design in `..` (the design folder). Status: **code and tests; nothing deployed.** No step in this folder calls Azure unless you run the deployment yourself.

## Layout

| Path | What it is |
|---|---|
| `settings.yaml` | The one file to change a model, the thinking level (`reasoning_effort`) or a structured output (`output_schema`): the Foundry model deployments (read by Terraform) and each agent's deployment and options (read by the deploy scripts). See `docs/hosted-agent.md` "Settings file". |
| `contracts/` | Copy of the design contracts plus `interfaces.md` (component boundaries, ports, HTTP API, invocation, MCP tool shapes). The single source for every schema. |
| `packages/quote_common/` | Shared Python package: contract loaders, catalog, JSON Pointer, event envelope and SSE, `session_ref` signing. |
| `mcp/a2ui/` | MCP server `mcp-a2ui`: FormSpec → A2UI 1.0 compiler. |
| `mcp/quoting/` | MCP server `mcp-quoting`: quoting tools behind adapter interfaces (stub engine included). |
| `bff/` | FastAPI BFF: session state owner, merge rules, double confirmations, end-of-form rules and hand-offs, SSE relay. |
| `agents/quote-agent/` | Hosted agent (Microsoft Agent Framework): main agent + memory agent, invocations protocol, fake model for tests. |
| `agents/document-agent/` | Foundry prompt agent definition (`gpt-5.6-luna`, set in `settings.yaml`) and its output schema. |
| `web/` | React + TypeScript frontend: A2UI renderer, insurance catalog, Variation C shell, phone layout. |
| `e2e/` | Full-stack end-to-end tests: `stack.py` starts the services locally (fake model), API journeys in pytest, browser journeys in `web/e2e-stack`, `docker-compose.yml` for CI. |
| `infra/terraform/` | Terraform for the Azure platform. `deploy/` holds the Foundry data-plane deployment (hosted agent, toolbox, knowledge base, document agent). |
| `../.github/workflows/` | Suggested CI/CD, including the visual review by an agent. At the repository root (the design folder); every job runs in `solution/`. |
| `docs/` | Style guide, architecture, patterns, CI/CD, Terraform, model and agent arguments, toolbox configuration. |
| `skills/` | Skills for the next agent: connect the quoting MCP tools and Foundry IQ, add rules, guardrails and fields, edit prompts, run the regression suites. |

## Engineering conventions

- **Keep the code light; reuse before you write.** Use `quote_common`, the framework or SDK feature, or an existing helper before adding code. One commit path in the BFF, one field shell and one binding hook in the renderer, one result builder per MCP server, maps and `for_each` in Terraform, one reusable CI job. No speculative abstractions, factories or unused options. When two pieces of code do the same thing, fold them together.
- **Python services** (BFF, agent, MCP servers): Python 3.13, one `uv` project per service with its own `.venv`, package code in `src/<package>/`, tests in `tests/unit` and `tests/component`. Install with `uv venv && uv pip install -e ../../packages/quote_common -e ".[test]"` (adjust the relative path). Lint with `ruff`.
- **Test levels**, the same names everywhere:
  - `unit`: one function or class, no I/O.
  - `component`: one service running in-process (FastAPI `TestClient`, MCP client over the ASGI app), every external dependency replaced by a fake.
  - `visual`: one visual element rendered alone in the browser and compared to a baseline screenshot (`web/`).
  - `e2e`: the whole stack running locally with the fake model; Playwright drives the browser (`web/e2e` against a mock BFF, `e2e/` against the real services).
  - `azure`: integration tests against a real deployment. Skipped unless `RUN_AZURE_TESTS=1`.
- **No network and no Azure** in `unit`, `component`, `visual` or `e2e`. Every Azure dependency sits behind an interface with an in-memory or fake implementation.
- **Configuration** of a running service comes from environment variables only, documented in each component's README and in `docs/`. Models and generation options are set once in `settings.yaml`, which Terraform and the deploy scripts read and the deploy writes into the service's environment. No secrets in the repository. Local and test values are obviously fake (`dev-only-...`).
- **Language**: customer-facing text is fr-CA and comes from the catalog (`label_fr`, `help_fr`, `enum_fr`) or the component's string table. Code, logs and the developer trace are English.
- **Ports**: see `contracts/interfaces.md`.
- **Brand**: the Desjardins mark in `web/` is a hand-drawn placeholder. Replace it with the official asset before any external showing (NFR-BRD-1).

## Running the tests

`./test_all.sh` runs every offline suite (`--quick` for unit, component and lint only; `--full` adds the full-stack
browser journeys). Each component's README says how to create its `.venv`; `docs/testing.md` explains the levels and
the regression routine.

## For the next agent

Start with `AGENTS.md`, then the skill for your task in `skills/`.
