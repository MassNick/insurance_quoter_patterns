/* What the customer sees of the session state, derived (never stored): the "Votre dossier" rows and
   their groups, the completion meter, the rail count and the step chips (D12). Ported from the
   prototype's renderStates / completion / updateProgress, but computed from the BFF's state only. */

import { SOURCE_SHORT } from '../a2ui/binding';
import { ageFrom, field, helpOf, labelFor, labelOf } from '../a2ui/catalog';
import { get, keyOf } from '../a2ui/pointer';
import type { AppState } from './store';

type Env = { value?: unknown; source?: string; status?: string } | undefined;
const env = (doc: unknown, p: string) => get(doc, p) as Env;
export const val = (doc: unknown, p: string) => env(doc, p)?.value;
const has = (doc: unknown, p: string) => {
  const v = val(doc, p);
  return v !== undefined && v !== null && v !== '';
};
const list = (doc: unknown, coll: string) => ((get(doc, coll) as unknown[]) ?? []).filter((x) => x != null);

export interface Row {
  key: string;
  label: string;
  help: string;
  value: string;
  meta?: { text: string; inferred: boolean };
}
export interface Group {
  title: string;
  n: number;
  total: number;
  rows: Row[];
}

function meta(e: Env, deterministic = false): Row['meta'] {
  if (!e) return undefined;
  if (e.status === 'pending_confirmation') return { text: 'Confirmation en attente', inferred: true };
  if (e.status === 'inferred') return { text: 'À confirmer', inferred: true };
  if (deterministic && e.status === 'confirmed') return { text: 'Confirmé deux fois', inferred: false };
  return e.source ? { text: SOURCE_SHORT[e.source] ?? '', inferred: false } : undefined;
}

const SHORT: Record<string, string> = { contact_consent: 'Consentement' };
const VEHICLE_HELP = 'Le véhicule à assurer, tel que décrit en conversation ou lu sur l’immatriculation.';
const DRIVER_HELP = 'Toute personne qui conduit un véhicule de la police, même à l’occasion.';

function fieldRow(doc: unknown, path: string): Row {
  const k = keyOf(path);
  const def = field(k);
  return {
    key: path,
    label: SHORT[k] ?? labelOf(k),
    help: helpOf(k),
    value: labelFor(k, val(doc, path)),
    meta: meta(env(doc, path), def?.deterministic),
  };
}

export function people(doc: unknown) {
  const self = [val(doc, '/quote/first_name'), val(doc, '/quote/last_name')].filter(Boolean).join(' ');
  const dName = (i: number) => (i === 0 ? self || 'Vous' : String(val(doc, `/drivers/${i}/driver_name`) ?? `Conducteur ${i + 1}`));
  const vLabel = (i: number) =>
    ['vehicle_year', 'vehicle_make', 'vehicle_model']
      .map((k) => val(doc, `/vehicles/${i}/${k}`))
      .filter(Boolean)
      .join(' ') || 'Véhicule à préciser';
  return { dName, vLabel, first: (i: number) => dName(i).split(' ')[0] };
}

type Assignment = { driver_index: number; vehicle_index: number; role: string };

const QUOTE = ['/quote/quote_goal', '/quote/contact_consent'];
const YOU = [
  '/quote/first_name',
  '/quote/last_name',
  '/quote/date_of_birth',
  '/quote/email',
  '/quote/phone_number',
  '/drivers/0/driver_license_number',
];
const INSURER = ['/drivers/0/years_with_current_insurer'];
const HISTORY = ['/underwriting/has_claims', '/underwriting/has_convictions_in_past_three_years', '/underwriting/was_denied_or_cancelled'];
const SHOWN_KEYS = new Set([
  'vehicle_make',
  'vehicle_model',
  'vehicle_year',
  'usage_type',
  'driver_name',
  'relationship_code',
  'driver_date_of_birth',
  'driver_vehicle_assignments',
]);

/** Every committed path in the state document (a leaf is an object with a value). */
function leaves(node: unknown, path = ''): string[] {
  if (!node || typeof node !== 'object') return [];
  if ('value' in node) return [path];
  return Object.entries(node).flatMap(([k, v]) => leaves(v, `${path}/${k}`));
}

export function groups(doc: unknown): Group[] {
  const { dName, vLabel, first } = people(doc);
  const g = (title: string, paths: string[]): Group => ({
    title,
    n: paths.filter((p) => has(doc, p)).length,
    total: paths.length,
    rows: paths.map((p) => fieldRow(doc, p)),
  });
  const vehicles = list(doc, '/vehicles');
  const drivers = list(doc, '/drivers');
  const asg = (val(doc, '/household/driver_vehicle_assignments') ?? []) as Assignment[];
  const vRows: Row[] = vehicles.length ? [] : [{ key: 'vehicles', label: 'Véhicule', help: VEHICLE_HELP, value: '' }];
  vehicles.forEach((_, i) => {
    const principal = asg.find((a) => a.vehicle_index === i && a.role === 'principal');
    vRows.push(
      {
        key: `/vehicles/${i}/vehicle_make`,
        label: `Véhicule ${i + 1}`,
        help: VEHICLE_HELP,
        value: has(doc, `/vehicles/${i}/vehicle_make`) ? vLabel(i) : '',
        meta: meta(env(doc, `/vehicles/${i}/vehicle_make`)),
      },
      fieldRow(doc, `/vehicles/${i}/usage_type`),
      {
        key: `p${i}`,
        label: 'Conducteur principal',
        help: 'La personne qui conduit ce véhicule le plus souvent. Chaque véhicule en a un.',
        value: principal ? dName(principal.driver_index) : '',
      },
      {
        key: `o${i}`,
        label: 'Conducteurs occasionnels',
        help: 'Les autres personnes qui conduisent ce véhicule à l’occasion.',
        value: asg
          .filter((a) => a.vehicle_index === i && a.role === 'occasional')
          .map((a) => first(a.driver_index))
          .join(', '),
      },
    );
  });
  const dRows: Row[] = drivers.length ? [] : [{ key: 'drivers', label: 'Conducteurs', help: DRIVER_HELP, value: '' }];
  drivers.forEach((_, i) => {
    const rel = i === 0 ? 'Vous' : labelFor('relationship_code', val(doc, `/drivers/${i}/relationship_code`)) || 'Lien à préciser';
    const age = i ? ageFrom(val(doc, `/drivers/${i}/driver_date_of_birth`)) : null;
    const nameEnv = env(doc, i === 0 ? '/quote/first_name' : `/drivers/${i}/driver_name`);
    dRows.push({
      key: `/drivers/${i}/driver_name`,
      label: `Conducteur ${i + 1}`,
      help: DRIVER_HELP,
      value: `${dName(i)} · ${rel}${age != null ? `, ${age} ans` : ''}`,
      meta: meta(nameEnv),
    });
  });
  const known = new Set([...QUOTE, ...YOU, ...INSURER, ...HISTORY]);
  const others = leaves(doc).filter(
    (p) =>
      !known.has(p) && !SHOWN_KEYS.has(keyOf(p)) && field(keyOf(p)) && !(field(keyOf(p)) as { system?: boolean }).system && has(doc, p),
  );
  const out = [
    g('Votre demande', QUOTE),
    g('Vous', YOU),
    { title: 'Véhicules', n: vehicles.length, total: Math.max(1, vehicles.length), rows: vRows },
    { title: 'Conducteurs', n: drivers.length, total: Math.max(1, drivers.length), rows: dRows },
    g('Assurance actuelle', INSURER),
    g('Antécédents', HISTORY),
  ];
  if (others.length) out.push(g('Autres renseignements', others));
  return out;
}

/** The completion meter: the prototype's ten checkpoints, computed from state. */
export function completion(doc: unknown): number {
  const need = ['/quote/first_name', '/quote/last_name', '/quote/date_of_birth', '/quote/email', ...HISTORY];
  const nv = list(doc, '/vehicles').length;
  const nd = list(doc, '/drivers').length;
  let got = need.filter((p) => has(doc, p)).length;
  if (nv && has(doc, '/vehicles/0/usage_type')) got++;
  if (nd) got++;
  if (has(doc, '/household/driver_vehicle_assignments') || (nd <= 1 && nv === 1)) got++;
  return Math.round((got / (need.length + 3)) * 100);
}

export const filledCount = (doc: unknown) => leaves(doc).filter((p) => has(doc, p)).length;

export const STEPS = ['Documents', 'Vous', 'Véhicules', 'Conducteurs', 'Antécédents', 'Soumission'];
const STEP_OF_SURFACE: [RegExp, number][] = [
  [/^doc/, 0],
  [/^(a_propos|about|policyholder|contact)/, 1],
  [/^vehic/, 2],
  [/^(conducteurs|qui_conduit|driver|assign)/, 3],
  [/^(antecedents|claims|convictions|history)/, 4],
  [/^(offres|offers|adjust)/, 5],
];

/** Index of the current step chip (-1 before consent): the open form's subject, else the first subject not done. */
export function currentStep(s: Pick<AppState, 'doc' | 'stage' | 'quotes' | 'chips'>): number {
  const { doc } = s;
  const open = s.stage ? STEP_OF_SURFACE.find(([re]) => re.test(s.stage ?? ''))?.[1] : undefined;
  if (open != null) return open;
  if (val(doc, '/quote/contact_consent') !== true) return -1;
  const vehicles = list(doc, '/vehicles');
  const done = [
    !!s.chips.documents || has(doc, '/quote/first_name') || leaves(doc).some((p) => env(doc, p)?.source === 'document'),
    ['/quote/first_name', '/quote/last_name', '/quote/date_of_birth', '/quote/email'].every((p) => has(doc, p)),
    vehicles.length > 0 &&
      vehicles.every((_, i) =>
        ['vehicle_make', 'vehicle_model', 'vehicle_year', 'usage_type'].every((k) => has(doc, `/vehicles/${i}/${k}`)),
      ),
    has(doc, '/household/driver_vehicle_assignments') || (list(doc, '/drivers').length === 1 && vehicles.length === 1),
    HISTORY.every((p) => has(doc, p)),
    s.quotes.length > 0,
  ];
  const i = done.indexOf(false);
  return i < 0 ? 5 : i;
}
