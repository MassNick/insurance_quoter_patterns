"""The French rule parsers shared by the fake memory and fake main clients."""

import pytest
from jsonschema import Draft202012Validator
from quote_common import load_schema

from quote_agent.fake import rules as R

pytestmark = pytest.mark.unit
# every fake memory output must satisfy the contract
VALIDATOR = Draft202012Validator(load_schema("memory-output.schema.json"))


def changes(text, state=None, topic="other"):
    """Extract from ``text`` (the output is checked against the contract); return the ``set`` changes by path
    and the whole output."""
    out = R.extract(text, state or {}, topic)
    VALIDATOR.validate(out)  # every fake memory output is schema-valid
    return {c["path"]: c["value"] for c in out["state_changes"] if c["op"] == "set"}, out


def test_consent_only_on_the_consent_question():
    assert changes("Oui, j’accepte", topic="consent")[0] == {"/quote/contact_consent": True}
    assert "/quote/contact_consent" not in changes("Oui, j’accepte", topic="docs_offer")[0]
    assert changes("Pourquoi? oui", topic="consent")[0] == {}


def test_name_email_phone_and_date_of_birth():
    assert changes("Marie Tremblay", topic="name")[0] == {"/quote/first_name": "Marie", "/quote/last_name": "Tremblay"}
    assert changes("je m'appelle jean-luc picard")[0]["/quote/first_name"] == "Jean-Luc"
    found = changes("Écrivez-moi à marie@exemple.com ou au 514 555-0142")[0]
    assert found == {"/quote/email": "marie@exemple.com", "/quote/phone_number": "514 555-0142"}
    assert changes("Je suis née le 18 janvier 1987")[0]["/quote/date_of_birth"] == "1987-01-18"
    assert changes("18/01/1987", topic="dob")[0]["/quote/date_of_birth"] == "1987-01-18"


def test_vehicles_including_lamborghini_and_second_vehicle():
    found, out = changes("Une Toyota RAV4 2021")
    assert found == {
        "/vehicles/0/vehicle_make": "Toyota",
        "/vehicles/0/vehicle_model": "RAV4",
        "/vehicles/0/vehicle_year": 2021,
    }
    assert out["state_changes"][0]["op"] == "add_entity"
    state = {"/vehicles/0/vehicle_make": "Toyota"}
    found, _ = changes("J’ai aussi une Lamborghini Huracán 2022", state)
    assert found["/vehicles/1/vehicle_make"] == "Lamborghini"
    assert found["/vehicles/1/vehicle_model"] == "Huracán"


def test_usage_on_the_usage_question():
    state = {
        "/vehicles/0/vehicle_make": "Honda",
        "/vehicles/0/vehicle_model": "Civic",
        "/vehicles/0/vehicle_year": 2019,
    }
    assert changes("Pour aller au travail", state, topic="usage")[0] == {"/vehicles/0/usage_type": "commute"}


def test_people_with_relationship_and_age():
    found, _ = changes("Moi et ma conjointe Julie, et mon fils Léo, 17 ans", {"/quote/first_name": "Marie"})
    assert found["/drivers/1/driver_name"] == "Julie" and found["/drivers/1/relationship_code"] == "spouse"
    assert found["/drivers/2/driver_name"] == "Léo" and found["/drivers/2/relationship_code"] == "child"
    assert found["/drivers/2/driver_date_of_birth"] == f"{R.YEAR - 17}-01-01"


def test_answer_mode_preference():
    assert changes("Je préfère répondre par écrit")[1]["session_preferences"] == {"answer_mode": "text"}
    assert changes("J’aimerais mieux des formulaires")[1]["session_preferences"] == {"answer_mode": "forms"}
    assert changes("D’accord")[1]["session_preferences"] is None


def test_history_answered_in_chat_and_off_topic_and_signal():
    found, _ = changes("Non, aucun", topic="history_chat")
    assert found == {
        "/underwriting/has_claims": False,
        "/underwriting/has_convictions_in_past_three_years": False,
        "/underwriting/was_denied_or_cancelled": False,
    }
    assert changes("Quelle est la météo demain?")[1]["intent"]["off_topic"] is True
    signals = changes("Nous avons 3 conducteurs mais une seule voiture")[1]["signals"]
    assert [s["code"] for s in signals] == ["multi_vehicle_likely"]


def test_state_and_input_round_trip_and_topics():
    assert R.flat_state('/quote/first_name="Marie"~chat/inferred\n/vehicles/1/vehicle_year=2021') == {
        "/quote/first_name": "Marie",
        "/vehicles/1/vehicle_year": 2021,
    }
    assert R.count({"/vehicles/1/vehicle_year": 2021}, "vehicles") == 2
    assert R.topic_of("") == "greet"
    assert R.topic_of("Merci! " + R.DOCS_OFFER) == "docs_offer"
    assert R.main_input("INPUT kind=message\nBonjour") == ("message", "Bonjour")


def test_knowledge_base():
    passages = R.kb_search("C’est quoi la franchise?")
    assert passages[0]["title"] == "Guide des protections auto, section 2.4"
    assert R.kb_match("la collision") and not R.kb_match("bonjour")
    assert R.kb_search("le prix de l’essence") == []
