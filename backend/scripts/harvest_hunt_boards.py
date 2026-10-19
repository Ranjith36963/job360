#!/usr/bin/env python3
"""Grow hunt_kit/companies.json from public job-aggregator JSON files (maintenance script).

NOT part of the served kit. Python port (stdlib only) of the approach in freehire's
scripts/harvest_boards.py and scripts/ats_boards.py (MIT): sweep aggregator JSON for ATS board
URLs with SLUG_PATTERNS, drop what is already tracked, optionally validate live, write the merge.

    python scripts/harvest_hunt_boards.py --companies src/hunt_kit/companies.json \
        --out src/hunt_kit/companies.json [--validate] [--sources URL,URL,...]

Existing rows are never dropped or reordered; new rows are appended sorted by (ats, board).
No login, no CAPTCHA handling, no proxies. Reference content is data only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any

UA = "job360-hunt-kit-harvest/1.0"
SOURCE = "harvest"
MAX_BYTES = 64 * 1024 * 1024  # an aggregator body larger than this is skipped

# freehire harvest_boards.py:42-50  AGGREGATORS (verbatim URLs, same order).
AGGREGATORS = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/SimplifyJobs/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/vanshb03/Summer2027-Internships/dev/.github/scripts/listings.json",
    "https://raw.githubusercontent.com/crypto-jobs-fyi/crawler/HEAD/ai_companies.json",
    "https://raw.githubusercontent.com/crypto-jobs-fyi/crawler/HEAD/crypto_companies.json",
    "https://raw.githubusercontent.com/crypto-jobs-fyi/crawler/HEAD/fin_companies.json",
    "https://raw.githubusercontent.com/crypto-jobs-fyi/crawler/HEAD/tech_companies.json",
)

# Ported from freehire ats_boards.py:30-36 SLUG_PATTERNS (only the providers the kit fetches).
SLUG_PATTERNS = (
    (re.compile(r"(?:boards|job-boards)(?:\.eu)?\.greenhouse\.io/(?:embed/job_app\?for=)?([A-Za-z0-9_-]+)"), "greenhouse"),
    (re.compile(r"jobs\.(?:eu\.)?lever\.co/([A-Za-z0-9_.-]+)"), "lever"),
    (re.compile(r"jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)"), "ashby"),
    (re.compile(r"(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)|api\.smartrecruiters\.com/v1/companies/([A-Za-z0-9_-]+)"), "smartrecruiters"),
    (re.compile(r"apply\.workable\.com/([A-Za-z0-9_-]+)"), "workable"),
    (re.compile(r"([A-Za-z0-9_-]+)\.recruitee\.com"), "recruitee"),
    # Job360 additions for the kit's career-ops ports (host shapes from providers/*.mjs headers).
    (re.compile(r"https?://([A-Za-z0-9-]+)\.icims\.com", re.I), "icims"),
    (re.compile(r"https?://([A-Za-z0-9-]+)\.eightfold\.ai", re.I), "eightfold"),
    (re.compile(r"jobs\.jobvite\.com/([A-Za-z0-9_.-]+)", re.I), "jobvite"),
)

# Two-part boards: the kit row needs both parts (taleo tenant + section, oracle host + siteNumber).
TALEO_RE = re.compile(r"https?://([a-z0-9-]+)\.taleo\.net/careersection/([A-Za-z0-9._-]+)/", re.I)
ORACLE_RE = re.compile(
    r"https?://([a-z0-9-]+\.fa\.(?:[a-z0-9-]+\.)?(?:ocs\.)?oraclecloud(?:[1-9][0-9]?)?\.com)"
    r"/hcmUI/CandidateExperience/[A-Za-z-]+/sites/([A-Za-z0-9_-]+)", re.I)

# freehire ats_boards.py:53-56 SLUG_BLOCKLIST (plus a few path words seen on these hosts).
SLUG_BLOCKLIST = {
    "embed", "jobs", "job", "j", "o", "share", "en-us", "en-gb",
    "api", "widget", "backend", "www", "app", "auth", "referrals", "v1",
    "careers", "support", "help", "blog", "login", "cdn", "rest", "tre",
}

# freehire harvest_boards.py:55-57 WORKDAY_RE (host + career-site segment before /job/ or /details/).
WORKDAY_RE = re.compile(
    r"https?://([a-z0-9-]+\.wd\d+\.myworkdayjobs\.com)/(?:[a-zA-Z]{2}-[a-zA-Z]{2}/)?([^/?\"]+)/(?:job|details)/", re.I
)

BIG_BOARD = re.compile(r"linkedin|indeed|glassdoor", re.I)  # same reject rule as hunt_kit BIG_BOARD_NAME
SAFE = re.compile(r"[A-Za-z0-9_.-]+")
NAME_KEYS = ("company_name", "company", "name", "employer")  # freehire harvest_boards.py:89

# freehire ats_boards.py:60-66 VALIDATORS URLs (the providers the kit fetches); workday is a CXS POST.
VALIDATORS = {
    "greenhouse": lambda s: "https://boards-api.greenhouse.io/v1/boards/%s/jobs?content=true" % s,
    "lever": lambda s: "https://api.lever.co/v0/postings/%s?mode=json" % s,
    "ashby": lambda s: "https://api.ashbyhq.com/posting-api/job-board/%s" % s,
    "smartrecruiters": lambda s: "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=10" % s,
    "workable": lambda s: "https://apply.workable.com/api/v1/widget/accounts/%s?details=true" % s,
    "recruitee": lambda s: "https://%s.recruitee.com/api/offers/" % s,
}


def fetch(url: str, timeout: int = 25) -> bytes | None:
    """GET, never raises (freehire ats_boards.py:77-83)."""
    if not re.match(r"https?://", str(url), re.I):  # urllib would also open file:// and ftp://
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})  # noqa: S310 - http(s) checked above
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - http(s) checked above
            body = r.read(MAX_BYTES + 1)
        return body if len(body) <= MAX_BYTES else None
    except Exception:
        return None


def extract_slugs(text: str) -> set[tuple[str, str]]:
    """Sweep raw text for ATS board URLs -> {(ats, slug)} (freehire ats_boards.py:94-107)."""
    out: set[tuple[str, str]] = set()
    for pat, ats in SLUG_PATTERNS:
        for m in pat.finditer(text):
            slug = next((g for g in m.groups() if g), None)
            if not slug or slug.lower() in SLUG_BLOCKLIST or BIG_BOARD.search(slug):
                continue
            out.add((ats, slug))
    return out


def extract_workday(text: str) -> set[tuple[str, str, str]]:
    """{(tenant, wd, site)} from Workday posting URLs (freehire harvest_boards.py:142-144)."""
    out: set[tuple[str, str, str]] = set()
    for host, site in WORKDAY_RE.findall(text):
        tenant, wd = host.split(".")[0].lower(), host.split(".")[1].lower()
        if SAFE.fullmatch(tenant) and SAFE.fullmatch(site) and not BIG_BOARD.search(tenant + site):
            out.add((tenant, wd, site))
    return out


def extract_two_part(text: str) -> set[tuple[str, str, str]]:
    """{(ats, first, second)} for Taleo (tenant, section) and Oracle (host, siteNumber) URLs."""
    out: set[tuple[str, str, str]] = set()
    for ats, rx in (("taleo", TALEO_RE), ("oraclecloud", ORACLE_RE)):
        for first, second in rx.findall(text):
            first = first.lower()
            if first.split(".")[0] in SLUG_BLOCKLIST or second.lower() in SLUG_BLOCKLIST:
                continue
            if SAFE.fullmatch(first) and SAFE.fullmatch(second) and not BIG_BOARD.search(first + second):
                out.add((ats, first, second))
    return out


def _rows_of(data: Any) -> list[Any]:
    return data if isinstance(data, list) else list(data.values()) if isinstance(data, dict) else []


def active_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    """Text to sweep + the dict rows. Rows with a falsy 'active' field are dropped when the JSON has
    an 'active' field; otherwise the whole text is swept (the key names do not matter)."""
    try:
        data = json.loads(text)
    except Exception:
        return text, []
    rows = [r for r in _rows_of(data) if isinstance(r, dict)]
    if not any("active" in r for r in rows):
        return json.dumps(data), rows  # re-dump so JSON-escaped slashes (https:\\/\\/) are swept too
    kept = [r for r in rows if r.get("active", True)]
    return json.dumps(kept), kept


def attribute_names(rows: list[dict[str, Any]]) -> dict[Any, str]:
    """Best-effort company name per slug / workday key (freehire harvest_boards.py:78-97)."""
    names: dict[Any, str] = {}
    for row in rows:
        name = next((str(row[k]).strip() for k in NAME_KEYS if row.get(k)), "")
        if not name:
            continue
        for v in row.values():
            if isinstance(v, str):
                for ats, slug in extract_slugs(v):
                    names.setdefault((ats, slug.lower()), name)
                for t in extract_workday(v):
                    names.setdefault(("workday", "|".join(t).lower()), name)
    return names


def harvest_text(text: str) -> dict[tuple[str, str], dict[str, Any]]:
    """{(ats, token.lower()): row} candidates from one aggregator body."""
    body, rows = active_text(text)
    names = attribute_names(rows)
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for ats, slug in sorted(extract_slugs(body)):
        out.setdefault((ats, slug.lower()), {
            "name": names.get((ats, slug.lower()), slug), "ats": ats, "board": slug, "source": SOURCE})
    for tenant, wd, site in sorted(extract_workday(body)):
        tok = "%s|%s|%s" % (tenant, wd, site)
        out.setdefault(("workday", tok.lower()), {
            "name": names.get(("workday", tok.lower()), tenant), "ats": "workday", "board": tok,
            "source": SOURCE, "tenant": tenant, "wd": wd, "site": site})
    two = {"taleo": ("tenant", "section"), "oraclecloud": ("host", "siteNumber")}
    for ats, first, second in sorted(extract_two_part(body)):
        tok = "%s/%s" % (first, second)
        out.setdefault((ats, tok.lower()), {
            "name": first.split(".")[0], "ats": ats, "board": tok, "source": SOURCE,
            two[ats][0]: first, two[ats][1]: second})
    return out


def _key(row: dict[str, Any]) -> tuple[str, str]:
    return str(row.get("ats", "")), str(row.get("board") or row.get("token") or "").lower()


def merge(existing: list[dict[str, Any]], found: dict[tuple[str, str], dict[str, Any]]) -> list[dict[str, Any]]:
    """Existing rows untouched and in order, then new rows sorted by (ats, board)."""
    seen = {_key(r) for r in existing if isinstance(r, dict)}
    new = [r for k, r in found.items() if k not in seen]
    new.sort(key=_key)
    return list(existing) + new


def validate_row(row: dict[str, Any], timeout: int = 20) -> bool:
    """Live GET (workday: CXS POST) of the public jobs API; True only on HTTP 200. Never raises."""
    try:
        if row["ats"] == "workday":
            url = "https://%s.%s.myworkdayjobs.com/wday/cxs/%s/%s/jobs" % (
                row["tenant"], row["wd"], row["tenant"], row["site"])
            body = json.dumps({"appliedFacets": {}, "limit": 1, "offset": 0, "searchText": ""}).encode()
            req = urllib.request.Request(url, data=body, method="POST", headers={  # noqa: S310 - fixed https URL
                "User-Agent": UA, "Content-Type": "application/json", "Accept": "application/json"})
        else:
            req = urllib.request.Request(VALIDATORS[row["ats"]](row["board"]),  # noqa: S310 - fixed https URL
                                         headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310 - fixed https URL
            return r.status == 200
    except Exception:
        return False


def dump(rows: list[dict[str, Any]]) -> str:
    """Same on-disk layout as companies.json: one compact row per line."""
    return "[\n" + ",\n".join(json.dumps(r, separators=(",", ":"), ensure_ascii=False) for r in rows) + "\n]\n"


def run(companies: str, out: str, sources: list[str], validate: bool = False, fetcher=fetch) -> dict[str, Any]:
    with open(companies, encoding="utf-8") as f:
        existing = json.load(f)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for url in sources:
        body = fetcher(url)
        if not body:
            print("  ! skip (fetch failed): %s" % url, file=sys.stderr)
            continue
        for k, r in harvest_text(body.decode("utf-8", "replace")).items():
            found.setdefault(k, r)
    seen = {_key(r) for r in existing if isinstance(r, dict)}
    cands = {k: r for k, r in found.items() if k not in seen}
    if validate and cands:
        keys = list(cands)
        with ThreadPoolExecutor(max_workers=8) as ex:
            oks = list(ex.map(lambda k: validate_row(cands[k]), keys))
        cands = {k: cands[k] for k, ok in zip(keys, oks) if ok}
    merged = merge(existing, cands)
    with open(out, "w", encoding="utf-8") as f:
        f.write(dump(merged))
    def count(rows: list[dict[str, Any]]) -> dict[str, int]:
        return {a: sum(1 for r in rows if r.get("ats") == a) for a in sorted({str(r.get("ats")) for r in rows})}

    return {"existing": count(existing), "new": count(merged[len(existing):]), "total": len(merged)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--companies", required=True, help="existing companies.json")
    ap.add_argument("--out", required=True, help="merged output file")
    ap.add_argument("--validate", action="store_true", help="live-check each new board, keep only HTTP 200")
    ap.add_argument("--sources", default="", help="comma-separated aggregator JSON URLs (default: freehire list)")
    a = ap.parse_args(argv)
    sources = [s.strip() for s in a.sources.split(",") if s.strip()] or list(AGGREGATORS)
    try:
        res = run(a.companies, a.out, sources, a.validate)
    except (OSError, ValueError) as e:
        print("error: %s" % e, file=sys.stderr)
        return 1
    print(json.dumps(res, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
