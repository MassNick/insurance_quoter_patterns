"""Persistence: one small document Store (in memory, or Cosmos DB) and a BlobStore (a local folder, or Azure Blob).

Containers (database ``quoting``, partition key ``/session_id``; architecture §6.3): ``sessions`` (one document per
session, optimistic concurrency on ``_etag``), ``events``, ``state_changes``, ``quotes``, ``confirmations`` and
``handoffs``. Append-only containers are written with ``create=True``.
"""

from __future__ import annotations

import asyncio
import copy
import tempfile
import uuid
from pathlib import Path
from typing import Any, Protocol

# The Cosmos containers, all partitioned by /session_id. InMemoryStore creates the same set.
CONTAINERS = ("sessions", "events", "state_changes", "quotes", "confirmations", "handoffs")


class Conflict(Exception):
    """A write lost: the ``etag`` is stale, or a ``create`` found the id taken."""


class Store(Protocol):
    """The document store the BFF needs: three async methods over the named containers in ``CONTAINERS``."""

    async def get(self, container: str, session_id: str, doc_id: str) -> dict[str, Any] | None:
        """The document ``doc_id`` of this session's partition, or None."""
        ...

    async def put(self, container: str, doc: dict[str, Any], *, etag: str | None = None,
                  create: bool = False) -> dict[str, Any]:
        """Upsert; ``etag`` replaces only that version, ``create`` only a new id. Both raise Conflict.

        Returns the stored document, with its new ``_etag``.
        """
        ...

    async def query(self, container: str, **equals: Any) -> list[dict[str, Any]]:
        """Every document of the container whose fields equal all the given values."""
        ...


class InMemoryStore:
    """Dict-backed Store for tests and local runs: it keeps deep copies and gives every write a fresh ``_etag``."""

    def __init__(self) -> None:
        """One empty dict per container, keyed by ``(session_id, id)``."""
        self.data: dict[str, dict[tuple[str, str], dict[str, Any]]] = {c: {} for c in CONTAINERS}

    async def get(self, container: str, session_id: str, doc_id: str) -> dict[str, Any] | None:
        """A copy of the document, or None."""
        doc = self.data[container].get((session_id, doc_id))
        return copy.deepcopy(doc) if doc else None

    async def put(self, container: str, doc: dict[str, Any], *, etag: str | None = None,
                  create: bool = False) -> dict[str, Any]:
        """Store a copy under a fresh ``_etag`` and return it.

        Raises Conflict when ``create`` finds the id taken or ``etag`` is stale.
        """
        key = (doc["session_id"], doc["id"])
        current = self.data[container].get(key)
        if (create and current) or (etag is not None and (current or {}).get("_etag") != etag):
            raise Conflict(f"{container}/{doc['id']}")
        stored = {**copy.deepcopy(doc), "_etag": uuid.uuid4().hex}
        self.data[container][key] = stored
        return copy.deepcopy(stored)

    async def query(self, container: str, **equals: Any) -> list[dict[str, Any]]:
        """Copies of the documents whose fields equal every keyword."""
        rows = [d for d in self.data[container].values() if all(d.get(k) == v for k, v in equals.items())]
        return copy.deepcopy(rows)


class CosmosStore:
    """The same Store on Cosmos DB. ``database`` is an ``azure.cosmos.aio`` DatabaseProxy (or a test fake)."""

    def __init__(self, database: Any) -> None:
        """Keep the database proxy; container clients are looked up by name on each call."""
        self.db = database

    @classmethod
    def connect(cls, endpoint: str, database: str) -> CosmosStore:
        """Open the Cosmos account at ``endpoint`` with the managed identity and wrap ``database``.

        The credential is DefaultAzureCredential. The Azure SDK is imported here, so the in-memory path needs none of
        it.
        """
        from azure.cosmos.aio import CosmosClient
        from azure.identity.aio import DefaultAzureCredential

        return cls(CosmosClient(endpoint, credential=DefaultAzureCredential()).get_database_client(database))

    async def get(self, container: str, session_id: str, doc_id: str) -> dict[str, Any] | None:
        """The item, or None on a 404. Any other error is raised."""
        try:
            return await self.db.get_container_client(container).read_item(item=doc_id, partition_key=session_id)
        except Exception as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise

    async def put(self, container: str, doc: dict[str, Any], *, etag: str | None = None,
                  create: bool = False) -> dict[str, Any]:
        """Create, replace if the ``_etag`` still matches, or upsert. A 409 or 412 becomes Conflict.

        ``create`` uses create_item, ``etag`` uses replace_item with IfNotModified, otherwise it upserts. The
        ``_etag`` field is never sent in the body: the SDK takes it as the match condition.
        """
        client = self.db.get_container_client(container)
        body = {k: v for k, v in doc.items() if k != "_etag"}
        try:
            if create:
                return await client.create_item(body=body)
            if etag is not None:
                from azure.core import MatchConditions

                return await client.replace_item(item=doc["id"], body=body, etag=etag,
                                                 match_condition=MatchConditions.IfNotModified)
            return await client.upsert_item(body=body)
        except Exception as exc:
            if getattr(exc, "status_code", None) in (409, 412):
                raise Conflict(f"{container}/{doc['id']}") from exc
            raise

    async def query(self, container: str, **equals: Any) -> list[dict[str, Any]]:
        """Parameterised equality query over one container (``SELECT * FROM c WHERE c.k = @k AND ...``).

        The keyword names are code constants, never customer input, which is why the f-string carries a noqa.
        """
        where = " AND ".join(f"c.{k} = @{k}" for k in equals) or "true"
        params = [{"name": f"@{k}", "value": v} for k, v in equals.items()]
        items = self.db.get_container_client(container).query_items(f"SELECT * FROM c WHERE {where}",  # noqa: S608
                                                                     parameters=params)
        return [item async for item in items]


class LocalBlobStore:
    """Blob store on a local folder, for tests and local runs. There is no malware scan."""

    def __init__(self, folder: str = "") -> None:
        """Use ``folder``, or a fresh temp folder when it is empty."""
        self.root = Path(folder or tempfile.mkdtemp(prefix="quote-bff-uploads-"))

    async def put(self, name: str, data: bytes, content_type: str) -> None:
        """Write ``data`` to ``name`` under the folder; the content type is not kept locally."""
        path = self.root / name
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get(self, name: str) -> bytes:
        """Read the bytes stored under ``name``."""
        return await asyncio.to_thread((self.root / name).read_bytes)


class AzureBlobStore:
    """Blob container ``uploads``. ``get`` waits for the Defender for Storage scan result index tag. [UNVERIFIED]"""

    # The index tag Defender for Storage sets on a scanned blob, and the value it has for a clean one.
    SCAN_TAG, CLEAN = "Malware Scanning scan result", "No threats found"

    def __init__(self, account_url: str, container: str, scan_timeout: float = 20.0) -> None:
        """Open ``container`` of the storage account with the managed identity.

        ``scan_timeout`` is how many seconds ``get`` waits for the Defender scan result.
        """
        from azure.identity.aio import DefaultAzureCredential
        from azure.storage.blob.aio import BlobServiceClient

        self.client = BlobServiceClient(account_url, credential=DefaultAzureCredential()).get_container_client(
            container)
        self.scan_timeout = scan_timeout

    async def put(self, name: str, data: bytes, content_type: str) -> None:
        """Upload, overwriting, with its content type. Defender for Storage scans the blob afterwards (FR-DOC-2)."""
        from azure.storage.blob import ContentSettings

        await self.client.upload_blob(name, data, overwrite=True,
                                      content_settings=ContentSettings(content_type=content_type))

    async def get(self, name: str) -> bytes:
        """Download the blob once Defender has scanned it clean (FR-DOC-2).

        Polls the "No threats found" index tag once a second for up to ``scan_timeout`` seconds. Raises
        PermissionError when a threat is found and TimeoutError when no scan result shows up.
        """
        blob = self.client.get_blob_client(name)
        for _ in range(int(self.scan_timeout)):
            result = (await blob.get_blob_tags()).get(self.SCAN_TAG)
            if result == self.CLEAN:
                return await (await blob.download_blob()).readall()
            if result:
                raise PermissionError("malware detected")
            await asyncio.sleep(1)
        raise TimeoutError("malware scan result not available")
