"""Generic applicant-tracking platforms for the hunt kit, part 5: SelfRecruit (selfrecruit.ge), Enlizt (enlizt.me),
Hurma (hurma.work), Compleo (jobs.compleo.app) and Spark (spark.work). Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  selfrecruit  tenant subdomain of <tenant>.selfrecruit.ge
  enlizt       tenant subdomain of <tenant>.enlizt.me
  hurma        tenant subdomain of <tenant>.hurma.work
  compleo      the tenant path segment of jobs.compleo.app/<tenant>/jobdetail/<code>
  spark        workspace slug of <workspace>.spark.work

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only. Where a site gives the posting only on its own page (SelfRecruit, Enlizt,
Compleo) the page is opened for the first 25 postings of a board (the title is not known before the page is read, so the
run's ``title_ok`` test cannot pre-filter them; the references open every posting) and a posting that was not opened is not
listed. Hurma lists a title and opens a posting only when the title passes ``title_ok``; Spark needs no page.
The first refusal ends a board's page requests.

NOT built (written up in README and NOTICE):
  Zoho Recruit (freehire zoho.go, career-ops zoho-recruit.mjs): already read by ``zohorecruit`` in kit_ats_more.py, so it
  is not built a second time. Its ``.eu`` data-region host (zoho.go:15) is not in that reader's tld list (``in``/``com``).
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
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
    _links,
    _Node,
    _open_details,
    _page_st,
    _remote_from,
    _sitemap_locs,
    _title_ok,
    _tree,
    _walk,
    _wrap,
    safe_fromstring,
)

_JSON = {"Accept": "application/json"}
_XHR = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
_XML = {"Accept": "application/xml, text/xml"}

DETAIL_CAP = 25  # page requests per board (the kit's own cap; the references open every posting)
SELFRECRUIT_PAGE = 10  # selfrecruit.go:28-30 the listing's fixed page size
SELFRECRUIT_PAGES = 40  # selfrecruit.go:32-36 bounds the walk (the reference allows 100)
HURMA_PAGE = 100
HURMA_PAGES = 50
SPARK_PAGE = 100
SPARK_PAGES = 100


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("�", "").split())


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is https on exactly ``host``, else ''."""
    try:
        p = urlsplit(url.strip())
        return p._replace(fragment="").geturl() if p.scheme == "https" and (p.hostname or "").lower() == host else ""
    except ValueError:
        return ""


def _tenant(row: dict[str, Any]) -> str:
    t = str(row.get("token") or "").strip().lower()
    return t if _label(t) else ""


def _no_filter(row: dict[str, Any]) -> dict[str, Any]:
    """The row without its ``title_ok`` test: for boards whose listing carries no title to test."""
    return {k: v for k, v in row.items() if k != "title_ok"}


def _path_match(rx: re.Pattern[str], base: str, href: str) -> re.Match[str] | None:
    try:
        return rx.match(urlsplit(urljoin(base, href)).path)
    except ValueError:
        return None


# --------------------------------------------------------------------------- SelfRecruit
# Borrowed from freehire internal/ingest/sources/selfrecruit.go:12-18 (selfrecruit.ge, a multi-tenant ATS; the board is the
# tenant subdomain; no JSON API and no ld+json), :27-36 (the listing is paged by OFFSET, /vacancies/<offset> in steps of 10; a page
# that adds no new posting link ends the walk), :41-58 (page links come from the tenant root), :64-90 (title in the element with
# class vacancy_title_inner, description in pub_vac_text_detail; a page without both is unreadable) and :100-118 (a posting link
# is a bare UUID path at the tenant root: /<uuid>, so /cv, /articles/<uuid> and /vacancies/<n> are not postings).
_SR_ID = re.compile(r"^/([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12})/?$")


def _first_class(root: _Node, cls: str) -> _Node | None:
    return next((n for n in _walk(root) if n.has_class(cls)), None)


def _selfrecruit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """SelfRecruit tenant: paged offset listing of posting links, then each posting page (title and description by class)."""
    tenant = _tenant(row)
    if not tenant:
        return None
    host = f"{tenant}.selfrecruit.ge"
    base = f"https://{host}/"
    found: dict[str, str] = {}
    for page in range(SELFRECRUIT_PAGES):
        _, text = _page_st(http, f"{base}vacancies/{page * SELFRECRUIT_PAGE}")
        if text is None:
            if page == 0:
                return None
            break
        fresh = 0
        for url, txt in _links(_tree(text), base, host, lambda h: bool(_path_match(_SR_ID, base, h))).items():
            if url not in found:
                found[url] = txt
                fresh += 1
        if not fresh:
            break
    pages = _open_details(_no_filter(row), http, [(u, t) for u, t in found.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for url in found:
        try:
            if url not in pages:
                continue
            root = _tree(pages[url])
            t, d = _first_class(root, "vacancy_title_inner"), _first_class(root, "pub_vac_text_detail")
            title = _clean(t.text()) if t is not None else ""
            m = _SR_ID.match(urlsplit(url).path)
            if not title or d is None or not m:
                continue  # selfrecruit.go:64-90: a page without both elements is unreadable
            jobs.append(_job(row, "selfrecruit", title=title, url=url, description=_body(d.inner_html()), remote=_remote_from(title),
                             extra={"job_id": m.group(1)}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Enlizt
# Borrowed from freehire internal/ingest/sources/enlizt.go:11-14 and :26-27 (<tenant>.enlizt.me; the root page lists /vagas/<slug>
# postings), :52-63 (each detail page carries a schema.org JobPosting ld+json; a page without one is dropped), :64-77 (id =
# identifier.value, else the slug; location from the single jobLocation Place; jobLocationType TELECOMMUTE = remote; description is
# entity-escaped HTML; datePosted RFC3339; company = hiringOrganization.name, else the row's), :95-106 (a posting link is
# /vagas/<slug> with nothing after the slug, so /vagas/<slug>/apply is not a second posting).
_ENL_VAGA = re.compile(r"^/vagas/([^/]+)/?$")


def _enlizt(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Enlizt career site: the root page lists /vagas/<slug> links; each page's JobPosting ld+json gives the job."""
    tenant = _tenant(row)
    if not tenant:
        return None
    host = f"{tenant}.enlizt.me"
    base = f"https://{host}/"
    _, text = _page_st(http, base)
    if text is None:
        return None
    found = _links(_tree(text), base, host, lambda h: bool(_path_match(_ENL_VAGA, base, h)))
    pages = _open_details(_no_filter(row), http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    for url in found:
        try:
            ld = _ld_posting(_tree(pages[url])) if url in pages else None
            if ld is None:
                continue
            slug = _ENL_VAGA.match(urlsplit(url).path)
            ident = ld.get("identifier")
            ident = _clean(ident.get("value")) if isinstance(ident, dict) else ""
            ident = ident or (slug.group(1) if slug else "")
            title = _clean(ld.get("title"))
            if not title or not ident:
                continue
            place = _ld_places(ld, region=True)
            remote = True if str(ld.get("jobLocationType") or "").upper() == "TELECOMMUTE" else _remote_from(place)
            j = _job(row, "enlizt", title=title, url=url, location=place, posted=_iso_date(ld.get("datePosted")),
                     description=_body(html.unescape(str(ld.get("description") or ""))), remote=remote, extra={"job_id": ident})
            org = ld.get("hiringOrganization")
            name = _clean(org.get("name")) if isinstance(org, dict) else ""
            if name:
                j["company"] = name
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Hurma
# Borrowed from freehire internal/ingest/sources/hurma.go:10-14 (<tenant>.hurma.work, a JSON API behind the career site), :23-27
# and :61-80 (GET https://<tenant>.hurma.work/api/v1/public-vacancies?page=<n>&per_page=100, pages bounded; the first page failing
# fails the board, a later one ends it; stop at meta.last_page, else on a short page), :34-39 (header X-Requested-With:
# XMLHttpRequest, without which the Laravel API answers an empty message), :41-49 (list item: name, public_url, residence,
# work_types; no description), :100-130 (the id is the number after /public-vacancies/ in public_url; detail at
# /api/v1/public-vacancies/<id> under "data"), :132-145 (title loses a leading "#<id> "), :147-164 (description = description,
# then Requirements/Responsibilities/Working conditions sections, then addition), :166-186 (work_types: first Full-time, Part-time,
# Contract/Freelance or Internship token is the employment type; remote is a text heuristic on residence and work_types).
_HURMA_ID = re.compile(r"/public-vacancies/(\d+)/?$")
_HURMA_TAG = re.compile(r"^#\d+\s+")
_HURMA_KINDS = {"full-time": "full_time", "full time": "full_time", "part-time": "part_time", "part time": "part_time",
                "contract": "contract", "freelance": "contract", "internship": "internship", "intern": "internship"}


def _hurma(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Hurma tenant: the paged public-vacancies JSON; a detail record (description) only for titles that pass ``title_ok``."""
    tenant = _tenant(row)
    if not tenant:
        return None
    host = f"{tenant}.hurma.work"
    base = f"https://{host}/api/v1/public-vacancies"
    items: dict[str, dict[str, Any]] = {}
    for page in range(1, HURMA_PAGES + 1):
        st, data = _get(http, f"{base}?{urlencode({'page': page, 'per_page': HURMA_PAGE})}", headers=_XHR)
        rows = data.get("data") if st == 200 and isinstance(data, dict) else None
        if not isinstance(rows, list):
            if page > 1:
                break
            return None
        fresh = 0
        for it in rows:
            m = _HURMA_ID.search(urlsplit(str(it.get("public_url") or "")).path) if isinstance(it, dict) else None
            if m and m.group(1) not in items and _on(str(it.get("public_url")), host):
                items[m.group(1)] = it
                fresh += 1
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        last = meta.get("last_page")
        if (isinstance(last, int) and not isinstance(last, bool) and last > 0 and page >= last) or not rows or (
                not isinstance(last, int) and len(rows) < HURMA_PAGE) or not fresh:
            break
    details: dict[str, dict[str, Any]] = {}
    tried = 0
    for ident, it in items.items():
        if tried >= DETAIL_CAP:
            break
        title = _HURMA_TAG.sub("", _clean(it.get("name")))
        if not title or not _title_ok(row, title):
            continue
        tried += 1
        st, data = _get(http, f"{base}/{ident}", headers=_XHR, once=True)
        rec = data.get("data") if st == 200 and isinstance(data, dict) else None
        if isinstance(rec, dict):
            details[ident] = rec
        elif st not in (404, 410):
            break  # the first refusal ends the detail requests
    jobs: list[Job] = []
    for ident, it in items.items():
        try:
            d = details.get(ident, {})
            title = _HURMA_TAG.sub("", _clean(d.get("name") or it.get("name")))
            if not title:
                continue
            place = _clean(d.get("residence") or it.get("residence"))
            kinds = _clean(d.get("work_types") or it.get("work_types"))
            kind = next((_HURMA_KINDS[p] for p in (x.strip().lower() for x in kinds.split(",")) if p in _HURMA_KINDS), "")
            parts = [("", d.get("description")), ("Requirements", d.get("demand")), ("Responsibilities", d.get("responsibility")),
                     ("Working conditions", d.get("working_conditions")), ("", d.get("addition"))]
            desc = _body("".join((f"<h3>{h}</h3>" if h else "") + str(v) for h, v in parts if isinstance(v, str) and v.strip()))
            jobs.append(_job(row, "hurma", title=title, url=_on(str(it["public_url"]), host), location=place, description=desc,
                             remote=_remote_from(place, kinds), extra={"job_id": ident, "employment_type": kind}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Compleo
# Borrowed from freehire internal/ingest/sources/compleo.go:11-17 (jobs.compleo.app; ONE sitemap for the whole platform, so a board
# is the slice of it under the tenant's path segment), :43-60 (sitemap https://jobs.compleo.app/sitemap.xml; keep the locs that start
# with https://jobs.compleo.app/<tenant>/jobdetail/ — the prefix carries both slashes so "wafx" does not take "wafxpro"), :65-92
# (each job page carries a schema.org JobPosting ld+json; a page without one, or without hiringOrganization.name, is dropped; the
# employer is the posting's, not the row's, because a tenant may be a recruiter), :94-104 (single jobLocation Place; datePosted
# RFC3339; description entity-escaped HTML) and :106-111 (the job code is the last path segment after /jobdetail/; other pages of the
# platform are not postings).
_COMPLEO_HOST = "jobs.compleo.app"
_COMPLEO_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
_COMPLEO_CODE = re.compile(r"^/([A-Za-z0-9_.-]{1,64})/jobdetail/([A-Za-z0-9]+)/?$")


def _compleo(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Compleo tenant: the platform sitemap filtered to this tenant, then each page's JobPosting ld+json."""
    tenant = str(row.get("token") or "").strip()
    if not _COMPLEO_TENANT.fullmatch(tenant):
        return None
    st, xml = _get(http, f"https://{_COMPLEO_HOST}/sitemap.xml", as_json=False, headers=_XML, max_bytes=_SITEMAP_MAX)
    root = safe_fromstring(xml) if st == 200 and isinstance(xml, str) else None
    if root is None:
        return None
    codes: dict[str, str] = {}
    for loc in _sitemap_locs(root, "url"):
        u = _on(loc, _COMPLEO_HOST)
        m = _COMPLEO_CODE.match(urlsplit(u).path) if u else None
        if m and m.group(1) == tenant and u not in codes:
            codes[u] = m.group(2)
    pages = _open_details(_no_filter(row), http, [(u, "") for u in codes], DETAIL_CAP)
    jobs: list[Job] = []
    for url, code in codes.items():
        try:
            ld = _ld_posting(_tree(pages[url])) if url in pages else None
            org = ld.get("hiringOrganization") if ld else None
            name = _clean(org.get("name")) if isinstance(org, dict) else ""
            title = _clean(ld.get("title")) if ld else ""
            if ld is None or not name or not title:
                continue
            place = _ld_places(ld, region=True)
            j = _job(row, "compleo", title=title, url=url, location=place, posted=_iso_date(ld.get("datePosted")),
                     description=_body(html.unescape(str(ld.get("description") or ""))), remote=_remote_from(place, title),
                     extra={"job_id": code})
            j["company"] = name
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Spark
# Borrowed from freehire internal/ingest/sources/spark.go:10-14 and :16-24 (Spark.work career API on hub.spark.work; the board is
# the workspace slug, sent on every request as the header X-workspace; the list carries each posting inline, no detail call;
# GET https://hub.spark.work/api/jobOpenings?pageNumber=<n>&pageSize=100; vacancyStatusId 20 = Active, anything else skipped),
# :39-47 (item: id, name, vacancyStatusId, openDate, fields {numeric field id -> string, some null}), :49-84 (page until pageCount
# or a short page; the first page failing fails the board, a later one ends it; URL https://<workspace>.spark.work/career/job/<id>/
# <name words joined by "-">), :96-109 (the description is the longest field value that carries HTML markup, by content since field
# ids vary per workspace) and :111-120 (work mode: a field value equal to remote / hybrid / on-site / onsite / on site / office).
# NOT IN REFERENCE: a location (the fields are keyed by workspace-specific ids), so it is left empty.
_SPARK_HOST = "hub.spark.work"
_SPARK_MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite", "office": "onsite"}


def _spark(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Spark.work workspace: the paged jobOpenings JSON with a workspace header; the body is inline, no detail request."""
    space = _tenant(row)
    if not space:
        return None
    jobs: list[Job] = []
    seen: set[int] = set()
    for page in range(1, SPARK_PAGES + 1):
        url = f"https://{_SPARK_HOST}/api/jobOpenings?{urlencode({'pageNumber': page, 'pageSize': SPARK_PAGE})}"
        st, data = _get(http, url, headers={**_JSON, "X-workspace": space})
        items = data.get("items") if st == 200 and isinstance(data, dict) else None
        if not isinstance(items, list):
            if page > 1:
                break
            return None
        fresh = 0
        for it in items:
            try:
                ident = it.get("id") if isinstance(it, dict) else None
                if not isinstance(ident, int) or isinstance(ident, bool) or ident in seen:
                    continue
                seen.add(ident)
                fresh += 1
                title = _clean(it.get("name"))
                if it.get("vacancyStatusId") != 20 or not title:
                    continue
                fields = it.get("fields") if isinstance(it.get("fields"), dict) else {}
                vals = [v for v in fields.values() if isinstance(v, str)]
                rich = max((v for v in vals if "<" in v), key=len, default="")
                mode = next((_SPARK_MODES[v.strip().lower()] for v in vals if v.strip().lower() in _SPARK_MODES), "")
                slug = quote("-".join(title.split()), safe="")
                jobs.append(_job(row, "spark", title=title, url=f"https://{space}.spark.work/career/job/{ident}/{slug}",
                                 posted=_iso_date(it.get("openDate")), description=_body(rich), remote=True if mode == "remote" else None,
                                 extra={"job_id": str(ident), "work_mode": mode}))
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        pc = data.get("pageCount")
        if (isinstance(pc, int) and not isinstance(pc, bool) and pc > 0 and page >= pc) or len(items) < SPARK_PAGE or not fresh:
            break
    return jobs


ATS_FETCHERS = {
    "compleo": _wrap(_compleo),
    "enlizt": _wrap(_enlizt),
    "hurma": _wrap(_hurma),
    "selfrecruit": _wrap(_selfrecruit),
    "spark": _wrap(_spark),
}
