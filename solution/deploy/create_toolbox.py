#!/usr/bin/env python3
"""Create a version of the toolbox `auto-quoting-toolbox` and promote or pin it (implementation plan 5.1 to 5.2).

The toolbox puts the two MCP servers behind one MCP endpoint. Entries: `quoting` -> mcp-quoting and `a2ui` -> mcp-a2ui, each over
a RemoteTool project connection (Entra, created by Terraform) with require_approval "never". Versions are immutable snapshots.
The first version becomes the default by itself; later ones are promoted with --promote, and --pin N rolls the default back or
forward without creating anything.

REST calls (azure-ai-projects >= 2.3 `project.toolboxes.*` makes the same ones):
  POST  {project}/toolboxes/{name}/versions?api-version=v1   create a version
  PATCH {project}/toolboxes/{name}?api-version=v1            {"default_version": "<n>"}
  GET   {project}/toolboxes/{name}/versions?api-version=v1   --list

Example:
  create_toolbox.py --project-endpoint $FOUNDRY_PROJECT_ENDPOINT --promote \
    --quoting-url https://<fqdn>/mcp --a2ui-url https://<fqdn>/mcp
"""

from __future__ import annotations

import argparse
import json
import sys

from common import FOUNDRY_SCOPE, Client, add_common_args, env, require

TOOLBOX = "auto-quoting-toolbox"  # name of the toolbox
API = {"api-version": "v1"}  # query string of every toolbox request


def build_version_body(quoting_url: str, a2ui_url: str, quoting_connection: str, a2ui_connection: str) -> dict:
    """The request body of one toolbox version (every tool carries a description, as the toolbox docs ask)."""
    entries = [
        ("quoting", quoting_url, quoting_connection, "Quoting tools: address, vehicle lookups, quote creation and changes"),
        ("a2ui", a2ui_url, a2ui_connection, "Form tools: render, update and close forms and offer cards"),
    ]
    return {
        "description": "Quoting tools and A2UI form tools for quote-agent",
        "tools": [
            {
                "type": "mcp",
                "server_label": label,
                "server_url": url,
                "require_approval": "never",
                "project_connection_id": connection,
                "description": description,
            }
            for label, url, connection, description in entries
        ],
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags; the project endpoint and the two MCP URLs may come from the environment."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument("--project-endpoint", default=env("FOUNDRY_PROJECT_ENDPOINT"), help="env FOUNDRY_PROJECT_ENDPOINT")
    p.add_argument("--name", default=TOOLBOX)
    p.add_argument("--quoting-url", default=env("MCP_QUOTING_URL"), help="https://<fqdn>/mcp (env MCP_QUOTING_URL)")
    p.add_argument("--a2ui-url", default=env("MCP_A2UI_URL"), help="https://<fqdn>/mcp (env MCP_A2UI_URL)")
    p.add_argument("--quoting-connection", default="conn-mcp-quoting")
    p.add_argument("--a2ui-connection", default="conn-mcp-a2ui")
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--promote", action="store_true", help="make the new version the default")
    mode.add_argument("--pin", metavar="VERSION", help="make an existing version the default; creates nothing")
    mode.add_argument("--list", action="store_true", help="list the versions; creates nothing")
    return p.parse_args(argv)


def run(args: argparse.Namespace, client: Client) -> dict:
    """Do what the flags ask: list the versions, pin one, or create a version (and promote it). Returns the result to print."""
    base = f"/toolboxes/{args.name}"
    if args.list:
        return client.request("GET", f"{base}/versions", params=API)
    if args.pin:
        return client.request("PATCH", base, params=API, body={"default_version": args.pin})
    body = build_version_body(args.quoting_url, args.a2ui_url, args.quoting_connection, args.a2ui_connection)
    created = client.request("POST", f"{base}/versions", params=API, body=body)
    version = str(created.get("version", "<new-version>"))
    if args.promote:
        client.request("PATCH", base, params=API, body={"default_version": version})
    return {"toolbox": args.name, "version": version, "promoted": args.promote}


def main(argv: list[str] | None = None, client: Client | None = None) -> int:
    """Run what the flags ask and print the result; with `--dry-run` the requests are printed instead and nothing is sent.

    `client` is for tests. Returns the exit code.
    """
    args = parse_args(argv)
    require(args, "project_endpoint")
    if not (args.list or args.pin):
        require(args, "quoting_url", "a2ui_url")
    client = client or Client(args.project_endpoint, FOUNDRY_SCOPE, dry_run=args.dry_run)
    result = run(args, client)
    if not args.dry_run:
        print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
