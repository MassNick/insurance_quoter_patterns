/* Insurance catalog: DocumentDrop (D18), FileDrop and the per-field "upload a photo" action.
   One path for every document: the file is checked (PDF, JPEG, PNG, HEIC, 10 MB), uploaded with
   POST /documents, then read with callAgentFunction extractDocument. Extracted values land in the
   surface with provenance "document" and are committed only when the customer submits. */
import { Fragment, useRef, useState } from 'react';
import { type Binding, FieldShell, useBinding, useSurface } from '../binding';
import { field, labelFor, labelOf } from '../catalog';
import { Icon, type IconName } from '../icons';
import { get, keyOf } from '../pointer';
import type { A2Component, DocumentExtraction } from '../types';
import type { Props } from './basic';

const TYPES = ['application/pdf', 'image/jpeg', 'image/png', 'image/heic'];
export const MAX_BYTES = 10 * 1024 * 1024;

export function fileProblem(f: File): string | null {
  if (!TYPES.includes(f.type) && !/\.(pdf|jpe?g|png|heic)$/i.test(f.name)) return 'Formats acceptés : PDF, JPEG, PNG ou HEIC.';
  if (f.size > MAX_BYTES) return 'Le fichier dépasse 10 Mo.';
  return null;
}

/** Upload + extract, shared by every document entry point. Resolves to the extraction or an error text. */
function useDocumentReader() {
  const { surface, host } = useSurface();
  return async (file: File, purpose: string, fieldPath?: string): Promise<DocumentExtraction | string> => {
    const problem = fileProblem(file);
    if (problem) return problem;
    try {
      const { document_id } = await host.uploadDocument(surface.id, file, purpose, fieldPath);
      const r = (await host.callFunction(surface.id, 'extractDocument', { document_id, purpose })) as DocumentExtraction;
      return r?.document_ok && r.fields ? r : 'Illisible, réessayez';
    } catch {
      return 'Le téléversement a échoué, réessayez';
    }
  };
}

/** A button that opens the file chooser; the hidden input is what tests drive. */
function FilePicker({
  label,
  icon,
  className,
  onFile,
  disabled,
}: {
  label: string;
  icon?: IconName;
  className: string;
  onFile: (f: File) => void;
  disabled?: boolean;
}) {
  const input = useRef<HTMLInputElement>(null);
  return (
    <>
      <button type="button" className={className} disabled={disabled} onClick={() => input.current?.click()}>
        {icon && <Icon name={icon} />} {label}
      </button>
      <input
        ref={input}
        type="file"
        hidden
        accept="application/pdf,image/jpeg,image/png,image/heic,.heic"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = '';
          if (f) onFile(f);
        }}
      />
    </>
  );
}

const pct = (n: number) => `${Math.round(n * 100)} %`;

/** Writes extracted values to their target paths with provenance "document". Returns how many landed. */
function useFill() {
  const { surface, dispatch } = useSurface();
  return (fields: Record<string, { value: unknown; confidence: number }>, fills: Record<string, string>) => {
    let n = 0;
    for (const [k, v] of Object.entries(fields)) {
      const target = fills[k];
      if (!target) continue;
      dispatch({ type: 'set', surfaceId: surface.id, path: target, value: v.value });
      dispatch({ type: 'provenance', surfaceId: surface.id, path: target, value: { source: 'document', note: pct(v.confidence) } });
      n++;
    }
    return n;
  };
}

export function FieldActions({ c, b }: { c: A2Component; b: Binding }) {
  const read = useDocumentReader();
  const [busy, setBusy] = useState(false);
  const actions = (c.fieldActions ?? []).filter((a: { kind?: string }) => a.kind === 'upload_photo');
  return actions.map((a: { id: string; label: string }) => (
    <FilePicker
      key={a.id}
      className="btn small"
      icon={busy ? undefined : 'camera'}
      label={busy ? 'Lecture…' : a.label}
      disabled={busy}
      onFile={async (f) => {
        setBusy(true);
        const r = await read(f, keyOf(b.path) === 'vin' ? 'vin' : 'licence', b.path);
        setBusy(false);
        if (typeof r === 'string') return b.check(null);
        const got = r.fields?.[keyOf(b.path)] ?? Object.values(r.fields ?? {})[0];
        if (!got) return;
        b.write(got.value);
        b.mark({ source: 'document', note: pct(got.confidence) });
      }}
    />
  ));
}

interface Doc {
  purpose: string;
  icon?: IconName;
  title: string;
  description?: string;
  fills?: Record<string, string>;
  labels?: Record<string, string>;
}
type Tile = { state: 'idle' | 'reading' | 'done' | 'missing'; text?: string };

/** One review row: an extracted value the customer can correct before submit. */
function ReviewRow({ doc, k, target, confidence, delay }: { doc: Doc; k: string; target: string; confidence: number; delay: number }) {
  const { surface } = useSurface();
  const b = useBinding({ id: `x_${target}`, component: 'Review', value: { '@path': target } });
  const def = field(keyOf(target));
  const id = `x-${surface.id}-${target.replace(/\W/g, '_')}`;
  const change = (raw: string) => {
    b.write(def?.type === 'integer' ? (raw === '' ? null : Number(raw)) : raw);
    b.mark({ source: 'document', note: 'modifié par vous' });
  };
  const value = b.value == null ? '' : String(b.value);
  return (
    <div className="xrow" style={{ animationDelay: `${delay}ms` }}>
      <label htmlFor={id}>{doc.labels?.[k] || labelOf(k) || labelOf(keyOf(target)) || k}</label>
      {def?.enum_fr ? (
        <select id={id} value={value} onChange={(e) => change(e.target.value)}>
          {def.enum_fr.map(([v, l]) => (
            <option key={v} value={v}>
              {l}
            </option>
          ))}
        </select>
      ) : (
        <input
          id={id}
          type={def?.type === 'date' ? 'date' : def?.type === 'integer' ? 'number' : 'text'}
          value={value}
          onChange={(e) => change(e.target.value)}
        />
      )}
      <span className={`conf${confidence < 0.9 ? ' mid' : ''}`}>{pct(confidence)}</span>
    </div>
  );
}

export function DocumentDrop({ c, scope }: Props) {
  const { surface, dispatch } = useSurface();
  const b = useBinding(c, scope);
  const read = useDocumentReader();
  const fill = useFill();
  const docs = (c.documents ?? []) as Doc[];
  const [tiles, setTiles] = useState<Record<string, Tile>>({});
  const results = (get(surface.dataModel, '/_documents/fields') ?? {}) as Record<string, Record<string, number>>;
  const readList = [...new Set([...((c.read ?? []) as string[]), ...((get(surface.dataModel, '/_documents/read') ?? []) as string[])])];
  const tileOf = (d: Doc): Tile => tiles[d.purpose] ?? (readList.includes(d.purpose) ? { state: 'done' } : { state: 'idle' });
  const setTile = (p: string, t: Tile) => setTiles((x) => ({ ...x, [p]: t }));
  /** Rows per document: what the agent read (with its confidence) or, on a reopened form, what is filled. */
  const rowsOf = (d: Doc): [string, string, number][] =>
    Object.entries(d.fills ?? {})
      .filter(([k, target]) => (results[d.purpose] ? k in results[d.purpose] : get(surface.dataModel, target) != null))
      .map(([k, target]) => [k, target, results[d.purpose]?.[k] ?? 1]);

  const upload = async (d: Doc, f: File) => {
    setTile(d.purpose, { state: 'reading' });
    const r = await read(f, d.purpose);
    if (typeof r === 'string') return setTile(d.purpose, { state: 'idle', text: r });
    const n = fill(r.fields ?? {}, d.fills ?? {});
    const confidences = Object.fromEntries(Object.entries(r.fields ?? {}).map(([k, v]) => [k, v.confidence]));
    dispatch({ type: 'set', surfaceId: surface.id, path: `/_documents/fields/${d.purpose}`, value: confidences, silent: true });
    dispatch({ type: 'set', surfaceId: surface.id, path: '/_documents/read', value: [...new Set([...readList, d.purpose])], silent: true });
    setTile(d.purpose, { state: 'done', text: n ? `${n} informations lues` : 'Lu' });
  };

  const done = docs.filter((d) => readList.includes(d.purpose));
  return (
    <FieldShell c={c} b={b}>
      <div className="docs">
        {docs.map((d) => {
          const t = tileOf(d);
          const filled = rowsOf(d).length;
          const okText =
            t.state === 'missing' ? 'Non fourni' : t.state === 'done' ? (t.text ?? (filled ? `${filled} informations lues` : 'Lu')) : 'Lu';
          return (
            <div key={d.purpose} className={`doc${t.state === 'idle' ? '' : ` ${t.state}`}`} data-doc={d.purpose}>
              <span className="dic">
                <Icon name={d.icon ?? 'upload'} />
              </span>
              <b>{d.title}</b>
              <small>{d.description ?? ''}</small>
              <div className="dbtns">
                <FilePicker className="up" icon="camera" label="Téléverser une photo" onFile={(f) => upload(d, f)} />
                <button type="button" className="skip" onClick={() => setTile(d.purpose, { state: 'missing' })}>
                  Je ne l’ai pas
                </button>
                <span className="ok">
                  <Icon name="ok" />
                  <span>{okText}</span>
                </span>
                {t.state === 'idle' && t.text && (
                  <span className="err" role="alert">
                    {t.text}
                  </span>
                )}
              </div>
              <div className="thumb">
                <div className="paper">
                  <div className="scan" />
                </div>
                <span>Lecture du document…</span>
              </div>
            </div>
          );
        })}
      </div>
      <div className="found" hidden={!done.length}>
        <div className="a2-text-h3" role="heading" aria-level={3}>
          Ce que j’ai trouvé
        </div>
        <div className="xlist">
          {done.map((d) => (
            <div key={d.purpose} className="xgroup">
              <h5>{d.title}</h5>
              {rowsOf(d).map(([k, target, conf], i) => (
                <ReviewRow key={target} doc={d} k={k} target={target} confidence={conf} delay={i * 70} />
              ))}
            </div>
          ))}
        </div>
      </div>
    </FieldShell>
  );
}

export function FileDrop({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const read = useDocumentReader();
  const fill = useFill();
  const [busy, setBusy] = useState(false);
  const [found, setFound] = useState<DocumentExtraction | string | null>(null);
  const take = async (f: File) => {
    setBusy(true);
    const r = await read(f, c.purpose ?? 'licence');
    setBusy(false);
    setFound(r);
    if (typeof r !== 'string') fill(r.fields ?? {}, c.fills ?? {});
  };
  return (
    <FieldShell c={c} b={b}>
      <div
        className={`drop${busy ? ' busy' : ''}`}
        onDragOver={(e) => e.preventDefault()}
        onDrop={(e) => {
          e.preventDefault();
          const f = e.dataTransfer.files?.[0];
          if (f) take(f);
        }}
      >
        <Icon name="upload" />
        <div>
          <strong>Déposez une photo ou un PDF</strong>, ou
        </div>
        <FilePicker
          className="btn small"
          label={busy ? 'L’agent de documents lit…' : found ? 'Remplacer le fichier' : 'Choisir un fichier'}
          disabled={busy}
          onFile={take}
        />
        {typeof found === 'string' && (
          <span className="err" role="alert">
            {found}
          </span>
        )}
        {found && typeof found !== 'string' && (
          <div className="extracted">
            {Object.entries(found.fields ?? {}).map(([k, v]) => (
              <Fragment key={k}>
                <div>
                  <span style={{ color: 'var(--ink-3)' }}>{labelOf(k) || k}</span> <strong>{labelFor(k, v.value)}</strong>
                </div>
                <div className={`conf ${v.confidence > 0.9 ? 'hi' : v.confidence > 0.75 ? 'mid' : 'lo'}`}>
                  {Math.round(v.confidence * 100)}%
                </div>
              </Fragment>
            ))}
          </div>
        )}
      </div>
    </FieldShell>
  );
}
