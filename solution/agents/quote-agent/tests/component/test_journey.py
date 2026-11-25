"""The whole French journey with the fake model, as the e2e suite runs it (the BFF's commits simulated)."""

import pytest
from conftest import of_type, post_turn
from quote_common import pointer

pytestmark = [pytest.mark.component, pytest.mark.anyio]


class Session:
    """Just enough of the BFF: commit memory.result and form values into the state the agent receives."""

    def __init__(self, http):
        """Start with an empty state, the ``intent`` phase and no answer-mode preference."""
        self.http, self.turn, self.phase, self.state = http, 0, "intent", {}
        self.preferences = {"answer_mode": None}
        self.calls: list[int] = []

    def commit(self, path, value, source="form"):
        """Write one confirmed value into the state, as the BFF does when it commits."""
        pointer.set(self.state, path, {"value": value, "source": source, "status": "confirmed"})

    async def send(self, **inp):
        """Send one input as the next turn, then do what the BFF would: apply the memory changes, the phase and
        the preferences. Returns the events and the answer text."""
        self.turn += 1
        events = await post_turn(
            self.http,
            inp,
            turn=self.turn,
            session="s_journey",
            state=self.state,
            phase=self.phase,
            preferences=self.preferences,
        )
        for memory in of_type(events, "memory.result"):
            if memory["data"].get("session_preferences"):
                self.preferences = memory["data"]["session_preferences"]
            for change in memory["data"].get("state_changes", []):
                if change["op"] == "set":
                    self.commit(change["path"], change["value"], "chat")
        for phase in of_type(events, "phase"):
            self.phase = phase["data"]["phase"]
        self.calls.append(events[-1]["data"]["model_calls"])
        done = of_type(events, "text.done")
        return events, done[0]["data"]["text"] if done else ""

    async def say(self, text):
        """Send a customer message."""
        return await self.send(kind="message", text=text)

    async def submit(self, surface_id, values):
        """Commit the form ``values`` and send the ``form_submitted`` input."""
        for path, value in values.items():
            self.commit(path, value)
        return await self.send(kind="form_submitted", surface_id=surface_id, changed_paths=list(values))


def surface(events):
    """The id of the surface the turn created, or None."""
    created = [e for e in of_type(events, "a2ui.message") if "createSurface" in e["data"]]
    return created[0]["data"]["createSurface"]["surfaceId"] if created else None


def labels(events):
    """The labels of every suggested reply in the events."""
    return [i["label"] for e in of_type(events, "suggestions") for i in e["data"]["items"]]


async def test_full_journey_from_greeting_to_offers(http):
    s = Session(http)
    _, text = await s.say("Bonjour, je veux une soumission auto")
    assert "puis-je les utiliser" in text
    _, text = await s.say("Oui, j’accepte")
    assert "fenêtre de confirmation" in text
    events, text = await s.send(kind="confirmation_result", accepted=True, changed_paths=["/quote/contact_consent"])
    assert "avez-vous vos documents sous la main?" in text and "par écrit ou me demander un formulaire" in text
    assert labels(events) == ["Oui, j’ai mes documents", "J’en ai seulement quelques-uns", "Je ne les ai pas"]
    _, text = await s.say("Je ne les ai pas")
    assert text.endswith("Quel est votre nom complet?")
    events, text = await s.say("Marie Tremblay")
    assert surface(events) == "a_propos" and "Enchanté, Marie!" in text
    _, text = await s.submit("a_propos", {"/quote/date_of_birth": "1987-01-18"})
    assert "adresse courriel" in text and "par écrit" not in text  # the one-time note is not repeated
    _, text = await s.say("marie.tremblay@exemple.com")
    assert "Quel véhicule voulez-vous assurer?" in text
    events, _ = await s.say("Une Toyota RAV4 2021")
    assert surface(events) == "vehicule_0"
    assert of_type(events, "a2ui.message")[0]["form_spec"]["fields"][0]["prefill"]["value"]["vehicle_model"] == "RAV4"
    _, text = await s.submit("vehicule_0", {"/vehicles/0/usage_type": "commute"})
    assert text.endswith("Qui conduit la RAV4?")
    _, text = await s.say("Moi et ma conjointe Julie, 45 ans")
    assert "j’ai ajouté Julie" in text
    _, text = await s.say("Non, c’est tout")
    assert "Y a-t-il un autre véhicule à assurer?" in text
    events, _ = await s.say("Non, un seul véhicule")
    assert surface(events) == "qui_conduit_quoi"
    assignment = [
        {"driver_index": 0, "vehicle_index": 0, "role": "principal"},
        {"driver_index": 1, "vehicle_index": 0, "role": "occasional"},
    ]
    events, _ = await s.submit("qui_conduit_quoi", {"/household/driver_vehicle_assignments": assignment})
    assert surface(events) == "antecedents"
    events, text = await s.submit(
        "antecedents",
        {
            f"/underwriting/{k}": False
            for k in ("has_claims", "has_convictions_in_past_three_years", "was_denied_or_cancelled")
        },
    )
    assert "Je calcule votre soumission?" in text
    assert of_type(events, "suggestions")[0]["data"]["items"][0] == {
        "label": "Obtenir ma soumission",
        "kind": "action",
        "action_id": "get_quote",
    }
    events, text = await s.send(kind="action", action_id="get_quote")
    assert of_type(events, "quote.created") and surface(events) == "offers_q_1_v1"
    assert "Je vous recommande **Plus**" in text and s.phase == "advise"
    assert s.calls == [2] * len(s.calls)  # every turn: memory + main


async def test_text_mode_preference_asks_in_chat(http):
    s = Session(http)
    await s.say("Bonjour, je veux une soumission auto")
    await s.say("Oui")
    await s.send(kind="confirmation_result", accepted=True, changed_paths=["/quote/contact_consent"])
    await s.say("Je ne les ai pas")
    events, text = await s.say("Je préfère répondre par écrit, pas de formulaire")
    memory = of_type(events, "memory.result")[0]["data"]
    assert memory["session_preferences"] == {"answer_mode": "text"}
    assert text.startswith("C’est noté : on continue par écrit.") and text.endswith("Quel est votre nom complet?")
    events, text = await s.say("Marie Tremblay")
    assert surface(events) is None and text == "Merci, Marie. Quelle est votre date de naissance?"
    _, text = await s.say("Le 18 janvier 1987")
    assert s.state["quote"]["date_of_birth"]["value"] == "1987-01-18" and "adresse courriel" in text


async def test_lamborghini_is_extracted_for_the_bff_rules(http):
    s = Session(http)
    events, _ = await s.say("Je veux assurer ma Lamborghini Urus 2023")
    changes = {c["path"]: c["value"] for c in of_type(events, "memory.result")[0]["data"]["state_changes"]}
    assert changes["/vehicles/0/vehicle_make"] == "Lamborghini" and changes["/vehicles/0/vehicle_model"] == "Urus"
    stop = [
        {"rule_id": "VEHICULE_EXOTIQUE", "severity": "stop", "paths": ["/vehicles/0/vehicle_make"], "inferred": False}
    ]
    events = await post_turn(
        http, {"kind": "message", "text": "Et maintenant?"}, turn=2, session="s_journey", rule_hits=stop
    )
    assert not of_type(events, "a2ui.message")
    assert "conseiller" in of_type(events, "text.done")[0]["data"]["text"]
