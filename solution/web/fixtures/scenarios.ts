/* Named customer journeys against the mock BFF, shared by unit tests, the visual harness and e2e. A
   journey is a list of inbound envelopes; replay() runs it through the real store reducer. */
import type { Inbound } from '../src/app/api';
import { createStore, type Store } from '../src/app/store';
import { MockBff } from './mock-bff';
import { VINS } from './services';

export const msg = (text: string, extra: Record<string, unknown> = {}): Inbound => ({ type: 'user.message', data: { text, ...extra } });
export const act = (surfaceId: string, name: string, values: Record<string, unknown> = {}): Inbound => ({
  type: 'a2ui.action',
  data: { name, surfaceId, sourceComponentId: `action_${name}`, timestamp: '2026-11-25T12:00:00.000Z', context: { surfaceId, values } },
});
export const confirm = (id: string, step: 1 | 2, accepted = true): Inbound => ({
  type: 'confirm.response',
  data: { confirmation_id: id, step, accepted },
});
export const reopen = (surface_id: string): Inbound => ({ type: 'ui.surface_reopen', data: { surface_id } });
const both = (id: string) => [confirm(id, 1), confirm(id, 2)];

export const DOCUMENT_VALUES = {
  '/quote/first_name': 'Marie',
  '/quote/last_name': 'Tremblay',
  '/quote/date_of_birth': '1987-01-18',
  '/drivers/0/driver_license_number': 'T6512-180187-08',
  '/vehicles/0/vehicle_year': 2021,
  '/vehicles/0/vehicle_make': 'Toyota',
  '/vehicles/0/vehicle_model': 'RAV4',
  '/vehicles/0/vin': VINS.rav4,
  '/drivers/0/years_with_current_insurer': 6,
  '/drivers/1/driver_name': 'Alex Tremblay',
  '/drivers/1/relationship_code': 'spouse',
};
export const HISTORY_VALUES = {
  '/underwriting/has_claims': false,
  '/underwriting/has_convictions_in_past_three_years': false,
  '/underwriting/was_denied_or_cancelled': false,
};
export const BOARD_VALUES = {
  '/household/driver_vehicle_assignments': [
    { driver_index: 0, vehicle_index: 0, role: 'principal' },
    { driver_index: 1, vehicle_index: 0, role: 'occasional' },
  ],
};

/** Journeys, each continuing the previous one. Confirmation ids are the mock's (cf_1, cf_2...). */
export const JOURNEY = {
  consentAsked: [msg('Obtenir une soumission auto'), msg('Oui, j’accepte')],
  documentsOpen: [...both('cf_1'), msg('Oui, j’ai mes documents')],
  vehicleOpen: [act('documents', 'submit', DOCUMENT_VALUES), ...both('cf_2')],
  boardOpen: [act('vehicule_doc', 'submit', { '/vehicles/0/usage_type': 'commute' })],
  historyOpen: [act('qui_conduit_quoi', 'submit', BOARD_VALUES)],
  historyConfirm: [act('antecedents', 'submit', HISTORY_VALUES)],
  quoteReady: both('cf_3'),
  offersOpen: [msg('Obtenir ma soumission', { via: 'suggestion', action_id: 'get_quote' })],
};
export const upTo = (last: keyof typeof JOURNEY): Inbound[] => {
  const keys = Object.keys(JOURNEY) as (keyof typeof JOURNEY)[];
  return keys.slice(0, keys.indexOf(last) + 1).flatMap((k) => JOURNEY[k]);
};

/** Runs inbound envelopes through the mock and the real reducer, as the session controller would. */
export function replay(steps: Inbound[], mock = new MockBff()): { store: Store; mock: MockBff } {
  const store = createStore();
  const start = mock.createSession();
  store.dispatch({ type: 'init', sessionId: start.session_id, quoteNumber: start.quote_number });
  for (const env of start.events) store.dispatch({ type: 'envelope', env });
  store.dispatch({ type: 'turnEnd' });
  for (const inbound of steps) {
    const text = inbound.type === 'user.message' ? String(inbound.data.text) : undefined;
    store.dispatch({ type: 'turnStart', inbound, userText: text, typing: inbound.type === 'user.message' });
    if (inbound.type === 'confirm.response') store.dispatch({ type: 'modal', step: inbound.data.step === 1 ? 2 : null });
    for (const env of mock.handle(inbound)) store.dispatch({ type: 'envelope', env });
    store.dispatch({ type: 'turnEnd' });
    store.dispatch({ type: 'snapshot', snapshot: mock.snapshot() });
  }
  return { store, mock };
}
