"""Generic applicant-tracking platforms for the hunt kit, part 4: ADP MyJobs and Eploy. Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  adpmyjobs  the career site's own slug (``stellantisexternalcx``); NOT a Workforce Now board (``adp``, cid/ccId)
  eploy      the careers host (``careers.pei.group`` or ``<tenant>.eploy.net``)

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. Posting pages / requisition details are opened only for cards whose
title passes the run's ``title_ok`` test, up to 25 per board, and the first refusal ends a board's detail requests.

Already read elsewhere (not rebuilt here): Cornerstone (``cornerstone``, kit_ats_more.py, career-ops csod.mjs) and Jibe
(``jibe``, kit_ats_more.py).
NOT built (written up in README and NOTICE):
  Dayforce (freehire dayforce.go:34-47): the listing is a POST guarded by next-auth's double-submit CSRF pair, a cookie that
  /api/auth/csrf sets and whose token must be echoed in a header. The kit's ``http`` keeps no cookies, so a jobs.dayforcehcm.com
  link stays reported as unsupported (hunt_kit.board_from_job_url).
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, unquote, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _join,
    _ld_places,
    _ld_posting,
    _open_details,
    _remote_from,
    _sitemap_locs,
    _title_ok,
    _tree,
    _walk,
    _wrap,
    safe_fromstring,
)

_JSON = {"Accept": "application/json"}
_XML = {"Accept": "application/xml, text/xml"}

DETAIL_CAP = 25  # detail requests per board (the kit's own cap; the references open every posting)
ADP_PAGE = 50  # adpmyjobs.go:44 $top
ADP_PAGES = 100  # adpmyjobs.go:45 bounds the paging so a site that ignores $skip cannot loop
_SITEMAP_MAX = 10_000_000  # a live-jobs.xml can list thousands of vacancies; the 2 MB page default would drop it


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("�", "").split())


# --------------------------------------------------------------------------- ADP MyJobs
# Borrowed from freehire internal/ingest/sources/adpmyjobs.go:12-24 (a second ADP product, not Workforce Now: the board is the
# career site's own slug), :33-34 (the site's public record GET https://myjobs.adp.com/public/staffing/v1/career-site/<slug>
# publishes the orgoid), :35-40 (the list lives on my.adp.com: https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/
# job-requisitions with $top=50&$skip=n; "myadp_prefix" is a literal path segment), :104-111 (no orgoid fails the board: an
# unauthorised listing returns 400 that reads as a vanished board), :113-130 (the orgoid goes in a REQUEST HEADER on the list
# and detail calls; page until a page is empty or skip + page >= count; a later page failing ends the walk with what was read),
# :56-81 (jobRequisitions[].reqId, jobTitle, jobDescription, postingDate, requisitionLocations[].address cityName /
# countrySubdivisionLevel1.longName / country.longName; the list omits the description), :149-170 (the per-requisition detail
# GET .../job-requisitions/<reqId> answers the same envelope one long; description entity-unescaped, then sanitised; URL
# https://myjobs.adp.com/<slug>/cx/job/<reqId>), :172-180 (company = the row's, else clientName, else the site name), :182-198
# (location = first requisitionLocations entry, "City, Region, Country", empty parts dropped; no other array is read).
_ADP_SLUG = re.compile(r"[A-Za-z0-9_-]{1,80}")
_ADP_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
_ADP_SITE = "https://myjobs.adp.com/public/staffing/v1/career-site/"
_ADP_LIST = "https://my.adp.com/myadp_prefix/mycareer/public/staffing/v1/job-requisitions"


def _adp_place(req: dict[str, Any]) -> str:
    locs = req.get("requisitionLocations")
    first = locs[0] if isinstance(locs, list) and locs else None
    a = first.get("address") if isinstance(first, dict) else None
    if not isinstance(a, dict):
        return ""

    def name(key: str) -> str:
        v = a.get(key)
        return _clean(v.get("longName")) if isinstance(v, dict) else ""
    return str(_join(_clean(a.get("cityName")), name("countrySubdivisionLevel1"), name("country")))


def _adpmyjobs(row: dict[str, Any], http: Http) -> list[Job] | None:
    """ADP MyJobs career site: the site record gives the orgoid, the staffing list is paged, details only for passing titles."""
    slug = str(row.get("token") or "").strip()
    if not _ADP_SLUG.fullmatch(slug):
        return None
    st, site = _get(http, _ADP_SITE + slug, headers=_JSON)
    orgoid = str(site.get("orgoid") or "").strip() if st == 200 and isinstance(site, dict) else ""
    if not _ADP_ID.fullmatch(orgoid):
        return None  # no site, or no orgoid: listing without it would 400 and read as a closed board
    hdr = {"orgoid": orgoid, "Accept": "application/json"}
    reqs: dict[str, dict[str, Any]] = {}
    for page in range(ADP_PAGES):
        skip = page * ADP_PAGE
        st, data = _get(http, f"{_ADP_LIST}?%24top={ADP_PAGE}&%24skip={skip}", headers=hdr)
        batch = data.get("jobRequisitions") if st == 200 and isinstance(data, dict) else None
        if not isinstance(batch, list):
            if page > 0:
                break  # a later page failing ends the walk with what was read
            return None
        if not batch:
            break
        for r in batch:
            ident = r.get("reqId") if isinstance(r, dict) else None
            ident = str(ident).strip() if isinstance(ident, (str, int)) and not isinstance(ident, bool) else ""
            if _ADP_ID.fullmatch(ident) and ident not in reqs:
                reqs[ident] = r
        total = data.get("count")
        if isinstance(total, int) and skip + len(batch) >= total:
            break
    company = _clean(row.get("name")) or _clean(site.get("clientName")) or _clean(site.get("name"))
    owner = {**row, "name": company} if company else row
    details: dict[str, dict[str, Any]] = {}
    tried = 0
    for ident, r in reqs.items():
        if tried >= DETAIL_CAP:
            break
        if not _title_ok(row, _clean(r.get("jobTitle"))):
            continue
        tried += 1
        st, d = _get(http, f"{_ADP_LIST}/{ident}", headers=hdr, once=True)
        lst = d.get("jobRequisitions") if st == 200 and isinstance(d, dict) else None
        if isinstance(lst, list) and lst and isinstance(lst[0], dict):
            details[ident] = lst[0]
        elif st not in (404, 410):
            break  # the first refusal ends the detail requests; every job stays listed
    jobs: list[Job] = []
    for ident, r in reqs.items():
        try:
            d = details.get(ident, {})
            title = _clean(d.get("jobTitle") or r.get("jobTitle"))
            if not title:
                continue
            place = _adp_place(d) or _adp_place(r)
            jobs.append(_job(owner, "adpmyjobs", title=title, url=f"https://myjobs.adp.com/{slug}/cx/job/{quote(ident, safe='')}",
                             location=place, posted=_iso_date(d.get("postingDate") or r.get("postingDate")),
                             description=_body(html.unescape(str(d.get("jobDescription") or ""))), remote=_remote_from(place),
                             extra={"job_id": ident}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Eploy
# Borrowed from freehire internal/ingest/sources/eploy.go:14-18 (a UK ATS; the board is the careers HOST, often the employer's
# own domain, always also <tenant>.eploy.net), :19-22 and :72-76 (the list is the site's own https://<host>/live-jobs.xml sitemap,
# the whole live inventory in one document, so no postback pager), :63-68 (a board must look like a host: a dot, none of / : ? # @
# or blanks), :78-81 (a sitemap INDEX fails the board: the inventory would be split across documents), :84-103 (a <loc> is https
# and on the board's host or on *.eploy.net, path /vacancies/<id>/<slug>.html, one posting per id), :147-185 (the vacancy page:
# h1 whose id ends h1JobTitle, body div.vac-details__description, schema.org JobPosting ld+json optional with datePosted and
# jobLocation locality / region / country) and career-ops providers/eploy.mjs:44-60 (https only, no userinfo, port, query or
# fragment on a <loc>), :73-97 and :101-123 (a title read from the slug when the page is not opened: "--" splits segments joined
# by " - ", dashes become spaces, a few acronyms upper-cased), :17-19 (lastmod is a modification time, never the posted date).
_EPLOY_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{0,120}\.[a-z]{2,24}")
_EPLOY_PATH = re.compile(r"/vacancies/(\d{1,12})/([^/?#]+)\.html/?")
_EPLOY_ACRONYMS = {"ai": "AI", "api": "API", "cio": "CIO", "cto": "CTO", "eu": "EU", "hcm": "HCM", "hr": "HR", "it": "IT",
                   "ml": "ML", "qa": "QA", "uk": "UK", "ui": "UI", "ux": "UX"}


def _eploy_host(token: str) -> str:
    h = token.strip().lower()
    bad = h in ("localhost", "localhost.localdomain") or h.endswith((".local", ".internal", ".localhost"))
    return "" if bad or not _EPLOY_HOST.fullmatch(h) else h


def _eploy_title(slug: str) -> str:
    out: list[str] = []
    for seg in re.split(r"-{2,}", unquote(slug)):
        words = [_EPLOY_ACRONYMS.get(w.lower(), w[:1].upper() + w[1:]) for w in re.sub(r"[-_\s]+", " ", seg).split()]
        if words:
            out.append(" ".join(words))
    return _clean(" - ".join(out))


def _eploy_loc(loc: str, host: str) -> tuple[str, str, str] | None:
    """(vacancy id, url, slug title) for a sitemap <loc> on the board's host or a *.eploy.net host, else None."""
    try:
        u = urlsplit(loc.strip())
        h = (u.hostname or "").lower()
        port = u.port
    except ValueError:
        return None
    if u.scheme != "https" or u.username is not None or u.password is not None or port is not None or u.query or u.fragment:
        return None
    if h != host and not (h.endswith(".eploy.net") and h != "eploy.net"):
        return None
    m = _EPLOY_PATH.fullmatch(u.path)
    title = _eploy_title(m.group(2)) if m else ""
    return (m.group(1), f"https://{h}{u.path}", title) if m and title else None


def _eploy_detail(page: str) -> dict[str, str]:
    """title / location / posted / description of a vacancy page: the JobPosting ld+json when present, else the page's own markup."""
    root = _tree(page)
    ld = _ld_posting(root) or {}
    title = _clean(ld.get("title"))
    desc = _body(ld.get("description"))
    if not title or not desc:
        read_markup = not desc  # only the FIRST description block is read: re-reading each nested one is quadratic
        for n in _walk(root):
            if not title and n.tag == "h1" and n.attrs.get("id", "").endswith("h1JobTitle"):
                title = n.text()
            elif read_markup and n.tag == "div" and n.has_class("vac-details__description"):
                read_markup = False
                desc = _body(n.inner_html())
    return {"title": title, "description": desc, "location": _ld_places(ld, region=True) if ld else "",
            "posted": _iso_date(ld.get("datePosted"))}


def _eploy(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Eploy careers site: the live-jobs.xml sitemap lists every vacancy; pages are opened only for passing titles."""
    host = _eploy_host(str(row.get("token") or ""))
    if not host:
        return None
    st, xml = _get(http, f"https://{host}/live-jobs.xml", as_json=False, headers=_XML, max_bytes=_SITEMAP_MAX)
    if st != 200 or not isinstance(xml, str):
        return None
    if not xml.strip():
        return []  # an empty body is an empty board
    root = safe_fromstring(xml)
    if root is None or str(root.tag).rsplit("}", 1)[-1] != "urlset":
        return None  # a login page, a template change or a sitemap INDEX must not read as zero vacancies
    items: dict[str, tuple[str, str]] = {}
    for loc in _sitemap_locs(root, "url"):
        try:
            hit = _eploy_loc(loc, host)
            if hit and hit[0] not in items:
                items[hit[0]] = (hit[1], hit[2])
        except Exception:  # noqa: BLE001, S112 - one bad <loc> must not lose the board
            continue
    pages = _open_details(row, http, [(u, t) for u, t in items.values()], DETAIL_CAP)
    jobs: list[Job] = []
    for ident, (url, slug_title) in items.items():
        try:
            d = _eploy_detail(pages[url]) if url in pages else {}
            place = d.get("location", "")
            jobs.append(_job(row, "eploy", title=d.get("title") or slug_title, url=url, location=place, posted=d.get("posted", ""),
                             description=d.get("description", ""), remote=_remote_from(place), extra={"job_id": ident}))
        except Exception:  # noqa: BLE001, S112 - one bad vacancy page must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "adpmyjobs": _wrap(_adpmyjobs),
    "eploy": _wrap(_eploy),
}
