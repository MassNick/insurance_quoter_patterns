"""post_deploy.sh in dry-run mode over a saved Terraform output: its steps, requests and agent commands."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

DEPLOY = Path(__file__).resolve().parents[1]
SCRIPT = DEPLOY / "post_deploy.sh"
# a saved `terraform output -json`
FIXTURE = DEPLOY / "tests" / "fixtures" / "tf-output.json"
SETTINGS = DEPLOY.parent / "settings.yaml"

pytestmark = pytest.mark.skipif(not (shutil.which("bash") and shutil.which("jq")), reason="needs bash and jq")


def run(*args: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Run the script with a minimal environment (this Python for the scripts, a fixed prompt version)."""
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin",
        "PYTHON": sys.executable,
        "PROMPT_VERSION": "test",
        **(env_extra or {}),
    }
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True, env=env, check=False)


def test_help_lists_the_steps():
    result = run("--help")
    assert result.returncode == 0
    for step in ("approve", "knowledge-base", "toolbox", "agents", "identity", "smoke", "--dry-run", "--tf-output"):
        assert step in result.stdout


def test_unknown_step_and_option_are_usage_errors():
    assert run("--dry-run", "--tf-output", str(FIXTURE), "nonsense").returncode == 2
    assert run("--bogus").returncode == 2


def test_dry_run_prints_every_request_from_the_terraform_outputs():
    result = run("--dry-run", "--tf-output", str(FIXTURE))
    assert result.returncode == 0, result.stderr
    requests = [json.loads(line) for line in result.stdout.splitlines() if line.startswith("{")]
    urls = [(r["method"], r["url"]) for r in requests]
    out = json.loads(FIXTURE.read_text())
    search = out["search_endpoint"]["value"]
    project = out["foundry_project_endpoint"]["value"]
    assert ("PUT", f"{search}/knowledgesources/kb-docs-source") in urls
    assert ("PUT", f"{search}/knowledgebases/auto-quoting-kb") in urls
    assert ("POST", f"{project}/toolboxes/auto-quoting-toolbox/versions") in urls
    assert ("PATCH", f"{project}/toolboxes/auto-quoting-toolbox") in urls
    assert any(u.endswith("/api/health") for _, u in urls)  # smoke goes through Front Door
    toolbox_entries = [r for r in requests if r["url"].endswith("/versions")][0]["body"]["tools"]
    assert [t["server_url"].split("//")[1].split(".")[0] for t in toolbox_entries] == [
        "ca-mcp-quoting-aiq-dev-cc",
        "ca-mcp-a2ui-aiq-dev-cc",
    ]


def test_dry_run_agent_commands_use_the_deploy_scripts_flags():
    result = run("--dry-run", "--tf-output", str(FIXTURE), "agents")
    lines = [line for line in result.stdout.splitlines() if line.startswith("+ uv run")]
    assert len(lines) == 2
    quote, doc = lines
    roles = json.loads(FIXTURE.read_text())["model_deployments"]["value"]  # the deployments follow the Terraform role map
    assert "deploy_quote_agent.py" in quote
    assert f"--main-model-deployment {roles['main']['deployment']}" in quote
    assert f"--memory-model-deployment {roles['memory']['deployment']}" in quote
    assert "--toolbox-mcp-url" in quote and "--foundry-iq-mcp-url" in quote and "--prompt-version test" in quote
    assert "deploy_document_agent.py" in doc and f"--model {roles['document']['deployment']}" in doc


def test_dry_run_passes_no_flag_that_settings_yaml_provides():
    result = run("--dry-run", "--tf-output", str(FIXTURE), "agents")
    commands = " ".join(line for line in result.stdout.splitlines() if line.startswith("+ uv run"))
    for flag in ("reasoning-effort", "max-output-tokens", "timeout-seconds", "output-schema", "--settings"):
        assert flag not in commands


def test_dry_run_knowledge_base_uses_the_deployments_of_the_terraform_role_map():
    result = run("--dry-run", "--tf-output", str(FIXTURE), "knowledge-base")
    assert result.returncode == 0, result.stderr
    source, base = (json.loads(line)["body"] for line in result.stdout.splitlines() if line.startswith("{"))
    roles = json.loads(FIXTURE.read_text())["model_deployments"]["value"]
    embedding = source["azureBlobParameters"]["ingestionParameters"]["embeddingModel"]["azureOpenAIParameters"]
    planning = base["models"][0]["azureOpenAIParameters"]
    assert (embedding["deploymentId"], embedding["modelName"]) == tuple(roles["embedding"].values())
    assert (planning["deploymentId"], planning["modelName"]) == tuple(roles["memory"].values())


def test_the_knowledge_base_planner_post_deploy_passes_is_the_one_settings_yaml_names():
    """Terraform's role map has main, memory, document and embedding, built from settings.yaml; post_deploy.sh takes the
    planning model of the knowledge base from the memory role. If knowledge.planning_deployment points elsewhere, change
    the PLANNING_* lines of post_deploy.sh (they would override the file)."""
    settings = yaml.safe_load(SETTINGS.read_text(encoding="utf-8"))
    planning = settings["knowledge"].get("planning_deployment")
    assert planning in (None, settings["agents"]["memory"]["deployment"])


def test_approve_step_lists_each_private_link_target():
    result = run("--dry-run", "--tf-output", str(FIXTURE), "approve")
    assert result.stdout.count("az network private-endpoint-connection list") == 2


def test_refuses_to_run_before_the_container_apps_exist(tmp_path):
    outputs = json.loads(FIXTURE.read_text())
    outputs["app_fqdns"]["value"] = {}
    saved = tmp_path / "tf.json"
    saved.write_text(json.dumps(outputs))
    result = run("--dry-run", "--tf-output", str(saved))
    assert result.returncode == 1
    assert "not deployed" in result.stderr
