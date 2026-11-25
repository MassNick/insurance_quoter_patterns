/* The session controller: the only code that talks to the BFF. It turns customer gestures into inbound
   envelopes (one at a time, in order), feeds every outbound envelope to the store, answers the
   renderer's function calls and uploads, and drives the double confirmation. */
import type { LocalAction } from '../a2ui/reducer';
import type { A2uiAction, SurfaceHost } from '../a2ui/types';
import type { BffApi, Envelope, Inbound } from './api';
import { type ChipStatus, pathsOf, type Store, type Suggestion } from './store';

/** Submits of an agent form that leave a "Formulaire envoyé" bubble in the conversation. */
const SUBMITS = new Set(['submit', 'save', 'select', 'confirm', 'send']);
/** Close actions (bff/README.md): no commit, no agent call, so no typing dots. */
const QUIET = new Set(['dismiss', 'cancel', 'back', 'nodocs', 'edit_answers']);

export class Session implements SurfaceHost {
  private queue: Promise<void> = Promise.resolve();
  private fc = 0;

  constructor(
    readonly store: Store,
    private readonly api: BffApi,
  ) {}

  private get s() {
    return this.store.get();
  }
  private trace(type: string, label: string, detail?: unknown) {
    this.store.dispatch({ type: 'trace', row: { type, label, detail } });
  }
  private feed = (env: Envelope) => this.store.dispatch({ type: 'envelope', env });

  async start() {
    try {
      const r = await this.api.createSession();
      this.store.dispatch({ type: 'init', sessionId: r.session_id, quoteNumber: r.quote_number });
      for (const env of r.events ?? []) this.feed(env);
      this.store.dispatch({ type: 'turnEnd' });
    } catch {
      this.store.dispatch({ type: 'turnFailed' });
    }
  }

  /** Runs one inbound event after the ones before it: the stream, then a state refresh. The typing dots
      are drawn by turnStart before anything touches the network (FR-CHAT-1: within 150 ms of send). */
  private run(inbound: Inbound, opts: { userText?: string; typing: boolean }, started = false) {
    if (started) this.store.dispatch({ type: 'turnStart', inbound, ...opts });
    this.queue = this.queue.then(async () => {
      const sid = this.s.sessionId;
      if (!sid) return;
      if (!started) this.store.dispatch({ type: 'turnStart', inbound, ...opts });
      this.trace(inbound.type, `[customer] ${JSON.stringify(inbound.data).slice(0, 90)}`, inbound);
      try {
        await this.api.turn(sid, inbound, this.feed);
        this.store.dispatch({ type: 'turnEnd' });
      } catch {
        this.store.dispatch({ type: 'turnFailed' });
        return;
      }
      await this.api
        .state(sid)
        .then((snapshot) => this.store.dispatch({ type: 'snapshot', snapshot }))
        .catch(() => undefined);
    });
    return this.queue;
  }

  send(text: string, extra: Record<string, unknown> = {}) {
    const t = text.trim();
    if (!t || this.s.busy) return;
    return this.run({ type: 'user.message', data: { text: t, ...extra } }, { userText: t, typing: true }, true);
  }

  suggestion(sg: Suggestion) {
    return this.send(
      sg.label,
      sg.kind === 'action' && sg.action_id ? { via: 'suggestion', action_id: sg.action_id } : { via: 'suggestion' },
    );
  }

  talkToAdvisor() {
    if (this.s.handoff) return;
    return this.send('Parler à un conseiller', { via: 'header', action_id: 'talk_to_advisor' });
  }

  retry(inbound: Inbound) {
    return this.run(inbound, { typing: true });
  }

  /** A2UI action from a surface (SurfaceHost). */
  onAction(action: A2uiAction) {
    const { surfaceId: sid, name } = action;
    if (sid === 'end_of_form' && this.s.handoff) return this.closeHandoff(); // the hand-off is sent: closing is local
    const chip = this.s.chips[sid];
    // Every action that is not a close commits and closes the surface as submitted (bff/README.md).
    const status: ChipStatus = QUIET.has(name) ? 'closed' : 'submitted';
    this.store.dispatch({ type: 'submitted', surfaceId: sid, status });
    if (QUIET.has(name)) {
      // Closing is the customer's call: close now, tell the BFF after.
      this.feed({ type: 'a2ui.message', actor: 'frontend', data: { version: '1.0', deleteSurface: { surfaceId: sid } } });
    }
    const agentForm = sid !== 'full_form' && sid !== 'end_of_form' && !chip?.edit;
    const userText = agentForm && SUBMITS.has(name) ? `Formulaire envoyé : ${chip?.label.replace(/^Formulaire : /, '') ?? sid}` : undefined;
    const done = this.run(
      { type: 'a2ui.action', data: { ...action } },
      { userText, typing: !QUIET.has(name) && sid !== 'full_form' && !chip?.edit },
    );
    // "Continuer sans documents" closes the form, then the conversation goes on (prototype journey).
    return name === 'nodocs' ? done.then(() => this.send('Continuer sans documents')) : done;
  }

  reopen(surfaceId: string) {
    const s = this.s;
    const chip = s.chips[surfaceId];
    if (s.busy || s.stage || s.handoff || !chip || chip.locked || (chip.status !== 'submitted' && chip.status !== 'closed')) return;
    if (s.mode === 'form') this.fullForm(false);
    return this.run({ type: 'ui.surface_reopen', data: { surface_id: surfaceId } }, { typing: false });
  }

  fullForm(open: boolean) {
    const s = this.s;
    if (open === (s.mode === 'form')) return;
    if (!open) {
      this.store.dispatch({ type: 'mode', mode: 'chat' });
      if (s.surfaces.byId.full_form && !s.surfaces.byId.full_form.deleted)
        return this.onAction({
          name: 'back',
          surfaceId: 'full_form',
          sourceComponentId: 'mode_toggle',
          timestamp: new Date().toISOString(),
          context: {},
        });
      return;
    }
    if (s.busy || s.handoff) return;
    // The mode switches when full_form arrives; before consent the BFF answers with a message instead.
    return this.run({ type: 'ui.surface_reopen', data: { surface_id: 'full_form' } }, { typing: false });
  }

  /** Double confirmation: each step answers every grouped confirmation_id (README, requests to the BFF). */
  confirm(step: 1 | 2, accepted: boolean) {
    const m = this.s.modal;
    if (!m) return;
    if (step === 2 && !accepted) return this.store.dispatch({ type: 'modal', step: 1 });
    const consent = this.s.confirmations.some((c) => m.ids.includes(c.confirmation_id) && pathsOf(c).includes('/quote/contact_consent'));
    if (step === 1 && accepted) this.store.dispatch({ type: 'modal', step: 2 });
    else {
      this.store.dispatch({ type: 'modal', step: null });
      if (consent) this.store.dispatch({ type: 'system', text: accepted ? 'Consentement confirmé (2 étapes)' : 'Consentement refusé' });
    }
    m.ids.forEach((id, i) => {
      const last = i === m.ids.length - 1;
      this.run({ type: 'confirm.response', data: { confirmation_id: id, step, accepted } }, { typing: last && (step === 2 || !accepted) });
    });
  }

  closeHandoff() {
    this.store.dispatch({ type: 'closeHandoff' });
  }

  async callFunction(surfaceId: string, call: string, args: Record<string, unknown>) {
    const sid = this.s.sessionId;
    if (!sid) throw new Error('no session');
    const functionCallId = `fc_${++this.fc}`;
    const message = {
      version: '1.0',
      callAgentFunction: {
        surfaceId,
        functionCallId,
        callFunction: { '@call': call, catalogId: this.s.surfaces.byId[surfaceId]?.catalogId, args },
      },
    };
    this.trace('a2ui.callAgentFunction', `${call} ← ${surfaceId}`, message);
    const r = await this.api.callFunction(sid, message);
    this.feed({
      type: 'a2ui.message',
      actor: 'bff',
      data: { version: '1.0', agentFunctionResponse: { functionCallId, value: r.agentFunctionResponse?.value } },
    });
    return r.agentFunctionResponse?.value;
  }

  uploadDocument(surfaceId: string, file: File, purpose: string, fieldPath?: string) {
    const sid = this.s.sessionId;
    if (!sid) return Promise.reject(new Error('no session'));
    this.trace('document.upload', `${purpose} · ${file.type || 'unknown type'} · ${Math.round(file.size / 1024)} KB`);
    return this.api.upload(sid, file, purpose, surfaceId, fieldPath);
  }

  /** Renderer-local actions (typing in a field, lookups) go straight to the store. */
  localDispatch = (action: LocalAction) => this.store.dispatch({ type: 'surface', action });
}
