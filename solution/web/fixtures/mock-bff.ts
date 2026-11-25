/* A scripted stand-in for the BFF + hosted agent, speaking the real contracts (interfaces.md § 1,
   events.md). Deterministic: the "agent" follows the prototype journey with fixed replies. The same
   class serves unit tests (mockApi, in process), the visual harness and the e2e suite (routeMockBff,
   over real HTTP + SSE through Playwright route interception). */
import { get, provPath, set } from '../src/a2ui/pointer';
import { boundFields } from '../src/a2ui/tree';
import type { A2Component, A2uiMessage, DataModel } from '../src/a2ui/types';
import type { BffApi, Envelope, Inbound } from '../src/app/api';
import { callFunction } from './services';
import { createSurface } from './surfaces';

export const GREETING =
  'Bonjour! Je suis votre conseiller virtuel Desjardins Assurances. Je prépare votre soumission auto avec vous, surtout en conversation, avec de courts formulaires au besoin.\n\nQue souhaitez-vous faire?';
export const OFFERS = [
  { offer_type: 'essential', monthly: 118.4, annual: 1421, deductible: 1000 },
  { offer_type: 'plus', monthly: 142.75, annual: 1713, deductible: 500 },
  { offer_type: 'premium', monthly: 171.9, annual: 2063, deductible: 250 },
];
const DOCS_OFFER =
  'Pour aller plus vite, avez-vous vos documents sous la main? Je peux lire votre **permis de conduire**, votre **certificat d’immatriculation** et votre **police d’assurance actuelle** pour remplir l’information à votre place.\n\nBon à savoir : vous pouvez me répondre par écrit ou me demander un formulaire, comme vous préférez.';
const DETERMINISTIC = new Set([
  '/quote/contact_consent',
  '/drivers/0/driver_license_number',
  '/underwriting/has_claims',
  '/underwriting/has_convictions_in_past_three_years',
  '/underwriting/was_denied_or_cancelled',
]);

type Change = [path: string, value: unknown, source: string, status?: string];
interface Pending {
  path: string;
  value: unknown;
  prev: unknown;
  source: string;
}

export class MockBff {
  readonly sessionId = 's_mock01';
  readonly quoteNumber = 'Q7K2M9TX';
  doc: Record<string, unknown> = { quote: {}, vehicles: [], drivers: [], underwriting: {}, household: {} };
  topic = 'greet';
  /** Every inbound envelope, for assertions. */
  readonly received: Inbound[] = [];
  private turn = 0;
  private seq = 0;
  private out: Envelope[] = [];
  private status: Record<string, string> = {};
  private titles: Record<string, string> = {};
  private edit = new Set<string>();
  private pending = new Map<string, Pending[]>();
  private afterConsent = false;
  private cf = 0;
  private quotes = 0;
  private prefs: string | null = null;
  private filled = 0;

  private e(type: string, data: unknown, actor = 'main_agent') {
    this.out.push({ type, session_id: this.sessionId, turn: this.turn, seq: ++this.seq, ts: '2026-11-25T12:00:00.000Z', actor, data });
  }
  private agent(text: string, suggestions?: (string | { label: string; action_id: string })[]) {
    if (!this.out.some((x) => x.type === 'turn.started')) this.e('turn.started', { phase: 'gather' });
    const words = text.match(/\S+\s*/g) ?? [];
    for (let i = 0; i < words.length; i += 3) this.e('text.delta', { delta: words.slice(i, i + 3).join('') });
    this.e('text.done', { text });
    if (suggestions) this.sugg(suggestions);
  }
  private bff(text: string) {
    this.e('text.done', { text }, 'bff');
  }
  private sugg(items: (string | { label: string; action_id: string })[]) {
    this.e(
      'suggestions',
      { items: items.map((x) => (typeof x === 'string' ? { label: x, kind: 'reply' } : { ...x, kind: 'action' })) },
      'bff',
    );
  }
  private finish() {
    if (this.out.some((x) => x.type === 'turn.started'))
      this.e('turn.completed', {
        model_calls: 2,
        usage: { input: 5200, cached_input: 4300, output: 120 },
        ttft_ms: 640,
        duration_ms: 1400,
      });
  }
  val(path: string) {
    return (get(this.doc, path) as { value?: unknown } | undefined)?.value;
  }
  private commit(changes: Change[]) {
    if (!changes.length) return;
    for (const [path, value, source, status] of changes)
      this.doc = set(this.doc, path, { value, source, status: status ?? (source === 'chat' ? 'inferred' : 'confirmed') });
    this.e(
      'state.delta',
      {
        changes: changes.map(([path, value, source, status]) => ({
          path,
          value,
          source,
          status: status ?? (source === 'chat' ? 'inferred' : 'confirmed'),
        })),
      },
      'bff',
    );
  }
  /** Commits a form or document submit; deterministic paths wait for one double confirmation per batch. */
  private submit(values: Record<string, unknown>, source: string, reason: string) {
    const now: Change[] = [];
    const held: Pending[] = [];
    for (const [path, v] of Object.entries(values)) {
      if (path.startsWith('/_') || /handoff_/.test(path)) continue;
      const prev = this.val(path);
      if (DETERMINISTIC.has(path) && prev !== v) held.push({ path, value: v, prev, source });
      else now.push([path, v, source]);
    }
    this.commit(now);
    this.filled = Object.keys(values).length;
    if (!held.length) return;
    const id = `cf_${++this.cf}`;
    this.pending.set(id, held);
    this.commit(held.map((h) => [h.path, h.value, source, 'pending_confirmation'] as Change));
    const changes = held.map((h) => ({ path: h.path, old_value: h.prev ?? null, new_value: h.value }));
    this.e('confirm.required', { confirmation_id: id, ...changes[0], reason, steps: 2, changes }, 'bff');
  }
  /** Renders a fixture surface the way mcp-a2ui would: prefilled from committed state, edit mode on reopen. */
  private show(id: string, opts: { edit?: boolean; data?: Record<string, unknown>; note?: string } = {}) {
    const msg = createSurface(id);
    const cs = msg.createSurface as NonNullable<A2uiMessage['createSurface']>;
    const comps = Object.fromEntries((cs.components ?? []).map((c) => [c.id, c])) as Record<string, A2Component>;
    let dataModel = (cs.dataModel ?? {}) as DataModel;
    for (const f of boundFields({ components: comps, dataModel, repeaterCounts: {} }))
      for (const p of f.paths) {
        const env = get(this.doc, p) as { value?: unknown; source?: string } | undefined;
        if (env?.value === undefined) continue;
        dataModel = set(dataModel, p, env.value);
        if (env.source && ['chat', 'document', 'lookup'].includes(env.source))
          dataModel = set(dataModel, provPath(p), { source: env.source });
      }
    for (const [p, v] of Object.entries(opts.data ?? {})) dataModel = set(dataModel, p, v);
    cs.dataModel = dataModel;
    if (opts.edit) {
      const primary = Object.values(comps).find((c) => c.component === 'Button' && c.variant === 'primary');
      if (primary && id !== 'end_of_form' && id !== 'full_form') comps[primary.child].text = 'Enregistrer';
      this.edit.add(id);
    } else this.edit.delete(id);
    if (opts.note) for (const c of Object.values(comps)) if (c.component === 'InPlaceNote' && c.tone === 'warn') c.text = opts.note;
    cs.components = Object.values(comps);
    this.status[id] = 'open';
    this.titles[id] = String(comps.title?.text ?? '').replace(/^#+\s*/, '');
    this.e('a2ui.message', msg, opts.edit || id === 'end_of_form' ? 'bff' : 'tool:a2ui.render_form');
  }
  private close(id: string, status = 'closed') {
    if (this.status[id] !== 'open') return;
    this.status[id] = status;
    this.e('a2ui.message', { version: '1.0', deleteSurface: { surfaceId: id } }, 'bff');
  }
  private closeAll() {
    for (const id of Object.keys(this.status)) this.close(id);
  }
  private endOfForm(reason: string, ruleId: string | null) {
    this.closeAll();
    const name = [this.val('/quote/first_name'), this.val('/quote/last_name')].filter(Boolean).join(' ') || 'Le client';
    const vehicle = ['vehicle_year', 'vehicle_make', 'vehicle_model']
      .map((k) => this.val(`/vehicles/0/${k}`))
      .filter(Boolean)
      .join(' ');
    const summary = [
      `${name} souhaite une soumission d’assurance auto.`,
      vehicle ? `Véhicules : ${vehicle}.` : '',
      `Consentement : ${this.val('/quote/contact_consent') ? 'confirmé en deux étapes.' : 'non donné.'}`,
    ]
      .filter(Boolean)
      .join('\n');
    this.show('end_of_form', {
      note: `**Pourquoi?** ${reason}${ruleId ? ` (règle ${ruleId})` : ''}`,
      data: {
        '/quote/first_name': this.val('/quote/first_name') ?? null,
        '/quote/last_name': this.val('/quote/last_name') ?? null,
        '/quote/handoff_summary': summary,
      },
    });
  }

  createSession(): { session_id: string; quote_number: string; events: Envelope[] } {
    this.out = [];
    this.turn++;
    this.bff(GREETING);
    this.sugg([
      'Obtenir une soumission auto',
      'Comparer avec mon assureur actuel',
      'J’ai une question sur les protections',
      'Parler à un conseiller',
    ]);
    return { session_id: this.sessionId, quote_number: this.quoteNumber, events: this.out };
  }

  /** One inbound event in, the envelopes of its stream out. */
  handle(inbound: Inbound): Envelope[] {
    this.received.push(inbound);
    this.out = [];
    const d = inbound.data as Record<string, unknown>;
    this.turn++; // every request gets its own turn number (bff/README.md)
    switch (inbound.type) {
      case 'user.message':
        this.message(String(d.text ?? ''), d.action_id as string | undefined);
        break;
      case 'a2ui.action':
        this.action(String(d.surfaceId), String(d.name), ((d.context as Record<string, unknown>)?.values ?? {}) as Record<string, unknown>);
        break;
      case 'ui.surface_reopen':
        if (d.surface_id === 'full_form' && this.val('/quote/contact_consent') !== true) {
          this.bff(
            'Avant d’afficher le formulaire complet, j’ai besoin de votre consentement à l’utilisation de vos renseignements pour cette soumission.',
          );
          this.sugg([{ label: 'Oui, j’accepte', action_id: 'consent_yes' }, 'Pourquoi en avez-vous besoin?']);
        } else this.show(String(d.surface_id), { edit: true });
        break;
      case 'confirm.response':
        this.confirmation(String(d.confirmation_id), Number(d.step), d.accepted === true);
        break;
    }
    this.finish();
    return this.out;
  }

  private message(text: string, action?: string) {
    const low = text.toLowerCase();
    if (action === 'talk_to_advisor') {
      this.bff('Bien sûr. Je prépare un résumé pour un conseiller; vérifiez-le avant l’envoi.');
      return this.endOfForm('Vous avez demandé à parler à un conseiller.', null);
    }
    if (action === 'consent_yes') {
      // The BFF's own consent question (full form): no agent turn; the full form opens after the confirmation.
      this.afterConsent = true;
      return this.submit(
        { '/quote/contact_consent': true },
        'chat',
        'Vous avez accepté que vos renseignements servent à cette soumission.',
      );
    }
    if (action === 'rule_confirm') {
      this.commit(
        ['vehicle_make', 'vehicle_model', 'vehicle_year'].map(
          (k) => [`/vehicles/0/${k}`, this.val(`/vehicles/0/${k}`), 'chat', 'confirmed'] as Change,
        ),
      );
      this.e(
        'rules.hit',
        {
          rule_id: 'VEHICULE_EXOTIQUE',
          severity: 'stop',
          paths: ['/vehicles/0/vehicle_make'],
          inferred: false,
          reason: 'Les véhicules exotiques demandent une évaluation par un conseiller.',
        },
        'bff',
      );
      this.bff(
        'Merci pour ces précisions. Pour cette demande, je dois vous diriger vers un conseiller. Voici pourquoi, et un résumé à vérifier avant l’envoi.',
      );
      return this.endOfForm('Les véhicules exotiques ou de prestige demandent une évaluation personnalisée.', 'VEHICULE_EXOTIQUE');
    }
    if (/lamborghini/.test(low)) {
      this.commit([
        ['/vehicles/0/vehicle_make', 'Lamborghini', 'chat'],
        ['/vehicles/0/vehicle_model', 'Huracán', 'chat'],
        ['/vehicles/0/vehicle_year', 2023, 'chat'],
      ]);
      this.e(
        'rules.hit',
        {
          rule_id: 'VEHICULE_EXOTIQUE',
          severity: 'stop',
          paths: ['/vehicles/0/vehicle_make'],
          inferred: true,
          reason: 'Les véhicules exotiques demandent une évaluation par un conseiller.',
        },
        'bff',
      );
      this.bff('Juste pour être certain : il s’agit bien d’une **2023 Lamborghini Huracán**?');
      return this.sugg([
        { label: 'Oui, c’est bien ça', action_id: 'rule_confirm' },
        { label: 'Non, ce n’est pas le bon véhicule', action_id: 'rule_correct' },
      ]);
    }
    if (/par écrit/.test(low)) {
      this.prefs = 'text';
      this.e('session.preferences', { answer_mode: 'text' }, 'bff');
      this.topic = 'name';
      return this.agent('C’est noté : on continue par écrit. Commençons par vous. Quel est votre nom complet?', [
        'Pourquoi avez-vous besoin de mon nom?',
      ]);
    }
    if (/formulaire/.test(low)) {
      this.prefs = 'forms';
      this.e('session.preferences', { answer_mode: 'forms' }, 'bff');
      this.agent('C’est noté : je vous propose des formulaires pour la suite. Voici un court formulaire pour faire connaissance.', [
        'J’ai une question',
      ]);
      return this.show('a_propos');
    }
    switch (this.topic) {
      case 'greet':
        this.commit([['/quote/quote_goal', 'new_quote', 'chat']]);
        this.topic = 'consent';
        return this.agent(
          'Avec plaisir.\n\nAvant de recueillir vos renseignements : **puis-je les utiliser pour préparer votre soumission?** Je vous demanderai de confirmer à l’écran.',
          ['Oui, j’accepte', 'Pourquoi en avez-vous besoin?', 'Non, pas maintenant'],
        );
      case 'consent':
        this.submit(
          { '/quote/contact_consent': true },
          'chat',
          'Vous avez accepté dans la conversation que vos renseignements servent à cette soumission.',
        );
        return this.agent('Merci. Une fenêtre de confirmation s’ouvre.');
      case 'docs':
        if (/pas|sans/.test(low)) {
          this.close('documents');
          this.topic = 'name';
          return this.agent('Aucun problème, on continue. Commençons par vous. Quel est votre nom complet?');
        }
        this.agent('Parfait. Téléversez vos documents; je vous montre ce que j’ai lu avant de l’utiliser.', [
          'Je ne les ai finalement pas',
        ]);
        return this.show('documents');
      case 'name': {
        const [first, ...rest] = text.trim().split(/\s+/);
        this.commit([
          ['/quote/first_name', first, 'chat'],
          ['/quote/last_name', rest.join(' '), 'chat'],
        ]);
        this.topic = 'dob';
        return this.agent(`Merci, ${first}. Quelle est votre date de naissance?`, ['Pourquoi ma date de naissance?']);
      }
      case 'dob':
        this.commit([['/quote/date_of_birth', text.trim(), 'chat']]);
        this.topic = 'email';
        return this.agent('Merci. Quelle adresse courriel puis-je utiliser pour vous envoyer votre soumission?');
      case 'email':
        this.commit([['/quote/email', text.trim(), 'chat']]);
        this.topic = 'vehicle';
        return this.agent('Merci! Quel véhicule voulez-vous assurer? Indiquez simplement l’année, la marque et le modèle.');
      case 'quote':
        return this.quote();
    }
    return this.agent('Bien noté.');
  }

  private quote() {
    this.quotes++;
    this.e(
      'quote.created',
      { quote_id: 'Q-1A2B3C4D', version: this.quotes, offers: OFFERS, reason: 'Première soumission', created_at: '2026-11-25T15:04:05Z' },
      'tool:quoting.create_quote',
    );
    this.e('phase', { phase: 'advise' });
    this.agent(
      'Voici vos 3 options pour votre RAV4. Je vous recommande **Plus**, qui inclut l’assistance routière et une voiture de location.',
      ['Qu’est-ce que la franchise?', 'Parler à un conseiller'],
    );
    this.show('offers_q_1a2b3c4d_v1');
    this.topic = 'offers';
  }

  private action(id: string, name: string, values: Record<string, unknown>) {
    if (['dismiss', 'cancel', 'back', 'nodocs', 'edit_answers'].includes(name)) return this.close(id);
    if (id === 'end_of_form' && name === 'send') {
      if (values['/quote/handoff_consent'] !== true)
        return this.e(
          'a2ui.message',
          {
            version: '1.0',
            updateDataModel: {
              surfaceId: id,
              path: '/_errors',
              value: { '/quote/handoff_consent': 'Cochez la case pour transmettre votre dossier à un conseiller.' },
            },
          },
          'bff',
        );
      this.commit(
        Object.entries(values)
          .filter(([p]) => /\/quote\/(first_name|last_name|phone_number|email)$/.test(p))
          .map(([p, v]) => [p, v, 'form'] as Change),
      );
      this.e(
        'handoff.created',
        { reference: 'CNS-482139', rule_id: this.val('/vehicles/0/vehicle_make') === 'Lamborghini' ? 'VEHICULE_EXOTIQUE' : null },
        'bff',
      );
      this.bff('C’est envoyé! Votre référence : CNS-482139. Un conseiller vous joindra d’ici 1 jour ouvrable.');
      return this.close(id, 'sent');
    }
    if (this.edit.has(id)) {
      this.close(id, 'submitted');
      this.submit(
        Object.fromEntries(Object.entries(values).filter(([p, v]) => this.val(p) !== v)),
        'form',
        'Vous avez modifié une réponse protégée.',
      );
      return this.bff(`Modifications enregistrées : ${this.titles[id]}.`);
    }
    this.close(id, 'submitted');
    if (id === 'documents') {
      this.submit(values, 'document', 'Lu sur votre permis de conduire.');
      if (this.pending.size) return;
      return this.afterDocuments();
    }
    if (id === 'a_propos') {
      this.submit(values, 'form', '');
      return this.agent('Merci! Votre dossier est à jour.', ['Continuer ma soumission']);
    }
    if (id === 'vehicule_doc') {
      this.submit(values, 'form', '');
      this.agent(
        'Parfait. Dernière étape pour les véhicules : **glissez chaque personne** vers le véhicule qu’elle conduit. Un conducteur principal par véhicule; les autres sont occasionnels.',
        ['Qu’est-ce qu’un conducteur principal?'],
      );
      return this.show('qui_conduit_quoi');
    }
    if (id === 'qui_conduit_quoi') {
      this.submit(values, 'form', '');
      this.agent('Parfait, c’est noté. Presque fini! Trois questions sur vos antécédents.', [
        'Qu’est-ce qui compte comme une réclamation?',
      ]);
      return this.show('antecedents');
    }
    if (id === 'antecedents') {
      this.submit(values, 'form', 'Vous venez de répondre dans le formulaire.');
      return;
    }
    if (id.startsWith('offers_')) {
      this.submit(values, 'form', '');
      return this.agent('Excellent choix : l’offre **Plus**. Je vous envoie le détail par courriel. Voulez-vous l’ajuster?', [
        'Réduire ma prime',
        'Parler à un conseiller',
      ]);
    }
    this.agent('Merci!');
  }

  private afterDocuments() {
    this.topic = 'vehicle_form';
    this.agent(
      `Merci! J’ai rempli **${this.filled} informations** à partir de vos documents. J’ai lu votre véhicule sur l’immatriculation. Il me manque seulement son utilisation.`,
      ['J’ai une question', 'Parler à un conseiller'],
    );
    this.show('vehicule_doc');
  }

  private confirmation(id: string, step: number, accepted: boolean) {
    const held = this.pending.get(id);
    if (!held) return this.e('error', { code: 'unknown_confirmation', message: 'unknown confirmation', retryable: false }, 'bff');
    if (!accepted) {
      this.pending.delete(id);
      this.commit(held.map((h) => [h.path, h.prev ?? null, h.source] as Change));
      this.e('confirm.rejected', { confirmation_id: id, reason: 'rejected', paths: held.map((h) => h.path) }, 'bff');
      return this.agent('D’accord, je n’ai rien changé. Je peux tout de même répondre à vos questions sur les protections.');
    }
    if (step < 2) return;
    this.pending.delete(id);
    this.commit(held.map((h) => [h.path, h.value, h.source, 'confirmed'] as Change));
    const path = held[0].path;
    if (path === '/quote/contact_consent' && this.afterConsent) {
      this.afterConsent = false;
      return this.show('full_form', { edit: true });
    }
    if (path === '/quote/contact_consent') {
      this.topic = 'docs';
      return this.agent(`Merci! ${DOCS_OFFER}`, ['Oui, j’ai mes documents', 'J’en ai seulement quelques-uns', 'Je ne les ai pas']);
    }
    if (path === '/drivers/0/driver_license_number') return this.afterDocuments();
    if (path.startsWith('/underwriting/')) {
      this.topic = 'quote';
      return this.agent('Merci, c’est noté. J’ai tout ce qu’il faut. Je calcule votre soumission?', [
        { label: 'Obtenir ma soumission', action_id: 'get_quote' },
        'J’ai une question',
      ]);
    }
    this.agent('Merci, c’est noté.');
  }

  fn(message: { callAgentFunction: { functionCallId: string; callFunction: { '@call': string; args: Record<string, unknown> } } }) {
    const c = message.callAgentFunction;
    return {
      version: '1.0',
      agentFunctionResponse: { functionCallId: c.functionCallId, value: callFunction(c.callFunction['@call'], c.callFunction.args) },
    };
  }

  private uploads = 0;
  upload() {
    return { document_id: `doc_${++this.uploads}` };
  }

  snapshot() {
    return {
      state: this.doc,
      preferences: { answer_mode: this.prefs },
      signals: [],
      advisor_signals: [],
      surfaces: Object.entries(this.status).map(([surface_id, status]) => ({
        surface_id,
        title: this.titles[surface_id],
        chip: this.titles[surface_id],
        status,
      })),
      quote_number: this.quoteNumber,
    };
  }
}

/** The mock as a BffApi, in process (unit tests, visual harness). */
export function mockApi(m: MockBff): BffApi {
  return {
    createSession: async () => m.createSession(),
    turn: async (_sid, inbound, onEnvelope) => {
      for (const e of m.handle(inbound)) onEnvelope(e);
    },
    callFunction: async (_sid, message) => m.fn(message as Parameters<MockBff['fn']>[0]),
    upload: async () => m.upload(),
    state: async () => m.snapshot(),
  };
}

export const sse = (events: Envelope[]) => `${events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(': keepalive\n\n')}`;
