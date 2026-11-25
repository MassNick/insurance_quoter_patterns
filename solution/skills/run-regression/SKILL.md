---
name: run-regression
description: Use when asked to run the tests or check a change end to end, for example "run the regression", "run everything", "is it green", "why is CI red", "a visual test fails", "update the screenshots", "we need Linux baselines", "run the visual review". It runs `./test_all.sh` in the right mode (`--quick`, default, `--full`) or a narrower suite, reads failures from the suite output, the service logs in `e2e/.logs/` and the Playwright results and traces, updates screenshot baselines one element at a time and only on purpose, and runs the visual review by an agent offline against `docs/style-guide.md`. It never updates every baseline to turn a run green, never skips or loosens a test to pass, never calls the review model or Azure unless the user asks, and never commits.
---

# Run the regression

`./test_all.sh` runs every offline suite, the same checks as the CI workflow minus Terraform and containers: no network,
no Azure, the fake model. This skill runs it, reads what failed, and handles the two things that need judgement:
screenshot baselines (per platform, updated only for intended changes) and the advisory visual review against the style
guide. Details live in `docs/testing.md` and `docs/cicd.md`. Paths are relative to the solution folder.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Never skip, mark xfail, loosen or delete a test to get a green run without the user's agreement.
- `deploy/visual_review.py --print-prompt` and `--dry-run` send nothing and count as offline; a real review run does not.
- Run the API journeys and `npm run test:stack` one after the other: they share `e2e/.logs/`.

## Read first

- `docs/testing.md`: the five levels, per-component commands, golden fixtures, visual regression, end to end, the fake
  model, "Before you merge" and "Not covered".
- `test_all.sh` (what each mode runs) and `.github/workflows/ci.yml` (the same checks in CI).
- `docs/cicd.md` "Visual review by an agent" and "Linux baselines"; `.github/workflows/visual-review.yml`.
- `docs/style-guide.md` (the review rubric) and `deploy/visual_review.py` (prompt, image selection, output format).
- `e2e/README.md` (stack, ports, logs) and `web/README.md` "Tests" and "Visual baselines".

## Steps

1. **Check the setup.** Every Python component needs its `.venv` and `web/` its `node_modules`; the commands are in
   `docs/testing.md` "Per component". Playwright 1.64.1 uses Chromium 1248 (`npx playwright install chromium` otherwise).
2. **Pick the mode.**

   | Command | Runs | When |
   |---|---|---|
   | `./test_all.sh --quick` | ruff and pytest of the 7 Python components (unit, component, golden), web typecheck, lint, unit | while iterating |
   | `./test_all.sh` | the above, web visual, web e2e with the mock BFF, API journeys on ports +20000 | before you say done |
   | `./test_all.sh --full` | the above, full-stack browser journeys on ports +30000 | before a hand-off |

   Narrower: `cd <component> && .venv/bin/python -m pytest tests/unit -q -k <name>`; `cd web && npx vitest run
   tests/unit/<file>`; `npx playwright test -c playwright.config.ts -g "<title>"`; API journeys alone:
   `bff/.venv/bin/python -m pytest -c e2e/pytest.ini e2e -k <name>`.
3. **Know what each suite proves.** Python unit and component: contracts, merge rules, consent gate, double
   confirmation, rules, tool policy, MCP tools, each service in-process with fakes. mcp-a2ui golden: the Python compiler
   equals the JavaScript reference. `deploy`: contracts check, packaged prompts identical, deployment scripts against
   fakes. Web unit: SSE client, A2UI renderer, app state, shell. Web visual: one element per screenshot against the
   committed baseline. Web e2e: every UI path against the scripted mock BFF. API journeys: the four real services with
   the fake model over HTTP and SSE. Full stack: the same plus the built web app in Chromium. None proves real-model
   behaviour, Azure, Docker images or Linux pixels (`docs/testing.md` "Not covered").
4. **Read a failure.** The run ends with `FAILED (<n>)` and the labels; search the output for `== <label>`, use the
   triage table below, then:
   - pytest: rerun the one test with `-x -q -k <name>`.
   - API journeys and full stack: `e2e/.logs/<bff|agent|quoting|a2ui|web>.log`, uvicorn lines and the services' JSON
     lines with PII redacted (`grep -nE '"level": "(ERROR|WARNING)"|Traceback' e2e/.logs/*.log`); follow the session id.
   - Web visual: `web/test-results/visual/<test>/` holds `*-actual.png`, `*-expected.png`, `*-diff.png`;
     `cd web && npx playwright show-report playwright-report/visual` shows them side by side.
   - Web e2e: traces of failures in `web/test-results/e2e/<test>/trace.zip` (`npx playwright show-trace <file>`), report
     in `web/playwright-report/e2e`. Full stack: `e2e/test-results/` and `e2e/playwright-report/`.
5. **Update baselines on purpose.** Baselines are per platform: `web/tests/visual/__screenshots__/<platform>/<element>-<project>.png`;
   macOS writes `darwin/` (the only set committed today). For each element whose diff is intended:
   `cd web && npm run test:visual:update -- -g "<element> @"` (add `--project=desktop` or `--project=phone` for one
   size; keep the trailing ` @`, and no `^`: the matched title starts with the project). Open the new PNGs and check
   the 390 px shots. A new element needs its entry in `src/visual/list.ts` and a scene in `src/visual/scenes.tsx`
   first; a removed element, its PNGs deleted. Never run the update without `-g` to clear a red run.
6. **Linux baselines** come only from the CI environment of the `web` job: `ubuntu-24.04`, Node 22,
   `npx playwright install --with-deps chromium`, then `npm run test:visual:update` in `web/`, with
   `web/tests/visual/__screenshots__/linux/` saved as an artifact, reviewed and committed. That needs a CI run on a
   branch or an ubuntu-24.04 machine: ask the user. A local Docker container is not the CI image (system fonts differ);
   never present its output as the CI set. Until `linux/` exists, the CI `web` job fails on every visual test.
7. **Visual review by an agent.** In CI, `visual-review.yml` takes the screenshots on pull requests that touch `web/`
   and posts one advisory comment (`azure-openai` through `deploy/visual_review.py`, or the Claude Code action with
   the same prompt); it never blocks a merge. Locally, offline:
   - Be the reviewer: `deploy/.venv/bin/python deploy/visual_review.py --print-prompt` (from the solution folder)
     gives the prompt; read `docs/style-guide.md`; look at the `*-actual.png` of failed tests in `web/test-results/visual/`,
     else the baselines of the elements you changed, at most 12. Write the result in the script's format: a first line
     `<!-- visual-review -->`, a summary, then a table of severity (`fail`, `warn`, `info`), area (`brand_tokens`,
     `grid_alignment`, `french_copy`, `contrast`, `phone_layout`, `other`), screenshot, finding, suggestion.
     Report it to the user; post nothing.
   - Check the request the script would send: `deploy/.venv/bin/python deploy/visual_review.py --dry-run --images
     web/test-results/visual --endpoint 'https://<review-endpoint>' --model '<vision-deployment>'` (images omitted).
   - A real run needs `VISUAL_REVIEW_ENDPOINT`, `VISUAL_REVIEW_MODEL` and an Entra sign-in with Cognitive Services
     OpenAI User on that resource; it calls Azure, so only when the user asks. It writes `visual-review.md`.
8. **Report** the mode, every label as printed (`ok:` or `FAILED:`), what did not run (`--full`, Terraform,
   containers, the `azure` level), the baselines you updated and why, and the review findings.

### Triage table

| Symptom | Likely cause | Look at |
|---|---|---|
| `<dir>: ruff` fails | lint in the changed files | `uvx ruff check <dir>` |
| `FixtureCoverageTest` | a rule without a fixture pair | `bff/tests/unit/rules/test_end_of_form.py` `FIXTURES` |
| `test_key_list_has_every_catalog_key...` | a catalog field added or removed | the count in `agents/quote-agent/tests/unit/test_prompts.py` |
| `test_token_budget_under_30k_and_counts_tools` | prompt, tool or FormSpec schema grew; tool count not 13 | `prompts.static_prefix_tokens()`, `contracts/tools.json` |
| `deploy: pytest`, `test_the_real_contracts_pass` | a packaged prompt not copied, or `quoting`/`a2ui` tool counts moved | `deploy/check_contracts.py` output |
| mcp-a2ui golden stale or unequal | catalog or demo copies changed; a compiler bug or new deviation | `node tests/golden/gen_golden.mjs`, mcp-a2ui README deviations |
| web unit test on a fixture surface | `web/fixtures/surfaces.json` stale | `npm run fixtures` |
| one visual element differs | an intended or accidental style change | the `*-diff.png`, then step 5 |
| every visual test fails on Linux | no `linux/` baselines | step 6 |
| web e2e fails after a BFF change | the mock BFF still sends the old events | `web/fixtures/mock-bff.ts`, `scenarios.ts` |
| API journeys: stack does not start, "busy" port | an earlier stack still running | `e2e/.logs/*.log`; stop it, or set `E2E_PORT_OFFSET` |
| API journey assertion on text | fake wording or `ASK` order changed | `agents/quote-agent/src/quote_agent/fake/` |
| BFF chat-path reply test fails | the hold-until-memory logic changed | `Bff.agent_turn`, `test_a_reply_that_streams_before_the_memory_result_waits_for_it` |
| quote-agent call-count test fails | a tool's `result_mode` or a new model call | `turn.tool_policy`, `test_invocations.py` |
| mcp-quoting `missing_fields` tests | a new `required_for_quote` field | `BASE` in `mcp/quoting/tests/helpers.py` |
| full stack fails, API journeys pass | web build, proxy or UI wording | `e2e/.logs/web.log`, `e2e/test-results/` trace |

## Tests to add or update

None by default. A fix you make while triaging gets the test of its level (see the skill for that change); an intended
visual change gets only its own updated baselines.

## Verify

`./test_all.sh` (or `--full` before a hand-off) ends with `all suites passed`; otherwise the `FAILED (<n>)` list is
in your report, with the cause of each item.

## Done when

- [ ] The right mode ran, and each label's result is reported as printed.
- [ ] Every failure has a cause, with the file or log line that shows it.
- [ ] Only intended baselines changed, each with its reason; Linux baselines were not faked.
- [ ] The visual review findings are reported when `web/` changed; nothing was posted or committed.

## Pitfalls

- `--quick` still runs the component and golden tests: it skips only the browser and API journey suites.
- A busy port makes the stack refuse to start on purpose, so stale code never answers; do not kill a run you did not start.
- Visual tests run at device scale 1 with fonts from the system; another Mac with other fonts can differ too.
- Updating a baseline after an accidental change hides the regression; read the diff first.
- The fake model tests the plumbing, not the prompts: a green run says nothing about real-model behaviour.
