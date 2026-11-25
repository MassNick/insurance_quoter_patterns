---
name: edit-agent-prompts
description: Use when asked to change what the main, memory or document agent is told, for example "change the agent's instructions", "make the agent ask X before Y", "change the tone", "add a rule to the system prompt", "the memory agent should extract X", "the document agent should read Y", "tighten the scope", or when a prompt test or the token budget fails. A request to change the model, the thinking level or a structured output is not a prompt change: it goes to `settings.yaml` (step 1). It edits the contract prompt in `contracts/prompts/`, copies it to the packaged copies, keeps the static prefix stable for prompt caching and under the token budget, keeps tool names aligned with `contracts/tools.json` and the fake model aligned with the new behaviour, runs the prompt tests, replays the journeys and prepares an evaluation set the user runs with real models. It never puts per-turn data in the static prompt, never deploys an agent version unless the user asks, and never claims model behaviour that only a real-model evaluation can show.
---

# Edit the agent prompts

The three prompts live in `contracts/prompts/` and are copied, byte for byte, into the agents that ship them. The main
agent's request starts with a static prefix (prompt, generated key list, 13 tool definitions) that must stay
byte-identical across turns so it is cached, and under 30,000 tokens; everything per turn goes after the history.
Keep the patterns of `docs/hosted-agent.md` "How prompts are assembled" and `docs/patterns.md` §7. Paths are relative
to the solution folder.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Edit `contracts/prompts/*.md` only, then copy. Never edit a packaged copy by hand.
- No volatile content in a prompt: no date, session value, name, count or state. It belongs in the context message.
- A rule that code can enforce goes in code (`skills/add-guardrail`); the prompt sentence is a hint, not the control.
- `deploy/check_contracts.py` and the `--dry-run` of the agent deploy scripts make no network call: they count as
  offline checks. Anything else that deploys waits for the user.

## Read first

- `docs/hosted-agent.md` "How prompts are assembled", "Per-turn call budget", "Injected arguments", "Hosted agent definition", and "Settings file" for models, thinking levels and structured outputs.
- `contracts/prompts/main-agent.md`, `memory-agent.md`, `document-agent.md` (the HTML comment at the top of each is
  stripped before sending and states the size target), and `contracts/prompts/token-budget.md`.
- `agents/quote-agent/src/quote_agent/prompts.py`: `instructions`, `key_list`, `tool_definitions`,
  `static_prefix_tokens`, `TOKEN_LIMIT`, `cache_key`, `context_message`, `main_input`, `memory_input`.
- `agents/quote-agent/src/quote_agent/agent.py`: `VolatileContext`, `Runtime.options`, `Runtime.run_memory`, `run_turn`.
- `contracts/tools.json` (logical names, `result_mode`, `injected_args`) and `contracts/memory-output.schema.json`.
- `agents/quote-agent/tests/unit/test_prompts.py`; `agents/document-agent/document_agent.py` and its `tests/test_definition.py`.
- `agents/quote-agent/src/quote_agent/fake/` and `docs/testing.md` "The fake model".

## Steps

1. **Place the change.** A model, a thinking level (`reasoning_effort`), a token limit or a structured output
   (`output_schema`, a schema file in `contracts/`) is not a prompt: edit `settings.yaml` and redeploy the agents
   (`docs/hosted-agent.md` "Settings file"; precedence: flag or environment variable, then `settings.yaml`, then the
   code default). For a prompt: main agent: scope, phases, turn format, forms, suggestions. Memory agent: what to
   extract, confidence bands, signals, intent, `session_preferences`. Document agent: what to read per purpose. Field
   lists come from the catalog (the key list is generated) and tool schemas from `contracts/tools.json`: never copy
   them into prose.
2. **Edit the contract prompt.** Keep the static part first and stable: same section order (`# Role` first,
   `# Scope and guardrails` before the generated `# State keys`, as `test_static_prefix_is_prompt_then_key_list_without_comments`
   checks), short imperative lines, English instructions, fr-CA replies by default. If the model needs new per-turn
   data, add a line to `prompts.context_message` (main) or `prompts.memory_input` (memory) and describe it under
   "# Context you receive each turn"; never interpolate it into the prompt. Notes for maintainers go in the HTML comment.
3. **Check tool mentions.** Use logical names only (`quoting.create_quote`, `a2ui.render_form`, `ui.suggest_replies`),
   never wire names. From the solution folder:
   ```bash
   python3 - <<'EOF'
   import json, pathlib, re
   names = {t["name"] for t in json.loads(pathlib.Path("contracts/tools.json").read_text())["tools"]}
   for p in sorted(pathlib.Path("contracts/prompts").glob("*-agent.md")):
       print(p.name, "unknown tools:", sorted(set(re.findall(r"\b(?:quoting|a2ui|knowledge|ui)\.[a-z_]+", p.read_text())) - names))
   EOF
   ```
   Every line must end with `unknown tools: []`.
4. **Copy and compare.**
   ```bash
   cp contracts/prompts/main-agent.md contracts/prompts/memory-agent.md agents/quote-agent/src/quote_agent/prompts/
   cp contracts/prompts/document-agent.md agents/document-agent/prompts/
   for f in main-agent memory-agent; do cmp contracts/prompts/$f.md agents/quote-agent/src/quote_agent/prompts/$f.md; done
   cmp contracts/prompts/document-agent.md agents/document-agent/prompts/document-agent.md
   deploy/.venv/bin/python deploy/check_contracts.py        # also fails on a drifted quote-agent copy
   ```
   The design keeps a copy in `../prompts/`; copy there too (byte-identical today) or note the divergence.
5. **Prompt version and caching.** Requests carry `prompt_cache_key = quote-<agent>:<PROMPT_VERSION>:<session_id>`
   (`prompts.cache_key`). `PROMPT_VERSION` is a runtime setting (`config.py`, default `v1`), set at deploy by
   `--prompt-version`; `deploy/post_deploy.sh` defaults it to `main:<short git sha>`, so each release starts new keys.
   There is no constant in code to bump: when a version is deployed by hand, pass a new `--prompt-version`. The
   document agent has no cache key; each deploy creates a new immutable version.
6. **Token budget.** Measure, then run the test that gates it:
   ```bash
   cd agents/quote-agent && .venv/bin/python -c "from quote_agent import prompts; print(prompts.static_prefix_tokens())"
   .venv/bin/python -m pytest tests/unit/test_prompts.py -q
   ```
   `test_token_budget_under_30k_and_counts_tools` fails at `TOKEN_LIMIT` (about 4 characters per token, not the
   deployed tokenizer). Keep the main prompt under about 3,000 tokens and the memory prompt under 1,500, as their
   comments say. If the figures move noticeably, update the tables of `contracts/prompts/token-budget.md`.
7. **Keep the fake aligned.** The fake model never reads the prompt; it mirrors the behaviour the journeys depend on.
   When the order of topics, a question, a form, the consent or document wording, or the reply after a STOP changes,
   update `fake/rules.py` (`ASK` markers in order, `extract`, `DOCS_OFFER`, `KB`) and `fake/client.py`
   (`_Main.decide`, `_Main.next_step`, `SUGG`). If text the browser journeys assert changes, update the mock BFF
   (`web/fixtures/mock-bff.ts`, `web/fixtures/scenarios.ts`). A new memory output field is a schema change first
   (`contracts/memory-output.schema.json`), then `Bff.on_memory`, the fake `extract` and `memory()` in `bff/tests/conftest.py`.
8. **Run the tests.** `(cd agents/quote-agent && .venv/bin/python -m pytest -q)`, `(cd agents/document-agent &&
   .venv/bin/python -m pytest -q)` for the document prompt, `(cd deploy && .venv/bin/python -m pytest -q)`, then
   `./test_all.sh`. Add a `test_prompts.py` assertion when the change adds a section other code relies on.
9. **Evaluate.** Offline, replay the journeys: `./test_all.sh` (API journeys and the mock-BFF browser journeys) and
   `./test_all.sh --full` before a hand-off. They prove the plumbing, not the model. For real models, write a small
   evaluation set the user runs: `agents/quote-agent/eval/cases.jsonl` (new, outside the shipped zip), one case per
   line, `{"id", "agent", "lang", "turns": [...], "expect"}`, 10 to 30 cases in fr-CA and English covering the change,
   the off-topic and red-team inputs of `skills/add-guardrail`, and two unchanged journeys. The user runs it against a
   dev deployment (the four services started as in `e2e/stack.py` but with `QUOTE_AGENT_MODEL=azure` and the dev
   settings of `docs/hosted-agent.md`, or a Foundry evaluation of the dev hosted agent), before and after the change.
   Read per case: the reply against `expect`; `turn.completed.model_calls` (at least 85 % of turns at 2);
   `usage.cached_input` over `usage.input` from turn 3 (target 70 %); `guardrail`, `error`, `suggestions.dropped`.
10. **Redeploy, only when the user asks.** A prompt reaches Foundry only as a new agent version:
    `deploy/post_deploy.sh agents` (both agents) or `deploy_quote_agent.py` / `deploy_document_agent.py`; follow
    `skills/deploy-to-azure`. Offline you may build and print the definition with
    `cd agents/quote-agent && .venv/bin/python deploy/deploy_quote_agent.py --project-endpoint '<project-endpoint>' --prompt-version '<version>' --dry-run`
    (no Azure call; the models, thinking levels, limits and output schemas default from `settings.yaml` and the printed
    environment shows what was resolved; it writes `dist/quote-agent.zip` unless you pass `--zip-out`; keep the quotes,
    or the shell reads `<` as a redirection). Versions are immutable: rollback is a redeploy of the previous commit.

## Tests to add or update

| Level | File | What |
|---|---|---|
| unit | `agents/quote-agent/tests/unit/test_prompts.py` | section order, no comments, key list, budget, context lines |
| unit | `agents/quote-agent/tests/unit/test_fake_main.py`, `test_rules.py` | fake FormSpecs valid, suggestions generic, new wording |
| component | `agents/quote-agent/tests/component/test_journey.py`, `test_invocations.py` | journey steps, two calls per turn |
| unit | `agents/document-agent/tests/test_definition.py`, `deploy/tests/test_check_contracts.py` | document definition; copies identical |
| e2e | `e2e/test_api_journey.py`, `web/e2e/journey.spec.ts` | only if visible wording or the journey changed |

## Verify

The commands of steps 3, 4 and 6, then `./test_all.sh`. Pass: no unknown tools, every `cmp` silent,
`contracts ok`, the static prefix under 30,000, all suites green.

## Done when

- [ ] The contract prompt changed and every copy is identical (or the design divergence is noted).
- [ ] No per-turn data in a static prompt; new per-turn data goes through the context message.
- [ ] Tool names match `contracts/tools.json`; the token budget test passes.
- [ ] The fake model and the mock BFF still describe the journeys the tests assert.
- [ ] The evaluation set is written and the user knows it was not run; the hand-off says to redeploy the agents (`deploy/post_deploy.sh agents` sets a new `PROMPT_VERSION` per commit).

## Pitfalls

- A date or a name in the prompt breaks caching for every session; `prompts.instructions` is cached per process.
- `deploy/check_contracts.py` compares only the quote-agent copies; the document-agent copy needs the `cmp` above.
- The fake parses French with regexes: rewording a question without moving its `ASK` marker breaks `topic_of`.
- The FormSpec schema is inlined into `a2ui.render_form`, so a schema or tool change moves the budget too.
- The memory agent's strict `response_format` means a schema-breaking prompt shows up as `memory.result {error}`. The schema is the file `agents.memory.output_schema` names in `settings.yaml`; the BFF still validates against `memory-output.schema.json`.
- Never quote the guardrail filters or tool names to the customer; the prompt says not to reveal them.
