"""Pillar 2 Batch 2.6 — thin ChromaDB wrapper for job embeddings.

CLAUDE.md rule #11 — ``chromadb`` is imported lazily inside the functions
that use it. Tests inject a fake client via ``VectorIndex(client=...)`` so
pytest never touches real Chroma state.

Persistent collection lives at ``backend/data/chroma/`` (gitignored). The
collection name is ``jobs`` — one row per ``job_id``.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger("job360.services.vector_index")


_DEFAULT_PATH = Path(__file__).resolve().parents[3] / "data" / "chroma"
_COLLECTION_NAME = "jobs"


def _make_client(persist_dir: Path):
    """Lazy ChromaDB PersistentClient. CLAUDE.md rule #11."""
    try:
        import chromadb  # type: ignore
    except ImportError as e:
        raise RuntimeError(
            "chromadb is not installed — run `pip install '.[semantic]'`"
        ) from e
    persist_dir.mkdir(parents=True, exist_ok=True)
    return chromadb.PersistentClient(path=str(persist_dir))


class VectorIndex:
    """Thin wrapper over a single Chroma collection.

    Parameters
    ----------
    persist_dir : Path | None
        Directory for the Chroma persistent store. Defaults to
        ``backend/data/chroma/``.
    client : object | None
        Optional pre-built client — tests inject a fake here that
        implements ``get_or_create_collection(name) -> <collection>`` and
        the collection's ``upsert(ids, embeddings, metadatas)`` /
        ``query(query_embeddings, n_results, where=...)`` shape.
    """

    def __init__(
        self,
        persist_dir: Optional[Path] = None,
        *,
        client=None,
        collection_name: str = _COLLECTION_NAME,
    ):
        self._persist_dir = persist_dir or _DEFAULT_PATH
        self._client = client
        self._collection_name = collection_name
        self._collection = None

    def _ensure_collection(self):
        if self._collection is not None:
            return self._collection
        if self._client is None:
            self._client = _make_client(self._persist_dir)
        self._collection = self._client.get_or_create_collection(
            self._collection_name
        )
        return self._collection

    def upsert(
        self,
        job_id: int,
        vector: list[float],
        metadata: Optional[dict] = None,
    ) -> None:
        """Insert-or-replace an embedding for a given job_id."""
        col = self._ensure_collection()
        col.upsert(
            ids=[str(job_id)],
            embeddings=[list(vector)],
            metadatas=[metadata or {}],
        )

    def query(
        self,
        vector: list[float],
        k: int = 10,
        filter_metadata: Optional[dict] = None,
    ) -> list[tuple[int, float]]:
        """Nearest-neighbour query. Returns ``[(job_id, distance), ...]``
        sorted ascending by distance (Chroma default)."""
        col = self._ensure_collection()
        kwargs = {"query_embeddings": [list(vector)], "n_results": k}
        if filter_metadata:
            kwargs["where"] = filter_metadata
        result = col.query(**kwargs)

        # Chroma returns {'ids': [[...]], 'distances': [[...]]} — one list
        # per query. We only queried once, so index [0].
        ids = result.get("ids", [[]])[0]
        distances = result.get("distances", [[]])[0]
        pairs: list[tuple[int, float]] = []
        for raw_id, dist in zip(ids, distances):
            try:
                pairs.append((int(raw_id), float(dist)))
            except (TypeError, ValueError):
                continue
        return pairs

    def delete(self, job_id: int) -> None:
        col = self._ensure_collection()
        col.delete(ids=[str(job_id)])

    def count(self) -> int:
        col = self._ensure_collection()
        try:
            return int(col.count())
        except AttributeError:
            return 0
