/* The Variation C shell. Layout follows the prototype's layout(): on desktop the conversation is centred
   and slides left to make room for a form; at 600 px or less the form covers the conversation while the
   suggestions and the composer stay usable above the bottom tab bar. */
import { type CSSProperties, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { matches, PHONE } from '../../a2ui/media';
import { useApp } from '../context';
import { Tooltip } from '../ui';
import { Chat, Peek } from './Chat';
import { ConfirmModal, FullForm, Trace } from './Overlays';
import { Dossier, Header, Rail, Steps } from './Shell';
import { StageHost } from './StageHost';

function useMedia(q: string) {
  const [m, setM] = useState(() => matches(q));
  useEffect(() => {
    if (typeof matchMedia !== 'function') return;
    const mq = matchMedia(q);
    const on = () => setM(mq.matches);
    mq.addEventListener('change', on);
    return () => mq.removeEventListener('change', on);
  }, [q]);
  return m;
}

/** Desktop geometry from the prototype: a 72 px rail, 20 px padding, the chat 36% wide while a form is open. */
export function desktopLayout(W: number, open: boolean): { chat: CSSProperties; stage: CSSProperties } {
  const left0 = 72;
  const pad = 20;
  const avail = W - left0;
  const narrow = avail < 820;
  const cw = open ? (narrow ? avail - 2 * pad : Math.max(340, Math.min(440, Math.round(avail * 0.36)))) : Math.min(720, avail - 2 * pad);
  const cl = open ? left0 + pad : left0 + Math.round((avail - cw) / 2);
  const sl = narrow ? left0 + pad : cl + cw + 24;
  return { chat: { left: cl, width: cw }, stage: { left: sl, width: W - sl - pad } };
}

export function App({ dev }: { dev: boolean }) {
  const ws = useRef<HTMLElement>(null);
  const [W, setW] = useState(1200);
  const [open, setOpen] = useState(false);
  const [pushed, setPushed] = useState(false);
  const [bottom, setBottom] = useState(0);
  const phone = useMedia(PHONE);
  const formMode = useApp((s) => s.mode === 'form');

  useLayoutEffect(() => {
    const el = ws.current;
    if (!el) return;
    setW(el.clientWidth);
    if (typeof ResizeObserver !== 'function') return;
    const ro = new ResizeObserver(() => setW(el.clientWidth));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  /* Phone: the stage ends above the suggestions and the composer (they stay usable while a form is open). */
  useLayoutEffect(() => {
    if (!phone || !open) return;
    const measure = () => {
      const sugg = document.getElementById('sugg');
      const comp = document.querySelector<HTMLElement>('.composer');
      const chat = document.getElementById('chat');
      const wsEl = ws.current;
      if (!sugg || !comp || !chat || !wsEl) return;
      setBottom(
        Math.round(wsEl.getBoundingClientRect().bottom - chat.getBoundingClientRect().bottom) + sugg.offsetHeight + comp.offsetHeight + 2,
      );
    };
    measure();
    if (typeof ResizeObserver !== 'function') return;
    const ro = new ResizeObserver(measure);
    ro.observe(document.getElementById('sugg') as HTMLElement);
    return () => ro.disconnect();
  }, [phone, open]);

  const onLayout = (phase: 'open' | 'unpush' | 'closed') => {
    if (phase === 'open') {
      setOpen(true);
      setPushed(true);
    } else if (phase === 'unpush') setPushed(false);
    else setOpen(false);
  };
  const geo = phone ? { chat: { left: 0, width: W }, stage: { left: 8, width: W - 16, bottom } } : desktopLayout(W, open);

  return (
    <div className="app" id="app">
      <Header />
      <Steps />
      <main className="ws" id="ws" ref={ws}>
        <Chat style={geo.chat} pushed={phone && pushed} />
        <StageHost style={geo.stage} visible={open} onLayout={onLayout} />
        <FullForm />
        <Rail />
        <Dossier />
        {phone && open && !formMode && <Peek />}
      </main>
      <ConfirmModal />
      <Tooltip />
      {dev && <Trace />}
    </div>
  );
}
