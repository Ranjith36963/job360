"""Every module the hunt kit imports must be downloadable.

The kit is run from the files GET /api/hunt-kit serves. A module that exists in
src/hunt_kit/ but is missing from KIT_FILES breaks every downloaded kit while
the repo's own tests (which import from disk) stay green.
"""
from __future__ import annotations

from pathlib import Path


def test_every_kit_module_and_data_file_is_served():
    from src.api.routes.hunt_kit import HUNT_KIT_DIR, KIT_FILES

    on_disk = {p.name for p in Path(HUNT_KIT_DIR).iterdir() if p.suffix in {".py", ".json", ".md"}}
    build_only = {"build_places.py"}  # rebuilds places.json; never needed to run the kit
    missing = sorted(on_disk - build_only - set(KIT_FILES))
    assert not missing, f"kit files not served by /api/hunt-kit: {missing}"
