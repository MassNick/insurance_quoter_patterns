/* a2ui.js
 * 1. Simulated A2UI MCP server: compiles an agent FormSpec into A2UI 1.0 messages using the
 *    state catalog. A form opens with ONE createSurface that carries both components and dataModel.
 *    Bindings use {"@path"}, validation uses "@checks" CheckRules ({condition: {"@call"...}, message}),
 *    headings are Markdown in Text, buttons have a Text child and action.event.
 * 2. A2UI renderer: applies 1.0 messages and renders the basic catalog components it needs plus the
 *    insurance catalog (YmmPicker, VinField, AddressLookup, Repeater, AssignmentMatrix, OfferCards,
 *    Summary, FileDrop, InPlaceNote, ActionBar). Field lookups and document reads leave the renderer as
 *    callAgentFunction and come back as agentFunctionResponse (no model call).
 * 3. Stage: the "ball from the bot icon expands into the form" animation and its reverse.
 * 4. Fake services standing in for the quoting MCP tools and the document agent.
 * Everything is local. No network calls.
 */
(function (G) {
  'use strict';
  const CAT = G.STATE_CATALOG;
  const FIELD = Object.fromEntries(CAT.fields.map((f) => [f.key, f]));
  /* Language of the customer UI. fr (Desjardins demo) uses label_fr, help_fr and enum_fr from the catalog. */
  const LANG = String(G.A2UI_LANG || (typeof document !== 'undefined' && document.documentElement.lang) || 'fr').slice(0, 2);
  const FR = LANG === 'fr';
  const L = (fr, en) => (FR ? fr : en);
  const flabel = (def) => (def ? (FR && def.label_fr) || def.label || '' : '');
  const fhelp = (def) => (def ? (FR && def.help_fr) || def.help || '' : '');
  const fenum = (def) => (def ? (FR && def.enum_fr) || def.enum || null : null);
  const ENTITY_BASE = { quote: '/quote', vehicle: '/vehicles', driver: '/drivers', underwriting: '/underwriting', household: '/household' };
  const MANY = { vehicle: true, driver: true };
  const A2UI_VERSION = '1.0';
  const BASIC_CATALOG_ID = 'https://a2ui.org/specification/v1_0/catalogs/basic/catalog.json';
  /* Custom catalog (protocolVersion 1.0). It re-exports the basic components used here and adds the
     insurance components and functions. The URL is a placeholder for where the team would publish it. */
  const CATALOG_ID = 'https://catalog.example.com/insurance-catalog/v1/catalog.json';
  const YEAR = new Date().getFullYear();
  const reduceMotion = () => G.matchMedia && G.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const wait = (ms) => new Promise((r) => setTimeout(r, ms));
  const cssVar = (n) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || '#0b6e5f';

  /* ------------------------------------------------------------------ utils */
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const md = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  function ptrParts(p) { return p === '/' || p === '' ? [] : p.split('/').slice(1).map((x) => x.replace(/~1/g, '/').replace(/~0/g, '~')); }
  const ptrEsc = (k) => String(k).replace(/~/g, '~0').replace(/\//g, '~1');
  /* Provenance of a bound value lives in the data model next to it, so the BFF can update it with a
     normal updateDataModel: /_provenance/<escaped pointer> = { source, note }. */
  const provPath = (path) => '/_provenance/' + ptrEsc(path);
  function ptrDel(o, p) { const parts = ptrParts(p); let c = o; for (let i = 0; i < parts.length - 1; i++) { if (c == null) return; c = c[parts[i]]; } if (c != null) { if (Array.isArray(c)) c[parts[parts.length - 1]] = undefined; else delete c[parts[parts.length - 1]]; } }
  function ptrGet(o, p) { let c = o; for (const k of ptrParts(p)) { if (c == null) return undefined; c = c[k]; } return c; }
  function ptrSet(o, p, v) {
    const parts = ptrParts(p); let c = o;
    parts.forEach((k, i) => {
      if (i === parts.length - 1) { c[k] = v; return; }
      if (c[k] == null) c[k] = /^\d+$/.test(parts[i + 1]) ? [] : {};
      c = c[k];
    });
  }
  const keyOf = (path) => { const p = ptrParts(path); return p[p.length - 1]; };
  const parentOf = (path) => path.slice(0, path.lastIndexOf('/')) || '/';
  const clone = (o) => (o === undefined ? undefined : JSON.parse(JSON.stringify(o)));
  function entityPath(entity, index) { return MANY[entity] ? `${ENTITY_BASE[entity]}/${index || 0}` : ENTITY_BASE[entity]; }
  function resolvePath(key, specEntity) {
    if (!key) return null;
    if (key.startsWith('/')) return key;
    const def = FIELD[key];
    if (!def) throw new Error('Unknown catalog key: ' + key);
    const idx = specEntity && specEntity.type === def.entity ? specEntity.index || 0 : 0;
    return entityPath(def.entity, idx) + '/' + key;
  }
  function defForPath(path) { return FIELD[keyOf(path)] || null; }
  function fmtMoney(n, cents) { if (n == null || n === '') return ''; const o = { minimumFractionDigits: cents ? 2 : 0, maximumFractionDigits: cents ? 2 : 0 }; return FR ? Number(n).toLocaleString('fr-CA', o) + '\u00a0$' : '$' + Number(n).toLocaleString('en-CA', o); }
  function labelFor(key, value) {
    const def = FIELD[key];
    if (value === true) return L('Oui', 'Yes');
    if (value === false) return L('Non', 'No');
    if (value == null || value === '') return '';
    const en = fenum(def);
    if (en) {
      if (Array.isArray(value)) return value.map((v) => (en.find((e) => e[0] === v) || [v, v])[1]).join(', ');
      const e = en.find((x) => x[0] === String(value));
      if (e) return e[1];
    }
    if (def && def.type === 'date' && FR && /^\d{4}-\d{2}-\d{2}$/.test(String(value))) { const [y, m, d] = String(value).split('-'); return `${Number(d)} ${['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'][Number(m) - 1]} ${y}`; }
    if (def && def.type === 'currency') return fmtMoney(value);
    if (def && def.type === 'integer' && key === 'annual_kilometers') return Number(value).toLocaleString(FR ? 'fr-CA' : 'en-CA') + ' km';
    if (Array.isArray(value)) return value.length + L(' élément', ' item') + (value.length === 1 ? '' : 's');
    if (typeof value === 'object') return JSON.stringify(value);
    return String(value);
  }
  function jsonHtml(obj) {
    const s = JSON.stringify(obj, null, 2);
    return esc(s).replace(/(&quot;(?:[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\b\d+(?:\.\d+)?\b/g, (m, str, colon, lit) => {
      if (str) return colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`;
      if (lit) return `<span class="b">${m}</span>`;
      return `<span class="n">${m}</span>`;
    });
  }
  const ICON = {
    bot: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 3v5c0 4.5-3 8.3-7 10-4-1.7-7-5.5-7-10V6l7-3z"/><path d="M9 12l2 2 4-4"/></svg>',
    info: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    chat: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z"/></svg>',
    warn: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 3l10 18H2L12 3z"/><path d="M12 10v4M12 17h.01"/></svg>',
    ok: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M5 12l5 5 9-10"/></svg>',
    lock: '<svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><rect x="5" y="11" width="14" height="10" rx="2"/><path d="M8 11V8a4 4 0 0 1 8 0v3"/></svg>',
    camera: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 8h3l2-3h6l2 3h3v11H4z"/><circle cx="12" cy="13" r="3.5"/></svg>',
    search: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><circle cx="11" cy="11" r="7"/><path d="M20 20l-4-4"/></svg>',
    close: '<svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    plus: '<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M12 5v14M5 12h14"/></svg>',
    help: '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><circle cx="12" cy="12" r="9"/><path d="M12 11v5M12 8h.01"/></svg>',
    /* Placeholder approximation of the Desjardins hexagon mark (replace with the official asset) and the bot made from it. */
    brand: '<svg viewBox="0 0 40 44" aria-hidden="true"><path class="mark-o" d="M20 3.5 35.5 12.5v19L20 40.5 4.5 31.5v-19z" fill="none" stroke="currentColor" stroke-width="5" stroke-linejoin="round"/><path class="mark-i" d="M20 14.5 27 18.5v7L20 29.5 13 25.5v-7z" fill="currentColor"/></svg>',
    botface: '<svg viewBox="0 0 40 44" aria-hidden="true"><path class="hex" d="M20 2 37 11.5v21L20 42 3 32.5v-21z"/><g class="eye"><ellipse class="lid" cx="13.5" cy="21" rx="4.2" ry="5.2"/><circle class="pupil" cx="14" cy="21.8" r="2.1"/></g><g class="eye"><ellipse class="lid" cx="26.5" cy="21" rx="4.2" ry="5.2"/><circle class="pupil" cx="27" cy="21.8" r="2.1"/></g><path d="M15.5 30.5q4.5 3 9 0" fill="none" stroke="#fff" stroke-width="1.8" stroke-linecap="round" opacity=".9"/></svg>',
    car: '<svg width="30" height="30" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M4 16v-4l2-5h12l2 5v4"/><path d="M4 12h16"/><circle cx="7.5" cy="16.5" r="1.8"/><circle cx="16.5" cy="16.5" r="1.8"/></svg>',
    idcard: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><rect x="3" y="5" width="18" height="14" rx="2"/><circle cx="9" cy="11" r="2.2"/><path d="M5.8 16c.6-1.6 1.8-2.4 3.2-2.4s2.6.8 3.2 2.4M14 10h4M14 13h4"/></svg>',
    regdoc: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M6 3h9l4 4v14H6z"/><path d="M14 3v5h5M9 13h7M9 16h7M9 10h3"/></svg>',
    policy: '<svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="M12 3l8 3v5c0 4.5-3.4 8.3-8 10-4.6-1.7-8-5.5-8-10V6l8-3z"/><path d="M9 12l2 2 4-4"/></svg>',
    upload: '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 16V4M7 9l5-5 5 5M4 20h16"/></svg>'
  };

  /* -------------------------------------------------------------- validation */
  const VIN_MAP = { A: 1, B: 2, C: 3, D: 4, E: 5, F: 6, G: 7, H: 8, J: 1, K: 2, L: 3, M: 4, N: 5, P: 7, R: 9, S: 2, T: 3, U: 4, V: 5, W: 6, X: 7, Y: 8, Z: 9 };
  const VIN_W = [8, 7, 6, 5, 4, 3, 2, 10, 0, 9, 8, 7, 6, 5, 4, 3, 2];
  function vinSum(v) { let s = 0; for (let i = 0; i < 17; i++) { const c = v[i]; s += (/\d/.test(c) ? +c : VIN_MAP[c]) * VIN_W[i]; } return s; }
  function vinValid(v) {
    v = String(v || '').toUpperCase().trim();
    if (!/^[A-HJ-NPR-Z0-9]{17}$/.test(v)) return false;
    const r = vinSum(v) % 11; return v[8] === (r === 10 ? 'X' : String(r));
  }
  const VIN_REG = {};
  let vinSeed = 7;
  function makeVin(year, make, model) {
    const chars = 'ABCDEFGHJKLMNPRSTUVWXYZ0123456789';
    let v = '2T3';
    for (let i = 3; i < 17; i++) { vinSeed = (vinSeed * 9301 + 49297) % 233280; v += chars[Math.floor((vinSeed / 233280) * chars.length)]; }
    const r = vinSum(v) % 11; v = v.slice(0, 8) + (r === 10 ? 'X' : String(r)) + v.slice(9);
    VIN_REG[v] = { year, make, model };
    return v;
  }
  function ageFrom(iso) { if (!iso) return null; const d = new Date(iso); if (isNaN(d)) return null; const n = new Date(); let a = n.getFullYear() - d.getFullYear(); const m = n.getMonth() - d.getMonth(); if (m < 0 || (m === 0 && n.getDate() < d.getDate())) a--; return a; }
  /* A2UI 1.0 CheckRule: { condition: <FunctionCall>, message }. required/email/regex are expected in the
     basic catalog [UNVERIFIED names]; numericRange, ageBetween, dateWithin, vinCheckDigit, ymmValid and
     onePrincipalPerVehicle are insurance-catalog functions implemented by the renderer. */
  const rule = (call, path, args, message) => ({ condition: { '@call': call, args: Object.assign({ value: { '@path': path } }, args || {}) }, message });
  const ruleCall = (r) => (r && r.condition && r.condition['@call']) || '';
  const ruleArgs = (r) => (r && r.condition && r.condition.args) || {};
  function checksFor(def, required, path) {
    const c = [];
    if (required) c.push(rule('required', path, null, L('Ce champ est obligatoire', 'This field is required')));
    if (!def) return c;
    if (def.type === 'email') c.push(rule('email', path, null, L('Entrez une adresse courriel valide', 'Enter a valid email address')));
    if (def.type === 'vin') c.push(rule('vinCheckDigit', path, null, L('Ce NIV n’est pas valide (chiffre de contrôle)', 'This VIN is not valid (check digit)')));
    else if (def.pattern) {
      const msg = (FR ? { phone_number: 'Entrez un numéro à 10 chiffres', postal_code: 'Entrez un code postal canadien, comme H2X 1Y4', driver_license_number: 'Format du permis du Québec : A1234-123456-12' } : { phone_number: 'Enter a 10-digit phone number', postal_code: 'Enter a Canadian postal code, like H2X 1Y4', driver_license_number: 'Quebec licence format: A1234-123456-12' })[def.key] || L('Format invalide', 'Invalid format');
      c.push(rule('regex', path, { pattern: def.pattern }, msg));
    }
    const max = def.max != null ? def.max : def.max_offset_years != null ? YEAR + def.max_offset_years : undefined;
    if (def.min != null || max != null) c.push(rule('numericRange', path, { min: def.min, max }, FR ? `Entrez une valeur${def.min != null ? ' de ' + def.min : ''}${max != null ? ' à ' + max : ''}` : `Enter a value${def.min != null ? ' from ' + def.min : ''}${max != null ? ' to ' + max : ''}`));
    if (def.min_age) c.push(rule('ageBetween', path, { min: def.min_age, max: def.max_age }, L(`Vous devez avoir ${def.min_age} ans ou plus`, `Must be ${def.min_age} or older`)));
    if (def.min_offset_days != null) c.push(rule('dateWithin', path, { from_days: def.min_offset_days, to_days: def.max_offset_days }, L(`Choisissez une date dans les ${def.max_offset_days} prochains jours`, `Pick a date in the next ${def.max_offset_days} days`)));
    return c;
  }
  function runCheck(ch, v) {
    const call = ruleCall(ch); const a = ruleArgs(ch);
    const empty = v == null || v === '' || (Array.isArray(v) && v.length === 0);
    if (call === 'required') return !empty;
    if (empty) return true;
    switch (call) {
      case 'email': return /^[^\s@]+@[^\s@]+\.[^\s@]{2,}$/.test(v);
      case 'regex': return new RegExp(a.pattern, 'i').test(String(v).trim());
      case 'vinCheckDigit': return vinValid(v);
      case 'numericRange': { const n = Number(v); return !isNaN(n) && (a.min == null || n >= a.min) && (a.max == null || n <= a.max); }
      case 'ageBetween': { const g = ageFrom(v); return g != null && g >= a.min && (a.max == null || g <= a.max); }
      case 'dateWithin': { const d = new Date(v); const t0 = new Date(); t0.setHours(0, 0, 0, 0); const days = (d - t0) / 864e5; return days >= a.from_days - 1 && days <= a.to_days; }
      default: return true;
    }
  }

  /* ------------------------------------------------------- fake MCP services */
  const VEHICLES = {
    Toyota: [['Corolla', 'car', 2000], ['Camry', 'car', 2000], ['RAV4', 'suv', 2000], ['Highlander', 'suv', 2001], ['Sienna', 'minivan', 2000], ['Tacoma', 'pickup', 2000]],
    Honda: [['Civic', 'car', 2000], ['Accord', 'car', 2000], ['CR-V', 'suv', 2000], ['HR-V', 'suv', 2016], ['Odyssey', 'minivan', 2000]],
    Ford: [['F-150', 'pickup', 2000], ['Escape', 'suv', 2001], ['Mustang', 'car', 2000], ['Explorer', 'suv', 2000]],
    Hyundai: [['Elantra', 'car', 2000], ['Tucson', 'suv', 2005], ['Kona', 'suv', 2018], ['Ioniq 5', 'suv', 2022]],
    Kia: [['Forte', 'car', 2010], ['Sportage', 'suv', 2000], ['Soul', 'car', 2010], ['Carnival', 'minivan', 2022]],
    Mazda: [['Mazda3', 'car', 2004], ['CX-5', 'suv', 2013], ['CX-30', 'suv', 2020], ['MX-5', 'car', 2000]],
    Subaru: [['Impreza', 'car', 2000], ['Outback', 'suv', 2000], ['Forester', 'suv', 2000], ['Crosstrek', 'suv', 2013]],
    Tesla: [['Model 3', 'car', 2017], ['Model Y', 'suv', 2020]],
    Chevrolet: [['Silverado', 'pickup', 2000], ['Equinox', 'suv', 2005], ['Bolt EV', 'car', 2017]],
    Volkswagen: [['Jetta', 'car', 2000], ['Golf', 'car', 2000], ['Tiguan', 'suv', 2009]],
    Nissan: [['Rogue', 'suv', 2008], ['Sentra', 'car', 2000], ['Leaf', 'car', 2011]],
    Lamborghini: [['Huracán', 'car', 2014], ['Urus', 'suv', 2018]],
    'Harley-Davidson': [['Street Glide', 'motorcycle', 2006], ['Sportster', 'motorcycle', 2000]]
  };
  function makeServices(log) {
    const L = log || (() => {});
    const FN = { 'quoting.lookup_make_model_year': 'lookupMakeModelYear', 'quoting.identify_vehicle_type': 'identifyVehicleType', 'quoting.decode_vin': 'decodeVin', 'quoting.identify_address': 'identifyAddress' };
    const t = (name, args, result, ms) => { L({ name, fn: FN[name] || 'extractDocument', args, result, ms: ms || 40 + Math.round(Math.random() * 120) }); return result; };
    return {
      years: () => { const y = []; for (let i = YEAR + 1; i >= 1990; i--) y.push(i); return y; },
      lookupYMM(q) {
        const year = Number(q.year) || null;
        const makes = Object.keys(VEHICLES).filter((m) => !year || VEHICLES[m].some((x) => year >= x[2]));
        const models = q.make && VEHICLES[q.make] ? VEHICLES[q.make].filter((x) => !year || year >= x[2]).map((x) => x[0]) : undefined;
        const valid = !!(year && q.make && q.model && models && models.includes(q.model));
        return t('quoting.lookup_make_model_year', q, { valid, normalized: valid ? { year, make: q.make, model: q.model } : null, makes, models });
      },
      identifyVehicleType(make, model, year) {
        const m = (VEHICLES[make] || []).find((x) => x[0] === model);
        return t('quoting.identify_vehicle_type', { make, model, year }, { vehicle_type: m ? m[1] : 'car', confidence: m ? 0.98 : 0.6 });
      },
      decodeVin(vin) {
        const v = String(vin || '').toUpperCase();
        const ok = vinValid(v);
        const reg = VIN_REG[v];
        return t('quoting.decode_vin', { vin: v }, ok ? Object.assign({ valid_checksum: true }, reg || { year: 2019 + (vinSum(v) % 5), make: 'Toyota', model: 'RAV4' }) : { valid_checksum: false });
      },
      identifyAddress(query) {
        const num = (String(query).match(/\d+/) || ['125'])[0];
        let street = String(query).replace(num, '').replace(/[,]/g, ' ').trim();
        if (!street || /^[A-Z]\d[A-Z]/i.test(street)) street = 'Rue des Érables';
        street = street.replace(/\b\w/g, (c) => c.toUpperCase());
        if (!/^(rue|avenue|boulevard|chemin|av|boul)\b/i.test(street)) street = 'Rue ' + street;
        const cands = [
          { address_line1: `${num} ${street}`, city: 'Laval', province_code: 'QC', postal_code: 'H7N 1A1', confidence: 0.93 },
          { address_line1: `${num} ${street}`, city: 'Montréal', province_code: 'QC', postal_code: 'H2X 1Y4', confidence: 0.71 },
          { address_line1: `${num} ${street}`, city: 'Québec', province_code: 'QC', postal_code: 'G1R 2T3', confidence: 0.58 }
        ];
        return t('quoting.identify_address', { query }, { candidates: cands });
      },
      extractDocument(purpose) {
        const r = {
          vin: () => { const vin = makeVin(2021, 'Toyota', 'RAV4'); return { document_ok: true, fields: { vin: { value: vin, confidence: 0.97 }, vehicle_year: { value: 2021, confidence: 0.9 }, vehicle_make: { value: 'Toyota', confidence: 0.92 } } }; },
          licence: () => ({ document_ok: true, fields: { first_name: { value: 'Marie', confidence: 0.99 }, last_name: { value: 'Tremblay', confidence: 0.99 }, date_of_birth: { value: '1987-01-18', confidence: 0.96 }, driver_license_number: { value: 'T6512-180187-08', confidence: 0.95 }, license_class: { value: '5', confidence: 0.88 } } }),
          registration: () => { const vin = makeVin(2021, 'Toyota', 'RAV4'); return { document_ok: true, fields: { vin: { value: vin, confidence: 0.95 }, vehicle_make: { value: 'Toyota', confidence: 0.97 }, vehicle_model: { value: 'RAV4', confidence: 0.94 }, vehicle_year: { value: 2021, confidence: 0.96 } } }; },
          policy: () => ({ document_ok: true, fields: { years_with_current_insurer: { value: 6, confidence: 0.86 }, listed_driver_1_name: { value: 'Alex Tremblay', confidence: 0.9 }, listed_driver_1_relationship: { value: 'spouse', confidence: 0.84 } } }),
          declaration: () => ({ document_ok: true, fields: { years_with_current_insurer: { value: 6, confidence: 0.82 }, policy_expiry: { value: `${YEAR}-12-31`, confidence: 0.9 }, has_claims: { value: false, confidence: 0.74 } } })
        }[purpose] || (() => ({ document_ok: false, reason: 'unknown purpose' }));
        return t('document.extract (document agent, gpt-5.6-luna)', { purpose }, r(), 900 + Math.round(Math.random() * 500));
      }
    };
  }

  /* -------------------------------------------------- compiler (MCP server) */
  const NARROW = { text: 1, number: 1, date: 1, select: 1, toggle: 1, currency: 1, email: 1, phone: 1, postal_code: 1, vin: 1 };
  function autoWidget(def) {
    if (!def) return 'text';
    switch (def.type) {
      case 'enum': return def.enum.length <= 4 ? 'chips' : def.enum.length > 7 ? 'select' : 'chips';
      case 'multi_enum': return 'chips';
      case 'boolean': return def.key === 'contact_consent' ? 'checkbox' : 'toggle';
      case 'date': return 'date';
      case 'integer': return 'number';
      case 'currency': return 'currency';
      case 'email': return 'email';
      case 'phone': return 'phone';
      case 'postal_code': return 'postal_code';
      case 'vin': return 'vin';
      case 'assignment_matrix': return 'assignment_board'; /* D17: drag to connect replaces the matrix */
      default: return 'text';
    }
  }
  function parseVisibleIf(expr, basePath) {
    if (!expr) return null;
    const m = String(expr).match(/^\s*([a-z0-9_/]+)\s*==\s*(true|false|'[^']*'|"[^"]*"|\d+)\s*$/i);
    if (!m) return null;
    let v = m[2]; v = v === 'true' ? true : v === 'false' ? false : /^\d+$/.test(v) ? Number(v) : v.slice(1, -1);
    const path = m[1].startsWith('/') ? m[1] : basePath + '/' + m[1];
    return { '@call': 'equals', args: { left: { '@path': path }, right: v } };
  }

  function compile(spec) {
    const comps = [];
    const data = {};
    let n = 0;
    const nid = (p) => `${p}_${++n}`;
    const sid = String(spec.form_id).replace(/[^A-Za-z0-9_]/g, '_');
    const add = (c) => { comps.push(c); return c.id; };
    const root = [];
    root.push(add({ id: 'title', component: 'Text', text: '## ' + spec.title }));
    if (spec.intro) root.push(add({ id: 'intro', component: 'InPlaceNote', tone: spec.intro_tone || 'chat', text: spec.intro }));

    function fieldComp(f, opts) {
      opts = opts || {};
      const path = f.key ? (opts.pathTemplate ? opts.pathTemplate(f.key) : resolvePath(f.key, spec.entity)) : null;
      const def = f.key ? FIELD[f.key.startsWith('/') ? keyOf(f.key) : f.key] : null;
      const widget = !f.widget || f.widget === 'auto' ? autoWidget(def) : f.widget;
      const label = f.label || flabel(def);
      const required = f.required != null ? f.required : !!(def && def.required_for_quote && !opts.inRepeat);
      const base = { label, hint: f.hint, help: f.help || fhelp(def) || undefined, required: required || undefined, readonly: f.readonly || undefined, deterministic: def && def.deterministic ? true : undefined };
      if (f.prefill && path && !opts.pathTemplate) {
        if (f.prefill.value !== undefined && !{ ymm: 1, address_lookup: 1, matrix: 1, offer_cards: 1, repeater: 1 }[widget]) ptrSet(data, path, clone(f.prefill.value));
        if (f.prefill.source || f.prefill.note) ptrSet(data, provPath(path), { source: f.prefill.source || 'chat', note: f.prefill.note });
      }
      if (f.visible_if && path) base.visibleIf = parseVisibleIf(f.visible_if, parentOf(path));
      if (f.field_actions) base.fieldActions = f.field_actions;
      const checks = checksFor(def, required, path);
      if (checks.length) base['@checks'] = checks;
      if (path && !opts.pathTemplate) base.provenance = { '@path': provPath(path) };
      const id = nid(widget);
      let c;
      const opt = (arr) => arr.map((e) => ({ value: String(e[0]), label: e[1] }));
      switch (widget) {
        case 'info':
          return { id: add({ id, component: 'InPlaceNote', tone: f.tone || 'info', text: [f.label, f.hint].filter(Boolean).join(FR ? '\u00a0: ' : ': ') }), wide: true };
        case 'text': case 'email': case 'phone': case 'postal_code': case 'number': case 'currency':
          c = Object.assign({ id, component: 'TextField', textFieldType: widget === 'text' ? (def && def.key === 'driver_license_number' ? 'licence' : 'shortText') : widget, value: { '@path': path } }, base);
          if (f.placeholder) c.placeholder = f.placeholder;
          if (f.computed) c.computed = f.computed;
          break;
        case 'date':
          c = Object.assign({ id, component: 'DateTimeInput', enableDate: true, enableTime: false, value: { '@path': path } }, base); break;
        case 'select': case 'chips': case 'radio': case 'segmented': case 'time_slots': {
          const options = f.options ? opt(f.options) : fenum(def) ? opt(fenum(def)) : [];
          c = Object.assign({ id, component: 'ChoicePicker', variant: widget === 'time_slots' ? 'slots' : widget, options, value: { '@path': path } }, base);
          if (def && def.type === 'multi_enum') { c.multiple = true; c.maxAllowedSelections = options.length; }
          break;
        }
        case 'toggle':
          c = Object.assign({ id, component: 'ChoicePicker', variant: 'segmented', valueType: 'boolean', options: [{ value: 'true', label: L('Oui', 'Yes') }, { value: 'false', label: L('Non', 'No') }], value: { '@path': path } }, base); break;
        case 'checkbox':
          c = Object.assign({ id, component: 'CheckBox', value: { '@path': path } }, base); break;
        case 'slider':
          c = Object.assign({ id, component: 'Slider', value: { '@path': path }, minValue: def && def.min != null ? def.min : 0, maxValue: f.max || (def && def.max) || 100, step: (def && def.step) || 1, unit: f.unit || (def && def.key === 'annual_kilometers' ? 'km' : '') }, base); break;
        case 'ymm': {
          const b = path ? parentOf(path) : entityPath('vehicle', spec.entity && spec.entity.index);
          c = Object.assign({ id, component: 'YmmPicker', year: { '@path': b + '/vehicle_year' }, make: { '@path': b + '/vehicle_make' }, model: { '@path': b + '/vehicle_model' }, vehicleType: { '@path': b + '/vehicle_type' } }, base);
          c.label = f.label || L('Année, marque et modèle', 'Year, make and model');
          c['@checks'] = [rule('ymmValid', b + '/vehicle_year', { make: { '@path': b + '/vehicle_make' }, model: { '@path': b + '/vehicle_model' } }, L('Choisissez une année, une marque et un modèle valides', 'Pick a valid year, make and model'))];
          c.required = f.required !== false; /* the full form lets an unknown vehicle stay empty */
          if (f.prefill && f.prefill.value && typeof f.prefill.value === 'object') { for (const k of ['vehicle_year', 'vehicle_make', 'vehicle_model', 'vehicle_type']) if (f.prefill.value[k] != null) ptrSet(data, b + '/' + k, f.prefill.value[k]); }
          return { id: add(c), wide: true };
        }
        case 'vin':
          c = Object.assign({ id, component: 'VinField', value: { '@path': path }, decode: true }, base); break;
        case 'address_lookup':
          c = Object.assign({ id, component: 'AddressLookup', line1: { '@path': '/quote/address_line1' }, city: { '@path': '/quote/city' }, province: { '@path': '/quote/province_code' }, postal: { '@path': '/quote/postal_code' } }, base);
          c.label = f.label || L('Adresse du domicile', 'Home address');
          if (f.prefill && f.prefill.value && typeof f.prefill.value === 'object') for (const k of Object.keys(f.prefill.value)) ptrSet(data, '/quote/' + k, f.prefill.value[k]);
          return { id: add(c), wide: true };
        case 'textarea':
          c = Object.assign({ id, component: 'TextField', textFieldType: 'longText', value: { '@path': path } }, base);
          return { id: add(c), wide: true };
        case 'document_drop':
          c = Object.assign({ id, component: 'DocumentDrop', documents: (f.data && f.data.documents) || [], read: (f.data && f.data.read) || [] }, base);
          if (f.data && f.data.values) for (const [p, v] of Object.entries(f.data.values)) ptrSet(data, p, clone(v));
          return { id: add(c), wide: true };
        case 'assignment_board':
          c = Object.assign({ id, component: 'AssignmentBoard', value: { '@path': path || '/household/driver_vehicle_assignments' }, drivers: (f.data && f.data.drivers) || [], vehicles: (f.data && f.data.vehicles) || [] }, base);
          c.label = f.label || L('Qui conduit quoi', 'Who drives what');
          c['@checks'] = [rule('onePrincipalPerVehicle', c.value['@path'], null, L('Chaque véhicule a besoin d’un conducteur principal', 'Each vehicle needs exactly one principal driver'))];
          if (f.prefill && f.prefill.value) ptrSet(data, c.value['@path'], clone(f.prefill.value));
          return { id: add(c), wide: true };
        case 'file':
          c = Object.assign({ id, component: 'FileDrop', purpose: (f.data && f.data.purpose) || 'licence', fills: (f.data && f.data.fills) || {} }, base); return { id: add(c), wide: true };
        case 'repeater': {
          const r = f.repeat || {};
          const coll = f.key && f.key.startsWith('/') ? f.key : '/drivers';
          const start = r.start != null ? r.start : 0;
          const template = (r.fields || []).map((tf) => {
            const tdef = FIELD[tf.key];
            const sub = fieldComp(tf, { inRepeat: true, pathTemplate: (k) => k }); /* relative @path, resolved per item */
            void tdef; return sub.id;
          });
          const compact = (r.fields || []).length === 1 && (r.fields[0].widget === 'vin');
          c = Object.assign({ id, component: 'Repeater', collection: coll, start, count: r.count || 1, min: r.min != null ? r.min : 0, max: r.max || 25, itemLabel: r.item_label || 'Item {n}', template, layout: compact ? 'list' : 'grid', addLabel: r.add_label || 'Add another' }, base);
          if (r.prefill) for (const [i, item] of r.prefill.entries()) for (const [k, v] of Object.entries(item)) ptrSet(data, `${coll}/${start + i}/${k}`, v);
          return { id: add(c), wide: true };
        }
        case 'matrix':
          c = Object.assign({ id, component: 'AssignmentMatrix', value: { '@path': path || '/household/driver_vehicle_assignments' }, rows: (f.data && f.data.rows) || [], cols: (f.data && f.data.cols) || [], choices: (f.data && f.data.choices) || ((FR && FIELD.driver_vehicle_assignments.item_role_fr) || FIELD.driver_vehicle_assignments.item.role).map((e) => ({ value: e[0], label: e[1] })) }, base);
          if (f.prefill && f.prefill.value) ptrSet(data, c.value['@path'], clone(f.prefill.value));
          return { id: add(c), wide: true };
        case 'offer_cards':
          c = Object.assign({ id, component: 'OfferCards', value: { '@path': path || '/underwriting/offer_type' }, offers: (f.data && f.data.offers) || [], recommended: f.data && f.data.recommended }, base);
          if (f.prefill && f.prefill.value) ptrSet(data, c.value['@path'], f.prefill.value);
          return { id: add(c), wide: true };
        case 'summary':
          return { id: add(Object.assign({ id, component: 'Summary', items: (f.data && f.data.items) || [] }, base)), wide: true };
        default:
          c = Object.assign({ id, component: 'TextField', textFieldType: 'shortText', value: { '@path': path } }, base);
      }
      if (c.component === 'ChoicePicker' && f.widget !== 'select' && (c.options.length > 3 || c.variant === 'radio' || c.variant === 'slots' || c.multiple)) return { id: add(c), wide: true };
      if (c.component === 'CheckBox' || c.component === 'Slider') return { id: add(c), wide: true };
      return { id: add(c), wide: !NARROW[widget] };
    }

    const layout = spec.layout || 'two_column';
    let rowBuf = [];
    const flush = () => { if (rowBuf.length === 1) root.push(rowBuf[0]); else if (rowBuf.length) root.push(add({ id: nid('row'), component: 'Row', children: rowBuf })); rowBuf = []; };
    for (const f of spec.fields) {
      if (f.widget === 'section') { flush(); root.push(add({ id: nid('section_title'), component: 'Text', text: '### ' + f.label })); continue; }
      const r = fieldComp(f);
      if (layout === 'two_column' && !r.wide) { rowBuf.push(r.id); if (rowBuf.length === 2) flush(); }
      else { flush(); root.push(r.id); }
    }
    flush();
    const actions = (spec.actions || [{ id: 'submit', label: L('Continuer', 'Continue'), style: 'primary' }]).map((a) => {
      const lid = add({ id: 'action_' + a.id + '_label', component: 'Text', text: a.label });
      const b = { id: 'action_' + a.id, component: 'Button', child: lid, action: { event: { name: a.id, context: { surfaceId: sid } } } };
      if (a.style === 'primary') b.variant = 'primary'; else if (a.style === 'link') b.variant = 'borderless';
      return add(b);
    });
    root.push(add({ id: 'actions', component: 'ActionBar', children: actions }));
    comps.unshift({ id: 'root', component: 'Column', children: root });
    /* A2UI 1.0: one message opens the form. dataModel is applied before components. */
    return [{ version: A2UI_VERSION, createSurface: { surfaceId: sid, catalogId: CATALOG_ID, dataModel: data, components: comps } }];
  }

  /* ----------------------------------------------------------------- renderer */
  function h(tag, attrs, html) {
    const el = document.createElement(tag);
    if (attrs) for (const [k, v] of Object.entries(attrs)) { if (v == null || v === false) continue; if (k === 'class') el.className = v; else el.setAttribute(k, v === true ? '' : v); }
    if (html != null) el.innerHTML = html;
    return el;
  }

  class Surface {
    constructor(surfaceId, host) {
      this.id = surfaceId; this.host = host || {};
      this.components = new Map(); this.data = {}; this.catalogId = null;
      this.bindings = []; this.visRules = []; this.provs = []; this.el = null; this.deleted = false;
      this.services = this.host.services || makeServices();
    }
    apply(msg) {
      if (msg.version !== A2UI_VERSION) throw new Error('A2UI version ' + msg.version + ' not supported; expected ' + A2UI_VERSION);
      if (msg.createSurface) {
        const cs = msg.createSurface;
        this.catalogId = cs.catalogId || null;
        if (cs.dataModel) this.data = clone(cs.dataModel);
        if (cs.components) for (const c of cs.components) this.components.set(c.id, c);
      }
      if (msg.updateComponents) { for (const c of msg.updateComponents.components) this.components.set(c.id, c); if (this.el) this.rerender(); }
      if (msg.updateDataModel) {
        const { path, value } = msg.updateDataModel; /* 1.0: value is required; null deletes the key */
        if (!path || path === '/') this.data = value == null ? {} : clone(value);
        else if (value === null) ptrDel(this.data, path);
        else ptrSet(this.data, path, clone(value));
        if (this.el) this.syncFrom(path || '/');
      }
      if (msg.deleteSurface) this.deleted = true;
    }
    get title() { const t = this.components.get('title'); return t ? String(t.text || '').replace(/^#+\s*/, '') : ''; }
    get(path) { return ptrGet(this.data, path); }
    /* Record where a value came from (chat, document, lookup) and show it on the field. */
    mark(path, prov) { this.set(provPath(path), prov, true); this.syncFrom(provPath(path)); this.syncFrom(path); }
    set(path, v, silent) {
      ptrSet(this.data, path, v);
      this.evalVisibility();
      if (!silent && this.host.onChange) this.host.onChange(this, path, v);
    }
    render() {
      const card = h('section', { class: 'form-card', 'data-surface': this.id, role: 'dialog', 'aria-label': this.title || 'Form' });
      const head = h('header', { class: 'form-head' });
      const body = h('div', { class: 'form-body' });
      const foot = h('footer', { class: 'form-foot' });
      card.append(head, body, foot);
      this.el = card; this.bodyEl = body; this.footEl = foot; this.headEl = head;
      this.rerender();
      return card;
    }
    rerender() {
      this.bindings = []; this.visRules = []; this.provs = [];
      const rootC = this.components.get('root');
      this.headEl.innerHTML = `<div><h2>${esc(this.title)}</h2><div class="surface-id">surface: ${esc(this.id)} · A2UI 1.0</div></div><div class="spacer"></div>`;
      const x = h('button', { class: 'btn ghost small', 'aria-label': L('Fermer le formulaire et répondre dans la conversation', 'Close form and answer in chat'), title: L('Fermer', 'Answer in chat instead') }, ICON.close);
      x.onclick = () => this.host.onAction && this.host.onAction(this, 'dismiss', {});
      this.headEl.append(x);
      this.bodyEl.innerHTML = ''; this.footEl.innerHTML = '';
      const col = h('div', { class: 'a2-col' });
      for (const cid of rootC.children) {
        const c = this.components.get(cid);
        if (!c || cid === 'title') continue;
        if (c.component === 'ActionBar') { this.renderFooter(c); continue; }
        const el = this.renderComp(c, {}); if (el) col.append(el);
      }
      this.bodyEl.append(col);
      this.evalVisibility();
    }
    renderFooter(row) {
      this.errSummary = h('div', { class: 'err-summary', 'aria-live': 'polite' });
      this.footEl.append(this.errSummary, h('div', { class: 'spacer' }));
      if (this.host.demoFill) {
        const df = h('button', { class: 'btn ghost small demo-fill', title: L('Démo seulement : réponses d’exemple', 'Demo only: fill with sample answers') }, L('Démo : remplir', 'Demo: fill sample'));
        df.onclick = () => this.host.demoFill(this);
        this.footEl.append(df);
      }
      for (const bid of row.children) {
        const b = this.components.get(bid);
        const label = (this.components.get(b.child) || {}).text || '';
        const btn = h('button', { class: 'btn ' + (b.variant === 'primary' ? 'primary' : b.variant === 'borderless' ? 'ghost' : '') }, esc(label));
        btn.onclick = () => this.fire(b.action.event.name);
        this.footEl.append(btn);
      }
    }
    fire(name) {
      if (name === 'submit' || name === 'select' || name === 'confirm') {
        const errs = this.validate();
        if (errs.length) { this.errSummary.textContent = FR ? `${errs.length} champ${errs.length > 1 ? 's à corriger' : ' à corriger'}` : `${errs.length} field${errs.length > 1 ? 's need' : ' needs'} attention`; const first = this.bodyEl.querySelector('.fld.invalid input, .fld.invalid select, .fld.invalid button'); if (first) first.focus(); return; }
        this.errSummary.textContent = '';
      }
      if (this.host.onAction) this.host.onAction(this, name, { values: this.collect() });
    }
    collect() {
      const out = {};
      for (const b of this.bindings) { if (b.isVisible && !b.isVisible()) continue; const v = this.get(b.path); if (v !== undefined && v !== null && v !== '') out[b.path] = clone(v); }
      return out;
    }
    validate() {
      const errs = [];
      for (const b of this.bindings) {
        if (!b.checks || (b.isVisible && !b.isVisible())) { b.showError && b.showError(null); continue; }
        const v = b.value ? b.value() : this.get(b.path);
        const bad = b.checks.find((ch) => { const local = b.localCheck ? b.localCheck(ruleCall(ch)) : undefined; return local !== undefined ? !local : !runCheck(ch, v); });
        b.showError(bad ? bad.message : null);
        if (bad) errs.push({ path: b.path, message: bad.message });
      }
      return errs;
    }
    evalVisibility() {
      for (const r of this.visRules) { const a = r.rule.args; const show = this.get(a.left['@path']) === a.right; r.el.hidden = !show; }
    }
    syncFrom(path) {
      if (path === '/' || path.startsWith('/_provenance')) {
        for (const p of this.provs) if (path === '/' || path === '/_provenance' || p.path === path) p.set(this.get(p.path));
        if (path !== '/') return;
      }
      for (const b of this.bindings) {
        if (path === '/' || b.path === path || b.path.startsWith(path + '/') || path.startsWith(b.path + '/')) {
          b.refresh && b.refresh();
          if (path !== '/' && b.fld) { b.fld.classList.remove('autofilled'); void b.fld.offsetWidth; b.fld.classList.add('autofilled'); }
        }
      }
      this.evalVisibility();
    }
    fieldShell(c, forId) {
      const fld = h('div', { class: 'fld' });
      const lbl = h(forId ? 'label' : 'div', forId ? { for: forId } : { class: 'lbl' });
      lbl.innerHTML = `${esc(c.label)}${c.required ? '<span class="req" aria-hidden="true">*</span>' : ''}${c.help ? `<span class="help" tabindex="0" data-help="${esc(c.help)}" data-title="${esc(c.label)}" aria-label="${esc(L('Aide', 'Help'))}">${ICON.help}</span>` : ''}${c.deterministic ? ` <span class="badge det" title="${esc(L('Toute modification est confirmée deux fois', 'Changes to this field need a double confirmation'))}">${ICON.lock} ${esc(L('confirmé deux fois', 'confirm twice'))}</span>` : ''}`;
      fld.append(lbl);
      if (c.hint) fld.append(h('div', { class: 'hint' }, md(c.hint)));
      const prov = h('div', { class: 'prov' });
      const err = h('div', { class: 'err', role: 'alert' });
      const setProv = (p) => { prov.innerHTML = p ? `<span class="badge ${esc(p.source)}">${esc(p.source === 'chat' ? L('Tiré de la conversation', 'from chat') : p.source === 'document' ? L('Lu sur votre document', 'from document') : p.source === 'lookup' ? L('Trouvé par recherche', 'from lookup') : p.source === 'previous_quote' ? L('Déjà fourni', 'already given') : p.source)}</span>${p.note ? `<span class="pnote" title="${esc(p.note)}">${esc(p.note)}</span>` : ''}` : ''; };
      const pp = c.provenance && c.provenance['@path'];
      if (pp) { this.provs.push({ path: pp, set: setProv }); setProv(this.get(pp)); }
      if (c.visibleIf) this.visRules.push({ el: fld, rule: c.visibleIf });
      return { fld, lbl, prov, err, setProv, showError: (m) => { err.textContent = m || ''; fld.classList.toggle('invalid', !!m); } };
    }
    bind(b) { this.bindings.push(b); return b; }
    renderComp(c, ctx) {
      const R = this['r' + c.component];
      if (!R) return h('div', { class: 'note warn' }, `Unknown component ${esc(c.component)}`);
      return R.call(this, c, ctx);
    }
    rColumn(c, ctx) { const el = h('div', { class: 'a2-col' }); for (const id of c.children) el.append(this.renderComp(this.components.get(id), ctx)); return el; }
    rRow(c, ctx) { const el = h('div', { class: 'a2-row' }); for (const id of c.children) el.append(this.renderComp(this.components.get(id), ctx)); return el; }
    rText(c) { const t = String(c.text || ''); const m = t.match(/^#{1,6}\s+(.*)$/); return m ? h('div', { class: 'a2-text-h3', role: 'heading' }, md(m[1])) : h('div', { class: c.variant === 'caption' ? 'a2-text-caption' : 'a2-text-body' }, md(t)); }
    rInPlaceNote(c) { return h('div', { class: 'note ' + (c.tone || 'info') }, `<span class="ic">${ICON[c.tone] || ICON.info}</span><div>${md(c.text)}</div>`); }
    /* Absolute {"@path": "/x"} or relative {"@path": "x"} resolved in the repeater item scope. */
    pathOf(c, ctx, key) { const p = (c[key || 'value'] || {})['@path']; return p && !p.startsWith('/') && ctx && ctx.scope ? ctx.scope + '/' + p : p; }
    rTextField(c, ctx) {
      const path = this.pathOf(c, ctx);
      const fid = 'f-' + this.id + '-' + path.replace(/\W/g, '_');
      const s = this.fieldShell(c, fid);
      const type = { email: 'email', phone: 'tel', number: 'number', currency: 'number' }[c.textFieldType] || 'text';
      const wrap = h('div', { class: 'input-wrap' + (c.textFieldType === 'currency' ? ' has-prefix' : '') });
      if (c.textFieldType === 'currency') wrap.append(h('span', { class: 'prefix' }, '$'));
      if (c.textFieldType === 'longText') {
        const ta = h('textarea', { id: fid, rows: 6, placeholder: c.placeholder || '' });
        const refreshT = () => { const v = this.get(path); ta.value = v == null ? '' : v; };
        ta.addEventListener('input', () => { this.set(path, ta.value); s.showError(null); });
        refreshT(); s.fld.append(ta, s.prov, s.err);
        this.bind({ path, checks: c['@checks'], fld: s.fld, refresh: refreshT, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
        return s.fld;
      }
      const inp = h('input', { id: fid, type, autocomplete: 'off', inputmode: c.textFieldType === 'number' || c.textFieldType === 'currency' ? 'numeric' : null, placeholder: c.placeholder || { phone: '(514) 555-0142', postal_code: 'H2X 1Y4', licence: 'A1234-123456-12', email: L('nom@exemple.com', 'name@example.com') }[c.textFieldType] || '', readonly: c.readonly });
      wrap.append(inp);
      const numeric = c.textFieldType === 'number' || c.textFieldType === 'currency';
      const refresh = () => { const v = this.get(path); inp.value = v == null ? '' : v; };
      inp.addEventListener('input', () => { let v = inp.value; if (numeric) v = v === '' ? '' : Number(v); this.set(path, v); s.showError(null); });
      inp.addEventListener('blur', () => {
        let v = inp.value.trim();
        if (c.textFieldType === 'postal_code' && /^[a-z]\d[a-z]\s?\d[a-z]\d$/i.test(v)) v = (v.slice(0, 3) + ' ' + v.slice(-3)).toUpperCase().replace('  ', ' ');
        if (c.textFieldType === 'phone') { const d = v.replace(/\D/g, ''); if (d.length === 10) v = `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`; }
        if (c.textFieldType === 'licence') v = v.toUpperCase();
        if (!numeric && v !== inp.value) { inp.value = v; this.set(path, v); }
        const b = binding; const bad = (b.checks || []).find((ch) => !runCheck(ch, numeric ? (v === '' ? '' : Number(v)) : v));
        s.showError(bad && v !== '' ? bad.message : null);
      });
      refresh();
      s.fld.append(wrap, s.prov, s.err);
      if (c.fieldActions) this.attachFieldActions(c, wrap, path, refresh);
      const binding = this.bind({ path, checks: c['@checks'], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    attachFieldActions(c, wrap, path, refresh) {
      for (const a of c.fieldActions) {
        const b = h('button', { class: 'btn small', type: 'button' }, `${a.kind === 'upload_photo' ? ICON.camera : ICON.search} ${esc(a.label)}`);
        b.onclick = async () => {
          if (!this.host.onFieldAction) return;
          b.disabled = true; const old = b.innerHTML; b.innerHTML = `<span class="spin"></span> ${L('Lecture…', 'Reading…')}`;
          try { const r = await this.host.onFieldAction(this, { id: a.id, kind: a.kind, path }); if (r && r.value !== undefined) { this.set(path, r.value); refresh(); this.mark(path, { source: r.source || 'document', note: r.note }); } }
          finally { b.disabled = false; b.innerHTML = old; }
        };
        wrap.append(b);
      }
    }
    rDateTimeInput(c, ctx) {
      const path = this.pathOf(c, ctx);
      const fid = 'f-' + this.id + '-' + path.replace(/\W/g, '_');
      const s = this.fieldShell(c, fid);
      const inp = h('input', { id: fid, type: 'date' });
      const win = (c['@checks'] || []).find((x) => ruleCall(x) === 'dateWithin');
      if (win) { const d0 = new Date(); const d1 = new Date(Date.now() + ruleArgs(win).to_days * 864e5); inp.min = d0.toISOString().slice(0, 10); inp.max = d1.toISOString().slice(0, 10); }
      const refresh = () => { inp.value = this.get(path) || ''; };
      inp.addEventListener('change', () => { this.set(path, inp.value); s.showError(null); });
      refresh();
      s.fld.append(inp, s.prov, s.err);
      this.bind({ path, checks: c['@checks'], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    rChoicePicker(c, ctx) {
      const path = this.pathOf(c, ctx);
      const s = this.fieldShell(c, c.variant === 'select' ? 'f-' + this.id + '-' + path.replace(/\W/g, '_') : null);
      const toVal = (v) => (c.valueType === 'boolean' ? v === 'true' : /^\d+$/.test(v) && FIELD[keyOf(path)] && FIELD[keyOf(path)].type === 'integer' ? Number(v) : v);
      const cur = () => { const v = this.get(path); return v == null ? (c.multiple ? [] : null) : v; };
      let ctrl; let refresh;
      if (c.variant === 'select') {
        ctrl = h('select', { id: 'f-' + this.id + '-' + path.replace(/\W/g, '_') });
        ctrl.append(h('option', { value: '' }, L('Sélectionner', 'Choose…')));
        for (const o of c.options) ctrl.append(h('option', { value: o.value }, esc(o.label)));
        refresh = () => { const v = cur(); ctrl.value = v == null ? '' : String(v); };
        ctrl.addEventListener('change', () => { this.set(path, ctrl.value === '' ? null : toVal(ctrl.value)); s.showError(null); });
      } else {
        const cls = { chips: 'chips', segmented: 'seg', radio: 'radios', slots: 'slots' }[c.variant] || 'chips';
        ctrl = h('div', { class: cls, role: c.multiple ? 'group' : 'radiogroup', 'aria-label': c.label });
        const btns = c.options.map((o) => {
          const b = h('button', { type: 'button', class: c.variant === 'radio' ? 'radio-card' : c.variant === 'segmented' ? '' : 'chip', role: c.multiple ? 'checkbox' : 'radio', 'data-v': o.value }, (c.variant === 'radio' ? '<span class="rdot"></span>' : '') + `<span>${esc(o.label)}</span>`);
          b.onclick = () => {
            if (c.multiple) { const arr = [...cur()]; const i = arr.indexOf(o.value); if (i >= 0) arr.splice(i, 1); else arr.push(o.value); this.set(path, arr); }
            else this.set(path, toVal(o.value));
            refresh(); s.showError(null);
          };
          ctrl.append(b); return b;
        });
        refresh = () => { const v = cur(); btns.forEach((b) => b.setAttribute('aria-checked', String(c.multiple ? v.includes(b.dataset.v) : v != null && String(v) === b.dataset.v))); };
      }
      refresh();
      s.fld.append(ctrl, s.prov, s.err);
      this.bind({ path, checks: c['@checks'], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    rCheckBox(c, ctx) {
      const path = this.pathOf(c, ctx);
      const s = this.fieldShell(Object.assign({}, c, { label: '' }), null);
      s.fld.querySelector('.lbl').remove();
      const lab = h('label', { class: 'check' });
      const inp = h('input', { type: 'checkbox' });
      lab.append(inp, h('span', null, `${md(c.label)}${c.required ? '<span class="req">*</span>' : ''}${c.deterministic ? ` <span class="badge det">${ICON.lock} ${esc(L('confirmé deux fois', 'confirm twice'))}</span>` : ''}`));
      const refresh = () => { inp.checked = this.get(path) === true; };
      inp.addEventListener('change', () => { this.set(path, inp.checked ? true : null); s.showError(null); });
      refresh();
      s.fld.append(lab, s.prov, s.err);
      this.bind({ path, checks: c['@checks'], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    rSlider(c, ctx) {
      const path = this.pathOf(c, ctx);
      const s = this.fieldShell(c, null);
      const wrap = h('div', { class: 'slider-wrap' });
      const inp = h('input', { type: 'range', min: c.minValue, max: c.maxValue, step: c.step, 'aria-label': c.label });
      const val = h('div', { class: 'slider-val' });
      const show = (v) => { val.textContent = v == null ? '—' : Number(v).toLocaleString(FR ? 'fr-CA' : 'en-CA') + (c.unit ? ' ' + c.unit : ''); };
      const refresh = () => { const v = this.get(path); inp.value = v == null ? Math.round((c.minValue + c.maxValue) / 4 / c.step) * c.step : v; show(v); };
      inp.addEventListener('input', () => { this.set(path, Number(inp.value)); show(inp.value); s.showError(null); });
      refresh();
      wrap.append(inp, val);
      s.fld.append(wrap, s.prov, s.err);
      this.bind({ path, checks: c['@checks'], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    rYmmPicker(c, ctx) {
      const P = { y: this.pathOf(c, ctx, 'year'), m: this.pathOf(c, ctx, 'make'), d: this.pathOf(c, ctx, 'model'), t: this.pathOf(c, ctx, 'vehicleType') };
      const s = this.fieldShell(c, null);
      const row = h('div', { class: 'a2-row' });
      const mk = (label) => { const sel = h('select', { 'aria-label': label }); return sel; };
      const ys = mk(L('Année', 'Year')), ms = mk(L('Marque', 'Make')), ds = mk(L('Modèle', 'Model'));
      const status = h('div', { class: 'hint' });
      const fill = (sel, items, ph, val) => { sel.innerHTML = ''; sel.append(h('option', { value: '' }, ph)); for (const x of items) sel.append(h('option', { value: x }, esc(x))); sel.value = val != null ? String(val) : ''; };
      let valid = false;
      const recompute = (silent) => {
        const y = this.get(P.y), m = this.get(P.m), d = this.get(P.d);
        fill(ys, this.services.years(), L('Année', 'Year'), y);
        const r1 = this.services.lookupYMM({ year: y || undefined, make: m || undefined, model: d || undefined });
        fill(ms, r1.makes, y ? L('Marque', 'Make') : L('Marque (choisissez l’année)', 'Make (pick a year)'), m && r1.makes.includes(m) ? m : '');
        fill(ds, r1.models || [], L('Modèle', 'Model'), d && r1.models && r1.models.includes(d) ? d : '');
        ds.disabled = !m; ms.disabled = !y;
        valid = r1.valid;
        if (valid) {
          const t = this.services.identifyVehicleType(m, d, y);
          if (this.get(P.t) !== t.vehicle_type) this.set(P.t, t.vehicle_type, silent);
          status.innerHTML = `<span class="ok-msg">${ICON.ok} ${L('Valide', 'Valid')} : ${esc(y)} ${esc(m)} ${esc(d)}</span> · type <span class="badge lookup">${esc(labelFor('vehicle_type', t.vehicle_type))}</span> <span style="color:var(--ink-3)">identified by quoting.identify_vehicle_type</span>`;
        } else if (y && m && d) status.innerHTML = FR ? `<span class="err">${esc(m)} n’offre pas de ${esc(d)} en ${esc(y)}</span>` : `<span class="err">${esc(d)} is not offered by ${esc(m)} for ${esc(y)}</span>`;
        else status.textContent = L('Validé en direct dans le catalogue de tarification.', 'Validated live against the rating catalog (quoting.lookup_make_model_year).');
      };
      ys.onchange = () => { this.set(P.y, ys.value ? Number(ys.value) : null); recompute(); s.showError(null); };
      ms.onchange = () => { this.set(P.m, ms.value || null); this.set(P.d, null); recompute(); s.showError(null); };
      ds.onchange = () => { this.set(P.d, ds.value || null); recompute(); s.showError(null); };
      row.append(ys, ms, ds);
      recompute(true);
      s.fld.append(row, status, s.prov, s.err);
      this.bind({ path: P.y, checks: c['@checks'], fld: s.fld, refresh: () => recompute(true), showError: s.showError, setProv: s.setProv, localCheck: (call) => (call === 'ymmValid' ? valid || (!c.required && !this.get(P.y) && !this.get(P.m) && !this.get(P.d)) : undefined), isVisible: () => !s.fld.hidden });
      for (const p of [P.m, P.d, P.t]) this.bind({ path: p, refresh: () => recompute(true) });
      return s.fld;
    }
    rVinField(c, ctx) {
      const path = this.pathOf(c, ctx);
      const s = this.fieldShell(c, null);
      const wrap = h('div', { class: 'input-wrap' });
      const inp = h('input', { type: 'text', maxlength: 17, placeholder: L('17 caractères', '17 characters'), 'aria-label': c.label || 'VIN', style: 'font-family:var(--mono);text-transform:uppercase;letter-spacing:.04em' });
      const st = h('div', { class: 'hint' });
      const decode = () => {
        const v = (this.get(path) || '').toUpperCase();
        if (!v) { st.textContent = ''; return; }
        if (v.length < 17) { st.textContent = L(`Encore ${17 - v.length} caractères`, `${17 - v.length} characters to go`); return; }
        const r = this.services.decodeVin(v);
        st.innerHTML = r.valid_checksum ? `<span class="ok-msg">${ICON.ok} ${L('Chiffre de contrôle valide', 'Check digit OK')} · ${esc(r.year)} ${esc(r.make)} ${esc(r.model)}</span>` : `<span class="err">${esc(L('Le chiffre de contrôle ne correspond pas : vérifiez le NIV', 'Check digit does not match: please re-check the VIN'))}</span>`;
      };
      const refresh = () => { inp.value = this.get(path) || ''; decode(); };
      inp.addEventListener('input', () => { this.set(path, inp.value.toUpperCase()); decode(); s.showError(null); });
      wrap.append(inp);
      if (c.fieldActions) this.attachFieldActions(c, wrap, path, refresh);
      refresh();
      s.fld.append(wrap, st, s.prov, s.err);
      this.bind({ path, checks: c['@checks'] || [rule('vinCheckDigit', path, null, 'This VIN is not valid (check digit)')], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => !s.fld.hidden });
      return s.fld;
    }
    rAddressLookup(c) {
      const s = this.fieldShell(c, null);
      const q = h('div', { class: 'input-wrap' });
      const inp = h('input', { type: 'text', placeholder: L('Commencez à écrire : 125 des Érables, ou un code postal', 'Start typing: 125 des Érables, or a postal code'), 'aria-label': L('Recherche d’adresse', 'Address search') });
      const go = h('button', { class: 'btn small', type: 'button' }, `${ICON.search} ${L('Chercher', 'Find')}`);
      q.append(inp, go);
      const list = h('div', { class: 'radios', style: 'margin-top:4px' });
      const detail = h('div', { class: 'a2-row', style: 'margin-top:4px' });
      const sub = (key, label, w) => {
        const def = FIELD[key]; const comp = { id: 'addr_' + key, label, value: { '@path': '/quote/' + key }, required: true, '@checks': checksFor(def, true, '/quote/' + key), textFieldType: key === 'postal_code' ? 'postal_code' : 'shortText' };
        if (w === 'select') return this.rChoicePicker(Object.assign(comp, { component: 'ChoicePicker', variant: 'select', options: fenum(def).map((e) => ({ value: e[0], label: e[1] })) }), {});
        return this.rTextField(comp, {});
      };
      const search = () => {
        const r = this.services.identifyAddress(inp.value || '125 des Érables');
        list.innerHTML = '';
        r.candidates.forEach((cd) => {
          const b = h('button', { type: 'button', class: 'radio-card', role: 'radio', 'aria-checked': 'false' }, `<span class="rdot"></span><span><strong>${esc(cd.address_line1)}</strong>, ${esc(cd.city)}, ${esc(cd.province_code)} ${esc(cd.postal_code)}</span><span class="conf ${cd.confidence > 0.85 ? 'hi' : cd.confidence > 0.65 ? 'mid' : 'lo'}" style="margin-left:auto">${Math.round(cd.confidence * 100)}%</span>`);
          b.onclick = () => {
            [...list.children].forEach((x) => x.setAttribute('aria-checked', 'false')); b.setAttribute('aria-checked', 'true');
            for (const k of ['address_line1', 'city', 'province_code', 'postal_code']) this.set('/quote/' + k, cd[k]);
            this.mark('/quote/address_line1', { source: 'lookup', note: 'identifyAddress (quoting.identify_address)' });
            this.syncFrom('/quote');
          };
          list.append(b);
        });
      };
      go.onclick = search;
      inp.addEventListener('keydown', (e) => { if (e.key === 'Enter') { e.preventDefault(); search(); } });
      detail.append(sub('address_line1', L('Adresse', 'Street address')), sub('city', L('Ville', 'City')), sub('province_code', L('Province', 'Province'), 'select'), sub('postal_code', L('Code postal', 'Postal code')));
      s.fld.append(q, list, detail, s.prov, s.err);
      return s.fld;
    }
    rRepeater(c) {
      const wrap = h('div', { class: 'fld' });
      const lbl = h('div', { class: 'lbl' }, `${esc(c.label || '')}${c.deterministic ? ` <span class="badge det">${ICON.lock} confirm twice</span>` : ''}`);
      wrap.append(lbl);
      if (c.hint) wrap.append(h('div', { class: 'hint' }, md(c.hint)));
      const list = h('div', { class: 'repeater' });
      const addBtn = h('button', { class: 'btn small', type: 'button', style: 'width:fit-content' }, `${ICON.plus} ${esc(c.addLabel)}`);
      let count = c.count;
      const existing = ptrGet(this.data, c.collection);
      if (Array.isArray(existing)) count = Math.max(count, existing.length - c.start);
      const draw = () => {
        this.bindings = this.bindings.filter((b) => !b.rep || b.rep !== c.id);
        list.innerHTML = '';
        for (let k = 0; k < count; k++) {
          const i = c.start + k;
          const label = c.itemLabel.replace('{n}', k + 1).replace('{i}', i);
          const before = this.bindings.length;
          if (c.layout === 'list') {
            const tpl = this.components.get(c.template[0]);
            const el = this.renderComp(Object.assign({}, tpl, { label }), { scope: c.collection + '/' + i });
            el.classList.add('rep-line');
            list.append(el);
          } else {
            const item = h('div', { class: 'rep-item' });
            const head = h('div', { class: 'rep-head' }, `<span>${esc(label)}</span><span class="spacer"></span>`);
            if (count > c.min) { const rm = h('button', { class: 'btn ghost small', type: 'button', 'aria-label': L('Retirer ', 'Remove ') + label }, L('Retirer', 'Remove')); rm.onclick = () => { const arr = ptrGet(this.data, c.collection); if (Array.isArray(arr)) arr.splice(i, 1); count--; draw(); }; head.append(rm); }
            const grid = h('div', { class: 'rep-grid' });
            for (const tid of c.template) grid.append(this.renderComp(this.components.get(tid), { scope: c.collection + '/' + i }));
            item.append(head, grid); list.append(item);
          }
          for (let j = before; j < this.bindings.length; j++) this.bindings[j].rep = c.id;
        }
        addBtn.hidden = count >= c.max;
        this.evalVisibility();
      };
      addBtn.onclick = () => { count++; draw(); };
      draw();
      wrap.append(list, addBtn);
      if (c.visibleIf) this.visRules.push({ el: wrap, rule: c.visibleIf });
      return wrap;
    }
    rAssignmentMatrix(c) {
      const path = c.value['@path'];
      const s = this.fieldShell(c, null);
      const tbl = h('table', { class: 'matrix' });
      const head = h('tr'); head.append(h('th', null, L('Conducteur', 'Driver')));
      c.cols.forEach((col) => head.append(h('th', null, esc(col.label))));
      tbl.append(head);
      const get = () => this.get(path) || [];
      const selects = [];
      c.rows.forEach((row, ri) => {
        const tr = h('tr'); tr.append(h('td', null, `<strong>${esc(row.label)}</strong>`));
        c.cols.forEach((col, ci) => {
          const td = h('td'); const sel = h('select', { 'aria-label': `${row.label}, ${col.label}` });
          for (const ch of c.choices) sel.append(h('option', { value: ch.value }, esc(ch.label)));
          sel.onchange = () => {
            const arr = get().filter((a) => !(a.driver_index === ri && a.vehicle_index === ci));
            if (sel.value !== 'none') arr.push({ driver_index: ri, vehicle_index: ci, role: sel.value });
            if (sel.value === 'principal') arr.forEach((a) => { if (a.vehicle_index === ci && a.driver_index !== ri && a.role === 'principal') { a.role = 'occasional'; } });
            this.set(path, arr); refresh(); s.showError(null);
          };
          selects.push({ sel, ri, ci }); td.append(sel); tr.append(td);
        });
        tbl.append(tr);
      });
      const refresh = () => { const arr = get(); for (const x of selects) { const a = arr.find((q) => q.driver_index === x.ri && q.vehicle_index === x.ci); x.sel.value = a ? a.role : 'none'; } };
      refresh();
      s.fld.append(tbl, h('div', { class: 'hint' }, L('Chaque véhicule a besoin d’un seul conducteur principal.', 'Each vehicle needs exactly one principal driver.')), s.prov, s.err);
      const ncols = c.cols.length;
      const okFn = () => { const arr = get(); for (let ci = 0; ci < ncols; ci++) if (arr.filter((a) => a.vehicle_index === ci && a.role === 'principal').length !== 1) return false; return true; };
      this.bind({ path, checks: [rule('onePrincipalPerVehicle', path, null, 'Each vehicle needs exactly one principal driver')], localCheck: (call) => (call === 'onePrincipalPerVehicle' ? okFn() : undefined), fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => true });
      return s.fld;
    }
    rOfferCards(c) {
      const path = c.value['@path'];
      const s = this.fieldShell(c, null);
      const grid = h('div', { class: 'offers', role: 'radiogroup', 'aria-label': c.label });
      const cards = c.offers.map((o) => {
        const b = h('button', { type: 'button', class: 'offer', role: 'radio', 'data-v': o.offer_type }, `${c.recommended === o.offer_type ? `<span class="o-tag">${L('Recommandé', 'Recommended')}</span>` : ''}<span class="o-name">${esc(labelFor('offer_type', o.offer_type))}</span><span class="o-price">${fmtMoney(o.monthly, true)}<small>${L('/mois', '/month')}</small></span><span class="a2-text-caption">${fmtMoney(o.annual)} ${L('par année', 'per year')} · ${L('franchise', 'deductible')} ${fmtMoney(o.deductible)}</span><ul>${(o.coverages || []).map((x) => `<li>${esc(x)}</li>`).join('')}</ul>`);
        b.onclick = () => { this.set(path, o.offer_type); refresh(); s.showError(null); };
        grid.append(b); return b;
      });
      const refresh = () => { const v = this.get(path); cards.forEach((b) => b.setAttribute('aria-checked', String(b.dataset.v === v))); };
      refresh();
      s.fld.append(grid, s.prov, s.err);
      this.bind({ path, checks: c['@checks'] || [rule('required', path, null, L('Choisissez une offre', 'Pick an offer'))], fld: s.fld, refresh, showError: s.showError, setProv: s.setProv, isVisible: () => true });
      return s.fld;
    }
    rSummary(c) {
      const s = this.fieldShell(c, null);
      const dl = h('dl', { class: 'summary' });
      for (const it of c.items) { dl.append(h('dt', null, esc(it.label))); dl.append(h('dd', null, `${esc(it.value)}${it.source ? ` <span class="badge ${esc(it.source)}">${esc(it.source)}</span>` : ''}`)); }
      s.fld.append(dl);
      return s.fld;
    }
    /* Insurance catalog: DocumentDrop. One tile per document (licence, registration, current policy).
       Each photo leaves the renderer as callAgentFunction; the BFF sends it to the document agent
       (gpt-5.6-luna) and answers with agentFunctionResponse. The customer reviews and can edit every
       extracted value before submit; values are written with provenance "document". */
    rDocumentDrop(c) {
      const s = this.fieldShell(c, null);
      const grid = h('div', { class: 'docs' });
      const found = h('div', { class: 'found', hidden: true }, `<div class="a2-text-h3" role="heading">${esc(L('Ce que j’ai trouvé', 'What I found'))}</div>`);
      const list = h('div', { class: 'xlist' }); found.append(list);
      const labelOf = (doc, k, target) => (doc.labels && doc.labels[k]) || flabel(FIELD[k]) || flabel(FIELD[keyOf(target)]) || k;
      const addRows = (doc, fields) => {
        found.hidden = false;
        const g = h('div', { class: 'xgroup' }, `<h5>${esc(doc.title)}</h5>`);
        let i = 0;
        for (const [k, v] of Object.entries(fields)) {
          const target = (doc.fills || {})[k]; if (!target) continue;
          const def = FIELD[keyOf(target)];
          const id = 'x-' + this.id + '-' + target.replace(/\W/g, '_');
          const row = h('div', { class: 'xrow', style: `animation-delay:${i++ * 70}ms` }, `<label for="${id}">${esc(labelOf(doc, k, target))}</label>`);
          let ctl;
          const en = fenum(def);
          if (en) { ctl = h('select', { id }); for (const e of en) ctl.append(h('option', { value: e[0] }, esc(e[1]))); }
          else ctl = h('input', { id, type: def && def.type === 'date' ? 'date' : def && def.type === 'integer' ? 'number' : 'text' });
          const refresh = () => { const x = this.get(target); ctl.value = x == null ? '' : String(x); };
          ctl.addEventListener('change', () => { let x = ctl.value; if (def && def.type === 'integer') x = x === '' ? null : Number(x); this.set(target, x); this.mark(target, { source: 'document', note: L('modifié par vous', 'edited by you') }); });
          const conf = v && v.confidence != null ? v.confidence : 1;
          row.append(ctl, h('span', { class: 'conf' + (conf < 0.9 ? ' mid' : '') }, Math.round(conf * 100) + ' %'));
          g.append(row);
          refresh();
          this.bind({ path: target, refresh, fld: row, showError: () => {}, isVisible: () => true });
        }
        list.append(g);
      };
      const markDone = (tile, n) => { tile.classList.remove('reading', 'missing'); tile.classList.add('done'); tile.querySelector('.ok span').textContent = n ? L(`${n} informations lues`, `${n} fields read`) : L('Lu', 'Read'); };
      const read = async (doc, tile) => {
        if (tile.classList.contains('done') || tile.classList.contains('reading')) return;
        tile.classList.add('reading');
        const r = this.host.onFieldAction ? await this.host.onFieldAction(this, { kind: 'document', purpose: doc.purpose }) : null;
        tile.classList.remove('reading');
        if (!r || !r.fields) { tile.querySelector('.ok span').textContent = L('Illisible, réessayez', 'Unreadable, try again'); return; }
        for (const [k, v] of Object.entries(r.fields)) {
          const target = (doc.fills || {})[k]; if (!target) continue;
          this.set(target, v.value); this.mark(target, { source: 'document', note: Math.round(v.confidence * 100) + ' %' });
        }
        markDone(tile, Object.keys(r.fields).filter((k) => (doc.fills || {})[k]).length);
        addRows(doc, r.fields);
        const read = new Set(this.get('/_documents/read') || []); read.add(doc.purpose); this.set('/_documents/read', [...read], true);
        if (this.host.onDocumentRead) this.host.onDocumentRead(this, doc.purpose);
        if (this.bodyEl) this.bodyEl.scrollTop = this.bodyEl.scrollHeight;
      };
      const tiles = [];
      for (const doc of c.documents || []) {
        const tile = h('div', { class: 'doc', 'data-doc': doc.purpose }, `<span class="dic">${ICON[doc.icon] || ICON.upload}</span><b>${esc(doc.title)}</b><small>${esc(doc.description || '')}</small><div class="dbtns"><button type="button" class="up">${ICON.camera} ${esc(L('Téléverser une photo', 'Upload a photo'))}</button><button type="button" class="skip">${esc(L('Je ne l’ai pas', 'I don’t have it'))}</button><span class="ok">${ICON.ok}<span>${esc(L('Lu', 'Read'))}</span></span></div><div class="thumb"><div class="paper"><div class="scan"></div></div><span>${esc(L('Lecture du document…', 'Reading the document…'))}</span></div>`);
        tile.querySelector('.up').onclick = () => read(doc, tile);
        tile.querySelector('.skip').onclick = () => { tile.classList.add('missing'); tile.querySelector('.ok span').textContent = L('Non fourni', 'Not provided'); };
        grid.append(tile); tiles.push({ doc, tile });
      }
      /* Reopened in edit mode: documents already read show their values from the data model. */
      const already = new Set([...(c.read || []), ...(this.get('/_documents/read') || [])]);
      for (const { doc, tile } of tiles) if (already.has(doc.purpose)) { const f = {}; for (const [k, t] of Object.entries(doc.fills || {})) if (this.get(t) != null) f[k] = { value: this.get(t), confidence: 1 }; markDone(tile, Object.keys(f).length); addRows(doc, f); }
      this.readAll = () => tiles.forEach(({ doc, tile }) => { if (!tile.classList.contains('missing')) read(doc, tile); });
      s.fld.append(grid, found, s.prov, s.err);
      return s.fld;
    }
    /* Insurance catalog: AssignmentBoard. Drag each person from the pool into a vehicle card: one
       "principal" slot (exactly one person) and an "occasional" slot (zero or more). Tap a person then
       a slot, or use Enter/Space, as the non-drag alternative. Writes the catalog shape
       [{driver_index, vehicle_index, role}] to /household/driver_vehicle_assignments. */
    rAssignmentBoard(c) {
      const path = c.value['@path'];
      const s = this.fieldShell(c, null);
      const D = c.drivers || [], V = c.vehicles || [];
      let A = [];
      let selected = null;
      const load = () => {
        A = V.map(() => ({ p: null, s: [] }));
        for (const a of this.get(path) || []) {
          const vi = V.findIndex((v) => v.index === a.vehicle_index), di = D.findIndex((d) => d.index === a.driver_index);
          if (vi < 0 || di < 0) continue;
          if (a.role === 'principal') A[vi].p = di; else if (a.role === 'occasional' && !A[vi].s.includes(di)) A[vi].s.push(di);
        }
      };
      const save = () => {
        const arr = [];
        A.forEach((a, vi) => { if (a.p != null) arr.push({ driver_index: D[a.p].index, vehicle_index: V[vi].index, role: 'principal' }); a.s.forEach((di) => arr.push({ driver_index: D[di].index, vehicle_index: V[vi].index, role: 'occasional' })); });
        this.set(path, arr);
      };
      const ini = (d) => String(d.label || '?').split(/\s+/).map((x) => x[0]).join('').slice(0, 2).toUpperCase();
      const first = (d) => String(d.label || '').split(' ')[0];
      const personHtml = (i, small) => { const d = D[i]; return small
        ? `<span class="pchip" data-p="${i}" title="${esc(L('Glissez pour déplacer', 'Drag to move'))}"><span class="av c${i % 5}">${esc(ini(d))}</span>${esc(first(d))}<button type="button" data-rm aria-label="${esc(L('Retirer', 'Remove'))} ${esc(first(d))}">×</button></span>`
        : `<div class="person" data-p="${i}" role="button" tabindex="0" aria-pressed="false"><span class="av c${i % 5}">${esc(ini(d))}</span><span><b>${esc(d.label)}</b><small>${esc(d.sub || '')}</small><span class="where"></span></span></div>`; };
      const board = h('div', { class: 'board' });
      board.innerHTML = `<div class="pool"><div class="pool-h">${esc(L('Conducteurs', 'Drivers'))}<span>${esc(L('Saisissez une personne et déposez-la dans un emplacement', 'Grab a person and drop them in a slot'))}</span></div><div class="pool-chips">${D.map((_, i) => personHtml(i)).join('')}</div></div>`
        + `<div class="vgrid">${V.map((v, vi) => `<div class="vcard" data-v="${vi}"><div class="vh">${ICON.car}<span>${esc(v.label)}<small>${esc(v.sub || '')}</small></span></div>`
        + `<div class="slot principal" data-v="${vi}" data-kind="p"><div class="slot-l">${esc(L('Conducteur principal', 'Principal driver'))}<em>${esc(L('1 personne', '1 person'))}</em></div><div class="slot-items"></div><span class="ph">${esc(L('Déposez une personne ici', 'Drop a person here'))}</span></div>`
        + `<div class="slot" data-v="${vi}" data-kind="s"><div class="slot-l">${esc(L('Conducteurs occasionnels', 'Occasional drivers'))}<em>${esc(L('0 ou plus', '0 or more'))}</em></div><div class="slot-items"></div><span class="ph">${esc(L('Déposez ici les autres conducteurs', 'Drop other drivers here'))}</span></div></div>`).join('')}</div>`
        + `<div class="board-msg" aria-live="polite"></div><div class="board-tip">${esc(L('Sans glisser : touchez une personne, puis un emplacement.', 'Without dragging: tap a person, then a slot.'))}</div>`;
      const msg = (t) => { board.querySelector('.board-msg').textContent = t || ''; };
      const vName = (vi) => V[vi].short || V[vi].label;
      const paint = (popKey) => {
        board.querySelectorAll('.slot').forEach((sl) => {
          const v = Number(sl.dataset.v), kind = sl.dataset.kind; const items = kind === 'p' ? (A[v].p != null ? [A[v].p] : []) : A[v].s;
          sl.querySelector('.slot-items').innerHTML = items.map((p) => personHtml(p, true)).join('');
          sl.classList.toggle('filled', items.length > 0); sl.classList.remove('missing');
          if (popKey !== `${v}${kind}`) sl.querySelectorAll('.pchip').forEach((x) => { x.style.animation = 'none'; });
        });
        board.querySelectorAll('.person').forEach((el) => {
          const p = Number(el.dataset.p); const w = [];
          A.forEach((a, v) => { if (a.p === p) w.push(L('Principal', 'Principal') + ' · ' + vName(v)); if (a.s.includes(p)) w.push(L('Occasionnel', 'Occasional') + ' · ' + vName(v)); });
          el.querySelector('.where').textContent = w.join(' — ');
        });
      };
      const place = (p, v, kind) => {
        let note = '';
        A[v].s = A[v].s.filter((x) => x !== p);
        if (kind === 'p') {
          A.forEach((a, w) => { if (a.p === p && w !== v) { a.p = null; note = L(`${first(D[p])} ne peut être conducteur principal que d’un véhicule : retiré(e) de ${vName(w)}.`, `${first(D[p])} can be principal driver of one vehicle only: removed from ${vName(w)}.`); } });
          if (A[v].p != null && A[v].p !== p) { const prev = A[v].p; if (!A[v].s.includes(prev)) A[v].s.push(prev); note = note || L(`${first(D[prev])} devient conducteur occasionnel de ${vName(v)}.`, `${first(D[prev])} becomes an occasional driver of ${vName(v)}.`); }
          A[v].p = p;
        } else { if (A[v].p === p) A[v].p = null; A[v].s.push(p); }
        msg(note); save(); paint(`${v}${kind}`); s.showError(null);
        const sl = board.querySelector(`.slot[data-v="${v}"][data-kind="${kind}"]`);
        if (sl && sl.animate && !reduceMotion()) sl.animate([{ transform: 'scale(1.05)' }, { transform: 'scale(.98)' }, { transform: 'none' }], { duration: 380, easing: 'cubic-bezier(.34,1.56,.64,1)' });
      };
      const removeFrom = (p, v, kind) => { if (kind === 'p') { if (A[v].p === p) A[v].p = null; } else A[v].s = A[v].s.filter((x) => x !== p); save(); paint(); };
      const slotAt = (x, y) => { const el = document.elementFromPoint(x, y); const sl = el && el.closest('.slot'); return sl && board.contains(sl) ? sl : null; };
      const scrollerOf = (el) => { let e = el.parentElement; while (e && e !== document.body) { const o = getComputedStyle(e).overflowY; if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight) return e; e = e.parentElement; } return null; };
      let drag = null, raf = 0;
      const hover = () => { const sl = slotAt(drag.x, drag.y); if (sl !== drag.over) { if (drag.over) drag.over.classList.remove('over'); if (sl) sl.classList.add('over'); drag.over = sl; } };
      /* Auto-scroll while a person is held near the top or bottom edge (phones). */
      const tick = () => {
        if (!drag || !drag.ghost) { raf = 0; return; }
        const sc = drag.scroller;
        if (sc) { const r = sc.getBoundingClientRect(); const edge = 64; if (drag.y > r.bottom - edge) sc.scrollTop += Math.ceil((drag.y - (r.bottom - edge)) / 5); else if (drag.y < r.top + edge) sc.scrollTop -= Math.ceil((r.top + edge - drag.y) / 5); }
        hover(); raf = requestAnimationFrame(tick);
      };
      const onMove = (e) => {
        if (!drag) return;
        drag.x = e.clientX; drag.y = e.clientY;
        if (!drag.ghost) {
          if (Math.hypot(e.clientX - drag.x0, e.clientY - drag.y0) < 6) return;
          const r = drag.src.getBoundingClientRect(); drag.dx = drag.x0 - r.left; drag.dy = drag.y0 - r.top;
          drag.ghost = drag.src.cloneNode(true); drag.ghost.classList.add('dragghost'); drag.ghost.style.width = r.width + 'px'; document.body.append(drag.ghost);
          drag.src.classList.add('dragging'); board.querySelectorAll('.slot').forEach((x) => x.classList.add('can'));
          drag.scroller = scrollerOf(board);
        }
        drag.ghost.style.left = (e.clientX - drag.dx) + 'px'; drag.ghost.style.top = (e.clientY - drag.dy) + 'px';
        hover(); if (!raf) raf = requestAnimationFrame(tick);
      };
      const onUp = async (e) => {
        if (!drag) return; const d = drag; drag = null; if (raf) { cancelAnimationFrame(raf); raf = 0; }
        d.src.removeEventListener('pointermove', onMove); d.src.removeEventListener('pointerup', onUp); d.src.removeEventListener('pointercancel', onUp);
        board.querySelectorAll('.slot').forEach((x) => x.classList.remove('can', 'over'));
        if (!d.ghost) { if (!d.from) { selected = selected === d.p ? null : d.p; board.querySelectorAll('.person').forEach((el) => el.setAttribute('aria-pressed', String(Number(el.dataset.p) === selected))); if (selected != null) msg(L(`${first(D[selected])} sélectionné(e) : touchez un emplacement.`, `${first(D[selected])} selected: tap a slot.`)); } return; }
        const sl = slotAt(e.clientX, e.clientY);
        const to = sl ? sl.querySelector('.slot-items').getBoundingClientRect() : d.src.getBoundingClientRect();
        if (!reduceMotion()) await d.ghost.animate([{ left: d.ghost.style.left, top: d.ghost.style.top }, { left: to.left + 'px', top: to.top + 'px', transform: sl ? 'scale(.8)' : 'none', opacity: sl ? 0.6 : 1 }], { duration: sl ? 200 : 260, easing: sl ? 'cubic-bezier(.2,.8,.2,1)' : 'cubic-bezier(.34,1.56,.64,1)', fill: 'forwards' }).finished;
        d.ghost.remove(); d.src.classList.remove('dragging');
        if (d.from) removeFrom(d.p, d.from.v, d.from.kind);
        if (sl) place(d.p, Number(sl.dataset.v), sl.dataset.kind);
      };
      board.addEventListener('pointerdown', (e) => {
        if (e.target.closest('[data-rm]') || e.button > 0) return;
        const src = e.target.closest('.person, .pchip'); if (!src) return;
        const slot = src.closest('.slot');
        drag = { p: Number(src.dataset.p), src, from: slot ? { v: Number(slot.dataset.v), kind: slot.dataset.kind } : null, x0: e.clientX, y0: e.clientY, x: e.clientX, y: e.clientY, ghost: null, over: null };
        try { src.setPointerCapture(e.pointerId); } catch (err) { /* synthetic events */ }
        src.addEventListener('pointermove', onMove); src.addEventListener('pointerup', onUp); src.addEventListener('pointercancel', onUp);
        e.preventDefault();
      });
      board.addEventListener('click', (e) => {
        const rm = e.target.closest('[data-rm]'); if (rm) { const ch = rm.closest('.pchip'); const sl = ch.closest('.slot'); removeFrom(Number(ch.dataset.p), Number(sl.dataset.v), sl.dataset.kind); return; }
        const sl = e.target.closest('.slot'); if (sl && selected != null && !e.target.closest('.pchip')) { place(selected, Number(sl.dataset.v), sl.dataset.kind); selected = null; board.querySelectorAll('.person').forEach((el) => el.setAttribute('aria-pressed', 'false')); }
      });
      board.addEventListener('keydown', (e) => { const el = e.target.closest('.person'); if (el && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); selected = Number(el.dataset.p); board.querySelectorAll('.person').forEach((x) => x.setAttribute('aria-pressed', String(x === el))); msg(L(`${first(D[selected])} sélectionné(e) : touchez un emplacement.`, `${first(D[selected])} selected: tap a slot.`)); } });
      this.placeOnBoard = (p, v, kind) => place(p, v, kind);
      const refresh = () => { load(); paint(); };
      refresh();
      s.fld.append(board, s.prov, s.err);
      const okFn = () => V.every((_, vi) => A[vi] && A[vi].p != null);
      this.bind({ path, checks: c['@checks'] || [rule('onePrincipalPerVehicle', path, null, L('Chaque véhicule a besoin d’un conducteur principal', 'Each vehicle needs exactly one principal driver'))], localCheck: (call) => (call === 'onePrincipalPerVehicle' ? okFn() : undefined), fld: s.fld, refresh, showError: (m) => { s.showError(m); if (m) A.forEach((a, vi) => { if (a.p == null) { const sl = board.querySelector(`.slot[data-v="${vi}"][data-kind="p"]`); if (sl) sl.classList.add('missing'); } }); }, setProv: s.setProv, isVisible: () => true });
      return s.fld;
    }
    rFileDrop(c) {
      const s = this.fieldShell(c, null);
      const box = h('div', { class: 'drop' }, `${ICON.upload}<div><strong>${L('Déposez une photo ou un PDF', 'Drop a photo or PDF')}</strong>${L(', ou', ', or')}</div>`);
      const btn = h('button', { class: 'btn small', type: 'button' }, L('Choisir un fichier (simulé)', 'Choose file (simulated)'));
      const out = h('div', { class: 'extracted' });
      btn.onclick = async () => {
        box.classList.add('busy'); btn.disabled = true; btn.innerHTML = `<span class="spin"></span> ${L('L’agent de documents lit…', 'Document agent is reading…')}`;
        const r = this.host.onFieldAction ? await this.host.onFieldAction(this, { kind: 'document', purpose: c.purpose }) : null;
        box.classList.remove('busy'); btn.disabled = false; btn.textContent = L('Remplacer le fichier', 'Replace file');
        if (!r || !r.fields) return;
        out.innerHTML = '';
        for (const [k, v] of Object.entries(r.fields)) {
          const conf = v.confidence; const cls = conf > 0.9 ? 'hi' : conf > 0.75 ? 'mid' : 'lo';
          out.append(h('div', null, `<span style="color:var(--ink-3)">${esc(flabel(FIELD[k]) || k)}</span> <strong>${esc(labelFor(k, v.value))}</strong>`), h('div', { class: 'conf ' + cls }, Math.round(conf * 100) + '%'));
          const target = c.fills[k];
          if (target) { this.set(target, v.value); this.mark(target, { source: 'document', note: Math.round(conf * 100) + '% confidence' }); }
        }
      };
      box.append(btn, out);
      s.fld.append(box, s.prov, s.err);
      return s.fld;
    }
  }

  /* -------------------------------------------------------------- the Stage */
  class Stage {
    /* root: element that gets .form-open; stageEl: where form cards live; anchor(): avatar element to fly from/to; layout(open): callback that moves the chat */
    constructor(opts) { Object.assign(this, opts); this.current = null; this.busy = Promise.resolve(); }
    centerOf(r) { return { x: r.left + r.width / 2, y: r.top + r.height / 2 }; }
    ball(x, y, size) { const b = h('div', { class: 'morph' }); Object.assign(b.style, { left: x - size / 2 + 'px', top: y - size / 2 + 'px', width: size + 'px', height: size + 'px' }); document.body.append(b); return b; }
    open(surface) { this.busy = this.busy.then(() => this._open(surface)); return this.busy; }
    close(surface) { this.busy = this.busy.then(() => this._close(surface)); return this.busy; }
    async _open(surface) {
      if (this.current && this.current !== surface) await this._close(this.current, true);
      this.current = surface;
      const card = surface.el || surface.render();
      card.classList.add('measuring');
      this.stageEl.append(card);
      const a = this.anchor();
      const aRect = a ? a.getBoundingClientRect() : { left: 20, top: 20, width: 30, height: 30 };
      if (a) { a.classList.remove('pulse', 'look-l', 'happy'); a.classList.add('look-r'); setTimeout(() => a.classList.remove('look-r'), 1300); }
      this.layout(true);
      if (reduceMotion()) { await wait(30); card.classList.remove('measuring'); card.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 160 }); return; }
      const start = this.centerOf(aRect);
      const ball = this.ball(start.x, start.y, 14);
      ball.animate([{ transform: 'scale(.2)', opacity: 0 }, { transform: 'scale(1.25)', opacity: 1 }, { transform: 'scale(1)' }], { duration: 220, easing: 'ease-out' });
      await wait(200);
      const layoutMs = this.layoutMs || 450;
      await wait(Math.max(0, layoutMs - 260));
      const target = card.getBoundingClientRect();
      const end = this.centerOf(target);
      const s = 14;
      const mid = { x: (start.x + end.x) / 2, y: Math.min(start.y, end.y) - 70 };
      await ball.animate([
        { left: start.x - s / 2 + 'px', top: start.y - s / 2 + 'px' },
        { left: mid.x - s / 2 + 'px', top: mid.y - s / 2 + 'px', offset: 0.5 },
        { left: end.x - s / 2 + 'px', top: end.y - s / 2 + 'px' }
      ], { duration: 520, easing: 'cubic-bezier(.45,.05,.35,1)', fill: 'forwards' }).finished;
      ball.classList.add('card');
      await ball.animate([
        { left: end.x - s / 2 + 'px', top: end.y - s / 2 + 'px', width: s + 'px', height: s + 'px', borderRadius: '50%', backgroundColor: cssVar('--accent') },
        { left: end.x - 40 + 'px', top: end.y - 40 + 'px', width: '80px', height: '80px', borderRadius: '40px', offset: 0.25 },
        { left: target.left + 'px', top: target.top + 'px', width: target.width + 'px', height: target.height + 'px', borderRadius: getComputedStyle(card).borderTopLeftRadius || '14px', backgroundColor: cssVar('--surface') }
      ], { duration: 420, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' }).finished;
      card.classList.remove('measuring');
      surface.bodyEl.querySelector('.a2-col').classList.add('reveal');
      [...surface.bodyEl.querySelector('.a2-col').children].forEach((el, i) => { el.style.animationDelay = Math.min(i * 45, 400) + 'ms'; });
      ball.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, fill: 'forwards' }).finished.then(() => ball.remove());
      const f = card.querySelector('input, select, .chip, button.radio-card');
      if (f && this.autofocus !== false) setTimeout(() => f.focus({ preventScroll: true }), 120);
    }
    async _close(surface, quick) {
      const card = surface.el;
      if (!card || !card.isConnected) { this.current = null; this.layout(false); return; }
      if (reduceMotion()) { card.remove(); this.current = null; this.layout(false); return; }
      const r = card.getBoundingClientRect();
      const rad = getComputedStyle(card).borderTopLeftRadius || '14px';
      const ball = h('div', { class: 'morph card' });
      Object.assign(ball.style, { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', borderRadius: rad });
      document.body.append(ball);
      card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 120, fill: 'forwards' });
      await wait(100);
      card.remove();
      if (this.onCloseStart) this.onCloseStart(quick);
      const c = this.centerOf(r);
      const s = 14;
      await ball.animate([
        { left: r.left + 'px', top: r.top + 'px', width: r.width + 'px', height: r.height + 'px', borderRadius: rad, backgroundColor: cssVar('--surface') },
        { left: c.x - 40 + 'px', top: c.y - 40 + 'px', width: '80px', height: '80px', borderRadius: '40px', offset: 0.7 },
        { left: c.x - s / 2 + 'px', top: c.y - s / 2 + 'px', width: s + 'px', height: s + 'px', borderRadius: '50%', backgroundColor: cssVar('--accent') }
      ], { duration: quick ? 260 : 380, easing: 'cubic-bezier(.5,0,.75,0)', fill: 'forwards' }).finished;
      ball.classList.remove('card');
      const a = this.anchor();
      const ar = a ? a.getBoundingClientRect() : { left: 20, top: 20, width: 30, height: 30 };
      const e = this.centerOf(ar);
      if (this.anchorShift) { const d = this.anchorShift(a); e.x -= d.x || 0; e.y -= d.y || 0; } /* where the bot lands once the conversation has slid back */
      if (a) a.classList.add('look-l');
      const mid = { x: (c.x + e.x) / 2, y: Math.min(c.y, e.y) - 60 };
      await ball.animate([
        { left: c.x - s / 2 + 'px', top: c.y - s / 2 + 'px', transform: 'scale(1)' },
        { left: mid.x - s / 2 + 'px', top: mid.y - s / 2 + 'px', offset: 0.5 },
        { left: e.x - s / 2 + 'px', top: e.y - s / 2 + 'px', transform: 'scale(.6)' }
      ], { duration: quick ? 320 : 480, easing: 'cubic-bezier(.45,.05,.35,1)', fill: 'forwards' }).finished;
      ball.remove();
      if (a) { a.classList.remove('pulse', 'look-l'); void a.offsetWidth; a.classList.add('pulse', 'happy'); setTimeout(() => a.classList.remove('happy'), 900); }
      this.current = null;
      if (!quick) this.layout(false);
      await wait(quick ? 0 : 200);
    }
  }

  G.A2UI = { LANG, FR, L, flabel, fhelp, fenum, A2UI_VERSION, CATALOG_ID, BASIC_CATALOG_ID, provPath, ptrEsc, CAT, FIELD, compile, Surface, Stage, makeServices, makeVin, vinValid, ptrGet, ptrSet, keyOf, parentOf, resolvePath, entityPath, labelFor, fmtMoney, jsonHtml, esc, md, h, ICON, wait, reduceMotion, ageFrom, YEAR, clone };
})(window);
