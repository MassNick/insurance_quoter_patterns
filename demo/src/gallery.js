/* gallery.js: browse the agent-generated A2UI forms. Each one replays the chat-to-form animation. */
(function (G) {
  'use strict';
  const A = G.A2UI;
  const { h, esc, md, ICON, wait, jsonHtml } = A;
  const $ = (s) => document.querySelector(s);
  const FORMS = G.FORMS;
  let current = null, surface = null, stage = null, runId = 0, opener = null, touring = false;

  const services = A.makeServices();
  const host = {
    services,
    demoFill(sf) { const f = FORMS.find((x) => x.spec.form_id === sf.id); if (!f) return;
      if (f.sample === 'readAll') { if (sf.readAll) sf.readAll(); return; }
      if (sf.placeOnBoard && sf.components && [...sf.components.values()].some((c) => c.component === 'AssignmentBoard')) { [[0, 0, 'p'], [1, 1, 'p'], [2, 1, 's'], [1, 0, 's']].forEach(([p, v, k], i) => setTimeout(() => sf.placeOnBoard(p, v, k), i * 260)); return; }
      for (const [p, v] of Object.entries(f.sample || {})) A.ptrSet(sf.data, p, v); if (sf.id === 'fleet_vins') for (let i = 0; i < 10; i++) A.ptrSet(sf.data, `/vehicles/${i}/vin`, A.makeVin(2023, 'Toyota', 'Corolla')); sf.rerender(); },
    async onFieldAction(sf, a) {
      await wait(900 + Math.random() * 500);
      const purpose = a.kind === 'document' ? a.purpose : a.id === 'upload_licence' ? 'licence' : 'vin';
      const r = services.extractDocument(purpose);
      if (a.kind === 'document') return r;
      if (purpose === 'vin' && sf.id === 'fleet_vins') { const v = A.makeVin(2023, 'Toyota', 'Corolla'); return { value: v, source: 'document', note: '97 %' }; }
      const f = purpose === 'licence' ? r.fields.driver_license_number : r.fields.vin;
      return { value: f.value, source: 'document', note: Math.round(f.confidence * 100) + ' %' };
    },
    async onAction(sf, name, payload) {
      const my = runId;
      await stage.close(sf);
      if (my !== runId) return;
      surface = null;
      const vals = payload.values || {};
      const n = Object.keys(vals).length;
      const shell = bot();
      const d = dots(shell); await wait(350);
      if (my !== runId) return;
      d.remove();
      const body = h('div', { class: 'bubble-body' });
      body.innerHTML = name === 'dismiss' || name === 'nodocs' || name === 'edit' ? '<p>Pas de problème, dites-le-moi dans la conversation.</p>' : `<p>Merci, j’ai enregistré <strong>${n} information${n === 1 ? '' : 's'}</strong>. Le BFF les a validées avec le catalogue et écrites dans Cosmos DB${hasDet(sf) ? ', après vous avoir demandé de confirmer deux fois les réponses protégées' : ''}.</p>`;
      shell.b.append(body);
      if (name !== 'dismiss' && n) { const det = h('details', { class: 'vals' }); det.innerHTML = `<summary>Message a2ui.action (${n} valeurs)</summary><pre class="json">${jsonHtml({ version: '1.0', action: { name, surfaceId: sf.id, sourceComponentId: 'action_' + name, timestamp: new Date().toISOString(), context: { values: vals } } })}</pre>`; shell.b.append(det); }
      const again = h('div', { class: 'again' });
      const b1 = h('button', { class: 'btn small' }, 'Rejouer'); b1.onclick = () => select(current.id);
      const b2 = h('button', { class: 'btn small primary' }, 'Formulaire suivant →'); b2.onclick = () => next();
      again.append(b1, b2); shell.b.append(again);
      scroll();
      if (touring) { await wait(1200); if (my === runId && touring) next(); }
    }
  };
  const hasDet = (sf) => [...sf.components.values()].some((c) => c.deterministic);

  function scroll() { const s = $('#mini-scroll'); s.scrollTop = s.scrollHeight; }
  function bot() { const m = h('div', { class: 'msg bot' }); const av = h('span', { class: 'avatar' }, ICON.botface); const b = h('div', { class: 'bubble' }); m.append(av, b); $('#mini-scroll').append(m); scroll(); return { m, av, b }; }
  function dots(shell) { const d = h('div', { class: 'typing' }, '<span></span><span></span><span></span>'); shell.b.append(d); scroll(); return d; }
  async function stream(shell, text, my) {
    const body = h('div', { class: 'bubble-body' }); shell.b.append(body);
    const parts = text.match(/\S+\s*/g) || [];
    let acc = '';
    for (let i = 0; i < parts.length; i += 2) { if (my !== runId) return; acc += parts.slice(i, i + 2).join(''); body.innerHTML = `<p>${md(acc)}<span class="caret"></span></p>`; await wait(28); }
    body.innerHTML = `<p>${md(text)}</p>`;
  }

  function layout(open) {
    const ws = $('#ws'); const W = ws.clientWidth; const chat = $('#mini'); const st = $('#gstage');
    const pad = 16; const narrow = W < 720;
    const chatW = open ? (narrow ? W - 2 * pad : Math.max(280, Math.min(360, Math.round(W * 0.34)))) : Math.min(560, W - 2 * pad);
    chat.style.width = chatW + 'px'; chat.style.left = (open ? pad : Math.round((W - chatW) / 2)) + 'px';
    if (open) { const sl = narrow ? pad : 2 * pad + chatW; st.style.left = sl + 'px'; st.style.width = (W - sl - pad) + 'px'; st.hidden = false; } else st.hidden = true;
    ws.classList.toggle('form-open', open); ws.classList.toggle('narrow', narrow);
  }

  async function select(id) {
    const my = ++runId;
    const f = FORMS.find((x) => x.id === id); if (!f) return;
    current = f;
    document.querySelectorAll('.g-item').forEach((el) => el.setAttribute('aria-current', String(el.dataset.id === id)));
    const idx = FORMS.indexOf(f);
    $('#g-title').textContent = `${idx + 1}. ${f.title}`;
    $('#g-tags').innerHTML = f.tags.map((t) => `<span class="tag">${esc(t)}</span>`).join('');
    const msgs = A.compile(f.spec);
    const specTok = Math.round(JSON.stringify(f.spec).length / 4), a2Tok = Math.round(JSON.stringify(msgs).length / 4);
    $('#p-spec').innerHTML = `<div class="tok">Agent output for <code>a2ui.render_form</code>: about <b>${specTok}</b> tokens. The A2UI MCP server compiles it into <b>${a2Tok}</b> tokens of A2UI the model never writes. Labels, help and options come from <code>label_fr</code>, <code>help_fr</code> and <code>enum_fr</code> in the state catalog.</div><pre class="json">${jsonHtml(f.spec)}</pre>`;
    $('#p-a2ui').innerHTML = `<div class="tok">A2UI 1.0: <b>one</b> <code>createSurface</code> message carrying the components and the data model. It travels as an MCP embedded resource (<code>application/a2ui+json</code>, audience: user, so it stays out of the model's context), then the BFF forwards it over SSE. Catalog: <code>insurance-catalog/v1</code> (protocolVersion 1.0), which re-exports the basic catalog components it uses and adds the insurance components and functions. Field lookups go back as <code>callAgentFunction</code>.</div>` + msgs.map((m) => `<pre class="json">${jsonHtml(m)}</pre>`).join('');
    try { history.replaceState(null, '', '#' + id); } catch (e) { /* data: or file: snapshot */ }
    // reset stage
    if (surface && surface.el && surface.el.isConnected) { await stage.close(surface, true); }
    if (my !== runId) return;
    surface = null;
    $('#gstage').innerHTML = ''; layout(false);
    const sc = $('#mini-scroll'); sc.innerHTML = '';
    const u = h('div', { class: 'msg user' }, `<div class="bubble"><div class="bubble-body">${esc(f.said)}</div></div>`);
    sc.append(u);
    await wait(250); if (my !== runId) return;
    const shell = bot(); opener = shell.av;
    const d = dots(shell);
    await wait(650); if (my !== runId) return;
    d.remove();
    await stream(shell, f.agent, my); if (my !== runId) return;
    const sf = new A.Surface(f.spec.form_id, host);
    for (const m of msgs) sf.apply(m);
    surface = sf;
    shell.b.append(h('span', { class: 'form-chip', 'data-state': 'open' }, `<i></i>Formulaire : ${esc(f.spec.title)}<span class="chip-st">${esc(f.spec.form_id)}</span>`));
    await stage.open(sf);
    if (touring && my === runId) { await wait(1600); if (my === runId && touring) { host.demoFill(sf); await wait(900); if (my === runId && touring) sf.fire(f.spec.actions && f.spec.actions[0] ? f.spec.actions[0].id : 'submit'); } }
  }
  function next() { const i = FORMS.indexOf(current); select(FORMS[(i + 1) % FORMS.length].id); }

  function boot() {
    $('#brand-mark').innerHTML = ICON.brand;
    stage = new A.Stage({ stageEl: $('#gstage'), anchor: () => opener, layout, layoutMs: 450, autofocus: false });
    const list = $('#g-list');
    let group = null;
    FORMS.forEach((f, i) => {
      if (f.group !== group) { group = f.group; list.append(h('div', { class: 'g-group' }, esc(group))); }
      const b = h('button', { class: 'g-item', 'data-id': f.id, 'aria-current': 'false' }, `<span class="g-n">${i + 1}</span><span class="g-t">${esc(f.title)}</span>`);
      b.onclick = () => { touring = false; $('#tour').setAttribute('aria-pressed', 'false'); select(f.id); if (G.innerWidth < 860) $('#g-side').classList.remove('open'); };
      list.append(b);
    });
    $('#g-count').textContent = FORMS.length;
    $('#tour').onclick = () => { touring = !touring; $('#tour').setAttribute('aria-pressed', String(touring)); if (touring) select(current ? current.id : FORMS[0].id); };
    $('#replay').onclick = () => select(current.id);
    $('#next').onclick = () => { touring = false; $('#tour').setAttribute('aria-pressed', 'false'); next(); };
    $('#menu').onclick = () => $('#g-side').classList.toggle('open');
    document.querySelectorAll('.vtab').forEach((t) => { t.onclick = () => { document.querySelectorAll('.vtab').forEach((x) => x.setAttribute('aria-selected', String(x === t))); document.querySelectorAll('.vpanel').forEach((p) => { p.hidden = p.id !== t.dataset.p; }); }; });
    G.addEventListener('resize', () => layout(!!surface));
    layout(false);
    const start = (location.hash || '').slice(1);
    select(FORMS.some((f) => f.id === start) ? start : FORMS[0].id);
  }
  G.addEventListener('DOMContentLoaded', boot);
})(window);
