"""Official job APIs that need a free key, for the Job360 hunt kit.

Same contract as ``kit_feeds.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries)``
returns a list of Job dicts, never raises, and returns ``[]`` with NO request when its key is
not set. Keys come only from the environment and are never printed or logged. Nothing here
ranks or scores. Standard library only; networking goes only through the ``http`` callable.

Feeds: Reed (UK, ``REED_API_KEY``), USAJOBS (US federal, ``USAJOBS_API_KEY`` +
``USAJOBS_EMAIL``), WhatJobs (``WHATJOBS_PUBLISHER_IDS``, one publisher id per country).
"""
from __future__ import annotations

import base64
import html as _html
import os
import re
from datetime import datetime
from typing import Any, Callable
from urllib.parse import urlencode

try:  # shared helpers (same folder)
    from kit_feeds import (  # type: ignore[import-not-found]  # sibling file, not a package module
        MAX_PAGES,
        HttpFn,
        Job,
        Stats,
        _date,
        _feed,
        _get,
        _job,
        _keep,
        _queries,
    )
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_feeds", _Path(__file__).with_name("kit_feeds.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_feeds.py not found next to this file")
    _kf = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_kf)
    MAX_PAGES, HttpFn, Job, Stats = _kf.MAX_PAGES, _kf.HttpFn, _kf.Job, _kf.Stats
    _date, _feed, _get, _job, _keep, _queries = (
        _kf._date, _kf._feed, _kf._get, _kf._job, _kf._keep, _kf._queries)


def _countries(countries: list[str]) -> set[str]:
    return {c.strip().upper() for c in countries or [] if c and c.strip()}


def _first_seen(jobs: list[Job], seen: set[str]) -> list[Job]:
    """Jobs whose ``extra['id']`` has not been seen yet (marks them seen)."""
    out: list[Job] = []
    for j in jobs:
        if j["extra"]["id"] not in seen:
            seen.add(j["extra"]["id"])
            out.append(j)
    return out


# ------------------------------------------------------------------------------ Reed
# Borrowed from freehire internal/ingest/sources/reed.go:40-41 (search + detail URLs), :113-116
# (HTTP Basic, key as username, blank password), :179-180 (keywords, resultsToTake=100,
# resultsToSkip), :185-199 (stop on empty page or skip+100 >= totalResults, dedupe by jobId),
# :83-111 (fields jobId, employerName, jobTitle, locationName, datePosted/date "dd/mm/yyyy",
# externalUrl, jobUrl, jobDescription; a job needs jobId and employerName). The search list
# truncates the description and has no externalUrl; freehire fetches /jobs/{id} for those, the
# kit does not (quota), so descriptions are snippets: marked "truncated". Our own Python.
REED_URL = "https://www.reed.co.uk/api/1.0/search"
REED_PAGE = 100  # the API's maximum page size (reed.go:42)


def _dmy(value: Any) -> str:
    """Reed's 'dd/mm/yyyy' as 'YYYY-MM-DD' ('' if unknown)."""
    try:
        return datetime.strptime(str(value or "").strip(), "%d/%m/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def parse_reed(payload: Any) -> list[Job]:
    """Parse a Reed search page (``results``); rows without jobId or employerName are dropped."""
    out: list[Job] = []
    for r in (payload or {}).get("results") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not r.get("jobId") or not str(r.get("employerName") or "").strip():
            continue
        loc = str(r.get("locationName") or "")
        out.append(_job(
            title=r.get("jobTitle"), company=r.get("employerName"),
            url=r.get("externalUrl") or r.get("jobUrl"), location=loc,
            posted=_dmy(r.get("datePosted") or r.get("date")), description=r.get("jobDescription"),
            source="feed:reed", board="reed", remote=True if re.search(r"remote", loc, re.I) else None,
            extra={"id": str(r["jobId"]), "salary_min": r.get("minimumSalary"),
                   "salary_max": r.get("maximumSalary"), "currency": r.get("currency"),
                   "truncated": True, "attribution": "Reed.co.uk"}))
    return out


@_feed  # type: ignore[untyped-decorator]  # _feed comes from the sibling file (Any)
def fetch_reed(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Reed.co.uk official API. Needs env REED_API_KEY; runs only when GB is one of your countries."""
    key = os.environ.get("REED_API_KEY", "")
    if not key or "GB" not in _countries(countries):
        return []
    auth = "Basic " + base64.b64encode((key + ":").encode()).decode()
    jobs: list[Job] = []
    seen: set[str] = set()
    for q in (_queries(queries) or [""]):
        for page in range(MAX_PAGES):
            skip = page * REED_PAGE
            params: dict[str, Any] = {"resultsToTake": REED_PAGE, "resultsToSkip": skip}
            if q:
                params["keywords"] = q
            body = _get(http, stats, f"{REED_URL}?{urlencode(params)}",
                        headers={"Authorization": auth, "Accept": "application/json"})
            got = parse_reed(body)
            if not got:
                break
            jobs += _first_seen(got, seen)
            if skip + REED_PAGE >= int((body or {}).get("totalResults") or 0):
                break
    return list(_keep(jobs, queries, days, search=False))  # the service matched the query itself


# ---------------------------------------------------------------------------- USAJOBS
# Borrowed from freehire internal/ingest/sources/usajobs.go:27 (https://data.usajobs.gov/api/search),
# :88 (header Authorization-Key), :92 (ResultsPerPage=500, Page=N), :97-100 (stop on an empty
# page), :51-82 (SearchResult.SearchResultItems[].MatchedObjectId / MatchedObjectDescriptor:
# PositionTitle, PositionURI, PositionLocationDisplay, PositionLocation[].LocationName,
# OrganizationName, DepartmentName, PositionStartDate, UserArea.Details.JobSummary/MajorDuties/
# Requirements/TeleworkEligible/RemoteIndicator), :115-125 (company, first concrete location),
# :141-153 (remote / hybrid / onsite), :155-172 (description), :176-178 (drop ":443").
# NOT IN REFERENCE: the User-Agent header (the API wants the account email there, so USAJOBS_EMAIL
# is required) and the Keyword param (freehire crawls everything; the kit searches per query).
USAJOBS_URL = "https://data.usajobs.gov/api/search"
USAJOBS_PAGE = 500  # the API's maximum page size (usajobs.go:30)


def parse_usajobs(payload: Any) -> list[Job]:
    """Parse a USAJOBS search page; items without MatchedObjectId or an agency name are dropped."""
    items = (((payload or {}).get("SearchResult") or {}).get("SearchResultItems")
             if isinstance(payload, dict) else None) or []
    out: list[Job] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        d = it.get("MatchedObjectDescriptor") or {}
        det = (d.get("UserArea") or {}).get("Details") or {}
        company = d.get("OrganizationName") or d.get("DepartmentName")
        if not it.get("MatchedObjectId") or not company:
            continue
        locs = d.get("PositionLocation") or []
        place = (locs[0] or {}).get("LocationName") if locs and isinstance(locs[0], dict) else None
        remote, telework = bool(det.get("RemoteIndicator")), bool(det.get("TeleworkEligible"))
        paras = [det.get("JobSummary"), *(det.get("MajorDuties") or []), det.get("Requirements")]
        body = "".join("<p>" + _html.escape(str(p)) + "</p>" for p in paras if p and str(p).strip())
        out.append(_job(
            title=d.get("PositionTitle"), company=company,
            url=str(d.get("PositionURI") or "").replace(":443/", "/", 1),
            location=place or d.get("PositionLocationDisplay"), posted=_date(d.get("PositionStartDate")),
            description=body, source="feed:usajobs", board="usajobs", remote=True if remote else None,
            extra={"id": str(it["MatchedObjectId"]), "department": d.get("DepartmentName"),
                   "work_mode": "remote" if remote else "hybrid" if telework else "onsite",
                   "attribution": "USAJOBS (U.S. Office of Personnel Management)"}))
    return out


@_feed  # type: ignore[untyped-decorator]  # _feed comes from the sibling file (Any)
def fetch_usajobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """USAJOBS official API. Needs env USAJOBS_API_KEY + USAJOBS_EMAIL; runs only for US users."""
    key, email = os.environ.get("USAJOBS_API_KEY", ""), os.environ.get("USAJOBS_EMAIL", "").strip()
    if not key or not email or "US" not in _countries(countries):
        return []
    headers = {"Authorization-Key": key, "User-Agent": email, "Accept": "application/json"}
    jobs: list[Job] = []
    seen: set[str] = set()
    for q in (_queries(queries) or [""]):
        for page in range(1, MAX_PAGES + 1):
            params: dict[str, Any] = {"ResultsPerPage": USAJOBS_PAGE, "Page": page}
            if q:
                params["Keyword"] = q
            got = parse_usajobs(_get(http, stats, f"{USAJOBS_URL}?{urlencode(params)}", headers=headers))
            if not got:
                break
            jobs += _first_seen(got, seen)
    return list(_keep(jobs, queries, days, search=False))  # the service matched the query itself


# --------------------------------------------------------------------------- WhatJobs
# Borrowed from freehire internal/ingest/sources/whatjobs.go:33 (https://api.whatjobs.com/api/v1/
# jobs.json), :367-375 (params publisher, user_ip, keyword, limit, page), :54 (user_ip 0.0.0.0,
# a crawl is not a visitor), :37 (limit 50), :78-130 (the per-country markets and the code each
# publisher id is read under), :151-161 (WHATJOBS_PUBLISHER_IDS "code:id,code:id", a pair without
# a colon is skipped), :183-199 (data[].title/company/location/snippet/url; snippet is the FULL
# description), :201-247 (stop on an EMPTY page, never a short one; repeats collapsed), :272-308
# + :260 (the keyword ranks, it does not filter: keep only postings naming the keyword's non-generic
# terms, stop when under 50% do), :313-324 (native id from url pub_api__cpl__<id>__<publisher>,
# postings without it dropped), :329 + :347 (cut the "#J-..-Ljbffr" reseller mark), :356-361
# (location "City, Country"), :211-214 (a blank keyword is refused). The tracked url is kept
# verbatim; the feed has no posting date. freehire's market code for the UK is "uk" (GB here;
# "gb:" is accepted in the env list too).
WHATJOBS_URL = "https://api.whatjobs.com/api/v1/jobs.json"
WHATJOBS_PAGE = 50
WHATJOBS_CRAWL_IP = "0.0.0.0"  # noqa: S104 - a placeholder user_ip sent to the feed, not a bind address
WHATJOBS_MARKETS = {  # code -> country name (whatjobs.go:78-130)
    "us": "United States", "br": "Brazil", "de": "Germany", "fr": "France", "ar": "Argentina",
    "at": "Austria", "uk": "United Kingdom", "be": "Belgium", "vn": "Vietnam", "bh": "Bahrain",
    "tr": "Turkey", "cl": "Chile", "co": "Colombia", "ch": "Switzerland", "hu": "Hungary",
    "dk": "Denmark", "ae": "United Arab Emirates", "ca": "Canada", "id": "Indonesia",
    "es": "Spain", "mx": "Mexico", "ve": "Venezuela", "in": "India", "my": "Malaysia",
    "om": "Oman", "ph": "Philippines", "pk": "Pakistan", "qa": "Qatar", "sa": "Saudi Arabia",
    "sg": "Singapore", "za": "South Africa", "fi": "Finland", "hk": "Hong Kong", "ie": "Ireland",
    "it": "Italy", "kw": "Kuwait", "lu": "Luxembourg", "nl": "Netherlands", "no": "Norway",
    "nz": "New Zealand", "pe": "Peru", "pl": "Poland", "pt": "Portugal", "se": "Sweden",
    "th": "Thailand", "au": "Australia", "sv": "El Salvador", "eg": "Egypt", "gr": "Greece",
    "ke": "Kenya", "py": "Paraguay",
}
_WJ_ID = re.compile(r"pub_api__cpl__(\d+)__\d+")
_WJ_MARK = re.compile(r"\s*#J-\d+-Ljbffr\s*$")
_WJ_GENERIC = {"developer", "engineer", "programmer", "dev", "specialist", "architect"}


def parse_whatjobs_ids(raw: str) -> dict[str, str]:
    """Read WHATJOBS_PUBLISHER_IDS ("us:7065,uk:7076") into {code: id}; malformed pairs are skipped."""
    out: dict[str, str] = {}
    for pair in (raw or "").split(","):
        code, sep, pid = pair.strip().partition(":")
        if sep and code and pid:
            out[code] = pid
    return out


def parse_whatjobs(payload: Any, country: str = "") -> list[Job]:
    """Parse a WhatJobs page; postings whose url carries no native id are dropped."""
    out: list[Job] = []
    for r in (payload or {}).get("data") or [] if isinstance(payload, dict) else []:
        m = _WJ_ID.search(str(r.get("url") or "")) if isinstance(r, dict) else None
        if not isinstance(r, dict) or not m:
            continue
        city = str(r.get("location") or "").strip()
        out.append(_job(
            title=r.get("title"), company=r.get("company"), url=r.get("url"),
            location=f"{city}, {country}" if city and country else city or country,
            description=_WJ_MARK.sub("", str(r.get("snippet") or "")), source="feed:whatjobs",
            board="whatjobs", extra={"id": m.group(1), "tracked_url": True, "attribution": "WhatJobs"}))
    return out


@_feed  # type: ignore[untyped-decorator]  # _feed comes from the sibling file (Any)
def fetch_whatjobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """WhatJobs FeedAPI. Needs env WHATJOBS_PUBLISHER_IDS ("gb:123,us:456"); only countries with an id."""
    ids = parse_whatjobs_ids(os.environ.get("WHATJOBS_PUBLISHER_IDS", ""))
    if not ids:
        return []
    jobs: list[Job] = []
    for iso in sorted(_countries(countries)):
        code = "uk" if iso == "GB" else iso.lower()
        pid = ids.get(code) or (ids.get("gb") if code == "uk" else None)
        if code not in WHATJOBS_MARKETS or not pid:
            continue
        seen: set[str] = set()
        for q in _queries(queries):  # a blank keyword would return the whole feed: never sent
            terms = [w for w in q.lower().split() if w not in _WJ_GENERIC]
            for page in range(1, MAX_PAGES + 1):
                params = {"publisher": pid, "user_ip": WHATJOBS_CRAWL_IP, "keyword": q,
                          "limit": WHATJOBS_PAGE, "page": page}
                body = _get(http, stats, f"{WHATJOBS_URL}?{urlencode(params)}")
                if not isinstance(body, dict) or not (body.get("data") or []):
                    break  # an EMPTY page ends the crawl, a short one does not
                rows = parse_whatjobs(body, WHATJOBS_MARKETS[code])
                hits = [j for j in rows if all(t in (j["title"] + " " + j["description"]).lower() for t in terms)]
                jobs += _first_seen(hits, seen)
                if not rows or (terms and len(hits) * 100 < len(rows) * 50):
                    break  # relevance collapsed: the feed is padding the result set
    return list(_keep(jobs, queries, days, search=False))  # the feed matched the keyword itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "reed": fetch_reed,
    "usajobs": fetch_usajobs,
    "whatjobs": fetch_whatjobs,
}
