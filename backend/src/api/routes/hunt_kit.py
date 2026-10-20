"""Hunt kit — a static download the user's OWN assistant runs to find jobs.

Owner decision 2026-10-13: hunts must be as wide as career-ops / jobsync /
ai-job-search (company job boards + open feeds, no login, never LinkedIn,
Indeed or Glassdoor). Product rule 4 still holds: Job360 never searches. The
server only hands out fixed files from ``src/hunt_kit/``; it fetches nothing,
runs nothing and keeps no per-user state. The assistant downloads the kit,
runs it on the user's machine, judges every job itself and brings only fits
back with ``bring_job`` + ``save_fit``.

Public on purpose (``tests/test_route_auth_coverage.py`` PUBLIC_ROUTES): an
assistant's fetch tool carries no Job360 credential, and the files hold no
user data. Only names in ``KIT_FILES`` resolve — a request never reaches the
filesystem with a caller-chosen path.
"""
from __future__ import annotations

import hashlib
from functools import cache
from pathlib import Path

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from src.core.settings import SITE_BASE_URL

router = APIRouter(tags=["hunt-kit"])

HUNT_KIT_DIR = Path(__file__).resolve().parents[2] / "hunt_kit"

# name -> media type. A .py file goes out as plain text: it is read and run by
# the assistant, never executed by a browser.
KIT_FILES: dict[str, str] = {
    "README.md": "text/markdown; charset=utf-8",
    "hunt_kit.py": "text/plain; charset=utf-8",
    "kit_ats.py": "text/plain; charset=utf-8",
    "kit_ats_more.py": "text/plain; charset=utf-8",
    "kit_ats_misc1.py": "text/plain; charset=utf-8",
    "kit_ats_misc2.py": "text/plain; charset=utf-8",
    "kit_ats_extra.py": "text/plain; charset=utf-8",
    "kit_feeds.py": "text/plain; charset=utf-8",
    "kit_gov.py": "text/plain; charset=utf-8",
    "kit_regional.py": "text/plain; charset=utf-8",
    "kit_bigco.py": "text/plain; charset=utf-8",
    "kit_startups.py": "text/plain; charset=utf-8",
    "kit_asia.py": "text/plain; charset=utf-8",
    "kit_bigboards.py": "text/plain; charset=utf-8",
    "kit_keyed.py": "text/plain; charset=utf-8",
    "kit_niche.py": "text/plain; charset=utf-8",
    "kit_places.py": "text/plain; charset=utf-8",
    "kit_xml.py": "text/plain; charset=utf-8",
    "companies.json": "application/json",
    "places.json": "application/json",
    "NOTICE.md": "text/markdown; charset=utf-8",
}

_HEADERS = {
    "Cache-Control": "public, max-age=3600",
    "Access-Control-Allow-Origin": "*",
    "X-Content-Type-Options": "nosniff",
}


class KitFile(BaseModel):
    name: str
    url: str
    bytes: int
    sha256: str


class KitIndex(BaseModel):
    how_to: str
    files: list[KitFile]


@cache
def _read(name: str) -> bytes:
    """One kit file's bytes (cached: the files only change with a deploy)."""
    return (HUNT_KIT_DIR / name).read_bytes()


@router.get("/hunt-kit", response_model=KitIndex)
async def hunt_kit_index(response: Response) -> KitIndex:
    """The kit's files with size and SHA-256, so the assistant can check what it ran."""
    response.headers.update(_HEADERS)
    return _index()


@cache
def _index() -> KitIndex:
    """Built once per process: a public route must not re-hash ~800 KB per request."""
    files = []
    for name in KIT_FILES:
        data = _read(name)
        files.append(KitFile(
            name=name, url=f"{SITE_BASE_URL}/api/hunt-kit/{name}", bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
        ))
    return KitIndex(
        how_to=(
            "Download every file into one folder, read README.md, then run "
            "`python hunt_kit.py --help`. Python 3.10+, standard library only. "
            "It reads company job boards and open job feeds with no login, and it "
            "never logs in. README.md also lists the no-key connectors, web-search "
            "queries and the Indeed rule for the assistant itself (the script reads LinkedIn, Glassdoor "
            "and Google Jobs at low volume). Read each job it keeps, "
            "judge it yourself, and bring only fits into Job360."
        ),
        files=files,
    )


@router.get("/hunt-kit/{name}")
async def hunt_kit_file(name: str) -> Response:
    """One kit file. Unknown names are 404 and never touch the filesystem."""
    media = KIT_FILES.get(name)
    if media is None:
        raise HTTPException(status_code=404, detail=f"No kit file {name!r}. Files: {', '.join(KIT_FILES)}")
    return Response(content=_read(name), media_type=media, headers=_HEADERS)
