"""More ATS board fetchers for the hunt kit: Rippling, Breezy, Pinpoint, Phenom, UKG (UltiPro), ADP Workforce Now,
Cornerstone, Gem, JazzHR, Softgarden, JOIN, Jibe, Keka, Zoho Recruit, RippleHire. Same contract as kit_ats.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only
through the ``http`` callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  rippling   slug from ats.rippling.com/<slug>/jobs
  breezy     tenant from <tenant>.breezy.hr
  pinpoint   tenant from <tenant>.pinpointhq.com
  phenom     the company's branded career host, e.g. careers.allianz.com (row may add url_prefix, lang, country)
  ultipro    host/tenant/boardId from https://<host>/<tenant>/JobBoard/<boardId>/, e.g.
             recruiting.ultipro.ca/YMC5001YMGT/7fde8f3e-7920-4d9d-8394-87bd44cc2330
  adp        cid/ccId from workforcenow.adp.com/.../recruitment.html?cid=<cid>&ccId=<ccId>
  cornerstone  <tenant>/<siteId> from <tenant>.csod.com/ux/ats/careersite/<siteId>/home (row may add corp)
  gem        board id from jobs.gem.com/<id>
  paylocity  company GUID from recruiting.paylocity.com/Recruiting/Jobs/All/<guid>
  keka       tenant from <tenant>.keka.com/careers (India)
  zohorecruit  tenant from <tenant>.zohorecruit.in or .com/jobs/Careers (row may add tld "in" or "com")
  ripplehire tenant/token from <tenant>.ripplehire.com/candidate/?token=<20-char career-site token> (India)

Endpoints and field names are adapted from the MIT-licensed career-ops providers and freehire adapters (see NOTICE.md);
the Keka, Zoho Recruit and RippleHire readers come from our own read of their public career pages.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import unicodedata
from datetime import date, datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
        _iso_date,
        _job,
        _jobvite_date,
        _join,
        _remote_from,
        _text,
        _title_ok,
        drop_blocks,
        finditer_closed,
    )
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_ats", _Path(__file__).with_name("kit_ats.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_ats.py not found next to this file")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    MAX_PAGES, Http, Job = _mod.MAX_PAGES, _mod.Http, _mod.Job
    _count, _get, _iso_date, _job, _jobvite_date = _mod._count, _mod._get, _mod._iso_date, _mod._job, _mod._jobvite_date
    _join, _remote_from, _text, _title_ok = _mod._join, _mod._remote_from, _mod._text, _mod._title_ok
    drop_blocks, finditer_closed = _mod.drop_blocks, _mod.finditer_closed

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_WORD = re.compile(r"\W+")


def _label(s: str) -> bool:
    """A DNS-style label: letters, digits, hyphens; not starting or ending with a hyphen."""
    return bool(re.fullmatch(r"[A-Za-z0-9-]{1,80}", s)) and s[0] != "-" and s[-1] != "-"


def _https(url: Any, host: str | None = None) -> str:
    """The URL when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p.geturl() if ok else ""


def _commas(*parts: Any) -> str:
    """Comma-join the non-empty parts as they are (the references do not drop repeats such as 'Berlin, Berlin')."""
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _pad(s: str) -> str:
    return " {} ".format(_WORD.sub(" ", s.lower()).strip())


def _add_country(name: str, country: Any) -> str:
    """Append the country when the location name does not already contain it as a whole word."""
    c = str(country or "").strip()
    return f"{name}, {c}" if c and _pad(c) not in _pad(name) else name


def _wrap(body: Callable[[dict[str, Any], Http], list[Job] | None]) -> Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]:
    """Turn ``body(row, http) -> jobs | None`` (None = the board failed) into a never-raising fetcher that counts."""
    def fetch(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
        try:
            jobs = body(row, http)
        except Exception:  # noqa: BLE001 - fetchers must never raise
            jobs = None
        _count(stats, ok=jobs is not None, jobs=len(jobs or []))
        return jobs or []
    fetch.__doc__ = body.__doc__
    return fetch


# --------------------------------------------------------------------------- Rippling
# Borrowed from career-ops providers/rippling.mjs:9 (GET https://ats.rippling.com/api/v2/board/<slug>/jobs?page=<n>&pageSize=1000),
# :27 (PAGE_SIZE 1000, stop on a short page :216), :126-162 (items[]: name, url host-locked to ats.rippling.com, locations[].name).
def _rippling(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Rippling public v2 board API (0-indexed pages of up to 1000)."""
    slug = str(row.get("token") or "")
    if not _label(slug):
        return None
    jobs: list[Job] = []
    for page in range(MAX_PAGES):
        st, data = _get(http, f"https://ats.rippling.com/api/v2/board/{quote(slug, safe='')}/jobs?page={page}&pageSize=1000", headers=_JSON)
        items = data.get("items") if isinstance(data, dict) else None
        if st != 200 or not isinstance(items, list):
            if page:
                break  # a hiccup after page 0 keeps what we have
            return None
        for j in items:
            title, url = str((j or {}).get("name") or "").strip(), _https((j or {}).get("url"), "ats.rippling.com")
            if not title or not url:
                continue
            loc = " · ".join(str(x.get("name") or "").strip() for x in (j.get("locations") or []) if isinstance(x, dict) and x.get("name"))
            dept = j.get("department")
            jobs.append(_job(row, "rippling", title=title, url=url, location=loc, remote=_remote_from(loc),
                             extra={"department": str(dept.get("name") or "") if isinstance(dept, dict) else ""}))
        if len(items) < 1000:
            break
    return jobs


# --------------------------------------------------------------------------- Breezy
# Borrowed from career-ops providers/breezy.mjs:61,67 (GET https://<tenant>.breezy.hr/json, one top-level array) and
# :123-164 (name, absolute https url, published_date; location.name + country.name, else city/state/country, + is_remote).
def _breezy(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Breezy HR public board feed (<tenant>.breezy.hr/json)."""
    tenant = str(row.get("token") or "").lower()
    if not _label(tenant):
        return None
    st, data = _get(http, f"https://{tenant}.breezy.hr/json", headers=_JSON)
    if st != 200 or not isinstance(data, list):
        return None
    jobs: list[Job] = []
    for j in data:
        url = _https((j or {}).get("url")) if isinstance(j, dict) else ""
        if not url or not j.get("name"):
            continue
        loc = j.get("location") if isinstance(j.get("location"), dict) else {}
        country = (loc.get("country") or {}).get("name") if isinstance(loc.get("country"), dict) else ""
        name = str(loc.get("name") or "").strip()
        place = _add_country(name, country) if name else _commas(loc.get("city"), loc.get("state"), country)
        if loc.get("is_remote") and "remote" not in place.lower():
            place = _commas(place, "Remote")
        jobs.append(_job(row, "breezy", title=str(j["name"]), url=url, location=place,
                         posted=_iso_date(j.get("published_date")), remote=True if loc.get("is_remote") else _remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- Pinpoint
# Borrowed from career-ops providers/pinpoint.mjs:50 (GET https://<slug>.pinpointhq.com/postings.json -> {data: [...]}),
# :89-94 (drop Pinpoint's seeded demo posting), :124-157 (title, https url, location.name else city/province); the
# description and workplace_type fields are from freehire internal/ingest/sources/pinpoint.go:29-60.
_PINPOINT_DEMO = "youtube.com/embed/https://www.youtube.com/embed/pFxm6fszrpw"


def _pinpoint(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Pinpoint public postings.json feed."""
    slug = str(row.get("token") or "").lower()
    if not _label(slug):
        return None
    st, data = _get(http, f"https://{slug}.pinpointhq.com/postings.json", headers=_JSON)
    rows = data.get("data") if isinstance(data, dict) else None
    if st != 200 or not isinstance(rows, list):
        return None
    jobs: list[Job] = []
    for j in rows:
        if not isinstance(j, dict) or _PINPOINT_DEMO in str(j.get("description") or ""):
            continue
        title, url = str(j.get("title") or "").strip(), _https(j.get("url"))
        if not title or not url:
            continue
        loc: dict[str, Any] = j["location"] if isinstance(j.get("location"), dict) else {}
        place = str(loc.get("name") or "").strip() or _commas(loc.get("city"), loc.get("province"))
        wt = str(j.get("workplace_type") or "")
        jobs.append(_job(row, "pinpoint", title=title, url=url, location=place, description=_text(j.get("description")),
                         remote=True if wt == "remote" else _remote_from(place), extra={"workplace_type": wt}))
    return jobs


# --------------------------------------------------------------------------- Phenom
# Borrowed from career-ops providers/phenom.mjs:17-25 (POST https://<branded host>/widgets, ddoKey refineSearch),
# :197-224 (request body, size 100, from = page*100), :131-154 (jobId, title, location, postedDate; job URL
# <origin>/<urlPrefix>/job/<jobId>/<slug>), :246-258 (stop on empty page, no new ids, or totalHits reached).
PHENOM_PAGE = 100


def _slugify(title: str) -> str:
    """Phenom's cosmetic job-page slug: ASCII alphanumerics, other runs become one hyphen."""
    s = "".join(c for c in unicodedata.normalize("NFKD", title) if not unicodedata.combining(c))
    return re.sub(r"[^a-zA-Z0-9]+", "-", s).strip("-") or "job"


def _phenom(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Phenom People CareerConnect widget API on the company's branded host."""
    host = str(row.get("token") or "").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}", host):
        return None
    prefix = str(row.get("url_prefix") or "global/en").strip("/")
    country = str(row.get("country") or "global")
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(MAX_PAGES):
        body = {"lang": str(row.get("lang") or "en_global"), "deviceType": "desktop", "country": country,
                "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
                "from": page * PHENOM_PAGE, "jobs": True, "counts": True, "all_fields": ["category", "country", "city"],
                "size": PHENOM_PAGE, "clearAll": False, "jdsource": "facets", "isSliderEnable": False, "pageId": "page10",
                "siteType": "external", "keywords": "", "global": country == "global", "selected_fields": {}, "locationData": {}}
        st, data = _get(http, f"https://{host}/widgets", method="POST", data=json.dumps(body).encode(), headers=_POST)
        rs = data.get("refineSearch") if isinstance(data, dict) else None
        if st != 200 or not isinstance(rs, dict):
            if page:
                break
            return None
        total = rs.get("totalHits") if isinstance(rs.get("totalHits"), int) else None
        listing = ((rs.get("data") or {}).get("jobs")) if isinstance(rs.get("data"), dict) else None
        listing = listing if isinstance(listing, list) else []
        fresh = 0
        for j in listing:
            jid = str((j or {}).get("jobId") or "") if isinstance(j, dict) else ""
            title = _text(j.get("title")) if jid else ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            loc = _text(j.get("location") or j.get("cityStateCountry") or j.get("cityState")) or _join(j.get("city"), j.get("state"), j.get("country"))
            jobs.append(_job(row, "phenom", title=title, url=f"https://{host}/{prefix}/job/{quote(jid, safe='')}/{_slugify(title)}",
                             location=loc, posted=_iso_date(j.get("postedDate") or j.get("dateCreated")), remote=_remote_from(loc),
                             extra={"job_id": jid}))
        if not listing or not fresh or (total is not None and (page + 1) * PHENOM_PAGE >= total):
            break
    return jobs


# --------------------------------------------------------------------------- UKG Pro (UltiPro)
# Borrowed from career-ops providers/ultipro.mjs:14 (POST <origin>/<tenant>/JobBoard/<boardId>/JobBoardView/LoadSearchResults),
# :66 (host recruiting[N].ultipro.com|ca), :157-175 (body: opportunitySearch Top 50 / Skip, OrderBy PostedDate), :198-214
# (Locations -> City, State.Code else LocalizedName), :229-271 (Id, Title, PostedDate, BriefDescription; URL
# .../OpportunityDetail?opportunityId=<Id>), :405-424 (stop on empty or short page, or no new ids). No detail fetch (:440-468).
ULTIPRO_PAGE = 50


def _ultipro(row: dict[str, Any], http: Http) -> list[Job] | None:
    """UKG Pro / UltiPro Recruiting public JobBoard search (token host/tenant/boardId)."""
    parts = str(row.get("token") or "").split("/")
    if len(parts) != 3 or not re.fullmatch(r"recruiting[0-9]*\.ultipro\.(?:com|ca)", parts[0].lower()) \
            or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", p) for p in parts[1:]):
        return None
    origin, tenant, board = "https://" + parts[0].lower(), parts[1], parts[2]
    jobs: list[Job] = []
    seen: set[str] = set()
    skip = 0
    for page in range(MAX_PAGES):
        body = {"opportunitySearch": {"Top": ULTIPRO_PAGE, "Skip": skip, "QueryString": "",
                                      "OrderBy": [{"Value": "postedDateDesc", "PropertyName": "PostedDate", "Ascending": False}], "Filters": []},
                "matchCriteria": {"PreferredJobs": [], "Educations": [], "LicenseAndCertifications": [], "Skills": [],
                                  "hasNoLicenses": False, "SkippedSkills": []}}
        st, data = _get(http, f"{origin}/{tenant}/JobBoard/{board}/JobBoardView/LoadSearchResults", method="POST",
                        data=json.dumps(body).encode(), headers=_POST)
        raw = data.get("opportunities") if isinstance(data, dict) else None
        if st != 200 or not isinstance(raw, list):
            if page:
                break
            return None
        fresh = 0
        for o in raw:
            oid = str((o or {}).get("Id") or "").strip() if isinstance(o, dict) else ""
            title = str(o.get("Title") or "").strip() if oid else ""
            if not oid or not title or oid in seen:
                continue
            seen.add(oid)
            fresh += 1
            labels: list[str] = []
            for loc in o.get("Locations") or []:
                a = loc.get("Address") if isinstance(loc, dict) else None
                st_code = ((a or {}).get("State") or {}).get("Code") if isinstance(a, dict) and isinstance(a.get("State"), dict) else ""
                lab = (_join((a or {}).get("City"), st_code) if isinstance(a, dict) else "") or (str(loc.get("LocalizedName") or "") if isinstance(loc, dict) else str(loc))
                if lab.strip() and lab.strip() not in labels:
                    labels.append(lab.strip())
            place = " / ".join(labels)
            jobs.append(_job(row, "ultipro", title=title, url=f"{origin}/{tenant}/JobBoard/{board}/OpportunityDetail?opportunityId={quote(oid, safe='')}",
                             location=place, posted=_iso_date(o.get("PostedDate")), description=_text(o.get("BriefDescription")),
                             remote=_remote_from(place), extra={"req_id": str(o.get("RequisitionNumber") or "")}))
        if not raw or not fresh or len(raw) < ULTIPRO_PAGE:
            break
        skip += len(raw)
    return jobs


# --------------------------------------------------------------------------- ADP Workforce Now
# Borrowed from career-ops providers/adp-workforcenow.mjs:56 (GET https://workforcenow.adp.com/mascsr/default/careercenter/
# public/events/staffing/v1/job-requisitions?cid&ccId&$skip&$top=20), :373 ($skip is ONE-based), :417 (advance $skip by rows
# returned), :295-332 (itemID, requisitionTitle, ExternalJobID in customFieldGroup.stringFields, requisitionLocations),
# :267-281 (posting URL .../recruitment.html?cid&ccId&lang=en_US&type=JS&jobId&jwId), :181-186 (postDate), :403-425 (stop).
ADP_API = "https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions"
ADP_PAGE = 20


def _adp_location(j: dict[str, Any]) -> str:
    out: list[str] = []
    for loc in j.get("requisitionLocations") or []:
        nc = loc.get("nameCode") if isinstance(loc, dict) else None
        nc = nc if isinstance(nc, dict) else {}
        a: dict[str, Any] = loc["address"] if isinstance(loc, dict) and isinstance(loc.get("address"), dict) else {}
        lab = str(nc.get("shortName") or nc.get("longName") or "").strip() or _join(
            a.get("cityName"), (a.get("countrySubdivisionLevel1") or {}).get("codeValue"), (a.get("country") or {}).get("codeValue"))
        if lab and lab not in out:
            out.append(lab)
    return " / ".join(out)


def _adp(row: dict[str, Any], http: Http) -> list[Job] | None:
    """ADP Workforce Now public recruitment API (token cid/ccId)."""
    parts = str(row.get("token") or "").split("/")
    if len(parts) != 2 or not all(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", p) for p in parts):
        return None
    cid, cc = parts
    jobs: list[Job] = []
    seen: set[str] = set()
    skip = 1
    for page in range(MAX_PAGES):
        st, data = _get(http, f"{ADP_API}?cid={quote(cid, safe='')}&ccId={quote(cc, safe='')}&$skip={skip}&$top={ADP_PAGE}", headers=_JSON)
        raw = data.get("jobRequisitions") if isinstance(data, dict) else None
        if st != 200 or not isinstance(raw, list):
            if page:
                break
            return None
        fresh = 0
        for j in raw:
            iid = str((j or {}).get("itemID") or "").strip() if isinstance(j, dict) else ""
            title = str(j.get("requisitionTitle") or "").strip() if iid else ""
            if not iid or not title or iid in seen:
                continue
            seen.add(iid)
            fresh += 1
            ext = next((str(f.get("stringValue") or "").strip() for f in ((j.get("customFieldGroup") or {}).get("stringFields") or [])
                        if isinstance(f, dict) and (f.get("nameCode") or {}).get("codeValue") == "ExternalJobID"), "") or iid
            url = (f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={quote(cid, safe='')}"
                   f"&ccId={quote(cc, safe='')}&lang=en_US&type=JS&jobId={quote(ext, safe='')}&jwId={quote(iid, safe='')}")
            place = _adp_location(j)
            jobs.append(_job(row, "adp", title=title, url=url, location=place, posted=_iso_date(j.get("postDate")),
                             remote=_remote_from(place), extra={"item_id": iid, "req_id": str(j.get("clientRequisitionID") or "")}))
        if not raw or not fresh or len(raw) < ADP_PAGE:
            break
        skip += len(raw)
    return jobs


# --------------------------------------------------------------------------- Cornerstone OnDemand
# Borrowed from career-ops providers/csod.mjs:15 (GET <origin>/ux/ats/careersite/<siteId>/home?c=<corp> carries an anonymous
# JWT as "token":"eyJ..."), :99-102 (token pattern), :17-19,226-243 (POST <origin>/services/x/career-site/v1/search with a
# Bearer token, pageNumber 1-based, pageSize 25), :142-159 (requisitionId, displayJobTitle, postingEffectiveDate M/D/YYYY,
# locations[].city/country; URL .../home/requisition/<id>?c=<corp>), :245-269 (stop). The kit's http cannot read Set-Cookie,
# so the session-cookie replay of :197-206 is NOT done: a tenant that insists on the cookie returns no jobs.
CSOD_PAGE = 25
_CSOD_TOKEN = re.compile(r'"token"\s*:\s*"([A-Za-z0-9._-]+)"')


def _cornerstone(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Cornerstone OnDemand career site (token tenant/siteId; optional row corp, default the tenant)."""
    parts = str(row.get("token") or "").split("/")
    if len(parts) != 2 or not _label(parts[0]) or not parts[1].isdigit():
        return None
    tenant, site = parts[0].lower(), int(parts[1])
    corp = str(row.get("corp") or tenant)
    origin = f"https://{tenant}.csod.com"
    st, page = _get(http, f"{origin}/ux/ats/careersite/{site}/home?c={quote(corp, safe='')}", as_json=False, headers={"Accept": "text/html"})
    m = _CSOD_TOKEN.search(page) if st == 200 and isinstance(page, str) else None
    if not m:
        return None
    hdr = dict(_POST, Authorization="Bearer " + m.group(1))
    jobs: list[Job] = []
    seen: set[str] = set()
    for n in range(1, MAX_PAGES + 1):
        body = {"careerSiteId": site, "careerSitePageId": site, "pageNumber": n, "pageSize": CSOD_PAGE, "cultureId": 1,
                "cultureName": "en-US", "searchText": "", "states": [], "countryCodes": [], "cities": [], "placeID": "",
                "radius": None, "postingsWithinDays": None, "customFieldCheckboxKeys": [], "customFieldDropdowns": [],
                "customFieldRadios": []}
        st, data = _get(http, f"{origin}/services/x/career-site/v1/search", method="POST", data=json.dumps(body).encode(), headers=hdr)
        d: Any = data.get("data") if isinstance(data, dict) else None
        raw = d.get("requisitions") if isinstance(d, dict) else None
        if st != 200 or not isinstance(raw, list):
            if n > 1:
                break
            return None
        total = d.get("totalCount") if isinstance(d.get("totalCount"), int) else None
        fresh = 0
        for r in raw:
            rid = str((r or {}).get("requisitionId") or "") if isinstance(r, dict) else ""
            title = _text(r.get("displayJobTitle")) if rid else ""
            if not rid or not title or rid in seen:
                continue
            seen.add(rid)
            fresh += 1
            labels: list[str] = []
            for loc in r.get("locations") or []:
                lab = _join(loc.get("city"), loc.get("country")) if isinstance(loc, dict) else ""
                if lab and lab not in labels:
                    labels.append(lab)
            place = " / ".join(labels)
            jobs.append(_job(row, "cornerstone", title=title, url=f"{origin}/ux/ats/careersite/{site}/home/requisition/{rid}?c={quote(corp, safe='')}",
                             location=place, posted=_jobvite_date(str(r.get("postingEffectiveDate") or "")), remote=_remote_from(place)))
        if not raw or not fresh or len(raw) < CSOD_PAGE or (total is not None and n * CSOD_PAGE >= total):
            break
    return jobs


# --------------------------------------------------------------------------- Gem
# Borrowed from career-ops providers/gem.mjs:24 (POST https://jobs.gem.com/api/public/graphql/batch, header batch: true),
# :27-59 (JobBoardList query: oatsExternalJobPostings(boardId) { jobPostings { extId title locations { name isoCountry isRemote } } }),
# :61-74 (ExternalJobPostingQuery: firstPublishedTsSec, descriptionHtml), :189-197 (location = name + country, plus Remote),
# :224-287 (list, then one batched detail request; the posting URL is https://jobs.gem.com/<board>/<extId>).
GEM_API = "https://jobs.gem.com/api/public/graphql/batch"
_GEM_LIST = "query JobBoardList($boardId: String!) { oatsExternalJobPostings(boardId: $boardId) { jobPostings { id extId title locations { name city isoCountry isRemote } job { department { name } employmentType } } } }"
_GEM_DETAIL = "query ExternalJobPostingQuery($boardId: String!, $extId: String!) { oatsExternalJobPosting(boardId: $boardId, extId: $extId) { extId firstPublishedTsSec descriptionHtml } }"


def _gem(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Gem public job-board GraphQL (token = board id)."""
    board = str(row.get("token") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", board):
        return None
    hdr = dict(_POST, batch="true")
    st, data = _get(http, GEM_API, method="POST", headers=hdr,
                    data=json.dumps([{"operationName": "JobBoardList", "variables": {"boardId": board}, "query": _GEM_LIST}]).encode())
    first = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
    posts = (((first or {}).get("data") or {}).get("oatsExternalJobPostings") or {}).get("jobPostings") if first else None
    if st != 200 or first is None or first.get("errors") or not isinstance(posts, list):
        return None
    posts = [p for p in posts if isinstance(p, dict) and p.get("extId") and p.get("title")][: MAX_PAGES * 50]
    details: dict[str, dict[str, Any]] = {}
    if posts:  # one batched request for dates and descriptions; a failure keeps the listing
        ops = [{"operationName": "ExternalJobPostingQuery", "variables": {"boardId": board, "extId": p["extId"]}, "query": _GEM_DETAIL} for p in posts]
        st, det = _get(http, GEM_API, method="POST", headers=hdr, data=json.dumps(ops).encode())
        for d in det if st == 200 and isinstance(det, list) else []:
            p = ((d or {}).get("data") or {}).get("oatsExternalJobPosting") if isinstance(d, dict) else None
            if isinstance(p, dict) and p.get("extId"):
                details[str(p["extId"])] = p
    jobs: list[Job] = []
    for p in posts:
        d = details.get(str(p["extId"]), {})
        labels: list[str] = []
        for loc in p.get("locations") or []:
            if isinstance(loc, dict):
                lab = " · ".join(x for x in (_join(str(loc.get("name") or "").strip(), loc.get("isoCountry")), "Remote" if loc.get("isRemote") else "") if x)
                if lab and lab not in labels:
                    labels.append(lab)
        place = " · ".join(labels)
        secs = d.get("firstPublishedTsSec")
        posted = ""
        if isinstance(secs, (int, float)) and secs > 0:
            posted = datetime.fromtimestamp(secs, tz=timezone.utc).strftime("%Y-%m-%d")
        dept = ((p.get("job") or {}).get("department") or {}).get("name") if isinstance(p.get("job"), dict) and isinstance((p.get("job") or {}).get("department"), dict) else ""
        jobs.append(_job(row, "gem", title=str(p["title"]), url=f"https://jobs.gem.com/{board}/{p['extId']}", location=place, posted=posted,
                         description=_text(d.get("descriptionHtml")), remote=_remote_from(place), extra={"department": dept or ""}))
    return jobs


# --------------------------------------------------------------------------- JazzHR (ApplyToJob)
# Borrowed from career-ops providers/jazzhr.mjs:37,63 (HTML board https://<t>.applytojob.com/apply), :136-146 (cards:
# <li class="list-group-item"> with an <a href=".../apply/<id>/<slug>"> title and a fa-map-marker location), :110-120 and
# :158-163 (fallback: any /apply/ link on the same origin, query dropped). Parsed with html.parser, no regex over the page.
# Detail pages (:178-199) are not fetched.
class _JazzParser(HTMLParser):
    def __init__(self, base: str) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[dict[str, str]] = []
        self.base, self._href, self._text, self._loc = base, "", "", False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "a" and "/apply/" in a.get("href", ""):
            self._href, self._text = a["href"], ""
        elif tag == "i" and "fa-map-marker" in a.get("class", ""):
            self._loc = True

    def handle_data(self, data: str) -> None:
        if self._href:
            self._text += data
        elif self._loc and data.strip() and self.rows:
            self.rows[-1]["location"], self._loc = " ".join(data.split()), False

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href:
            u, base = urlsplit(urljoin(self.base, self._href)), urlsplit(self.base)
            title = " ".join(self._text.split())
            if (u.scheme, u.netloc) == (base.scheme, base.netloc) and re.fullmatch(r"/apply/[^/].*", u.path) and title:
                self.rows.append({"title": title, "url": u._replace(query="", fragment="").geturl(), "location": ""})
            self._href = ""


def _jazzhr(row: dict[str, Any], http: Http) -> list[Job] | None:
    """JazzHR public board page (<tenant>.applytojob.com/apply)."""
    tenant = str(row.get("token") or "").lower()
    if not _label(tenant):
        return None
    base = f"https://{tenant}.applytojob.com/apply"
    st, page = _get(http, base, as_json=False, headers={"Accept": "text/html"})
    if st != 200 or not isinstance(page, str):
        return None
    p = _JazzParser(base)
    p.feed(page)
    by_url: dict[str, dict[str, str]] = {}
    for r in p.rows:  # one row per posting link; the first card wins
        by_url.setdefault(r["url"], r)
    return [_job(row, "jazzhr", title=r["title"], url=r["url"], location=r["location"], remote=_remote_from(r["location"]))
            for r in list(by_url.values())[: MAX_PAGES * 100]]


# --------------------------------------------------------------------------- Softgarden
# Borrowed from career-ops providers/softgarden.mjs:47-49 (GET https://<tenant>.softgarden.io/<lang>/widgets/jobs, lang default
# de), :86-117 (blocks split on <div class="matchElement" id="job_id_<id>">; <a href="../../job/<id>/<slug>"> title,
# class="location-view-item" cities, class="matchValue date"), :56-76 (dates D.M.Y or M/D/Y, 2-digit years are 20xx).
_SG_LINK = re.compile(r'<a href="([^"]*/job/[^"]+)"[^>]*>([^<]*)</a>')
_SG_CITY = re.compile(r'class="location-view-item"[^>]*>([^<]*)</span>')
_SG_DATE = re.compile(r'class="matchValue date"[^>]*>([^<]*)</div>')


def _sg_date(raw: str) -> str:
    m = re.fullmatch(r"(\d{1,2})([./])(\d{1,2})\2(\d{2,4})", raw.strip())
    if not m:
        return ""
    a, b, y = int(m.group(1)), int(m.group(3)), int(m.group(4))
    day, month = (a, b) if m.group(2) == "." else (b, a)
    try:
        return date(y + 2000 if y < 100 else y, month, day).isoformat()
    except ValueError:
        return ""


def _softgarden(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Softgarden hosted jobs widget (token = tenant; optional row lang, default de)."""
    tenant, lang = str(row.get("token") or "").lower(), str(row.get("lang") or "de").lower()
    if not _label(tenant) or not re.fullmatch(r"[a-z]{2}", lang):
        return None
    widget = f"https://{tenant}.softgarden.io/{lang}/widgets/jobs"
    st, page = _get(http, widget, as_json=False, headers={"Accept": "text/html"})
    if st != 200 or not isinstance(page, str):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for block in page.split('<div class="matchElement" id="job_id_')[1:][: MAX_PAGES * 100]:
        jid, link = block.split('"', 1)[0], _SG_LINK.search(block)
        title = _text(link.group(2)) if link else ""
        if not link or not jid.isdigit() or jid in seen or not title:
            continue
        seen.add(jid)
        place = " / ".join(dict.fromkeys(c for c in (_text(x) for x in _SG_CITY.findall(block)) if c))
        d = _SG_DATE.search(block)
        jobs.append(_job(row, "softgarden", title=title, url=urljoin(widget, html.unescape(link.group(1))), location=place,
                         posted=_sg_date(_text(d.group(1))) if d else "", remote=_remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- JOIN
# Borrowed from career-ops providers/join.mjs:53,59 (GET https://join.com/companies/<slug>, data is the __NEXT_DATA__ script),
# :61-66 (props.pageProps.initialState.jobs.items), :76-87 (jobs.pagination.pageCount; later pages are ?page=N),
# :93-99 (title, city.cityName, URL https://join.com/companies/<company.domain or slug>/jobs/<idParam>).
def _next_data(page: str) -> Any:
    """The JSON inside the __NEXT_DATA__ script tag (a linear find, no regex), or None."""
    i = page.find("__NEXT_DATA__")
    start = page.find(">", i) + 1 if i >= 0 else 0
    end = page.find("</script>", start) if start else -1
    try:
        return json.loads(page[start:end]) if end > start else None
    except ValueError:
        return None


def _join_board(row: dict[str, Any], http: Http) -> list[Job] | None:
    """JOIN (join.com) company page (token = company slug)."""
    slug = str(row.get("token") or "")
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", slug):
        return None
    base = f"https://join.com/companies/{slug}"
    items: list[Any] = []
    pages, n, domain = 1, 1, slug
    while n <= min(pages, MAX_PAGES):
        st, page = _get(http, base if n == 1 else f"{base}?page={n}", as_json=False, headers={"Accept": "text/html"})
        data = _next_data(page) if st == 200 and isinstance(page, str) else None
        state: Any = (((data or {}).get("props") or {}).get("pageProps") or {}).get("initialState") if isinstance(data, dict) else None
        listing = ((state or {}).get("jobs") or {}).get("items") if isinstance(state, dict) else None
        if not isinstance(listing, list):
            if n > 1:
                break
            return None
        items += listing
        if n == 1:
            count = (state["jobs"].get("pagination") or {}).get("pageCount")
            pages = count if isinstance(count, int) else 1
            domain = str((state.get("company") or {}).get("domain") or slug)
        n += 1
    jobs: list[Job] = []
    for j in items:
        title = str(j.get("title") or "").strip() if isinstance(j, dict) else ""
        if not title or not j.get("idParam"):
            continue
        city = j.get("city")
        place = str(city.get("cityName") or "") if isinstance(city, dict) else ""
        jobs.append(_job(row, "join", title=title, url=f"https://join.com/companies/{quote(domain, safe='')}/jobs/{quote(str(j['idParam']), safe='')}",
                         location=place, remote=_remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- Jibe (iCIMS Jibe Apply)
# Borrowed from career-ops providers/jibeapply.mjs:5-6,33-42 (GET https://<slug>.jibeapply.com/api/jobs), :106-124 (first page
# gives totalCount and the page size; later pages are ?page=N), :52-83 (jobs[].data title, slug or req_id, full_location else
# city+country, hiring_organization; URL <origin>/jobs/<slug>). A branded host can be set with the row field host.
def _jibe(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Jibe Apply public jobs API (token = slug of <slug>.jibeapply.com, or row host for a branded domain)."""
    slug, host = str(row.get("token") or "").lower(), str(row.get("host") or "").lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}", host):
        host = f"{slug}.jibeapply.com" if _label(slug) else ""
    if not host:
        return None
    items: list[Any] = []
    total, size, page = 0, 0, 1
    while page <= MAX_PAGES:
        st, data = _get(http, f"https://{host}/api/jobs" + (f"?page={page}" if page > 1 else ""), headers=_JSON)
        listing = data.get("jobs") if isinstance(data, dict) else None
        if st != 200 or not isinstance(listing, list):
            if page > 1:
                break
            return None
        items += listing
        if page == 1:
            total = data["totalCount"] if isinstance(data.get("totalCount"), int) else 0
            size = len(listing) or 10
        if not listing or page * size >= total:
            break
        page += 1
    jobs: list[Job] = []
    for it in items:
        d = (it.get("data") or it) if isinstance(it, dict) else {}
        title, key = str(d.get("title") or "").strip(), d.get("slug") or d.get("req_id")
        if not title or not key:
            continue
        place = str(d.get("full_location") or "") or _join(d.get("city"), d.get("country"))
        jobs.append(_job(row, "jibe", title=title, url=f"https://{host}/jobs/{quote(str(key), safe='')}", location=place,
                         remote=_remote_from(place), extra={"organization": str(d.get("hiring_organization") or "")}))
    return jobs


# --------------------------------------------------------------------------- Keka Hire (India)
# Source: own read of live Keka boards (2026-10-19), no third-party code. The careers page
# https://<tenant>.keka.com/careers/ links its assets under /ats/documents/<GUID>/; the public list
# /careers/api/embedjobs/default/active/<GUID> is a JSON array (id, title, description HTML, jobLocations
# [{city, state, countryCode, countryName}], publishedOn, experience, departmentName, jobType). The posting
# is /careers/jobdetails/<id>. robots.txt allows only /careers, so the reader makes exactly these two calls.
_KEKA_GUID = re.compile(r"/ats/documents/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/")


def keka_guid(page: Any) -> str:
    """The portal GUID on a Keka careers page, '' when the page carries none."""
    m = _KEKA_GUID.search(page) if isinstance(page, str) else None
    return m.group(1).lower() if m else ""


def keka_jobs(row: dict[str, Any], tenant: str, items: Any) -> list[Job] | None:
    """Jobs from a Keka embedjobs list (None when it is not a list)."""
    if not isinstance(items, list):
        return None
    jobs: list[Job] = []
    for j in items[: MAX_PAGES * 100]:
        if not isinstance(j, dict):
            continue
        title, jid = str(j.get("title") or "").strip(), str(j.get("id") or "").strip()
        if not title or not jid.isdigit():
            continue
        places = [_join(x.get("city") or x.get("name"), x.get("state"), x.get("countryName") or x.get("countryCode"))
                  for x in (j.get("jobLocations") or []) if isinstance(x, dict)]
        place = " · ".join(dict.fromkeys(p for p in places if p))
        jt = j.get("jobType")
        jt_word = jt if isinstance(jt, str) and not jt.strip().isdigit() else ""  # a numeric code says nothing we can read
        jobs.append(_job(row, "keka", title=title, url=f"https://{tenant}.keka.com/careers/jobdetails/{jid}", location=place,
                         posted=_iso_date(j.get("publishedOn")), description=_text(j.get("description")),
                         remote=_remote_from(place, jt_word),
                         extra={"department": str(j.get("departmentName") or ""), "experience": str(j.get("experience") or "")}))
    return jobs


def _keka(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Keka Hire careers board (token = tenant of <tenant>.keka.com): careers page -> GUID -> active jobs list."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    st, page = _get(http, f"https://{tenant}.keka.com/careers/", as_json=False, headers={"Accept": "text/html"})
    guid = keka_guid(page) if st == 200 else ""
    if not guid:
        return None
    st, items = _get(http, f"https://{tenant}.keka.com/careers/api/embedjobs/default/active/{guid}", headers=_JSON)
    return keka_jobs(row, tenant, items) if st == 200 else None


# --------------------------------------------------------------------------- Zoho Recruit
# Source: own read of live Zoho Recruit career sites (2026-10-19), no third-party code. GET
# https://<tenant>.zohorecruit.<in|com>/jobs/Careers carries the job rows as HTML-escaped JSON in a hidden
# <input value="[{...}]"> (Posting_Title, City, State, Country, Remote_Job, id; some tenants add Date_Opened and
# Job_Description). The posting is /jobs/Careers/<id>. An unknown tenant (or the wrong domain) answers 200
# "Page does not exist". robots.txt allows /jobs.
ZOHO_TLDS = ("in", "com")
# `[^<>]` (not `[^>]`): a failed attempt stops at the next "<", so a page full of "<input " with no
# ">" is read in linear time instead of quadratic (security review 2026-10-19).
_INPUT_TAG = re.compile(r"<input\b[^<>]*>", re.I)
_VALUE_ATTR = re.compile(r'\bvalue="([^"]*)"')


def zoho_rows(page: Any) -> list[dict[str, Any]] | None:
    """Job rows from a Zoho Recruit careers page; None when the page is not a career site."""
    if not isinstance(page, str) or "Page does not exist" in page[:5000]:
        return None
    rows: list[dict[str, Any]] = []
    portal = False
    for tag in _INPUT_TAG.findall(page):
        if 'type="hidden"' not in tag:
            continue
        v = _VALUE_ATTR.search(tag)
        raw = html.unescape(v.group(1)) if v else ""
        if not raw.startswith("[{"):
            continue
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            continue
        if data[0].get("api_name") == "Job_Openings":
            portal = True  # the field layout block: a real career site even when it lists no jobs
        elif "Posting_Title" in data[0] or "Job_Opening_Name" in data[0]:
            portal = True
            rows += [d for d in data if isinstance(d, dict)]
    return rows if portal else None


def _truthy(v: Any) -> bool:
    return v is True or str(v).strip().lower() == "true"


def zoho_jobs(row: dict[str, Any], origin: str, rows: list[dict[str, Any]]) -> list[Job]:
    """Jobs from Zoho Recruit rows; every optional field may be missing."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for r in rows[: MAX_PAGES * 100]:
        title = str(r.get("Posting_Title") or r.get("Job_Opening_Name") or "").strip()
        jid = str(r.get("id") or "").strip()
        if not title or not jid.isdigit() or jid in seen or r.get("Publish") is False:
            continue
        seen.add(jid)
        place = _join(*(r.get(k) for k in ("City", "State", "Country") if str(r.get(k) or "").strip().lower() != "none"))
        remote = True if _truthy(r.get("Remote_Job")) else _remote_from(place)
        jobs.append(_job(row, "zohorecruit", title=title, url=f"{origin}/jobs/Careers/{jid}", location=place,
                         posted=_iso_date(r.get("Date_Opened")), description=_text(r.get("Job_Description")), remote=remote,
                         extra={"job_type": str(r.get("Job_Type") or "")}))
    return jobs


def _zohorecruit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Zoho Recruit career site (token = tenant; row tld 'in' or 'com', else .in is tried first, then .com)."""
    tenant, tld = str(row.get("token") or "").strip().lower(), str(row.get("tld") or "").strip().lower()
    if not _label(tenant):
        return None
    for t in ((tld,) if tld in ZOHO_TLDS else ZOHO_TLDS):
        origin = f"https://{tenant}.zohorecruit.{t}"
        st, page = _get(http, f"{origin}/jobs/Careers", as_json=False, headers={"Accept": "text/html"})
        rows = zoho_rows(page) if st == 200 else None
        if rows is not None:
            return zoho_jobs(row, origin, rows)
    return None


# --------------------------------------------------------------------------- RippleHire (India)
# Source: own read of live RippleHire career sites (2026-10-20), no third-party code. One host per tenant,
# https://<tenant>.ripplehire.com (app.ripplehire.com is a shared sandbox that answers any tenant's token with
# another company's jobs, so it is refused). The career-site token (20 letters/digits) is public: GET
# /candidate/careers redirects to /candidate/?token=<token>&source=CAREERSITE. The list is POST
# /candidate/candidatejobsearch, form careerSiteUrlParams={"page","search":"*:*","token","pagesize"} -> totalJobCount
# and jobVoList[] (jobSeq, jobTitle, locations, jobReqExp, jobMinExp in YEARS; no description, no date, no remote
# flag). Detail: GET /candidate/candidatejobdetail?token=&jobSeq= -> jobVO.jobDesc (HTML), publishDetails.CAREER_SITE
# (ISO) or jobPostingDate (DD-Mon-YYYY). A bad or expired token answers 500. robots.txt redirects to a login page.
RIPPLEHIRE_PAGE = 100
RIPPLEHIRE_DETAIL_CAP = 25  # detail requests per board (the rest keep the list fields, no description)
_RIPPLEHIRE_TOKEN = re.compile(r"[A-Za-z0-9]{20}")
_RIPPLEHIRE_SHARED = frozenset({"app", "www"})
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def ripplehire_board(token: Any) -> tuple[str, str] | None:
    """(tenant, career-site token) from a board 'tenant/token', or None when either part is malformed."""
    tenant, _, tok = str(token or "").strip().partition("/")
    tenant = tenant.lower()
    if not _label(tenant) or tenant in _RIPPLEHIRE_SHARED or not _RIPPLEHIRE_TOKEN.fullmatch(tok):
        return None
    return tenant, tok


def _ripplehire_posted(vo: dict[str, Any]) -> str:
    """The career-site publish date (ISO), else jobPostingDate '19-Oct-2026'; '' when neither reads."""
    pub = vo.get("publishDetails")
    if isinstance(pub, str):
        try:
            pub = json.loads(pub)
        except (ValueError, RecursionError):  # a hostile, deeply nested string must not empty the board
            pub = None
    iso = _iso_date(pub.get("CAREER_SITE")) if isinstance(pub, dict) else ""
    if iso:
        return iso
    try:
        return datetime.strptime(str(vo.get("jobPostingDate") or "").strip(), "%d-%b-%Y").date().isoformat()
    except ValueError:
        return ""


def ripplehire_jobs(row: dict[str, Any], tenant: str, tok: str, items: list[dict[str, Any]],
                    details: dict[str, dict[str, Any]]) -> list[Job]:
    """Jobs from RippleHire list items, with description and date from ``details`` (jobSeq -> jobVO) when fetched."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for j in items:
        title, seq = str(j.get("jobTitle") or "").strip(), str(j.get("jobSeq") or "").strip()
        if not title or not (seq.isascii() and seq.isdigit()) or seq in seen:
            continue
        seen.add(seq)
        place = str(j.get("locations") or "").strip()
        vo = details.get(seq) or {}
        years = j.get("jobMinExp")
        extra: dict[str, Any] = {"experience": str(j.get("jobReqExp") or "")}
        if isinstance(years, (int, float)) and not isinstance(years, bool) and 0 <= years <= 40:
            extra["years_min"] = int(years)
        jobs.append(_job(row, "ripplehire", title=title, location=place,
                         url=f"https://{tenant}.ripplehire.com/candidate/?token={tok}&source=CAREERSITE#detail/job/{seq}",
                         posted=_ripplehire_posted(vo), description=_CTRL.sub("", _text(vo.get("jobDesc"))),
                         remote=_remote_from(place, title), extra=extra))
    return jobs


def _ripplehire(row: dict[str, Any], http: Http) -> list[Job] | None:
    """RippleHire career site (token = 'tenant/careerSiteToken'): paged list, then details for title matches."""
    parsed = ripplehire_board(row.get("token"))
    if parsed is None:
        return None
    tenant, tok = parsed
    origin = f"https://{tenant}.ripplehire.com"
    hdr = {"Content-Type": "application/x-www-form-urlencoded; charset=UTF-8", "Accept": "application/json",
           "X-Requested-With": "XMLHttpRequest", "Referer": f"{origin}/candidate/?token={tok}&source=CAREERSITE"}
    items: list[dict[str, Any]] = []
    for page in range(MAX_PAGES):
        params = json.dumps({"page": page, "search": "*:*", "token": tok, "pagesize": RIPPLEHIRE_PAGE})
        body = f"careerSiteUrlParams={quote(params, safe='')}&lang=en".encode()
        st, data = _get(http, f"{origin}/candidate/candidatejobsearch", method="POST", data=body, headers=hdr)
        if st != 200 or not isinstance(data, dict) or not isinstance(data.get("jobVoList"), list):
            if page == 0:
                return None
            break
        batch = [j for j in data["jobVoList"] if isinstance(j, dict)]
        items += batch
        total = data.get("totalJobCount")
        if len(batch) < RIPPLEHIRE_PAGE or (isinstance(total, int) and len(items) >= total):
            break
    details: dict[str, dict[str, Any]] = {}
    for j in items:
        if len(details) >= RIPPLEHIRE_DETAIL_CAP:
            break
        seq = str(j.get("jobSeq") or "").strip()
        if not (seq.isascii() and seq.isdigit()) or seq in details or not _title_ok(row, str(j.get("jobTitle") or "")):
            continue
        st, d = _get(http, f"{origin}/candidate/candidatejobdetail?token={tok}&jobSeq={seq}", headers=_JSON)
        vo = d.get("jobVO") if st == 200 and isinstance(d, dict) else None
        details[seq] = vo if isinstance(vo, dict) else {}
    return ripplehire_jobs(row, tenant, tok, items, details)


# --------------------------------------------------------------------------- Paylocity
# Borrowed from freehire internal/ingest/sources/paylocity.go (MIT): board = company GUID (:13-14); listing GET
# https://recruiting.paylocity.com/Recruiting/Jobs/All/<guid> (:45), whose window.pageData embeds the whole Jobs array,
# sliced out by bracket matching (:53, bracketSlice in nextflight.go:117-152); brief fields JobId, JobTitle, LocationName,
# PublishedDate, IsRemote, JobLocation.Country (:133-146); posting URL /Recruiting/Jobs/Details/<JobId> (:78); a brief with
# no id is skipped (:75); a failed detail fetch keeps the job with no description (:69-73, :81); description is the <div>
# after <div class="job-listing-header">Description</div> (:107-130), else the legacy schema.org ld+json "description"
# (:84-89); remote = IsRemote or "remote" in the location (:101). Fixtures follow paylocity_test.go:11-32, :122, :140-145.
# The listing is one request, so a bad listing fails the board (:46-60). The detail cap is the kit's own.
PAYLOCITY_DETAIL_CAP = 25  # detail-page requests per board (the rest keep the listing fields, no description)
_PAYLOCITY_JOBS_KEY = re.compile(r'"Jobs"\s*:')
_PAYLOCITY_HEADER = re.compile(
    r"""<div\b[^<>]*class=["'][^"']*\bjob-listing-header\b[^"']*["'][^<>]*>\s*Description\s*</div>\s*(<div\b[^>]*>)""", re.I)
_PAYLOCITY_LD = re.compile(r"""<script\b[^<>]*type=["']application/ld\+json["'][^<>]*>([\s\S]*?)</script>""", re.I)
_PAYLOCITY_LD_END = re.compile(r"</script>", re.I)
_PAYLOCITY_DIV = re.compile(r"<(/?)div\b[^<>]*>", re.I)
_PAYLOCITY_DROP = re.compile(r"<(script|style)\b[^<>]*>", re.I)


def _paylocity_closer(name: str) -> re.Pattern[str]:
    return re.compile(rf"</{name}\s*>", re.I)


def _paylocity_drop(text: str) -> str:
    """Remove every <script>/<style> element (one linear pass; the lazy regex was quadratic on unclosed ones)."""
    return str(drop_blocks(text, _PAYLOCITY_DROP, _paylocity_closer))


def _bracket_slice(s: str, key: re.Pattern[str], open_: str, close: str) -> str:
    """The balanced ``open_...close`` text after the first ``key`` match, skipping brackets inside JSON strings ('' if none)."""
    m = key.search(s)
    if not m:
        return ""
    start = s.find(open_, m.end())
    if start < 0:
        return ""
    depth, in_str, i = 0, False, start
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 1  # skip the escaped character
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return ""


def _paylocity_description(page: str) -> str:
    """Description block of a detail page (the div after the 'Description' header), else the ld+json description."""
    m = _PAYLOCITY_HEADER.search(page)
    if m:
        depth, end = 1, len(page)
        for t in _PAYLOCITY_DIV.finditer(page, m.end()):
            depth += -1 if t.group(1) else 1
            if depth == 0:
                end = t.start()
                break
        body = str(_text(_paylocity_drop(page[m.end():end])))
        if body:
            return body
    for lm in finditer_closed(_PAYLOCITY_LD, _PAYLOCITY_LD_END, page):
        raw = lm.group(1)
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("description"):
                return str(_text(_paylocity_drop(html.unescape(str(node["description"])))))
    return ""


def fetch_paylocity(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Paylocity career site (token = company GUID): window.pageData Jobs list + per-job detail page (capped)."""
    try:
        guid = str(row.get("token") or "").strip()
        if not _label(guid):
            _count(stats, ok=False)
            return []
        base = "https://recruiting.paylocity.com/Recruiting/Jobs"
        st, page = _get(http, f"{base}/All/{quote(guid, safe='')}", as_json=False, headers={"Accept": "text/html"})
        briefs: Any = None
        if st == 200 and isinstance(page, str):
            try:
                briefs = json.loads(_bracket_slice(page, _PAYLOCITY_JOBS_KEY, "[", "]") or "null")
            except ValueError:
                briefs = None
        if not isinstance(briefs, list):  # not 200, no Jobs array, or unreadable: skip the board
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        details = 0  # detail requests so far: only cards whose title the user searches use the cap
        for b in briefs:
            jid = b.get("JobId") if isinstance(b, dict) else None
            if not isinstance(jid, int) or isinstance(jid, bool) or jid <= 0:
                continue
            url, place = f"{base}/Details/{jid}", str(b.get("LocationName") or "").strip()
            desc = ""
            title = str(b.get("JobTitle") or "")
            if details < PAYLOCITY_DETAIL_CAP and _title_ok(row, title):
                details += 1
                dst, det = _get(http, url, as_json=False, headers={"Accept": "text/html"})
                if dst == 200 and isinstance(det, str):  # a failing detail only costs the description
                    desc = _paylocity_description(det)
            loc = b.get("JobLocation")
            country = str(loc.get("Country") or "") if isinstance(loc, dict) else ""
            jobs.append(_job(row, "paylocity", title=title, url=url, location=place,
                             posted=_iso_date(b.get("PublishedDate")), description=desc,
                             remote=True if b.get("IsRemote") is True else _remote_from(place),
                             extra={"country": country}))
        jobs = [j for j in jobs if j["title"]]
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001 - fetchers must never raise
        _count(stats, ok=False)
        return []


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "rippling": _wrap(_rippling),
    "breezy": _wrap(_breezy),
    "pinpoint": _wrap(_pinpoint),
    "phenom": _wrap(_phenom),
    "ultipro": _wrap(_ultipro),
    "adp": _wrap(_adp),
    "cornerstone": _wrap(_cornerstone),
    "gem": _wrap(_gem),
    "jazzhr": _wrap(_jazzhr),
    "softgarden": _wrap(_softgarden),
    "join": _wrap(_join_board),
    "jibe": _wrap(_jibe),
    "keka": _wrap(_keka),
    "zohorecruit": _wrap(_zohorecruit),
    "ripplehire": _wrap(_ripplehire),
    "paylocity": fetch_paylocity,
}
