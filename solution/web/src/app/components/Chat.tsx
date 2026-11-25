/* The conversation: welcome, transcript (streaming text, typing dots, citations, retry, form chips),
   suggestion bubbles above the composer, the composer, and the phone peek. */
import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { BotFace, Icon } from '../../a2ui/icons';
import { inline, Paragraphs } from '../../a2ui/md';
import { useApp, useSession } from '../context';
import type { Chip, Msg } from '../store';

export function FormChip({ chip }: { chip: Chip }) {
  const session = useSession();
  const st = chip.status;
  const disabled = st === 'open' || st === 'sent' || chip.locked;
  return (
    <button
      type="button"
      className={`form-chip${st === 'submitted' || st === 'sent' ? ' done' : ''}${chip.locked ? ' locked' : ''}`}
      data-state={st}
      data-surface={chip.surfaceId}
      disabled={disabled}
      onClick={() => session.reopen(chip.surfaceId)}
    >
      {st === 'submitted' || st === 'sent' ? <Icon name="ok" /> : <i />}
      {chip.label}
      {st === 'open' && <span className="chip-st">ouvert</span>}
      {st === 'sent' && <span className="chip-st">envoyé</span>}
      {st === 'closed' && <span className="chip-act">Rouvrir</span>}
      {st === 'submitted' && (
        <span className="chip-act">
          <Icon name="pencil" />
          <span className="d-only">Voir ou modifier</span>
          <span className="p-only">Modifier</span>
        </span>
      )}
    </button>
  );
}

export function Message({ m }: { m: Msg }) {
  const session = useSession();
  const chips = useApp((s) => s.chips);
  if (m.role === 'user')
    return (
      <div className="msg user">
        <div className="bubble">
          <div className="bubble-body">{m.text}</div>
        </div>
      </div>
    );
  if (m.role === 'system')
    return (
      <div className="msg system">
        <div className="bubble-body">{m.text}</div>
      </div>
    );
  return (
    <div className="msg bot" data-msg={m.id}>
      <span className={`avatar${m.typing ? ' think' : ''}`}>
        <BotFace />
      </span>
      <div className="bubble">
        {m.typing && (
          <div className="typing" role="status" aria-label="Le conseiller écrit">
            <span />
            <span />
            <span />
          </div>
        )}
        {m.text && (
          <div className="bubble-body">
            <Paragraphs text={m.text} tail={m.streaming ? <span className="caret" /> : null} />
            {!!m.citations?.length && (
              <div className="sources">
                {m.citations.map((c, i) => (
                  <div key={c.id ?? c.ref_id ?? c.title}>
                    [{i + 1}]{' '}
                    {/^https:\/\//.test(c.url ?? '') ? (
                      <a href={c.url} target="_blank" rel="noreferrer">
                        {c.title}
                      </a>
                    ) : (
                      c.title
                    )}
                  </div>
                ))}
              </div>
            )}
            {m.retry && (
              <button type="button" className="btn small" style={{ marginTop: 8 }} onClick={() => m.retry && session.retry(m.retry)}>
                Réessayer
              </button>
            )}
          </div>
        )}
        {m.chips.map((id) => chips[id] && <FormChip key={id} chip={chips[id]} />)}
      </div>
    </div>
  );
}

export function Chat({ style, pushed }: { style: CSSProperties; pushed: boolean }) {
  const session = useSession();
  const messages = useApp((s) => s.messages);
  const sessionId = useApp((s) => s.sessionId);
  const busy = useApp((s) => s.busy);
  const suggestions = useApp((s) => s.suggestions);
  const sKey = useApp((s) => s.suggestionsKey);
  const scroll = useRef<HTMLDivElement>(null);
  const [text, setText] = useState('');
  const down = () => {
    const el = scroll.current;
    if (el) el.scrollTop = el.scrollHeight;
  };
  useLayoutEffect(down);
  /* The conversation re-wraps when it slides (0.5 s): scroll to the end once it has settled (prototype layout()). */
  // biome-ignore lint/correctness/useExhaustiveDependencies: runs when the geometry changes
  useEffect(() => {
    const t = setTimeout(down, 520);
    return () => clearTimeout(t);
  }, [style.left, style.width, pushed]);
  /* Screen readers hear a reply once, when it is complete (NFR-ACC-1), not token by token. */
  const lastDone = [...messages].reverse().find((m) => m.role === 'bot' && m.text && !m.streaming)?.text ?? '';
  const send = () => {
    if (busy || !text.trim()) return;
    session.send(text);
    setText('');
  };
  return (
    <section className={`chat${pushed ? ' pushed' : ''}`} id="chat" aria-label="Conversation" style={style}>
      <div className="chat-scroll" id="scroll" ref={scroll}>
        <div className="welcome">
          <span className="avatar">
            <BotFace />
          </span>
          <h2>Bonjour!</h2>
          <p>Je suis votre conseiller virtuel Desjardins Assurances.</p>
        </div>
        {!sessionId && busy && <Message m={{ id: 'boot', role: 'bot', text: '', typing: true, chips: [] }} />}
        {messages.map((m) => (
          <Message key={m.id} m={m} />
        ))}
      </div>
      <div className="sr-only" aria-live="polite">
        {lastDone}
      </div>
      <div className="sugg" id="sugg" role="group" aria-label="Réponses suggérées">
        {suggestions.map((sg, i) => (
          <button
            key={`${sKey}-${sg.label}`}
            className="sg"
            type="button"
            style={{ animationDelay: `${i * 60}ms` }}
            onClick={() => !busy && session.suggestion(sg)}
          >
            {sg.label}
          </button>
        ))}
      </div>
      <div className="composer">
        <textarea
          id="input"
          rows={1}
          placeholder="Écrivez votre message…"
          aria-label="Message"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              send();
            }
          }}
        />
        <button className="icon-btn send" id="send" type="button" aria-label="Envoyer" disabled={busy} onClick={send}>
          <Icon name="send" />
        </button>
      </div>
    </section>
  );
}

/** Phone: while a form covers the conversation, the latest reply peeks above the form for 6.5 s. */
export function Peek() {
  const peek = useApp((s) => s.peek);
  const { store } = useSession();
  const [on, setOn] = useState(false);
  useEffect(() => {
    if (!peek) return;
    setOn(true);
    const t = setTimeout(() => setOn(false), 6500);
    return () => clearTimeout(t);
  }, [peek]);
  if (!peek) return null;
  return (
    <button
      type="button"
      id="peek"
      className={`peek${on ? ' on' : ''}`}
      onClick={() => {
        setOn(false);
        store.dispatch({ type: 'peekOff' });
      }}
    >
      <span className="avatar">
        <BotFace />
      </span>
      <span className="pt">{inline(peek.text.replace(/\n+/g, ' '))}</span>
    </button>
  );
}
