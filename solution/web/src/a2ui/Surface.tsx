/* A surface rendered as the prototype's form card: title, close button, the 12-column body and the
   ActionBar footer. Buttons that submit validate first (prototype Surface.fire); every action leaves
   as an A2UI 1.0 action message carrying the bound values. */
import { forwardRef, type ReactNode, useRef } from 'react';
import { SurfaceContext } from './binding';
import { Icon } from './icons';
import type { LocalAction, SurfaceState } from './reducer';
import { Node } from './registry';
import { collect, validate } from './tree';
import type { A2Component, SurfaceHost } from './types';

/** Action names that only fire on a valid form: the commits (submit, save), the prototype's select/confirm, the hand-off send. */
const VALIDATED = new Set(['submit', 'save', 'select', 'confirm', 'send']);

export const surfaceTitle = (s: SurfaceState): string => String(s.components.title?.text ?? '').replace(/^#+\s*/, '');

interface Props {
  surface: SurfaceState;
  dispatch: (a: LocalAction) => void;
  host: SurfaceHost;
  className?: string;
  /** Replaces body and footer (the end-of-form confirmation after a hand-off). */
  done?: ReactNode;
}

export const FormCard = forwardRef<HTMLElement, Props>(function FormCard({ surface, dispatch, host, className, done }, ref) {
  const body = useRef<HTMLDivElement>(null);
  const title = surfaceTitle(surface);
  const root = surface.components.root;
  const children = (root?.children ?? []) as string[];
  const bar = children.map((id) => surface.components[id]).find((c) => c?.component === 'ActionBar');

  const fire = (button: A2Component, name: string) => {
    if (VALIDATED.has(name)) {
      const errors = validate(surface);
      dispatch({ type: 'showErrors', surfaceId: surface.id, errors });
      if (Object.keys(errors).length) {
        requestAnimationFrame(() =>
          body.current?.querySelector<HTMLElement>('.fld.invalid input, .fld.invalid select, .fld.invalid button')?.focus(),
        );
        return;
      }
    }
    host.onAction({
      name,
      surfaceId: surface.id,
      sourceComponentId: button.id,
      timestamp: new Date().toISOString(),
      context: { ...(button.action?.event?.context ?? {}), values: collect(surface) },
    });
  };
  const n = surface.errorCount;

  return (
    <SurfaceContext.Provider value={{ surface, dispatch, host }}>
      <section
        ref={ref}
        className={`form-card${className ? ` ${className}` : ''}`}
        data-surface={surface.id}
        role="dialog"
        aria-label={title || 'Formulaire'}
      >
        <header className="form-head">
          <div>
            <h2>{title}</h2>
            <div className="surface-id">surface: {surface.id} · A2UI 1.0</div>
          </div>
          <div className="spacer" />
          <button
            type="button"
            className="btn ghost small"
            aria-label="Fermer le formulaire et répondre dans la conversation"
            title="Fermer"
            onClick={() => fire({ id: 'dismiss', component: 'Button' }, 'dismiss')}
          >
            <Icon name="close" />
          </button>
        </header>
        {done ?? (
          <>
            <div className="form-body" ref={body}>
              <div className="a2-col">
                {children
                  .filter((id) => id !== 'title' && id !== bar?.id)
                  .map((id) => (
                    <Node key={id} id={id} />
                  ))}
              </div>
            </div>
            <footer className="form-foot">
              <div className="err-summary" aria-live="polite">
                {n ? `${n} champ${n > 1 ? 's à corriger' : ' à corriger'}` : ''}
              </div>
              <div className="spacer" />
              {((bar?.children ?? []) as string[]).map((bid) => {
                const b = surface.components[bid];
                if (!b) return null;
                const variant = b.variant === 'primary' ? 'primary' : b.variant === 'borderless' ? 'ghost' : '';
                return (
                  <button key={bid} type="button" className={`btn ${variant}`.trim()} onClick={() => fire(b, b.action?.event?.name ?? bid)}>
                    {surface.components[b.child]?.text ?? ''}
                  </button>
                );
              })}
            </footer>
          </>
        )}
      </section>
    </SurfaceContext.Provider>
  );
});
