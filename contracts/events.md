# Event contract

Two hops carry the same envelope:

- **Frontend ⇄ BFF.** `POST /api/sessions/{sid}/turns` returns `text/event-stream`. Uploads go to `POST /api/sessions/{sid}/documents`. Inbound events that the BFF handles alone (`ui.surface_reopen`, a save on an edit-mode surface, the hand-off button) use the same endpoint: the stream carries only the BFF's events and closes, with no `turn.started` and no hosted-agent call.
- **BFF ⇄ hosted agent.** This hop uses the Foundry **invocations** protocol:
  - The call is `POST {project}/agents/quote-agent/endpoint/protocols/invocations?api-version=v1&agent_session_id={asid}`, with SSE.
  - The BFF gets an Entra token for `https://ai.azure.com/.default` with its managed identity.
  - It passes correlation IDs in `x-client-session-id`, `x-client-turn-id` and `traceparent`.

Invocations is used rather than Responses for two reasons. The turn carries more than text (state, form submissions, confirmations), and the stream carries more than text (A2UI messages, state deltas, signals, suggestions). Foundry evaluations still work, because invocations agents are a supported eval target with freeform `input_messages`.

## Envelope

Every event, in both directions, is one JSON object:

```json
{ "type": "text.delta", "session_id": "s_8f2c", "turn": 7, "seq": 31, "ts": "2026-11-24T15:04:05.120Z", "actor": "main_agent", "data": { } }
```

`actor` says who produced the event: `customer`, `frontend`, `bff`, `main_agent`, `memory_agent`, `document_agent` or `tool:<name>`. A lookup the BFF runs for a form field (address or YMM) appears as `tool.call` / `tool.result` with `actor: "bff"` and no model call.

`seq` is monotonic per turn, so the frontend can drop duplicates when a stream reconnects. The BFF writes every event to the Cosmos `events` container before it forwards the event. That is the full conversation log the requirements ask for.

## Inbound: frontend → BFF

| type | data | Notes |
|---|---|---|
| `user.message` | `{ text }` | Typed text, or the label of a clicked suggestion (`via: "suggestion"`). The header button "Parler à un conseiller" sends `{ text, via: "header", action_id: "talk_to_advisor" }`. The two answers to a rule confirmation question carry `action_id: "rule_confirm"` or `"rule_correct"`. The BFF handles `talk_to_advisor` and `rule_confirm` alone, by opening the hand-off surface (D19), and does not call the agent. |
| `a2ui.action` | The A2UI 1.0 `action` message: `{ name, surfaceId, sourceComponentId, timestamp, context }` | Form submit or button. `context` carries the bound values. The BFF validates them against the catalog before they reach state. On a surface the BFF rendered itself, the BFF handles the action alone and no turn starts. A save on a reopened chip or on `full_form` means: commit with `source=form`, rule check, a system note for the next turn, `deleteSurface`. On `end_of_form`, `send` creates the hand-off and `edit_answers` closes the surface so the customer can reopen a chip. |
| `ui.surface_reopen` | `{ surface_id }` | The customer clicked a form chip in the transcript (D16) whose state is `submitted` or `closed`. The BFF loads that surface's FormSpec from its surface registry and renders it again through `mcp-a2ui`, prefilled from the current committed state, in edit mode. No model call, no turn. The reserved id `full_form` opens full-form mode (D20): the whole quote as one form built from the catalog. |
| `a2ui.callAgentFunction` | The A2UI 1.0 `callAgentFunction` message: `{ surfaceId, functionCallId, callFunction: { "@call", catalogId, args } }` | A field lookup or document read the renderer can't do locally: `lookupMakeModelYear`, `identifyVehicleType`, `decodeVin`, `identifyAddress`, `extractDocument`. The BFF calls `mcp-quoting` or the document agent and replies with `agentFunctionResponse`. No model call, no turn. Logged as `tool.call` / `tool.result` with `actor: "bff"`. |
| `confirm.response` | `{ confirmation_id, step: 1 \| 2, accepted }` | Double confirmation for deterministic fields. |
| `document.uploaded` | `{ document_id, purpose: "vin" \| "licence" \| "registration" \| "declaration", surface_id?, field_path? }` | Sent after the upload. The BFF calls the document agent. For a `DocumentDrop` tile (`surface_id` of the documents form; D18) the extracted fields go back to that surface with their confidence for review, and are committed only when the customer submits it. |

## BFF → hosted agent (one invocation per turn)

```json
{
  "turn": 7,
  "input": { "kind": "message", "text": "We have 3 drivers but only one car" },
  "state": { "quote": { }, "vehicles": [ ], "drivers": [ ], "underwriting": { }, "household": { } },
  "phase": "gather",
  "open_surfaces": ["drivers_1"],
  "signals": [ ],
  "preferences": { "answer_mode": "text" },
  "rule_hits": [ { "rule_id": "JEUNE_CONDUCTEUR_PRINCIPAL", "severity": "flag", "paths": ["/quote/date_of_birth", "/household/driver_vehicle_assignments"], "inferred": false } ],
  "system_notes": [ "Form 'vehicle_0' edited outside the chat: /vehicles/0/usage_type pleasure -> commute" ],
  "session_ref": "<HMAC-signed, 15-minute token: session id + user id + expiry>"
}
```

- `preferences.answer_mode` is `"text"`, `"forms"` or `null` (D14). The main agent sees it as PREFERENCES.
- `rule_hits` lists the end-of-form rule results that are still active (D19). The main agent sees them as RULES.
- `system_notes` are what happened between turns without a model call: reopened-form saves, full-form saves, rule hits and hand-offs. The hosted agent appends them to the history as system messages **before** the new input, so history stays append-only and the cache prefix holds. The BFF clears them once the turn has started.

`input.kind` is one of:
- `message`
- `form_submitted` (carries `surface_id` and the changed paths)
- `action` (a button, for example `get_quote`, or `rule_correct` after the customer says a value that triggered a STOP rule is wrong)
- `document_extracted`
- `confirmation_result`
- `warmup`: sent once when the chat page loads. It starts the hosted-agent session (sandbox cold start) before the first message. It makes no model call and returns only `turn.completed` with `model_calls: 0`.

For every input kind other than `message`, the hosted agent writes a one-line text summary (for example "Form about-you submitted: 7 fields" or "Licence photo read: 4 fields") and gives it to the memory agent as its input. Both agents still run, so the turn is still 2 model calls.

The hosted agent loads conversation history from Cosmos (Agent Framework `CosmosHistoryProvider`, partition key `/session_id`). It then runs the **memory agent** and the **main agent** concurrently.

## Outbound: hosted agent → BFF → frontend

| type | Producer | data | Frontend effect |
|---|---|---|---|
| `turn.started` | hosted agent | `{ phase }` | Confirms the turn. The frontend has already drawn the 3 animated dots when the customer pressed send (target: within 150 ms), so it does not wait for this event. |
| `text.delta` | main agent | `{ delta }` | Streams text. The dots are replaced on the first delta. |
| `text.done` | main agent, or the **BFF** for templated messages | `{ text, citations? }` | Finalizes the bubble. Templated BFF messages (the greeting, the off-topic short-circuit, the rule confirmation question, the hand-off confirmation) arrive as `text.done` with `actor: "bff"` and no deltas. |
| `suggestions` | `ui.suggest_replies`, checked by the **BFF** | `{ items: [{ label, kind, action_id? }] }` | Bubbles above the composer. The BFF drops any item that breaks D15 (more than 4 items, a label over 40 characters, a PII pattern such as an email, phone, postal code, VIN or licence number, a proper name, or a value the customer has not given) and logs each drop. If nothing is left, no bubbles show. |
| `a2ui.message` | `a2ui.*` tools, or the **BFF** | One A2UI 1.0 message (`"version": "1.0"`): `createSurface` (carrying `components` and `dataModel`, so one message opens a form), `updateComponents`, `updateDataModel` (`value` required; `null` deletes), `deleteSurface`, or `agentFunctionResponse` | `createSurface` plays the open animation (chat slides left, ball to form) and adds a form chip to the transcript (`Formulaire : <titre>`, state `open`). `deleteSurface` plays the reverse and sets the chip to `submitted`, `closed` or `sent`. The BFF sends `deleteSurface` itself after it accepts a valid submit, so closing doesn't wait for a model. It also sends `updateDataModel` for live autofill (the value, plus `{ source, note }` at `/_provenance/<escaped pointer>` for the badge), and for server-side validation errors at the reserved path `/_errors` (`{ "/quote/postal_code": "Not a Canadian postal code" }`). For a `createSurface` from `a2ui.render_form`, the hosted agent adds `form_spec` (the FormSpec the model sent) next to the message. The BFF keeps it in its surface registry for reopening (D16) and strips it before forwarding. The BFF also produces `createSurface` itself, compiled by `mcp-a2ui`, for reopened chips, `full_form` and `end_of_form`. |
| `tool.call` / `tool.result` | hosted agent middleware | `{ name, args_redacted, ms, result_mode }` | Trace drawer only. Also an OTel span. |
| `memory.result` | memory agent | `MemoryAgentOutput` (see `memory-output.schema.json`) | Not forwarded raw. The BFF turns it into the events below, including `session.preferences` when `session_preferences.answer_mode` is not null. |
| `state.delta` | **BFF** | `{ changes: [{ path, value, source, status }] }` | States tab updates and flashes. Also sent as A2UI `updateDataModel` to any open surface bound to those paths (live autofill). |
| `signals` | **BFF** | `{ items: [...] }` | States tab, Signals section. |
| `confirm.required` | **BFF** | `{ confirmation_id, path, old_value, new_value, reason, steps: 2 }` | Double-confirm popup. The change stays `pending_confirmation` until both steps are accepted. |
| `quote.created` / `quote.updated` | `quoting.*_quote` result | `{ quote_id, version, offers, previous_version? }` | Quotes tab. Earlier versions move to Past quotes. |
| `session.preferences` | **BFF** | `{ answer_mode: "text" \| "forms" \| null }` | Stored in the session document (D14). Trace drawer only; the customer sees no change except how the agent asks. |
| `rules.hit` | **BFF** (`EndOfFormRules`) | `{ rule_id, severity: "stop" \| "flag", paths, inferred, reason }` (`RuleHit.event_data()` in `bff/rules/end_of_form.py`) | Emitted once per new hit after a commit (D19). `reason` is the customer-facing text in the session language; matched values stay out of the event because they can be PII. `flag`: an advisor signal is added and the journey continues. `stop` with `inferred: true`: the BFF asks one confirmation question first. `stop` otherwise: the BFF closes every open surface and renders the `end_of_form` surface (reason, rule id, editable summary, contact fields, consent to transmit). |
| `handoff.created` | **BFF** | `{ reference, rule_id }` | The customer sent the `end_of_form` surface with the consent box checked. `reference` looks like `CNS-123456` and is shown in the confirmation. `rule_id` is null for a customer request ("Parler à un conseiller"). The chip becomes `sent` and the other chips lock. |
| `phase` | `ui.set_phase` | `{ phase }` | Phase stepper in the trace drawer (Intent, Gather, Advise). The customer-facing step chips (Documents, Vous, Véhicules, Conducteurs, Antécédents, Soumission; D12) are derived by the frontend from state and surfaces. |
| `guardrail` | hosted agent | `{ category: "off_topic" \| "content_safety" \| "pii_request", action }` | Trace drawer. The reply text comes from the main agent. |
| `turn.completed` | hosted agent | `{ model_calls: 2, usage: { input, cached_input, output }, ttft_ms, duration_ms }` | Trace drawer. Also written to App Insights as metrics. |
| `error` | any | `{ code, message, retryable }` | Inline retry bubble. |

## State merge rules (BFF)

The BFF is the **only writer** of session state.

1. **Source precedence**, from highest to lowest: `form`, then `document` (after the user reviews it), then `lookup`, then `chat` (memory agent), then `system`. A lower source never overwrites a value with status `confirmed`. It produces a conflict, and the main agent asks about it on the next turn.
2. **Deterministic fields** (`deterministic: true` in the catalog) never commit directly. They become `pending_confirmation`, then `confirm.required`, then a two-step popup, then `confirmed`. A refusal reverts the value and logs `confirm.rejected`.
3. **Memory changes** with `confidence < 0.6` are stored as `inferred` and shown with a dotted underline. A form prefill can propose them, but they never reach `create_quote` until confirmed.
4. **Derived fields** (`years_licensed`, `years_as_principal_driver`) are computed by the BFF, never by a model.
5. Every commit writes three things:
   - the new state document in Cosmos `sessions`, with an optimistic-concurrency `_etag`;
   - an append-only row in Cosmos `state_changes` (path, old, new, source, turn, actor, trace_id);
   - the event in `events`.
6. **End-of-form rules run after every commit batch** (D19), whatever the source: memory-agent deltas, form submits, document reviews, reopened-form and full-form saves, confirmations. The BFF calls `DEFAULT_RULES.check(state, acknowledged)` from `bff/rules/end_of_form.py` once per batch, synchronously, before the main agent runs. It reads the whole merged state and returns the hits not yet acknowledged, STOP first:
   - `flag` adds an advisor signal to the session (`advisor_signals`), and the journey continues.
   - `stop` ends the online quote. If every matched value is only `inferred` (from chat), `needs_confirmation` is true and the BFF first asks one templated confirmation question with two suggestions. "Yes" commits the value as `confirmed`; the next check finds a firm hit and the STOP stands. "No" reverts the value, and the agent gets `input.kind = action` with `action_id = rule_correct`.
   - A `stop` that stands closes the open surfaces and renders `end_of_form`. Sending it creates a hand-off row in Cosmos `handoffs` and emits `handoff.created`.
   - When the check ends the turn (a question or a STOP), the agent is not invoked for a form, document or confirmation input. On the chat path, where the memory delta commits mid-turn, the BFF drops the main agent's in-flight reply instead of forwarding it.
   - The BFF stores each handled hit's fingerprint in the session (`acknowledged`), so the same match is not reported again at every commit. A corrected or firmed-up value has a new fingerprint. Every new hit is logged as `rules.hit` and added to `system_notes`.
7. **Preferences are session settings, not catalog fields.** A non-null `session_preferences.answer_mode` from the memory agent replaces the stored value (the last statement wins). It is not subject to source precedence or confirmation.
8. **Edit-mode saves** (a reopened chip or `full_form`) commit with `source=form` and go through the same validation, deterministic-field and rule rules as any form submit.
