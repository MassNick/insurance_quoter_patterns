/* Fake quoting functions and document extraction, ported from the prototype's makeServices (deterministic:
   fixed VINs, no randomness). Used by unit tests, the visual harness and the mock BFF. */

export const VINS = { rav4: '2T3P1RFV3MW123456', civic: 'JHMFC1F35FX012345' };

const VEHICLES: Record<string, [string, string, number][]> = {
  Toyota: [
    ['Corolla', 'car', 2000],
    ['Camry', 'car', 2000],
    ['RAV4', 'suv', 2000],
    ['Highlander', 'suv', 2001],
    ['Sienna', 'minivan', 2000],
    ['Tacoma', 'pickup', 2000],
  ],
  Honda: [
    ['Civic', 'car', 2000],
    ['Accord', 'car', 2000],
    ['CR-V', 'suv', 2000],
    ['HR-V', 'suv', 2016],
    ['Odyssey', 'minivan', 2000],
  ],
  Ford: [
    ['F-150', 'pickup', 2000],
    ['Escape', 'suv', 2001],
    ['Mustang', 'car', 2000],
    ['Explorer', 'suv', 2000],
  ],
  Hyundai: [
    ['Elantra', 'car', 2000],
    ['Tucson', 'suv', 2005],
    ['Kona', 'suv', 2018],
    ['Ioniq 5', 'suv', 2022],
  ],
  Mazda: [
    ['Mazda3', 'car', 2004],
    ['CX-5', 'suv', 2013],
    ['CX-30', 'suv', 2020],
  ],
  Subaru: [
    ['Outback', 'suv', 2000],
    ['Forester', 'suv', 2000],
    ['Crosstrek', 'suv', 2013],
  ],
  Tesla: [
    ['Model 3', 'car', 2017],
    ['Model Y', 'suv', 2020],
  ],
  Lamborghini: [
    ['Huracán', 'car', 2014],
    ['Urus', 'suv', 2018],
  ],
  'Harley-Davidson': [
    ['Street Glide', 'motorcycle', 2006],
    ['Sportster', 'motorcycle', 2000],
  ],
};

const EXTRACTIONS: Record<string, Record<string, { value: string | number | boolean; confidence: number }>> = {
  licence: {
    first_name: { value: 'Marie', confidence: 0.99 },
    last_name: { value: 'Tremblay', confidence: 0.99 },
    date_of_birth: { value: '1987-01-18', confidence: 0.96 },
    driver_license_number: { value: 'T6512-180187-08', confidence: 0.95 },
  },
  registration: {
    vin: { value: VINS.rav4, confidence: 0.95 },
    vehicle_make: { value: 'Toyota', confidence: 0.97 },
    vehicle_model: { value: 'RAV4', confidence: 0.94 },
    vehicle_year: { value: 2021, confidence: 0.96 },
  },
  policy: {
    years_with_current_insurer: { value: 6, confidence: 0.86 },
    listed_driver_1_name: { value: 'Alex Tremblay', confidence: 0.9 },
    listed_driver_1_relationship: { value: 'spouse', confidence: 0.84 },
  },
  vin: { vin: { value: VINS.civic, confidence: 0.97 } },
};

type Args = Record<string, unknown>;

/** The five renderer functions the BFF answers (interfaces.md § 1), keyed by their A2UI names. */
export const FUNCTIONS: Record<string, (a: Args) => unknown> = {
  lookupMakeModelYear({ year, make, model }) {
    const y = Number(year) || null;
    const makes = Object.keys(VEHICLES).filter((m) => !y || VEHICLES[m].some((x) => y >= x[2]));
    const list = typeof make === 'string' ? VEHICLES[make] : undefined;
    const models = list ? list.filter((x) => !y || y >= x[2]).map((x) => x[0]) : undefined;
    const valid = !!(y && make && model && models?.includes(String(model)));
    return { valid, normalized: valid ? { year: y, make, model } : null, makes, models };
  },
  identifyVehicleType({ make, model }) {
    const m = (VEHICLES[String(make)] ?? []).find((x) => x[0] === model);
    return { vehicle_type: m ? m[1] : 'car', confidence: m ? 0.98 : 0.6 };
  },
  decodeVin({ vin }) {
    if (vin === VINS.rav4) return { valid_checksum: true, year: 2021, make: 'Toyota', model: 'RAV4' };
    if (vin === VINS.civic) return { valid_checksum: true, year: 2015, make: 'Honda', model: 'Civic' };
    return { valid_checksum: true, year: 2019, make: 'Toyota', model: 'RAV4' };
  },
  identifyAddress({ query }) {
    const num = String(query).match(/\d+/)?.[0] ?? '125';
    return {
      candidates: [
        { address_line1: `${num} Rue des Érables`, city: 'Laval', province_code: 'QC', postal_code: 'H7N 1A1', confidence: 0.93 },
        { address_line1: `${num} Rue des Érables`, city: 'Montréal', province_code: 'QC', postal_code: 'H2X 1Y4', confidence: 0.71 },
        { address_line1: `${num} Rue des Érables`, city: 'Québec', province_code: 'QC', postal_code: 'G1R 2T3', confidence: 0.58 },
      ],
    };
  },
  extractDocument({ purpose }) {
    const fields = EXTRACTIONS[String(purpose)];
    return fields ? { purpose, document_ok: true, fields } : { purpose, document_ok: false, reason: 'unknown purpose', fields: {} };
  },
};

export const callFunction = (call: string, args: Args): unknown => FUNCTIONS[call]?.(args) ?? null;
