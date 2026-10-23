"""Startup boards for the Job360 hunt kit: Wellfound, Work at a Startup, Getro, Built In, Welcome to the Jungle.

Same contract as ``kit_bigboards.FEEDS`` / ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days,
countries) -> list[Job]``, never raises, networking only through the ``http`` callable the core passes in. No
login, no cookie, no proxy, no CAPTCHA solving. Nothing here ranks or scores.

HARD LIMITS per site per run: at most ``MAX_SEARCHES`` (3) searches, at most ``PAGES`` (2) result pages per
search, ``PAUSE_S`` seconds between two requests to one site, and the whole site stops at the first answer that
is not HTTP 200 and at the first page that is not the page it should be (a Cloudflare or login page): nothing is
retried or worked around (the core's http is called with ``once=True``). Standard library only.

Country gating: an empty ``countries`` list sends no request, ever. Built In is a US board: it runs only for
"US" (or "*", which the core passes for ``--visa-onsite``). Welcome to the Jungle is narrowed by the user's
country codes (offices.country_code); with "*" it searches with no country filter. Wellfound, Work at a Startup
and Getro take no country in their references, so they run for any non-empty ``countries``.

Every ad names its own employer (the startup, the portfolio company); the board name is never put in
``company``. ``url`` is the public ad address (Wellfound /jobs/<id>-<slug>, Work at a Startup search_path, Getro:
the first-party apply link the network lists, Built In /job/<slug>/<id>, WTTJ /en/companies/<org>/jobs/<slug>).

The endpoints, parameters, headers and field names follow the MIT-licensed freehire (internal/ingest/sources,
commit shown in NOTICE.md) and career-ops (providers/*.mjs, commit 8f48928) as cited above each fetcher; the code
is original, EXCEPT the regular expressions _NEXT_DATA, _BI_ITEM, _BI_COMPANY, _BI_TOOLTIP, _BI_SALARY, _BI_MULTI
and _BI_MODES, which are taken from career-ops (MIT; getro.mjs:123 and builtin.mjs:123, :129-135). The container this
was written in could not reach any of these hosts, so every parser is tested on fixtures shaped like the references'
own test data only. These are unofficial front-end endpoints: they can change or refuse a script without notice, in
which case the feed simply returns nothing. Wellfound sits behind a Cloudflare JavaScript challenge that a plain
request does not pass (freehire wellfound.go:21-24: "measured live"), so from most networks it answers 403 or a
challenge page and the site stops at once. Work at a Startup needs a search key that only a logged-in session holds
(freehire workatastartup.go:15-18); the kit will not take one out of a session, so it reads that site ONLY when the
user has put such a key in the environment variable ``WAAS_ALGOLIA_KEY`` themselves, and sends no request otherwise.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

try:
    import kit_feeds as _kf  # type: ignore[import-not-found]  # sibling file, not a package module
    import kit_regional as _kr  # type: ignore[import-not-found]
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_feeds as _kf
    import kit_regional as _kr

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

# kit_feeds / kit_regional helpers, typed here because mypy cannot see the sibling files.
_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _kf._feed
_keep: Callable[..., list[Job]] = _kf._keep
_queries: Callable[[list[str]], list[str]] = _kf._queries
_date: Callable[[Any], str] = _kf._date
strip_html: Callable[[Any], str] = _kf.strip_html
_made: Callable[..., Job] = _kr._made  # one Job: title_only when no description was read
_unique: Callable[[list[Job]], list[Job]] = _kr._unique

MAX_SEARCHES = 3  # searches per site per run
PAGES = 2  # result pages per search
PAUSE_S = 3.0  # seconds between two requests to one site (tests set it to 0)

_WS = re.compile(r"\s+")
# A challenge, login wall or bot check. Applied only to a page that lacks the content it should have.
_BLOCK = re.compile(
    r"just a moment|cf-chl|challenge-platform|captcha|access denied|attention required|verify you are (?:a )?human|"
    r"sign in to (?:continue|view)|/login", re.I)
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
       "Chrome/151.0.0.0 Safari/537.36")  # career-ops user-agent.mjs:22-23 (BROWSER_LIKE_USER_AGENT)
_HTML_HEADERS = {"User-Agent": _UA, "Accept": "text/html,application/xhtml+xml"}
_NEXT_DATA = re.compile(r"""<script\b[^>]*\sid\s*=\s*["']__NEXT_DATA__["'][^>]*>(.*?)</script\b[^>]*>""", re.S | re.I)
_SLUG_OK = re.compile(r"^[a-z0-9_-]+$", re.I)


def _allowed(countries: list[str], *codes: str) -> bool:
    """True when the user's countries hold one of ``codes`` or "*"; with no ``codes``, when they hold anything."""
    have = {str(c).upper() for c in countries if str(c).strip()}
    if not have:
        return False
    return not codes or "*" in have or bool(have & set(codes))


def _blocked(text: Any) -> bool:
    return isinstance(text, str) and bool(_BLOCK.search(text))


def _md_text(text: Any) -> str:
    """Wellfound and Work at a Startup descriptions are Markdown (freehire wellfound.go:302-306): plain text out."""
    t = strip_html(text)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"^\s*#{1,6}\s*", "", t, flags=re.M)
    return _WS.sub(" ", re.sub(r"[*_`]{1,3}", "", t)).strip()


def _next_data(page: Any) -> Any:
    """The parsed __NEXT_DATA__ JSON of a Next.js page, or None."""
    m = _NEXT_DATA.search(page) if isinstance(page, str) else None
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


class _Site:
    """One site's run: counted requests, a pause between them, and the stop rule (first non-200 ends the site)."""

    def __init__(self, name: str, http: HttpFn, stats: Stats, max_requests: int) -> None:
        self.name, self.http, self.stats = name, http, stats
        self.stopped = False
        self.sent = 0
        self.max_requests = max_requests

    def stop(self, why: str) -> None:
        if not self.stopped:
            print(f"warning: {self.name} stopped for this run: {why}", file=sys.stderr)
        self.stopped = True

    def get(self, url: str, *, headers: dict[str, str], as_json: bool = False, data: Any = None) -> Any:
        """The body on HTTP 200, else None and the whole site stops. Never retries."""
        if self.stopped:
            return None
        if self.sent >= self.max_requests:
            self.stopped = True  # the site's own budget, not a refusal: no warning
            return None
        if self.sent and PAUSE_S > 0:
            time.sleep(PAUSE_S)
        self.sent += 1
        self.stats["tried"] = self.stats.get("tried", 0) + 1
        kw: dict[str, Any] = {"headers": headers, "as_json": as_json, "once": True}
        if data is not None:
            kw.update(method="POST", data=json.dumps(data).encode())
        status, body = self.http(url, **kw)
        if status == 200 and body is not None:
            self.stats["ok"] = self.stats.get("ok", 0) + 1
            return body
        self.stop(f"HTTP {status} (nothing read)")
        return None


def _algolia(site: _Site, url: str, headers: dict[str, str], params: dict[str, Any]) -> Any:
    """One Algolia query: POST {"params": "<urlencoded>"} (freehire workatastartup.go:133-139, wttj.mjs:233-234)."""
    hdr = {**headers, "Content-Type": "application/json", "Accept": "application/json"}
    return site.get(url, headers=hdr, as_json=True, data={"params": urlencode(params)})


# --------------------------------------------------------------------------------------- Wellfound
# Mirrors freehire wellfound.go:54 (page URL https://wellfound.com/role/r/<slug>?page=<n>; the board is a role-
# taxonomy slug, :14-15 of the type comment), :57 (a posting's public page https://wellfound.com/jobs/<id>-<slug>),
# :25-27 (the page is server-rendered Next.js: <script id="__NEXT_DATA__"> holds props.pageProps.apolloState.data, a
# normalized Apollo cache keyed "<Type>:<id>"), :135-141 (no script, bad JSON or no data is a loud failure = here the
# site stops), :94-100 (wellfoundSearchTotals: pageCount, totalJobCount) and
# :208-232 (the totals sit at ROOT_QUERY.talent["seoLandingPageJobSearchResults(...)"] {pageCount, totalJobCount}; no
# field = no trustworthy stopping point), :253 (pagination trusts the stated pageCount), :71-78
# (JobListingSearchResult: id, slug, title, description (Markdown), compensation, remote, locationNames), :28-33 and
# :170-205 (the entry names NO company: the hiring startup is found only through
# StartupResult.highlightedJobListings[]. __ref, so a reverse index is built; StartupResult keys visited in sorted
# order, :188, first mapping wins), :288 (an entry with no id or no resolvable company is dropped), :296 (location =
# locationNames joined "; "), :303-312 (the description is Markdown; compensation is a display string kept as text).
# The listing already carries each full description: no detail request. wellfound_pace_test.go:5-33: one request in
# flight at a time and spaced out (the vendor's ceiling is on CONCURRENT requests): this reader is strictly sequential
# and waits PAUSE_S between requests. NOT IN REFERENCE: how a search query becomes a role slug (the kit slugifies the
# title: "Data Scientist" -> "data-scientist"; an unknown slug returns no page data and ends the site); a direct
# (non-Firecrawl) request: freehire says Cloudflare blocks it (wellfound.go:21-24), so a 403 or challenge page is the
# expected answer from many networks and stops the site; the headers (the career-ops browser User-Agent is sent).
WF_ROLE_URL = "https://wellfound.com/role/r/{slug}?page={page}"
WF_JOB_URL = "https://wellfound.com/jobs/{id}-{slug}"
WF_MAX_REQUESTS = MAX_SEARCHES * PAGES


def _slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def parse_wellfound_page(page: Any) -> tuple[list[Job], int] | None:
    """One role page -> (jobs, pageCount); None when it is not the expected Next.js page (blocked / re-templated)."""
    data = _next_data(page)
    try:
        apollo = data["props"]["pageProps"]["apolloState"]["data"]
    except (KeyError, TypeError):
        return None
    if not isinstance(apollo, dict) or not apollo:
        return None
    page_count = None
    root = apollo.get("ROOT_QUERY")
    talent = root.get("talent") if isinstance(root, dict) else None
    for key, val in (talent.items() if isinstance(talent, dict) else []):
        if key.startswith("seoLandingPageJobSearchResults(") and isinstance(val, dict):
            pc = val.get("pageCount")
            page_count = pc if isinstance(pc, int) and not isinstance(pc, bool) else None
            break
    if page_count is None:
        return None  # no stated page count: no trustworthy stopping point (wellfound.go:210-211, :233)
    company_of: dict[str, str] = {}
    for key in sorted(k for k in apollo if k.startswith("StartupResult:")):
        startup = apollo[key]
        if not isinstance(startup, dict):
            continue
        for ref in startup.get("highlightedJobListings") or []:
            r = ref.get("__ref") if isinstance(ref, dict) else None
            if r and r not in company_of:
                company_of[r] = str(startup.get("name") or "").strip()
    out: list[Job] = []
    for key in sorted(k for k in apollo if k.startswith("JobListingSearchResult:")):
        e = apollo[key]
        if not isinstance(e, dict):
            continue
        jid = str(e.get("id") or "").strip()
        company = company_of.get(key, "")
        title = str(e.get("title") or "").strip()
        if not (jid and company and title):  # no id or no resolvable employer: not used (wellfound.go:288)
            continue
        names = [str(n) for n in (e.get("locationNames") or []) if isinstance(n, str) and n.strip()]
        extra: dict[str, Any] = {"id": jid}
        comp = str(e.get("compensation") or "").strip()
        if comp:
            extra["compensation"] = comp
        out.append(_made(
            "wellfound", "Wellfound", title=title, company=company,
            url=WF_JOB_URL.format(id=jid, slug=str(e.get("slug") or "").strip()),
            place="; ".join(names), country="", description=_md_text(e.get("description")),
            remote=True if e.get("remote") is True else None, extra=extra))
    return out, page_count


@_feed
def fetch_wellfound(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Wellfound role pages (one slug per search title), low volume. Runs for any non-empty ``countries``."""
    slugs = [s for s in dict.fromkeys(_slugify(q) for q in _queries(queries)) if s][:MAX_SEARCHES]
    if not slugs or not _allowed(countries):
        return []
    site = _Site("wellfound", http, stats, WF_MAX_REQUESTS)
    jobs: list[Job] = []
    for slug in slugs:
        for page in range(1, PAGES + 1):
            body = site.get(WF_ROLE_URL.format(slug=quote(slug), page=page), headers=_HTML_HEADERS)
            if body is None:
                break
            parsed = parse_wellfound_page(body)
            if parsed is None:
                site.stop("not a role page with data (a Cloudflare challenge or a changed page)")
                break
            got, page_count = parsed
            jobs += got
            if page >= page_count:
                break
        if site.stopped:
            break
    return _keep(_unique(jobs), queries, days, search=False)  # the role slug is the search


# ------------------------------------------------------------------------------ Work at a Startup
# Mirrors freehire workatastartup.go:36-37 (Algolia app id 45BWZJ1SGC, index WaaSPublicCompanyJob_production), :79
# (URL https://45BWZJ1SGC-dsn.algolia.net/1/indexes/WaaSPublicCompanyJob_production/query), :15-18 and :35, :71-74 (the
# Algolia search key is NOT public: the page ships a neutered key, a working one exists only in a logged-in session, so
# freehire reads it from the environment variable WAAS_ALGOLIA_KEY and errors when it is unset), :75-78 (headers
# X-Algolia-Application-Id, X-Algolia-API-Key), :131-143 (POST {"params": "<urlencoded>"} with query, distinct=false,
# hitsPerPage, numericFilters), :22-24 and :135 (distinct=false because the index collapses to one posting per company
# otherwise), :52-68 and :150-172 (a hit: id, title, description (Markdown), remote "no"|"yes"|"only", created_at
# RFC3339, company_name, locations_for_search (take the first plain string, :178; some records hold nested-array
# garbage), search_path = the canonical job URL, else https://www.workatastartup.com/jobs/<id>; a hit with no id or no
# company is dropped, :154), :19-20 (a hit carries the full posting: no detail call). WHY NOT RUN BY DEFAULT: the kit
# takes no key out of a login (the owner's rule that also keeps Indeed with the assistant). The reader therefore sends
# NO request unless the user set WAAS_ALGOLIA_KEY themselves. NOT IN REFERENCE: sending the search title as Algolia
# ``query`` and reading a second page with ``page`` (freehire sends an empty query and bisects an id window instead;
# ``query`` and ``page`` are standard Algolia search parameters).
WAAS_KEY_ENV = "WAAS_ALGOLIA_KEY"  # freehire workatastartup.go:35
WAAS_APP = "45BWZJ1SGC"
WAAS_INDEX = "WaaSPublicCompanyJob_production"
WAAS_URL = f"https://{WAAS_APP}-dsn.algolia.net/1/indexes/{WAAS_INDEX}/query"
WAAS_PER_PAGE = 50  # NOT IN REFERENCE (freehire asks for 1000, its max); the kit keeps the page small
WAAS_MAX_REQUESTS = MAX_SEARCHES * PAGES


def _first_scalar(values: Any) -> str:
    for v in values if isinstance(values, list) else []:
        if isinstance(v, str):
            return v
    return ""


def parse_waas_hits(payload: Any) -> list[Job]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    out: list[Job] = []
    for h in hits if isinstance(hits, list) else []:
        if not isinstance(h, dict):
            continue
        jid = str(h.get("id") or "").strip()
        company = str(h.get("company_name") or "").strip()
        title = str(h.get("title") or "").strip()
        if not jid or jid == "0" or not company or not title:
            continue
        mode = str(h.get("remote") or "").lower()
        out.append(_made(
            "workatastartup", "Work at a Startup", title=title, company=company,
            url=str(h.get("search_path") or "").strip() or f"https://www.workatastartup.com/jobs/{jid}",
            place=_first_scalar(h.get("locations_for_search")), country="", posted=_date(h.get("created_at")),
            description=_md_text(h.get("description")),
            remote=True if mode in ("yes", "only") else (False if mode == "no" else None), extra={"id": jid}))
    return out


@_feed
def fetch_workatastartup(queries: list[str], http: HttpFn, stats: Stats, *, days: int,
                         countries: list[str]) -> list[Job]:
    """Work at a Startup (YC), only with a search key the user put in WAAS_ALGOLIA_KEY; no request otherwise."""
    key = os.environ.get(WAAS_KEY_ENV, "").strip()
    qs = _queries(queries)[:MAX_SEARCHES]
    if not key or not qs or not _allowed(countries):
        if not key and qs and _allowed(countries):
            print(f"warning: workatastartup skipped: {WAAS_KEY_ENV} is not set (the kit takes no key out of a "
                  "login)", file=sys.stderr)
        return []
    site = _Site("workatastartup", http, stats, WAAS_MAX_REQUESTS)
    headers = {"X-Algolia-Application-Id": WAAS_APP, "X-Algolia-API-Key": key}
    jobs: list[Job] = []
    for q in qs:
        for page in range(PAGES):
            body = _algolia(site, WAAS_URL, headers, {"query": q, "distinct": "false",
                                                      "hitsPerPage": WAAS_PER_PAGE, "page": page})
            if body is None:
                break
            jobs += parse_waas_hits(body)
            hits = body.get("hits") if isinstance(body, dict) else None
            if not isinstance(hits, list) or len(hits) < WAAS_PER_PAGE:  # the raw page, not the kept rows
                break
        if site.stopped:
            break
    return _keep(_unique(jobs), queries, days, search=False)  # Algolia matched the query itself


# ------------------------------------------------------------------------------------------ Getro
# Mirrors career-ops providers/getro.mjs:10-13 (POST https://api.getro.com/api/v2/collections/<id>/search/jobs, body
# {"hitsPerPage":N,"page":P,"filters":{"page":P},"query":""}, answer {results:{jobs:[...],count}}), :65 (page size 20,
# the API's hard cap), :15-17 and :115-135 (the numeric collection id is network.id in the board page's
# __NEXT_DATA__ props.pageProps.network.id; resolved with one GET of the board's careers_url), :18-21 and :237
# (the search API needs a ``referer`` header = the board's origin + "/", else it has answered 406), :29-31 (jobs
# come newest-first), :213-215 and :264-289 (stop paging once a dated posting is older than the age bound; an
# undated posting is kept), :53-62 (created_at is Unix seconds, older boards ISO text), :265-286 (a job without a url
# is skipped; company = organization.name, "the portfolio company, not the fund"; location = locations, else
# searchable_locations, plus "Remote" when work_mode == "remote"; postedAt), :165-175 (salary: compensation_amount_
# min_cents / max_cents / compensation_currency, only when compensation_period is "year" or empty), :259-262 (stop on
# an empty page or at results.count). freehire getro.go:19-21 and :79 agree (the first-party apply link is ``url``),
# :54 (the same endpoint; its body uses snake_case hits_per_page, :158: the kit mirrors career-ops' camelCase
# body). The network list is the one career-ops names: templates/portals.example.yml:3358-3404 (b2venture
# id 4283 :3361, Cherry Ventures id 44081 :3368; Atomico, Earlybird, Speedinvest, HV Capital, Point Nine with their
# careers_url and no id: resolved from the page, :3376). Insight Partners (:3408) and the Ottawa ecosystem board
# (:3425) are left out: one is a 11.5k-job global board (getro.mjs:68-72), the other a regional one. NOT IN
# REFERENCE: sending the search title in the ``query`` field (both references send ""); whether the API honours it is
# unverified, so a run that gets unrelated jobs back simply returns them. The kit also does not retry (the
# reference retries transient errors, getro.mjs:43-45) and caps each search at PAGES pages instead of 40.
GETRO_API = "https://api.getro.com/api/v2/collections/{id}/search/jobs"
GETRO_PER_PAGE = 20  # getro.mjs:65
GETRO_NETWORKS: list[tuple[str, int | None, str]] = [  # (name, collection id or None, careers_url)
    ("b2venture", 4283, "https://jobs.b2venture.vc/jobs"),
    ("Cherry Ventures", 44081, "https://talent.cherry.vc/jobs"),
    ("Atomico", None, "https://careers.atomico.com/jobs"),
    ("Earlybird", None, "https://jobs.earlybird.com/jobs"),
    ("Speedinvest", None, "https://careers.speedinvest.com/jobs"),
    ("HV Capital", None, "https://hv.getro.com/jobs"),
    ("Point Nine", None, "https://jobs.pointnine.com/jobs"),
    ("Techstars", None, "https://jobs.techstars.com/jobs"),  # ApplyPilot sites.yaml:92
]
GETRO_MAX_REQUESTS = MAX_SEARCHES * (PAGES + 1)  # pages plus one id lookup per search


def extract_collection_id(page: Any) -> str | None:
    """network.id from a Getro board page's __NEXT_DATA__ (getro.mjs:115-135); None when absent."""
    try:
        nid = _next_data(page)["props"]["pageProps"]["network"]["id"]
    except (KeyError, TypeError):
        return None
    if isinstance(nid, bool):
        return None
    if isinstance(nid, int) and nid > 0:
        return str(nid)
    return nid if isinstance(nid, str) and nid.isdigit() and int(nid) > 0 else None


def _epoch(value: Any) -> float | None:
    """Unix seconds (or ms, or ISO text) -> seconds; None when unusable (getro.mjs:53-62)."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return None if value <= 0 else (value / 1000 if value >= 1e12 else float(value))
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _getro_place(j: dict[str, Any]) -> str:
    def clean(v: Any) -> list[str]:
        return [x.strip() for x in v if isinstance(x, str) and x.strip()] if isinstance(v, list) else []
    parts = clean(j.get("locations")) or clean(j.get("searchable_locations"))
    if j.get("work_mode") == "remote" and not any("remote" in p.lower() for p in parts):
        parts.append("Remote")
    return ", ".join(parts)


def parse_getro_jobs(payload: Any) -> tuple[list[Job], list[float | None], int | None]:
    """One search answer -> (jobs, created_at seconds per job, results.count); jobs with no url/employer dropped."""
    results = payload.get("results") if isinstance(payload, dict) else None
    results = results if isinstance(results, dict) else {}
    count = results.get("count")
    raw = results.get("jobs") if isinstance(results.get("jobs"), list) else []
    jobs: list[Job] = []
    when: list[float | None] = []
    for j in raw or []:
        if not isinstance(j, dict):
            continue
        url = str(j.get("url") or "").strip()
        org = j.get("organization")
        name = org.get("name") if isinstance(org, dict) else None
        company = str(name or j.get("organization_name") or "").strip()
        title = str(j.get("title") or "").strip()
        if not (url and company and title):  # no portfolio company named: not used (never the fund's name)
            continue
        ts = _epoch(j.get("created_at"))
        extra: dict[str, Any] = {"id": str(j.get("id") or "")}
        lo, hi = j.get("compensation_amount_min_cents"), j.get("compensation_amount_max_cents")
        period = str(j.get("compensation_period") or "").strip().lower()
        if period in ("", "year") and any(isinstance(v, (int, float)) and v > 0 for v in (lo, hi)):
            extra["salary_min"] = int(lo) // 100 if isinstance(lo, (int, float)) and lo > 0 else None
            extra["salary_max"] = int(hi) // 100 if isinstance(hi, (int, float)) and hi > 0 else None
            extra["salary_currency"] = str(j.get("compensation_currency") or "").strip()
        jobs.append(_made(
            "getro", "Getro", title=title, company=company, url=url, place=_getro_place(j), country="",
            posted=_date(ts), remote=True if j.get("work_mode") == "remote" else None, extra=extra))
        when.append(ts)
    return jobs, when, count if isinstance(count, int) and not isinstance(count, bool) else None


def _getro_searches(queries: list[str]) -> list[tuple[str, int | None, str, str]]:
    """At most MAX_SEARCHES (name, id, careers_url, query) over networks and titles (diagonals of the grid)."""
    qs = _queries(queries) or [""]
    grid = sorted(((i + j, j, n, q) for i, n in enumerate(GETRO_NETWORKS) for j, q in enumerate(qs)),
                  key=lambda t: (t[0], t[1]))
    return [(n[0], n[1], n[2], q) for _, _, n, q in grid[:MAX_SEARCHES]]


@_feed
def fetch_getro(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Getro VC-portfolio job networks (career-ops' list), low volume. Runs for any non-empty ``countries``."""
    if not _allowed(countries):
        return []
    site = _Site("getro", http, stats, GETRO_MAX_REQUESTS)
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).timestamp() if days and days > 0 else 0.0
    ids: dict[str, str | None] = {}
    jobs: list[Job] = []
    for _name, nid, careers, q in _getro_searches(queries):
        cid = str(nid) if nid else ids.get(careers)
        if not cid and careers not in ids:
            page = site.get(careers, headers=_HTML_HEADERS)
            cid = extract_collection_id(page)
            ids[careers] = cid
            if not cid and _blocked(page):
                site.stop("a Cloudflare, login or CAPTCHA page instead of the board page")
        if site.stopped:
            break
        if not cid:
            continue  # the id could not be read from that board's page: skip this network
        origin = "{0.scheme}://{0.netloc}/".format(urlsplit(careers))
        total = float("inf")
        for page in range(PAGES):
            if page * GETRO_PER_PAGE >= total:
                break
            body = site.get(GETRO_API.format(id=cid), headers={"referer": origin, "accept": "application/json",
                                                              "content-type": "application/json"},
                            as_json=True, data={"hitsPerPage": GETRO_PER_PAGE, "page": page,
                                                "filters": {"page": page}, "query": q})
            if body is None:
                break
            got, when, count = parse_getro_jobs(body)
            if count is not None:
                total = count
            if not got and not (isinstance(body, dict) and (body.get("results") or {}).get("jobs")):
                break
            jobs += got
            if cutoff and any(t is not None and t < cutoff for t in when):
                break  # newest first: everything after this is older still (getro.mjs:288)
        if site.stopped:
            break
    return _keep(_unique(jobs), queries, days, search=False)  # the query went to the API; the age bound is core's


# ------------------------------------------------------------------------------------------ Built In
# Mirrors career-ops providers/builtin.mjs:84 (host builtin.com; the same markup serves the city hosts, :13-17), :465-
# 469 (search path /jobs?search=<q>), :85 (builtin orders newest-first), :485-487 (URL <host><path>&page=<n>), :85 (3
# pages by default; the kit uses PAGES), :30-35 and :123 (the spine: the schema.org ItemList JSON blob, one entry
# {"@type":"ListItem", "position":N,"name":<title>,"url":<url>,"description":<~400 chars>}, keys in that order,
# captured with the ITEM regex taken from career-ops, MIT), :36-45 and :125-131 (the cards: anchored on
# data-id="job-card-title", the id is data-builtin-track-job-id; fields follow FontAwesome icons: fa-clock posted
# badge, fa-house-building workplace mode, fa-location-dot location or "N Locations" with a data-bs-title tooltip,
# fa-sack-dollar salary band), :306-313 (the company is the LAST /company/ anchor before the card title, within the
# previous card's title), :193-205 (a field is the next non-empty text node after its icon, within 600 chars), :218-230
# (posted: "Yesterday", "Reposted 15 Days Ago", "2 Hours Ago", "30+ Days Ago"), :247-255 (salary only for "Annually"
# bands), :237-239 ("Hourly" bands are a Built In data error), :270-278 (location: mode + places; empty when no place),
# :346-350 (job id = the number at the end of the url), :515 (stop a search at an empty page or a page with nothing
# new), :524-530 (drift guard: many rows but no card), :494 (User-Agent only). The ItemList carries the title and a
# ~400 char description excerpt; the company comes ONLY from the card: a row whose card gave no company is dropped (the
# kit never writes the board name there). Built In is a US tech board (builtin.mjs:4-5): the reader runs for "US" or
# "*" only. NOT IN REFERENCE: the other market hosts are not used (one national host); the 400-char excerpt is the
# whole description (the full ad is on the linked page).
BI_HOST = "builtin.com"
BI_SEARCH_URL = "https://" + BI_HOST + "/jobs?search={q}&page={page}"
BI_MAX_REQUESTS = MAX_SEARCHES * PAGES
_BI_ITEM = re.compile(
    r'\{"@type":"ListItem","position":\d+,"name":("(?:[^"\\]|\\.)*"),"url":("(?:[^"\\]|\\.)*")'
    r'(?:,"description":("(?:[^"\\]|\\.)*"))?\}')
_BI_CARD = re.compile(r'data-id="job-card-title"')
_BI_CARD_ID = re.compile(r'data-builtin-track-job-id="(\d+)"')
_BI_COMPANY = re.compile(r'<a[^>]+href="/company/[^"]*"[^>]*>([\s\S]{0,240}?)</a>')
_BI_TOOLTIP = re.compile(r'aria-label="Job locations"[^>]*data-bs-title="([^"]*)"')
_BI_MULTI = re.compile(r"^\d+\s+Locations?$", re.I)
_BI_SALARY = re.compile(r"^(\d+(?:\.\d+)?)K(?:-(\d+(?:\.\d+)?)K)?\s+Annually$", re.I)
_BI_MODES = ["Remote or Hybrid", "Remote", "Hybrid", "In-Office"]
_BI_CARD_CAP = 12_000
_BI_UNIT_DAYS = {"minute": 0, "hour": 0, "day": 1, "week": 7, "month": 30, "year": 365}


def _bi_text(s: str) -> str:
    return _WS.sub(" ", strip_html(re.sub(r"<[^>]*>", " ", s))).strip()


def _bi_after_icon(seg: str, icon: str) -> str:
    at = seg.find(icon)
    if at == -1:
        return ""
    for m in re.finditer(r">([^<>]+)<", seg[at:at + 600]):
        t = _bi_text(m.group(1))
        if t:
            return t
    return ""


def _bi_posted(text: str) -> str:
    """"Yesterday", "Reposted 15 Days Ago", "2 Hours Ago" -> YYYY-MM-DD ('' when unknown)."""
    t = re.sub(r"^reposted\s+", "", text.strip(), flags=re.I)
    now = datetime.now(timezone.utc)
    if re.fullmatch(r"today", t, re.I):
        return now.strftime("%Y-%m-%d")
    if re.fullmatch(r"yesterday", t, re.I):
        return (now - timedelta(days=1)).strftime("%Y-%m-%d")
    m = re.fullmatch(r"(\d+|an?)\+?\s+(minute|hour|day|week|month|year)s?\s+ago", t, re.I)
    if not m:
        return ""
    n = 1 if m.group(1).lower() in ("a", "an") else int(m.group(1))
    return (now - timedelta(days=n * _BI_UNIT_DAYS[m.group(2).lower()])).strftime("%Y-%m-%d")


def parse_builtin_cards(page: str) -> dict[str, dict[str, Any]]:
    """Job id -> {company, location, posted, salary_min/max} from the rendered cards (builtin.mjs:288-343)."""
    starts = [m.start() for m in _BI_CARD.finditer(page)]
    out: dict[str, dict[str, Any]] = {}
    for i, start in enumerate(starts):
        seg = page[start:min(starts[i + 1] if i + 1 < len(starts) else len(page), start + _BI_CARD_CAP)]
        idm = _BI_CARD_ID.search(seg)
        if not idm:
            continue
        pre = page[max(0, start - 2500) if i == 0 else starts[i - 1]:start]
        company = ""
        for cm in _BI_COMPANY.finditer(pre):
            company = _bi_text(cm.group(1))
        mode_raw = _bi_after_icon(seg, "fa-house-building")
        mode = next((w for w in _BI_MODES if w.lower() == mode_raw.lower()), "")
        loc_raw = _bi_after_icon(seg, "fa-location-dot")
        places: list[str] = []
        if loc_raw and not _BI_MULTI.match(loc_raw):
            places = [loc_raw]
        else:
            tip = _BI_TOOLTIP.search(seg)
            if tip:
                import html as _html
                places = [p for p in (_bi_text(x) for x in re.split(
                    r"</div>|<br\s*/?>", _html.unescape(tip.group(1)), flags=re.I)) if p]
        location = " · ".join(p for p in [mode, *places] if p) if places else (mode if "remote" in mode.lower() else "")
        rec: dict[str, Any] = {"company": company, "location": location, "mode": mode,
                               "posted": _bi_posted(_bi_after_icon(seg, "fa-clock"))}
        sm = _BI_SALARY.match(_bi_after_icon(seg, "fa-sack-dollar"))
        if sm:
            rec["salary_min"] = round(float(sm.group(1)) * 1000)
            rec["salary_max"] = round(float(sm.group(2)) * 1000) if sm.group(2) else rec["salary_min"]
        out[idm.group(1)] = rec
    return out


def parse_builtin_page(page: Any) -> list[Job]:
    """One listing page -> Jobs (ItemList spine joined to the cards on the job id); no company = dropped."""
    html = page if isinstance(page, str) else ""
    cards = parse_builtin_cards(html)
    out: list[Job] = []
    for m in _BI_ITEM.finditer(html):
        try:
            title, url = json.loads(m.group(1)), json.loads(m.group(2))
            desc = json.loads(m.group(3)) if m.group(3) else ""
        except ValueError:
            continue  # a malformed item must never abort the page
        idm = re.search(r"/(\d+)(?:[/?#]|$)", str(url))
        card = cards.get(idm.group(1) if idm else "", {})
        company = card.get("company", "")
        if not (title and url and company):
            continue
        extra: dict[str, Any] = {"id": idm.group(1) if idm else ""}
        for k in ("salary_min", "salary_max"):
            if k in card:
                extra[k] = card[k]
        out.append(_made(
            "builtin", "Built In", title=title, company=company, url=urljoin("https://" + BI_HOST, str(url)),
            place=card.get("location", ""), country="", posted=card.get("posted", ""), description=desc,
            remote=True if card.get("mode") == "Remote" else None, extra=extra))
    return out


@_feed
def fetch_builtin(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Built In's US tech job search, low volume. Runs for "US" (or "*") only."""
    qs = _queries(queries)[:MAX_SEARCHES]
    if not qs or not _allowed(countries, "US"):
        return []
    site = _Site("builtin", http, stats, BI_MAX_REQUESTS)
    seen: set[str] = set()
    jobs: list[Job] = []
    rows = located = 0
    for q in qs:
        for page in range(1, PAGES + 1):
            body = site.get(BI_SEARCH_URL.format(q=quote(q, safe=""), page=page), headers={"User-Agent": _UA})
            if body is None:
                break
            got = parse_builtin_page(body)
            if not got:
                if not _BI_ITEM.search(body if isinstance(body, str) else ""):
                    if _blocked(body):
                        site.stop("a challenge or login page instead of results")
                break
            rows += len(got)
            located += sum(1 for j in got if j["location"])
            new = [j for j in got if j["url"] not in seen]
            seen.update(j["url"] for j in new)
            jobs += new
            if not new:
                break
        if site.stopped:
            break
    if rows >= 5 and located / rows < 0.5:  # drift guard (builtin.mjs:524-530)
        print(f"warning: builtin resolved a location for only {located}/{rows} cards (markup may have changed)",
              file=sys.stderr)
    return _keep(jobs, queries, days, search=False)  # Built In matched the search itself


# ------------------------------------------------------------------------------ Welcome to the Jungle
# Mirrors career-ops providers/wttj.mjs:43 (credentials from https://www.welcometothejungle.com/api/env on every run,
# the public Algolia id and client key rotate), :78-100 (that payload is ``window.env = {...}``: the JSON between the
# first "{" and the last "}"; PUBLIC_ALGOLIA_APPLICATION_ID must match [A-Z0-9]{6,16}, PUBLIC_ALGOLIA_API_KEY_CLIENT
# is 16..500 chars), :6-9 and :231 (the key is referer-locked: every Algolia request sends Referer
# https://www.welcometothejungle.com/), :45 (index wttj_jobs_production_en), :203-234 (POST https://<appId>-dsn.
# algolia.net/1/indexes/wttj_jobs_production_en/query, body {"params": "<urlencoded>"} with query, hitsPerPage,
# attributesToRetrieve = name,slug,organization,offices,remote,published_at_timestamp,salary_yearly_minimum,
# salary_maximum,salary_period,salary_currency; headers x-algolia-application-id, x-algolia-api-key, referer,
# content-type), :222 (``filters`` is an Algolia expression, e.g. offices.country_code:FR AND contract_type:full_time,
# :13-34: filter server-side, a keyword alone cannot narrow a global board), :46 and :183 (100 hits per query), :117-
# 160 (a hit: name = title, slug and organization.slug build https://www.welcometothejungle.com/en/companies/<org>/
# jobs/<slug> (slugs limited to [a-z0-9_-]), organization.name = the employer, location = offices[0] city and country
# + "Remote" when remote == "fulltime", published_at_timestamp = epoch seconds, salary_yearly_minimum / salary_maximum
# (maximum only for salary_period "yearly") / salary_currency), :206 and :244 (one request per query, deduplicated by
# url), :237-241 (an answer without ``hits`` is an error = the site stops). Narrowing: the user's country codes become
# ``offices.country_code:XX`` (joined with OR; NOT IN REFERENCE: the reference shows one code; the OR join is
# standard Algolia filter syntax); "*" sends no country filter, and then a query is required (wttj.mjs:172-176).
# NOT IN REFERENCE: a second page (the reference reads one page of 100 hits per query, so the kit does the same).
WTTJ_ENV_URL = "https://www.welcometothejungle.com/api/env"
WTTJ_ORIGIN = "https://www.welcometothejungle.com"
WTTJ_INDEX = "wttj_jobs_production_en"
WTTJ_HITS = 100
WTTJ_ATTRS = ("name,slug,organization,offices,remote,published_at_timestamp,salary_yearly_minimum,"
              "salary_maximum,salary_period,salary_currency")
WTTJ_MAX_REQUESTS = 1 + MAX_SEARCHES  # the env lookup plus one request per query


def parse_wttj_env(text: Any) -> tuple[str, str] | None:
    """(app id, client key) from the ``window.env = {...}`` payload; None when it is not that (wttj.mjs:78-100)."""
    if not isinstance(text, str):
        return None
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        env = json.loads(text[start:end + 1])
    except ValueError:
        return None
    if not isinstance(env, dict):
        return None
    app = str(env.get("PUBLIC_ALGOLIA_APPLICATION_ID") or "").strip()
    key = str(env.get("PUBLIC_ALGOLIA_API_KEY_CLIENT") or "").strip()
    if not re.fullmatch(r"[A-Z0-9]{6,16}", app, re.I) or not 16 <= len(key) <= 500:
        return None
    return app, key


def parse_wttj_hits(payload: Any) -> list[Job] | None:
    """Algolia answer -> Jobs; None when the answer has no ``hits`` list (an error)."""
    hits = payload.get("hits") if isinstance(payload, dict) else None
    if not isinstance(hits, list):
        return None
    out: list[Job] = []
    for h in hits:
        if not isinstance(h, dict):
            continue
        title = str(h.get("name") or "").strip()
        slug = str(h.get("slug") or "").strip()
        org: dict[str, Any] = h["organization"] if isinstance(h.get("organization"), dict) else {}
        org_slug = str(org.get("slug") or "").strip()
        company = str(org.get("name") or "").strip()
        if not (title and company and _SLUG_OK.match(slug) and _SLUG_OK.match(org_slug)):
            continue
        offices = h.get("offices") if isinstance(h.get("offices"), list) else []
        office = offices[0] if offices and isinstance(offices[0], dict) else {}
        parts = [str(office.get(k)).strip() for k in ("city", "country") if str(office.get(k) or "").strip()]
        if h.get("remote") == "fulltime":
            parts.append("Remote")
        extra: dict[str, Any] = {"id": slug}
        lo, hi = h.get("salary_yearly_minimum"), h.get("salary_maximum")
        lo_v = lo if isinstance(lo, (int, float)) and not isinstance(lo, bool) and lo > 0 else 0
        hi_v = hi if (h.get("salary_period") == "yearly" and isinstance(hi, (int, float))
                      and not isinstance(hi, bool) and hi > 0) else 0
        if lo_v or hi_v:
            extra.update(salary_min=lo_v or hi_v, salary_max=hi_v or lo_v,
                         salary_currency=str(h.get("salary_currency") or "").strip().upper())
        ts = h.get("published_at_timestamp")
        out.append(_made(
            "wttj", "Welcome to the Jungle", title=title, company=company,
            url=f"{WTTJ_ORIGIN}/en/companies/{org_slug}/jobs/{slug}", place=", ".join(parts), country="",
            posted=_date(ts) if isinstance(ts, (int, float)) and not isinstance(ts, bool) and ts > 0 else "",
            remote=True if h.get("remote") == "fulltime" else None, extra=extra))
    return out


def _wttj_filter(countries: list[str]) -> str | None:
    """The Algolia filter for the user's codes; "" (no filter) only when "*" is the sole entry; None when nothing valid.

    The core adds "*" next to the user's own countries for --visa-onsite (the default). WTTJ hits carry no ad text,
    so the visa check can keep none of them: dropping the filter then would only trade the user's countries for a
    global top-100 sample."""
    have = [str(c).upper() for c in countries]
    codes = list(dict.fromkeys(c for c in have if re.fullmatch(r"[A-Z]{2}", c)))
    if codes:
        return " OR ".join(f"offices.country_code:{c}" for c in codes)
    return "" if "*" in have else None


@_feed
def fetch_wttj(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Welcome to the Jungle's public Algolia index, narrowed to the user's countries, low volume."""
    flt = _wttj_filter(countries)
    qs = _queries(queries)[:MAX_SEARCHES]
    if flt is None or (not qs and not flt):
        return []
    site = _Site("wttj", http, stats, WTTJ_MAX_REQUESTS)
    creds = parse_wttj_env(site.get(WTTJ_ENV_URL, headers={"User-Agent": _UA}))
    if creds is None:
        if not site.stopped:
            site.stop("the /api/env payload held no Algolia id and key")
        return []
    app, key = creds
    url = f"https://{app}-dsn.algolia.net/1/indexes/{WTTJ_INDEX}/query"
    headers = {"x-algolia-application-id": app, "x-algolia-api-key": key, "referer": WTTJ_ORIGIN + "/"}
    jobs: list[Job] = []
    for q in qs or [""]:
        params: dict[str, Any] = {"query": q, "hitsPerPage": WTTJ_HITS, "attributesToRetrieve": WTTJ_ATTRS}
        if flt:
            params["filters"] = f"({flt})" if " OR " in flt else flt
        body = _algolia(site, url, headers, params)
        if body is None:
            break
        got = parse_wttj_hits(body)
        if got is None:
            site.stop("an Algolia answer without hits")
            break
        jobs += got
    return _keep(_unique(jobs), queries, days, search=False)  # Algolia matched the query itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "wellfound": fetch_wellfound,
    "workatastartup": fetch_workatastartup,
    "getro": fetch_getro,
    "builtin": fetch_builtin,
    "wttj": fetch_wttj,
}
