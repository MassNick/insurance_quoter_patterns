/* The ball-to-form morph (FR-UI-1, FR-UI-2, FR-UI-3), ported from the prototype's Stage class: a ball
   leaves the bot avatar, travels in an arc to the form area and grows into the card; closing plays the
   exact reverse. With prefers-reduced-motion, a short crossfade only. */

import { reduceMotion } from '../a2ui/media';

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const cssVar = (n: string) => getComputedStyle(document.documentElement).getPropertyValue(n).trim() || '#00874E';
const center = (r: { left: number; top: number; width: number; height: number }) => ({ x: r.left + r.width / 2, y: r.top + r.height / 2 });
const FALLBACK = { left: 20, top: 20, width: 30, height: 30 };
const S = 14;

function ball(cls: string, style: Partial<CSSStyleDeclaration>): HTMLDivElement {
  const b = document.createElement('div');
  b.className = cls;
  Object.assign(b.style, style);
  document.body.append(b);
  return b;
}
const px = (n: number) => `${n}px`;
const at = (x: number, y: number) => ({ left: px(x - S / 2), top: px(y - S / 2) });

/** card is in the DOM with visibility hidden (class measuring); layoutMs is how long the chat takes to slide. */
export async function morphOpen(card: HTMLElement, anchor: HTMLElement | null, layoutMs: number): Promise<void> {
  if (anchor) {
    anchor.classList.remove('pulse', 'look-l', 'happy');
    anchor.classList.add('look-r');
    setTimeout(() => anchor.classList.remove('look-r'), 1300);
  }
  if (reduceMotion() || !card.animate) {
    await wait(30);
    card.classList.remove('measuring');
    card.animate?.([{ opacity: 0 }, { opacity: 1 }], { duration: 140 });
    return;
  }
  const start = center(anchor?.getBoundingClientRect() ?? FALLBACK);
  const b = ball('morph', { ...at(start.x, start.y), width: px(S), height: px(S) });
  b.animate([{ transform: 'scale(.2)', opacity: 0 }, { transform: 'scale(1.25)', opacity: 1 }, { transform: 'scale(1)' }], {
    duration: 220,
    easing: 'ease-out',
  });
  await wait(200 + Math.max(0, layoutMs - 260));
  const target = card.getBoundingClientRect();
  const end = center(target);
  const mid = { x: (start.x + end.x) / 2, y: Math.min(start.y, end.y) - 70 };
  await b.animate([at(start.x, start.y), { ...at(mid.x, mid.y), offset: 0.5 }, at(end.x, end.y)], {
    duration: 520,
    easing: 'cubic-bezier(.45,.05,.35,1)',
    fill: 'forwards',
  }).finished;
  b.classList.add('card');
  await b.animate(
    [
      { ...at(end.x, end.y), width: px(S), height: px(S), borderRadius: '50%', backgroundColor: cssVar('--accent') },
      { left: px(end.x - 40), top: px(end.y - 40), width: '80px', height: '80px', borderRadius: '40px', offset: 0.25 },
      {
        left: px(target.left),
        top: px(target.top),
        width: px(target.width),
        height: px(target.height),
        borderRadius: getComputedStyle(card).borderTopLeftRadius || '14px',
        backgroundColor: cssVar('--surface'),
      },
    ],
    { duration: 420, easing: 'cubic-bezier(.2,.9,.25,1)', fill: 'forwards' },
  ).finished;
  card.classList.remove('measuring');
  const col = card.querySelector('.form-body .a2-col');
  if (col) {
    col.classList.add('reveal');
    [...col.children].forEach((el, i) => {
      (el as HTMLElement).style.animationDelay = `${Math.min(i * 45, 400)}ms`;
    });
  }
  b.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 160, fill: 'forwards' }).finished.then(() => b.remove());
}

/** onCardGone: remove the card (React state); anchor() is read after the conversation starts sliding back. */
export async function morphClose(
  card: HTMLElement | null,
  anchor: () => HTMLElement | null,
  opts: { onCardGone: () => void; shiftX?: () => number },
): Promise<void> {
  if (!card?.isConnected || reduceMotion() || !card.animate) {
    opts.onCardGone();
    return;
  }
  const r = card.getBoundingClientRect();
  const rad = getComputedStyle(card).borderTopLeftRadius || '14px';
  const b = ball('morph card', { left: px(r.left), top: px(r.top), width: px(r.width), height: px(r.height), borderRadius: rad });
  card.animate([{ opacity: 1 }, { opacity: 0 }], { duration: 120, fill: 'forwards' });
  await wait(100);
  opts.onCardGone();
  const c = center(r);
  await b.animate(
    [
      {
        left: px(r.left),
        top: px(r.top),
        width: px(r.width),
        height: px(r.height),
        borderRadius: rad,
        backgroundColor: cssVar('--surface'),
      },
      { left: px(c.x - 40), top: px(c.y - 40), width: '80px', height: '80px', borderRadius: '40px', offset: 0.7 },
      { ...at(c.x, c.y), width: px(S), height: px(S), borderRadius: '50%', backgroundColor: cssVar('--accent') },
    ],
    { duration: 380, easing: 'cubic-bezier(.5,0,.75,0)', fill: 'forwards' },
  ).finished;
  b.classList.remove('card');
  const a = anchor();
  const e = center(a?.getBoundingClientRect() ?? FALLBACK);
  e.x -= opts.shiftX?.() ?? 0;
  a?.classList.add('look-l');
  const mid = { x: (c.x + e.x) / 2, y: Math.min(c.y, e.y) - 60 };
  await b.animate(
    [
      { ...at(c.x, c.y), transform: 'scale(1)' },
      { ...at(mid.x, mid.y), offset: 0.5 },
      { ...at(e.x, e.y), transform: 'scale(.6)' },
    ],
    {
      duration: 480,
      easing: 'cubic-bezier(.45,.05,.35,1)',
      fill: 'forwards',
    },
  ).finished;
  b.remove();
  if (a) {
    a.classList.remove('pulse', 'look-l');
    void a.offsetWidth;
    a.classList.add('pulse', 'happy');
    setTimeout(() => a.classList.remove('happy'), 900);
  }
  await wait(200);
}
