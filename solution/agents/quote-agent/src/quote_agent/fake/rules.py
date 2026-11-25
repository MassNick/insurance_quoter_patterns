"""French rule-based parsing shared by the fake memory and fake main clients (port of demo/src/journey.js).

Everything works on what the real models see: the compact STATE lines, the input text and the previous
assistant message. ``ASK`` holds the main agent's questions; ``topic_of`` recognizes them in the history.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

# to turn an age into a birth year
YEAR = date.today().year
# French month names; the index + 1 is the month number
MONTHS = [
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
]
# makes and models the fake recognizes (make -> models)
MAKES = {
    "Toyota": ["RAV4", "Corolla", "Camry", "Highlander", "Sienna"],
    "Honda": ["Civic", "CR-V", "Accord", "Odyssey"],
    "Ford": ["F-150", "Escape", "Mustang"],
    "Hyundai": ["Elantra", "Tucson", "Kona", "Ioniq 5"],
    "Kia": ["Forte", "Sportage", "Soul"],
    "Mazda": ["Mazda3", "CX-5", "CX-30"],
    "Subaru": ["Outback", "Forester", "Crosstrek"],
    "Tesla": ["Model 3", "Model Y"],
    "Chevrolet": ["Equinox", "Silverado", "Bolt EV"],
    "Volkswagen": ["Jetta", "Golf", "Tiguan"],
    "Nissan": ["Rogue", "Sentra", "Leaf"],
    "Lamborghini": ["Huracán", "Urus"],
}
# capitalized words that are not first names: pronouns, greetings, makes and models
NOT_NAMES = {
    "Moi",
    "Mon",
    "Ma",
    "Mes",
    "Notre",
    "Nos",
    "Oui",
    "Non",
    "Il",
    "Elle",
    "Je",
    "Et",
    "Seulement",
    "Plusieurs",
    "Une",
    "Un",
    "Le",
    "La",
    "Les",
    "Personne",
    "Aussi",
    "Bonjour",
    "Merci",
    "Desjardins",
    "Quand",
    "Pour",
    "Avec",
    "Lui",
    "Son",
    "Sa",
    "Ses",
    "Qui",
    "Quel",
    "Quelle",
    *MAKES,
    *(m for ms in MAKES.values() for m in ms),
}
# relationship words -> ``relationship_code``
REL = [
    (re.compile(r"\b(conjoint|conjointe|mari|femme|partenaire|chum|blonde|époux|épouse)\b", re.I), "spouse"),
    (re.compile(r"\b(fils|fille|enfant)\b", re.I), "child"),
    (re.compile(r"\b(père|mère|parent)\b", re.I), "parent"),
    (re.compile(r"\b(frère|sœur|soeur|ami|amie|colocataire|coloc)\b", re.I), "other"),
]
# words that mark a message as off topic
OFF_TOPIC = re.compile(r"météo|recette|blague|bitcoin|politique|hockey|horoscope")
# a message that asks a question
QUESTION = re.compile(r"\?|c['’]est quoi|qu['’]est-ce")
# answers that mean there are no more drivers
DONE_DRIVERS = re.compile(r"seulement moi|moi seulement|juste moi|^non\b|c['’]est tout|personne d['’]autre")

# the document offer, with the one-time note that the customer may answer in writing or ask for forms
DOCS_OFFER = (
    "Pour aller plus vite, avez-vous vos documents sous la main? Je peux lire votre **permis de conduire**, "
    "votre **certificat d’immatriculation** et votre **police d’assurance actuelle** pour remplir "
    "l’information à votre place.\n\nBon à savoir : vous pouvez me répondre par écrit ou me demander un "
    "formulaire, comme vous préférez. Dites-le-moi simplement en cours de route."
)
# topic → a marker the main agent's question always contains (order matters: first match wins)
ASK = {
    "consent": "puis-je les utiliser pour préparer votre soumission?",
    "docs_offer": "avez-vous vos documents sous la main?",
    "name": "Quel est votre nom complet?",
    "dob": "Quelle est votre date de naissance?",
    "email": "Quelle adresse courriel puis-je utiliser",
    "vehicle_detail": "Quels sont l’année et le modèle de votre",
    "usage": "Comment utilisez-vous la",
    "second_vehicle": "Y a-t-il un autre véhicule à assurer?",
    "second_which": "Lequel? Indiquez son année, sa marque et son modèle.",
    "vehicle": "Quel véhicule voulez-vous assurer?",
    "driver_names": "Quel est son prénom et son lien avec vous?",
    "drivers": "Qui conduit la",
    "more_drivers": "Y a-t-il d’autres conducteurs?",
    "history_chat": "avez-vous eu **une réclamation**",
    "quote_ready": "Je calcule votre soumission?",
    "kb": "Sur quelle protection?",
}
# the fake knowledge base: (regex on the question, answer text, passage titles)
KB = [
    (
        r"autres que la collision|tous risques|\bvol\b|vandalisme",
        "Les **risques autres que la collision** couvrent le vol, le vandalisme, le feu, la grêle et le bris de "
        "vitre [1].",
        ["Guide des protections auto, section 3.2"],
    ),
    (
        r"collision",
        "La **protection collision** paie la réparation de votre véhicule après un accident avec un autre "
        "véhicule ou un objet, moins votre franchise [1]. Au Québec, si l’autre conducteur est identifié, "
        "la convention d’indemnisation directe s’applique souvent [2].",
        ["Guide des protections auto, section 3.1", "Convention d’indemnisation directe"],
    ),
    (
        r"responsabilité",
        "La **responsabilité civile** paie les dommages que vous causez aux biens des autres. Au Québec, les blessures "
        "corporelles sont couvertes par la SAAQ [1].",
        ["Guide des protections auto, section 1.1"],
    ),
    (
        r"franchise",
        "La **franchise** est le montant que vous payez lors d’une réclamation avant que l’assureur paie le reste. Une "
        "franchise plus élevée réduit votre prime [1].",
        ["Guide des protections auto, section 2.4"],
    ),
    (
        r"compte comme (une )?réclamation",
        "Une **réclamation** est toute demande d’indemnisation à un assureur auto, que vous soyez responsable ou non : "
        "accident, vol, bris de vitre [1].",
        ["Guide des protections auto, section 6.1"],
    ),
    (
        r"conducteur principal|occasionnel",
        "Le **conducteur principal** est la personne qui utilise le véhicule le plus souvent. Les personnes qui le "
        "conduisent à l’occasion sont des conducteurs occasionnels [1].",
        ["Guide des protections auto, section 5.3"],
    ),
]


# ------------------------------------------------------------------ reading what the model sees
def memory_sections(text: str) -> tuple[str, str, str, str]:
    """(last assistant message, input kind, input text, STATE lines) from ``prompts.memory_input``."""
    m = re.match(r"LAST_ASSISTANT_MESSAGE\n(.*?)\n\nINPUT kind=(\w+)\n(.*)\n\nSTATE\n?(.*)$", text, re.S)
    if not m:
        msg = "unexpected memory agent input"
        raise ValueError(msg)
    last = "" if m.group(1) == "(none)" else m.group(1)
    return last, m.group(2), m.group(3), m.group(4)


def main_input(text: str) -> tuple[str, str]:
    """(kind, body) from ``prompts.main_input``."""
    m = re.match(r"INPUT kind=(\w+)\n(.*)$", text, re.S)
    return (m.group(1), m.group(2)) if m else ("message", text)


def flat_state(lines: str) -> dict[str, Any]:
    """Compact STATE lines (``/path=<json>~source/status``) → ``{path: value}``."""
    state: dict[str, Any] = {}
    for line in lines.splitlines():
        m = re.match(r"^(/\S+?)=(.*?)(?:~[a-z]+/[a-z_]*)?$", line)
        if m:
            state[m.group(1)] = json.loads(m.group(2))
    return state


def topic_of(assistant_text: str) -> str:
    """The question the assistant last asked: the ``ASK`` topic whose marker is in the text,
    ``greet`` for no text, ``other`` otherwise."""
    if not assistant_text:
        return "greet"
    return next((topic for topic, marker in ASK.items() if marker in assistant_text), "other")


def has(state: dict[str, Any], path: str) -> bool:
    """True when ``path`` holds a non-empty value."""
    return state.get(path) not in (None, "", [])


def count(state: dict[str, Any], collection: str) -> int:
    """Number of entities in ``collection`` (highest index + 1) from state paths like ``/vehicles/1/vehicle_make``."""
    idx = [int(m.group(1)) for p in state if (m := re.match(rf"^/{collection}/(\d+)/", p))]
    return max(idx) + 1 if idx else 0


def apply(state: dict[str, Any], memory: dict[str, Any]) -> dict[str, Any]:
    """The state the BFF will commit from this memory output (used to look one step ahead)."""
    out = dict(state)
    for change in memory["state_changes"]:
        if change["op"] == "set":
            out[change["path"]] = change["value"]
        elif change["op"] == "clear":
            out.pop(change["path"], None)
    return out


# ------------------------------------------------------------------ parsers
def parse_date(text: str) -> str | None:
    """An ISO date from ``YYYY-MM-DD``, ``DD/MM/YYYY`` or ``18 janvier 1987`` in ``text``; None when there is none."""
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", text):
        return m.group(0)
    if m := re.search(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b", text):
        return f"{m.group(3)}-{int(m.group(2)):02d}-{int(m.group(1)):02d}"
    if (m := re.search(r"\b(\d{1,2})(?:er)?\s+([a-zéû]+)\s+(\d{4})\b", text.lower())) and m.group(2) in MONTHS:
        return f"{m.group(3)}-{MONTHS.index(m.group(2)) + 1:02d}-{int(m.group(1)):02d}"
    return None


def parse_people(text: str) -> list[tuple[str, str, int | None]]:
    """(first name, relationship code, age) for every capitalized name near a relationship word."""
    people = []
    for m in re.finditer(r"\b([A-ZÀ-Ý][a-zà-ÿ'’-]+)\b", text):
        if m.group(1) in NOT_NAMES:
            continue
        near = [(abs(r.start() - m.start()), rel) for rx, rel in REL for r in rx.finditer(text)]
        near = [n for n in near if n[0] < 30]
        if near:
            age = re.search(r"(\d{1,2})\s*ans", text[m.start() : m.start() + 40])
            people.append((m.group(1), min(near)[1], int(age.group(1)) if age else None))
    return people


def usage_of(low: str) -> str | None:
    """The ``usage_type`` code for how the customer uses the vehicle (``low`` is lowercased text); None if unclear."""
    for pattern, code in (
        (r"ferme|agricole", "farm"),
        (r"affaires|clients|livraison", "business"),
        (r"travail|école|bureau|job", "commute"),
        (r"agrément|loisir|perso|courses|fins de semaine", "pleasure"),
    ):
        if re.search(pattern, low):
            return code
    return None


def answer_mode(low: str) -> str | None:
    """``forms`` or ``text`` when the customer says how they prefer to answer (``low`` is lowercased), else None."""
    if (
        "formulaire" in low
        and re.search(r"préf|plutôt|aime mieux|aimerais|voudrais|veux|un formulaire", low)
        and not re.search(r"pas de formulaire|sans formulaire", low)
    ):
        return "forms"
    if re.search(r"par écrit|en écrivant|par texte|en conversation|clavard|pas de formulaire|sans formulaire", low):
        return "text"
    return None


def tcase(word: str) -> str:
    """Capitalize each part of a name, keeping spaces and hyphens (``jean-luc`` becomes ``Jean-Luc``)."""
    return "".join(x[:1].upper() + x[1:].lower() if len(x) > 1 else x for x in re.split(r"([ -])", word))


def first_incomplete_vehicle(state: dict[str, Any], keys: tuple[str, ...]) -> int:
    """Index of the first vehicle missing any of ``keys``; the last vehicle (0 if none) when all are complete."""
    n = count(state, "vehicles")
    return next((i for i in range(n) if any(not has(state, f"/vehicles/{i}/{k}") for k in keys)), max(n - 1, 0))


# ------------------------------------------------------------------ the fake memory agent
def extract(text: str, state: dict[str, Any], topic: str) -> dict[str, Any]:
    """MemoryAgentOutput (contracts/memory-output.schema.json) for one customer message."""
    low = text.lower()
    changes: list[dict[str, Any]] = []
    signals: list[dict[str, Any]] = []

    def put(path: str, value: Any, confidence: float = 0.9, op: str = "set") -> None:
        """Record one state change, with the start of the message as its evidence."""
        changes.append({"path": path, "op": op, "value": value, "confidence": confidence, "evidence": text[:160]})

    if not has(state, "/quote/quote_goal") and re.search(r"soumission|assur|voiture|auto|véhicule|compar", low):
        put("/quote/quote_goal", "compare_current" if "compar" in low else "new_quote", 0.86)
    if topic == "consent" and re.search(r"\b(oui|accepte|d['’]accord)\b", low) and "pourquoi" not in low:
        put("/quote/contact_consent", True, 0.95)
    stop = re.compile(r"^(et|je|j|mais|pour|avec|de|du|des|le|la|les|mon|ma|mes|qui|ou|né|née)$", re.I)
    nm = re.search(
        r"(?:je m['’]appelle|mon nom est|moi c['’]est)\s+([A-Za-zÀ-ÿ'-]+)(?:\s+([A-Za-zÀ-ÿ'-]+))?", text, re.I
    ) or re.search(r"\b[Jj]e suis\s+([A-ZÀ-Ý][a-zà-ÿ'-]+)(?:\s+([A-ZÀ-Ý][a-zà-ÿ'-]+))?", text)
    if not nm and topic == "name" and not re.search(r"pourquoi|formulaire|écrit|question|conseiller|oui|non", low):
        nm = re.match(r"^\s*([A-Za-zÀ-ÿ'-]+)(?:\s+([A-Za-zÀ-ÿ'-]+(?:\s+[A-Za-zÀ-ÿ'-]+)?))?\s*$", text)
    if nm and not stop.match(nm.group(1)):
        put("/quote/first_name", tcase(nm.group(1)), 0.95)
        if nm.group(2) and not stop.match(nm.group(2)):
            put("/quote/last_name", tcase(nm.group(2)), 0.92)
    if m := re.search(r"[^\s@]+@[^\s@]+\.[a-z]{2,}", text, re.I):
        put("/quote/email", m.group(0), 0.98)
    if m := re.search(r"\b\d{3}[ .-]?\d{3}[ .-]?\d{4}\b", text):
        put("/quote/phone_number", m.group(0), 0.95)
    if (topic == "dob" or re.search(r"né|née|naissance", low)) and (dob := parse_date(text)):
        put("/quote/date_of_birth", dob, 0.93)

    n_veh = count(state, "vehicles")
    for make, models in MAKES.items():
        if not re.search(rf"\b{make}\b", text, re.I):
            continue
        model = next(
            (mo for mo in models if re.search(r"\b" + mo.split(" ")[0].replace("á", "[aá]"), text, re.I)), None
        )
        year = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text)
        new = topic in ("second_vehicle", "second_which") or re.search(r"aussi|deuxième|autre", low)
        i = n_veh if new or (n_veh and state.get("/vehicles/0/vehicle_make") not in (None, make)) else 0
        if i >= n_veh:
            put(f"/vehicles/{i}/vehicle_make", None, 1.0, op="add_entity")
            n_veh = i + 1
        put(f"/vehicles/{i}/vehicle_make", make, 0.9)
        if model:
            put(f"/vehicles/{i}/vehicle_model", model, 0.88)
        if year:
            put(f"/vehicles/{i}/vehicle_year", int(year.group(1)), 0.9)
    if topic in ("vehicle_detail", "usage"):
        i = first_incomplete_vehicle(state, ("vehicle_model", "vehicle_year", "usage_type"))
        if topic == "usage" and (usage := usage_of(low)):
            put(f"/vehicles/{i}/usage_type", usage, 0.85)
        if topic == "vehicle_detail" and (year := re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", text)):
            put(f"/vehicles/{i}/vehicle_year", int(year.group(1)), 0.9)

    n_drv = max(count(state, "drivers"), 1)
    known = {str(state.get(f"/drivers/{i}/driver_name", "")).split(" ")[0].lower() for i in range(1, n_drv)}
    for name, rel, age in parse_people(text):
        if name.lower() in known:
            continue
        put(f"/drivers/{n_drv}/driver_name", None, 1.0, op="add_entity")
        put(f"/drivers/{n_drv}/driver_name", name, 0.9)
        put(f"/drivers/{n_drv}/relationship_code", rel, 0.85)
        if age:
            put(f"/drivers/{n_drv}/driver_date_of_birth", f"{YEAR - age}-01-01", 0.5)
        n_drv += 1
    if (
        (m := re.search(r"(\d)\s+conducteurs?", low))
        and int(m.group(1)) > 1
        and re.search(r"une voiture|un véhicule|une auto|un char|une seule", low)
    ):
        signals.append(
            {
                "code": "multi_vehicle_likely",
                "confidence": 0.7,
                "related_paths": ["/vehicles"],
                "message": f"{m.group(1)} conducteurs pour un seul véhicule : peut-être un deuxième véhicule.",
                "suggested_follow_up": "Demander s’il y a un autre véhicule à assurer.",
            }
        )
    if topic == "history_chat" and re.match(r"^\s*(non|aucun)", low):
        for key in ("has_claims", "has_convictions_in_past_three_years", "was_denied_or_cancelled"):
            put(f"/underwriting/{key}", False, 0.9)

    goal = has(state, "/quote/quote_goal") or any(c["path"] == "/quote/quote_goal" for c in changes)
    consent = has(state, "/quote/contact_consent") or any(c["path"] == "/quote/contact_consent" for c in changes)
    mode = answer_mode(low)
    return {
        "state_changes": changes,
        "signals": signals,
        "conflicts": [],
        "intent": {"phase_hint": "gather" if goal and consent else "intent", "off_topic": bool(OFF_TOPIC.search(low))},
        "session_preferences": {"answer_mode": mode} if mode else None,
    }


# ------------------------------------------------------------------ the fake knowledge base
def kb_search(query: str) -> list[dict[str, str]]:
    """Passages of the first fake knowledge base entry that matches the question: one per title, only the
    first with text. An empty list when nothing matches."""
    low = query.lower()
    for i, (pattern, content, titles) in enumerate(KB):
        if re.search(pattern, low):
            return [
                {
                    "id": f"kb_{i}_{n + 1}",
                    "title": title,
                    "url": f"kb://guide-protections-auto/{i}/{n + 1}",
                    "content": content if n == 0 else "",
                }
                for n, title in enumerate(titles)
            ]
    return []


def kb_match(text: str) -> bool:
    """True when ``text`` is something the fake knowledge base can answer."""
    return any(re.search(pattern, text.lower()) for pattern, _, _ in KB)
