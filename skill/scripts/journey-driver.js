/* journey-driver.js: automated click-through for demo/journey.html (Variation C, French UI).
 * Paste into the browser JavaScript tool on the journey page. Set window.__path first, or leave it unset for 'docs':
 *   'docs'  documents → licence confirmation → email → vehicle (usage typed in chat fills the open form) → drivers
 *           from the policy + one typed → second vehicle → drag board → history (3 protected answers) → quote → v2
 *   'text'  "je préfère répondre par écrit": the whole quote in chat, no form except none needed → quote
 *   'rule'  Lamborghini: inferred STOP → confirmation question → end-of-form view → send → chips locked
 *   'edit'  reopen a submitted chip, change a value, save (no model call), then full-form mode and save
 * Poll ({log: __log, errs: __errs, done: __done}) every ~10 s. In a hidden browser pane timers are throttled:
 * the run is slower but still correct, so do not time-box it tightly.
 */
(() => {
  window.__log = []; window.__errs = []; window.__done = false;
  if (!window.__driverHooks) {
    window.__driverHooks = true;
    window.addEventListener('error', (e) => window.__errs.push(String(e.message)));
    window.addEventListener('unhandledrejection', (e) => window.__errs.push('rejection: ' + String((e.reason && e.reason.stack) || e.reason)));
  }
  const $ = (s, r) => (r || document).querySelector(s); const $$ = (s, r) => [...(r || document).querySelectorAll(s)];
  const log = (m) => window.__log.push(m);
  const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
  const S = () => window.__journey.S;
  async function idle() { for (let i = 0; i < 600; i++) { if (!S().busy && !$('.morph') && !$('.modal-back')) { await sleep(350); if (!S().busy && !$('.morph') && !$('.modal-back')) return; } await sleep(100); } throw new Error('timeout waiting for idle'); }
  async function modal() { for (let i = 0; i < 80 && !$('.modal'); i++) await sleep(100); if (!$('.modal')) throw new Error('no confirmation popup'); $('.modal [data-a="yes"]').click(); await sleep(150); $('#cf-ack').click(); $('.modal [data-a="yes"]').click(); await sleep(300); log('confirmed twice'); }
  async function sg(label) { await idle(); const b = $$('#sugg .sg').find((x) => x.textContent.startsWith(label)); if (!b) throw new Error('no suggestion "' + label + '" | have: ' + $$('#sugg .sg').map((x) => x.textContent).join(' / ')); b.click(); log('» ' + label); await sleep(250); }
  async function type(t) { await idle(); $('#input').value = t; $('#send').click(); log('» ' + t); await sleep(250); }
  async function card(id) { for (let i = 0; i < 120 && !$('#stage .form-card'); i++) await sleep(100); await idle(); const c = $('#stage .form-card'); if (!c) throw new Error('no form opened'); if (id && c.dataset.surface !== id) throw new Error(`expected form ${id}, got ${c.dataset.surface}`); log('form ' + c.dataset.surface); return c; }
  async function fill() { const c = await card(); $('.demo-fill', c).click(); await sleep(2800); }
  async function submit() { const c = $('#stage .form-card'); $('.form-foot .btn.primary', c).click(); await sleep(600); const e = c.isConnected && c.querySelector('.err-summary'); if (e && e.textContent) throw new Error('form did not validate: ' + e.textContent); }
  const bot = () => { const b = $$('.msg.bot .bubble-body').pop(); return b ? b.textContent : ''; };
  const expect = (cond, msg) => { if (!cond) throw new Error('expected: ' + msg + ' | last bot: ' + bot().slice(0, 120)); };
  async function start() { window.__journey.reset(); await sleep(600); await sg('Obtenir une soumission auto'); await sg('Oui, j’accepte'); await sleep(1200); await modal(); await idle(); expect(/documents sous la main/.test(bot()), 'document offer after consent'); expect(/par écrit ou me demander un formulaire/.test(bot()), 'one-time text-or-forms note'); }
  const PATHS = {
    async docs() {
      await start();
      await sg('Oui, j’ai mes documents'); await card('documents'); await fill(); await submit(); await modal(); await idle();
      expect(S().state.drivers.length >= 2, 'policy driver added');
      await type('marie.tremblay@exemple.com'); await card('vehicule_0');
      await type('Je m’en sers pour aller au travail'); await idle();
      expect($('#stage .form-card .chip[aria-checked="true"]'), 'usage typed in chat filled the open form');
      await submit(); await idle();
      await sg('Oui, une autre personne'); await type('Léo, mon fils, 17 ans'); await sg('Non, c’est tout');
      await sg('Oui, j’ai un autre véhicule'); await type('Une Honda Civic 2015'); await card('vehicule_1'); await fill(); await submit();
      await card('qui_conduit_quoi'); await fill(); await sleep(1500); await submit();
      await card('antecedents'); await fill(); await submit(); await modal(); await idle();
      await sg('Obtenir ma soumission'); await card(); await fill(); await submit(); await idle();
      await sg('Réduire ma prime'); await idle();
      expect(S().quotes.length === 2, 'quote version 2');
    },
    async text() {
      await start(); await sg('Je ne les ai pas');
      await type('Je préfère répondre par écrit'); await idle(); expect(S().prefs.answer_mode === 'text', 'answer_mode text');
      await type('Je m’appelle Samir Haddad'); await type('3 mars 1990'); await type('samir@exemple.com');
      await type('Honda'); await type('Civic 2019'); await sg('Aller au travail'); await sg('Seulement moi');
      await sg('Non, aucun des trois'); await sleep(1200); await modal(); await idle();
      expect(!$$('.form-chip').length, 'no form opened in text mode');
      await sg('Obtenir ma soumission'); await card(); log('quote ready');
    },
    async rule() {
      await start(); await sg('Je ne les ai pas');
      await idle(); $('#lambo').click(); await sleep(500); await idle(); expect(/bien d’une/.test(bot()), 'inferred STOP asks first');
      await sg('Oui, c’est bien ça'); await card('fin_conseiller');
      $('#stage .form-card .demo-fill').click(); await sleep(400); $('#stage .form-card .form-foot .btn.primary').click(); await sleep(600);
      expect($('#stage .form-card .done-box'), 'hand-off sent'); $('#stage .form-card .form-foot .btn.primary').click(); await sleep(1500); await idle();
      expect($$('.form-chip').every((c) => c.disabled), 'chips locked after send');
    },
    async edit() {
      await start(); await sg('Je ne les ai pas'); await type('Marie Tremblay'); await card('a_propos'); await fill(); await submit(); await idle();
      $('.form-chip').click(); const c = await card('a_propos');
      const d = c.querySelector('input[type=date]'); d.value = '1988-01-18'; d.dispatchEvent(new Event('change', { bubbles: true }));
      await submit(); await sleep(1200); await idle(); expect(S().state.quote.date_of_birth.value === '1988-01-18', 'edit saved without a turn');
      $('#m-form').click(); await sleep(1500); expect(!$('#fullform').hidden, 'full form open');
      $('#fullform .form-foot .btn.primary').click(); await sleep(1500); await idle(); expect($('#fullform').hidden, 'full form saved');
    }
  };
  const path = window.__path || 'docs';
  PATHS[path]().then(() => { log(`path ${path}: done, ${$$('#trace-list .tr-row').length} trace rows`); window.__done = true; }, (e) => { window.__errs.push(String(e.message || e)); window.__done = true; });
  return 'started ' + path;
})();
