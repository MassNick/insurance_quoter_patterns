/* forms.js: FormSpecs the main agent would send to a2ui.render_form.
 * Each entry is gallery metadata plus the FormSpec (the contract in contracts/form-spec.schema.json)
 * plus "sample" answers used by the demo-only "fill sample" button.
 */
(function (G) {
  'use strict';
  const Y = new Date().getFullYear();
  const iso = (d) => d.toISOString().slice(0, 10);
  const inDays = (n) => iso(new Date(Date.now() + n * 864e5));
  const OFFERS = [
    { offer_type: 'essential', monthly: 118.4, annual: 1420.8, deductible: 1000, coverages: ['Responsabilité civile 1\u00a0M$', 'Collision ou versement', 'Risques autres que la collision'] },
    { offer_type: 'plus', monthly: 142.75, annual: 1713, deductible: 500, coverages: ['Responsabilité civile 2\u00a0M$', 'Collision et autres risques', 'Assistance routière', 'Voiture de location jusqu’à 1\u00a0200\u00a0$'] },
    { offer_type: 'premium', monthly: 171.9, annual: 2062.8, deductible: 250, coverages: ['Responsabilité civile 2\u00a0M$', 'Remplacement à neuf (5 ans)', 'Pardon d’accident', 'Voiture de location jusqu’à 2\u00a0000\u00a0$'] }
  ];
  const UPLOAD_VIN = { id: 'upload_vin', label: 'Photo du NIV', kind: 'upload_photo' };

  const FORMS = [
    /* ---------- Variation C: small subject forms used by the journey (D13, D17, D18, D19) ---------- */
    {
      id: 'documents', group: 'Variation C · petits formulaires par sujet', title: 'Vos documents : permis, immatriculation, police actuelle',
      said: 'Oui, j’ai mes documents.', agent: 'Parfait. Téléversez vos documents ; je vous montre ce que j’ai lu avant de l’utiliser.',
      tags: ['DocumentDrop', 'agent de documents', 'révision avant envoi'],
      spec: { form_id: 'documents', title: 'Vos documents', intro: 'Notre agent de documents lit chaque photo. Vous vérifiez ce qu’il a trouvé avant qu’on l’utilise.', layout: 'single',
        actions: [{ id: 'nodocs', label: 'Continuer sans documents', style: 'secondary' }, { id: 'submit', label: 'Utiliser ces informations', style: 'primary' }],
        fields: [
          { widget: 'document_drop', label: 'Téléversez ceux que vous avez', data: { documents: [
            { purpose: 'licence', icon: 'idcard', title: 'Permis de conduire', description: 'Nom, date de naissance et numéro de permis.', fills: { first_name: '/quote/first_name', last_name: '/quote/last_name', date_of_birth: '/quote/date_of_birth', driver_license_number: '/drivers/0/driver_license_number' } },
            { purpose: 'registration', icon: 'regdoc', title: 'Certificat d’immatriculation', description: 'Année, marque, modèle et numéro d’identification du véhicule.', fills: { vehicle_year: '/vehicles/0/vehicle_year', vehicle_make: '/vehicles/0/vehicle_make', vehicle_model: '/vehicles/0/vehicle_model', vin: '/vehicles/0/vin' } },
            { purpose: 'policy', icon: 'policy', title: 'Police d’assurance actuelle', description: 'Ancienneté chez votre assureur et conducteurs inscrits.', fills: { years_with_current_insurer: '/drivers/0/years_with_current_insurer', listed_driver_1_name: '/drivers/1/driver_name', listed_driver_1_relationship: '/drivers/1/relationship_code' }, labels: { listed_driver_1_name: 'Autre conducteur inscrit', listed_driver_1_relationship: 'Lien avec vous' } }
          ] } }
        ] },
      sample: 'readAll'
    },
    {
      id: 'about-you', group: 'Variation C · petits formulaires par sujet', title: 'À propos de vous (court formulaire)',
      said: 'Marie Tremblay.', agent: 'Enchanté, Marie ! Deux détails rapides.',
      tags: ['2 colonnes', 'préremplissage depuis la conversation'],
      spec: { form_id: 'a_propos', title: 'À propos de vous', entity: { type: 'quote' },
        fields: [
          { key: 'first_name', prefill: { value: 'Marie', source: 'chat', note: '« Marie Tremblay »' } },
          { key: 'last_name', prefill: { value: 'Tremblay', source: 'chat' } },
          { key: 'date_of_birth', hint: 'Sert à établir votre expérience de conduite.' },
          { key: 'email', hint: 'Nous vous y enverrons votre soumission.' }
        ] },
      sample: { '/quote/date_of_birth': '1987-01-18', '/quote/email': 'marie.tremblay@exemple.com' }
    },
    {
      id: 'vehicle-usage', group: 'Variation C · petits formulaires par sujet', title: 'Votre véhicule et son utilisation',
      said: 'Une Toyota RAV4 2021.', agent: 'Voici ce que j’ai compris. Vérifiez-le et indiquez son utilisation.',
      tags: ['YmmPicker', 'tuiles', 'validation en direct'],
      spec: { form_id: 'vehicule_0', title: 'Votre véhicule', entity: { type: 'vehicle', index: 0 }, layout: 'single',
        fields: [
          { key: 'vehicle_year', widget: 'ymm', prefill: { value: { vehicle_year: 2021, vehicle_make: 'Toyota', vehicle_model: 'RAV4' }, source: 'chat' } },
          { key: 'usage_type', widget: 'chips' }
        ] },
      sample: { '/vehicles/0/usage_type': 'commute' }
    },
    {
      id: 'other-drivers', group: 'Variation C · petits formulaires par sujet', title: 'Les autres conducteurs (répéteur court)',
      said: 'J’aimerais un formulaire pour les conducteurs.', agent: 'C’est noté : je vous propose des formulaires pour la suite. Ajoutez les autres conducteurs ici.',
      tags: ['préférence de réponse', 'répéteur'],
      spec: { form_id: 'conducteurs', title: 'Les autres conducteurs', intro: 'Vous êtes déjà inscrit. Ajoutez les personnes qui conduisent, même à l’occasion.', layout: 'single',
        fields: [
          { key: '/drivers', widget: 'repeater', label: 'Autres conducteurs', repeat: { start: 1, count: 2, min: 0, max: 6, item_label: 'Conducteur {n}', add_label: 'Ajouter un conducteur', fields: [{ key: 'driver_name', widget: 'text', label: 'Prénom et nom' }, { key: 'relationship_code', widget: 'select' }, { key: 'driver_date_of_birth', widget: 'date' }] } }
        ] },
      sample: { '/drivers/1/driver_name': 'Alex Tremblay', '/drivers/1/relationship_code': 'spouse', '/drivers/1/driver_date_of_birth': '1986-05-02', '/drivers/2/driver_name': 'Léo Tremblay', '/drivers/2/relationship_code': 'child', '/drivers/2/driver_date_of_birth': '2009-03-09' }
    },
    {
      id: 'history-c', group: 'Variation C · petits formulaires par sujet', title: 'Vos antécédents (3 réponses protégées)',
      said: 'Continuer ma soumission.', agent: 'Presque fini ! Trois questions sur vos antécédents.',
      tags: ['champs protégés', 'double confirmation'],
      spec: { form_id: 'antecedents', title: 'Vos antécédents', intro: 'Trois questions. Ces réponses sont protégées : vous les confirmez avant qu’on les utilise.', entity: { type: 'underwriting' }, layout: 'single',
        fields: [{ key: 'has_claims', widget: 'toggle' }, { key: 'has_convictions_in_past_three_years', widget: 'toggle' }, { key: 'was_denied_or_cancelled', widget: 'toggle' }] },
      sample: { '/underwriting/has_claims': false, '/underwriting/has_convictions_in_past_three_years': false, '/underwriting/was_denied_or_cancelled': false }
    },
    {
      id: 'end-of-form', group: 'Variation C · petits formulaires par sujet', title: 'Fin du formulaire : transfert à un conseiller (règle)',
      said: 'Oui, c’est bien ça.', agent: 'Merci pour ces précisions. Pour cette demande, je dois vous diriger vers un conseiller. Voici pourquoi, et un résumé à vérifier avant l’envoi.',
      tags: ['EndOfFormRules', 'rendu par le BFF', 'résumé modifiable'],
      spec: { form_id: 'fin_conseiller', title: 'Poursuivons avec un conseiller', intro: 'La soumission en ligne s’arrête ici. Un conseiller reprend votre dossier sans que vous ayez à tout répéter.', entity: { type: 'quote' },
        actions: [{ id: 'edit', label: 'Modifier mes réponses', style: 'secondary' }, { id: 'submit', label: 'Envoyer à un conseiller', style: 'primary' }],
        fields: [
          { widget: 'info', tone: 'warn', hint: '**Pourquoi ?** Les véhicules exotiques ou de prestige, comme une Lamborghini, demandent une évaluation personnalisée par un conseiller (règle VEHICULE_EXOTIQUE).' },
          { widget: 'section', label: 'Votre résumé, à vérifier' },
          { key: 'first_name', required: true, prefill: { value: 'Samir' } }, { key: 'last_name', required: true, prefill: { value: 'Haddad' } },
          { key: 'phone_number', required: true }, { key: 'email', required: false, prefill: { value: 'samir@exemple.com' } },
          { key: 'handoff_summary', widget: 'textarea', hint: 'Modifiez-le au besoin : c’est ce que le conseiller lira en premier.', prefill: { value: 'Samir Haddad souhaite une soumission d’assurance auto.\nVéhicule : 2023 Lamborghini Huracán.\nConsentement : confirmé en deux étapes.' } },
          { key: 'handoff_consent', widget: 'checkbox', required: true }
        ] },
      sample: { '/quote/phone_number': '514 555-0142', '/quote/handoff_consent': true }
    },
    {
      id: 'consent', group: 'Soumission, titulaire et coordonnées', title: 'Consentement à l’utilisation de mes renseignements',
      said: 'Oui, allez-y.', agent: 'Merci. Avant de recueillir des renseignements personnels, veuillez confirmer comment ils seront utilisés.',
      tags: ['déterministe', 'préremplissage depuis la conversation', 'double confirmation'],
      spec: { form_id: 'consent', title: 'Votre consentement', layout: 'single', entity: { type: 'quote' },
        intro: 'Vous avez écrit **«\u00a0oui, allez-y\u00a0»** dans la conversation. Le consentement est un champ protégé\u00a0: vous le confirmerez donc deux fois.',
        fields: [
          { widget: 'info', tone: 'info', label: 'Ce que nous utilisons', hint: 'vos réponses, les documents que vous téléversez et les recherches d’adresse et de véhicule, uniquement pour préparer cette soumission.' },
          { key: 'contact_consent', widget: 'checkbox', label: 'J’accepte que mes renseignements soient utilisés pour préparer ma soumission d’assurance auto et qu’un conseiller puisse communiquer avec moi à ce sujet.', prefill: { value: true, source: 'chat', note: '«\u00a0oui, allez-y\u00a0»' } }
        ],
        actions: [{ id: 'submit', label: 'J’accepte', style: 'primary' }, { id: 'dismiss', label: 'Pas maintenant', style: 'link' }] },
      sample: { '/quote/contact_consent': true }
    },
    {
      id: 'policyholder', group: 'Soumission, titulaire et coordonnées', title: 'Identité du titulaire de la police',
      said: 'Je m’appelle Marie Tremblay, je veux assurer mon auto.', agent: 'C’est un plaisir de faire votre connaissance, Marie. J’ai inscrit ce que vous m’avez dit. Vérifiez-le et ajoutez votre date de naissance.',
      tags: ['préremplissage depuis la conversation', 'validation'],
      spec: { form_id: 'policyholder', title: 'À propos de vous', entity: { type: 'quote' },
        fields: [
          { key: 'first_name', prefill: { value: 'Marie', source: 'chat', note: '«\u00a0Je m’appelle Marie Tremblay\u00a0»' } },
          { key: 'last_name', prefill: { value: 'Tremblay', source: 'chat' } },
          { key: 'date_of_birth', hint: 'Vous devez avoir 16 ans ou plus.' },
          { key: 'gender', widget: 'select' },
          { key: 'policyholder_slot', label: 'Qui est le titulaire de la police\u00a0?', prefill: { value: 'self', source: 'chat', note: '«\u00a0assurer mon auto\u00a0»' } }
        ] },
      sample: { '/quote/first_name': 'Marie', '/quote/last_name': 'Tremblay', '/quote/date_of_birth': '1986-03-14', '/quote/gender': 'female', '/quote/policyholder_slot': 'self' }
    },
    {
      id: 'contact', group: 'Soumission, titulaire et coordonnées', title: 'Coordonnées',
      said: 'Vous pouvez m’écrire par message texte au 514 555 0142.', agent: 'Compris. Voici où nous enverrons votre soumission.',
      tags: ['préremplissage depuis la conversation', 'validation', 'format du téléphone'],
      spec: { form_id: 'contact', title: 'Comment vous joindre', entity: { type: 'quote' },
        fields: [
          { key: 'email', hint: 'Nous y enverrons votre soumission.' },
          { key: 'phone_number', prefill: { value: '514 555 0142', source: 'chat', note: '«\u00a0par message texte au 514 555 0142\u00a0»' } },
          { key: 'phone_type', prefill: { value: 'mobile', source: 'chat', note: '«\u00a0par message texte\u00a0»' } }
        ] },
      sample: { '/quote/email': 'marie.tremblay@example.com', '/quote/phone_number': '(514) 555-0142', '/quote/phone_type': 'mobile' }
    },
    {
      id: 'address', group: 'Soumission, titulaire et coordonnées', title: 'Adresse avec recherche',
      said: 'J’habite sur la rue des Érables, à Laval.', agent: 'J’ai trouvé quelques adresses qui correspondent à votre rue. Choisissez la vôtre et sélectionnez une date de début.',
      tags: ['outil de recherche', 'validation du code postal', 'plage de dates'],
      spec: { form_id: 'address', title: 'Adresse du domicile et date de début', entity: { type: 'quote' },
        intro: 'Vous avez écrit **«\u00a0rue des Érables, à Laval\u00a0»**. Cliquez sur Chercher pour voir les adresses correspondantes.',
        fields: [
          { widget: 'address_lookup', label: 'Adresse du domicile', hint: 'Là où le véhicule est habituellement stationné la nuit.' },
          { key: 'effective_date', hint: 'La protection peut commencer aujourd’hui ou dans les 90 prochains jours.' }
        ] },
      sample: { '/quote/address_line1': '125 Rue des Érables', '/quote/city': 'Laval', '/quote/province_code': 'QC', '/quote/postal_code': 'H7N 1A1', '/quote/effective_date': inDays(14) }
    },
    {
      id: 'vehicle', group: 'Véhicule', title: 'Année, marque et modèle avec validation en direct',
      said: 'C’est une Toyota RAV4 2021.', agent: 'Voici votre véhicule. Je l’ai validé à l’aide de notre catalogue, et vous pouvez ajouter le NIV à partir d’une photo.',
      tags: ['préremplissage depuis la conversation', 'outil de recherche', 'validation en cascade', 'remplissage par photo'],
      spec: { form_id: 'vehicle_1', title: 'Votre véhicule', entity: { type: 'vehicle', index: 0 },
        fields: [
          { widget: 'ymm', key: 'vehicle_year', prefill: { value: { vehicle_year: 2021, vehicle_make: 'Toyota', vehicle_model: 'RAV4' }, source: 'chat', note: '«\u00a0une Toyota RAV4 2021\u00a0»' } },
          { key: 'vin', required: false, hint: 'Facultatif, mais il permet d’obtenir un prix plus précis.', field_actions: [UPLOAD_VIN] },
          { key: 'vehicle_value', hint: 'Valeur actuelle approximative.' }
        ] },
      sample: { '/vehicles/0/vehicle_year': 2021, '/vehicles/0/vehicle_make': 'Toyota', '/vehicles/0/vehicle_model': 'RAV4', '/vehicles/0/vehicle_value': 31000 }
    },
    {
      id: 'vehicle-details', group: 'Véhicule', title: 'Acquisition et état du véhicule',
      said: 'Je l’ai achetée usagée il y a deux ans, avec un prêt.', agent: 'Merci. Quelques détails rapides sur la façon dont vous avez obtenu le véhicule.',
      tags: ['préremplissage depuis la conversation', 'boutons oui/non'],
      spec: { form_id: 'vehicle_1_details', title: 'Comment vous avez obtenu le véhicule', entity: { type: 'vehicle', index: 0 },
        fields: [
          { key: 'acquisition_condition', prefill: { value: 'used', source: 'chat', note: '«\u00a0achetée usagée\u00a0»' } },
          { key: 'acquisition_type', prefill: { value: 'financed', source: 'chat', note: '«\u00a0avec un prêt\u00a0»' } },
          { key: 'antique_vehicle' },
          { key: 'modified_for_performance', hint: 'Modifications du moteur, de l’échappement ou de la suspension pour accroître la performance.' }
        ] },
      sample: { '/vehicles/0/acquisition_condition': 'used', '/vehicles/0/acquisition_type': 'financed', '/vehicles/0/antique_vehicle': false, '/vehicles/0/modified_for_performance': false }
    },
    {
      id: 'usage', group: 'Véhicule', title: 'Utilisation, kilométrage, hors Québec (champs conditionnels)',
      said: 'Je me rends en voiture à mon travail, à Ottawa, quelques fois par mois.', agent: 'Comme vous conduisez jusqu’en Ontario, j’ai ajouté deux questions sur le temps passé à l’extérieur du Québec.',
      tags: ['champs conditionnels', 'curseur', 'signal\u00a0: hors province'],
      spec: { form_id: 'vehicle_1_usage', title: 'Comment vous l’utilisez', entity: { type: 'vehicle', index: 0 },
        fields: [
          { key: 'usage_type', prefill: { value: 'commute', source: 'chat', note: '«\u00a0me rends en voiture à mon travail\u00a0»' } },
          { key: 'annual_kilometers', widget: 'slider', max: 60000 },
          { key: 'outside_quebec', prefill: { value: true, source: 'chat', note: '«\u00a0à Ottawa\u00a0»' } },
          { key: 'outside_quebec_more_than_25_weeks', visible_if: 'outside_quebec == true' },
          { key: 'weeks_outside_quebec', visible_if: 'outside_quebec == true' }
        ] },
      sample: { '/vehicles/0/usage_type': 'commute', '/vehicles/0/annual_kilometers': 18000, '/vehicles/0/outside_quebec': true, '/vehicles/0/outside_quebec_more_than_25_weeks': false, '/vehicles/0/weeks_outside_quebec': 6 }
    },
    {
      id: 'parking', group: 'Véhicule', title: 'Stationnement',
      said: 'Elle reste dans le garage la nuit.', agent: 'Le stationnement influe sur le risque de vol. Confirmez où le véhicule passe la nuit.',
      tags: ['préremplissage depuis la conversation'],
      spec: { form_id: 'vehicle_1_parking', title: 'Lieu de stationnement', entity: { type: 'vehicle', index: 0 },
        fields: [
          { key: 'parking_place_type_code', widget: 'radio', prefill: { value: 'private_garage', source: 'chat', note: '«\u00a0dans le garage\u00a0»' } },
          { key: 'parking_hours_code', prefill: { value: 'night', source: 'chat', note: '«\u00a0la nuit\u00a0»' } },
          { key: 'night_parking_label', hint: 'Facultatif\u00a0: tout détail inhabituel, comme un garage partagé.' }
        ] },
      sample: { '/vehicles/0/parking_place_type_code': 'private_garage', '/vehicles/0/parking_hours_code': 'night', '/vehicles/0/night_parking_label': 'Garage attenant à la maison' }
    },
    {
      id: 'fleet', group: 'Véhicule', title: '10 voitures identiques\u00a0: une saisie année-marque-modèle, 10 champs NIV avec photo',
      said: 'Nous avons 10 Corolla 2023 identiques pour l’entreprise.', agent: 'Dix voitures identiques\u00a0: saisissez le modèle une seule fois, puis un NIV par voiture. Une photo du NIV remplit chaque champ pour vous.',
      tags: ['cas atypique', 'répétition ×10', 'remplissage par photo', 'signal\u00a0: flotte'],
      spec: { form_id: 'fleet_vins', title: '10 véhicules identiques', entity: { type: 'vehicle', index: 0 }, layout: 'single',
        intro: 'Vous avez écrit **«\u00a010 Corolla 2023 identiques\u00a0»**. L’année, la marque et le modèle s’appliquent aux 10 voitures.',
        fields: [
          { widget: 'ymm', key: 'vehicle_year', prefill: { value: { vehicle_year: 2023, vehicle_make: 'Toyota', vehicle_model: 'Corolla' }, source: 'chat', note: '«\u00a0Corolla 2023\u00a0»' } },
          { widget: 'repeater', key: '/vehicles', label: 'NIV de chaque voiture', hint: 'Saisissez chaque NIV ou téléversez une photo de la plaque du NIV (pare-brise ou cadre de porte).',
            repeat: { count: 10, start: 0, min: 10, max: 10, item_label: 'Voiture {n}', fields: [{ key: 'vin', widget: 'vin', field_actions: [UPLOAD_VIN] }] } }
        ] },
      sample: {}
    },
    {
      id: 'driver-profile', group: 'Conducteur', title: 'Profil du conducteur',
      said: 'Ma femme et moi travaillons toutes les deux, et je suis infirmière.', agent: 'Quelques questions sur vous en tant que conducteur. J’ai déduit votre état civil, veuillez le confirmer.',
      tags: ['valeur déduite', 'préremplissage depuis la conversation'],
      spec: { form_id: 'driver_0_profile', title: 'Vous, en tant que conducteur', entity: { type: 'driver', index: 0 },
        fields: [
          { key: 'marital_status_code', prefill: { value: 'married', source: 'chat', note: 'déduit de «\u00a0ma femme\u00a0»\u00a0: à confirmer' } },
          { key: 'employment_status_code', widget: 'select', prefill: { value: 'employed', source: 'chat', note: '«\u00a0travaillons toutes les deux\u00a0»' } },
          { key: 'occupation_label', prefill: { value: 'Infirmière', source: 'chat', note: '«\u00a0je suis infirmière\u00a0»' } },
          { key: 'education_level_code', widget: 'select' }
        ] },
      sample: { '/drivers/0/marital_status_code': 'married', '/drivers/0/employment_status_code': 'employed', '/drivers/0/occupation_label': 'Infirmière', '/drivers/0/education_level_code': 'bachelor' }
    },
    {
      id: 'licence', group: 'Conducteur', title: 'Permis de conduire et expérience',
      said: 'J’ai obtenu mon permis de conduire à 17 ans.', agent: 'Voici les détails de votre permis de conduire. Vous pouvez remplir le numéro à partir d’une photo de votre permis.',
      tags: ['déterministe', 'remplissage par photo', 'champ calculé', 'validation du format'],
      spec: { form_id: 'driver_0_licence', title: 'Votre permis de conduire', entity: { type: 'driver', index: 0 },
        fields: [
          { key: 'driver_license_number', field_actions: [{ id: 'upload_licence', label: 'Photo du permis', kind: 'upload_photo' }] },
          { key: 'age_obtained_license', prefill: { value: 17, source: 'chat', note: '«\u00a0à 17 ans\u00a0»' } },
          { key: 'license_year', label: 'Année d’obtention du premier permis de conduire' },
          { key: 'years_licensed', readonly: true, hint: 'Calculé par le BFF à partir de l’année d’obtention du premier permis.' },
          { key: 'has_foreign_license' },
          { key: 'heavy_vehicle_experience_years' }
        ] },
      sample: { '/drivers/0/driver_license_number': 'T6512-140386-08', '/drivers/0/age_obtained_license': 17, '/drivers/0/license_year': 2003, '/drivers/0/years_licensed': Y - 2003, '/drivers/0/has_foreign_license': false, '/drivers/0/heavy_vehicle_experience_years': 0 }
    },
    {
      id: 'history', group: 'Conducteur', title: 'Historique d’assurance et âge de conducteur principal',
      said: 'Je suis chez mon assureur depuis 6 ans.', agent: 'La fidélité et l’expérience comptent dans le calcul de votre prix. Deux chiffres rapides à indiquer.',
      tags: ['préremplissage depuis la conversation', 'champ dérivé'],
      spec: { form_id: 'driver_0_history', title: 'Votre historique d’assurance', entity: { type: 'driver', index: 0 },
        fields: [
          { key: 'years_with_current_insurer', prefill: { value: 6, source: 'chat', note: '«\u00a0depuis 6 ans\u00a0»' } },
          { key: 'age_became_principal_driver', hint: 'L’âge où vous avez eu, pour la première fois, un véhicule que vous conduisiez principalement.' },
          { key: 'years_as_principal_driver', readonly: true, hint: 'Calculé à partir de votre âge et de la réponse ci-dessus.' }
        ] },
      sample: { '/drivers/0/years_with_current_insurer': 6, '/drivers/0/age_became_principal_driver': 21, '/drivers/0/years_as_principal_driver': 18 }
    },
    {
      id: 'additional-drivers', group: 'Conducteur', title: 'Conducteurs additionnels (liste répétable)',
      said: 'Nous sommes 3 à conduire\u00a0: moi, mon mari Alex et notre fils Léo.', agent: 'Vous avez mentionné 3 conducteurs. Parlez-moi un peu d’Alex et de Léo.',
      tags: ['répétition', 'signal\u00a0: 3 conducteurs', 'préremplissage depuis la conversation'],
      spec: { form_id: 'drivers_extra', title: 'Autres conducteurs', layout: 'single',
        intro: 'Vous avez écrit que **3 conducteurs** utilisent la voiture. Ajoutez ou retirez des conducteurs au besoin.',
        fields: [
          { widget: 'repeater', key: '/drivers', label: 'Conducteurs du ménage',
            repeat: { count: 2, start: 1, min: 0, max: 6, item_label: 'Conducteur {n}', add_label: 'Ajouter un conducteur', prefill: [{ driver_name: 'Alex Tremblay', relationship_code: 'spouse' }, { driver_name: 'Léo Tremblay', relationship_code: 'child' }],
              fields: [{ key: 'driver_name' }, { key: 'relationship_code', widget: 'select' }, { key: 'driver_date_of_birth' }, { key: 'age_obtained_license' }, { key: 'limited_use_frequency_code', widget: 'select' }] } }
        ] },
      sample: { '/drivers/1/driver_name': 'Alex Tremblay', '/drivers/1/relationship_code': 'spouse', '/drivers/1/driver_date_of_birth': '1984-07-02', '/drivers/1/age_obtained_license': 18, '/drivers/1/limited_use_frequency_code': 'occasional', '/drivers/2/driver_name': 'Léo Tremblay', '/drivers/2/relationship_code': 'child', '/drivers/2/driver_date_of_birth': `${Y - 17}-05-20`, '/drivers/2/age_obtained_license': 17, '/drivers/2/limited_use_frequency_code': 'student_away' }
    },
    {
      id: 'claims', group: 'Souscription', title: 'Réclamations et refus d’assurance (liste répétable conditionnelle)',
      said: 'J’ai eu un petit accident en 2023.', agent: 'Merci de me l’avoir dit. Veuillez confirmer vos réclamations. Ces réponses sont confirmées deux fois.',
      tags: ['déterministe', 'liste répétable conditionnelle', 'préremplissage depuis la conversation'],
      spec: { form_id: 'claims', title: 'Historique de réclamations', entity: { type: 'underwriting' }, layout: 'single',
        fields: [
          { key: 'has_claims', prefill: { value: true, source: 'chat', note: '«\u00a0un petit accident en 2023\u00a0»' } },
          { widget: 'repeater', key: '/underwriting/claims_detail', label: 'Réclamations', visible_if: 'has_claims == true',
            repeat: { count: 1, start: 0, min: 1, max: 6, item_label: 'Réclamation {n}', add_label: 'Ajouter une réclamation',
              fields: [{ key: 'date', widget: 'date', label: 'Date' }, { key: 'type', widget: 'select', label: 'Type', options: [['collision', 'Collision'], ['theft', 'Vol'], ['glass', 'Bris de vitre'], ['vandalism', 'Vandalisme'], ['other', 'Autre']] }, { key: 'amount', widget: 'currency', label: 'Montant payé' }, { key: 'at_fault', widget: 'toggle', label: 'Étiez-vous responsable\u00a0?' }] } },
          { key: 'was_denied_or_cancelled' }
        ] },
      sample: { '/underwriting/has_claims': true, '/underwriting/claims_detail/0/date': '2023-06-12', '/underwriting/claims_detail/0/type': 'collision', '/underwriting/claims_detail/0/amount': 2400, '/underwriting/claims_detail/0/at_fault': false, '/underwriting/was_denied_or_cancelled': false }
    },
    {
      id: 'convictions', group: 'Souscription', title: 'Infractions routières',
      said: 'J’ai reçu une contravention pour excès de vitesse l’an dernier.', agent: 'Dernière question sur votre dossier de conduite. Les infractions des 3 dernières années comptent.',
      tags: ['déterministe', 'liste répétable conditionnelle'],
      spec: { form_id: 'convictions', title: 'Infractions routières', entity: { type: 'underwriting' }, layout: 'single',
        fields: [
          { key: 'has_convictions_in_past_three_years', prefill: { value: true, source: 'chat', note: '«\u00a0une contravention pour excès de vitesse l’an dernier\u00a0»' } },
          { widget: 'repeater', key: '/underwriting/convictions_detail', label: 'Infractions', visible_if: 'has_convictions_in_past_three_years == true',
            repeat: { count: 1, start: 0, min: 1, max: 6, item_label: 'Infraction {n}', add_label: 'Ajouter une infraction',
              fields: [{ key: 'date', widget: 'date', label: 'Date' }, { key: 'type', widget: 'select', label: 'Type', options: [['speeding', 'Excès de vitesse'], ['distracted', 'Conduite distraite'], ['red_light', 'Feu rouge brûlé'], ['impaired', 'Conduite avec les capacités affaiblies'], ['other', 'Autre']] }] } }
        ] },
      sample: { '/underwriting/has_convictions_in_past_three_years': true, '/underwriting/convictions_detail/0/date': `${Y - 1}-08-03`, '/underwriting/convictions_detail/0/type': 'speeding' }
    },
    {
      id: 'assignments', group: 'Affectations du ménage', title: 'Qui conduit quoi : glisser-déposer les conducteurs',
      said: 'Alex conduit surtout la Civic, et moi la RAV4.', agent: 'Dernière étape avant le calcul du prix\u00a0: qui conduit quel véhicule\u00a0?',
      tags: ['AssignmentBoard', 'glisser-déposer', 'préremplissage depuis la conversation', 'validation entre champs'],
      spec: { form_id: 'assignments', title: 'Qui conduit quoi\u00a0?', intro: 'Glissez chaque personne vers le véhicule qu’elle conduit. Un conducteur principal par véhicule.', entity: { type: 'household' }, layout: 'single',
        fields: [
          { key: 'driver_vehicle_assignments', widget: 'assignment_board', label: 'Conducteurs et véhicules',
            data: { drivers: [{ index: 0, label: 'Marie Tremblay', sub: 'Vous' }, { index: 1, label: 'Alex Tremblay', sub: 'Conjoint ou conjointe' }, { index: 2, label: 'Léo Tremblay', sub: 'Enfant, 17 ans' }], vehicles: [{ index: 0, label: '2021 Toyota RAV4', short: 'RAV4', sub: 'Aller au travail ou à l’école' }, { index: 1, label: '2015 Honda Civic', short: 'Civic', sub: 'Agrément' }] },
            prefill: { value: [{ driver_index: 0, vehicle_index: 0, role: 'principal' }, { driver_index: 1, vehicle_index: 1, role: 'principal' }], source: 'chat', note: '«\u00a0Alex conduit surtout la Civic, et moi la RAV4\u00a0»' } }
        ] },
      sample: { '/household/driver_vehicle_assignments': [{ driver_index: 0, vehicle_index: 0, role: 'principal' }, { driver_index: 1, vehicle_index: 1, role: 'principal' }, { driver_index: 2, vehicle_index: 1, role: 'occasional' }, { driver_index: 1, vehicle_index: 0, role: 'occasional' }] }
    },
    {
      id: 'young-driver', group: 'Conducteur', title: 'Jeune conducteur, aux études loin du domicile familial',
      said: 'Léo a 17 ans, il étudie à Sherbrooke pendant l’année.', agent: 'Les étudiants qui vivent loin du domicile familial peuvent être admissibles à un tarif réduit. Quelques détails sur Léo.',
      tags: ['signal\u00a0: étudiant loin du foyer', 'signal\u00a0: jeune conducteur', 'boutons radio'],
      spec: { form_id: 'driver_2_student', title: 'À propos de Léo', entity: { type: 'driver', index: 2 },
        intro: 'Signal de l’agent de mémoire\u00a0: **jeune conducteur** et **étudiant loin du domicile familial**.', intro_tone: 'info',
        fields: [
          { key: 'driver_name', prefill: { value: 'Léo Tremblay', source: 'chat' }, readonly: true },
          { key: 'driver_date_of_birth' },
          { key: 'age_obtained_license' },
          { key: 'education_level_code', widget: 'select', prefill: { value: 'cegep', source: 'chat', note: '«\u00a0il étudie à Sherbrooke\u00a0»' } },
          { key: 'limited_use_frequency_code', widget: 'radio', prefill: { value: 'student_away', source: 'chat', note: '«\u00a0pendant l’année\u00a0»' } }
        ] },
      sample: { '/drivers/2/driver_name': 'Léo Tremblay', '/drivers/2/driver_date_of_birth': `${Y - 17}-05-20`, '/drivers/2/age_obtained_license': 17, '/drivers/2/education_level_code': 'cegep', '/drivers/2/limited_use_frequency_code': 'student_away' }
    },
    {
      id: 'doc-licence', group: 'Documents', title: 'Téléversement du permis de conduire\u00a0: l’agent de documents extrait les champs',
      said: 'Puis-je simplement vous envoyer une photo de mon permis de conduire\u00a0?', agent: 'Bien sûr. Téléversez-la ici. L’agent de documents la lit et remplit les champs, que vous pourrez ensuite vérifier.',
      tags: ['agent de documents', 'niveaux de confiance', 'déterministe'],
      spec: { form_id: 'doc_licence', title: 'Remplir à partir de votre permis', entity: { type: 'driver', index: 0 }, layout: 'single',
        fields: [
          { widget: 'file', label: 'Photo de votre permis de conduire', hint: 'Lu par l’agent de documents (gpt-5.6-luna). Rien n’est enregistré tant que vous n’avez pas soumis le formulaire.', data: { purpose: 'licence', fills: { driver_license_number: '/drivers/0/driver_license_number', first_name: '/quote/first_name', last_name: '/quote/last_name', date_of_birth: '/quote/date_of_birth' } } },
          { key: 'driver_license_number' },
          { key: '/quote/first_name', label: 'Prénom' },
          { key: '/quote/last_name', label: 'Nom de famille' },
          { key: '/quote/date_of_birth', label: 'Date de naissance', widget: 'date' }
        ] },
      sample: { '/drivers/0/driver_license_number': 'T6512-140386-08', '/quote/first_name': 'Marie', '/quote/last_name': 'Tremblay', '/quote/date_of_birth': '1986-03-14' }
    },
    {
      id: 'goal-date', group: 'Soumission, titulaire et coordonnées', title: 'Objectif de la soumission et date de début',
      said: 'Ma police se renouvelle à la fin de décembre, je veux comparer.', agent: 'Compris, vous comparez avant votre renouvellement. Confirmez votre objectif et la date à laquelle la nouvelle protection doit commencer.',
      tags: ['préremplissage depuis la conversation', 'plage de dates'],
      spec: { form_id: 'goal', title: 'Votre objectif', entity: { type: 'quote' }, layout: 'single',
        fields: [
          { key: 'quote_goal', widget: 'radio', prefill: { value: 'compare_current', source: 'chat', note: '«\u00a0je veux comparer\u00a0»' } },
          { key: 'effective_date', hint: 'Conseil\u00a0: choisissez le jour où votre police actuelle prend fin.' }
        ] },
      sample: { '/quote/quote_goal': 'compare_current', '/quote/effective_date': inDays(30) }
    },
    {
      id: 'offers', group: 'Conseils', title: 'Offres de la soumission (cartes)',
      said: 'Je veux obtenir ma soumission.', agent: 'Voici vos trois options. Je vous recommande Plus, qui couvre la voiture de location dont vous avez besoin.',
      tags: ['render_offers', 'recommandation'],
      spec: { form_id: 'offers_q_1042', title: 'Votre soumission\u00a0: 3 options', entity: { type: 'underwriting' }, layout: 'single',
        fields: [{ key: 'offer_type', widget: 'offer_cards', label: 'Choisissez une offre', data: { offers: OFFERS, recommended: 'plus' } }],
        actions: [{ id: 'select', label: 'Choisir cette offre', style: 'primary' }] },
      sample: { '/underwriting/offer_type': 'plus' }
    },
    {
      id: 'adjust', group: 'Conseils', title: 'Modifier la soumission (crée une nouvelle version)',
      said: 'Puis-je la rendre moins chère\u00a0?', agent: 'Une franchise plus élevée ou un kilométrage réduit fait baisser le prix. Ajustez et je referai le calcul sous forme de nouvelle version.',
      tags: ['modify_quote', 'sélection multiple', 'curseur'],
      spec: { form_id: 'adjust_q_1042', title: 'Modifier votre soumission', entity: { type: 'underwriting' }, layout: 'single',
        intro: 'Chaque changement crée une **nouvelle version de la soumission**. Votre version actuelle reste sous Soumissions précédentes.', intro_tone: 'info',
        fields: [
          { key: 'deductible', prefill: { value: '500', source: 'previous_quote' } },
          { key: 'add_ons', label: 'Protections facultatives', prefill: { value: ['roadside'], source: 'previous_quote' } },
          { key: '/vehicles/0/annual_kilometers', label: 'Kilométrage annuel', widget: 'slider', max: 60000, prefill: { value: 18000, source: 'previous_quote' } }
        ],
        actions: [{ id: 'submit', label: 'Recalculer le prix', style: 'primary' }] },
      sample: { '/underwriting/deductible': '1000', '/underwriting/add_ons': ['roadside'], '/vehicles/0/annual_kilometers': 15000 }
    },
    {
      id: 'property', group: 'Conseils', title: 'Vente croisée\u00a0: assurance habitation',
      said: 'Offrez-vous aussi de l’assurance habitation\u00a0?', agent: 'Oui. Le regroupement fait habituellement baisser les deux primes. Voulez-vous qu’un conseiller prépare une soumission habitation\u00a0?',
      tags: ['signal\u00a0: regroupement', 'plages horaires conditionnelles'],
      spec: { form_id: 'property', title: 'Soumission habitation', entity: { type: 'underwriting' }, layout: 'single',
        fields: [
          { widget: 'info', tone: 'ok', label: 'Regroupement', hint: 'l’assurance auto et l’assurance habitation réunies permettent habituellement d’économiser de 10 à 15\u00a0%.' },
          { key: 'interested_by_property_quote', label: 'Préparer aussi une soumission habitation\u00a0?', prefill: { value: true, source: 'chat', note: '«\u00a0aussi de l’assurance habitation\u00a0»' } },
          { key: 'callback_slot', widget: 'time_slots', label: 'Quand un conseiller peut-il vous appeler\u00a0?', visible_if: 'interested_by_property_quote == true', options: [['mon-am', 'Lundi, 9\u00a0h à 12\u00a0h'], ['mon-pm', 'Lundi, 13\u00a0h à 17\u00a0h'], ['tue-am', 'Mardi, 9\u00a0h à 12\u00a0h'], ['tue-pm', 'Mardi, 13\u00a0h à 17\u00a0h'], ['wed-am', 'Mercredi, 9\u00a0h à 12\u00a0h'], ['wed-pm', 'Mercredi, 13\u00a0h à 17\u00a0h']] }
        ] },
      sample: { '/underwriting/interested_by_property_quote': true, '/underwriting/callback_slot': 'tue-am' }
    },
    {
      id: 'callback', group: 'Conseils', title: 'Parler à un conseiller (transfert)',
      said: 'C’est compliqué, est-ce que quelqu’un peut m’appeler\u00a0?', agent: 'Bien sûr. Choisissez un moment et un conseiller vous appellera, votre dossier en main.',
      tags: ['transfert', 'préremplissage depuis l’état de la session'],
      spec: { form_id: 'callback', title: 'Rappel par un conseiller', entity: { type: 'quote' }, layout: 'single',
        fields: [
          { key: 'phone_number', prefill: { value: '(514) 555-0142', source: 'chat' } },
          { key: '/underwriting/callback_slot', widget: 'time_slots', label: 'Plage horaire préférée', required: true, options: [['today-pm', 'Aujourd’hui, 15\u00a0h à 17\u00a0h'], ['tom-am', 'Demain, 9\u00a0h à 12\u00a0h'], ['tom-pm', 'Demain, 13\u00a0h à 17\u00a0h'], ['fri-am', 'Vendredi, 9\u00a0h à 12\u00a0h']] }
        ],
        actions: [{ id: 'submit', label: 'Réserver l’appel', style: 'primary' }] },
      sample: { '/quote/phone_number': '(514) 555-0142', '/underwriting/callback_slot': 'tom-am' }
    },
    {
      id: 'conflict', group: 'Véhicule', title: 'Résoudre un conflit (conversation ou document)',
      said: '(téléverse une photo du NIV après avoir dit «\u00a02019\u00a0»)', agent: 'Le NIV que vous avez téléversé indique 2021, mais vous m’avez dit 2019. Quelle année est la bonne\u00a0?',
      tags: ['conflit de mémoire', 'document contre conversation'],
      spec: { form_id: 'conflict_vehicle_year', title: 'Quelle année est la bonne\u00a0?', entity: { type: 'vehicle', index: 0 }, layout: 'single',
        intro: 'Dans la conversation, vous avez écrit **2019**, mais la photo du NIV correspond à une **Toyota RAV4 2021**.', intro_tone: 'warn',
        fields: [{ key: 'vehicle_year', widget: 'radio', label: 'Année du modèle', options: [['2021', '2021 (d’après la photo du NIV, confiance de 97\u00a0%)'], ['2019', '2019 (ce que vous avez dit dans la conversation)']] }],
        actions: [{ id: 'submit', label: 'Utiliser cette année', style: 'primary' }] },
      sample: { '/vehicles/0/vehicle_year': 2021 }
    },
    {
      id: 'second-vehicle', group: 'Véhicule', title: 'Deuxième véhicule (à partir du signal des 3 conducteurs)',
      said: 'Oui, nous avons aussi une vieille Civic.', agent: 'Bien vu. J’ajoute la Civic, seulement l’essentiel.',
      tags: ['signal\u00a0: plusieurs véhicules', 'add_entity', 'compact'],
      spec: { form_id: 'vehicle_2', title: 'Deuxième véhicule', entity: { type: 'vehicle', index: 1 },
        intro: 'Signal\u00a0: **3 conducteurs et 1 véhicule**. Vous avez confirmé une deuxième voiture.', intro_tone: 'info',
        fields: [
          { widget: 'ymm', key: '/vehicles/1/vehicle_year', prefill: { value: { vehicle_make: 'Honda', vehicle_model: 'Civic' }, source: 'chat', note: '«\u00a0une vieille Civic\u00a0»' } },
          { key: 'usage_type' },
          { key: 'annual_kilometers', widget: 'slider', max: 60000 }
        ] },
      sample: { '/vehicles/1/vehicle_year': 2015, '/vehicles/1/vehicle_make': 'Honda', '/vehicles/1/vehicle_model': 'Civic', '/vehicles/1/usage_type': 'pleasure', '/vehicles/1/annual_kilometers': 8000 }
    },
    {
      id: 'review', group: 'Conseils', title: 'Révision avant le calcul du prix (résumé)',
      said: 'Laissez-moi tout revérifier d’abord.', agent: 'Voici tout ce que j’utiliserai pour votre soumission. Modifiez ce que vous voulez dans la conversation, ou obtenez votre prix.',
      tags: ['résumé', 'pastilles de provenance'],
      spec: { form_id: 'review', title: 'Révisez vos renseignements', layout: 'single',
        fields: [
          { widget: 'summary', label: 'Ce qui servira au calcul de votre prix', data: { items: [
            { label: 'Titulaire de la police', value: 'Marie Tremblay, née le 1986-03-14', source: 'form' },
            { label: 'Adresse', value: '125 Rue des Érables, Laval, QC H7N 1A1', source: 'lookup' },
            { label: 'Véhicule', value: '2021 Toyota RAV4 (VUS)', source: 'form' },
            { label: 'Utilisation', value: 'Aller au travail ou à l’école, 18\u00a0000\u00a0km par année', source: 'chat' },
            { label: 'Conducteurs', value: 'Marie (conductrice principale), Alex, Léo', source: 'form' },
            { label: 'Permis de conduire', value: 'T6512-…-08, 22 ans de permis', source: 'document' },
            { label: 'Réclamations / infractions', value: 'Aucune / aucune', source: 'form' }
          ] } },
          { key: '/quote/review_confirmed', widget: 'checkbox', label: 'Je confirme que ces renseignements sont exacts, au meilleur de ma connaissance.', required: true }
        ],
        actions: [{ id: 'submit', label: 'Obtenir ma soumission', style: 'primary' }] },
      sample: { '/quote/review_confirmed': true }
    },
    {
      id: 'registration', group: 'Documents', title: 'Téléversement du certificat d’immatriculation\u00a0: remplit le véhicule',
      said: 'J’ai mon certificat d’immatriculation ici.', agent: 'Téléversez-le et je remplirai les renseignements du véhicule à partir de celui-ci.',
      tags: ['agent de documents', 'remplissage automatique (année, marque, modèle, NIV)'],
      spec: { form_id: 'doc_registration', title: 'Remplir à partir de votre certificat d’immatriculation', entity: { type: 'vehicle', index: 0 }, layout: 'single',
        fields: [
          { widget: 'file', label: 'Certificat d’immatriculation', data: { purpose: 'registration', fills: { vin: '/vehicles/0/vin', vehicle_make: '/vehicles/0/vehicle_make', vehicle_model: '/vehicles/0/vehicle_model', vehicle_year: '/vehicles/0/vehicle_year' } } },
          { widget: 'ymm', key: 'vehicle_year' },
          { key: 'vin', required: false }
        ] },
      sample: { '/vehicles/0/vehicle_year': 2021, '/vehicles/0/vehicle_make': 'Toyota', '/vehicles/0/vehicle_model': 'RAV4' }
    }
  ];
  G.FORMS = FORMS;
  G.SAMPLE_OFFERS = OFFERS;
})(window);
