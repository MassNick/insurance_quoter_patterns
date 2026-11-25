"""The knowledge source and base requests, the ingestion wait and the defaults taken from settings.yaml."""

import json

import pytest
import yaml

import create_knowledge_base as kb

SEARCH = "https://srch.search.windows.net"
STORAGE = "/subscriptions/0/resourceGroups/rg/providers/Microsoft.Storage/storageAccounts/stkb"
# every required flag, with an embedding deployment and no planning one
ARGS = [
    "--search-endpoint", SEARCH, "--storage-id", STORAGE, "--openai-endpoint", "https://aif.openai.azure.com",
    "--embedding-deployment", "emb", "--embedding-model", "text-embedding-3-large",
]  # fmt: skip
# the flags of a planning deployment
PLANNING = ["--planning-deployment", "plan", "--planning-model", "gpt-x"]
# the required flags without any deployment
BASE = [
    "--search-endpoint", SEARCH, "--storage-id", STORAGE, "--openai-endpoint", "https://aif.openai.azure.com",
]  # fmt: skip
# a settings file with an embedding and a planning deployment
SETTINGS = """
deployments:
  emb-dep: {model: text-embedding-x, version: "1", sku: GlobalStandard, capacity: 10}
  plan-dep: {model: gpt-plan, version: "2", sku: GlobalStandard, capacity: 10}
knowledge:
  embedding_deployment: emb-dep
  planning_deployment: plan-dep
"""


@pytest.fixture(autouse=True)
def no_deployment_environment(monkeypatch):
    """Drop the environment variables that would take precedence over the settings file."""
    for name in ("EMBEDDING_DEPLOYMENT", "EMBEDDING_MODEL", "PLANNING_DEPLOYMENT", "PLANNING_MODEL"):
        monkeypatch.delenv(name, raising=False)


def settings_file(tmp_path, text=SETTINGS):
    """Flags that point the script at a temporary settings file with `text`."""
    path = tmp_path / "settings.yaml"
    path.write_text(text, encoding="utf-8")
    return ["--settings", str(path)]


def test_source_reads_the_container_keyless_and_ingests_privately():
    body = kb.build_source(STORAGE, "kb-docs", "https://aif.openai.azure.com", "emb", "text-embedding-3-large")
    params = body["azureBlobParameters"]
    assert body["kind"] == "azureBlob"
    assert params["connectionString"] == f"ResourceId={STORAGE}"  # no key, no SAS
    assert params["containerName"] == "kb-docs"
    assert params["ingestionParameters"]["networkAccessMode"] == "private"
    assert params["ingestionParameters"]["embeddingModel"]["azureOpenAIParameters"] == {
        "resourceUri": "https://aif.openai.azure.com", "deploymentId": "emb", "modelName": "text-embedding-3-large",
    }  # fmt: skip


def test_knowledge_base_plans_with_a_model_but_does_not_synthesize_answers():
    planning = kb.aoai("https://aif.openai.azure.com", "plan", "gpt-x")
    body = kb.build_knowledge_base(planning)
    assert body["knowledgeSources"] == [{"name": "kb-docs-source"}]
    assert body["models"] == [planning]
    assert body["retrievalReasoningEffort"] == {"kind": "low"}
    assert body["outputMode"] == "extractiveData"  # the main agent writes the answer


def test_knowledge_base_without_a_model_is_minimal():
    body = kb.build_knowledge_base(None)
    assert body["models"] == [] and body["retrievalReasoningEffort"] == {"kind": "minimal"}


def test_main_creates_the_source_then_the_knowledge_base(make_client):
    client, fake = make_client(
        {("PUT", "/knowledgesources/kb-docs-source"): {}, ("PUT", "/knowledgebases/auto-quoting-kb"): {}}, base_url=SEARCH
    )
    assert kb.main(ARGS + PLANNING, client=client) == 0
    assert [r.url.path for r in fake.requests] == ["/knowledgesources/kb-docs-source", "/knowledgebases/auto-quoting-kb"]
    assert {r.url.params["api-version"] for r in fake.requests} == {"2026-10-01"}
    assert json.loads(fake.requests[1].content)["models"][0]["azureOpenAIParameters"]["deploymentId"] == "plan"


def test_planning_model_name_is_required_with_a_planning_deployment():
    with pytest.raises(SystemExit, match="--planning-model"):
        kb.main(ARGS + ["--planning-deployment", "plan", "--dry-run"])


def test_dry_run_prints_two_requests(capsys):
    assert kb.main(ARGS + ["--dry-run"]) == 0
    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [line["method"] for line in lines] == ["PUT", "PUT"]


def status(end=None, failed=0):
    """A knowledge source status body: `end` is when the last synchronization ended, `failed` its failed items."""
    return {"lastSynchronizationState": {"endTime": end, "itemsUpdatesFailed": failed}}


def test_wait_polls_until_the_first_synchronization_ends(make_client):
    answers = iter([status(), status(), status("2026-11-25T10:00:00Z")])
    client, fake = make_client(
        {("GET", "/knowledgesources/kb-docs-source/status"): lambda r: __import__("httpx").Response(200, json=next(answers))},
        base_url=SEARCH,
    )
    sleeps = []
    result = kb.wait_for_ingestion(client, {"api-version": "2026-10-01"}, 600, sleep=sleeps.append)
    assert result["lastSynchronizationState"]["endTime"] and len(fake.requests) == 3 and sleeps == [15, 15]


def test_wait_fails_on_failed_items_and_on_timeout(make_client):
    client, _ = make_client({("GET", "/knowledgesources/kb-docs-source/status"): status("t", failed=2)}, base_url=SEARCH)
    with pytest.raises(RuntimeError, match="2 failed items"):
        kb.wait_for_ingestion(client, {}, 60, sleep=lambda s: None)
    client, _ = make_client({("GET", "/knowledgesources/kb-docs-source/status"): status()}, base_url=SEARCH)
    with pytest.raises(TimeoutError):
        kb.wait_for_ingestion(client, {}, 0, sleep=lambda s: None)


def test_the_default_settings_file_is_the_solution_root_one_and_names_the_deployments():
    args = kb.parse_args(BASE)
    root = yaml.safe_load(kb.SETTINGS.read_text(encoding="utf-8"))
    assert args.settings == kb.SETTINGS and kb.SETTINGS.name == "settings.yaml" and (kb.SETTINGS.parent / "contracts").is_dir()
    for role in ("embedding", "planning"):
        key = root["knowledge"].get(f"{role}_deployment")  # planning may be left out: a model-free base
        assert getattr(args, f"{role}_deployment") == key  # the deployment name is the key under `deployments`
        assert getattr(args, f"{role}_model") == (root["deployments"][key]["model"] if key else None)
    assert args.embedding_deployment in root["deployments"]


def test_a_settings_file_sets_both_deployments_and_their_model_names(tmp_path, capsys):
    args = kb.parse_args([*BASE, *settings_file(tmp_path)])
    assert (args.embedding_deployment, args.embedding_model) == ("emb-dep", "text-embedding-x")
    assert (args.planning_deployment, args.planning_model) == ("plan-dep", "gpt-plan")
    assert kb.main([*BASE, *settings_file(tmp_path), "--dry-run"]) == 0
    source, base = (json.loads(line)["body"] for line in capsys.readouterr().out.splitlines())
    embedding = source["azureBlobParameters"]["ingestionParameters"]["embeddingModel"]["azureOpenAIParameters"]
    assert (embedding["deploymentId"], embedding["modelName"]) == ("emb-dep", "text-embedding-x")
    planning = base["models"][0]["azureOpenAIParameters"]
    assert (planning["deploymentId"], planning["modelName"]) == ("plan-dep", "gpt-plan")


def test_flags_and_environment_variables_win_over_the_settings_file(tmp_path, monkeypatch):
    monkeypatch.setenv("PLANNING_DEPLOYMENT", "env-plan")
    monkeypatch.setenv("PLANNING_MODEL", "env-model")
    flags = ["--embedding-deployment", "flag-emb", "--embedding-model", "flag-model"]
    args = kb.parse_args([*BASE, *settings_file(tmp_path), *flags])
    assert (args.embedding_deployment, args.embedding_model) == ("flag-emb", "flag-model")
    assert (args.planning_deployment, args.planning_model) == ("env-plan", "env-model")


def test_a_deployment_outside_the_settings_file_needs_its_model_name(tmp_path):
    with pytest.raises(SystemExit, match="--embedding-model"):
        kb.main([*BASE, *settings_file(tmp_path), "--embedding-deployment", "custom", "--dry-run"])
    args = kb.parse_args([*BASE, *settings_file(tmp_path), "--embedding-deployment", "emb-dep"])
    assert args.embedding_model == "text-embedding-x"  # a key of `deployments`: its model is the default


def test_no_planning_deployment_in_the_settings_file_gives_a_model_free_base(tmp_path, capsys):
    only_embedding = SETTINGS.replace("  planning_deployment: plan-dep\n", "")
    assert kb.main([*BASE, *settings_file(tmp_path, only_embedding), "--dry-run"]) == 0
    base = json.loads(capsys.readouterr().out.splitlines()[1])["body"]
    assert base["models"] == [] and base["retrievalReasoningEffort"] == {"kind": "minimal"}
