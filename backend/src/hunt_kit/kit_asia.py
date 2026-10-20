"""South-East Asian job boards for the Job360 hunt kit (fourth national-services module).

Glints and Kalibrr are read here; Tech in Asia and ITviec follow.

Same contract as ``kit_gov.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. Each fetcher runs ONLY when
one of its countries is in the user's ``countries`` (or "*", which the core passes for ``--visa-onsite``: then
it runs for all its countries and the CORE keeps only ads that say they sponsor a visa, exactly as for
kit_gov) and returns ``[]`` with no request otherwise. Nothing here ranks or scores. No logins, no cookies,
no CAPTCHA solving. Standard library only.

Every ad names its own employer; the board name is never put in ``company``. One run sends at most
``ASIA_MAX_REQUESTS`` requests per site, and the FIRST answer that is not HTTP 200 (a refusal, a 4xx/5xx), or a
page that is a CAPTCHA / challenge / login wall, ends that site's run at once: repeating a refused request only
holds the refusal up.

Endpoints, params, headers and field names follow the MIT-licensed references named above each fetcher
(see NOTICE.md); the code is original. The container this was written in could not reach any of these hosts,
so every parser is tested on fixtures shaped like the references' data only. These are unofficial front-end
endpoints: they can change or refuse a script without notice, in which case the feed simply returns nothing.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

try:
    import kit_feeds as _kf  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_feeds as _kf

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

# kit_feeds helpers, typed here because mypy cannot see the sibling file.
_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _kf._feed
_job: Callable[..., Job] = _kf._job
_keep: Callable[..., list[Job]] = _kf._keep
_queries: Callable[[list[str]], list[str]] = _kf._queries
_date: Callable[[Any], str] = _kf._date
_join_place: Callable[[str, str], str] = _kf._join_place
strip_html: Callable[[Any], str] = _kf.strip_html

ASIA_MAX_REQUESTS = 24  # hard cap per site per run (searches + detail pages + discovery)
PAUSE_S = 1.0  # between requests to one site
_WS = re.compile(r"\s+")
_BLOCKED = re.compile(r"captcha|cf-chl|cf-turnstile|just a moment|attention required|/users?/sign_in|/login\b", re.I)

BROWSER_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/124.0.0.0 Safari/537.36")


def _wanted(countries: list[str], *codes: str) -> list[str]:
    """The given ISO codes the user named (in the user's order), then, for "*", the rest (in the order given).

    The core passes ``countries + ["*"]`` by default, so the user's own markets must come first: a fetcher that
    runs out of requests then misses another market, never the user's."""
    have = [c.upper() for c in countries]
    out = [c for c in dict.fromkeys(have) if c in codes]
    if "*" in have:  # --visa-onsite: run everywhere; the core keeps only ads that sponsor a visa
        out += [c for c in codes if c not in out]
    return out


class _Site:
    """Requests left for one site in one run; any non-200 answer (or a block page) ends the site."""

    def __init__(self, http: HttpFn, stats: Stats, name: str) -> None:
        self.http, self.stats, self.name, self.left, self.sent = http, stats, name, ASIA_MAX_REQUESTS, 0

    @property
    def open(self) -> bool:
        return self.left > 0

    def stop(self, why: str) -> None:
        if self.left > 0:
            print(f"warning: {self.name} stopped for this run: {why}", file=sys.stderr)
        self.left = 0

    def get(self, url: str, *, headers: dict[str, str], as_json: bool, data: Any = None, marker: str = "") -> Any:
        """The body on HTTP 200, else None and the site is closed. Never retries. For an HTML page a body that
        lacks its expected ``marker`` and reads like a CAPTCHA, challenge or login wall also closes the site."""
        if self.left <= 0:
            return None
        if self.sent and PAUSE_S > 0:
            time.sleep(PAUSE_S)
        self.left -= 1
        self.sent += 1
        self.stats["tried"] = self.stats.get("tried", 0) + 1
        kw: dict[str, Any] = {"headers": headers, "as_json": as_json, "once": True}
        if data is not None:
            kw.update(method="POST", data=json.dumps(data).encode())
        status, body = self.http(url, **kw)
        if status != 200 or body is None:
            self.stop(f"HTTP {status} (nothing read)")
            return None
        if marker and isinstance(body, str) and marker not in body and _BLOCKED.search(body[:20000]):
            self.stop("the page is a CAPTCHA, challenge or login wall")
            return None
        self.stats["ok"] = self.stats.get("ok", 0) + 1
        return body


def _made(name: str, attribution: str, *, title: Any, company: Any, url: Any, place: str, country: str,
          posted: str = "", description: Any = "", remote: bool | None = None,
          extra: dict[str, Any] | None = None) -> Job:
    """One Job: location ends with the country name; ``title_only`` when no description was read."""
    ex = {"attribution": attribution, **(extra or {})}
    if not strip_html(description):
        ex["title_only"] = True
    return _job(title=_WS.sub(" ", str(title or "")).strip(), company=company, url=url,
                location=_join_place(place or "", country), posted=posted, description=description,
                source="feed:" + name, board=name, remote=remote, extra=ex)


def _unique(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for j in jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            out.append(j)
    return out


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _d(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _shortener(host: str) -> bool:
    return host.lower() in {"bit.ly", "cutt.ly", "forms.gle", "goo.gl", "is.gd", "lnkd.in", "rb.gy", "rebrand.ly",
                            "s.id", "shorturl.at", "t.co", "tinyurl.com"}


def _own_link(raw: Any) -> str:
    """An employer apply link that is plain http(s) and not through a redirector, else ''."""
    link = _s(raw)
    parts = urlsplit(link)
    return link if parts.scheme in ("http", "https") and parts.hostname and not _shortener(parts.hostname) else ""


# ------------------------------------------------------------------------------------------ Glints
# Borrowed from career-ops providers/glints.mjs:25 (endpoint https://glints.com/api/v2-alc/graphql), :38-67
# (the searchJobsV3 query: jobsInPage { id title company { name brandName } city { name } country { code name }
# createdAt }, hasMore), :6 (Glints covers Singapore, Indonesia, Malaysia and Vietnam), :208-215 (variables
# data: SearchTerm, CountryCode, includeExternalJobs true, pageSize, page), :151 (POST body {operationName:
# "searchJobsV3", query, variables}) and :156-161 (headers: the firewall blocks non-browser user agents, so a
# Chrome UA plus origin https://glints.com and referer https://glints.com/id/opportunities/jobs/explore;
# the UA comment is :152), :123 (job URL <base>/id/opportunities/jobs/<id>), :135 (company = company.name, else
# brandName), :241-242 (stop when hasMore is false or the raw jobsInPage is shorter than the page size), :27-28
# (30 rows a page; the reference reads 3 pages, this kit reads 2). NOT IN REFERENCE: the order of requests
# (every market and query gets its first page before any gets a second, so the request cap never starves a
# market; the reference reads one market at a time).
# NOT IN REFERENCE: Taiwan (TW; the reference names only ID SG MY VN, so TW is not run); any date filter
# (days is applied locally on createdAt); the /id/ path segment for the other three countries (the reference
# builds every URL with /id/); freehire has no Glints source; salaries are not read.
GLINTS_API = "https://glints.com/api/v2-alc/graphql"
GLINTS_BASE = "https://glints.com"
GLINTS_PAGE = 30
GLINTS_PAGES = 2
GLINTS_MARKETS = {"ID": "Indonesia", "SG": "Singapore", "MY": "Malaysia", "VN": "Vietnam"}
GLINTS_HEADERS = {"content-type": "application/json", "user-agent": BROWSER_UA, "origin": GLINTS_BASE,
                  "referer": GLINTS_BASE + "/id/opportunities/jobs/explore"}
GLINTS_QUERY = """
query searchJobsV3($data: JobSearchConditionInput!) {
  searchJobsV3(data: $data) {
    jobsInPage { id title company { name brandName } city { name } country { code name } createdAt }
    expInfo
    hasMore
  }
}"""


def parse_glints(payload: Any, market: str) -> list[Job]:
    """Parse one searchJobsV3 answer: title, employer, city, date; the listing carries no description."""
    page = _d(_d(_d(payload).get("data")).get("searchJobsV3"))
    out: list[Job] = []
    for r in page.get("jobsInPage") or []:
        if not isinstance(r, dict):
            continue
        company = _d(r.get("company"))
        city = _d(r.get("city"))
        country = _d(r.get("country"))
        jid = _s(r.get("id"))
        name = _s(company.get("name")) or _s(company.get("brandName"))
        if not (jid and name and _s(r.get("title"))):
            continue
        out.append(_made(
            "glints", "Glints", title=r.get("title"), company=name,
            url=f"{GLINTS_BASE}/id/opportunities/jobs/{quote(jid, safe='')}", place=_s(city.get("name")),
            country=_s(country.get("name")) or market, posted=_date(r.get("createdAt")), extra={"id": jid}))
    return out


@_feed
def fetch_glints(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Glints search (SG, ID, MY, VN) for the user's matching countries."""
    markets = _wanted(countries, *GLINTS_MARKETS)
    if not markets:
        return []
    site = _Site(http, stats, "glints")
    jobs: list[Job] = []
    pending = [(cc, q) for cc in markets for q in _queries(queries)]
    for page in range(1, GLINTS_PAGES + 1):  # page 1 of every (market, query) before any page 2
        more_pages: list[tuple[str, str]] = []
        for cc, q in pending:
            if not site.open:
                break
            variables = {"data": {"SearchTerm": q, "CountryCode": cc, "includeExternalJobs": True,
                                  "pageSize": GLINTS_PAGE, "page": page}}
            body = site.get(GLINTS_API, headers=GLINTS_HEADERS, as_json=True, data={
                "operationName": "searchJobsV3", "query": GLINTS_QUERY, "variables": variables})
            jobs += parse_glints(body, GLINTS_MARKETS[cc])
            result = _d(_d(_d(body).get("data")).get("searchJobsV3"))
            rows = result.get("jobsInPage")  # the raw page: rows the parser drops still count
            if result.get("hasMore") is not False and isinstance(rows, list) and len(rows) >= GLINTS_PAGE:
                more_pages.append((cc, q))
        pending = more_pages
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# ----------------------------------------------------------------------------------------- Kalibrr
# Borrowed from freehire internal/ingest/sources/kalibrr.go:34 (search URL /kjs/job_board/search), :112-116
# (params country = the country NAME as the platform spells it, function, limit, offset; this kit sends country,
# limit, offset and the keyword), :71-106 (row: id, name, slug, company_name, company.code / company.name,
# description, qualifications, activation_date, apply_redirect_url, is_work_from_home, google_location
# address_components city/region/country), :35 (posting URL https://www.kalibrr.com/c/<company code>/jobs/<id>/
# <slug>), :138-143 (the search ignores a country it does not know and answers the unfiltered market, so every
# row's country is checked; freehire fails the board, this kit drops the row), :188-191 (a row needs id, title,
# company and safe code/slug segments), :25-28 (type comment: offset paging under the default sort repeats rows).
# From career-ops providers/kalibrr.mjs:9-10 (limit/offset paging and the server-side keyword filter `text`),
# :22-27 (`count` is the total match count), :112-129 (the canonical posting URL), :37-41 (apply_redirect_url is
# not used as the URL; kept in extra), :162-170 (location city, region, country; "Remote" for
# is_work_from_home), :178 (posted = activation_date, else created_at).
# NOT IN REFERENCE: dropping a repeated id (freehire :144-146 fails the whole board on one; this kit keeps the
# first and drops the rest); the combination of `text` with `country` (freehire sends country and function,
# career-ops sends text and no country); the function facet is NOT sent (freehire pins "IT and Software" and
# limit 2000, offset 0 only, but this kit serves every field and pages 100 at a time); any experience-years
# field (so no years_min).
KALIBRR_SEARCH = "https://www.kalibrr.com/kjs/job_board/search"
KALIBRR_LIMIT = 100
KALIBRR_PAGES = 2
KALIBRR_MARKETS = {"PH": "Philippines", "ID": "Indonesia"}
_SEG = re.compile(r"^[A-Za-z0-9_-]+$")


def parse_kalibrr(payload: Any, market: str) -> list[Job]:
    """Parse one Kalibrr search page (``jobs``): only rows whose own country is the market's."""
    out: list[Job] = []
    for r in _d(payload).get("jobs") or []:
        if not isinstance(r, dict):
            continue
        comp = _d(r.get("company"))
        gl = _d(r.get("google_location"))
        addr = _d(gl.get("address_components"))
        if _s(addr.get("country")).lower() != market.lower():  # an unknown country answers the whole market
            continue
        jid, slug, code = r.get("id"), _s(r.get("slug")), _s(comp.get("code"))
        company = _s(r.get("company_name")) or _s(comp.get("name"))
        title = _s(r.get("name"))
        if not (isinstance(jid, int) and jid > 0 and title and company and _SEG.match(code) and _SEG.match(slug)):
            continue
        place = ", ".join(dict.fromkeys(p for p in (_s(addr.get("city")), _s(addr.get("region"))) if p))
        extra: dict[str, Any] = {"id": jid}
        apply_url = _own_link(r.get("apply_redirect_url"))
        if apply_url:
            extra["apply_url"] = apply_url
        out.append(_made(
            "kalibrr", "Kalibrr", title=title, company=company,
            url=f"https://www.kalibrr.com/c/{code}/jobs/{jid}/{slug}", place=place, country=market,
            posted=_date(r.get("activation_date") or r.get("created_at")),
            description="\n".join(x for x in (_s(r.get("description")), _s(r.get("qualifications"))) if x),
            remote=True if r.get("is_work_from_home") is True else None, extra=extra))
    return out


@_feed
def fetch_kalibrr(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Kalibrr search (PH, ID) for the user's matching countries; the posting text comes with the rows."""
    markets = _wanted(countries, *KALIBRR_MARKETS)
    if not markets:
        return []
    site = _Site(http, stats, "kalibrr")
    jobs: list[Job] = []
    for cc in markets:
        name = KALIBRR_MARKETS[cc]
        for q in _queries(queries):
            for page in range(KALIBRR_PAGES):
                params = {"country": name, "text": q, "limit": KALIBRR_LIMIT, "offset": page * KALIBRR_LIMIT}
                body = site.get(f"{KALIBRR_SEARCH}?{urlencode(params)}",
                                headers={"Accept": "application/json", "User-Agent": BROWSER_UA}, as_json=True)
                jobs += parse_kalibrr(body, name)
                rows = _d(body).get("jobs")
                total = _d(body).get("count")
                if not site.open or not isinstance(rows, list) or len(rows) < KALIBRR_LIMIT or \
                        (isinstance(total, int) and (page + 1) * KALIBRR_LIMIT >= total):
                    break
    return _keep(_unique(jobs), queries, days, search=False)


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "glints": fetch_glints,
    "kalibrr": fetch_kalibrr,
}
