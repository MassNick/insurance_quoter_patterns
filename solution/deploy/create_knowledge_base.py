#!/usr/bin/env python3
"""Create the Foundry IQ knowledge source over the blob container `kb-docs` and the knowledge base `auto-quoting-kb` (plan 3.2).

Azure AI Search REST, GA contract 2026-10-01, Entra auth (the Search service has local auth disabled). The knowledge source
ingests privately (shared private links; Search S2 or higher). The knowledge base plans queries with a model but does not
synthesize the answer: the main agent writes it (outputMode extractiveData). Omit --planning-deployment for a model-free one.

  PUT {search}/knowledgesources/{name}?api-version=2026-10-01
  PUT {search}/knowledgebases/{name}?api-version=2026-10-01
  GET {search}/knowledgesources/{name}/status?api-version=2026-10-01      with --wait

The embedding and planning deployments and their model names default from settings.yaml (--settings): `knowledge.*_deployment`
names a key of `deployments`, and that entry's `model` is the model name. A flag or an environment variable wins.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from pathlib import Path

import yaml

from common import SEARCH_SCOPE, Client, add_common_args, env, require

SOURCE = "kb-docs-source"  # name of the knowledge source over the kb-docs container
KB = "auto-quoting-kb"  # name of the knowledge base
# default of --settings: the solution root file
SETTINGS = Path(__file__).resolve().parents[1] / "settings.yaml"


def aoai(endpoint: str, deployment: str, model: str) -> dict:
    """An `azureOpenAI` model entry (resource, deployment id, model name) for the source's embeddings or the base's planning."""
    return {
        "kind": "azureOpenAI",
        "azureOpenAIParameters": {"resourceUri": endpoint, "deploymentId": deployment, "modelName": model},
    }


def build_source(storage_id: str, container: str, openai_endpoint: str, embedding_deployment: str, embedding_model: str) -> dict:
    """The knowledge source body: the blob container read keylessly (the Search identity), ingested privately
    and embedded with `embedding_deployment`."""
    return {
        "name": SOURCE,
        "kind": "azureBlob",
        "description": "Coverage definitions, underwriting rules, discounts and FAQ (fr-CA and en-CA)",
        "azureBlobParameters": {
            "connectionString": f"ResourceId={storage_id}",  # keyless: the Search identity reads the container
            "containerName": container,
            "isADLSGen2": False,
            "ingestionParameters": {
                "networkAccessMode": "private",
                "embeddingModel": aoai(openai_endpoint, embedding_deployment, embedding_model),
                "contentExtractionMode": "minimal",
                "ingestionSchedule": None,
            },
        },
    }


def build_knowledge_base(planning: dict | None) -> dict:
    """The knowledge base body. With a `planning` model it plans queries (reasoning effort low), without one it is
    model-free (minimal). It only extracts data: the main agent writes the answer."""
    return {
        "name": KB,
        "description": "Grounded coverage explanations for the quote agent",
        "knowledgeSources": [{"name": SOURCE}],
        "models": [planning] if planning else [],
        "retrievalReasoningEffort": {"kind": "low" if planning else "minimal"},
        "outputMode": "extractiveData",
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the flags; deployments and model names left unset are filled from settings.yaml (`fill_from_settings`)."""
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_common_args(p)
    p.add_argument(
        "--settings",
        type=Path,
        default=SETTINGS,
        help="settings.yaml: the knowledge and deployments entries (default: %(default)s)",
    )
    p.add_argument(
        "--search-endpoint", default=env("SEARCH_ENDPOINT"), help="https://<search>.search.windows.net (env SEARCH_ENDPOINT)"
    )
    p.add_argument("--storage-id", default=env("KB_STORAGE_ID"), help="ARM id of the kb-docs storage account (env KB_STORAGE_ID)")
    p.add_argument("--container", default="kb-docs")
    p.add_argument(
        "--openai-endpoint", default=env("OPENAI_ENDPOINT"), help="https://<account>.openai.azure.com (env OPENAI_ENDPOINT)"
    )
    p.add_argument(
        "--embedding-deployment",
        default=env("EMBEDDING_DEPLOYMENT"),
        help="embedding deployment (env EMBEDDING_DEPLOYMENT; default: knowledge.embedding_deployment)",
    )
    p.add_argument(
        "--embedding-model",
        default=env("EMBEDDING_MODEL"),
        help="model name behind the deployment (env EMBEDDING_MODEL; default: its model in settings.yaml)",
    )
    p.add_argument(
        "--planning-deployment",
        default=env("PLANNING_DEPLOYMENT"),
        help="small chat deployment for query planning (env PLANNING_DEPLOYMENT; default: knowledge.planning_deployment, if set)",
    )
    p.add_argument(
        "--planning-model",
        default=env("PLANNING_MODEL"),
        help="model name behind it (env PLANNING_MODEL; default: its model in settings.yaml)",
    )
    p.add_argument("--api-version", default="2026-10-01")
    p.add_argument("--wait", type=int, default=0, metavar="SECONDS", help="poll the ingestion status for up to this long")
    args = p.parse_args(argv)
    fill_from_settings(args)
    return args


def fill_from_settings(args: argparse.Namespace) -> None:
    """Where no flag or environment variable gave them: the deployments named in `knowledge`, and the `model` of a deployment
    that is a key of `deployments` (the deployment name is that key). No planning deployment in the file: a model-free base."""
    cfg = yaml.safe_load(args.settings.read_text(encoding="utf-8")) or {}
    for role in ("embedding", "planning"):
        deployment = getattr(args, f"{role}_deployment") or (cfg.get("knowledge") or {}).get(f"{role}_deployment")
        model = getattr(args, f"{role}_model") or ((cfg.get("deployments") or {}).get(deployment) or {}).get("model")
        setattr(args, f"{role}_deployment", deployment)
        setattr(args, f"{role}_model", model)


def wait_for_ingestion(client: Client, params: dict, seconds: int, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Poll until the first synchronization has an end time. Raises on failed items or timeout."""
    deadline = time.monotonic() + seconds
    while True:
        status = client.request("GET", f"/knowledgesources/{SOURCE}/status", params=params)
        last = status.get("lastSynchronizationState") or {}
        if last.get("endTime"):
            if last.get("itemsUpdatesFailed"):
                raise RuntimeError(f"ingestion finished with {last['itemsUpdatesFailed']} failed items: {json.dumps(last)[:400]}")
            return status
        if time.monotonic() >= deadline:
            raise TimeoutError(f"knowledge source not synchronized after {seconds}s")
        sleep(15)


def main(argv: list[str] | None = None, client: Client | None = None, sleep: Callable[[float], None] = time.sleep) -> int:
    """Create the knowledge source, then the knowledge base; with `--wait`, poll the ingestion until it ends.

    `--dry-run` prints the two requests and sends nothing. `client` and `sleep` are for tests. Returns the exit code.
    """
    args = parse_args(argv)
    require(args, "search_endpoint", "storage_id", "openai_endpoint", "embedding_deployment", "embedding_model")
    if args.planning_deployment:
        require(args, "planning_model")
    client = client or Client(args.search_endpoint, SEARCH_SCOPE, dry_run=args.dry_run)
    params = {"api-version": args.api_version}

    source = build_source(args.storage_id, args.container, args.openai_endpoint, args.embedding_deployment, args.embedding_model)
    planning = aoai(args.openai_endpoint, args.planning_deployment, args.planning_model) if args.planning_deployment else None
    client.request("PUT", f"/knowledgesources/{SOURCE}", params=params, body=source)
    client.request("PUT", f"/knowledgebases/{KB}", params=params, body=build_knowledge_base(planning))
    if args.wait and not args.dry_run:
        wait_for_ingestion(client, params, args.wait, sleep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
