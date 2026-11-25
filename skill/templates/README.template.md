# {{Product}} quoting agent on Azure AI Foundry

Status: **design only.** Nothing is deployed. The demos simulate agent behaviour and make no Azure calls.
Started: {{YYYY-MM-DD}}. Starting point: {{the brief or diagram this refines}}.

{{Two or three sentences: who uses it, what the agent does, how chat and generated forms work together, and who owns the state.}}

## What is in this folder

| Path | What it is |
|---|---|
| [`demo/journey.html`](demo/journey.html) | Interactive journey: dots, streaming, clickable bullet suggestions, A2UI ball-to-form animation and reverse, States/Quotes side tabs, double-confirm popup, trace drawer. Opens standalone. |
| [`demo/form-gallery.html`](demo/form-gallery.html) | {{N ≥ 20}} agent-generated A2UI forms, each with the animation, its FormSpec and the compiled A2UI messages. |
| [`architecture.md`](architecture.md), [`architecture.drawio`](architecture.drawio) | Azure services, data flow, sequences, identity, observability. |
| [`requirements.md`](requirements.md) | FR/NFR with acceptance criteria and traceability. |
| [`implementation-plan.md`](implementation-plan.md) | Spikes, phased steps, skeletons, effort, risks. |
| [`process-flow.md`](process-flow.md), [`process-flow.html`](process-flow.html) | BPMN-style swimlanes with the tool and A2UI calls at every step. |
| [`contracts/`](contracts/) | The single source of truth: state catalog, tools, FormSpec schema, memory and document output schemas, events. |
| [`prompts/`](prompts/) | Main, memory and document agent prompts, plus the token budget. |
| [`skill/`](skill/) | This skill: rebuilds everything above from refined inputs. |

## Key design decisions

<!-- One row per decision. Keep the IDs stable across rebuilds; add new ones at the end. -->
| # | Decision | Why |
|---|---|---|
| D1 | {{hosting: one hosted agent container holding the main and memory agents}} | {{constraint from the brief}} |
| D2 | {{protocol between the BFF and the hosted agent}} | |
| D3 | {{model-call budget per turn and how it is enforced (terminal tools, pre-executed actions)}} | |
| D4 | {{state ownership: the BFF is the single writer}} | |
| D5 | {{A2UI version, FormSpec → compiler → messages, data binding to state pointers}} | |
| D6 | {{toolbox and knowledge (Foundry IQ)}} | |
| D7 | {{document agent and its model}} | |
| D8 | {{deterministic fields: list them exactly as in the catalog}} | |
| D9 | {{caching layout}} | |
| D10 | {{observability}} | |
| D11 | {{regions and data residency}} | |

## Open questions (assumptions used until answered)

- **Q1: {{question}}** {{the assumption in use and its consequence}}

## How to open the demos

Open `demo/journey.html` or `demo/form-gallery.html` directly in a browser. There is no server, network or key. Rebuild them with `python3 demo/build.py`.

## Sources (read {{date range}})

- {{official URL}}: {{what it was used for}}
