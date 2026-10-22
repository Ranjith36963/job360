"""Generic applicant-tracking platforms for the hunt kit, part 3: Radancy (TalentBrew) and UKG Ready. Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  radancy             the career-site host (``careers.ing.com``), optionally ``host/<lang>`` (default ``en``)
  ukgready            ``<pod host>/<tenant>`` (``secure4.saashr.com/6162397``); NOT UKG Pro Recruiting (``ultipro``)

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. Posting pages are opened only for cards whose title passes the run's
``title_ok`` test, up to 25 per board, and the first refusal ends a board's detail requests.

NOT built (written up in README and NOTICE):
  HumanBit (freehire humanbit.go:9-18, :37-44): the board's listing page is a Next.js flight stream of about 5.6 MB (the
  reference measures it, :14-16) and the kit refuses a page over 2 MB; its fields also live on two page shapes read through
  the flight primitives. A jobs.humanbit.ai link is reported as unsupported (hunt_kit.board_from_job_url).
"""
# ruff: noqa: E501
from __future__ import annotations

import re
import sys
import uuid
from datetime import date
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    MAX_PAGES,
    Http,
    Job,
    _body,
    _get,
    _job,
    _join,
    _ld_posting,
    _Node,
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
_XML = {"Accept": "application/xml, text/xml, application/rss+xml"}
_XHR = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}

DETAIL_CAP = 25  # detail requests per board (the kit's own cap; the references open every posting)
UKG_PAGE = 200  # ukgready.go:78-80 the API maximum


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("�", "").split())


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is https on exactly ``host``, else ''."""
    try:
        p = urlsplit(url.strip())
        return p._replace(fragment="").geturl() if p.scheme == "https" and (p.hostname or "").lower() == host else ""
    except ValueError:
        return ""


def _outer(root: _Node, pred: Callable[[_Node], bool]) -> list[_Node]:
    """Nodes matching ``pred`` in document order, not looking inside a match (so hostile nesting stays linear)."""
    out: list[_Node] = []
    stack = [iter(root.kids)]
    while stack:
        for k in stack[-1]:
            if isinstance(k, _Node):
                if pred(k):
                    out.append(k)
                else:
                    stack.append(iter(k.kids))
                    break
        else:
            stack.pop()
    return out


# --------------------------------------------------------------------------- Radancy (TalentBrew)
# Borrowed from career-ops providers/radancy.mjs:6-22 and :195-199 (the board is a career-site host; list at
# https://<host>/<lang>/search-jobs, lang defaults to en), :86-108 and :114-135 (the JSON fragment endpoint
# <list>/results?...&RecordsPerPage=100 with SearchResultsModuleName="Search Results" and NO SearchFiltersModuleName, which would
# re-attach a multi-megabyte facet blob; header X-Requested-With; a random "_" cache-buster per request, :64-76), :140-152
# (data-total-pages bounds the walk), :199-203 (modern card: li.search-results-list__item, a.search-results-list__job-link,
# data-job-id, the location span in __job-info--location), :158-190 (legacy card: a[data-job-id] with a /job/ href, title in the
# heading, location in .job-location inside the anchor or right after it), :331-337 (stop on an empty page or a page that adds
# nothing new) and freehire internal/ingest/sources/radancy.go:11-14 and :31-47 (fallback: https://<host>/sitemap.xml keeps the
# locs with a /job/ segment and a trailing numeric id; every job is read from its page), :53-77 (JobPosting ld+json: title,
# hiringOrganization.name, jobLocation[0].address locality/region/country, jobLocationType TELECOMMUTE = remote), :103-111 (a
# purely numeric addressRegion is dropped), :113-125 (id = last numeric path segment), :61 (datePosted is unpadded "2026-6-17").
# A custom-domain career site shows no vendor host, so a pasted link is matched by its /job/<city>/<slug>/<n>/<id> path.
_RADANCY_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")
_RADANCY_LANG = re.compile(r"[a-z]{2}(?:-[a-z]{2})?")
_RADANCY_TOTAL = re.compile(r'data-total-pages="(\d+)"')
_CARD_CLS = "search-results-list__item"


def _radancy_target(token: str) -> tuple[str, str] | None:
    parts = [p for p in token.strip().lower().split("/") if p]
    if len(parts) not in (1, 2) or not _RADANCY_HOST.fullmatch(parts[0]):
        return None
    lang = parts[1] if len(parts) == 2 else "en"
    return (parts[0], lang) if _RADANCY_LANG.fullmatch(lang) else None


def _radancy_cards(frag: str, host: str) -> list[dict[str, str]]:
    """The postings of one results fragment (modern or legacy markup); a card with a bad or foreign href is skipped."""
    root = _tree(frag)
    out: list[dict[str, str]] = []

    def card(link: _Node, loc: str, ident: str) -> None:
        try:
            u = _on(urljoin(f"https://{host}/", link.attrs.get("href", "")), host)
            title = _clean(next((h.text() for h in _walk(link) if h.tag in ("h1", "h2", "h3", "h4", "h5", "h6")), "") or link.text())
            nums = re.findall(r"/(\d+)(?=[/?#]|$)", urlsplit(u).path)
            ident = ident or (nums[-1] if nums else "")
            if u and title and ident and "/job/" in urlsplit(u).path:
                out.append({"id": ident, "title": title, "location": _clean(loc), "url": urlsplit(u)._replace(query="").geturl()})
        except ValueError:
            return
    modern = _outer(root, lambda n: n.tag == "li" and any(c.startswith(_CARD_CLS) for c in n.attrs.get("class", "").split()))
    for li in modern:
        link = next((a for a in _walk(li) if a.tag == "a" and any(c.startswith("search-results-list__job-link") for c in a.attrs.get("class", "").split())), None)
        loc = next((n for n in _walk(li) if any("__job-info--location" in c for c in n.attrs.get("class", "").split())), None)
        if link is not None:
            span = next((s for s in _walk(loc) if s.tag == "span"), loc) if loc is not None else None
            card(link, span.text() if span is not None else "", link.attrs.get("data-job-id", "") or li.attrs.get("data-job-id", ""))
    if not out:  # legacy markup: the anchor is the row; the location sits inside it or right after it
        for parent in (root, *_walk(root)):
            for i, a in enumerate(parent.kids):
                if not (isinstance(a, _Node) and a.tag == "a" and a.attrs.get("data-job-id") and "/job/" in a.attrs.get("href", "")):
                    continue
                place = next((n for n in _walk(a) if "job-location" in n.attrs.get("class", "").split()), None)
                if place is None:  # a bounded look at the siblings after the anchor (Barclays-style cards)
                    place = next((s for s in parent.kids[i + 1:i + 5] if isinstance(s, _Node) and "job-location" in s.attrs.get("class", "").split()), None)
                card(a, place.text() if place is not None else "", a.attrs["data-job-id"])
    uniq: dict[str, dict[str, str]] = {}
    for c in out:
        uniq.setdefault(c["id"], c)
    return list(uniq.values())


def _radancy_fragment_url(host: str, lang: str, page: int) -> str:
    q = {"ActiveFacetID": "0", "CurrentPage": str(page), "RecordsPerPage": "100", "Distance": "50", "RadiusUnitType": "0",
         "Keywords": "", "Location": "", "ShowRadius": "False", "IsPagination": "True", "CustomFacetName": "", "FacetTerm": "",
         "FacetType": "0", "SearchResultsModuleName": "Search Results", "SortCriteria": "0", "SortDirection": "0",
         "SearchType": "5", "_": uuid.uuid4().hex}
    return f"https://{host}/{lang}/search-jobs/results?{urlencode(q)}"


def _radancy_date(value: Any) -> str:
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:[T ].*)?", str(value or "").strip())
    try:
        return date(int(m.group(1)), int(m.group(2)), int(m.group(3))).isoformat() if m else ""
    except ValueError:
        return ""


def _radancy_place(ld: dict[str, Any]) -> str:
    locs = ld.get("jobLocation")
    first = (locs[0] if isinstance(locs, list) and locs else locs)
    a = first.get("address") if isinstance(first, dict) else None
    if not isinstance(a, dict):
        return ""
    region = str(a.get("addressRegion") or "").strip()
    country = a.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    return str(_join(a.get("addressLocality"), "" if region.isdigit() else region, country))


def _radancy(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Radancy / TalentBrew career site: JSON results fragment (cards), else sitemap.xml; JobPosting pages for wanted jobs."""
    target = _radancy_target(str(row.get("token") or ""))
    if target is None:
        return None
    host, lang = target
    cards: dict[str, dict[str, str]] = {}
    last = MAX_PAGES
    for page in range(1, MAX_PAGES + 1):
        if page > last:
            break
        st, data = _get(http, _radancy_fragment_url(host, lang, page), headers=_XHR)
        frag = data.get("results") if st == 200 and isinstance(data, dict) else None
        if not isinstance(frag, str):
            break
        if page == 1:
            m = _RADANCY_TOTAL.search(frag)
            last = min(int(m.group(1)), MAX_PAGES) if m else MAX_PAGES
        fresh = 0
        for c in _radancy_cards(frag, host):
            if c["id"] not in cards:
                cards[c["id"]] = c
                fresh += 1
        if not fresh:
            break
    items: list[tuple[str, str]]
    if cards:
        items = [(c["url"], c["title"]) for c in cards.values()]
    else:  # freehire radancy.go: the sitemap names the job pages; the slug before the two numbers is only the title test
        st, xml = _get(http, f"https://{host}/sitemap.xml", as_json=False, headers=_XML)
        root = safe_fromstring(xml) if st == 200 and isinstance(xml, str) else None
        if root is None:
            return None
        items = []
        for loc in _sitemap_locs(root, "url"):
            u = _on(loc, host)
            segs = [s for s in urlsplit(u).path.split("/") if s]
            if u and "job" in segs and segs.index("job") <= len(segs) - 3 and segs[-1].isdigit():
                slug = segs[-3] if len(segs) - 3 > segs.index("job") else segs[-2]
                items.append((u, slug.replace("-", " ").replace("_", " ")))
                cards[segs[-1]] = {"id": segs[-1], "title": "", "location": "", "url": u}
    pages = _open_details(row, http, items, DETAIL_CAP)
    jobs: list[Job] = []
    for c in cards.values():
        try:
            page = pages.get(c["url"])
            ld = _ld_posting(_tree(page)) if page else None
            org = ld.get("hiringOrganization") if ld else None
            title = _clean(ld.get("title")) if ld else ""
            title = title or c["title"]
            if not title or (not c["title"] and ld is None):
                continue  # a sitemap entry whose page was not read has no title (radancy.go: skipped)
            place = _radancy_place(ld) if ld else ""
            place = place or c["location"]
            remote = True if ld and ld.get("jobLocationType") == "TELECOMMUTE" else _remote_from(place, title)
            j = _job(row, "radancy", title=title, url=c["url"], location=place, posted=_radancy_date(ld.get("datePosted")) if ld else "",
                     description=_body(ld.get("description")) if ld else "", remote=remote, extra={"job_id": c["id"]})
            name = _clean(org.get("name")) if isinstance(org, dict) else ""
            if name:
                j["company"] = name
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- UKG Ready
# Borrowed from freehire internal/ingest/sources/ukgready.go:11-16 (UKG Ready is a DIFFERENT product from UKG Pro Recruiting /
# UltiPro, which the kit reads as "ultipro"), :18-35 (a board is "<pod host>/<tenant>"; the host selects the hosting environment
# and one environment is fronted by several white-label hosts: secure*.saashr.com, secure*.entertimeonline.com,
# secure*.yourpayrollhr.com, secure.workforceready.<cc>, *.mykronos.com; tenant ids are case-insensitive), :37-45 and :101-105
# (keyless REST: /ta/rest/ui/recruitment/companies/|<tenant>/job-requisitions[/<id>], the pipe percent-encoded), :112-114 (page
# size 200 is the API maximum), :117-127 (parse "<host>/<tenant>", no third segment), :130-134 (public URL
# https://<host>/ta/<tenant>.careers?ShowJob=<id>), :163-186 (posting fields id, job_title, location city/state/country,
# job_description, job_requirement, is_remote_job, employee_type.name), :265-290 (offset is a 1-BASED PAGE NUMBER, not a row
# offset; _paging.total bounds the walk, an empty page also stops it; a later page failing keeps the earlier ones) and :292-327
# and :329-340 (the listing description is an ~250-character preview; the detail holds the body = job_description +
# job_requirement). The reference restricts no host; the kit accepts only the vendor families named above. NOT taken: pay figures
# and the tenant currency request (:344-365), employment-type vocabulary. The reference skips a posting whose detail failed;
# the kit keeps the listing row with its preview.
_UKG_HOST = re.compile(r"(?:[a-z0-9-]+\.)*(?:saashr\.com|entertimeonline\.com|yourpayrollhr\.com|mykronos\.com|"
                       r"workforceready\.[a-z]{2,3}(?:\.[a-z]{2})?)")
_UKG_TENANT = re.compile(r"[a-z0-9_-]{1,40}")


def _ukg_board(token: str) -> tuple[str, str] | None:
    host, _, tenant = token.strip().lower().partition("/")
    if not _UKG_HOST.fullmatch(host) or not _UKG_TENANT.fullmatch(tenant):
        return None
    return host, tenant


def _ukgready(row: dict[str, Any], http: Http) -> list[Job] | None:
    """UKG Ready career site: keyless job-requisitions REST list (1-based page numbers), detail JSON for wanted rows."""
    board = _ukg_board(str(row.get("token") or ""))
    if board is None:
        return None
    host, tenant = board
    base = f"https://{host}/ta/rest/ui/recruitment/companies/%7C{quote(tenant, safe='')}/job-requisitions"
    rows: dict[int, dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        st, data = _get(http, f"{base}?{urlencode({'offset': page, 'size': UKG_PAGE})}", headers=_JSON)
        batch = data.get("job_requisitions") if st == 200 and isinstance(data, dict) else None
        if not isinstance(batch, list):
            if page > 1:
                break  # a hiccup after page 1 keeps what we have (ukgready.go:275-281)
            return None
        for r in batch:
            rid = r.get("id") if isinstance(r, dict) else None
            if isinstance(rid, int) and not isinstance(rid, bool) and rid > 0 and rid not in rows:
                rows[rid] = r
        paging = data.get("_paging")
        total = paging.get("total") if isinstance(paging, dict) else None
        if not batch or (isinstance(total, int) and len(rows) >= total):
            break
    details: dict[int, dict[str, Any]] = {}
    tried = 0
    for rid, r in rows.items():
        title = _clean(r.get("job_title"))
        if tried >= DETAIL_CAP:
            break
        if not title or not _title_ok(row, title):
            continue
        tried += 1
        st, d = _get(http, f"{base}/{rid}", headers=_JSON, once=True)
        if st in (404, 410):
            continue
        if st != 200:
            break  # the first refusal ends the detail requests
        if isinstance(d, dict):
            details[rid] = d
    jobs: list[Job] = []
    for rid, r in rows.items():
        try:
            title = _clean(r.get("job_title"))
            if not title:
                continue
            raw_loc = r.get("location")
            loc: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
            place = _join(*(_clean(loc.get(k)) for k in ("city", "state", "country")))
            d = details.get(rid, r)
            desc = _body(str(d.get("job_description") or "") + str(d.get("job_requirement") or ""))
            et = r.get("employee_type")
            jobs.append(_job(row, "ukgready", title=title, url=f"https://{host}/ta/{tenant}.careers?ShowJob={rid}", location=place,
                             description=desc, remote=True if r.get("is_remote_job") is True else _remote_from(place),
                             extra={"job_id": str(rid), "employment_type": _clean(et.get("name")) if isinstance(et, dict) else ""}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "radancy": _wrap(_radancy),
    "ukgready": _wrap(_ukgready),
}
