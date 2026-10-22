"""Public-sector and education board fetchers for the hunt kit: NEOGOV and SchoolSpring. Same contract as
kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file
IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  neogov        ``<domain>/<agency>[/<folder>]`` with domain schooljobs.com or governmentjobs.com
                (``schooljobs.com/cochisecollege``): the two domains are separate tenant spaces
  schoolspring  a district host ``<district>.schoolspring.com`` (that district's board), or any other text, which is
                a SEARCH KEYWORD on the national index (one central index serves every employer there)

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see
NOTICE.md); the code is original. The container this was written in could not reach any of these hosts, so every
parser is tested on fixtures shaped like the references' test data only. Posting pages are opened only for cards whose
title passes the run's ``title_ok`` test, up to 25 per board, and the first refusal ends the board's detail pages.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from datetime import date, datetime, timezone
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
    _iso_date,
    _job,
    _Node,
    _open_details,
    _remote_from,
    _title_ok,
    _tree,
    _walk,
    _wrap,
)

_JSON = {"Accept": "application/json"}
_HTMLH = {"Accept": "text/html"}
_XHR = {"Accept": "text/html", "X-Requested-With": "XMLHttpRequest"}

# Detail-page requests per board (the kit's own cap; the references fan out over every posting).
DETAIL_CAP = 25
NEOGOV_PAGES = 30  # career-ops neogov.mjs:32 DEFAULT_MAX_PAGES (10 cards a page, so 300 postings)
SCHOOLSPRING_PAGE = 100


# --------------------------------------------------------------------------- shared helpers
def _clean(value: Any) -> str:
    """Entity-decoded text on one line (the APIs leave entities in plain-text fields)."""
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _not_future(day: str, today: date | None = None) -> str:
    return day if day and day <= (today or datetime.now(timezone.utc).date()).isoformat() else ""


def _by_id(root: _Node, ident: str) -> _Node | None:
    return next((n for n in _walk(root) if n.attrs.get("id") == ident), None)


# --------------------------------------------------------------------------- NEOGOV
# Borrowed from freehire internal/ingest/sources/neogov.go:13-20 (governmentjobs.com and schooljobs.com are separate
# tenant spaces, so the board is "<domain>/<agency>"; the list is a Knockout SPA that serves its cards only to an XHR),
# :45-46 and :64-67 (header X-Requested-With: XMLHttpRequest; GET https://www.<domain>/careers/home/index?agency=<a>&page=N),
# :48-49 and :114-123 (total from <span id="job-postings-number">), :78-95 (dedup on the posting id; stop on an empty page or
# the stated total), :128-161 (li.list-item[data-job-id], a.item-details-link for title and href, first ul.list-meta li =
# location, .list-entry = teaser snippet), :100-111 and :163-179 (the detail page is plain HTML, body in #details-info;
# a failed detail keeps the snippet) and career-ops providers/neogov.mjs:23-28 (the bare hosts redirect to www.),
# :29 and :59-64 (agency / folder segment shape, "home" is the site's own path), :71-78 (departmentFolder scopes the list to
# a custom careers page; sort=PostingDate newest first), :121-124 (a page that adds nothing new ends the walk), :157-188
# (a body with no cards but the not-found / listing container is an empty board, any other body is NOT a NEOGOV list; an href
# must resolve to /careers/<agency>/[<folder>/]jobs/<id> on the same origin). The list shows only "Posted N weeks ago",
# so no date is read.
_NEOGOV_DOMAINS = ("schooljobs.com", "governmentjobs.com")
_NEOGOV_SEG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_NEOGOV_COUNT = re.compile(r'id="job-postings-number">\s*(\d+)')
_NEOGOV_PATH = re.compile(r"^/careers/[^/]+/(?:[^/]+/)?jobs/\d+")


def _neogov_target(token: str) -> tuple[str, str, str] | None:
    parts = [p for p in token.strip().lower().split("/") if p]
    if parts and parts[0].startswith("www."):
        parts[0] = parts[0][4:]
    if len(parts) not in (2, 3) or parts[0] not in _NEOGOV_DOMAINS:
        return None
    agency, folder = parts[1], (parts[2] if len(parts) == 3 else "")
    if not _NEOGOV_SEG.fullmatch(agency) or agency == "home" or (folder and not _NEOGOV_SEG.fullmatch(folder)):
        return None
    return parts[0], agency, folder


def _list_items(root: _Node) -> list[_Node]:
    """Every li.list-item in document order, not looking inside one (cards do not nest, and unclosed ones in a hostile
    page would otherwise make each card re-walk everything below it)."""
    out: list[_Node] = []
    stack = [iter(root.kids)]
    while stack:
        for k in stack[-1]:
            if isinstance(k, _Node):
                if k.tag == "li" and k.has_class("list-item"):
                    out.append(k)
                else:
                    stack.append(iter(k.kids))
                    break
        else:
            stack.pop()
    return out


def _neogov_cards(frag: str, host: str) -> list[dict[str, str]]:
    """The postings of one listing fragment; a card with no id, no link, a bad or foreign href is skipped."""
    out: list[dict[str, str]] = []
    for li in _list_items(_tree(frag)):
        if not li.attrs.get("data-job-id", "").strip():
            continue  # e.g. a "no results" placeholder card
        try:
            link = next((a for a in _walk(li) if a.tag == "a" and a.has_class("item-details-link")), None)
            if link is None:
                continue
            u = urlsplit(urljoin(f"https://{host}/", link.attrs.get("href", "")))
            title = _clean(link.text())
            if u.scheme != "https" or (u.hostname or "").lower() != host or not _NEOGOV_PATH.match(u.path) or not title:
                continue
            meta = next((n for n in _walk(li) if n.tag == "ul" and n.has_class("list-meta")), None)
            first = next((n.text() for n in _walk(meta) if n.tag == "li"), "") if meta is not None else ""
            entry = next((n.text() for n in _walk(li) if n.has_class("list-entry")), "")
            out.append({"id": li.attrs["data-job-id"].strip(), "title": title, "location": first, "snippet": entry,
                        "url": u._replace(query="", fragment="").geturl()})
        except ValueError:
            continue
    return out


def _neogov(row: dict[str, Any], http: Http) -> list[Job] | None:
    """NEOGOV career site (governmentjobs.com / schooljobs.com): XHR list pages, #details-info pages for wanted cards."""
    target = _neogov_target(str(row.get("token") or ""))
    if target is None:
        return None
    domain, agency, folder = target
    host = f"www.{domain}"
    cards: dict[str, dict[str, str]] = {}
    total = 0
    for page in range(1, NEOGOV_PAGES + 1):
        q = {"agency": agency, **({"departmentFolder": folder} if folder else {}), "sort": "PostingDate",
             "isDescendingSort": "true", "page": str(page)}
        st, frag = _get(http, f"https://{host}/careers/home/index?{urlencode(q)}", as_json=False, headers=_XHR)
        if st != 200 or not isinstance(frag, str):
            if page > 1:
                break  # a hiccup after page 1 keeps what we have
            return None
        if page == 1:
            m = _NEOGOV_COUNT.search(frag)
            total = int(m.group(1)) if m else 0
        found = _neogov_cards(frag, host)
        if not found:
            if page == 1 and "jobs-not-found-container" not in frag and "search-results-listing-container" not in frag:
                return None  # not a NEOGOV list (a wrong agency answers the site's home page)
            break
        fresh = 0
        for c in found:
            if c["id"] not in cards:
                cards[c["id"]] = c
                fresh += 1
        if not fresh or (total and len(cards) >= total):
            break
    pages = _open_details(row, http, [(c["url"], c["title"]) for c in cards.values()], DETAIL_CAP)
    jobs: list[Job] = []
    for c in cards.values():
        try:
            info = _by_id(_tree(pages[c["url"]]), "details-info") if c["url"] in pages else None
            desc = (_body(info.inner_html()) if info is not None else "") or _body(c["snippet"])
            jobs.append(_job(row, "neogov", title=c["title"], url=c["url"], location=c["location"], description=desc,
                             remote=_remote_from(c["location"]), extra={"job_id": c["id"], "agency": agency}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- SchoolSpring
# Borrowed from freehire internal/ingest/sources/schoolspring.go:15-19 and :106-114 (keyless JSON API api.schoolspring.com;
# the national domain www.schoolspring.com sees every employer, so a board there is a search KEYWORD, :20-51), :418-421
# (GET /api/Jobs/GetPagedJobsWithSearch?domainName=&keyword=&page=&size=100&sortDateAscending=false; the other filters may
# be left out), :127-134 and :278-289 (envelope {success, message, value}: success=false is a refusal, not an empty list),
# :236-273 (page until a page adds no new jobId), :139-143 (listing row: jobId, title, location), :301-337 and :163-203 (detail
# GET /api/Jobs/<id>?domainName=: success, jobInfo.jobTitle/jobDescription/employerName = the DISTRICT/displayDate,
# jobLocations[].displayLocation joined with "; "; a posting with no employer or no body is skipped), :410-413 (entities are
# decoded), :420-431 (public page https://www.schoolspring.com/jobdetail?jobId=<id>) and career-ops providers/schoolspring.mjs:
# 7-16 and :18-51 (a district board is <district>.schoolspring.com, passed as domainName; www and api are the platform's own
# hosts), :57-75 (keyword '' for a district), :111-123 (stop on a short page, or one that adds nothing), :167-202 (listing:
# jobId digits, title, location, employer, displayDate; district page https://<host>/?jobid=<id>; location = "<place> -
# <school>"). A district board lists every row (company = the board's name), a keyword board lists only postings whose
# detail page was opened, because only the detail names the employer (the listing's own employer is the school, go:136-138).
# NOT taken: pay figures and employment type (go:339-408).
_SS_API = "https://api.schoolspring.com"
_SS_NATIONAL = "www.schoolspring.com"
_SS_DISTRICT = re.compile(r"[a-z0-9][a-z0-9-]{0,62}\.schoolspring\.com")


def _ss_board(token: str) -> tuple[str, str, str] | None:
    """(domainName, keyword, public-page host) for a district host or a keyword; None when there is no board."""
    t = " ".join(token.split())
    if not t or len(t) > 80 or any(ord(c) < 32 for c in token):
        return None
    low = t.lower()
    if low.endswith(".schoolspring.com"):
        return (low, "", low) if _SS_DISTRICT.fullmatch(low) and low not in (_SS_NATIONAL, "api.schoolspring.com") else None
    return _SS_NATIONAL, t, _SS_NATIONAL


def _ss_url(public: str, jid: str, keyword_board: bool) -> str:
    return f"https://{public}/jobdetail?jobId={jid}" if keyword_board else f"https://{public}/?jobid={jid}"


def _schoolspring(row: dict[str, Any], http: Http) -> list[Job] | None:
    """SchoolSpring (K-12): keyless JSON API; a district host is one board, anything else is a national keyword search."""
    board = _ss_board(str(row.get("token") or ""))
    if board is None:
        return None
    domain, keyword, public = board
    keyword_board = bool(keyword)
    rows: dict[int, dict[str, Any]] = {}
    for page in range(1, MAX_PAGES + 1):
        q = urlencode({"domainName": domain, "keyword": keyword, "page": page, "size": SCHOOLSPRING_PAGE,
                       "sortDateAscending": "false"})
        st, data = _get(http, f"{_SS_API}/api/Jobs/GetPagedJobsWithSearch?{q}", headers=_JSON)
        value = data.get("value") if isinstance(data, dict) else None
        listing = value.get("jobsList") if isinstance(value, dict) else None
        if st != 200 or not isinstance(data, dict) or data.get("success") is not True or not isinstance(listing, list):
            if page > 1:
                break  # a hiccup after page 1 keeps what we have (schoolspring.go:246-255)
            return None
        fresh = 0
        for r in listing:
            jid = r.get("jobId") if isinstance(r, dict) else None
            if isinstance(jid, int) and not isinstance(jid, bool) and jid > 0 and jid not in rows:
                rows[jid] = r
                fresh += 1
        if not fresh or len(listing) < SCHOOLSPRING_PAGE:
            break
    # detail resources for wanted rows, at most DETAIL_CAP; the first refusal (not 200/404/410) ends the run
    details: dict[int, dict[str, Any]] = {}
    tried = 0
    for jid, r in rows.items():
        if tried >= DETAIL_CAP:
            break
        title = _clean(r.get("title"))
        if not title or not _title_ok(row, title):
            continue
        tried += 1
        st, d = _get(http, f"{_SS_API}/api/Jobs/{jid}?domainName={quote(domain, safe='')}", headers=_JSON, once=True)
        if st in (404, 410):
            continue
        if st != 200:
            break
        v = d.get("value") if isinstance(d, dict) and d.get("success") is True else None
        info = v.get("jobInfo") if isinstance(v, dict) else None
        if isinstance(info, dict):
            details[jid] = v  # type: ignore[assignment]
    jobs: list[Job] = []
    for jid, r in rows.items():
        try:
            title, place = _clean(r.get("title")), _clean(r.get("location"))
            school, company, desc, posted = _clean(r.get("employer")), "", "", _iso_date(r.get("displayDate"))
            d = details.get(jid)
            if d is not None:
                info = d["jobInfo"]
                title = _clean(info.get("jobTitle")) or title
                company, desc = _clean(info.get("employerName")), _body(info.get("jobDescription"))
                places = [p for p in (_clean(x.get("displayLocation")) for x in d.get("jobLocations") or []
                                      if isinstance(x, dict)) if p]
                place = "; ".join(dict.fromkeys(places)) or place
                posted = _iso_date(info.get("displayDate")) or posted
            elif keyword_board:
                continue  # only a posting page names the district (schoolspring.go:301-311)
            elif school:
                place = f"{place} - {school}" if place else school
            if keyword_board and (not company or not desc):
                continue
            if not title:
                continue
            j = _job(row, "schoolspring", title=title, url=_ss_url(public, str(jid), keyword_board), location=place,
                     posted=_not_future(posted), description=desc, remote=_remote_from(place), extra={"job_id": str(jid)})
            if company:
                j["company"] = company  # the employer named in the ad, never the board
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "neogov": _wrap(_neogov),
    "schoolspring": _wrap(_schoolspring),
}
