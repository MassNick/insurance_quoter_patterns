# Testing and regression

How the solution is tested, how to run each suite, and what to run before you merge a change. Every suite below is
offline: no network, no Azure, no real model. Paths are relative to the solution folder.

## One command

```bash
./test_all.sh            # every Python component (ruff + pytest), web typecheck/lint/unit/visual/e2e, API journeys
./test_all.sh --quick    # Python unit + component + lint, web typecheck/lint/unit only (while iterating)
./test_all.sh --full     # everything, plus the full-stack browser journeys (before a hand-off)
```

It needs each component's `.venv` and `web/node_modules` (see below). It runs the same checks as the CI workflow
(`.github/workflows/ci.yml`) minus Terraform and containers; CI itself is described in [cicd.md](cicd.md).

## The five levels

The same names everywhere ([README](../README.md#engineering-conventions)):

| Level | What it tests | Where |
|---|---|---|
| `unit` | One function or class, no I/O | `tests/unit/` of each Python service (and `packages/quote_common/tests/`, `deploy/tests/`, `agents/document-agent/tests/`); `web/tests/unit/` |
| `component` | One service in-process (FastAPI `TestClient`, an MCP client over the ASGI app), every dependency faked | `tests/component/` of each Python service; the React component tests in `web/tests/unit/` |
| `visual` | One visual element rendered alone, compared with a baseline screenshot | `web/tests/visual/` |
| `e2e` | The whole stack locally with the fake model; Playwright drives the browser | `web/e2e/` (mock BFF), `e2e/` (API journeys), `web/e2e-stack/` (browser, real services) |
| `azure` | Integration against a real deployment, skipped unless `RUN_AZURE_TESTS=1` | None written yet (see [Not covered](#not-covered-or-not-verified)) |

mcp-a2ui also has `tests/golden/`: unit-level comparisons with the JavaScript reference compiler.

## Per component

Counts measured on 2026-11-25 (macOS arm64, Python 3.13.14, Node 26.5, Chromium 1248; CI uses Node 22). All passed.

| Component | Command (from the component folder) | Tests | Count |
|---|---|---|---|
| `packages/quote_common` | `.venv/bin/python -m pytest` | `tests/`: catalog, contract loaders, JSON Pointer, SSE round trip, `session_ref` sign/verify/expiry, `is_usable`, PII redaction in logs, the inbound Entra check | 17 |
| `mcp/a2ui` | `.venv/bin/python -m pytest` | `tests/unit/` 114: every widget, check, layout rule, prefill path, edit mode, validation. `tests/golden/` 122: Python versus JavaScript compiler. `tests/component/` 18: the server in-process and as a real process | 254 |
| `mcp/quoting` | `.venv/bin/python -m pytest` | `tests/unit/` 115: tools, `missing_fields`, `quote_blocked`, stubs, HTTP and Cosmos adapters, VIN. `tests/component/` 25: the server in-process and as a process | 140 |
| `bff` | `.venv/bin/python -m pytest` | `tests/unit/` 197, of which `tests/unit/rules/` 95 (the copied rules tests, plus 113 subtests): merge rules, consent gate, statuses, rule glue, D15 filter, FormSpecs, adapters. `tests/component/` 50: the API with a scripted `FakeHostedAgent`, fake MCP clients and in-memory stores | 247 |
| `agents/quote-agent` | `.venv/bin/python -m pytest` (or `-m unit`) | `tests/unit/` 53: prompt assembly, token budget, cache key, tool policy, event mapping, fake parsers, wiring (including `MEMORY_OUTPUT_SCHEMA`), deploy definition, zip and `settings.yaml` defaults. `tests/component/` 17: the invocations app with the fake model and in-process fake MCP servers, including the whole journey | 70 |
| `agents/document-agent` | `.venv/bin/python -m pytest` | `tests/test_definition.py`: the prompt agent definition, its strict output schema and the deploy script's `settings.yaml` defaults | 9 |
| `deploy` | `.venv/bin/python -m pytest` | `tests/`: the deployment scripts against fakes (contracts check, toolbox, knowledge base with its `settings.yaml` defaults, rollout, smoke test, visual review, `post_deploy.sh` dry run) | 70 |
| `web` unit + component | `npm test` | `tests/unit/` (Vitest, jsdom): SSE client 7, A2UI core 18, app state 22, shell 11, components 77 | 135 |
| `web` visual | `npm run test:visual` | `tests/visual/`: 64 elements at 1280 px, 28 of them also at 390 px | 92 |
| `web` e2e (mock BFF) | `npm run test:e2e` | `e2e/journey.spec.ts` 5, `e2e/phone.spec.ts` 1 | 6 |
| API journeys | `bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e` (from the solution folder) | `e2e/test_api_journey.py` | 7 |
| Browser, full stack | `cd web && npm run test:stack` | `web/e2e-stack/full-stack.spec.ts` | 3 |

Total: 807 Python tests, 233 web tests, 10 full-stack journeys.

Setup, once per component, from the solution folder (the CI does the same):

```bash
(cd packages/quote_common && uv venv && uv pip install -e ".[test]")
(cd bff && uv venv && uv pip install -e ../packages/quote_common -e ".[test]")
for d in mcp/a2ui mcp/quoting agents/quote-agent agents/document-agent; do
  (cd "$d" && uv venv && uv pip install -e ../../packages/quote_common -e ".[test]")
done
(cd deploy && uv venv && uv pip install -r pyproject.toml --extra test)
(cd web && npm ci)    # Playwright 1.64.1 uses Chromium 1248; otherwise npx playwright install chromium
```

Pick a level with the folder: `.venv/bin/python -m pytest tests/unit`, `tests/component`, `tests/golden`. Lint with
`uvx ruff check .` in each Python folder and `npm run typecheck && npm run lint` in `web/`.

## Golden fixtures

Three sets of tests are pinned to something outside the solution's own code.

**mcp-a2ui against the JavaScript compiler.** `mcp/a2ui/tests/golden/` holds copies of the design demo's compiler and
forms (`a2ui.js`, `forms.js`), 7 extra FormSpecs (`extra.json`), and `golden.json`: 40 FormSpecs with the JavaScript
output in French and English, under a frozen clock (2026-11-25). `test_golden.py`:

- with Node installed, regenerates the file into a temporary folder and fails if the committed `golden.json` is stale;
- requires the Python output to equal the JavaScript output for every form and language, after canonical JSON, with one
  intended difference (French repeater defaults);
- checks every output against the A2UI conformance rules.

After a change to the catalog, `extra.json` or the demo copies: `cd mcp/a2ui && node tests/golden/gen_golden.mjs`, then
run the tests. A difference is either a bug or a new deviation to document in the
[mcp-a2ui README](../mcp/a2ui/README.md#deviations-from-the-javascript-compiler).

**The web fixtures.** `web/fixtures/surfaces.json` (46 surfaces) is generated by `web/fixtures/gen.mjs`
(`npm run fixtures`): the 40 golden surfaces in French, plus the journey surfaces of `web/fixtures/journey-specs.mjs`
(vehicle from a document, two-driver board, offers, `end_of_form`, `full_form`, legacy matrix) compiled with the golden
folder's compiler, with mcp-a2ui's two output differences applied. The unit tests, the visual harness and the mock BFF
all use it. Order after a catalog or compiler change: regenerate `golden.json`, then `npm run fixtures`, then `npm test`
and `npm run test:visual`.

**The rules tests.** `bff/tests/unit/rules/test_end_of_form.py` is the design's `bff/rules/test_end_of_form.py` with
the imports changed to `quote_bff.rules` and the run line updated. The module under test,
`bff/src/quote_bff/rules/end_of_form.py`, is byte-identical to the design's. Check with
`cmp ../bff/rules/end_of_form.py bff/src/quote_bff/rules/end_of_form.py` (from the solution folder). `FixtureCoverageTest`
fails when a registered rule has no positive and negative fixture.

## Visual regression

**One element per screenshot.** `web/visual.html?el=<name>` renders exactly one element from the fixtures
(`web/src/visual/main.tsx`; the list is `web/src/visual/list.ts`, the scenes `web/src/visual/scenes.tsx`), freezes the
clock at 2026-11-25, then sets `body[data-ready="1"]`. `web/tests/visual/visual.spec.ts` screenshots the element's
target. Open `http://localhost:5173/visual.html` with `npm run dev` to browse them.

**Settings** (`web/playwright.visual.config.ts`): Chromium, `fr-CA`, `America/Toronto`, reduced motion, animations
disabled, caret hidden, device scale 1, `maxDiffPixelRatio` 0.002. Two projects: `desktop` at 1280 × 900 and `phone` at
390 × 844 (elements with `phone: true` in `list.ts`).

**Baselines** live per platform in `web/tests/visual/__screenshots__/<platform>/<element>-<project>.png`. Only
`darwin/` exists (92 PNG files, generated on macOS arm64). Fonts are the prototype's stack resolved by the system, so
another platform needs its own set.

**Change the look on purpose:**

1. Make the change and run `npm run test:visual`.
2. Open `web/playwright-report/visual` and look at each diff.
3. Update only the elements that changed on purpose: `npm run test:visual:update -- -g "<element> @"`. The test title is
   `<element> @desktop` (and `@phone`), so the trailing ` @` stops a bare name from also matching longer element names.
4. Review the new PNG files in the pull request and commit them with the change.

Never update every baseline to turn a red run green. A new element: add it to `list.ts` (with `phone: true` if it
matters on phones) and a scene to `scenes.tsx`, then generate its baseline. The rubric is
[style-guide.md](style-guide.md).

**Agent visual review.** On pull requests that touch `web/`, `.github/workflows/visual-review.yml` takes the screenshots
and asks a vision model (`deploy/visual_review.py`, or the Claude Code action) to compare them with the style guide and
comment. It is advisory and never blocks a merge. Setup and variables: [cicd.md](cicd.md).

## End to end

Three suites, all with the fake model and no Azure ([e2e/README.md](../e2e/README.md)):

| Suite | What runs | Command | Ports |
|---|---|---|---|
| Browser, mock BFF | The built web app; the BFF and agent are scripted (`web/fixtures/mock-bff.ts`) behind route interception (`web/e2e/mock-route.ts`). Fast; every UI path | `cd web && npm run test:e2e` | 5175 |
| API journeys | BFF, quote-agent (`QUOTE_AGENT_MODEL=fake`), mcp-quoting, mcp-a2ui as local processes, over HTTP + SSE | `bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e` | +20000 |
| Browser, full stack | The same four services, the built web app and Chromium | `cd web && npm run test:stack` | +30000 |

`e2e/stack.py` starts the services, each from its own `.venv`, refuses a busy port, and writes each service's log to
`e2e/.logs/<name>.log`. The two full-stack suites use different port offsets but share the log folder: run them one
after the other. In CI the browser suite runs in containers (`e2e/docker-compose.yml`).

Reuse the gestures: `start`, `consent`, `confirmTwice`, `send`, `suggestion`, `form` in `web/e2e/helpers.ts`; `Session`
(`say`, `submit`, `confirm`, `value`, `surface`, `consented`) in `e2e/test_api_journey.py`. Assert on what the customer or
the next system sees (text, forms, state values, events), not on timings.

### The fake model

`QUOTE_AGENT_MODEL=fake` swaps the two Foundry chat clients for deterministic ones in
`agents/quote-agent/src/quote_agent/fake/`. They carry the same framework layers as the real client, so the
orchestration, both middlewares and the tool loop run unchanged.

- `rules.py`: French parsing shared by both fakes. `extract(text, state, topic)` returns a schema-valid memory output;
  `ASK` maps each question the main agent asks to a marker, and `topic_of` finds the topic of the last assistant
  message (first match wins); `MAKES` lists the makes it recognizes; `KB` and `kb_search` are the fake knowledge base.
- `client.py`: `FakeMemoryClient` (calls `extract`) and `FakeMainClient`, whose `_Main.decide()` and `next_step()`
  follow the design demo's journey, one branch per step.

The fake does not read the prompt text: it tests the plumbing, not the model's behaviour.

**Teach it new wording.**

1. A new customer phrase that should set a value: a regex in `rules.extract` (respect the topic, so "oui" only sets what
   the last question asked). A new make: add it to `MAKES`.
2. A new question the agent asks: its marker in `ASK`, in the right order, so the next turn knows the topic.
3. A new reply or form: a branch in `_Main.decide()` or a step in `_Main.next_step()`; FormSpecs must validate
   (`test_every_fake_form_spec_is_valid`) and suggestions stay generic (`test_suggestions_are_generic_and_within_limits`).
4. Tests in the quote-agent component: parsing in `tests/unit/test_rules.py`, the journey in
   `tests/component/test_journey.py`. Then use the wording in `e2e/test_api_journey.py` or `web/e2e-stack/`.

**The mock BFF** (`web/fixtures/mock-bff.ts`, journeys in `web/fixtures/scenarios.ts`) is a second, scripted copy of
the BFF's visible behaviour for the web's unit, visual and e2e tests. When you change what the BFF sends (an event, a
deterministic field, a templated message), update it too, or the web suites keep testing the old behaviour.

## Before you merge

Run `./test_all.sh` for every change, and `--full` before a hand-off. In addition:

| Change | Also do |
|---|---|
| Prompt (`contracts/prompts/*.md`) | Copy the file into `agents/quote-agent/src/quote_agent/prompts/` (or `agents/document-agent/prompts/`). `deploy/check_contracts.py` (CI job `contracts`) compares the quote-agent copies with the contracts; the document-agent copy has no test, so check it with `cmp`. Deploy under a new `PROMPT_VERSION` (a runtime setting passed as `--prompt-version`; `post_deploy.sh` sets one per commit). Run the quote-agent suite (prompt assembly, the 30,000-token budget). The offline suites do not test the model's behaviour: plan a real-model check ([hosted-agent.md](hosted-agent.md)). |
| End-of-form rule | `cd bff && .venv/bin/python -m pytest tests/unit/rules`, then the BFF suite. Fixture pair, registry order test, a test class ([patterns.md](patterns.md#add-a-rule)). For an end-to-end journey, teach the fake the wording and add an API journey. Keep or note the design copy. |
| State field (catalog) | Every Python suite (`quote_common` pins the deterministic set; `bff`, `mcp-quoting`, `mcp-a2ui` read the catalog), the token budget in quote-agent, `node tests/golden/gen_golden.mjs` and `npm run fixtures` if a golden form uses it, `npm test`, `npm run test:visual`. The mock BFF's `DETERMINISTIC` set if the flag changed ([patterns.md](patterns.md#add-a-field)). |
| UI (web) | `npm run typecheck && npm run lint && npm test && npm run test:visual && npm run test:e2e`. Update only the baselines that changed on purpose; check the 390 px shots; read the visual review comment on the pull request. |
| MCP tool (`contracts/tools.json`, a server) | `uv run --project deploy --extra test python deploy/check_contracts.py` (tool counts the toolbox expects), the server's suite (it builds its tool list from `tools.json`), quote-agent (policy, events, token budget), the BFF if it calls the tool (`FUNCTIONS`, `TOOL_ARGS` in `service.py`), the `deploy` suite (toolbox and smoke test counts), the API journeys. |
| `packages/quote_common` | Every Python component: they all depend on it. |
| Events or the envelope (`contracts/events.md`) | BFF, quote-agent, web unit tests (`sse.test.ts`, `app-state.test.ts`), the mock BFF, both full-stack suites. |

## Not covered or not verified

- **The `azure` level.** No test uses it yet: the marker is declared in `bff/pyproject.toml`, and the
  [mcp-quoting README](../mcp/quoting/README.md) describes the integration tests to write against the real engine.
  After a deployment, `deploy/smoke_test.py` checks health, the toolbox tool list and one real turn
  ([deployment.md](deployment.md)).
- **The real models.** Every suite uses the fake model and the `FakeDocumentExtractor`. Prompt adherence, the 85 %
  two-call ratio, caching and the document agent's reading are not tested offline (quote-agent README, "Unverified").
- **Docker images** are not built by any local suite. CI builds them for the container e2e job
  (`e2e/docker-compose.yml`); the mcp-a2ui and quote-agent READMEs note they were not built here.
- **Linux visual baselines** are not committed. The CI `web` job runs `npm run test:visual` on Ubuntu, so it fails
  until someone runs `npm run test:visual:update` once in the CI image, reviews and commits `linux/`.
- **Browsers.** Every Playwright config uses Chromium only. No Firefox, WebKit or Safari; the phone is 390 px
  emulation, not a real device.
- **Accessibility.** Keyboard, focus and ARIA behaviour are covered by unit tests and the e2e journeys; there is no
  automated accessibility audit.
- **Integrations not wired**: the hand-off has no CRM or queue to test; the toolbox's wire names and A2UI pass-through
  and Foundry IQ's result shape are checked only after a deployment ([toolbox.md](toolbox.md)).
