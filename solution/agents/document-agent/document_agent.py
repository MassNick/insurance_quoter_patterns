"""Definition of the Foundry prompt agent ``document-agent`` (architecture §4.3, implementation plan Phase 7).

The BFF is the only caller (Responses protocol, agent reference); the main agent never sees documents.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from quote_common import load_schema

DEFAULT_SCHEMA = "document-output.schema.json"  # a file in contracts/; the deploy script takes it from settings.yaml
# copy of contracts/prompts/document-agent.md
PROMPT = Path(__file__).parent / "prompts" / "document-agent.md"


def instructions() -> str:
    """The agent's instructions: prompts/document-agent.md (a copy of the contract prompt) without its HTML comments."""
    return re.sub(r"<!--.*?-->", "", PROMPT.read_text(encoding="utf-8"), flags=re.S).strip()


def _nullable(prop: dict[str, Any]) -> dict[str, Any]:
    """A property schema that also accepts ``null`` (strict mode requires every property; ``null`` means not read)."""
    if "type" not in prop:
        return {"anyOf": [prop, {"type": "null"}]}
    types = prop["type"] if isinstance(prop["type"], list) else [prop["type"]]
    return {**prop, "type": [*types, "null"]}


def _strict(node: Any) -> Any:
    """Copy of a JSON schema in strict-output form: no ``$schema``, ``$id`` or ``field_map``; every property
    required (optional ones nullable); no additional properties."""
    if isinstance(node, list):
        return [_strict(v) for v in node]
    if not isinstance(node, dict):
        return node
    out = {k: _strict(v) for k, v in node.items() if k not in ("$schema", "$id", "field_map")}
    if "properties" in out:
        required = set(out.get("required", []))
        out["properties"] = {k: v if k in required else _nullable(v) for k, v in out["properties"].items()}
        out["required"] = list(out["properties"])
        out["additionalProperties"] = False
    return out


def strict_schema(schema_file: str = DEFAULT_SCHEMA) -> dict[str, Any]:
    """``contracts/<schema_file>`` for strict structured output: every property required,
    optional ones nullable (null = not read). ``strip_nulls`` turns an output back into the contract shape."""
    return _strict(load_schema(schema_file))


def strip_nulls(value: Any) -> Any:
    """Drop every ``null`` from a strict output to get the contract shape back (``null`` meant not read)."""
    if isinstance(value, dict):
        return {k: strip_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [strip_nulls(v) for v in value]
    return value


def build_definition(model: str, reasoning_effort: str = "", output_schema: str = DEFAULT_SCHEMA) -> dict[str, Any]:
    """PromptAgentDefinition payload (azure.ai.projects.models, kind=prompt). ``model`` is the deployment name;
    an empty ``reasoning_effort`` leaves the model's default."""
    payload: dict[str, Any] = {
        "kind": "prompt",
        "model": model,
        "instructions": instructions(),
        "text": {
            "format": {
                "type": "json_schema",
                "name": "DocumentAgentOutput",
                "schema": strict_schema(output_schema),
                "strict": True,
            }
        },
    }
    if reasoning_effort:
        payload["reasoning"] = {"effort": reasoning_effort}
    return payload
