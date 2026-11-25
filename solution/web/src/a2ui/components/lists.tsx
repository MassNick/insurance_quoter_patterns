/* Insurance catalog: Repeater (relative @path per item), AssignmentMatrix (legacy, D17) and OfferCards. */
import { FieldShell, useBinding, useSurface } from '../binding';
import { fmtMoney, labelFor } from '../catalog';
import { Icon } from '../icons';
import { Node, RenderComponent } from '../registry';
import { collect, repeaterCount } from '../tree';
import type { Props } from './basic';

export function Repeater({ c, scope }: Props) {
  const { surface, dispatch } = useSurface();
  const b = useBinding(c, scope);
  const n = repeaterCount(c, surface);
  const start = c.start ?? 0;
  const setCount = (count: number, removeIndexPath?: string) =>
    dispatch({ type: 'repeaterCount', surfaceId: surface.id, componentId: c.id, count, removeIndexPath });
  const items = Array.from({ length: n }, (_, k) => {
    const i = start + k;
    const label = String(c.itemLabel ?? 'Élément {n}')
      .replace('{n}', String(k + 1))
      .replace('{i}', String(i));
    const itemScope = `${c.collection}/${i}`;
    if (c.layout === 'list') {
      const tpl = surface.components[c.template[0]];
      return tpl ? <RenderComponent key={i} c={{ ...tpl, label, fldClass: 'rep-line' }} scope={itemScope} /> : null;
    }
    return (
      <div key={i} className="rep-item">
        <div className="rep-head">
          <span>{label}</span>
          <span className="spacer" />
          {n > (c.min ?? 0) && (
            <button className="btn ghost small" type="button" aria-label={`Retirer ${label}`} onClick={() => setCount(n - 1, itemScope)}>
              Retirer
            </button>
          )}
        </div>
        <div className="rep-grid">
          {(c.template as string[]).map((tid) => (
            <Node key={tid} id={tid} scope={itemScope} />
          ))}
        </div>
      </div>
    );
  });
  return (
    <FieldShell c={c} b={b}>
      <div className="repeater">{items}</div>
      {n < (c.max ?? 25) && (
        <button className="btn small" type="button" style={{ width: 'fit-content' }} onClick={() => setCount(n + 1)}>
          <Icon name="plus" /> {c.addLabel ?? 'Ajouter'}
        </button>
      )}
    </FieldShell>
  );
}

type Assignment = { driver_index: number; vehicle_index: number; role: string };

export function AssignmentMatrix({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const arr = (Array.isArray(b.value) ? b.value : []) as Assignment[];
  const choose = (ri: number, ci: number, role: string) => {
    const next = arr.filter((a) => !(a.driver_index === ri && a.vehicle_index === ci)).map((a) => ({ ...a }));
    if (role !== 'none') next.push({ driver_index: ri, vehicle_index: ci, role });
    if (role === 'principal')
      for (const a of next) if (a.vehicle_index === ci && a.driver_index !== ri && a.role === 'principal') a.role = 'occasional';
    b.write(next);
  };
  return (
    <FieldShell c={c} b={b}>
      <table className="matrix">
        <tbody>
          <tr>
            <th>Conducteur</th>
            {c.cols.map((col: { label: string }, ci: number) => (
              // biome-ignore lint/suspicious/noArrayIndexKey: columns are positions (vehicle_index)
              <th key={ci}>{col.label}</th>
            ))}
          </tr>
          {c.rows.map((row: { label: string }, ri: number) => (
            // biome-ignore lint/suspicious/noArrayIndexKey: rows are positions (driver_index)
            <tr key={ri}>
              <td>
                <strong>{row.label}</strong>
              </td>
              {c.cols.map((col: { label: string }, ci: number) => (
                // biome-ignore lint/suspicious/noArrayIndexKey: columns are positions (vehicle_index)
                <td key={ci}>
                  <select
                    aria-label={`${row.label}, ${col.label}`}
                    value={arr.find((a) => a.driver_index === ri && a.vehicle_index === ci)?.role ?? 'none'}
                    onChange={(e) => choose(ri, ci, e.target.value)}
                  >
                    {c.choices.map((ch: { value: string; label: string }) => (
                      <option key={ch.value} value={ch.value}>
                        {ch.label}
                      </option>
                    ))}
                  </select>
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
      <div className="hint">Chaque véhicule a besoin d’un seul conducteur principal.</div>
    </FieldShell>
  );
}

interface Offer {
  offer_type: string;
  monthly: number;
  annual: number;
  deductible: number;
  coverages?: string[];
}

export function OfferCards({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const { surface, host } = useSurface();
  /* showAdjust (render_offers show_adjust): ask to adjust the quote; the BFF closes the cards and the agent turn follows. */
  const adjust = () =>
    host.onAction({
      name: 'adjust',
      surfaceId: surface.id,
      sourceComponentId: c.id,
      timestamp: new Date().toISOString(),
      context: { surfaceId: surface.id, values: collect(surface) },
    });
  return (
    <FieldShell c={c} b={b}>
      <div className="offers" role="radiogroup" aria-label={c.label}>
        {(c.offers as Offer[]).map((o) => (
          <button
            key={o.offer_type}
            type="button"
            className="offer"
            role="radio"
            aria-checked={b.value === o.offer_type}
            data-v={o.offer_type}
            onClick={() => b.write(o.offer_type)}
          >
            {c.recommended === o.offer_type && <span className="o-tag">Recommandé</span>}
            <span className="o-name">{labelFor('offer_type', o.offer_type)}</span>
            <span className="o-price">
              {fmtMoney(o.monthly, true)}
              <small>/mois</small>
            </span>
            <span className="a2-text-caption">
              {fmtMoney(o.annual)} par année · franchise {fmtMoney(o.deductible)}
            </span>
            <ul>
              {(o.coverages ?? []).map((x) => (
                <li key={x}>{x}</li>
              ))}
            </ul>
          </button>
        ))}
      </div>
      {c.showAdjust && (
        <button type="button" className="btn ghost small" style={{ width: 'fit-content' }} onClick={adjust}>
          Ajuster ma soumission
        </button>
      )}
    </FieldShell>
  );
}
