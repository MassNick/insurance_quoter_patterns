/* The form stage: shows the surface the store says belongs there, with the ball-to-form morph on the
   way in and its reverse on the way out (one at a time, in order). Also draws the hand-off
   confirmation inside the end-of-form card. */
import { type CSSProperties, useEffect, useRef, useState } from 'react';
import { flushSync } from 'react-dom';
import { Icon } from '../../a2ui/icons';
import { isPhone } from '../../a2ui/media';
import { get } from '../../a2ui/pointer';
import { FormCard } from '../../a2ui/Surface';
import { useApp, useSession } from '../context';
import { morphClose, morphOpen } from '../stage';

interface Props {
  style: CSSProperties;
  visible: boolean;
  /** open: the conversation makes room (desktop) or slides away (phone); 'unpush': phone close starts; closed: back to the chat. */
  onLayout: (phase: 'open' | 'unpush' | 'closed') => void;
}

export function StageHost({ style, visible, onLayout }: Props) {
  const session = useSession();
  const target = useApp((s) => s.stage);
  const byId = useApp((s) => s.surfaces.byId);
  const handoff = useApp((s) => s.handoff);
  const [shown, setShown] = useState<string | null>(null);
  const [measuring, setMeasuring] = useState(false);
  const card = useRef<HTMLElement>(null);
  const current = useRef<string | null>(null);
  const chain = useRef(Promise.resolve());

  const anchor = (id: string) => {
    const msg = session.store.get().chips[id]?.messageId;
    const own = msg ? document.querySelector<HTMLElement>(`[data-msg="${msg}"] .avatar`) : null;
    return own ?? [...document.querySelectorAll<HTMLElement>('#scroll .msg.bot .avatar')].pop() ?? null;
  };

  // biome-ignore lint/correctness/useExhaustiveDependencies: the stage reacts to its target only; the chain keeps animations in order
  useEffect(() => {
    chain.current = chain.current.then(async () => {
      const prev = current.current;
      if (prev && prev !== target) {
        await morphClose(card.current, () => anchor(prev), {
          onCardGone: () => {
            current.current = null;
            flushSync(() => setShown(null));
            onLayout('unpush');
          },
          shiftX: () => (isPhone() ? new DOMMatrix(getComputedStyle(document.getElementById('scroll') as HTMLElement).transform).m41 : 0),
        });
        if (!target) {
          onLayout('closed');
          if (!isPhone()) document.getElementById('input')?.focus();
        }
      }
      if (target && current.current !== target) {
        onLayout('open');
        current.current = target;
        flushSync(() => {
          setShown(target);
          setMeasuring(true);
        });
        if (card.current) await morphOpen(card.current, anchor(target), 500);
        setMeasuring(false);
        if (!isPhone())
          setTimeout(
            () =>
              card.current?.querySelector<HTMLElement>('input, select, .chip, button.radio-card, textarea')?.focus({ preventScroll: true }),
            120,
          );
      }
    });
  }, [target]);

  const surface = shown ? byId[shown] : undefined;
  const done =
    handoff && shown === 'end_of_form' && surface ? (
      <>
        <div className="form-body">
          <div className="done-box">
            <div className="big">
              <Icon name="ok" size={34} />
            </div>
            <h3>C’est envoyé!</h3>
            <p>
              Référence : <b>{handoff.reference}</b>
            </p>
            <p style={{ color: 'var(--ink-2)' }}>
              Un conseiller vous appellera d’ici 1 jour ouvrable au{' '}
              <b>{String(get(surface.dataModel, '/quote/phone_number') ?? 'numéro fourni')}</b>. Votre résumé lui a été transmis.
            </p>
          </div>
        </div>
        <footer className="form-foot">
          <div className="spacer" />
          <button type="button" className="btn primary" onClick={() => session.closeHandoff()}>
            Fermer
          </button>
        </footer>
      </>
    ) : undefined;

  return (
    <div className="form-stage" id="stage" hidden={!visible} style={style}>
      {surface && (
        <FormCard
          ref={card}
          surface={surface}
          dispatch={session.localDispatch}
          host={session}
          className={[measuring && 'measuring', shown === 'end_of_form' && 'endcard'].filter(Boolean).join(' ') || undefined}
          done={done}
        />
      )}
    </div>
  );
}
