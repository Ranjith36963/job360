"""National and regional job services for the Job360 hunt kit (second feed module).

Same contract as ``kit_feeds.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. Each fetcher runs ONLY
when one of its countries is in the user's ``countries`` and returns ``[]`` (with no request) otherwise.
Nothing here ranks or scores; ``queries`` is a plain keyword match, sent to the service when it can
search itself, else applied locally. No logins, no cookies. Standard library only.

Endpoints, params, headers and field names follow the MIT-licensed open-source references named above
each fetcher (see NOTICE.md); the code is original. The container this was written in could not reach
any of these hosts, so every parser is tested on fixtures shaped like the references' responses only.
These are unofficial front-end APIs: they can change or start refusing scripts without notice, in which
case the feed simply returns nothing.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode

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
_get: Callable[..., Any] = _kf._get
_keep: Callable[..., list[Job]] = _kf._keep
_queries: Callable[[list[str]], list[str]] = _kf._queries
_date: Callable[[Any], str] = _kf._date
_join_place: Callable[[str, str], str] = _kf._join_place
strip_html: Callable[[Any], str] = _kf.strip_html
NATIONAL_PAGES: int = _kf.NATIONAL_PAGES
# NATIONAL_PAGES (from kit_feeds) caps pages per query for services that search server-side.
EURES_RETRY_WAIT_S = 2  # pause before retrying a dropped EURES connection
LOCAL_PAGES = 5  # pages for the two services that cannot search (EURES, Trudvsem): matched locally
_JSON = {"Content-Type": "application/json"}


def _wanted(countries: list[str], *codes: str) -> list[str]:
    """The given ISO codes that appear in the user's countries (in the order given), or all of them for "*"."""
    have = {c.upper() for c in countries}
    if "*" in have:  # --visa-onsite: run everywhere; the core keeps only ads that sponsor a visa
        return list(codes)
    return [c for c in codes if c in have]


def _made(name: str, attribution: str, *, title: Any, company: Any, url: Any, place: str, country: str,
          posted: str = "", description: Any = "", remote: bool | None = None,
          extra: dict[str, Any] | None = None) -> Job:
    """One Job: location ends with the country name; ``title_only`` when the API gave no description."""
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


# ------------------------------------------------------------------------------------- EURES
# Borrowed from freehire internal/ingest/sources/eures.go:45 (POST search URL), :54 (resultsPerPage 50),
# :69-71 (publicationPeriod LAST_THREE_DAYS, sortSearch MOST_RECENT, requestLanguage en), :115-134 (request
# body: every filter array present, empty), :151 (locationCodes = lowercase country codes), :161-181
# (response: numberRecords, jvs[id,title,description,creationDate ms,locationMap,employer.name]),
# :52 (portal URL), :92-100 (31 country names; "el" is Greece), :212 (stop on a short page or total).
# NOT IN REFERENCE: how a keyword is sent (it always sends an empty list), so queries are matched locally
# here, and the per-posting detail fetch (city name) is skipped: the location is the country name.
EURES_SEARCH = "https://europa.eu/eures/api/jv-searchengine/public/jv-search/search"
EURES_PORTAL = "https://europa.eu/eures/portal/jv-se/jv-details/"
EURES_COUNTRIES = {
    "AT": "Austria", "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus", "CZ": "Czechia",
    "DK": "Denmark", "EE": "Estonia", "FI": "Finland", "FR": "France", "DE": "Germany", "GR": "Greece",
    "HU": "Hungary", "IS": "Iceland", "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LI": "Liechtenstein",
    "LT": "Lithuania", "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "NO": "Norway", "PL": "Poland",
    "PT": "Portugal", "RO": "Romania", "SK": "Slovakia", "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
    "CH": "Switzerland",
}


def _eures_code(iso: str) -> str:
    return "el" if iso == "GR" else iso.lower()  # EURES uses the EU language code for Greece


def parse_eures(payload: Any, fallback: str = "") -> list[Job]:
    """Parse one EURES search page (``jvs``). Description is the posting's HTML, translated when possible."""
    out: list[Job] = []
    for r in (payload or {}).get("jvs") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        names: list[str] = []
        for key in (r.get("locationMap") or {}):
            iso = "GR" if str(key).upper() == "EL" else str(key).upper()
            if iso in EURES_COUNTRIES and EURES_COUNTRIES[iso] not in names:
                names.append(EURES_COUNTRIES[iso])
        ms = r.get("creationDate")
        out.append(_made(
            "eures", "EURES (European Commission)", title=r.get("title"),
            company=(r.get("employer") or {}).get("name"), url=EURES_PORTAL + quote(str(r["id"]), safe="") + "?lang=en",
            place="", country=", ".join(names) or fallback,
            posted=_date(ms / 1000) if isinstance(ms, (int, float)) and ms > 0 else "",
            description=r.get("description")))
    return out


@_feed
def fetch_eures(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """EU/EEA/CH vacancies from EURES for the user's matching countries (one request series, matched locally)."""
    iso = _wanted(countries, *EURES_COUNTRIES)
    if not iso:
        return []
    # an ad with no locationMap is placed by the request's countries, but never when "*" spread the request
    # over every EURES country: that would make an unplaced ad look like it sits in the user's country
    fallback = "" if "*" in countries else ", ".join(EURES_COUNTRIES[c] for c in iso)
    jobs: list[Job] = []
    for q in _queries(queries) or [""]:
        jobs += _eures_series(q, iso, fallback, http, stats)
    return _keep(_unique(jobs), queries, days)


def _eures_series(q: str, iso: list[str], fallback: str, http: HttpFn, stats: Stats) -> list[Job]:
    """One EURES request series (up to LOCAL_PAGES pages) for one title, or the newest ads when q is empty."""
    # NOT IN REFERENCE (freehire sends keywords: []): with no keyword EURES serves the newest of ~343K ads, so
    # the ~250 read hold almost no title match. A TITLE keyword object narrows DE/FR/NL "machine learning
    # engineer" to 16,467 (tested live from the owner's PC, 2026-10-18); a plain string answers 400.
    keywords = [{"keyword": q, "specificSearchCode": "TITLE"}] if q else []
    jobs: list[Job] = []
    for page in range(1, LOCAL_PAGES + 1):
        body = {"resultsPerPage": 50, "page": page, "sortSearch": "MOST_RECENT", "keywords": keywords,
                "publicationPeriod": "LAST_THREE_DAYS", "occupationUris": [], "skillUris": [],
                "requiredExperienceCodes": [], "positionScheduleCodes": [], "sectorCodes": [],
                "educationAndQualificationLevelCodes": [], "positionOfferingCodes": [],
                "locationCodes": [_eures_code(c) for c in iso], "euresFlagCodes": [],
                "otherBenefitsCodes": [], "requiredLanguages": [], "sessionId": "hunt-kit",
                "requestLanguage": "en"}
        # freehire eures.go:208 posts exactly this JSON (Content-Type + Accept only, no other header,
        # sources/http.go:733-744). The status is logged, so a refusal never reads as "answered nothing".
        stats["tried"] = stats.get("tried", 0) + 1
        for attempt in range(2):  # the connection drops now and then (status 0, live 2026-10-18): one retry
            if attempt:
                time.sleep(EURES_RETRY_WAIT_S)
            status, resp = http(EURES_SEARCH, method="POST", data=json.dumps(body).encode(),
                                headers={**_JSON, "Accept": "application/json"})
            if status != 0:
                break
        if status == 200 and resp is not None:
            stats["ok"] = stats.get("ok", 0) + 1
        else:
            resp = None
            print(f"warning: eures answered HTTP {status} on page {page} (nothing read)", file=sys.stderr)
        got = parse_eures(resp, fallback)
        jobs += got
        total = int((resp or {}).get("numberRecords") or 0) if isinstance(resp, dict) else 0
        if len(got) < 50 or len(jobs) >= total:
            break
    return jobs


# -------------------------------------------------------------------------------------- VDAB
# Borrowed from career-ops providers/vdab.mjs:42 (POST vacatureLight/zoek), :48 (public header
# vej-key-monitor, a front-end constant), :157-178 (body: criteria.trefwoord + empty facet arrays,
# onlineSindsCode = days, pagina 0-based, zoekmodus C2, paginaGrootte), :126-145 (resultaten[]:
# id.id, vacaturefunctie.naam, vacatureBedrijfsnaam, tewerkstellingsLocatieRegioOfAdres,
# eerstePublicatieDatum), :53 (detail URL). Skipped: its 403 key self-heal and the detail fetch.
VDAB_SEARCH = "https://www.vdab.be/rest/vindeenjob/v4/vacatureLight/zoek"
VDAB_JOB = "https://www.vdab.be/vindeenjob/vacatures/"
# The public build-time constant VDAB's own front-end JS stamps on every request (vdab.mjs:44-48:
# "not a per-visitor session token"). Not a credential.
VDAB_FRONTEND_ID = "b277002f-e1fa-4fc5-868a-fdab633c3851"


def parse_vdab(payload: Any) -> list[Job]:
    """Parse a VDAB ``vacatureLight`` search (``resultaten``): title, employer and place only."""
    out: list[Job] = []
    for r in (payload or {}).get("resultaten") or [] if isinstance(payload, dict) else []:
        jid = ((r or {}).get("id") or {}).get("id") if isinstance(r, dict) else None
        if not jid:
            continue
        out.append(_made(
            "vdab", "VDAB", title=(r.get("vacaturefunctie") or {}).get("naam"),
            company=r.get("vacatureBedrijfsnaam"), url=VDAB_JOB + quote(str(jid), safe=""),
            place=str(r.get("tewerkstellingsLocatieRegioOfAdres") or ""), country="Belgium",
            posted=_date(r.get("eerstePublicatieDatum"))))
    return out


@_feed
def fetch_vdab(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Belgium's (Flanders) VDAB job search, only when BE is one of the user's countries."""
    if not _wanted(countries, "BE"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(NATIONAL_PAGES):
            body = {"criteria": {"trefwoord": q, "diplomaCodes": [], "arbeidsduurCodes": [],
                                 "arbeidsregimeCodes": [], "contractTypeCodes": [], "jobdomeinCodes": [],
                                 "internationaalCodes": [], "beroepCodes": [], "ervaringCodes": [],
                                 "rijbewijsCodes": [], "attestCodes": [], "taalCriteria": {"taalSelecties": []},
                                 "onlineSindsCode": str(min(days, 1000) if days and days > 0 else 1000),
                                 "sorteerVeld": "STANDAARD"},
                    "pagina": page, "zoekmodus": "C2", "paginaGrootte": 100}
            got = parse_vdab(_get(http, stats, VDAB_SEARCH, method="POST", data=json.dumps(body).encode(),
                                  headers={**_JSON, "vej-key-monitor": VDAB_FRONTEND_ID}))
            jobs += got
            if len(got) < 100:
                break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# ------------------------------------------------------------------------------------ Jobnet
# Borrowed from freehire internal/ingest/sources/jobnet.go:24 (BFF /FindJob/Search, 100 per page,
# newest first), :40 (header x-csrf: 1), :55-72 (totalJobAdCount, jobAds[jobAdId,title,hiringOrgName,
# description,postalDistrictName,publicationDate,jobAdUrl]), :102 (stop at the total); and from
# ai-job-search .agents/skills/jobnet-search/cli/src/commands/search.ts:65-85 (searchString,
# resultsPerPage, pageNumber, orderType), :93-102 (own http(s) jobAdUrl only for isExternal ads),
# :133-138 (location = postalDistrictName or municipality; url jobnet.dk/find-job/<id>).
JOBNET_SEARCH = "https://jobnet.dk/bff/FindJob/Search"
JOBNET_JOB = "https://jobnet.dk/find-job/"


def parse_jobnet(payload: Any) -> list[Job]:
    """Parse a Jobnet.dk search page (``jobAds``); the description is inline when the API gives it."""
    out: list[Job] = []
    for r in (payload or {}).get("jobAds") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not r.get("jobAdId") or not r.get("hiringOrgName"):
            continue
        own = str(r.get("jobAdUrl") or "").strip()
        url = own if r.get("isExternal") and own.lower().startswith(("http://", "https://")) \
            else JOBNET_JOB + quote(str(r["jobAdId"]), safe="")
        out.append(_made(
            "jobnet", "Jobnet (STAR, Denmark)", title=r.get("title"), company=r["hiringOrgName"], url=url,
            place=str(r.get("postalDistrictName") or r.get("municipality") or ""), country="Denmark",
            posted=_date(r.get("publicationDate")), description=r.get("description")))
    return out


@_feed
def fetch_jobnet(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Denmark's Jobnet.dk job search, only when DK is one of the user's countries."""
    if not _wanted(countries, "DK"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        seen = 0
        for page in range(1, NATIONAL_PAGES + 1):
            qs = urlencode({"searchString": q, "resultsPerPage": 100, "pageNumber": page,
                            "orderType": "PublicationDate"})
            body = _get(http, stats, f"{JOBNET_SEARCH}?{qs}", headers={"x-csrf": "1"})
            got = parse_jobnet(body)
            jobs += got
            seen += len((body or {}).get("jobAds") or []) if isinstance(body, dict) else 0
            total = int((body or {}).get("totalJobAdCount") or 0) if isinstance(body, dict) else 0
            if not got or (total and seen >= total):
                break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# ----------------------------------------------------------------------------- Tyomarkkinatori
# Borrowed from freehire internal/ingest/sources/tyomarkkinatori.go:58 (POST search URL), :136-157
# (body: query, filters.regions, paging.pageNumber 0-based + pageSize, sorting LATEST; response content[],
# lastPage), :66 (pageSize 90), :163-181 (item: id, multilingual title, employer.ownerName /
# ownerOfficeName, location.municipalities[].label + foreignCountry, publishDate), :100-118 (language
# order en, fi, sv), :62 (public page URL), :183-197 (location text + "Finland" for a domestic post).
# NOT IN REFERENCE: that an empty ``regions`` list means "all regions" (it always sends one region), and
# the body comes from a detail endpoint that rate-limits (403), so it is not fetched: title only.
TMT_SEARCH = "https://tyomarkkinatori.fi/api/jobpostingfulltext/search/v2/search"
TMT_PAGE = "https://tyomarkkinatori.fi/henkiloasiakkaat/avoimet-tyopaikat/%s/%s"


def _tmt_pick(value: Any) -> tuple[str, str]:
    """(text, language) from a {lang: text} value: English, Finnish, Swedish, then anything."""
    if isinstance(value, dict):
        for k in ("en", "fi", "sv", *value):
            if str(value.get(k) or "").strip():
                return str(value[k]).strip(), str(k)
    return "", "fi"


def parse_tyomarkkinatori(payload: Any) -> list[Job]:
    """Parse a Job Market Finland search page (``content``): title, employer and municipality only."""
    out: list[Job] = []
    for r in (payload or {}).get("content") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not r.get("id"):
            continue
        title, lang = _tmt_pick(r.get("title"))
        emp = r.get("employer") or {}
        company = _tmt_pick(emp.get("ownerName"))[0] or str(emp.get("ownerOfficeName") or "").strip()
        loc = r.get("location") or {}
        places = [_tmt_pick(m.get("label"))[0] for m in loc.get("municipalities") or [] if isinstance(m, dict)]
        domestic = not loc.get("foreignCountry")
        if not (title and company):
            continue
        out.append(_made(
            "tyomarkkinatori", "Job Market Finland (Tyomarkkinatori)", title=title, company=company,
            url=TMT_PAGE % (quote(str(r["id"]), safe=""), lang), place=", ".join(p for p in places if p),
            country="Finland" if domestic else "", posted=_date(r.get("publishDate"))))
    return out


@_feed
def fetch_tyomarkkinatori(
    queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]
) -> list[Job]:
    """Finland's Job Market Finland search, only when FI is one of the user's countries."""
    if not _wanted(countries, "FI"):
        return []
    jobs: list[Job] = []
    for q in _queries(queries):
        for page in range(NATIONAL_PAGES):
            body = {"query": q, "filters": {"regions": []}, "paging": {"pageNumber": page, "pageSize": 90},
                    "sorting": "LATEST"}
            resp = _get(http, stats, TMT_SEARCH, method="POST", data=json.dumps(body).encode(), headers=_JSON)
            got = parse_tyomarkkinatori(resp)
            jobs += got
            if not got or page >= int((resp or {}).get("lastPage") or 0):
                break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# ---------------------------------------------------------------------------------- Trudvsem
# Borrowed from freehire internal/ingest/sources/trudvsem.go:24 (GET vacancies/region/<OKATO code>),
# :25-29 (limit 100; ``offset`` is a 0-based PAGE index, page 100 errors), :40-66 (response:
# meta.total, results.vacancies[].vacancy{id, region.name, company.name, creation-date, job-name,
# vac_url, requirements, duty}), :99 (stop on a short page or at the total), :139-146 (duty and
# requirements under Russian headings). NOT IN REFERENCE: a region list or any text search, so only the
# one region code the reference's test names (Moscow, test file line 44) is read and queries match locally.
TRUDVSEM_REGION = "https://opendata.trudvsem.ru/api/v1/vacancies/region/"
TRUDVSEM_REGIONS = ("7700000000000",)  # Moscow


def parse_trudvsem(payload: Any) -> list[Job]:
    """Parse one Trudvsem region page (``results.vacancies``); duty and requirements are the description."""
    out: list[Job] = []
    rows = ((payload or {}).get("results") or {}).get("vacancies") if isinstance(payload, dict) else None
    for w in rows or []:
        v = (w or {}).get("vacancy") if isinstance(w, dict) else None
        if not isinstance(v, dict) or not v.get("id") or not str(v.get("job-name") or "").strip():
            continue
        parts = [h + ":\n" + str(v[k]).strip() for k, h in (("duty", "Обязанности"), ("requirements", "Требования"))
                 if str(v.get(k) or "").strip()]
        out.append(_made(
            "trudvsem", "Rabota Rossii (trudvsem.ru)", title=v["job-name"],
            company=(v.get("company") or {}).get("name"), url=v.get("vac_url"),
            place=str((v.get("region") or {}).get("name") or ""), country="Russia",
            posted=_date(v.get("creation-date")), description="\n\n".join(parts)))
    return out


@_feed
def fetch_trudvsem(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Russia's Rabota Rossii open data (Moscow region only), only when RU is one of the user's countries."""
    if not _wanted(countries, "RU"):
        return []
    jobs: list[Job] = []
    for region in TRUDVSEM_REGIONS:
        for page in range(LOCAL_PAGES):
            body = _get(http, stats, f"{TRUDVSEM_REGION}{region}?" + urlencode({"limit": 100, "offset": page}))
            got = parse_trudvsem(body)
            jobs += got
            total = int(((body or {}).get("meta") or {}).get("total") or 0) if isinstance(body, dict) else 0
            if len(got) < 100 or (page + 1) * 100 >= total:
                break
    return _keep(_unique(jobs), queries, days)


# ----------------------------------------------------------------------------------- Jobstreet
# Borrowed from career-ops providers/jobstreet.mjs:18-34 (v5 search at /api/jobsearch/v5/search, site
# keys per market), :45-57 (the hosts used), :64-76 (detail path /job/<id>; /id/job/<id> on the
# Indonesian hosts only), :143-176 (data[]: id, title, advertiser.description or companyName,
# locations[0].label, listingDate, teaser), :184-193 (params siteKey, keywords, pageSize, page),
# :249 (stop on a short page). NOT IN REFERENCE: Philippines and Thailand hosts, so PH and TH are skipped.
JOBSTREET_MARKETS = {  # ISO -> (origin, siteKey, country name)
    "AU": ("https://www.seek.com.au", "AU-Main", "Australia"),
    "NZ": ("https://www.seek.co.nz", "NZ-Main", "New Zealand"),
    "MY": ("https://my.jobstreet.com", "MY-Main", "Malaysia"),
    "SG": ("https://sg.jobstreet.com", "SG-Main", "Singapore"),
    "ID": ("https://id.jobstreet.com", "ID-Main", "Indonesia"),
    "HK": ("https://hk.jobsdb.com", "HK-Main", "Hong Kong"),
}


def parse_jobstreet(payload: Any, origin: str, country: str) -> list[Job]:
    """Parse a SEEK/Jobstreet/JobsDB v5 search page (``data``); the description is the short teaser."""
    out: list[Job] = []
    locale = "/id/job/" if origin == "https://id.jobstreet.com" else "/job/"
    for r in (payload or {}).get("data") or [] if isinstance(payload, dict) else []:
        if not isinstance(r, dict) or not str(r.get("id") or "").strip() or not str(r.get("title") or "").strip():
            continue
        locs = r.get("locations") or []
        place = str((locs[0] or {}).get("label") or "") if locs and isinstance(locs[0], dict) else ""
        out.append(_made(
            "jobstreet", "SEEK / Jobstreet / JobsDB", title=r["title"],
            company=(r.get("advertiser") or {}).get("description") or r.get("companyName"),
            url=f"{origin}{locale}{quote(str(r['id']).strip(), safe='')}", place=place, country=country,
            posted=_date(r.get("listingDate")), description=r.get("teaser"),
            extra={"truncated": True, "work_types": r.get("workTypes")}))
    return out


@_feed
def fetch_jobstreet(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """SEEK (AU, NZ), Jobstreet (MY, SG, ID) and JobsDB (HK) search, for the user's matching countries."""
    jobs: list[Job] = []
    for cc in _wanted(countries, *JOBSTREET_MARKETS):
        origin, site_key, name = JOBSTREET_MARKETS[cc]
        for q in _queries(queries):
            for page in range(1, NATIONAL_PAGES + 1):
                qs = urlencode({"siteKey": site_key, "keywords": q, "pageSize": 30, "page": page})
                got = parse_jobstreet(_get(http, stats, f"{origin}/api/jobsearch/v5/search?{qs}"), origin, name)
                jobs += got
                if len(got) < 30:
                    break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


# ------------------------------------------------------------------------------------------- NAV
# NOT FROM A REFERENCE REPO: read off the live service on 2026-10-19 (no key, robots.txt "User-agent: *
# Disallow:" allows it). GET /stillinger/api/search?q=<title>&size=50&from=<offset>&sort=published answers
# an Elasticsearch-shaped body: hits.total.value, hits.hits[]._source{title, businessName, employer.name,
# uuid, published, expires, locationList[]{city, municipal, county, country}, generatedSearchMetadata.
# shortSummary, properties.workLanguage[]}. Live: the server returns at most 25 rows per request whatever
# ``size`` says, so paging advances ``from`` by the rows actually received and stops at the total.
# The summary is a one-sentence AI snippet, not the ad, so ads are marked ``truncated``.
NAV_SEARCH = "https://arbeidsplassen.nav.no/stillinger/api/search"
NAV_JOB = "https://arbeidsplassen.nav.no/stillinger/stilling/"
# Live 2026-10-19: ~25 requests in a few minutes drew HTTP 429 (no Retry-After) that lasted over 100 s,
# so the reader spaces its requests out and stops at the first refusal.
NAV_PAUSE_S = 1.5  # pause between two NAV requests


def _nav_place(locations: Any) -> tuple[str, str]:
    """(places, country name) from NAV's ``locationList``: up to three distinct cities, 'NORGE' -> Norway."""
    places: list[str] = []
    country = ""
    for loc in locations if isinstance(locations, list) else []:
        if not isinstance(loc, dict):
            continue
        name = str(loc.get("city") or loc.get("municipal") or loc.get("county") or "").strip().title()
        if name and name not in places:
            places.append(name)
        country = country or str(loc.get("country") or "").strip()
    return ", ".join(places[:3]), "Norway" if country.upper() in ("", "NORGE", "NORWAY") else country.title()


def parse_nav(payload: Any) -> list[Job]:
    """Parse one NAV arbeidsplassen search page (``hits.hits``): title, employer, place and a one-line snippet."""
    rows = ((payload or {}).get("hits") or {}).get("hits") if isinstance(payload, dict) else None
    out: list[Job] = []
    for h in rows if isinstance(rows, list) else []:
        s = (h or {}).get("_source") if isinstance(h, dict) else None
        if not isinstance(s, dict):
            continue
        uid = str(s.get("uuid") or h.get("_id") or "").strip()
        title = str(s.get("title") or "").replace("\\&", "&").strip()
        if not re.fullmatch(r"[0-9a-fA-F-]{36}", uid):
            uid = ""  # an ad id is a UUID; anything else ("..") could point the link at another page
        if not (uid and title):
            continue
        place, country = _nav_place(s.get("locationList"))
        out.append(_made(
            "nav", "NAV arbeidsplassen (Norwegian Labour and Welfare Administration)", title=title,
            company=s.get("businessName") or (s.get("employer") or {}).get("name"),
            url=NAV_JOB + quote(uid, safe=""), place=place, country=country, posted=_date(s.get("published")),
            description=(s.get("generatedSearchMetadata") or {}).get("shortSummary"),
            extra={"truncated": True, "work_language": (s.get("properties") or {}).get("workLanguage") or [],
                   "expires": _date(s.get("expires"))}))
    return out


@_feed
def fetch_nav(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Norway's NAV arbeidsplassen job search (newest first), only when NO is one of the user's countries."""
    if not _wanted(countries, "NO"):
        return []
    jobs: list[Job] = []
    sent = 0
    for q in _queries(queries):
        offset = 0
        for _ in range(NATIONAL_PAGES):
            if sent:
                time.sleep(NAV_PAUSE_S)
            sent += 1
            qs = urlencode({"q": q, "size": 50, "from": offset, "sort": "published"})
            body = _get(http, stats, f"{NAV_SEARCH}?{qs}", headers={"Accept": "application/json"})
            if body is None:  # refused (429 or an outage): stop at once rather than hammer a service that said no
                print("warning: nav answered with an error (rest of NAV skipped)", file=sys.stderr)
                return _keep(_unique(jobs), queries, days, search=False)
            rows = ((body or {}).get("hits") or {}).get("hits") if isinstance(body, dict) else None
            jobs += parse_nav(body)
            offset += len(rows) if isinstance(rows, list) else 0
            total = (((body or {}).get("hits") or {}).get("total") or {}).get("value") if isinstance(body, dict) else 0
            total_n = int(total) if isinstance(total, (int, float)) else 0  # "29+" must not drop the feed
            if not rows or offset >= total_n:
                break
    return _keep(_unique(jobs), queries, days, search=False)  # the service matched the query itself


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "eures": fetch_eures,
    "vdab": fetch_vdab,
    "jobnet": fetch_jobnet,
    "tyomarkkinatori": fetch_tyomarkkinatori,
    "trudvsem": fetch_trudvsem,
    "jobstreet": fetch_jobstreet,
    "nav": fetch_nav,
}
