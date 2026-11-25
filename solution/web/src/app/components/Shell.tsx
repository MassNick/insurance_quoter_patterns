/* Header (brand, quote number, Conversation | Formulaire complet, Parler à un conseiller), step chips,
   the left rail that becomes the phone tab bar, and "Votre dossier" with its two tabs. */
import { useEffect, useRef } from 'react';
import { fmtMoney, labelFor } from '../../a2ui/catalog';
import { Icon } from '../../a2ui/icons';
import { isPhone, reduceMotion } from '../../a2ui/media';
import { BRAND_NAME, BRAND_SUB, BrandMark } from '../brand';
import { useApp, useSession } from '../context';
import { completion, currentStep, filledCount, groups, STEPS, val } from '../dossier';
import type { Quote } from '../store';

export function Header() {
  const session = useSession();
  const quoteNumber = useApp((s) => s.quoteNumber);
  const mode = useApp((s) => s.mode);
  return (
    <header className="hdr">
      <span className="logo">
        <BrandMark />
        <span className="wm">
          <b>{BRAND_NAME}</b>
          <small>{BRAND_SUB}</small>
        </span>
      </span>
      <span className="sp" />
      <span className="qnum">
        Numéro de soumission : <b>{quoteNumber}</b>
      </span>
      <div className="mode" role="group" aria-label="Mode">
        <button type="button" aria-pressed={mode === 'chat'} onClick={() => session.fullForm(false)}>
          <Icon name="chat" size={15} />
          Conversation
        </button>
        <button type="button" aria-pressed={mode === 'form'} onClick={() => session.fullForm(true)}>
          <Icon name="form" size={15} />
          Formulaire complet
        </button>
      </div>
      <button type="button" className="agent-btn" onClick={() => session.talkToAdvisor()}>
        <Icon name="headset" />
        <span className="t">Parler à un conseiller</span>
      </button>
    </header>
  );
}

export function Steps() {
  const doc = useApp((s) => s.doc);
  const stage = useApp((s) => s.stage);
  const quotes = useApp((s) => s.quotes);
  const chips = useApp((s) => s.chips);
  const si = currentStep({ doc, stage, quotes, chips });
  return (
    <nav className="steps" aria-label="Étapes">
      {STEPS.map((label, i) => (
        <span key={label} className={i === si ? 'on' : si >= 0 && i < si ? 'done' : undefined} aria-current={i === si ? 'step' : undefined}>
          {label}
        </span>
      ))}
    </nav>
  );
}

export function Rail() {
  const { store } = useSession();
  const open = useApp((s) => s.dossier.open);
  const doc = useApp((s) => s.doc);
  return (
    <button
      type="button"
      className="rail"
      id="dtab"
      aria-expanded={open}
      aria-controls="dossier"
      onClick={() => store.dispatch({ type: 'dossier', open: !open })}
    >
      <span className="rail-i">
        <Icon name="form" size={22} />
        Dossier
      </span>
      <span className="rail-i">
        <Icon name="person" />
        Vous
      </span>
      <span className="rail-i">
        <Icon name="vehicle" />
        Véhicule
      </span>
      <span className="rail-i">
        <Icon name="shield" />
        Soumission
      </span>
      <span className="cnt" id="dcount">
        {filledCount(doc)}
      </span>
    </button>
  );
}

function QuoteCard({ q, current, selected }: { q: Quote; current: boolean; selected: string }) {
  const time = q.created_at ? new Date(q.created_at).toLocaleTimeString('fr-CA', { hour: '2-digit', minute: '2-digit' }) : '';
  return (
    <div className={`q-card${current ? ' current' : ''}`}>
      <div className="q-top">
        <span>
          {q.quote_id} · version {q.version}
        </span>
        <span className="q-sub">{time}</span>
      </div>
      <div className="q-sub">
        {q.reason}
        {q.delta_monthly != null && (
          <>
            {' · '}
            <span className={q.delta_monthly < 0 ? 'down' : 'up'}>
              {q.delta_monthly < 0 ? '' : '+'}
              {fmtMoney(q.delta_monthly, true)}/mois
            </span>
          </>
        )}
      </div>
      <table className="q-tbl">
        <tbody>
          <tr>
            <th>Offre</th>
            <th>Par mois</th>
            <th>Franchise</th>
          </tr>
          {q.offers.map((o) => (
            <tr key={o.offer_type} className={o.offer_type === selected ? 'sel' : undefined}>
              <td>{labelFor('offer_type', o.offer_type)}</td>
              <td>{fmtMoney(o.monthly, true)}</td>
              <td>{fmtMoney(o.deductible)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

const panelFrames = (phone: boolean, open: boolean): Keyframe[] => {
  const axis = phone ? 'translateY' : 'translateX';
  const off = phone ? '105%' : '-105%';
  return open
    ? [{ transform: `${axis}(${off})` }, { transform: `${axis}(${phone ? '-2%' : '3%'})`, offset: 0.65 }, { transform: 'none' }]
    : [{ transform: 'none' }, { transform: `${axis}(${off})` }];
};

/** "Votre dossier": collapsed until the rail is clicked, then a spring and staggered items (FR-UI-4). */
export function Dossier() {
  const { store } = useSession();
  const { open, tab } = useApp((s) => s.dossier);
  const doc = useApp((s) => s.doc);
  const signals = useApp((s) => s.signals);
  const flash = useApp((s) => s.flash);
  const quotes = useApp((s) => s.quotes);
  const quotesPing = useApp((s) => s.quotesPing);
  const panel = useRef<HTMLElement>(null);
  const shown = useRef(false);
  const quotesTab = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    const p = panel.current;
    if (!p || open === shown.current) return;
    shown.current = open;
    const phone = isPhone();
    if (!p.animate) return void p.classList.toggle('open', open);
    if (open) {
      p.classList.add('open');
      p.animate(panelFrames(phone, true), { duration: reduceMotion() ? 1 : 620, easing: 'cubic-bezier(.22,1,.36,1)' });
      if (!reduceMotion())
        p.querySelectorAll<HTMLElement>('.d-item, .q-card').forEach((it, i) => {
          it.animate(
            [
              { opacity: 0, transform: 'translateY(12px)' },
              { opacity: 1, transform: 'none' },
            ],
            { duration: 320, delay: 220 + i * 70, easing: 'cubic-bezier(.2,.8,.2,1)', fill: 'backwards' },
          );
        });
    } else
      p.animate(panelFrames(phone, false), { duration: reduceMotion() ? 1 : 280, easing: 'cubic-bezier(.4,0,1,1)' }).finished.then(
        () => !shown.current && p.classList.remove('open'),
      );
  }, [open]);
  useEffect(() => {
    const t = quotesTab.current;
    if (!quotesPing || !t) return;
    t.classList.remove('ping');
    void t.offsetWidth;
    t.classList.add('ping');
  }, [quotesPing]);

  const pct = completion(doc);
  const selected = String(val(doc, '/underwriting/offer_type') ?? 'plus');
  const setTab = (t: 'states' | 'quotes') => store.dispatch({ type: 'dossier', tab: t });
  return (
    <aside className="dossier" id="dossier" ref={panel} aria-label="Votre dossier" aria-hidden={!open}>
      <div className="dhead">
        <h3>Votre dossier</h3>
        <button type="button" className="x" aria-label="Fermer" onClick={() => store.dispatch({ type: 'dossier', open: false })}>
          ×
        </button>
      </div>
      <div className="dtabs" role="tablist">
        <button
          type="button"
          role="tab"
          id="tab-states"
          aria-controls="states"
          aria-selected={tab === 'states'}
          onClick={() => setTab('states')}
        >
          Renseignements
        </button>
        <button
          type="button"
          role="tab"
          id="tab-quotes"
          aria-controls="quotes"
          aria-selected={tab === 'quotes'}
          ref={quotesTab}
          onClick={() => setTab('quotes')}
        >
          Soumissions <span className="num">{quotes.length || ''}</span>
        </button>
      </div>
      <div className="dbody" id="states" role="tabpanel" aria-labelledby="tab-states" hidden={tab !== 'states'}>
        <div className="d-item">
          <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 13, fontWeight: 600 }}>
            <span>Soumission complétée</span>
            <span>{pct} %</span>
          </div>
          <div className="meter">
            <i style={{ width: `${pct}%` }} />
          </div>
          <div style={{ fontSize: 12, color: 'var(--ink-3)' }}>Mis à jour à chaque réponse. Survolez un champ pour une explication.</div>
        </div>
        {signals.length > 0 && (
          <div className="d-item">
            <div className="grp">
              <h4>Ce que j’ai remarqué</h4>
            </div>
            {signals.map((x) => (
              <div key={x.code} className={`signal${x.flag ? ' flag' : ''}`}>
                {x.message}
              </div>
            ))}
          </div>
        )}
        {groups(doc).map((g) => (
          <div key={g.title} className="d-item grp">
            <h4>
              {g.title}
              <span>
                {g.n}/{g.total}
              </span>
            </h4>
            {g.rows.map((r) => {
              const hot = flash.paths.includes(r.key);
              return (
                <div key={hot ? `${r.key}#${flash.n}` : r.key} className={`row${hot ? ' flash' : ''}`}>
                  <span className="k" tabIndex={0} data-help={r.help} data-title={r.label}>
                    {r.label}
                    <Icon name="help" />
                  </span>
                  <span className={`v${r.value ? '' : ' empty'}`}>
                    {r.value || '—'}
                    {r.value && r.meta?.text && <small className={r.meta.inferred ? 'inf' : undefined}>{r.meta.text}</small>}
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="dbody" id="quotes" role="tabpanel" aria-labelledby="tab-quotes" hidden={tab !== 'quotes'}>
        {quotes.length ? (
          [...quotes].reverse().map((q, i) => <QuoteCard key={`${q.quote_id}-${q.version}`} q={q} current={i === 0} selected={selected} />)
        ) : (
          <div style={{ color: 'var(--ink-3)', fontSize: 13 }}>
            Aucune soumission pour l’instant. Elle apparaîtra ici dès qu’elle sera calculée.
          </div>
        )}
      </div>
    </aside>
  );
}
