"""Pytest setup for the end-to-end tests: puts this folder on ``sys.path`` so tests can import ``stack``."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))  # so tests import stack.py
