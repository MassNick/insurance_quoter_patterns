"""Prompt assembly for both agents: static prefix, volatile context, inputs, cache keys and the token budget.

Cache layout (contracts/prompts/token-budget.md): the static prefix (system prompt + tool definitions + compact
key list) is byte-identical across turns and sessions; the append-only history comes next (the framework's
history provider loads it); the volatile context and the input come last. The volatile context is added by
``VolatileContext`` (agent.py) as a provider context message, so it is sent but never stored in history.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from agent_framework import CharacterEstimatorTokenizer
from quote_common import load_catalog, load_schema, load_tools

# copies of contracts/prompts packaged with the agent (deploy/check_contracts.py checks they match)
PROMPTS_DIR = Path(__file__).parent / "prompts"
# the static prefix (prompt + tools + key list) must stay under this; tests/unit/test_prompts.py enforces it
TOKEN_LIMIT = 30_000
TOKENIZER = CharacterEstimatorTokenizer()  # ~4 chars/token; re-measure with the deployed tokenizer in CI


def _prompt(name: str) -> str:
    """A prompt file of this package with its HTML comments stripped."""
    raw = (PROMPTS_DIR / name).read_text(encoding="utf-8")
    return re.sub(r"<!--.*?-->", "", raw, flags=re.S).strip()


@lru_cache(maxsize=1)
def key_list() -> str:
    """Compact catalog key list: ``entity.key:type[codes]``, labels dropped."""
    lines = []
    for f in load_catalog()["fields"]:
        codes = f"[{'|'.join(code for code, _ in f['enum'])}]" if f.get("enum") else ""
        lines.append(f"{f['entity']}.{f['key']}:{f['type']}{codes}")
    return "\n".join(lines)


@lru_cache(maxsize=2)
def instructions(agent: str) -> str:
    """Static system prompt for ``main`` or ``memory``: prompt text, then the key list."""
    return f"{_prompt(f'{agent}-agent.md')}\n\n# State keys (entity.key:type[codes])\n{key_list()}"


def _inline(node: Any, schema: dict[str, Any]) -> Any:
    """Copy of a JSON schema node with every ``$ref`` to form-spec.schema.json replaced by ``schema``."""
    if isinstance(node, dict):
        if node.get("$ref") == "form-spec.schema.json":
            return schema
        return {k: _inline(v, schema) for k, v in node.items()}
    return [_inline(v, schema) for v in node] if isinstance(node, list) else node


@lru_cache(maxsize=1)
def tool_definitions() -> list[dict[str, Any]]:
    """The 13 model-visible tool definitions (wire name, description, parameters), FormSpec schema inlined
    (its ``$defs`` moved to the parameters root so ``#/$defs/field`` still resolves)."""
    schema = load_schema("form-spec.schema.json")
    form_spec = {k: v for k, v in schema.items() if k not in ("$schema", "$id", "$defs")}
    definitions = []
    for t in load_tools()["tools"]:
        params = _inline(t["inputSchema"], form_spec)
        if params != t["inputSchema"]:
            params = {**params, "$defs": schema["$defs"]}
        definitions.append({"name": t["wire_name"], "description": t["description"], "parameters": params})
    return definitions


def static_prefix_tokens() -> int:
    """Main agent static prefix: prompt + tool definitions + key list. Must stay under TOKEN_LIMIT."""
    tools = "".join(json.dumps(t, ensure_ascii=False, separators=(",", ":")) for t in tool_definitions())
    return TOKENIZER.count_tokens(instructions("main")) + TOKENIZER.count_tokens(tools)


def cache_key(agent: str, prompt_version: str, session_id: str) -> str:
    """``prompt_cache_key`` of ``agent`` (``main`` or ``memory``): one per prompt version and session."""
    return f"quote-{agent}:{prompt_version}:{session_id}"


@lru_cache(maxsize=1)
def memory_response_format(schema_file: str) -> dict[str, Any]:
    """Structured output from ``contracts/<schema_file>`` (``$schema`` and ``$id`` removed).

    Not strict: a change's ``value`` is any JSON (string, number, boolean, null, array or object), which strict mode
    cannot express (the Responses API rejects it with ``invalid_json_schema``). The runtime validates every output
    against the same schema and reports a mismatch as a memory error.
    """
    schema = {k: v for k, v in load_schema(schema_file).items() if k not in ("$schema", "$id")}
    return {"type": "json_schema", "json_schema": {"name": "MemoryAgentOutput", "schema": schema, "strict": False}}


def compact_state(state: dict[str, Any], prefix: str = "") -> list[str]:
    """Non-empty fields as ``path=<json value>~source/status`` lines."""
    lines: list[str] = []
    items = enumerate(state) if isinstance(state, list) else state.items()
    for key, node in items:
        path = f"{prefix}/{key}"
        if isinstance(node, dict) and "value" in node and "source" in node:
            if node["value"] not in (None, "", []):
                value = json.dumps(node["value"], ensure_ascii=False)
                lines.append(f"{path}={value}~{node['source']}/{node.get('status', '')}")
        elif isinstance(node, dict | list):
            lines.extend(compact_state(node, path))
        elif node not in (None, ""):
            lines.append(f"{path}={json.dumps(node, ensure_ascii=False)}")
    return lines


def context_message(req: Any) -> str:
    """Volatile context for the main agent, sent after the history and before INPUT."""
    dump = lambda v: json.dumps(v, ensure_ascii=False, separators=(",", ":"))  # noqa: E731
    return "\n".join(
        [
            "STATE",
            *compact_state(req.state),
            f"PHASE {req.phase}",
            f"OPEN_SURFACES {dump(req.open_surfaces)}",
            f"SIGNALS {dump(req.signals)}",
            f"PREFERENCES {dump(req.preferences)}",
            f"RULES {dump(req.rule_hits)}",
        ]
    )


def input_summary(inp: Any) -> str:
    """One-line text for a non-message input (memory agent input and main agent INPUT)."""
    match inp.kind:
        case "form_submitted":
            return f"Form {inp.surface_id} submitted: {len(inp.changed_paths)} fields"
        case "action":
            return f"Button {inp.action_id} clicked"
        case "document_extracted":
            return f"Document {inp.purpose} read: {len(inp.changed_paths)} fields"
        case "confirmation_result":
            return f"Confirmation {'accepted' if inp.accepted else 'rejected'}: {', '.join(inp.changed_paths)}"
    return inp.kind


def main_input(inp: Any, pre_executed: tuple[str, Any] | None = None) -> str:
    """The main agent's INPUT block: the text of a message, otherwise a one-line summary and the input's fields,
    plus the ``PRE_EXECUTED`` quote result of a button action."""
    if inp.kind == "message":
        return f"INPUT kind=message\n{inp.text}"
    details = inp.model_dump(exclude={"kind"}, exclude_none=True)
    text = f"INPUT kind={inp.kind}\n{input_summary(inp)}\n{json.dumps(details, ensure_ascii=False)}"
    if pre_executed:
        text += f"\nPRE_EXECUTED {pre_executed[0]}\n{json.dumps(pre_executed[1], ensure_ascii=False)}"
    return text


def memory_input(req: Any, last_assistant: str) -> str:
    """The memory agent's whole input: the last assistant message, the INPUT and the compact STATE."""
    body = req.input.text if req.input.kind == "message" else input_summary(req.input)
    return "\n".join(
        [
            "LAST_ASSISTANT_MESSAGE",
            last_assistant or "(none)",
            "",
            f"INPUT kind={req.input.kind}",
            body,
            "",
            "STATE",
            *compact_state(req.state),
        ]
    )
