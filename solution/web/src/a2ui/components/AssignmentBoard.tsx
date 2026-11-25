/* Insurance catalog: AssignmentBoard (D17, FR-A2UI-14). Drag each person from the pool into a vehicle
   card: one "principal" slot (exactly one person) and an "occasional" slot (zero or more). Pointer
   capture, a ghost that follows the finger, auto-scroll near the edges. Without dragging: tap or press
   Enter on a person, then on a slot (NFR-ACC-3). Writes [{driver_index, vehicle_index, role}]. */
import { type PointerEvent as RPointerEvent, useRef, useState } from 'react';
import { FieldShell, useBinding } from '../binding';
import { Icon } from '../icons';
import { reduceMotion } from '../media';
import type { Props } from './basic';

interface Person {
  index: number;
  label: string;
  sub?: string;
}
interface Vehicle extends Person {
  short?: string;
}
type Kind = 'p' | 's';
type Slots = { p: number | null; s: number[] }[];
type Assignment = { driver_index: number; vehicle_index: number; role: string };

const initials = (d: Person) =>
  String(d.label || '?')
    .split(/\s+/)
    .map((x) => x[0])
    .join('')
    .slice(0, 2)
    .toUpperCase();
const first = (d: Person) => String(d.label || '').split(' ')[0];

function scrollerOf(el: HTMLElement): HTMLElement | null {
  let e = el.parentElement;
  while (e && e !== document.body) {
    const o = getComputedStyle(e).overflowY;
    if ((o === 'auto' || o === 'scroll') && e.scrollHeight > e.clientHeight) return e;
    e = e.parentElement;
  }
  return null;
}

export function AssignmentBoard({ c, scope }: Props) {
  const b = useBinding(c, scope);
  const D = (c.drivers ?? []) as Person[];
  const V = (c.vehicles ?? []) as Vehicle[];
  const [selected, setSelected] = useState<number | null>(null);
  const [msg, setMsg] = useState('');
  const [popKey, setPopKey] = useState('');
  const board = useRef<HTMLDivElement>(null);

  const A: Slots = V.map(() => ({ p: null, s: [] }));
  for (const a of (b.value ?? []) as Assignment[]) {
    const vi = V.findIndex((v) => v.index === a.vehicle_index);
    const di = D.findIndex((d) => d.index === a.driver_index);
    if (vi < 0 || di < 0) continue;
    if (a.role === 'principal') A[vi].p = di;
    else if (a.role === 'occasional' && !A[vi].s.includes(di)) A[vi].s.push(di);
  }
  const vName = (vi: number) => V[vi].short || V[vi].label;
  const save = (next: Slots) => {
    const arr: Assignment[] = [];
    next.forEach((a, vi) => {
      if (a.p != null) arr.push({ driver_index: D[a.p].index, vehicle_index: V[vi].index, role: 'principal' });
      for (const di of a.s) arr.push({ driver_index: D[di].index, vehicle_index: V[vi].index, role: 'occasional' });
    });
    b.write(arr);
  };
  const copy = (): Slots => A.map((a) => ({ p: a.p, s: [...a.s] }));

  const place = (p: number, v: number, kind: Kind) => {
    const next = copy();
    let note = '';
    next[v].s = next[v].s.filter((x) => x !== p);
    if (kind === 'p') {
      next.forEach((a, w) => {
        if (a.p === p && w !== v) {
          a.p = null;
          note = `${first(D[p])} ne peut être conducteur principal que d’un véhicule : retiré(e) de ${vName(w)}.`;
        }
      });
      const prev = next[v].p;
      if (prev != null && prev !== p) {
        if (!next[v].s.includes(prev)) next[v].s.push(prev);
        note ||= `${first(D[prev])} devient conducteur occasionnel de ${vName(v)}.`;
      }
      next[v].p = p;
    } else {
      if (next[v].p === p) next[v].p = null;
      next[v].s.push(p);
    }
    setMsg(note);
    setPopKey(`${v}${kind}`);
    save(next);
    const sl = board.current?.querySelector<HTMLElement>(`.slot[data-v="${v}"][data-kind="${kind}"]`);
    if (sl?.animate && !reduceMotion())
      sl.animate([{ transform: 'scale(1.05)' }, { transform: 'scale(.98)' }, { transform: 'none' }], {
        duration: 380,
        easing: 'cubic-bezier(.34,1.56,.64,1)',
      });
  };
  const removeFrom = (p: number, v: number, kind: Kind) => {
    const next = copy();
    if (kind === 'p') {
      if (next[v].p === p) next[v].p = null;
    } else next[v].s = next[v].s.filter((x) => x !== p);
    setPopKey('');
    save(next);
  };
  const select = (p: number | null) => {
    setSelected(p);
    if (p != null) setMsg(`${first(D[p])} sélectionné(e) : touchez un emplacement.`);
  };
  /* The drag handlers outlive a render; they call the latest place/remove through this ref. */
  const live = useRef({ place, removeFrom, select, selected });
  live.current = { place, removeFrom, select, selected };

  const onPointerDown = (e: RPointerEvent<HTMLDivElement>) => {
    const target = e.target as HTMLElement;
    if (target.closest('[data-rm]') || e.button > 0) return;
    const src = target.closest<HTMLElement>('.person, .pchip');
    const root = board.current;
    if (!src || !root) return;
    const slotEl = src.closest<HTMLElement>('.slot');
    const d = {
      p: Number(src.dataset.p),
      from: slotEl ? { v: Number(slotEl.dataset.v), kind: slotEl.dataset.kind as Kind } : null,
      x0: e.clientX,
      y0: e.clientY,
      x: e.clientX,
      y: e.clientY,
      dx: 0,
      dy: 0,
      ghost: null as HTMLElement | null,
      over: null as HTMLElement | null,
      scroller: null as HTMLElement | null,
    };
    let raf = 0;
    const slotAt = (x: number, y: number) => {
      const sl = document.elementFromPoint(x, y)?.closest<HTMLElement>('.slot');
      return sl && root.contains(sl) ? sl : null;
    };
    const hover = () => {
      const sl = slotAt(d.x, d.y);
      if (sl === d.over) return;
      d.over?.classList.remove('over');
      sl?.classList.add('over');
      d.over = sl;
    };
    const tick = () => {
      if (!d.ghost) {
        raf = 0;
        return;
      }
      const sc = d.scroller;
      if (sc) {
        const r = sc.getBoundingClientRect();
        if (d.y > r.bottom - 64) sc.scrollTop += Math.ceil((d.y - (r.bottom - 64)) / 5);
        else if (d.y < r.top + 64) sc.scrollTop -= Math.ceil((r.top + 64 - d.y) / 5);
      }
      hover();
      raf = requestAnimationFrame(tick);
    };
    const move = (ev: PointerEvent) => {
      d.x = ev.clientX;
      d.y = ev.clientY;
      if (!d.ghost) {
        if (Math.hypot(ev.clientX - d.x0, ev.clientY - d.y0) < 6) return;
        const r = src.getBoundingClientRect();
        d.dx = d.x0 - r.left;
        d.dy = d.y0 - r.top;
        d.ghost = src.cloneNode(true) as HTMLElement;
        d.ghost.classList.add('dragghost');
        d.ghost.style.width = `${r.width}px`;
        document.body.append(d.ghost);
        src.classList.add('dragging');
        for (const x of root.querySelectorAll('.slot')) x.classList.add('can');
        d.scroller = scrollerOf(root);
      }
      d.ghost.style.left = `${ev.clientX - d.dx}px`;
      d.ghost.style.top = `${ev.clientY - d.dy}px`;
      hover();
      if (!raf) raf = requestAnimationFrame(tick);
    };
    const up = async (ev: PointerEvent) => {
      src.removeEventListener('pointermove', move);
      src.removeEventListener('pointerup', up);
      src.removeEventListener('pointercancel', up);
      if (raf) cancelAnimationFrame(raf);
      raf = 0;
      for (const x of root.querySelectorAll('.slot')) x.classList.remove('can', 'over');
      const L = live.current;
      if (!d.ghost) {
        if (!d.from) L.select(L.selected === d.p ? null : d.p);
        return;
      }
      const sl = slotAt(ev.clientX, ev.clientY);
      const to = (sl?.querySelector('.slot-items') ?? src).getBoundingClientRect();
      const g = d.ghost;
      d.ghost = null;
      if (g.animate && !reduceMotion())
        await g.animate(
          [
            { left: g.style.left, top: g.style.top },
            { left: `${to.left}px`, top: `${to.top}px`, transform: sl ? 'scale(.8)' : 'none', opacity: sl ? 0.6 : 1 },
          ],
          { duration: sl ? 200 : 260, easing: sl ? 'cubic-bezier(.2,.8,.2,1)' : 'cubic-bezier(.34,1.56,.64,1)', fill: 'forwards' },
        ).finished;
      g.remove();
      src.classList.remove('dragging');
      if (d.from) L.removeFrom(d.p, d.from.v, d.from.kind);
      if (sl) live.current.place(d.p, Number(sl.dataset.v), sl.dataset.kind as Kind);
    };
    try {
      src.setPointerCapture(e.pointerId);
    } catch {
      /* synthetic events have no active pointer */
    }
    src.addEventListener('pointermove', move);
    src.addEventListener('pointerup', up);
    src.addEventListener('pointercancel', up);
    e.preventDefault();
  };

  const activateSlot = (v: number, kind: Kind) => {
    if (selected == null) return;
    place(selected, v, kind);
    setSelected(null);
  };
  const where = (p: number) =>
    A.flatMap((a, v) => [
      ...(a.p === p ? [`Principal · ${vName(v)}`] : []),
      ...(a.s.includes(p) ? [`Occasionnel · ${vName(v)}`] : []),
    ]).join(' — ');

  const chip = (p: number, v: number, kind: Kind) => (
    <span
      key={p}
      className="pchip"
      data-p={p}
      title="Glissez pour déplacer"
      style={popKey === `${v}${kind}` ? undefined : { animation: 'none' }}
    >
      <span className={`av c${p % 5}`}>{initials(D[p])}</span>
      {first(D[p])}
      <button type="button" data-rm aria-label={`Retirer ${first(D[p])}`} onClick={() => removeFrom(p, v, kind)}>
        ×
      </button>
    </span>
  );
  const slot = (v: number, kind: Kind) => {
    const items = kind === 'p' ? (A[v].p != null ? [A[v].p] : []) : A[v].s;
    const label = kind === 'p' ? 'Conducteur principal' : 'Conducteurs occasionnels';
    return (
      <div
        className={`slot${kind === 'p' ? ' principal' : ''}${items.length ? ' filled' : ''}${kind === 'p' && b.error && !items.length ? ' missing' : ''}`}
        data-v={v}
        data-kind={kind}
        role="button"
        tabIndex={0}
        aria-label={`${label}, ${vName(v)}`}
        onClick={(e) => !(e.target as HTMLElement).closest('.pchip') && activateSlot(v, kind)}
        onKeyDown={(e) => {
          if ((e.key === 'Enter' || e.key === ' ') && e.target === e.currentTarget) {
            e.preventDefault();
            activateSlot(v, kind);
          }
        }}
      >
        <div className="slot-l">
          {label}
          <em>{kind === 'p' ? '1 personne' : '0 ou plus'}</em>
        </div>
        <div className="slot-items">{items.map((p) => chip(p as number, v, kind))}</div>
        <span className="ph">{kind === 'p' ? 'Déposez une personne ici' : 'Déposez ici les autres conducteurs'}</span>
      </div>
    );
  };

  return (
    <FieldShell c={c} b={b}>
      <div className="board" ref={board} onPointerDown={onPointerDown}>
        <div className="pool">
          <div className="pool-h">
            Conducteurs<span>Saisissez une personne et déposez-la dans un emplacement</span>
          </div>
          <div className="pool-chips">
            {D.map((d, i) => (
              <div
                key={d.index}
                className="person"
                data-p={i}
                role="button"
                tabIndex={0}
                aria-pressed={selected === i}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    select(i);
                  }
                }}
              >
                <span className={`av c${i % 5}`}>{initials(d)}</span>
                <span>
                  <b>{d.label}</b>
                  <small>{d.sub ?? ''}</small>
                  <span className="where">{where(i)}</span>
                </span>
              </div>
            ))}
          </div>
        </div>
        <div className="vgrid">
          {V.map((veh, v) => (
            <div key={veh.index} className="vcard" data-v={v}>
              <div className="vh">
                <Icon name="car" />
                <span>
                  {veh.label}
                  <small>{veh.sub ?? ''}</small>
                </span>
              </div>
              {slot(v, 'p')}
              {slot(v, 's')}
            </div>
          ))}
        </div>
        <div className="board-msg" aria-live="polite">
          {msg}
        </div>
        <div className="board-tip">Sans glisser : touchez une personne, puis un emplacement.</div>
      </div>
    </FieldShell>
  );
}
