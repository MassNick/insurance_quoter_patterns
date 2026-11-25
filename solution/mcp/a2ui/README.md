# mcp-a2ui

MCP server (`mcp_a2ui`) that turns a FormSpec into A2UI 1.0: the intent-level form description the main agent writes (`contracts/form-spec.schema.json`) becomes one `createSurface` message with components and data model, using the state catalog for widgets, labels, options and checks. Four tools: `render_form`, `update_form`, `close_form`, `render_offers`. Stateless, streamable HTTP with JSON responses at `/mcp`, `GET /healthz`, port 8102.

Status: code and tests. Nothing here calls Azure.

## Layout

```
src/mcp_a2ui/
  compiler.py     compile() (port of the demo's compile()), prefill from the committed state, edit mode, validate()
  conformance.py  the A2UI 1.0 conformance rules (port of the check in skill/scripts/check_artifacts.py)
  server.py       tools.json -> MCP tools, argument validation, error results, app factory, main
  __main__.py     python -m mcp_a2ui
tests/unit/       one function or class, no I/O: every widget, check, layout rule, prefill path, edit mode, validation
tests/golden/     the Python compiler against the JavaScript one (a2ui.js, forms.js, extra.json, gen_golden.mjs, golden.json)
tests/component/  the server in-process (SDK client over memory, ASGI app over HTTP, the BFF's JSON-RPC call sequence) and as a real process (SDK client over a socket, JSON logs)
```

## Run and test

```bash
cd solution/mcp/a2ui
uv venv && uv pip install -e ../../packages/quote_common -e ".[test]"

.venv/bin/python -m pytest                      # everything: 254 tests
.venv/bin/python -m pytest tests/unit           # 114
.venv/bin/python -m pytest tests/golden         # 122, regenerates golden.json with Node when node is installed
.venv/bin/python -m pytest tests/component      # 18
.venv/bin/ruff check . && .venv/bin/ruff format --check .

.venv/bin/python -m mcp_a2ui                    # serve on :8102
curl -s localhost:8102/healthz
```

`quote_common` finds `contracts/` by walking up from its own folder, which works for an editable install in this repository. Set `QUOTE_CONTRACTS_DIR` for any other install (the image does). `pyproject.toml` maps `quote-common` to the local package (`[tool.uv.sources]`, as mcp-quoting does); keep it: without it `uv pip install` (0.13.9) left `cryptography` out of the environment and `import mcp` failed.

Docker (build context is `solution/`; the daemon was not running when this was written, see Not verified):

```bash
docker build -f mcp/a2ui/Dockerfile -t mcp-a2ui:dev solution
docker run --rm -p 8102:8102 mcp-a2ui:dev
```

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `PORT` | `8102` | Listen port |
| `HOST` | `0.0.0.0` | Bind address. A loopback address turns on the SDK's Host-header check |
| `A2UI_LANG` | `fr` | Language of the customer UI: `fr` (fr-CA) or `en`. The compiler takes `lang` as an argument; this is where the server gets it |
| `QUOTE_CONTRACTS_DIR` | `<solution>/contracts` | Folder with `tools.json`, `state-catalog.json` and `form-spec.schema.json` (the image sets `/app/contracts`) |
| `LOG_LEVEL` | `INFO` | Level of the shared JSON logger (`quote_common.logs`): one redacted JSON object per line. A tool crash logs the exception type and the code frames, never the message |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | unset | When set, the logger also exports to Application Insights |
| `ENTRA_AUDIENCE` | unset | Turns the inbound Entra check on (NFR-SEC-3, `quote_common.auth`): every request except `/healthz` needs a Bearer token of the tenant whose `aud` is one of these values (comma-separated: the `api://` URI and/or the client id) and the app role `Tools.Invoke`. Unset: no check, nothing needs Azure |
| `ENTRA_TENANT_ID` | none, required with `ENTRA_AUDIENCE` | The tenant that issues the tokens. Missing: the process logs `configuration error: ...` and exits with code 1 |
| `ENTRA_ALLOWED_PRINCIPAL_IDS` | empty | Comma-separated object ids of the callers that may call (the BFF's and the Foundry project's managed identities: the toolbox calls as the project). Empty: any caller of the tenant that has the role |

The Entra check and the Application Insights export need the `azure` extra (`pip install 'mcp-a2ui[azure]'`, which the image installs). No secret is read.

## Tools

Names, descriptions and input schemas are read from `contracts/tools.json` at startup, so the contract cannot drift; the server refuses to start if a tool there has no handler. What a caller adds before the MCP call (`injected_args` in tools.json: `state`, `mode`, `version`, `offers`, and `session_ref` on every tool) is declared as optional properties, because an MCP client forwards only what the schema declares, and `render_form`'s `form_spec` carries the FormSpec schema, which is how the model learns the format. Arguments are validated against that schema before a handler runs. The SDK is `mcp` 2.x, where `FastMCP` became `MCPServer`; like mcp-quoting this server uses the public low-level `mcp.server.lowlevel.Server`.

| Tool | Arguments (required in bold) | What it does |
|---|---|---|
| `render_form` | **`form_spec`**, `state`, `mode` (`new` or `edit`), `session_ref` | Validates the FormSpec and answers one `createSurface`. `state` fills every bound path the FormSpec does not prefill. `mode: "edit"` relabels the primary button "Enregistrer" |
| `update_form` | **`surface_id`**, `set` (`{path: value}`), `errors` (`{path: message}`), `session_ref` | One `updateDataModel` per `set` path (`null` deletes the key), one at `/_errors` with the whole `errors` map. To add or remove fields the caller renders the form again |
| `close_form` | **`surface_id`**, `reason` (in tools.json, unused), `session_ref` | One `deleteSurface` |
| `render_offers` | **`quote_id`**, `version`, `offers`, `highlight`, `show_adjust`, `session_ref` | An `offer_cards` surface `offers_<quote_id>_v<version>` (lower case, `[a-z0-9_]`, at most 40 characters). `version` and `offers` are optional in the schema but required to run: the hosted agent adds them from the last quote result. `show_adjust` puts `showAdjust: true` on `OfferCards` |

**Result.** Success is a `CallToolResult` with two parts: a `TextContent` summary for the model, `{"surface_id": ..., "status": "shown"}` (`updated` and `closed` for the other tools), and an `EmbeddedResource` (`uri a2ui://surfaces/<id>`, `mimeType: application/a2ui+json`, `annotations.audience: ["user"]`) whose text is the JSON array of A2UI messages. The messages never appear in the model-facing text. `session_ref` is accepted and neither read nor returned.

**Errors** are tool results with `isError: true` and a body `{"error": code, "message": ...}`, never protocol errors, and show no partial UI:

| `error` | When |
|---|---|
| `invalid_arguments` | The arguments break the schema, the FormSpec breaks the FormSpec schema or names a key the catalog does not have (the message lists every problem, at most 10, as `fields[2].widget: ...`), `update_form` has neither `set` nor `errors`, `render_offers` lacks `version` or `offers` |
| `unknown_tool` | No such tool |
| `internal_error` | Anything else. Details stay out of the result; the log has the exception type and the code frames, never the message |

## What the compiler emits

`compile(spec, state=None, mode="new", lang="fr", *, year=None)` returns `[createSurface]`: `"version": "1.0"`, `catalogId`, `dataModel` and a flat `components` list with identifier ids (`<widget>_<n>`), `{"@path"}` bindings (relative inside repeaters), `"@checks"` CheckRules `{condition: {"@call", args}, message}`, headings as Markdown in `Text`, buttons with a `Text` child and `action.event.name`. Row packing and ids follow the demo.

| Widget | Component | Notes |
|---|---|---|
| `auto` | by catalog type | enum up to 7 options: `chips`, more: `select`; boolean: `toggle` (`contact_consent`: `checkbox`); `assignment_matrix`: `assignment_board`; others by type |
| `text`, `email`, `phone`, `postal_code`, `number`, `currency`, `textarea` | `TextField` | `textFieldType`; `licence` for the licence number; `longText` for `textarea` |
| `date` | `DateTimeInput` | date only |
| `select`, `chips`, `radio`, `time_slots` | `ChoicePicker` | `time_slots` is variant `slots`; `options` override the catalog enum; `multi_enum` sets `multiple` |
| `toggle` | `ChoicePicker` | segmented, Oui/Non (`valueType: boolean`) |
| `checkbox`, `slider` | `CheckBox`, `Slider` | slider range from the catalog, `max` and `unit` from the field |
| `ymm`, `vin`, `address_lookup` | `YmmPicker`, `VinField`, `AddressLookup` | `ymm` binds year, make, model and type and adds `ymmValid`; the address binds `/quote` line, city, province, postal code |
| `repeater` | `Repeater` | template fields have relative paths and are never auto-required; one `vin` field is a `list` layout |
| `assignment_board`, `matrix` | `AssignmentBoard`, `AssignmentMatrix` | `onePrincipalPerVehicle` on the board; matrix choices from `item_role_fr` |
| `document_drop`, `file` | `DocumentDrop`, `FileDrop` | |
| `offer_cards`, `summary`, `info`, `section` | `OfferCards`, `Summary`, `InPlaceNote`, a `### ` `Text` | `info` takes `tone`; `section` closes the pending row |

Checks come from the catalog: `required`, `email`, `regex` (the catalog pattern, with a French message for phone, postal code and licence), `vinCheckDigit`, `numericRange` (an offset in years is added to the current year), `ageBetween`, `dateWithin`. Messages are French by default, English with `lang="en"`. `visible_if` accepts `key == true|false|number|'text'` and compiles to an `equals` call on the sibling path.

### Prefill from the committed state

`state` is the session state as the BFF sends it: the five entity keys `quote`, `vehicles`, `drivers`, `underwriting`, `household`, each field wrapped in an envelope `{value, source, status, evidence?, ...}`. One function, `_Compiler.prefill`, fills the data model for every widget: the FormSpec's own `prefill` first, then every other bound path from the state.

- Bound paths are the field's path; the four `ymm` paths; the four address paths; the board's and the cards' value path; the `fills` target paths of a `document_drop`; and, for a repeater, `<collection>/<start + row>/<template key>` for every row.
- A repeater grows its row count to hold the rows the state already has (never past `max`). The items of a list field such as `claims_detail` are plain values under its envelope and are read as `/underwriting/claims_detail/0/date`.
- A value with `source` `chat`, `document` or `lookup` gets `{source, note}` at `/_provenance/<escaped pointer>`, where `note` is the envelope's `evidence` (what the BFF's live autofill sends). Values from a form or the system need no badge. Any status prefills, including `inferred` and `pending_confirmation`; a `null` value does not.
- The FormSpec's `prefill` wins over the state for its paths. The BFF removes prefill from a stored FormSpec before it reopens it, so the committed values show.

## FormSpec validation

`validate(spec)` checks the FormSpec against `contracts/form-spec.schema.json` as it is, then every `key` against the catalog (a bare key must be a catalog key, or an item key of the list field the repeater is bound to; a pointer must start with `/quote`, `/vehicles`, `/drivers`, `/underwriting` or `/household`) and every `visible_if`. The demo's 33 FormSpecs and the BFF's `full_form` and `end_of_form` builders all pass.

## Deviations from the JavaScript compiler

The golden test requires the same output as `demo/src/a2ui.js` for every FormSpec, apart from these:

| Difference | Why | Tested by |
|---|---|---|
| A repeater's default `itemLabel` and `addLabel` are French in the French UI: `Élément {n}`, `Ajouter` (the JavaScript leaves `Item {n}` and `Add another` in both languages) | the customer UI is fr-CA | `test_repeater_defaults_follow_the_language...`; the golden test maps the two JavaScript defaults to the French ones |
| `visible_if` reads `TRUE` and `False` in any case (the JavaScript reads `TRUE` as the text `RU`) | a bug | `test_parse_visible_if` |
| A falsy explicit `prefill.value` (`""`, `0`, `false`) is applied on `offer_cards`, `matrix` and `assignment_board` (the JavaScript skips it) | prefill means what it says | `test_explicit_prefill_keeps_falsy_values` |
| `OfferCards` gets `showAdjust: true` when `data.show_adjust` is set | `render_offers.show_adjust` | widget table, component test |
| `compile` still emits `visibleIf: null` for an expression it cannot parse, like the JavaScript, but `validate` refuses such a FormSpec first | the model gets an error, not an always-visible field | `test_problems_name_the_bad_field` |

New, not in the JavaScript: prefill from the state, `mode="edit"`, `validate`, the `year` argument (the clock for `numericRange`), the four tools.

## Golden test

`tests/golden/a2ui.js` and `forms.js` are copies of `demo/src/a2ui.js` and `demo/src/forms.js`. `extra.json` adds 7 FormSpecs with the widgets and options the demo does not use (`matrix`, widgets without a key, `visible_if` forms, every action style, no actions, prefill of every composite widget, entity indexes). `gen_golden.mjs` runs both scripts in a Node `vm` with the catalog injected as `window.STATE_CATALOG` (as `demo/build.py` does), a frozen clock (2026-11-25) and `A2UI_LANG` `fr` and `en`, and writes `golden.json`: 40 FormSpecs, each with its French and English JavaScript output.

The test compares `json.dumps(..., sort_keys=True)` of the Python and JavaScript output, so a number printed differently also fails. With Node installed (18 or later) it regenerates `golden.json` first and also fails when the committed file is stale; without Node it uses the committed file.

```bash
node tests/golden/gen_golden.mjs        # rewrite golden.json after changing the catalog, forms.js, a2ui.js or extra.json
```

To follow the demo, copy the two files from `demo/src/`, regenerate, and run the tests: a difference is either a bug here or a new deviation to document above.

## How to extend

**A widget.** Add its name to the `widget` enum in `contracts/form-spec.schema.json`. In `_Compiler.field` add a `case` that builds the component with `mk("<Component>", ...)`; set `bound` (the absolute paths it binds) and `explicit` (`{path: value}` from the FormSpec's `prefill`) when it binds more than its own key, and the shared `prefill` does the rest, from the FormSpec and from the state. Add it to `_NARROW` if two may share a row. Add a row to `WIDGETS` in `tests/unit/test_compiler.py` and a FormSpec to `tests/golden/extra.json` if the JavaScript compiler has it. The renderer in `web/` must implement the component.

**A catalog component or check function** (for example a new check). Catalog-driven checks are built in `_Compiler.checks` with `_rule("<function>", path, message, **args)`. Give the French and English message, add a test in `tests/unit/test_compiler.py`, and have the renderer implement the function.

**A catalog field.** Edit `contracts/state-catalog.json` only. Type, labels, enum, pattern, range and `deterministic` reach the form through `auto`. Regenerate `golden.json` if a demo form uses it.

**A tool.** Add its entry to `contracts/tools.json` (description, `inputSchema`, `injected_args`) and its handler to `HANDLERS`. A new injected argument also needs its schema in `INJECTED`.

## Requests to other components

- **Web renderer**: show provenance for every bound path, including the `ymm` and address sub-paths and repeater items (`/_provenance/<escaped pointer>`); `OfferCards.showAdjust`; the French repeater defaults.
- **mcp-quoting**: `quote_id` and `version` must give a valid surface id, `offers_<quote_id>_v<version>` (`Q-1A2B3C4D` and 1 is fine; the FormSpec schema allows 40 characters).

## Not verified

- The Docker image was not built (no daemon). The same install was run in a clean virtualenv (`pip install` of `quote_common` and this package, contracts through `QUOTE_CONTRACTS_DIR`, `python -m mcp_a2ui`) and called over a real socket: `/healthz`, `initialize`, `tools/call`, JSON responses.
- Application Insights export: the server starts and serves with the `azure` extra installed and a connection string that points at a closed local port. Nothing was exported to Azure.
- The Entra check is tested for the paths that need no Entra: off when `ENTRA_AUDIENCE` is unset, 401 without a token and with an invalid one, `/healthz` open. A valid token, the principal list and the role are `quote_common.auth`'s own tests; nothing here was run against a real tenant.
- Whether the Foundry toolbox passes an `EmbeddedResource` with its annotations through (implementation plan spike S3). If not, the fallback is the same messages in `structuredContent`.
- Messages are checked against the structural rules of `check_artifacts.py`, not against the official A2UI 1.0 JSON schemas (no network in tests) or the custom catalog's schema.
- The renderer is not wired: nothing here has been seen in a browser.
