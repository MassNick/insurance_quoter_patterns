#!/usr/bin/env node
/* Regenerates fixtures/surfaces.json from mcp-a2ui's golden output (../mcp/a2ui/tests/golden): the 40 golden
 * FormSpecs as the server compiles them (French), plus the journey surfaces of journey-specs.mjs compiled with the
 * golden folder's reference compiler (a2ui.js) under the same frozen clock. Two server deltas of mcp-a2ui's Python
 * compiler are applied: French repeater defaults ("Élément {n}", "Ajouter") and OfferCards.showAdjust.
 *
 *   npm run fixtures
 */
import { readFileSync, writeFileSync } from 'node:fs';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import { JOURNEY_SPECS } from './journey-specs.mjs';

const here = dirname(fileURLToPath(import.meta.url));
const goldenDir = resolve(here, '../../mcp/a2ui/tests/golden');
const golden = JSON.parse(readFileSync(resolve(goldenDir, 'golden.json'), 'utf8'));
const catalog = JSON.parse(readFileSync(resolve(here, '../../contracts/state-catalog.json'), 'utf8'));
const FROZEN = Date.UTC(2026, 10, 25, 12, 0, 0);

class FrozenDate extends Date {
  constructor(...args) {
    super(...(args.length ? args : [FROZEN]));
  }
  static now() {
    return FROZEN;
  }
}
const window = { STATE_CATALOG: catalog, A2UI_LANG: 'fr', matchMedia: () => ({ matches: false }) };
const ctx = vm.createContext({ window, Date: FrozenDate, performance: { now: () => 0 }, setTimeout, console });
for (const f of ['a2ui.js', 'forms.js']) vm.runInContext(readFileSync(resolve(goldenDir, f), 'utf8'), ctx, { filename: f });
const samples = Object.fromEntries(window.FORMS.map((f) => [f.id, typeof f.sample === 'object' ? f.sample : {}]));

/** What mcp-a2ui's Python compiler does differently from the reference (see its README). */
function serverDeltas(messages, spec) {
  for (const c of messages[0].createSurface.components) {
    if (c.component === 'Repeater') {
      if (c.itemLabel === 'Item {n}') c.itemLabel = 'Élément {n}';
      if (c.addLabel === 'Add another') c.addLabel = 'Ajouter';
    }
    if (c.component === 'OfferCards' && spec.fields.some((f) => f.data?.show_adjust)) c.showAdjust = true;
  }
  return messages;
}

const out = {};
for (const f of golden.forms)
  out[f.spec.form_id] = { fixture: f.id, title: f.spec.title, messages: serverDeltas(f.fr, f.spec), sample: samples[f.id] ?? {} };
for (const e of JOURNEY_SPECS)
  out[e.spec.form_id] = {
    fixture: e.id,
    title: e.spec.title,
    messages: serverDeltas(JSON.parse(JSON.stringify(window.A2UI.compile(e.spec))), e.spec),
    sample: e.sample ?? {},
  };
writeFileSync(resolve(here, 'surfaces.json'), `${JSON.stringify(out, null, 1)}\n`);
console.log(`wrote fixtures/surfaces.json (${Object.keys(out).length} surfaces, golden year ${golden.year})`);
