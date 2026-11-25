"""Where the contract files live, and loaders for them.

Resolution order for the contracts folder:
1. the ``QUOTE_CONTRACTS_DIR`` environment variable (container images copy ``contracts/`` and set it);
2. ``<solution>/contracts`` found by walking up from this file (editable installs in the repo).
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOG_FILE = "state-catalog.json"  # the file that marks a folder as the contracts folder


def contracts_dir() -> Path:
    """The contracts folder, resolved as the module docstring says. Raises ``FileNotFoundError`` when there is none."""
    env = os.environ.get("QUOTE_CONTRACTS_DIR")
    if env:
        path = Path(env)
        if not (path / CATALOG_FILE).is_file():
            msg = f"QUOTE_CONTRACTS_DIR={env} has no {CATALOG_FILE}"
            raise FileNotFoundError(msg)
        return path
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "contracts"
        if (candidate / CATALOG_FILE).is_file():
            return candidate
    msg = "contracts folder not found; set QUOTE_CONTRACTS_DIR"
    raise FileNotFoundError(msg)


def _load_json(name: str) -> Any:
    """Parse one JSON file of the contracts folder."""
    return json.loads((contracts_dir() / name).read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def load_catalog() -> dict[str, Any]:
    """The state catalog (``state-catalog.json``) as parsed JSON; cached."""
    return _load_json(CATALOG_FILE)


@lru_cache(maxsize=1)
def load_tools() -> dict[str, Any]:
    """The MCP tool contract (``tools.json``) as parsed JSON; cached."""
    return _load_json("tools.json")


@lru_cache(maxsize=8)
def load_schema(name: str) -> dict[str, Any]:
    """Load a JSON schema by file name, for example ``memory-output.schema.json``."""
    return _load_json(name)


def prompts_dir() -> Path:
    """The ``prompts`` folder of the contracts folder (the agents' instructions)."""
    return contracts_dir() / "prompts"
