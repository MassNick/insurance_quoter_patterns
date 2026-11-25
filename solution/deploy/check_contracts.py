#!/usr/bin/env python3
"""Contract checks that need no running service (CI job `contracts`): the schemas are valid JSON Schema, every tool in
tools.json is well formed, the tool counts that the toolbox and the smoke test rely on still hold, and the prompts
packaged with the quote-agent are byte-identical to contracts/prompts.
Exit code 1 lists the problems."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

# the contracts folder of the solution
CONTRACTS = Path(__file__).resolve().parents[1] / "contracts"
# the quote-agent's packaged copies of contracts/prompts
PACKAGED_PROMPTS = Path(__file__).resolve().parents[1] / "agents/quote-agent/src/quote_agent/prompts"
# the values tools.json allows for result_mode
RESULT_MODES = {"terminal", "narrate"}
TOOLBOX_TOOLS = {"quoting": 6, "a2ui": 4}  # what create_toolbox.py puts behind the toolbox and smoke_test.py expects


def valid_schema(schema: object) -> str | None:
    """The error message when `schema` is not valid JSON Schema (2020-12), else None."""
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        return exc.message
    return None


def check(contracts: Path = CONTRACTS, packaged: Path = PACKAGED_PROMPTS) -> list[str]:
    """Every contract problem as a message; an empty list means all is well.

    Checks: each schema is valid, tools.json is well formed (unique names, known result modes, valid input
    schemas), the tool counts and toolbox servers match what the toolbox scripts expect, and the prompts
    packaged with the quote-agent are byte-identical to contracts/prompts.
    """
    problems = []
    for path in sorted(contracts.glob("*.schema.json")):
        if error := valid_schema(json.loads(path.read_text(encoding="utf-8"))):
            problems.append(f"{path.name}: {error}")
    catalog = json.loads((contracts / "state-catalog.json").read_text(encoding="utf-8"))
    if not catalog:
        problems.append("state-catalog.json is empty")
    doc = json.loads((contracts / "tools.json").read_text(encoding="utf-8"))
    names = [t["name"] for t in doc["tools"]]
    problems += [f"tools.json: duplicate tool {n}" for n in sorted({n for n in names if names.count(n) > 1})]
    for tool in doc["tools"]:
        if tool["result_mode"] not in RESULT_MODES:
            problems.append(f"tools.json: {tool['name']} has result_mode {tool['result_mode']!r}")
        if error := valid_schema(tool["inputSchema"]):
            problems.append(f"tools.json: {tool['name']} inputSchema: {error}")
    for server, expected in TOOLBOX_TOOLS.items():
        count = sum(n.startswith(f"{server}.") for n in names)
        if count != expected:
            problems.append(f"tools.json: {count} {server} tools, the toolbox scripts expect {expected}")
    labels = {s["server_label"] for s in doc["toolbox"]["servers"]}
    if labels != set(TOOLBOX_TOOLS):
        problems.append(f"tools.json: toolbox servers {sorted(labels)} differ from {sorted(TOOLBOX_TOOLS)}")
    for copy in sorted(packaged.glob("*.md")):
        source = contracts / "prompts" / copy.name
        if not source.exists() or source.read_bytes() != copy.read_bytes():
            problems.append(f"{copy.relative_to(packaged.parents[4])}: differs from contracts/prompts/{copy.name}")
    return problems


def main() -> int:
    """Print the problems and a verdict; exit code 1 when there are problems."""
    problems = check()
    for problem in problems:
        print(problem)
    print("contracts ok" if not problems else f"{len(problems)} problem(s)")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
