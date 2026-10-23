"""Marketplace and recruiting-site readers for the hunt kit, batch 5 (branch wave3/boards5): GeekHunter, Likeit, Strider (onstrider), micro1 and
Alignerr. Same contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the
``http`` callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  geekhunter  the company's GeekHunter slug (``omega-gestao-educacional-ltda`` for geekhunter.com/pt/<slug>/jobs)
  likeit      the tenant subdomain of <tenant>.likeit.fi (``rekrymesta``)
  onstrider   any word (``onstrider:all``): Strider is ONE site, the employer is hidden behind "Strider"
  micro1      any word (``micro1:all``): jobs.micro1.ai is ONE board
  alignerr    any word (``alignerr:all``): www.alignerr.com is ONE site

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only. Posting pages are opened only for cards whose title passes the run's
``title_ok`` test, up to 25 per board, and the first refusal ends a board's detail requests. Likeit carries every posting in
its feed, so it opens nothing. A posting page the kit did not open is not listed, except Alignerr (its listing already
names title and location of each active posting).

NOT built (written up in README and NOTICE):
  Energy Jobline (freehire energyjobline.go; http.go:388-396 as read in kit_sites_b): its ad pages answer only a client that
  calls itself Googlebot. The kit never pretends to be someone else, so energyjobline.com is reported as unsupported.
Referral links: the references rewrite the outbound link of onstrider, micro1 and alignerr with the operator's own referral
code (onstrider.go:166-185, micro1.go:183-205, alignerr.go:45-55). The kit has no such code and never invents one: it
lists the plain posting page.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, quote, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    _FEED_MAX,
    _SITEMAP_MAX,
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _label,
    _ld_places,
    _ld_posting,
    _next_data,
    _open_details,
    _page_st,
    _remote_from,
    _rfc822_date,
    _sitemap_locs,
    _slug,
    _tree,
    _walk,
    _words,
    _wrap,
    safe_fromstring,
)
from kit_sites_a import (  # type: ignore[import-not-found]  # noqa: E402  # sibling file, not a package module
    _bracket,
    _decode_flight,
    _flight_rows,
)

_XML = {"Accept": "application/xml, text/xml, application/rss+xml"}

DETAIL_CAP = 25  # detail requests per board (the references open every posting)


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is a plain https link on exactly ``host`` (no userinfo, no odd port), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme != "https" or (p.hostname or "").lower() != host or p.username is not None or p.password is not None:
            return ""
        return p._replace(fragment="").geturl() if p.port in (None, 443) else ""
    except ValueError:
        return ""


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _strs(value: Any) -> list[str]:
    """A schema.org value that is one string or a list of them -> list of non-empty strings."""
    return [str(x) for x in (value if isinstance(value, list) else [value]) if isinstance(x, str) and x]


# --------------------------------------------------------------------------- GeekHunter
# Borrowed from freehire internal/ingest/sources/geekhunter.go:31 and :40 (board = the company's slug; the listing is
# https://www.geekhunter.com/pt/<slug>/jobs), :33-37 and :39-43 (the board page inlines every open posting in one ItemList
# ld+json block, no paging; a failed listing fails the board), :111-135 (<script id="itemList">, itemListElement[].url), :60-100
# (each posting's own page carries a JobPosting ld+json: title, description with HTML entities, datePosted, employmentType list,
# hiringOrganization.name, jobLocation), :69-76 (a posting with no jobLocation at all is the remote one), :137-147 (the id is
# the last /jobs/<slug> path segment).
_GH_HOST = "www.geekhunter.com"
_GH_PATH = re.compile(r"^/pt/[A-Za-z0-9_.-]{1,120}/jobs/([^/?#]{1,200})/?$")


def _geekhunter(row: dict[str, Any], http: Http) -> list[Job] | None:
    """GeekHunter board: one ItemList ld+json listing, one JobPosting ld+json page per wanted posting."""
    slug = str(row.get("token") or "").strip()
    if not _slug(slug):
        return None
    st, page = _page_st(http, f"https://{_GH_HOST}/pt/{quote(slug, safe='')}/jobs")
    if page is None:
        return None
    found: dict[str, str] = {}
    for n in _walk(_tree(page)):
        if n.tag == "script" and n.attrs.get("id") == "itemList":
            try:
                data = json.loads(n.raw_text(), strict=False)
            except (ValueError, RecursionError):
                return None
            for el in _d(data).get("itemListElement") or []:
                u = _on(str(_d(el).get("url") or ""), _GH_HOST)
                m = _GH_PATH.match(urlsplit(u).path) if u else None
                if m and m.group(1) not in found:
                    found[m.group(1)] = u
            break
    pages = _open_details(row, http, [(u, _words(jid)) for jid, u in found.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for jid, u in found.items():
        try:
            p = _ld_posting(_tree(pages[u])) if u in pages else None
            title = _clean(p.get("title")) if p else ""
            if not p or not title:
                continue
            org = _d(p.get("hiringOrganization"))
            place = _ld_places(p, region=True)
            remote = True if not p.get("jobLocation") else _remote_from(place)  # geekhunter.go:70-75
            et = _strs(p.get("employmentType"))
            j = _job(row, "geekhunter", title=title, url=u, location=place, posted=_iso_date(p.get("datePosted")),
                     description=_body(html.unescape(str(p.get("description") or ""))), remote=remote,
                     extra={"job_id": jid, "employment_type": et[0] if et else ""})
            if _clean(org.get("name")):
                j["company"] = _clean(org.get("name"))
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Likeit
# Borrowed from freehire internal/ingest/sources/likeit.go:11-18 (the board is the tenant subdomain <board>.likeit.fi), :46 (the
# keyless feed https://<board>.likeit.fi/jsp/duuni/AdvertRss.jsp carries every posting inline), :28-44 (item title, link,
# description = escaped HTML body, category = the location, pubDate, dc:date), :55-58 and :74-83 (id from the link's ?id= query;
# an item without one, such as the channel's AdvertList link, is skipped), :85-91 (dc:date RFC 3339 first, else pubDate).
def _likeit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Likeit tenant: one RSS feed, the full posting inline; no detail request."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    host = f"{board}.likeit.fi"
    st, xml = _get(http, f"https://{host}/jsp/duuni/AdvertRss.jsp", as_json=False, headers=_XML, max_bytes=_FEED_MAX)
    root = safe_fromstring(xml) if st == 200 and isinstance(xml, str) else None
    if root is None:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for item in root.iter():
        if item.tag.rsplit("}", 1)[-1] != "item":
            continue
        try:
            f = {c.tag.rsplit("}", 1)[-1]: (c.text or "") for c in item}
            url = _on(f.get("link", ""), host)
            ids = parse_qs(urlsplit(url).query).get("id") if url else None
            jid = (ids or [""])[0].strip()
            title = _clean(f.get("title"))
            if not url or not jid or not title or jid in seen:
                continue
            seen.add(jid)
            place = _clean(f.get("category"))
            jobs.append(_job(row, "likeit", title=title, url=url, location=place,
                             posted=_iso_date(f.get("date")) or _rfc822_date(f.get("pubDate")),
                             description=_body(f.get("description")), remote=_remote_from(place), extra={"job_id": jid}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Strider (onstrider)
# Borrowed from freehire internal/ingest/sources/onstrider.go:12-27 (Strider's careers site is HubSpot-hosted and boardless; the
# sitemap names every vacancy, each page carries a JobPosting ld+json, the employer is always "Strider"), :43 (the sitemap is
# https://www.onstrider.com/sitemap.xml), :124-131 (only canonical https://www.onstrider.com/jobs/<slug>-<8 hex> URLs: the /pt/
# and /en/ copies and the /preview-slug-<uuid>/ duplicates are not vacancies), :133-158 (a vacancy is open only when the page's
# data-position-open attribute is "1"; the visible "Accepting applications" / "Closed" labels are both always in the markup, and
# a page without the attribute counts as closed), :106-109 (a posting without identifier.value is skipped; jobLocationType
# TELECOMMUTE = remote), :119 and :240-257 (employmentType is one string or a list), :215-226 and :228-238 (location = the
# applicantLocationRequirements country names, a two-letter code spelled out so "CO" is Colombia, not Colorado).
_OS_HOST = "www.onstrider.com"
_OS_PATH = re.compile(r"^/jobs/([a-z0-9-]+)-[0-9a-f]{8}/?$")
_OS_COUNTRIES = {
    "AR": "Argentina", "BO": "Bolivia", "BR": "Brazil", "CL": "Chile", "CO": "Colombia", "CR": "Costa Rica", "CU": "Cuba",
    "DO": "Dominican Republic", "EC": "Ecuador", "GT": "Guatemala", "HN": "Honduras", "MX": "Mexico", "NI": "Nicaragua",
    "PA": "Panama", "PE": "Peru", "PR": "Puerto Rico", "PY": "Paraguay", "SV": "El Salvador", "UY": "Uruguay", "VE": "Venezuela",
}


def _sitemap(http: Http, url: str) -> list[str] | None:
    st, xml = _get(http, url, as_json=False, headers=_XML, max_bytes=_SITEMAP_MAX)
    root = safe_fromstring(xml) if st == 200 and isinstance(xml, str) else None
    return None if root is None else _sitemap_locs(root, "url")


def _onstrider(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Strider: sitemap of vacancy URLs, one JobPosting ld+json page per wanted vacancy, open ones only."""
    locs = _sitemap(http, f"https://{_OS_HOST}/sitemap.xml")
    if locs is None:
        return None
    found: dict[str, str] = {}
    for loc in locs:
        u = _on(loc, _OS_HOST)
        m = _OS_PATH.match(urlsplit(u).path) if u else None
        if m and u not in found:
            found[u] = _words(m.group(1))
    pages = _open_details(row, http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    for u in found:
        try:
            if u not in pages:
                continue
            tree = _tree(pages[u])
            flag = next((n.attrs["data-position-open"] for n in _walk(tree) if "data-position-open" in n.attrs), "")
            p = _ld_posting(tree)
            ident = _d(p.get("identifier")).get("value") if p else None
            title = _clean(p.get("title")) if p else ""
            if flag != "1" or not p or not title or not isinstance(ident, str) or not ident.strip():
                continue  # closed (or no longer saying so), no JobPosting, or no native id
            reqs = p.get("applicantLocationRequirements")
            raw = [_clean(_d(x).get("name")) for x in reqs] if isinstance(reqs, list) else []
            names = [_OS_COUNTRIES.get(c.upper(), c) for c in raw if c]
            et = _strs(p.get("employmentType"))
            remote = True if str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE" else None
            jobs.append(_job(row, "onstrider", title=title, url=u, location=", ".join(names), posted=_iso_date(p.get("datePosted")),
                             description=_body(p.get("description")), remote=remote,
                             extra={"job_id": ident.strip(), "employment_type": et[0] if et else ""}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- micro1
# Borrowed from freehire internal/ingest/sources/micro1.go:13-23 (jobs.micro1.ai is boardless; the sitemap names every posting,
# each page inlines its payload in the Next.js flight stream, there is no ld+json), :39 (https://jobs.micro1.ai/sitemap.xml),
# :154-166 (only canonical https://jobs.micro1.ai/post/<uuid> URLs), :222-233 and :211-220 (the object after
# `"data":{"client_job_id"` holds client_job_id = the id, job_role_name, job_description, job_status, create_datetime,
# location_type, location_name), :96 (no id = skipped), :101 (a status other than "open" is skipped), :105-110 (job_description is
# a "$N" reference to the stream's text row N), :128-138 (location_type remote / hybrid / onsite). The reference's skill tagging
# (:140-152) and referral link (:183-205) are not read.
_M1_HOST = "jobs.micro1.ai"
_M1_PATH = re.compile(r"^/post/([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})/?$")


def _micro1(row: dict[str, Any], http: Http) -> list[Job] | None:
    """micro1: sitemap of post URLs, one flight-stream page per wanted posting, open ones only."""
    locs = _sitemap(http, f"https://{_M1_HOST}/sitemap.xml")
    if locs is None:
        return None
    found: dict[str, str] = {}
    for loc in locs:
        u = _on(loc, _M1_HOST)
        m = _M1_PATH.match(urlsplit(u).path) if u else None
        if m and u not in found:
            found[u] = ""
    pages = _open_details(row, http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    for u in found:
        try:
            if u not in pages:
                continue
            flight = _decode_flight(pages[u])
            data = json.loads(_bracket(flight, '"data":{"client_job_id"', "{", "}") or "null", strict=False)
            d = _d(data)
            ident, title = str(d.get("client_job_id") or "").strip(), _clean(d.get("job_role_name"))
            status = str(d.get("job_status") or "")
            if not ident or not title or (status and status != "open"):
                continue
            desc = str(d.get("job_description") or "")
            if desc.startswith("$"):
                desc = _flight_rows(flight).get(desc[1:], "")
            mode = str(d.get("location_type") or "").strip().lower()
            place = _clean(d.get("location_name"))
            jobs.append(_job(row, "micro1", title=title, url=u, location=place, posted=_iso_date(d.get("create_datetime")),
                             description=_body(desc), remote=True if mode == "remote" else _remote_from(place, title),
                             extra={"job_id": ident, "work_mode": mode if mode in ("remote", "hybrid", "onsite") else ""}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Alignerr
# Borrowed from freehire internal/ingest/sources/alignerr.go:11-16 and :63 (www.alignerr.com is boardless; the /jobs page is a
# Next.js page whose __NEXT_DATA__ holds every active posting), :67-85 (props.pageProps.initialJobs[]: id, title, location),
# :64 and :129 (the detail page https://www.alignerr.com/jobs/<id> holds props.pageProps.job), :87-96 (job: name,
# htmlLongDescription, shortDescription, isActive, firstPostDate, createdAt, jobType, location), :143 (isActive false = closed),
# :147-152 (long description, else the short one), :155 (the listing's location, else the job's), :169-190 (jobType FULL_TIME /
# PART_TIME / CONTRACT / INTERN). A posting whose page was not opened is listed from the listing alone (title and location).
_AL_HOST = "www.alignerr.com"
_AL_ID = re.compile(r"[A-Za-z0-9_-]{1,80}")
_AL_TYPES = {"FULL_TIME": "full_time", "FULLTIME": "full_time", "PART_TIME": "part_time", "PARTTIME": "part_time",
             "CONTRACT": "contract", "CONTRACTOR": "contract", "TEMPORARY": "contract", "INTERN": "internship",
             "INTERNSHIP": "internship"}


def _alignerr(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Alignerr: the listing's __NEXT_DATA__, one detail page per wanted posting (closed ones dropped)."""
    st, page = _page_st(http, f"https://{_AL_HOST}/jobs")
    data = _next_data(page) if page else None
    items = _d(_d(_d(data).get("props")).get("pageProps")).get("initialJobs")
    if not isinstance(items, list):
        return None
    cards: dict[str, dict[str, str]] = {}
    for it in items:
        jid = str(_d(it).get("id") or "").strip()
        if _AL_ID.fullmatch(jid) and jid not in cards:
            cards[jid] = {"title": _clean(_d(it).get("title")), "location": _clean(_d(it).get("location")),
                          "url": f"https://{_AL_HOST}/jobs/{jid}"}
    pages = _open_details(row, http, [(c["url"], c["title"]) for c in cards.values()], DETAIL_CAP)
    jobs: list[Job] = []
    for jid, c in cards.items():
        try:
            job: dict[str, Any] = {}
            if c["url"] in pages:
                job = _d(_d(_d(_d(_next_data(pages[c["url"]])).get("props")).get("pageProps")).get("job"))
                if job and job.get("isActive") is not True:
                    continue  # alignerr.go:127-130
            title = _clean(job.get("name")) or c["title"]
            if not title or (c["url"] in pages and not job):
                continue
            place = c["location"] or _clean(job.get("location"))
            kind = _AL_TYPES.get(str(job.get("jobType") or "").strip().upper(), "")
            jobs.append(_job(row, "alignerr", title=title, url=c["url"], location=place,
                             posted=_iso_date(job.get("firstPostDate")) or _iso_date(job.get("createdAt")),
                             description=_body(job.get("htmlLongDescription")) or _body(job.get("shortDescription")),
                             remote=_remote_from(place), extra={"job_id": jid, "employment_type": kind}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "alignerr": _wrap(_alignerr),
    "geekhunter": _wrap(_geekhunter),
    "likeit": _wrap(_likeit),
    "micro1": _wrap(_micro1),
    "onstrider": _wrap(_onstrider),
}
