/* The shared UI primitives: one Modal (a bottom sheet on phones, through the prototype CSS) and one
   Tooltip layer for every [data-help] element (field help in forms, rows in the dossier). */
import { type ReactNode, useEffect, useLayoutEffect, useRef, useState } from 'react';

const FOCUSABLE = 'button:not([disabled]), input:not([disabled]), select, textarea, [tabindex]:not([tabindex="-1"])';

/** Traps focus, Esc runs onEscape, focus returns to where it was when the modal closes (FR-UI-6). */
export function Modal(props: { labelledBy: string; describedBy?: string; focusKey: unknown; onEscape: () => void; children: ReactNode }) {
  const { labelledBy, describedBy, focusKey, onEscape, children } = props;
  const box = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const before = document.activeElement as HTMLElement | null;
    return () => before?.focus?.();
  }, []);
  // biome-ignore lint/correctness/useExhaustiveDependencies: focus moves when the step changes, not on every render
  useEffect(() => {
    const first =
      box.current?.querySelector<HTMLElement>('.modal-foot .btn.primary:not([disabled])') ??
      box.current?.querySelector<HTMLElement>(FOCUSABLE);
    first?.focus();
  }, [focusKey]);
  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === 'Escape') {
      e.preventDefault();
      onEscape();
      return;
    }
    if (e.key !== 'Tab' || !box.current) return;
    const items = [...box.current.querySelectorAll<HTMLElement>(FOCUSABLE)];
    const i = items.indexOf(document.activeElement as HTMLElement);
    const next = e.shiftKey ? (i <= 0 ? items.length - 1 : i - 1) : i === items.length - 1 ? 0 : i + 1;
    e.preventDefault();
    items[next]?.focus();
  };
  return (
    <div className="modal-back">
      <div
        ref={box}
        className="modal"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={labelledBy}
        aria-describedby={describedBy}
        onKeyDown={onKeyDown}
      >
        {children}
      </div>
    </div>
  );
}

/** One tooltip for the whole page: hover or focus any [data-help] element (FR-UI-10). */
export function Tooltip() {
  const [tip, setTip] = useState<{ title: string; help: string; rect: DOMRect } | null>(null);
  const el = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const show = (e: Event) => {
      const t = (e.target as HTMLElement).closest?.<HTMLElement>('[data-help]');
      if (t?.dataset.help) setTip({ title: t.dataset.title ?? '', help: t.dataset.help, rect: t.getBoundingClientRect() });
    };
    const hide = (e: Event) => (e.type === 'focusout' || (e.target as HTMLElement).closest?.('[data-help]')) && setTip(null);
    for (const [ev, fn] of [
      ['mouseover', show],
      ['focusin', show],
      ['mouseout', hide],
      ['focusout', hide],
    ] as const)
      document.addEventListener(ev, fn);
    return () => {
      for (const [ev, fn] of [
        ['mouseover', show],
        ['focusin', show],
        ['mouseout', hide],
        ['focusout', hide],
      ] as const)
        document.removeEventListener(ev, fn);
    };
  }, []);
  useLayoutEffect(() => {
    const t = el.current;
    if (!t || !tip) return;
    const x = Math.min(tip.rect.left, innerWidth - t.offsetWidth - 12);
    t.style.left = `${Math.max(12, x)}px`;
    t.style.top = `${Math.min(tip.rect.bottom + 8, innerHeight - t.offsetHeight - 8)}px`;
  }, [tip]);
  if (!tip) return null;
  return (
    <div ref={el} className="tip" role="tooltip">
      <b>{tip.title}</b>
      {tip.help}
    </div>
  );
}
