"""The shared HTTP client and argument helpers: dry run, bearer token, errors and event streams."""

import argparse
import json

import httpx
import pytest

from common import Client, add_common_args, decode_sse, env, require
from tests.conftest import sse


def test_decode_sse_reads_data_lines_only():
    text = ': keepalive\n\ndata: {"type": "a"}\n\nevent: x\ndata: {"type": "b"}\n\n'
    assert decode_sse(text) == [{"type": "a"}, {"type": "b"}]


def test_env_treats_empty_as_unset(monkeypatch):
    monkeypatch.setenv("X_SET", "v")
    monkeypatch.setenv("X_EMPTY", "")
    assert env("X_SET") == "v"
    assert env("X_EMPTY", "fallback") == "fallback"
    assert env("X_MISSING") is None


def test_require_exits_with_the_missing_flags():
    args = argparse.Namespace(project_endpoint=None, quoting_url="x")
    with pytest.raises(SystemExit) as exc:
        require(args, "project_endpoint", "quoting_url")
    assert "--project-endpoint" in str(exc.value)
    assert "--quoting-url" not in str(exc.value)


def test_dry_run_prints_one_json_line_and_sends_nothing(capsys):
    def boom(request):
        raise AssertionError("dry run must not send")

    client = Client("https://h.example/api/", "s", dry_run=True, transport=httpx.MockTransport(boom))
    assert client.request("POST", "/x", params={"api-version": "v1"}, body={"a": 1}) == {}
    line = json.loads(capsys.readouterr().out)
    assert line == {"method": "POST", "url": "https://h.example/api/x", "params": {"api-version": "v1"}, "body": {"a": 1}}


def test_bearer_token_comes_from_the_credential_for_the_scope(make_client, credential):
    client, fake = make_client({("GET", "/base/ping"): {"ok": True}}, scope="https://ai.azure.com/.default")
    assert client.request("GET", "/ping") == {"ok": True}
    assert fake.requests[0].headers["authorization"] == "Bearer fake-token"
    assert credential.scopes == ["https://ai.azure.com/.default"]


def test_no_scope_means_no_authorization_header(make_client):
    client, fake = make_client({("GET", "/base/ping"): {}}, scope=None)
    client.request("GET", "/ping")
    assert "authorization" not in fake.requests[0].headers


def test_http_errors_raise_with_status_and_body(make_client):
    client, _ = make_client({("GET", "/base/x"): httpx.Response(403, text="forbidden")})
    with pytest.raises(RuntimeError, match="HTTP 403: forbidden"):
        client.request("GET", "/x")


def test_event_streams_come_back_as_a_list(make_client):
    client, _ = make_client({("POST", "/base/t"): sse({"type": "a"}, {"type": "b"})})
    assert client.request("POST", "/t", body={}) == [{"type": "a"}, {"type": "b"}]


def test_common_args_add_dry_run():
    parser = argparse.ArgumentParser()
    add_common_args(parser)
    assert parser.parse_args(["--dry-run"]).dry_run is True
