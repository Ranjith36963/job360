"""ESCO-based skill normalisation.

Data attribution (plan §8 risk-table row 1): this module consumes
data derived from the European Skills, Competences, Qualifications
and Occupations (ESCO) classification, © European Union, 2024,
licensed under CC BY 4.0 — see
https://esco.ec.europa.eu/en/about-esco/data-science-and-esco.
Downstream redistributors must preserve the attribution.

Batch 1.3b (Pillar 1). Given a raw skill string (``"Python prog."``),
returns the closest ESCO concept URI + canonical label
(``"Python (computer programming)"`` at
``http://data.europa.eu/esco/skill/fbf4f6f3-...``) using cosine
similarity over precomputed sentence-transformers embeddings of the
~13,900 ESCO skills dataset.

Runtime ergonomics:

* The ESCO CSV + precomputed embedding matrix are BUILD-TIME
  artefacts, generated once by ``scripts/build_esco_index.py`` and
  checked into ``backend/data/esco/``. They are NOT fetched at
  runtime — 20 MB of one-shot I/O has no place in the request path.
* ``sentence-transformers`` is an optional dependency (~300 MB wheel)
  declared under the ``esco`` extra in ``pyproject.toml``. Importing
  it lazily here keeps CI/test runs — which never need ESCO — fast.
* ``normalize_skill(name)`` returns ``None`` when the index or the
  encoder are unavailable. Callers gracefully fall back to raw
  strings in that case. This matches the established "optional
  enrichment, never fatal" pattern used by ``GitHubEnricher``.

Only the *lookup* path is imported at module load. The *encode* path
(needed to embed the user's raw skill string) pulls
``sentence-transformers`` lazily on first call — skipping CI where
the extra isn't installed.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger("job360.profile.skill_normalizer")

# Default location — ``backend/data/esco/`` relative to the ``backend``
# project root. Build script drops 2 files here; anything else is
# ignored. Missing dir → normalize returns None.
_DEFAULT_ESCO_DIR = Path(__file__).resolve().parents[3] / "data" / "esco"

_LABELS_FILE = "labels.json"       # list[{"uri": str, "label": str, "alt_labels": [str]}]
_EMBEDDINGS_FILE = "embeddings.npy"  # shape: (N, dim), float32

_MIN_COSINE_SIMILARITY = 0.55  # threshold below which we call it "no match"


@dataclass(frozen=True)
class ESCOMatch:
    """Winning ESCO concept match for a raw skill string."""

    uri: str
    label: str
    similarity: float


class _ESCOIndex:
    """Lazy-loaded ESCO embedding index. Singleton per-process.

    Load happens on first ``normalize_skill`` call. If either of the
    two artefact files is missing, or ``numpy`` cannot be imported,
    we flip ``self.available`` to False and remain in that state for
    the process lifetime. No auto-retry — rebuilding the index is a
    dev-workflow step, not a runtime concern.
    """

    def __init__(self, data_dir: Path = _DEFAULT_ESCO_DIR):
        self.data_dir = data_dir
        self.available: bool = False
        self.labels: list[dict] = []
        self.embeddings = None  # numpy.ndarray when loaded
        self._encoder = None
        self._loaded_once = False

    def _load(self) -> None:
        """Populate the index from disk. Idempotent; silent on missing data."""
        if self._loaded_once:
            return
        self._loaded_once = True

        labels_path = self.data_dir / _LABELS_FILE
        emb_path = self.data_dir / _EMBEDDINGS_FILE
        if not labels_path.exists() or not emb_path.exists():
            logger.debug("ESCO index artefacts missing at %s — normalizer disabled", self.data_dir)
            return

        try:
            import numpy as np  # type: ignore
        except ImportError:
            logger.info("numpy not installed — ESCO normalizer disabled")
            return

        try:
            with labels_path.open(encoding="utf-8") as f:
                self.labels = json.load(f)
            self.embeddings = np.load(emb_path)
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load ESCO index from %s: %s", self.data_dir, e)
            return

        if not isinstance(self.labels, list) or self.embeddings is None:
            return
        if len(self.labels) != self.embeddings.shape[0]:
            logger.warning(
                "ESCO index mismatch: %d labels but %d embeddings — disabled",
                len(self.labels),
                self.embeddings.shape[0],
            )
            return

        self.available = True
        logger.info("ESCO index loaded: %d concepts from %s", len(self.labels), self.data_dir)

    def _get_encoder(self):
        """Lazy-import sentence-transformers. ``None`` if extra not installed."""
        if self._encoder is not None:
            return self._encoder
        try:
            from sentence_transformers import SentenceTransformer  # type: ignore
        except ImportError:
            logger.debug("sentence-transformers not installed; encode path disabled")
            return None
        try:
            self._encoder = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
        except Exception as e:  # noqa: BLE001
            logger.warning("Failed to load ESCO encoder: %s", e)
            return None
        return self._encoder

    def normalize(self, raw: str) -> Optional[ESCOMatch]:
        """Encode ``raw`` and return the closest ESCO match above threshold.

        Returns ``None`` when:
          * the index is missing / unloadable
          * sentence-transformers / numpy is unavailable
          * the best cosine similarity is below ``_MIN_COSINE_SIMILARITY``
        """
        if not raw or not raw.strip():
            return None
        self._load()
        if not self.available:
            return None
        encoder = self._get_encoder()
        if encoder is None:
            return None

        import numpy as np  # safe: _load would have returned if numpy missing

        try:
            query = encoder.encode([raw.strip()], normalize_embeddings=True)[0]
        except Exception as e:  # noqa: BLE001
            logger.debug("Failed to encode %r: %s", raw, e)
            return None

        # Cosine sim == dot product since both sides are L2-normalised.
        sims = self.embeddings @ query
        idx = int(np.argmax(sims))
        score = float(sims[idx])
        if score < _MIN_COSINE_SIMILARITY:
            return None
        entry = self.labels[idx]
        return ESCOMatch(
            uri=entry.get("uri", ""),
            label=entry.get("label", ""),
            similarity=score,
        )


# Module-level singleton — callers should stick to the public helpers below.
_INDEX = _ESCOIndex()


def normalize_skill(raw: str) -> Optional[ESCOMatch]:
    """Return the ESCO match for ``raw`` or ``None`` if no confident match.

    Thin wrapper over the singleton index. Safe to call in tight loops;
    the index is loaded once per process.
    """
    return _INDEX.normalize(raw)


def reset_index_for_testing(data_dir: Optional[Path] = None) -> None:
    """Reset the module-level singleton — used by tests that swap data dirs."""
    global _INDEX
    _INDEX = _ESCOIndex(data_dir=data_dir or _DEFAULT_ESCO_DIR)


def is_available() -> bool:
    """Pillar 2 Batch 2.6 — tell callers whether the ESCO index is live.

    Downstream callers (Batch 2.6 `skill_matcher`) use this to decide
    whether to route skills through ESCO-based canonicalisation in addition
    to the Batch-2.3 static table. Returns False when the index artefacts
    are missing or when the sentence-transformers dep isn't installed,
    matching the "optional enrichment, never fatal" pattern.
    """
    _INDEX._load()
    return _INDEX.available


def index_status() -> dict:
    """Return a small dict describing current index state. Used by ops surfaces."""
    _INDEX._load()
    return {
        "available": _INDEX.available,
        "concepts": len(_INDEX.labels) if _INDEX.available else 0,
        "data_dir": str(_INDEX.data_dir),
    }
