"""Niche job sites, batch B, for the Job360 hunt kit (3 small feeds).

Same contract as ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. Every ad names its own
employer (the board name is never put in ``company``) and links to the original posting where the site
gives that link. One run sends at most ``SITES_MAX_REQUESTS`` requests per site; the first answer that is
not HTTP 200 (403, 406, 429, status 0, 5xx) and a login or CAPTCHA page end that site's run at once; only a
404/410 on an ad page skips that ad. Nothing here ranks or scores. No logins, no cookies, no keys of yours.
Standard library only.

Country rules: the sites list ads worldwide, so
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
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import urlsplit

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
PAUSE_S = 1.0  # between two requests to one site (tests set it to 0)
AIRSWIFT_PAUSE_S = 15.0  # airswift.com answered 429 to anything faster (freehire pacer.go:106-138)
AIRSWIFT_DETAILS = 8  # so few pages, because of that pace

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
    for loc in _sitemap_locs(_fetch(http, stats, "airswift", budget, AIRSWIFT_SITEMAP, as_json=False)):
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


def _brief_url(url: Any) -> str:
    """The posting's own link when it is https on briefhq.ai, else the careers page."""
    try:
        u = urlsplit(str(url or "").strip())
        host = (u.hostname or "").lower()
    except ValueError:
        return BRIEFHQ_URL
    return u.geturl() if u.scheme == "https" and host in ("briefhq.ai", "www.briefhq.ai") else BRIEFHQ_URL


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
                         url=_brief_url(p.get("url")), place=place, country="",
                         posted=_date(p.get("datePosted")), description=_unescaped(p.get("description")),
                         remote=remote))
    return out


@_feed
def fetch_briefhq(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Brief's own careers page (one request); worldwide."""
    budget = _Budget(SITES_MAX_REQUESTS)
    jobs = parse_briefhq(_fetch(http, stats, "briefhq", budget, BRIEFHQ_URL, as_json=False))
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


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


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "airswift": fetch_airswift,
    "briefhq": fetch_briefhq,
    "functionalworks": fetch_functionalworks,
}
