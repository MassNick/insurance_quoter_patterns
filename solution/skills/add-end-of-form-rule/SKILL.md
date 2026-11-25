---
name: add-end-of-form-rule
description: Use when asked to add, change or retire a rule that stops the online quote or flags it for an advisor, for example "stop the quote when…", "flag the file if…", "add an underwriting knock-out", "add a make to the exotic list", "change the young-driver age", "a new STOP or FLAG rule". It writes the predicate and its fr-CA and English reasons in the BFF rules module, the copied rules tests, a BFF component test and, when a journey must reach the rule from words, the fake-model wording and an API end-to-end journey. It never renames or reuses a released rule id, never puts matched values in events or logs, never calls a model from a rule, and never deploys.
---

# Add an end-of-form rule

A rule is a pure predicate on the merged session state, registered on `DEFAULT_RULES` in
`bff/src/quote_bff/rules/end_of_form.py`. `Bff.commit` already runs every registered rule after every commit batch
(`state.apply_rules`), so a new rule needs no wiring. Keep the patterns of `docs/patterns.md` §4 and §5: rules run before
the agent, an inferred STOP asks once (`rule_confirm` / `rule_correct`), a standing STOP opens the one `end_of_form`
surface, and events carry paths, never values. Paths are relative to the solution folder.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Never rename or reuse a released rule id, and never change `_fingerprint`: both are stored in events, hand-offs and
  the `acknowledged` set of live sessions.
- Predicates are pure: read only through `get_value`, `get_status`, `get_envelope`, `iter_fields`, `count_entities`;
  no I/O, no model, no clock except `today()`. Never swallow an error to return `[]`.
- No second place that runs rules, writes state or opens the hand-off. No new event type.
- Underwriting owns the list and the thresholds (requirements Q8). If the condition or severity is unclear, ask; when
  unsure, choose FLAG.

## Read first

- `docs/patterns.md` §4 (end-of-form rules) and §5 (hand-off): trigger, code path, events, ask-first, invariants.
- `bff/src/quote_bff/rules/end_of_form.py`: the docstring "How to add a rule", the five example rules, the readers.
- `bff/src/quote_bff/state.py` `apply_rules`: how hits become `go`, `ask` or `stop`, advisor signals, `rule_question`.
- `bff/src/quote_bff/service.py`: `Bff.commit`, `ask_rule_confirmation`, `end_of_form`, `on_rule_answer`,
  `send_handoff`, and `agent_turn` (on the chat path it holds the main reply until `memory.result` and drops it on
  `ask` or `stop`).
- `bff/src/quote_bff/forms.py`: `STRINGS` (`rule_question`, `rule_yes`, `rule_no`, `stop_intro`, `eof_*`), `end_of_form_spec`, `handoff_summary`.
- `contracts/events.md`: `rules.hit`, `handoff.created`, merge rule 6. `contracts/state-catalog.json`: keys and enum codes.
- `bff/tests/unit/rules/test_end_of_form.py` (`FIXTURES`, `DefaultRegistryTest`, one class per rule) and
  `bff/tests/component/test_journey.py` (the Lamborghini, firm-stop and young-driver tests).
- `docs/testing.md` "The rules tests" and "The fake model"; `e2e/test_api_journey.py::test_lamborghini_stop_rule_asks_first_then_hands_off`.

## Steps

1. **Pin the rule.** Write down the id (UPPER_SNAKE, French-ish like `USAGE_AGRICOLE`), the severity, the catalog paths
   it reads, the exact condition (enum codes such as `"farm"`, booleans with `is True`), whether it counts only once
   confirmed, and the two reasons. A field that is not in the catalog comes first: use `skills/add-field-or-form`.
2. **Write the predicate** in `end_of_form.py`, after the example rules:
   `@DEFAULT_RULES.rule(id=..., description=..., severity=Severity.STOP | Severity.FLAG, reason_fr=..., reason_en=...)`
   on `def my_rule(state) -> list[str]`. Return the matched field paths in a stable order, or `[]`. Put lists and
   thresholds in module constants (as `EXOTIC_MAKES`, `YOUNG_DRIVER_AGE_LIMIT`). For a deterministic field, test
   `get_status(state, path) == STATUS_CONFIRMED` as `refus_ou_resiliation` does. To change a rule, keep its id and
   edit the predicate or constant. To retire one, remove the decorator and its fixtures; the id stays burnt.
3. **Write the reasons.** `reason_fr` in fr-CA, vouvoiement, one or two short sentences that say what happens next; no
   rule id, no jargon, no blame, no matched value. `reason_en` says the same in English. They appear as written in
   `rules.hit.reason`, in the end-of-form note "Pourquoi? … (règle <id>)" and, for a FLAG, in the hand-off summary.
4. **Rules tests** in `bff/tests/unit/rules/test_end_of_form.py`: an id constant; a `FIXTURES[ID] = (positive, near
   miss)` pair built with `envelope`, `guessed`, `vehicle_state`, `make_state` or `young_state`; the `(ID, Severity.X)`
   tuple in `DefaultRegistryTest.test_the_five_example_rules_are_registered_in_order`; and a `RulesTestCase` class:
   paths and severity on a match, near misses, an empty or partial state (no hit, no exception), several vehicles or
   drivers when it iterates, and `inferred` versus `confirmed` when the predicate reads `status`.
   Run `cd bff && .venv/bin/python -m pytest tests/unit/rules -q`.
5. **Keep the design copy aligned, or diverge on purpose.** Default is aligned: copy the module to
   `../bff/rules/end_of_form.py` and port the test changes to `../bff/rules/test_end_of_form.py` (keep its relative
   imports, its `python3 -m unittest` run line and its `# ruff: noqa` line). Mirror the id, severity, paths and
   condition in `RULES` of `../demo/src/journey.js` (its `reason` strings are shorter demo text; do not copy the Python
   ones), then `node --check ../demo/src/journey.js` and `python3 ../demo/build.py`. Add the rule to FR-RUL-2 in
   `../requirements.md` if the design should list it. Diverge only when the user agrees, for a production-only rule
   (confidential list, a field the demo does not model) or a frozen design: leave `../` untouched and replace the
   "byte-identical" claims in `docs/patterns.md` §4, `docs/testing.md` "The rules tests" and the docstring of
   `bff/src/quote_bff/rules/__init__.py` with one sentence naming the extra rules.
6. **BFF component test** in `bff/tests/component/test_journey.py`, copied from the closest existing test:
   - STOP from chat: `api.agent.script.append(chat_turn((path, value)))`, `api.say(...)`; one `rules.hit` with
     `inferred: True`, one BFF `text.done`, suggestions `["rule_confirm", "rule_correct"]`, no forwarded main reply;
     then `api.say("Oui, c’est bien ça", action_id="rule_confirm")` opens `end_of_form` with no new invocation.
   - STOP from a form: `form_event(...)` then `api.submit(...)`; no `turn.started`, an `end_of_form` `createSurface`.
   - FLAG: `turn.started` still there, the hit in `body["rule_hits"]`, `"Rule <ID>: flag"` in `system_notes`, one
     `advisor_signals` entry, and no second `rules.hit` at the next commit.
   - Personal-data paths (dates of birth) need `api.consent()` first. Assert the matched value is absent from every
     `rules.hit` (`value not in json.dumps(...)`).
7. **Fake-model wording**, only if an end-to-end journey must reach the rule from words. The fake parses French in
   `agents/quote-agent/src/quote_agent/fake/rules.py`: a make goes in `MAKES`, a value is a topic-scoped regex in
   `extract`, a new question needs its `ASK` marker and a step in `_Main.next_step()` (`fake/client.py`). `_Main.decide()`
   already answers a STOP in RULES with a hand-off sentence. Tests: `tests/unit/test_rules.py` and
   `tests/component/test_journey.py` (see `test_lamborghini_is_extracted_for_the_bff_rules`).
8. **API journey** in `e2e/test_api_journey.py`, modelled on the Lamborghini test: `Session(stack).consented()`,
   `s.say(...)`, the `rules.hit` with your id, `rule_confirm`, `s.surface()["surfaceId"] == "end_of_form"`, `send`
   with `"/quote/handoff_consent": True`, then `handoff.created` with `CNS-` and your `rule_id`. For a FLAG, assert
   the journey goes on and `s.state()["advisor_signals"]` holds the id. Browser journeys use the mock BFF
   (`web/fixtures/mock-bff.ts` scripts only the Lamborghini rule); extend it only if the user wants a browser journey.
9. **Docs:** the rule table in `docs/patterns.md` §4. Tell the user the hand-off still lands only in Cosmos `handoffs`.

## Tests to add or update

| Level | File | What |
|---|---|---|
| unit | `bff/tests/unit/rules/test_end_of_form.py` | fixture pair, registry order, the rule's test class |
| unit | `bff/tests/unit/test_state.py` | only if `apply_rules` behaviour changes (it should not) |
| component | `bff/tests/component/test_journey.py` | ask-first, firm STOP or FLAG journey, no values in events |
| unit, component | `agents/quote-agent/tests/unit/test_rules.py`, `tests/component/test_journey.py` | only with new fake wording |
| e2e | `e2e/test_api_journey.py` | one journey through the real services with the fake model |

## Verify

```bash
cmp ../bff/rules/end_of_form.py bff/src/quote_bff/rules/end_of_form.py   # silent when aligned
diff ../bff/rules/test_end_of_form.py bff/tests/unit/rules/test_end_of_form.py   # only the run line, noqa line, imports
(cd ../bff && python3 -m unittest)                                       # the design copy, stdlib only
(cd bff && .venv/bin/python -m pytest tests/unit/rules -q && .venv/bin/python -m pytest -q) && uvx ruff check bff
./test_all.sh                                                            # includes the API journeys (ports +20000)
```

Pass: every suite green, `FixtureCoverageTest` included, and `test_all.sh` ends with `all suites passed`.

## Done when

- [ ] The rule is registered with a unique id, a severity, a description and fr-CA and English reasons.
- [ ] A positive and a negative fixture, the registry order and a test class pass.
- [ ] A BFF component test proves ask-first or the firm STOP, or the FLAG and its advisor signal.
- [ ] The design copy is byte-identical, or the divergence is written in the three places of step 5.
- [ ] Fake wording and an API journey exist if the user wants the rule reachable from chat.
- [ ] `docs/patterns.md` §4 lists the rule; `./test_all.sh` passed, or its failures are reported as they are.

## Pitfalls

- A positive fixture that also fires another rule (an exotic farm vehicle) fails `FixtureCoverageTest`; a rule that
  fires on `clean_state()` fails `NoHitTest`.
- A deterministic field read without its status is `pending_confirmation` during the popup: not `inferred`, so the
  STOP would be firm and skip the question. Require `confirmed`.
- Enum labels are not codes: compare `"farm"`, not `"Agricole"`. Strings such as `"true"` are not `True`.
- Changing a predicate or a constant changes fingerprints, so hits can fire again in sessions in progress.
- Age rules use `today()`: the rules tests pin it to 2026-11-24, the BFF tests (`pinned_today`) to 2026-11-25.
- A predicate that raises sets `rules_error` and blocks pricing until a later check passes; the commit still stands.
- On the chat path assert on forwarded events; the dropped reply is still in `events` with `forwarded: false`.
- The API journeys and `npm run test:stack` share `e2e/.logs/`: run them one after the other.
