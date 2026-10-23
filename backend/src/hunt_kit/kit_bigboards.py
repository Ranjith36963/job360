"""LinkedIn, Glassdoor and Google Jobs for the Job360 hunt kit (fourth national-services module).

OWNER DECISION 2026-10-19 (in chat): the script reads these three sites itself, at the low volume below. This
reverses the 2026-10-14 removal of the big-board reader (PR #816) and the 2026-10-16 rule that only the assistant
reads LinkedIn. Indeed is NOT here: the only script route into Indeed needs its app's API key copied out of the
app, which the owner's rule forbids, so Indeed stays with the assistant (README "LinkedIn, Glassdoor, Google
Jobs and Indeed").

Same contract as ``kit_gov.FEEDS`` / ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days,
countries) -> list[Job]``, never raises, networking only through the ``http`` callable the core passes in. A
fetcher runs ONLY when one of the user's ``countries`` has a place name here (or "*", which the core passes for
``--visa-onsite``: then it also searches with no location, and the CORE keeps only ads that say they sponsor a
visa, exactly as for kit_gov) and returns ``[]`` with no request otherwise. Nothing here ranks or scores.

HARD LIMITS per site per run: at most ``MAX_SEARCHES`` (5) searches, each one (title, place); at most
``PAGES`` (3) result pages per search; at most ``DETAIL_CAP`` (10) detail fetches; ``PAUSE_S`` seconds between
two requests to the same site. The whole site stops at the first answer that is not HTTP 200 (a 429, a 999, a
timeout, a redirect the core cannot follow), and at the first login, CAPTCHA, "security check" or unexpected
page: nothing is retried, solved or worked around (the core's http is called with ``once=True``, so it does not
re-send a 429 or 5xx either). No login, no cookies, no proxy, no key copied from an app. Standard library only.

Every ad names its own employer; the site name is never put in ``company``. ``url`` is the public ad address
(LinkedIn /jobs/view/<id>, Glassdoor /job-listing/j?jl=<id>, Google Jobs: the apply link Google lists).

The endpoints, parameters, headers and field names follow the MIT-licensed JobSpy (commit 1988484, see
NOTICE.md) as cited above each fetcher; the code is original. The container this was written in could not reach
any of these hosts, so every parser is tested on fixtures shaped like JobSpy's parsing expectations only. These
are unofficial front-end endpoints behind bot protection: they can change or refuse a script without notice, in
which case the feed simply returns nothing. Glassdoor's edge fingerprints the TLS handshake (JobSpy sends it
through curl_cffi ``impersonate="chrome"``, util.py:77-85); ``urllib`` cannot imitate that, so Glassdoor may
answer 403 from some networks, and Google may show its consent or "unusual traffic" page: the site then stops.
"""
from __future__ import annotations

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

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
_Tree: Any = _kr._Tree  # forgiving stdlib HTML tree
_walk: Callable[[Any], Any] = _kr._walk
_first: Callable[..., Any] = _kr._first
_text: Callable[[Any], str] = _kr._text

MAX_SEARCHES = 5  # searches (one title in one place) per site per run
PAGES = 3  # result pages per search
DETAIL_CAP = 10  # ads whose full description is fetched, per site per run
PAUSE_S = 5.0  # seconds between two requests to one site (tests set it to 0)

_WS = re.compile(r"\s+")
# A login wall, CAPTCHA or bot check. Applied only to a page that lacks the content it should have (a normal
# LinkedIn page carries "/login" links), so it never fires on a real result.
_BLOCK = re.compile(
    r"authwall|/uas/login|checkpoint/challenge|captcha|security (?:check|verification)|unusual traffic|/sorry/|"
    r"consent\.google\.com|verify you are (?:a )?human|access denied|just a moment|px-captcha|"
    r"sign in to (?:continue|view)|"
    # Google's JavaScript challenge page (owner's PC, 2026-10-20: 200, 93 KB, no results markup)
    r"/httpservice/retry/enablejs|\bSG_REL\b|\bsg_trbl\b", re.I)

# ISO code -> the place name sent as the location (LinkedIn location=, Glassdoor autocomplete term, Google "near").
# NOT IN REFERENCE: JobSpy takes a free-text location; these country names are the kit's own choice.
PLACE_NAMES = {
    "AE": "United Arab Emirates", "AR": "Argentina", "AT": "Austria", "AU": "Australia", "BE": "Belgium",
    "BG": "Bulgaria", "BH": "Bahrain", "BR": "Brazil", "CA": "Canada", "CH": "Switzerland", "CL": "Chile",
    "CN": "China", "CO": "Colombia", "CZ": "Czechia", "DE": "Germany", "DK": "Denmark", "EG": "Egypt",
    "ES": "Spain", "FI": "Finland", "FR": "France", "GB": "United Kingdom", "GR": "Greece", "HK": "Hong Kong",
    "HU": "Hungary", "ID": "Indonesia", "IE": "Ireland", "IL": "Israel", "IN": "India", "IT": "Italy",
    "JP": "Japan", "KR": "South Korea", "KW": "Kuwait", "MX": "Mexico", "MY": "Malaysia", "NG": "Nigeria",
    "NL": "Netherlands", "NO": "Norway", "NZ": "New Zealand", "OM": "Oman", "PH": "Philippines",
    "PK": "Pakistan", "PL": "Poland", "PT": "Portugal", "QA": "Qatar", "RO": "Romania", "SA": "Saudi Arabia",
    "SE": "Sweden", "SG": "Singapore", "TH": "Thailand", "TR": "Turkey", "UA": "Ukraine", "US": "United States",
    "VN": "Vietnam", "ZA": "South Africa",
}


def _hours(days: int) -> int:
    return days * 24 if days and days > 0 else 0


def _slots(countries: list[str]) -> list[str | None]:
    """The user's countries that have a place name, in order, then None (no location) when "*" is present."""
    out: list[str | None] = []
    for c in countries:
        code = str(c).upper()
        if code in PLACE_NAMES and code not in out:
            out.append(code)
    if any(str(c) == "*" for c in countries):
        out.append(None)
    return out


def _searches(queries: list[str], slots: list[str | None]) -> list[tuple[str, str | None]]:
    """At most MAX_SEARCHES (title, country) pairs, spread over titles AND countries (diagonals of the grid)."""
    qs = _queries(queries)
    grid = sorted(((i + j, i, q, s) for i, q in enumerate(qs) for j, s in enumerate(slots)),
                  key=lambda t: (t[0], t[1]))
    return [(q, s) for _, _, q, s in grid[:MAX_SEARCHES]]


def _blocked(text: Any) -> bool:
    return isinstance(text, str) and bool(_BLOCK.search(text))


class _Site:
    """One site's run: counted requests, a pause between them, and the stop rule (first non-200 ends the site)."""

    def __init__(self, name: str, http: HttpFn, stats: Stats, max_requests: int = 0,
                 pause: float | None = None) -> None:
        self.name, self.http, self.stats = name, http, stats
        self.stopped = False
        self.sent = 0
        self.max_requests = max_requests  # 0 = no cap beyond the search/page/detail limits
        self.pause = pause

    def stop(self, why: str) -> None:
        if not self.stopped:
            print(f"warning: {self.name} stopped for this run: {why}", file=sys.stderr)
        self.stopped = True

    def get(self, url: str, *, headers: dict[str, str], as_json: bool = False, data: Any = None) -> Any:
        """The body on HTTP 200, else None and the whole site stops. Never retries."""
        if self.stopped:
            return None
        if self.max_requests and self.sent >= self.max_requests:
            self.stopped = True  # the site's own budget, not a refusal: no warning
            return None
        wait = PAUSE_S if self.pause is None or PAUSE_S <= 0 else max(PAUSE_S, self.pause)
        if self.sent and wait > 0:
            time.sleep(wait)
        self.sent += 1
        self.stats["tried"] = self.stats.get("tried", 0) + 1
        kw: dict[str, Any] = {"headers": headers, "as_json": as_json, "once": True}
        if data is not None:
            kw.update(method="POST", data=json.dumps(data).encode())
        status, body = self.http(url, **kw)
        if status == 200 and body is not None:
            self.stats["ok"] = self.stats.get("ok", 0) + 1
            return body
        later = " (rate limit per connection; try again tomorrow)" if status == 429 else ""
        self.stop(f"HTTP {status} (nothing read){later}")
        return None


def _remote_word(*parts: str) -> bool | None:
    text = " ".join(parts).lower()
    return True if any(k in text for k in ("remote", "work from home", "wfh")) else None


# ---------------------------------------------------------------------------------------- LinkedIn
# Mirrors JobSpy jobspy/linkedin/__init__.py:46 (base https://www.linkedin.com), :71 (the logged-out search
# /jobs-guest/jobs/api/seeMoreJobPostings/search), :116-127 (params keywords, location, distance, f_TPR =
# "r" + hours*3600, start; a None param is dropped), linkedin/constant.py:5 (10 cards a page; offsets
# 0, 10, 20), :9-13 (the three headers, copied below), __init__.py:80 (cards are class base-search-card), :92
# (id = last part of data-entity-urn), :130-148 (card: h3.base-search-card__title, h4.base-search-card__subtitle =
# the employer, span.job-search-card__location, time[datetime]; a card missing the title, employer or location
# is skipped), :164 (public URL /jobs/view/<id>), :81-88 and constant.py:7 (no cards: the exact empty page
# "<!DOCTYPE html>\n\n<!---->" is the end of the results, any other page is "unexpected page with no jobs
# (blocked?)" = stop), :107 (a page of fewer than 10 cards is the last), :213-231 (detail: GET /jobs/view/<id>,
# description = div.show-more-less-html__markup), linkedin/util.py:9-23 and __init__.py:232-248 (the criteria
# list: Seniority level, Employment type, Job function, Industries), linkedin/util.py:26-31 and constant.py:23
# (remote = "remote", "work from home" or "wfh" in the title or location). JobSpy pauses random 3-7 s between
# pages (__init__.py:47, :67); the kit pauses PAUSE_S. NOT IN REFERENCE: an "apply on company site" URL (JobSpy
# never reads one for LinkedIn; the assistant follows that button, README); how the core's http would tell
# JobSpy's redirect check (:218-221, a redirect off /jobs/view/ means a login wall) apart, because the http
# callable returns no final URL: a detail page without the description markup but with a login/CAPTCHA marker
# stops the site instead.
LI_BASE = "https://www.linkedin.com"
LI_SEARCH = LI_BASE + "/jobs-guest/jobs/api/seeMoreJobPostings/search"
LI_PER_PAGE = 10
# Owner's home PC smoke test 2026-10-20 (docs/product/hunt-kit-smoke-2026-10-20.md): LinkedIn answered 429 on the
# 10th request at 5 s apart, so a run sends at most 8, at least 8 s apart (the kit's own numbers, NOT IN REFERENCE).
LI_MAX_REQUESTS = 8
LI_PAUSE_S = 8.0
LI_EMPTY = "<!DOCTYPE html>\n\n<!---->"
LI_HEADERS = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image"
        "/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "accept-language": "en-US,en;q=0.9",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KH"
        "TML, like Gecko) Chrome/120.0.0.0 Safari/537.36"),
}
_LI_CRITERIA = {"Seniority level": "seniority", "Employment type": "employment_type",
                "Job function": "job_function", "Industries": "industries"}
_LI_CRIT_RX = re.compile(
    r"""description__job-criteria-subheader[^>]*>\s*(.*?)\s*</h3>\s*<span[^>]*description__job-criteria-text[^>]*>"""
    r"""\s*(.*?)\s*</span>""", re.S)


def _li_search_url(q: str, place: str | None, start: int, days: int) -> str:
    params: dict[str, Any] = {"keywords": q}
    if place:
        params.update(location=place, distance=50)  # 50 = JobSpy's default distance (jobspy/__init__.py:64)
    if _hours(days):
        params["f_TPR"] = f"r{_hours(days) * 3600}"
    params["start"] = start
    return f"{LI_SEARCH}?{urlencode(params)}"


def parse_linkedin_cards(page: Any) -> list[tuple[str, Job]]:
    """Parse one search page (``base-search-card`` cards) into (job id, Job); no description yet."""
    tree = _Tree()
    try:
        tree.feed(page if isinstance(page, str) else "")
        tree.close()
    except Exception:  # noqa: BLE001 - malformed markup: keep the cards read so far
        pass
    out: list[tuple[str, Job]] = []
    for card in _walk(tree.root):
        urn = card.attrs.get("data-entity-urn", "")
        if "base-search-card" not in card.classes() or not urn:
            continue
        job_id = urn.split(":")[-1].strip()
        title = _first(card, lambda n: n.tag == "h3" and "base-search-card__title" in n.classes())
        sub = _first(card, lambda n: n.tag == "h4" and "base-search-card__subtitle" in n.classes())
        loc = _first(card, lambda n: n.tag == "span" and "job-search-card__location" in n.classes())
        if not (job_id and title is not None and sub is not None and loc is not None):
            continue
        when = _first(card, lambda n: n.tag == "time")
        link = _first(sub, lambda n: n.tag == "a" and bool(n.attrs.get("href")))
        extra: dict[str, Any] = {"id": job_id}
        if link is not None:
            extra["company_url"] = link.attrs["href"].split("?")[0]
        place = _text(loc)
        out.append((job_id, _made(
            "linkedin", "LinkedIn", title=_text(title), company=_text(sub), url=f"{LI_BASE}/jobs/view/{job_id}",
            place=place, country="", posted=_date(when.attrs.get("datetime", "") if when is not None else ""),
            remote=_remote_word(_text(title), place), extra=extra)))
    return out


def _div_inner(page: str, cls: str) -> str:
    """The inner HTML of the first <div> whose class list holds ``cls`` (nested divs balanced)."""
    m = re.search(r"""<div\b[^<>]*\bclass=["'][^"']*\b""" + re.escape(cls) + r"""\b[^"']*["'][^<>]*>""", page)
    if not m:
        return ""
    depth, rest = 1, page[m.end():]
    for t in re.finditer(r"<(/?)div\b[^<>]*>", rest):
        depth += -1 if t.group(1) else 1
        if depth == 0:
            return rest[:t.start()]
    return rest


def parse_linkedin_detail(page: Any) -> tuple[str, dict[str, str]]:
    """(description text, criteria) from a /jobs/view/<id> page; ("", {}) when the page carries neither."""
    html = page if isinstance(page, str) else ""
    desc = strip_html(_div_inner(html, "show-more-less-html__markup"))
    crit: dict[str, str] = {}
    for head, val in _LI_CRIT_RX.findall(html):
        for label, key in _LI_CRITERIA.items():
            if label in strip_html(head) and strip_html(val):
                crit.setdefault(key, _WS.sub(" ", strip_html(val)))
    return desc, crit


@_feed
def fetch_linkedin(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """LinkedIn's logged-out job search, low volume, for the user's countries (and no location for "*")."""
    searches = _searches(queries, _slots(countries))
    if not searches:
        return []
    site = _Site("linkedin", http, stats, max_requests=LI_MAX_REQUESTS, pause=LI_PAUSE_S)
    seen: set[str] = set()
    jobs: list[Job] = []
    for q, iso in searches:
        for page in range(PAGES):
            body = site.get(_li_search_url(q, PLACE_NAMES.get(iso or ""), page * LI_PER_PAGE, days), headers=LI_HEADERS)
            if body is None:
                break
            cards = parse_linkedin_cards(body)
            if not cards:
                if body.strip() != LI_EMPTY:
                    site.stop("unexpected page with no jobs (blocked?)")
                break
            new = [(i, j) for i, j in cards if i not in seen]
            seen.update(i for i, _ in new)
            jobs += [j for _, j in new]
            if not new or len(cards) < LI_PER_PAGE:
                break
        if site.stopped:
            break
    jobs = _keep(_unique(jobs), queries, days, search=False)  # LinkedIn matched the keywords itself
    for job in jobs[:DETAIL_CAP]:
        if site.stopped:
            break
        page = site.get(job["url"], headers=LI_HEADERS)
        if page is None:
            break
        desc, crit = parse_linkedin_detail(page)
        if desc:
            job["description"] = desc
            job["extra"].pop("title_only", None)
            job["extra"].update(crit)
        elif _blocked(page):
            site.stop("a login or security page instead of the ad")
    return jobs


# --------------------------------------------------------------------------------------- Glassdoor
# Mirrors JobSpy jobspy/glassdoor/__init__.py:51 (base https://<domain>), glassdoor/constant.py:111-133 (the
# domain per country, copied below by ISO code), __init__.py:133-137 (location lookup GET /autocomplete/location?
# locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs&term=<place>), :146-158 (first place: locationId and
# locationType, constant.py:10 C/S/N -> CITY/STATE/COUNTRY; parameterUrlInput "IL.0,12_I<type><id>"), :139-143
# (variables keyword, numJobsToShow, filterParams), :160-177 (filters: radius when a location is set, fromAge =
# days, remoteWorkType), __init__.py:123-129 (POST <base>/graph with a one-element JSON list: operationName,
# variables, query), constant.py:26-101 (the JobSearchResultsQuery, copied below), :3 (100 ads a page), __init__.py
# :70-86 (variables pageNumber, pageCursor; answer = response.json()[0]; data.jobListings missing = an API error =
# stop; next cursor = paginationCursors[pageNumber == page + 1]), :93 and :187-209 (an ad: job.listingId,
# job.jobTitleText, header.employerNameFromSearch = the employer, header.locationName / locationType, header.ageInDays
# -> posted, map.country, job_url <base>/job-listing/j?jl=<id>), :212 (remote = WORK_FROM_HOME or location "Remote"),
# :218 (overview.website -> the employer's own site), :250-271 and constant.py:4, :103-109 (descriptions: one
# POST of aliased jobView queries, 25 ids at most per request). JobSpy sends one header, gd-csrf-token
# (constant.py:6-8), and a Chrome-impersonating client (util.py:77-85). The kit does NOT use the token value that
# JobSpy hard-codes there; it asks the site's own public page for one (below). JobSpy skips the autocomplete
# answer only when there is no location (:144); the kit sends no autocomplete request then. NOT IN REFERENCE:
# where a token is fetched from. This checkout hard-codes it, so the page (/Job/computer-science-jobs.htm) and the
# pattern ("token":"...") are the kit's GUESS from older JobSpy releases and must be checked on a real run: with
# no token found the site stops. NOT IN REFERENCE either: an "apply on company site" URL (the employer's website
# from overview.website is kept as extra.company_site, nothing more), the exact User-Agent curl_cffi's "chrome"
# impersonation sends (the Chrome string JobSpy sends to LinkedIn, linkedin/constant.py:12, is used), and the
# format of map.country (a name or a code: both are handled). JobSpy's own age filter (:183-185) is not repeated:
# the request already carries fromAge and the core filters by date.
GD_PER_PAGE = 100
GD_DETAILS_PER_REQUEST = 25
GD_HEADER = "gd-csrf-token"  # the one header JobSpy sends (glassdoor/constant.py:6-8)
GD_CSRF_PAGE = "/Job/computer-science-jobs.htm"  # NOT IN REFERENCE (guess, see above)
_GD_TOKEN_RX = re.compile(r'"token":\s*"([^"]+)"')  # NOT IN REFERENCE (guess, see above)
GD_LOCATION_TYPES = {"C": "CITY", "S": "STATE", "N": "COUNTRY"}
GD_DOMAINS = {  # ISO code -> host (glassdoor/constant.py:111-133; MY and VN use the .com site there too)
    "AR": "www.glassdoor.com.ar", "AU": "www.glassdoor.com.au", "AT": "www.glassdoor.at",
    "BE": "fr.glassdoor.be", "BR": "www.glassdoor.com.br", "CA": "www.glassdoor.ca", "FR": "www.glassdoor.fr",
    "DE": "www.glassdoor.de", "HK": "www.glassdoor.com.hk", "IN": "www.glassdoor.co.in", "IE": "www.glassdoor.ie",
    "IT": "www.glassdoor.it", "MY": "www.glassdoor.com", "MX": "www.glassdoor.com.mx", "NL": "www.glassdoor.nl",
    "NZ": "www.glassdoor.co.nz", "SG": "www.glassdoor.sg", "ES": "www.glassdoor.es", "CH": "de.glassdoor.ch",
    "GB": "www.glassdoor.co.uk", "US": "www.glassdoor.com", "VN": "www.glassdoor.com",
}
GD_DEFAULT_DOMAIN = GD_DOMAINS["US"]  # JobSpy's default country is the USA (model.py:251)
GD_SEARCH_QUERY = """
query JobSearchResultsQuery(
    $keyword: String,
    $locationId: Int,
    $locationType: LocationTypeEnum,
    $numJobsToShow: Int!,
    $pageCursor: String,
    $pageNumber: Int,
    $filterParams: [FilterParams],
    $parameterUrlInput: String
) {
    jobListings(
        contextHolder: {
            searchParams: {
                keyword: $keyword,
                locationId: $locationId,
                locationType: $locationType,
                numPerPage: $numJobsToShow,
                pageCursor: $pageCursor,
                pageNumber: $pageNumber,
                filterParams: $filterParams,
                parameterUrlInput: $parameterUrlInput,
                searchType: SR
            }
        }
    ) {
        jobListings {
            jobview {
                header {
                    adOrderSponsorshipLevel
                    ageInDays
                    employer {
                        id
                    }
                    employerNameFromSearch
                    jobTypeKeys
                    locationName
                    locationType
                    payCurrency
                    payPeriod
                    payPeriodAdjustedPay {
                        p10
                        p90
                    }
                    rating
                    remoteWorkTypes
                }
                job {
                    jobTitleText
                    listingId
                }
                map {
                    country
                }
                overview {
                    headquarters
                    overview {
                        description
                    }
                    primaryIndustry {
                        industryName
                    }
                    revenue
                    size
                    squareLogoUrl
                    website
                }
            }
        }
        paginationCursors {
            cursor
            pageNumber
        }
    }
}
"""
GD_DETAILS_ALIAS = """
    j%s: jobView(listingId: %s, contextHolder: {queryString: "q", pageTypeEnum: SERP}) {
        job {
            description
        }
    }
"""


def _gd_headers(token: str) -> dict[str, str]:
    return {GD_HEADER: token, "User-Agent": LI_HEADERS["user-agent"], "Accept": "application/json"}


def _country_name(text: Any) -> str:
    t = str(text or "").strip()
    return PLACE_NAMES.get(t.upper(), t) if len(t) == 2 else t


def parse_glassdoor_listings(payload: Any, domain: str) -> tuple[list[tuple[str, Job]], dict[int, str]]:
    """One /graph answer -> ((listing id, Job) ads, {page number: cursor}). ({}, {}) when it has no jobListings."""
    try:
        result = payload[0]["data"]["jobListings"]
    except (KeyError, IndexError, TypeError):
        return [], {}
    if not isinstance(result, dict):
        return [], {}
    cursors = {int(c["pageNumber"]): str(c["cursor"]) for c in result.get("paginationCursors") or []
               if isinstance(c, dict) and c.get("cursor") and isinstance(c.get("pageNumber"), int)}
    out: list[tuple[str, Job]] = []
    for entry in result.get("jobListings") or []:
        view = entry.get("jobview") if isinstance(entry, dict) else None
        if not isinstance(view, dict):
            continue
        head, job = view.get("header") or {}, view.get("job") or {}
        jid = str(job.get("listingId") or "").strip()
        company = str(head.get("employerNameFromSearch") or "").strip()
        title = str(job.get("jobTitleText") or "").strip()
        if not (jid and title and company):  # an ad with no employer is not used
            continue
        age = head.get("ageInDays")
        posted = (datetime.now(timezone.utc).date() - timedelta(days=age)).isoformat() if isinstance(age, int) else ""
        name = str(head.get("locationName") or "").strip()
        site = str((view.get("overview") or {}).get("website") or "").strip()
        extra: dict[str, Any] = {"id": jid}
        if site:
            extra["company_site"] = site if site.startswith("http") else "https://" + site
        remote = "WORK_FROM_HOME" in (head.get("remoteWorkTypes") or []) or name == "Remote"
        out.append((jid, _made(
            "glassdoor", "Glassdoor", title=title, company=company, url=f"https://{domain}/job-listing/j?jl={jid}",
            place=name, country=_country_name((view.get("map") or {}).get("country")), posted=posted,
            remote=True if remote else None, extra=extra)))
    return out, cursors


def _gd_token(site: _Site, domain: str, tokens: dict[str, str]) -> str:
    """The page's own CSRF token for this host ("" and the site stops when the page carries none)."""
    if domain not in tokens:
        page = site.get(f"https://{domain}{GD_CSRF_PAGE}", headers={
            "User-Agent": LI_HEADERS["user-agent"], "Accept": LI_HEADERS["accept"],
            "Accept-Language": LI_HEADERS["accept-language"]})
        m = _GD_TOKEN_RX.search(page) if isinstance(page, str) else None
        if m:
            tokens[domain] = m.group(1)
        elif page is not None:
            site.stop("no token on the public page" + (" (a security page)" if _blocked(page) else ""))
    return tokens.get(domain, "")


def _gd_variables(site: _Site, domain: str, token: str, q: str, place: str | None, days: int) -> dict[str, Any] | None:
    """The search variables, or None (a location Glassdoor does not know skips this search; a refusal stops)."""
    filters: dict[str, Any] = {}
    if place:
        filters["radius"] = 50
    if days and days > 0:
        filters["fromAge"] = days
    variables: dict[str, Any] = {"keyword": q, "numJobsToShow": GD_PER_PAGE,
                                 "filterParams": [{"filterKey": k, "values": str(v)} for k, v in filters.items()]}
    if not place:
        return variables
    places = site.get(f"https://{domain}/autocomplete/location?locationTypeFilters=CITY,STATE,COUNTRY&caller=jobs"
                      f"&term={quote(place)}", headers=_gd_headers(token), as_json=True)
    if not isinstance(places, list) or not places or not isinstance(places[0], dict):
        if places is not None:
            print(f"warning: glassdoor does not know the place '{place}' (search skipped)", file=sys.stderr)
        return None
    loc_id, loc_type = places[0].get("locationId"), GD_LOCATION_TYPES.get(str(places[0].get("locationType")))
    if not isinstance(loc_id, int) or not loc_type:
        return None
    return {**variables, "locationId": loc_id, "locationType": loc_type,
            "parameterUrlInput": f"IL.0,12_I{loc_type}{loc_id}"}


@_feed
def fetch_glassdoor(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Glassdoor's job search (its own GraphQL endpoint), low volume, for the user's countries Glassdoor has."""
    searches = [(q, iso) for q, iso in _searches(queries, _slots(countries)) if iso is None or iso in GD_DOMAINS]
    if not searches:
        return []
    site = _Site("glassdoor", http, stats)
    tokens: dict[str, str] = {}
    seen: set[str] = set()
    found: list[tuple[str, str, Job]] = []  # (domain, listing id, job)
    for q, iso in searches:
        domain = GD_DOMAINS[iso] if iso else GD_DEFAULT_DOMAIN
        token = _gd_token(site, domain, tokens)
        variables = _gd_variables(site, domain, token, q, PLACE_NAMES.get(iso or ""), days) if token else None
        if variables is None:
            if site.stopped:
                break
            continue
        cursor: str | None = None
        for page in range(1, PAGES + 1):
            body = site.get(f"https://{domain}/graph", headers=_gd_headers(token), as_json=True, data=[{
                "operationName": "JobSearchResultsQuery", "query": GD_SEARCH_QUERY,
                "variables": {**variables, "pageNumber": page, "pageCursor": cursor}}])
            if body is None:
                break
            ads, cursors = parse_glassdoor_listings(body, domain)
            try:
                has_data = bool(body[0]["data"]["jobListings"])
            except (KeyError, IndexError, TypeError):
                has_data = False
            if not has_data:
                site.stop("an API error instead of listings")
                break
            new = [(i, j) for i, j in ads if i not in seen]
            seen.update(i for i, _ in new)
            found += [(domain, i, j) for i, j in new]
            cursor = cursors.get(page + 1)
            if not cursor or not new:
                break
        if site.stopped:
            break
    keep = {id(j) for j in _keep([j for _, _, j in found], queries, days, search=False)}
    found = [t for t in found if id(t[2]) in keep]
    by_domain: dict[str, list[tuple[str, Job]]] = {}
    for domain, jid, job in found[:DETAIL_CAP]:
        by_domain.setdefault(domain, []).append((jid, job))
    for domain, rows in by_domain.items():
        if site.stopped:
            break
        for start in range(0, len(rows), GD_DETAILS_PER_REQUEST):
            batch = rows[start:start + GD_DETAILS_PER_REQUEST]
            aliases = "".join(GD_DETAILS_ALIAS % (jid, jid) for jid, _ in batch)
            body = site.get(f"https://{domain}/graph", headers=_gd_headers(tokens[domain]), as_json=True, data=[{
                "operationName": "JobDetails", "variables": {}, "query": f"query JobDetails {{{aliases}}}"}])
            try:
                views = body[0]["data"] if body is not None else {}
            except (KeyError, IndexError, TypeError):
                views = {}
            for jid, job in batch:
                view = views.get("j" + jid) if isinstance(views, dict) else None
                desc = strip_html(((view or {}).get("job") or {}).get("description")) if isinstance(view, dict) else ""
                if desc:
                    job["description"] = desc
                    job["extra"].pop("title_only", None)
    return [j for _, _, j in found]


# ------------------------------------------------------------------------------------- Google Jobs
# Mirrors JobSpy jobspy/google/__init__.py:27-28 (search page https://www.google.com/search, next pages
# https://www.google.com/async/callback:550), :75 (query "<title> jobs"), :97-98 (" near <place>"), :77-85 and
# :100-102 (a time phrase from the age in hours: <=24 "since yesterday", <=72 "in the last 3 days", <=168 "in
# the last week", else "in the last month"), :110-111 (GET with q and udm=8 and the headers_initial set),
# :116-118 (the cursor of page 2: the data-async-fc attribute of <div jsname="Yust4d">), google/util.py:24-33 (the
# ads of page 1 sit in the page as JSON after '520084652":' and before '}]]]]]'), __init__.py:136-141 (next page:
# GET callback:550 with fc=<cursor>, fcv=3, async=<the async blob, copied below> and the headers_jobs set),
# :147-172 (the answer: the JSON between the first "[[[" and the last "]]]"; its [0] is a list of [_, text] pairs;
# a text starting "[[[" is JSON whose dict key "520084652" holds the ad, google/util.py:6-21; the next cursor is the
# first data-async-fc="..." of the answer), :174-212 (an ad is a list: [0] title, [1] employer, [2] place, [3][0][0]
# the apply link, [12] "N days ago", [19] the description, [28] the id; remote = "remote" or "wfh" in the
# description; a link seen already is dropped), :52-71 (stop when a page has no ads or no cursor), constant.py:1-27
# and :29-50 (the two header sets, copied below). The description comes with the ad, so there is no detail fetch.
# JobSpy reads "N days ago" as N days even for "5 hours ago" (:191-194); the kit counts hours and minutes as today.
# NOT IN REFERENCE: how a Google consent or "unusual traffic" page looks (JobSpy fails on "no job data returned",
# :129-130); the kit stops the site when such a page carries a block/consent marker and ends just that search
# when it is a plain empty result.
GOOGLE_SEARCH = "https://www.google.com/search"
GOOGLE_CALLBACK = "https://www.google.com/async/callback:550"
G_HEADERS_INITIAL = {
    "accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image"
        "/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"),
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=0, i",
    "referer": "https://www.google.com/",
    "sec-ch-prefers-color-scheme": "dark",
    "sec-ch-ua": "\"Chromium\";v=\"130\", \"Google Chrome\";v=\"130\", \"Not?A_Brand\";v=\"99\"",
    "sec-ch-ua-arch": "\"arm\"",
    "sec-ch-ua-bitness": "\"64\"",
    "sec-ch-ua-form-factors": "\"Desktop\"",
    "sec-ch-ua-full-version": "\"130.0.6723.58\"",
    "sec-ch-ua-full-version-list": (
        "\"Chromium\";v=\"130.0.6723.58\", \"Google Chrome\";v=\"130.0.6723.58\", \"Not?"
        "A_Brand\";v=\"99.0.0.0\""),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": "\"\"",
    "sec-ch-ua-platform": "\"macOS\"",
    "sec-ch-ua-platform-version": "\"15.0.1\"",
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "document",
    "sec-fetch-mode": "navigate",
    "sec-fetch-site": "same-origin",
    "sec-fetch-user": "?1",
    "upgrade-insecure-requests": "1",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KH"
        "TML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
    "x-browser-channel": "stable",
    "x-browser-copyright": "Copyright 2024 Google LLC. All rights reserved.",
    "x-browser-year": "2024",
}
G_HEADERS_JOBS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "priority": "u=1, i",
    "referer": "https://www.google.com/",
    "sec-ch-prefers-color-scheme": "dark",
    "sec-ch-ua": "\"Chromium\";v=\"130\", \"Google Chrome\";v=\"130\", \"Not?A_Brand\";v=\"99\"",
    "sec-ch-ua-arch": "\"arm\"",
    "sec-ch-ua-bitness": "\"64\"",
    "sec-ch-ua-form-factors": "\"Desktop\"",
    "sec-ch-ua-full-version": "\"130.0.6723.58\"",
    "sec-ch-ua-full-version-list": (
        "\"Chromium\";v=\"130.0.6723.58\", \"Google Chrome\";v=\"130.0.6723.58\", \"Not?"
        "A_Brand\";v=\"99.0.0.0\""),
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-model": "\"\"",
    "sec-ch-ua-platform": "\"macOS\"",
    "sec-ch-ua-platform-version": "\"15.0.1\"",
    "sec-ch-ua-wow64": "?0",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin",
    "user-agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KH"
        "TML, like Gecko) Chrome/130.0.0.0 Safari/537.36"),
}
G_ASYNC = (
    "_basejs:/xjs/_/js/k=xjs.s.en_US.JwveA-JiKmg.2018.O/am=AAAAAAAAAAAAAAAA"
    "AAAAAAAAAAAAAAAAAAAAAAAAAAAAIAAAAAAAAACAAAoICAAAAAAAKMAfAAAAIAQAAAAAAA"
    "AAAAAACCAAAEJDAAACAAAAAGABAIAAARBAAABAAAAAgAgQAABAASKAfv8JAAABAAAAAAwA"
    "QAQACQAAAAAAcAEAQABoCAAAABAAAIABAACAAAAEAAAAFAAAAAAAAAAAAAAAAAAAAAAAAA"
    "CAQADoBwAAAAAAAAAAAAAQBAAAAATQAAoACOAHAAAAAAAAAQAAAIIAAAA_ZAACAAAAAAAA"
    "cB8APB4wHFJ4AAAAAAAAAAAAAAAACECCYA5If0EACAAAAAAAAAAAAAAAAAAAUgRNXG4AMA"
    "E/dg=0/br=1/rs=ACT90oGxMeaFMCopIHq5tuQM-6_3M_VMjQ,_basecss:/xjs/_/ss/k"
    "=xjs.s.IwsGu62EDtU.L.B1.O/am=QOoQIAQAAAQAREADEBAAAAAAAAAAAAAAAAAAAAAgA"
    "QAAIAAAgAQAAAIAIAIAoEwCAADIC8AfsgEAawwAPkAAjgoAGAAAAAAAAEADAAAAAAIgAEC"
    "HAAAAAAAAAAABAQAggAARQAAAQCEAAAAAIAAAABgAAAAAIAQIACCAAfB-AAFIQABoCEA_C"
    "gEAAIABAACEgHAEwwAEFQAM4CgAAAAAAAAAAAAACABCAAAAQEAAABAgAMCPAAA4AoE2BAE"
    "AggSAAIoAQAAAAAgAAAAACCAQAAAxEwA_ZAACAAAAAAAAAAkAAAAAAAAgAAAAAAAAAAAAA"
    "AAAAAAAAAAAQAEAAAAAAAAAAAAAAAAAAAAAQA/br=1/rs=ACT90oGZc36t3uUQkj0srnIv"
    "vbHjO2hgyg,_basecomb:/xjs/_/js/k=xjs.s.en_US.JwveA-JiKmg.2018.O/ck=xjs"
    ".s.IwsGu62EDtU.L.B1.O/am=QOoQIAQAAAQAREADEBAAAAAAAAAAAAAAAAAAAAAgAQAAI"
    "AAAgAQAAAKAIAoIqEwCAADIK8AfsgEAawwAPkAAjgoAGAAACCAAAEJDAAACAAIgAGCHAIA"
    "AARBAAABBAQAggAgRQABAQSOAfv8JIAABABgAAAwAYAQICSCAAfB-cAFIQABoCEA_ChEAA"
    "IABAACEgHAEwwAEFQAM4CgAAAAAAAAAAAAACABCAACAQEDoBxAgAMCPAAA4AoE2BAEAggT"
    "QAIoASOAHAAgAAAAACSAQAIIxEwA_ZAACAAAAAAAAcB8APB4wHFJ4AAAAAAAAAAAAAAAAC"
    "ECCYA5If0EACAAAAAAAAAAAAAAAAAAAUgRNXG4AMAE/d=1/ed=1/dg=0/br=1/ujg=1/rs"
    "=ACT90oFNLTjPzD_OAqhhtXwe2pg1T3WpBg,_fmt:prog,_id:fc_5FwaZ86OKsfdwN4P4"
    "La3yA4_2")
_G_FC_FIRST = re.compile(r'<div jsname="Yust4d"[^>]+data-async-fc="([^"]+)"')
_G_FC_ANY = re.compile(r'data-async-fc="([^"]+)"')
_G_INITIAL = re.compile('520084652":(' + r"\[.*?\]\s*])\s*}\s*]\s*]\s*]\s*]\s*]")
_G_INITIAL_END = re.compile(r"\]\s*]\s*}\s*]\s*]\s*]\s*]\s*]")  # where every _G_INITIAL match ends
_G_KEY = "520084652"


def _g_time_phrase(hours: int) -> str:
    if hours <= 24:
        return "since yesterday"
    if hours <= 72:
        return "in the last 3 days"
    if hours <= 168:
        return "in the last week"
    return "in the last month"


def _g_find_info(data: Any) -> list[Any] | None:
    """The first list stored under the key 520084652 anywhere in nested dicts/lists (google/util.py:6-21)."""
    if isinstance(data, dict):
        for key, value in data.items():
            if key == _G_KEY and isinstance(value, list):
                return value
            found = _g_find_info(value)
            if found:
                return found
    elif isinstance(data, list):
        for item in data:
            found = _g_find_info(item)
            if found:
                return found
    return None


def parse_google_ad(info: Any) -> Job | None:
    """One Google Jobs ad list -> Job; None without a title, an employer or an apply link."""
    try:
        url = info[3][0][0] if info[3] and info[3][0] else None
        title, company, place = info[0], info[1], info[2]
        ago, desc, gid = info[12], info[19], info[28]
    except (IndexError, KeyError, TypeError):
        return None
    if not (isinstance(url, str) and url.startswith("http") and isinstance(title, str) and title.strip()
            and isinstance(company, str) and company.strip()):
        return None
    posted = ""
    if isinstance(ago, str):
        m = re.search(r"\d+", ago)
        if m:
            back = 0 if re.search(r"hour|minute", ago, re.I) else int(m.group())
            posted = (datetime.now(timezone.utc) - timedelta(days=back)).strftime("%Y-%m-%d")
    text = desc if isinstance(desc, str) else ""
    return _made("googlejobs", "Google Jobs", title=title, company=company, url=url,
                 place=place if isinstance(place, str) else "", country="", posted=posted, description=text,
                 remote=True if re.search(r"remote|wfh", text, re.I) else None, extra={"id": str(gid or "")})


def parse_google_first_page(page: Any) -> tuple[list[Job], str | None]:
    """Page 1 (the search page): its ads and the cursor of page 2."""
    html = page if isinstance(page, str) else ""
    m = _G_FC_FIRST.search(html)
    out: list[Job] = []
    for hit in _kf.finditer_closed(_G_INITIAL, _G_INITIAL_END, html):
        try:
            job = parse_google_ad(json.loads(hit.group(1)))
        except ValueError:
            continue
        if job:
            out.append(job)
    return out, (m.group(1) if m else None)


def parse_google_next_page(text: Any) -> tuple[list[Job], str | None]:
    """A later page (the callback answer): its ads and the cursor of the next page."""
    body = text if isinstance(text, str) else ""
    try:
        parsed = json.loads(body[body.index("[[["):body.rindex("]]]") + 3])[0]
    except (ValueError, IndexError, TypeError):
        return [], None
    m = _G_FC_ANY.search(body)
    out: list[Job] = []
    for pair in parsed if isinstance(parsed, list) else []:
        try:
            raw = pair[1]
            if not (isinstance(raw, str) and raw.startswith("[[[")):
                continue
            job = parse_google_ad(_g_find_info(json.loads(raw)))
        except (ValueError, IndexError, TypeError):
            continue
        if job:
            out.append(job)
    return out, (m.group(1) if m else None)


@_feed
def fetch_googlejobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Google's job listings (the udm=8 search), low volume, for the user's countries (no "near" for "*")."""
    searches = _searches(queries, _slots(countries))
    if not searches:
        return []
    site = _Site("googlejobs", http, stats)
    seen: set[str] = set()
    jobs: list[Job] = []
    for q, iso in searches:
        query = f"{q} jobs"
        if iso:
            query += f" near {PLACE_NAMES[iso]}"
        if _hours(days):
            query += " " + _g_time_phrase(_hours(days))
        body = site.get(f"{GOOGLE_SEARCH}?{urlencode({'q': query, 'udm': '8'})}", headers=G_HEADERS_INITIAL)
        if body is None:
            break
        got, cursor = parse_google_first_page(body)
        if not got and not cursor:
            if _blocked(body):
                site.stop("a consent, security or JavaScript-challenge page instead of results")
                break
            continue  # a plain empty result: this search only
        page = 1
        while True:
            for job in got:
                if job["url"] not in seen:
                    seen.add(job["url"])
                    jobs.append(job)
            page += 1
            if not cursor or page > PAGES:
                break
            qs = urlencode({"fc": [cursor], "fcv": ["3"], "async": [G_ASYNC]}, doseq=True)
            nxt = site.get(f"{GOOGLE_CALLBACK}?{qs}", headers=G_HEADERS_JOBS)
            if nxt is None:
                break
            got, cursor = parse_google_next_page(nxt)
            if not got:
                break
        if site.stopped:
            break
    return _keep(jobs, queries, days, search=False)  # Google matched the query itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "linkedin": fetch_linkedin,
    "glassdoor": fetch_glassdoor,
    "googlejobs": fetch_googlejobs,
}
