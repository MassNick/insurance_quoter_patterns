"""`python -m mcp_a2ui` as a real process, called by the SDK's own streamable HTTP client."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx2
import pytest
from mcp import Client

pytestmark = [pytest.mark.component, pytest.mark.anyio]
FORM_SPEC = {"form_id": "about_you", "title": "A propos", "fields": [{"key": "first_name"}]}  # a small valid FormSpec


def free_port() -> int:
    """A TCP port on localhost that is free right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture
def server():
    """The URL of a server process on a free port; stops it afterwards and checks that its logs are JSON lines."""
    port = free_port()
    environ = {**os.environ, "HOST": "127.0.0.1", "PORT": str(port)}
    environ.pop("ENTRA_AUDIENCE", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_a2ui"], env=environ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx2.get(f"{url}/healthz").status_code == 200:
                    break
            except httpx2.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("the server did not start")
        yield url
    finally:
        process.terminate()
        output = process.communicate(timeout=10)[0]
    logged = [json.loads(line) for line in output.splitlines()]  # every log line is one JSON object
    assert logged and {"ts", "level", "logger", "msg"} <= set(logged[0])


async def test_a_real_mcp_client_lists_and_calls_tools_over_http(server):
    async with Client(f"{server}/mcp") as client:
        assert len((await client.list_tools()).tools) == 4
        opened = await client.call_tool("render_form", {"form_spec": FORM_SPEC})
        refused = await client.call_tool("render_form", {"form_spec": {"form_id": "about_you"}})
    assert not opened.is_error and json.loads(opened.content[0].text) == {"surface_id": "about_you", "status": "shown"}
    assert refused.is_error and json.loads(refused.content[0].text)["error"] == "invalid_arguments"


def test_the_process_exits_with_a_clear_error_when_the_tenant_is_missing():
    environ = {**os.environ, "HOST": "127.0.0.1", "PORT": str(free_port()), "ENTRA_AUDIENCE": "api://dev-only-mcp-a2ui"}
    environ.pop("ENTRA_TENANT_ID", None)
    process = subprocess.Popen(
        [sys.executable, "-m", "mcp_a2ui"], env=environ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )
    output = process.communicate(timeout=20)[0]
    assert process.returncode == 1
    assert json.loads(output.splitlines()[-1])["msg"] == (
        "configuration error: ENTRA_TENANT_ID is required when ENTRA_AUDIENCE is set"
    )
