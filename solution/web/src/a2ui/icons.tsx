/* The prototype's inline SVG icons (demo/src/a2ui.js ICON, journey.html header and rail), as JSX. */
import type { ReactElement } from 'react';

const stroke = { fill: 'none', stroke: 'currentColor' } as const;
const PATHS: Record<string, { size: number; width: number; body: ReactElement }> = {
  info: {
    size: 16,
    width: 2,
    body: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5M12 8h.01" />
      </>
    ),
  },
  chat: { size: 16, width: 2, body: <path d="M21 12a8 8 0 0 1-11.6 7.1L4 20l1-4.6A8 8 0 1 1 21 12z" /> },
  warn: {
    size: 16,
    width: 2,
    body: (
      <>
        <path d="M12 3l10 18H2L12 3z" />
        <path d="M12 10v4M12 17h.01" />
      </>
    ),
  },
  ok: { size: 16, width: 2.2, body: <path d="M5 12l5 5 9-10" /> },
  lock: {
    size: 11,
    width: 2.6,
    body: (
      <>
        <rect x="5" y="11" width="14" height="10" rx="2" />
        <path d="M8 11V8a4 4 0 0 1 8 0v3" />
      </>
    ),
  },
  camera: {
    size: 14,
    width: 2,
    body: (
      <>
        <path d="M4 8h3l2-3h6l2 3h3v11H4z" />
        <circle cx="12" cy="13" r="3.5" />
      </>
    ),
  },
  search: {
    size: 14,
    width: 2.2,
    body: (
      <>
        <circle cx="11" cy="11" r="7" />
        <path d="M20 20l-4-4" />
      </>
    ),
  },
  close: { size: 16, width: 2.2, body: <path d="M6 6l12 12M18 6L6 18" /> },
  plus: { size: 14, width: 2.4, body: <path d="M12 5v14M5 12h14" /> },
  help: {
    size: 13,
    width: 2.4,
    body: (
      <>
        <circle cx="12" cy="12" r="9" />
        <path d="M12 11v5M12 8h.01" />
      </>
    ),
  },
  car: {
    size: 30,
    width: 1.8,
    body: (
      <>
        <path d="M4 16v-4l2-5h12l2 5v4" />
        <path d="M4 12h16" />
        <circle cx="7.5" cy="16.5" r="1.8" />
        <circle cx="16.5" cy="16.5" r="1.8" />
      </>
    ),
  },
  idcard: {
    size: 24,
    width: 1.8,
    body: (
      <>
        <rect x="3" y="5" width="18" height="14" rx="2" />
        <circle cx="9" cy="11" r="2.2" />
        <path d="M5.8 16c.6-1.6 1.8-2.4 3.2-2.4s2.6.8 3.2 2.4M14 10h4M14 13h4" />
      </>
    ),
  },
  regdoc: {
    size: 24,
    width: 1.8,
    body: (
      <>
        <path d="M6 3h9l4 4v14H6z" />
        <path d="M14 3v5h5M9 13h7M9 16h7M9 10h3" />
      </>
    ),
  },
  policy: {
    size: 24,
    width: 1.8,
    body: (
      <>
        <path d="M12 3l8 3v5c0 4.5-3.4 8.3-8 10-4.6-1.7-8-5.5-8-10V6l8-3z" />
        <path d="M9 12l2 2 4-4" />
      </>
    ),
  },
  upload: { size: 18, width: 2, body: <path d="M12 16V4M7 9l5-5 5 5M4 20h16" /> },
  pencil: { size: 13, width: 2.4, body: <path d="M4 20h4L19 9l-4-4L4 16v4z" /> },
  send: { size: 20, width: 2.4, body: <path d="M5 12h14M13 6l6 6-6 6" /> },
  form: {
    size: 15,
    width: 2,
    body: (
      <>
        <rect x="4" y="3" width="16" height="18" rx="2" />
        <path d="M8 8h8M8 12h8M8 16h5" />
      </>
    ),
  },
  headset: {
    size: 16,
    width: 2,
    body: (
      <>
        <path d="M4 14v-2a8 8 0 0 1 16 0v2" />
        <rect x="3" y="14" width="4" height="6" rx="1" />
        <rect x="17" y="14" width="4" height="6" rx="1" />
      </>
    ),
  },
  person: {
    size: 22,
    width: 2,
    body: (
      <>
        <circle cx="12" cy="8" r="4" />
        <path d="M4 21c1-4 4.5-6 8-6s7 2 8 6" />
      </>
    ),
  },
  vehicle: {
    size: 22,
    width: 2,
    body: (
      <>
        <path d="M5 16V11l2-5h10l2 5v5" />
        <circle cx="8" cy="16" r="2" />
        <circle cx="16" cy="16" r="2" />
      </>
    ),
  },
  shield: { size: 22, width: 2, body: <path d="M12 3l8 4v5c0 4.5-3.4 8.3-8 9-4.6-.7-8-4.5-8-9V7l8-4z" /> },
};

export type IconName = keyof typeof PATHS;

export function Icon({ name, size }: { name: IconName; size?: number }) {
  const p = PATHS[name];
  const s = size ?? p.size;
  return (
    <svg
      width={s}
      height={s}
      viewBox="0 0 24 24"
      strokeWidth={p.width}
      strokeLinecap={name === 'send' ? 'round' : undefined}
      strokeLinejoin={name === 'send' ? 'round' : undefined}
      aria-hidden="true"
      {...stroke}
    >
      {p.body}
    </svg>
  );
}

/** The bot: a mini hexagon with eyes (FR-UI-11). */
export function BotFace() {
  return (
    <svg viewBox="0 0 40 44" aria-hidden="true">
      <path className="hex" d="M20 2 37 11.5v21L20 42 3 32.5v-21z" />
      <g className="eye">
        <ellipse className="lid" cx="13.5" cy="21" rx="4.2" ry="5.2" />
        <circle className="pupil" cx="14" cy="21.8" r="2.1" />
      </g>
      <g className="eye">
        <ellipse className="lid" cx="26.5" cy="21" rx="4.2" ry="5.2" />
        <circle className="pupil" cx="27" cy="21.8" r="2.1" />
      </g>
      <path d="M15.5 30.5q4.5 3 9 0" fill="none" stroke="#fff" strokeWidth="1.8" strokeLinecap="round" opacity=".9" />
    </svg>
  );
}
