"""India and Gulf job boards for the Job360 hunt kit (third national-services module).

Same contract as ``kit_gov.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. Each fetcher runs ONLY
when one of its countries is in the user's ``countries`` (or "*", which the core passes for
``--visa-onsite``: then it runs everywhere it can, and the CORE keeps only ads that say they sponsor a
visa, exactly as for kit_gov) and returns ``[]`` with no request otherwise. Nothing here ranks or scores.
No logins, no cookies. Standard library only.

Every ad names its own employer; the board name is never put in ``company``. One run sends at most
``REGIONAL_MAX_REQUESTS`` requests per site (searches plus detail pages), and a refusal (403, 406, 429)
ends that site's run at once: repeating a refused request only holds the refusal up.

Endpoints, params, headers and field names follow the MIT-licensed references named above each fetcher
(see NOTICE.md); the code is original. The container this was written in could not reach any of these
hosts, so every parser is tested on fixtures shaped like the references' data only. These are unofficial
front-end endpoints behind bot protection (Akamai/Cloudflare): they can change or refuse a script
without notice, in which case the feed simply returns nothing. Bayt and GulfTalent also fingerprint the
TLS handshake (freehire fingerprinthttp.go:19-25) and freehire measured both as 403 from a datacenter IP
and through its proxy (AGENTS.md:731); ``urllib`` cannot imitate that handshake, so they may answer 403
from some networks. The Naukri posting API is known to refuse hosting-network IPs (freehire AGENTS.md:658)
and is expected to work from the user's own home connection.
"""
from __future__ import annotations

import json
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlencode, urljoin

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
safe_fromstring: Callable[[Any], Any] = _kf.safe_fromstring
NATIONAL_PAGES: int = _kf.NATIONAL_PAGES  # pages per title per site

REGIONAL_MAX_REQUESTS = 45  # hard cap per site per run (searches + detail pages)
BAYT_MAX_SEARCHES = 30  # of those, at most this many are search pages
DETAIL_CAP = 15  # detail pages per run (Bayt); GulfTalent reads GT_DETAILS
PAUSE_S = 1.0  # between requests to the two HTML sites (Bayt's edge throttles a fast burst to 403)
REFUSALS = (403, 406, 429)  # the edge refusing the client: end this site's run

_WS = re.compile(r"\s+")


def _wanted(countries: list[str], *codes: str) -> list[str]:
    """The given ISO codes that appear in the user's countries, then (for "*") all the others.

    The user's own codes come first: the core adds "*" next to them by default (--visa-onsite), and a shared
    request budget spent in a fixed market order could otherwise run out before the user's own country."""
    have = {c.upper() for c in countries}
    mine = [c for c in codes if c in have]
    if "*" in have:  # --visa-onsite: run everywhere; the core keeps only ads that sponsor a visa
        return mine + [c for c in codes if c not in mine]
    return mine


class _Budget:
    """Requests left for one site in one run."""

    def __init__(self, n: int = REGIONAL_MAX_REQUESTS) -> None:
        self.left = n


def _fetch(http: HttpFn, stats: Stats, name: str, budget: _Budget, url: str, **kw: Any) -> Any:
    """One counted request; the body on HTTP 200, else None (status warned on stderr, a refusal ends the run)."""
    if budget.left <= 0:
        return None
    budget.left -= 1
    stats["tried"] = stats.get("tried", 0) + 1
    status, body = http(url, **kw)
    if status == 200 and body is not None:
        stats["ok"] = stats.get("ok", 0) + 1
        return body
    print(f"warning: {name} answered HTTP {status} (nothing read)", file=sys.stderr)
    if status in REFUSALS:
        budget.left = 0
    return None


def _made(name: str, attribution: str, *, title: Any, company: Any, url: Any, place: str, country: str,
          posted: str = "", description: Any = "", remote: bool | None = None,
          extra: dict[str, Any] | None = None) -> Job:
    """One Job: location ends with the country name; ``title_only`` when no description was read."""
    ex = {"attribution": attribution, **(extra or {})}
    if not strip_html(description):
        ex["title_only"] = True
    return _job(title=title, company=company, url=url, location=_join_place(place or "", country),
                posted=posted, description=description, source="feed:" + name, board=name,
                remote=remote, extra=ex)


def _unique(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for j in jobs:
        if j["url"] not in seen:
            seen.add(j["url"])
            out.append(j)
    return out


def _pause() -> None:
    if PAUSE_S > 0:
        time.sleep(PAUSE_S)


# ---------------------------------------------------------------------- tiny HTML tree (stdlib only)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())


class _Node:
    __slots__ = ("tag", "attrs", "kids")

    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag = tag
        self.attrs = attrs
        self.kids: list[_Node | str] = []

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class _Tree(HTMLParser):
    """Builds a forgiving node tree: an end tag closes back to its open match, unknown ones are ignored."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self.stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = _Node(tag, {k: v or "" for k, v in attrs})
        self.stack[-1].kids.append(n)
        if tag not in _VOID:
            self.stack.append(n)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.stack[-1].kids.append(_Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self.stack) - 1, 0, -1):
            if self.stack[i].tag == tag:
                del self.stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self.stack[-1].kids.append(data)


def _walk(node: _Node) -> Iterator[_Node]:
    """Every element under (and including) ``node``, in document order."""
    todo = [node]
    while todo:
        n = todo.pop()
        yield n
        todo.extend(k for k in reversed(n.kids) if isinstance(k, _Node))


def _text(node: _Node) -> str:
    parts: list[str] = []
    todo: list[_Node | str] = [node]
    while todo:
        n = todo.pop()
        if isinstance(n, str):
            parts.append(n)
        else:
            todo.extend(reversed(n.kids))
    return _WS.sub(" ", "".join(parts)).strip()


def _first(node: _Node, test: Callable[[_Node], bool]) -> _Node | None:
    for n in _walk(node):
        if test(n):
            return n
    return None


# ------------------------------------------------------------- schema.org JobPosting in a page
_LD = re.compile(r"""<script[^>]+type=["']application/ld\+json["'][^>]*>(.*?)</script>""", re.I | re.S)


def _ld_posting(page: str) -> dict[str, Any]:
    """The page's first schema.org JobPosting object ({} when there is none)."""
    for m in _LD.finditer(page or ""):
        try:
            data = json.loads(m.group(1), strict=False)
        except ValueError:
            continue
        nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for n in nodes:
            t = n.get("@type") if isinstance(n, dict) else None
            if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                return dict(n)
    return {}


def _posting_place(p: dict[str, Any]) -> tuple[str, str]:
    """(locality, country text) from the posting's first jobLocation address."""
    loc = p.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = loc.get("address") if isinstance(loc, dict) else None
    if not isinstance(addr, dict):
        return "", ""
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return str(addr.get("addressLocality") or "").strip(), str(country or "").strip()


def _posting_company(p: dict[str, Any]) -> str:
    org = p.get("hiringOrganization")
    return str(org.get("name") or "").strip() if isinstance(org, dict) else ""


# ------------------------------------------------------------------------------------- Naukri
# Borrowed from freehire internal/ingest/sources/naukri.go:45 (search URL /jobapi/v3/search), :48-51
# (user agent "okhttp/4.9.0": the Android app's HTTP stack; the website's identical endpoint answers
# "recaptcha required", a browser string gets 406, an unknown one 403), :89-91 (headers appid 105 and
# systemid 105; without them 400), :53-54 (100 rows a page), :188-194 (params noOfResults, urlType
# search_by_keyword, searchType adv, jobAge, sort f = newest first, pageNo), :125-146 (row: jobId, title,
# companyName, jdURL, createdDate epoch ms, placeholders[type location]), :345-361 (a row needs jobId,
# title, companyName and a jdURL starting /job-listings-; public URL = https://www.naukri.com + jdURL;
# a "Hybrid - " prefix on the location placeholder is dropped; createdDate is milliseconds), :213 (stop on
# an empty page or at noOfJobs), :294-304 (a 406/403/429 is the edge refusing the client: stop). From
# JobSpy jobspy/naukri/__init__.py:109 (the search is by the `keyword` param) and :138-141 (a location of
# "Remote" is remote). The search row's jobDescription is a ~300-character snippet (freehire
# AGENTS.md:656), used as the description; the posting API detail (naukri.go:46) is skipped, which also
# spares the refusal it gives hosting IPs (AGENTS.md:658). NOT IN REFERENCE: freehire never sends
# `keyword` (it filters by functionAreaIdGid) and JobSpy uses other app ids (109/Naukri) and a signed
# `nkparam` header; that `keyword` works with appid 105 is a guess to verify on a real run.
NAUKRI_SEARCH = "https://www.naukri.com/jobapi/v3/search"
NAUKRI_BASE = "https://www.naukri.com"
NAUKRI_PAGE = 100
NAUKRI_HEADERS = {"User-Agent": "okhttp/4.9.0", "appid": "105", "systemid": "105"}


def parse_naukri(payload: Any) -> list[Job]:
    """Parse one Naukri search page (``jobDetails``): title, employer, place, date and a ~300 character snippet."""
    out: list[Job] = []
    for r in (payload or {}).get("jobDetails") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict):
            continue
        title = _WS.sub(" ", str(r.get("title") or "")).strip()
        company = str(r.get("companyName") or "").strip()
        path = str(r.get("jdURL") or "").strip()
        if not (str(r.get("jobId") or "").strip() and title and company and path.startswith("/job-listings-")):
            continue
        labels = {str(p.get("type")): str(p.get("label") or "").strip()
                  for p in r.get("placeholders") or [] if isinstance(p, dict)}
        place = labels.get("location", "")
        place = place[len("Hybrid - "):] if place.startswith("Hybrid - ") else place
        ms = r.get("createdDate")
        extra: dict[str, Any] = {"truncated": True}
        if labels.get("experience"):
            extra["experience"] = labels["experience"]
        # freehire naukri.go:364-366: minimumExperience is the employer's stated floor; 0 states none.
        mn = str(r.get("minimumExperience") or "").strip()
        if mn.isdigit() and int(mn) > 0:
            extra["years_min"] = int(mn)
        out.append(_made(
            "naukri", "Naukri", title=title, company=company, url=NAUKRI_BASE + path,
            place="" if place in ("India", "Remote") else place, country="India",
            posted=_date(ms / 1000) if isinstance(ms, (int, float)) and ms > 0 else "",
            description=r.get("jobDescription"), remote=True if place.endswith("Remote") else None, extra=extra))
    return out


@_feed
def fetch_naukri(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """India's Naukri search (Android-app API), only when IN is one of the user's countries."""
    if not _wanted(countries, "IN"):
        return []
    budget = _Budget()
    jobs: list[Job] = []
    for q in _queries(queries):
        seen = 0
        for page in range(1, NATIONAL_PAGES + 1):
            params: dict[str, Any] = {"noOfResults": NAUKRI_PAGE, "urlType": "search_by_keyword",
                                      "searchType": "adv", "keyword": q, "sort": "f", "pageNo": page}
            if days and days > 0:
                params["jobAge"] = days
            body = _fetch(http, stats, "naukri", budget, f"{NAUKRI_SEARCH}?{urlencode(params)}",
                          headers=NAUKRI_HEADERS)
            got = parse_naukri(body)
            jobs += got
            rows = (body or {}).get("jobDetails") if isinstance(body, dict) else None
            seen += len(rows) if isinstance(rows, list) else 0
            total = int((body or {}).get("noOfJobs") or 0) if isinstance(body, dict) else 0
            if not rows or (total and seen >= total) or len(rows) < NAUKRI_PAGE:
                break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# --------------------------------------------------------------------------------------- Bayt
# Borrowed from freehire internal/ingest/sources/bayt.go:49 (origin https://www.bayt.com), :111 (listing
# /en/<country>/jobs/?page=N), :64 (job path /jobs/<slug>-<id>/), :171-177 (relative links resolve against
# the origin), :190-224 (detail: the page's schema.org JobPosting gives title, description, datePosted,
# hiringOrganization.name as the employer :206-209, jobLocation.address.addressLocality / addressCountry;
# an ad with no employer is not used), :57 (3 detail workers: a fast burst is throttled to 403), and from
# fingerprinthttp.go:41 and :189-205 (the Chrome header set the edge expects: sec-ch-ua*, user-agent,
# accept, sec-fetch-*, accept-language). From JobSpy jobspy/bayt/__init__.py:108-131 (a title search is
# /en/<country>/jobs/<title-slug>-jobs/?page=N, slug = lower-case words joined by "-"; filters
# jb_last_modification_date_interval[] and options[sort][]=d), jobspy/bayt/constant.py:6-7 (interval
# 3 for up to 24 h, 2 for 7 days, 1 for 30 days), :3 (30 cards a page), bayt/__init__.py:157 (cards are
# li[data-js-job]), :185-257 (card: data-job-id, h2 a[href] title and link, the company in the
# div.job-company-location-wrapper after the h2, dt.jb-label-location spans = city..country,
# dt.jb-label-remote, [data-automation-jobactivedate] = epoch SECONDS) and constant.py:25-27 (country slugs are
# the hyphenated name; the tests use "uae" and "saudi-arabia", freehire bayt_test.go:57,104).
# NOT IN REFERENCE: the slugs "qatar", "kuwait", "bahrain" and "oman" (written by JobSpy's hyphenated-name
# rule only), and any Egypt, Jordan or Lebanon coverage (neither reference names them), so those are skipped.
BAYT_BASE = "https://www.bayt.com"
BAYT_PAGE = 30
BAYT_MARKETS = {  # ISO -> (country slug, country name)
    "AE": ("uae", "United Arab Emirates"),
    "SA": ("saudi-arabia", "Saudi Arabia"),
    "QA": ("qatar", "Qatar"),
    "KW": ("kuwait", "Kuwait"),
    "BH": ("bahrain", "Bahrain"),
    "OM": ("oman", "Oman"),
}
_CHROME_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/144.0.0.0 Safari/537.36")
CHROME_HEADERS = {
    "sec-ch-ua": '"Chromium";v="144", "Not(A:Brand";v="99"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"macOS"',
    "User-Agent": _CHROME_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "sec-fetch-site": "none",
    "sec-fetch-mode": "navigate",
    "sec-fetch-user": "?1",
    "sec-fetch-dest": "document",
    "Accept-Language": "en-US,en;q=0.9",
}


def _bayt_slug(text: str) -> str:
    return quote("-".join(text.lower().split()), safe="")


def _bayt_interval(days: int) -> int:
    """Bayt's jb_last_modification_date_interval for a window in days (0 = no filter)."""
    hours = days * 24 if days and days > 0 else 0
    return next((v for limit, v in ((24, 3), (24 * 7, 2), (24 * 30, 1)) if 0 < hours <= limit), 0)


def _bayt_search_url(slug: str, q: str, page: int, days: int) -> str:
    params: list[tuple[str, Any]] = []
    if _bayt_interval(days):
        params += [("filters[jb_last_modification_date_interval][]", _bayt_interval(days)), ("options[sort][]", "d")]
    params.append(("page", page))
    return f"{BAYT_BASE}/en/{slug}/jobs/{_bayt_slug(q)}-jobs/?{urlencode(params)}"


def parse_bayt_cards(page: str, country: str) -> list[Job]:
    """Parse one Bayt search page (``li[data-js-job]`` cards): title, employer, place, date; no description."""
    tree = _Tree()
    try:
        tree.feed(page or "")
        tree.close()
    except Exception:  # noqa: BLE001 - malformed markup: keep the cards read so far
        pass
    out: list[Job] = []
    for li in _walk(tree.root):
        if li.tag != "li" or "data-js-job" not in li.attrs:
            continue
        h2 = _first(li, lambda n: n.tag == "h2")
        link = _first(h2, lambda n: n.tag == "a" and bool(n.attrs.get("href"))) if h2 else None
        wrap = _first(li, lambda n: n.tag == "div" and "job-company-location-wrapper" in n.classes())
        if link is None or wrap is None:
            continue
        clink = _first(wrap, lambda n: n.tag == "a" and "/company/" in n.attrs.get("href", ""))
        loc = _first(li, lambda n: n.tag == "dt" and "jb-label-location" in n.classes())
        parts = [t for t in (_text(s) for s in _walk(loc) if s.tag == "span") if t] if loc else []
        stamp = _first(li, lambda n: "data-automation-jobactivedate" in n.attrs)
        raw = stamp.attrs["data-automation-jobactivedate"].strip() if stamp else ""
        secs = int(raw) if raw.isdecimal() else 0
        remote = _first(li, lambda n: n.tag == "dt" and "jb-label-remote" in n.classes())
        out.append(_made(
            "bayt", "Bayt.com", title=_text(link), company=_text(clink) if clink else _text(wrap),
            url=urljoin(BAYT_BASE, link.attrs["href"]), place=", ".join(parts[:-1]), country=country,
            posted=_date(secs / 1000 if secs > 10**11 else secs),
            remote=True if remote is not None and _text(remote) == "Remote" else None,
            extra={"id": li.attrs.get("data-job-id", "")}))
    return out


def _with_detail(job: Job, page: Any) -> Job:
    """Fill a card-only job from its page's JobPosting: the description, and what the card lacked."""
    p = _ld_posting(page if isinstance(page, str) else "")
    desc = strip_html(p.get("description"))
    if not desc:
        return job
    job["description"] = desc
    job["extra"].pop("title_only", None)
    job["company"] = job["company"] or _posting_company(p)
    job["posted"] = job["posted"] or _date(p.get("datePosted"))
    return job


@_feed
def fetch_bayt(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Bayt.com title search (Gulf), for the user's matching countries; the newest ads get their description."""
    markets = _wanted(countries, *BAYT_MARKETS)
    if not markets:
        return []
    budget = _Budget()
    searches = 0
    jobs: list[Job] = []
    for cc in markets:
        slug, name = BAYT_MARKETS[cc]
        for q in _queries(queries):
            for page in range(1, NATIONAL_PAGES + 1):
                if searches >= BAYT_MAX_SEARCHES or budget.left <= 0:
                    break
                searches += 1
                if searches > 1:
                    _pause()
                body = _fetch(http, stats, "bayt", budget, _bayt_search_url(slug, q, page, days),
                              as_json=False, headers=CHROME_HEADERS)
                got = parse_bayt_cards(body, name) if isinstance(body, str) else []
                jobs += got
                if len(got) < BAYT_PAGE:
                    break
    jobs = _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself
    for job in jobs[:DETAIL_CAP]:  # newest first (sorted by date when the window is set)
        if budget.left <= 0:
            break
        _pause()
        _with_detail(job, _fetch(http, stats, "bayt", budget, job["url"], as_json=False, headers=CHROME_HEADERS))
    return jobs


# ----------------------------------------------------------------------------------- GulfTalent
# Borrowed from freehire internal/ingest/sources/gulftalent.go:43 (sitemap index
# https://www.gulftalent.com/sitemap.xml), :46 and :96 (the job shards are the ones whose URL contains
# "_jx"; jl/jc/co shards are category, company and course pages), :56 (job path /jobs/<slug>-<id>,
# /<country>/jobs/<slug>-<id> in the comment at :54), :101-106 (one unreadable shard only drops its slice),
# :118-149 (detail: the page's schema.org JobPosting gives title, description, datePosted, the employer
# hiringOrganization.name (an ad with no employer is dropped) and jobLocation.address locality / country),
# :50 (4 detail workers; the Akamai edge throttles fast bursts), and the same Chrome headers as Bayt
# (fingerprinthttp.go:41, :189-205). Test shapes: gulftalent_test.go:10-47 (index, shard, detail) and :112
# (https://www.gulftalent.com/uae/jobs/maintenance-specialist-604168). NOT IN REFERENCE: any search (the
# site is read only through its sitemap), so the user's titles are matched locally against the URL's
# title slug and only the matching ads are fetched; the order of the shards and of the ids (the highest
# shard number and the highest id are ASSUMED newest); the size of a shard (the core caps a body at
# 20 MB, a larger shard reads as empty); the country slugs (only "uae" is in the reference test; "saudi-arabia",
# "qatar", "kuwait", "bahrain" and "oman" are guessed from Bayt's), so an ad under another first path
# segment is skipped; and whether addressCountry is a name or an ISO code (the fixture uses "UAE").
GT_INDEX = "https://www.gulftalent.com/sitemap.xml"
GT_SHARDS = 2  # job shards read per run (highest number first)
GT_DETAILS = 25  # detail pages per run
_GT_JOB = re.compile(r"^https?://[^/]+/([^/]+)/jobs/([^/]+)-(\d+)/?$")
GULFTALENT_MARKETS = {  # ISO -> (first path segment, country name)
    "AE": ("uae", "United Arab Emirates"),
    "SA": ("saudi-arabia", "Saudi Arabia"),
    "QA": ("qatar", "Qatar"),
    "KW": ("kuwait", "Kuwait"),
    "BH": ("bahrain", "Bahrain"),
    "OM": ("oman", "Oman"),
}


def _sitemap_locs(text: Any) -> list[str]:
    """Every <loc> of a sitemap or sitemap index (namespace-blind); [] when it is not parseable XML."""
    root = safe_fromstring(text) if isinstance(text, str) else None
    if root is None:
        return []
    return [(e.text or "").strip() for e in root.iter() if str(e.tag).rsplit("}", 1)[-1] == "loc" and e.text]


def parse_gulftalent_ad(page: Any, url: str, country: str) -> Job | None:
    """Parse one GulfTalent job page's JobPosting; None without a title or an employer."""
    p = _ld_posting(page if isinstance(page, str) else "")
    company = _posting_company(p)
    if not (p and company and str(p.get("title") or "").strip()):
        return None
    locality, ctext = _posting_place(p)
    return _made(
        "gulftalent", "GulfTalent", title=p.get("title"), company=company, url=url, place=locality,
        country=country or ctext, posted=_date(p.get("datePosted")), description=p.get("description"))


@_feed
def fetch_gulftalent(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """GulfTalent (Gulf) read through its sitemap; the user's titles match the ad URLs, then those ads are read."""
    markets = {GULFTALENT_MARKETS[c][0]: GULFTALENT_MARKETS[c][1] for c in _wanted(countries, *GULFTALENT_MARKETS)}
    qs = [q.lower().split() for q in _queries(queries)]
    if not markets or not qs:
        return []
    budget = _Budget()
    index = _fetch(http, stats, "gulftalent", budget, GT_INDEX, as_json=False, headers=CHROME_HEADERS)
    shards = sorted((u for u in _sitemap_locs(index) if "_jx" in u), reverse=True)[:GT_SHARDS]
    ads: dict[str, tuple[int, str]] = {}  # url -> (id, country name)
    for shard in shards:
        _pause()
        for loc in _sitemap_locs(_fetch(http, stats, "gulftalent", budget, shard, as_json=False,
                                        headers=CHROME_HEADERS)):
            m = _GT_JOB.match(loc)
            if not m or m.group(1) not in markets:
                continue
            words = m.group(2).lower().replace("-", " ")
            if any(all(w in words for w in q) for q in qs):
                ads[loc] = (int(m.group(3)), markets[m.group(1)])
    jobs: list[Job] = []
    for url, (_id, name) in sorted(ads.items(), key=lambda kv: kv[1][0], reverse=True)[:GT_DETAILS]:
        if budget.left <= 0:
            break
        _pause()
        job = parse_gulftalent_ad(_fetch(http, stats, "gulftalent", budget, url, as_json=False,
                                         headers=CHROME_HEADERS), url, name)
        if job:
            jobs.append(job)
    return _keep(_unique(jobs), queries, days)


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "naukri": fetch_naukri,
    "bayt": fetch_bayt,
    "gulftalent": fetch_gulftalent,
}
