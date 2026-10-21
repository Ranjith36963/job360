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
    # rebuild places.json / companies.json; never run by the kit
    build_only = {"build_places.py", "build_companies.py", "build_india.py"}
    missing = sorted(on_disk - build_only - set(KIT_FILES))
    assert not missing, f"kit files not served by /api/hunt-kit: {missing}"


def test_hunt_kit_not_imported():
    """The server never imports the kit: it only serves its bytes. That is why
    tests/test_runtime_deps_declared.py may skip src/hunt_kit/ (its sibling-module
    imports are the user's machine's business, not a server dependency)."""
    import re

    src = Path(__file__).resolve().parent.parent / "src"
    pattern = re.compile(r"^\s*(from|import)\s+(src\.)?hunt_kit\b", re.M)
    offenders = [
        str(p.relative_to(src))
        for p in src.rglob("*.py")
        if "hunt_kit" not in p.parent.parts and pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert not offenders, f"server code imports the hunt kit: {offenders}"


def test_company_list_uses_board_not_token():
    """Secret scanners read `"token": "<slug>"` as an API key (gitleaks
    generic-api-key fired on company names like safetyculture-2), so the file
    names the public board slug `board`; the kit maps it back on load."""
    import json

    from src.api.routes.hunt_kit import HUNT_KIT_DIR

    raw = (Path(HUNT_KIT_DIR) / "companies.json").read_text(encoding="utf-8")
    assert '"token"' not in raw
    rows = json.loads(raw)
    assert rows and all(r.get("board") for r in rows)
