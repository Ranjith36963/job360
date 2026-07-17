"""ChromaDB persist path (SI3).

The default store path was hand-computed as
``Path(__file__).resolve().parents[3] / "data" / "chroma"`` — but parents[3] is
the REPO ROOT, not ``backend/``. So embeddings were written to
``<repo>/data/chroma`` while the module docstring, ``.gitignore`` and every
other component expected ``backend/data/chroma``: nothing else could find the
store, and a deploy that only persists ``backend/data/`` dropped it on every
redeploy.

These tests pin the path to ``settings.DATA_DIR`` — the single place the app
declares where runtime data lives — so the two can never drift apart again.
No chromadb import needed: this is pure path math (rule #11 stays satisfied).
"""
from __future__ import annotations

from src.core.settings import DATA_DIR
from src.services.vector_index import _DEFAULT_PATH, VectorIndex


def test_default_path_is_under_the_apps_data_dir():
    assert _DEFAULT_PATH == DATA_DIR / "chroma"


def test_default_path_lives_inside_backend_not_the_repo_root():
    """The exact regression: parents[3] put it one level too high."""
    assert _DEFAULT_PATH.parent.name == "data"
    assert _DEFAULT_PATH.parents[1].name == "backend", (
        f"chroma must persist under backend/data/, got {_DEFAULT_PATH}"
    )


def test_index_defaults_to_that_path():
    # client injected so no real Chroma is touched (rule #11).
    idx = VectorIndex(client=object())
    assert idx._persist_dir == DATA_DIR / "chroma"


def test_explicit_persist_dir_still_wins(tmp_path):
    idx = VectorIndex(persist_dir=tmp_path, client=object())
    assert idx._persist_dir == tmp_path
