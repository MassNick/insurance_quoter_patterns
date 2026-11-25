"""The deploy script's definition, zip and settings defaults (dry run only: no Azure call)."""

import importlib.util
import json
import zipfile
from pathlib import Path

import pytest
import yaml

pytestmark = pytest.mark.unit
SCRIPT = Path(__file__).resolve().parents[2] / "deploy" / "deploy_quote_agent.py"
# the script is not an importable package module: load it from its path
spec = importlib.util.spec_from_file_location("deploy_quote_agent", SCRIPT)
deploy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(deploy)
# the one flag the script always needs
ENDPOINT = ["--project-endpoint", "https://dev-only.services.ai.azure.com/api/projects/dev-only"]
# everything required when the settings file gives nothing
REQUIRED = [*ENDPOINT, "--main-model-deployment", "dev-only-main", "--memory-model-deployment", "dev-only-memory"]
# a settings file that differs from the real one in every key the script reads
CUSTOM_SETTINGS = """
agents:
  main: {deployment: file-main, reasoning_effort: high, max_output_tokens: 1111}
  memory:
    deployment: file-memory
    reasoning_effort: minimal
    max_output_tokens: 222
    timeout_seconds: 7
    output_schema: document-output.schema.json  # another contract file, to see that it is used
"""


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch):
    """Drop the environment variables that would override the settings file or the flags."""
    for name in deploy.FROM_SETTINGS:
        monkeypatch.delenv(name, raising=False)


def settings_file(tmp_path, text=CUSTOM_SETTINGS):
    """Flags that point the script at a temporary settings file with ``text``."""
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--settings", str(path)]


def env_of(argv):
    """The environment variables of the definition rendered for ``argv``."""
    return deploy.render_definition(deploy.parse_args(argv))["definition"]["environment_variables"]


def test_definition_has_runtime_size_protocol_and_env_without_empties():
    doc = deploy.render_definition(deploy.parse_args([*REQUIRED, "--main-reasoning-effort", "low"]))
    d = doc["definition"]
    assert (d["cpu"], d["memory"]) == ("1", "2Gi")
    assert d["code_configuration"] == {
        "runtime": "python_3_13",
        "entry_point": ["python", "main.py"],
        "dependency_resolution": "remote_build",
    }
    assert d["protocol_versions"] == [{"protocol": "invocations", "version": "2.0.0"}]
    env = d["environment_variables"]
    assert env["QUOTE_AGENT_MODEL"] == "azure" and env["PORT"] == "8088" and env["MAIN_REASONING_EFFORT"] == "low"
    assert "TOOLBOX_MCP_URL" not in env and "MAIN_TEMPERATURE" not in env  # empty options are dropped
    assert not any(k.endswith(("KEY", "SECRET", "PASSWORD")) for k in env)


def test_zip_is_flat_and_carries_package_common_and_contracts(tmp_path):
    path = deploy.build_zip(tmp_path / "quote-agent.zip")
    names = set(zipfile.ZipFile(path).namelist())
    assert {
        "main.py",
        "requirements.txt",
        "quote_agent/app.py",
        "quote_agent/prompts/main-agent.md",
        "quote_common/contracts.py",
        "contracts/tools.json",
        "contracts/state-catalog.json",
        "contracts/memory-output.schema.json",  # the runtime loads MEMORY_OUTPUT_SCHEMA from contracts/
    } <= names
    assert not any("__pycache__" in n or ".venv" in n for n in names)


def test_hosted_agent_definition_model_accepts_the_definition():
    models = pytest.importorskip("azure.ai.projects.models")
    d = deploy.render_definition(deploy.parse_args(REQUIRED))["definition"]
    hosted = models.HostedAgentDefinition(
        cpu=d["cpu"],
        memory=d["memory"],
        code_configuration=models.CodeConfiguration(**d["code_configuration"]),
        protocol_versions=[models.ProtocolVersionRecord(**p) for p in d["protocol_versions"]],
        environment_variables=d["environment_variables"],
    )
    assert hosted.as_dict()["kind"] == "hosted"


def test_the_default_settings_file_is_the_solution_root_one_and_sets_the_agents():
    args = deploy.parse_args(ENDPOINT)
    root = yaml.safe_load((deploy.SOLUTION / "settings.yaml").read_text(encoding="utf-8"))
    assert args.settings == deploy.SOLUTION / "settings.yaml"
    env = env_of(ENDPOINT)
    for placeholder, (agent, key) in deploy.FROM_SETTINGS.items():
        if key in root["agents"][agent]:  # every key the file sets reaches the hosted agent's environment
            assert env[placeholder] == str(root["agents"][agent][key])
    for agent in ("main", "memory"):
        assert (
            root["agents"][agent]["deployment"] in root["deployments"]
        )  # the deployment name is a key under `deployments`
    assert (deploy.SOLUTION / "contracts" / env["MEMORY_OUTPUT_SCHEMA"]).is_file()


def test_a_settings_file_sets_the_models_thinking_limits_timeout_and_output_schema(tmp_path):
    env = env_of([*ENDPOINT, *settings_file(tmp_path)])
    assert (env["MAIN_MODEL_DEPLOYMENT"], env["MEMORY_MODEL_DEPLOYMENT"]) == ("file-main", "file-memory")
    assert (env["MAIN_REASONING_EFFORT"], env["MAIN_MAX_OUTPUT_TOKENS"]) == ("high", "1111")
    assert (env["MEMORY_REASONING_EFFORT"], env["MEMORY_MAX_OUTPUT_TOKENS"]) == ("minimal", "222")
    assert (env["MEMORY_TIMEOUT_SECONDS"], env["MEMORY_OUTPUT_SCHEMA"]) == ("7", "document-output.schema.json")
    assert "MAIN_TEMPERATURE" not in env  # not a settings key: still empty and dropped


def test_a_flag_wins_over_the_environment_which_wins_over_the_settings_file(tmp_path, monkeypatch):
    argv = [*ENDPOINT, *settings_file(tmp_path)]
    monkeypatch.setenv("MAIN_REASONING_EFFORT", "medium")
    monkeypatch.setenv("MEMORY_TIMEOUT_SECONDS", "9")
    env = env_of([*argv, "--memory-timeout-seconds", "5", "--main-model-deployment", "flag-main"])
    assert env["MAIN_REASONING_EFFORT"] == "medium"  # the environment beat the file
    assert env["MEMORY_TIMEOUT_SECONDS"] == "5"  # the flag beat the environment
    assert env["MAIN_MODEL_DEPLOYMENT"] == "flag-main"  # the flag beat the file
    assert env["MEMORY_MODEL_DEPLOYMENT"] == "file-memory" and env["MAIN_MAX_OUTPUT_TOKENS"] == "1111"
    assert "MAIN_MAX_OUTPUT_TOKENS" not in env_of([*argv, "--main-max-output-tokens", ""])  # an empty flag: unset


def test_without_settings_values_the_code_defaults_apply_and_the_deployments_are_required(tmp_path):
    argv = [*ENDPOINT, *settings_file(tmp_path, "agents: {}\n")]
    with pytest.raises(SystemExit) as exc:
        deploy.parse_args(argv)
    assert exc.value.code == 2  # no deployment from a flag, the environment or the file
    env = env_of([*REQUIRED, *settings_file(tmp_path, "agents: {}\n")])
    assert env["MEMORY_OUTPUT_SCHEMA"] == "memory-output.schema.json" and env["MEMORY_TIMEOUT_SECONDS"] == "4"
    assert "MAIN_REASONING_EFFORT" not in env and "MEMORY_MAX_OUTPUT_TOKENS" not in env


def test_an_output_schema_that_is_not_a_contract_file_is_a_usage_error():
    with pytest.raises(SystemExit) as exc:
        deploy.parse_args([*REQUIRED, "--memory-output-schema", "nope.schema.json"])
    assert exc.value.code == 2


def test_dry_run_prints_the_resolved_values(tmp_path, capsys):
    path = settings_file(tmp_path)
    deploy.main([*ENDPOINT, *path, "--zip-out", str(tmp_path / "quote-agent.zip"), "--dry-run"])
    printed = json.loads(capsys.readouterr().out)
    env = printed["definition"]["environment_variables"]
    assert printed["settings"] == path[1]
    assert env["MAIN_MODEL_DEPLOYMENT"] == "file-main" and env["MAIN_REASONING_EFFORT"] == "high"
    assert env["MEMORY_TIMEOUT_SECONDS"] == "7" and env["MEMORY_OUTPUT_SCHEMA"] == "document-output.schema.json"
