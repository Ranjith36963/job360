"""Job-board readers for the hunt kit, wave 4 batch A: Jobindex.dk, Jobbank.dk, hh.ru, Habr Career and Getmatch. Same contract as
kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at
import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  jobindex    the search words sent as ``q`` (``python``, ``python aarhus``): jobindex.dk has no per-employer board
  jobbankdk   the search words sent as ``key`` to the RSS feed of jobbank.dk (Akademikernes Jobbank, not the Canadian jobbank)
  hhru        a numeric hh.ru professional_role id (``96``): hh.ru lists one role at a time
  habrcareer  any word (``habrcareer:all``): career.habr.com is ONE board
  getmatch    any word (``getmatch:all``): getmatch.ru is ONE board

Endpoints, params and field names are adapted from the MIT-licensed freehire adapters and the MIT-licensed AI-Job-Search-Suite
(ajs) skills (see NOTICE.md); the code is original. The container this was written in could not reach any of these hosts, so every
parser is tested on fixtures shaped like the references' data only. Every posting link is built from an id on the board's own host
(or checked to be https on exactly that host) and no link found in a response is ever followed to another host. A refusal
(403/429) ends a board's paging and its detail requests; the first page failing fails the board, a later page keeps what was
gathered. A posting without an employer name is not listed (these boards list many employers). Detail pages are opened only for
cards whose title passes the run's ``title_ok`` test, up to 25 per board (Habr Career and Getmatch); the others list with the
summary the listing carries.

NOT opened: hh.ru vacancy pages (freehire hhru.go:23-26 says they sit behind DDoS-Guard), so an hh.ru posting carries the salary
line the listing holds and no body. Jobindex and Jobbank.dk postings carry no body either (the references read only the listing).
Jobbank.dk may answer with a Cloudflare challenge (jobbank-search SKILL.md:36); the board then fails and the kit does not retry.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote_plus, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    _FEED_MAX,
    MAX_PAGES,
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _ld_posting,
    _open_details,
    _page_st,
    _remote_from,
    _rfc822_date,
    _tree,
    _wrap,
    safe_fromstring,
)
from kit_latam import _json_details, _mode, _pos_id  # type: ignore[import-not-found]  # noqa: E402  # sibling file

DETAIL_CAP = 25  # detail requests per board
_JSON = {"Accept": "application/json"}
_QUERY = re.compile(r"[^\x00-\x1f\x7f]{1,80}")


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _on(url: str, hosts: tuple[str, ...]) -> str:
    """The URL (fragment dropped) when it is a plain https link on exactly one of ``hosts`` (no userinfo, no odd port), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme != "https" or (p.hostname or "").lower() not in hosts or p.username is not None or p.password is not None:
            return ""
        return p._replace(fragment="").geturl() if p.port in (None, 443) else ""
    except ValueError:
        return ""


def _query(row: dict[str, Any]) -> str:
    """The row's search words, else '' (a control character or an over-long token is no query)."""
    q = " ".join(str(row.get("token") or "").split())
    return q if _QUERY.fullmatch(q) else ""


def _json_after(page: str, marker: str) -> Any:
    """The JSON value that starts at the first ``{`` after ``marker`` (raw_decode: linear, stops at the object's end), else None."""
    i = page.find(marker)
    start = page.find("{", i + len(marker)) if i >= 0 else -1
    if start < 0:
        return None
    try:
        return json.JSONDecoder().raw_decode(page, start)[0]
    except (ValueError, RecursionError):
        return None


def _find_key(root: Any, key: str, need: type) -> Any:
    """First value stored under ``key`` of type ``need`` in a JSON tree (breadth first, at most 100000 nodes), else None."""
    queue, i = [root], 0
    while i < len(queue) and i < 100_000:
        node = queue[i]
        i += 1
        if isinstance(node, dict):
            if isinstance(node.get(key), need):
                return node[key]
            queue.extend(v for v in node.values() if isinstance(v, (dict, list)))
        elif isinstance(node, list):
            queue.extend(v for v in node if isinstance(v, (dict, list)))
    return None


# --------------------------------------------------------------------------- Jobindex.dk
# Borrowed from ajs .agents/skills/jobindex-search/cli/src/helpers.ts:1 (BASE_URL https://www.jobindex.dk), commands/search.ts:38-45
# (GET /jobsoegning?q=<words>&page=<n>&jobage=<days>&sort=<score|date>), SKILL.md:55 (20 results a page), helpers.ts:70-90
# (the results are embedded in the page as ``var Stash = {...}``), :97-110 (the object under searchResponse holds hitcount and
# results[]), :113-139 (tid = id, headline = title, company.name or companytext, area or geojson.features[0].properties.title =
# place, firstdate = date) and :135 (the ad is https://www.jobindex.dk/jobannonce/<tid>).
_JI_HOSTS = ("www.jobindex.dk",)
_JI_TID = re.compile(r"[A-Za-z0-9]{1,24}")


def _jobindex(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Jobindex.dk search: the Stash blob of each results page (20 a page), no detail request."""
    q = _query(row)
    if not q:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, MAX_PAGES + 1):
        _, text = _page_st(http, f"https://{_JI_HOSTS[0]}/jobsoegning?q={quote_plus(q)}&page={page}&jobage=9999&sort=date")
        sr = _find_key(_json_after(text, "var Stash = "), "searchResponse", dict) if text else None
        results = _l(_d(sr).get("results"))
        if sr is None or not results:
            if page == 1:
                return None if sr is None else []
            break
        fresh = 0
        for r in results:
            try:
                r = _d(r)
                tid = str(r.get("tid") or "").strip()
                title = _clean(r.get("headline"))
                company = _clean(_d(r.get("company")).get("name")) or _clean(r.get("companytext"))
                if not _JI_TID.fullmatch(tid) or tid in seen:
                    continue
                seen.add(tid)
                fresh += 1
                if not title or not company:
                    continue
                feats = _l(_d(r.get("geojson")).get("features"))
                place = _clean(r.get("area")) or (_clean(_d(_d(feats[0]).get("properties")).get("title")) if feats else "")
                j = _job(row, "jobindex", title=title, url=f"https://{_JI_HOSTS[0]}/jobannonce/{tid}", location=place,
                         posted=_iso_date(r.get("firstdate")), remote=_remote_from(place), extra={"job_id": tid})
                j["company"] = company
                jobs.append(j)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        hits = _d(sr).get("hitcount")
        if not fresh or (isinstance(hits, int) and not isinstance(hits, bool) and len(seen) >= hits):
            break
    return jobs


# --------------------------------------------------------------------------- Jobbank.dk
# Borrowed from ajs .agents/skills/jobbank-search/cli/src/helpers.ts:3 (BASE_URL https://jobbank.dk) and :88-95 (GET /job/rss
# ?key=<words>, one answer of up to 100 items, no paging: SKILL.md:50), :37-60 (item title, description, link, pubDate), :140-176
# (description is "<job type> hos <company>, <place> (Ansøgningsfrist: <date>)"), :178-184 (the link is /job/<id>/<company>/<title>)
# and :99-104 (a 403 with a Cloudflare "just a moment" page means the portal is closed to bots: the kit stops, SKILL.md:36).
_JB_HOSTS = ("jobbank.dk", "www.jobbank.dk")
_JB_ID = re.compile(r"^/job/(\d{1,12})(?:/|$)")
_JB_DESC = re.compile(r"^(.{0,200}?) hos (.{1,200}?)(?:, (.{0,200}?))?(?: \(Ans[øo]gningsfrist: [^()]{0,40}\))?$")


def _jobbankdk(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Jobbank.dk RSS search feed: every posting inline, no detail request."""
    q = _query(row)
    if not q:
        return None
    st, xml = _get(http, f"https://jobbank.dk/job/rss?key={quote_plus(q)}", as_json=False,
                   headers={"Accept": "application/rss+xml, application/xml, text/xml"}, max_bytes=_FEED_MAX)
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
            url = _on(f.get("link", ""), _JB_HOSTS)
            m = _JB_ID.search(urlsplit(url).path) if url else None
            title = _clean(f.get("title"))
            desc = _clean(f.get("description"))
            dm = _JB_DESC.match(desc)
            if not m or not title or not dm or not dm.group(2).strip() or m.group(1) in seen:
                continue
            seen.add(m.group(1))
            place = (dm.group(3) or "").strip()
            j = _job(row, "jobbankdk", title=title, url=url, location=place, posted=_rfc822_date(f.get("pubDate")),
                     description=desc, remote=_remote_from(place), extra={"job_id": m.group(1), "employment_type": dm.group(1).strip()})
            j["company"] = dm.group(2).strip()
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- hh.ru
# Borrowed from freehire internal/ingest/sources/hhru.go:61-66 (GET https://hh.ru/search/vacancy?professional_role=<id>
# &items_on_page=100&page=<n>&order_by=publication_time&period=7), :19-22 and :263-273 (the vacancy list is JSON inside
# <template id="HH-Lux-InitialState">), :73-99 (vacancySearchResult.vacancies[]: vacancyId, name, @isAdv, area.name,
# company.visibleName or name, compensation from/to/currencyCode, workFormats[].workFormatsElement, publicationTime.$),
# :165-171 (promoted @isAdv vacancies are skipped), :172-186 (a page that adds no new vacancy ends the walk; the search is
# 20 pages deep at most), :213-218 (visibleName carries ``Employer::org path``: only the employer is kept) and :237-247 and
# :249-262 (the work format and the salary line).
_HH_HOST = "hh.ru"
_HH_PAGES = 20
_HH_STATE = re.compile(r'<template[^<>]*\bid="HH-Lux-InitialState"[^<>]*>')


def _hh_state(page: str) -> dict[str, Any] | None:
    """The JSON object inside the HH-Lux-InitialState <template> (entities decoded), else None."""
    m = _HH_STATE.search(page)
    if m is None:
        return None
    end = page.find("</template", m.end())
    try:
        data = json.loads(html.unescape(page[m.end():end if end >= 0 else len(page)]))
    except (ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


def _hh_salary(c: dict[str, Any]) -> str:
    lo, hi = c.get("from"), c.get("to")
    ok = [x for x in (lo, hi) if isinstance(x, int) and not isinstance(x, bool) and x >= 0]
    if not ok:
        return ""
    cur = _clean(c.get("currencyCode"))[:8]
    amount = f"{lo}–{hi}" if len(ok) == 2 else (f"от {lo}" if ok[0] == lo else f"до {hi}")
    gross = c.get("gross")
    tax = " (до вычета налогов)" if gross is True else " (на руки)" if gross is False else ""
    return f"Зарплата: {amount} {cur}".rstrip() + tax


def _hhru(row: dict[str, Any], http: Http) -> list[Job] | None:
    """hh.ru search of one professional role: the embedded state of each page, no vacancy page opened."""
    role = str(row.get("token") or "").strip()
    if not re.fullmatch(r"[0-9]{1,6}", role):
        return None
    jobs: list[Job] = []
    seen: set[int] = set()
    for page in range(_HH_PAGES):
        _, text = _page_st(http, f"https://{_HH_HOST}/search/vacancy?professional_role={role}&items_on_page=100&page={page}"
                                  "&order_by=publication_time&period=7", max_bytes=_FEED_MAX)
        state = _hh_state(text) if text else None
        vacancies = _d(_d(state).get("vacancySearchResult")).get("vacancies") if state else None
        if not isinstance(vacancies, list):
            if page == 0:
                return None
            break
        fresh = 0
        for v in vacancies:
            try:
                v = _d(v)
                vid = _pos_id(v.get("vacancyId"))
                if not vid or v.get("@isAdv") or vid in seen:
                    continue
                seen.add(vid)
                fresh += 1
                co = _d(v.get("company"))
                company = _clean(co.get("visibleName") or co.get("name")).split("::", 1)[0].strip()
                title = _clean(v.get("name"))
                if not title or not company:
                    continue
                formats = {str(e).strip().upper() for wf in _l(v.get("workFormats")) for e in _l(_d(wf).get("workFormatsElement"))}
                mode = next((m for k, m in (("REMOTE", "remote"), ("HYBRID", "hybrid"), ("ON_SITE", "onsite")) if k in formats), "")
                kind = {"FULL": "full_time", "PART": "part_time", "PROJECT": "contract"}.get(str(_d(v.get("employment")).get("@type") or "").upper(), "")
                j = _job(row, "hhru", title=title, url=f"https://{_HH_HOST}/vacancy/{vid}", location=_clean(_d(v.get("area")).get("name")),
                         posted=_iso_date(_d(v.get("publicationTime")).get("$")), description=_hh_salary(_d(v.get("compensation"))),
                         remote=True if mode == "remote" else None, extra={"job_id": str(vid), "work_mode": mode, "employment_type": kind})
                j["company"] = company
                jobs.append(j)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        if not fresh:
            break
    return jobs


# --------------------------------------------------------------------------- Habr Career
# Borrowed from freehire internal/ingest/sources/habrcareer.go:36-37 (GET https://career.habr.com/api/frontend/vacancies?type=all
# &sort=date&page=<n>; the vacancy page is https://career.habr.com/vacancies/<id>), :41-48 (headers Accept and Referer), :99-122
# (list[] and meta.totalPages; an empty page or the last page ends the walk, 50 pages at most), :82-99 and :147-160 (id, title,
# remoteWork, publishedDate.date, company.title, locations[].title), :163-176 (the vacancy page's JobPosting ld+json gives the
# description; a page that cannot be read leaves the vacancy listed with no body). Habr paginates only to about 748 vacancies
# (habrcareer.go:20-23); the rest needs a login and is not read.
_HB_HEADERS = {"Accept": "application/json", "Referer": "https://career.habr.com/vacancies"}
_HB_PAGES = 50


def _habrcareer(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Habr Career: the paged JSON listing, one JobPosting ld+json page per wanted vacancy."""
    cards: dict[int, dict[str, Any]] = {}
    for page in range(1, _HB_PAGES + 1):
        _, data = _get(http, f"https://career.habr.com/api/frontend/vacancies?type=all&sort=date&page={page}", headers=_HB_HEADERS)
        items = _d(data).get("list")
        if not isinstance(items, list):
            if page == 1:
                return None
            break
        fresh = 0
        for it in items:
            vid = _pos_id(_d(it).get("id"))
            if vid and vid not in cards:
                cards[vid] = _d(it)
                fresh += 1
        pages = _d(_d(data).get("meta")).get("totalPages")
        if not fresh or (isinstance(pages, int) and not isinstance(pages, bool) and 0 < pages <= page):
            break
    urls = {vid: f"https://career.habr.com/vacancies/{vid}" for vid in cards}
    pages_html = _open_details(row, http, [(urls[v], _clean(c.get("title"))) for v, c in cards.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for vid, c in cards.items():
        try:
            title, company = _clean(c.get("title")), _clean(_d(c.get("company")).get("title"))
            if not title or not company:
                continue
            place = ", ".join(dict.fromkeys(t for t in (_clean(_d(x).get("title")) for x in _l(c.get("locations"))) if t))
            remote = c.get("remoteWork") is True
            desc = ""
            if urls[vid] in pages_html:
                posting = _ld_posting(_tree(pages_html[urls[vid]]))
                desc = _body(posting.get("description")) if posting else ""
            j = _job(row, "habrcareer", title=title, url=urls[vid], location=place,
                     posted=_iso_date(_d(c.get("publishedDate")).get("date")), description=desc,
                     remote=True if remote else None, extra={"job_id": str(vid), "work_mode": "remote" if remote else ""})
            j["company"] = company
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Getmatch
# Borrowed from freehire internal/ingest/sources/getmatch.go:25-28 (GET https://getmatch.ru/api/offers?offset=<n>&limit=100; the
# offer detail is /api/offers/<id>), :29-31 (200 pages at most), :53-60 and :70-83 (offers[] and meta.total), :88-105 (id,
# position, url = a path on getmatch.ru, published_at without a zone, company.name, offer_description = summary, description = full
# body, location_items[].label and .format), :113-128 (a later page failing ends the walk with what was gathered; the first page
# failing fails the board), :139-153 (the full detail body wins over the summary, a failed detail leaves the summary) and
# :280-293 (a work mode counts only when every location agrees).
_GM_HOST = "getmatch.ru"
_GM_PAGES = 30  # 100 offers a page; getmatch.go allows 200


def _gm_url(path: Any) -> str:
    """The offer link: a plain path on getmatch.ru (never a protocol-relative or absolute link to anywhere else), else ''."""
    p = str(path or "").strip()
    if not p.startswith("/") or p.startswith("//") or "\\" in p or any(ord(ch) < 33 for ch in p):
        return ""
    return _on(f"https://{_GM_HOST}{p}", (_GM_HOST,))


def _getmatch(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Getmatch: the paged offers feed, one detail call per wanted offer."""
    cards: dict[int, dict[str, Any]] = {}
    for page in range(_GM_PAGES):
        _, data = _get(http, f"https://{_GM_HOST}/api/offers?offset={page * 100}&limit=100", headers=_JSON)
        items = _d(data).get("offers")
        if not isinstance(items, list):
            if page == 0:
                return None
            break
        fresh = 0
        for it in items:
            oid = _pos_id(_d(it).get("id"))
            if oid and oid not in cards:
                cards[oid] = _d(it)
                fresh += 1
        total = _d(_d(data).get("meta")).get("total")
        if not fresh or (isinstance(total, int) and not isinstance(total, bool) and total > 0 and (page + 1) * 100 >= total):
            break
    urls = {oid: _gm_url(c.get("url")) for oid, c in cards.items()}
    details = _json_details(row, http, [(f"https://{_GM_HOST}/api/offers/{oid}", _clean(c.get("position"))) for oid, c in cards.items()], DETAIL_CAP, _JSON)
    jobs: list[Job] = []
    for oid, c in cards.items():
        try:
            title, company = _clean(c.get("position")), _clean(_d(c.get("company")).get("name"))
            if not title or not company or not urls[oid]:
                continue
            det = details.get(f"https://{_GM_HOST}/api/offers/{oid}", {})
            locs = [_d(x) for x in _l(c.get("location_items"))]
            modes = {_mode(x.get("format")) for x in locs} - {""}
            mode = next(iter(modes)) if len(modes) == 1 else ""
            place = ", ".join(dict.fromkeys(t for t in (_clean(x.get("label")) for x in locs) if t))
            body = str(det.get("description") or "").strip() or str(c.get("offer_description") or "")
            j = _job(row, "getmatch", title=title, url=urls[oid], location=place, posted=_iso_date(c.get("published_at")),
                     description=_body(body), remote=True if mode == "remote" else None, extra={"job_id": str(oid), "work_mode": mode})
            j["company"] = company
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "getmatch": _wrap(_getmatch),
    "habrcareer": _wrap(_habrcareer),
    "hhru": _wrap(_hhru),
    "jobbankdk": _wrap(_jobbankdk),
    "jobindex": _wrap(_jobindex),
}
