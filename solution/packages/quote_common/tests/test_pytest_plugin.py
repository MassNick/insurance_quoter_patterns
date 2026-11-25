"""The ``azure`` test level (quote_common.pytest_plugin): skipped by default, run with ``RUN_AZURE_TESTS=1``."""

import os
import subprocess
import sys

TEST = "import pytest\n\n@pytest.mark.azure\ndef test_real():\n    pass\n"  # a test file with one azure test


def run(tmp_path, **env):
    """Run pytest on that test file inside ``tmp_path``, with ``env`` set; returns pytest's output."""
    (tmp_path / "test_x.py").write_text(TEST)
    out = subprocess.run([sys.executable, "-m", "pytest", "-q", "-rs", "-p", "no:cacheprovider", str(tmp_path)],
                         capture_output=True, text=True, check=False, env={**os.environ, "RUN_AZURE_TESTS": "", **env}, cwd=tmp_path)
    return out.stdout


def test_azure_tests_are_skipped_unless_asked(tmp_path):
    assert "1 skipped" in run(tmp_path) and "RUN_AZURE_TESTS=1" in run(tmp_path)
    assert "1 passed" in run(tmp_path, RUN_AZURE_TESTS="1")
