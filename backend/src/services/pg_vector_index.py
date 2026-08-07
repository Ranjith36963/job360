"""PgVectorIndex — the job vector store, in Postgres (pgvector).

WHY THIS REPLACES THE CHROMA-ON-DISK STORE (2026-08-07). The vectors lived in
ChromaDB at ``/data/chroma`` on the BACKEND container's local filesystem, and
that placement had three structural consequences, all measured in prod:

  1. The worker image ships without the ``[semantic]`` extra, and the nightly
     catalog refresh — the only thing that runs the pipeline on a schedule —
     runs on the WORKER. It could never embed anything. The catalog grew
     7,761 -> 8,184 overnight while embeddings stayed at exactly 284, with
     ``SEMANTIC_ENABLED=true`` the whole time.
  2. Backend and worker are separate containers with separate disks, so they
     cannot share an index even if both had the encoder.
  3. Worst: ``job_embeddings`` (Postgres, permanent) recorded "job N is
     embedded", so the backfill SKIPPED those jobs — while the vectors sat on
     a container disk a redeploy can reset. The bookkeeping could claim
     coverage the index did not have, and nothing would ever refill the hole.

Putting the vector in the SAME ROW as its audit record removes all three: one
store shared by every service, surviving deploys, inside the existing
db-backup, and structurally impossible to desync from its own bookkeeping.

The interface mirrors ``VectorIndex`` (upsert / query / delete / count) so
callers are unchanged. Distances are cosine distance (``<=>``), matching
Chroma's default, so ranking code needs no adjustment.

At ~8k jobs an exact scan is milliseconds; no ANN index is created (see
migration 0027). Add ivfflat/hnsw when the catalog justifies it.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from src.repositories import pgsync

logger = logging.getLogger("job360.services.pg_vector_index")

# Migration 0027 is TOLERANT: on a Postgres without pgvector it skips the
# column rather than failing the boot. So every method here degrades to a
# no-op / empty result instead of raising — a missing semantic index must
# read as "no semantic results", never as a 500 on the jobs route.
_MISSING_COLUMN_HINTS = ("embedding", "does not exist", "undefined_column")


def _is_missing_vector_column(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "embedding" in msg and ("does not exist" in msg or "undefined" in msg)


def vector_column_available(db_path: Optional[str] = None) -> bool:
    """Is ``job_embeddings.embedding`` present on THIS database?

    Migration 0027 is tolerant, so the column exists only where pgvector does.
    Callers that build "which jobs still need embedding" SQL must ask first:
    a predicate naming a non-existent column is a hard error, and it would
    fire inside run_search — turning a dark feature into a broken search.
    Uses current_schema() rather than to_regclass/search_path (saved-memory
    rule: search_path falls back to public and a schema-isolated test would
    read the wrong table).
    """
    if db_path is None:
        from src.core.settings import DB_PATH  # noqa: PLC0415

        db_path = str(DB_PATH)
    try:
        with pgsync.connect(db_path) as conn:
            cur = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE table_schema = current_schema() "
                "AND table_name = 'job_embeddings' AND column_name = 'embedding'"
            )
            row = cur.fetchone()
            return bool(row and int(row[0]) > 0)
    except Exception:  # noqa: BLE001 — absence is the answer, not an error
        return False


def _to_literal(vector: list[float]) -> str:
    """pgvector's text input form: '[0.1,0.2,...]'.

    Sent as a string parameter and cast in SQL, so no pgvector Python adapter
    is required — the worker image needs no extra dependency to WRITE vectors
    (it still needs sentence-transformers to COMPUTE them).
    """
    return "[" + ",".join(f"{float(x):.6f}" for x in vector) + "]"


class PgVectorIndex:
    """Job embedding store backed by ``job_embeddings.embedding``."""

    def __init__(self, db_path: Optional[str] = None) -> None:
        if db_path is None:
            from src.core.settings import DB_PATH  # noqa: PLC0415

            db_path = str(DB_PATH)
        self._db_path = db_path

    def upsert(
        self,
        job_id: int,
        vector: list[float],
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        """Insert-or-replace the embedding for ``job_id``.

        ``metadata`` is accepted for interface parity with the Chroma index
        and deliberately ignored: everything it carried (source, title) is
        already a column on ``jobs``, reachable by join — storing a second
        copy is exactly the desync this class exists to remove.
        """
        from src.services.embeddings import MODEL_NAME  # noqa: PLC0415

        try:
            with pgsync.connect(self._db_path) as conn:
                conn.execute(
                    "INSERT INTO job_embeddings(job_id, model_version, embedding) "
                    "VALUES (?, ?, ?::public.vector) "
                    "ON CONFLICT(job_id) DO UPDATE SET "
                    "  model_version = EXCLUDED.model_version, "
                    "  embedding = EXCLUDED.embedding, "
                    "  embedding_updated_at = now()::text",
                    (int(job_id), MODEL_NAME, _to_literal(vector)),
                )
        except Exception as exc:  # noqa: BLE001
            if _is_missing_vector_column(exc):
                logger.debug("pgvector column absent — upsert skipped")
                return
            raise

    def query(
        self,
        vector: list[float],
        k: int = 10,
        filter_metadata: Optional[dict[str, Any]] = None,
    ) -> list[tuple[int, float]]:
        """Nearest neighbours as ``[(job_id, distance), ...]``, ascending.

        Cosine distance (``<=>``) to match Chroma's default so downstream
        ranking is unchanged. Rows without a vector are skipped. Jobs deleted
        from the catalog cascade out of this table, so a result always names a
        live job.
        """
        try:
            with pgsync.connect(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT job_id, public.cosine_distance(embedding, ?::public.vector) AS distance "
                    "FROM job_embeddings WHERE embedding IS NOT NULL "
                    "ORDER BY public.cosine_distance(embedding, ?::public.vector) LIMIT ?",
                    (_to_literal(vector), _to_literal(vector), int(k)),
                )
                return [(int(r[0]), float(r[1])) for r in cur.fetchall()]
        except Exception as exc:  # noqa: BLE001
            if _is_missing_vector_column(exc):
                logger.debug("pgvector column absent — no semantic results")
                return []
            raise

    def delete(self, job_id: int) -> None:
        try:
            with pgsync.connect(self._db_path) as conn:
                conn.execute(
                    "UPDATE job_embeddings SET embedding = NULL WHERE job_id = ?",
                    (int(job_id),),
                )
        except Exception as exc:  # noqa: BLE001
            if not _is_missing_vector_column(exc):
                raise

    def count(self) -> int:
        """How many jobs actually have a VECTOR — not how many have an audit
        row. Those two numbers could differ under the old store; here they
        cannot, which is the point."""
        try:
            with pgsync.connect(self._db_path) as conn:
                cur = conn.execute(
                    "SELECT count(*) FROM job_embeddings WHERE embedding IS NOT NULL"
                )
                row = cur.fetchone()
                return int(row[0]) if row else 0
        except Exception as exc:  # noqa: BLE001
            if _is_missing_vector_column(exc):
                return 0
            raise
