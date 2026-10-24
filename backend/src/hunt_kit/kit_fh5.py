"""Freehire batch B and JobSpy readers for the hunt kit (branch wave4/fh5): Vouch (a board) and BDJobs (a job-site feed).
Same contracts as kit_ats_eu.py (``fetch(row, http, stats) -> list[Job]``) and kit_markets3.py (``fetch_x(queries, http, stats,
*, days, countries) -> list[Job]``): never raises, network only through the ``http`` callable, no file IO at import, standard
library only. Nothing here ranks or scores a job.

Board token (companies.json ``board`` / ``--companies-extra "ats:token"``):
  vouch  the company cuid of vouch.careers/companies/<cuid> (vouch.go:15-19)

Feed: ``bdjobs`` (Bangladesh; runs when BD is among the user's countries or "*").

Endpoints, params and field names are adapted from the MIT-licensed freehire (commit bed5ec9) vouch.go and JobSpy bdjobs
sources (see NOTICE.md); the code is original. The container this was written in could not reach either host, so every parser
is tested on fixtures shaped like the references' data only.

Already read elsewhere (skipped, not rebuilt):
  freehire seek.go     SEEK (AU, NZ) and Jobstreet/JobsDB are the ``jobstreet`` feed of kit_gov.py.
  freehire pythonorg.go  the python.org jobs feed is ``pythonjobs`` in kit_niche.py.
NOT built:
  JobSpy hellowork (France, French-language): jobspy/hellowork/__init__.py:79 opens its session with ``TLSRotating``, which
  is a Chrome TLS fingerprint (jobspy/util.py:77-87, ``impersonate="chrome"``). The kit's urllib helper cannot imitate one
  and never pretends to be a browser, so hellowork.com is reported as unsupported.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
    import kit_feeds as _kf  # type: ignore[import-not-found]
    import kit_sites_b as _sb  # type: ignore[import-not-found]
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
    import kit_feeds as _kf
    import kit_sites_b as _sb
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _body,
    _iso_date,
    _job,
    _page_st,
    _remote_from,
    _wrap,
)
from kit_sites_a import (  # type: ignore[import-not-found]  # noqa: E402  # sibling file, not a package module
    _bracket,
    _decode_flight,
)

Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]
_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _kf._feed
_keep: Callable[..., list[Job]] = _kf._keep
_queries: Callable[[list[str]], list[str]] = _kf._queries
_gated: Callable[[list[str], str], bool] = _kf._gated
_made: Callable[..., Job] = _sb._made
_unique: Callable[[list[Job]], list[Job]] = _sb._unique
_Budget: Any = _sb._Budget
_fetch: Callable[..., Any] = _sb._fetch

VOUCH_HOST = "vouch.careers"
VOUCH_BASE = "https://vouch.careers"
BDJOBS_SEARCH = "https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch"
BDJOBS_DETAILS = "https://gateway.bdjobs.com/jobapply/api/JobSubsystem/Job-Details"
BDJOBS_JOB = "https://bdjobs.com/h/details/"
BDJOBS_PAGES = 4  # 50 postings a page per query (bdjobs/constant.py:3); the reference walks every page
BDJOBS_DETAILS_CAP = 15  # description requests per run
BDJOBS_MAX_REQUESTS = 30  # hard cap per run: search pages plus detail calls
PAUSE_S = 1.0  # between two requests to BDJobs (tests set it to 0)
_WS = re.compile(r"\s+")


def _clean(value: Any) -> str:
    return _WS.sub(" ", html.unescape(str(value or "")).replace("�", "")).strip()


# --------------------------------------------------------------------------- Vouch
# Borrowed from freehire internal/ingest/sources/vouch.go:11-18 and :31-33 (GET https://vouch.careers/companies/<board>: a
# Next.js App Router page that inlines every posting in its RSC flight stream as a "listings":[...] array, so one GET per
# board assembles every Job), :36-40 (a page with no flight, or a flight with no listings array, fails the board), :56-59 (a
# listing that is not activated, or is a draft, or is unlisted, or has no id, is not a vacancy), :61-70 (the posting link is
# the listing's own relative url resolved against https://vouch.careers, the canonical /companies/<board>/<id> when absent),
# :72-74 and :112-122 (company.name else the row's company; workMode: remote wins over hybrid over on-site among the
# employmentType tokens), :96-111 (the body is the pitch, HTML-escaped, then description, must and nice), :150-173 (the
# locations: address, else city, administrative_area, country) and vouch_test.go:21-34, :70-105 (shapes). A resolved link on
# any host but vouch.careers, or not https, is NOT IN REFERENCE (vouch.go:68 returns whatever resolves): the canonical link is
# used instead, so no job link leaves the board's own host.
_VOUCH_BOARD = re.compile(r"[A-Za-z0-9_-]{1,64}")
_VOUCH_MODES = {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite"}


def _vouch_link(listing: dict[str, Any], board: str, jid: str) -> str:
    """The listing's own link when it resolves to https://vouch.careers/companies/..., else the canonical one."""
    canonical = f"{VOUCH_BASE}/companies/{board}/{quote(jid, safe='')}"
    raw = listing.get("url")
    if isinstance(raw, str) and raw.strip():
        try:
            u = urlsplit(urljoin(VOUCH_BASE + "/", raw.strip()))
            if u.scheme == "https" and u.hostname == VOUCH_HOST and u.netloc == VOUCH_HOST and u.path.startswith("/companies/"):
                return u._replace(fragment="").geturl()
        except ValueError:
            pass
    return canonical


def _vouch_place(listing: dict[str, Any]) -> str:
    locs = listing.get("locations")
    first = locs[0] if isinstance(locs, list) and locs else None
    if not isinstance(first, dict):
        return ""
    address = _clean(first.get("address"))
    if address:
        return address
    parts = [_clean(first.get(k)) for k in ("city", "administrative_area", "country")]
    return ", ".join(dict.fromkeys(p for p in parts if p))


def _vouch_mode(listing: dict[str, Any]) -> str:
    tokens = listing.get("employmentType")
    modes = {_VOUCH_MODES.get(str(t).strip().lower(), "") for t in tokens} if isinstance(tokens, list) else set()
    for m in ("remote", "hybrid", "onsite"):
        if m in modes:
            return m
    return ""


def _vouch_body(listing: dict[str, Any]) -> str:
    def text(key: str) -> str:
        v = listing.get(key)
        return v if isinstance(v, str) else ""
    pitch = text("pitch")
    return (f"<p>{html.escape(pitch)}</p>" if pitch else "") + text("description") + text("must") + text("nice")


def _vouch(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Vouch company page: the flight stream's ``listings`` array, one GET per board."""
    board = str(row.get("token") or "").strip()
    if not _VOUCH_BOARD.fullmatch(board):
        return None
    _st, page = _page_st(http, f"{VOUCH_BASE}/companies/{board}")
    if page is None:
        return None
    raw = _bracket(_decode_flight(page), '"listings":', "[", "]")
    try:
        listings = json.loads(raw, strict=False) if raw else None
    except (ValueError, RecursionError):  # deeply nested JSON must cost only this board
        return None
    if not isinstance(listings, list):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for listing in listings:
        try:
            if not isinstance(listing, dict):
                continue
            jid = listing.get("id")
            title = _clean(listing.get("title"))
            if not isinstance(jid, str) or not jid or jid in seen or not title:
                continue
            if listing.get("activated") is not True or listing.get("draft") is True or listing.get("unlisted") is True:
                continue
            seen.add(jid)
            company = listing.get("company")
            place = _vouch_place(listing)
            mode = _vouch_mode(listing)
            jobs.append(_job(row, "vouch", title=title, url=_vouch_link(listing, board, jid), location=place,
                             posted=_iso_date(listing.get("publishedAt")), description=_body(_vouch_body(listing)),
                             remote=True if mode == "remote" else _remote_from(place),
                             extra={"job_id": jid, "work_mode": mode}))
            name = _clean(company.get("name")) if isinstance(company, dict) else ""
            if name:
                jobs[-1]["company"] = name  # the employer named on the listing, else the row's company
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "vouch": _wrap(_vouch),
}


# --------------------------------------------------------------------------- BDJobs
# Borrowed from JobSpy (MIT) jobspy/bdjobs/__init__.py:37-39 (GET https://api.bdjobs.com/Jobs/api/JobSearch/GetJobSearch,
# details at https://gateway.bdjobs.com/jobapply/api/JobSubsystem/Job-Details), :46-72 (paged by ``pg`` from 1; page 1 lists
# ``premiumData`` before ``data``; the walk ends at common.totalpages or a page with nothing new; a failed page ends the
# search), :74-94 and constant.py:3-9 (params rpp=50, isPro=0, ToggleJobs=true, isFresher=false, keyword; a Bangladeshi district
# filter is not used), :96-124 (Jobid, publishDate, WorkPlace "Home" = remote, location without "Anywhere in Bangladesh",
# jobTitle, companyName, Salary, link https://bdjobs.com/h/details/<Jobid>), :146-153 (salary "Tk. N[ - M] (Monthly)"), :156-198
# and constant.py:15-26 (details: ln=1, IsCorporate=false, data[0] sections joined under their headings; ApplyURL is not read, so
# the link always stays on bdjobs.com), bdjobs/model.py. Keyword search is the site's own, then the kit's keyword match runs on
# the result.
_BD_ID = re.compile(r"[A-Za-z0-9_-]{1,24}")
_BD_SALARY = re.compile(r"Tk\. (\d{1,9})(?: - (\d{1,9}))? \(Monthly\)")
_BD_SECTIONS = (
    ("Job Highlights", "JobKeyPoints"), ("Responsibilities", "JobDescription"), ("Education", "EducationRequirements"),
    ("Experience", "experience"), ("Additional Requirements", "AdditionJobRequirements"),
    ("Compensation & Other Benefits", "JobOtherBenifits"), ("Apply Instructions", "ApplyInstruction"),
)


def _bd_cards(payload: Any, first_page: bool) -> tuple[list[dict[str, Any]], int]:
    """(search rows with page-1 premium rows first, common.totalpages or 0)."""
    if not isinstance(payload, dict):
        return [], 0
    rows: list[Any] = []
    if first_page and isinstance(payload.get("premiumData"), list):
        rows += payload["premiumData"]
    if isinstance(payload.get("data"), list):
        rows += payload["data"]
    common = payload.get("common")
    pages = common.get("totalpages") if isinstance(common, dict) else 0
    return [r for r in rows if isinstance(r, dict)], pages if isinstance(pages, int) and not isinstance(pages, bool) else 0


def _bd_job(card: dict[str, Any]) -> Job | None:
    """One search row as a Job (description empty), None for an unusable row."""
    jid = card.get("Jobid")
    jid = str(jid) if isinstance(jid, (str, int)) and not isinstance(jid, bool) else ""
    title, company = _clean(card.get("jobTitle")), _clean(card.get("companyName"))
    if not _BD_ID.fullmatch(jid) or not title or not company:
        return None
    place = _clean(str(card.get("location") or "").replace("Anywhere in Bangladesh", "")).strip(", ")
    home = card.get("WorkPlace") == "Home"
    extra: dict[str, Any] = {"job_id": jid}
    m = _BD_SALARY.fullmatch(_clean(card.get("Salary")))
    if m:
        extra["salary"] = {"min": int(m.group(1)), "max": int(m.group(2) or m.group(1)), "currency": "BDT", "interval": "month"}
    job = _made("bdjobs", "BDJobs", title=title, company=company, url=BDJOBS_JOB + jid, place=place, country="Bangladesh",
                posted=_iso_date(card.get("publishDate")), description="", remote=True if home else None, extra=extra)
    return job


def _bd_description(payload: Any) -> str:
    """Plain text of data[0]'s sections, '' when the answer has none."""
    data = payload.get("data") if isinstance(payload, dict) else None
    d = data[0] if isinstance(data, list) and data and isinstance(data[0], dict) else None
    if d is None:
        return ""
    html_ = "".join(f"<h4>{h}</h4>{d[f]}" for h, f in _BD_SECTIONS if isinstance(d.get(f), str) and d[f].strip())
    return str(_body(html_))


@_feed
def fetch_bdjobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """BDJobs (Bangladesh): the site's keyword search, a few pages a query, then descriptions for the kept postings."""
    if not _gated(countries, "BD"):
        return []
    budget = _Budget(BDJOBS_MAX_REQUESTS)
    jobs: list[Job] = []
    for q in _queries(queries) or [""]:
        seen: set[str] = set()
        for page in range(1, BDJOBS_PAGES + 1):
            if page > 1:
                _sb._pause(PAUSE_S)
            params = {"rpp": 50, "isPro": 0, "ToggleJobs": "true", "isFresher": "false", "keyword": q, "pg": page}
            body = _fetch(http, stats, "bdjobs", budget, BDJOBS_SEARCH + "?" + urlencode(params), headers={"Accept": "application/json"})
            cards, last = _bd_cards(body, page == 1)
            fresh = 0
            for card in cards:
                try:
                    job = _bd_job(card)
                except Exception:  # noqa: BLE001, S112 - one bad row must not lose the search
                    continue
                if job and job["extra"]["job_id"] not in seen:
                    seen.add(job["extra"]["job_id"])
                    jobs.append(job)
                    fresh += 1
            if not fresh or page >= last or budget.left <= 0:
                break
    kept = _keep(_unique(jobs), queries, days)
    for job in kept[:BDJOBS_DETAILS_CAP]:
        if budget.left <= 0:
            break
        _sb._pause(PAUSE_S)
        params = {"jobId": job["extra"]["job_id"], "ln": "1", "IsCorporate": "false"}
        body = _fetch(http, stats, "bdjobs", budget, BDJOBS_DETAILS + "?" + urlencode(params), ad=True, headers={"Accept": "application/json"})
        try:
            text = _bd_description(body)
        except Exception:  # noqa: BLE001 - a bad detail leaves the posting listed without a body
            text = ""
        if text:
            job["description"] = text
            job["extra"].pop("title_only", None)
    return kept


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "bdjobs": fetch_bdjobs,
}
