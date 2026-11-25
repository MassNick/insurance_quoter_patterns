"""Create a new version of the Foundry prompt agent ``document-agent``. Never run by tests or CI.

The model deployment, reasoning effort and output schema default from the ``agents.document`` entry of settings.yaml
(``--settings``, the solution root by default); a flag wins.

    python deploy_document_agent.py --project-endpoint https://<resource>.services.ai.azure.com/api/projects/<p> \
        [--model <deployment>] [--reasoning-effort low] [--output-schema document-output.schema.json] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

from document_agent import DEFAULT_SCHEMA, build_definition

# default of --settings: the solution root file
SETTINGS = Path(__file__).resolve().parents[2] / "settings.yaml"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags. The settings file is read first because it gives the defaults (``agents.document``);
    a flag wins over it."""
    settings = argparse.ArgumentParser(add_help=False)  # parsed first: it decides the defaults of the other options
    settings.add_argument("--settings", type=Path, default=SETTINGS, help="Settings file (default: %(default)s)")
    path = settings.parse_known_args(argv)[0].settings
    entry = ((yaml.safe_load(path.read_text(encoding="utf-8")) or {}).get("agents") or {}).get("document") or {}
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], parents=[settings])
    parser.add_argument("--project-endpoint", required=True, help="Foundry project endpoint")
    parser.add_argument("--agent-name", default="document-agent", help="Foundry agent name")
    parser.add_argument(
        "--model",
        default=entry.get("deployment"),
        required=not entry.get("deployment"),
        help="Vision model deployment name (default: settings.yaml agents.document.deployment)",
    )
    parser.add_argument(
        "--reasoning-effort",
        default=str(entry.get("reasoning_effort") or ""),
        help="Thinking level, empty = the model's default (default: settings.yaml agents.document.reasoning_effort)",
    )
    parser.add_argument(
        "--output-schema",
        default=entry.get("output_schema") or DEFAULT_SCHEMA,
        help="Structured output: a file in contracts/ (default: settings.yaml agents.document.output_schema)",
    )
    parser.add_argument("--description", default="Reads one insurance document and returns strict JSON fields")
    parser.add_argument("--dry-run", action="store_true", help="Print the definition only; no Azure call")
    return parser.parse_args(argv)


def sdk_definition(payload: dict) -> object:
    """The ``build_definition`` payload as the SDK's PromptAgentDefinition."""
    from azure.ai.projects.models import (
        PromptAgentDefinition,
        PromptAgentDefinitionTextOptions,
        Reasoning,
        TextResponseFormatJsonSchema,
    )

    fmt = payload["text"]["format"]
    return PromptAgentDefinition(
        model=payload["model"],
        instructions=payload["instructions"],
        text=PromptAgentDefinitionTextOptions(
            format=TextResponseFormatJsonSchema(name=fmt["name"], schema=fmt["schema"], strict=True)
        ),
        reasoning=Reasoning(**payload["reasoning"]) if "reasoning" in payload else None,
    )


def main(argv: list[str] | None = None) -> None:
    """Build the agent definition and create a version of it in Foundry (the only Azure call).

    ``--dry-run`` prints the resolved definition and the settings file used, and calls nothing.
    """
    args = parse_args(argv)
    payload = build_definition(args.model, args.reasoning_effort, args.output_schema)
    if args.dry_run:
        shown = {
            "agent_name": args.agent_name,
            "settings": str(args.settings),
            "output_schema": args.output_schema,
            "definition": payload,
        }
        print(json.dumps(shown, indent=2, ensure_ascii=False))
        return
    from azure.ai.projects import AIProjectClient
    from azure.identity import DefaultAzureCredential

    with AIProjectClient(endpoint=args.project_endpoint, credential=DefaultAzureCredential()) as project:
        created = project.agents.create_version(
            args.agent_name, definition=sdk_definition(payload), description=args.description
        )
        print(f"created {created.name} version {created.version}")


if __name__ == "__main__":
    main()
