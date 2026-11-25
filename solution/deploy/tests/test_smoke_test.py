"""The three smoke checks against fake BFF and toolbox backends."""

import json
from pathlib import Path

import httpx
import pytest

import smoke_test as st
from tests.conftest import sse

PROJECT = "https://aif.services.ai.azure.com/api/projects/p"
# path of the toolbox MCP endpoint
MCP = "/api/projects/p/toolboxes/auto-quoting-toolbox/mcp"
# the tool contract the expected counts come from
TOOLS_JSON = Path(__file__).resolve().parents[2] / "contracts" / "tools.json"


def served(separator: str, quoting: int = 6, a2ui: int = 4) -> list[dict]:
    """Toolbox tool entries named `quoting<sep>tN` and `a2ui<sep>tN`, with the given count per server."""
    return [{"name": f"quoting{separator}t{i}"} for i in range(quoting)] + [{"name": f"a2ui{separator}t{i}"} for i in range(a2ui)]


def mcp_routes(tools: list[dict], as_sse: bool = False) -> dict:
    """Routes of a fake toolbox MCP endpoint that lists `tools`, answering with JSON or an event stream."""

    def answer(request: httpx.Request) -> httpx.Response:
        """Answer an MCP request: 202 for a notification, otherwise a JSON-RPC result."""
        message = json.loads(request.content)
        if "id" not in message:
            return httpx.Response(202)
        result = {"tools": tools} if message["method"] == "tools/list" else {"capabilities": {}}
        reply = {"jsonrpc": "2.0", "id": message["id"], "result": result}
        return sse(reply) if as_sse else httpx.Response(200, json=reply)

    return {("POST", MCP): answer}


def bff_routes(model_calls: int = 2, with_text: bool = True) -> dict:
    """Routes of a fake BFF whose turn reports `model_calls` and may send no text."""
    events = [{"type": "turn.started", "data": {}}]
    events += [{"type": "text.delta", "data": {"delta": "Bonjour"}}] if with_text else []
    events += [{"type": "turn.completed", "data": {"model_calls": model_calls}}]
    return {
        ("GET", "/api/health"): {"status": "ok"},
        ("GET", "/api/health/sse"): sse({"type": "health"}),
        ("POST", "/api/sessions"): {"session_id": "s_1"},
        ("POST", "/api/sessions/s_1/turns"): sse(*events),
    }


def test_expected_tool_counts_match_the_contract():
    names = [t["name"] for t in json.loads(TOOLS_JSON.read_text())["tools"]]
    assert {s: sum(n.startswith(s + ".") for n in names) for s in st.EXPECTED_TOOLS} == st.EXPECTED_TOOLS


def test_bff_check_passes_on_health_and_sse(make_client):
    bff, _ = make_client(bff_routes(), base_url="https://afd.example", scope=None)
    assert "ok" in st.check_bff(bff)


def test_bff_check_fails_when_unhealthy(make_client):
    routes = bff_routes() | {("GET", "/api/health"): {"status": "degraded"}}
    bff, _ = make_client(routes, base_url="https://afd.example", scope=None)
    with pytest.raises(AssertionError, match="degraded"):
        st.check_bff(bff)


def test_turn_check_wants_exactly_two_model_calls(make_client):
    bff, _ = make_client(bff_routes(), base_url="https://afd.example", scope=None)
    assert "model_calls=2" in st.check_turn(bff)
    bff3, _ = make_client(bff_routes(model_calls=3), base_url="https://afd.example", scope=None)
    with pytest.raises(AssertionError, match="model_calls = 3"):
        st.check_turn(bff3)


def test_turn_check_wants_some_text(make_client):
    bff, _ = make_client(bff_routes(with_text=False), base_url="https://afd.example", scope=None)
    with pytest.raises(AssertionError, match="no text"):
        st.check_turn(bff)


def test_toolbox_check_accepts_either_wire_separator(make_client):
    for separator in (".", "__"):
        foundry, _ = make_client(mcp_routes(served(separator)), base_url=PROJECT)
        assert "10 tools" in st.check_toolbox(foundry)


def test_toolbox_check_reads_an_event_stream_answer(make_client):
    foundry, _ = make_client(mcp_routes(served("."), as_sse=True), base_url=PROJECT)
    assert "10 tools" in st.check_toolbox(foundry)


def test_toolbox_check_fails_when_a_server_is_missing(make_client):
    foundry, _ = make_client(mcp_routes(served(".", quoting=6, a2ui=0)), base_url=PROJECT)
    with pytest.raises(AssertionError, match="expected"):
        st.check_toolbox(foundry)


def test_main_reports_failures_with_exit_code_1(make_client, capsys):
    bff, _ = make_client(bff_routes(model_calls=5), base_url="https://afd.example", scope=None)
    code = st.main(["--bff-url", "https://afd.example", "--checks", "bff,turn"], bff=bff)
    out = capsys.readouterr().out
    assert code == 1 and "PASS bff" in out and "FAIL turn" in out


def test_main_dry_run_checks_nothing_and_exits_0(capsys):
    code = st.main(["--bff-url", "https://afd.example", "--project-endpoint", PROJECT, "--dry-run"])
    captured = capsys.readouterr()
    assert code == 0 and len(captured.out.splitlines()) == 7 and "FAIL" not in captured.out
