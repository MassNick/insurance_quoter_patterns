/* One renderer per visual element (src/visual/list.ts), all built from the shared fixtures: compiled
   surfaces, fake functions and mock-BFF journeys. */
import type { ReactNode } from 'react';
import { type MockBff, mockApi } from '../../fixtures/mock-bff';
import { act, msg, replay, upTo } from '../../fixtures/scenarios';
import { type FixtureProps, FixtureSurface, fakeHost } from '../../fixtures/surface';
import type { A2Component, A2uiMessage } from '../a2ui/types';
import type { Inbound } from '../app/api';
import { App } from '../app/components/App';
import { Chat, FormChip } from '../app/components/Chat';
import { ConfirmModal } from '../app/components/Overlays';
import { Dossier, Header, Rail, Steps } from '../app/components/Shell';
import { AppContext } from '../app/context';
import { Session } from '../app/session';
import type { Chip, Store } from '../app/store';

export interface Scene {
  render: () => ReactNode;
  /** Runs after the first paint (clicks, uploads); the screenshot waits for it. */
  play?: (root: HTMLElement) => Promise<void>;
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));

type SurfaceProps = Omit<FixtureProps, 'host'> & { pendingUploads?: boolean };
const SurfaceScene = ({ pendingUploads, ...p }: SurfaceProps) => <FixtureSurface host={fakeHost(pendingUploads)} {...p} />;
const one = (id: string, component: string, more: Omit<SurfaceProps, 'id' | 'component'> = {}): Scene => ({
  render: () => <SurfaceScene id={id} component={component} {...more} />,
});
const form = (id: string, more: Omit<SurfaceProps, 'id'> = {}, play?: Scene['play']): Scene => ({
  render: () => <SurfaceScene id={id} {...more} />,
  play,
});

/** Feeds a file to the n-th file input, as a customer choosing a photo would. */
async function upload(root: HTMLElement, n: number, name = 'photo.jpg') {
  const input = root.querySelectorAll<HTMLInputElement>('input[type=file]')[n];
  const dt = new DataTransfer();
  dt.items.add(new File(['fake image bytes'], name, { type: 'image/jpeg' }));
  input.files = dt.files;
  input.dispatchEvent(new Event('change', { bubbles: true }));
  await wait(80);
}
const click = async (root: HTMLElement, selector: string) => {
  root.querySelector<HTMLElement>(selector)?.click();
  await wait(80);
};

function withSession(store: Store, mock: MockBff, node: ReactNode) {
  return <AppContext.Provider value={new Session(store, mockApi(mock))}>{node}</AppContext.Provider>;
}
function journey(steps: Inbound[], node: (store: Store) => ReactNode, edit?: (store: Store) => void): Scene {
  return {
    render: () => {
      const { store, mock } = replay(steps);
      edit?.(store);
      return withSession(store, mock, node(store));
    },
  };
}
const shell = (children: ReactNode, height = 640) => (
  <div className="app" style={{ height }}>
    {children}
  </div>
);
const ws = (children: ReactNode, height = 560) => (
  <main className="ws" style={{ height, flex: 'none' }}>
    {children}
  </main>
);

const chip = (surfaceId: string, label: string, status: Chip['status'], locked = false): Chip => ({
  surfaceId,
  label,
  status,
  prev: status,
  edit: false,
  locked,
  messageId: 'm',
});
const UNKNOWN: A2uiMessage = {
  version: '1.0',
  updateComponents: {
    surfaceId: 'a_propos',
    components: [{ id: 'mystery', component: 'FancyWidget', label: '<b>should not render</b>' } as A2Component],
  },
};
const LAMBO_SENT: Inbound[] = [
  msg('Je conduis une Lamborghini Huracán 2023'),
  msg('Oui, c’est bien ça', { via: 'suggestion', action_id: 'rule_confirm' }),
  act('end_of_form', 'send', { '/quote/phone_number': '(514) 555-0142', '/quote/handoff_consent': true }),
];

export const SCENES: Record<string, Scene> = {
  'component-text-heading': one('full_form', 'section_title_1'),
  'component-note-info': one('consent', 'info_1'),
  'component-note-chat': one('documents', 'intro'),
  'component-note-warn': one('end_of_form', 'info_1'),
  'component-textfield-empty': one('a_propos', 'email_5'),
  'component-textfield-provenance': one('a_propos', 'text_1'),
  'component-textfield-error': one('contact', 'email_1', { data: { '/quote/email': 'marie@exemple' }, errors: true }),
  'component-textfield-currency': one('vehicle_1', 'currency_3', { data: { '/vehicles/0/vehicle_value': 32500 } }),
  'component-textfield-licence': one('driver_0_licence', 'text_1', { data: { '/drivers/0/driver_license_number': 'T6512-180187-08' } }),
  'component-textfield-longtext': one('end_of_form', 'textarea_9'),
  'component-textfield-suggestion': one('a_propos', 'text_1', {
    local: [{ type: 'set', surfaceId: 'a_propos', path: '/quote/first_name', value: 'Mari' }],
    extra: [{ version: '1.0', updateDataModel: { surfaceId: 'a_propos', path: '/quote/first_name', value: 'Marie-Ève' } }],
  }),
  'component-textfield-server-error': one('contact', 'phone_2', {
    data: { '/quote/phone_number': '(514) 555-0000', '/_errors': { '/quote/phone_number': 'Ce numéro n’est pas en service.' } },
  }),
  'component-datetime': one('goal', 'date_2', { data: { '/quote/effective_date': '2026-12-01' } }),
  'component-choice-chips': one('vehicule_0', 'chips_2', { data: { '/vehicles/0/usage_type': 'commute' } }),
  'component-choice-chips-multiple': one('adjust_q_1042', 'chips_2'),
  'component-choice-select': one('policyholder', 'select_5', { sample: true }),
  'component-choice-radio': one('vehicle_1_parking', 'radio_1', { sample: true }),
  'component-choice-segmented': one('antecedents', 'toggle_1', { data: { '/underwriting/has_claims': false } }),
  'component-choice-slots': one('callback', 'time_slots_2', { sample: true }),
  'component-checkbox': one('end_of_form', 'checkbox_10', { data: { '/quote/handoff_consent': true } }),
  'component-checkbox-protected': one('consent', 'checkbox_2'),
  'component-slider': one('vehicle_1_usage', 'slider_2', { data: { '/vehicles/0/annual_kilometers': 18000 } }),
  'component-ymm-empty': one('vehicle_1', 'ymm_1', {
    data: { '/vehicles/0/vehicle_year': null, '/vehicles/0/vehicle_make': null, '/vehicles/0/vehicle_model': null },
  }),
  'component-ymm-valid': one('vehicule_doc', 'ymm_1'),
  'component-ymm-invalid': one('vehicule_0', 'ymm_1', {
    data: { '/vehicles/0/vehicle_year': 2018, '/vehicles/0/vehicle_make': 'Tesla', '/vehicles/0/vehicle_model': 'Model Y' },
  }),
  'component-vin-valid': one('vehicle_1', 'vin_2', { data: { '/vehicles/0/vin': '2T3P1RFV3MW123456' } }),
  'component-vin-invalid': one('vehicle_1', 'vin_2', { data: { '/vehicles/0/vin': '2T3P1RFV0MW123456' } }),
  'component-address-candidates': {
    ...one('address', 'address_lookup_1'),
    play: async (root) => {
      await click(root, '.input-wrap .btn');
      await click(root, '.radio-card');
    },
  },
  'component-repeater-grid': one('conducteurs', 'repeater_1', { sample: true }),
  'component-repeater-vin-list': one('fleet_vins', 'repeater_2'),
  'component-matrix': one('affectations_matrice', 'matrix_1'),
  'component-board-empty': one('qui_conduit_quoi', 'assignment_board_1'),
  'component-board-assigned': one('assignments', 'assignment_board_1'),
  'component-board-missing': one('qui_conduit_quoi', 'assignment_board_1', { errors: true }),
  'component-offers': one('offers_q_1a2b3c4d_v1', 'offer_cards_1', { data: { '/underwriting/offer_type': 'plus' } }),
  'component-summary': one('review', 'summary_1'),
  'component-documents-idle': one('documents', 'document_drop_1'),
  'component-documents-reading': {
    ...one('documents', 'document_drop_1', { pendingUploads: true }),
    play: (root) => upload(root, 0, 'permis.jpg'),
  },
  'component-documents-review': {
    ...one('documents', 'document_drop_1'),
    play: async (root) => {
      await upload(root, 0, 'permis.jpg');
      await upload(root, 1, 'immatriculation.jpg');
      await upload(root, 2, 'police.pdf');
    },
  },
  'component-documents-missing': { ...one('documents', 'document_drop_1'), play: (root) => click(root, '.doc[data-doc="policy"] .skip') },
  'component-filedrop': { ...one('doc_licence', 'file_1'), play: (root) => upload(root, 0, 'permis.jpg') },
  'component-unknown': one('a_propos', 'mystery', { extra: [UNKNOWN] }),
  'form-documents': form('documents'),
  'form-about': form('a_propos'),
  'form-vehicle': form('vehicule_doc'),
  'form-drivers': form('conducteurs', { sample: true }),
  'form-board': form('qui_conduit_quoi'),
  'form-history': form('antecedents'),
  'form-history-errors': form('antecedents', {}, (root) => click(root, '.form-foot .btn.primary')),
  'form-offers': form('offers_q_1a2b3c4d_v1'),
  'form-end-of-form': form('end_of_form', { sample: true }),
  'form-end-of-form-sent': journey(
    LAMBO_SENT,
    () => <App dev={false} />,
    (store) =>
      store.dispatch({
        type: 'surface',
        action: { type: 'set', surfaceId: 'end_of_form', path: '/quote/phone_number', value: '(514) 555-0142' },
      }),
  ),
  chips: journey([], () => (
    <div className="msg bot" style={{ padding: 16 }}>
      <div className="bubble">
        <FormChip chip={chip('a_propos', 'Formulaire : À propos de vous', 'open')} />
        <br />
        <FormChip chip={chip('vehicule_doc', 'Formulaire : Votre véhicule', 'submitted')} />
        <br />
        <FormChip chip={chip('documents', 'Formulaire : Vos documents', 'closed')} />
        <br />
        <FormChip chip={chip('end_of_form', 'Formulaire : Poursuivons avec un conseiller', 'sent', true)} />
        <br />
        <FormChip chip={chip('antecedents', 'Formulaire : Vos antécédents', 'submitted', true)} />
      </div>
    </div>
  )),
  suggestions: journey(upTo('consentAsked').slice(0, 1), () => ws(<Chat style={{ left: 0, width: '100%' }} pushed={false} />)),
  header: journey([], () => shell(<Header />, 64)),
  steps: journey(upTo('vehicleOpen'), () => shell(<Steps />, 48)),
  rail: journey(upTo('vehicleOpen'), () => ws(<Rail />)),
  transcript: journey(
    upTo('documentsOpen'),
    () => ws(<Chat style={{ left: 0, width: '100%' }} pushed={false} />, 900),
    (store) => {
      store.dispatch({
        type: 'envelope',
        env: {
          type: 'text.done',
          actor: 'main_agent',
          data: {
            text: 'La **protection collision** paie la réparation de votre véhicule après un accident [1].',
            citations: [{ title: 'Guide des protections auto, section 3.1' }],
          },
        },
      });
      store.dispatch({ type: 'system', text: 'Modifications enregistrées : Votre véhicule.' });
      store.dispatch({ type: 'turnStart', inbound: msg('Et la franchise?'), userText: 'Et la franchise?', typing: true });
      store.dispatch({ type: 'turnFailed' });
    },
  ),
  'dossier-states': journey(
    upTo('offersOpen'),
    () => ws(<Dossier />, 900),
    (s) => s.dispatch({ type: 'dossier', open: true }),
  ),
  'dossier-quotes': journey(
    upTo('offersOpen'),
    () => ws(<Dossier />, 700),
    (s) => s.dispatch({ type: 'dossier', open: true, tab: 'quotes' }),
  ),
  'modal-step-1': journey(upTo('consentAsked'), () => <ConfirmModal />),
  'modal-step-2': journey(
    upTo('historyConfirm'),
    () => <ConfirmModal />,
    (s) => s.dispatch({ type: 'modal', step: 2 }),
  ),
  'full-form': journey(
    [...upTo('vehicleOpen'), act('vehicule_doc', 'dismiss'), { type: 'ui.surface_reopen', data: { surface_id: 'full_form' } }],
    () => <App dev={false} />,
  ),
  'app-chat-form': journey(upTo('vehicleOpen'), () => <App dev={false} />),
};
