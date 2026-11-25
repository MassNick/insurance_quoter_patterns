/* The small Markdown subset the prototype renders: **bold**, [n] citation marks, paragraphs and line
   breaks. Built as React nodes, never as HTML strings, so server text cannot inject markup. */
import { Fragment, type ReactNode } from 'react';

export function inline(text: string, cites = false): ReactNode[] {
  const out: ReactNode[] = [];
  const re = cites ? /\*\*(.+?)\*\*|\[(\d)\]|\n/g : /\*\*(.+?)\*\*|\n/g;
  let last = 0;
  for (const m of text.matchAll(re)) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const k = out.length;
    if (m[1] != null) out.push(<strong key={k}>{m[1]}</strong>);
    else if (m[2] != null)
      out.push(
        <span key={k} className="cite">
          [{m[2]}]
        </span>,
      );
    else out.push(<br key={k} />);
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

/** A bot reply: paragraphs split on blank lines; `tail` (the streaming caret) goes in the last one. */
export function Paragraphs({ text, tail }: { text: string; tail?: ReactNode }) {
  const paras = text.split(/\n\n+/);
  return (
    <>
      {paras.map((p, i) => (
        // biome-ignore lint/suspicious/noArrayIndexKey: paragraphs have no identity beyond their order
        <p key={i}>
          {inline(p, true)}
          {i === paras.length - 1 ? tail : null}
        </p>
      ))}
    </>
  );
}

export const Inline = ({ text }: { text: string }) => <Fragment>{inline(text)}</Fragment>;
