---
name: quote-solution
description: Start here for any work on the Azure AI Foundry auto-insurance quote solution (hosted quote-agent, BFF, mcp-quoting, mcp-a2ui, A2UI web in fr-CA, Foundry IQ). Use it when you take the solution over or are unsure where a change belongs, for example "where do I start", "how does this fit together", "which skill do I use", "what is still stubbed", "what is left before go-live", "can I say this is done". It gives the one-screen map (components, ports, who owns state, the one commit path), the invariants every change must keep, a task-to-skill routing table, what is still stubbed or open, and the routine to run before you say done. It never edits code, calls Azure, deploys or commits by itself.
---

# Quote solution: map, invariants, routing

This skill changes nothing: it tells you how the solution fits together, which skill owns your task, and which rules no
change may break. A Foundry hosted agent writes the conversation, the BFF owns the session state, two MCP servers on
Container Apps do lookups, pricing and forms (A2UI 1.0), and the web renders fr-CA. Keep the patterns in
`docs/patterns.md`; when a doc and the code disagree, follow the code (and `contracts/interfaces.md` for boundaries).

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Load the task skill from the routing table before you edit anything. Report every doc/code disagreement you find (file:line).

## Read first

Paths are relative to the solution folder.

- `AGENTS.md`: the hard rules and the task table.
- `README.md`: layout, engineering conventions, the five test levels.
- `contracts/interfaces.md`: ports, HTTP API, invocation body, MCP tool shapes. It wins over the design docs.
- `docs/patterns.md`: the nine conversation patterns and the invariants checklist.
- `docs/architecture.md`: Azure components, identities, decisions in force, what is still open.
- `docs/testing.md`: how to run each suite, and "Before you merge" per type of change.

## Steps

### 1. Read the map

| Component | Path | Port (local) | In Azure | Owns |
|---|---|---|---|---|
| web | `web/` | 5173 (8080 in its image) | Container app, only with `deploy_web = true` | nothing |
| BFF | `bff/` | 8000 | Container app | all session state: Cosmos `sessions`, `events`, `state_changes`, `quotes`, `confirmations`, `handoffs`; Blob `uploads` |
| quote-agent | `agents/quote-agent/` | 8088 | Foundry hosted agent (invocations, SSE) | Cosmos `agent_history` |
| mcp-quoting | `mcp/quoting/` | 8101 (`/mcp`, `/healthz`) | Container app, 1 replica | reads `sessions` only; quotes in `InMemoryQuoteStore` |
| mcp-a2ui | `mcp/a2ui/` | 8102 (`/mcp`, `/healthz`) | Container app | nothing (catalog in the image) |
| document-agent | `agents/document-agent/` | none | Foundry prompt agent | nothing |
| toolbox `auto-quoting-toolbox` | `deploy/create_toolbox.py` | | Foundry toolbox over both MCP servers | nothing |
| knowledge base `auto-quoting-kb` | `deploy/create_knowledge_base.py` | | Foundry IQ on AI Search, source Blob `kb-docs` | the index |
| `quote_common` | `packages/quote_common/` | | inside every Python service | `is_usable`, `session_ref`, `wrap_from_env`, `logs.redact` |

Flow: browser → Front Door → BFF → quote-agent. The agent reaches the MCP servers only through the toolbox and Foundry
IQ directly. The BFF calls mcp-quoting lookups, mcp-a2ui `render_form` and the document agent itself. Form lookups and
document reads never use the model (`POST /api/sessions/{sid}/a2ui/functions`).

**The one commit path** is `Bff.commit(t, changes, source, actor, ...)` in `bff/src/quote_bff/service.py`: merge
(validate, consent gate, precedence), hold deterministic fields, run the end-of-form rules, write, emit. It returns
`go`, `ask` or `stop`; the agent runs only on `go`. Every caller goes through it: `on_memory`, `on_action`,
`send_handoff`, `on_document`, `on_rule_answer`, `resolve`.

### 2. Check the invariants (`docs/patterns.md` "Invariants to keep")

| Invariant | Pinned by |
|---|---|
| One state writer, one commit path (`Bff.commit`) | `bff/tests/component/test_journey.py` |
| One definition of "priceable": `quote_common.state.is_usable`; quote tools read committed state, never model arguments | `test_missing_fields_match_mcp_quoting` |
| Deterministic fields are confirmed twice by the customer; no model commits one | `test_deterministic_fields_never_commit_directly` |
| No personal data stored before the confirmed contact consent (hand-off fields excepted) | `test_pii_is_withheld_until_the_contact_consent_is_confirmed` |
| Rules run after every commit batch, before the agent; ask once before an inferred STOP; no matched values in events | `bff/tests/unit/rules/` |
| After a STOP or hand-off: no agent form, no quote, chips locked | `test_an_agent_form_after_a_stop_is_not_relayed`, `test_create_quote_refuses_while_the_session_is_blocked` |
| A hand-off needs its consent box and is stored only in Cosmos `handoffs` | `test_lamborghini_inferred_stop_...` |
| The model never sees injected arguments (`session_ref`, `state`, `mode`, `version`, `offers`) | `test_injected_args_override_the_model_and_are_hidden_from_it` |
| Two model calls per turn, three at most | `agents/quote-agent/tests/unit/test_policy.py` |
| Static prefix under 30,000 tokens and byte-identical; deploy any prompt or tool change under a new `PROMPT_VERSION` (a runtime setting, set at deploy) | `agents/quote-agent/tests/unit/test_prompts.py` |
| BFF-only actions make no model call (reopen, saves, close actions, rule answers, hand-off) | `bff/tests/component/test_surfaces.py` |
| Customer text fr-CA from the catalog or a string table (`forms.STRINGS`); one known exception, the fallback sentence in `Turn._map_result` | review (no single test) |
| Suggestions generic: at most 4, at most 40 characters, no PII, names or values | `bff/tests/unit/test_suggestions_forms.py` |

### 3. Route the task

| Task | Skill |
|---|---|
| Connect the insurer's rating engine, vehicle catalog, address or VIN service; persistent quote store; a new MCP tool or server | [`connect-quoting-systems`](../connect-quoting-systems/SKILL.md) |
| Create or refill the Foundry IQ knowledge base, citations, fake knowledge answers | [`connect-foundry-iq`](../connect-foundry-iq/SKILL.md) |
| Add or change a STOP or FLAG rule | [`add-end-of-form-rule`](../add-end-of-form-rule/SKILL.md) |
| Add a guardrail (deployment filter, prompt, BFF, tool policy) | [`add-guardrail`](../add-guardrail/SKILL.md) |
| Change the main, memory or document agent instructions | [`edit-agent-prompts`](../edit-agent-prompts/SKILL.md) |
| Add a state field (deterministic, `pii`, `required_for_quote`) or a form | [`add-field-or-form`](../add-field-or-form/SKILL.md) |
| Run the suites, update screenshot baselines, Linux baselines, visual review | [`run-regression`](../run-regression/SKILL.md) |
| Terraform, images, Foundry data plane, smoke test, rollback | [`deploy-to-azure`](../deploy-to-azure/SKILL.md) |
| Connect the hand-off to a CRM or work queue | No skill. `docs/patterns.md` §5 and `docs/architecture.md` "Hand-off". Ask the user which system first |

### 4. Know what is still stubbed or open

| Item | Where | Next |
|---|---|---|
| Stub quoting engine and lookups (demo prices) | `mcp/quoting/src/mcp_quoting/adapters/stubs.py`, the default of `from_env` | `connect-quoting-systems` |
| Quotes in memory, so mcp-quoting runs 1 replica and loses open quotes at each release | `InMemoryQuoteStore`; `max = 1` in `infra/terraform/locals.tf` | `connect-quoting-systems` |
| Empty knowledge base: nothing uploads to Blob `kb-docs`; locally a fake KB | `deploy/create_knowledge_base.py`; `KB` in `agents/quote-agent/src/quote_agent/fake/rules.py` | `connect-foundry-iq` |
| Hand-off stored in Cosmos `handoffs` only, deliberately unconnected; `handoff_done` promises a callback nothing delivers | `Bff.send_handoff`, `forms.STRINGS` | Ask the user (design Q7) |
| Models are the GPT-5.6 family on `GlobalStandard` in Canada Central; the Law 25 assessment of that SKU is still to do | `settings.yaml` (`deployments`, `agents`, `knowledge`), Terraform `location` | `deploy-to-azure`, `edit-agent-prompts` (thinking level, structured output) |
| Desjardins mark is a hand-drawn placeholder | `web/src/app/brand.tsx` (`TODO(NFR-BRD-1)`) | Official asset before any external showing, then new baselines |
| Linux screenshot baselines missing; CI `web` job fails on Ubuntu | only `web/tests/visual/__screenshots__/darwin/` | `run-regression` |
| No `azure`-level test written | marker declared in `bff/pyproject.toml` only | `connect-quoting-systems` |
| Citation shape: the agent emits objects, the web renders strings | `turn.py` `_citations`; `web/src/app/store.ts`, `Chat.tsx` | `connect-foundry-iq` |
| Nothing deployed; `[UNVERIFIED]` Foundry items | `infra/README.md` "Verify before build" | `deploy-to-azure` |

### 5. Before you say done

Run the routine in Verify, then the extra suites `docs/testing.md` "Before you merge" names for your type of change.

## Tests to add or update

None for this skill. Each task skill names its tests. A change that touches several components runs the union of
their rows in `docs/testing.md` "Before you merge".

## Verify

Setup once per component (`docs/testing.md` "Per component"): each Python folder needs its `.venv`, `web/` needs
`npm ci`.

| Command | When | Pass |
|---|---|---|
| `./test_all.sh --quick` | While iterating: Python lint and tests, web typecheck, lint, unit | ends with `all suites passed` |
| `./test_all.sh` | Before you say done: adds web visual, web e2e (mock BFF), API journeys | `all suites passed`, exit 0 |
| `./test_all.sh --full` | Before a hand-off: adds the full-stack browser journeys | `all suites passed`, exit 0 |
| `uv run --project deploy --extra test python deploy/check_contracts.py` | Any change in `contracts/` | `contracts ok` |
| `terraform -chdir=infra/terraform init -backend=false` then `validate` (same for `infra/terraform/entra`) | Any change in `infra/` | `Success! The configuration is valid.` |

A failure prints `FAILED (n):` and the suites. Report them as printed; do not update baselines or loosen a test to
turn a run green.

## Done when

- [ ] The task skill's own "Done when" list is complete.
- [ ] Every invariant in step 2 still holds, and its pinning test passes.
- [ ] Contracts, code, tests and docs changed in that order; the design copies (`../contracts/`) match or the divergence is noted.
- [ ] `./test_all.sh` passed (or each failure is reported as it is), and `--full` before a hand-off.
- [ ] No secret, no commit unless asked, no push, no Azure call the user did not ask for.

## Pitfalls

- On Linux, `web: visual` fails until a `linux/` baseline set exists. On macOS the `darwin/` set applies.
- `deploy/check_contracts.py` compares the quote-agent prompt copies with `contracts/prompts/`; nothing compares the document-agent copy, so check it with `cmp contracts/prompts/document-agent.md agents/document-agent/prompts/document-agent.md`.
- `web/fixtures/mock-bff.ts` is a second, scripted copy of the BFF's behaviour. Change it with the BFF.
- The offline suites run the fake model: they test the plumbing, not the model's behaviour.
- `contracts/tools.json` and `contracts/state-catalog.json` are byte-identical to the design's `../contracts/`. Keep them so.
- A new state writer, a second "priceable" rule or a tool argument the model chooses breaks an invariant even when tests pass.
