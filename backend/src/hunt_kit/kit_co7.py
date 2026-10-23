"""Three career-ops board readers for the hunt kit: PrevueAPS, Red Rover K12 and Senjob. Same
contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  prevueaps        tenant label of <tenant>.prevueaps.ca
  redrover         the numeric organisation id of jobs.redroverk12.com/org/<id>
  senjob           any word (``senjob:all``): senjob.com is ONE national board (Senegal), its listing pages are read

Endpoints, params and field names are adapted from the MIT-licensed career-ops provider adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures shaped
like the references' test data only. Every request stays on the board's own host (the tenant host, jobs.redroverk12.com /
api.redroverk12.com, senjob.com); a posting link on any other host is dropped, never opened. No posting page is opened.

NOT IN REFERENCE and left out: a posting date for PrevueAPS, a description for Red Rover and Senjob.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    MAX_PAGES,
    Http,
    Job,
    _get,
    _https,
    _iso_date,
    _job,
    _page_st,
    _wrap,
)

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json", "Origin": "https://jobs.redroverk12.com"}


def _line(value: Any) -> str:
    """Entity-decoded text on one line."""
    return " ".join(html.unescape(str(value or "")).split())


# --------------------------------------------------------------------------- PrevueAPS
# Borrowed from career-ops providers/prevueaps.mjs:20 (GET https://<tenant>.prevueaps.ca/core/jobs/<domainId>?getParams=<json>),
# :14-38 (the numeric domainId is read from the tenant's own /jobs/ page, four shapes tried in order), :53 (the host is
# <tenant>.prevueaps.ca), :57-65 (the getParams display hints), :179-183 and :202-211 (data.jobs; an empty list is a quiet board,
# anything that is not a list is a failed board), :215-250 (title, jobUrl, jobLocation or city + abbreviation/stateName, and
# employment type, classification and pay folded into the description).
_PREVUE_PARAMS = {"showDate": True, "showLocation": True, "showEmploymentType": True, "showCategory": True,
                  "showClassification": True, "showWorkplaceType": True, "customCategoryTitle": ""}
_PREVUE_ID = (
    re.compile(r"/core/jobs/(\d{1,12})"),
    re.compile(r"data-domain-id\s*=\s*[\"'](\d{1,12})[\"']", re.I),
    re.compile(r"[\"']?domainId[\"']?\s*[:=]\s*[\"']?(\d{1,12})", re.I),
    re.compile(r"[\"']?siteId[\"']?\s*[:=]\s*[\"']?(\d{1,12})", re.I),
)


def _domain_id(page: str) -> str:
    """The tenant's numeric site id from its /jobs/ page, strongest signal first (prevueaps.mjs:112-134), else ''."""
    for rx in _PREVUE_ID:
        m = rx.search(page)
        if m:
            return m.group(1)
    return ""


def _prevueaps(row: dict[str, Any], http: Http) -> list[Job] | None:
    """PrevueAPS tenant board: the numeric site id from /jobs/, then one JSON call."""
    tenant = str(row.get("token") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", tenant):
        return None
    host = f"{tenant}.prevueaps.ca"
    _st, page = _page_st(http, f"https://{host}/jobs/")
    dom = _domain_id(page) if page else ""
    if not dom:
        return None
    params = quote(json.dumps(_PREVUE_PARAMS, separators=(",", ":")), safe="")
    st, data = _get(http, f"https://{host}/core/jobs/{dom}?getParams={params}", headers=_JSON)
    inner = data.get("data") if isinstance(data, dict) else None
    items = inner.get("jobs") if isinstance(inner, dict) else None
    if st != 200 or not isinstance(items, list):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for it in items:
        try:
            title = _line(it.get("title"))
            url = _https(it.get("jobUrl"), host)  # the tenant's own posting page; any other host is dropped
            if not title or not url or url in seen:
                continue
            seen.add(url)
            place = _line(it.get("jobLocation")) or ", ".join(x for x in (_line(it.get("city")), _line(it.get("abbreviation")) or _line(it.get("stateName"))) if x)
            bits = [_line(it.get("employmentType")), _line(it.get("classification"))]
            lo, hi = (it.get("minSalary"), it.get("maxSalary"))
            pay = " - ".join(_line(x) for x in (lo, hi) if isinstance(x, str) and _line(x))
            if pay:
                bits.append(" ".join(x for x in (pay, _line(it.get("payTypeFrame"))) if x))
            jobs.append(_job(row, "prevueaps", title=title, url=url, location=place, description=" · ".join(b for b in bits if b)))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Red Rover K12
# Borrowed from career-ops providers/redrover.mjs:24 and :75-84 (POST https://api.redroverk12.com/graphql, the GetJobPostings query
# with search {orgId, searchTerm "", locationIds [], jobPostingCategoryIds []}), :27-42 (the fields read), :59 (the org id is
# digits only), :99-123 (errors, a missing site object and hasMoreData fail the board: one call holds at most 500 postings and no
# cursor is known), :128-143 (numeric id and a name are required; a posting with no public activation date is internal-only and a
# closed one is skipped; the page is <origin>/org/<orgId>/opening/<id>; location is "<place> - <organisation>", "(Remote)" added).
_RR_QUERY = ("query GetJobPostings($search: JobPostingSearchInput!) {\n  jobSeekerSiteUnauthenticated {\n    jobPostingSearch(search: $search) {\n"
             "      results {\n        id\n        name\n        organizationName\n        location { name }\n        activePublicOnDateUtc\n"
             "        closedOnDateUtc\n        allowsRemote\n      }\n      hasMoreData\n    }\n  }\n}")


def _redrover(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Red Rover K12 district board: one GraphQL call, public postings only; more than one call holds fails the board."""
    org = str(row.get("token") or "").strip()
    if not re.fullmatch(r"\d{1,12}", org):
        return None
    body = json.dumps({"operationName": "GetJobPostings", "query": _RR_QUERY,
                       "variables": {"search": {"orgId": org, "searchTerm": "", "locationIds": [], "jobPostingCategoryIds": []}}}).encode()
    st, data = _get(http, "https://api.redroverk12.com/graphql", method="POST", data=body, headers=_POST)
    if st != 200 or not isinstance(data, dict) or (isinstance(data.get("errors"), list) and data["errors"]):
        return None
    inner = data.get("data")
    site = inner.get("jobSeekerSiteUnauthenticated") if isinstance(inner, dict) else None
    if site is None:
        return []
    search = site.get("jobPostingSearch") if isinstance(site, dict) else None
    if not isinstance(search, dict) or search.get("hasMoreData"):
        return None
    rows = search.get("results")
    jobs: list[Job] = []
    seen: set[str] = set()
    for it in rows if isinstance(rows, list) else []:
        try:
            jid, title = str(it.get("id") if it.get("id") is not None else "").strip(), _line(it.get("name"))
            if not re.fullmatch(r"\d{1,12}", jid) or not title or jid in seen:
                continue
            if not it.get("activePublicOnDateUtc") or it.get("closedOnDateUtc"):
                continue  # internal-only or closed
            seen.add(jid)
            loc = it.get("location")
            where = " - ".join(x for x in (_line(loc.get("name")) if isinstance(loc, dict) else "", _line(it.get("organizationName"))) if x)
            remote = bool(it.get("allowsRemote"))
            jobs.append(_job(row, "redrover", title=title, url=f"https://jobs.redroverk12.com/org/{org}/opening/{jid}",
                             location=f"{where} (Remote)" if remote else where, posted=_iso_date(it.get("activePublicOnDateUtc")),
                             remote=True if remote else None, extra={"job_id": jid}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Senjob
# Borrowed from career-ops providers/senjob.mjs:40 and :99-105 (GET https://senjob.com/offres-d-emploi.php, then ?page=N), :43 and
# :201-227 (at most 10 pages; a page that adds no new posting ends the board, the board pins "sticky" postings to every page),
# :23-28 and :56-63 (a posting is /jobseekers/<slug>_e_<id>.html, its date is a hidden <span style="display:none">YYYY-MM-DD</span>),
# :119-155 (title from the anchor with comments dropped, place from the text after it up to the "Publi" label, date from a sibling
# row, rows merged by posting id, company left unnamed), :170-175 and :212-215 (a first page that still holds posting links but
# parses to nothing is a broken board, never an empty one). A flat event stream, not an element tree, so a row left unclosed in a
# hostile page cannot hide the rows below it.
_SEN_URL = "https://senjob.com/offres-d-emploi.php"
_SEN_HREF = re.compile(r"https://senjob\.com/jobseekers/[^?#\s\"'<>]{0,300}?_e_(\d{1,12})\.html")
_SEN_SHAPE = re.compile(r"/jobseekers/[^\s\"'<>]{0,300}_e_\d{1,12}\.html")
_SEN_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEN_HIDDEN = re.compile(r"display\s*:\s*none", re.I)
_SEN_LABEL = re.compile(r"Publi[ée]", re.I)


class _SenEvents(HTMLParser):
    """Flat document-order events from the listing, with no element tree: ("a", id, url, title) per posting anchor, ("t", text) per
    text node, ("d", iso date) per hidden date span. Rows left unclosed in a hostile page cannot nest or hide the rows below them."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.events: list[tuple[str, str, str, str]] = []
        self._a: tuple[str, str, list[str]] | None = None
        self._span: list[str] | None = None

    def _flush(self) -> None:
        if self._a is not None:
            self.events.append(("a", self._a[0], self._a[1], " ".join("".join(self._a[2]).split())[:300]))
            self._a = None
        if self._span is not None:
            text = "".join(self._span).strip()
            if _SEN_DATE.fullmatch(text):
                self.events.append(("d", text, "", ""))
            self._span = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        if self._span is not None:
            self._flush()
        if tag == "a":
            self._flush()
            href = a.get("href") or ""
            m = _SEN_HREF.fullmatch(href) if len(href) < 500 else None
            if m and (urlsplit(href).hostname or "") == "senjob.com":
                self._a = (m.group(1), href, [])
        elif tag == "span" and _SEN_HIDDEN.search(a.get("style") or ""):
            self._flush()
            self._span = []

    def handle_endtag(self, tag: str) -> None:
        if (tag == "a" and self._a is not None) or (self._span is not None):
            self._flush()

    def handle_data(self, data: str) -> None:
        if self._a is not None:
            if sum(len(x) for x in self._a[2]) < 300:
                self._a[2].append(data)
        elif self._span is not None:
            if sum(len(x) for x in self._span) < 40:
                self._span.append(data)
        else:
            self.events.append(("t", data, "", ""))


def _sen_events(page: str) -> list[tuple[str, str, str, str]]:
    p = _SenEvents()
    try:
        p.feed(page)
        p.close()
    except Exception:  # noqa: BLE001 - a malformed page yields what was read so far
        pass
    p._flush()
    return p.events


def _sen_parse(page: str) -> dict[str, dict[str, str]]:
    """Posting id -> {title, url, loc, date}, in first-seen order; a repeated id fills what is still missing."""
    out: dict[str, dict[str, str]] = {}
    cur = ""
    buf: list[str] | None = None  # text collected for the place of the posting whose title was just read

    def close() -> None:
        nonlocal buf
        if buf is not None and cur:
            place = _SEN_LABEL.split(" ".join(" ".join(buf).split()), 1)[0]
            out[cur]["loc"] = place.strip(" |:-")
        buf = None

    for kind, a, b, c in _sen_events(page):
        if kind == "a":
            close()
            cur = a
            rec = out.setdefault(cur, {"title": "", "url": b, "loc": "", "date": ""})
            title = " ".join(c.split())
            if title and not rec["title"]:
                rec["title"] = title
                buf = []
        elif kind == "t" and buf is not None:
            buf.append(a)
            if sum(len(x) for x in buf) > 300 or _SEN_LABEL.search(a):
                close()
        elif kind == "d" and cur and not out[cur]["date"]:
            out[cur]["date"] = a
    close()
    return out


def _senjob(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Senjob (Senegal): the national listing pages, newest first; sticky repeats merge by posting id."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for pg in range(1, MAX_PAGES + 1):
        _st, page = _page_st(http, _SEN_URL if pg == 1 else f"{_SEN_URL}?page={pg}")
        if page is None:
            if pg == 1:
                return None
            break  # a refusal ends the board, what was gathered stays
        found = _sen_parse(page)
        if not found:
            if pg == 1 and _SEN_SHAPE.search(page):
                return None  # posting links present, nothing parsed: the markup changed
            break
        fresh = 0
        for jid, rec in found.items():
            if jid in seen:
                continue
            seen.add(jid)
            fresh += 1
            if not rec["title"]:
                continue
            j = _job(row, "senjob", title=rec["title"], url=rec["url"], location=rec["loc"], posted=_iso_date(rec["date"]), extra={"job_id": jid})
            j["company"] = "?"  # the listing names no employer; never invented from the slug
            jobs.append(j)
        if not fresh:
            break
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "prevueaps": _wrap(_prevueaps),
    "redrover": _wrap(_redrover),
    "senjob": _wrap(_senjob),
}
