"""The shared helpers: catalog, contracts, JSON Pointer, SSE, session_ref, the usability rule and log redaction."""

import pytest

from quote_common import envelope, pointer, session_ref
from quote_common.catalog import get_catalog
from quote_common.contracts import load_schema, load_tools

pytestmark = pytest.mark.unit


def test_catalog_loads_and_knows_deterministic_fields():
    cat = get_catalog()
    assert len(cat.fields) >= 68
    assert cat.deterministic_keys == {
        "contact_consent", "has_claims", "has_convictions_in_past_three_years", "was_denied_or_cancelled", "driver_license_number",
    }
    assert cat.label("vehicle_year", "fr") == "Année du véhicule"
    assert cat.resolve_path("vehicle_year", {"type": "vehicle", "index": 2}) == "/vehicles/2/vehicle_year"
    assert cat.resolve_path("first_name") == "/quote/first_name"
    with pytest.raises(KeyError):
        cat.resolve_path("no_such_key")


def test_contracts_load():
    assert load_tools()["tools"]
    assert load_schema("memory-output.schema.json")["title"] == "MemoryAgentOutput"


def test_pointer_roundtrip():
    doc: dict = {}
    pointer.set(doc, "/vehicles/1/vehicle_make", "Honda")
    assert doc == {"vehicles": [None, {"vehicle_make": "Honda"}]}
    assert pointer.get(doc, "/vehicles/1/vehicle_make") == "Honda"
    assert pointer.get(doc, "/vehicles/5/x", "d") == "d"
    assert pointer.provenance_path("/quote/first_name") == "/_provenance/~1quote~1first_name"
    pointer.delete(doc, "/vehicles/1/vehicle_make")
    assert doc["vehicles"][1] == {}


def test_sse_roundtrip():
    e = envelope.Envelope(type="text.delta", session_id="s_1", turn=2, seq=3, actor="main_agent", data={"delta": "Bonjour"}, form_spec={"x": 1})
    raw = envelope.sse_encode(e).decode()
    assert raw.startswith("data: ") and raw.endswith("\n\n")
    (back,) = envelope.sse_decode(": keepalive\n\n" + raw)
    assert back["data"]["delta"] == "Bonjour"
    assert "form_spec" not in back


def test_session_ref_sign_verify_and_expiry():
    key = b"dev-only-test-key"
    tok = session_ref.sign("s_42", key, ttl_seconds=60, now=1000)
    assert session_ref.verify(tok, key, now=1010) == "s_42"
    with pytest.raises(session_ref.InvalidSessionRef):
        session_ref.verify(tok, key, now=2000)
    with pytest.raises(session_ref.InvalidSessionRef):
        session_ref.verify(tok, b"other-key", now=1010)
    with pytest.raises(session_ref.InvalidSessionRef):
        session_ref.verify("garbage", key)


def test_is_usable_follows_merge_rule_3():
    from quote_common.state import is_usable

    assert is_usable({"value": 2021, "status": "confirmed"}, "vehicle_year")
    assert is_usable({"value": 2021, "status": "inferred", "confidence": 0.9}, "vehicle_year")
    assert not is_usable({"value": 2021, "status": "inferred", "confidence": 0.5}, "vehicle_year")
    assert not is_usable({"value": 2021, "status": "inferred"}, "vehicle_year")
    assert not is_usable({"value": False, "status": "inferred", "confidence": 0.95}, "has_claims")
    assert not is_usable({"value": False, "status": "pending_confirmation"}, "has_claims")
    assert not is_usable({"value": "", "status": "confirmed"}, "email")
    assert not is_usable(None, "email")
    assert not is_usable({"value": [], "status": "confirmed"}, "driver_vehicle_assignments")


def test_json_logs_redact_pii(capsys):
    import json as _json
    import logging

    from quote_common import logs

    logs.setup("INFO")
    logging.getLogger("t").info("mail marie@exemple.com tel 514 555-0142", extra={"vin": "2T3W1RFV5MW104812", "n": 3})
    line = _json.loads(capsys.readouterr().err.strip().splitlines()[-1])
    assert "marie@" not in line["msg"] and "555-0142" not in line["msg"]
    assert line["vin"] == "[redacted]" and line["n"] == 3 and line["level"] == "INFO"
    assert line["ts"].endswith("Z") and "T" in line["ts"]
