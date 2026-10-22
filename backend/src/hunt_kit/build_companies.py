#!/usr/bin/env python3
"""Add employers from official visa-sponsor registers to companies.json (stdlib only, reproducible).

Sources (all official, public, free)
- Netherlands IND "Public register Work" (recognised sponsors for regular labour and highly skilled
  migrants): an HTML table on ind.nl, updated monthly. Register id NL-IND, country NL.
- UK Home Office "Register of licensed sponsors: workers": a CSV linked from the gov.uk publication
  page (Open Government Licence v3.0). Only "Skilled Worker" route rows. Register id GB-HO, country GB.
- Companies House "BasicCompanyDataAsOneFile" (free bulk product, Open Government Licence v3.0): the UK
  register has ~120,000 sponsors, so it is narrowed by DATA, not keywords: a UK sponsor is kept only
  when an active Companies House company with the same normalized name has a SIC code starting with
  62, 63 or 72 (computer programming / IT, information services, scientific R&D). --uk-all skips it.

For every kept employer the script probes ONLY two strong board names (the normalized name joined,
and hyphenated) on the ATS endpoints the kit already reads (greenhouse, lever, ashby, workable,
recruitee, personio, teamtailor, smartrecruiters, join). A board is accepted only if it has >= 1 job
AND either its own company name normalizes equal to the register name ("match": "name"), or the ATS
gives no company name and the board name is the full joined/hyphenated name, >= 6 chars
("match": "slug"). Anything else is skipped. Boards already in companies.json get the register fields
added; they are never duplicated.

Usage:
    python build_companies.py                         # NL + UK, downloads everything
    python build_companies.py --only nl --limit 200 --out /tmp/test.json
    python build_companies.py --ch BasicCompanyDataAsOneFile-2026-10-01.zip
    python build_companies.py --uk-all                # every UK Skilled Worker sponsor (slow)

Probe results are cached on disk (--cache, default ./build_companies_cache), so a stopped run
(Ctrl-C, --max-minutes) continues where it left off when rerun with the same command.

The tables LEGAL_SUFFIXES and GEO_SUFFIXES below are the ONLY hand-written name data; like
REGION_RULES in build_places.py they are small, explicit and documented.
"""
# ruff: noqa: UP031, S310, S311, E501
from __future__ import annotations

import argparse
import concurrent.futures
import csv
import html
import html.parser
import http.client
import importlib
import io
import json
import random
import re
import sys
import threading
import time
import unicodedata
import urllib.parse
import zipfile
from pathlib import Path
from typing import Any, Callable, Iterable

HERE = Path(__file__).resolve().parent
UA = "job360-hunt-kit-build/1.0 (+https://job360.uk)"

IND_URLS = (
    "https://ind.nl/en/public-register-recognised-sponsors/public-register-work",
    "https://ind.nl/en/public-register-recognised-sponsors/public-register-regular-labour-and-highly-skilled-migrants",
)
GOVUK_PAGE = "https://www.gov.uk/government/publications/register-of-licensed-sponsors-workers"
CH_PAGE = "https://download.companieshouse.gov.uk/en_output.html"
CH_BASE = "https://download.companieshouse.gov.uk/"
UK_ROUTE = "Skilled Worker"
SIC_PREFIXES = ("62", "63", "72")

REGISTERS = {  # register id -> (country, companies.json source)
    "NL-IND": ("NL", "nl-ind"),
    "GB-HO": ("GB", "gb-ho"),
}
REGISTER_SOURCES = {src for _, src in REGISTERS.values()}

PER_HOST = 6  # concurrent probes per ATS (the kit's own runs use 3 per host)
JITTER_S = 0.15  # random wait before each probe
MIN_SLUG_MATCH = 6  # a board with no company name of its own needs a name at least this long
TEMPLATE_BOARDS = 3  # this many boards on one ATS with the very same jobs = demo content (see collect_hits)

# Legal-form words at the END of a company name, as fold() spells them ("B.V." -> "b v").
# Stripped repeatedly, longest first, only while something is left.
LEGAL_SUFFIXES: tuple[tuple[str, ...], ...] = tuple(sorted({
    tuple(s.split()) for s in (
        # NL / BE
        "b v", "bv", "n v", "nv", "v o f", "vof", "c v", "cv", "b v b a", "bvba",
        # UK / IE / US
        "ltd", "limited", "plc", "p l c", "llp", "l l p", "lp", "inc", "incorporated", "llc", "l l c",
        "corp", "corporation", "co", "company", "cic", "uc", "dac",
        # DE / AT / CH
        "gmbh", "ag", "kg", "gmbh co kg", "ug", "se",
        # FR / ES / IT / PT / LU
        "sa", "s a", "sas", "s a s", "sarl", "s a r l", "sl", "s l", "srl", "s r l", "spa", "s p a", "lda",
        # Nordics / other
        "ab", "as", "a s", "aps", "asa", "oy", "oyj", "kft", "sp z o o", "pty", "pte", "pvt", "private",
        # holding words that name the same employer
        "holding", "holdings",
    )
}, key=lambda t: (-len(t), t)))
# Country / area words at the END of a name: "Acme Nederland B.V." and "Acme UK Ltd" are Acme's local entity.
GEO_SUFFIXES: tuple[tuple[str, ...], ...] = tuple(sorted({
    tuple(s.split()) for s in (
        "uk", "u k", "united kingdom", "great britain", "gb", "england", "scotland", "wales",
        "nederland", "netherlands", "the netherlands", "holland", "nl", "benelux", "europe", "emea", "eu",
    )
}, key=lambda t: (-len(t), t)))
TRADING = re.compile(r"\s+(?:t/a|t\\a|ta|trading as)\s+", re.I)


# ============================================================== names
def fold(s: str) -> str:
    """Lowercase, strip accents, keep only letters/digits separated by single spaces (same as the kit)."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    return " ".join(re.findall(r"[a-z0-9]+", s))


def norm_name(name: str) -> str:
    """Company name -> comparable key: folded, legal-form and country words stripped from the end."""
    w = fold(name).split()
    if w and w[0] == "the" and len(w) > 1:
        w = w[1:]
    changed = True
    while changed:
        changed = False
        for table in (LEGAL_SUFFIXES, GEO_SUFFIXES):
            for suf in table:
                n = len(suf)
                if len(w) > n and tuple(w[-n:]) == suf:
                    w = w[:-n]
                    changed = True
                    break
    return " ".join(w)


def squash(key: str) -> str:
    """A normalized name without spaces ("bol com" == "bolcom")."""
    return key.replace(" ", "")


def trading_names(name: str) -> list[str]:
    """'Acme Ltd T/A Widgets' -> ['Acme Ltd', 'Widgets'] (the legal name first)."""
    return [p.strip() for p in TRADING.split(name or "") if p.strip()]


def slug_variants(key: str) -> list[str]:
    """The two strong board names for a normalized name: joined and hyphenated (no first-word, hq, inc, ai)."""
    w = key.split()
    out: list[str] = []
    for s in ("".join(w), "-".join(w)):
        if len(s) >= 3 and s not in out:
            out.append(s)
    return out


# ============================================================== registers
class _IndTable(html.parser.HTMLParser):
    """Collect (organisation, KVK number) pairs from the IND register table rows."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._in_table = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "table":
            self._in_table += 1
        elif self._in_table and tag == "tr":
            self._row = []
        elif self._row is not None and tag in ("td", "th"):
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "table":
            self._in_table = max(0, self._in_table - 1)
        elif tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join("".join(self._cell).split()))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if len(self._row) >= 2:
                self.rows.append((self._row[0], self._row[1]))
            self._row = None

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)


def parse_ind(page: str) -> list[dict[str, str]]:
    """IND register page -> [{name, kvk}] (header row and rows without an 8-digit KVK number skipped)."""
    p = _IndTable()
    p.feed(page)
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for org, kvk in p.rows:
        org = " ".join(org.replace('"', " ").split())  # the page doubles quotes: ""Aa-Dee"" Machinefabriek
        if not org or not re.fullmatch(r"\d{8}", kvk.strip()) or org.lower() in seen:
            continue
        seen.add(org.lower())
        out.append({"name": org, "kvk": kvk.strip()})
    return out


def govuk_csv_link(page: str) -> str:
    """The register CSV asset link on the gov.uk publication page ('' if none)."""
    for m in re.finditer(r'href="(https://assets\.publishing\.service\.gov\.uk/[^"]+\.csv)"', page):
        return html.unescape(m.group(1))
    return ""


def parse_uk_csv(text: str, route: str = UK_ROUTE) -> list[dict[str, str]]:
    """Home Office register CSV -> [{name, town}] for one route, deduped by name."""
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for r in csv.DictReader(io.StringIO(text.lstrip("﻿"))):
        r = {(k or "").strip(): (v or "").strip() for k, v in r.items()}
        if r.get("Route", "") != route:
            continue
        name = " ".join(r.get("Organisation Name", "").split())
        if not name or name.lower() in seen:
            continue
        seen.add(name.lower())
        out.append({"name": name, "town": r.get("Town/City", "")})
    return out


def ch_zip_link(page: str) -> str:
    """The BasicCompanyDataAsOneFile zip link on the Companies House download page ('' if none)."""
    m = re.search(r'href="([^"]*BasicCompanyDataAsOneFile[^"]*\.zip)"', page)
    return urllib.parse.urljoin(CH_BASE, m.group(1)) if m else ""


def ch_sic_names(rows: Iterable[dict[str, str]], prefixes: tuple[str, ...] = SIC_PREFIXES) -> set[str]:
    """Normalized names of ACTIVE companies whose SIC codes start with one of `prefixes`."""
    keep: set[str] = set()
    for r in rows:
        if (r.get("CompanyStatus") or "").strip().lower() != "active":
            continue
        sics = [(r.get("SICCode.SicText_%d" % i) or "").strip() for i in range(1, 5)]
        if any(s[:2] in prefixes for s in sics if s[:1].isdigit()):
            key = norm_name(r.get("CompanyName") or "")
            if key:
                keep.add(key)
    return keep


def read_ch_zip(path: Path) -> Iterable[dict[str, str]]:
    """Stream the Companies House CSV out of its zip (header names stripped of spaces)."""
    with zipfile.ZipFile(path) as z:
        name = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        with z.open(name) as raw:
            f = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
            rd = csv.reader(f)
            head = [h.strip() for h in next(rd)]
            for row in rd:
                yield dict(zip(head, row))


# ============================================================== downloads
def _fetch(url: str, dest: Path | None = None, _hops: int = 5) -> bytes:
    """GET a fixed https dataset URL (https redirects only). With `dest`, streams to that file instead."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise ValueError(f"refusing non-https URL: {url}")
    conn = http.client.HTTPSConnection(parts.hostname, timeout=120)
    try:
        path = parts.path + (f"?{parts.query}" if parts.query else "")
        conn.request("GET", path or "/", headers={"User-Agent": UA})
        resp = conn.getresponse()
        if resp.status in (301, 302, 303, 307, 308) and _hops > 0:
            location = urllib.parse.urljoin(url, resp.getheader("Location") or "")
            return _fetch(location, dest, _hops - 1)
        if resp.status != 200:
            raise OSError(f"HTTP {resp.status} for {url}")
        if dest is None:
            return resp.read()
        tmp = dest.with_suffix(dest.suffix + ".part")
        with open(tmp, "wb") as out:
            while True:
                chunk = resp.read(1 << 20)
                if not chunk:
                    break
                out.write(chunk)
        tmp.replace(dest)
        return b""
    finally:
        conn.close()


def load_nl(cache: Path) -> list[dict[str, str]]:
    """Download (once per run) and parse the IND register."""
    last: Exception | None = None
    for url in IND_URLS:
        try:
            page = _fetch(url).decode("utf-8", "replace")
        except OSError as e:
            last = e
            continue
        (cache / "ind_register.html").write_text(page, encoding="utf-8")
        rows = parse_ind(page)
        if rows:
            return rows
    raise SystemExit("could not read the IND register (%s); check %s" % (last, IND_URLS[0]))


def load_gb(cache: Path) -> list[dict[str, str]]:
    """Find the current CSV on the gov.uk page, download and parse it."""
    link = govuk_csv_link(_fetch(GOVUK_PAGE).decode("utf-8", "replace"))
    if not link:
        raise SystemExit("no .csv link found on %s" % GOVUK_PAGE)
    text = _fetch(link).decode("utf-8-sig", "replace")
    (cache / "uk_register.csv").write_text(text, encoding="utf-8")
    print("UK register: %s" % link, file=sys.stderr)
    return parse_uk_csv(text)


def ch_zip_path(cache: Path, given: str | None) -> Path:
    """--ch PATH, or the current Companies House zip (downloaded once into the cache)."""
    if given:
        p = Path(given)
        if not p.is_file():
            raise SystemExit("--ch %s: no such file" % given)
        return p
    try:
        link = ch_zip_link(_fetch(CH_PAGE).decode("utf-8", "replace"))
        if not link:
            raise OSError("no BasicCompanyDataAsOneFile link on %s" % CH_PAGE)
        name = link.rsplit("/", 1)[-1]
        # The name comes from a scraped href: never let it become a path outside the cache.
        if not re.fullmatch(r"BasicCompanyDataAsOneFile-[A-Za-z0-9-]+\.zip", name):
            raise OSError("unexpected Companies House file name %r" % name[:80])
        dest = cache / name
        if not dest.is_file():
            print("downloading %s (about 500 MB) ..." % link, file=sys.stderr, flush=True)
            _fetch(link, dest)
        return dest
    except OSError as e:
        raise SystemExit(
            "Companies House bulk data could not be fetched (%s).\n"
            "The UK list is only narrowed with it; without it the run would probe ~120,000 sponsors.\n"
            "Download BasicCompanyDataAsOneFile-*.zip from %s and pass --ch PATH, or use --uk-all on purpose."
            % (e, CH_PAGE)
        ) from None


# ============================================================== probes
Http = Callable[..., "tuple[int, Any]"]
MISS: dict[str, Any] = {"n": 0}


def _kit() -> Any:
    """The sibling hunt_kit module (its http helper, safe XML and JOIN page reader)."""
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    return importlib.import_module("hunt_kit")


def _miss_or_err(st: int) -> dict[str, Any]:
    """A clear 4xx (not 429) is a definitive miss; 0 / 429 / 5xx is an error to retry on the next run."""
    return dict(MISS) if 400 <= st < 500 and st != 429 else {"err": st}


def _hit(name: Any, titles: list[str], locs: list[str], n: int | None = None) -> dict[str, Any]:
    nm = str(name).strip() if isinstance(name, str) else ""
    return {"n": len(titles) if n is None else n, "name": nm or None, "t": titles[:3],
            "loc": list(dict.fromkeys(x for x in locs if x))[:8]}


def _s(v: Any) -> str:
    return str(v).strip() if isinstance(v, (str, int, float)) else ""


def probe_greenhouse(http: Http, slug: str) -> dict[str, Any]:
    st, d = http("https://boards-api.greenhouse.io/v1/boards/%s" % slug)
    if st != 200 or not isinstance(d, dict):
        return _miss_or_err(st)
    st, j = http("https://boards-api.greenhouse.io/v1/boards/%s/jobs" % slug)
    if st != 200 or not isinstance(j, dict):
        return _miss_or_err(st)
    jobs = [x for x in j.get("jobs") or [] if isinstance(x, dict)]
    return _hit(d.get("name"), [_s(x.get("title")) for x in jobs],
                [_s((x.get("location") or {}).get("name")) for x in jobs if isinstance(x.get("location"), dict)])


def probe_lever(http: Http, slug: str) -> dict[str, Any]:
    res = dict(MISS)
    for base in ("https://api.lever.co", "https://api.eu.lever.co"):
        st, d = http("%s/v0/postings/%s?mode=json" % (base, slug))
        if st == 200 and isinstance(d, list):
            jobs = [x for x in d if isinstance(x, dict)]
            return _hit(None, [_s(x.get("text")) for x in jobs],
                        [_s((x.get("categories") or {}).get("location")) for x in jobs if isinstance(x.get("categories"), dict)])
        res = _miss_or_err(st)
        if "err" in res:
            return res
    return res


def probe_ashby(http: Http, slug: str) -> dict[str, Any]:
    st, d = http("https://api.ashbyhq.com/posting-api/job-board/%s" % slug)
    if st != 200 or not isinstance(d, dict):
        return _miss_or_err(st)
    jobs = [x for x in d.get("jobs") or [] if isinstance(x, dict)]
    return _hit(None, [_s(x.get("title")) for x in jobs], [_s(x.get("location")) for x in jobs])


def probe_workable(http: Http, slug: str) -> dict[str, Any]:
    st, d = http("https://apply.workable.com/api/v1/widget/accounts/%s" % slug)
    if st != 200 or not isinstance(d, dict):
        return _miss_or_err(st)
    jobs = [x for x in d.get("jobs") or [] if isinstance(x, dict)]
    return _hit(d.get("name"), [_s(x.get("title")) for x in jobs],
                [", ".join(p for p in (_s(x.get("city")), _s(x.get("country"))) if p) for x in jobs])


def probe_recruitee(http: Http, slug: str) -> dict[str, Any]:
    st, d = http("https://%s.recruitee.com/api/offers/" % slug)
    if st != 200 or not isinstance(d, dict):
        return _miss_or_err(st)
    offers = [x for x in d.get("offers") or [] if isinstance(x, dict)]
    name = next((x.get("company_name") for x in offers if x.get("company_name")), None)
    return _hit(name, [_s(x.get("title")) for x in offers], [_s(x.get("location")) for x in offers])


def probe_personio(http: Http, slug: str) -> dict[str, Any]:
    # 5 MB: a Personio XML feed carries every posting with its description
    st, text = http("https://%s.jobs.personio.de/xml" % slug, as_json=False, max_bytes=5_000_000)
    if st != 200 or not isinstance(text, str):
        return _miss_or_err(st)
    if "<workzag-jobs" not in text:
        return dict(MISS)
    root = _kit()._import_kit("kit_xml").safe_fromstring(text)
    if root is None:
        return dict(MISS)
    pos = root.findall("position")
    return _hit(None, [(p.findtext("name") or "").strip() for p in pos], [(p.findtext("office") or "").strip() for p in pos])


_TT = "{https://teamtailor.com/locations}"


def probe_teamtailor(http: Http, slug: str) -> dict[str, Any]:
    st, text = http("https://%s.teamtailor.com/jobs.rss" % slug, as_json=False, max_bytes=5_000_000)  # RSS: full descriptions
    if st != 200 or not isinstance(text, str):
        return _miss_or_err(st)
    if "<rss" not in text:
        return dict(MISS)
    root = _kit()._import_kit("kit_xml").safe_fromstring(text)
    if root is None:
        return dict(MISS)
    items = list(root.iter("item"))
    locs = [", ".join(p for p in ((loc.findtext(_TT + "city") or "").strip(), (loc.findtext(_TT + "country") or "").strip()) if p)
            for it in items for loc in it.iter(_TT + "location")]
    return _hit(root.findtext("channel/title"), [(i.findtext("title") or "").strip() for i in items], locs)


def probe_smartrecruiters(http: Http, slug: str) -> dict[str, Any]:
    st, d = http("https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100" % slug)
    if st != 200 or not isinstance(d, dict):
        return _miss_or_err(st)
    jobs = [x for x in d.get("content") or [] if isinstance(x, dict)]
    name = next(((x.get("company") or {}).get("name") for x in jobs if isinstance(x.get("company"), dict)), None)
    total = d.get("totalFound")
    return _hit(name, [_s(x.get("name")) for x in jobs],
                [", ".join(p for p in (_s((x.get("location") or {}).get("city")), _s((x.get("location") or {}).get("country"))) if p)
                 for x in jobs if isinstance(x.get("location"), dict)],
                n=total if isinstance(total, int) and jobs else None)


def probe_join(http: Http, slug: str) -> dict[str, Any]:
    st, page = http("https://join.com/companies/%s" % slug, as_json=False, headers={"Accept": "text/html"})
    if st != 200 or not isinstance(page, str):
        return _miss_or_err(st)
    data = _kit()._import_kit("kit_ats_more")._next_data(page)
    state = (((data or {}).get("props") or {}).get("pageProps") or {}).get("initialState") if isinstance(data, dict) else None
    if not isinstance(state, dict):
        return dict(MISS)
    items = [x for x in ((state.get("jobs") or {}).get("items") or []) if isinstance(x, dict)]
    company = state.get("company")
    name = company.get("name") if isinstance(company, dict) else None
    return _hit(name, [_s(x.get("title")) for x in items],
                [_s((x.get("city") or {}).get("cityName")) for x in items if isinstance(x.get("city"), dict)])


# The endpoints are the ones the kit itself reads (hunt_kit.py discover/_gh/_lever/_ashby/_smartrecruiters,
# kit_ats.py workable/recruitee/personio/teamtailor, kit_ats_more.py join). Greenhouse adds the board root
# (/v1/boards/<slug>), which carries the board's company name.
PROBES: dict[str, Callable[[Http, str], dict[str, Any]]] = {
    "greenhouse": probe_greenhouse,
    "lever": probe_lever,
    "ashby": probe_ashby,
    "workable": probe_workable,
    "recruitee": probe_recruitee,
    "personio": probe_personio,
    "teamtailor": probe_teamtailor,
    "smartrecruiters": probe_smartrecruiters,
    "join": probe_join,
}


def accept(key: str, slug: str, res: dict[str, Any]) -> str | None:
    """The precision rule: 'name', 'slug' or None for one register name and one probe result."""
    if not res or int(res.get("n") or 0) < 1:
        return None
    board_name = norm_name(res.get("name") or "")
    if board_name:  # the ATS names the company: it must be the register's company
        return "name" if squash(board_name) == squash(key) else None
    if slug in slug_variants(key) and len(slug) >= MIN_SLUG_MATCH:
        return "slug"
    return None


# ============================================================== cache + runner
class ProbeCache:
    """Append-only JSON-lines file of probe results ("ats:slug" -> result). Errors are not kept."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.data: dict[str, dict[str, Any]] = {}
        self.lock = threading.Lock()
        if path and path.is_file():
            for line in path.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue  # a line cut by a hard stop
                if isinstance(rec, dict) and isinstance(rec.get("k"), str) and isinstance(rec.get("r"), dict):
                    self.data[rec["k"]] = rec["r"]
        self._f = open(path, "a", encoding="utf-8") if path else None  # noqa: SIM115 - closed in close()

    def get(self, k: str) -> dict[str, Any] | None:
        return self.data.get(k)

    def put(self, k: str, r: dict[str, Any]) -> None:
        with self.lock:
            self.data[k] = r
            if self._f is not None and "err" not in r:
                self._f.write(json.dumps({"k": k, "r": r}, ensure_ascii=False) + "\n")
                self._f.flush()

    def close(self) -> None:
        if self._f is not None:
            self._f.close()


class Throttle:
    """One ATS's pace: a minimum gap between requests, and a shared pause after a 429 (doubling, up to 5 min)."""

    def __init__(self, min_gap: float = 0.0) -> None:
        self.min_gap = min_gap
        self.lock = threading.Lock()
        self.next_at = 0.0
        self.backoff = 0.0

    def wait(self) -> None:
        with self.lock:
            now = time.monotonic()
            at = max(now, self.next_at)
            self.next_at = at + self.min_gap
        if at > now:
            time.sleep(at - now)

    def throttled(self) -> float:
        with self.lock:
            self.backoff = min(300.0, max(15.0, self.backoff * 2))
            self.next_at = max(self.next_at, time.monotonic() + self.backoff)
            return self.backoff

    def ok(self) -> None:
        with self.lock:
            self.backoff = self.backoff / 2 if self.backoff > 15.0 else 0.0


# Minimum seconds between two probes on one ATS. Workable and Recruitee answer 429 to bursts: one at a time.
MIN_GAP = {"workable": 1.0, "recruitee": 1.0}
SERIAL = {"workable", "recruitee"}


def run_probes(
    tasks: list[tuple[str, str]], http: Http, cache: ProbeCache, per_host: int = PER_HOST,
    deadline: float = 0.0, jitter: float = JITTER_S,
) -> tuple[dict[tuple[str, str], dict[str, Any]], int]:
    """Probe each (ats, slug) once, at most `per_host` at a time per ATS. Returns (results, tasks left undone).

    A 429 pauses that ATS for everyone and puts the board back in the queue; it is never cached.
    """
    results: dict[tuple[str, str], dict[str, Any]] = {}
    queues: dict[str, list[str]] = {}
    for ats, slug in tasks:
        hit = cache.get("%s:%s" % (ats, slug))
        if hit is not None and "err" not in hit:
            results[(ats, slug)] = hit
        else:
            queues.setdefault(ats, []).append(slug)
    todo = sum(len(q) for q in queues.values())
    pace = {ats: Throttle(MIN_GAP.get(ats, 0.0)) for ats in queues}
    lock = threading.Lock()
    done = {"n": 0, "hits": 0, "err": 0}
    t0 = time.monotonic()
    last = [t0]

    def worker(ats: str, q: list[str]) -> None:
        while True:
            if deadline and time.monotonic() > deadline:
                return
            with lock:
                if not q:
                    return
                slug = q.pop()
            pace[ats].wait()
            if jitter:
                time.sleep(random.uniform(0, jitter))
            try:
                r = PROBES[ats](http, slug)
            except Exception as e:  # noqa: BLE001 - one odd answer must not stop the sweep
                r = {"err": "%s" % type(e).__name__}
            if r.get("err") == 429:
                with lock:
                    q.append(slug)  # try it again after the pause
                print("%s: 429, pausing %.0fs" % (ats, pace[ats].throttled()), file=sys.stderr, flush=True)
                continue
            pace[ats].ok()
            cache.put("%s:%s" % (ats, slug), r)
            with lock:
                results[(ats, slug)] = r
                done["n"] += 1
                done["hits"] += int(r.get("n") or 0) > 0
                done["err"] += "err" in r
                now = time.monotonic()
                if now - last[0] > 30 or done["n"] == todo:
                    last[0] = now
                    print("probes %d/%d, boards with jobs %d, errors %d, %.0fs"
                          % (done["n"], todo, done["hits"], done["err"], now - t0), file=sys.stderr, flush=True)

    threads = [threading.Thread(target=worker, args=(ats, q), daemon=True)
               for ats, q in queues.items() for _ in range(1 if ats in SERIAL else max(1, per_host))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    left = sum(len(q) for q in queues.values())
    return results, left


def make_http(kit: Any) -> Http:
    """The kit's http helper (public hosts only, honest User-Agent, per-host limit) without its quick 429 retries."""

    def h(url: str, *, as_json: bool = True, headers: dict[str, str] | None = None) -> tuple[int, Any]:
        st, _u, body = kit.http_request(url, retries=0, headers=headers)
        if not 200 <= st < 300:
            return st, None
        text = kit.decode_body(body)
        if not as_json:
            return st, text
        try:
            return st, json.loads(text)
        except ValueError:
            return 0, None

    return h


# ============================================================== companies.json
def dump_rows(rows: list[dict[str, Any]]) -> str:
    """The companies.json text: one compact row per line (same layout as the committed file)."""
    return "[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in rows) + "\n]\n"


def _ids(v: Any) -> list[str]:
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def strip_registers(rows: list[dict[str, Any]], registers: Iterable[str]) -> list[dict[str, Any]]:
    """Undo a previous build for `registers`: their added rows go, their fields leave enriched rows."""
    drop = set(registers)
    out: list[dict[str, Any]] = []
    for r in rows:
        if "sponsor_register" not in r:
            out.append(r)
            continue
        regs = [x for x in _ids(r.get("sponsor_register")) if x not in drop and x in REGISTERS]
        added_by_register = set(_ids(r.get("source"))) <= REGISTER_SOURCES
        if not regs:
            if added_by_register:
                continue  # a row only a rebuilt register had added
            out.append({k: v for k, v in r.items() if k not in ("country", "sponsor_register", "match")})
            continue
        r = dict(r)
        r["country"] = ",".join(sorted({REGISTERS[x][0] for x in regs}))
        r["sponsor_register"] = ",".join(sorted(regs))
        if added_by_register:
            r["source"] = ",".join(sorted({REGISTERS[x][1] for x in regs}))
        out.append(r)
    return out


def merge(rows: list[dict[str, Any]], hits: dict[tuple[str, str], dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    """Add register fields to existing rows with the same ats+board, append the rest sorted. -> (rows, added, enriched)."""
    index = {(r.get("ats"), str(r.get("board") or r.get("token") or "").lower()): i for i, r in enumerate(rows)}
    out = [dict(r) for r in rows]
    added: list[dict[str, Any]] = []
    enriched = 0
    for (ats, slug), h in sorted(hits.items()):
        regs = set(h["registers"])
        i = index.get((ats, slug.lower()))
        if i is not None:
            row = out[i]
            if h["match"] == "slug" and squash(norm_name(str(row.get("name") or ""))) != squash(norm_name(h["name"])):
                continue  # the list already names this board's company, and it is another one
            regs |= set(_ids(row.get("sponsor_register")))
            if "sponsor_register" not in row:
                enriched += 1
            row["country"] = ",".join(sorted({REGISTERS[x][0] for x in regs}))
            row["sponsor_register"] = ",".join(sorted(regs))
            row["match"] = "name" if h["match"] == "name" or row.get("match") == "name" else "slug"
            if set(_ids(row.get("source"))) <= REGISTER_SOURCES:
                row["source"] = ",".join(sorted({REGISTERS[x][1] for x in regs}))
            continue
        added.append({
            "name": h["name"], "ats": ats, "board": slug,
            "source": ",".join(sorted({REGISTERS[x][1] for x in regs})),
            "country": ",".join(sorted({REGISTERS[x][0] for x in regs})),
            "sponsor_register": ",".join(sorted(regs)),
            "match": h["match"],
        })
    added.sort(key=lambda r: (r["ats"], r["board"]))
    return out + added, len(added), enriched


def collect_hits(
    targets: dict[str, dict[str, Any]], results: dict[tuple[str, str], dict[str, Any]]
) -> dict[tuple[str, str], dict[str, Any]]:
    """Apply the precision rule to every (register name, board) pair. 'name' beats 'slug' for one board.

    Demo and trial boards are dropped: when TEMPLATE_BOARDS or more boards on one ATS show the very same
    job count and first titles, that is the ATS's sample content, not a company's own jobs.
    """
    prints: dict[tuple[Any, ...], int] = {}
    for (ats, _), r in results.items():
        if int(r.get("n") or 0) > 0:
            fp = (ats, r.get("n"), tuple(r.get("t") or ()))
            prints[fp] = prints.get(fp, 0) + 1
    hits: dict[tuple[str, str], dict[str, Any]] = {}
    for key, t in sorted(targets.items()):
        for slug in slug_variants(key):
            for ats in PROBES:
                res = results.get((ats, slug))
                if res and prints.get((ats, res.get("n"), tuple(res.get("t") or ())), 0) >= TEMPLATE_BOARDS:
                    continue
                m = accept(key, slug, res or {})
                if not m:
                    continue
                h = hits.setdefault((ats, slug), {"name": t["name"], "registers": set(), "match": m,
                                                  "t": (res or {}).get("t", []), "n": (res or {}).get("n", 0)})
                h["registers"] |= t["registers"]
                if m == "name" and h["match"] != "name":
                    h["match"], h["name"] = "name", t["name"]
    return hits


# ============================================================== slug-hit check
# A "slug" hit is a guess (the ATS does not name the company). It is kept only when the board has at least one job
# in the register's own country. Boards whose every job is a sample/demo posting are dropped whatever the match.
SAMPLE_JOB = re.compile(r"\((?:sample|demo|test)\)|^\s*(?:sample|demo|test)\s+(?:job|vacancy|position|posting)\b", re.I)
VERIFY_MAX_JOBS = 500  # jobs kept per board in the verify cache


def board_jobs(kit: Any, http: Http, ats: str, slug: str) -> list[list[str]] | None:
    """[[title, location], ...] from the kit's own board readers, or None if the board did not answer."""
    b = {"name": slug, "ats": ats, "token": slug}
    if ats in ("greenhouse", "ashby", "smartrecruiters"):
        url = {"greenhouse": "https://boards-api.greenhouse.io/v1/boards/%s/jobs",
               "ashby": "https://api.ashbyhq.com/posting-api/job-board/%s",
               "smartrecruiters": "https://api.smartrecruiters.com/v1/companies/%s/postings?limit=100"}[ats] % slug
        st, d = http(url)
        if st != 200 or not isinstance(d, dict):
            return None
        parse = {"greenhouse": kit.parse_greenhouse, "ashby": kit.parse_ashby,
                 "smartrecruiters": kit.parse_smartrecruiters_list}[ats]
        return [[str(r.get("title") or ""), str(r.get("loc") or "")] for r in parse(d, b)][:VERIFY_MAX_JOBS]
    if ats == "lever":
        for base in ("https://api.lever.co", "https://api.eu.lever.co"):
            st, d = http("%s/v0/postings/%s?mode=json" % (base, slug))
            if st == 200 and isinstance(d, list):
                return [[str(r.get("title") or ""), str(r.get("loc") or "")] for r in kit.parse_lever(d, b)][:VERIFY_MAX_JOBS]
        return None
    fetch = kit._merged("ATS_FETCHERS", kit.ATS_MODULES).get(ats)
    if fetch is None:
        return None
    stats: dict[str, Any] = {}
    jobs = fetch(b, http, stats)
    if not stats.get("ok"):
        return None
    return [[str(j.get("title") or ""), str(j.get("location") or "")] for j in jobs][:VERIFY_MAX_JOBS]


def verify_hits(
    hits: dict[tuple[str, str], dict[str, Any]],
    fetch: Callable[[str, str], list[list[str]] | None],
    countries_of: Callable[[str], Iterable[str]],
) -> tuple[dict[tuple[str, str], dict[str, Any]], dict[str, int]]:
    """Keep slug hits with >= 1 job in a register country (only those registers); drop sample-only boards."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    st = {"slug_before": 0, "slug_kept": 0, "slug_no_country_job": 0, "unverified": 0, "sample_boards": 0}
    for key, h in sorted(hits.items()):
        slug_hit = h["match"] == "slug"
        st["slug_before"] += slug_hit
        if not slug_hit and not any(SAMPLE_JOB.search(t or "") for t in h.get("t") or []):
            out[key] = h
            continue
        jobs = fetch(*key)
        if jobs is None:
            if slug_hit:
                st["unverified"] += 1  # the board did not answer: not kept this run (and not cached)
            else:
                out[key] = h
            continue
        if jobs and all(SAMPLE_JOB.search(t) for t, _ in jobs):
            st["sample_boards"] += 1
            continue
        if not slug_hit:
            out[key] = h
            continue
        where: dict[str, str] = {}
        for _, loc in jobs:
            for cc in countries_of(loc):
                where.setdefault(cc, loc)
        regs = {r for r in h["registers"] if REGISTERS[r][0] in where}
        if not regs:
            st["slug_no_country_job"] += 1
            continue
        st["slug_kept"] += 1
        out[key] = dict(h, registers=regs, where=where[REGISTERS[sorted(regs)[0]][0]])
    return out, st


def cached_fetch(kit: Any, http: Http, cache: ProbeCache) -> Callable[[str, str], list[list[str]] | None]:
    """board_jobs with an on-disk cache (verify.jsonl; boards that did not answer are not kept)."""

    def fetch(ats: str, slug: str) -> list[list[str]] | None:
        k = "%s:%s" % (ats, slug)
        hit = cache.get(k)
        if hit is not None:
            return list(hit.get("jobs") or [])
        jobs = board_jobs(kit, http, ats, slug)
        if jobs is not None:
            cache.put(k, {"jobs": jobs})
        return jobs

    return fetch


def build_targets(entries: list[tuple[str, str]]) -> dict[str, dict[str, Any]]:
    """[(register id, register name)] -> normalized key -> {name, registers}. T/A trading names count too."""
    big = _kit().BIG_BOARD_NAME  # the kit never reads LinkedIn, Indeed or Glassdoor boards
    targets: dict[str, dict[str, Any]] = {}
    for reg, name in entries:
        if big.search(name):
            continue
        for i, part in enumerate(trading_names(name)):
            key = norm_name(part)
            if not slug_variants(key):
                continue
            t = targets.setdefault(key, {"name": trading_names(name)[0] if i == 0 else part, "registers": set()})
            t["registers"].add(reg)
    return targets


# ============================================================== main
def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    ap.add_argument("--only", choices=("nl", "gb"), help="one register only")
    ap.add_argument("--limit", type=int, default=0, help="first N employers per register (test runs)")
    ap.add_argument("--uk-all", action="store_true", help="do not narrow the UK list with Companies House SIC codes")
    ap.add_argument("--ch", help="local BasicCompanyDataAsOneFile-*.zip (default: download into the cache)")
    ap.add_argument("--cache", default="build_companies_cache", help="folder for downloads and the probe cache")
    ap.add_argument("--out", default=str(HERE / "companies.json"))
    ap.add_argument("--companies", default=str(HERE / "companies.json"), help="the existing list to extend")
    ap.add_argument("--max-minutes", type=float, default=0, help="stop probing after this long (rerun to continue)")
    ap.add_argument("--per-host", type=int, default=PER_HOST)
    ap.add_argument("--ats", default=",".join(PROBES),
                    help="probe only these ATS this run (cached results of the others still count)")
    ap.add_argument("--verify-slug-country", action=argparse.BooleanOptionalAction, default=True,
                    help="keep a slug hit only if its board has a job in the register country (default on, cached)")
    ap.add_argument("--hits", default="", help="also write every accepted hit with sample titles to this JSON file")
    a = ap.parse_args(argv)

    cache_dir = Path(a.cache)
    cache_dir.mkdir(parents=True, exist_ok=True)
    regs = ["NL-IND", "GB-HO"] if not a.only else (["NL-IND"] if a.only == "nl" else ["GB-HO"])
    entries: list[tuple[str, str]] = []
    counts: dict[str, Any] = {}
    if "NL-IND" in regs:
        nl = load_nl(cache_dir)
        counts["nl_register_rows"] = len(nl)
        nl = nl[: a.limit] if a.limit else nl
        entries += [("NL-IND", r["name"]) for r in nl]
    if "GB-HO" in regs:
        gb = load_gb(cache_dir)
        counts["gb_skilled_worker_rows"] = len(gb)
        if not a.uk_all:
            print("reading Companies House SIC codes ...", file=sys.stderr, flush=True)
            it_names = ch_sic_names(read_ch_zip(ch_zip_path(cache_dir, a.ch)))
            counts["ch_it_companies"] = len(it_names)
            gb = [r for r in gb if norm_name(trading_names(r["name"])[0]) in it_names]
            counts["gb_kept_after_sic"] = len(gb)
        gb = gb[: a.limit] if a.limit else gb
        entries += [("GB-HO", r["name"]) for r in gb]

    targets = build_targets(entries)
    tasks = sorted({(ats, s) for key in targets for s in slug_variants(key) for ats in PROBES})
    counts["names_probed"] = len(targets)
    counts["probe_tasks"] = len(tasks)
    print("%d employers -> %d names -> %d probes" % (len(entries), len(targets), len(tasks)), file=sys.stderr, flush=True)

    kit = _kit()
    kit.MAX_PER_HOST = max(1, min(8, a.per_host))  # the kit's own per-host limit, raised for this one-off build
    cache = ProbeCache(cache_dir / "probes.jsonl")
    deadline = time.monotonic() + a.max_minutes * 60 if a.max_minutes else 0.0
    try:
        wanted = {x.strip() for x in a.ats.split(",") if x.strip()}
        results, left = run_probes([t for t in tasks if t[0] in wanted], make_http(kit), cache,
                                   per_host=kit.MAX_PER_HOST, deadline=deadline)
    finally:
        cache.close()
    for t in tasks:  # boards of the ATS not probed this run: their cached answers still count
        r = cache.get("%s:%s" % t)
        if t not in results and r is not None and "err" not in r:
            results[t] = r

    hits = collect_hits(targets, results)
    if a.verify_slug_country:
        vcache = ProbeCache(cache_dir / "verify.jsonl")
        fetch = cached_fetch(kit, kit.http, vcache)
        todo = [k for k, h in hits.items() if h["match"] == "slug" or any(SAMPLE_JOB.search(t or "") for t in h.get("t") or [])]
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as ex:  # warm the cache in parallel
            list(ex.map(lambda k: fetch(*k), todo))
        try:
            hits, vstats = verify_hits(hits, fetch, lambda loc: kit.parse_loc(loc).countries)
        finally:
            vcache.close()
        counts["verify"] = vstats
    rows = json.loads(Path(a.companies).read_text(encoding="utf-8"))
    rows = strip_registers(rows, regs)
    rows, added, enriched = merge(rows, hits)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:  # LF on every OS, like the committed file
        f.write(dump_rows(rows))
    by_ats: dict[str, int] = {}
    by_match: dict[str, int] = {}
    for (ats, _), h in hits.items():
        by_ats[ats] = by_ats.get(ats, 0) + 1
        by_match[h["match"]] = by_match.get(h["match"], 0) + 1
    counts.update(hits=len(hits), hits_by_ats=dict(sorted(by_ats.items())), hits_by_match=by_match,
                  rows_added=added, rows_enriched=enriched, probes_left=left, total_rows=len(rows))
    if a.hits:
        Path(a.hits).write_text(json.dumps(
            [{"ats": k[0], "board": k[1], "name": h["name"], "match": h["match"], "registers": sorted(h["registers"]),
              "jobs": h["n"], "sample_titles": h["t"], "job_in_country": h.get("where", "")}
             for k, h in sorted(hits.items())],
            ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(counts, indent=1))
    if left:
        print("stopped with %d probes left; rerun the same command to continue (cache: %s)" % (left, cache_dir),
              file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
