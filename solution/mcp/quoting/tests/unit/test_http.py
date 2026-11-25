"""The HTTP helper and the real-system adapters, against httpx.MockTransport (no network)."""

import httpx
import pytest
from helpers import with_check_digit
from pydantic import ValidationError

from mcp_quoting.adapters import (
    HttpAddressService,
    HttpApi,
    HttpBffStateReader,
    HttpRatingEngine,
    HttpVehicleCatalog,
    HttpVinDecoder,
    SessionNotFound,
)

pytestmark = [pytest.mark.unit, pytest.mark.anyio]

OFFER = {
    "offer_type": "plus",
    "monthly": 99.5,
    "annual": 1194,
    "deductible": 500,
    "coverages": ["Responsabilité civile 2 M$"],
}


class Server:
    """A MockTransport that records requests and answers from a script of responses or exceptions."""

    def __init__(self, *script):
        """`script` is the answers in order, the last one repeating: a response, an exception or a JSON body."""
        self.script, self.requests = list(script), []
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        """Record the request and give the next scripted answer."""
        self.requests.append(request)
        answer = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(answer, Exception):
            raise answer
        return answer if isinstance(answer, httpx.Response) else httpx.Response(200, json=answer)

    def api(self, **kwargs):
        """An `HttpApi` on this server without backoff; `kwargs` set `retries`, `token` and so on."""
        return HttpApi("https://real.test/api", transport=self.transport, backoff=0, **kwargs)


# --------------------------------------------------------------------------- HttpApi
async def test_api_sends_to_the_base_url_and_decodes_json():
    server = Server({"ok": True})
    assert await server.api().request("GET", "/years", params={"year": 2020}) == {"ok": True}
    assert str(server.requests[0].url) == "https://real.test/api/years?year=2020"


@pytest.mark.parametrize("status", [429, 502, 503, 504])
async def test_api_retries_transient_statuses(status):
    server = Server(httpx.Response(status), httpx.Response(status), {"ok": True})
    assert await server.api().request("GET", "/x") == {"ok": True} and len(server.requests) == 3


async def test_api_retries_transport_errors_and_timeouts():
    server = Server(httpx.ConnectError("down"), httpx.ReadTimeout("slow"), {"ok": True})
    assert await server.api().request("GET", "/x") == {"ok": True} and len(server.requests) == 3


async def test_api_gives_up_after_the_configured_retries():
    server = Server(httpx.Response(503))
    with pytest.raises(httpx.HTTPStatusError):
        await server.api(retries=2).request("GET", "/x")
    assert len(server.requests) == 3
    down = Server(httpx.ConnectError("down"))
    with pytest.raises(httpx.ConnectError):
        await down.api(retries=1).request("GET", "/x")
    assert len(down.requests) == 2


async def test_api_does_not_retry_client_errors():
    server = Server(httpx.Response(400, json={"detail": "bad"}))
    with pytest.raises(httpx.HTTPStatusError) as caught:
        await server.api().request("GET", "/x")
    assert caught.value.response.status_code == 400 and len(server.requests) == 1


async def test_api_auth_hook_adds_a_bearer_token_on_every_attempt():
    tokens = iter(["t1", "t2"])

    async def token():
        """The next token of the list."""
        return next(tokens)

    server = Server(httpx.Response(503), {"ok": True})
    await server.api(token=token).request("GET", "/x")
    assert [r.headers["Authorization"] for r in server.requests] == ["Bearer t1", "Bearer t2"]


async def test_an_explicit_authorization_header_replaces_the_token():
    async def token():
        """Must not be called: an explicit `Authorization` header replaces the token."""
        raise AssertionError("not needed")

    server = Server({"ok": True})
    await server.api(token=token).request("GET", "/x", headers={"Authorization": "SessionRef abc"})
    assert server.requests[0].headers["Authorization"] == "SessionRef abc"


# --------------------------------------------------------------------------- rating
async def test_rating_posts_the_snapshot_and_validates_the_offers():
    server = Server({"offers": [OFFER]})
    offers = await HttpRatingEngine(server.api()).price({"vehicles": [{"vehicle_year": 2021}]}, ["plus"])
    assert [o.model_dump() for o in offers] == [OFFER]
    request = server.requests[0]
    assert (request.method, request.url.path) == ("POST", "/api/rate")
    assert request.read() == b'{"snapshot":{"vehicles":[{"vehicle_year":2021}]},"offer_types":["plus"]}'


async def test_rating_mapping_functions_adapt_another_wire_format():
    server = Server(
        {
            "results": [
                {"plan": "plus", "price": 99.5, "yearly": 1194, "excess": 500, "cover": ["Responsabilité civile 2 M$"]}
            ]
        }
    )
    engine = HttpRatingEngine(
        server.api(),
        to_payload=lambda snapshot, offer_types: {"plans": offer_types, "risk": snapshot["vehicles"]},
        to_offers=lambda body: [
            {
                "offer_type": r["plan"],
                "monthly": r["price"],
                "annual": r["yearly"],
                "deductible": r["excess"],
                "coverages": r["cover"],
            }
            for r in body["results"]
        ],
    )
    assert [o.model_dump() for o in await engine.price({"vehicles": []}, ("plus",))] == [OFFER]
    assert server.requests[0].read() == b'{"plans":["plus"],"risk":[]}'


async def test_rating_refuses_offers_that_break_the_contract():
    for bad in (
        {**OFFER, "offer_type": "gold"},
        {**OFFER, "monthly": "cheap"},
        {k: v for k, v in OFFER.items() if k != "coverages"},
    ):
        with pytest.raises(ValidationError):
            await HttpRatingEngine(Server({"offers": [bad]}).api()).price({}, ["plus"])


# --------------------------------------------------------------------------- vehicle catalog
async def test_catalog_requests_and_omitted_year():
    server = Server(
        [2027, 2026], ["Toyota"], ["RAV4"], {"vehicle_type": "suv", "body_style": "suv", "confidence": 0.9}, ["Honda"]
    )
    catalog = HttpVehicleCatalog(server.api())
    assert await catalog.years() == [2027, 2026]
    assert await catalog.makes(2021) == ["Toyota"]
    assert await catalog.models("Toyota", 2021) == ["RAV4"]
    assert await catalog.vehicle_type("Toyota", "RAV4", 2021) == {
        "vehicle_type": "suv",
        "body_style": "suv",
        "confidence": 0.9,
    }
    assert await catalog.makes() == ["Honda"]
    assert [(r.url.path, dict(r.url.params)) for r in server.requests] == [
        ("/api/years", {}),
        ("/api/makes", {"year": "2021"}),
        ("/api/models", {"make": "Toyota", "year": "2021"}),
        ("/api/vehicle-type", {"make": "Toyota", "model": "RAV4", "year": "2021"}),
        ("/api/makes", {}),
    ]


async def test_catalog_mapping_functions_adapt_another_wire_format():
    server = Server({"makes": [{"name": "Toyota"}]}, {"class": "SUV"})
    catalog = HttpVehicleCatalog(
        server.api(),
        to_names=lambda body: [m["name"] for m in body["makes"]],
        to_type=lambda body: {"vehicle_type": body["class"].lower(), "body_style": None, "confidence": 1.0},
    )
    assert await catalog.makes() == ["Toyota"]
    assert (await catalog.vehicle_type("Toyota", "RAV4"))["vehicle_type"] == "suv"


# --------------------------------------------------------------------------- address and VIN
async def test_address_search_and_mapping():
    server = Server({"candidates": [{"address_line1": "1 Rue A"}]}, {"hits": [{"address_line1": "2 Rue B"}]})
    assert await HttpAddressService(server.api()).candidates("1 rue a", "H2X 1Y4") == [{"address_line1": "1 Rue A"}]
    assert dict(server.requests[0].url.params) == {"q": "1 rue a", "postal_code": "H2X 1Y4"}
    mapped = HttpAddressService(server.api(), to_candidates=lambda body: body["hits"])
    assert await mapped.candidates("2 rue b") == [{"address_line1": "2 Rue B"}]
    assert dict(server.requests[1].url.params) == {"q": "2 rue b"}


async def test_vin_decoder_checks_the_digit_before_calling_out():
    server = Server({"year": 2021, "make": "Toyota", "model": "RAV4", "trim": "XLE", "dealer": "ignored"})
    decoder = HttpVinDecoder(server.api())
    good = with_check_digit("2T3W1RFV0MC123456")
    assert await decoder.decode(good) == {
        "valid_checksum": True,
        "year": 2021,
        "make": "Toyota",
        "model": "RAV4",
        "trim": "XLE",
    }
    assert server.requests[0].url.path == f"/api/vin/{good}"
    assert await decoder.decode("2T3W1RFV0MC123456") == {"valid_checksum": False} and len(server.requests) == 1


async def test_vin_mapping_function():
    server = Server({"modelYear": 2020, "manufacturer": "Honda", "series": "Civic"})
    decoder = HttpVinDecoder(
        server.api(),
        to_vehicle=lambda b: {"year": b["modelYear"], "make": b["manufacturer"], "model": b["series"], "trim": None},
    )
    assert (await decoder.decode("11111111111111111"))["make"] == "Honda"


# --------------------------------------------------------------------------- BFF state reader
BFF_ANSWER = {
    "session_id": "s_1",
    "phase": "gather",
    "state": {"quote": {"first_name": {"value": "Marie"}}, "vehicles": [], "drivers": []},
    "quote_blocked": False,
    "missing_fields": ["/quote/email"],
}


async def test_bff_reader_authorizes_with_the_session_ref_and_returns_the_state():
    server = Server(BFF_ANSWER)
    state = await HttpBffStateReader(server.api()).read("s_1", "tok.sig")
    assert state == {**BFF_ANSWER["state"], "quote_blocked": False}  # state kept; phase and missing_fields left behind
    request = server.requests[0]
    assert (
        request.url.path == "/api/internal/sessions/s_1/state"
        and request.headers["Authorization"] == "SessionRef tok.sig"
    )


async def test_bff_reader_keeps_quote_blocked():
    state = await HttpBffStateReader(Server({**BFF_ANSWER, "quote_blocked": True}).api()).read("s_1", "r")
    assert state["quote_blocked"] is True and state["quote"] == BFF_ANSWER["state"]["quote"]


@pytest.mark.parametrize(
    "answer",
    [
        {"state": BFF_ANSWER["state"]},  # no quote_blocked: an older BFF
        {"state": BFF_ANSWER["state"], "preferences": {"answer_mode": "text"}, "quote_blocked": None},  # no phase
        {**BFF_ANSWER, "extra": {"anything": 1}},  # keys the reader does not know
    ],
)
async def test_bff_reader_tolerates_other_shapes_of_the_answer(answer):
    state = await HttpBffStateReader(Server(answer).api()).read("s_1", "r")
    assert state == {**BFF_ANSWER["state"], "quote_blocked": False}


async def test_bff_reader_maps_404_to_session_not_found_and_lets_other_errors_through():
    with pytest.raises(SessionNotFound):
        await HttpBffStateReader(Server(httpx.Response(404)).api()).read("s_9", "r")
    with pytest.raises(httpx.HTTPStatusError):
        await HttpBffStateReader(Server(httpx.Response(401)).api()).read("s_1", "bad")
