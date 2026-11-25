# Style guide: Desjardins Assurances quote assistant (Variation C)

This guide is the reference for the customer UI in `web/`. It is also the rubric the visual-review agent uses in CI (`docs/cicd.md`). The approved prototype is `../../demo/journey.html` (design folder).

> **Brand placeholder.** The hexagon mark in the UI is a hand-drawn approximation of the Desjardins logo. Replace it with the official asset from the Desjardins brand team before any external showing (NFR-BRD-1). Keep it in its single module so the swap is one change.

## 1. Principles

1. **Conversation first.** The customer can do everything by writing. Forms are short, about one subject each, and appear only when a subject has several structured fields or the customer asks for them.
2. **Never ask the customer to choose text or forms.** Say once that both work; honour what they say.
3. **Nothing is lost.** Every form leaves a chip in the conversation that reopens it. The dossier panel shows everything known, with its source.
4. **Protected answers are confirmed twice.** Consent, claims, convictions, refusal or cancellation, licence number.
5. **When the online quote cannot continue, say why and hand over cleanly.** The end-of-form view gives the reason, an editable summary, and a consent box.

## 2. Tokens

Use the CSS custom properties; never hard-code a color in a component.

| Token | Value | Use |
|---|---|---|
| `--g` | `#00874E` | Primary green: primary buttons, selected tiles, user bubbles, links, focus of brand elements |
| `--g2` | `#006B3F` | Primary hover |
| `--g3` | `#0B5A3A` | Dark header, status bar, header buttons' text when inverted |
| `--m1` … `--m4` | `#CDE7DA`, `#E3F1EA`, `#EEF6F2`, `#F6FBF8` | Mints: autofill flash, chips, panel groups, page gradient, bot surfaces |
| `--ink`, `--ink-2`, `--ink-3` | `#2B2B2B`, `#4F4F4F`, `#6B6B6B` | Text, secondary text, hints |
| `--line`, `--line-2`, `--line-3` | `#8F8F8F`, `#D6D6D6`, `#E8E8E8` | Input borders, card borders, dividers |
| `--warn`, `--warn-soft` | `#9A4D00`, `#FFF3E0` | Protected-field badge, end-of-form reason, "À confirmer" |
| `--danger`, `--danger-soft` | `#B3261E`, `#FCE8E6` | Validation errors, required marker |
| `--r-card`, `--r-ctl`, `--r-bubble` | 14 px, 10 px, 18 px | Cards, inputs, chat bubbles |
| Buttons, chips, suggestions | radius 999 px | Pills |
| Focus | `3px solid #1A73E8`, offset 2 px | Every focusable element |

Contrast: body text on white and on `--m3` meets WCAG AA (4.5:1); `--ink-3` is only for hints at 12.5 px or larger.

## 3. Typography

- Font stack `Roboto, "Segoe UI", -apple-system, …`; body 15 px / 1.5.
- Headings are **light (300) and green** (`--g`): form titles 28 px (21 px on phones), welcome 30 px, full-form title 34 px.
- Section headings inside forms: 13 px, weight 700, uppercase, letter-spacing .06em, green, with a 1 px `--line-3` underline.
- Labels 14 px weight 700; hints 12.5 px italic `--ink-2`; errors 12.5 px weight 600 `--danger`.
- French typography: typographic apostrophe `’`, non-breaking space before `: ; ? ! %` and inside `« »`, amounts as `1 420 $`, dates as `18 janvier 1987`.

## 4. Layout

### Desktop (Variation C "Assistant plein écran")
- Demo bar (development only), then a **64 px dark-green header** (`--g3`): logo, quote number, the pill toggle **Conversation | Formulaire complet**, and **Parler à un conseiller**.
- **Step chips** under the header: Documents, Vous, Véhicules, Conducteurs, Antécédents, Soumission (current = filled green, done = mint).
- A **72 px left rail** opens the dossier panel; its counter shows how many values are known.
- The conversation is centered (max 720 px) when no form is open. When a form opens, the conversation slides left to about 36 % of the width (340–440 px) and the form takes the rest.

### Phone (≤ 600 px)
- 56 px header; the mode toggle shows icons only; the advisor button is a round icon.
- The rail becomes a **bottom tab bar** (58 px + safe area).
- A form **covers the conversation**; the conversation slides left and fades; the **suggestions and composer stay usable** below the form. A reply that arrives while a form is open shows as a **peek** at the top of the form for 6.5 s.
- The dossier panel and the confirmation modal rise from the bottom as **sheets**.
- Inputs use 16 px text (no zoom on focus); tiles use 2 columns; every grid field spans 12 columns except paired short fields.

## 5. The form grid (A2UI)

Every A2UI form sits on a **12-column grid** (column gap 16 px, row gap 20 px; 12 px / 16 px on phones). A field spans 12 columns; a `Row` splits into 6 + 6. Year/make/model sit on a 3-column inner grid. Never place a field outside the grid or give it a fixed pixel width.

Form card: white, radius 14 px, shadow `--shadow-card`; header padding 22 × 32 px; footer with a top divider, the primary button on the right (pill, green), secondary buttons as gray pills, "Démo : remplir" only in demos.

## 6. Components

| Component | Rules |
|---|---|
| **Bot avatar** | Hexagon in `--g` with two eyes. States: blink every 5 s; `think` (pupils move) while waiting; `look-r` when the ball leaves for a form; `look-l` + `happy` when it returns; a ring pulse on return. |
| **Chat bubbles** | Bot: white, radius 4/18/18/18 px, soft shadow. Customer: green with white text, radius 18/4/18/18 px. System notes: centered dashed pill. Max width 82 % (86 % on phones). |
| **Typing dots** | Three green dots bouncing, shown within 150 ms of send; replaced by the first streamed text. A green caret blinks while streaming. |
| **Suggestions** | Pills above the composer, white with a soft shadow, hover turns the border and text green. Generic only (no names, emails, models or values the customer has not given), at most 4, at most 40 characters. One row with horizontal scroll on phones. |
| **Form chip** | "Formulaire : <titre>" under the bot message that opened the form. States: `ouvert` (pulsing dot, disabled), `Voir ou modifier` with a check (submitted), `Rouvrir` (closed), `envoyé` (hand-off sent, disabled). After a hand-off is sent, all chips lock. |
| **Tiles (choices)** | 52 px min height, radius 12 px, selected = green fill with white text. Auto-fill columns of at least 150 px. |
| **Provenance badge** | Under a field: green dot + "Tiré de la conversation", "Lu sur votre document" or "Trouvé par recherche". |
| **Protected badge** | Next to the label: lock + "confirmé deux fois", warn colors. |
| **Help** | A green info icon after the label opens a dark tooltip with the catalog's `help_fr`. The dossier rows use the same tooltip. |
| **Autofill flash** | A value written by the conversation or a document flashes mint for 1.6 s. |
| **DocumentDrop** | Three tiles (licence, registration, current policy), each with "Téléverser une photo" and "Je ne l’ai pas"; a scanning animation while the document agent reads; review rows (label, editable value, confidence pill, amber below 90 %). |
| **AssignmentBoard** | A mint pool of people (avatar initials) above vehicle cards; each card has a "Conducteur principal" slot (exactly one, mint background) and a "Conducteurs occasionnels" slot. Dragging tilts the ghost (−3°, 1.06×); slots highlight when a person is over them; dropping springs the slot. Tap a person then a slot as the alternative; Enter/Space selects with the keyboard. On phones the pool stays sticky and the form auto-scrolls near the edges. |
| **Confirmation modal** | Two steps with a progress bar: "Confirmez cette réponse" (old → new value) then "Confirmez-vous?" with a required checkbox. |
| **End-of-form view** | Amber reason note with the rule id, the customer's contact fields, the editable summary (textarea), a required consent checkbox, "Modifier mes réponses" and "Envoyer à un conseiller". Success shows a green check, the reference `CNS-######` and the callback promise. |
| **Dossier panel** | Tabs "Renseignements" and "Soumissions"; a completion meter; "Ce que j’ai remarqué" signals (amber for advisor flags); groups Votre demande, Vous, Véhicules, Conducteurs, Assurance actuelle, Antécédents; each row shows the value and its state ("À confirmer", "Confirmation en attente", source, "Confirmé deux fois"). |

## 7. Motion

| Motion | Spec |
|---|---|
| Form open | The conversation slides (0.5 s, `cubic-bezier(.2,.8,.2,1)`); a 14 px green ball pops at the bot avatar (0.22 s), arcs to the form center (0.52 s), expands to an 88 px circle then to the card (0.42 s, `cubic-bezier(.2,.9,.25,1)`); fields fade up with a 45 ms stagger. |
| Form close | The reverse: the card shrinks to a ball, arcs back to the avatar, which turns happy and pulses. |
| Dossier panel | Spring in (0.62 s, overshoot 3 %) from the rail (desktop) or the bottom (phone), then items fade up with a 70 ms stagger. |
| Suggestions and chips | Pop in with a small overshoot (`cubic-bezier(.34,1.56,.64,1)`), 60 ms stagger. |
| Reduced motion | With `prefers-reduced-motion: reduce`, every animation is cut to ~0 ms and the form simply appears. |

## 8. Copy

- fr-CA, vouvoiement, short sentences, plain words. The agent introduces itself as "votre conseiller virtuel Desjardins Assurances".
- Field labels are complete words, never codes or underscores (from `label_fr`).
- Buttons start with a verb: "Continuer", "Enregistrer", "Envoyer à un conseiller", "Obtenir ma soumission".
- Errors say what to do: "Entrez une adresse courriel valide".

## 9. Accessibility

Keyboard reachable everything (including the board), visible focus, labels bound to inputs, `aria-live` for the transcript and board messages, dialogs with `role="dialog"`/`alertdialog`, 44 px minimum touch targets, AA contrast, reduced-motion support.

## 10. Visual regression and review

Each visual element above has a baseline screenshot in `web/tests/visual/__screenshots__/` (desktop and 390 px where relevant). A change that moves a baseline needs a human approval in the PR; the visual-review agent comments on brand tokens, grid alignment, French copy, contrast and phone layout using this guide.
