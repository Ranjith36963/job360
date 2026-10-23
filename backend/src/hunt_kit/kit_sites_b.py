"""Niche job sites, batch B, for the Job360 hunt kit (nine small feeds).

Same contract as ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. Every ad names its own
employer (the board name is never put in ``company``) and links to the original posting where the site
gives that link. One run sends at most ``SITES_MAX_REQUESTS`` requests per site; the first answer that is
not HTTP 200 (403, 406, 429, status 0, 5xx) and a login or CAPTCHA page end that site's run at once; only a
404/410 on an ad page skips that ad. Nothing here ranks or scores. No logins, no cookies, no keys of yours.
Standard library only.

Country rules: Joppy (Spain only, joppy.go:15) and emagine (the markets named at emagine.go:13-15) run only
when one of their countries is in the user's ``countries`` (or "*"). The other sites list ads worldwide, so
they run for everyone and a place that names a country the user did not choose is dropped; a remote ad, or a
place that names no country the kit knows, is kept. NOT IN REFERENCE: that soft country filter (freehire
keeps every ad and filters later).

Endpoints, params, headers and field names follow the MIT-licensed freehire files named above each reader
(see NOTICE.md); the code is original. The container this was written in could not reach any of these
hosts, so every parser is tested on fixtures shaped like the references' test data only. These are
unofficial front-end endpoints: they can change or refuse a script without notice, in which case the feed
simply returns nothing.

Left out on purpose (not an error): aijobs (aijobs.go:17-23: a Django CSRF POST that needs a cookie jar and
a session cookie; the kit sends no cookies), energyjobline (http.go:388-396: its ad pages carry the data only
for a client that calls itself Googlebot; the kit never pretends to be someone else) and nextflight
(nextflight.go is freehire's shared Next.js flight reader, not a job site).
"""
from __future__ import annotations

import html as _html
import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlsplit

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
safe_fromstring: Callable[[Any], Any] = _kf.safe_fromstring
_made: Callable[..., Job] = _kr._made
_unique: Callable[[list[Job]], list[Job]] = _kr._unique
_wanted: Callable[..., list[str]] = _kr._wanted
_Budget: Any = _kr._Budget
_kb_block: Any = None  # kit_bigboards' login/CAPTCHA regex, loaded lazily in _blocked()
_sitemap_locs: Callable[[Any], list[str]] = _kr._sitemap_locs
_posting_place: Callable[[dict[str, Any]], tuple[str, str]] = _kr._posting_place
_posting_company: Callable[[dict[str, Any]], str] = _kr._posting_company
_Tree: Any = _kr._Tree
_walk: Callable[[Any], Any] = _kr._walk
_first: Callable[..., Any] = _kr._first
_text: Callable[[Any], str] = _kr._text
# own copy: the closing tag is matched as `</script\b[^>]*>` (kit_regional's `</script>` misses `</script >`)
_LD_OPEN = re.compile(r"""<script[^>]+type=["']application/ld\+json["'][^>]*>""", re.I)
SCAN_MAX = 2_000_000  # characters of a page scanned for script blocks
_SCRIPT_OPEN = re.compile(r"<script", re.I)
_SCRIPT_CLOSE = re.compile(r"</script\b", re.I)


def _script_bodies(page: Any, open_tag: re.Pattern[str]) -> Iterator[str]:
    """Bodies of the ``<script>`` blocks whose opening tag matches ``open_tag``, in page order.

    Same result as ``<open tag>(.*?)</script\b[^>]*>`` with re.I | re.S, but in one forward pass: a lazy
    ``.*?`` retried at every unclosed ``<script`` is quadratic (8000 repeats took 11 s). Here the cursor never
    moves back, and a missing ``>`` or ``</script`` ends the scan, as nothing later can close. Only the first
    SCAN_MAX characters are read."""
    if not isinstance(page, str):
        return
    text = page[:SCAN_MAX]  # no .lower(): it can change the length and shift every index
    pos = 0
    while True:
        opener = _SCRIPT_OPEN.search(text, pos)
        if not opener:
            return
        gt = text.find(">", opener.start())
        if gt < 0:
            return
        if not open_tag.fullmatch(text, opener.start(), gt + 1):
            pos = gt + 1
            continue
        closer = _SCRIPT_CLOSE.search(text, gt + 1)
        if not closer:
            return
        end = text.find(">", closer.end())
        if end < 0:
            return
        yield text[gt + 1:closer.start()]
        pos = end + 1

SITES_MAX_REQUESTS = 30  # hard cap per site per run (index + searches + detail pages)
DETAIL_CAP = 15  # ad pages read per run, per site
LINK_CAP = 2000  # unique ad links collected from one page (the query filter runs after, so above DETAIL_CAP)
PAUSE_S = 1.0  # between two requests to one site (tests set it to 0)
AIRSWIFT_PAUSE_S = 15.0  # airswift.com answered 429 to anything faster (freehire pacer.go:106-138)
AIRSWIFT_DETAILS = 8  # so few pages, because of that pace
EMAGINE_PAGES = 3  # of 500 ads each (emagine.go:41)
GOLANG_PAGES = 3  # of 1000 rows each (golangjobs.go:51)
JOPPY_COMPANIES = 25  # company pages per run
GOLANG_MAX_AGE_DAYS = 60  # golangjobs.go:55-61: the board never retires an ad, so age is the only staleness signal

_WS = re.compile(r"\s+")


def _pause(seconds: float | None = None) -> None:
    s = PAUSE_S if seconds is None else seconds
    if s > 0:
        time.sleep(s)


# ------------------------------------------------------------------------------------ countries
# ISO -> names a place may use. Original data (neither reference lists one); ambiguous names such as
# Georgia or Jersey are left out on purpose.
_EXTRA_NAMES = {
    "AR": "Argentina", "BD": "Bangladesh", "BG": "Bulgaria", "CL": "Chile", "CN": "China", "CO": "Colombia",
    "CY": "Cyprus", "CZ": "Czech Republic", "EE": "Estonia", "EG": "Egypt", "GR": "Greece",
    "HK": "Hong Kong", "HR": "Croatia", "HU": "Hungary", "ID": "Indonesia", "IL": "Israel", "IS": "Iceland",
    "JP": "Japan", "KE": "Kenya", "LK": "Sri Lanka", "LT": "Lithuania", "LU": "Luxembourg", "LV": "Latvia",
    "MT": "Malta", "MY": "Malaysia", "NG": "Nigeria", "PE": "Peru", "PH": "Philippines", "PK": "Pakistan",
    "QA": "Qatar", "RO": "Romania", "RS": "Serbia", "SA": "Saudi Arabia", "SK": "Slovakia",
    "SI": "Slovenia", "TH": "Thailand", "TR": "Turkey", "UA": "Ukraine", "VN": "Vietnam",
}
_ALIASES = {
    "GB": ("uk", "great britain", "england", "scotland", "wales", "northern ireland"),
    "US": ("usa", "united states of america"), "AE": ("uae",), "DE": ("deutschland",), "ES": ("espana",),
    "NL": ("the netherlands", "holland"), "CZ": ("czechia",), "TR": ("turkiye",), "KR": ("south korea",),
}


def _norm(text: str) -> str:
    return " " + re.sub(r"[^a-z0-9]+", " ", text.lower()).strip() + " "


def _build_names() -> dict[str, tuple[str, ...]]:
    base: dict[str, str] = dict(getattr(_kf, "_COUNTRY_NAMES", {}))
    base.update(_EXTRA_NAMES)
    out: dict[str, tuple[str, ...]] = {}
    for iso, name in base.items():
        out[iso] = (_norm(name),) + tuple(_norm(a) for a in _ALIASES.get(iso, ()))
    return out


_NAMES = _build_names()


def _iso_hits(text: str, *, codes: bool = True) -> set[str]:
    """ISO codes of the countries a place text names. A bare two-letter code counts only as the LAST
    comma-separated part ("Atlanta, US"), so "San Francisco, CA, US" is the United States, not Canada."""
    norm = _norm(text or "").replace(" new south wales ", " ")  # Australia, not the "wales" alias of GB
    hits = {iso for iso, names in _NAMES.items() if any(n in norm for n in names)}
    if codes:
        last = re.split(r"[,;|]", text or "")[-1].strip()
        if len(last) == 2 and last.isupper():
            last = "GB" if last == "UK" else last
            if last in _NAMES:
                hits.add(last)
    return hits


def _place_ok(place: str, countries: list[str], remote: bool | None = False) -> bool:
    """True when the ad can serve one of the user's countries: no countries given or "*", a remote ad,
    a place that names no country the kit knows, or a place that names one of them."""
    have = {("GB" if c.upper() == "UK" else c.upper()) for c in countries or []}
    if not have or "*" in have or remote:
        return True
    hits = _iso_hits(place)
    return not hits or bool(hits & have)


def _remote(text: str) -> bool | None:
    return True if "remote" in (text or "").lower() else None


# --------------------------------------------------------------------------------------- helpers
_STOPWORDS = frozenset(("and", "the", "for", "with", "jr", "sr", "new"))


def _slug_hit(slug: str, queries: list[str]) -> bool:
    """A cheap look at an ad's URL slug before spending a request on it: any word (three letters or more)
    of any query occurs in the slug. No queries means every ad passes. The real match runs on the page."""
    words = {w for q in _queries(queries) for w in re.split(r"\W+", q.lower()) if len(w) >= 3 and w not in _STOPWORDS}
    if not words:
        return True
    text = slug.lower().replace("-", " ")
    return any(w in text for w in words)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _unescaped(value: Any) -> str:
    return _html.unescape(str(value)) if value else ""


def _ld_postings(page: str) -> list[dict[str, Any]]:
    """Every schema.org JobPosting object in the page's ld+json blocks (a list or @graph is opened)."""
    out: list[dict[str, Any]] = []
    for body in _script_bodies(page, _LD_OPEN):
        try:
            data = json.loads(body, strict=False)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for n in nodes:
            t = n.get("@type") if isinstance(n, dict) else None
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                out.append(dict(n))
    return out


def _plain(node: Any) -> str:
    """Readable text of a node: block tags become line breaks, script and style are skipped."""
    block = frozenset("p div li br h1 h2 h3 h4 h5 h6 ul ol tr section".split())
    parts: list[str] = []
    todo: list[Any] = [node]
    while todo:
        n = todo.pop()
        if isinstance(n, str):
            parts.append(n)
            continue
        if n.tag in ("script", "style"):
            continue
        if n.tag in block:
            todo.append("\n")
        todo.extend(reversed(n.kids))
    lines = [_WS.sub(" ", ln).strip() for ln in "".join(parts).split("\n")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _tree(page: Any) -> Any:
    t = _Tree()
    try:
        t.feed(page if isinstance(page, str) else "")
        t.close()
    except Exception:  # noqa: BLE001 - a broken page is just empty
        pass
    return t.root


def _by_class(root: Any, cls: str, tag: str | None = None) -> Any:
    return _first(root, lambda n: (tag is None or n.tag == tag) and cls in n.classes())


_HAS_CONTENT = ("ld+json", "<h1", "__NEXT_DATA__", "<urlset", "<sitemapindex", "<loc>")
GONE = (404, 410)


def _blocked(body: Any) -> bool:
    """A 200 HTML page that lacks the content it should have and looks like a login wall or bot check."""
    global _kb_block
    if not isinstance(body, str) or any(m in body for m in _HAS_CONTENT):
        return False
    if _kb_block is None:
        try:
            import kit_bigboards as _kbm  # type: ignore[import-not-found]
        except ImportError:
            sys.path.insert(0, str(Path(__file__).resolve().parent))
            import kit_bigboards as _kbm
        _kb_block = _kbm._BLOCK
    return bool(_kb_block.search(body))


_SITEMAP_MAX = 10_000_000  # a sitemap can list tens of thousands of postings; the 2 MB page default would drop it


def _fetch(http: HttpFn, stats: Stats, name: str, budget: Any, url: str, *, ad: bool = False,
           check_block: bool = True, **kw: Any) -> Any:
    """One counted request; the body on HTTP 200, else None.

    The run ends (budget.left = 0) on the first answer that is not HTTP 200 (403/406/429 refusals, status 0,
    5xx, anything else) and on a 200 that is a login or CAPTCHA page. The one exception: a 404/410 on an ad
    page (``ad=True``) only skips that ad (airswift.go:67-71). ``check_block=False`` skips the login/CAPTCHA
    check for a body that is not a page (a JS bundle names ``captchaToken`` without being a security wall)."""
    if budget.left <= 0:
        return None
    budget.left -= 1
    stats["tried"] = stats.get("tried", 0) + 1
    status, body = http(url, once=True, **kw)  # once: the first 429 must end the site, not be retried twice
    if status == 200 and body is not None:
        if check_block and _blocked(body):
            print(f"warning: {name} answered a login or security page (run stopped)", file=sys.stderr)
            budget.left = 0
            return None
        stats["ok"] = stats.get("ok", 0) + 1
        return body
    print(f"warning: {name} answered HTTP {status} (nothing read)", file=sys.stderr)
    if not (ad and status in GONE) and status != 200:
        budget.left = 0
    return None


def _read(http: HttpFn, stats: Stats, name: str, budget: Any, urls: list[str], cap: int,
          parse: Callable[[Any, str], Job | None], *, pause: float | None = None) -> list[Job]:
    """Read at most ``cap`` ad pages (HTML text) and parse each; a refusal ends the run."""
    jobs: list[Job] = []
    for url in urls[:cap]:
        if budget.left <= 0:
            break
        _pause(pause)
        job = parse(_fetch(http, stats, name, budget, url, ad=True, as_json=False), url)
        if job:
            jobs.append(job)
    return jobs


# ---------------------------------------------------------------------------------------- Airswift
# Borrowed from freehire internal/ingest/sources/airswift.go:67-71 (a 404/410 on an ad page is a confirmed-gone
# ad: skipped, the run goes on), :36 (sitemap https://www.airswift.com/sitemap.xml),
# :127 (ad URL https://www.airswift.com/jobs/<slug>-<id>), :107-122 and :84-86 (a page carrying the element
# class "c-jobs-article-expired" is a closed ad: dropped), :135-160 and :91-104 (the page's ld+json JobPosting
# gives title, description (HTML-escaped: unescaped first, :101), datePosted and jobLocation[0].address
# addressLocality / addressCountry), :41 and :37-40 (the employer is the literal "Airswift": the agency
# anonymises every end client), freehire pacer.go:106-138 (detail pages answer 429 under any quick burst;
# 15 s a request was its last, still unproven, guess; the kit reads only AIRSWIFT_DETAILS pages). Test
# shapes: airswift_test.go:9-33 and :36-76. NOT IN REFERENCE: the title match on the URL slug before
# a page is read, and that the highest id is the newest.
AIRSWIFT_SITEMAP = "https://www.airswift.com/sitemap.xml"
_AS_JOB = re.compile(r"^https://(?:www\.)?airswift\.com/jobs/([a-z0-9-]+)-(\d+)/?$")
_AS_EXPIRED = re.compile(r"""class\s*=\s*["'][^"']*c-jobs-article-expired""", re.I)


def parse_airswift_ad(page: Any, url: str) -> Job | None:
    """One Airswift ad page; None for a closed ad or a page without a JobPosting."""
    if not isinstance(page, str) or _AS_EXPIRED.search(page):
        return None
    ps = _ld_postings(page)
    p = ps[0] if ps else {}
    title = _WS.sub(" ", str(p.get("title") or "")).strip()
    if not (p and title):
        return None
    locality, ctext = _posting_place(p)
    return _made("airswift", "Airswift", title=title, company="Airswift", url=url, place=locality, country=ctext,
                 posted=_date(p.get("datePosted")), description=_unescaped(p.get("description")),
                 remote=_remote(locality))


@_feed
def fetch_airswift(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Airswift (oil, gas and engineering recruiter) read through its sitemap; worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    ads: dict[str, int] = {}
    for loc in _sitemap_locs(_fetch(http, stats, "airswift", budget, AIRSWIFT_SITEMAP, as_json=False,
                                  max_bytes=_SITEMAP_MAX)):
        m = _AS_JOB.match(loc)
        if m and _slug_hit(m.group(1), queries):
            ads[loc] = int(m.group(2))
    urls = sorted(ads, key=lambda u: ads[u], reverse=True)
    jobs = _read(http, stats, "airswift", budget, urls, AIRSWIFT_DETAILS, parse_airswift_ad,
                 pause=AIRSWIFT_PAUSE_S)
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


# ---------------------------------------------------------------------------------------- Brief
# Borrowed from freehire internal/ingest/sources/briefhq.go:11-15 and :27-28 (one static careers page,
# https://briefhq.ai/careers/, inlines every vacancy as its own JobPosting ld+json block; the Organization and
# BreadcrumbList blocks beside them are ignored, :33), :37 (a block without a title is skipped), :40-48 (url,
# title, employer = hiringOrganization.name, description HTML-escaped, :50), :43 (jobLocationType TELECOMMUTE =
# remote), :79-81 and briefhq_test.go:76 (location = locality, region, country, e.g. "San Francisco, CA, US").
# Test shape: briefhq_test.go:12-39 and :47-102. NOT IN REFERENCE: the "Brief" employer name when a block
# names none (freehire takes it from its config entry, briefhq_test.go:51).
BRIEFHQ_URL = "https://briefhq.ai/careers/"


_BRIEF_ID = re.compile(r"^[a-z0-9-]+$")


def _brief_url(url: Any, ident: Any = None) -> str:
    """The posting's own link when it is https on briefhq.ai, else the careers page plus "#<identifier>"
    (freehire briefhq.go:15-16 documents the per-job deeplink careers/#<slug>, :40 keys by identifier.value)."""
    try:
        u = urlsplit(str(url or "").strip())
        host = (u.hostname or "").lower()
        if u.scheme == "https" and host in ("briefhq.ai", "www.briefhq.ai"):
            return u.geturl()
    except ValueError:
        pass
    slug = ident.get("value") if isinstance(ident, dict) else ident
    slug = str(slug).strip() if isinstance(slug, (str, int)) else ""
    return BRIEFHQ_URL + "#" + slug if _BRIEF_ID.match(slug) else BRIEFHQ_URL


def parse_briefhq(page: Any, page_url: str = BRIEFHQ_URL) -> list[Job]:
    out: list[Job] = []
    for p in _ld_postings(page if isinstance(page, str) else ""):
        title = _WS.sub(" ", str(p.get("title") or "")).strip()
        if not title:
            continue
        loc = p.get("jobLocation")
        loc = loc[0] if isinstance(loc, list) and loc else loc
        addr = loc.get("address") if isinstance(loc, dict) else None
        addr = addr if isinstance(addr, dict) else {}
        country = addr.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        place = ", ".join(str(x).strip() for x in (addr.get("addressLocality"), addr.get("addressRegion"), country)
                          if x and str(x).strip())
        remote = True if str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE" else _remote(place)
        out.append(_made("briefhq", "Brief careers page", title=title, company=_posting_company(p) or "Brief",
                         url=_brief_url(p.get("url"), p.get("identifier")), place=place, country="",
                         posted=_date(p.get("datePosted")), description=_unescaped(p.get("description")),
                         remote=remote))
    return out


@_feed
def fetch_briefhq(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Brief's own careers page (one request); worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    jobs = parse_briefhq(_fetch(http, stats, "briefhq", budget, BRIEFHQ_URL, as_json=False))
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    seen: set[tuple[str, str]] = set()  # url-less postings share the careers URL: key on (title, url)
    uniq: list[Job] = []
    for j in jobs:
        if (j["title"], j["url"]) not in seen:
            seen.add((j["title"], j["url"]))
            uniq.append(j)
    return _keep(uniq, queries, days)


# ---------------------------------------------------------------------------------------- emagine
# Borrowed from freehire internal/ingest/sources/emagine.go:28 (POST https://portal-api.emagine.org/api/JobAds/Search),
# :56-99 (body: skipCount, maxResultCount 500 (:41), sorting "Id asc", supportedLanguageId "EN" (mandatory: a
# missing language is a 500), filter with all eight lists present and empty: a null list is a 400), :101-125
# (response items[] id, title, isPartTime, area.name, jobAdWorkLocation workLocationType / city / region /
# country; totalCount), :182-198 (page by skipCount until totalCount; an empty page ends it), :32 and :204-222
# (detail GET .../api/JobAds/details/<id>/EN gives description and status; "Closed" is dropped), :35 (public
# page https://portal.emagine.org/jobs/<id>/<slug>, slug decorative), :36-38 (employer is "emagine": the portal
# never names the end client), :13-15 (markets DE, PL, FR, PT, SE, UK and India), :247-254 (every ad is a
# contract; part-time wins). Test shapes: emagine_test.go:60-70 (listing) and :163-170 (detail). The listing
# states no posting date (:107-108). NOT IN REFERENCE: the Content-Type header on the POST, the slug rule
# (lower-case words joined by "-"), reading the newest ids first (the reference pages by id ascending
# for a stable crawl), and matching the user's titles against the listing title and practice area only.
EMAGINE_SEARCH = "https://portal-api.emagine.org/api/JobAds/Search"
EMAGINE_DETAIL = "https://portal-api.emagine.org/api/JobAds/details/%d/EN"
EMAGINE_JOB = "https://portal.emagine.org/jobs/%d/%s"
EMAGINE_PAGE = 500
EMAGINE_MARKETS = ("DE", "PL", "FR", "PT", "SE", "GB", "IN")


def _emagine_body(skip: int) -> bytes:
    return json.dumps({
        "skipCount": skip, "maxResultCount": EMAGINE_PAGE, "sorting": "Id asc",
        "filter": {"textFilters": [], "workLocationTypes": [], "workLocations": [], "professionalRolesIds": [],
                   "consultantSeniorities": [], "languageProficiencies": [], "industriesIds": [],
                   "recordIdsToExclude": []},
        "supportedLanguageId": "EN"}).encode()


def parse_emagine(payload: Any) -> list[Job]:
    """Listing items to jobs (title only: the description lives behind the detail call)."""
    out: list[Job] = []
    for r in (payload or {}).get("items") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict):
            continue
        ad_id, title = r.get("id"), _WS.sub(" ", str(r.get("title") or "")).strip()
        if not (isinstance(ad_id, int) and not isinstance(ad_id, bool) and ad_id > 0 and title):
            continue
        wl = _dict(r.get("jobAdWorkLocation"))
        parts: list[str] = []
        for key in ("city", "region", "country"):
            v = str(wl.get(key) or "").strip()
            if v and v not in parts:
                parts.append(v)
        area = _dict(r.get("area"))
        slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")
        job = _made("emagine", "emagine", title=title, company="emagine", url=EMAGINE_JOB % (ad_id, slug),
                    place=", ".join(parts), country="", remote=True if wl.get("workLocationType") == "Remote" else None,
                    extra={"contract": True, "part_time": bool(r.get("isPartTime")), "emagine_id": ad_id,
                           "area": str(area.get("name") or "").strip(), "country_text": str(wl.get("country") or "")})
        out.append(job)
    return out


@_feed
def fetch_emagine(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """emagine's freelance-contract portal, only when one of its markets is in the user's countries."""
    if not _wanted(countries, *EMAGINE_MARKETS):
        return []
    budget = _Budget(SITES_MAX_REQUESTS)
    ads: list[Job] = []
    seen = 0  # RAW items read: skipCount and the totalCount test count them, never kept rows (emagine.go:186, :193)
    reach = EMAGINE_PAGE * EMAGINE_PAGES
    skipped = 0  # oldest ads left unread because the board outgrew the page cap
    pages, requests, total = 0, 0, 0
    while pages < EMAGINE_PAGES and budget.left > 0:
        if requests:
            _pause()  # a pause before every request after the first
        requests += 1
        body = _fetch(http, stats, "emagine", budget, EMAGINE_SEARCH, method="POST", data=_emagine_body(seen),
                      headers={"Content-Type": "application/json"})
        raw = body.get("items") if isinstance(body, dict) else None
        if not isinstance(raw, list) or not raw:
            break
        count = body.get("totalCount") if isinstance(body, dict) else 0
        total = count if isinstance(count, int) and not isinstance(count, bool) else 0
        if requests == 1 and total > reach:
            # The listing runs oldest id first (emagine.go:86 "Id asc"; "Id desc" is NOT IN REFERENCE, so it is not
            # used), so past the cap the first pages hold only the oldest ads. Start the walk at the newest ones.
            skipped = seen = total - reach
            continue  # this first page is the oldest rows: not kept
        ads += parse_emagine(body)
        seen += len(raw)
        pages += 1
        if seen >= total:
            break
    if pages >= EMAGINE_PAGES and seen < total:  # the cap was hit with ads still unread (the board grew mid-walk)
        skipped = skipped or total - seen
    if skipped:
        print(f"warning: emagine lists {total} ads; {skipped} were not read (page cap {reach})", file=sys.stderr)
    ads = [j for j in ads if _place_ok(j["extra"]["country_text"], countries, False)]
    wanted = [j for j in _unique(ads) if _title_hit(j, queries)]
    wanted.sort(key=lambda j: j["extra"]["emagine_id"], reverse=True)
    for job in wanted[:DETAIL_CAP]:
        if budget.left <= 0:
            break
        _pause()
        d = _fetch(http, stats, "emagine", budget, EMAGINE_DETAIL % job["extra"]["emagine_id"], ad=True)
        if isinstance(d, dict):
            if str(d.get("status") or "").strip().lower() == "closed":
                job["extra"]["closed"] = True
                continue
            desc = strip_html(_unescaped(d.get("description")))
            if desc:
                job["description"] = desc
                job["extra"].pop("title_only", None)
    wanted = [j for j in wanted if not j["extra"].get("closed")]
    return _keep(wanted, queries, days, search=False)  # matched on the title above


def _title_hit(job: Job, queries: list[str]) -> bool:
    """Plain keyword match on the title and practice area (the listing has nothing else)."""
    qs = [q.lower().split() for q in _queries(queries)]
    if not qs:
        return True
    hay = (job["title"] + " " + str(job["extra"].get("area") or "")).lower()
    return any(all(w in hay for w in q) for q in qs)


# --------------------------------------------------------------------------------- Functional Works
# Borrowed from freehire internal/ingest/sources/functionalworks.go:21 (POST
# https://functional.works-hub.com/api/graphql), :22-26 (the one query: jobs(page_size:2000, vertical:"functional",
# published:true) { title slug remote firstPublished descriptionHtml company { name } location { city country }
# tags { label } }; the API has no paging argument and the body carries every description, so one request is
# the whole board), :27-28 (ad page https://functional.works-hub.com/jobs/<slug>), :42-59 (posting field names),
# :61-80 (POST the query as {"query": ...} and decode data.jobs), :82-88 (an ad with no slug or no company is
# dropped), :100-101 (remote flag = remote), :110-120 (location = "City, Country", whichever part exists).
# Test shape: functionalworks_test.go:45-52. NOT IN REFERENCE: the Content-Type header on the POST.
FW_URL = "https://functional.works-hub.com/api/graphql"
FW_JOB = "https://functional.works-hub.com/jobs/%s"
FW_QUERY = ('{ jobs(page_size:2000, vertical:"functional", published:true) { title slug remote firstPublished '
            'descriptionHtml company { name } location { city country } tags { label } } }')


def parse_functionalworks(payload: Any) -> list[Job]:
    data = payload.get("data") if isinstance(payload, dict) else None
    rows = data.get("jobs") if isinstance(data, dict) else None
    out: list[Job] = []
    for r in rows if isinstance(rows, list) else []:
        if not isinstance(r, dict):
            continue
        slug = str(r.get("slug") or "").strip()
        co = _dict(r.get("company"))
        company = str(co.get("name") or "").strip()
        title = _WS.sub(" ", str(r.get("title") or "")).strip()
        if not (slug and company and title):
            continue
        loc = _dict(r.get("location"))
        place = ", ".join(x for x in (str(loc.get("city") or "").strip(), str(loc.get("country") or "").strip()) if x)
        tags = [str(t.get("label")) for t in r.get("tags") or [] if isinstance(t, dict) and t.get("label")]
        remote = True if r.get("remote") is True else _remote(place)
        out.append(_made("functionalworks", "Functional Works", title=title, company=company, url=FW_JOB % slug,
                         place=place, country="", posted=_date(r.get("firstPublished")),
                         description=r.get("descriptionHtml"), remote=remote, extra={"tags": tags}))
    return out


@_feed
def fetch_functionalworks(queries: list[str], http: HttpFn, stats: Stats, *, days: int,
                          countries: list[str]) -> list[Job]:
    """Functional Works (functional-programming jobs): one GraphQL request; worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    body = _fetch(http, stats, "functionalworks", budget, FW_URL, method="POST",
                  data=json.dumps({"query": FW_QUERY}).encode(), headers={"Content-Type": "application/json"})
    jobs = [j for j in parse_functionalworks(body) if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


# -------------------------------------------------------------------------------------- Golang Jobs
# Borrowed from freehire internal/ingest/sources/golangjobs.go:43 (home https://www.golangjobs.tech/), :65-67
# (the app bundle path is src="/assets/index-<hash>.js" on the home page, fetched from the fixed host :233),
# :68-70 (the bundle names the site's own Supabase project and its PUBLIC anon key: "https://<20 chars>.supabase.co",
# "eyJ..."; the key is the one every visitor's browser uses, not a secret of yours), :90-92 (headers apikey and
# Authorization: Bearer <anon key>), :209-221 (GET <project>/rest/v1/jobs with select, is_archived=eq.false,
# order=id, limit 1000 (:51), offset), :47-48 (columns id, title, company, type, description, posted_at,
# application_url, slug, cities(name,country)), :244-271 (a row needs title, company, slug and posted_at;
# the link is application_url when it is a web URL no other row shares, else https://www.golangjobs.tech/golang-jobs/<slug>,
# :122-130 and :251-254), :55-61 (rows older than 60 days are dropped: the board never retires an ad),
# :273-293 (city "Remote" = remote, its country is the hiring scope: a country, a region or "Global").
# Test shape: golangjobs_test (testdata/golangjobs/jobs.json). Left out: the count queries (:192-206) that
# prove the whole table was read; the kit takes up to GOLANG_PAGES pages. NOT IN REFERENCE: nothing else.
GOLANG_HOME = "https://www.golangjobs.tech/"
GOLANG_POSTING = "https://www.golangjobs.tech/golang-jobs/"
GOLANG_COLUMNS = ("id,title,company,type,description,salary_min,salary_max,salary_currency,"
                  "posted_at,application_url,slug,cities(name,country)")
GOLANG_PAGE = 1000
_GO_ASSET = re.compile(r'src="/(assets/index-[A-Za-z0-9_-]+\.js)"')
_GO_CLIENT = re.compile(r'"(https://[a-z0-9]{20}\.supabase\.co)"\s*,\s*"(eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+)"')


def _web_url(u: Any) -> str:
    """The posting link as a plain https host link (no userinfo, no port, no fragment); "" otherwise."""
    try:
        s = urlsplit(str(u or "").strip())
        host = s.hostname
        s.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return ""
    if s.scheme != "https" or not host or s.username is not None or s.password is not None or s.port is not None:
        return ""
    return f"https://{host}{s.path}" + (f"?{s.query}" if s.query else "")


def parse_golangjobs(rows: Any, now: datetime | None = None) -> list[Job]:
    now = now or datetime.now(timezone.utc)
    cutoff = (now - timedelta(days=GOLANG_MAX_AGE_DAYS)).strftime("%Y-%m-%d")
    rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
    links: dict[str, int] = {}
    for r in rows:
        link = _web_url(r.get("application_url"))
        if link:
            links[link] = links.get(link, 0) + 1
    out: list[Job] = []
    for r in rows:
        title = _WS.sub(" ", str(r.get("title") or "")).strip()
        company = _WS.sub(" ", str(r.get("company") or "")).strip()
        slug = str(r.get("slug") or "").strip()
        posted = _date(r.get("posted_at"))
        if not (title and company and slug and posted and posted >= cutoff):
            continue
        link = _web_url(r.get("application_url"))
        url = link if link and links.get(link) == 1 else GOLANG_POSTING + quote(slug, safe="")
        city_row = _dict(r.get("cities"))
        city, country = str(city_row.get("name") or "").strip(), str(city_row.get("country") or "").strip()
        is_remote = city.lower() == "remote"
        if is_remote:
            place = "Remote" + ("" if not country or country.lower() == "global" else ", " + country)
        else:
            place = ", ".join(x for x in (city, country) if x)
        job = _made("golangjobs", "Golang Jobs", title=title, company=company, url=url, place=place, country="",
                    posted=posted, description=r.get("description"), remote=True if is_remote else None,
                    extra={"tags": ["go"], "scope_country": country})
        out.append(job)
    return out


@_feed
def fetch_golangjobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Golang Jobs (Go developer postings), read the way the site's own page reads them; worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    home = _fetch(http, stats, "golangjobs", budget, GOLANG_HOME, as_json=False)
    asset = _GO_ASSET.search(home) if isinstance(home, str) else None
    if not asset:
        return []
    _pause()
    bundle = _fetch(http, stats, "golangjobs", budget, GOLANG_HOME + asset.group(1), as_json=False,
                    check_block=False)
    client = _GO_CLIENT.search(bundle) if isinstance(bundle, str) else None
    if not client:
        return []
    base, key = client.group(1), client.group(2)
    headers = {"apikey": key, "Authorization": "Bearer " + key}
    rows: list[Any] = []
    capped = False
    for _page in range(GOLANG_PAGES):
        _pause()
        # newest first (golangjobs.go:213 sets order=id, ascending; id.desc is NOT IN REFERENCE): the page cap then cuts
        # the OLDEST rows, and the 60-day window (GOLANG_MAX_AGE_DAYS) only keeps the newest ones. A row added
        # mid-walk shifts the offsets and repeats one row: _unique() drops it
        qs = (f"select={quote(GOLANG_COLUMNS, safe=',()')}&is_archived=eq.false&order=id.desc"
              f"&limit={GOLANG_PAGE}&offset={len(rows)}")
        got = _fetch(http, stats, "golangjobs", budget, base + "/rest/v1/jobs?" + qs, headers=headers)
        if not isinstance(got, list) or not got:
            break
        rows += got
        capped = len(got) >= GOLANG_PAGE
        if not capped:
            break
    if capped:  # the last page was full: older rows may remain (the row count is not asked for)
        print(f"warning: golangjobs page cap reached after {len(rows)} rows; older ads were not read", file=sys.stderr)
    # A remote ad's country is its hiring scope, so it is checked like any place ("Global" or a region names none).
    jobs = [j for j in parse_golangjobs(rows)
            if _place_ok(j["extra"]["scope_country"] or j["location"], countries, False)]
    return _keep(_unique(jobs), queries, days)


# ------------------------------------------------------------------------------------------- Joppy
# Borrowed from freehire internal/ingest/sources/joppy.go:15-21 (Spain-only tech platform; no search API: the
# directory is walked), :46-48 (base https://www.joppy.me, sitemap /sitemap.directory.xml), :315-339 (company
# slugs come from /companies/<slug> and /companies/<slug>/<uid> URLs; every slug is walked, the company page
# being the ground truth), :150-176 (GET /companies/<slug>; the page's __NEXT_DATA__ script holds
# props.pageProps.company {name, slug, jobs[]} with every open ad in full), :70-102 (job fields uid, title,
# description, isSalaryPublic, sponsorVisa, relocationPack, skills[name,isMandatory], place{isRemote, isHybrid,
# isOffice, located, cities[]}, languages[name,level], salaryMin, salaryMax), :180-209 (an ad needs uid and
# title; URL https://www.joppy.me/companies/<company slug>/<uid>; employer = company name), :222-248 (work
# mode from the three flags, location = cities joined by "; " else the free text), :203-208 (salary only
# when public, EUR a year), :260-313 (skills, languages, visa and relocation folded into the description).
# Test shapes: joppy_test.go:36-56 (company page), :170-200 (ads) and :205-221 (sitemap). NOT IN REFERENCE:
# reading JOPPY_COMPANIES pages only, ordered by the sitemap's lastmod (newest first) then ads listed
# before bare companies, and that the real sitemap carries lastmod (the fixture does).
JOPPY_BASE = "https://www.joppy.me"
JOPPY_SITEMAP = JOPPY_BASE + "/sitemap.directory.xml"
_NEXT_DATA_OPEN = re.compile(r"""<script[^>]+id=["']__NEXT_DATA__["'][^>]*>""", re.I)


def _sitemap_entries(text: Any) -> list[tuple[str, str]]:
    """(loc, lastmod) of every <url> in a sitemap (namespace-blind); [] when it is not parseable XML."""
    root = safe_fromstring(text) if isinstance(text, str) else None
    if root is None:
        return []
    out: list[tuple[str, str]] = []
    for el in root.iter():
        if str(el.tag).rsplit("}", 1)[-1] != "url":
            continue
        kids = {str(k.tag).rsplit("}", 1)[-1]: (k.text or "").strip() for k in el}
        if kids.get("loc"):
            out.append((kids["loc"], kids.get("lastmod", "")))
    return out


def joppy_slugs(sitemap: Any) -> list[str]:
    """Company slugs from the directory sitemap: newest lastmod first, then those with an ad listed."""
    prefix = JOPPY_BASE + "/companies/"
    info: dict[str, tuple[str, bool]] = {}
    for loc, lastmod in _sitemap_entries(sitemap):
        if not loc.startswith(prefix):
            continue
        slug, _, rest = loc[len(prefix):].partition("/")
        if not slug:
            continue
        old_mod, old_ad = info.get(slug, ("", False))
        info[slug] = (max(old_mod, lastmod), old_ad or bool(rest))
    return sorted(sorted(info), key=lambda s: info[s], reverse=True)


def parse_joppy_company(page: Any) -> list[Job]:
    body = next(_script_bodies(page, _NEXT_DATA_OPEN), None)
    try:
        data: Any = json.loads(body) if body is not None else None
        company = data["props"]["pageProps"]["company"]
    except (ValueError, KeyError, TypeError):
        return []
    if not isinstance(company, dict):
        return []
    name, slug = str(company.get("name") or "").strip(), str(company.get("slug") or "").strip()
    out: list[Job] = []
    for j in company.get("jobs") or []:
        if not isinstance(j, dict):
            continue
        uid, title = str(j.get("uid") or "").strip(), _WS.sub(" ", str(j.get("title") or "")).strip()
        if not (name and slug and uid and title):
            continue
        place = _dict(j.get("place"))
        cities = [str(c).strip() for c in place.get("cities") or [] if str(c).strip()]
        where = "; ".join(cities) if cities else str(place.get("located") or "").strip()
        remote = True if (place.get("isRemote") and not place.get("isOffice") and not place.get("isHybrid")) else None
        skills = [s for s in j.get("skills") or [] if isinstance(s, dict) and str(s.get("name") or "").strip()]
        must = [str(s["name"]).strip() for s in skills if s.get("isMandatory")]
        nice = [str(s["name"]).strip() for s in skills if not s.get("isMandatory")]
        langs = [f"{str(x.get('name')).strip()} (level {x.get('level')}/5)" for x in j.get("languages") or []
                 if isinstance(x, dict) and str(x.get("name") or "").strip()]
        extras = [("Must-have skills: " + ", ".join(must) + ".") if must else "",
                  ("Nice-to-have skills: " + ", ".join(nice) + ".") if nice else "",
                  ("Required languages: " + ", ".join(langs) + ".") if langs else "",
                  "The employer sponsors a work visa for this role." if j.get("sponsorVisa") else "",
                  "A relocation package is offered." if j.get("relocationPack") else "",
                  "Open only to candidates already eligible to work in the EU." if j.get("onlyEuCandidates") else ""]
        lo, hi = j.get("salaryMin"), j.get("salaryMax")
        if j.get("isSalaryPublic") and (lo or hi):
            extras.append("Salary: " + " - ".join(str(x) for x in (lo, hi) if x) + " EUR a year.")
        desc = strip_html(j.get("description"))
        body = "\n\n".join(x for x in [desc, *extras] if x)
        out.append(_made("joppy", "Joppy", title=title, company=name,
                         url=f"{JOPPY_BASE}/companies/{slug}/{uid}", place=where, country="Spain",
                         description=body, remote=remote,
                         extra={"tags": must + nice, "visa_sponsor": bool(j.get("sponsorVisa"))}))
    return out


@_feed
def fetch_joppy(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Joppy (Spain), only when ES is one of the user's countries; reads the newest company pages."""
    if not _wanted(countries, "ES"):
        return []
    budget = _Budget(SITES_MAX_REQUESTS)
    slugs = joppy_slugs(_fetch(http, stats, "joppy", budget, JOPPY_SITEMAP, as_json=False, max_bytes=_SITEMAP_MAX))
    jobs: list[Job] = []
    for slug in slugs[:JOPPY_COMPANIES]:
        if budget.left <= 0:
            break
        _pause()
        jobs += parse_joppy_company(_fetch(http, stats, "joppy", budget, f"{JOPPY_BASE}/companies/{quote(slug)}",
                                           as_json=False))
    return _keep(_unique(jobs), queries, days)


# ------------------------------------------------------------------------------------------ Rapyd
# Borrowed from freehire internal/ingest/sources/rapyd.go:12-17 (Rapyd's own WordPress careers site, no ATS,
# server-rendered HTML, no JobPosting block), :33 (listing https://www.rapyd.net/company/careers-search/),
# :92-98 (positions are the links matching /company/careers/positions/<slug>/, resolved against the listing URL,
# first seen order, no repeats), :100-108 (slug regex), :45-90 (detail page: :58-65 the h1 is the title, a page
# without one is not a posting; :67-70 the element with class "country-term" is the location; :72-77 the column
# with class "single-career-position__main" is the description; :86-88 remote only if the location text says
# so, never the title). Test shape: rapyd_test.go:25-36 (detail), :14-23 (listing) and :74-113. NOT IN
# REFERENCE: the employer name "Rapyd" (freehire uses e.Company from its config entry, rapyd.go:83;
# rapyd_test.go:75), the title match on the URL slug before a page is read, and the cap on pages.
RAPYD_LIST = "https://www.rapyd.net/company/careers-search/"
_RAPYD_POS = re.compile(r"/company/careers/positions/([^/?#]+)")


def rapyd_links(page: Any) -> list[str]:
    """Absolute position URLs on the listing page, in order, without repeats."""
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(page, str):
        return out
    for n in _walk(_tree(page)):
        href = n.attrs.get("href", "") if n.tag == "a" else ""
        m = _RAPYD_POS.search(href)
        if m:
            url = f"https://www.rapyd.net/company/careers/positions/{m.group(1)}/"
            if url not in seen:
                seen.add(url)
                out.append(url)
                if len(out) >= LINK_CAP:
                    break
    return out


def parse_rapyd_ad(page: Any, url: str) -> Job | None:
    root = _tree(page)
    h1 = _first(root, lambda n: n.tag == "h1")
    title = _text(h1) if h1 else ""
    if not title:
        return None
    loc = _by_class(root, "country-term")
    where = _text(loc) if loc else ""
    main = _by_class(root, "single-career-position__main")
    return _made("rapyd", "Rapyd careers", title=title, company="Rapyd", url=url, place=where, country="",
                 description=_plain(main) if main else "", remote=_remote(where))


@_feed
def fetch_rapyd(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Rapyd's own careers site (listing, then position pages); worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    links = [u for u in rapyd_links(_fetch(http, stats, "rapyd", budget, RAPYD_LIST, as_json=False))
             if _slug_hit(u.rstrip("/").rsplit("/", 1)[-1], queries)]
    jobs = _read(http, stats, "rapyd", budget, links, DETAIL_CAP, parse_rapyd_ad)
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


# ----------------------------------------------------------------------------------- StartupAndVC
# Borrowed from freehire internal/ingest/sources/startupandvc.go:13-23 (curated venture-capital jobs board that
# links out to the original posting, one listing page of at most 100 cards, no paging), :37-44 (base
# https://www.startupandvc.com/ and listing https://www.startupandvc.com/venture-capital-jobs), :46-59 (listing
# links), :130-138 (a vacancy link is /venture-capital-jobs/<slug>; the bare listing path and deeper paths never
# match), :62-94 (detail page: ld+json JobPosting gives title, hiringOrganization.name as the employer,
# datePosted; no JobPosting = not a posting), :96-116 (posting struct; location = first jobLocation place),
# :118-128 (the real description is the element with class "w-richtext"; the ld+json one is a one-line stub),
# :140-143 (datePosted layout "Jan 2, 2006", e.g. "Jul 07, 2026"), :145-163 (the original posting is the href of
# the page's a.button-large "Apply now" link with its query and fragment cut off: they are the board's
# tracking). Test shapes: startupandvc_test.go:17-33 (detail) and :37-48 (listing). NOT IN REFERENCE: the title
# match on the URL slug before a page is read, the cap on pages, and dropping an ad whose page names no employer
# (freehire falls back to e.Company, startupandvc.go:88).
SVC_BASE = "https://www.startupandvc.com/"
SVC_LIST = SVC_BASE + "venture-capital-jobs"
_SVC_SLUG = re.compile(r"/venture-capital-jobs/([a-z0-9][a-z0-9-]*)(?:$|[?#])")


def _svc_date(value: Any) -> str:
    s = str(value or "").strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return _date(s)


def _svc_slug(url: str) -> str:
    m = _SVC_SLUG.search(url)
    return m.group(1) if m else ""


def svc_links(page: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    if not isinstance(page, str):
        return out
    for n in _walk(_tree(page)):
        href = n.attrs.get("href", "") if n.tag == "a" else ""
        slug = _svc_slug(href) if not href.startswith("//") else ""
        if not slug:
            continue
        try:
            host = (urlsplit(href).hostname or "").lower() if href.startswith("http") else "www.startupandvc.com"
        except ValueError:
            continue
        # NOT IN REFERENCE: freehire resolves any absolute href (html.go:228-251); the kit sends a request only
        # to the board's own host, and rebuilds the https URL from the slug (no http, port, query or repeats).
        if host in ("www.startupandvc.com", "startupandvc.com"):
            url = SVC_BASE + "venture-capital-jobs/" + slug
            if url not in seen:
                seen.add(url)
                out.append(url)
                if len(out) >= LINK_CAP:
                    break
    return out


def _svc_apply_link(href: str) -> str:
    """The Apply-now target without query, fragment, userinfo; "" when it is not a plain https host link."""
    try:
        u = urlsplit(href.strip())
        host = u.hostname
        u.port  # noqa: B018 - raises ValueError on a bad port
    except ValueError:
        return ""
    if u.scheme != "https" or not host or u.username is not None or u.password is not None:
        return ""
    return f"https://{host}{u.path}"


def parse_startupandvc_ad(page: Any, url: str) -> Job | None:
    ps = _ld_postings(page if isinstance(page, str) else "")
    p = ps[0] if ps else {}
    title, company = _WS.sub(" ", str(p.get("title") or "")).strip(), _posting_company(p)
    if not (title and company):
        return None
    locality, ctext = _posting_place(p)
    root = _tree(page)
    body = _by_class(root, "w-richtext")
    desc = _plain(body) if body else ""
    btn = _first(root, lambda n: n.tag == "a" and "button-large" in n.classes() and bool(n.attrs.get("href")))
    link = _svc_apply_link(btn.attrs["href"]) if btn else ""
    link = link or url
    return _made("startupandvc", "StartupAndVC", title=title, company=company, url=link, place=locality,
                 country=ctext, posted=_svc_date(p.get("datePosted")),
                 description=desc or _unescaped(p.get("description")), remote=_remote(locality))


@_feed
def fetch_startupandvc(queries: list[str], http: HttpFn, stats: Stats, *, days: int,
                       countries: list[str]) -> list[Job]:
    """StartupAndVC (venture-capital roles): the listing, then the matching ads; worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    links = [u for u in svc_links(_fetch(http, stats, "startupandvc", budget, SVC_LIST, as_json=False))
             if _slug_hit(_svc_slug(u), queries)]
    jobs = _read(http, stats, "startupandvc", budget, links, DETAIL_CAP, parse_startupandvc_ad)
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


# ---------------------------------------------------------------------------------------- TechTree
# Borrowed from freehire internal/ingest/sources/techtree.go:11-16 (small AI-recruiting board; flat sitemap, ld+json
# JobPosting plus a "prose" body), :37 (sitemap https://jobs.techtree.dev/sitemap.xml), :39-47 (an ad is
# /job/<uuid>; other sitemap entries are dropped), :68-105 (detail page: :81-84 an ad with no employer in
# hiringOrganization.name is dropped; :86-90 the full text is the element with class "prose" and a page without it
# is dropped, the ld+json description is only a short summary; :92 jobLocation.address is a plain string, e.g.
# "Latin America (remote)"), :107-118 (datePosted is a zone-less ISO time, "2026-09-11T07:46:06.765320"),
# :120-136 (posting struct). Test shapes: techtree_test.go:22-31 (sitemap) and :34-40 (ld+json ad with a "prose"
# body); the sitemap entry shape is testdata/techtree_sitemap.xml:19-23.
# NOT IN REFERENCE: which ads come first in the sitemap (the kit reads the first DETAIL_CAP in file order; the
# URL carries no title to match, so the user's titles are matched on the page).
TT_SITEMAP = "https://jobs.techtree.dev/sitemap.xml"
_TT_JOB = re.compile(r"/job/([0-9a-fA-F-]{36})")


def parse_techtree_ad(page: Any, url: str) -> Job | None:
    ps = _ld_postings(page if isinstance(page, str) else "")
    p = ps[0] if ps else {}
    title, company = _WS.sub(" ", str(p.get("title") or "")).strip(), _posting_company(p)
    root = _tree(page)
    body = _by_class(root, "prose")
    if not (title and company and body):
        return None
    loc = p.get("jobLocation")
    addr = loc.get("address") if isinstance(loc, dict) else ""
    where = str(addr if isinstance(addr, str) else "").strip()
    return _made("techtree", "TechTree", title=title, company=company, url=url, place=where, country="",
                 posted=_date(p.get("datePosted")), description=_plain(body), remote=_remote(where))


@_feed
def fetch_techtree(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """TechTree (AI recruiting agency board) read through its sitemap; worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    urls: list[str] = []
    seen: set[str] = set()
    for loc in _sitemap_locs(_fetch(http, stats, "techtree", budget, TT_SITEMAP, as_json=False,
                                      max_bytes=_SITEMAP_MAX)):
        m = _TT_JOB.search(loc)
        if m and "techtree.dev" in loc:
            url = "https://jobs.techtree.dev/job/" + m.group(1)
            if url not in seen:
                seen.add(url)
                urls.append(url)
                if len(urls) >= LINK_CAP:
                    break
    jobs = _read(http, stats, "techtree", budget, urls, DETAIL_CAP, parse_techtree_ad)
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "airswift": fetch_airswift,
    "briefhq": fetch_briefhq,
    "emagine": fetch_emagine,
    "functionalworks": fetch_functionalworks,
    "golangjobs": fetch_golangjobs,
    "joppy": fetch_joppy,
    "rapyd": fetch_rapyd,
    "startupandvc": fetch_startupandvc,
    "techtree": fetch_techtree,
}
