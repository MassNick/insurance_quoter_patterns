/* The double-confirm modal (confirm.required -> two confirm.response steps), the full-form mode
   (reserved surface full_form) and the developer trace drawer (?dev=1, English). */
import { Fragment, useEffect, useRef, useState } from 'react';
import { labelFor, labelOf } from '../../a2ui/catalog';
import { reduceMotion } from '../../a2ui/media';
import { keyOf } from '../../a2ui/pointer';
import { FormCard } from '../../a2ui/Surface';
import { useApp, useSession } from '../context';
import { changesOf } from '../store';
import { Modal } from '../ui';

const fmt = (path: string, v: unknown) => (v === undefined || v === null ? 'non indiqué' : labelFor(keyOf(path), v));

export function ConfirmModal() {
  const session = useSession();
  const modal = useApp((s) => s.modal);
  const all = useApp((s) => s.confirmations);
  const [ack, setAck] = useState(false);
  if (!modal) return null;
  const items = all.filter((c) => modal.ids.includes(c.confirmation_id));
  const reason = [...new Set(items.map((c) => c.reason).filter(Boolean))].join(' ');
  const progress = (
    <div className="progress2">
      <i className="on" />
      <i className={modal.step === 2 ? 'on' : undefined} />
    </div>
  );
  return (
    <Modal labelledBy="cf-title" describedBy="cf-body" focusKey={modal.step} onEscape={() => session.confirm(modal.step, false)}>
      {modal.step === 1 ? (
        <>
          <div className="modal-head">
            <div className="step">Étape 1 de 2 · information protégée</div>
            <h3 id="cf-title">Confirmez cette réponse</h3>
            {progress}
          </div>
          <div className="modal-body" id="cf-body">
            {reason}
            <dl className="change">
              {items.flatMap(changesOf).map((c) => (
                <Fragment key={c.path}>
                  <dt>{labelOf(keyOf(c.path)) || c.path}</dt>
                  <dd>
                    {fmt(c.path, c.old_value)} → <strong>{fmt(c.path, c.new_value)}</strong>
                  </dd>
                </Fragment>
              ))}
            </dl>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn" onClick={() => session.confirm(1, false)}>
              Annuler
            </button>
            <button
              type="button"
              className="btn primary"
              onClick={() => {
                setAck(false);
                session.confirm(1, true);
              }}
            >
              C’est exact
            </button>
          </div>
        </>
      ) : (
        <>
          <div className="modal-head">
            <div className="step">Étape 2 de 2 · dernière vérification</div>
            <h3 id="cf-title">Confirmez-vous?</h3>
            {progress}
          </div>
          <div className="modal-body" id="cf-body">
            Votre réponse sera enregistrée à votre dossier, avec l’heure de votre confirmation. Vous pourrez la modifier plus tard.
            <label className="check" style={{ marginTop: 12 }}>
              <input type="checkbox" id="cf-ack" checked={ack} onChange={(e) => setAck(e.target.checked)} />{' '}
              <span>Je confirme que ces renseignements sont exacts.</span>
            </label>
          </div>
          <div className="modal-foot">
            <button type="button" className="btn" onClick={() => session.confirm(2, false)}>
              Retour
            </button>
            <button type="button" className="btn primary" disabled={!ack} onClick={() => session.confirm(2, true)}>
              Oui, je confirme
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

export function FullForm() {
  const session = useSession();
  const mode = useApp((s) => s.mode);
  const surface = useApp((s) => s.surfaces.byId.full_form);
  const box = useRef<HTMLElement>(null);
  const live = mode === 'form' && surface && !surface.deleted;
  useEffect(() => {
    if (live && box.current?.animate && !reduceMotion())
      box.current.animate(
        [
          { opacity: 0, transform: 'translateY(16px)' },
          { opacity: 1, transform: 'none' },
        ],
        { duration: 320, easing: 'cubic-bezier(.2,.8,.2,1)' },
      );
  }, [live]);
  return (
    <section className="fullform" id="fullform" ref={box} hidden={mode !== 'form'} aria-label="Formulaire complet">
      <div className="inner">
        <h1>Votre soumission</h1>
        <p className="lead">
          Tout ce que vous avez dit ou téléversé est déjà rempli. Les champs « Tiré de la conversation » sont à vérifier.
        </p>
        {live && <FormCard surface={surface} dispatch={session.localDispatch} host={session} />}
      </div>
    </section>
  );
}

export function Trace() {
  const rows = useApp((s) => s.trace);
  const phase = useApp((s) => s.phase);
  const [open, setOpen] = useState(false);
  const [detail, setDetail] = useState<number | null>(null);
  const list = useRef<HTMLDivElement>(null);
  useEffect(() => {
    document.body.classList.toggle('dev', open);
  }, [open]);
  useEffect(() => {
    if (list.current) list.current.scrollTop = list.current.scrollHeight;
  });
  const turns = rows
    .filter((r) => r.type === 'turn.completed')
    .map((r) => (r.detail as { data?: { model_calls?: number; ttft_ms?: number } }).data ?? {});
  const ttft = turns.map((t) => t.ttft_ms ?? 0).sort((a, b) => a - b);
  const cls = (t: string) =>
    t.startsWith('turn')
      ? 't-turn'
      : t.startsWith('tool')
        ? 't-tool'
        : t.startsWith('a2ui') || t.startsWith('ui.')
          ? 't-a2ui'
          : /^(state|signals|session|memory)/.test(t)
            ? 't-state'
            : /^(confirm|rules|handoff)/.test(t)
              ? 't-rules'
              : t === 'guardrail'
                ? 't-guard'
                : t === 'error'
                  ? 't-warn'
                  : 't-io';
  return (
    <>
      <button type="button" className="pbtn trace-toggle" aria-pressed={open} onClick={() => setOpen(!open)}>
        Trace <span className="n">{rows.length}</span>
      </button>
      <section className={`trace${open ? ' open' : ''}`} aria-label="Trace" aria-hidden={!open}>
        <div className="trace-head">
          <h3>Trace · BFF, hosted agent, tools, A2UI, rules</h3>
          <div className="meters">
            <span>
              phase <b>{phase}</b>
            </span>
            <span>
              <b>{turns.length}</b> turns
            </span>
            <span>
              <b>{turns.length ? Math.round((turns.filter((t) => t.model_calls === 2).length / turns.length) * 100) : 0}%</b> at 2 model
              calls
            </span>
            <span>
              TTFT p95 <b>{ttft.length ? ttft[Math.min(ttft.length - 1, Math.floor(ttft.length * 0.95))] : 0} ms</b>
            </span>
          </div>
          <span className="sp" />
          <button type="button" className="btn small" onClick={() => setOpen(false)}>
            Close
          </button>
        </div>
        <div className="trace-list" ref={list}>
          {rows.map((r, i) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: trace rows are append-only
            <div key={i} className="tr-row">
              <button type="button" className={`tr-head${detail === i ? ' open' : ''}`} onClick={() => setDetail(detail === i ? null : i)}>
                <span className="tr-t">+{(r.ms / 1000).toFixed(2)}s</span>
                <span className={`tr-type ${cls(r.type)}`}>{r.type}</span>
                <span className="tr-label">{r.label}</span>
              </button>
              {detail === i && r.detail !== undefined && <pre className="json">{JSON.stringify(r.detail, null, 2)}</pre>}
            </div>
          ))}
        </div>
      </section>
    </>
  );
}
