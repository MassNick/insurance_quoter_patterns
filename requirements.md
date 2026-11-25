# Requirements: auto insurance quoting agent

Status: design only. Last updated: 2026-11-24.
Sources: the user's brief, [`README.md`](README.md) (D1–D20, Q1–Q8), [`architecture.md`](architecture.md), [`contracts/`](contracts/) and [`prompts/`](prompts/).

**Conventions**
- **MUST** is required for the first release. **SHOULD** is expected; leaving it out needs a written reason.
- Each requirement has a testable acceptance criterion (AC).
- Field names, enums, patterns and `deterministic` flags come from [`contracts/state-catalog.json`](contracts/state-catalog.json). Event names come from [`contracts/events.md`](contracts/events.md).
- "Turn" means one customer response: a message, a form submit, a button action, a document upload or a confirmation result. Each turn produces one invocation of the hosted agent. Actions the BFF handles alone are not turns: reopening a form chip and saving it, a full-form save, confirming a rule hit, opening or sending the hand-off. They produce no invocation and no model call.

---

## 1. Functional requirements

### FR-INT: Intent phase

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-INT-1 | MUST | The agent finds out the customer's goal (`quote_goal`) at the start. | On a new session, the first assistant message has 2–4 suggestion bullets that map to `quote_goal` values. After the customer picks one or states a goal, `/quote/quote_goal` is set within that turn. |
| FR-INT-2 | MUST | The agent gets consent before it collects personal details. | In a scripted test, no `a2ui.message` renders a field marked `pii` while `/quote/contact_consent` is not `confirmed`. |
| FR-INT-3 | MUST | The agent records phase changes (`intent`, `gather`, `advise`) with `ui.set_phase`. | A `phase` event is emitted at each transition, and `sessions.phase` matches the last event. |
| FR-INT-4 | MUST | The customer experience is French first (`fr-CA`): greeting, replies, form labels, help and suggestions. The agent switches to English when the customer writes in English. The developer trace drawer stays in English (D12). | A new session greets in French and shows French labels (`label_fr`) and suggestions. An English message gets English replies. The trace drawer shows English in both cases. |

### FR-GAT: Gather phase

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-GAT-1 | MUST | The agent collects every `required_for_quote` field in this order: policyholder and contact, address, vehicles, drivers, claims and convictions, driver-vehicle assignments. | `create_quote` is not called while the BFF completeness check fails. If it is called directly, it returns `missing_fields`. |
| FR-GAT-2 | MUST | Addresses are validated with `quoting.identify_address`. | A partial address or postal code shows at most 5 candidates. Picking one fills `address_line1`, `city`, `province_code` and `postal_code` with `source=lookup`. |
| FR-GAT-3 | MUST | Vehicles are identified with `quoting.lookup_make_model_year`, `quoting.identify_vehicle_type` and `quoting.decode_vin`. | An invalid year/make/model combination cannot be submitted. A valid VIN fills year, make and model. `vehicle_type` is proposed from make and model. |
| FR-GAT-4 | MUST | A session supports several vehicles (up to 25) and drivers (up to 10). | A test session with 3 drivers and 2 vehicles stores `/drivers/0..2` and `/vehicles/0..1`, and each shows separately in the States tab. |
| FR-GAT-5 | MUST | When the memory agent reports a conflict, the agent asks which value is right before moving on. | With a conflict on `/vehicles/0/vehicle_year`, the next assistant turn asks about it and opens no new topic. |
| FR-GAT-6 | SHOULD | The agent uses signals to ask better questions. | With the signal `multi_vehicle_likely` (3 drivers, 1 car), the next turn asks whether there is another vehicle. |
| FR-GAT-7 | MUST | Gathering is text first (D13). The agent asks in chat by default, one question per turn. It opens a small subject form (one subject: Vous, Véhicule, Conducteurs or Antécédents; about 5 fields at most) only when the subject still has several structured fields or the customer asks for forms. The driver-vehicle assignment always uses the AssignmentBoard. | In the golden set: no subject form has more than 6 visible fields (repeater rows count once); at most one chat question per turn when no form is open; no form opens for a subject with a single missing field unless `answer_mode = forms`. |
| FR-GAT-8 | MUST | The system flags `claim_question_asked`, `conviction_question_asked` and `denied_or_cancelled_question_asked` are set when the question is shown. | After the underwriting form is shown, all three flags are `true` with `source=system`. |
| FR-GAT-9 | MUST | Right after the intent is identified and consent is confirmed, the agent offers **once** to read documents (driver's licence, registration certificate, current insurance policy) to pre-fill. If the customer agrees, it opens the DocumentDrop form. Without documents, it gathers through chat and subject forms (D13). | In the golden set, the offer appears in the first gather turn of every session and never again. Declining it opens no documents form. |
| FR-GAT-10 | MUST | The agent never asks the customer to choose between text and forms. It says once that they can answer by text or ask for forms. It follows a stated preference: `answer_mode = text` means no forms except the AssignmentBoard; `forms` means subject forms (D14). | A rubric check finds no "text or form?" question. With `answer_mode = text`, the only surface opened before the quote is `assignments` (plus documents if accepted). With `forms`, each remaining subject opens as a form. |

### FR-ADV: Advise phase

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-ADV-1 | MUST | Prices come only from `quoting.create_quote` or `quoting.modify_quote`. The agent never invents a price. | An evaluation check finds every amount in assistant text in a `quotes` record of the same session. |
| FR-ADV-2 | MUST | The agent recommends one offer and gives a 2–3 sentence reason tied to what the customer said. | In the golden set, an LLM judge scores the recommendation as specific and grounded in session facts in at least 90% of advise turns. |
| FR-ADV-3 | MUST | Offers are shown with `a2ui.render_offers`: Essential, Plus and Premium cards with a select button and an adjust panel (deductible, add-ons). | After the first quote, an `offers-{quote_id}` surface is created and selecting a card sets `/underwriting/offer_type`. |
| FR-ADV-4 | MUST | Quote changes create a new version and keep earlier versions. | A deductible change creates version n+1. Version n stays readable and moves to Past quotes. |
| FR-ADV-5 | MUST | Coverage explanations come from `knowledge.knowledge_base_retrieve` with a citation. With no answer, the agent says so and offers a callback. | Every coverage explanation in the golden set has at least one citation, and the groundedness evaluator passes. |
| FR-ADV-6 | SHOULD | The agent mentions 1–2 relevant discounts or protections, and offers a home quote when `interested_by_property_quote` is true. | Covered in the golden set by a rubric check. |
| FR-ADV-7 | SHOULD | The agent can offer an advisor callback (`callback_slot`). | The callback form stores `/underwriting/callback_slot`, and the change is logged in `state_changes`. |

### FR-MEM: Memory agent

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-MEM-1 | MUST | The memory agent runs on every turn, concurrently with the main agent. It receives the user message (or a text rendering of a structured input), the last assistant message and the current state. | Each `turn.completed` shows `model_calls ≥ 2`, and the trace shows `invoke_agent memory` and `invoke_agent main` overlapping in time. |
| FR-MEM-2 | MUST | Its output is only JSON that validates against `memory-output.schema.json` (strict `response_format`): `state_changes`, `signals`, `conflicts` and `intent`. | Over 1,000 test turns, 0 schema-validation failures reach the BFF. |
| FR-MEM-3 | MUST | Paths resolve to catalog keys, and values are normalized: enum codes, ISO dates, integers, `A1A 1A1` postal codes. | The BFF rejects and logs any path that does not exist in the catalog. Rejections are below 0.5% of changes. |
| FR-MEM-4 | MUST | It never overwrites a `confirmed` field. It reports a conflict instead. | A test that contradicts a form-confirmed value produces a conflict and no state change. |
| FR-MEM-5 | MUST | It returns `signals`: conclusions from what the customer said, each with `code`, `message`, `confidence`, `related_paths` and `suggested_follow_up`. | "3 drivers and one car" produces `multi_vehicle_likely`. The golden set covers every signal code at least once, with recall of at least 80%. |
| FR-MEM-6 | MUST | The BFF applies the output: it updates Cosmos DB and its own session state, then emits `state.delta` and `signals` to the frontend. | After a `memory.result`, `sessions` and the BFF cache hold the same `_etag`, and the States tab shows the change within 300 ms. |
| FR-MEM-7 | MUST | The memory agent does not emit changes with confidence below 0.6; it raises a signal instead. If such a change arrives anyway, the BFF stores it as `inferred` and never uses it for pricing. | A test that injects a 0.5 change shows it as `inferred` and excludes it from `create_quote` input. |
| FR-MEM-8 | MUST | It sets `intent.off_topic` for messages outside auto-insurance quoting. | Off-topic test prompts are flagged with at least 95% recall. |
| FR-MEM-9 | MUST | It never computes derived fields (`years_licensed`, `years_as_principal_driver`). | No memory output in the test set touches those paths. The BFF computes them. |
| FR-MEM-10 | MUST | It returns `session_preferences.answer_mode` (`text` or `forms`) only when the customer states how they want to answer, and null otherwise. The BFF stores a non-null value in the session document (`session_preferences.answer_mode`), emits `session.preferences`, and passes it to the main agent as PREFERENCES (D14). | "Je préfère répondre par écrit" sets `text`; "montrez-moi un formulaire" sets `forms`; an ordinary answer returns null and leaves the stored value unchanged. Precision of at least 95% on the preference test set. |

### FR-SES: Session persistence

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-SES-1 | MUST | The hosted agent's session history is persisted in Cosmos DB `agent_history` (partition key `/session_id`). | After a hosted session is recycled, the next turn continues with full context. |
| FR-SES-2 | MUST | The BFF keeps the session state in its own cache and in Cosmos `sessions`. Cosmos is the source of truth. | Killing the BFF replica mid-session loses no state. The next turn reloads from Cosmos. |

### FR-A2UI: Agent-generated forms

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-A2UI-1 | MUST | The agent shows forms by calling `a2ui.render_form` with a FormSpec (`form-spec.schema.json`) on the A2UI MCP server, which is connected to the agent's toolbox. The server compiles the FormSpec into A2UI 1.0: one `createSurface` carrying components and data model. The agent never writes raw A2UI. | The tool output is an MCP embedded resource (`application/a2ui+json`, audience user) holding one `createSurface` with `"version": "1.0"`, `components` and `dataModel`; ids are identifiers and bindings use `@path`. An invalid FormSpec returns a tool error and shows no partial UI. |
| FR-A2UI-2 | MUST | Forms can capture **every** state key in the catalog, for any entity. | A test compiles one FormSpec per entity that contains all its keys. Every key renders a widget and none reports "unsupported type". |
| FR-A2UI-3 | MUST | Contact forms capture first name, last name, email (format check), phone (North American pattern) and phone type. | An invalid email or phone blocks submit and shows an inline message. |
| FR-A2UI-4 | MUST | Vehicle year/make/model uses one cascading picker with automatic validation: year range 1950 to next year, make list for the year, model list for the make, validated by `lookup_make_model_year`. | An invalid combination cannot be submitted. Changing the make clears an invalid model. |
| FR-A2UI-5 | MUST | Driver forms capture:<br>• marital status, employment status, occupation, education level<br>• driver's licence number (pattern, deterministic), age when licensed, licence year<br>• age when the driver became a principal driver, years with current insurer<br>• heavy-vehicle experience, foreign licence, limited-use frequency | Each field renders with catalog validation. `years_licensed` and `years_as_principal_driver` are shown read-only (computed). |
| FR-A2UI-6 | MUST | Forms are versatile. For each field the agent can say what it wants (`hint`), override label and options, and show in-place information: the form `intro`, and a prefill `note` such as "You said 'a 2019 Civic' in chat". | A FormSpec with a hint, an intro and a prefill note renders all three in place. |
| FR-A2UI-7 | MUST | The agent can autofill forms. Values from chat, documents or lookups prefill fields and carry a provenance badge. While a form is open, new memory-agent values on bound paths fill in live (`updateDataModel`). Fields the customer edited are never overwritten; the new value shows as a suggestion. | Typing "she got her licence at 17" in chat while the drivers form is open fills `age_obtained_license` with a "from chat" badge. If the customer had already typed 18, the 18 stays and 17 is offered as a suggestion. |
| FR-A2UI-8 | MUST | The agent can add buttons and fields for atypical situations with `repeat` and `field_actions`. Example: 10 identical cars give one year/make/model field plus 10 VIN fields. Each VIN field has an Upload VIN photo button that sends the image to the document agent and autofills that VIN. | A FormSpec with `repeat.count = 10` renders 10 VIN rows, each with an upload button. Uploading a photo on row 4 fills only row 4, after the malware scan and extraction. |
| FR-A2UI-9 | MUST | Fields can be conditional (`visible_if`). | `weeks_outside_quebec` appears only after `outside_quebec` is answered Yes. |
| FR-A2UI-10 | MUST | An open form can be changed with `a2ui.update_form` (add or remove fields, prefill, errors, intro) and closed with `a2ui.close_form`. | Each call results in exactly one `updateComponents`/`updateDataModel` or `deleteSurface` for that surface. |
| FR-A2UI-11 | MUST | Validation runs in the browser (A2UI checks) and again in the BFF (catalog). A server-side failure keeps the form open with errors under `/_errors`. | A tampered submit with an invalid postal code is rejected by the BFF, and the form shows the error. |
| FR-A2UI-12 | MUST | Custom components exist for lists, assignments and documents: `Repeater` (additional drivers, claims, convictions), `AssignmentBoard` (`driver_vehicle_assignments`; replaces `AssignmentMatrix`, D17), `DocumentDrop` (D18) and `OfferCards`. | Each component renders from a compiled FormSpec in the renderer test suite. |
| FR-A2UI-13 | MUST | At least 20 distinct agent-generated form patterns are specified and demonstrated. | `demo/form-gallery.html` lists at least 20 forms, each with its FormSpec, the compiled A2UI messages and the open/close animation. |
| FR-A2UI-14 | MUST | **AssignmentBoard** (widget `assignment_board`, D17). People are dragged from a pool onto vehicle cards. Each card has one "Conducteur principal" slot (exactly 1) and a "Conducteurs occasionnels" slot (0 or more). Tap-to-place and keyboard alternatives exist, and the page scrolls automatically while dragging on a phone. It writes `/household/driver_vehicle_assignments` with role `principal` or `occasional`; check `onePrincipalPerVehicle`. | 3 drivers and 2 vehicles: submit stores one `principal` per vehicle and any `occasional` entries, and a vehicle without a principal blocks submit with a message. The same result is reachable with the keyboard only and with taps only. On a 375 px phone, dragging to an off-screen card scrolls the page. |
| FR-A2UI-15 | MUST | **Form chips** (D16). Every surface leaves a chip in the transcript (`Formulaire : <titre>`) with state `open`, `submitted`, `closed` or `sent`. Clicking a `submitted` or `closed` chip sends `ui.surface_reopen`. The BFF renders the same FormSpec again through `mcp-a2ui`, prefilled from the current committed state, in edit mode, with no model call and no new turn. Saving goes through the normal BFF commit and rule check, and appends a system note that the next turn's agent receives. | Reopening a submitted vehicle form shows the committed values. Saving a changed usage writes `state_changes` with `source=form`, runs the rule check once, emits no `turn.started`, and the next invocation carries the system note. Chips lock after a hand-off is sent. |
| FR-A2UI-16 | MUST | **Full-form mode** (D20). The header toggle "Conversation \| Formulaire complet" shows the whole quote as one long form, prefilled with everything known (chat, documents, forms) and grouped by catalog entity. Saving commits with `source=form` and runs the rule check. Switching back returns to the conversation. | With a session holding chat, document and form values, the full form shows all of them under their entity. Saving one change commits only that path with `source=form`, runs the rule check, and makes no model call. Switching back shows the conversation where it was. |

### FR-CHAT: Chat experience

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-CHAT-1 | MUST | Three animated dots show while the agent responds. They appear on send and are replaced by the first streamed text. | Dots are visible within 150 ms of send and are removed on the first `text.delta`. |
| FR-CHAT-2 | MUST | Responses stream token by token (`text.delta`) to reduce time to first token. | Text appears progressively. No `text.delta` is buffered for more than 50 ms in the BFF. |
| FR-CHAT-3 | MUST | Suggested replies and actions appear as up to 4 clickable bubbles above the composer, each at most 40 characters. They are generic and fit any customer at that step: never personal data, names, emails, vehicle models or values the customer has not given (D15). A click sends the reply, or triggers the action for `kind=action`. | Clicking a bubble sends `user.message` with `via: "suggestion"`, and bubbles from earlier turns disappear. In the golden set, no suggestion contains a value from the session state or a proper name. |
| FR-CHAT-4 | MUST | Replies are short: at most 3 short paragraphs and at most one question per turn when no form is open. | A rubric check on the golden set passes on at least 95% of turns. |
| FR-CHAT-5 | MUST | A dropped stream resumes without duplicated text. | After a network drop, the reconnect with `Last-Event-ID` replays the missing events only. |
| FR-CHAT-6 | SHOULD | Errors show an inline retry bubble. | A `retryable` `error` event shows Retry, which resends the same turn. |
| FR-CHAT-7 | MUST | The BFF validates every `suggestions` event before relaying it. It drops items that contain a PII pattern (email, phone, postal code, VIN, licence number), a proper name, a value the customer has not given, or more than 40 characters, keeps at most 4, and logs each drop (D15). | A test stream with "Oui, c'est marie@example.com" and a 60-character label relays neither item and logs both. An all-invalid list shows no bubbles. |

### FR-DET: Deterministic fields

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-DET-1 | MUST | When a field with `deterministic: true` changes in the BFF, from any source, the change is held as `pending_confirmation` and `confirm.required` is emitted automatically. Today these fields are `contact_consent`, `driver_license_number`, `has_claims`, `has_convictions_in_past_three_years` and `was_denied_or_cancelled`. | A memory-agent change to `contact_consent` produces `confirm.required` in the same turn and no commit. |
| FR-DET-2 | MUST | The frontend shows a two-step confirmation popup. The value commits only after both steps are accepted. Example: agreeing in chat to share information opens the double-confirm popup automatically. | Clicking "Yes, I agree" in chat opens step 1. Only after step 2 does `/quote/contact_consent` become `confirmed`. |
| FR-DET-3 | MUST | A refusal at either step reverts the value, logs `confirm.rejected`, and informs the agent (`input.kind=confirmation_result`). | After a refusal, the value is unchanged and the next agent turn acknowledges the refusal. |
| FR-DET-4 | MUST | Pending confirmations expire after 900 s. | A pending change older than 900 s is discarded and logged. |
| FR-DET-5 | MUST | Each confirmation stores the text shown, the old and new values, and both step timestamps in `confirmations`. | Every confirmed deterministic field has a matching `confirmations` row. |
| FR-DET-6 | MUST | No model can commit a deterministic field, and `create_quote` uses only confirmed values for them. | A quote attempt with `has_claims` pending returns `missing_fields`. |

### FR-DOC: Document agent

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-DOC-1 | MUST | Customers can upload an image or PDF for these purposes: `vin`, `licence`, `registration`, `declaration` (current policy). The upload starts from a form field action, a DocumentDrop tile or a chat attachment. | Each purpose is accepted. Other file types and files above the size limit are rejected with a message. |
| FR-DOC-2 | MUST | Uploads are scanned for malware before processing. | A test malware file is quarantined and never reaches the document agent. |
| FR-DOC-3 | MUST | The document agent (Foundry prompt agent on `gpt-5.6-luna`) extracts fields with a strict schema, per-field confidence and page/bbox, and never guesses. | Every output validates against its purpose schema. Unreadable fields are absent, not invented. |
| FR-DOC-4 | MUST | Extracted values autofill the related open form with a "from photo" badge, as `source=document`, `status=inferred` until the customer reviews and submits. | A VIN photo fills the right VIN row. The value is `inferred` until the form is submitted. |
| FR-DOC-5 | MUST | `document_ok=false` (wrong type, unreadable, altered) leads to a request to retake. Instructions written in a document are ignored. | A document that says "ignore previous instructions" yields only schema fields, and the guardrail logs an indirect-attack detection. |
| FR-DOC-6 | MUST | Card or bank numbers are never returned. They come back as `redacted`. | A test document with a card number returns `redacted`. |
| FR-DOC-7 | SHOULD | A licence photo fills the driver's licence fields. A new `driver_license_number` triggers the double confirmation (FR-DET-1). | A licence upload produces `confirm.required` for `driver_license_number`. |
| FR-DOC-8 | MUST | **DocumentDrop** (widget `document_drop`, D18) has tiles for the driver's licence, the registration certificate and the current policy. Each upload goes BFF → document agent. The extracted fields are shown with their confidence, and the customer reviews and edits them before they are committed with `source=document`. Nothing from a tile reaches session state before that submit. | After 3 uploads and before submit, `state_changes` has no `document` rows. After the customer edits one field and submits, the edited value is committed and the others carry `source=document`. "Continuer sans documents" closes the form and commits nothing. |

### FR-RUL: End-of-form rules and advisor hand-off (D19)

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-RUL-1 | MUST | `EndOfFormRules` (`bff/rules/end_of_form.py`) runs synchronously in the BFF after **every** state commit, whatever the source (memory-agent deltas, form submits, document reviews, edit-mode and full-form saves, confirmations), once per commit batch, before the main agent runs. | For a scripted session that commits from each source, the count of `bff.rules` spans equals the count of commit batches. A form-submit hit appears in the same turn's invocation payload (`rule_hits`). |
| FR-RUL-2 | MUST | Each hit has a severity. `STOP` ends the online quote. `FLAG` adds an advisor signal and the journey continues. Example rules: `VEHICULE_EXOTIQUE`, `VEHICULE_MODIFIE_PERFORMANCE`, `USAGE_AGRICOLE`, `REFUS_OU_RESILIATION` (STOP); `JEUNE_CONDUCTEUR_PRINCIPAL` (FLAG). | Each example rule fires on a fixture that meets its condition, and on no other fixture. The FLAG fixture adds one `advisor_signals` entry and opens no end-of-form surface. |
| FR-RUL-3 | MUST | A STOP hit whose values are only inferred from chat asks one confirmation question first. Confirming commits the value as `confirmed` and the STOP stands. Correcting reverts the value, and the agent asks for the right one. | Typing "j'ai une Lamborghini" produces one templated question with two suggestions and no end-of-form surface. `rule_confirm` opens it; `rule_correct` reverts `/vehicles/0/vehicle_make` and the next turn asks for the vehicle. |
| FR-RUL-4 | MUST | On a STOP that stands, the BFF closes every open surface and renders the end-of-form surface: reason, rule id, an editable short summary of the conversation, contact fields and consent to transmit. After it, the agent opens no form and `get_quote` is refused. | After a STOP, every open surface gets `deleteSurface` and one `end_of_form` surface opens. On the chat path the main agent's in-flight reply of that turn is not forwarded; for a form submit the agent is not invoked. `get_quote` returns "not ready". |
| FR-RUL-5 | MUST | Sending the end-of-form surface requires the consent box. It creates an advisor hand-off with a reference such as `CNS-123456` that carries the summary as the customer edited it, and emits `handoff.created`. | Send without consent shows an inline error. Send with consent writes one `handoffs` row with the edited summary, contact details, rule id and consent timestamp, and the confirmation shows the same reference. |
| FR-RUL-6 | MUST | The rule check, the confirmation question, the end-of-form surface and the hand-off make no model call. | The trace for these steps has no `chat` span, and no `turn.completed` is emitted for them. |
| FR-RUL-7 | MUST | Every hit is auditable: a `rules.hit` event with `rule_id`, `severity`, `paths`, `inferred` and `reason` (no matched values), and a system note for the next turn. A handled hit is acknowledged by its fingerprint and not reported again until the matched values change. "Modifier mes réponses" closes the surface; a later commit that clears the hit lifts the STOP, and the earlier hits stay in the log. | An auditor can list every hit for a session with its paths and outcome. After correcting the vehicle, the quote can continue, and the first hit is still in `events`. |
| FR-RUL-8 | SHOULD | The header button "Parler à un conseiller" opens the same hand-off surface with the reason "customer request" and no rule id. | Clicking it at any step opens the surface with prefilled contact fields and no model call. |

### FR-QUO: Quotes

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-QUO-1 | MUST | Each quote is stored with `quote_id`, `version`, offers and timestamp. The Quotes tab shows the current quote and past ones. | After 2 modifications, the tab shows the current v3 and past v1 and v2. |
| FR-QUO-2 | MUST | A modification shows the monthly delta against the previous version. | v2 shows "+$X/month" or "−$X/month" against v1. |
| FR-QUO-3 | MUST | Each quote stores the hash of its input state snapshot. | The hash matches a recomputation from `state_changes` up to the quote's turn. |
| FR-QUO-4 | MUST | `create_quote` and `modify_quote` read committed state server-side through the signed `session_ref`. The model never passes field values. | Tool arguments in traces contain no state values, and a forged or expired `session_ref` is rejected. |

### FR-LOG: Logging

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-LOG-1 | MUST | Every inbound and outbound event (the whole conversation, every A2UI message, every tool event) is written to Cosmos `events` before it is forwarded. | For a test session, `events` replays the full conversation and all forms in order (`turn`, `seq`). |
| FR-LOG-2 | MUST | Every state change is appended to `state_changes` with path, old, new, source, status, turn, actor and `trace_id`. | The count of `state.delta` changes equals the count of `state_changes` rows. |
| FR-LOG-3 | MUST | Every log row carries `session_id`, `turn`, `seq` and `trace_id`. | A query joins a quote to its turn, its state changes and its trace. |
| FR-LOG-4 | SHOULD | The change feed exports `events`, `state_changes` and `confirmations` to immutable Blob storage, pseudonymized. | The exported blobs have a time-based immutability policy and no clear-text PII. |

### FR-OBS: Observability

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-OBS-1 | MUST | Agent traces and tool usage go through OpenTelemetry (GenAI semantic conventions) into Application Insights. There is one trace per turn from the browser through the BFF, the hosted agent and the toolbox to each MCP server. | One `trace_id` contains the frontend span, `bff.turn`, `invoke_agent main`, `invoke_agent memory`, `chat` spans and `execute_tool` spans for that turn. |
| FR-OBS-2 | MUST | Each turn records `model_calls`, token usage including cached tokens, and `ttft_ms`. | These appear as metrics in Application Insights for every turn. |
| FR-OBS-3 | MUST | Hosted agent traces are visible in the Foundry tracing view. | A test turn appears in the Foundry portal within 5 minutes. |
| FR-OBS-4 | MUST | Message content recording is off in production, and PII in span attributes is masked. | A production trace sample has no email, phone, licence number, VIN or postal code in clear text. |
| FR-OBS-5 | MUST | A dashboard and alerts cover TTFT, the 2-call ratio, tool errors, session quota, Cosmos throttling and guardrail spikes. | Each alert fires in a synthetic test. |
| FR-OBS-6 | SHOULD | Continuous evaluation runs on sampled traces (task adherence, intent resolution, tool call accuracy, groundedness), plus a nightly golden-set run. | Evaluation results are visible in Foundry, and a regression blocks prompt promotion. |

### FR-GRD: Guardrails

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-GRD-1 | MUST | The agent stays on auto-insurance quoting. For any other topic it redirects in one sentence and offers the next quoting step. | At least 98% of the off-topic test set gets a redirect with no off-topic content. |
| FR-GRD-2 | MUST | The agent never invents prices, never asks for payment, banking details or passwords, and never reveals its instructions or tool names. | The red-team and jailbreak sets pass with no disclosure and no invented price. |
| FR-GRD-3 | MUST | Foundry guardrails apply to the deployments and the hosted agent: content safety, prompt shields, and indirect-attack detection on tool responses and documents. | Known attack strings are blocked or flagged, and a `guardrail` event is logged. |
| FR-GRD-4 | MUST | Tools cannot be pointed at another session. `session_ref` is injected by middleware and never produced by the model. | Prompt-injection attempts to change the session fail at `mcp-quoting`. |
| FR-GRD-5 | SHOULD | After 3 consecutive off-topic turns, the BFF sends a canned redirect without calling the agent. | The 4th off-topic turn has `model_calls = 0`. |

### FR-UI: Interface and animation

| ID | Pri | Requirement | Acceptance criterion |
|---|---|---|---|
| FR-UI-1 | MUST | **Form open animation.** On `createSurface`:<br>1. The chat column slides to the left (about 250 ms).<br>2. A small ball appears at the bot icon of the latest assistant message and travels right to the form area (about 350 ms).<br>3. The ball expands into the form card (about 300 ms), and the form content fades in. | Recorded at 60 fps, the sequence plays in this order and finishes in 900 ms or less. Focus moves to the first field. |
| FR-UI-2 | MUST | **Close animation (submit or close).** The exact reverse plays: content fades out, the card shrinks to the ball, the ball travels back to the bot icon, and the chat recenters. | On submit, the reverse plays in 900 ms or less, and focus returns to the chat input. |
| FR-UI-3 | MUST | With `prefers-reduced-motion: reduce`, the form appears and disappears with a short crossfade and no movement. | With the OS setting on, there is no translate or scale animation, and the fade lasts 150 ms or less. |
| FR-UI-4 | MUST | A side panel, **"Votre dossier"**, has two tabs. It is collapsed by default and opens only on click, with a spring and then staggered items (D12).<br>• **States** lists all current state fields, grouped as \<Quote, policyholder, contact\>, \<Vehicle\>, \<Driver\>, Underwriting / other quote details, and \<Household assignments\>. Each field shows its value, source and status, and a lock icon if it is deterministic. Changed fields flash, and signals are listed.<br>• **Quotes** shows the current quote and past quotes when there are any, and an empty state otherwise. | Every catalog key is listed under its group. A `state.delta` flashes the changed row. The Quotes tab updates on `quote.created` and `quote.updated`. On load the panel is collapsed; it opens only on a click, and with reduced motion it opens with no spring. |
| FR-UI-5 | MUST | Step chips show Documents, Vous, Véhicules, Conducteurs, Antécédents and Soumission, derived from state and surfaces. The trace drawer keeps the Intent, Gather and Advise stepper (D12). | Committing the last required field of a subject marks its chip done. The trace stepper follows `phase` events. |
| FR-UI-6 | MUST | The double-confirm popup is accessible. It traps focus, Esc cancels the current step, and each step has a clear title and states the old and new values. | It passes keyboard-only and screen-reader checks. |
| FR-UI-7 | SHOULD | Demo and dev builds have a trace drawer, in English, showing tool calls, A2UI messages, state deltas, rule hits and model calls per turn. Production builds do not. | The drawer is present in the demo and absent in a production build. |
| FR-UI-8 | MUST | **Phone layout at 600 px wide or less** (D12): the form covers the conversation while the suggestions and the composer stay usable; "Votre dossier" and other panels become sheets; the left rail becomes a bottom tab bar. From 601 to 900 px, the form opens as a full-width sheet with the ball motion adapted vertically, and the panel becomes a drawer. | At 375 × 812, chat, form and panel are usable with no horizontal scroll, and a suggestion can be clicked and a message sent while a form is open. |
| FR-UI-9 | MUST | The header is dark green, with the logo, the toggle "Conversation \| Formulaire complet" (FR-A2UI-16) and the button "Parler à un conseiller" (FR-RUL-8). A left rail on desktop becomes a bottom tab bar on phones (D12). | Both controls work by keyboard and screen reader, at desktop and phone widths. |
| FR-UI-10 | MUST | Field labels are plain French from `label_fr`, with help from `help_fr` shown on hover and on keyboard focus (D12). | Every rendered field in a French session has a French label. Help appears on hover and on focus, and is announced to screen readers. |
| FR-UI-11 | SHOULD | The bot avatar is a mini Desjardins-style hexagon with eyes. The ball of the form animation leaves from it (FR-UI-1) (D12). | Visual review against the Variation C prototype. |

---

## 2. Non-functional requirements

| ID | Pri | Requirement | Measure / acceptance |
|---|---|---|---|
| NFR-PERF-1 | MUST | Time to first token, from send to the first rendered text, is at most 1.5 s at p95. | Frontend real-user monitoring metric, plus `turn.completed.ttft_ms`. |
| NFR-PERF-2 | MUST | The animated dots appear at most 150 ms after send. | Real-user monitoring. Dots are shown locally, with no network wait. |
| NFR-PERF-3 | MUST | At least 85% of turns use exactly 2 model calls (memory + main). The hard cap is 3, allowing at most one narrate round. | Distribution of `turn.completed.model_calls` per hour. Alert below 85%. See Q1. |
| NFR-PERF-4 | MUST | The form animation starts at most 100 ms after `createSurface` arrives. The whole sequence takes at most 900 ms. | Performance marks in the frontend. |
| NFR-PERF-5 | SHOULD | Lookup tools take at most 800 ms at p95, and `create_quote` at most 3 s at p95. | `execute_tool` span durations. |
| NFR-PERF-6 | SHOULD | The end-of-form rule check adds at most 50 ms at p95 to a commit. A reopened chip or the full form renders at most 500 ms at p95 after the click (no model call). | `bff.rules` and `bff.reopen` span durations. |
| NFR-COST-1 | MUST | The main agent prompt plus all tool descriptions stay **under 30,000 tokens**, measured in CI with the deployed model's tokenizer. | The CI check fails the build at 30k or more. The current estimate is about 4k ([`prompts/token-budget.md`](prompts/token-budget.md)). |
| NFR-COST-2 | MUST | The conversation is cached to lower cost: cached-input-token ratio of at least 70% from turn 3 onward. | `cached_input / input` per call, per session, in Application Insights. |
| NFR-COST-3 | SHOULD | Cost per completed quote stays under a target set after pricing is verified (architecture V14). | A workbook tile computes it daily. |
| NFR-AVL-1 | MUST | The quoting journey (frontend, BFF, hosted agent, tools) is available 99.9% per month. | Synthetic end-to-end probe every 5 minutes. The composite SLA is reviewed against component SLAs. |
| NFR-AVL-2 | SHOULD | Disaster recovery to Canada East with RPO ≤ 5 min and RTO ≤ 1 h. | A game-day failover test twice a year. |
| NFR-SCL-1 | MUST | The system handles the planned peak of concurrent sessions within the hosted-agent session quotas. The defaults are 2,000 concurrent sessions per subscription per region in Canada Central and 1,000 in Canada East; increases are requested before campaigns. The BFF autoscales to 20 replicas, and Cosmos and Search autoscale. | A load test at 1.5× planned peak shows no `session_quota_exceeded` and meets NFR-PERF-1. |
| NFR-SEC-1 | MUST | No API keys or secrets in code, images, environment variables or pages. Entra managed identities and agent identities are used everywhere. Key Vault holds only the `session_ref` signing key. | A secret scan in CI finds nothing. Infrastructure review confirms keyless auth (`disableLocalAuth` where supported). |
| NFR-SEC-2 | MUST | The data plane is private: private endpoints for Foundry, Cosmos, Storage, Search, Key Vault and ACR. Public ingress goes only through Front Door WAF. | An Azure Policy compliance report. |
| NFR-SEC-3 | MUST | MCP servers accept only allow-listed Entra callers with the right audience and app role. | Calls with any other token get 401 or 403 in tests. |
| NFR-SEC-4 | MUST | The BFF meets OWASP ASVS Level 2. All inputs are validated against the catalog, uploads are scanned, and sessions are rate-limited. | A pen test before launch. |
| NFR-SEC-5 | SHOULD | A red-team run (Foundry cloud red teaming, preview) happens before each prompt-version release. | The report is attached to the release. |
| NFR-PRV-1 | MUST | The system complies with Quebec Law 25 and PIPEDA:<br>• consent before collection (FR-INT-2, FR-DET);<br>• purpose limitation and a privacy notice link in the UI;<br>• a privacy impact assessment before launch;<br>• a person in charge of personal information is named. | Signed privacy impact assessment. Privacy review checklist. |
| NFR-PRV-2 | MUST | All stored data (Cosmos, Storage, Search, Application Insights, Foundry standard-setup stores) stays in Canada. Where model inference is processed is decided explicitly (Q2). | Resource locations in the infrastructure. A Data Zone or Global deployment needs a documented decision. |
| NFR-PRV-3 | MUST | Retention follows the TTLs in [`architecture.md` §6.3](architecture.md#63-cosmos-db-containers). A deletion request is fulfilled within 30 days across all containers and `agent_history`. Exports are pseudonymized so they can stay immutable. | A deletion test finds no PII left for the session. |
| NFR-PRV-4 | MUST | Telemetry contains no clear-text PII (FR-OBS-4). | A sampled audit each month. |
| NFR-PRV-5 | MUST | Personal data reaches an advisor only through a hand-off the customer sent with the consent box checked. Suggested replies never contain personal data (FR-CHAT-7). | No `handoffs` row without a consent timestamp. A monthly sample of relayed suggestions has no PII. |
| NFR-ACC-1 | MUST | The interface meets WCAG 2.2 AA:<br>• keyboard access everywhere, with focus moving into a form on open and back to chat on close;<br>• labelled fields with announced errors;<br>• streamed replies announced once complete (`aria-live="polite"`), not token by token;<br>• bullets are real buttons or links;<br>• colour contrast at least 4.5:1. | An axe scan plus a manual screen-reader pass (NVDA and VoiceOver). |
| NFR-ACC-2 | MUST | The animation honours `prefers-reduced-motion` (FR-UI-3). | A manual check with the OS setting on. |
| NFR-ACC-3 | MUST | Every drag action has a single-pointer and a keyboard alternative (WCAG 2.2, 2.5.7 Dragging Movements): the AssignmentBoard and any DocumentDrop drop zone. | The AssignmentBoard test (FR-A2UI-14) passes with keyboard only and with taps only. |
| NFR-I18N-1 | MUST | The UI, forms, suggestions and agent replies are French first (`fr-CA`) and also support `en-CA`, with locale formats for dates, currency and numbers. The developer trace stays in English (D12). | French and English test journeys pass. The catalog carries `label_fr` and `help_fr` for every field and `enum_fr` for every enum option (Q6). |
| NFR-BRD-1 | MUST | The official Desjardins logo replaces the placeholder mark before any external showing of the client demo (D12). The brand team approves the build. | A release checklist item signed by the brand team. The build contains no placeholder mark. |
| NFR-AUD-1 | MUST | Every quote can be traced to its turn, its input state snapshot, its confirmations and its trace. | An auditor walkthrough on 10 sampled quotes. |
| NFR-AUD-2 | MUST | Every advisor hand-off can be traced to its rule hit (or the customer's request), the summary as sent, the contact details, the consent timestamp and the session's events. | An auditor walkthrough on 10 sampled hand-offs. |
| NFR-MNT-1 | MUST | The design is catalog-driven. Adding a state field changes only `state-catalog.json`: memory paths, the form compiler, BFF validation, the States tab and the demos all derive from it. A new widget type is the only reason to change code. | Adding a test field needs no code change and passes all tests. |
| NFR-MNT-2 | MUST | Prompts are versioned (`PROMPT_VERSION`). A new version is promoted only after the golden-set evaluation passes. | The pipeline blocks promotion on an evaluation regression. |
| NFR-MNT-3 | SHOULD | An A2UI version change (the design moved from v0.9.1 to 1.0 on 2026-11-24) touches only the compiler, the renderer and the BFF's A2UI relay. | A spike upgrade changes no FormSpec or prompt. |
| NFR-MNT-4 | MUST | End-of-form rules are code with tests in `bff/rules/end_of_form.py`. A rule change ships through the normal release with its fixtures and the golden-set evaluation (Q8). | Each rule has a positive and a negative fixture, and CI fails if a rule has none. |

---

## 3. Traceability: brief → requirements → artifacts

| User requirement (from the brief) | Requirement IDs | Shown in |
|---|---|---|
| Use Azure Foundry and Azure services, and the Azure agent SDK for agent development | NFR-SEC-1, NFR-SEC-2 | `architecture.md` §2, §4; `architecture.drawio`; `implementation-plan.md` |
| Exactly 1 hosted agent container, in Foundry | FR-MEM-1, FR-SES-1 | `architecture.md` §4.1 (D1); `architecture.drawio` |
| 2 model calls per user response (main quoter + memory) | FR-MEM-1, NFR-PERF-3 | `architecture.md` §8; trace drawer in `demo/journey.html` |
| Document AI on GPT 5.6-luna reads documents and extracts fields | FR-DOC-1 … FR-DOC-7 | `architecture.md` §4.3, §5 (c); `prompts/document-agent.md`; VIN upload in `demo/journey.html` and `demo/form-gallery.html` |
| Hosted agent session persisted in Cosmos DB | FR-SES-1 | `architecture.md` §6.3 (`agent_history`) |
| Memory agent: entities and conclusions; receives the user message and the current state each turn; outputs state changes as structured output | FR-MEM-1 … FR-MEM-4, FR-MEM-7, FR-MEM-9 | `contracts/memory-output.schema.json`; `prompts/memory-agent.md`; `architecture.md` §5 (a) |
| The BFF receives that state and updates Cosmos DB and its own state for the session | FR-MEM-6, FR-SES-2 | `contracts/events.md` (merge rules); `architecture.md` §6, §10 |
| A `signals` field for potential signals (3 drivers, 1 car → more cars) | FR-MEM-5, FR-GAT-6 | States tab, Signals section, in `demo/journey.html` |
| Main agent: Intent → Gather → Advise in one prompt | FR-INT-1 … FR-INT-3, FR-GAT-1 … FR-GAT-8, FR-ADV-1 … FR-ADV-7 | `prompts/main-agent.md`; phase stepper in `demo/journey.html`; `process-flow.md` |
| Tools: identify address, car type, make/model/year, create quote, modify quote | FR-GAT-2, FR-GAT-3, FR-ADV-1, FR-ADV-4, FR-QUO-4 | `contracts/tools.json`; `architecture.md` §5 (e) |
| Chat plus A2UI tools on an MCP server connected to the toolbox | FR-A2UI-1, FR-A2UI-10 | `architecture.md` §4.4, §7; `contracts/form-spec.schema.json` |
| Forms for name, phone, email, make/model/year with automatic validation, marital status, driver licence, education, age became principal driver, years with current insurer, and any listed state | FR-A2UI-2 … FR-A2UI-5, FR-A2UI-9, FR-A2UI-11, FR-A2UI-12 | `demo/form-gallery.html`; `contracts/state-catalog.json` |
| Very versatile forms: the agent says what it wants per field, plus in-place information | FR-A2UI-6 | `demo/form-gallery.html` (hints, intro, prefill notes) |
| Buttons and fields for atypical situations (10 cars → 1 make/model/year + 10 VIN fields with upload autofill) | FR-A2UI-8, FR-DOC-4 | Fleet form in `demo/form-gallery.html` |
| The agent can autofill forms | FR-A2UI-7, FR-DOC-4 | `demo/journey.html` (prefill and live autofill); `architecture.md` §7.3 |
| Recommended actions and responses as clickable bullets | FR-CHAT-3 | `demo/journey.html` |
| 3 animated dots while the agent responds | FR-CHAT-1, NFR-PERF-2 | `demo/journey.html` |
| Streamed responses to reduce time to first token | FR-CHAT-2, NFR-PERF-1 | `demo/journey.html`; `architecture.md` §9 |
| Cache the conversation to lower cost | NFR-COST-2 | `architecture.md` §10; `prompts/token-budget.md` |
| Agent prompt plus tool descriptions under 30k tokens | NFR-COST-1 | `prompts/token-budget.md` |
| Deterministic fields → automatic confirmation popup with double confirm (for example, consent to share information) | FR-DET-1 … FR-DET-6 | Consent popup in `demo/journey.html`; `architecture.md` §5 (d) |
| All changes and the whole conversation logged | FR-LOG-1 … FR-LOG-4, NFR-AUD-1 | `architecture.md` §15 |
| Agent traces and tool usage through an observability pipeline | FR-OBS-1 … FR-OBS-6 | `architecture.md` §13 |
| Basic guardrails keep the agent on quoting | FR-GRD-1 … FR-GRD-5 | `prompts/main-agent.md`; `architecture.md` §14; off-topic reply in `demo/journey.html` |
| HTML experience: dots, streaming, bullets, A2UI animation, side panel with States and Quotes tabs | FR-UI-1 … FR-UI-5, FR-CHAT-1 … FR-CHAT-3 | `demo/journey.html` |
| Architecture (Azure services and data flow) | — | `architecture.md`, `architecture.drawio` |
| Functional and non-functional requirements | — | This file |
| Detailed implementation steps | — | `implementation-plan.md` |
| BPMN-style process flow with the tool calls and A2UI calls at every step | — | `process-flow.md`, `process-flow.html` |
| A skill that rebuilds the artifacts from refined requirements and tools | NFR-MNT-1 | `skill/SKILL.md` and its templates |
| Second HTML gallery with at least 20 custom A2UI forms, each showing the animation | FR-A2UI-13, FR-UI-1, FR-UI-2 | `demo/form-gallery.html` |
| D12: client demo UI for Desjardins Assurances (Variation C), French first, phone layout, official logo before external showing | FR-INT-4, FR-UI-4, FR-UI-5, FR-UI-7 … FR-UI-11, NFR-I18N-1, NFR-BRD-1 | `architecture.md` §7.5; `demo/ui-variations.html` |
| D13: text-first gathering, small subject forms, document offer once | FR-GAT-7, FR-GAT-9 | `prompts/main-agent.md`; `architecture.md` §4.2, §5 (f); `process-flow.md` (Gather) |
| D14: answer-mode preference captured by the memory agent | FR-GAT-10, FR-MEM-10 | `contracts/memory-output.schema.json`; `prompts/memory-agent.md`; `contracts/events.md` (`session.preferences`) |
| D15: generic suggested replies, validated by the BFF | FR-CHAT-3, FR-CHAT-7, NFR-PRV-5 | `contracts/tools.json` (`ui.suggest_replies`); `prompts/main-agent.md`; `architecture.md` §14 |
| D16: reopenable form chips | FR-A2UI-15 | `contracts/events.md` (`ui.surface_reopen`); `architecture.md` §5 (g), §7.4 |
| D17: AssignmentBoard replaces the matrix | FR-A2UI-12, FR-A2UI-14, NFR-ACC-3 | `architecture.md` §7.2 |
| D18: DocumentDrop with review before commit | FR-DOC-1, FR-DOC-8, FR-A2UI-12 | `architecture.md` §4.3, §5 (f) |
| D19: end-of-form rules in the BFF and advisor hand-off | FR-RUL-1 … FR-RUL-8, NFR-PERF-6, NFR-AUD-2, NFR-MNT-4 | `bff/rules/end_of_form.py`; `architecture.md` §5 (h), §6.4; `contracts/events.md` (`rules.hit`, `handoff.created`) |
| D20: full-form mode | FR-A2UI-16 | `architecture.md` §5 (g), §7.4 |

---

## 4. Open questions

Copied from [`README.md`](README.md). The design uses the stated assumption until each question is answered.

| # | Question | Assumption in use | Requirements affected |
|---|---|---|---|
| Q1 | Does "2 model calls per user response" mean two LLM agents per turn, or two completions? | Two completions per turn, enforced with terminal tools and pre-executed button actions. A free-text request that needs a quote or a knowledge-base answer narrated costs 3. If the rule is a hard cap, those cases would show the result first and narrate it on the next turn. | NFR-PERF-3, FR-ADV-1, FR-ADV-4, FR-ADV-5 |
| Q2 | Which model deployments? | `gpt-5.6-luna` for documents is given. The main and memory deployments are placeholders. Availability in Canada regions is not verified, and a Data Zone or Global deployment may be needed. | FR-DOC-3, NFR-PRV-2 |
| Q3 | Is the quote engine an existing rating API? | The `quoting` MCP server is assumed to wrap one. The demo uses a fake rating formula. | FR-ADV-1, FR-QUO-* |
| Q4 | How do customers authenticate? | An anonymous session that can later sign in with Microsoft Entra External ID. | NFR-SEC-*, NFR-PRV-3 |
| Q5 | Is a custom A2UI catalog acceptable? | Yes. It adds YMM picker, VIN field, address lookup, repeater, AssignmentBoard, DocumentDrop and offer cards on top of the A2UI 1.0 basic catalog components, and the React renderer must implement them and support 1.0. | FR-A2UI-4, FR-A2UI-8, FR-A2UI-12, FR-A2UI-14, FR-DOC-8, NFR-MNT-3 |
| Q6 | Who signs off the French labels and help? | Every catalog field now has `label_fr` and `help_fr`, and enum options have `enum_fr` (D12). The client signs off the wording before go-live. | FR-INT-4, FR-UI-10, NFR-I18N-1 |
| Q7 | Where do advisor hand-offs go? | The BFF writes each hand-off to Cosmos `handoffs`, and the advisor system (CRM or work queue) reads it from there. The integration and any call-back delay promised to the customer are to confirm. | FR-RUL-5, NFR-AUD-2 |
| Q8 | Who owns the end-of-form rules and their thresholds? | Underwriting owns the rule list and thresholds (the exotic make list, the under-25 limit for a young principal driver). The BFF holds them in code with tests. | FR-RUL-2, NFR-MNT-4 |
