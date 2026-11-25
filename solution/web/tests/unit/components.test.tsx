/* Every A2UI component, mounted from the compiled fixtures: rendering, binding, validation, events,
   and the accessibility basics (roles, labels, keyboard on the board). */
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { VINS } from '../../fixtures/services';
import { type FixtureProps, FixtureSurface, fakeHost } from '../../fixtures/surface';
import { createSurface, SURFACES } from '../../fixtures/surfaces';
import { get } from '../../src/a2ui/pointer';
import { applyMessage, emptySurfaces, type SurfaceState } from '../../src/a2ui/reducer';
import { FormCard } from '../../src/a2ui/Surface';
import { BASIC_CATALOG_ID } from '../../src/a2ui/types';

function mount(id: string, props: Omit<FixtureProps, 'id' | 'host'> = {}, pendingUploads = false) {
  const host = fakeHost(pendingUploads);
  const ref: { s?: SurfaceState } = {};
  const utils = render(<FixtureSurface id={id} host={host} onState={(s) => (ref.s = s)} {...props} />);
  return { ...utils, host, value: (p: string) => get(ref.s?.dataModel, p), user: userEvent.setup() };
}
const submit = (name = /Continuer/) => screen.getByRole('button', { name });

describe('form card', () => {
  it('renders the title, the French labels from the catalog, help, hints and the close button', () => {
    mount('a_propos');
    expect(screen.getByRole('dialog', { name: 'À propos de vous' })).toBeInTheDocument();
    expect(screen.getByLabelText(/Prénom/)).toHaveValue('Marie');
    expect(screen.getByText('Sert à établir votre expérience de conduite.')).toBeInTheDocument();
    expect(screen.getAllByRole('img', { name: /^Aide : / }).length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: 'Fermer le formulaire et répondre dans la conversation' })).toBeInTheDocument();
  });

  it('blocks an invalid submit, shows every error, counts them and focuses the first', async () => {
    const { host, user } = mount('a_propos');
    await user.click(submit());
    expect(host.actions).toEqual([]);
    expect(screen.getByText('2 champs à corriger')).toBeInTheDocument();
    expect(screen.getAllByRole('alert').filter((a) => a.textContent === 'Ce champ est obligatoire')).toHaveLength(2);
    await waitFor(() => expect(screen.getByLabelText(/Date de naissance/)).toHaveFocus());
  });

  it('sends a valid submit as an A2UI action with the flat bound values', async () => {
    const { host, user } = mount('a_propos');
    fireEvent.change(screen.getByLabelText(/Date de naissance/), { target: { value: '1987-01-18' } });
    await user.type(screen.getByLabelText(/Adresse courriel/), 'marie@exemple.com');
    await user.click(submit());
    expect(host.actions).toHaveLength(1);
    expect(host.actions[0]).toMatchObject({
      name: 'submit',
      surfaceId: 'a_propos',
      sourceComponentId: 'action_submit',
      context: {
        surfaceId: 'a_propos',
        values: {
          '/quote/first_name': 'Marie',
          '/quote/last_name': 'Tremblay',
          '/quote/date_of_birth': '1987-01-18',
          '/quote/email': 'marie@exemple.com',
        },
      },
    });
  });

  it('closes without validating', async () => {
    const { host, user } = mount('a_propos');
    await user.click(screen.getByRole('button', { name: /Fermer le formulaire/ }));
    expect(host.actions[0]).toMatchObject({ name: 'dismiss', surfaceId: 'a_propos' });
  });

  it('shows provenance badges, a server error under /_errors, and offers a live value instead of overwriting an edit', async () => {
    mount('contact', { data: { '/_errors': { '/quote/phone_number': 'Ce numéro n’est pas en service.' } } });
    expect(screen.getAllByText('Tiré de la conversation').length).toBeGreaterThan(0);
    expect(screen.getByText('Ce numéro n’est pas en service.')).toBeInTheDocument();
  });

  it('accepts a suggested value on a field the customer edited (FR-A2UI-7)', async () => {
    const { value, user } = mount('a_propos', {
      local: [{ type: 'set', surfaceId: 'a_propos', path: '/quote/first_name', value: 'Mari' }],
      extra: [{ version: '1.0', updateDataModel: { surfaceId: 'a_propos', path: '/quote/first_name', value: 'Marie-Ève' } }],
    });
    expect(screen.getByLabelText(/Prénom/)).toHaveValue('Mari');
    expect(screen.getByText('Marie-Ève')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Utiliser' }));
    expect(value('/quote/first_name')).toBe('Marie-Ève');
  });

  it('renders a safe fallback for an unknown component, never its content', () => {
    const { container } = mount('a_propos', {
      extra: [
        {
          version: '1.0',
          updateComponents: { surfaceId: 'a_propos', components: [{ id: 'x', component: 'FancyWidget', label: '<b>boom</b>' }] },
        },
      ],
      component: 'x',
    });
    expect(screen.getByText('Cet élément ne peut pas être affiché.')).toBeInTheDocument();
    expect(container.textContent).not.toContain('boom');
  });
});

describe('basic components', () => {
  it('TextField: formats postal code, phone and licence on blur and checks the value', async () => {
    const { value, user } = mount('address', { component: 'address_lookup_1' });
    const postal = screen.getByLabelText(/Code postal/);
    await user.type(postal, 'h2x1y4');
    await user.tab();
    expect(value('/quote/postal_code')).toBe('H2X 1Y4');
    await user.clear(postal);
    await user.type(postal, 'Z9Z 9Z9');
    await user.tab();
    expect(screen.getByText('Entrez un code postal canadien, comme H2X 1Y4')).toBeInTheDocument();
  });

  it('TextField: phone and licence formatting, currency as a number, protected badge', async () => {
    const c = mount('contact', { component: 'phone_2' });
    const phone = screen.getByLabelText(/Numéro de téléphone/);
    await c.user.clear(phone);
    await c.user.type(phone, '5145550142');
    await c.user.tab();
    expect(c.value('/quote/phone_number')).toBe('(514) 555-0142');
    c.unmount();
    const l = mount('driver_0_licence', { component: 'text_1' });
    expect(screen.getByText('confirmé deux fois')).toBeInTheDocument();
    await l.user.type(screen.getByLabelText(/Numéro de permis/), 't6512-180187-08');
    await l.user.tab();
    expect(l.value('/drivers/0/driver_license_number')).toBe('T6512-180187-08');
    l.unmount();
    const v = mount('vehicle_1', { component: 'currency_3' });
    await v.user.type(screen.getByLabelText(/Valeur du véhicule/), '32500');
    expect(v.value('/vehicles/0/vehicle_value')).toBe(32500);
  });

  it('TextField longText and DateTimeInput bind their values', async () => {
    const t = mount('end_of_form', { component: 'textarea_9' });
    expect(screen.getByLabelText(/Résumé de la conversation/).tagName).toBe('TEXTAREA');
    t.unmount();
    const d = mount('goal', { component: 'date_2' });
    fireEvent.change(screen.getByLabelText(/Date de début/), { target: { value: '2026-12-01' } });
    expect(d.value('/quote/effective_date')).toBe('2026-12-01');
  });

  it('ChoicePicker: chips and segmented are radiogroups; booleans and multi-select keep their types', async () => {
    const c = mount('vehicule_0', { component: 'chips_2' });
    const group = screen.getByRole('radiogroup', { name: 'Utilisation principale' });
    await c.user.click(within(group).getByRole('radio', { name: 'Affaires' }));
    expect(c.value('/vehicles/0/usage_type')).toBe('business');
    expect(within(group).getByRole('radio', { name: 'Affaires' })).toHaveAttribute('aria-checked', 'true');
    c.unmount();
    const b = mount('antecedents', { component: 'toggle_1' });
    await b.user.click(screen.getByRole('radio', { name: 'Non' }));
    expect(b.value('/underwriting/has_claims')).toBe(false);
    b.unmount();
    const m = mount('adjust_q_1042', { component: 'chips_2' });
    await m.user.click(screen.getByRole('checkbox', { name: 'Pardon d’accident' }));
    expect(m.value('/underwriting/add_ons')).toEqual(['roadside', 'accident_forgiveness']);
  });

  it('ChoicePicker: select, radio cards and time slots', async () => {
    const s = mount('policyholder', { component: 'select_5' });
    const select = screen.getByRole('combobox');
    await s.user.selectOptions(select, within(select).getAllByRole('option')[2]);
    expect(s.value('/quote/gender') ?? s.value('/quote/phone_type')).toBeTruthy();
    s.unmount();
    const r = mount('vehicle_1_parking', { component: 'radio_1' });
    const radios = screen.getAllByRole('radio');
    await r.user.click(radios[1]);
    expect(radios[1]).toHaveAttribute('aria-checked', 'true');
    r.unmount();
    mount('callback', { component: 'time_slots_2' });
    expect(screen.getByRole('radiogroup').className).toBe('slots');
  });

  it('CheckBox stores true or nothing; Slider writes a number and shows it in fr-CA', async () => {
    const c = mount('end_of_form', { component: 'checkbox_10' });
    await c.user.click(screen.getByRole('checkbox'));
    expect(c.value('/quote/handoff_consent')).toBe(true);
    await c.user.click(screen.getByRole('checkbox'));
    expect(c.value('/quote/handoff_consent')).toBeNull();
    c.unmount();
    const s = mount('vehicle_1_usage', { component: 'slider_2' });
    fireEvent.change(screen.getByRole('slider'), { target: { value: '18000' } });
    expect(s.value('/vehicles/0/annual_kilometers')).toBe(18000);
    expect(screen.getByText(/18\s000 km/)).toBeInTheDocument();
  });

  it('Summary lists items with French source badges', () => {
    mount('review', { component: 'summary_1' });
    expect(screen.getByText('Adresse')).toBeInTheDocument();
    expect(screen.getByText('Recherche')).toBeInTheDocument();
  });
});

describe('insurance components', () => {
  it('YmmPicker cascades through lookupMakeModelYear and sets the vehicle type from identifyVehicleType', async () => {
    const { host, value, user } = mount('vehicle_1', {
      component: 'ymm_1',
      data: { '/vehicles/0/vehicle_year': null, '/vehicles/0/vehicle_make': null, '/vehicles/0/vehicle_model': null },
    });
    expect(screen.getByRole('combobox', { name: 'Marque' })).toBeDisabled();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Année' }), '2021');
    await waitFor(() => expect(screen.getByRole('combobox', { name: 'Marque' })).toBeEnabled());
    await user.selectOptions(screen.getByRole('combobox', { name: 'Marque' }), 'Toyota');
    await waitFor(() => expect(screen.getByRole('option', { name: 'RAV4' })).toBeInTheDocument());
    await user.selectOptions(screen.getByRole('combobox', { name: 'Modèle' }), 'RAV4');
    await screen.findByText(/Valide : 2021 Toyota RAV4/);
    expect(value('/vehicles/0/vehicle_type')).toBe('suv');
    expect(host.calls.map((c) => c[0])).toContain('identifyVehicleType');
  });

  it('YmmPicker: changing the make clears the model; an invalid combination blocks submit', async () => {
    const { value, user, host } = mount('vehicule_0', {
      data: {
        '/vehicles/0/vehicle_year': 2018,
        '/vehicles/0/vehicle_make': 'Tesla',
        '/vehicles/0/vehicle_model': 'Model Y',
        '/vehicles/0/usage_type': 'commute',
      },
    });
    await screen.findByText('Tesla n’offre pas de Model Y en 2018');
    await user.click(submit());
    expect(host.actions).toEqual([]);
    expect(screen.getByText('Choisissez une année, une marque et un modèle valides')).toBeInTheDocument();
    await user.selectOptions(screen.getByRole('combobox', { name: 'Marque' }), 'Toyota');
    expect(value('/vehicles/0/vehicle_model')).toBeNull();
  });

  it('VinField counts characters, checks the digit and decodes a valid VIN', async () => {
    const { user } = mount('vehicle_1', { component: 'vin_2' });
    const vin = screen.getByRole('textbox', { name: /NIV/ });
    await user.type(vin, '2t3p1rfv3mw1234');
    expect(screen.getByText('Encore 2 caractères')).toBeInTheDocument();
    await user.type(vin, '56');
    await screen.findByText(/Chiffre de contrôle valide · 2021 Toyota RAV4/);
    await user.clear(vin);
    await user.type(vin, '2T3P1RFV0MW123456');
    expect(screen.getByText('Le chiffre de contrôle ne correspond pas : vérifiez le NIV')).toBeInTheDocument();
  });

  it('VinField photo action: upload, extract, fill only that row with a document badge (FR-A2UI-8)', async () => {
    const { host, value, user } = mount('fleet_vins', { component: 'repeater_2' });
    const inputs = document.querySelectorAll<HTMLInputElement>('input[type=file]');
    expect(inputs).toHaveLength(10);
    await user.upload(inputs[3], new File(['x'], 'niv.jpg', { type: 'image/jpeg' }));
    await waitFor(() => expect(value('/vehicles/3/vin')).toBe(VINS.civic));
    expect(value('/vehicles/2/vin')).toBeUndefined();
    expect(host.uploads).toEqual([['niv.jpg', 'vin']]);
  });

  it('AddressLookup searches identifyAddress and fills the four fields with a lookup badge', async () => {
    const { value, user } = mount('address', { component: 'address_lookup_1' });
    await user.type(screen.getByRole('textbox', { name: 'Recherche d’adresse' }), '125 des Érables{Enter}');
    const first = await screen.findByRole('radio', { name: /Laval/ });
    await user.click(first);
    expect(value('/quote/city')).toBe('Laval');
    expect(value('/quote/postal_code')).toBe('H7N 1A1');
    expect(screen.getByText('Trouvé par recherche')).toBeInTheDocument();
  });

  it('Repeater adds and removes rows with relative bindings', async () => {
    const { value, user } = mount('conducteurs', { sample: true });
    expect(screen.getAllByText(/^Conducteur \d$/)).toHaveLength(2);
    await user.click(screen.getByRole('button', { name: 'Retirer Conducteur 1' }));
    expect(screen.getAllByText(/^Conducteur \d$/)).toHaveLength(1);
    expect(value('/drivers/1/driver_name')).toBe('Léo Tremblay');
    await user.click(screen.getByRole('button', { name: /Ajouter un conducteur/ }));
    expect(screen.getAllByText(/^Conducteur \d$/)).toHaveLength(2);
  });

  it('AssignmentMatrix keeps one principal per vehicle', async () => {
    const { value, user } = mount('affectations_matrice', { component: 'matrix_1' });
    await user.selectOptions(screen.getByRole('combobox', { name: 'Alex, RAV4' }), 'principal');
    expect(value('/household/driver_vehicle_assignments')).toEqual([
      { driver_index: 0, vehicle_index: 0, role: 'occasional' },
      { driver_index: 1, vehicle_index: 0, role: 'principal' },
    ]);
  });

  it('AssignmentBoard: keyboard only, then tap only, and the missing principal blocks submit (FR-A2UI-14)', async () => {
    const { value, user, host } = mount('qui_conduit_quoi');
    await user.click(submit(/Confirmer/));
    expect(host.actions).toEqual([]);
    expect(screen.getByText('Chaque véhicule a besoin d’un conducteur principal')).toBeInTheDocument();
    expect(document.querySelector('.slot.principal')).toHaveClass('missing');
    // keyboard: focus a person, Enter, focus a slot, Enter
    const marie = screen.getByRole('button', { name: /Marie Tremblay/ });
    marie.focus();
    await user.keyboard('{Enter}');
    expect(marie).toHaveAttribute('aria-pressed', 'true');
    screen.getByRole('button', { name: 'Conducteur principal, RAV4' }).focus();
    await user.keyboard('{Enter}');
    // tap: a person, then a slot
    await user.click(screen.getByRole('button', { name: /Alex Tremblay/ }));
    await user.click(screen.getByRole('button', { name: 'Conducteurs occasionnels, RAV4' }));
    expect(value('/household/driver_vehicle_assignments')).toEqual([
      { driver_index: 0, vehicle_index: 0, role: 'principal' },
      { driver_index: 1, vehicle_index: 0, role: 'occasional' },
    ]);
    expect(screen.getByText(/Principal · RAV4/)).toBeInTheDocument();
    await user.click(submit(/Confirmer/));
    expect(host.actions[0].context.values).toEqual({
      '/household/driver_vehicle_assignments': value('/household/driver_vehicle_assignments'),
    });
  });

  it('AssignmentBoard: a new principal moves the previous one to occasional; × removes', async () => {
    const { value, user } = mount('assignments', { component: 'assignment_board_1' });
    await user.click(screen.getByRole('button', { name: /Léo Tremblay/ }));
    await user.click(screen.getByRole('button', { name: 'Conducteur principal, RAV4' }));
    expect(screen.getByText('Marie devient conducteur occasionnel de RAV4.')).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Retirer Marie' }));
    const arr = value('/household/driver_vehicle_assignments') as { driver_index: number; role: string }[];
    expect(arr.find((a) => a.driver_index === 0)).toBeUndefined();
  });

  it('OfferCards: radiogroup with the recommended tag; selecting fires select', async () => {
    const { host, user } = mount('offers_q_1a2b3c4d_v1');
    expect(screen.getByText('Recommandé')).toBeInTheDocument();
    await user.click(submit(/Choisir cette offre/));
    expect(screen.getAllByRole('alert').map((a) => a.textContent)).toContain('Choisissez une offre');
    await user.click(screen.getByRole('radio', { name: /Plus/ }));
    await user.click(submit(/Choisir cette offre/));
    expect(host.actions[0]).toMatchObject({ name: 'select', context: { values: { '/underwriting/offer_type': 'plus' } } });
  });

  it('DocumentDrop: upload, read, review with confidence, edit, and nothing leaves before submit (D18)', async () => {
    const { host, value, user } = mount('documents');
    const tiles = document.querySelectorAll<HTMLInputElement>('.doc input[type=file]');
    await user.upload(tiles[0], new File(['x'], 'permis.jpg', { type: 'image/jpeg' }));
    await screen.findByText('Ce que j’ai trouvé');
    expect(screen.getByText('4 informations lues')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: 'Prénom' })).toHaveValue('Marie');
    expect(screen.getAllByText('99 %')).toHaveLength(2);
    expect(host.calls.find((c) => c[0] === 'extractDocument')?.[1]).toEqual({ document_id: 'doc_1', purpose: 'licence' });
    await user.clear(screen.getByRole('textbox', { name: 'Prénom' }));
    await user.type(screen.getByRole('textbox', { name: 'Prénom' }), 'Marie-Ève');
    expect(value('/quote/first_name')).toBe('Marie-Ève');
    expect(host.actions).toEqual([]);
    await user.click(screen.getByRole('button', { name: 'Utiliser ces informations' }));
    expect(host.actions[0].context.values).toMatchObject({
      '/quote/first_name': 'Marie-Ève',
      '/drivers/0/driver_license_number': 'T6512-180187-08',
    });
  });

  it('DocumentDrop: rejects other file types and too large files, marks "Je ne l’ai pas"', async () => {
    const { host, user } = mount('documents');
    const input = document.querySelectorAll<HTMLInputElement>('.doc input[type=file]')[0];
    fireEvent.change(input, { target: { files: [new File(['x'], 'notes.txt', { type: 'text/plain' })] } });
    expect(await screen.findByText('Formats acceptés : PDF, JPEG, PNG ou HEIC.')).toBeInTheDocument();
    const big = new File(['x'], 'big.pdf', { type: 'application/pdf' });
    Object.defineProperty(big, 'size', { value: 11 * 1024 * 1024 });
    fireEvent.change(input, { target: { files: [big] } });
    expect(await screen.findByText('Le fichier dépasse 10 Mo.')).toBeInTheDocument();
    expect(host.uploads).toEqual([]);
    await user.click(within(document.querySelector('[data-doc="policy"]') as HTMLElement).getByRole('button', { name: 'Je ne l’ai pas' }));
    expect(document.querySelector('[data-doc="policy"]')).toHaveClass('missing');
  });

  it('DocumentDrop shows the reading state while the document agent works', async () => {
    mount('documents', {}, true);
    const input = document.querySelectorAll<HTMLInputElement>('.doc input[type=file]')[1];
    await act(async () => {
      fireEvent.change(input, { target: { files: [new File(['x'], 'immat.jpg', { type: 'image/jpeg' })] } });
    });
    expect(document.querySelector('[data-doc="registration"]')).toHaveClass('reading');
  });

  it('FileDrop reads a document and fills its targets', async () => {
    const { value, user } = mount('doc_licence');
    await user.upload(
      document.querySelector<HTMLInputElement>('.drop input[type=file]') as HTMLInputElement,
      new File(['x'], 'permis.jpg', { type: 'image/jpeg' }),
    );
    await waitFor(() => expect(value('/drivers/0/driver_license_number')).toBe('T6512-180187-08'));
    expect(screen.getByRole('button', { name: /Remplacer le fichier/ })).toBeInTheDocument();
  });

  it('insurance components fall back on a basic-catalog surface', () => {
    const msg = createSurface('offers_q_1a2b3c4d_v1');
    if (msg.createSurface) msg.createSurface.catalogId = BASIC_CATALOG_ID;
    const surface = applyMessage(emptySurfaces(), msg).byId.offers_q_1a2b3c4d_v1;
    render(<FormCard surface={surface} dispatch={() => undefined} host={fakeHost()} />);
    expect(screen.queryByRole('radiogroup')).toBeNull();
    expect(screen.getByText('Cet élément ne peut pas être affiché.')).toBeInTheDocument();
  });
});

describe('mcp-a2ui output', () => {
  it.each(Object.keys(SURFACES))('renders %s from the golden fixtures with no unknown component', (id) => {
    const { container, unmount } = mount(id);
    expect(screen.getByRole('dialog')).toBeInTheDocument();
    expect(container.querySelector('[data-unknown]')).toBeNull();
    unmount();
  });

  it('shows provenance on YMM sub-paths and repeater items', () => {
    mount('vehicule_0', {
      data: {
        '/_provenance/~1vehicles~10~1vehicle_year': null,
        '/_provenance/~1vehicles~10~1vehicle_make': { source: 'document', note: '97 %' },
      },
      component: 'ymm_1',
    });
    expect(screen.getByText('97 %')).toBeInTheDocument();
    cleanup();
    mount('conducteurs', {
      sample: true,
      data: { '/_provenance/~1drivers~12~1driver_name': { source: 'chat', note: '« Léo »' } },
      component: 'repeater_1',
    });
    expect(screen.getByText('« Léo »')).toBeInTheDocument();
  });

  it('OfferCards.showAdjust asks to adjust the quote with an action', async () => {
    const { host, user } = mount('offers_q_1a2b3c4d_v1', { data: { '/underwriting/offer_type': 'plus' } });
    await user.click(screen.getByRole('button', { name: 'Ajuster ma soumission' }));
    expect(host.actions[0]).toMatchObject({
      name: 'adjust',
      sourceComponentId: 'offer_cards_1',
      context: { values: { '/underwriting/offer_type': 'plus' } },
    });
  });
});
