"""FormSpecs the BFF builds itself (full_form, end_of_form, edit mode), surface bindings, and the string table.

No model call builds any of this: the specs come from the catalog and the committed state, and mcp-a2ui compiles
them (architecture §5 g-h, §7.4). Customer-facing strings are fr-CA, with English for ``en`` sessions.
"""

from __future__ import annotations

import re
from typing import Any

from quote_bff.rules.end_of_form import get_value
from quote_bff.state import CATALOG, DERIVED

A2UI_VERSION = "1.0"  # stamped on every A2UI message the BFF builds
FULL_FORM, END_OF_FORM = "full_form", "end_of_form"  # reserved surface ids

# Customer-facing text by language, filled with ``text(doc, key, **placeholders)``. Both languages have the same keys
# in the same order; the groups are commented in "fr" only.
STRINGS: dict[str, dict[str, str]] = {
    "fr": {
        # Greeting and its suggestions (Bff.create_session).
        "greeting": "Bonjour! Je suis votre conseiller virtuel Desjardins Assurances. Je prépare votre soumission "
                    "auto avec vous, surtout en conversation, avec de courts formulaires au besoin.\n\n"
                    "Que souhaitez-vous faire?",
        "sugg_quote": "Obtenir une soumission auto", "sugg_compare": "Comparer avec mon assureur actuel",
        "sugg_question": "J’ai une question sur les protections", "sugg_advisor": "Parler à un conseiller",
        # The rule confirmation question and its two answers (FR-RUL-3).
        "rule_question": "Juste pour être certain : vous avez bien indiqué « {value} » ({label})?",
        "rule_yes": "Oui, c’est bien ça", "rule_no": "Non, je corrige",
        # The end_of_form surface and the hand-off (FR-RUL-4, FR-RUL-5); eof_rule is appended to eof_why.
        "stop_intro": "Pour cette demande, je dois vous diriger vers un conseiller. Voici pourquoi, et un résumé "
                      "à vérifier avant l’envoi.",
        "eof_title": "Poursuivons avec un conseiller",
        "eof_intro": "La soumission en ligne s’arrête ici. Un conseiller reprend votre dossier sans que vous ayez "
                     "à tout répéter.",
        "eof_why": "**Pourquoi?** {reason}", "eof_rule": " (règle {rule_id})",
        "customer_request": "Vous avez demandé à parler à un conseiller.",
        "eof_section": "Votre résumé, à vérifier",
        "eof_summary_hint": "Modifiez-le au besoin : c’est ce que le conseiller lira en premier.",
        "eof_send": "Envoyer à un conseiller", "eof_edit": "Modifier mes réponses",
        "consent_required": "Cochez la case pour transmettre votre dossier à un conseiller.",
        "required": "Ce champ est obligatoire.",
        "handoff_done": "C’est envoyé! Votre référence : {reference}. Un conseiller vous joindra d’ici "
                        "1 jour ouvrable.",
        # Said by the BFF without the agent: save, quote blocked, off-topic redirect (FR-GRD-5), retake (FR-DOC-5).
        "saved": "Modifications enregistrées : {title}.",
        "not_ready": "Je ne peux pas calculer de soumission en ligne pour le moment. Un conseiller peut "
                     "poursuivre avec vous.",
        "off_topic": "Je peux seulement vous aider avec votre soumission d’assurance auto. On continue?",
        "sugg_continue": "Continuer ma soumission",
        "retake": "Je n’arrive pas à lire ce document. Pouvez-vous reprendre la photo?",
        # The reason of the double confirmation, and the text of an ``error`` event.
        "confirm_reason": "Cette réponse est protégée : confirmez-la en deux étapes.",
        "try_again": "Réessayez dans un moment.",
        # The consent asked before the full form opens (FR-INT-2), and its two suggestions.
        "consent_first": "Avant d’afficher le formulaire complet, j’ai besoin de votre consentement à l’utilisation de "
                         "vos renseignements pour cette soumission.",
        "sugg_consent_yes": "Oui, j’accepte", "sugg_consent_why": "Pourquoi en avez-vous besoin?",
        # Edit-mode buttons and the full form (D20): s_* are its sections, vehicle_n and driver_n number the rows.
        "save": "Enregistrer", "cancel": "Annuler", "back": "Retour à la conversation",
        "full_title": "Votre soumission complète", "full_save": "Enregistrer et continuer",
        "s_you": "Vous", "s_vehicles": "Véhicules", "s_drivers": "Conducteurs",
        "s_history": "Antécédents et options", "s_assign": "Qui conduit quoi",
        "vehicle_n": "Véhicule {n}", "driver_n": "Conducteur {n}", "holder": "titulaire", "you": "Vous",
        # The lines of the hand-off summary (handoff_summary).
        "sum_goal": "{name} souhaite une soumission d’assurance auto.", "sum_client": "Le client",
        "sum_vehicles": "Véhicules : {items}.", "sum_drivers": "Conducteurs : {items}.",
        "sum_notes": "À noter : {items}", "sum_consent_yes": "Consentement : confirmé en deux étapes.",
        "sum_consent_no": "Consentement : non donné.",
    },
    "en": {
        # Same keys, in the same order, as "fr".
        "greeting": "Hello! I am your Desjardins Insurance virtual advisor. I prepare your car quote with you, "
                    "mostly in conversation, with short forms when needed.\n\nWhat would you like to do?",
        "sugg_quote": "Get a car quote", "sugg_compare": "Compare with my current insurer",
        "sugg_question": "I have a coverage question", "sugg_advisor": "Talk to an advisor",
        "rule_question": "Just to be sure: you said “{value}” ({label})?",
        "rule_yes": "Yes, that’s right", "rule_no": "No, let me correct it",
        "stop_intro": "For this request, I need to direct you to an advisor. Here is why, and a summary to check "
                      "before sending.",
        "eof_title": "Let’s continue with an advisor",
        "eof_intro": "The online quote ends here. An advisor picks up your file without you repeating anything.",
        "eof_why": "**Why?** {reason}", "eof_rule": " (rule {rule_id})",
        "customer_request": "You asked to talk to an advisor.",
        "eof_section": "Your summary, to check",
        "eof_summary_hint": "Edit it if needed: it is what the advisor reads first.",
        "eof_send": "Send to an advisor", "eof_edit": "Edit my answers",
        "consent_required": "Check the box to send your file to an advisor.",
        "required": "This field is required.",
        "handoff_done": "Sent! Your reference: {reference}. An advisor will contact you within 1 business day.",
        "saved": "Changes saved: {title}.",
        "not_ready": "I can’t calculate an online quote right now. An advisor can continue with you.",
        "off_topic": "I can only help with your car insurance quote. Shall we continue?",
        "sugg_continue": "Continue my quote",
        "retake": "I can’t read this document. Could you retake the photo?",
        "confirm_reason": "This answer is protected: confirm it in two steps.",
        "try_again": "Try again in a moment.",
        "consent_first": "Before I show the complete form, I need your consent to use your information for this quote.",
        "sugg_consent_yes": "Yes, I agree", "sugg_consent_why": "Why do you need it?",
        "save": "Save", "cancel": "Cancel", "back": "Back to the conversation",
        "full_title": "Your complete quote", "full_save": "Save and continue",
        "s_you": "You", "s_vehicles": "Vehicles", "s_drivers": "Drivers",
        "s_history": "History and options", "s_assign": "Who drives what",
        "vehicle_n": "Vehicle {n}", "driver_n": "Driver {n}", "holder": "policyholder", "you": "You",
        "sum_goal": "{name} wants a car insurance quote.", "sum_client": "The customer",
        "sum_vehicles": "Vehicles: {items}.", "sum_drivers": "Drivers: {items}.",
        "sum_notes": "Note: {items}", "sum_consent_yes": "Consent: confirmed in two steps.",
        "sum_consent_no": "Consent: not given.",
    },
}


def lang(doc: dict[str, Any]) -> str:
    """The session language, ``en`` or ``fr``: any locale that does not start with ``en`` is French."""
    return "en" if str(doc.get("locale", "fr")).lower().startswith("en") else "fr"


def text(doc: dict[str, Any], key: str, **kw: Any) -> str:
    """The string ``key`` in the session language, with ``kw`` filled into its placeholders."""
    return STRINGS[lang(doc)][key].format(**kw)


def a2ui(kind: str, **body: Any) -> dict[str, Any]:
    """One A2UI 1.0 message, ``{version, <kind>: body}`` (createSurface, updateDataModel, deleteSurface...)."""
    return {"version": A2UI_VERSION, kind: body}


def label_of(doc: dict[str, Any], path: str, value: Any) -> tuple[str, str]:
    """(field label, value label) in the session language, enum codes turned into their labels."""
    key = path.rsplit("/", 1)[-1]
    labels = dict(CATALOG.enum_labels(key, lang(doc)) or [])
    return CATALOG.label(key, lang(doc)), str(labels.get(str(value), value))


# ---------------------------------------------------------------------------------------------------------------
# Surface bindings
# ---------------------------------------------------------------------------------------------------------------


def bound_paths(message: dict[str, Any]) -> list[str]:
    """State pointers a compiled createSurface binds. Repeater rows become ``/drivers/*/key`` patterns.

    DocumentDrop ``fills`` count too; ``/_...`` paths (errors, provenance) do not. A submit keeps only these paths.
    """
    comps = {c.get("id"): c for c in (message.get("createSurface") or {}).get("components", [])}
    out: set[str] = set()
    templated: set[str] = set()
    for c in comps.values():
        if c.get("component") == "Repeater":
            for tid in c.get("template", []):
                templated.add(tid)
                rel = ((comps.get(tid) or {}).get("value") or {}).get("@path")
                if rel:
                    out.add(f"{c.get('collection')}/*/{rel}")
        if c.get("component") == "DocumentDrop":
            out.update(p for d in c.get("documents", []) for p in (d.get("fills") or {}).values())
    for cid, c in comps.items():
        if cid in templated:
            continue
        for v in c.values():
            ptr = v.get("@path") if isinstance(v, dict) else None
            if isinstance(ptr, str) and ptr.startswith("/") and not ptr.startswith("/_"):
                out.add(ptr)
    return sorted(out)


def is_bound(path: str, bound: list[str]) -> bool:
    """True when ``path`` is one of the ``bound`` pointers; a ``*`` in a pattern stands for one row index."""
    return any(p == path or ("*" in p and re.fullmatch(re.escape(p).replace(r"\*", r"\d+"), path)) for p in bound)


def values_from_context(context: Any) -> dict[str, Any]:
    """The bound values of an A2UI action: ``context.values`` (pointer → value), else pointer keys of ``context``.

    Only state pointers are kept: keys that are not ``/...`` or that start with ``/_`` are dropped.
    """
    ctx = context if isinstance(context, dict) else {}
    values = ctx["values"] if isinstance(ctx.get("values"), dict) else ctx
    return {k: v for k, v in values.items() if isinstance(k, str) and k.startswith("/") and not k.startswith("/_")}


# ---------------------------------------------------------------------------------------------------------------
# FormSpecs
# ---------------------------------------------------------------------------------------------------------------


def edit_spec(spec: dict[str, Any], doc: dict[str, Any]) -> dict[str, Any]:
    """A stored FormSpec for reopening: no prefill (mcp-a2ui fills from the committed state), Save and Cancel.

    FR-A2UI-15, D16. Returns a copy; the stored spec is not changed.
    """

    def strip(fields: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Drop ``prefill`` from every field, repeater rows included.

        DocumentDrop fields get the committed values of their ``fills`` in ``data.values``.
        """
        out = []
        for f in fields:
            f = {k: v for k, v in f.items() if k != "prefill"}
            if isinstance(f.get("repeat"), dict):
                f["repeat"] = {**f["repeat"], "fields": strip(f["repeat"].get("fields", []))}
            if f.get("widget") == "document_drop":  # documents already read show their committed values
                fills = [p for d in (f.get("data") or {}).get("documents", []) for p in (d.get("fills") or {}).values()]
                f["data"] = {**(f.get("data") or {}),
                             "values": {p: get_value(doc, p) for p in fills if get_value(doc, p) is not None}}
            out.append(f)
        return out

    return {**spec, "fields": strip(spec.get("fields", [])), "actions": [
        {"id": "cancel", "label": text(doc, "cancel"), "style": "secondary"},
        {"id": "submit", "label": text(doc, "save"), "style": "primary"}]}


def vehicle_label(doc: dict[str, Any], i: int) -> str:
    """Short name of vehicle ``i``: year, make and model, else the numbered default ("Véhicule 2")."""
    parts = [get_value(doc, f"/vehicles/{i}/{k}") for k in ("vehicle_year", "vehicle_make", "vehicle_model")]
    return " ".join(str(p) for p in parts if p) or text(doc, "vehicle_n", n=i + 1)


def driver_label(doc: dict[str, Any], i: int) -> str:
    """Name of driver ``i``, else the numbered default ("Conducteur 2")."""
    return get_value(doc, f"/drivers/{i}/driver_name") or text(doc, "driver_n", n=i + 1)


def _entity_keys(entity: str, skip: set[str]) -> list[str]:
    """The catalog keys of ``entity`` a form may ask for: no system flags, no lists, nothing in ``skip``."""
    return [k for k, f in CATALOG.fields.items()
            if f["entity"] == entity and not f.get("system") and f["type"] != "list" and k not in skip]


def full_form_spec(doc: dict[str, Any]) -> dict[str, Any]:
    """D20 (FR-A2UI-16): the whole quote as one form, from the catalog.

    One section per entity, repeaters for vehicles and drivers, the AssignmentBoard for the household; system flags
    left out, derived fields read-only. Every field is optional: the customer fills what they know.
    """
    opt = {"required": False}
    quote_skip = {"person_name", "handoff_summary", "handoff_consent", "city", "province_code", "postal_code"}
    vehicle_skip = {"vehicle_make_model", "night_parking_label"}
    fields: list[dict[str, Any]] = [{"widget": "section", "label": text(doc, "s_you")}]
    for k in _entity_keys("quote", quote_skip):
        fields.append({"key": f"/quote/{k}", **opt, **({"widget": "address_lookup"} if k == "address_line1" else {})})

    def repeater(coll: str, entity: str, skip: set[str], item: str, limit: int) -> dict[str, Any]:
        """A repeater over the ``coll`` entities, one row per known entity.

        At least one row and at most ``limit``; derived fields are read-only.
        """
        rows = [{"key": k, **opt, **({"readonly": True} if k in DERIVED else {})} for k in _entity_keys(entity, skip)]
        return {"key": f"/{coll}", "widget": "repeater", "label": text(doc, f"s_{coll}"),
                "repeat": {"count": max(1, len(doc[coll])), "min": 0, "max": limit, "item_label": item,
                           "fields": rows}}

    fields += [{"widget": "section", "label": text(doc, "s_vehicles")},
               repeater("vehicles", "vehicle", vehicle_skip, text(doc, "vehicle_n", n="{n}"), 25),
               {"widget": "section", "label": text(doc, "s_drivers")},
               repeater("drivers", "driver", set(), text(doc, "driver_n", n="{n}"), 10),
               {"widget": "section", "label": text(doc, "s_history")}]
    fields += [{"key": f"/underwriting/{k}", **opt} for k in _entity_keys("underwriting", set())]
    board = {"drivers": [{"index": i, "label": driver_label(doc, i), "sub": text(doc, "you") if i == 0 else ""}
                         for i in range(len(doc["drivers"]))],
             "vehicles": [{"index": i, "label": vehicle_label(doc, i)} for i in range(len(doc["vehicles"]))]}
    fields += [{"widget": "section", "label": text(doc, "s_assign")},
               {"key": "/household/driver_vehicle_assignments", "widget": "assignment_board", **opt, "data": board}]
    return {"form_id": FULL_FORM, "title": text(doc, "full_title"), "layout": "two_column",
            "entity": {"type": "quote"}, "fields": fields,
            "actions": [{"id": "back", "label": text(doc, "back"), "style": "secondary"},
                        {"id": "submit", "label": text(doc, "full_save"), "style": "primary"}]}


def handoff_summary(doc: dict[str, Any]) -> str:
    """The editable hand-off summary: a template over committed state and advisor signals (no model call).

    FR-RUL-4, FR-RUL-6. The customer can edit it before sending; it is what the advisor reads first.
    """
    name = get_value(doc, "/drivers/0/driver_name") or text(doc, "sum_client")
    lines = [text(doc, "sum_goal", name=name)]
    if doc["vehicles"]:
        lines.append(text(doc, "sum_vehicles", items="; ".join(vehicle_label(doc, i)
                                                               for i in range(len(doc["vehicles"])))))
    if doc["drivers"]:
        people = []
        for i in range(len(doc["drivers"])):
            rel = get_value(doc, f"/drivers/{i}/relationship_code")
            role = text(doc, "holder") if i == 0 else label_of(doc, "/drivers/0/relationship_code", rel)[1].lower()
            people.append(f"{driver_label(doc, i)} ({role})")
        lines.append(text(doc, "sum_drivers", items=", ".join(people)))
    if doc["advisor_signals"]:
        lines.append(text(doc, "sum_notes", items=" ".join(s["reason"] for s in doc["advisor_signals"])))
    consent = get_value(doc, "/quote/contact_consent") is True
    lines.append(text(doc, "sum_consent_yes" if consent else "sum_consent_no"))
    return "\n".join(lines)


def end_of_form_spec(doc: dict[str, Any], reason: str, rule_id: str | None) -> dict[str, Any]:
    """The end_of_form FormSpec: the reason, contact fields, an editable summary and the consent box.

    FR-RUL-4, FR-RUL-5. The reason carries the rule id; ``rule_id`` is None when the customer asked for an advisor.
    The first name, last name and phone are required, and so is the consent box; mcp-a2ui prefills the contact fields
    from the state.
    """
    why = text(doc, "eof_why", reason=reason) + (text(doc, "eof_rule", rule_id=rule_id) if rule_id else "")
    return {
        "form_id": END_OF_FORM, "title": text(doc, "eof_title"), "intro": text(doc, "eof_intro"),
        "entity": {"type": "quote"}, "layout": "two_column",
        "actions": [{"id": "edit_answers", "label": text(doc, "eof_edit"), "style": "secondary"},
                    {"id": "send", "label": text(doc, "eof_send"), "style": "primary"}],
        "fields": [
            {"widget": "info", "tone": "warn", "hint": why},
            {"widget": "section", "label": text(doc, "eof_section")},
            {"key": "first_name", "required": True}, {"key": "last_name", "required": True},
            {"key": "phone_number", "required": True}, {"key": "email", "required": False},
            {"key": "handoff_summary", "widget": "textarea", "hint": text(doc, "eof_summary_hint"),
             "prefill": {"value": handoff_summary(doc)}},
            {"key": "handoff_consent", "widget": "checkbox", "required": True},
        ],
    }
