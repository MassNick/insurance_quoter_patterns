---
name: foundry-quoting-agent-design
description: Rebuild the full design package for a conversational quoting agent on Azure AI Foundry. The package is the contracts, prompts, architecture, requirements, implementation plan, BPMN process flow, an interactive journey demo and a 20+ A2UI form gallery. Use it when given refined business requirements, states or tools for the auto insurance quoting agent, or a similar quoting product (home, travel, commercial auto), and the design artifacts must be regenerated consistently. Design only: it never deploys, calls Azure, or commits.
---

# Foundry quoting agent design

This skill regenerates every artifact in a quoting-agent design folder from refined inputs. The reference instance is `design/auto-insurance-quoting/`; this skill lives in its `skill/` folder. Everything is derived from a small set of **contracts**. Change the contracts first, then regenerate the rest, then verify.

## Hard rules

- Design only. Never deploy, never call Azure or a model API, never push, and never commit the design folder. Other folders under `design/` stay untracked too.
- Never put an API key, connection string or secret in any file or page. Use placeholders such as `<aca-env>`, `{project_endpoint}` and `<BFF managed identity token>`.
- Demos are standalone HTML: no network calls, no CDN, everything inlined by `demo/build.py`. Agent behaviour is simulated and labelled "simulated".
- Use a fictional brand by default. Use a real client's brand only when that client asked for a demo of their own experience (the reference is a Desjardins Assurances demo). Then: draw only a placeholder mark until the client's brand team supplies the official asset, say so in a comment and in the demo bar, keep the page local, and never publish it.
- Every Azure fact you write must be checked against current docs (step 2). Mark anything you could not verify **[UNVERIFIED]**.

## Inputs to collect

Ask for any of these that are missing, and say what default you will use:

| Input | Where it lands | Default from the reference |
|---|---|---|
| Product, market, languages, currency, units, home region | `state-catalog.json` `market`; architecture region choice | Auto, Canada/Quebec, en+fr, CAD, km, Canada Central |
| State fields, grouped by entity (quote, vehicle, driver, underwriting, household…), with types, enums, validation | `contracts/state-catalog.json` `fields` | 68 fields |
| Customer language and friendly labels | `label_fr`, `help_fr`, `enum_fr` per field (hover help in the dossier and on form labels) | fr-CA customer UI; developer trace in English |
| End-of-form rules (stop the online quote, or flag for the advisor) | `bff/rules/end_of_form.py` (`EndOfFormRules`) and its JS mirror `RULES` in `demo/src/journey.js` | 4 STOP + 1 FLAG examples |
| Which fields are **deterministic** (double confirmation) | `deterministic: true` in the catalog | consent, claims, convictions, refused/cancelled, licence number |
| Fields required to price | `required_for_quote: true` | see catalog |
| Tools: quoting, lookups, A2UI, knowledge, local UI tools, with result mode (`terminal` / `narrate`) | `contracts/tools.json` | 13 tools |
| Phases and their order of topics | `prompts/main-agent.md`; `nextTopic()` in `demo/src/journey.js` | Intent, Gather, Advise |
| Memory-agent signal codes | `memory-output.schema.json` `signals.code` enum | 16 codes |
| Model deployments | README Q2; architecture §2 | `gpt-5.6-luna` for documents; main and memory are placeholders |
| Constraints: model calls per turn, prompt and tool token cap, TTFT, caching | README decisions; requirements NFR | 2 calls; under 30k tokens; TTFT p95 ≤ 1.5 s |
| Hosting constraints | README decisions | 1 hosted agent container in Foundry; MCP servers and the BFF on Container Apps; Cosmos DB |

## Workflow

### 1. Read the current state

Read `README.md` (decisions D1–D11, open questions) and every file in `contracts/` and `prompts/`. Read the brief's original requirement list in `requirements.md` §3 (traceability). Write down what the new input changes. Most refinements touch only the catalog, the tools, and a few forms.

### 2. Refresh the evidence (docs churn weekly)

Re-check current docs for anything the change depends on. Use the sources list at the bottom of `README.md` and the deeper evidence base in `../microsoft-foundry-agents/findings/`. A fact older than 30 days must be re-read before you rely on it. Topics:
- Foundry hosted agents (container contract, protocols, sandbox sizes, regions, quotas)
- Microsoft Agent Framework (Python API names change often; check the current upgrade notes)
- Toolbox and MCP tool
- Foundry IQ knowledge base MCP
- Cosmos DB history provider
- Guardrails
- Prompt caching (`prompt_cache_key`)
- App Insights / OpenTelemetry GenAI conventions
- A2UI: the design targets **1.0** (current since the 2026-11-11 release; v0.9.1 is closed). Check the spec status line at https://a2ui.org/specification/ and whether the chosen React/web renderer supports the current version

### 3. Update the contracts (single source of truth)

Edit in this order. When starting a new design folder, copy `templates/contracts/` and `templates/prompts/` first:
1. **`contracts/state-catalog.json`.** For each field: `key`, `entity`, `type`, `label`, and as needed `enum`, `pattern`, `min`/`max`, `deterministic`, `required_for_quote`, `derived_from`, `visible_if`, `lookup_tool`, `pii`, `document_extractable`. Mark fields not in the business list with `added: true`.
2. **`contracts/tools.json`.** For each tool: `name` (logical, dotted), `wire_name`, `server`, `result_mode`, a short `description` (one or two sentences: the token cap counts these), `inputSchema` and `output`. Arguments the model must never choose (`session_ref`) go in `injected_args`, never in the schema.
3. **`contracts/form-spec.schema.json`.** Change it only if a new widget or field property is needed. Each new widget also needs a renderer in `demo/src/a2ui.js` (`r<Component>` method) and a row in architecture §7.2.
4. **`contracts/memory-output.schema.json`.** Add new signal codes here.
5. **`contracts/events.md`** and **`contracts/document-output.schema.json`.** Change these when new event types, input kinds or document purposes appear.

Then run the consistency check (step 8) before touching anything else.

### 4. Update the prompts and the token budget

- `prompts/main-agent.md`: role, scope guardrails, the phases with the topic order, the turn format. Keep it under 3,000 tokens. The static text must not include anything that changes per turn, because the prompt-cache prefix must stay byte-identical.
- `prompts/memory-agent.md`: extraction rules, confidence bands and signal examples for the new domain.
- `prompts/document-agent.md`: the purposes and the fields per purpose.
- Re-measure with `python3 skill/scripts/check_artifacts.py --tokens` and update `prompts/token-budget.md`. The static prefix (main prompt + tools + compact key list) must stay under 30,000 tokens.

### 5. Regenerate the documents

Use `templates/*.template.md` for structure; the reference instance shows the expected depth. Keep each document consistent with the contracts. Never restate a contract differently: link to it.

| Document | Template | Must contain |
|---|---|---|
| `README.md` | `templates/README.template.md` | file map, decisions table (D#), open questions (Q#), how to open the demos, dated sources |
| `architecture.md` | `templates/architecture.template.md` | component table, Mermaid architecture diagram, sequence diagrams (turn, form, document, deterministic confirm, pre-executed action), state and merge rules, A2UI pipeline and catalog, model-call budget, streaming, caching, identity/RBAC table, networking, observability, guardrails, audit, failure modes, cost, verify-before-build list |
| `architecture.drawio` | (edit the existing file) | zones: Azure, Foundry, Container Apps, data; labelled edges with protocols. Validate the XML parses and every edge's source and target exist |
| `requirements.md` | `templates/requirements.template.md` | FR groups with MUST/SHOULD and acceptance criteria, NFRs with numbers, traceability brief → FR/NFR → artifact, open questions |
| `implementation-plan.md` | `templates/implementation-plan.template.md` | spikes first, phases 0–14 with Build / Names / How / Done when, code skeletons, the CI token-budget step, dependency graph, effort, risks |
| `process-flow.md` + `process-flow.html` | edit the `/* <flow-data> */` JSON block in the HTML, then run the generator in the appendix of `process-flow.md` | one node per step with lane, BPMN type, tool calls, A2UI calls, state paths, events, model calls. Every tool name must exist in `tools.json` |

### 6. Regenerate the demos

All demo code is in `demo/src/`. A clean copy (with `build.py`) is in `templates/demo/`. Copy it to `demo/` to start a new design folder.

- **Forms (`demo/src/forms.js`).** One entry per form: `id`, `group`, `title`, `said` (customer utterance), `agent` (one-sentence agent message), `tags`, `spec` (a FormSpec that validates against the schema) and `sample` (`path → value` for the demo fill button). The gallery needs **at least 20**. Cover:
  - every state group;
  - prefill from chat;
  - an inferred value;
  - a deterministic field;
  - conditional fields;
  - lookups;
  - a repeat (the "N identical items → one shared field + N item fields with photo upload" pattern);
  - a matrix;
  - a document upload;
  - a conflict;
  - offers;
  - adjust;
  - a handoff;
  - a summary.
- **Journey (`demo/src/journey.js`).** Update these together:
  - the `spec*()` builders (small subject FormSpecs, at most about 5 fields each, returning `{ spec, sample, chip }`);
  - `nextStep()` (topic order, text-or-form choice by `answer_mode`, completion rules);
  - `decide()` (the simulated main agent: one plan per turn, with text, form, suggestions, KB, hand-off);
  - `memoryAgent()` regex rules (the simulated extraction, including `session_preferences`);
  - `SUGG` (generic suggestions only: never a name, value or model the customer has not given);
  - `RULES` (mirror of the BFF rules);
  - `KB` (knowledge answers with citations) and `rate()` (the fake price).
- **Gallery entries for the subject components.** Include a `document_drop` form, an `assignment_board` form (drag to connect) and the `fin_conseiller` end-of-form form.
- **Renderer (`demo/src/a2ui.js`).** Change it only for new widgets or validators.
- **Build.** Run `python3 demo/build.py`. It inlines the CSS, the JS and `contracts/state-catalog.json` into `demo/journey.html` and `demo/form-gallery.html`.

The journey must keep these behaviours, because they are requirements:
- 3 animated dots on send;
- streamed text;
- clickable bullet suggestions;
- the A2UI animation (chat slides left; a ball leaves the bot icon, moves right and expands into the form; the reverse on submit);
- the States and Quotes side tabs;
- the double-confirm popup for deterministic fields;
- live autofill of an open form from chat;
- the trace drawer with model-call, cache and TTFT meters.

The Variation C client demo adds these (D12–D20):
- a one-time document offer right after intent and consent, and a one-time note that the customer can answer by text or ask for forms (never ask them to choose);
- generic suggestion bubbles above the composer, filtered by the BFF;
- reopenable form chips ("Formulaire : …": open, submitted, closed, sent) that re-render the surface in edit mode with no model call;
- the collapsed dossier panel (Renseignements and Soumissions tabs) that springs open from the rail and staggers its items, with friendly labels and hover help;
- "Conversation | Formulaire complet" in the header (one long prefilled surface, reserved id `full_form`);
- the end-of-form view after a STOP rule (reason, rule id, editable summary, consent, hand-off reference);
- a phone frame (demo bar → Téléphone) where the same page runs at 390 px: the form covers the conversation, the composer stays usable, panels become sheets, and the trace keeps streaming to the parent page.

### 6b. End-of-form rules (BFF)

Rules live in `bff/rules/end_of_form.py` (copy from `templates/bff/`). The module docstring has the step-by-step "How to add a rule" guide. The BFF runs `EndOfFormRules.check(state)` after **every** commit batch, whatever the source (memory deltas, form submits, document reviews, full-form saves, confirmations), not as a callback on the memory agent: forms and documents never pass through the memory agent. A STOP on a value only inferred from chat asks one confirmation question first. Keep the JS mirror (`RULES` in `demo/src/journey.js`) in step with the Python rules, and run `cd bff && python3 -m unittest` (each rule needs a positive and a negative fixture).

### 7. Update README and traceability

Add or change decisions (D#) and open questions (Q#). In `requirements.md` §3, map every line of the new brief to FR/NFR IDs and to the artifact that shows it.

### 8. Verify (do not skip)

1. `python3 skill/scripts/check_artifacts.py` from the design folder. It checks:
   - JSON validity;
   - every FormSpec key exists in the catalog;
   - at least 20 forms with unique `form_id`s;
   - every tool named in the process flow and the docs exists in `tools.json`;
   - the deterministic fields listed in README match the catalog;
   - the token budget is under 30k;
   - every compiled form is A2UI 1.0 (version, single createSurface, identifier ids, `@path`, `@checks`, resolved children);
   - there are no secrets;
   - no `@inline` markers are left in the built pages.
2. `node --check demo/src/*.js` (if Node is available).
3. Open `demo/journey.html` and `demo/form-gallery.html` in the built-in browser. Use a desktop-sized viewport (about 1400×860), then a narrow one (about 390 wide).
   - Check the console for errors.
   - Run `skill/scripts/journey-driver.js` in the page with the browser JavaScript tool, once per path (`window.__path` = `docs`, `text`, `rule`, `edit`). It clicks through with the demo fill button, confirms the popups, and reports `__log` / `__errs`. Poll `window.__done`.
   - Switch the demo bar to Téléphone and repeat one path inside the iframe (`document.querySelector('#phones iframe').contentWindow`).
   - Take screenshots: mid-animation (about 2.3 s after a form-opening click), the open form, the States tab, the Quotes tab, the trace drawer.
4. Open `process-flow.html` and check that the node count equals the table row count.
5. `cd bff && python3 -m unittest`.
6. Report what you verified and what you could not (for example, that dark mode can't be checked when the pane renders local files in light mode).

## Gotchas learned building the reference

- The built-in browser pane renders local files as `data:` snapshots. `location.reload()` does nothing there, so the journey's Restart resets state in the page. To load a new build, open a new tab on the file URL.
- The A2UI data model binds to the same JSON Pointers as the session state. That is what makes live autofill a single `updateDataModel` from the BFF. Keep pointers identical across catalog, FormSpec, state and process flow.
- The memory agent's output must be visible to the main agent's plan in the simulation. In the real system the main agent reads the raw message itself, so both agents run concurrently.
- A form submit must **upgrade** chat-inferred values to `form`/`confirmed` even when the value didn't change.
- Gallery FormSpecs carry illustrative chat prefills. The journey strips every prefill and recomputes it from session state (`withPrefill`), so it never shows a value the customer didn't give.
- Keep a dedicated vehicle range for fleet items, and leave them out of personal pricing and the assignment matrix.
- Tool names with a dot are logical. Map them to wire names (`__`) for OpenAI-style function calling.
- A2UI 1.0 rules the compiler must keep:
  - `"version": "1.0"` on every message;
  - one `createSurface` carrying `components` and `dataModel`, with no `theme`;
  - identifier ids (underscores, never hyphens) for surfaces and components;
  - `{"@path": ...}` bindings, relative inside repeater rows;
  - `"@checks"` CheckRules `{condition: {"@call", args}, message}`;
  - Markdown headings in `Text` (no h1–h5 variants);
  - buttons with a Text `child` and `action.event.name`;
  - `updateDataModel` always with `value` (`null` deletes).

  `check_artifacts.py` compiles every FormSpec and fails on any of these.
- Field lookups and document reads are A2UI `callAgentFunction` → BFF → MCP tool → `agentFunctionResponse`. They never become a model turn.
- Class-name collisions bite: the InPlaceNote tone `chat` picked up the `.chat` container layout, and a demo-bar `.note` picked up the form-note style. Prefix chrome classes.
- A hidden browser pane throttles timers and animations. A time-boxed walk over the gallery reports false "no card" failures; render every FormSpec directly (`A.compile` + `Surface.render`) to check them, and poll the driver instead of waiting a fixed time.
- `data:` snapshots drop the URL hash. Use the demo bar (device, trace) rather than `#phone`.
- The phone frame is a `srcdoc` iframe built from the page's own HTML captured before any DOM change; prepend `<!doctype html>` or it renders in quirks mode.
- The drag board uses pointer capture on the dragged chip, so it needs no document-level listeners and works with touch. Auto-scroll the nearest scrolling ancestor while a person is held near its edge.
- Values read from documents are committed with source `document`, not `form`, even though they arrive through a form submit.
