"""South-East Asian job boards for the Job360 hunt kit (fourth national-services module).

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


# -------------------------------------------------------------------------------------- Tech in Asia
# Borrowed from freehire internal/ingest/sources/techinasia.go:40 (search page https://www.techinasia.com/jobs/search),
# :55 (the page names the bundle /assets/v5/app.<8 hex>.js), :43 (bundle host https://static.techinasia.com/assets/v5/),
# :56 (credentials apiKey:"...", appId:"..." in the bundle: they rotate with each deploy, so they are read every
# run), :212 (query URL https://<appid lower-case>-dsn.algolia.net/1/indexes/job_postings/query), :206-219
# (POST body {"params": "query=&hitsPerPage=1000&page=N&analytics=false&attributesToHighlight=[]"}, headers
# X-Algolia-Application-Id, X-Algolia-API-Key, Referer https://www.techinasia.com/), :195 (stop at nbPages),
# :108-133 (hit: id, title, description, external_link, published_at, company.name, city.name / country_name,
# work_arrangement, is_remote, experience_min), :248-258 and :267 (external_link used as the URL only when it
# is plain http(s), not a redirector, and not shared by several postings; else
# https://www.techinasia.com/jobs/<id>), :296-299 (experience_min > 0 is the stated years floor), :319-322
# (published_at is a zone-less "2006-01-02 15:04:05", read as UTC), :324-337 (work_arrangement on-site / hybrid /
# remote, else is_remote == 1). From career-ops providers/techinasia.mjs:9-10 (markets Indonesia, Singapore,
# Malaysia, Vietnam, Philippines, Japan), :23-28 (the whole board is read unfiltered), :122-139 (bundle and
# credential regexes). NOT IN REFERENCE: a country or keyword filter on the index (both are applied locally
# to the 1000-hit page); India (IN), which neither reference names, so it is not run; the Content-Type of the
# POST (career-ops :376 sends application/x-www-form-urlencoded, freehire's helper JSON; the core's http sets
# no Content-Type, so urllib sends application/x-www-form-urlencoded, as career-ops does).
TIA_SEARCH = "https://www.techinasia.com/jobs/search"
TIA_ASSETS = "https://static.techinasia.com/assets/v5/"
TIA_POSTING = "https://www.techinasia.com/jobs/"
TIA_REFERER = "https://www.techinasia.com/"
TIA_HITS = 1000
TIA_PAGES = 2
TIA_MARKETS = {"ID": "Indonesia", "SG": "Singapore", "MY": "Malaysia", "VN": "Vietnam", "PH": "Philippines",
               "JP": "Japan"}
_TIA_ASSET = re.compile(r"/assets/v5/(app\.[0-9a-f]{8}\.js)")
_TIA_CREDS = re.compile(r"""apiKey:\s*"([A-Za-z0-9]{16,64})"\s*,\s*appId:\s*"([A-Za-z0-9]{6,16})\"""")


def _tia_date(v: Any) -> str:
    s = _s(v)
    return s[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", s) else ""


def _tia_rows(payload: Any) -> list[dict[str, Any]]:
    hits = payload.get("hits") if isinstance(payload, dict) else None
    return [h for h in hits or [] if isinstance(h, dict)]


def _tia_shared(payloads: list[Any]) -> dict[str, int]:
    """How many hits, over every page read, carry each apply link (a link used twice is not one posting's own)."""
    shared: dict[str, int] = {}
    for payload in payloads:
        for h in _tia_rows(payload):
            link = _own_link(h.get("external_link"))
            if link:
                shared[link] = shared.get(link, 0) + 1
    return shared


def parse_techinasia(payload: Any, wanted: set[str], shared: dict[str, int] | None = None) -> list[Job]:
    """Parse one Algolia result page; only hits whose country name is in ``wanted`` (lower-case names).

    ``shared`` counts apply links over all pages (default: this page only)."""
    rows = _tia_rows(payload)
    if shared is None:
        shared = _tia_shared([payload])
    out: list[Job] = []
    for h in rows:
        comp = _d(h.get("company"))
        city = _d(h.get("city"))
        country = _s(city.get("country_name"))
        hid, title, company = _s(h.get("id")), _s(h.get("title")), _s(comp.get("name"))
        if not (hid and title and company) or country.lower() not in wanted:
            continue
        page_url = TIA_POSTING + quote(hid, safe="")
        link = _own_link(h.get("external_link"))
        arrangement = _s(h.get("work_arrangement"))
        remote = True if arrangement == "remote" or (not arrangement and h.get("is_remote") == 1) else None
        extra: dict[str, Any] = {"id": hid, "board_url": page_url}
        years = h.get("experience_min")
        if isinstance(years, int) and not isinstance(years, bool) and years > 0:
            extra["years_min"] = years
        out.append(_made(
            "techinasia", "Tech in Asia", title=title, company=company,
            url=link if link and shared[link] == 1 else page_url, place=_s(city.get("name")), country=country,
            posted=_tia_date(h.get("published_at")), description=h.get("description"), remote=remote, extra=extra))
    return out


@_feed
def fetch_techinasia(queries: list[str], http: HttpFn, stats: Stats, *, days: int,
                     countries: list[str]) -> list[Job]:
    """Tech in Asia's job index (SG, ID, MY, PH, VN, JP) for the user's matching countries."""
    markets = _wanted(countries, *TIA_MARKETS)
    if not markets:
        return []
    wanted = {TIA_MARKETS[c].lower() for c in markets}
    site = _Site(http, stats, "techinasia")
    page = site.get(TIA_SEARCH, headers={"User-Agent": BROWSER_UA, "Accept": "text/html"}, as_json=False,
                    marker="/assets/v5/app.")
    asset = _TIA_ASSET.search(page) if isinstance(page, str) else None
    bundle = site.get(TIA_ASSETS + asset.group(1), headers={"User-Agent": BROWSER_UA, "Referer": TIA_REFERER},
                      as_json=False) if asset else None
    creds = _TIA_CREDS.search(bundle) if isinstance(bundle, str) else None
    if not creds:
        return []
    api_key, app_id = creds.group(1), creds.group(2)
    bodies: list[Any] = []
    for n in range(TIA_PAGES):
        params = urlencode({"query": "", "hitsPerPage": TIA_HITS, "page": n, "analytics": "false",
                            "attributesToHighlight": "[]"})
        body = site.get(f"https://{app_id.lower()}-dsn.algolia.net/1/indexes/job_postings/query",
                        headers={"X-Algolia-Application-Id": app_id, "X-Algolia-API-Key": api_key,
                                 "Referer": TIA_REFERER, "Accept": "application/json"},
                        as_json=True, data={"params": params})
        bodies.append(body)
        pages = body.get("nbPages") if isinstance(body, dict) else None
        if not site.open or not isinstance(pages, int) or n + 1 >= pages:
            break
    shared = _tia_shared(bodies)
    jobs = [j for body in bodies for j in parse_techinasia(body, wanted, shared)]
    return _keep(_unique(jobs), queries, days)  # the index is read whole: the user's titles are matched here


# ------------------------------------------------------------------------------------------ ITviec
# Borrowed from freehire internal/ingest/sources/itviec.go:38 (listing https://itviec.com/it-jobs), :104-109
# (page N is ?page=N), :113-128 (a card is an element with BOTH data-job-key and
# data-search--job-selection-job-slug-value; the posting URL is /it-jobs/<slug>), :76-78 (the page also links
# "company spotlight" postings that are not cards and must not be read), :199-236 (the posting page's
# schema.org JobPosting gives the description, datePosted, hiringOrganization.name (an ad without an employer
# or description is dropped), jobLocation address; the h1 keeps the title's punctuation that the JobPosting
# strips), :267-312 (jobLocation is one Place or a list; the city is addressRegion, addressLocality
# "Not Available" is ignored), :314-341 (the work-mode badge text in .preview-header-item: At office / Hybrid /
# Remote), :42-49 (the site refused sequential sweeps with 429: one request a second). From career-ops
# providers/itviec.mjs:137-144 (a keyword search is /it-jobs/<keywords joined by "-">, page N is ?page=N),
# :140 (the encoded keyword loses any "/", %2F), :51 (a pause between pages), :242-250 (a page that still links
# jobs but yields no cards is a markup change).
# Tests follow freehire's testdata itviec_list_page1.html and itviec_detail_salary.html shapes. NOT IN REFERENCE:
# a years-of-experience field (ITviec states none), the effect of `days` (applied locally on datePosted), any
# country other than Vietnam, and the per-query slot cap with posting pages picked round-robin across queries.
ITVIEC_LIST = "https://itviec.com/it-jobs"
ITVIEC_PAGES = 2
ITVIEC_DETAILS = 12
ITVIEC_PER_QUERY = 6
_TAG = re.compile(r"<[a-zA-Z][^<>]*>")
_KEY = re.compile(r"""\sdata-job-key=['"]([^'"]+)['"]""")
_SLUG = re.compile(r"""\sdata-search--job-selection-job-slug-value=['"]([^'"]+)['"]""")
_LD = re.compile(r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script\b[^>]*>""", re.I | re.S)
_H1 = re.compile(r"<h1[^>]*>(.*?)</h1>", re.I | re.S)
_BADGE = re.compile(r"""preview-header-item[^>]*>(.*?)</div>\s*</div>""", re.I | re.S)


def parse_itviec_cards(page: Any) -> list[tuple[str, str]]:
    """(card key, posting slug) for every real job card of a listing page; spotlight links are ignored."""
    out: list[tuple[str, str]] = []
    for tag in _TAG.findall(page if isinstance(page, str) else ""):
        key, slug = _KEY.search(tag), _SLUG.search(tag)
        if key and slug and key.group(1).strip() and slug.group(1).strip():
            out.append((key.group(1).strip(), slug.group(1).strip()))
    return out


def _ld_posting(page: str) -> dict[str, Any]:
    for m in _LD.finditer(page):
        try:
            data = json.loads(m.group(1), strict=False)
        except ValueError:
            continue
        for n in data if isinstance(data, list) else [data]:
            if isinstance(n, dict) and n.get("@type") == "JobPosting":
                return n
    return {}


def _itviec_description(raw: Any) -> str:
    """The JobPosting description is newline-separated lines: bare headings/text or employer HTML."""
    lines = [ln.strip() for ln in str(raw or "").splitlines() if ln.strip()]
    return "\n".join(lines)


def parse_itviec_posting(page: Any, url: str, key: str) -> Job | None:
    """Parse one ITviec posting page; None without a JobPosting, an employer, a title or a description."""
    text = page if isinstance(page, str) else ""
    p = _ld_posting(text)
    org = p.get("hiringOrganization")
    company = _s(org.get("name")) if isinstance(org, dict) else ""
    h1 = _H1.search(text)
    title = strip_html(h1.group(1)) if h1 else ""
    title = title or _s(p.get("title"))
    description = strip_html(_itviec_description(p.get("description")))
    if not (p and company and title and description):
        return None
    locs = p.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs]
    cities: list[str] = []
    for loc in locs:
        addr = loc.get("address") if isinstance(loc, dict) else None
        if not isinstance(addr, dict):
            continue
        city = _s(addr.get("addressRegion"))
        if not city and _s(addr.get("addressLocality")).lower() != "not available":
            city = _s(addr.get("addressLocality"))
        if city and city not in cities:
            cities.append(city)
    modes = {m for m in (strip_html(b) for b in _BADGE.findall(text)) if m in ("At office", "Hybrid", "Remote")}
    return _made("itviec", "ITviec", title=title, company=company, url=url, place=", ".join(cities),
                 country="Vietnam", posted=_date(p.get("datePosted")), description=description,
                 remote=True if modes == {"Remote"} else None, extra={"id": key})


def _itviec_list_url(q: str, page: int) -> str:
    path = ITVIEC_LIST
    slug = "-".join(q.lower().split()).replace("/", "")  # itviec.mjs:140 strips the encoded slash
    if slug:
        path += "/" + quote(slug, safe="")
    return path if page <= 1 else f"{path}?page={page}"


@_feed
def fetch_itviec(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """ITviec (Vietnam, IT jobs): keyword listing pages, then the newest cards' posting pages."""
    if not _wanted(countries, "VN"):
        return []
    site = _Site(http, stats, "itviec")
    headers = {"User-Agent": BROWSER_UA, "Accept": "text/html,application/xhtml+xml"}
    seen: set[str] = set()
    per_query: list[list[tuple[str, str]]] = []  # each query's (slug, key) picks, newest first
    for q in _queries(queries):
        picks: list[tuple[str, str]] = []
        per_query.append(picks)
        for page in range(1, ITVIEC_PAGES + 1):
            body = site.get(_itviec_list_url(q, page), headers=headers, as_json=False, marker="data-job-key")
            got = parse_itviec_cards(body)
            for key, slug in got:
                if slug not in seen and len(picks) < ITVIEC_PER_QUERY:
                    seen.add(slug)
                    picks.append((slug, key))
            if not site.open or not got or len(picks) >= ITVIEC_PER_QUERY:
                break
    order = [p[i] for i in range(ITVIEC_PER_QUERY) for p in per_query if i < len(p)]  # round-robin by query
    jobs: list[Job] = []
    for slug, key in order[:ITVIEC_DETAILS]:
        if not site.open:
            break
        url = f"{ITVIEC_LIST}/{quote(slug, safe='')}"
        job = parse_itviec_posting(site.get(url, headers=headers, as_json=False, marker="ld+json"), url, key)
        if job:
            jobs.append(job)
    return _keep(_unique(jobs), queries, days, search=False)  # the site matched the keyword itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "glints": fetch_glints,
    "kalibrr": fetch_kalibrr,
    "techinasia": fetch_techinasia,
    "itviec": fetch_itviec,
}
