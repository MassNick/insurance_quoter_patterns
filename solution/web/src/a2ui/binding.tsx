/* What every A2UI field shares: the surface context, the useBinding hook (read and write an @path,
   checks, provenance, server errors, live-autofill flash and suggestions) and the FieldShell that draws
   the label, help, protected badge, hint, provenance, suggestion and error around the control. */
import { createContext, type ReactNode, useContext, useEffect, useRef } from 'react';
import { labelFor } from './catalog';
import { firstError, resolvePath } from './checks';
import { Icon } from './icons';
import { Inline } from './md';
import { get, keyOf, provPath, set } from './pointer';
import type { LocalAction, SurfaceState } from './reducer';
import type { A2Component, Provenance, SurfaceHost } from './types';

export interface SurfaceCtx {
  surface: SurfaceState;
  dispatch: (a: LocalAction) => void;
  host: SurfaceHost;
}
export const SurfaceContext = createContext<SurfaceCtx | null>(null);
export function useSurface(): SurfaceCtx {
  const ctx = useContext(SurfaceContext);
  if (!ctx) throw new Error('A2UI component rendered outside a surface');
  return ctx;
}

/** Short source names (dossier rows, Summary badges). */
export const SOURCE_SHORT: Record<string, string> = {
  chat: 'Conversation',
  form: 'Formulaire',
  document: 'Document',
  lookup: 'Recherche',
  system: 'Calculé',
};

const PROV_TEXT: Record<string, string> = {
  chat: 'Tiré de la conversation',
  document: 'Lu sur votre document',
  lookup: 'Trouvé par recherche',
  previous_quote: 'Déjà fourni',
};

export function Badge({ kind, children, title }: { kind: string; children: ReactNode; title?: string }) {
  return (
    <span className={`badge ${kind}`} title={title}>
      {children}
    </span>
  );
}

export function useBinding(c: A2Component, scope?: string, key = 'value') {
  const { surface, dispatch } = useSurface();
  const ref = c[key]?.['@path'] as string | undefined;
  const path = ref ? resolvePath(ref, scope) : c.id;
  const sid = surface.id;
  const errors = (surface.dataModel._errors ?? {}) as Record<string, string>;
  return {
    path,
    value: get(surface.dataModel, path),
    /* The compiled provenance prop, else the badge next to the resolved path (YMM sub-paths, repeater items). */
    prov: get(surface.dataModel, key === 'value' && c.provenance ? c.provenance['@path'] : provPath(path)) as Provenance | undefined,
    error: surface.shown[path] ?? errors[path] ?? null,
    suggestion: surface.suggestions[path],
    flash: surface.flash[path] ?? 0,
    write: (value: unknown, silent?: boolean) => dispatch({ type: 'set', surfaceId: sid, path, value, silent }),
    mark: (prov: Provenance) => dispatch({ type: 'provenance', surfaceId: sid, path, value: prov }),
    accept: () => dispatch({ type: 'acceptSuggestion', surfaceId: sid, path }),
    /** Blur check (prototype TextField): show the first failing check for a non-empty value. */
    check: (value: unknown) => {
      const msg =
        value === '' || value == null
          ? null
          : firstError(c['@checks'], { data: set(surface.dataModel, path, value), scope, component: c, lookups: surface.lookups });
      dispatch({ type: 'showErrors', surfaceId: sid, errors: { [path]: msg ?? '' }, merge: true });
    },
  };
}
export type Binding = ReturnType<typeof useBinding>;

interface ShellProps {
  c: A2Component;
  b: Binding;
  htmlFor?: string;
  /** CheckBox draws its own label. */
  noLabel?: boolean;
  children: ReactNode;
}

export function ProtectedBadge() {
  return (
    <Badge kind="det" title="Toute modification est confirmée deux fois">
      <Icon name="lock" /> confirmé deux fois
    </Badge>
  );
}

export function FieldShell({ c, b, htmlFor, noLabel, children }: ShellProps) {
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const node = el.current;
    if (!b.flash || !node) return;
    node.classList.remove('autofilled');
    void node.offsetWidth;
    node.classList.add('autofilled');
  }, [b.flash]);
  const Label = htmlFor ? 'label' : 'div';
  return (
    <div ref={el} className={`fld${c.fldClass ? ` ${c.fldClass}` : ''}${b.error ? ' invalid' : ''}`}>
      {!noLabel && (
        <Label className={htmlFor ? undefined : 'lbl'} htmlFor={htmlFor}>
          {c.label}
          {c.required && (
            <span className="req" aria-hidden="true">
              *
            </span>
          )}
          {c.help && (
            <span className="help" tabIndex={0} data-help={c.help} data-title={c.label} role="img" aria-label={`Aide : ${c.help}`}>
              <Icon name="help" />
            </span>
          )}
          {c.deterministic && <ProtectedBadge />}
        </Label>
      )}
      {c.hint && (
        <div className="hint">
          <Inline text={c.hint} />
        </div>
      )}
      {children}
      <div className="prov">
        {b.prov && <Badge kind={b.prov.source}>{PROV_TEXT[b.prov.source] ?? b.prov.source}</Badge>}
        {b.prov?.note && (
          <span className="pnote" title={b.prov.note}>
            {b.prov.note}
          </span>
        )}
      </div>
      {b.suggestion !== undefined && (
        <div className="suggest">
          Nouvelle valeur proposée : <b>{labelFor(keyOf(b.path), b.suggestion)}</b>
          <button type="button" className="btn ghost small" onClick={b.accept}>
            Utiliser
          </button>
        </div>
      )}
      <div className="err" role="alert">
        {b.error}
      </div>
    </div>
  );
}
