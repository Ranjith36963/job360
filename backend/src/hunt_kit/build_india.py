#!/usr/bin/env python3
"""Add Indian company boards (Keka Hire, Zoho Recruit) to companies.json (stdlib only, reproducible).

Build-only: the kit never runs this file and /api/hunt-kit does not serve it.

Seed list: ``india_boards.txt`` next to this file, one ``ats:tenant`` per line (``zohorecruit:tenant.com``
for a Zoho site on .com). It was made from the Common Crawl URL index (keyless CDX API), e.g.
``https://index.commoncrawl.org/CC-MAIN-2026-39-index?url=*.keka.com/careers*&output=json&fl=url``
(also CC-MAIN-2025-51 and CC-MAIN-2025-30), keeping the distinct host label of each URL. Only the
host names were kept; no crawl content is used or shipped.

Every seed board is read once with the kit's own reader (``kit_ats_more.ATS_FETCHERS``), at most
``--workers`` (default 3) at a time, with the kit's honest User-Agent. A board is kept only when it
answers with at least one job. ``country`` is set to IN when more than half of its jobs name India.
Rows this script added before (source ``commoncrawl``, ats keka/zohorecruit) are replaced; every other
row is left exactly as it is. New rows are appended sorted by (ats, board).

Usage (from this folder):
    python build_india.py                        # read every seed board, rewrite companies.json
    python build_india.py --out /tmp/c.json --limit 20
"""
# ruff: noqa: S310, E501
from __future__ import annotations

import argparse
import concurrent.futures
import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
SOURCE = "commoncrawl"
ATS = ("keka", "zohorecruit")
_TITLE = re.compile(r"<title[^>]*>([^<]{1,200})</title>", re.I)
_FOOTER = re.compile(r'<p class="text-secondary mb-3">([^<]{1,120})</p>')
_NAME_PATTERNS = (re.compile(r"^(?:.*\b)?(?:careers?|jobs?) at (.+)$", re.I), re.compile(r"^(.+?) careers?\b", re.I))
_INDIA = re.compile(r"\bIndia\b|, IN(?: ·|$)", re.I)
_GENERIC = {"keka", "keka hire", "careersite", "careers", "career site", "jobs", "page does not exist", "home"}


def _modules() -> tuple[Any, Any, Any]:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    import build_companies  # type: ignore[import-not-found]
    import kit_ats_more  # type: ignore[import-not-found]

    import hunt_kit  # type: ignore[import-not-found]

    return build_companies, hunt_kit, kit_ats_more


def company_name(page: Any, tenant: str) -> str:
    """The employer name a careers page shows (title, then the Keka footer), else the tenant."""
    text = page if isinstance(page, str) else ""
    for rx in (_TITLE, _FOOTER):
        m = rx.search(text)
        raw = " ".join(html.unescape(m.group(1)).split()) if m else ""
        # "Technical Program Manager – eCommerce | GoKwik": a title like that puts the company
        # LAST after "|" (review 2026-10-19); with no "|" the company comes first ("Acme - Careers").
        # A part that says "X Careers" / "Careers at X" names the company wherever it sits
        # ("Teachmint Careers | Join Our Team").
        parts = [x.strip() for x in raw.split("|") if x.strip()]
        named = ""
        for part in parts:
            for p in _NAME_PATTERNS:
                mm = p.match(re.split(r"\s+[\-–]\s+", part)[0].strip())
                if mm:
                    named = mm.group(1).strip()
                    break
            if named:
                break
        if named:
            raw = named
        else:
            raw = parts[-1] if len(parts) > 1 else re.split(r"\s+[\-–]\s+", raw)[0].strip()
        if raw and raw.lower() not in _GENERIC and len(raw) <= 80:
            return raw
    return tenant


def read_seeds(path: Path) -> list[tuple[str, str, str]]:
    """(ats, tenant, tld) from the seed file; unknown or malformed lines are skipped."""
    out: list[tuple[str, str, str]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        ats, _, tok = line.partition(":")
        tenant, _, tld = tok.strip().lower().partition(".")
        if ats in ATS and re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", tenant) and tld in ("", "in", "com"):
            out.append((ats, tenant, tld))
    return sorted(set(out))


def probe(ats: str, tenant: str, tld: str, http: Any, more: Any) -> dict[str, Any] | None:
    """Read one board like the kit does; also read the employer name. None when it fails or has no jobs."""
    pages: dict[str, Any] = {}

    def h(url: str, **kw: Any) -> tuple[int, Any]:
        st, body = http(url, **kw)
        if not kw.get("as_json", True) and st == 200:
            pages[url] = body
        return st, body

    row: dict[str, Any] = {"name": tenant, "ats": ats, "token": tenant}
    if tld:
        row["tld"] = tld
    stats: dict[str, Any] = {}
    jobs = more.ATS_FETCHERS[ats](row, h, stats)
    if not stats.get("ok") or not jobs:
        return None
    page = next(iter(pages.values()), "")
    india = sum(1 for j in jobs if _INDIA.search(j.get("location") or ""))
    used_tld = ""
    if ats == "zohorecruit":
        used_tld = "com" if ".zohorecruit.com/" in jobs[0]["url"] else ""
    return {"name": company_name(page, tenant), "jobs": len(jobs), "india": india, "tld": used_tld}


def main(argv: list[str] | None = None) -> int:
    """Read the seed boards and write companies.json."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--seeds", default=str(HERE / "india_boards.txt"))
    ap.add_argument("--companies", default=str(HERE / "companies.json"))
    ap.add_argument("--out", default="", help="output path (default: rewrite --companies)")
    ap.add_argument("--workers", type=int, default=3, help="boards read at once (max 3)")
    ap.add_argument("--limit", type=int, default=0, help="first N seeds only (test runs)")
    ap.add_argument("--report", default="", help="optional JSON file with every board's result")
    a = ap.parse_args(argv)
    bc, kit, more = _modules()
    http = bc.make_http(kit)
    seeds = read_seeds(Path(a.seeds))
    if a.limit:
        seeds = seeds[: a.limit]
    results: dict[tuple[str, str], dict[str, Any] | None] = {}

    def one(seed: tuple[str, str, str]) -> None:
        time.sleep(0.3)  # polite: a short gap per board on top of the worker cap
        results[(seed[0], seed[1])] = probe(*seed, http, more)

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(3, a.workers))) as ex:
        list(ex.map(one, seeds))
    rows = json.loads(Path(a.companies).read_text(encoding="utf-8"))
    rows = [r for r in rows if not (r.get("source") == SOURCE and r.get("ats") in ATS)]
    have = {(r.get("ats"), str(r.get("board") or "").lower()) for r in rows}
    added: list[dict[str, Any]] = []
    for (ats, tenant), res in sorted(results.items()):
        if not res or (ats, tenant) in have:
            continue
        row: dict[str, Any] = {"name": res["name"], "ats": ats, "board": tenant, "source": SOURCE}
        if res["india"] * 2 > res["jobs"]:
            row["country"] = "IN"
        if res["tld"]:
            row["tld"] = res["tld"]
        added.append(row)
    rows += added
    out = a.out or a.companies
    with open(out, "w", encoding="utf-8", newline="\n") as f:  # LF on every OS, like the committed file
        f.write(bc.dump_rows(rows))
    if a.report:
        Path(a.report).write_text(json.dumps({f"{k[0]}:{k[1]}": v for k, v in sorted(results.items())}, indent=1), encoding="utf-8")
    ok = sum(1 for v in results.values() if v)
    print(json.dumps({"seeds": len(seeds), "with_jobs": ok, "failed_or_empty": len(seeds) - ok, "rows_added": len(added),
                      "country_in": sum(1 for r in added if r.get("country") == "IN"), "total_rows": len(rows)}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
