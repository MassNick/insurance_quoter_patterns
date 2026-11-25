/* journey.js: the interactive quoting journey, Desjardins demo, Variation C ("Assistant plein écran").
 * Simulates, in the browser: the BFF (single writer of state, merge rules, deterministic double
 * confirmations, end-of-form rules after every commit, logging), the hosted agent (memory agent + main
 * agent, two model calls per turn), the toolbox tools, Foundry IQ and the document agent.
 * Customer UI in French (fr-CA); the developer trace stays in English.
 * No network calls. Model behaviour is scripted and rule-based.
 */
(function (G) {
  'use strict';
  const A = G.A2UI;
  const { FIELD, h, esc, md, ICON, wait, labelFor, fmtMoney, jsonHtml, flabel, fhelp } = A;
  const $ = (s, r) => (r || document).querySelector(s);
  const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const now = () => performance.now();
  const rnd = (a, b) => a + Math.round(Math.random() * (b - a));
  const reduce = () => matchMedia('(prefers-reduced-motion: reduce)').matches;
  const listFr = (a) => (a.length <= 1 ? a[0] || '' : a.slice(0, -1).join(', ') + ' et ' + a[a.length - 1]);
  const STATIC_PREFIX = 4155; /* measured by skill/scripts/check_artifacts.py, see prompts/token-budget.md */
  const YEAR = new Date().getFullYear();

  /* ---------- phone preview: the same page runs inside a phone frame (srcdoc iframe) ---------- */
  const EMBED = document.documentElement.dataset.embed === 'phone';
  const SRC_HTML = EMBED ? '' : '<!doctype html>\n' + document.documentElement.outerHTML; /* captured before the page changes */
  if (EMBED) document.documentElement.classList.add('embed');
  const isPhone = () => matchMedia('(max-width: 600px)').matches;

  /* ------------------------------------------------------------ session state */
  let S;
  let T0 = now();
  function fresh() {
    S = {
      sessionId: 's_' + Math.random().toString(36).slice(2, 8),
      quoteNo: Math.random().toString(36).slice(2, 10).toUpperCase(),
      turn: 0, phase: 'intent', topic: 'greet',
      state: { quote: {}, vehicles: [], drivers: [], underwriting: {}, household: {} },
      signals: [], quotes: [], prefs: { answer_mode: null }, asked: {}, acknowledged: new Set(),
      openSurface: null, openerAvatar: null, historyTokens: 0, busy: false, mode: 'chat',
      ended: false, sent: false, ruleAsk: null, stopHit: null, assignDone: false, docsRead: [], usageFor: 0, countHint: 0
    };
  }
  fresh();
  const env = (path) => A.ptrGet(S.state, path);
  const val = (path) => { const e = env(path); return e && typeof e === 'object' && 'value' in e ? e.value : undefined; };
  const has = (path) => { const v = val(path); return v !== undefined && v !== null && v !== ''; };
  const VP = (i, k) => `/vehicles/${i}/${k}`;
  const DP = (i, k) => `/drivers/${i}/${k}`;
  const nVeh = () => S.state.vehicles.length;
  const nDrv = () => S.state.drivers.length;
  const selfName = () => [val('/quote/first_name'), val('/quote/last_name')].filter(Boolean).join(' ');
  const dName = (i) => (i === 0 ? selfName() || 'Vous' : val(DP(i, 'driver_name')) || `Conducteur ${i + 1}`);
  const dFirst = (i) => dName(i).split(' ')[0];
  const vLabel = (i) => [val(VP(i, 'vehicle_year')), val(VP(i, 'vehicle_make')), val(VP(i, 'vehicle_model'))].filter(Boolean).join(' ') || 'Véhicule à préciser';
  const vShort = (i) => val(VP(i, 'vehicle_model')) || val(VP(i, 'vehicle_make')) || 'véhicule';
  const ageOf = (i) => { const d = i === 0 ? val('/quote/date_of_birth') : val(DP(i, 'driver_date_of_birth')); return d ? A.ageFrom(d) : null; };
  const flash = new Set();

  /* ----------------------------------------------------------------- tracing */
  const traceList = () => $('#trace-list');
  function typeCls(t) {
    if (t.startsWith('model') || t.startsWith('document.agent')) return 't-model';
    if (t.startsWith('tool')) return 't-tool';
    if (t.startsWith('a2ui') || t.startsWith('ui.')) return 't-a2ui';
    if (t.startsWith('state') || t.startsWith('signals') || t.startsWith('memory') || t.startsWith('session')) return 't-state';
    if (t.startsWith('confirm')) return 't-confirm';
    if (t.startsWith('rules') || t.startsWith('handoff')) return 't-rules';
    if (t.startsWith('cosmos')) return 't-db';
    if (t.startsWith('turn')) return 't-turn';
    if (t.startsWith('guardrail')) return 't-guard';
    return 't-io';
  }
  function traceRow(type, label, detail, cls, ms) {
    const row = h('div', { class: 'tr-row' });
    const head = h('button', { class: 'tr-head', type: 'button' }, `<span class="tr-t">+${(ms / 1000).toFixed(2)}s</span><span class="tr-type ${cls || typeCls(type)}">${esc(type)}</span><span class="tr-label">${esc(label)}</span>`);
    row.append(head);
    if (detail !== undefined) {
      const pre = h('pre', { class: 'json', hidden: true }, jsonHtml(detail));
      head.onclick = () => { pre.hidden = !pre.hidden; head.classList.toggle('open', !pre.hidden); };
      row.append(pre);
    } else head.classList.add('nodetail');
    const list = traceList();
    if (list) { list.append(row); list.scrollTop = list.scrollHeight; const cnt = $('#trace-count'); if (cnt) cnt.textContent = list.children.length; }
  }
  function trace(type, label, detail, cls) {
    const ms = Math.round(now() - T0);
    if (EMBED) { try { parent.postMessage({ proto: 'trace', type, label, detail: detail === undefined ? undefined : JSON.parse(JSON.stringify(detail)), cls, ms }, '*'); } catch (e) { /* not cloneable */ } return; }
    traceRow(type, label, detail, cls, ms);
  }
  function cosmos(container, doc) { trace('cosmos.write', `${container} (pk /session_id = ${S.sessionId})`, Object.assign({ session_id: S.sessionId, turn: S.turn }, doc)); }

  /* -------------------------------------------------------------- chat view */
  const scroller = () => $('#scroll');
  const down = () => { const s = scroller(); s.scrollTop = s.scrollHeight; };
  function botShell() {
    const m = h('div', { class: 'msg bot' }); const av = h('span', { class: 'avatar' }, ICON.botface); const b = h('div', { class: 'bubble' });
    m.append(av, b); scroller().append(m); down(); return { m, av, b };
  }
  function userBubble(t) { scroller().append(h('div', { class: 'msg user' }, `<div class="bubble"><div class="bubble-body">${esc(t)}</div></div>`)); down(); }
  function sysNote(t) { scroller().append(h('div', { class: 'msg system' }, `<div class="bubble-body">${esc(t)}</div>`)); down(); }
  const renderMd = (text) => text.split(/\n\n+/).map((p) => `<p>${md(p).replace(/\[(\d)\]/g, '<span class="cite">[$1]</span>').replace(/\n/g, '<br>')}</p>`).join('');
  async function streamInto(shell, text, cites, ttft) {
    const mine = S;
    shell.av.classList.add('think');
    const dots = h('div', { class: 'typing', role: 'status', 'aria-label': 'Le conseiller écrit' }, '<span></span><span></span><span></span>');
    shell.b.append(dots); down();
    await wait(ttft || rnd(450, 800));
    if (S !== mine) return;
    shell.av.classList.remove('think');
    const body = h('div', { class: 'bubble-body' }); dots.replaceWith(body);
    const parts = text.match(/\S+\s*/g) || []; let acc = '';
    for (let i = 0; i < parts.length; i += 2) { if (S !== mine) return; acc += parts.slice(i, i + 2).join(''); body.innerHTML = renderMd(acc).replace(/<\/p>$/, '<span class="caret"></span></p>'); down(); await wait(reduce() ? 0 : rnd(16, 36)); }
    body.innerHTML = renderMd(text);
    if (cites && cites.length) body.append(h('div', { class: 'sources' }, cites.map((c, i) => `[${i + 1}] ${esc(c)}`).join('<br>')));
    down();
    if (isPhone() && $('#stage .form-card')) peek(text);
  }
  /* phone: while a form covers the conversation, the latest reply shows as a peek above the form */
  function peek(text) {
    let p = $('#peek');
    if (!p) { p = h('button', { id: 'peek', class: 'peek', type: 'button' }); $('#ws').append(p); p.onclick = () => p.classList.remove('on'); }
    p.innerHTML = `<span class="avatar">${ICON.botface}</span><span class="pt">${md(text.replace(/\n+/g, ' '))}</span>`;
    p.classList.remove('on'); void p.offsetWidth; p.classList.add('on');
    clearTimeout(p._t); p._t = setTimeout(() => p.classList.remove('on'), 6500);
  }
  const hidePeek = () => { const p = $('#peek'); if (p) p.classList.remove('on'); };

  /* Suggested replies (ui.suggest_replies). D15: generic, fit any customer, never personal data.
     The BFF drops anything that looks like personal data before it reaches the page. */
  const PII = [/@/, /\d{3}[ .-]?\d{3}[ .-]?\d{4}/, /\b\d{4}-\d{2}-\d{2}\b/, /\b[A-Z]\d{4}-\d{6}-\d{2}\b/];
  function validateSuggestions(items) {
    const kept = [], dropped = [];
    for (const it of items) { const l = it.label || it; if (l.length > 40 || PII.some((re) => re.test(l))) dropped.push(l); else kept.push(it); }
    return { kept: kept.slice(0, 4), dropped };
  }
  function setSugg(items) {
    const box = $('#sugg'); box.innerHTML = '';
    (items || []).forEach((it, i) => {
      const b = h('button', { class: 'sg', type: 'button', style: `animation-delay:${i * 60}ms` }, esc(it.label || it));
      b.onclick = () => { if (S.busy) return; setSugg([]); if (it.action) runTurn({ kind: 'action', action_id: it.action, text: it.label }); else send(it.label || it); };
      box.append(b);
    });
  }

  /* ------------------------------------------------------- dossier (side panel) */
  const SRC_FR = { chat: 'Conversation', form: 'Formulaire', document: 'Document', lookup: 'Recherche', system: 'Calculé', confirmed: 'Confirmé' };
  function meta(e) {
    if (!e) return null;
    if (e.status === 'pending_confirmation') return { t: 'Confirmation en attente', inf: true };
    if (e.status === 'inferred') return { t: 'À confirmer', inf: true };
    return { t: SRC_FR[e.source] || '', inf: false };
  }
  function row(label, help, value, m, key) {
    const empty = value === '' || value == null;
    return `<div class="row${flash.has(key) ? ' flash' : ''}"><span class="k" tabindex="0" data-help="${esc(help || '')}" data-title="${esc(label)}">${esc(label)}${ICON.help}</span><span class="v${empty ? ' empty' : ''}">${empty ? '—' : esc(value) + (m && m.t ? `<small class="${m.inf ? 'inf' : ''}">${esc(m.t)}</small>` : '')}</span></div>`;
  }
  const SHORT = { contact_consent: 'Consentement' };
  const fieldRow = (path, labelOverride) => { const def = FIELD[A.keyOf(path)]; const e = env(path); const m = def && def.deterministic && e && e.status === 'confirmed' ? { t: 'Confirmé deux fois', inf: false } : meta(e); return row(labelOverride || SHORT[A.keyOf(path)] || flabel(def), fhelp(def), labelFor(A.keyOf(path), val(path)), m, path); };
  function completion() {
    const need = ['/quote/first_name', '/quote/last_name', '/quote/date_of_birth', '/quote/email', '/underwriting/has_claims', '/underwriting/has_convictions_in_past_three_years', '/underwriting/was_denied_or_cancelled'];
    let got = need.filter(has).length; const tot = need.length + 3;
    if (nVeh() && has(VP(0, 'usage_type'))) got++;
    if (S.asked.drivers) got++;
    if (S.assignDone || (nDrv() <= 1 && nVeh() <= 1 && nVeh())) got++;
    return Math.round((got / tot) * 100);
  }
  function renderStates() {
    const count = (function cnt(o) { if (!o || typeof o !== 'object') return 0; if ('value' in o && 'source' in o) return o.value == null || o.value === '' ? 0 : 1; return Object.values(o).reduce((s, x) => s + cnt(x), 0); })(S.state);
    $('#dcount').textContent = count;
    const pct = completion();
    let html = `<div class="d-item"><div style="display:flex;justify-content:space-between;font-size:13px;font-weight:600"><span>Soumission complétée</span><span>${pct} %</span></div><div class="meter"><i style="width:${pct}%"></i></div><div style="font-size:12px;color:var(--ink-3)">Mis à jour à chaque réponse. Survolez un champ pour une explication.</div></div>`;
    if (S.signals.length) html += `<div class="d-item"><div class="grp"><h4>Ce que j’ai remarqué</h4></div>${S.signals.map((s) => `<div class="signal${s.flag ? ' flag' : ''}">${esc(s.message)}</div>`).join('')}</div>`;
    const grp = (title, n, tot, rows) => `<div class="d-item grp"><h4>${esc(title)}<span>${n}/${tot}</span></h4>${rows}</div>`;
    const paths = (ps) => ps.map((p) => fieldRow(p)).join('');
    const cnt = (ps) => ps.filter(has).length;
    let k = ['/quote/quote_goal', '/quote/contact_consent']; html += grp('Votre demande', cnt(k), k.length, paths(k));
    k = ['/quote/first_name', '/quote/last_name', '/quote/date_of_birth', '/quote/email', '/quote/phone_number', '/drivers/0/driver_license_number']; html += grp('Vous', cnt(k), k.length, paths(k));
    let vr = nVeh() ? '' : row('Véhicule', 'Le véhicule à assurer, tel que décrit en conversation ou lu sur l’immatriculation.', '', null, 'vehicles');
    const asg = val('/household/driver_vehicle_assignments') || [];
    for (let i = 0; i < nVeh(); i++) {
      const e = env(VP(i, 'vehicle_make'));
      vr += row(`Véhicule ${i + 1}`, 'Le véhicule à assurer, tel que décrit en conversation ou lu sur l’immatriculation.', has(VP(i, 'vehicle_make')) ? vLabel(i) : '', meta(e), VP(i, 'vehicle_make'));
      vr += fieldRow(VP(i, 'usage_type'));
      const p = asg.find((a) => a.vehicle_index === i && a.role === 'principal');
      vr += row('Conducteur principal', 'La personne qui conduit ce véhicule le plus souvent. Chaque véhicule en a un.', p ? dName(p.driver_index) : '', null, '/household/driver_vehicle_assignments');
      vr += row('Conducteurs occasionnels', 'Les autres personnes qui conduisent ce véhicule à l’occasion.', asg.filter((a) => a.vehicle_index === i && a.role === 'occasional').map((a) => dFirst(a.driver_index)).join(', '), null, '/household/driver_vehicle_assignments');
    }
    html += grp('Véhicules', nVeh(), Math.max(1, nVeh()), vr);
    let dr = nDrv() ? '' : row('Conducteurs', 'Toute personne qui conduit un véhicule de la police, même à l’occasion.', '', null, 'drivers');
    for (let i = 0; i < nDrv(); i++) {
      const rel = i === 0 ? 'Vous' : labelFor('relationship_code', val(DP(i, 'relationship_code'))) || 'Lien à préciser';
      const age = ageOf(i);
      dr += row(`Conducteur ${i + 1}`, 'Toute personne qui conduit un véhicule de la police, même à l’occasion.', `${dName(i)} · ${rel}${age != null && i ? ', ' + age + ' ans' : ''}`, meta(env(i === 0 ? '/quote/first_name' : DP(i, 'driver_name'))), DP(i, 'driver_name'));
    }
    html += grp('Conducteurs', nDrv(), Math.max(1, nDrv()), dr);
    k = ['/drivers/0/years_with_current_insurer']; html += grp('Assurance actuelle', cnt(k), k.length, paths(k));
    k = ['/underwriting/has_claims', '/underwriting/has_convictions_in_past_three_years', '/underwriting/was_denied_or_cancelled']; html += grp('Antécédents', cnt(k), k.length, paths(k));
    $('#states').innerHTML = html; flash.clear();
    updateProgress();
  }
  const OFFER_FR = { essential: 'Essentiel', plus: 'Plus', premium: 'Premium' };
  function renderQuotes() {
    $('#quotes-count').textContent = S.quotes.length || '';
    if (!S.quotes.length) { $('#quotes').innerHTML = '<div style="color:var(--ink-3);font-size:13px">Aucune soumission pour l’instant. Elle apparaîtra ici dès qu’elle sera calculée.</div>'; return; }
    const sel = val('/underwriting/offer_type') || 'plus';
    $('#quotes').innerHTML = S.quotes.slice().reverse().map((q, i) => `<div class="q-card${i === 0 ? ' current' : ''}"><div class="q-top"><span>${esc(q.quote_id)} · version ${q.version}</span><span class="q-sub">${esc(q.created)}</span></div><div class="q-sub">${esc(q.reason)}${q.delta != null ? ` · <span class="${q.delta < 0 ? 'down' : 'up'}">${q.delta < 0 ? '' : '+'}${fmtMoney(q.delta, true)}/mois</span>` : ''}</div><table class="q-tbl"><tr><th>Offre</th><th>Par mois</th><th>Franchise</th></tr>${q.offers.map((o) => `<tr class="${o.offer_type === sel ? 'sel' : ''}"><td>${OFFER_FR[o.offer_type]}</td><td>${fmtMoney(o.monthly, true)}</td><td>${fmtMoney(o.deductible)}</td></tr>`).join('')}</table></div>`).join('');
  }
  const STEP_OF = { greet: -1, kb: -1, consent: -1, docs_offer: 0, docs: 0, name: 1, about: 1, dob: 1, email: 1, vehicle: 2, vehicle_form: 2, vehicle_detail: 2, usage: 2, rule_confirm: 2, drivers: 3, driver_names: 3, drivers_form: 3, second_vehicle: 3, second_which: 3, assign: 3, history: 4, history_chat: 4, quote_ready: 5, quote: 5, offers: 5, after: 5 };
  function updateProgress() {
    const si = STEP_OF[S.topic] != null ? STEP_OF[S.topic] : -1;
    $$('#steps span').forEach((sp, i) => { sp.classList.toggle('on', i === si); sp.classList.toggle('done', si >= 0 && i < si); });
  }
  const topic = (t) => { S.topic = t; updateProgress(); };
  let tipEl = null;
  function showTip(t) { hideTip(); if (!t.dataset.help) return; const r = t.getBoundingClientRect(); tipEl = h('div', { class: 'tip', role: 'tooltip' }, `<b>${esc(t.dataset.title || '')}</b>${esc(t.dataset.help)}`); document.body.append(tipEl); const tw = tipEl.offsetWidth; let x = r.left; if (x + tw > innerWidth - 12) x = innerWidth - tw - 12; tipEl.style.left = Math.max(12, x) + 'px'; tipEl.style.top = Math.min(r.bottom + 8, innerHeight - tipEl.offsetHeight - 8) + 'px'; }
  function hideTip() { if (tipEl) { tipEl.remove(); tipEl = null; } }
  document.addEventListener('mouseover', (e) => { const t = e.target.closest('[data-help]'); if (t) showTip(t); });
  document.addEventListener('mouseout', (e) => { if (e.target.closest('[data-help]')) hideTip(); });
  document.addEventListener('focusin', (e) => { const t = e.target.closest('[data-help]'); if (t) showTip(t); });
  document.addEventListener('focusout', hideTip);
  let dOpen = false;
  function panelFrames(open) {
    if (isPhone()) return open ? [{ transform: 'translateY(105%)' }, { transform: 'translateY(-2%)', offset: 0.65 }, { transform: 'none' }] : [{ transform: 'none' }, { transform: 'translateY(105%)' }];
    return open ? [{ transform: 'translateX(-105%)' }, { transform: 'translateX(3%)', offset: 0.65 }, { transform: 'none' }] : [{ transform: 'none' }, { transform: 'translateX(-105%)' }];
  }
  /* Collapsed unless the rail is clicked: spring in, then the items appear one after the other. */
  async function togglePanel(force) {
    const open = force != null ? force : !dOpen;
    if (open === dOpen) return;
    dOpen = open; const p = $('#dossier');
    $('#dtab').setAttribute('aria-expanded', String(open)); p.setAttribute('aria-hidden', String(!open));
    if (open) {
      renderStates(); renderQuotes(); p.classList.add('open');
      const items = $$('.d-item, .q-card', p); items.forEach((i) => { i.style.opacity = 0; });
      p.animate(panelFrames(true), { duration: reduce() ? 1 : 620, easing: 'cubic-bezier(.22,1,.36,1)' });
      items.forEach((it, i) => it.animate([{ opacity: 0, transform: 'translateY(12px)' }, { opacity: 1, transform: 'none' }], { duration: 320, delay: 220 + i * 70, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'forwards' }).finished.then(() => { it.style.opacity = ''; }));
    } else {
      await p.animate(panelFrames(false), { duration: reduce() ? 1 : 280, easing: 'cubic-bezier(.4,0,1,1)' }).finished;
      p.classList.remove('open');
    }
  }
  function selectTab(which) {
    $$('.dtabs button').forEach((b) => b.setAttribute('aria-selected', String(b.dataset.tab === which)));
    $('#states').hidden = which !== 'states'; $('#quotes').hidden = which !== 'quotes';
  }
  function flashTab(which) { const t = $(`.dtabs button[data-tab="${which}"]`); if (t) { t.classList.remove('ping'); void t.offsetWidth; t.classList.add('ping'); } }

  /* ------------------------------------------------------- BFF merge rules */
  const PREC = { form: 4, document: 3, lookup: 2, chat: 1, system: 0 };
  function commit(path, value, source, confidence, evidence, statusOverride) {
    const prev = env(path);
    const e = { value, source, confidence, status: statusOverride || (source === 'chat' ? 'inferred' : 'confirmed'), turn: S.turn, updated_at: new Date().toISOString() };
    if (evidence) e.evidence = evidence;
    A.ptrSet(S.state, path, e);
    flash.add(path);
    cosmos('state_changes', { path, old: prev ? prev.value : null, new: value, source, status: e.status, actor: source === 'form' ? 'customer' : source === 'chat' ? 'memory-agent' : source });
    pushToSurface(path, value, source, evidence);
    return e;
  }
  /* Live autofill: a value the memory agent or a document produced goes into the open form. */
  function pushToSurface(path, value, source, evidence) {
    const sf = S.openSurface;
    if (!sf || source === 'form' || !sf.bindings.some((b) => b.path === path)) return;
    const msg = { version: '1.0', updateDataModel: { surfaceId: sf.id, path, value } };
    const prov = { version: '1.0', updateDataModel: { surfaceId: sf.id, path: A.provPath(path), value: { source, note: evidence ? `« ${evidence} »` : undefined } } };
    trace('a2ui.message', `updateDataModel → ${sf.id} ${path} (live autofill)`, msg);
    sf.apply(msg); sf.apply(prov);
  }
  function derive(paths) {
    if (paths.some((p) => /\/quote\/(first_name|last_name)$/.test(p)) && (has('/quote/first_name') || has('/quote/last_name'))) {
      if (!nDrv()) S.state.drivers.push({});
      if (val(DP(0, 'driver_name')) !== selfName()) commit(DP(0, 'driver_name'), selfName(), 'system', 1);
      if (!has(DP(0, 'relationship_code'))) commit(DP(0, 'relationship_code'), 'self', 'system', 1);
    }
  }
  /* changes: [{path, value, confidence, evidence}] from one source. Returns pending deterministic changes. */
  function merge(changes, source) {
    const applied = [], pending = [];
    for (const ch of changes) {
      const key = A.keyOf(ch.path); const def = FIELD[key];
      const cur = env(ch.path);
      const curVal = cur && typeof cur === 'object' && 'value' in cur ? cur.value : undefined;
      if (JSON.stringify(curVal) === JSON.stringify(ch.value)) {
        if (cur && (PREC[source] > PREC[cur.source] || (cur.status === 'inferred' && source !== 'chat')) && !(def && def.deterministic && cur.status === 'pending_confirmation')) { cur.source = source; cur.status = 'confirmed'; cur.confidence = 1; cur.turn = S.turn; flash.add(ch.path); applied.push({ path: ch.path, value: ch.value, source, upgraded: true }); }
        continue;
      }
      if (cur && cur.status === 'confirmed' && PREC[source] < PREC[cur.source]) { trace('memory.conflict', `${ch.path}: confirmed ${curVal}, chat says ${ch.value}`, { path: ch.path, current_value: curVal, stated_value: ch.value }); continue; }
      if (def && def.deterministic && source !== 'system') {
        const e = commit(ch.path, ch.value, source, ch.confidence, ch.evidence, 'pending_confirmation');
        e.prev = cur || null;
        pending.push({ path: ch.path, label: flabel(def), old: curVal, value: ch.value, source, evidence: ch.evidence });
        continue;
      }
      commit(ch.path, ch.value, source, ch.confidence != null ? ch.confidence : 1, ch.evidence);
      applied.push({ path: ch.path, value: ch.value, source });
    }
    if (applied.length || pending.length) {
      trace('state.delta', `${applied.length} change${applied.length === 1 ? '' : 's'} from ${source}${pending.length ? `, ${pending.length} pending confirmation` : ''}`, { type: 'state.delta', data: { changes: applied.concat(pending.map((p) => ({ path: p.path, value: p.value, source, status: 'pending_confirmation' }))) } });
      cosmos('sessions', { op: 'replace', if_match: '<_etag>', changed_paths: applied.map((a) => a.path).concat(pending.map((p) => p.path)) });
      derive(changes.map((c) => c.path));
      afterCommit(source);
    }
    return pending;
  }
  async function confirmDeterministic(pending, reason) {
    if (!pending.length) return true;
    const cid = 'cf_' + Math.random().toString(36).slice(2, 7);
    trace('confirm.required', `${pending.length} deterministic field${pending.length > 1 ? 's' : ''}: ${pending.map((p) => A.keyOf(p.path)).join(', ')}`, { type: 'confirm.required', data: { confirmation_id: cid, changes: pending.map((p) => ({ path: p.path, old_value: p.old ?? null, new_value: p.value, source: p.source })) } });
    const ok = await doubleConfirm(pending, reason, cid);
    for (const p of pending) {
      const e = env(p.path);
      if (ok) { e.status = 'confirmed'; delete e.prev; flash.add(p.path); }
      else if (e.prev) A.ptrSet(S.state, p.path, e.prev); else A.ptrSet(S.state, p.path, undefined);
    }
    cosmos('confirmations', { confirmation_id: cid, accepted: ok, paths: pending.map((p) => p.path) });
    trace(ok ? 'confirm.accepted' : 'confirm.rejected', ok ? 'Both steps accepted; values committed' : 'Rejected; values reverted', { confirmation_id: cid, accepted: ok });
    if (S.openSurface) for (const p of pending) { const e = env(p.path); S.openSurface.apply({ version: '1.0', updateDataModel: { surfaceId: S.openSurface.id, path: p.path, value: e ? e.value : null } }); }
    afterCommit('confirmation');
    return ok;
  }
  function doubleConfirm(pending, reason, cid) {
    return new Promise((resolve) => {
      const back = h('div', { class: 'modal-back' });
      const modal = h('div', { class: 'modal', role: 'alertdialog', 'aria-modal': 'true', 'aria-labelledby': 'cf-title' });
      back.append(modal); $('#modal-root').append(back);
      const fmt = (p, v) => (v === undefined || v === null ? 'non indiqué' : labelFor(A.keyOf(p.path), v));
      const changes = pending.map((p) => `<dt>${esc(p.label)}</dt><dd>${esc(fmt(p, p.old))} → <strong>${esc(fmt(p, p.value))}</strong></dd>`).join('');
      const step = (n) => {
        modal.innerHTML = n === 1
          ? `<div class="modal-head"><div class="step">Étape 1 de 2 · information protégée</div><h3 id="cf-title">Confirmez cette réponse</h3><div class="progress2"><i class="on"></i><i></i></div></div><div class="modal-body">${esc(reason)}<dl class="change">${changes}</dl></div><div class="modal-foot"><button class="btn" data-a="no">Annuler</button><button class="btn primary" data-a="yes">C’est exact</button></div>`
          : `<div class="modal-head"><div class="step">Étape 2 de 2 · dernière vérification</div><h3 id="cf-title">Confirmez-vous?</h3><div class="progress2"><i class="on"></i><i class="on"></i></div></div><div class="modal-body">Votre réponse sera enregistrée à votre dossier, avec l’heure de votre confirmation. Vous pourrez la modifier plus tard.<label class="check" style="margin-top:12px"><input type="checkbox" id="cf-ack"> <span>Je confirme que ces renseignements sont exacts.</span></label></div><div class="modal-foot"><button class="btn" data-a="back">Retour</button><button class="btn primary" data-a="yes" disabled>Oui, je confirme</button></div>`;
        const yes = modal.querySelector('[data-a=yes]'); const ack = modal.querySelector('#cf-ack');
        if (ack) ack.onchange = () => { yes.disabled = !ack.checked; };
        $$('[data-a]', modal).forEach((b) => { b.onclick = () => {
          const a = b.dataset.a;
          trace('confirm.response', `step ${n}: ${a === 'yes' ? 'accepted' : a === 'back' ? 'back' : 'cancelled'}`, { type: 'confirm.response', data: { confirmation_id: cid, step: n, accepted: a === 'yes' } });
          if (a === 'yes' && n === 1) return step(2);
          if (a === 'back') return step(1);
          back.remove(); resolve(a === 'yes');
        }; });
      };
      step(1);
    });
  }

  /* ---------------------------------- end-of-form rules (mirror of bff/rules/end_of_form.py) */
  /* EndOfFormRules.check(state) runs after EVERY commit batch, whatever the source (memory agent,
     form submit, document review, full form, confirmation), before the main agent runs. */
  const EXOTIC = ['lamborghini', 'ferrari', 'mclaren', 'bugatti', 'rolls-royce', 'bentley', 'aston martin'];
  const eachVehicle = (pred) => { const out = []; for (let i = 0; i < nVeh(); i++) { const p = pred(i); if (p) out.push(p); } return out; };
  const RULES = [
    { id: 'VEHICULE_EXOTIQUE', severity: 'STOP', reason: 'Les véhicules exotiques ou de prestige, comme une Lamborghini, demandent une évaluation personnalisée par un conseiller : valeur de remplacement, entreposage et protections particulières.', match: () => eachVehicle((i) => (EXOTIC.includes(String(val(VP(i, 'vehicle_make')) || '').toLowerCase()) ? VP(i, 'vehicle_make') : null)) },
    { id: 'VEHICULE_MODIFIE_PERFORMANCE', severity: 'STOP', reason: 'Un véhicule modifié pour la performance doit être évalué par un conseiller avant qu’on puisse l’assurer.', match: () => eachVehicle((i) => (val(VP(i, 'modified_for_performance')) === true ? VP(i, 'modified_for_performance') : null)) },
    { id: 'USAGE_AGRICOLE', severity: 'STOP', reason: 'Les véhicules à usage agricole sont assurés par notre équipe d’assurance agricole. Un conseiller spécialisé prendra le relais.', match: () => eachVehicle((i) => (val(VP(i, 'usage_type')) === 'farm' ? VP(i, 'usage_type') : null)) },
    { id: 'REFUS_OU_RESILIATION', severity: 'STOP', reason: 'Comme une assurance vous a été refusée ou résiliée récemment, un conseiller doit étudier votre dossier avec vous.', match: () => (val('/underwriting/was_denied_or_cancelled') === true && env('/underwriting/was_denied_or_cancelled').status === 'confirmed' ? ['/underwriting/was_denied_or_cancelled'] : []) },
    { id: 'JEUNE_CONDUCTEUR_PRINCIPAL', severity: 'FLAG', reason: 'Un conducteur de moins de 25 ans est conducteur principal : le conseiller vérifiera les rabais pour jeunes conducteurs.', match: () => (val('/household/driver_vehicle_assignments') || []).filter((a) => a.role === 'principal' && (ageOf(a.driver_index) ?? 99) < 25).map(() => '/household/driver_vehicle_assignments') }
  ];
  function checkRules() {
    const hits = [];
    for (const r of RULES) {
      const paths = [...new Set(r.match())]; if (!paths.length) continue;
      const fingerprint = r.id + ':' + JSON.stringify(paths.map((p) => [p, val(p)]));
      if (S.acknowledged.has(fingerprint)) continue;
      hits.push({ rule_id: r.id, severity: r.severity.toLowerCase(), paths, values: paths.map(val), inferred: paths.every((p) => env(p) && env(p).status === 'inferred'), fingerprint, reason: r.reason });
    }
    hits.sort((a, b) => (a.severity === b.severity ? (a.inferred === b.inferred ? 0 : a.inferred ? 1 : -1) : a.severity === 'stop' ? -1 : 1));
    return { hits, stop: hits.find((x) => x.severity === 'stop') || null, flags: hits.filter((x) => x.severity === 'flag') };
  }
  function afterCommit(source) {
    renderStates(); renderQuotes();
    const res = checkRules();
    trace('rules.check', `EndOfFormRules.check after ${source} commit · ${res.hits.length ? res.hits.map((x) => x.rule_id + (x.inferred ? ' (inferred)' : '')).join(', ') : 'no hit'}`, { module: 'bff/rules/end_of_form.py', class: 'EndOfFormRules', trigger: source, hits: res.hits.map(({ rule_id, severity, paths, inferred }) => ({ rule_id, severity, paths, inferred })) });
    for (const f of res.flags) {
      S.acknowledged.add(f.fingerprint);
      if (!S.signals.some((x) => x.code === f.rule_id)) S.signals.push({ code: f.rule_id, message: f.reason, flag: true });
      trace('rules.hit', `${f.rule_id} (FLAG): advisor signal added, quote continues`, { type: 'rules.hit', data: { rule_id: f.rule_id, severity: 'flag', paths: f.paths, inferred: f.inferred, reason: f.reason } });
      renderStates();
    }
    if (res.stop && !S.ended && !S.sent) {
      trace('rules.hit', `${res.stop.rule_id} (STOP)${res.stop.inferred ? ': value only inferred from chat, ask to confirm first' : ': end the online quote'}`, { type: 'rules.hit', data: { rule_id: res.stop.rule_id, severity: 'stop', paths: res.stop.paths, inferred: res.stop.inferred, reason: res.stop.reason } });
      S.stopHit = res.stop;
    }
  }

  /* ----------------------------------------------------- memory agent (sim) */
  const MONTHS = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
  const MAKES = { Toyota: ['RAV4', 'Corolla', 'Camry', 'Highlander', 'Sienna'], Honda: ['Civic', 'CR-V', 'Accord', 'Odyssey'], Ford: ['F-150', 'Escape', 'Mustang'], Hyundai: ['Elantra', 'Tucson', 'Kona', 'Ioniq 5'], Kia: ['Forte', 'Sportage', 'Soul'], Mazda: ['Mazda3', 'CX-5', 'CX-30'], Subaru: ['Outback', 'Forester', 'Crosstrek'], Tesla: ['Model 3', 'Model Y'], Chevrolet: ['Equinox', 'Silverado', 'Bolt EV'], Volkswagen: ['Jetta', 'Golf', 'Tiguan'], Nissan: ['Rogue', 'Sentra', 'Leaf'], Lamborghini: ['Huracán', 'Urus'] };
  const NOT_NAMES = new Set(['Moi', 'Mon', 'Ma', 'Mes', 'Notre', 'Nos', 'Oui', 'Non', 'Il', 'Elle', 'Je', 'Et', 'Seulement', 'Plusieurs', 'Une', 'Un', 'Le', 'La', 'Les', 'Personne', 'Aussi', 'Bonjour', 'Merci', 'Desjardins', 'Quand', 'Pour', 'Avec', 'Lui', 'Son', 'Sa', 'Ses', 'Qui', 'Quel', 'Quelle', ...Object.keys(MAKES), ...Object.values(MAKES).flat()]);
  const REL = [[/\b(conjoint|conjointe|mari|femme|partenaire|chum|blonde|époux|épouse)\b/i, 'spouse'], [/\b(fils|fille|enfant)\b/i, 'child'], [/\b(père|mère|parent)\b/i, 'parent'], [/\b(frère|sœur|soeur|ami|amie|colocataire|coloc)\b/i, 'other']];
  function parsePeople(t) {
    const out = [];
    const names = [...t.matchAll(/\b([A-ZÀ-Ý][a-zà-ÿ'-]+)\b/g)].filter((m) => !NOT_NAMES.has(m[1]));
    for (const n of names) {
      let best = null;
      for (const [re, rel] of REL) { const g = new RegExp(re.source, 'gi'); let mm; while ((mm = g.exec(t))) { const dist = Math.abs(mm.index - n.index); if (dist < 30 && (!best || dist < best.dist)) best = { rel, dist }; } }
      if (!best) continue;
      const after = t.slice(n.index, n.index + 40).match(/(\d{1,2})\s*ans/);
      out.push({ name: n[1], rel: best.rel, age: after ? Number(after[1]) : null });
    }
    return out;
  }
  function parseDate(t) {
    let m = t.match(/\b(\d{4})-(\d{2})-(\d{2})\b/); if (m) return `${m[1]}-${m[2]}-${m[3]}`;
    m = t.match(/\b(\d{1,2})\/(\d{1,2})\/(\d{4})\b/); if (m) return `${m[3]}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`;
    m = t.toLowerCase().match(/\b(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})\b/); if (m && MONTHS.indexOf(m[2]) >= 0) return `${m[3]}-${String(MONTHS.indexOf(m[2]) + 1).padStart(2, '0')}-${m[1].padStart(2, '0')}`;
    return null;
  }
  const tcase = (w) => w.split(/([ -])/).map((x) => (x.length > 1 ? x.charAt(0).toUpperCase() + x.slice(1).toLowerCase() : x)).join('');
  const usageOf = (low) => (/ferme|agricole/.test(low) ? 'farm' : /affaires|clients|livraison/.test(low) ? 'business' : /travail|école|bureau|job/.test(low) ? 'commute' : /agrément|loisir|perso|courses|fins de semaine/.test(low) ? 'pleasure' : null);
  function prefOf(low) {
    if (/formulaire/.test(low) && /(préf|plutôt|aime mieux|aimerais|voudrais|veux|un formulaire|des formulaires)/.test(low) && !/pas de formulaire|sans formulaire/.test(low)) return 'forms';
    if (/(par écrit|en écrivant|par texte|en conversation|clavard|pas de formulaire|sans formulaire|répondre ici)/.test(low)) return 'text';
    return null;
  }
  /* Returns MemoryAgentOutput (contracts/memory-output.schema.json). */
  function memoryAgent(input) {
    const out = { state_changes: [], signals: [], conflicts: [], intent: { phase: S.phase, off_topic: false, wants_human: false, asks_question: false }, session_preferences: { answer_mode: null } };
    if (input.kind !== 'message') return out;
    const t = input.text; const low = t.toLowerCase();
    const set = (path, value, confidence, evidence) => out.state_changes.push({ path, op: 'set', value, confidence: confidence ?? 0.9, evidence: (evidence || t).slice(0, 160) });
    const add = (path) => out.state_changes.push({ path, op: 'add_entity', value: null, confidence: 1, evidence: t.slice(0, 160) });
    out.intent.off_topic = /météo|recette|blague|bitcoin|politique|hockey|horoscope/.test(low);
    out.intent.wants_human = /conseiller|humain|une personne réelle/.test(low) && !/principal|occasionnel/.test(low) && !/pourquoi/.test(low);
    out.intent.asks_question = /\?|c['’]est quoi|qu['’]est-ce/.test(low);
    out.session_preferences.answer_mode = prefOf(low);
    if (out.session_preferences.answer_mode) out.signals.push({ code: 'ANSWER_MODE_PREFERENCE', message: out.session_preferences.answer_mode === 'text' ? 'Préfère répondre par écrit.' : 'Préfère des formulaires.', confidence: 0.9, related_paths: [], suggested_follow_up: 'Honour the preference; do not ask again.' });
    if (!has('/quote/quote_goal') && /soumission|assur|voiture|auto|véhicule|compar/.test(low)) set('/quote/quote_goal', /compar/.test(low) ? 'compare_current' : 'new_quote', 0.86);
    if (S.topic === 'consent' && /\b(oui|accepte|d['’]accord|j['’]accepte)\b/.test(low) && !/pourquoi/.test(low)) set('/quote/contact_consent', true, 0.95);
    const STOP = /^(et|je|j|mais|pour|avec|de|du|des|le|la|les|mon|ma|mes|qui|ou|né|née)$/i;
    let nm = t.match(/(?:je m['’]appelle|mon nom est|moi c['’]est)\s+([A-Za-zÀ-ÿ'-]+)(?:\s+([A-Za-zÀ-ÿ'-]+))?/i) || t.match(/\b[Jj]e suis\s+([A-ZÀ-Ý][a-zà-ÿ'-]+)(?:\s+([A-ZÀ-Ý][a-zà-ÿ'-]+))?/);
    if (!nm && !has('/quote/first_name') && (S.topic === 'name' || S.topic === 'about')) { const p = t.trim().match(/^([A-Za-zÀ-ÿ'-]+)(?:\s+([A-Za-zÀ-ÿ'-]+))?(?:\s+([A-Za-zÀ-ÿ'-]+))?$/); if (p && !/pourquoi|formulaire|écrit|continuer|question|conseiller|oui|non/i.test(t)) nm = p; }
    if (nm && !STOP.test(nm[1])) { set('/quote/first_name', tcase(nm[1]), 0.95); if (nm[2] && !STOP.test(nm[2])) set('/quote/last_name', tcase(nm[3] ? nm[2] + ' ' + nm[3] : nm[2]), 0.92); }
    const em = t.match(/[^\s@]+@[^\s@]+\.[a-z]{2,}/i); if (em) set('/quote/email', em[0], 0.98, em[0]);
    const ph = t.match(/\b\d{3}[ .-]?\d{3}[ .-]?\d{4}\b/); if (ph) set('/quote/phone_number', ph[0], 0.95, ph[0]);
    const dob = (['dob', 'about'].includes(S.topic) || /né|née|naissance/.test(low)) && parseDate(t); if (dob) set('/quote/date_of_birth', dob, 0.93);
    /* vehicles */
    for (const mk of Object.keys(MAKES)) if (new RegExp('\\b' + mk + '\\b', 'i').test(t)) {
      let model = null; for (const mo of MAKES[mk]) if (new RegExp('\\b' + mo.split(' ')[0].replace(/á/g, '[aá]'), 'i').test(t)) model = mo;
      const yr = (t.match(/\b(19[5-9]\d|20[0-3]\d)\b/) || [])[1];
      let i = 0;
      if (['second_vehicle', 'second_which'].includes(S.topic) || /aussi|deuxième|autre/.test(low)) i = nVeh();
      else if (nVeh() && has(VP(0, 'vehicle_make')) && env(VP(0, 'vehicle_make')).status === 'confirmed' && val(VP(0, 'vehicle_make')) !== mk) i = nVeh();
      if (i >= nVeh()) add(VP(i, 'vehicle_make'));
      set(VP(i, 'vehicle_make'), mk, 0.9); if (model) set(VP(i, 'vehicle_model'), model, 0.88); if (yr) set(VP(i, 'vehicle_year'), Number(yr), 0.9);
    }
    if (S.topic === 'vehicle_detail') {
      const i = S.usageFor; const mo = (MAKES[val(VP(i, 'vehicle_make'))] || []).find((x) => new RegExp(x.split(' ')[0].replace(/á/g, '[aá]'), 'i').test(t)); const yr = (t.match(/\b(19[5-9]\d|20[0-3]\d)\b/) || [])[1];
      if (mo) set(VP(i, 'vehicle_model'), mo, 0.88); if (yr) set(VP(i, 'vehicle_year'), Number(yr), 0.9);
    }
    const us = usageOf(low);
    if (us && (S.topic === 'usage' || (S.openSurface && /^vehicule_/.test(S.openSurface.id)))) set(VP(S.topic === 'usage' ? S.usageFor : Number(S.openSurface.id.split('_')[1]), 'usage_type'), us, 0.85);
    if (S.topic === 'rule_confirm' && S.ruleAsk) {
      if (/^non|pas le bon/.test(low)) for (const p of S.ruleAsk.paths) { const base = p.slice(0, p.lastIndexOf('/')); for (const k of ['vehicle_make', 'vehicle_model', 'vehicle_year']) out.state_changes.push({ path: base + '/' + k, op: 'clear', value: null, confidence: 0.95, evidence: t.slice(0, 160) }); }
    }
    /* drivers */
    const people = parsePeople(t);
    let di = nDrv() || 1;
    for (const p of people) {
      if (S.state.drivers.some((d, i) => i > 0 && String(val(DP(i, 'driver_name')) || '').split(' ')[0].toLowerCase() === p.name.toLowerCase())) continue;
      add(DP(di, 'driver_name')); set(DP(di, 'driver_name'), p.name, 0.9); set(DP(di, 'relationship_code'), p.rel, 0.85);
      if (p.age) set(DP(di, 'driver_date_of_birth'), `${YEAR - p.age}-01-01`, 0.5, `${p.age} ans`);
      di++;
    }
    const dn = low.match(/(\d)\s+conducteurs?/);
    if (dn && Number(dn[1]) > 1) { S.countHint = Number(dn[1]); if (/une voiture|un véhicule|une auto|un char|une seule/.test(low)) out.signals.push({ code: 'MORE_DRIVERS_THAN_VEHICLES', message: `${dn[1]} conducteurs pour un seul véhicule : il y a peut-être un deuxième véhicule à assurer.`, confidence: 0.7, related_paths: ['/vehicles'], suggested_follow_up: 'Ask whether there is another vehicle to insure.' }); }
    /* history answered in chat (text mode): one question covers the three protected answers */
    if (S.topic === 'history_chat' && /^non|aucun/.test(low)) { set('/underwriting/has_claims', false, 0.9); set('/underwriting/has_convictions_in_past_three_years', false, 0.9); set('/underwriting/was_denied_or_cancelled', false, 0.9); }
    return out;
  }
  function applyMemory(mem) {
    trace('memory.result', `${mem.state_changes.length} state change${mem.state_changes.length === 1 ? '' : 's'}, ${mem.signals.length} signal${mem.signals.length === 1 ? '' : 's'}${mem.session_preferences.answer_mode ? ', answer_mode=' + mem.session_preferences.answer_mode : ''}${mem.intent.off_topic ? ', off_topic' : ''}`, mem);
    for (const c of mem.state_changes.filter((c) => c.op === 'add_entity')) { const coll = c.path.split('/')[1]; const i = Number(c.path.split('/')[2]); while (S.state[coll].length <= i) S.state[coll].push({}); }
    const removed = new Set();
    for (const c of mem.state_changes.filter((c) => c.op === 'clear')) { const base = c.path.slice(0, c.path.lastIndexOf('/')); const coll = base.split('/')[1]; const i = Number(base.split('/')[2]); if (!removed.has(base) && S.state[coll][i]) { removed.add(base); S.state[coll].splice(i, 1); flash.add(base); cosmos('state_changes', { path: base, op: 'remove_entity', actor: 'memory-agent' }); } }
    if (mem.session_preferences.answer_mode) { S.prefs.answer_mode = mem.session_preferences.answer_mode; trace('session.preferences', `answer_mode = ${S.prefs.answer_mode} (persisted in the session document)`, { type: 'session.preferences', data: S.prefs }); cosmos('sessions', { op: 'patch', path: '/preferences/answer_mode', value: S.prefs.answer_mode }); }
    for (const s of mem.signals) if (!S.signals.some((x) => x.code === s.code)) S.signals.push(s);
    if (mem.signals.length) trace('signals', mem.signals.map((s) => s.code).join(', '), { type: 'signals', data: { items: mem.signals } });
    return merge(mem.state_changes.filter((c) => c.op === 'set'), 'chat');
  }

  /* ------------------------------------------------------ quoting (fake) */
  function rate(opts) {
    opts = opts || {};
    const v = (p, d) => { const x = val(p); return x == null || x === '' ? d : x; };
    let total = 0;
    for (let i = 0; i < Math.max(1, nVeh()); i++) {
      const typeF = { car: 1, suv: 1.06, minivan: 1.02, pickup: 1.1 }[v(VP(i, 'vehicle_type'), 'car')] || 1;
      const age = YEAR - v(VP(i, 'vehicle_year'), 2018);
      let p = 780 + Math.max(4000, 34000 - age * 2600) * 0.012;
      p *= typeF * ({ pleasure: 0.95, commute: 1.06, business: 1.22, farm: 0.9 }[v(VP(i, 'usage_type'), 'commute')] || 1);
      total += i === 0 ? p : p * 0.85;
    }
    for (let i = 1; i < nDrv(); i++) { const a = ageOf(i); if (a != null && a < 25) total *= 1.28; }
    if (v('/underwriting/has_claims', false)) total *= 1.18;
    if (v('/underwriting/has_convictions_in_past_three_years', false)) total *= 1.12;
    total *= Math.max(0.9, 1 - 0.01 * v('/drivers/0/years_with_current_insurer', 0));
    const ded = Number(opts.deductible || v('/underwriting/deductible', '500'));
    const dedF = (d) => ({ 250: 1.09, 500: 1, 1000: 0.92, 2500: 0.85 }[d] || 1);
    const addOns = opts.add_ons || v('/underwriting/add_ons', []);
    const addM = addOns.reduce((s, a) => s + ({ roadside: 4.5, rental_car: 6, accident_forgiveness: 9, new_vehicle_replacement: 11 }[a] || 0), 0);
    const mk = (type, f, d, cov) => { const annual = Math.round(total * f * dedF(d) + addM * 12); return { offer_type: type, monthly: Math.round((annual / 12) * 100) / 100, annual, deductible: d, coverages: cov }; };
    return [
      mk('essential', 0.84, opts.deductible ? ded : 1000, ['Responsabilité civile 1 M$', 'Collision ou versement', 'Risques autres que la collision']),
      mk('plus', 1, ded, ['Responsabilité civile 2 M$', 'Collision et autres risques', 'Assistance routière', 'Voiture de location jusqu’à 1 200 $']),
      mk('premium', 1.21, opts.deductible ? ded : 250, ['Responsabilité civile 2 M$', 'Remplacement à neuf (5 ans)', 'Pardon d’accident', 'Voiture de location jusqu’à 2 000 $'])
    ];
  }
  let fcSeq = 0;
  /* A2UI 1.0 renderer-to-agent function call, answered by the BFF directly: no model call, no turn. */
  function agentFunction(fn, toolName, args, result) {
    const id = 'fc_' + (++fcSeq); const sid = S.openSurface ? S.openSurface.id : undefined;
    trace('a2ui.callAgentFunction', `${fn} ← ${sid || 'renderer'}`, { version: '1.0', callAgentFunction: { surfaceId: sid, functionCallId: id, callFunction: { '@call': fn, catalogId: A.CATALOG_ID, args } } });
    toolCall(toolName, args, result, 'terminal', 'BFF answers the renderer, no model call');
    trace('a2ui.agentFunctionResponse', `${fn} → ${sid || 'renderer'}`, { version: '1.0', agentFunctionResponse: { functionCallId: id, value: result } });
  }
  function toolCall(name, args, result, resultMode, by) {
    trace('tool.call', `${name}${by ? ' · ' + by : ''} (${resultMode})`, { name, arguments: args, result_mode: resultMode, via: name.startsWith('knowledge') ? 'Foundry IQ knowledge base MCP endpoint' : name.startsWith('ui.') ? 'local function tool in the hosted agent' : name.startsWith('document') ? 'BFF → document agent (Foundry prompt agent)' : 'toolbox MCP endpoint → ' + name.split('.')[0] });
    trace('tool.result', `${name} · ${rnd(60, 240)} ms`, result);
  }
  function createQuote(reason, opts, by) {
    const id = S.quotes.length ? S.quotes[0].quote_id : 'Q-' + (1000 + rnd(10, 99));
    const offers = rate(opts);
    const prev = S.quotes[S.quotes.length - 1];
    const sel = val('/underwriting/offer_type') || 'plus';
    const q = { quote_id: id, version: S.quotes.length + 1, offers, reason, created: new Date().toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' }) };
    if (prev) { const a = prev.offers.find((o) => o.offer_type === sel), b = offers.find((o) => o.offer_type === sel); q.delta = Math.round((b.monthly - a.monthly) * 100) / 100; q.previous_version = prev.version; }
    toolCall(prev ? 'quoting.modify_quote' : 'quoting.create_quote', prev ? { quote_id: id, changes: opts, session_ref: '<injected by hosted agent>' } : { offer_types: ['essential', 'plus', 'premium'], session_ref: '<injected by hosted agent>' }, { quote_id: id, version: q.version, previous_version: q.previous_version, delta_monthly: q.delta, offers }, 'narrate', by);
    S.quotes.push(q);
    trace(prev ? 'quote.updated' : 'quote.created', `${id} v${q.version}`, { type: prev ? 'quote.updated' : 'quote.created', data: q });
    cosmos('quotes', { quote_id: id, version: q.version, offers });
    renderQuotes(); flashTab('quotes');
    return q;
  }

  /* ----------------------------------------------- journey forms (FormSpecs) */
  /* Small, subject-focused forms (D13). Each builder returns make(edit) → { spec, sample, chip }; the
     chip keeps make() so the customer can reopen the form later (D16). */
  const pf = (path) => { const e = env(path); if (!e || e.value == null || e.value === '') return undefined; return ['chat', 'document', 'lookup'].includes(e.source) ? { value: e.value, source: e.source, note: e.evidence ? `« ${e.evidence} »` : undefined } : { value: e.value }; };
  const fld = (path, extra) => { const o = Object.assign({ key: path }, extra || {}); const p = pf(path); if (p) o.prefill = p; return o; };
  const ymmPrefill = (i) => { const v = {}; for (const k of ['vehicle_year', 'vehicle_make', 'vehicle_model']) if (has(VP(i, k))) v[k] = val(VP(i, k)); const e = env(VP(i, 'vehicle_make')); return Object.keys(v).length ? { value: v, source: e && ['chat', 'document'].includes(e.source) ? e.source : undefined } : undefined; };
  const relOpts = () => (A.fenum(FIELD.relationship_code) || []).filter((e) => e[0] !== 'self');
  const DOCS = [
    { purpose: 'licence', icon: 'idcard', title: 'Permis de conduire', description: 'Nom, date de naissance et numéro de permis.', fills: { first_name: '/quote/first_name', last_name: '/quote/last_name', date_of_birth: '/quote/date_of_birth', driver_license_number: '/drivers/0/driver_license_number' } },
    { purpose: 'registration', icon: 'regdoc', title: 'Certificat d’immatriculation', description: 'Année, marque, modèle et numéro d’identification du véhicule.', fills: { vehicle_year: '/vehicles/0/vehicle_year', vehicle_make: '/vehicles/0/vehicle_make', vehicle_model: '/vehicles/0/vehicle_model', vin: '/vehicles/0/vin' } },
    { purpose: 'policy', icon: 'policy', title: 'Police d’assurance actuelle', description: 'Ancienneté chez votre assureur et conducteurs inscrits.', fills: { years_with_current_insurer: '/drivers/0/years_with_current_insurer', listed_driver_1_name: '/drivers/1/driver_name', listed_driver_1_relationship: '/drivers/1/relationship_code' }, labels: { listed_driver_1_name: 'Autre conducteur inscrit', listed_driver_1_relationship: 'Lien avec vous' } }
  ];
  function specDocs() {
    return (edit) => {
      const values = {}; if (edit) for (const d of DOCS) for (const p of Object.values(d.fills)) if (has(p)) values[p] = val(p);
      return { spec: { form_id: 'documents', title: 'Vos documents', intro: 'Notre agent de documents lit chaque photo. Vous vérifiez ce qu’il a trouvé avant qu’on l’utilise.', layout: 'single', actions: edit ? [{ id: 'submit', label: 'Enregistrer', style: 'primary' }] : [{ id: 'nodocs', label: 'Continuer sans documents', style: 'secondary' }, { id: 'submit', label: 'Utiliser ces informations', style: 'primary' }], fields: [{ widget: 'document_drop', label: 'Téléversez ceux que vous avez', data: { documents: DOCS, read: S.docsRead.slice(), values: edit ? values : undefined } }] }, sample: 'readAll', chip: 'vos documents' };
    };
  }
  function specAbout(o) {
    return (edit) => {
      const withName = edit || o.name, withEmail = edit ? has('/quote/email') || o.email : o.email;
      return { spec: { form_id: 'a_propos', title: withName ? 'À propos de vous' : 'Deux détails sur vous', layout: 'two_column', entity: { type: 'quote' }, fields: [...(withName ? [fld('/quote/first_name'), fld('/quote/last_name')] : []), fld('/quote/date_of_birth', { hint: 'Sert à établir votre expérience de conduite.' }), ...(withEmail ? [fld('/quote/email', { hint: 'Nous vous y enverrons votre soumission.' })] : [])] }, sample: { '/quote/first_name': 'Marie', '/quote/last_name': 'Tremblay', '/quote/date_of_birth': '1987-01-18', '/quote/email': 'marie.tremblay@exemple.com' }, chip: 'à propos de vous' };
    };
  }
  function specVehicle(i) {
    return () => ({ spec: { form_id: `vehicule_${i}`, title: i ? 'Votre deuxième véhicule' : 'Votre véhicule', intro: has(VP(i, 'vehicle_make')) ? 'Vérifiez ce que j’ai compris et ajoutez son utilisation.' : undefined, layout: 'single', entity: { type: 'vehicle', index: i }, fields: [{ key: VP(i, 'vehicle_year'), widget: 'ymm', prefill: ymmPrefill(i) }, fld(VP(i, 'usage_type'), { widget: 'chips' })] }, sample: Object.assign(i ? { [VP(i, 'vehicle_year')]: 2015, [VP(i, 'vehicle_make')]: 'Honda', [VP(i, 'vehicle_model')]: 'Civic' } : { [VP(i, 'vehicle_year')]: 2021, [VP(i, 'vehicle_make')]: 'Toyota', [VP(i, 'vehicle_model')]: 'RAV4' }, has(VP(i, 'vehicle_make')) ? { [VP(i, 'vehicle_year')]: val(VP(i, 'vehicle_year')), [VP(i, 'vehicle_make')]: val(VP(i, 'vehicle_make')), [VP(i, 'vehicle_model')]: val(VP(i, 'vehicle_model')) } : {}, { [VP(i, 'usage_type')]: 'commute' }), chip: i ? 'deuxième véhicule' : 'véhicule' });
  }
  function driversRepeater() {
    const rows = []; for (let i = 1; i < nDrv(); i++) rows.push({ driver_name: val(DP(i, 'driver_name')), relationship_code: val(DP(i, 'relationship_code')), driver_date_of_birth: val(DP(i, 'driver_date_of_birth')) });
    return { key: '/drivers', widget: 'repeater', label: 'Autres conducteurs', repeat: { start: 1, count: Math.max(1, rows.length), min: 0, max: 6, item_label: 'Conducteur {n}', add_label: 'Ajouter un conducteur', fields: [{ key: 'driver_name', widget: 'text', label: 'Prénom et nom' }, { key: 'relationship_code', widget: 'select', label: 'Lien avec vous', options: relOpts() }, { key: 'driver_date_of_birth', widget: 'date', label: 'Date de naissance' }], prefill: rows } };
  }
  function specDrivers() {
    return () => ({ spec: { form_id: 'conducteurs', title: 'Les autres conducteurs', intro: 'Vous êtes déjà inscrit. Ajoutez les personnes qui conduisent, même à l’occasion. Laissez vide s’il n’y en a pas.', layout: 'single', fields: [driversRepeater()] }, sample: { '/drivers/1/driver_name': 'Alex Tremblay', '/drivers/1/relationship_code': 'spouse', '/drivers/1/driver_date_of_birth': '1986-05-02' }, chip: 'conducteurs' });
  }
  function specAssign() {
    return () => {
      const drivers = []; for (let i = 0; i < nDrv(); i++) { const a = ageOf(i); drivers.push({ index: i, label: dName(i), sub: i === 0 ? 'Vous' : `${labelFor('relationship_code', val(DP(i, 'relationship_code'))) || 'Conducteur'}${a != null && a < 30 ? ', ' + a + ' ans' : ''}` }); }
      const vehicles = []; for (let i = 0; i < nVeh(); i++) vehicles.push({ index: i, label: vLabel(i), short: vShort(i), sub: labelFor('usage_type', val(VP(i, 'usage_type'))) || 'Utilisation à préciser' });
      const cur = val('/household/driver_vehicle_assignments');
      return { spec: { form_id: 'qui_conduit_quoi', title: 'Qui conduit quoi?', intro: 'Glissez chaque personne vers le véhicule qu’elle conduit. Un conducteur principal par véhicule; les autres sont occasionnels.', layout: 'single', actions: [{ id: 'submit', label: 'Confirmer', style: 'primary' }], fields: [{ key: '/household/driver_vehicle_assignments', widget: 'assignment_board', label: 'Conducteurs et véhicules', data: { drivers, vehicles }, prefill: cur ? { value: cur } : undefined }] }, sample: 'board', chip: 'qui conduit quoi' };
    };
  }
  function specHistory() {
    return () => ({ spec: { form_id: 'antecedents', title: 'Vos antécédents', intro: 'Trois questions. Ces réponses sont protégées : vous les confirmez avant qu’on les utilise.', layout: 'single', entity: { type: 'underwriting' }, fields: [fld('/underwriting/has_claims', { widget: 'toggle' }), fld('/underwriting/has_convictions_in_past_three_years', { widget: 'toggle' }), fld('/underwriting/was_denied_or_cancelled', { widget: 'toggle' })] }, sample: { '/underwriting/has_claims': false, '/underwriting/has_convictions_in_past_three_years': false, '/underwriting/was_denied_or_cancelled': false }, chip: 'antécédents' });
  }
  function specOffers(q) {
    return () => ({ spec: { form_id: `offres_${q.quote_id}_v${q.version}`.toLowerCase().replace(/[^a-z0-9_]/g, '_'), title: 'Votre soumission : 3 options', layout: 'single', entity: { type: 'underwriting' }, actions: [{ id: 'select', label: 'Choisir cette offre', style: 'primary' }], fields: [{ key: 'offer_type', widget: 'offer_cards', label: 'Choisissez une offre', data: { offers: q.offers, recommended: 'plus' }, prefill: has('/underwriting/offer_type') ? { value: val('/underwriting/offer_type') } : undefined }] }, sample: { '/underwriting/offer_type': 'plus' }, chip: 'votre soumission' });
  }
  function convSummary() {
    const lines = [`${selfName() || 'Le client'} souhaite une soumission d’assurance auto (${labelFor('quote_goal', val('/quote/quote_goal')) || 'nouvelle soumission'}).`];
    if (nVeh()) lines.push('Véhicules : ' + S.state.vehicles.map((_, i) => vLabel(i) + (has(VP(i, 'usage_type')) ? ', ' + labelFor('usage_type', val(VP(i, 'usage_type'))).toLowerCase() : '')).join('; ') + '.');
    if (nDrv()) lines.push('Conducteurs : ' + S.state.drivers.map((_, i) => `${dName(i)} (${i === 0 ? 'titulaire' : (labelFor('relationship_code', val(DP(i, 'relationship_code'))) || 'lien à préciser').toLowerCase()})`).join(', ') + '.');
    if (S.signals.length) lines.push('À noter : ' + S.signals.map((s) => s.message).join(' '));
    if (has('/drivers/0/years_with_current_insurer')) lines.push(`Assuré chez son assureur actuel depuis ${val('/drivers/0/years_with_current_insurer')} ans.`);
    lines.push('Consentement : ' + (val('/quote/contact_consent') ? 'confirmé en deux étapes.' : 'non donné.'));
    return lines.join('\n');
  }
  function specFin(hit) {
    return () => {
      S.ended = true;
      return { spec: { form_id: 'fin_conseiller', title: 'Poursuivons avec un conseiller', intro: 'La soumission en ligne s’arrête ici. Un conseiller reprend votre dossier sans que vous ayez à tout répéter.', layout: 'two_column', entity: { type: 'quote' }, actions: [{ id: 'edit', label: 'Modifier mes réponses', style: 'secondary' }, { id: 'send', label: 'Envoyer à un conseiller', style: 'primary' }], fields: [{ widget: 'info', tone: 'warn', hint: `**Pourquoi?** ${hit.reason}${hit.rule_id ? ` (règle ${hit.rule_id})` : ''}` }, { widget: 'section', label: 'Votre résumé, à vérifier' }, fld('/quote/first_name', { required: true }), fld('/quote/last_name', { required: true }), fld('/quote/phone_number', { required: true }), fld('/quote/email', { required: false }), { key: '/quote/handoff_summary', widget: 'textarea', label: 'Résumé de la conversation', hint: 'Modifiez-le au besoin : c’est ce que le conseiller lira en premier.', prefill: { value: convSummary() } }, { key: '/quote/handoff_consent', widget: 'checkbox', label: 'J’accepte que ce résumé et mes réponses soient transmis à un conseiller Desjardins Assurances.', required: true }] }, sample: Object.assign(has('/quote/first_name') ? {} : { '/quote/first_name': 'Marie' }, has('/quote/last_name') ? {} : { '/quote/last_name': 'Tremblay' }, { '/quote/phone_number': '514 555-0142', '/quote/handoff_consent': true }), chip: 'transfert à un conseiller', cls: 'endcard', bff: true };
    };
  }

  /* --------------------------------------------------- main agent (sim) */
  const SUGG = {
    greet: ['Obtenir une soumission auto', 'Comparer avec mon assureur actuel', 'J’ai une question sur les protections', 'Parler à un conseiller'],
    consent: ['Oui, j’accepte', 'Pourquoi en avez-vous besoin?', 'Non, pas maintenant'],
    docs: ['Oui, j’ai mes documents', 'J’en ai seulement quelques-uns', 'Je ne les ai pas'],
    form: ['J’ai une question', 'Parler à un conseiller'],
    vehicle: ['Je ne connais pas le modèle exact', 'J’ai plus d’un véhicule'],
    drivers: ['Seulement moi', 'Moi et une autre personne', 'Plusieurs personnes'],
    moreDrivers: ['Non, c’est tout', 'Oui, une autre personne'],
    kb: ['La collision', 'Les risques autres que la collision', 'La responsabilité civile', 'La franchise'],
    after: [{ label: 'Réduire ma prime', action: 'modify:deductible=1000' }, { label: 'Ajouter des protections', action: 'modify:add_ons=accident_forgiveness' }, 'Parler à un conseiller']
  };
  const usageSugg = () => (A.fenum(FIELD.usage_type) || []).map((e) => e[1].replace(/\s*\(.*\)$/, ''));
  const KB = [
    [/autres que la collision|tous risques|\bvol\b|vandalisme/, 'Les **risques autres que la collision** couvrent le vol, le vandalisme, le feu, la grêle et le bris de vitre [1].', ['Guide des protections auto, section 3.2']],
    [/collision/, 'La **protection collision** paie la réparation de votre véhicule après un accident avec un autre véhicule ou un objet, moins votre franchise [1]. Au Québec, si l’autre conducteur est identifié, la convention d’indemnisation directe s’applique souvent [2].', ['Guide des protections auto, section 3.1', 'Convention d’indemnisation directe']],
    [/responsabilité/, 'La **responsabilité civile** paie les dommages que vous causez aux biens des autres. Au Québec, les blessures corporelles sont couvertes par la SAAQ [1].', ['Guide des protections auto, section 1.1']],
    [/franchise/, 'La **franchise** est le montant que vous payez lors d’une réclamation avant que l’assureur paie le reste. Une franchise plus élevée réduit votre prime [1].', ['Guide des protections auto, section 2.4']],
    [/compte comme (une )?réclamation/, 'Une **réclamation** est toute demande d’indemnisation à un assureur auto, que vous soyez responsable ou non : accident, vol, bris de vitre [1].', ['Guide des protections auto, section 6.1']],
    [/conducteur principal|occasionnel/, 'Le **conducteur principal** est la personne qui utilise le véhicule le plus souvent. Les personnes qui le conduisent à l’occasion sont des conducteurs occasionnels [1].', ['Guide des protections auto, section 5.3']]
  ];
  const WHY = [[/pourquoi .*nom/, 'Votre nom doit correspondre à celui de votre permis de conduire : c’est ce qui identifie le conducteur assuré.'], [/pourquoi .*courriel/, 'Je vous y envoie votre soumission, valide 30 jours. Rien d’autre sans votre accord.'], [/pourquoi .*naissance/, 'Votre âge et votre expérience de conduite influencent la prime.']];
  const DOCS_OFFER = 'Pour aller plus vite, avez-vous vos documents sous la main? Je peux lire votre **permis de conduire**, votre **certificat d’immatriculation** et votre **police d’assurance actuelle** pour remplir l’information à votre place.\n\nBon à savoir : vous pouvez me répondre par écrit ou me demander un formulaire, comme vous préférez. Dites-le-moi simplement en cours de route.';
  const formPlan = (t, text, make, sugg) => { topic(t); return { text, form: make, suggestions: sugg || SUGG.form }; };
  const historyMissing = () => ['/underwriting/has_claims', '/underwriting/has_convictions_in_past_three_years', '/underwriting/was_denied_or_cancelled'].some((p) => !has(p));
  function nextStep(prefix) {
    prefix = prefix || ''; const P = S.prefs.answer_mode;
    S.phase = 'gather';
    if (!S.asked.docs) { S.asked.docs = true; topic('docs_offer'); return { text: prefix + DOCS_OFFER, suggestions: SUGG.docs }; }
    if (!has('/quote/first_name')) {
      if (P === 'forms') return formPlan('about', prefix + 'Voici un court formulaire pour faire connaissance.', specAbout({ name: true, email: !has('/quote/email') }));
      topic('name'); return { text: prefix + 'Commençons par vous. Quel est votre nom complet?', suggestions: ['Pourquoi avez-vous besoin de mon nom?'] };
    }
    if (!has('/quote/date_of_birth')) {
      if (P === 'text') { topic('dob'); return { text: prefix + `Merci, ${val('/quote/first_name')}. Quelle est votre date de naissance?`, suggestions: ['Pourquoi ma date de naissance?'] }; }
      return formPlan('about', prefix + `Enchanté, ${val('/quote/first_name')}! Deux détails rapides.`, specAbout({ email: P === 'forms' && !has('/quote/email') }));
    }
    if (!has('/quote/email')) {
      if (P === 'forms') return formPlan('about', prefix + 'Il me manque votre courriel.', specAbout({ email: true }));
      topic('email'); return { text: prefix + 'Quelle adresse courriel puis-je utiliser pour vous envoyer votre soumission?', suggestions: ['Pourquoi avez-vous besoin de mon courriel?'] };
    }
    if (!nVeh()) {
      if (P === 'forms') { S.state.vehicles.push({}); return formPlan('vehicle_form', prefix + 'Choisissez le véhicule à assurer.', specVehicle(0)); }
      topic('vehicle'); return { text: prefix + 'Quel véhicule voulez-vous assurer? Indiquez simplement l’année, la marque et le modèle.', suggestions: SUGG.vehicle };
    }
    const vi = S.state.vehicles.findIndex((_, i) => !has(VP(i, 'vehicle_make')) || !has(VP(i, 'vehicle_model')) || !has(VP(i, 'vehicle_year')) || !has(VP(i, 'usage_type')));
    if (vi >= 0) {
      const full = has(VP(vi, 'vehicle_make')) && has(VP(vi, 'vehicle_model')) && has(VP(vi, 'vehicle_year'));
      if (P === 'text' && full) { topic('usage'); S.usageFor = vi; return { text: prefix + `Comment utilisez-vous la ${vShort(vi)}?`, suggestions: usageSugg() }; }
      if (P === 'text' && has(VP(vi, 'vehicle_make'))) { topic('vehicle_detail'); S.usageFor = vi; return { text: prefix + `Quels sont l’année et le modèle de votre ${val(VP(vi, 'vehicle_make'))}?`, suggestions: ['Je ne connais pas le modèle exact'] }; }
      if (P === 'text' && !has(VP(vi, 'vehicle_make'))) { topic('vehicle'); return { text: prefix + 'Quel véhicule voulez-vous assurer? Indiquez l’année, la marque et le modèle.', suggestions: SUGG.vehicle }; }
      const e = env(VP(vi, 'vehicle_make'));
      return formPlan('vehicle_form', prefix + (e && e.source === 'document' ? 'J’ai lu votre véhicule sur l’immatriculation. Il me manque seulement son utilisation.' : has(VP(vi, 'vehicle_make')) ? 'Voici ce que j’ai compris. Vérifiez-le et indiquez son utilisation.' : 'Choisissez le véhicule dans la liste.'), specVehicle(vi));
    }
    if (!S.asked.drivers) {
      if (P === 'forms') return formPlan('drivers_form', prefix + 'Qui conduit vos véhicules? Ajoutez les autres conducteurs ici.', specDrivers());
      topic('drivers');
      if (nDrv() > 1) { const fromPolicy = S.state.drivers.some((d, i) => i > 0 && env(DP(i, 'driver_name')) && env(DP(i, 'driver_name')).source === 'document'); return { text: prefix + `${fromPolicy ? 'Votre police mentionne' : 'J’ai noté'} ${listFr(S.state.drivers.map((_, i) => dFirst(i)))} comme conducteurs. Y en a-t-il d’autres?`, suggestions: SUGG.moreDrivers }; }
      if (S.countHint > 1) return { text: prefix + `Vous m’avez parlé de **${S.countHint} conducteurs**. Qui sont les autres? Donnez-moi leur prénom et leur lien avec vous.`, suggestions: [] };
      return { text: prefix + `Qui conduit la ${vShort(0)}?`, suggestions: SUGG.drivers };
    }
    if (nDrv() > nVeh() && !S.asked.second) {
      S.asked.second = true; topic('second_vehicle');
      if (!S.signals.some((s) => s.code === 'MORE_DRIVERS_THAN_VEHICLES')) { S.signals.push({ code: 'MORE_DRIVERS_THAN_VEHICLES', message: `${nDrv()} conducteurs pour ${nVeh()} véhicule : il y a peut-être un autre véhicule à assurer.` }); renderStates(); }
      return { text: prefix + `Vous êtes **${nDrv()} conducteurs** pour un seul véhicule. Y a-t-il un autre véhicule à assurer? C’est souvent moins cher ensemble.`, suggestions: ['Oui, j’ai un autre véhicule', 'Non, un seul véhicule'] };
    }
    if ((nDrv() > 1 || nVeh() > 1) && !S.assignDone) return formPlan('assign', prefix + 'Dernière étape pour les véhicules : **glissez chaque personne** vers le véhicule qu’elle conduit. Un conducteur principal par véhicule; les autres sont occasionnels.', specAssign(), ['Qu’est-ce qu’un conducteur principal?']);
    if (historyMissing()) {
      if (P === 'text') { topic('history_chat'); return { text: prefix + 'Dernières questions. Au cours des dernières années, avez-vous eu **une réclamation** (6 ans), **une infraction** au Code de la sécurité routière (3 ans) ou **un refus ou une résiliation** d’assurance (3 ans)?', suggestions: ['Non, aucun des trois', 'Oui, au moins un', 'Qu’est-ce qui compte comme une réclamation?'] }; }
      return formPlan('history', prefix + 'Presque fini! Trois questions sur vos antécédents.', specHistory(), ['Qu’est-ce qui compte comme une réclamation?']);
    }
    if (!S.quotes.length) { topic('quote_ready'); return { text: prefix + 'J’ai tout ce qu’il faut. Je calcule votre soumission?', suggestions: [{ label: 'Obtenir ma soumission', action: 'get_quote' }, 'J’ai une question'] }; }
    topic('after'); return { text: prefix + 'Votre soumission est à jour. Voulez-vous l’ajuster?', suggestions: SUGG.after };
  }
  function decide(input, mem) {
    if (S.stopHit) {
      const hit = S.stopHit; S.stopHit = null;
      if (hit.inferred) { S.ruleAsk = hit; topic('rule_confirm'); const i = Number(hit.paths[0].split('/')[2]); return { text: `Juste pour être certain : il s’agit bien d’une **${vLabel(i)}**?`, suggestions: ['Oui, c’est bien ça', 'Non, ce n’est pas le bon véhicule'] }; }
      return { text: 'Merci pour ces précisions. Pour cette demande, je dois vous diriger vers un conseiller. Voici pourquoi, et un résumé à vérifier avant l’envoi.', end: hit };
    }
    if (input.kind === 'confirmation_result') {
      if (input.field === 'contact_consent') return input.accepted ? nextStep('Merci! ') : { text: 'Pas de problème. Je peux tout de même répondre à vos questions sur les protections.', suggestions: ['Finalement, j’accepte', 'J’ai une question sur les protections'] };
      return nextStep(input.accepted ? 'Merci, c’est noté. ' : 'D’accord, je n’ai rien changé. ');
    }
    if (input.kind === 'action') {
      if (input.action_id === 'get_quote') { S.phase = 'advise'; topic('offers'); const q = S.quotes[S.quotes.length - 1]; return { text: `Voici vos 3 options pour ${nVeh() > 1 ? 'vos ' + nVeh() + ' véhicules' : 'votre ' + vShort(0)}. Je vous recommande **Plus**, qui inclut l’assistance routière et une voiture de location.`, form: specOffers(q), formTool: 'a2ui.render_offers', suggestions: ['Qu’est-ce que la franchise?', 'Parler à un conseiller'] }; }
      const q = S.quotes[S.quotes.length - 1];
      return { text: `C’est fait : nouvelle version de votre soumission. ${q.delta != null ? `L’offre choisie passe à **${fmtMoney(q.offers.find((o) => o.offer_type === (val('/underwriting/offer_type') || 'plus')).monthly, true)} par mois** (${q.delta < 0 ? '' : '+'}${fmtMoney(q.delta, true)}).` : ''} L’historique est dans votre dossier, onglet Soumissions.`, suggestions: SUGG.after };
    }
    if (input.kind === 'form_submitted') {
      const id = input.surface_id;
      if (id === 'documents') return nextStep(`Merci! J’ai rempli **${input.changes.length} informations** à partir de vos documents. `);
      if (id === 'conducteurs') { S.asked.drivers = true; return nextStep('Merci. '); }
      if (id === 'qui_conduit_quoi') { S.assignDone = true; return nextStep('Parfait, c’est noté. '); }
      if (/^offres_/.test(id)) { topic('after'); return { text: `Excellent choix : l’offre **${OFFER_FR[val('/underwriting/offer_type')] || 'Plus'}**. Je vous envoie le détail par courriel. Voulez-vous l’ajuster?`, suggestions: SUGG.after }; }
      return nextStep(/^vehicule_/.test(id) ? 'Parfait. ' : 'Merci! ');
    }
    const t = input.text; const low = t.toLowerCase();
    if (mem.intent.off_topic) { trace('guardrail', 'off-topic request redirected (content filter + system prompt scope)', { category: 'off_topic', action: 'redirect' }); return { text: 'Je ne peux pas vous aider avec ce sujet, mais je peux répondre à vos questions sur l’assurance auto ou poursuivre votre soumission.', suggestions: S.topic === 'greet' ? SUGG.greet : ['Continuer ma soumission'] }; }
    const pref = mem.session_preferences.answer_mode;
    if (pref && !['greet', 'kb', 'consent'].includes(S.topic)) { const plan = nextStep(pref === 'forms' ? 'C’est noté : je vous propose des formulaires pour la suite. ' : 'C’est noté : on continue par écrit. '); if (pref === 'text' && S.openSurface && S.openSurface.id !== 'qui_conduit_quoi') plan.closeOpen = true; return plan; }
    if (mem.intent.wants_human) return { text: 'Bien sûr. Je prépare un résumé pour un conseiller; vérifiez-le avant l’envoi.', end: { rule_id: null, reason: 'Vous avez demandé à parler à un conseiller.' } };
    const why = WHY.find(([re]) => re.test(low)); if (why) return { text: why[1], suggestions: S.openSurface ? SUGG.form : [] };
    const kb = KB.find(([re]) => re.test(low));
    if (kb && (S.topic === 'kb' || mem.intent.asks_question || S.topic === 'greet')) { const wasKb = S.topic === 'kb'; if (wasKb) topic('greet'); return { text: kb[1], citations: kb[2], kb: { query: t }, suggestions: wasKb ? ['Obtenir une soumission auto', 'Une autre question'] : S.topic === 'greet' ? SUGG.greet : S.openSurface ? SUGG.form : ['Continuer ma soumission'] }; }
    switch (S.topic) {
      case 'greet':
        if (/question|protection|une autre/.test(low)) { topic('kb'); return { text: 'Bien sûr. Sur quelle protection?', suggestions: SUGG.kb }; }
        topic('consent');
        return { text: `Avec plaisir${S.countHint > 1 ? `. J’ai noté **${S.countHint} conducteurs**; on y reviendra` : ''}.\n\nAvant de recueillir vos renseignements : **puis-je les utiliser pour préparer votre soumission?** Je vous demanderai de confirmer à l’écran.`, suggestions: SUGG.consent };
      case 'kb': topic('greet'); return { text: 'Je n’ai pas trouvé cette protection dans notre guide. Voulez-vous en parler à un conseiller?', suggestions: ['Parler à un conseiller', 'Obtenir une soumission auto'] };
      case 'consent':
        if (/pourquoi/.test(low)) return { text: 'Vos réponses servent uniquement à préparer cette soumission et à communiquer avec vous à son sujet. Les réponses protégées, comme le consentement ou les réclamations, sont toujours confirmées deux fois.', suggestions: SUGG.consent };
        if (/non|pas maintenant/.test(low)) return { text: 'Pas de problème. Je peux tout de même répondre à vos questions sur les protections.', suggestions: ['Finalement, j’accepte', 'J’ai une question sur les protections'] };
        if (has('/quote/contact_consent') || (env('/quote/contact_consent') || {}).status === 'pending_confirmation') return { text: 'Merci. Une fenêtre de confirmation s’ouvre.' };
        break;
      case 'docs_offer':
        if (/je ne les ai pas|pas mes|^non\b|aucun|sans documents/.test(low)) { S.noDocs = true; return nextStep('Aucun problème, on continue. '); }
        if (/oui|quelques|seulement|certains|j['’]ai/.test(low)) return formPlan('docs', /quelques|seulement|certains/.test(low) ? 'Parfait. Téléversez ceux que vous avez et indiquez « Je ne l’ai pas » pour les autres. Je vous montre ce que j’ai lu avant de l’utiliser.' : 'Parfait. Téléversez vos documents; je vous montre ce que j’ai lu avant de l’utiliser.', specDocs(), ['Je ne les ai finalement pas']);
        break;
      case 'docs': if (/pas|sans documents/.test(low)) { S.noDocs = true; const p = nextStep('Aucun problème, on continue. '); p.closeOpen = true; return p; } break;
      case 'name': if (has('/quote/first_name')) return nextStep(); return { text: 'Je n’ai pas bien saisi votre nom. Pouvez-vous me l’écrire en entier?' };
      case 'dob': if (has('/quote/date_of_birth')) return nextStep('Merci. '); return { text: 'Je n’ai pas reconnu la date. Écrivez-la par exemple sous la forme jour, mois et année.' };
      case 'email': if (has('/quote/email')) return nextStep('Merci! '); return { text: 'Je n’ai pas reconnu d’adresse courriel. Pouvez-vous la réécrire?' };
      case 'vehicle':
        if (/connais pas/.test(low)) { if (!nVeh()) S.state.vehicles.push({}); return formPlan('vehicle_form', 'Pas de souci, choisissez-le dans la liste.', specVehicle(nVeh() - 1)); }
        if (/plus d['’]un/.test(low)) return { text: 'Parfait, on les ajoute un à la fois. Commençons par le premier : quels sont son année, sa marque et son modèle?', suggestions: ['Je ne connais pas le modèle exact'] };
        if (nVeh() && has(VP(nVeh() - 1, 'vehicle_make'))) return nextStep('Noté. ');
        if (!nVeh()) S.state.vehicles.push({});
        return formPlan('vehicle_form', 'Je n’ai pas reconnu ce véhicule. Choisissez-le dans la liste.', specVehicle(nVeh() - 1));
      case 'vehicle_detail':
        if (has(VP(S.usageFor, 'vehicle_model')) && has(VP(S.usageFor, 'vehicle_year'))) return nextStep('Noté. ');
        return formPlan('vehicle_form', 'Pas de souci, choisissez-le dans la liste.', specVehicle(S.usageFor));
      case 'usage': if (has(VP(S.usageFor, 'usage_type'))) return nextStep('Noté. '); return { text: 'Pour quel usage principal? Agrément, aller au travail ou à l’école, affaires ou agricole?', suggestions: usageSugg() };
      case 'rule_confirm': if (/^non|pas le bon/.test(low)) { S.ruleAsk = null; topic('vehicle'); return { text: 'Pas de problème. Quel véhicule voulez-vous assurer?', suggestions: SUGG.vehicle }; } break;
      case 'drivers': case 'driver_names': {
        const added = mem.state_changes.filter((c) => c.op === 'set' && /\/drivers\/\d+\/driver_name$/.test(c.path));
        if (/seulement moi|moi seulement|juste moi|^non\b|c['’]est tout|personne d['’]autre/.test(low)) { S.asked.drivers = true; return nextStep(); }
        if (added.length) { topic('drivers'); return { text: `Merci, j’ai ajouté ${listFr(added.map((c) => `${c.value} (${(labelFor('relationship_code', val(c.path.replace('driver_name', 'relationship_code'))) || '').toLowerCase()})`))}. Y a-t-il d’autres conducteurs?`, suggestions: SUGG.moreDrivers }; }
        if (/autre personne|plusieurs|oui/.test(low)) { topic('driver_names'); return { text: /plusieurs/.test(low) ? 'Qui sont-ils? Donnez-moi leur prénom, leur lien avec vous et leur âge s’ils ont moins de 25 ans.' : 'Quel est son prénom et son lien avec vous? Indiquez aussi son âge si cette personne a moins de 25 ans.', suggestions: [] }; }
        return { text: 'Je n’ai pas bien compris. Donnez-moi le prénom de chaque conducteur et son lien avec vous.' };
      }
      case 'second_vehicle':
        if (/^non|un seul/.test(low)) return nextStep('D’accord, un seul véhicule. ');
        if (mem.state_changes.some((c) => /vehicle_make$/.test(c.path))) return nextStep(`Parfait, j’ai ajouté la **${vLabel(nVeh() - 1)}**. `);
        if (/oui|autre/.test(low)) { if (S.prefs.answer_mode === 'forms') { S.state.vehicles.push({}); return formPlan('vehicle_form', 'Ajoutez votre deuxième véhicule.', specVehicle(nVeh() - 1)); } topic('second_which'); return { text: 'Lequel? Indiquez son année, sa marque et son modèle.', suggestions: ['Je ne connais pas le modèle exact'] }; }
        break;
      case 'second_which':
        if (mem.state_changes.some((c) => /vehicle_make$/.test(c.path))) return nextStep(`Parfait, j’ai ajouté la **${vLabel(nVeh() - 1)}**. `);
        S.state.vehicles.push({}); return formPlan('vehicle_form', 'Pas de souci, choisissez-le dans la liste.', specVehicle(nVeh() - 1));
      case 'history_chat':
        if (/oui|au moins/.test(low)) return formPlan('history', 'D’accord. Précisez dans ce court formulaire.', specHistory());
        if (/^non|aucun/.test(low)) return { text: 'Merci. Une fenêtre de confirmation s’ouvre : ces réponses sont protégées.' };
        break;
      default: break;
    }
    if (S.openSurface) return { text: mem.state_changes.length ? 'C’est noté, je l’ai ajouté au formulaire.' : 'Prenez votre temps. Vous pouvez aussi répondre ici, je remplirai le formulaire pour vous.', suggestions: SUGG.form };
    if (/continuer/.test(low)) return nextStep();
    return nextStep('Bien noté. ');
  }

  /* --------------------------------------------------------- the turn loop */
  const meters = { turns: 0, two: 0, cached: 0, input: 0, ttft: [] };
  function resetMeters() { Object.assign(meters, { turns: 0, two: 0, cached: 0, input: 0, ttft: [] }); const el = $('#meters'); if (el) el.innerHTML = '<span>No turns yet</span>'; }
  function updateMeters(calls, cached, input, ttft) {
    meters.turns++; if (calls === 2) meters.two++; meters.cached += cached; meters.input += input; meters.ttft.push(ttft);
    const p95 = meters.ttft.slice().sort((a, b) => a - b)[Math.floor(meters.ttft.length * 0.95)] || ttft;
    const html = `<span><b>${meters.turns}</b> turns</span><span><b>${Math.round((meters.two / meters.turns) * 100)}%</b> at 2 model calls</span><span><b>${Math.round((meters.cached / meters.input) * 100)}%</b> cached input</span><span>TTFT p95 <b>${p95} ms</b></span><span>static prompt + tools <b>~${STATIC_PREFIX.toLocaleString('en-CA')}</b> / 30,000 tokens</span>`;
    if (EMBED) { parent.postMessage({ proto: 'meters', html }, '*'); return; }
    const el = $('#meters'); if (el) el.innerHTML = html;
  }
  const setComposer = (on) => { $('#send').disabled = !on; };
  function countFilled() { let n = 0; const walk = (o) => { if (!o || typeof o !== 'object') return; if ('value' in o && 'source' in o) { n++; return; } Object.values(o).forEach(walk); }; walk(S.state); return n; }
  async function runTurn(input) {
    if (S.busy) return;
    if (input.kind === 'message' && S.topic === 'quote_ready' && /^(oui|calcul|vas-y|d['’]accord|obtenir)/i.test(input.text)) input = { kind: 'action', action_id: 'get_quote', text: input.text };
    S.busy = true; setComposer(false); setSugg([]);
    S.turn++; const turn = S.turn; const t0 = now();
    if (input.kind === 'message' || input.kind === 'action') userBubble(input.text);
    if (input.kind === 'form_submitted') userBubble(`Formulaire envoyé : ${input.chip}`);
    trace('turn.started', `turn ${turn} · input.kind = ${input.kind}`, { type: 'turn.started', session_id: S.sessionId, turn, data: { phase: S.phase, answer_mode: S.prefs.answer_mode } }, 't-turn');
    trace('invocation', 'BFF → hosted agent quote-agent (invocations, SSE)', { method: 'POST', url: '{project}/agents/quote-agent/endpoint/protocols/invocations?api-version=v1&agent_session_id=' + S.sessionId, headers: { authorization: 'Bearer <BFF managed identity token>', traceparent: '00-<trace-id>-<span-id>-01' }, body: { input: Object.assign({}, input, { changes: undefined }) } });
    cosmos('events', { type: input.kind === 'message' ? 'user.message' : input.kind, data: input.text ? { text: input.text } : { surface_id: input.surface_id, action_id: input.action_id } });
    /* BFF work before the model calls: commit form values (and confirm protected ones), confirm a rule question, run pre-executed actions */
    if (input.kind === 'form_submitted') { const pend = merge(input.changes, input.surface_id === 'documents' ? 'document' : 'form'); if (pend.length) await confirmDeterministic(pend, 'Vous venez de répondre dans le formulaire.'); }
    if (input.kind === 'message' && S.topic === 'rule_confirm' && S.ruleAsk && /^oui|c['’]est bien/i.test(input.text)) {
      for (const p of S.ruleAsk.paths) { const e = env(p); if (e) { e.status = 'confirmed'; flash.add(p); } }
      trace('confirm.accepted', `customer confirmed ${S.ruleAsk.paths.join(', ')} for rule ${S.ruleAsk.rule_id}`, { rule_id: S.ruleAsk.rule_id, paths: S.ruleAsk.paths });
      S.ruleAsk = null; afterCommit('confirmation');
    }
    if (input.kind === 'action' && input.action_id === 'get_quote') createQuote('Première soumission', null, 'pre-executed by orchestrator (button)');
    if (input.kind === 'action' && input.action_id.startsWith('modify:')) {
      const [k, v] = input.action_id.slice(7).split('=');
      const opts = k === 'deductible' ? { deductible: v } : { add_ons: [...new Set([...(val('/underwriting/add_ons') || []), v])] };
      if (opts.deductible) merge([{ path: '/underwriting/deductible', value: String(v) }], 'system');
      if (opts.add_ons) merge([{ path: '/underwriting/add_ons', value: opts.add_ons }], 'system');
      createQuote(k === 'deductible' ? `Franchise portée à ${fmtMoney(v)}` : 'Pardon d’accident ajouté', opts, 'pre-executed by orchestrator (button)');
    }
    const shell = botShell();
    const histIn = S.historyTokens; const mainIn = STATIC_PREFIX + histIn + 300 + countFilled() * 18;
    const cached = turn <= 1 ? 0 : Math.min(mainIn - 200, STATIC_PREFIX + Math.round(histIn * 0.95));
    trace('model.call', `main agent · MAIN_MODEL_DEPLOYMENT · prompt_cache_key quote-main:v13:${S.sessionId}`, { agent: 'main', operation: 'chat', stream: true, input_tokens: mainIn, cached_tokens: cached, tools_in_context: 13, volatile_state: { phase: S.phase, answer_mode: S.prefs.answer_mode, rule_hit: S.stopHit ? S.stopHit.rule_id : null } });
    trace('model.call', 'memory agent · MEMORY_MODEL_DEPLOYMENT · response_format=MemoryAgentOutput (strict)', { agent: 'memory', operation: 'chat', stream: false, input: { user_message: input.text || `<${input.kind} summary>`, last_assistant_message: '<…>', state: '<compact>' } });
    const mem = memoryAgent(input);
    const ttft = rnd(380, 820);
    await wait(Math.round(ttft * 0.6));
    const pending = applyMemory(mem);
    const plan = decide(input, mem);
    let extra = 0;
    if (plan.kb) { toolCall('knowledge.knowledge_base_retrieve', { query: plan.kb.query, top: 3 }, { results: (plan.citations || []).map((c, i) => ({ id: 'kb_' + (i + 1), title: c, score: 0.82 - i * 0.07 })) }, 'narrate', 'main agent'); trace('model.call', 'main agent · narrate the knowledge result (extra call, counted)', { agent: 'main', reason: 'narrate tool' }); extra = 1; }
    await streamInto(shell, plan.text, plan.citations, Math.round(ttft * 0.4));
    const outTok = Math.round(plan.text.length / 4) + 40;
    S.historyTokens += Math.round((input.text || '').length / 4) + outTok + 60;
    if (plan.closeOpen && S.openSurface) { const sf = S.openSurface; toolCall('a2ui.close_form', { surface_id: sf.id }, { status: 'closed' }, 'terminal', 'main agent'); await closeForm(sf, 'dismissed'); }
    if (plan.form) { const b = plan.form(false); toolCall(plan.formTool || 'a2ui.render_form', b.spec, { surface_id: b.spec.form_id, status: 'shown', 'model sees': '{ surface_id, status: shown } only' }, 'terminal', 'main agent'); await openForm(plan.form, shell, { origin: 'agent' }); }
    const phaseNow = S.phase;
    if (phaseNow !== S.lastPhase) { toolCall('ui.set_phase', { phase: phaseNow }, { ok: true }, 'terminal', 'main agent'); S.lastPhase = phaseNow; }
    if (plan.suggestions && plan.suggestions.length) {
      const v = validateSuggestions(plan.suggestions);
      toolCall('ui.suggest_replies', { items: plan.suggestions.map((x) => x.label || x) }, { ok: true, bff_validation: { kept: v.kept.length, dropped: v.dropped } }, 'terminal', 'main agent');
      setSugg(v.kept);
    }
    const calls = 2 + extra;
    trace('turn.completed', `turn ${turn} · ${calls} model calls${calls > 2 ? ' (narrate-tool exception)' : ''} · cached ${mainIn ? Math.round((cached / mainIn) * 100) : 0}% · TTFT ${ttft} ms`, { type: 'turn.completed', data: { model_calls: calls, usage: { input: mainIn, cached_input: cached, output: outTok }, ttft_ms: ttft, duration_ms: Math.round(now() - t0) } }, calls > 2 ? 't-warn' : 't-turn');
    updateMeters(calls, cached, mainIn, ttft);
    S.busy = false; setComposer(true);
    if (plan.end) await endOfForm(plan.end, shell);
    if (pending.length) {
      const consent = pending.some((p) => p.path === '/quote/contact_consent');
      const ok = await confirmDeterministic(pending, consent ? 'Vous avez accepté dans la conversation que vos renseignements servent à cette soumission.' : 'L’agent a compris cette réponse dans votre message.');
      if (consent) { sysNote(ok ? 'Consentement confirmé (2 étapes)' : 'Consentement refusé'); return runTurn({ kind: 'confirmation_result', accepted: ok, field: 'contact_consent' }); }
      return runTurn({ kind: 'confirmation_result', accepted: ok, field: A.keyOf(pending[0].path) });
    }
    if (S.stopHit && !S.ended) return runTurn({ kind: 'confirmation_result', accepted: true, field: 'rules' });
  }

  /* -------------------------------------------------------------- forms */
  let lastSvc = '';
  const services = A.makeServices((call) => {
    if (call.name.startsWith('document')) return; /* traced by the host */
    const k = call.name + JSON.stringify(call.args); if (k === lastSvc) return; lastSvc = k;
    if (S.quietSvc) return; /* initial render: values came with the FormSpec */
    agentFunction(call.fn, call.name, call.args, call.result);
  });
  const PENCIL = '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4"><path d="M4 20h4L19 9l-4-4L4 16v4z"/></svg>';
  function setChip(chip, state) {
    if (!chip) return;
    const label = esc(chip.dataset.label);
    chip.dataset.state = state;
    chip.classList.toggle('done', state === 'done' || state === 'sent');
    chip.disabled = state === 'open' || state === 'sent';
    chip.innerHTML = state === 'open' ? `<i></i>${label}<span class="chip-st">ouvert</span>`
      : state === 'done' ? `${ICON.ok}${label}<span class="chip-act">${PENCIL}<span class="d-only">Voir ou modifier</span><span class="p-only">Modifier</span></span>`
      : state === 'sent' ? `${ICON.ok}${label}<span class="chip-st">envoyé</span>`
      : `<i></i>${label}<span class="chip-act">Rouvrir</span>`;
  }
  async function openForm(make, shell, opts) {
    opts = opts || {};
    const b = make(!!opts.edit);
    if (S.openSurface) { const old = S.openSurface; setChip(old.chip, old.edit ? 'done' : 'closed'); S.openSurface = null; }
    const msgs = A.compile(b.spec);
    const sf = new A.Surface(b.spec.form_id, host);
    sf.edit = !!opts.edit; sf.make = make; sf.sample = b.sample; sf.chipLabel = b.chip;
    for (const m of msgs) { const k = Object.keys(m).find((x) => x !== 'version'); trace('a2ui.message', `${k} → ${b.spec.form_id}${k === 'createSurface' ? ` (${m.createSurface.components.length} components + dataModel)` : ''}${opts.origin === 'bff' ? ' · rendered by the BFF, no model call' : opts.edit ? ' · reopened by the BFF in edit mode, no model call' : ''}`, m); sf.apply(m); }
    S.openSurface = sf; S.openerAvatar = shell.av;
    S.quietSvc = true; const card = sf.render(); S.quietSvc = false;
    if (b.cls) card.classList.add(b.cls);
    if (sf.edit) { const p = card.querySelector('.form-foot .btn.primary'); if (p && !b.bff) p.textContent = 'Enregistrer'; }
    if (opts.chip) { sf.chip = opts.chip; setChip(sf.chip, 'open'); }
    else { const chip = h('button', { class: 'form-chip', type: 'button', 'data-label': 'Formulaire : ' + b.chip }); chip.onclick = () => reopen(chip, make, b.bff); shell.b.append(chip); sf.chip = chip; setChip(chip, 'open'); }
    down();
    await stage.open(sf);
  }
  async function reopen(chip, make, isFin) {
    if (S.busy || S.openSurface || S.sent || (S.ended && !isFin)) return;
    if (S.mode === 'form') setMode('chat');
    trace('ui.surface_reopen', `client → BFF: reopen ${chip.dataset.label}`, { type: 'ui.surface_reopen', data: { chip: chip.dataset.label, mode: 'edit' } });
    const msg = chip.closest('.msg');
    await openForm(make, { av: msg.querySelector('.avatar'), b: msg.querySelector('.bubble') }, { edit: true, chip, origin: isFin ? 'bff' : undefined });
  }
  async function closeForm(sf, how) {
    trace('a2ui.message', `deleteSurface → ${sf.id}`, { version: '1.0', deleteSurface: { surfaceId: sf.id } });
    const s = scroller();
    if (S.openerAvatar) { const r = S.openerAvatar.getBoundingClientRect(), sr = s.getBoundingClientRect(); if (!isPhone() && (r.top < sr.top || r.bottom > sr.bottom)) S.openerAvatar = null; }
    await stage.close(sf);
    if (S.openSurface === sf) S.openSurface = null;
    setChip(sf.chip, how === 'sent' ? 'sent' : how === 'dismissed' ? (sf.edit ? 'done' : 'closed') : 'done');
  }
  function toChanges(sf, values) {
    let ch = Object.entries(values || {}).filter(([p]) => !p.startsWith('/_') && !/handoff_/.test(p)).map(([path, value]) => ({ path, value }));
    if (sf.id === 'conducteurs' || sf.id === 'full_form') {
      const rows = {}; ch = ch.filter((c) => { const m = c.path.match(/^\/drivers\/(\d+)\/(\w+)$/); if (!m || m[1] === '0') return true; (rows[m[1]] = rows[m[1]] || {})[m[2]] = c.value; return false; });
      const keep = Object.keys(rows).sort((a, b) => a - b).map((k) => rows[k]).filter((r) => r.driver_name);
      S.state.drivers.length = Math.max(1, Math.min(S.state.drivers.length, 1));
      keep.forEach((r, j) => { S.state.drivers.push({}); for (const [k, v] of Object.entries(r)) ch.push({ path: DP(j + 1, k), value: v }); });
      const asg = val('/household/driver_vehicle_assignments'); if (asg) env('/household/driver_vehicle_assignments').value = asg.filter((a) => a.driver_index < keep.length + 1);
    }
    return ch;
  }
  async function saveEdit(sf, values) {
    const changes = toChanges(sf, values);
    const pend = merge(changes, sf.id === 'documents' ? 'document' : 'form');
    if (pend.length) await confirmDeterministic(pend, 'Vous avez modifié une réponse protégée.');
    if (sf.id === 'qui_conduit_quoi') S.assignDone = true;
    await closeForm(sf, 'saved');
    sysNote(`Modifications enregistrées : ${sf.title}.`);
    S.historyTokens += 20; trace('history.note', 'system note appended to history for the next turn (no model call now)', { role: 'system', content: `Customer edited ${sf.id}: ${changes.map((c) => c.path).join(', ')}` });
    await handleStopOutsideTurn();
  }
  async function handleStopOutsideTurn() {
    if (!S.stopHit || S.ended) return;
    const hit = S.stopHit; S.stopHit = null;
    if (hit.inferred) { S.ruleAsk = hit; topic('rule_confirm'); const sh = botShell(); trace('ui.notice', 'BFF template message (rule confirmation), no model call'); await streamInto(sh, `Juste pour être certain : il s’agit bien d’une **${vLabel(Number(hit.paths[0].split('/')[2]))}**?`); setSugg(['Oui, c’est bien ça', 'Non, ce n’est pas le bon véhicule']); return; }
    const sh = botShell(); trace('ui.notice', 'BFF template message (end of form), no model call');
    await streamInto(sh, 'Pour cette demande, je dois vous diriger vers un conseiller. Voici pourquoi, et un résumé à vérifier avant l’envoi.');
    await endOfForm(hit, sh);
  }
  /* End of form (D19): the BFF closes open surfaces and renders the hand-off surface itself. */
  async function endOfForm(hit, shell) {
    if (S.ended && S.openSurface && S.openSurface.id === 'fin_conseiller') return;
    while (S.busy) await wait(80);
    if (S.mode === 'form') setMode('chat');
    if (S.openSurface) await closeForm(S.openSurface, 'dismissed');
    S.lastHit = hit;
    trace('handoff.prepare', `end-of-form view${hit.rule_id ? ' for rule ' + hit.rule_id : ' (customer asked for an advisor)'}`, { rule_id: hit.rule_id, reason: hit.reason });
    await openForm(specFin(hit), shell, { origin: 'bff' });
    setSugg([]);
  }
  async function finAction(sf, name) {
    if (name === 'dismiss' || name === 'edit') { S.ended = false; await closeForm(sf, 'dismissed'); sysNote('Rouvrez un formulaire dans la conversation pour modifier vos réponses. Le résumé sera mis à jour.'); setSugg(['Continuer ma soumission', 'Parler à un conseiller']); return; }
    if (name === 'send') {
      const errs = sf.validate(); if (errs.length) { sf.errSummary.textContent = `${errs.length} champ${errs.length > 1 ? 's' : ''} à corriger`; return; }
      const ref = 'CNS-' + rnd(100000, 999999);
      const contact = { first_name: sf.get('/quote/first_name'), last_name: sf.get('/quote/last_name'), phone_number: sf.get('/quote/phone_number'), email: sf.get('/quote/email') };
      merge(Object.entries(contact).filter(([, v]) => v).map(([k, v]) => ({ path: '/quote/' + k, value: v })), 'form');
      trace('handoff.created', `${ref} · advisor callback with the customer's edited summary`, { type: 'handoff.created', data: { reference: ref, rule_id: (S.lastHit && S.lastHit.rule_id) || null, summary: sf.get('/quote/handoff_summary'), contact: { phone_number: '<redacted in logs>', email: '<redacted in logs>' }, consent_to_transmit: true } });
      cosmos('handoffs', { reference: ref, rule_id: (S.lastHit && S.lastHit.rule_id) || null, status: 'queued' });
      S.sent = true;
      sf.bodyEl.innerHTML = `<div class="done-box"><div class="big">${ICON.ok.replace('width="16" height="16"', 'width="34" height="34"')}</div><h3>C’est envoyé!</h3><p>Référence : <b>${ref}</b></p><p style="color:var(--ink-2)">Un conseiller vous appellera d’ici 1 jour ouvrable au <b>${esc(contact.phone_number || 'numéro fourni')}</b>. Votre résumé lui a été transmis.</p></div>`;
      sf.footEl.innerHTML = ''; const close = h('button', { class: 'btn primary', type: 'button' }, 'Fermer'); sf.footEl.append(h('div', { class: 'spacer' }), close);
      close.onclick = async () => { await closeForm(sf, 'sent'); $$('.form-chip').forEach((c) => { if (c !== sf.chip) { c.disabled = true; c.classList.add('locked'); } }); sysNote('Dossier transmis à un conseiller.'); setSugg([]); };
    }
  }
  const host = {
    services,
    demoFill(sf) {
      trace('demo', `demo fill: sample answers for ${sf.id}`);
      if (sf.sample === 'readAll') { if (sf.readAll) sf.readAll(); return; }
      if (sf.sample === 'board') {
        const steps = []; for (let v = 0; v < nVeh(); v++) steps.push([Math.min(v, nDrv() - 1), v, 'p']);
        for (let p = 0; p < nDrv(); p++) if (p >= nVeh()) steps.push([p, 0, 's']);
        if (nDrv() > 1 && nVeh() > 1) steps.push([1, 0, 's']);
        steps.forEach(([p, v, k], i) => setTimeout(() => sf.placeOnBoard && sf.placeOnBoard(p, v, k), i * 260));
        return;
      }
      for (const [p, v] of Object.entries(sf.sample || {})) A.ptrSet(sf.data, p, v);
      S.quietSvc = true; sf.rerender(); S.quietSvc = false;
    },
    onChange() {},
    async onFieldAction(sf, a) {
      if (a.kind !== 'document') return null;
      trace('a2ui.callAgentFunction', `extractDocument ← ${sf.id}`, { version: '1.0', callAgentFunction: { surfaceId: sf.id, functionCallId: 'fc_' + (++fcSeq), callFunction: { '@call': 'extractDocument', catalogId: A.CATALOG_ID, args: { purpose: a.purpose, file: '<blob ref, stored in Blob Storage>' } } } });
      trace('document.agent', `BFF → document agent (Foundry prompt agent, gpt-5.6-luna, structured output) · ${a.purpose}`, { agent: 'document-agent', model: 'gpt-5.6-luna', response_format: 'DocumentExtraction (strict)', counted_in_turn: false });
      await wait(rnd(1100, 1500));
      const r = services.extractDocument(a.purpose);
      trace('a2ui.agentFunctionResponse', `extractDocument → ${sf.id} (${Object.keys(r.fields || {}).length} fields)`, { version: '1.0', agentFunctionResponse: { value: r } });
      if (!S.docsRead.includes(a.purpose)) S.docsRead.push(a.purpose);
      return r;
    },
    onAction(sf, name, payload) {
      trace('a2ui.action', `${sf.id} · ${name}${sf.edit ? ' (edit mode)' : ''}`, { version: '1.0', action: { surfaceId: sf.id, name, context: payload && payload.values ? { values: payload.values } : {} } });
      if (sf.id === 'full_form') return fullFormAction(sf, name, payload);
      if (sf.id === 'fin_conseiller') return finAction(sf, name);
      if (S.busy) return;
      if (name === 'dismiss') { closeForm(sf, 'dismissed').then(() => { if (!sf.edit) { sysNote('Formulaire fermé. Vous pouvez répondre ici, ou le rouvrir depuis la conversation.'); setSugg(['Continuer ma soumission']); } }); return; }
      if (name === 'nodocs') { closeForm(sf, 'dismissed').then(() => send('Continuer sans documents')); return; }
      if (sf.edit) { saveEdit(sf, payload.values); return; }
      const changes = toChanges(sf, payload.values);
      closeForm(sf, 'submitted').then(() => runTurn({ kind: 'form_submitted', surface_id: sf.id, chip: sf.chipLabel, changes }));
    }
  };

  /* ---------------------------------------- full form mode (D20): one long prefilled surface */
  let fullSf = null;
  function specFull() {
    const nr = { required: false };
    const fields = [{ widget: 'section', label: 'Vous' }, fld('/quote/first_name', nr), fld('/quote/last_name', nr), fld('/quote/date_of_birth', nr), fld('/quote/email', nr), fld('/quote/phone_number', nr)];
    for (let i = 0; i < Math.max(1, nVeh()); i++) fields.push({ widget: 'section', label: `Véhicule ${i + 1}` }, { key: VP(i, 'vehicle_year'), widget: 'ymm', required: i < nVeh() && has(VP(i, 'vehicle_make')), prefill: ymmPrefill(i) }, fld(VP(i, 'usage_type'), { widget: 'chips', required: false }));
    fields.push({ widget: 'section', label: 'Autres conducteurs' }, driversRepeater());
    fields.push({ widget: 'section', label: 'Antécédents' }, fld('/underwriting/has_claims', { widget: 'toggle', required: false }), fld('/underwriting/has_convictions_in_past_three_years', { widget: 'toggle', required: false }), fld('/underwriting/was_denied_or_cancelled', { widget: 'toggle', required: false }));
    return { form_id: 'full_form', title: 'Votre soumission complète', layout: 'two_column', entity: { type: 'quote' }, actions: [{ id: 'back', label: 'Retour à la conversation', style: 'secondary' }, { id: 'submit', label: 'Enregistrer et continuer', style: 'primary' }], fields };
  }
  function setMode(m) {
    if (m === 'form' && (S.busy || S.sent)) return;
    S.mode = m;
    $('#m-chat').setAttribute('aria-pressed', String(m === 'chat')); $('#m-form').setAttribute('aria-pressed', String(m === 'form'));
    const ff = $('#fullform');
    if (m === 'form') {
      const spec = specFull();
      trace('ui.surface_reopen', 'client → BFF: full form (reserved id full_form)', { type: 'ui.surface_reopen', data: { surface_id: 'full_form', mode: 'edit' } });
      const msgs = A.compile(spec);
      fullSf = new A.Surface(spec.form_id, host); fullSf.sample = {};
      for (const msg of msgs) { trace('a2ui.message', `createSurface → ${spec.form_id} (full form, prefilled with ${countFilled()} known values) · rendered by the BFF, no model call`, msg); fullSf.apply(msg); }
      S.quietSvc = true; const card = fullSf.render(); S.quietSvc = false;
      ff.innerHTML = '<div class="inner"><h1>Votre soumission</h1><p class="lead">Tout ce que vous avez dit ou téléversé est déjà rempli. Les champs « Tiré de la conversation » sont à vérifier.</p></div>';
      $('.inner', ff).append(card); ff.hidden = false;
      if (!reduce()) { ff.animate([{ opacity: 0, transform: 'translateY(16px)' }, { opacity: 1, transform: 'none' }], { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' }); $$('.a2-col > *', card).forEach((c, i) => c.animate([{ opacity: 0, transform: 'translateY(10px)' }, { opacity: 1, transform: 'none' }], { duration: 300, delay: 60 + Math.min(i, 14) * 40, fill: 'backwards' })); }
    } else if (!ff.hidden) {
      ff.animate([{ opacity: 1 }, { opacity: 0 }], { duration: reduce() ? 1 : 180 }).finished.then(() => { if (S.mode === 'chat') { ff.hidden = true; ff.innerHTML = ''; fullSf = null; } });
    }
  }
  async function fullFormAction(sf, name, payload) {
    if (name === 'back' || name === 'dismiss') { setMode('chat'); return; }
    if (name !== 'submit') return;
    const changes = toChanges(sf, payload.values);
    for (const c of changes) { const m = c.path.match(/^\/vehicles\/(\d+)\//); if (m) while (nVeh() <= Number(m[1])) S.state.vehicles.push({}); }
    const pend = merge(changes, 'form');
    if (pend.length) await confirmDeterministic(pend, 'Vous avez répondu dans le formulaire complet.');
    if (nDrv() > 1) S.asked.drivers = true;
    setMode('chat'); sysNote('Formulaire complet enregistré. Votre dossier est à jour.');
    S.historyTokens += 20; trace('history.note', 'system note appended to history for the next turn (no model call now)', { role: 'system', content: `Customer saved the full form (${changes.length} values)` });
    await handleStopOutsideTurn();
    if (!S.ended && !S.busy) setSugg(['Continuer ma soumission']);
  }

  /* -------------------------------------------------------------- layout */
  let stageBottom = 0;
  function layout(open) {
    const ws = $('#ws'); const W = ws.clientWidth; const chat = $('#chat'); const st = $('#stage');
    document.body.classList.toggle('form-open', !!open);
    if (isPhone()) {
      /* phone: the conversation slides left under the form; suggestions and the composer stay usable below it */
      chat.style.left = '0px'; chat.style.width = W + 'px';
      chat.classList.toggle('pushed', !!open);
      if (open) { const cr = chat.getBoundingClientRect(), wr = ws.getBoundingClientRect(); stageBottom = Math.round(wr.bottom - cr.bottom) + $('#sugg').offsetHeight + $('.composer').offsetHeight + 2; st.style.left = '8px'; st.style.width = (W - 16) + 'px'; st.style.bottom = stageBottom + 'px'; st.hidden = false; }
      else { st.hidden = true; hidePeek(); }
      setTimeout(down, 520); return;
    }
    chat.classList.remove('pushed'); st.style.bottom = '';
    const left0 = 72, pad = 20, avail = W - left0, narrow = avail < 820;
    const cw = open ? (narrow ? avail - 2 * pad : Math.max(340, Math.min(440, Math.round(avail * 0.36)))) : Math.min(720, avail - 2 * pad);
    const cl = open ? left0 + pad : left0 + Math.round((avail - cw) / 2);
    chat.style.width = cw + 'px'; chat.style.left = cl + 'px';
    if (open) { const sl = narrow ? left0 + pad : cl + cw + 24; st.style.left = sl + 'px'; st.style.width = (W - sl - pad) + 'px'; st.hidden = false; } else st.hidden = true;
    setTimeout(down, 520);
  }
  const stage = new A.Stage({
    stageEl: $('#stage'),
    anchor: () => (S.openerAvatar && S.openerAvatar.isConnected ? S.openerAvatar : $$('#scroll .msg.bot .avatar').pop()),
    layout: (open) => layout(open), layoutMs: 500, autofocus: !isPhone(),
    onCloseStart: () => { if (isPhone()) { $('#chat').classList.remove('pushed'); hidePeek(); } },
    anchorShift: () => (isPhone() ? { x: new DOMMatrix(getComputedStyle(scroller()).transform).m41 } : { x: 0 })
  });

  /* --------------------------------------------------------------- boot */
  async function greet() {
    S.busy = true; setComposer(false);
    const sh = botShell();
    trace('ui.greeting', 'static greeting rendered by the BFF, no model call', { type: 'assistant.message', data: { template: 'greeting.fr-CA' } });
    await streamInto(sh, 'Bonjour! Je suis votre conseiller virtuel Desjardins Assurances. Je prépare votre soumission auto avec vous, surtout en conversation, avec de courts formulaires au besoin.\n\nQue souhaitez-vous faire?', null, 500);
    S.busy = false; setComposer(true);
    const v = validateSuggestions(SUGG.greet); setSugg(v.kept);
  }
  function send(text) { const t = (text != null ? text : $('#input').value).trim(); if (!t || S.busy) return; if (text == null) $('#input').value = ''; setSugg([]); runTurn({ kind: 'message', text: t }); }
  function reset() {
    $$('.morph,.modal-back,.tip,.dragghost').forEach((e) => e.remove());
    fresh(); T0 = now(); dOpen = false; $('#dossier').classList.remove('open'); $('#dtab').setAttribute('aria-expanded', 'false');
    stage.current = null; stage.busy = Promise.resolve();
    $('#scroll').innerHTML = `<div class="welcome"><span class="avatar">${ICON.botface}</span><h2>Bonjour!</h2><p>Je suis votre conseiller virtuel Desjardins Assurances.</p></div>`;
    $('#stage').innerHTML = ''; S.mode = 'form'; setMode('chat'); $('#fullform').hidden = true; $('#fullform').innerHTML = '';
    $('#qnum').textContent = S.quoteNo; selectTab('states');
    const tl = traceList(); if (tl) tl.innerHTML = ''; const tc = $('#trace-count'); if (tc) tc.textContent = ''; resetMeters();
    setSugg([]); renderStates(); renderQuotes(); updateProgress(); layout(false); greet();
  }

  /* --------------------------------- demo controls: trace, device, replay, end-of-form rule */
  const SB_ICONS = '<svg width="18" height="12" viewBox="0 0 18 12"><rect x="0" y="8" width="3" height="4" rx="1"/><rect x="5" y="5.5" width="3" height="6.5" rx="1"/><rect x="10" y="3" width="3" height="9" rx="1"/><rect x="15" y="0" width="3" height="12" rx="1"/></svg><svg width="16" height="12" viewBox="0 0 16 12"><path d="M8 11.5 10.6 8.6a3.7 3.7 0 0 0-5.2 0z"/><path d="M8 4.8c1.9 0 3.6.7 4.9 1.9l1.4-1.5A8.9 8.9 0 0 0 8 2.6 8.9 8.9 0 0 0 1.7 5.2l1.4 1.5A7 7 0 0 1 8 4.8z"/></svg><svg width="26" height="12" viewBox="0 0 26 12"><rect x=".5" y=".5" width="22" height="11" rx="3" fill="none" stroke="currentColor" opacity=".45"/><rect x="2" y="2" width="17" height="8" rx="1.8"/><rect x="23.6" y="4" width="1.6" height="4" rx=".8" opacity=".45"/></svg>';
  let device = 'desktop';
  const frame = () => $('#phones iframe');
  function fitPhones() {
    const box = $('#phones'); if (!box || box.hidden) return; const p = $('.phone', box); if (!p) return;
    const k = Math.min(1, (box.clientHeight - 64) / 868, (box.clientWidth - 48) / 414);
    const hold = $('.holder', p); hold.style.width = Math.floor(414 * k) + 'px'; hold.style.height = Math.floor(868 * k) + 'px'; $('.frame', p).style.transform = `scale(${k})`;
  }
  function setDevice(d) {
    device = d; document.body.classList.toggle('dev-phone', d === 'phone'); $('#phones').hidden = d !== 'phone';
    $$('#dseg button').forEach((x) => x.setAttribute('aria-pressed', String(x.dataset.d === d)));
    const tl = traceList(); if (tl) tl.innerHTML = ''; resetMeters(); T0 = now();
    if (d === 'phone') {
      const box = $('#phones'); box.innerHTML = '';
      const p = h('figure', { class: 'phone' }, `<div class="holder"><div class="frame"><div class="screen"><iframe title="Soumission auto sur téléphone"></iframe></div><div class="sb"><b>9:41</b><span>${SB_ICONS}</span></div><i class="island"></i><i class="home"></i></div></div><figcaption>Variation C sur téléphone (390 × 844)</figcaption>`);
      box.append(p); $('iframe', p).srcdoc = SRC_HTML.replace('<html', '<html data-embed="phone"'); fitPhones();
    } else { $('#phones').innerHTML = ''; reset(); }
  }
  function toggleTrace(force) {
    const open = force != null ? force : !$('#trace').classList.contains('open');
    $('#trace').classList.toggle('open', open); document.body.classList.toggle('dev', open); document.body.classList.toggle('trace-open', open);
    $('#btn-trace').setAttribute('aria-pressed', String(open)); setTimeout(fitPhones, 320);
  }
  function lambo() {
    if (S.busy || S.ended || S.sent) return;
    if (!has('/quote/contact_consent')) { trace('demo', 'demo shortcut: consent recorded so the rule demo can start'); commit('/quote/contact_consent', true, 'confirmed', 1); S.asked.docs = true; S.phase = 'gather'; renderStates(); }
    if (!['vehicle', 'second_vehicle', 'second_which'].includes(S.topic)) topic('vehicle');
    send('Je conduis une Lamborghini Huracán 2023');
  }

  /* --------------------------------------------------------------- wiring */
  $('#brand-mark').innerHTML = ICON.brand;
  $('#send').onclick = () => send();
  $('#input').addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  $('#dtab').onclick = () => togglePanel();
  $('#dclose').onclick = () => togglePanel(false);
  $$('.dtabs button').forEach((b) => { b.onclick = () => selectTab(b.dataset.tab); });
  $('#m-chat').onclick = () => setMode('chat');
  $('#m-form').onclick = () => setMode('form');
  $('#h-agent').onclick = () => { if (!S.busy && !S.ended && !S.sent) send('Parler à un conseiller'); };
  $('#btn-trace').onclick = () => toggleTrace();
  $('#trace-close').onclick = () => toggleTrace(false);
  $('#btn-reset').onclick = () => { if (device === 'phone') { const f = frame(); if (f) f.contentWindow.postMessage({ proto: 'reset' }, '*'); const tl = traceList(); if (tl) tl.innerHTML = ''; resetMeters(); T0 = now(); } else reset(); };
  $('#lambo').onclick = () => { if (device === 'phone') { const f = frame(); if (f) f.contentWindow.postMessage({ proto: 'lambo' }, '*'); } else lambo(); };
  $$('#dseg button').forEach((b) => { b.onclick = () => { if (b.dataset.d !== device) setDevice(b.dataset.d); }; });
  addEventListener('message', (e) => {
    const d = e.data; if (!d || !d.proto) return;
    if (EMBED && e.source === parent) { if (d.proto === 'reset') reset(); if (d.proto === 'lambo') lambo(); return; }
    if (!EMBED && frame() && e.source === frame().contentWindow) { if (d.proto === 'trace') traceRow(d.type, d.label, d.detail, d.cls, d.ms); if (d.proto === 'meters') { const el = $('#meters'); if (el) el.innerHTML = d.html; } }
  });
  addEventListener('resize', () => { layout(!!$('#stage .form-card')); fitPhones(); });
  new ResizeObserver(() => { if (isPhone() && $('#stage .form-card')) layout(true); }).observe($('#sugg'));
  reset();
  if (!EMBED && /phone/.test(location.hash)) setDevice('phone');
  G.__journey = { get S() { return S; }, runTurn, send, checkRules, togglePanel, setMode, setDevice, toggleTrace, reset, lambo };
})(window);
