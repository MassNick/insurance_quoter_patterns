"""Deterministic chat clients with the same layers as FoundryChatClient (function invocation, chat middleware,
telemetry), so the real orchestration, middleware and tool loop run unchanged with QUOTE_AGENT_MODEL=fake.

The main policy follows demo/src/journey.js ``decide()`` / ``nextStep()`` closely enough for the e2e journey.
"""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Sequence
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatMiddlewareLayer,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    FunctionInvocationLayer,
    Message,
)
from agent_framework.observability import ChatTelemetryLayer
from quote_common import get_catalog

from quote_agent.fake import rules as R
from quote_agent.tools import logical_name

# suggested replies by situation (generic: no personal data)
SUGG = {
    "greet": [
        "Obtenir une soumission auto",
        "Comparer avec mon assureur actuel",
        "J’ai une question sur les protections",
        "Parler à un conseiller",
    ],
    "consent": ["Oui, j’accepte", "Pourquoi en avez-vous besoin?", "Non, pas maintenant"],
    "docs": ["Oui, j’ai mes documents", "J’en ai seulement quelques-uns", "Je ne les ai pas"],
    "form": ["J’ai une question", "Parler à un conseiller"],
    "vehicle": ["Je ne connais pas le modèle exact", "J’ai plus d’un véhicule"],
    "drivers": ["Seulement moi", "Moi et une autre personne", "Plusieurs personnes"],
    "more_drivers": ["Non, c’est tout", "Oui, une autre personne"],
    "kb": ["La collision", "Les risques autres que la collision", "La responsabilité civile", "La franchise"],
    "after": ["Qu’est-ce que la franchise?", "Parler à un conseiller"],
}
# the suggestion that sends the ``get_quote`` button action
GET_QUOTE = {"label": "Obtenir ma soumission", "kind": "action", "action_id": "get_quote"}
# the fixed questions below contain the marker phrase of their topic in rules.ASK, so ``topic_of`` finds them
CONSENT = (
    "Avec plaisir.\n\nAvant de recueillir vos renseignements : **puis-je les utiliser pour préparer votre "
    "soumission?** Je vous demanderai de confirmer à l’écran."
)
ASK_VEHICLE = "Quel véhicule voulez-vous assurer? Indiquez simplement l’année, la marque et le modèle."
HISTORY = (
    "Dernières questions. Au cours des dernières années, avez-vous eu **une réclamation** (6 ans), "
    "**une infraction** au Code de la sécurité routière (3 ans) ou **un refus ou une résiliation** "
    "d’assurance (3 ans)?"
)
# the documents the drop zone offers: purpose, icon, title and the state paths each one fills
DOCS = [
    {
        "purpose": "licence",
        "icon": "idcard",
        "title": "Permis de conduire",
        "description": "Nom, date de naissance et numéro de permis.",
        "fills": {
            "first_name": "/quote/first_name",
            "last_name": "/quote/last_name",
            "date_of_birth": "/quote/date_of_birth",
            "driver_license_number": "/drivers/0/driver_license_number",
        },
    },
    {
        "purpose": "registration",
        "icon": "regdoc",
        "title": "Certificat d’immatriculation",
        "description": "Année, marque, modèle et numéro d’identification du véhicule.",
        "fills": {
            "vehicle_year": "/vehicles/0/vehicle_year",
            "vehicle_make": "/vehicles/0/vehicle_make",
            "vehicle_model": "/vehicles/0/vehicle_model",
            "vin": "/vehicles/0/vin",
        },
    },
    {
        "purpose": "declaration",
        "icon": "policy",
        "title": "Police d’assurance actuelle",
        "description": "Ancienneté chez votre assureur et conducteurs inscrits.",
        "fills": {"years_with_current_insurer": "/drivers/0/years_with_current_insurer"},
    },
]
# the three underwriting questions (``/underwriting/<key>``)
UNDERWRITING = ("has_claims", "has_convictions_in_past_three_years", "was_denied_or_cancelled")


class _FakeClient(FunctionInvocationLayer, ChatMiddlewareLayer, ChatTelemetryLayer, BaseChatClient):
    """Base of the fake chat clients: the same layers as ``FoundryChatClient``, so a subclass only writes ``reply``."""

    OTEL_PROVIDER_NAME = "fake"  # provider name in the GenAI telemetry spans

    def reply(self, messages: list[Message], options: dict[str, Any]) -> list[Content]:
        """The contents (text and tool calls) of the next completion for ``messages``."""
        raise NotImplementedError

    def _inner_get_response(self, *, messages: Sequence[Message], stream: bool, options: Any, **kwargs: Any) -> Any:
        """Run ``reply`` and return it as a response, or as a stream of updates when ``stream`` is set.

        Token usage is estimated at 4 characters per token; cached input is always 0.
        """
        contents = self.reply(list(messages), dict(options))
        out_chars = sum(len(c.text or "") + len(str(c.arguments or "")) for c in contents)
        in_chars = sum(len(m.text) for m in messages) + len(options.get("instructions") or "")
        usage = {
            "input_token_count": in_chars // 4,
            "output_token_count": out_chars // 4,
            "cache_read_input_token_count": 0,
        }
        updates = [ChatResponseUpdate(role="assistant", contents=[c]) for c in contents]
        full = "".join(c.text for c in contents if c.type == "text" and c.text)
        if stream and full:  # like the Responses API's output_text.done: the stream repeats the whole text (replace)
            done = Content.from_text(text=full, merge_mode="replace")
            updates.append(ChatResponseUpdate(role="assistant", contents=[done]))
        updates.append(ChatResponseUpdate(role="assistant", contents=[Content.from_usage(usage)]))
        if stream:

            async def gen() -> AsyncIterator[ChatResponseUpdate]:
                """Yield the prepared updates one by one."""
                for update in updates:
                    yield update

            return self._build_response_stream(gen())

        async def respond() -> ChatResponse:
            """Return all the updates as one response."""
            return ChatResponse.from_updates(updates)

        return respond()


class FakeMemoryClient(_FakeClient):
    """Fake memory agent: the rules' extraction of the last message as MemoryAgentOutput JSON
    (nothing is extracted from a non-message input)."""

    def reply(self, messages: list[Message], options: dict[str, Any]) -> list[Content]:
        """The extraction for the last message as MemoryAgentOutput JSON (its prompt sections are parsed back out)."""
        last, kind, text, state = R.memory_sections(messages[-1].text)
        flat = R.flat_state(state)
        output = R.extract(text if kind == "message" else "", flat, R.topic_of(last))
        return [Content.from_text(json.dumps(output, ensure_ascii=False))]


class FakeMainClient(_FakeClient):
    """The main agent's fake model: decides the next step of the journey the way a model reading the context
    would (``_Main``), then answers with text and tool calls."""

    def reply(self, messages: list[Message], options: dict[str, Any]) -> list[Content]:
        """The plan's text in chunks, then its tool calls (a phase change and the suggested replies are added
        when due), named as the registered tools spell them."""
        names = {logical_name(t.name): t.name for t in options.get("tools") or [] if hasattr(t, "name")}
        plan = _Main(messages).decide()
        contents = [Content.from_text(chunk) for chunk in re.findall(r"\S+\s*", plan.text)] if plan.text else []
        calls = list(plan.calls)
        if plan.phase and plan.phase != plan.current_phase:
            calls.append(("ui.set_phase", {"phase": plan.phase}))
        if plan.suggestions is not None:
            items = [s if isinstance(s, dict) else {"label": s, "kind": "reply"} for s in plan.suggestions]
            calls.append(("ui.suggest_replies", {"items": items}))
        for n, (name, args) in enumerate(calls):
            contents.append(
                Content.from_function_call(
                    call_id=f"fake_{len(messages)}_{n}",
                    name=names[name],
                    arguments=json.dumps(args, ensure_ascii=False),
                )
            )
        return contents


class _Plan:
    """One main completion: what to say, which tools to call, the phase to set and the suggested replies."""

    def __init__(
        self,
        text: str = "",
        suggestions: list[Any] | None = None,
        calls: Sequence[tuple[str, Any]] = (),
        phase: str | None = None,
        current_phase: str = "",
    ) -> None:
        """``suggestions``: strings or action dicts (None = no ``ui.suggest_replies`` call). ``calls``: (logical
        tool name, arguments). ``phase`` is set when it differs from ``current_phase``."""
        self.text, self.suggestions, self.calls = text, suggestions, list(calls)
        self.phase, self.current_phase = phase, current_phase


def _result_text(content: Content) -> str:
    """The text of a tool result, whatever its shape (string, list of contents or JSON value)."""
    result = content.result
    if isinstance(result, list):
        return "".join(getattr(c, "text", "") or "" for c in result)
    return result if isinstance(result, str) else json.dumps(result)


class _Main:
    """One main completion: read the context the way a model would, then pick the next step."""

    def __init__(self, messages: list[Message]) -> None:
        """Read the messages the way a model does and make the extraction the memory agent would make.

        ``lines``: the context lines by keyword (PHASE, PREFERENCES, RULES, ...). ``state``: STATE as
        {path: value}. ``kind``, ``text`` and ``details``: the INPUT. ``pre``: the PRE_EXECUTED quote result.
        ``last`` and ``topic``: the assistant's last message and the question it asked. ``memory``: the
        extraction for this message. ``S``: the state once that is committed. ``P``: the answer mode
        (``forms``, ``text`` or None).
        """
        self.messages = messages
        idx = next(
            i
            for i in range(len(messages) - 1, -1, -1)
            if messages[i].role == "system" and messages[i].text.startswith("STATE")
        )
        context = messages[idx].text
        self.history = messages[:idx]
        self.lines = dict(line.split(" ", 1) for line in context.splitlines() if re.match(r"^[A-Z_]+ ", line))
        self.state = R.flat_state(context)
        self.current_phase = self.lines.get("PHASE", "")
        self.phase = "gather"
        user = next(m for m in reversed(messages) if m.role == "user")
        self.kind, body = R.main_input(user.text)
        self.text = body if self.kind == "message" else ""
        self.details = json.loads(body.split("\n")[1]) if self.kind != "message" and "\n" in body else {}
        pre = re.search(r"\nPRE_EXECUTED \S+\n(.*)$", body, re.S)
        self.pre = json.loads(pre.group(1)) if pre else None
        self.last = next((m.text for m in reversed(self.history) if m.role == "assistant" and m.text), "")
        self.topic = R.topic_of(self.last)
        self.memory = R.extract(self.text, self.state, self.topic)
        self.S = R.apply(self.state, self.memory)
        prefs = json.loads(self.lines.get("PREFERENCES", "{}"))
        self.P = (self.memory["session_preferences"] or {}).get("answer_mode") or prefs.get("answer_mode")

    # ---------------------------------------------------------------- helpers
    def say(self, text: str, suggestions: list[Any] | None = None, calls: Sequence[tuple[str, Any]] = ()) -> _Plan:
        """A plan that says ``text`` with the suggested replies and tool ``calls``, in the phase decided so far."""
        return _Plan(text, suggestions or [], calls, self.phase, self.current_phase)

    def form(self, spec: dict[str, Any], text: str, suggestions: list[Any] | None = None) -> _Plan:
        """A plan that says ``text`` and renders the FormSpec ``spec`` (generic suggestions unless given)."""
        return self.say(text, suggestions or SUGG["form"], [("a2ui.render_form", {"form_spec": spec})])

    def asked(self, topic: str) -> bool:
        """True when the assistant already asked the ``topic`` question earlier in the conversation."""
        return any(m.role == "assistant" and R.topic_of(m.text) == topic for m in self.history if m.text)

    def answers(self) -> list[tuple[str, str]]:
        """(topic of the assistant question, customer answer) pairs, including this turn."""
        pairs, last = [], ""
        for m in self.history:
            if m.role == "assistant" and m.text:
                last = m.text
            elif m.role == "user":
                kind, body = R.main_input(m.text)
                if kind == "message":
                    pairs.append((R.topic_of(last), body.lower()))
        return [*pairs, (self.topic, self.text.lower())]

    def drivers_done(self) -> bool:
        """True when the customer has said there are no more drivers."""
        return any(
            t in ("drivers", "more_drivers", "driver_names") and R.DONE_DRIVERS.search(a) for t, a in self.answers()
        )

    def quoted(self) -> bool:
        """True when a quote was already created in this conversation (a tool call or a pre-executed result)."""
        return any(
            c.type == "function_call" and logical_name(c.name or "") == "quoting.create_quote"
            for m in self.history
            for c in m.contents
        ) or any("PRE_EXECUTED quoting.create_quote" in m.text for m in self.history if m.role == "user")

    def has(self, path: str) -> bool:
        """True when ``path`` has a value in the state as this turn will leave it."""
        return R.has(self.S, path)

    def vshort(self, i: int) -> str:
        """Short name of vehicle ``i``: its model, else its make, else ``véhicule``."""
        return self.S.get(f"/vehicles/{i}/vehicle_model") or self.S.get(f"/vehicles/{i}/vehicle_make") or "véhicule"

    def vlabel(self, i: int) -> str:
        """Label of vehicle ``i``: year, make and model, or a placeholder when nothing is known."""
        parts = [self.S.get(f"/vehicles/{i}/{k}") for k in ("vehicle_year", "vehicle_make", "vehicle_model")]
        return " ".join(str(p) for p in parts if p) or "Véhicule à préciser"

    def driver(self, i: int) -> str:
        """First name of driver ``i`` (``Vous`` for the unnamed customer, a numbered label for another driver)."""
        return (
            (self.S.get("/quote/first_name") or "Vous")
            if i == 0
            else (self.S.get(f"/drivers/{i}/driver_name") or f"Conducteur {i + 1}")
        )

    def changed(self, suffix: str) -> list[dict[str, Any]]:
        """This turn's ``set`` changes whose path ends with ``suffix``."""
        return [c for c in self.memory["state_changes"] if c["op"] == "set" and c["path"].endswith(suffix)]

    @staticmethod
    def label(key: str, code: Any) -> str:
        """The French label of ``code`` for the catalog field ``key`` (empty when unknown)."""
        return next((lab for c, lab in get_catalog().enum_labels(key) or [] if c == code), "")

    # ---------------------------------------------------------------- forms (FormSpec, contracts/form-spec.schema.json)
    def about(self, name: bool, email: bool) -> dict[str, Any]:
        """FormSpec ``a_propos``: the name (when ``name``), the date of birth and the email (when ``email``)."""
        fields = [{"key": "/quote/first_name"}, {"key": "/quote/last_name"}] if name else []
        fields.append({"key": "/quote/date_of_birth", "hint": "Sert à établir votre expérience de conduite."})
        if email:
            fields.append({"key": "/quote/email", "hint": "Nous vous y enverrons votre soumission."})
        return {
            "form_id": "a_propos",
            "title": "À propos de vous" if name else "Deux détails sur vous",
            "layout": "two_column",
            "entity": {"type": "quote"},
            "fields": fields,
        }

    def vehicle(self, i: int) -> dict[str, Any]:
        """FormSpec ``vehicule_<i>``: year-make-model and usage, prefilled with what the customer already said."""
        known = {
            k: self.S[f"/vehicles/{i}/{k}"]
            for k in ("vehicle_year", "vehicle_make", "vehicle_model")
            if self.has(f"/vehicles/{i}/{k}")
        }
        ymm: dict[str, Any] = {"key": f"/vehicles/{i}/vehicle_year", "widget": "ymm"}
        if known:
            ymm["prefill"] = {"value": known, "source": "chat", "note": f"Vous avez dit « {self.vlabel(i)} »"}
        spec = {
            "form_id": f"vehicule_{i}",
            "title": "Votre deuxième véhicule" if i else "Votre véhicule",
            "layout": "single",
            "entity": {"type": "vehicle", "index": i},
            "fields": [ymm, {"key": f"/vehicles/{i}/usage_type", "widget": "chips"}],
        }
        if known:
            spec["intro"] = "Vérifiez ce que j’ai compris et ajoutez son utilisation."
        return spec

    def assignment(self) -> dict[str, Any]:
        """FormSpec ``qui_conduit_quoi``: the board that assigns each driver to the vehicles they drive."""
        drivers = [
            {
                "index": i,
                "label": self.driver(i),
                "sub": "Vous"
                if i == 0
                else self.label("relationship_code", self.S.get(f"/drivers/{i}/relationship_code")) or "Conducteur",
            }
            for i in range(max(R.count(self.S, "drivers"), 1))
        ]
        vehicles = [
            {
                "index": i,
                "label": self.vlabel(i),
                "short": self.vshort(i),
                "sub": self.label("usage_type", self.S.get(f"/vehicles/{i}/usage_type")) or "Utilisation à préciser",
            }
            for i in range(R.count(self.S, "vehicles"))
        ]
        return {
            "form_id": "qui_conduit_quoi",
            "title": "Qui conduit quoi?",
            "layout": "single",
            "intro": "Glissez chaque personne vers le véhicule qu’elle conduit. Un conducteur principal par "
            "véhicule; les autres sont occasionnels.",
            "actions": [{"id": "submit", "label": "Confirmer", "style": "primary"}],
            "fields": [
                {
                    "key": "/household/driver_vehicle_assignments",
                    "widget": "assignment_board",
                    "label": "Conducteurs et véhicules",
                    "data": {"drivers": drivers, "vehicles": vehicles},
                }
            ],
        }

    @staticmethod
    def history_form() -> dict[str, Any]:
        """FormSpec ``antecedents``: the three underwriting yes/no questions."""
        return {
            "form_id": "antecedents",
            "title": "Vos antécédents",
            "layout": "single",
            "intro": "Trois questions. Ces réponses sont protégées : vous les confirmez avant qu’on les utilise.",
            "entity": {"type": "underwriting"},
            "fields": [{"key": f"/underwriting/{k}", "widget": "toggle"} for k in UNDERWRITING],
        }

    @staticmethod
    def documents() -> dict[str, Any]:
        """FormSpec ``documents``: the drop zone for the licence, the registration and the current policy."""
        return {
            "form_id": "documents",
            "title": "Vos documents",
            "layout": "single",
            "intro": "Notre agent de documents lit chaque photo. Vous vérifiez ce qu’il a trouvé avant qu’on "
            "l’utilise.",
            "actions": [
                {"id": "nodocs", "label": "Continuer sans documents", "style": "secondary"},
                {"id": "submit", "label": "Utiliser ces informations", "style": "primary"},
            ],
            "fields": [
                {"widget": "document_drop", "label": "Téléversez ceux que vous avez", "data": {"documents": DOCS}}
            ],
        }

    def offers(self, quote: dict[str, Any]) -> _Plan:
        """The plan after a quote result: the offers card with a recommendation, or a request for the missing
        fields (back to the ``gather`` phase) when the quote was refused."""
        self.phase = "advise"
        if quote.get("missing_fields") or not quote.get("quote_id"):
            self.phase = "gather"
            missing = [m.get("label") or m.get("key", "") for m in quote.get("missing_fields") or []]
            return self.say(
                "Il me manque encore quelques renseignements avant de calculer votre soumission"
                + (f" : {', '.join(missing)}." if missing else "."),
                ["Continuer ma soumission"],
            )
        n = R.count(self.S, "vehicles")
        what = f"vos {n} véhicules" if n > 1 else f"votre {self.vshort(0)}"
        return self.say(
            f"Voici vos {len(quote.get('offers', []))} options pour {what}. Je vous recommande **Plus**, "
            "qui inclut l’assistance routière et une voiture de location.",
            SUGG["after"],
            [("a2ui.render_offers", {"quote_id": quote["quote_id"], "highlight": "plus", "show_adjust": True})],
        )

    # ---------------------------------------------------------------- the journey
    def next_step(self, prefix: str = "") -> _Plan:
        """The next question or form of the journey, from what is still unknown: documents, name, birth date, email,
        vehicles, drivers, assignments, history, then the quote. ``prefix`` is a short acknowledgement put before it."""
        P, first = self.P, self.S.get("/quote/first_name")
        if not self.asked("docs_offer"):
            return self.say(prefix + R.DOCS_OFFER, SUGG["docs"])
        if not first:
            if P == "forms":
                return self.form(
                    self.about(True, not self.has("/quote/email")),
                    prefix + "Voici un court formulaire pour faire connaissance.",
                )
            return self.say(
                prefix + "Commençons par vous. Quel est votre nom complet?", ["Pourquoi avez-vous besoin de mon nom?"]
            )
        if not self.has("/quote/date_of_birth"):
            if P == "text":
                return self.say(
                    prefix + f"Merci, {first}. Quelle est votre date de naissance?", ["Pourquoi ma date de naissance?"]
                )
            return self.form(
                self.about(False, P == "forms" and not self.has("/quote/email")),
                prefix + f"Enchanté, {first}! Deux détails rapides.",
            )
        if not self.has("/quote/email"):
            if P == "forms":
                return self.form(self.about(False, True), prefix + "Il me manque votre courriel.")
            return self.say(
                prefix + "Quelle adresse courriel puis-je utiliser pour vous envoyer votre soumission?",
                ["Pourquoi avez-vous besoin de mon courriel?"],
            )
        nv = R.count(self.S, "vehicles")
        if not nv:
            if P == "forms":
                return self.form(self.vehicle(0), prefix + "Choisissez le véhicule à assurer.")
            return self.say(prefix + ASK_VEHICLE, SUGG["vehicle"])
        keys = ("vehicle_make", "vehicle_model", "vehicle_year", "usage_type")
        vi = next((i for i in range(nv) if any(not self.has(f"/vehicles/{i}/{k}") for k in keys)), None)
        if vi is not None:
            make = self.S.get(f"/vehicles/{vi}/vehicle_make")
            full = all(self.has(f"/vehicles/{vi}/{k}") for k in keys[:3])
            if P == "text" and full:
                usages = [lab for _, lab in get_catalog().enum_labels("usage_type") or []]
                return self.say(prefix + f"Comment utilisez-vous la {self.vshort(vi)}?", usages[:4])
            if P == "text":
                return (
                    self.say(
                        prefix + f"Quels sont l’année et le modèle de votre {make}?",
                        ["Je ne connais pas le modèle exact"],
                    )
                    if make
                    else self.say(prefix + ASK_VEHICLE, SUGG["vehicle"])
                )
            return self.form(
                self.vehicle(vi),
                prefix
                + (
                    "Voici ce que j’ai compris. Vérifiez-le et indiquez son utilisation."
                    if make
                    else "Choisissez le véhicule dans la liste."
                ),
            )
        nd = max(R.count(self.S, "drivers"), 1)
        if not self.drivers_done():
            if nd > 1:
                names = [self.driver(i) for i in range(nd)]
                return self.say(
                    prefix + f"J’ai noté {', '.join(names[:-1])} et {names[-1]} comme conducteurs. "
                    "Y a-t-il d’autres conducteurs?",
                    SUGG["more_drivers"],
                )
            return self.say(prefix + f"Qui conduit la {self.vshort(0)}?", SUGG["drivers"])
        if nd > nv and not self.asked("second_vehicle"):
            return self.say(
                prefix + f"Vous êtes **{nd} conducteurs** pour un seul véhicule. Y a-t-il un autre "
                "véhicule à assurer? C’est souvent moins cher ensemble.",
                ["Oui, j’ai un autre véhicule", "Non, un seul véhicule"],
            )
        if (nd > 1 or nv > 1) and not self.has("/household/driver_vehicle_assignments"):
            return self.form(
                self.assignment(),
                prefix + "Dernière étape pour les véhicules : **glissez chaque "
                "personne** vers le véhicule qu’elle conduit.",
                ["Qu’est-ce qu’un conducteur principal?"],
            )
        if any(not self.has(f"/underwriting/{k}") for k in UNDERWRITING):
            if P == "text":
                return self.say(
                    prefix + HISTORY,
                    ["Non, aucun des trois", "Oui, au moins un", "Qu’est-ce qui compte comme une réclamation?"],
                )
            return self.form(
                self.history_form(),
                prefix + "Presque fini! Trois questions sur vos antécédents.",
                ["Qu’est-ce qui compte comme une réclamation?"],
            )
        if not self.quoted():
            return self.say(
                prefix + "J’ai tout ce qu’il faut. Je calcule votre soumission?", [GET_QUOTE, "J’ai une question"]
            )
        self.phase = "advise"
        return self.say(prefix + "Votre soumission est à jour. Voulez-vous l’ajuster?", SUGG["after"])

    def narrate(self) -> _Plan:
        """Second completion after a narrate tool (knowledge base or a quote the model asked for)."""
        calls = {
            c.call_id: logical_name(c.name or "")
            for m in self.messages
            for c in m.contents
            if c.type == "function_call"
        }
        result = next(c for c in self.messages[-1].contents if c.type == "function_result")
        data = json.loads(_result_text(result) or "null")
        if calls.get(result.call_id) == "quoting.create_quote":
            return self.offers(data or {})
        passages = (data or {}).get("passages") or []
        if not passages:
            return self.say(
                "Je n’ai pas trouvé cette protection dans notre guide. Voulez-vous en parler à un conseiller?",
                ["Parler à un conseiller", "Continuer ma soumission"],
            )
        return self.say(passages[0]["content"], ["Continuer ma soumission", "J’ai une autre question"])

    def decide(self) -> _Plan:  # one branch per journey step, as in demo/src/journey.js decide()
        """The plan for this completion: narrate a tool result, or react to the kind of input."""
        if self.messages[-1].role == "tool":
            return self.narrate()
        if any(h.get("severity") == "stop" for h in json.loads(self.lines.get("RULES", "[]"))):
            return self.say(
                "Pour cette demande, je dois vous diriger vers un conseiller. Vérifiez le résumé avant l’envoi.",
                ["Parler à un conseiller"],
            )
        d, low, topic = self.details, self.text.lower(), self.topic
        if self.kind == "confirmation_result":
            if "/quote/contact_consent" in d.get("changed_paths", []):
                return (
                    self.next_step("Merci! ")
                    if d.get("accepted")
                    else self.say(
                        "Pas de problème. Je peux tout de même répondre à vos questions sur les protections.",
                        ["Finalement, j’accepte", "J’ai une question sur les protections"],
                    )
                )
            return self.next_step("Merci, c’est noté. " if d.get("accepted") else "D’accord, je n’ai rien changé. ")
        if self.kind == "action":
            if d.get("action_id") == "get_quote" and isinstance(self.pre, dict):
                return self.offers(self.pre)
            if d.get("action_id") == "modify_quote":
                return self.say(
                    "C’est fait : nouvelle version de votre soumission. L’historique est dans votre "
                    "dossier, onglet Soumissions.",
                    SUGG["after"],
                )
            if d.get("action_id") == "rule_correct":
                return self.say("Pas de problème. " + ASK_VEHICLE, SUGG["vehicle"])
            return self.next_step()
        if self.kind == "form_submitted":
            sid = d.get("surface_id", "")
            if sid == "documents":
                n = len(d.get("changed_paths", []))
                return self.next_step(f"Merci! J’ai rempli **{n} informations** à partir de vos documents. ")
            if sid.startswith("offres_"):
                return self.say(
                    "Excellent choix. Je vous envoie le détail par courriel. Voulez-vous l’ajuster?", SUGG["after"]
                )
            return self.next_step("Parfait. " if sid.startswith("vehicule_") else "Merci! ")
        if self.kind != "message":
            return self.next_step("Merci! ")

        if self.memory["intent"]["off_topic"]:
            return self.say(
                "Je ne peux pas vous aider avec ce sujet, mais je peux répondre à vos questions sur "
                "l’assurance auto ou poursuivre votre soumission.",
                SUGG["greet"] if topic == "greet" else ["Continuer ma soumission"],
            )
        mode = (self.memory["session_preferences"] or {}).get("answer_mode")
        if mode and topic not in ("greet", "kb", "consent"):
            return self.next_step(
                "C’est noté : je vous propose des formulaires pour la suite. "
                if mode == "forms"
                else "C’est noté : on continue par écrit. "
            )
        if R.kb_match(low) and (topic in ("kb", "greet") or R.QUESTION.search(low)):
            return _Plan(calls=[("knowledge.knowledge_base_retrieve", {"query": self.text})])
        if topic == "greet":
            if re.search(r"question|protection", low):
                return self.say("Bien sûr. Sur quelle protection?", SUGG["kb"])
            self.phase = "intent"
            return self.say(CONSENT, SUGG["consent"])
        if topic == "kb":
            return self.say(
                "Je n’ai pas trouvé cette protection dans notre guide. Voulez-vous en parler à un conseiller?",
                ["Parler à un conseiller", "Obtenir une soumission auto"],
            )
        if topic == "consent":
            self.phase = "intent"
            if "pourquoi" in low:
                return self.say(
                    "Vos réponses servent uniquement à préparer cette soumission et à communiquer avec "
                    "vous à son sujet. Les réponses protégées sont toujours confirmées deux fois. "
                    "Alors, **puis-je les utiliser pour préparer votre soumission?**",
                    SUGG["consent"],
                )
            if re.search(r"\bnon\b|pas maintenant", low):
                return self.say(
                    "Pas de problème. Je peux tout de même répondre à vos questions sur les protections.",
                    ["Finalement, j’accepte", "J’ai une question sur les protections"],
                )
            if self.changed("/contact_consent"):
                return self.say("Merci. Une fenêtre de confirmation s’ouvre.")
            return self.say(CONSENT, SUGG["consent"])
        if topic == "docs_offer":
            if re.search(r"je ne les ai pas|pas mes|^non\b|aucun|sans documents", low):
                return self.next_step("Aucun problème, on continue. ")
            if re.search(r"oui|quelques|seulement|certains|j['’]ai", low):
                return self.form(
                    self.documents(),
                    "Parfait. Téléversez vos documents; je vous montre ce que j’ai lu avant de l’utiliser.",
                    ["Je ne les ai finalement pas"],
                )
        if topic == "name":
            return (
                self.next_step()
                if self.has("/quote/first_name")
                else self.say("Je n’ai pas bien saisi votre nom. Quel est votre nom complet?")
            )
        if topic == "dob":
            return (
                self.next_step("Merci. ")
                if self.has("/quote/date_of_birth")
                else self.say(
                    "Je n’ai pas reconnu la date. Quelle est votre date de naissance? Par exemple : 18 janvier 1987."
                )
            )
        if topic == "email":
            return (
                self.next_step("Merci! ")
                if self.has("/quote/email")
                else self.say("Je n’ai pas reconnu d’adresse courriel. Quelle adresse courriel puis-je utiliser?")
            )
        nv = R.count(self.S, "vehicles")
        if topic in ("vehicle", "vehicle_detail", "usage", "second_which"):
            new = self.changed("/vehicle_make")
            if new and topic == "second_which":
                return self.next_step(f"Parfait, j’ai ajouté la **{self.vlabel(nv - 1)}**. ")
            if topic == "vehicle" and "connais pas" in low:
                return self.form(self.vehicle(max(nv - 1, 0)), "Pas de souci, choisissez-le dans la liste.")
            if topic == "vehicle" and re.search(r"plus d['’]un", low):
                return self.say(
                    "Parfait, on les ajoute un à la fois. Quel véhicule voulez-vous assurer? Commençons "
                    "par le premier.",
                    ["Je ne connais pas le modèle exact"],
                )
            if nv and (new or topic != "vehicle"):
                return self.next_step("Noté. ")
            return self.form(
                self.vehicle(nv if topic == "second_which" else 0),
                "Je n’ai pas reconnu ce véhicule. Choisissez-le dans la liste.",
            )
        if topic in ("drivers", "more_drivers", "driver_names"):
            added = self.changed("/driver_name")
            if R.DONE_DRIVERS.search(low):
                return self.next_step()
            if added:
                rel = [self.S.get(c["path"].replace("driver_name", "relationship_code")) for c in added]
                names = [
                    f"{c['value']} ({self.label('relationship_code', r).lower()})"
                    for c, r in zip(added, rel, strict=True)
                ]
                return self.say(
                    f"Merci, j’ai ajouté {', '.join(names)}. Y a-t-il d’autres conducteurs?", SUGG["more_drivers"]
                )
            if re.search(r"autre personne|plusieurs|oui", low):
                return self.say(
                    "Quel est son prénom et son lien avec vous? Indiquez aussi son âge si cette personne "
                    "a moins de 25 ans.",
                    [],
                )
            return self.say(
                "Je n’ai pas bien compris. Qui conduit la " + f"{self.vshort(0)}? Donnez-moi le prénom "
                "de chaque conducteur et son lien avec vous."
            )
        if topic == "second_vehicle":
            if re.search(r"^non|un seul", low):
                return self.next_step("D’accord, un seul véhicule. ")
            if self.changed("/vehicle_make"):
                return self.next_step(f"Parfait, j’ai ajouté la **{self.vlabel(nv - 1)}**. ")
            if re.search(r"oui|autre", low):
                if self.P == "forms":
                    return self.form(self.vehicle(nv), "Ajoutez votre deuxième véhicule.")
                return self.say(
                    "Lequel? Indiquez son année, sa marque et son modèle.", ["Je ne connais pas le modèle exact"]
                )
        if topic == "history_chat":
            if re.search(r"oui|au moins", low):
                return self.form(self.history_form(), "D’accord. Précisez dans ce court formulaire.")
            if re.search(r"^non|aucun", low):
                return self.say("Merci. Une fenêtre de confirmation s’ouvre : ces réponses sont protégées.")
        if topic == "quote_ready" and re.search(r"oui|calcul|vas-y|d['’]accord|obtenir", low):
            return _Plan(calls=[("quoting.create_quote", {})])
        if json.loads(self.lines.get("OPEN_SURFACES", "[]")):
            return self.say(
                "C’est noté, je l’ai ajouté au formulaire."
                if self.memory["state_changes"]
                else "Prenez votre temps. Vous pouvez aussi répondre ici, je remplirai le formulaire pour vous.",
                SUGG["form"],
            )
        return self.next_step("" if "continuer" in low else "Bien noté. ")
