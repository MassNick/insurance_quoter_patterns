"""check_contracts.py: the real contracts pass and each kind of problem is reported."""

import json
import shutil
from pathlib import Path

import check_contracts as cc

# the real contracts folder of the solution
REAL = Path(cc.CONTRACTS)


def copy_contracts(tmp_path: Path) -> Path:
    """A temporary copy of the real contracts folder, to break on purpose."""
    dest = tmp_path / "contracts"
    shutil.copytree(REAL, dest)
    return dest


def test_the_real_contracts_pass():
    assert cc.check() == []


def test_a_broken_schema_is_reported(tmp_path):
    dest = copy_contracts(tmp_path)
    (dest / "form-spec.schema.json").write_text(json.dumps({"type": "nonsense"}))
    assert any("form-spec.schema.json" in p for p in cc.check(dest))


def test_a_bad_result_mode_a_duplicate_and_a_missing_server_tool_are_reported(tmp_path):
    dest = copy_contracts(tmp_path)
    doc = json.loads((dest / "tools.json").read_text())
    doc["tools"][0]["result_mode"] = "sometimes"
    doc["tools"].append(dict(doc["tools"][1]))
    doc["tools"] = [t for t in doc["tools"] if t["name"] != "a2ui.close_form"]
    (dest / "tools.json").write_text(json.dumps(doc))
    problems = "\n".join(cc.check(dest))
    assert "result_mode 'sometimes'" in problems
    assert "duplicate tool" in problems
    assert "3 a2ui tools" in problems


def test_a_packaged_prompt_that_drifted_from_the_contract_is_reported(tmp_path):
    packaged = tmp_path / "agents/quote-agent/src/quote_agent/prompts"
    shutil.copytree(cc.PACKAGED_PROMPTS, packaged)
    assert cc.check(packaged=packaged) == []
    (packaged / "main-agent.md").write_text("edited here only")
    assert cc.check(packaged=packaged) == [
        "agents/quote-agent/src/quote_agent/prompts/main-agent.md: differs from contracts/prompts/main-agent.md"]
