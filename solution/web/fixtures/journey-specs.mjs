/* FormSpecs for the journey surfaces that the gallery (demo/src/forms.js) does not already hold.
 * Compiled by fixtures/gen.mjs with the prototype compiler; the output lands in surfaces.json.
 * Values mirror demo/src/journey.js (specVehicle, specAssign, specOffers, specFin, specFull). */

const DRIVERS_REPEATER = {
  key: '/drivers',
  widget: 'repeater',
  label: 'Autres conducteurs',
  repeat: {
    start: 1,
    count: 1,
    min: 0,
    max: 6,
    item_label: 'Conducteur {n}',
    add_label: 'Ajouter un conducteur',
    fields: [
      { key: 'driver_name', widget: 'text', label: 'Prénom et nom' },
      {
        key: 'relationship_code',
        widget: 'select',
        label: 'Lien avec vous',
        options: [
          ['spouse', 'Conjoint ou conjointe'],
          ['child', 'Enfant'],
          ['parent', 'Parent'],
          ['other', 'Autre'],
        ],
      },
      { key: 'driver_date_of_birth', widget: 'date', label: 'Date de naissance' },
    ],
  },
};

export const OFFERS = [
  {
    offer_type: 'essential',
    monthly: 118.4,
    annual: 1421,
    deductible: 1000,
    coverages: ['Responsabilité civile 1 M$', 'Collision ou versement', 'Risques autres que la collision'],
  },
  {
    offer_type: 'plus',
    monthly: 142.75,
    annual: 1713,
    deductible: 500,
    coverages: ['Responsabilité civile 2 M$', 'Collision et autres risques', 'Assistance routière', 'Voiture de location jusqu’à 1 200 $'],
  },
  {
    offer_type: 'premium',
    monthly: 171.9,
    annual: 2063,
    deductible: 250,
    coverages: ['Responsabilité civile 2 M$', 'Remplacement à neuf (5 ans)', 'Pardon d’accident', 'Voiture de location jusqu’à 2 000 $'],
  },
];

export const JOURNEY_SPECS = [
  {
    id: 'vehicle-from-document',
    spec: {
      form_id: 'vehicule_doc',
      title: 'Votre véhicule',
      intro: 'Vérifiez ce que j’ai compris et ajoutez son utilisation.',
      layout: 'single',
      entity: { type: 'vehicle', index: 0 },
      fields: [
        {
          key: '/vehicles/0/vehicle_year',
          widget: 'ymm',
          prefill: { value: { vehicle_year: 2021, vehicle_make: 'Toyota', vehicle_model: 'RAV4' }, source: 'document' },
        },
        { key: '/vehicles/0/usage_type', widget: 'chips' },
      ],
    },
    sample: { '/vehicles/0/usage_type': 'commute' },
  },
  {
    id: 'board-two-drivers',
    spec: {
      form_id: 'qui_conduit_quoi',
      title: 'Qui conduit quoi?',
      intro:
        'Glissez chaque personne vers le véhicule qu’elle conduit. Un conducteur principal par véhicule; les autres sont occasionnels.',
      layout: 'single',
      actions: [{ id: 'submit', label: 'Confirmer', style: 'primary' }],
      fields: [
        {
          key: '/household/driver_vehicle_assignments',
          widget: 'assignment_board',
          label: 'Conducteurs et véhicules',
          data: {
            drivers: [
              { index: 0, label: 'Marie Tremblay', sub: 'Vous' },
              { index: 1, label: 'Alex Tremblay', sub: 'Conjoint ou conjointe' },
            ],
            vehicles: [{ index: 0, label: '2021 Toyota RAV4', short: 'RAV4', sub: 'Aller au travail ou à l’école' }],
          },
        },
      ],
    },
    sample: {
      '/household/driver_vehicle_assignments': [
        { driver_index: 0, vehicle_index: 0, role: 'principal' },
        { driver_index: 1, vehicle_index: 0, role: 'occasional' },
      ],
    },
  },
  {
    id: 'offers-journey',
    spec: {
      form_id: 'offers_q_1a2b3c4d_v1',
      title: 'Votre soumission : 3 options',
      layout: 'single',
      entity: { type: 'underwriting' },
      actions: [{ id: 'select', label: 'Choisir cette offre', style: 'primary' }],
      fields: [
        {
          key: 'offer_type',
          widget: 'offer_cards',
          label: 'Choisissez une offre',
          data: { offers: OFFERS, recommended: 'plus', show_adjust: true },
        },
      ],
    },
    sample: { '/underwriting/offer_type': 'plus' },
  },
  {
    id: 'end-of-form-bff',
    spec: {
      form_id: 'end_of_form',
      title: 'Poursuivons avec un conseiller',
      intro: 'La soumission en ligne s’arrête ici. Un conseiller reprend votre dossier sans que vous ayez à tout répéter.',
      layout: 'two_column',
      entity: { type: 'quote' },
      actions: [
        { id: 'edit_answers', label: 'Modifier mes réponses', style: 'secondary' },
        { id: 'send', label: 'Envoyer à un conseiller', style: 'primary' },
      ],
      fields: [
        {
          widget: 'info',
          tone: 'warn',
          hint: '**Pourquoi?** Les véhicules exotiques ou de prestige, comme une Lamborghini, demandent une évaluation personnalisée par un conseiller : valeur de remplacement, entreposage et protections particulières. (règle VEHICULE_EXOTIQUE)',
        },
        { widget: 'section', label: 'Votre résumé, à vérifier' },
        { key: '/quote/first_name', required: true, prefill: { value: 'Samir' } },
        { key: '/quote/last_name', required: true, prefill: { value: 'Haddad' } },
        { key: '/quote/phone_number', required: true },
        { key: '/quote/email', required: false },
        {
          key: '/quote/handoff_summary',
          widget: 'textarea',
          label: 'Résumé de la conversation',
          hint: 'Modifiez-le au besoin : c’est ce que le conseiller lira en premier.',
          prefill: {
            value:
              'Samir Haddad souhaite une soumission d’assurance auto (Obtenir une nouvelle soumission).\nVéhicules : 2023 Lamborghini Huracán.\nConsentement : confirmé en deux étapes.',
          },
        },
        {
          key: '/quote/handoff_consent',
          widget: 'checkbox',
          label: 'J’accepte que ce résumé et mes réponses soient transmis à un conseiller Desjardins Assurances.',
          required: true,
        },
      ],
    },
    sample: { '/quote/phone_number': '514 555-0142', '/quote/handoff_consent': true },
  },
  {
    id: 'full-form',
    spec: {
      form_id: 'full_form',
      title: 'Votre soumission complète',
      layout: 'two_column',
      entity: { type: 'quote' },
      actions: [
        { id: 'back', label: 'Retour à la conversation', style: 'secondary' },
        { id: 'submit', label: 'Enregistrer et continuer', style: 'primary' },
      ],
      fields: [
        { widget: 'section', label: 'Vous' },
        { key: '/quote/first_name', required: false, prefill: { value: 'Marie', source: 'document', note: '98 %' } },
        { key: '/quote/last_name', required: false, prefill: { value: 'Tremblay', source: 'document' } },
        { key: '/quote/date_of_birth', required: false, prefill: { value: '1987-01-18', source: 'document' } },
        {
          key: '/quote/email',
          required: false,
          prefill: { value: 'marie.tremblay@exemple.com', source: 'chat', note: '« marie.tremblay@exemple.com »' },
        },
        { key: '/quote/phone_number', required: false },
        { widget: 'section', label: 'Véhicule 1' },
        {
          key: '/vehicles/0/vehicle_year',
          widget: 'ymm',
          required: true,
          prefill: { value: { vehicle_year: 2021, vehicle_make: 'Toyota', vehicle_model: 'RAV4' }, source: 'document' },
        },
        { key: '/vehicles/0/usage_type', widget: 'chips', required: false, prefill: { value: 'commute' } },
        { widget: 'section', label: 'Autres conducteurs' },
        DRIVERS_REPEATER,
        { widget: 'section', label: 'Antécédents' },
        { key: '/underwriting/has_claims', widget: 'toggle', required: false },
        { key: '/underwriting/has_convictions_in_past_three_years', widget: 'toggle', required: false },
        { key: '/underwriting/was_denied_or_cancelled', widget: 'toggle', required: false },
      ],
    },
    sample: {},
  },
  {
    id: 'matrix-legacy',
    spec: {
      form_id: 'affectations_matrice',
      title: 'Qui conduit quoi (tableau)',
      layout: 'single',
      entity: { type: 'household' },
      fields: [
        {
          key: 'driver_vehicle_assignments',
          widget: 'matrix',
          label: 'Conducteurs et véhicules',
          data: { rows: [{ label: 'Marie' }, { label: 'Alex' }], cols: [{ label: 'RAV4' }, { label: 'Civic' }] },
          prefill: { value: [{ driver_index: 0, vehicle_index: 0, role: 'principal' }] },
        },
      ],
    },
    sample: {},
  },
];
