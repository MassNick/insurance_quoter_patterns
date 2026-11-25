/* Insurance catalog: YmmPicker, VinField and AddressLookup. Their lookups leave the renderer as
   callAgentFunction (lookupMakeModelYear, identifyVehicleType, decodeVin, identifyAddress); the BFF
   answers with no model call. */
import { useEffect, useState } from 'react';
import { Badge, FieldShell, useBinding, useSurface } from '../binding';
import { clock, labelFor } from '../catalog';
import { vinValid, ymmKey } from '../checks';
import { Icon } from '../icons';
import { addressFields } from '../tree';
import { ChoicePicker, type Props, TextField } from './basic';
import { FieldActions } from './documents';

interface YmmAnswer {
  valid?: boolean;
  makes?: string[];
  models?: string[];
}

export function YmmPicker({ c, scope }: Props) {
  const { surface, dispatch, host } = useSurface();
  const y = useBinding(c, scope, 'year');
  const m = useBinding(c, scope, 'make');
  const d = useBinding(c, scope, 'model');
  const t = useBinding(c, scope, 'vehicleType');
  const [ans, setAns] = useState<YmmAnswer | null>(null);
  const [type, setType] = useState<string | null>(null);
  const year = y.value as number | undefined;
  const make = m.value as string | undefined;
  const model = d.value as string | undefined;

  // biome-ignore lint/correctness/useExhaustiveDependencies: a lookup runs when the year, make or model changes, nothing else
  useEffect(() => {
    let live = true;
    const args = { year: year || undefined, make: make || undefined, model: model || undefined };
    host
      .callFunction(surface.id, 'lookupMakeModelYear', args)
      .then(async (raw) => {
        const r = (raw ?? {}) as YmmAnswer;
        if (!live) return;
        setAns(r);
        dispatch({ type: 'lookup', surfaceId: surface.id, key: ymmKey(year, make, model), valid: !!r.valid });
        if (!r.valid) return setType(null);
        const vt = ((await host.callFunction(surface.id, 'identifyVehicleType', { make, model, year })) ?? {}) as { vehicle_type?: string };
        if (!live || !vt.vehicle_type) return;
        setType(vt.vehicle_type);
        if (t.value !== vt.vehicle_type) t.write(vt.vehicle_type, true);
      })
      .catch(() => live && setAns({ valid: false }));
    return () => {
      live = false;
    };
  }, [year, make, model]);

  const nowYear = clock.now().getFullYear();
  const years = Array.from({ length: nowYear + 2 - 1950 }, (_, i) => nowYear + 1 - i);
  const makes = ans?.makes ?? (make ? [make] : []);
  const models = ans?.models ?? (model ? [model] : []);
  const select = (
    label: string,
    value: unknown,
    items: (string | number)[],
    placeholder: string,
    onChange: (v: string) => void,
    disabled = false,
  ) => (
    <select
      aria-label={label}
      value={value != null && items.map(String).includes(String(value)) ? String(value) : ''}
      disabled={disabled}
      onChange={(e) => onChange(e.target.value)}
    >
      <option value="">{placeholder}</option>
      {items.map((x) => (
        <option key={x} value={x}>
          {x}
        </option>
      ))}
    </select>
  );
  let status = <>Validé en direct dans le catalogue de tarification.</>;
  if (ans?.valid)
    status = (
      <>
        <span className="ok-msg">
          <Icon name="ok" /> Valide : {year} {make} {model}
        </span>
        {type && (
          <>
            {' '}
            · type <Badge kind="lookup">{labelFor('vehicle_type', type)}</Badge>
          </>
        )}
        <span className="dev-only"> identified by quoting.identify_vehicle_type</span>
      </>
    );
  else if (ans && year && make && model)
    status = (
      <span className="err">
        {make} n’offre pas de {model} en {year}
      </span>
    );

  return (
    <FieldShell c={c} b={{ ...y, prov: y.prov ?? m.prov ?? d.prov }}>
      <div className="a2-row">
        {select('Année', year, years, 'Année', (v) => y.write(v ? Number(v) : null))}
        {select(
          'Marque',
          make,
          makes,
          year ? 'Marque' : 'Marque (choisissez l’année)',
          (v) => {
            m.write(v || null);
            d.write(null);
          },
          !year,
        )}
        {select('Modèle', model, models, 'Modèle', (v) => d.write(v || null), !make)}
      </div>
      <div className="hint">{status}</div>
    </FieldShell>
  );
}

export function VinField({ c, scope }: Props) {
  const { surface, host } = useSurface();
  const b = useBinding(c, scope);
  const vin = String(b.value ?? '').toUpperCase();
  const [decoded, setDecoded] = useState<{ year?: number; make?: string; model?: string } | null>(null);
  const ok = vin.length === 17 && vinValid(vin);
  useEffect(() => {
    let live = true;
    setDecoded(null);
    if (ok)
      host
        .callFunction(surface.id, 'decodeVin', { vin })
        .then((r) => live && setDecoded((r ?? {}) as { year?: number }))
        .catch(() => undefined);
    return () => {
      live = false;
    };
  }, [ok, vin, host, surface.id]);
  let status = null;
  if (vin && vin.length < 17) status = <>Encore {17 - vin.length} caractères</>;
  else if (vin && !ok) status = <span className="err">Le chiffre de contrôle ne correspond pas : vérifiez le NIV</span>;
  else if (ok)
    status = (
      <span className="ok-msg">
        <Icon name="ok" /> Chiffre de contrôle valide{decoded?.make ? ` · ${decoded.year} ${decoded.make} ${decoded.model}` : ''}
      </span>
    );
  return (
    <FieldShell c={c} b={b}>
      <div className="input-wrap">
        <input
          type="text"
          maxLength={17}
          placeholder="17 caractères"
          aria-label={c.label || 'NIV'}
          style={{ fontFamily: 'var(--mono)', textTransform: 'uppercase', letterSpacing: '.04em' }}
          value={vin}
          onChange={(e) => b.write(e.target.value.toUpperCase())}
        />
        {c.fieldActions && <FieldActions c={c} b={b} />}
      </div>
      <div className="hint">{status}</div>
    </FieldShell>
  );
}

interface Candidate {
  address_line1: string;
  city: string;
  province_code: string;
  postal_code: string;
  confidence: number;
}

export function AddressLookup({ c, scope }: Props) {
  const { surface, dispatch, host } = useSurface();
  const b = useBinding(c, scope);
  const [query, setQuery] = useState('');
  const [cands, setCands] = useState<Candidate[]>([]);
  const [picked, setPicked] = useState(-1);
  const subs = addressFields(c);
  const search = async () => {
    const r = (await host.callFunction(surface.id, 'identifyAddress', { query: query || '125 des Érables' })) as {
      candidates?: Candidate[];
    };
    setCands(r?.candidates ?? []);
    setPicked(-1);
  };
  const pick = (cd: Candidate, i: number) => {
    setPicked(i);
    for (const k of ['address_line1', 'city', 'province_code', 'postal_code'] as const)
      dispatch({ type: 'set', surfaceId: surface.id, path: `/quote/${k}`, value: cd[k] });
    dispatch({ type: 'provenance', surfaceId: surface.id, path: '/quote/address_line1', value: { source: 'lookup' } });
  };
  return (
    <FieldShell c={c} b={b}>
      <div className="input-wrap">
        <input
          type="text"
          placeholder="Commencez à écrire : 125 des Érables, ou un code postal"
          aria-label="Recherche d’adresse"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={(e) => {
            if (e.key !== 'Enter') return;
            e.preventDefault();
            search();
          }}
        />
        <button className="btn small" type="button" onClick={search}>
          <Icon name="search" /> Chercher
        </button>
      </div>
      <div className="radios" role="radiogroup" aria-label="Adresses trouvées" style={{ marginTop: 4 }}>
        {cands.map((cd, i) => (
          <button
            key={`${cd.city}-${cd.postal_code}`}
            type="button"
            className="radio-card"
            role="radio"
            aria-checked={picked === i}
            onClick={() => pick(cd, i)}
          >
            <span className="rdot" />
            <span>
              <strong>{cd.address_line1}</strong>, {cd.city}, {cd.province_code} {cd.postal_code}
            </span>
            <span className={`conf ${cd.confidence > 0.85 ? 'hi' : cd.confidence > 0.65 ? 'mid' : 'lo'}`} style={{ marginLeft: 'auto' }}>
              {Math.round(cd.confidence * 100)}%
            </span>
          </button>
        ))}
      </div>
      <div className="a2-row" style={{ marginTop: 4 }}>
        {subs.map((s) =>
          s.component === 'ChoicePicker' ? <ChoicePicker key={s.id} c={s} scope={scope} /> : <TextField key={s.id} c={s} scope={scope} />,
        )}
      </div>
    </FieldShell>
  );
}
