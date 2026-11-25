"""Shared helpers for the auto quoting services (BFF, hosted agent, MCP servers).

Keep this package small and stable: every Python service depends on it.
"""

from quote_common.catalog import Catalog, get_catalog
from quote_common.contracts import contracts_dir, load_catalog, load_schema, load_tools
from quote_common.envelope import Envelope, sse_decode, sse_encode

__all__ = [
    "Catalog",
    "Envelope",
    "contracts_dir",
    "get_catalog",
    "load_catalog",
    "load_schema",
    "load_tools",
    "sse_decode",
    "sse_encode",
]
