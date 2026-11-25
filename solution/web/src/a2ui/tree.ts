/* Walks a surface's component tree the way it renders (visibility, repeater items, address sub-fields)
   and lists its bound fields. Used to validate a submit, to collect the action's bound values, and by
   the mock BFF to prefill a reopened surface. */
import { field } from './catalog';
import { evalCall, firstError, resolvePath } from './checks';
import { get, provPath } from './pointer';
import type { SurfaceState } from './reducer';
import type { A2Component, CheckRule } from './types';

export interface BoundField {
  component: A2Component;
  scope?: string;
  /** The path errors are shown on (the component's value, or a YmmPicker's year). */
  path: string;
  /** Every path the field writes, collected on submit. */
  paths: string[];
}

type Walkable = Pick<SurfaceState, 'components' | 'dataModel' | 'repeaterCounts'>;

const BOUND_KEYS: Record<string, string[]> = { YmmPicker: ['year', 'make', 'model', 'vehicleType'] };
const required = (path: string): CheckRule => ({
  condition: { '@call': 'required', args: { value: { '@path': path } } },
  message: 'Ce champ est obligatoire',
});

/** AddressLookup edits four quote fields; they render and validate as ordinary fields. */
export function addressFields(c: A2Component): A2Component[] {
  const sub = (key: string, label: string, extra: Partial<A2Component>): A2Component => {
    const path = `/quote/${key}`;
    return {
      id: `${c.id}_${key}`,
      label,
      required: true,
      value: { '@path': path },
      provenance: { '@path': provPath(path) },
      '@checks': [required(path)],
      ...extra,
      component: extra.component ?? 'TextField',
    };
  };
  const postal = field('postal_code') as unknown as { pattern: string };
  return [
    sub('address_line1', 'Adresse', { textFieldType: 'shortText' }),
    sub('city', 'Ville', { textFieldType: 'shortText' }),
    sub('province_code', 'Province', {
      component: 'ChoicePicker',
      variant: 'select',
      options: (field('province_code')?.enum_fr ?? []).map(([value, label]) => ({ value, label })),
    }),
    sub('postal_code', 'Code postal', {
      textFieldType: 'postal_code',
      '@checks': [
        required('/quote/postal_code'),
        {
          condition: { '@call': 'regex', args: { value: { '@path': '/quote/postal_code' }, pattern: postal.pattern } },
          message: 'Entrez un code postal canadien, comme H2X 1Y4',
        },
      ],
    }),
  ];
}

export function repeaterCount(c: A2Component, s: Walkable): number {
  if (s.repeaterCounts[c.id] != null) return s.repeaterCounts[c.id];
  const existing = get(s.dataModel, c.collection);
  return Math.max(c.count ?? 1, Array.isArray(existing) ? existing.length - (c.start ?? 0) : 0);
}

export function isVisible(c: A2Component, s: Walkable, scope?: string): boolean {
  return !c.visibleIf || evalCall(c.visibleIf, { data: s.dataModel, scope });
}

export function boundFields(s: Walkable): BoundField[] {
  const out: BoundField[] = [];
  const visit = (id: string, scope?: string) => {
    const c = s.components[id];
    if (!c || id === 'title' || c.component === 'ActionBar' || !isVisible(c, s, scope)) return;
    switch (c.component) {
      case 'Column':
      case 'Row':
        for (const ch of c.children ?? []) visit(ch, scope);
        return;
      case 'Repeater': {
        const n = repeaterCount(c, s);
        for (let k = 0; k < n; k++) for (const t of c.template ?? []) visit(t, `${c.collection}/${(c.start ?? 0) + k}`);
        return;
      }
      case 'AddressLookup':
        for (const sub of addressFields(c)) out.push({ component: sub, path: sub.value['@path'], paths: [sub.value['@path']] });
        return;
      case 'DocumentDrop':
      case 'FileDrop': {
        const fills: string[] =
          c.component === 'FileDrop'
            ? Object.values(c.fills ?? {})
            : (c.documents ?? []).flatMap((d: { fills?: Record<string, string> }) => Object.values(d.fills ?? {}));
        out.push({ component: c, path: c.id, paths: fills });
        return;
      }
    }
    const keys = BOUND_KEYS[c.component] ?? ['value'];
    const paths = keys
      .map((k) => c[k]?.['@path'])
      .filter(Boolean)
      .map((p: string) => resolvePath(p, scope));
    if (paths.length) out.push({ component: c, scope, path: paths[0], paths });
  };
  visit('root');
  return out;
}

/** Checks the prototype renderer applies when the compiled component carries none. */
const FALLBACK: Record<string, [string, string]> = {
  OfferCards: ['required', 'Choisissez une offre'],
  VinField: ['vinCheckDigit', 'Ce NIV n’est pas valide (chiffre de contrôle)'],
  AssignmentMatrix: ['onePrincipalPerVehicle', 'Chaque véhicule a besoin d’un conducteur principal'],
};
const checksOf = (f: BoundField): CheckRule[] | undefined => {
  const fb = FALLBACK[f.component.component];
  if (f.component['@checks'] && f.component.component !== 'AssignmentMatrix') return f.component['@checks'];
  return fb ? [{ condition: { '@call': fb[0], args: { value: { '@path': f.path } } }, message: fb[1] }] : undefined;
};

/** Errors keyed by path for every visible field whose checks fail. */
export function validate(s: SurfaceState): Record<string, string> {
  const errors: Record<string, string> = {};
  for (const f of boundFields(s)) {
    const msg = firstError(checksOf(f), { data: s.dataModel, scope: f.scope, lookups: s.lookups, component: f.component });
    if (msg) errors[f.path] = msg;
  }
  return errors;
}

/** The bound values an action carries: every non-empty value of a visible field. */
export function collect(s: Walkable): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const f of boundFields(s))
    for (const p of f.paths) {
      const v = get(s.dataModel, p);
      if (v !== undefined && v !== null && v !== '') out[p] = structuredClone(v);
    }
  return out;
}
