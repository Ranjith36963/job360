"""Batch 3.5.3 pre-flight gate — does this source honor conditional GET?

For each candidate RSS/XML source:
  1. Fetch the URL once and capture ETag / Last-Modified.
  2. Re-fetch with If-None-Match / If-Modified-Since and observe the
     server's reply.
  3. Print a verdict: QUALIFIES (etag|last-mod|both) or REJECTED (no
     validators | server ignores If-*).

If 0 qualify, Batch 3.5.3's migration step is blocked — adopting
conditional fetch on a source whose upstream ignores the headers just
fills the cache with un-validatable entries (pure downside). The gate
exists to prevent that.

Usage:
    python scripts/preflight_conditional_cache.py

Requires network access to hit the real upstreams. No auth; all URLs
are public.
"""
from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from typing import Optional

import httpx

USER_AGENT = "Job360/1.0 (UK Job Search Aggregator) +preflight-conditional-cache"

CANDIDATES: list[dict] = [
    {
        "name": "jobs_ac_uk",
        "url": "https://www.jobs.ac.uk/feeds/subject-areas/computer-sciences",
    },
    {
        "name": "biospace",
        "url": "https://www.biospace.com/rss/jobs/data-science",
    },
    {
        "name": "weworkremotely",
        "url": "https://weworkremotely.com/remote-jobs.rss",
    },
    # Fallbacks
    {
        "name": "nhs_jobs_xml",
        "url": "https://www.jobs.nhs.uk/api/v1/feed/all_current_vacancies.xml",
    },
    {
        "name": "realworkfromanywhere",
        "url": "https://www.realworkfromanywhere.com/rss.xml",
    },
]


@dataclass
class Verdict:
    name: str
    etag: Optional[str] = None
    last_modified: Optional[str] = None
    conditional_status: Optional[int] = None
    error: Optional[str] = None

    @property
    def verdict(self) -> str:
        if self.error:
            return f"REJECTED (error: {self.error})"
        has_etag = bool(self.etag)
        has_lm = bool(self.last_modified)
        if not (has_etag or has_lm):
            return "REJECTED (no validators)"
        if self.conditional_status == 304:
            if has_etag and has_lm:
                return "QUALIFIES (both)"
            if has_etag:
                return "QUALIFIES (etag)"
            return "QUALIFIES (last-mod)"
        return f"REJECTED (server returned {self.conditional_status} on conditional GET — ignores If-*)"


async def probe(client: httpx.AsyncClient, name: str, url: str) -> Verdict:
    v = Verdict(name=name)
    try:
        # First fetch — observe validator headers
        r1 = await client.get(url, follow_redirects=True, timeout=20.0)
        v.etag = r1.headers.get("etag")
        v.last_modified = r1.headers.get("last-modified")
        if not (v.etag or v.last_modified):
            return v

        # Conditional fetch — does the server honor it?
        cond_headers: dict[str, str] = {}
        if v.etag:
            cond_headers["If-None-Match"] = v.etag
        if v.last_modified:
            cond_headers["If-Modified-Since"] = v.last_modified

        r2 = await client.get(
            url, headers=cond_headers, follow_redirects=True, timeout=20.0
        )
        v.conditional_status = r2.status_code
    except Exception as e:  # noqa: BLE001 — we want the error string in the report
        v.error = f"{type(e).__name__}: {str(e)[:200]}"
    return v


async def main() -> int:
    print(f"# Batch 3.5.3 — conditional-fetch pre-flight\n")
    print(f"Probing {len(CANDIDATES)} candidates.\n")
    verdicts: list[Verdict] = []
    async with httpx.AsyncClient(
        headers={"User-Agent": USER_AGENT}, http2=False
    ) as client:
        for c in CANDIDATES:
            v = await probe(client, c["name"], c["url"])
            verdicts.append(v)
            print(f"## {v.name}")
            print(f"  url              : {c['url']}")
            print(f"  ETag             : {v.etag!r}")
            print(f"  Last-Modified    : {v.last_modified!r}")
            print(f"  conditional-GET  : HTTP {v.conditional_status}"
                  if v.conditional_status is not None else "  conditional-GET  : not attempted")
            print(f"  VERDICT          : {v.verdict}\n")

    qualifying = [v for v in verdicts if v.verdict.startswith("QUALIFIES")]
    print(f"\n# Summary — {len(qualifying)}/{len(verdicts)} qualify\n")
    for v in verdicts:
        print(f"  - {v.name:25s} {v.verdict}")

    if len(qualifying) == 0:
        print("\n**GATE: BLOCKED — zero candidates qualify. Migration is deferred.**")
        return 2
    if len(qualifying) == 1:
        print(f"\n**GATE: PROCEED (minimum — 1 qualifier: {qualifying[0].name}).**")
        return 1
    print(f"\n**GATE: PROCEED — {len(qualifying)} qualifiers.**")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
