/* Pointer helpers, the A2UI reducer, @checks and the tree walk (validate, collect). */
import { describe, expect, it } from 'vitest';
import { createSurface } from '../../fixtures/surfaces';
import { evalCall, firstError, vinValid, ymmKey } from '../../src/a2ui/checks';
import { escapeKey, get, provPath, remove, set } from '../../src/a2ui/pointer';
import { applyMessage, emptySurfaces, type SurfacesState, surfacesReducer } from '../../src/a2ui/reducer';
import { boundFields, collect, validate } from '../../src/a2ui/tree';
import { BASIC_CATALOG_ID, type CheckRule } from '../../src/a2ui/types';

const open = (id: string): SurfacesState => applyMessage(emptySurfaces(), createSurface(id));
const udm = (surfaceId: string, path: string, value: unknown) => ({
  version: '1.0',
  updateDataModel: { surfaceId, path, value: value as never },
});

describe('pointer', () => {
  it('reads, writes immutably, deletes and escapes', () => {
    const a = { quote: { first_name: 'Marie' } };
    const b = set(a, '/vehicles/0/vin', 'X');
    expect(a).toEqual({ quote: { first_name: 'Marie' } });
    expect(b).toEqual({ quote: { first_name: 'Marie' }, vehicles: [{ vin: 'X' }] });
    expect(get(b, '/vehicles/0/vin')).toBe('X');
    expect(remove(b, '/quote/first_name')).toEqual({ quote: {}, vehicles: [{ vin: 'X' }] });
    expect(provPath('/quote/a~b')).toBe('/_provenance/~1quote~1a~0b');
    expect(get({ '/quote/x': 1 }, `/${escapeKey('/quote/x')}`)).toBe(1);
  });
});

describe('A2UI reducer', () => {
  it('opens a surface from one createSurface with components and data model', () => {
    const s = open('vehicule_doc').byId.vehicule_doc;
    expect(s.catalogId).toContain('insurance-catalog');
    expect(s.components.ymm_1.component).toBe('YmmPicker');
    expect(get(s.dataModel, '/vehicles/0/vehicle_make')).toBe('Toyota');
  });

  it('rejects another protocol version and an unknown catalog', () => {
    const v = applyMessage(emptySurfaces(), { ...createSurface('a_propos'), version: '0.9' });
    expect(v.byId).toEqual({});
    expect(v.rejected[0]).toMatch(/version/);
    const msg = createSurface('a_propos');
    if (msg.createSurface) msg.createSurface.catalogId = 'https://evil.example/catalog.json';
    expect(applyMessage(emptySurfaces(), msg).byId).toEqual({});
  });

  it('defaults a missing catalogId to the basic catalog', () => {
    const msg = createSurface('a_propos');
    if (msg.createSurface) delete msg.createSurface.catalogId;
    expect(applyMessage(emptySurfaces(), msg).byId.a_propos.catalogId).toBe(BASIC_CATALOG_ID);
  });

  it('applies updateDataModel: value, null deletes, provenance and /_errors, with a flash per server value', () => {
    let s = open('a_propos');
    s = applyMessage(s, udm('a_propos', '/quote/email', 'a@b.ca'));
    s = applyMessage(s, udm('a_propos', provPath('/quote/email'), { source: 'chat', note: '« a@b.ca »' }));
    s = applyMessage(s, udm('a_propos', '/_errors', { '/quote/email': 'Adresse refusée' }));
    const sf = s.byId.a_propos;
    expect(get(sf.dataModel, '/quote/email')).toBe('a@b.ca');
    expect(get(sf.dataModel, provPath('/quote/email'))).toEqual({ source: 'chat', note: '« a@b.ca »' });
    expect(sf.flash['/quote/email']).toBe(1);
    expect(sf.flash['/_errors']).toBeUndefined();
    s = applyMessage(s, udm('a_propos', '/quote/email', null));
    expect(get(s.byId.a_propos.dataModel, '/quote/email')).toBeUndefined();
  });

  it('never overwrites a field the customer edited: the server value becomes a suggestion (FR-A2UI-7)', () => {
    let s = open('a_propos');
    s = surfacesReducer(s, { type: 'set', surfaceId: 'a_propos', path: '/quote/first_name', value: 'Mari' });
    s = applyMessage(s, udm('a_propos', '/quote/first_name', 'Marie-Ève'));
    expect(get(s.byId.a_propos.dataModel, '/quote/first_name')).toBe('Mari');
    expect(s.byId.a_propos.suggestions['/quote/first_name']).toBe('Marie-Ève');
    s = surfacesReducer(s, { type: 'acceptSuggestion', surfaceId: 'a_propos', path: '/quote/first_name' });
    expect(get(s.byId.a_propos.dataModel, '/quote/first_name')).toBe('Marie-Ève');
    expect(s.byId.a_propos.suggestions).toEqual({});
  });

  it('clears a shown and a server error when the customer edits the field', () => {
    let s = applyMessage(open('a_propos'), udm('a_propos', '/_errors', { '/quote/email': 'Non' }));
    s = surfacesReducer(s, { type: 'showErrors', surfaceId: 'a_propos', errors: { '/quote/email': 'Entrez une adresse courriel valide' } });
    s = surfacesReducer(s, { type: 'set', surfaceId: 'a_propos', path: '/quote/email', value: 'm@x.ca' });
    expect(s.byId.a_propos.shown).toEqual({});
    expect(get(s.byId.a_propos.dataModel, '/_errors')).toEqual({});
  });

  it('handles updateComponents, deleteSurface, agentFunctionResponse and purge', () => {
    let s = open('a_propos');
    s = applyMessage(s, {
      version: '1.0',
      updateComponents: { surfaceId: 'a_propos', components: [{ id: 'title', component: 'Text', text: '## Autre titre' }] },
    });
    expect(s.byId.a_propos.components.title.text).toBe('## Autre titre');
    s = applyMessage(s, { version: '1.0', agentFunctionResponse: { functionCallId: 'fc_1', value: { ok: true } } });
    expect(s.functionResponses.fc_1).toEqual({ ok: true });
    s = applyMessage(s, { version: '1.0', deleteSurface: { surfaceId: 'a_propos' } });
    expect(s.byId.a_propos.deleted).toBe(true);
    expect(surfacesReducer(s, { type: 'purge', surfaceId: 'a_propos' }).byId).toEqual({});
  });

  it('removes a repeater item and its data', () => {
    let s = open('conducteurs');
    s = applyMessage(s, udm('conducteurs', '/drivers', [{}, { driver_name: 'Alex' }, { driver_name: 'Léo' }]));
    s = surfacesReducer(s, {
      type: 'repeaterCount',
      surfaceId: 'conducteurs',
      componentId: 'repeater_1',
      count: 1,
      removeIndexPath: '/drivers/1',
    });
    expect(get(s.byId.conducteurs.dataModel, '/drivers')).toEqual([{}, { driver_name: 'Léo' }]);
    expect(s.byId.conducteurs.repeaterCounts.repeater_1).toBe(1);
  });
});

describe('@checks', () => {
  const rule = (call: string, args: Record<string, unknown> = {}): CheckRule => ({
    condition: { '@call': call, args: { value: { '@path': '/v' }, ...args } },
    message: call,
  });
  const ok = (call: string, v: unknown, args: Record<string, unknown> = {}) =>
    evalCall(rule(call, args).condition, { data: { v: v as never } });

  it('implements the basic and insurance functions with the prototype semantics', () => {
    expect(ok('required', '')).toBe(false);
    expect(ok('required', [])).toBe(false);
    expect(ok('required', 'x')).toBe(true);
    expect(ok('email', 'marie@exemple.com')).toBe(true);
    expect(ok('email', 'marie@exemple')).toBe(false);
    expect(ok('email', '')).toBe(true); // empty passes every check but required
    expect(ok('regex', 'h2x 1y4', { pattern: '^[ABCEGHJ-NPRSTVXY]\\d[ABCEGHJ-NPRSTV-Z][ -]?\\d[ABCEGHJ-NPRSTV-Z]\\d$' })).toBe(true);
    expect(ok('regex', 'Z2X 1Y4', { pattern: '^[ABCEGHJ-NPRSTVXY]\\d[ABCEGHJ-NPRSTV-Z][ -]?\\d[ABCEGHJ-NPRSTV-Z]\\d$' })).toBe(false);
    expect(ok('numericRange', 2027, { min: 1950, max: 2027 })).toBe(true);
    expect(ok('numericRange', 2028, { min: 1950, max: 2027 })).toBe(false);
    expect(ok('ageBetween', '2010-11-26', { min: 16 })).toBe(false);
    expect(ok('ageBetween', '2010-11-25', { min: 16 })).toBe(true);
    expect(ok('dateWithin', '2026-12-10', { from_days: 0, to_days: 90 })).toBe(true);
    expect(ok('dateWithin', '2027-06-10', { from_days: 0, to_days: 90 })).toBe(false);
    expect(ok('somethingNew', 'x')).toBe(true);
  });

  it('checks the VIN check digit', () => {
    expect(vinValid('2T3P1RFV3MW123456')).toBe(true);
    expect(vinValid('2T3P1RFV0MW123456')).toBe(false);
    expect(vinValid('2T3P1RFV3MW12345')).toBe(false);
    expect(vinValid('IT3P1RFV3MW123456')).toBe(false);
  });

  it('validates year, make and model from the lookup answer', () => {
    const c = rule('ymmValid', { make: { '@path': '/m' }, model: { '@path': '/d' } });
    const component = { id: 'y', component: 'YmmPicker', required: true };
    const data = { v: 2021, m: 'Toyota', d: 'RAV4' };
    expect(evalCall(c.condition, { data, component, lookups: { [ymmKey(2021, 'Toyota', 'RAV4')]: true } })).toBe(true);
    expect(evalCall(c.condition, { data, component, lookups: {} })).toBe(false);
    expect(evalCall(c.condition, { data: {}, component })).toBe(false);
    expect(evalCall(c.condition, { data: {}, component: { ...component, required: false } })).toBe(true);
  });

  it('requires exactly one principal per vehicle', () => {
    const component = { id: 'b', component: 'AssignmentBoard', vehicles: [{ index: 0 }, { index: 1 }] };
    const one = [{ driver_index: 0, vehicle_index: 0, role: 'principal' }];
    expect(evalCall(rule('onePrincipalPerVehicle').condition, { data: { v: one }, component })).toBe(false);
    const both = [...one, { driver_index: 1, vehicle_index: 1, role: 'principal' }];
    expect(evalCall(rule('onePrincipalPerVehicle').condition, { data: { v: both }, component })).toBe(true);
  });

  it('resolves relative paths in a repeater scope and returns the first failing message', () => {
    const checks: CheckRule[] = [
      { condition: { '@call': 'required', args: { value: { '@path': 'driver_name' } } }, message: 'Ce champ est obligatoire' },
    ];
    expect(firstError(checks, { data: { drivers: [{}, {}] }, scope: '/drivers/1' })).toBe('Ce champ est obligatoire');
    expect(firstError(checks, { data: { drivers: [{}, { driver_name: 'Alex' }] }, scope: '/drivers/1' })).toBeNull();
  });
});

describe('tree walk', () => {
  it('lists bound fields, including YMM paths, address sub-fields, repeater items and document fills', () => {
    const paths = (id: string) => boundFields(open(id).byId[id]).flatMap((f) => f.paths);
    expect(paths('vehicule_doc')).toEqual([
      '/vehicles/0/vehicle_year',
      '/vehicles/0/vehicle_make',
      '/vehicles/0/vehicle_model',
      '/vehicles/0/vehicle_type',
      '/vehicles/0/usage_type',
    ]);
    expect(paths('address')).toContain('/quote/postal_code');
    expect(paths('conducteurs')).toEqual([
      '/drivers/1/driver_name',
      '/drivers/1/relationship_code',
      '/drivers/1/driver_date_of_birth',
      '/drivers/2/driver_name',
      '/drivers/2/relationship_code',
      '/drivers/2/driver_date_of_birth',
    ]);
    expect(paths('documents')).toContain('/drivers/0/driver_license_number');
  });

  it('skips fields hidden by visibleIf', () => {
    const s = open('vehicle_1_usage'); // prefilled outside_quebec = true
    const shown = boundFields(s.byId.vehicle_1_usage).map((f) => f.path);
    const hidden = boundFields(applyMessage(s, udm('vehicle_1_usage', '/vehicles/0/outside_quebec', false)).byId.vehicle_1_usage).map(
      (f) => f.path,
    );
    expect(hidden).not.toContain('/vehicles/0/weeks_outside_quebec');
    expect(shown).toContain('/vehicles/0/weeks_outside_quebec');
  });

  it('validates every visible field and collects the non-empty bound values', () => {
    let s = open('a_propos');
    expect(validate(s.byId.a_propos)).toEqual({
      '/quote/date_of_birth': 'Ce champ est obligatoire',
      '/quote/email': 'Ce champ est obligatoire',
    });
    s = applyMessage(s, udm('a_propos', '/quote/email', 'oops'));
    expect(validate(s.byId.a_propos)['/quote/email']).toBe('Entrez une adresse courriel valide');
    expect(collect(s.byId.a_propos)).toEqual({ '/quote/first_name': 'Marie', '/quote/last_name': 'Tremblay', '/quote/email': 'oops' });
  });

  it('applies the renderer fallback checks: an offer must be picked, a board needs its principals', () => {
    expect(validate(open('offers_q_1a2b3c4d_v1').byId.offers_q_1a2b3c4d_v1)).toEqual({
      '/underwriting/offer_type': 'Choisissez une offre',
    });
    expect(Object.values(validate(open('qui_conduit_quoi').byId.qui_conduit_quoi))).toEqual([
      'Chaque véhicule a besoin d’un conducteur principal',
    ]);
  });
});
