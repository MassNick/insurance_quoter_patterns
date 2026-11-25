"""The toolbox version body and the create, promote, pin and list requests."""

import json

import pytest

import create_toolbox as ct

PROJECT = "https://aif.services.ai.azure.com/api/projects/p"
# the flags every run needs
ARGS = ["--project-endpoint", PROJECT, "--quoting-url", "https://q.example/mcp", "--a2ui-url", "https://a.example/mcp"]
# the path of the toolbox under the project endpoint
BASE = "/api/projects/p/toolboxes/auto-quoting-toolbox"


def test_version_body_has_both_entries_with_connections_and_no_approval():
    body = ct.build_version_body("https://q/mcp", "https://a/mcp", "conn-q", "conn-a")
    assert [(t["server_label"], t["server_url"], t["project_connection_id"]) for t in body["tools"]] == [
        ("quoting", "https://q/mcp", "conn-q"),
        ("a2ui", "https://a/mcp", "conn-a"),
    ]
    assert all(t["type"] == "mcp" and t["require_approval"] == "never" and t["description"] for t in body["tools"])


def test_environment_variables_are_the_defaults(monkeypatch):
    monkeypatch.setenv("FOUNDRY_PROJECT_ENDPOINT", "https://e/api/projects/p")
    monkeypatch.setenv("MCP_QUOTING_URL", "https://q/mcp")
    monkeypatch.setenv("MCP_A2UI_URL", "https://a/mcp")
    args = ct.parse_args([])
    assert (args.project_endpoint, args.quoting_url, args.a2ui_url) == (
        "https://e/api/projects/p",
        "https://q/mcp",
        "https://a/mcp",
    )
    assert args.quoting_connection == "conn-mcp-quoting"


def test_create_then_promote_uses_the_version_the_service_returned(make_client):
    client, fake = make_client({("POST", f"{BASE}/versions"): {"version": "3"}, ("PATCH", BASE): {}}, base_url=PROJECT)
    args = ct.parse_args(ARGS + ["--promote"])
    assert ct.run(args, client) == {"toolbox": "auto-quoting-toolbox", "version": "3", "promoted": True}
    assert [r.method for r in fake.requests] == ["POST", "PATCH"]
    assert fake.bodies()[1] == {"default_version": "3"}
    assert fake.requests[0].url.params["api-version"] == "v1"


def test_create_without_promote_sends_one_request(make_client):
    client, fake = make_client({("POST", f"{BASE}/versions"): {"version": "1"}}, base_url=PROJECT)
    ct.run(ct.parse_args(ARGS), client)
    assert [r.method for r in fake.requests] == ["POST"]


def test_pin_only_patches_the_default_version(make_client):
    client, fake = make_client({("PATCH", BASE): {}}, base_url=PROJECT)
    ct.run(ct.parse_args(ARGS + ["--pin", "2"]), client)
    assert [r.method for r in fake.requests] == ["PATCH"]
    assert fake.bodies() == [{"default_version": "2"}]


def test_list_only_reads(make_client):
    client, fake = make_client({("GET", f"{BASE}/versions"): {"data": []}}, base_url=PROJECT)
    ct.run(ct.parse_args(ARGS + ["--list"]), client)
    assert [r.method for r in fake.requests] == ["GET"]


def test_promote_pin_and_list_are_exclusive():
    with pytest.raises(SystemExit):
        ct.parse_args(ARGS + ["--promote", "--pin", "1"])


def test_main_dry_run_prints_the_requests_and_needs_no_credentials(capsys):
    assert ct.main(ARGS + ["--promote", "--dry-run"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [(line["method"], line["url"].rsplit("/", 1)[-1]) for line in lines] == [
        ("POST", "versions"),
        ("PATCH", "auto-quoting-toolbox"),
    ]


def test_main_asks_for_the_urls_when_creating():
    with pytest.raises(SystemExit, match="--quoting-url"):
        ct.main(["--project-endpoint", "https://e", "--dry-run"])
