#!/usr/bin/env python3
"""Smoke tests after a deployment: readiness, the toolbox tool list, and one real chat turn.

  bff      GET /api/health and /api/health/sse through the edge (Front Door), so the private link to the BFF is exercised too.
  toolbox  MCP initialize + tools/list on the toolbox consumer endpoint: 6 quoting + 4 a2ui tools. Prints the served names,
           which settles the wire-name question (dot or double underscore, implementation plan R11).
  turn     POST /api/sessions, then one user.message through the whole stack with the real hosted agent. No fakes. Expects
           turn.started, text, and turn.completed with model_calls == 2 (NFR-PERF-3).

`bff` and `turn` run from anywhere. `toolbox` needs a network path to the private Foundry endpoint (self-hosted runner).
Exit code 1 if any check fails. --dry-run prints the requests and checks nothing.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Callable

from common import FOUNDRY_SCOPE, Client, add_common_args, env, require

TOOLBOX = "auto-quoting-toolbox"  # name of the toolbox
EXPECTED_TOOLS = {"quoting": 6, "a2ui": 4}  # contracts/tools.json
# headers of the toolbox's MCP requests (JSON or an event stream may answer)
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def check_bff(bff: Client) -> str:
    """`GET /api/health` and `/api/health/sse` through the edge. Returns the success message; raises
    `AssertionError` when a check fails (nothing is checked in a dry run)."""
    health = bff.request("GET", "/api/health")
    events = bff.request("GET", "/api/health/sse")
    if not bff.dry_run:
        assert health.get("status") == "ok", f"/api/health returned {health}"
        assert any(e.get("type") == "health" for e in events), f"/api/health/sse sent {events}"
    return "BFF health and SSE ok"


def rpc(foundry: Client, path: str, method: str, params: dict | None, msg_id: int | None) -> dict:
    """One MCP JSON-RPC call (the server may answer with JSON or an SSE stream). Returns the `result` object."""
    message: dict = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        message["params"] = params
    if msg_id is not None:
        message["id"] = msg_id
    reply = foundry.request("POST", path, params={"api-version": "v1"}, body=message, headers=MCP_HEADERS)
    replies = reply if isinstance(reply, list) else [reply]
    for r in replies:
        if r.get("id") == msg_id and "error" in r:
            raise AssertionError(f"{method}: {r['error']}")
        if r.get("id") == msg_id and msg_id is not None:
            return r.get("result", {})
    return {}


def check_toolbox(foundry: Client) -> str:
    """MCP `initialize` and `tools/list` on the toolbox: the served tools must match `EXPECTED_TOOLS` per server,
    whatever the separator in their names. Returns a summary line with the tool names."""
    path = f"/toolboxes/{TOOLBOX}/mcp"
    rpc(
        foundry,
        path,
        "initialize",
        {"protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "smoke", "version": "1"}},
        1,
    )
    rpc(foundry, path, "notifications/initialized", None, None)
    names = sorted(t["name"] for t in rpc(foundry, path, "tools/list", {}, 2).get("tools", []))
    if not foundry.dry_run:
        counts: dict[str, int] = {}
        for name in names:
            server = re.split(r"[._-]", name, maxsplit=1)[0]  # quoting.x, quoting__x or quoting-x: the separator is what R11 asks
            counts[server] = counts.get(server, 0) + 1
        assert counts == EXPECTED_TOOLS, f"expected {EXPECTED_TOOLS}, the toolbox serves {counts}: {names}"
    return f"{len(names)} tools: {', '.join(names)}"


def check_turn(bff: Client) -> str:
    """Create a session and send one message through the whole stack. Expects `turn.started`, some text and a
    `turn.completed` with `model_calls` equal to 2."""
    session = bff.request("POST", "/api/sessions", body={"locale": "fr-CA"})
    sid = session.get("session_id", "<session_id>")
    events = bff.request("POST", f"/api/sessions/{sid}/turns", body={"type": "user.message", "data": {"text": "Bonjour"}})
    if bff.dry_run:
        return "turn requested"
    types = [e["type"] for e in events]
    assert "turn.started" in types, f"no turn.started in {types}"
    assert "text.delta" in types or "text.done" in types, f"no text in {types}"
    done = next((e for e in events if e["type"] == "turn.completed"), None)
    assert done, f"no turn.completed in {types}"
    assert done["data"].get("model_calls") == 2, f"model_calls = {done['data'].get('model_calls')}, expected 2"
    return f"{len(events)} events, model_calls=2"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags; the BFF URL and the project endpoint may come from the environment."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--bff-url", default=env("BFF_URL"), help="https://<front-door-host> (env BFF_URL)")
    p.add_argument(
        "--project-endpoint", default=env("FOUNDRY_PROJECT_ENDPOINT"), help="env FOUNDRY_PROJECT_ENDPOINT, for the toolbox check"
    )
    p.add_argument("--checks", default="bff,toolbox,turn", help="comma-separated subset of bff,toolbox,turn")
    return p.parse_args(argv)


def main(argv: list[str] | None = None, bff: Client | None = None, foundry: Client | None = None) -> int:
    """Run the selected checks and print PASS or FAIL for each. Exit code 1 if one fails; `--dry-run` only prints the
    requests. `bff` and `foundry` replace the clients in tests."""
    args = parse_args(argv)
    selected = [c.strip() for c in args.checks.split(",") if c.strip()]
    unknown = set(selected) - {"bff", "toolbox", "turn"}
    if unknown:
        sys.exit(f"error: unknown check(s) {sorted(unknown)}")
    if {"bff", "turn"} & set(selected):
        require(args, "bff_url")
        bff = bff or Client(args.bff_url, dry_run=args.dry_run, timeout=120.0)  # a cold hosted session can take a while
    if "toolbox" in selected:
        require(args, "project_endpoint")
        foundry = foundry or Client(args.project_endpoint, FOUNDRY_SCOPE, dry_run=args.dry_run)

    runners: dict[str, Callable[[], str]] = {
        "bff": lambda: check_bff(bff),
        "toolbox": lambda: check_toolbox(foundry),
        "turn": lambda: check_turn(bff),
    }
    failed = 0
    for name in selected:
        try:
            detail = runners[name]()
            print(f"DRY {name}" if args.dry_run else f"PASS {name}: {detail}", file=sys.stderr if args.dry_run else sys.stdout)
        except (AssertionError, RuntimeError) as exc:
            failed += 1
            print(f"FAIL {name}: {exc}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
