// Generates golden.json: the FormSpecs of forms.js (the demo's 33) and of extra.json (widgets and options the demo does
// not use) compiled by the reference JavaScript compiler (a2ui.js), in French and English, with the clock frozen so the
// output is reproducible.
//
//   node tests/golden/gen_golden.mjs [--catalog <state-catalog.json>] [--out <golden.json>]
//
// a2ui.js and forms.js are copies of demo/src/a2ui.js and demo/src/forms.js (the design demo). The catalog is
// injected the way demo/build.py does it: window.STATE_CATALOG = <contracts/state-catalog.json>.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const arg = (name, fallback) => {
  const i = process.argv.indexOf(name);
  return i > 0 ? process.argv[i + 1] : fallback;
};
const contracts = process.env.QUOTE_CONTRACTS_DIR || path.resolve(here, '../../../../contracts');
const catalog = JSON.parse(fs.readFileSync(arg('--catalog', path.join(contracts, 'state-catalog.json')), 'utf8'));
const out = arg('--out', path.join(here, 'golden.json'));
const extra = JSON.parse(fs.readFileSync(path.join(here, 'extra.json'), 'utf8'));

const FROZEN = Date.UTC(2026, 10, 25, 12, 0, 0); // new Date() and Date.now() inside a2ui.js and forms.js
class FrozenDate extends Date {
  constructor(...args) {
    if (args.length) super(...args);
    else super(FROZEN);
  }
  static now() {
    return FROZEN;
  }
}

function compileAll(lang) {
  const window = { STATE_CATALOG: catalog, A2UI_LANG: lang, matchMedia: () => ({ matches: false }) };
  const ctx = vm.createContext({ window, Date: FrozenDate, performance: { now: () => 0 }, setTimeout, console });
  for (const file of ['a2ui.js', 'forms.js']) vm.runInContext(fs.readFileSync(path.join(here, file), 'utf8'), ctx, { filename: file });
  return [...window.FORMS, ...extra].map((f) => ({ id: f.id, spec: f.spec, messages: window.A2UI.compile(f.spec) }));
}

const fr = compileAll('fr');
const en = compileAll('en');
const forms = fr.map((f, i) => ({ id: f.id, spec: f.spec, fr: f.messages, en: en[i].messages }));
const year = new FrozenDate().getFullYear();
fs.writeFileSync(out, `{"year":${year},"forms":[\n${forms.map((f) => JSON.stringify(f)).join(',\n')}\n]}\n`);
console.log(`wrote ${out}: ${forms.length} forms, year ${year}`);
