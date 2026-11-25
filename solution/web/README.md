# web (customer frontend)

React 19 + TypeScript (strict) + Vite. The Variation C shell of the Desjardins Assurances demo and an A2UI 1.0
renderer for the insurance catalog (`https://catalog.example.com/insurance-catalog/v1/catalog.json`). It talks only to
the BFF (`../contracts/interfaces.md` § 1, `../contracts/events.md`). Customer text is fr-CA; the developer trace is
English.

**Status:** code and tests; nothing deployed. No test touches the network: the BFF is replaced by a scripted mock.

The approved look and behaviour is the prototype `../../demo/journey.html`. Its CSS is reused as is
(`src/styles/prototype.css` = `demo/src/styles.css`, `src/styles/shell.css` = the inline styles of
`demo/src/journey.html` without the demo bar and phone frame); `src/styles/additions.css` holds the few new rules.

## Layout

| Path | What it holds |
|---|---|
| `src/a2ui/` | The renderer, independent of the app. `reducer.ts` (the A2UI message reducer), `tree.ts` (walk, validate, collect), `checks.ts` (`@checks` functions), `binding.tsx` (surface context, `useBinding`, `FieldShell`, `Badge`), `registry.tsx` (component name → React component, safe `Unknown` fallback), `Surface.tsx` (the form card and its ActionBar), `components/` (basic + insurance catalog), `catalog.ts` (state catalog import and fr-CA formatting), `pointer.ts`, `md.tsx`, `icons.tsx`, `media.ts` |
| `src/app/` | The shell. `api.ts` (BFF client and the one SSE reader), `store.ts` (the one event reducer and a tiny external store), `session.ts` (the controller: the only code that calls the BFF), `dossier.ts` (derived dossier rows, completion, step chips), `stage.ts` (ball-to-form morph), `ui.tsx` (the one `Modal` and the one `Tooltip`), `brand.tsx` (placeholder logo), `components/` |
| `src/visual/` | Visual harness (`visual.html?el=<name>`, dev/test only, not in the production build): `list.ts` (the elements), `scenes.tsx` (one renderer each) |
| `fixtures/` | The one fixture folder for unit, visual and e2e tests: `surfaces.json` (compiled surfaces), `services.ts` (fake lookups and document reads), `mock-bff.ts` (scripted BFF + agent), `scenarios.ts` (named journeys), `surface.tsx` (a fixture surface with a fake host) |
| `tests/unit/` | Vitest + Testing Library + jsdom |
| `tests/visual/` | Playwright screenshot per element; baselines in `__screenshots__/<platform>/` |
| `e2e/` | Playwright against the built app (Vite preview) with the mock BFF behind route interception |

## How it works

```
BFF --SSE--> api.turn (parse, one attempt) --> store.reduce(envelope)
                                                                              |-- a2ui/reducer (surfaces)
React components <-- useApp(selector) <---------------------------------------'
customer gesture --> Session (queue, one inbound at a time) --> POST /turns | /a2ui/functions | /documents
```

- **One event reducer** (`store.ts`): every outbound event of `events.md` (`text.delta/done`, `suggestions`,
  `a2ui.message`, `state.delta`, `signals`, `rules.hit`, `confirm.required/rejected`, `quote.*`, `handoff.created`,
  `phase`, `error`, ...) and every local UI action. The customer view is a pure function of what the BFF sent and what
  the customer did. After each stream the session refreshes `GET /state` to reconcile the dossier and chip statuses.
- **Renderer**: `createSurface` (components + data model in one message), `updateComponents` (upsert by id),
  `updateDataModel` (`null` deletes; `/_provenance/<escaped pointer>` badges; `/_errors` server errors),
  `deleteSurface`, `agentFunctionResponse`. Other versions and unknown catalogs are rejected. A value the server pushes
  onto a field the customer edited becomes a suggestion ("Utiliser") instead of overwriting it (FR-A2UI-7).
  Lookups and document reads leave as `callAgentFunction` (`POST /a2ui/functions`); buttons leave as A2UI `action`
  messages in `a2ui.action`, with `context.values` = a flat `{pointer: value}` map of the visible bound fields.
- **Typing dots** are drawn synchronously when the customer sends, before any network call (FR-CHAT-1).
- **Stage**: `StageHost` follows the store's `stage` surface; it plays the prototype morph in and out (one at a time),
  a crossfade with `prefers-reduced-motion`.
- **Double confirmation**: `confirm.required` events of a stream are grouped in one modal (with their `changes[]`);
  each step sends `confirm.response` for every confirmation id. Esc cancels the current step, focus is trapped.
- **Phone (≤ 600 px)**: the form covers the conversation; suggestions and the composer stay usable; the rail becomes a
  bottom tab bar, the dossier and the modal become sheets; a peek shows the latest reply over an open form.

## Commands

```bash
npm ci
npm run dev          # http://localhost:5173, /api proxied to BFF_PROXY_TARGET (default http://localhost:8000)
npm run build        # typecheck + production bundle in dist/
npm run preview      # serve dist/ on 5173 (same /api proxy)
npm run typecheck
npm run lint         # Biome (format + lint); npm run format fixes
npm test             # unit + component (Vitest, jsdom)
npm run test:visual  # screenshots per element (starts Vite on 5174)
npm run test:visual:update   # regenerate baselines (then review, see below)
npm run test:e2e     # builds, serves with Vite preview on 5175, runs Playwright
npm run test:all     # all of the above
npm run fixtures     # regenerate fixtures/surfaces.json (see Fixtures)
```

Local run against the real BFF: start it with `COOKIE_SECURE=false` (plain http) and `npm run dev`; the session cookie
`qsid` is HttpOnly and every call is made with `credentials: "include"`.

Playwright is pinned to 1.64.1, which uses the cached Chromium build `chromium-1248`
(`npx playwright install chromium` otherwise).

## Configuration

| Variable | When | Default | Meaning |
|---|---|---|---|
| `VITE_BFF_URL` | build | empty | BFF origin. Empty means same origin (`/api` on the page's host), which is how the app runs behind Front Door. |
| `VITE_ENABLE_TRACE` | build | on | `false`: the developer trace drawer never appears (production; the Dockerfile sets it). When on, it appears only with `?dev=1` (FR-UI-7). |
| `BFF_PROXY_TARGET` | dev / preview | `http://localhost:8000` | Where the Vite server proxies `/api`. |

## Tests

| Level | Where | What |
|---|---|---|
| unit + component | `tests/unit` | pointer, reducer, checks, tree walk; SSE parser and client (chunking, CRLF, keepalive, no re-send, HTTP errors, abort, upload); event reducer, dossier derivations, session controller; every A2UI component (rendering, binding, validation, events, keyboard and tap on the board); the shell (suggestions, chips and reopen, modal steps with Esc and focus, dossier tabs and tooltip, header, full form, trace gating); every fixture surface renders with no unknown component |
| visual | `tests/visual` | one element per screenshot from `src/visual/list.ts`: each component in key states, a form per subject (documents, about, vehicle, drivers, board, history and its errors, offers, end of form and sent), chip states, suggestions, header, steps, rail, transcript, dossier (both tabs), modal steps 1 and 2, full form, the whole app with a form open; desktop 1280 px and 390 px where relevant |
| e2e | `e2e` | full journey (consent with double confirm, documents read and submitted, licence confirmation, vehicle, drag-to-assign board, history with grouped confirmation, quote offers); text-only preference; Lamborghini rule → confirmation question → end of form → send → chips locked; chip reopen and save; full-form mode (consent first); phone journey |

### Visual baselines

Baselines are per platform: `tests/visual/__screenshots__/darwin/` was generated on macOS (arm64, Chromium 1248, system
fonts through the prototype's font stack, animations disabled, reduced motion, fixed clock 2026-11-25, `fr-CA`,
`America/Toronto`). A Linux CI run needs its own `linux/` set: run `npm run test:visual:update` once in the CI image and
commit the result.

To change the look on purpose: make the change, run `npm run test:visual`, open `playwright-report/visual` to see each
diff, run `npm run test:visual:update -- -g "<element> @"` for the elements that changed on purpose (the trailing ` @` is
part of the test title `<element> @desktop`, and keeps a bare name from also matching longer names), review the new PNGs
in the pull request (the CI workflow can attach them for the visual review agent), and commit them with the change.
Never update all baselines to make a red run green without looking at the diffs.

### Fixtures

`fixtures/surfaces.json` comes from mcp-a2ui's golden output (`../mcp/a2ui/tests/golden/golden.json`, the 40 FormSpecs as
the server compiles them, in French) plus the journey surfaces of `fixtures/journey-specs.mjs` (vehicle from a document,
two-driver board, offers, `end_of_form`, `full_form`, legacy matrix) compiled with the golden folder's reference compiler
under the same frozen clock. `npm run fixtures` regenerates it.

## Adding an A2UI component

1. Write it in `src/a2ui/components/` as `({ c, scope }: Props) => JSX`. Read and write its bound value with
   `useBinding(c, scope, key)` and wrap it in `<FieldShell c={c} b={binding}>` (label, help, protected badge, hint,
   provenance, suggestion and error come from there). Lookups go through `useSurface().host.callFunction(...)`.
2. Register it in `src/a2ui/registry.tsx` (`basic(...)` or `insurance(...)`).
3. If it binds several paths, add them to `BOUND_KEYS` in `src/a2ui/tree.ts` so validation and `collect` see them; a
   new check function goes in `src/a2ui/checks.ts`.
4. Add a FormSpec using it to `fixtures/journey-specs.mjs` (or get it into mcp-a2ui's golden set), run
   `npm run fixtures`, add a component test in `tests/unit/components.test.tsx`, a visual element in
   `src/visual/list.ts` + `scenes.tsx`, and generate its baseline.

## Docker

```bash
cd ..   # the solution folder: the build reads contracts/
docker build -f web/Dockerfile -t quote-web .                       # same-origin /api (default)
docker build -f web/Dockerfile --build-arg VITE_BFF_URL=https://bff.example -t quote-web .
```

Node 22 build stage, then `nginxinc/nginx-unprivileged` (non-root uid 101) on port 8080, `GET /` → 200, unknown paths →
`index.html`. The BFF URL is fixed at build time; the default (empty) is same origin.

## Brand

The Desjardins mark is the prototype's hand-drawn placeholder, in one module (`src/app/brand.tsx`, marked TODO). It must
be replaced with the official asset, approved by the brand team, before any external showing (NFR-BRD-1).

## Deviations from the prototype

- Chip labels use the surface title (`Formulaire : Votre véhicule`) as events.md says; the prototype had short
  lowercase labels. A `chip` string next to an `a2ui.message` envelope is used when present.
- "Modifications enregistrées : …" after an edit-mode or full-form save is the BFF's message (a bot bubble), not the
  prototype's local system note.
- Accessibility: replies are announced once complete through a separate live region (the prototype announced the
  whole transcript token by token); board slots are focusable buttons so placement works by keyboard (the prototype only
  selected people by keyboard); the modal traps focus and Esc cancels the step; "Retour" sends nothing.
- The dossier also lists, under "Autres renseignements", committed fields the prototype's fixed rows do not show
  (FR-UI-4). The year list runs from 1950 to next year (FR-A2UI-4; the prototype stopped at 1990).
- Customer text only in French: the YMM status's tool name shows only with the trace open; Summary badges use French
  source names; the address lookup no longer shows the internal tool name as a note.
- Two CSS fixes: the "from chat" badge dot sat above its text (`.badge.chat` inherited `.chat`'s column layout); reduced
  motion also turns off smooth scrolling.
- Dates `YYYY-MM-DD` are read as local days (the prototype read them as UTC, a day early in Québec for age checks).
- Real uploads (file chooser on every tile and field action, drop on FileDrop, PDF/JPEG/PNG/HEIC up to 10 MB);
  OfferCards `showAdjust` adds "Ajuster ma soumission" (action `adjust`).
- Not ported (demo page only): demo bar, phone-frame preview, "Démo : remplir", replay and the Lamborghini shortcut. The
  trace drawer opens from a small "Trace" button with `?dev=1`. The quick variant of the close morph (when one form
  replaces another) plays the normal reverse.
- Fonts are the prototype's stack resolved by the system (Roboto is not bundled), so baselines are per platform.

## Requests to other components

- **bff:** `nodocs` closes without the agent; the web follows it with `user.message "Continuer sans documents"` (the
  prototype's behaviour). If the BFF prefers to run the agent itself, the web can drop that message.
- **bff / quote-agent:** optional `chip` (short label) next to `createSurface` in `a2ui.message`; `quote.created` and
  `quote.updated` with `reason` and `created_at` (the Quotes tab shows them when present).
- **mcp-a2ui:** the renderer builds the AddressLookup sub-field checks (required, postal code regex) itself, as the
  prototype did; emitting them on the component would keep validation server-defined. Confirm that `showAdjust` means
  "fire action `adjust`" (the BFF then closes the cards and passes `action_id: adjust` to the agent).
- **contracts:** add `confirm.rejected` and `confirm.required.changes[]` to events.md's outbound table, and the action
  names (`submit`/`save`, `cancel`/`dismiss`/`back`/`nodocs`/`edit_answers`, `send`, `adjust`).
