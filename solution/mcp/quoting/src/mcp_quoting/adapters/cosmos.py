"""The Azure state reader: the committed session document straight from Cosmos DB."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .base import SessionNotFound


class CosmosStateReader:
    """Reads `sessions` (id = partition key = session id) with the container app's identity, read-only.

    `container` is an `azure.cosmos.aio` ContainerProxy, or anything with `async read_item(item, partition_key)`.
    The identity needs the data-plane role "Cosmos DB Built-in Data Reader" on the account. `quote_blocked` is true
    when the document's `stop` (the active STOP rule hit) or `rules_error` is set, as the BFF computes it."""

    def __init__(self, container: Any) -> None:
        """`container` is the `sessions` container (see the class)."""
        self._container = container

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> CosmosStateReader:
        """Needs the `azure` extra and `COSMOS_ENDPOINT`. Database `quoting` and container `sessions` by default."""
        from azure.cosmos.aio import CosmosClient  # lazy: stub-only setups do not install it
        from azure.identity.aio import DefaultAzureCredential

        client = CosmosClient(env["COSMOS_ENDPOINT"], credential=DefaultAzureCredential())
        database = client.get_database_client(env.get("COSMOS_DATABASE", "quoting"))
        return cls(database.get_container_client(env.get("COSMOS_CONTAINER", "sessions")))

    async def read(self, session_id: str, session_ref: str) -> dict[str, Any]:
        """The session document plus `quote_blocked`. A missing document raises `SessionNotFound`; `session_ref` is
        not used, since this reader has its own identity."""
        try:
            doc = await self._container.read_item(item=session_id, partition_key=session_id)
        except Exception as exc:  # azure.cosmos exceptions carry the HTTP status; importing them would need the extra
            if getattr(exc, "status_code", None) == 404:
                raise SessionNotFound(session_id) from exc
            raise
        return {**doc, "quote_blocked": bool(doc.get("stop") or doc.get("rules_error"))}
