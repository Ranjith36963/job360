"""Public-sector and education board fetchers for the hunt kit: NEOGOV, AppliTrack, SchoolSpring and EDJOIN. Same
contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  neogov        ``<domain>/<agency>[/<folder>]`` with domain schooljobs.com or governmentjobs.com
                (``schooljobs.com/cochisecollege``): the two domains are separate tenant spaces
  applitrack    district slug of www.applitrack.com/<slug>/onlineapp/
  schoolspring  a district host ``<district>.schoolspring.com`` (that district's board), or any other text, which is
                a SEARCH KEYWORD on the national index (one central index serves every employer there)
  edjoin        an EDJOIN JOB-TYPE id (digits), NOT a district: EDJOIN is one central index and the id selects a slice

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see
NOTICE.md); the code is original. The container this was written in could not reach any of these hosts, so every
parser is tested on fixtures shaped like the references' test data only. Posting pages are opened only for cards whose
title passes the run's ``title_ok`` test, up to 25 per board, and the first refusal ends the board's detail pages.

PeopleSoft HCM career sites (freehire peoplesoft.go, career-ops peoplesoft.mjs) are deliberately NOT read: the search
list only grows by a form POSTBACK that PeopleTools accepts inside the cookie session the first GET opened (peoplesoft.go
:35-36 "ONE cookie jar", :112-160; peoplesoft.mjs:31-40, :79-88, :244-267), and the kit's ``http`` returns (status, body)
with no Set-Cookie and no jar, so that session cannot be carried. A /psc/<site>/<portal>/<node>/c/HRS_HRAM_FL...
link is reported as unsupported instead (hunt_kit.board_from_job_url).
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
    _epoch_date,
    _get,
    _iso_date,
    _job,
    _join,
    _ld_posting,
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
EDJOIN_PAGE = 500


# --------------------------------------------------------------------------- shared helpers
def _clean(value: Any) -> str:
    """Entity-decoded text on one line (the APIs leave entities in plain-text fields)."""
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _us_date(value: str) -> str:
    """'9/1/2026' (month/day/year, unpadded) -> '2026-09-01'; '' for anything else, free text included."""
    if not re.fullmatch(r"\d{1,2}/\d{1,2}/\d{4}", value.strip()):
        return ""
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


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


# --------------------------------------------------------------------------- AppliTrack
# Borrowed from freehire internal/ingest/sources/applitrack.go:16-22 (board = tenant path segment, case-insensitive),
# :50-58 and :261-283 (nothing is served as HTML: Output.asp answers JavaScript whose document.write('...') arguments carry the
# page, so the text is unwrapped first; ?all=1 is the site's own "All Vacancies" list), :68-72 (a consortium lists one
# requisition once per district under the SAME AppliTrackJobId: collapse them), :73-76 (windows-1252, handled by the kit's
# decode_body), :285-321 (applitrackWritten: backslash escapes the next character; a call that runs off the end is a cut
# response and refuses the page), :446-474 (fields are a span.label "<Name>:" followed by span.normal), :191-217 and :481-509
# (detail: Output.asp?AppliTrackJobId=<id>, block id "p<id>_", the body is the first span.normal that is not inside a field
# row; an id the board no longer carries is answered 200 with an empty list), :255-259 (permalink .../jobpostings/view.asp?
# AppliTrackJobId=<id>), :516-518 (dates are unpadded month/day/year; "ASAP" and the like are no date), and career-ops
# providers/applitrack.mjs:7-14 and :65 (the whole board in ONE request: jobpostings/Output.asp?all=1), :117 (a body with no
# "function applyFor" prelude is not this endpoint), :125-136 (a posting is a <table class='title'> block holding
# "JobID: <n>", td#wrapword = title, Location and Date Posted fields). NOT taken: freehire's technology-category crawl
# (:24-45, :342-377: an IT catalogue's own choice; the kit's user filters by title) and career-ops' default_location (:16-19).
_APPLITRACK_SLUG = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}")
_APPLITRACK_WRITE = re.compile(r"document\.write\('([^'\\]*(?:\\.[^'\\]*)*)('?)", re.S)
_APPLITRACK_ESC = re.compile(r"\\(.)", re.S)
_APPLITRACK_SPLIT = re.compile(r"<table\s+class=['\"]title['\"]", re.I)
_APPLITRACK_ID = re.compile(r"JobID:\s*(\d+)", re.I)


def _applitrack_markup(body: str) -> str | None:
    """The markup the response's document.write('...') calls emit; None when a call is cut off or the body is not
    an Output.asp script at all."""
    out: list[str] = []
    for m in _APPLITRACK_WRITE.finditer(body):
        if not m.group(2):
            return None  # the call never closed: a cut response must not read as a complete board
        out.append(_APPLITRACK_ESC.sub(r"\1", m.group(1)))
    if not out and "function applyFor" not in body:
        return None
    return "".join(out)


def _label_value(root: _Node, label: str) -> str:
    """The span.normal after the span.label '<label>:' in the same row ('' when the row is absent)."""
    for parent in (root, *_walk(root)):
        for i, k in enumerate(parent.kids):
            if isinstance(k, _Node) and k.tag == "span" and k.has_class("label") and k.text().rstrip(":").strip() == label:
                for sib in parent.kids[i + 1:i + 7]:  # the value sits right after its label; a bounded look keeps a hostile row linear
                    if isinstance(sib, _Node) and sib.tag == "span" and sib.has_class("normal"):
                        return str(sib.text()).replace("�", "")
    return ""


def _applitrack_body(page: str, jid: str) -> str:
    """Description of one posting page: the first span.normal under block ``p<jid>_`` that is not inside a field row."""
    block = _by_id(_tree(_applitrack_markup(page) or ""), f"p{jid}_")
    if block is None:
        return ""
    stack: list[tuple[_Node, bool]] = [(block, False)]
    while stack:
        node, in_row = stack.pop()
        for k in reversed(node.kids):
            if not isinstance(k, _Node):
                continue
            if k.tag == "span" and k.has_class("normal") and not in_row:
                return str(_body(k.inner_html()))
            stack.append((k, in_row or k.tag == "li"))
    return ""


def _applitrack(row: dict[str, Any], http: Http) -> list[Job] | None:
    """AppliTrack K-12 district board: the whole list in one Output.asp?all=1 request, posting pages for wanted ones."""
    slug = str(row.get("token") or "").strip().lower()
    if not _APPLITRACK_SLUG.fullmatch(slug):
        return None
    base = f"https://www.applitrack.com/{slug}/onlineapp/jobpostings/"
    st, body = _get(http, base + "Output.asp?all=1", as_json=False, headers=_HTMLH)
    markup = _applitrack_markup(body) if st == 200 and isinstance(body, str) else None
    if markup is None:
        return None
    postings: dict[str, dict[str, str]] = {}
    for block in _APPLITRACK_SPLIT.split(markup)[1:]:
        try:
            m = _APPLITRACK_ID.search(block)
            if m is None or m.group(1) in postings:
                continue  # no id, or one requisition listed once per district
            root = _tree("<table class='title'" + block)
            title = _clean(next((n.text() for n in _walk(root) if n.attrs.get("id") == "wrapword"), ""))
            if not title:
                continue
            postings[m.group(1)] = {"title": title, "location": _clean(_label_value(root, "Location")),
                                    "posted": _us_date(_label_value(root, "Date Posted"))}
        except Exception:  # noqa: BLE001, S112 - one bad block must not lose the board
            continue
    view = {i: f"{base}view.asp?AppliTrackJobId={i}" for i in postings}
    pages = _open_details(row, http, [(f"{base}Output.asp?AppliTrackJobId={i}", p["title"]) for i, p in postings.items()],
                          DETAIL_CAP)
    jobs: list[Job] = []
    for jid, p in postings.items():
        try:
            page = pages.get(f"{base}Output.asp?AppliTrackJobId={jid}")
            jobs.append(_job(row, "applitrack", title=p["title"], url=view[jid], location=p["location"], posted=p["posted"],
                             description=_applitrack_body(page, jid) if page else "", remote=_remote_from(p["location"]),
                             extra={"job_id": jid}))
        except Exception:  # noqa: BLE001, S112
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


# --------------------------------------------------------------------------- EDJOIN
# Borrowed from freehire internal/ingest/sources/edjoin.go:13-37 (ONE central index for California K-12 / county offices /
# community colleges; THE BOARD IS A JOB-TYPE ID, not a district: districtID is only another facet), :41-47 and :214-229 (GET
# https://www.edjoin.org/Home/LoadJobs?rows=&page=&sort=postingDate&sortVal=0&order=desc&searchType=all&jobTypes=<board>&days=0&
# catID=0&districtID=0&recruitmentCenterID=0; catID, districtID and recruitmentCenterID MUST be sent as 0 or the site answers an
# HTML 500), :47-52 (totalRecords is exact; the same posting can sit on two adjacent pages, so dedup on postingID; an unknown
# job type answers 200 with 0 rows), :103-130 (envelope {totalRecords, data[]}; row fields postingID, positionTitle,
# districtName, city, countyName, stateName, postingDate, FullTimePartTime), :173-207 (stop on an empty page or at the total;
# a row with no id or no district is skipped), :237-251 and :132-135 (the body only on the posting page, in its schema.org
# JobPosting ld+json "description"), :256-291 (company = the posting's own districtName; location = city, else county, then
# state), :278-280 (https://www.edjoin.org/Home/JobPosting/<id>), :293-312 (FullTimePartTime is a comma-joined multi-select;
# "Remote" is the platform's own remote flag), :401-417 (postingDate is .NET "/Date(<ms>)/"; a non-positive value is unset).
# NOT taken: pay fields (:335-399). The reference skips a posting whose page it cannot read; the kit keeps the listing row.
_EDJOIN_DATE = re.compile(r"^/Date\((-?\d+)\)/$")


def _edjoin_date(value: Any) -> str:
    m = _EDJOIN_DATE.match(str(value or ""))
    return _epoch_date(int(m.group(1)) / 1000) if m else ""


def _edjoin(row: dict[str, Any], http: Http) -> list[Job] | None:
    """EDJOIN (California education): LoadJobs JSON for one job-type id, ld+json posting pages for wanted ads."""
    jt = str(row.get("token") or "").strip()
    if not re.fullmatch(r"\d{1,6}", jt):
        return None
    rows: dict[int, dict[str, Any]] = {}
    total = 0
    for page in range(1, MAX_PAGES + 1):
        q = urlencode({"rows": EDJOIN_PAGE, "page": page, "sort": "postingDate", "sortVal": 0, "order": "desc",
                       "searchType": "all", "jobTypes": jt, "days": 0, "catID": 0, "districtID": 0,
                       "recruitmentCenterID": 0})
        st, data = _get(http, f"https://www.edjoin.org/Home/LoadJobs?{q}", headers=_JSON)
        batch = data.get("data") if isinstance(data, dict) else None
        if st != 200 or not isinstance(batch, list):
            if page > 1:
                break
            return None
        if page == 1 and isinstance(data.get("totalRecords"), int):
            total = data["totalRecords"]
        for p in batch:
            pid = p.get("postingID") if isinstance(p, dict) else None
            if isinstance(pid, int) and not isinstance(pid, bool) and pid > 0 and pid not in rows \
                    and str(p.get("districtName") or "").strip():
                rows[pid] = p
        if not batch or (total > 0 and len(rows) >= total):
            break
    url_of = {pid: f"https://www.edjoin.org/Home/JobPosting/{pid}" for pid in rows}
    pages = _open_details(row, http, [(url_of[pid], _clean(p.get("positionTitle"))) for pid, p in rows.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for pid, p in rows.items():
        try:
            title, company = _clean(p.get("positionTitle")), _clean(p.get("districtName"))
            if not title or not company:
                continue
            desc = ""
            if url_of[pid] in pages:
                ld = _ld_posting(_tree(pages[url_of[pid]]))
                desc = _body(ld.get("description")) if ld else ""
            place = _join(_clean(p.get("city")) or _clean(p.get("countyName")), _clean(p.get("stateName")))
            schedule = {s.strip() for s in str(p.get("FullTimePartTime") or "").split(",") if s.strip()}
            j = _job(row, "edjoin", title=title, url=url_of[pid], location=place, posted=_edjoin_date(p.get("postingDate")),
                     description=desc, remote=True if "Remote" in schedule else None,
                     extra={"posting_id": str(pid), "schedule": ", ".join(sorted(schedule))})
            j["company"] = company  # the district named in the ad, never the board (a job-type slice)
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "applitrack": _wrap(_applitrack),
    "edjoin": _wrap(_edjoin),
    "neogov": _wrap(_neogov),
    "schoolspring": _wrap(_schoolspring),
}
