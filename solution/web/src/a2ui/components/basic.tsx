/* Basic catalog components (Column, Row, Text, TextField, DateTimeInput, ChoicePicker, CheckBox,
   Slider) and the insurance catalog's display components (InPlaceNote, Summary). Ported from the
   prototype renderer (demo/src/a2ui.js); every field goes through useBinding + FieldShell. */
import { Fragment } from 'react';
import { Badge, FieldShell, ProtectedBadge, SOURCE_SHORT, useBinding, useSurface } from '../binding';
import { field } from '../catalog';
import { Icon, type IconName } from '../icons';
import { Inline } from '../md';
import { keyOf } from '../pointer';
import { Node } from '../registry';
import type { A2Component } from '../types';
import { FieldActions } from './documents';

export interface Props {
  c: A2Component;
  scope?: string;
}

const fid = (surfaceId: string, path: string) => `f-${surfaceId}-${path.replace(/\W/g, '_')}`;

export function Column({ c, scope }: Props) {
  return (
    <div className="a2-col">
      {(c.children as string[]).map((id) => (
        <Node key={id} id={id} scope={scope} />
      ))}
    </div>
  );
}

export function Row({ c, scope }: Props) {
  return (
    <div className="a2-row">
      {(c.children as string[]).map((id) => (
        <Node key={id} id={id} scope={scope} />
      ))}
    </div>
  );
}

export function Text({ c }: Props) {
  const t = String(c.text ?? '');
  const h = t.match(/^#{1,6}\s+(.*)$/);
  if (h)
    return (
      <div className="a2-text-h3" role="heading" aria-level={3}>
        <Inline text={h[1]} />
      </div>
    );
  return (
    <div className={c.variant === 'caption' ? 'a2-text-caption' : 'a2-text-body'}>
      <Inline text={t} />
    </div>
  );
}

const TONE_ICON: Record<string, IconName> = { info: 'info', chat: 'chat', warn: 'warn', ok: 'ok' };
export function InPlaceNote({ c }: Props) {
  const tone = c.tone ?? 'info';
  return (
    <div className={`note ${tone}`}>
      <span className="ic">
        <Icon name={TONE_ICON[tone] ?? 'info'} />
      </span>
      <div>
        <Inline text={String(c.text ?? '')} />
      </div>
    </div>
  );
}

/** A component this renderer does not know, or an insurance component on a basic-catalog surface. */
export function Unknown({ c }: Props) {
  return (
    <div className="note warn" data-unknown={c.component}>
      <span className="ic">
        <Icon name="warn" />
      </span>
      <div>Cet élément ne peut pas être affiché.</div>
    </div>
  );
}

const PLACEHOLDER: Record<string, string> = {
  phone: '(514) 555-0142',
  postal_code: 'H2X 1Y4',
  licence: 'A1234-123456-12',
  email: 'nom@exemple.com',
};
const INPUT_TYPE: Record<string, string> = { email: 'email', phone: 'tel', number: 'number', currency: 'number' };

/** Blur formatting from the prototype: postal code A1A 1A1, phone (514) 555-0142, licence upper case. */
export function normalize(type: string, raw: string): string {
  let v = raw.trim();
  if (type === 'postal_code' && /^[a-z]\d[a-z]\s?\d[a-z]\d$/i.test(v))
    v = `${v.slice(0, 3)} ${v.slice(-3)}`.toUpperCase().replace('  ', ' ');
  if (type === 'phone') {
    const d = v.replace(/\D/g, '');
    if (d.length === 10) v = `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}`;
  }
  if (type === 'licence') v = v.toUpperCase();
  return v;
}

export function TextField({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const id = fid(useSurface().surface.id, b.path);
  const type = String(c.textFieldType ?? 'shortText');
  const text = b.value == null ? '' : String(b.value);
  if (type === 'longText')
    return (
      <FieldShell c={c} b={b} htmlFor={id}>
        <textarea id={id} rows={6} placeholder={c.placeholder ?? ''} value={text} onChange={(e) => b.write(e.target.value)} />
      </FieldShell>
    );
  const numeric = type === 'number' || type === 'currency';
  return (
    <FieldShell c={c} b={b} htmlFor={id}>
      <div className={`input-wrap${type === 'currency' ? ' has-prefix' : ''}`}>
        {type === 'currency' && <span className="prefix">$</span>}
        <input
          id={id}
          type={INPUT_TYPE[type] ?? 'text'}
          autoComplete="off"
          inputMode={numeric ? 'numeric' : undefined}
          placeholder={c.placeholder ?? PLACEHOLDER[type] ?? ''}
          readOnly={c.readonly}
          value={text}
          onChange={(e) => b.write(numeric && e.target.value !== '' ? Number(e.target.value) : e.target.value)}
          onBlur={(e) => {
            const v = numeric ? e.target.value : normalize(type, e.target.value);
            if (!numeric && v !== e.target.value) b.write(v);
            b.check(numeric && v !== '' ? Number(v) : v);
          }}
        />
        {c.fieldActions && <FieldActions c={c} b={b} />}
      </div>
    </FieldShell>
  );
}

export function DateTimeInput({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const id = fid(useSurface().surface.id, b.path);
  const win = (c['@checks'] ?? []).find((x: { condition: { '@call': string } }) => x.condition['@call'] === 'dateWithin')?.condition.args;
  const day = (n: number) => new Date(Date.now() + n * 864e5).toISOString().slice(0, 10);
  return (
    <FieldShell c={c} b={b} htmlFor={id}>
      <input
        id={id}
        type="date"
        min={win ? day(0) : undefined}
        max={win ? day(win.to_days) : undefined}
        value={String(b.value ?? '')}
        onChange={(e) => b.write(e.target.value)}
      />
    </FieldShell>
  );
}

const CHOICE_CLASS: Record<string, string> = { chips: 'chips', segmented: 'seg', radio: 'radios', slots: 'slots' };

export function ChoicePicker({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const id = fid(useSurface().surface.id, b.path);
  const options = (c.options ?? []) as { value: string; label: string }[];
  const toVal = (v: string) =>
    c.valueType === 'boolean' ? v === 'true' : /^\d+$/.test(v) && field(keyOf(b.path))?.type === 'integer' ? Number(v) : v;
  const current = b.value;
  if (c.variant === 'select')
    return (
      <FieldShell c={c} b={b} htmlFor={id}>
        <select
          id={id}
          value={current == null ? '' : String(current)}
          onChange={(e) => b.write(e.target.value === '' ? null : toVal(e.target.value))}
        >
          <option value="">Sélectionner</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </FieldShell>
    );
  const list = (Array.isArray(current) ? current : []) as string[];
  const checked = (v: string) => (c.multiple ? list.includes(v) : current != null && String(current) === v);
  const pick = (v: string) => b.write(c.multiple ? (list.includes(v) ? list.filter((x) => x !== v) : [...list, v]) : toVal(v));
  return (
    <FieldShell c={c} b={b}>
      {/* biome-ignore lint/a11y/useAriaPropsSupportedByRole: both roles (group, radiogroup) take aria-label; the linter cannot read a conditional role */}
      <div className={CHOICE_CLASS[c.variant] ?? 'chips'} role={c.multiple ? 'group' : 'radiogroup'} aria-label={c.label}>
        {options.map((o) => {
          const cls = c.variant === 'radio' ? 'radio-card' : c.variant === 'segmented' ? undefined : 'chip';
          const body = (
            <>
              {c.variant === 'radio' && <span className="rdot" />}
              <span>{o.label}</span>
            </>
          );
          return c.multiple ? (
            <button
              key={o.value}
              type="button"
              className={cls}
              role="checkbox"
              aria-checked={checked(o.value)}
              data-v={o.value}
              onClick={() => pick(o.value)}
            >
              {body}
            </button>
          ) : (
            <button
              key={o.value}
              type="button"
              className={cls}
              role="radio"
              aria-checked={checked(o.value)}
              data-v={o.value}
              onClick={() => pick(o.value)}
            >
              {body}
            </button>
          );
        })}
      </div>
    </FieldShell>
  );
}

export function CheckBox({ c, scope }: Props) {
  const b = useBinding(c, scope);
  return (
    <FieldShell c={c} b={b} noLabel>
      <label className="check">
        <input type="checkbox" checked={b.value === true} onChange={(e) => b.write(e.target.checked ? true : null)} />
        <span>
          <Inline text={String(c.label ?? '')} />
          {c.required && <span className="req">*</span>}
          {c.deterministic && (
            <>
              {' '}
              <ProtectedBadge />
            </>
          )}
        </span>
      </label>
    </FieldShell>
  );
}

export function Slider({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const v = b.value as number | null | undefined;
  const fmt = (n: number) => `${n.toLocaleString('fr-CA')}${c.unit ? ` ${c.unit}` : ''}`;
  const rest = Math.round((c.minValue + c.maxValue) / 4 / c.step) * c.step;
  return (
    <FieldShell c={c} b={b}>
      <div className="slider-wrap">
        <input
          type="range"
          min={c.minValue}
          max={c.maxValue}
          step={c.step}
          aria-label={c.label}
          value={v ?? rest}
          onChange={(e) => b.write(Number(e.target.value))}
        />
        <div className="slider-val">{v == null ? '—' : fmt(Number(v))}</div>
      </div>
    </FieldShell>
  );
}

export function Summary({ c, scope }: Props) {
  const b = useBinding(c, scope);
  return (
    <FieldShell c={c} b={b}>
      <dl className="summary">
        {(c.items ?? []).map((it: { label: string; value: string; source?: string }) => (
          <Fragment key={it.label}>
            <dt>{it.label}</dt>
            <dd>
              {it.value}
              {it.source && (
                <>
                  {' '}
                  <Badge kind={it.source}>{SOURCE_SHORT[it.source] ?? it.source}</Badge>
                </>
              )}
            </dd>
          </Fragment>
        ))}
      </dl>
    </FieldShell>
  );
}
