---
name: add-field-or-form
description: Use when asked to collect something new or show it differently, for example "add a field for X", "we also need the winter tires / the garage address / the number of years insured", "make X required to price", "X is personal data", "X must be confirmed twice", "add a form for Y", "a new widget", "show X in the dossier". It adds the state field to `contracts/state-catalog.json` with every flag it needs (type, fr-CA labels, help and enum labels, `deterministic`, `pii`, `required_for_quote`, `system`), checks the memory output path, the BFF merge and commit, the FormSpec compiled by mcp-a2ui, the web renderer (only if a new widget is needed), the web fixtures, the visual baselines and the dossier, with tests at each level. It never adds a second state writer, a new state source or status, a field label outside the catalog, or a baseline updated to turn a red run green.
---

# Add a field or a form

The catalog `contracts/state-catalog.json` drives everything: BFF validation and the merge rules, the memory agent's
key list, the A2UI compiler, the full form, the dossier and pricing input. Most field changes are one catalog entry plus
the tests that pin lists; a form is usually a FormSpec the agent writes at run time. Keep the patterns of
`docs/patterns.md` §1, §2, §3 and §6 and its "Add a field" checklist. Paths are relative to the solution folder.

## Hard rules

- No API key, connection string or secret in any file, prompt, log or message. Use `<placeholders>` and `dev-only-...` test values.
- Do not commit unless the user asks. Never push.
- Do not call Azure, run `terraform apply` or execute the `deploy/` scripts unless the user asks for that step. Offline suites and `terraform validate` are always fine.
- Keep the code light: reuse `quote_common`, `Bff.commit` and the existing helpers. No speculative abstractions.
- Contracts first (`contracts/`), then code, then tests, then docs.
- Customer text is fr-CA, from the catalog or a string table. Code, logs and the developer trace are English.
- Run `./test_all.sh` (or the narrower suite named below) before you say done, and report failures as they are.
- Every state change goes through `Bff.commit`. No new source, status, entity or second consent check.
- One field shell and one binding hook in the renderer (`FieldShell`, `useBinding`); a new widget never draws its own
  label, error, provenance or protected badge.
- Update a screenshot baseline only for an element that changed on purpose, after looking at its diff.

## Read first

- `docs/patterns.md` §1 (envelopes, one commit path, pricing rule), §2 (deterministic), §3 (consent gate), §6 (forms)
  and "Add a field"; `docs/testing.md` "Golden fixtures", "Visual regression", "Before you merge".
- `contracts/state-catalog.json` (look at a field of the same type), `contracts/form-spec.schema.json`,
  `contracts/memory-output.schema.json` (`state_changes[].path`), `contracts/document-output.schema.json` (`field_map`).
- `bff/src/quote_bff/state.py` (`field_for`, `normalize`, `ERRORS`, `merge`, `DERIVED`, `missing_for_quote`) and
  `bff/src/quote_bff/forms.py` (`full_form_spec`, `end_of_form_spec`).
- `mcp/a2ui/src/mcp_a2ui/compiler.py` (`_AUTO_WIDGET`, `_PATTERN_MESSAGES`, `_Compiler.field`, `compile`, `validate`)
  and `mcp/a2ui/README.md` "How to extend"; `web/README.md` "Adding an A2UI component".
- `web/src/a2ui/binding.tsx` (`useBinding`, `FieldShell`), `web/src/a2ui/registry.tsx`, `web/src/app/dossier.ts`.
- `mcp/quoting/src/mcp_quoting/state.py` (`missing_fields`, `snapshot`) and `adapters/` (the rating engine input).

## Steps

1. **Catalog entry** in `contracts/state-catalog.json` `fields`, next to its neighbours:
   - `key` (snake_case, unique across the whole catalog: lookups are by key alone, hence `driver_` prefixes), `entity`
     (`quote`, `vehicle`, `driver`, `underwriting`, `household`), `type` (one of the types already used: `string`,
     `integer`, `boolean`, `date`, `enum`, `multi_enum`, `email`, `phone`, `postal_code`, `vin`, `currency`, `list`,
     `assignment_matrix`), `label` (English), `label_fr`, `help_fr` (one plain fr-CA sentence, vouvoiement: it is the
     hover help in forms and the dossier), `enum` and `enum_fr` with the same codes in the same order, and the checks
     it needs (`pattern`, `min`/`max`, `min_age`/`max_age`, `max_length`, `min_offset_days`, `visible_if`, `lookup_tool`).
   - Flags: `required_for_quote` (listed in `missing_fields` by the BFF and mcp-quoting, priced through `is_usable`);
     `deterministic` (held `pending_confirmation`, confirmed twice, never priced while inferred); `pii` (withheld at
     commit before the contact consent and left out of compiled forms until then, except on `end_of_form`); `system`
     (only the BFF writes it; models and forms are rejected; the full form hides it); `document_extractable` with a
     `field_map` entry when a document can fill it; `added: true` when it is not in the brief's list.
   - Never add `derived_from` without the code in `state.derive` and `state.DERIVED`.
   - Keep `../contracts/state-catalog.json` byte-identical (`cp`, then `cmp`) or note the divergence.
2. **Memory output path.** The `state_changes[].path` pattern already accepts any snake_case key on the five entities:
   no schema change. A new entity is out of scope (the pattern, `state.STATE_KEYS`, `state._PATH` and the compiler's
   `_ENTITY_POINTER` would all move): stop and ask. The memory agent learns the key from the generated key list; add an
   extraction hint to `contracts/prompts/memory-agent.md` only when the mapping is not obvious (`skills/edit-agent-prompts`).
3. **BFF merge and commit: nothing to write.** `state.field_for` accepts the path, `state.normalize` checks type, enum,
   pattern and range, and a bad value is rejected with a message from `state.ERRORS` (fr-CA). Add a message there only
   for a new kind of check. mcp-a2ui shows "Format invalide" for a `pattern` unless the key is in `_PATTERN_MESSAGES` (fr, en).
4. **Pricing** (`required_for_quote`): the rating adapter must accept the input (`mcp/quoting/src/mcp_quoting/adapters/`,
   the stub included; `skills/connect-quoting-systems`), and `BASE` in `mcp/quoting/tests/helpers.py` must hold a value.
5. **A form.** The agent writes FormSpecs at run time (`a2ui.render_form {form_spec}`, validated by mcp-a2ui
   `validate`), so a new subject form is a prompt change (`skills/edit-agent-prompts`): at most about 5 fields, bound to
   catalog keys, `widget: "auto"` unless a specific widget is needed, an identifier `form_id` with underscores. Name it
   with a prefix that `STEP_OF_SURFACE` in `web/src/app/dossier.ts` maps to a step chip, or add a regex there. For the
   journeys, give the fake a builder like `_Main.vehicle` in `agents/quote-agent/src/quote_agent/fake/client.py`, a
   step in `_Main.next_step()`, and add it to `test_every_fake_form_spec_is_valid`. BFF-owned specs: `full_form_spec`
   picks the field up from the catalog; `end_of_form_spec` is fixed.
6. **A new widget**, only when no existing one fits: the `widget` enum in `contracts/form-spec.schema.json`; a `case` in
   `_Compiler.field` (`mk("<Component>", ...)`, `bound`, `explicit`; `_NARROW` if half width); a row in `WIDGETS` in
   `mcp/a2ui/tests/unit/test_compiler.py`; `tests/golden/extra.json` only if the JavaScript compiler has it, otherwise a
   deviation row in `mcp/a2ui/README.md`. In `web/`: the component in `src/a2ui/components/` with `useBinding(c, scope,
   key)` inside `<FieldShell c={c} b={binding}>`, registered in `src/a2ui/registry.tsx` (`basic` or `insurance`),
   `BOUND_KEYS` in `src/a2ui/tree.ts` if it binds several paths, a check in `src/a2ui/checks.ts` if needed.
7. **Fixtures.** After a catalog or compiler change that a golden form uses: `cd mcp/a2ui && node
   tests/golden/gen_golden.mjs`, then `cd web && npm run fixtures`. A FormSpec showing a new widget goes in
   `web/fixtures/journey-specs.mjs` before `npm run fixtures`.
8. **Visual.** A new widget gets an element in `web/src/visual/list.ts` (`C('<name>', phone)`) and a scene in
   `web/src/visual/scenes.tsx`, then only its baseline: `cd web && npm run test:visual:update -- -g "component-<name> @"`
   (the trailing ` @` stops the pattern matching longer names; do not anchor it with `^`, the title starts with the project).
   Elements that moved because a form now shows the field: read each diff, then update those elements only
   (`skills/run-regression`).
9. **Dossier.** Labels and help come from the catalog (the web imports it at build time). A non-system field with a
   value appears under "Autres renseignements"; to place it in a named group add its path to `QUOTE`, `YOU`, `INSURER`
   or `HISTORY` in `web/src/app/dossier.ts`, and to `completion()` only if it should count.
10. **Pinned lists.** Deterministic: `test_catalog_loads_and_knows_deterministic_fields` (quote_common), the
    parametrization of `test_deterministic_fields_never_commit_directly` (`bff/tests/unit/test_state.py`), `DETERMINISTIC`
    in `web/fixtures/mock-bff.ts`, FR-DET-1 in `../requirements.md`. Every field: the key count in
    `test_key_list_has_every_catalog_key_with_codes_and_no_labels` (`agents/quote-agent/tests/unit/test_prompts.py`).
11. **Docs:** the `pii` or deterministic lists in `docs/patterns.md` §2 and §3 when a flag changed. Deploy under a new
    `PROMPT_VERSION` (a runtime setting; `post_deploy.sh` sets one per commit): the key list is part of the cached prefix.

## Tests to add or update

| Level | Where | What |
|---|---|---|
| unit | `bff/tests/unit/test_state.py` | normalize and reject cases; deterministic or `pii` behaviour if flagged |
| unit | `agents/quote-agent/tests/unit/test_prompts.py` | key count, token budget |
| unit | `mcp/a2ui/tests/unit/test_compiler.py`, `tests/golden/` | auto widget for the type; `WIDGETS` row for a new widget |
| unit | `mcp/quoting/tests/unit/test_state.py`, `test_tools.py` | `missing_fields` with and without the new required field |
| component | `bff/tests/component/test_journey.py`, `test_surfaces.py` | a submit commits the path; `confirm.required`; withheld before consent; full form |
| component | `web/tests/unit/components.test.tsx`, `app-state.test.ts` | the new widget; the dossier group |
| visual | `web/tests/visual/` via `src/visual/list.ts` | the new element; deliberate updates only |
| e2e | `e2e/test_api_journey.py`, `web/e2e/journey.spec.ts` | only when a journey must fill the field or open the form |

## Verify

```bash
cmp ../contracts/state-catalog.json contracts/state-catalog.json && deploy/.venv/bin/python deploy/check_contracts.py
for d in packages/quote_common bff mcp/quoting mcp/a2ui agents/quote-agent; do (cd "$d" && .venv/bin/python -m pytest -q) || echo "FAILED $d"; done
(cd mcp/a2ui && node tests/golden/gen_golden.mjs) && (cd web && npm run fixtures && npm run typecheck && npm test && npm run test:visual)
./test_all.sh
```

Pass: `contracts ok`, no `FAILED` line, the golden test agrees with the JavaScript compiler, and the visual suite fails
on no element you did not change on purpose.

## Done when

- [ ] The catalog entry has its type, fr-CA label, help and enum labels, and exactly the flags it needs.
- [ ] The design catalog is identical, or the divergence is noted.
- [ ] Every pinned list and count is updated; the rating adapter and `BASE` know a new required input.
- [ ] Golden file, web fixtures and only the intended baselines are regenerated.
- [ ] A test at each touched level proves the field is validated, committed, gated and shown as intended.
- [ ] `./test_all.sh` passed, or its failures are reported as they are.

## Pitfalls

- An enum stores codes; rules, suggestions and the rating engine compare codes, never `enum_fr` labels.
- A `pii` field disappears from a compiled form until consent is confirmed: test the form with `api.consent()` first.
- `deterministic` plus `pii` (like the licence number) is withheld before consent, then confirmed twice after it.
- `test_full_form_is_built_from_the_catalog` caps the full form at 40 top-level fields; vehicle and driver fields go
  into the repeaters, quote and underwriting fields do not.
- Forgetting `npm run fixtures` leaves the web tests on stale surfaces; forgetting `gen_golden.mjs` fails the golden test.
- macOS can only update `darwin/` baselines; Linux ones come from the CI image (`skills/run-regression`).
- A field that only the advisor needs may belong in the hand-off summary (`forms.handoff_summary`), not in a form.
