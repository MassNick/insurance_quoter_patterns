"""Create a new version of the hosted agent ``quote-agent`` from source (Foundry source-zip deploy).

Never run by tests or CI. ``--dry-run`` builds the zip and prints the definition without calling Azure.
Every option is documented in ../README.md (Deploy). The definition template is ../agent.yaml.

The models, thinking levels, token limits, timeout and memory output schema default from the ``agents`` entries of
settings.yaml (``--settings``, the solution root by default). Precedence: flag, then the environment variable of the
same name (MAIN_REASONING_EFFORT, ...), then settings.yaml, then the default in OPTIONS.

    python deploy/deploy_quote_agent.py --project-endpoint https://<resource>.services.ai.azure.com/api/projects/<p> \
        --toolbox-mcp-url '<project>/toolboxes/auto-quoting-toolbox/mcp?api-version=v1' \
        --foundry-iq-mcp-url '<search>/knowledgebases/auto-quoting-kb/mcp?api-version=2026-11-01-preview' \
        --cosmos-endpoint https://<account>.documents.azure.com:443/ --wait
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import zipfile
from pathlib import Path

import yaml

# the quote-agent folder: agent.yaml, main.py, src/
AGENT_DIR = Path(__file__).resolve().parent.parent
# the solution root: contracts/, packages/quote_common and settings.yaml
SOLUTION = AGENT_DIR.parent.parent
# default of --settings
SETTINGS = SOLUTION / "settings.yaml"
# agent.yaml placeholder -> (entry under `agents` in settings.yaml, key): the options that settings.yaml gives a default
FROM_SETTINGS = {
    "MAIN_MODEL_DEPLOYMENT": ("main", "deployment"),
    "MAIN_REASONING_EFFORT": ("main", "reasoning_effort"),
    "MAIN_MAX_OUTPUT_TOKENS": ("main", "max_output_tokens"),
    "MEMORY_MODEL_DEPLOYMENT": ("memory", "deployment"),
    "MEMORY_REASONING_EFFORT": ("memory", "reasoning_effort"),
    "MEMORY_MAX_OUTPUT_TOKENS": ("memory", "max_output_tokens"),
    "MEMORY_TIMEOUT_SECONDS": ("memory", "timeout_seconds"),
    "MEMORY_OUTPUT_SCHEMA": ("memory", "output_schema"),
}
# (option, agent.yaml placeholder, default, help)
OPTIONS = [
    ("--project-endpoint", "FOUNDRY_PROJECT_ENDPOINT", None, "Foundry project endpoint (required)"),
    ("--main-model-deployment", "MAIN_MODEL_DEPLOYMENT", None, "Main agent model deployment name"),
    ("--memory-model-deployment", "MEMORY_MODEL_DEPLOYMENT", None, "Memory agent model deployment name"),
    ("--main-reasoning-effort", "MAIN_REASONING_EFFORT", "", "Main agent reasoning effort (thinking level)"),
    ("--main-temperature", "MAIN_TEMPERATURE", "", "Main agent temperature"),
    ("--main-max-output-tokens", "MAIN_MAX_OUTPUT_TOKENS", "", "Main agent max output tokens"),
    ("--memory-reasoning-effort", "MEMORY_REASONING_EFFORT", "", "Memory agent reasoning effort"),
    ("--memory-temperature", "MEMORY_TEMPERATURE", "", "Memory agent temperature"),
    ("--memory-max-output-tokens", "MEMORY_MAX_OUTPUT_TOKENS", "", "Memory agent max output tokens"),
    ("--memory-timeout-seconds", "MEMORY_TIMEOUT_SECONDS", "4", "Memory agent timeout before the turn goes on"),
    (
        "--memory-output-schema",
        "MEMORY_OUTPUT_SCHEMA",
        "memory-output.schema.json",
        "Memory agent output schema (file in contracts/)",
    ),
    ("--prompt-version", "PROMPT_VERSION", "v1", "Prompt version, part of prompt_cache_key"),
    ("--toolbox-mcp-url", "TOOLBOX_MCP_URL", "", "Toolbox MCP consumer endpoint (quoting.* and a2ui.* tools)"),
    ("--foundry-iq-mcp-url", "FOUNDRY_IQ_MCP_URL", "", "Foundry IQ knowledge base MCP endpoint"),
    ("--foundry-iq-token-scope", "FOUNDRY_IQ_TOKEN_SCOPE", "https://search.azure.com/.default", "Entra scope"),
    ("--cosmos-endpoint", "COSMOS_ENDPOINT", "", "Cosmos DB account endpoint for agent_history"),
    ("--cosmos-database", "COSMOS_DATABASE", "quoting", "Cosmos DB database"),
    ("--cosmos-container", "COSMOS_CONTAINER", "agent_history", "Cosmos DB container (partition key /session_id)"),
    ("--cpu", "CPU", "1", "vCPU per session"),
    ("--memory", "MEMORY", "2Gi", "Memory per session"),
    ("--protocol-version", "PROTOCOL_VERSION", "2.0.0", "Invocations protocol version [UNVERIFIED]"),
]


def settings_defaults(path: Path) -> dict[str, str]:
    """Option defaults by agent.yaml placeholder, from the `agents` entries of settings.yaml (absent keys: none)."""
    agents = (yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("agents") or {}
    found = {name: (agents.get(agent) or {}).get(key) for name, (agent, key) in FROM_SETTINGS.items()}
    return {name: str(value) for name, value in found.items() if value is not None}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags. For the options settings.yaml can fill, the precedence is: flag, environment variable,
    settings.yaml, default. Exits with a usage error for a missing required value or a ``--memory-output-schema``
    that is not a file in contracts/."""
    settings = argparse.ArgumentParser(add_help=False)  # parsed first: it decides the defaults of the other options
    settings.add_argument("--settings", type=Path, default=SETTINGS, help="Settings file (default: %(default)s)")
    from_settings = settings_defaults(settings.parse_known_args(argv)[0].settings)
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[settings])
    parser.add_argument("--agent-name", default="quote-agent", help="Foundry agent name")
    parser.add_argument("--description", default=None, help="Version description (default: from agent.yaml)")
    parser.add_argument("--zip-out", type=Path, default=AGENT_DIR / "dist" / "quote-agent.zip", help="Zip path")
    parser.add_argument("--wait", action="store_true", help="Poll until the version is active or failed")
    parser.add_argument("--dry-run", action="store_true", help="Build the zip and print the definition only")
    for flag, name, default, help_ in OPTIONS:
        if name in FROM_SETTINGS:
            default = os.environ.get(name) or from_settings.get(name, default)
            agent, key = FROM_SETTINGS[name]
            help_ += f" (default: the environment variable of that name, else settings.yaml agents.{agent}.{key})"
        parser.add_argument(flag, default=default, required=default is None, help=help_)
    args = parser.parse_args(argv)
    if not (SOLUTION / "contracts" / args.memory_output_schema).is_file():
        parser.error(f"--memory-output-schema: {args.memory_output_schema!r} is not a file in contracts/")
    return args


def render_definition(args: argparse.Namespace) -> dict:
    """agent.yaml with placeholders filled; empty environment variables are dropped."""
    text = (AGENT_DIR / "agent.yaml").read_text(encoding="utf-8")
    for flag, name, _, _ in OPTIONS:
        text = text.replace("${" + name + "}", json.dumps(str(getattr(args, flag[2:].replace("-", "_")))))
    doc = yaml.safe_load(text)
    env = doc["definition"]["environment_variables"]
    doc["definition"]["environment_variables"] = {k: str(v) for k, v in env.items() if v not in ("", None)}
    doc["description"] = args.description or doc["description"]
    return doc


def build_zip(out: Path) -> Path:
    """Flat zip: main.py, requirements.txt, quote_agent/, quote_common/ and contracts/ at the root."""
    sources = [
        (AGENT_DIR / "main.py", "main.py"),
        (AGENT_DIR / "requirements.txt", "requirements.txt"),
        *[
            (p, f"quote_agent/{p.relative_to(AGENT_DIR / 'src' / 'quote_agent')}")
            for p in (AGENT_DIR / "src" / "quote_agent").rglob("*")
        ],
        *[
            (p, f"quote_common/{p.relative_to(SOLUTION / 'packages/quote_common/src/quote_common')}")
            for p in (SOLUTION / "packages/quote_common/src/quote_common").rglob("*")
        ],
        *[(p, f"contracts/{p.relative_to(SOLUTION / 'contracts')}") for p in (SOLUTION / "contracts").rglob("*")],
    ]
    out.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, name in sorted(sources, key=lambda s: s[1]):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, name)
    return out


def deploy(args: argparse.Namespace, doc: dict, zip_path: Path) -> None:
    """Create a version of the hosted agent from the zip (the only Azure call; never run by tests).

    With ``--wait``, poll until the version is active or failed; exit code 1 when it failed.
    """
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import CodeConfiguration, HostedAgentDefinition, ProtocolVersionRecord
    from azure.identity import DefaultAzureCredential

    d = doc["definition"]
    definition = HostedAgentDefinition(
        cpu=d["cpu"],
        memory=d["memory"],
        code_configuration=CodeConfiguration(**d["code_configuration"]),
        protocol_versions=[ProtocolVersionRecord(**p) for p in d["protocol_versions"]],
        environment_variables=d["environment_variables"],
    )
    with AIProjectClient(endpoint=args.project_endpoint, credential=DefaultAzureCredential()) as project:
        with zip_path.open("rb") as code:
            created = project.agents.create_version_from_code(
                args.agent_name, definition=definition, code=code, description=doc["description"]
            )
        print(f"created {created.name} version {created.version} (status {created.status})")
        while args.wait and created.status not in ("active", "failed"):
            time.sleep(5)
            created = project.agents.get_version(agent_name=args.agent_name, agent_version=created.version)
            print(f"status {created.status}")
        if created.status == "failed":
            sys.exit(1)


def main(argv: list[str] | None = None) -> None:
    """Resolve the options, render the definition, build the zip, then deploy it.

    ``--dry-run`` prints the resolved values (and the settings file used) and stops before any Azure call.
    """
    args = parse_args(argv)
    doc = render_definition(args)
    zip_path = build_zip(args.zip_out)
    if args.dry_run:
        shown = {"agent_name": args.agent_name, "settings": str(args.settings), **doc, "zip": str(zip_path)}
        print(json.dumps(shown, indent=2))
        return
    deploy(args, doc, zip_path)


if __name__ == "__main__":
    main()
