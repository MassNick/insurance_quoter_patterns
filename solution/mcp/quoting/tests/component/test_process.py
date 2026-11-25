"""`python -m mcp_quoting` as a real process, called by the SDK's own streamable HTTP client."""

import json
import os
import socket
import subprocess
import sys
import time

import httpx
import pytest
from helpers import KEY, ref
from mcp import Client

pytestmark = pytest.mark.component


def free_port() -> int:
    """A TCP port on localhost that is free right now."""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(**env) -> subprocess.Popen:
    """Start `python -m mcp_quoting` as a process on localhost, with `env` added to its environment."""
    environ = {**os.environ, "HOST": "127.0.0.1", "SESSION_REF_KEY": KEY.decode(), **env}
    environ.pop("QUOTE_CONTRACTS_DIR", None)
    return subprocess.Popen(
        [sys.executable, "-m", "mcp_quoting"], env=environ, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
    )


@pytest.fixture
def server():
    """The URL of a server process on a free port; stops it afterwards and checks that its logs are JSON lines."""
    port = free_port()
    process = start(PORT=str(port))
    url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                if httpx.get(f"{url}/healthz").status_code == 200:
                    break
            except httpx.TransportError:
                time.sleep(0.1)
        else:
            pytest.fail("the server did not start")
        yield url
    finally:
        process.terminate()
        output = process.communicate(timeout=10)[0]
    for line in output.splitlines():
        assert {"ts", "level", "logger", "msg"} <= set(json.loads(line))  # one JSON object per line, shared format


@pytest.mark.anyio
async def test_a_real_mcp_client_lists_and_calls_tools_over_http(server):
    async with Client(f"{server}/mcp") as client:
        assert len((await client.list_tools()).tools) == 6
        found = await client.call_tool("lookup_make_model_year", {"year": 2021, "make": "Lamborghini", "model": "Urus"})
        refused = await client.call_tool("create_quote", {"session_ref": ref()})  # no BFF is running: an upstream error
    assert found.structured_content["valid"] is True
    assert refused.is_error and refused.structured_content["error"] == "upstream_error"


def test_the_process_exits_with_a_clear_error_when_the_key_is_missing():
    process = start(SESSION_REF_KEY="")
    output = process.communicate(timeout=20)[0]
    assert process.returncode == 1
    assert json.loads(output.splitlines()[-1])["msg"] == "configuration error: SESSION_REF_KEY is required"
