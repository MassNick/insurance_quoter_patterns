"""aca_rollout.sh in dry-run mode: the `az` commands it would run for a release, a finish and a rollback."""

import shutil
import subprocess
from pathlib import Path

import pytest

# the shell script under test
SCRIPT = Path(__file__).resolve().parents[1] / "aca_rollout.sh"
pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="needs bash")


def run(*args: str) -> subprocess.CompletedProcess:
    """Run the script with `--dry-run` and `args`."""
    return subprocess.run(["bash", str(SCRIPT), "--dry-run", *args], capture_output=True, text=True, check=False)


def test_canary_release_pins_traffic_creates_the_revision_then_splits():
    result = run("release", "-g", "rg", "ca-bff", "acr.azurecr.io/bff:abc", "10")
    steps = [line for line in result.stderr.splitlines() if line.startswith("+ az")]
    assert result.returncode == 0
    assert "--revision-weight <current>=100" in steps[0]  # stop following the latest revision first
    assert "containerapp update -n ca-bff -g rg --image acr.azurecr.io/bff:abc --revision-suffix r" in steps[1]
    assert steps[2].endswith("=10 <current>=90")
    previous, new = result.stdout.split()
    assert previous == "<current>" and new.startswith("ca-bff--r")


def test_default_is_a_full_cut_over():
    steps = [line for line in run("release", "-g", "rg", "ca-mcp-quoting", "img").stderr.splitlines() if line.startswith("+ az")]
    assert steps[-1].endswith("=100") and "<current>=" not in steps[-1]


def test_finish_promotes_the_new_revision_and_retires_the_old_one():
    steps = run("finish", "-g", "rg", "app", "app--r2", "app--r1").stderr.splitlines()
    assert steps[0].endswith("--revision-weight app--r2=100")
    assert steps[1].endswith("revision deactivate -n app -g rg --revision app--r1")


def test_rollback_restores_the_previous_revision_and_retires_the_new_one():
    steps = run("rollback", "-g", "rg", "app", "app--r2", "app--r1").stderr.splitlines()
    assert steps[0].endswith("--revision-weight app--r1=100")
    assert steps[1].endswith("revision deactivate -n app -g rg --revision app--r2")


def test_same_revision_is_a_no_op():
    result = run("finish", "-g", "rg", "app", "app--r1", "app--r1")
    assert result.returncode == 0 and "nothing to finish" in result.stderr


def test_usage_errors_exit_2():
    assert run("bogus").returncode == 2
    assert run("release", "app", "img").returncode == 2
