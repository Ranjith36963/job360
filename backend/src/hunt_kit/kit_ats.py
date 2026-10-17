"""ATS board fetchers for the hunt kit: Workday, Workable, Recruitee, Personio, Teamtailor, BambooHR.

Each fetcher takes ``(row, http, stats)`` where ``row`` is a companies.json row
(``name``, ``ats``, ``token``; Workday also ``tenant``, ``wd``, ``site``) and returns
``list[Job]``. They never raise: any network or parse problem returns ``[]``.

Networking goes only through the ``http`` callable passed in (no urllib here).
Importing this module does no network and no file IO. Standard library only.

Endpoint patterns are adapted from the MIT-licensed career-ops providers
(santifer/career-ops). Nothing here ranks or scores a job.
"""
# ruff: noqa: E741, E501
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET

try:  # shared safe-XML helper (same folder)
    from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_xml", _Path(__file__).with_name("kit_xml.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_xml.py not found next to this file")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    safe_fromstring = _mod.safe_fromstring
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, cast

Job = dict[str, Any]
Http = Callable[..., "tuple[int, Any]"]

MAX_PAGES = 10  # hard cap on pages per board
SEARCH_PAGES = 2  # Workday: pages per searched title
WORKDAY_PAGE = 20

_TAG_RE = re.compile(r"<[^>]+>")
_BLOCK_RE = re.compile(r"</?(p|div|br|li|ul|ol|h[1-6]|tr)\b[^>]*>", re.I)
_WS_RE = re.compile(r"[ \t\r\f\v]+")
_NL_RE = re.compile(r"\n\s*\n+")


# --------------------------------------------------------------------------- helpers
def _text(value: Any) -> str:
    """Plain-text version of an HTML fragment (tags dropped, entities decoded)."""
    if not value:
        return ""
    s = _BLOCK_RE.sub("\n", str(value))
    s = _TAG_RE.sub("", s)
    s = re.sub(r"<[^>]*$", "", s)  # dangling, unterminated tag at the very end
    s = html.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = "\n".join(line.strip() for line in s.split("\n"))
    return _NL_RE.sub("\n", s).strip()


def _iso_date(value: Any) -> str:
    """First 10 chars of an ISO date/datetime string if valid, else ''."""
    if not value:
        return ""
    s = str(value).strip()[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return ""
    return s


def _rfc822_date(value: Any) -> str:
    """'Tue, 03 Sep 2024 10:00:00 +0000' -> '2024-09-03'; '' if unparseable."""
    if not value:
        return ""
    try:
        from email.utils import parsedate_to_datetime

        return parsedate_to_datetime(str(value)).strftime("%Y-%m-%d")
    except (TypeError, ValueError, IndexError):
        return _iso_date(value)


def _workday_posted(label: Any, today: date | None = None) -> str:
    """'Posted Today' / 'Posted 5 Days Ago' -> date; '30+ Days Ago' and others -> ''."""
    s = str(label or "").lower()
    today = today or datetime.now(timezone.utc).date()
    if "today" in s:
        return today.isoformat()
    if "yesterday" in s:
        return (today - timedelta(days=1)).isoformat()
    m = re.search(r"(\d+)\+?\s*days?\s*ago", s)
    if m and "+" not in m.group(0):
        return (today - timedelta(days=int(m.group(1)))).isoformat()
    return ""


def _join(*parts: Any) -> str:
    """Comma-join the non-empty parts, skipping consecutive duplicates."""
    out: list[str] = []
    for p in parts:
        p = str(p).strip() if p else ""
        if p and p not in out:
            out.append(p)
    return ", ".join(out)


def _job(row: dict[str, Any], source: str, *, title: str, url: str, location: str = "", posted: str = "",
         description: str = "", remote: bool | None = None, visa_flag: bool | None = None,
         extra: dict[str, Any] | None = None) -> Job:
    """Build a Job dict in the shared shape."""
    return {
        "title": (title or "").strip(),
        "company": str(row.get("name") or row.get("token") or ""),
        "url": url or "",
        "location": location or "",
        "posted": posted or "",
        "description": description or "",
        "source": source,
        "board": str(row.get("token") or row.get("tenant") or ""),
        "remote": remote,
        "visa_flag": visa_flag,
        "extra": extra or {},
    }


def _remote_from(*texts: Any) -> bool | None:
    """True when a location/workplace field literally says remote; else None (unknown)."""
    for t in texts:
        if t and "remote" in str(t).lower():
            return True
    return None


def _get(http: Http, url: str, *, as_json: bool = True, **kw: Any) -> tuple[int, Any]:
    """Call http and never raise."""
    try:
        return http(url, as_json=as_json, **kw)
    except Exception:  # noqa: BLE001 - fetchers must never raise
        return 0, None


def _count(stats: dict[str, Any], *, ok: bool, jobs: int = 0) -> None:
    """Update the shared tried/ok/jobs counters."""
    stats["tried"] = stats.get("tried", 0) + 1
    if ok:
        stats["ok"] = stats.get("ok", 0) + 1
    stats["jobs"] = stats.get("jobs", 0) + jobs


# --------------------------------------------------------------------------- Workday
def _workday_base(row: dict[str, Any]) -> tuple[str, str, str]:
    """(origin, tenant, site) for a Workday row; tenant defaults to token, wd to 'wd1'."""
    tenant = str(row.get("tenant") or row.get("token") or "")
    wd = str(row.get("wd") or "wd1")
    if wd.isdigit():
        wd = "wd" + wd
    site = str(row.get("site") or "")
    return f"https://{tenant}.{wd}.myworkdayjobs.com", tenant, site


def fetch_workday(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Workday CXS: POST list (20/page, max 10 pages) then GET each posting's detail."""
    try:
        origin, tenant, site = _workday_base(row)
        if not (tenant and site):
            _count(stats, ok=False)
            return []
        api = f"{origin}/wday/cxs/{tenant}/{site}/jobs"
        hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
        postings: list[dict[str, Any]] = []
        ok = False
        # With the user's titles, ask the board's own search for each one (a few pages), so only
        # matching postings get a detail fetch. Without them, list the whole board (capped).
        raw = row.get("search_texts") or []
        texts = [str(s) for s in (raw.split("\n") if isinstance(raw, str) else raw) if str(s).strip()]
        seen_paths: set[str] = set()
        for text, max_pages in ([(s, SEARCH_PAGES) for s in texts] or [("", MAX_PAGES)]):
            for page in range(max_pages):
                body = json.dumps({"appliedFacets": {}, "limit": WORKDAY_PAGE,
                                   "offset": page * WORKDAY_PAGE, "searchText": text}).encode()
                status, data = _get(http, api, method="POST", data=body, headers=hdrs)
                if status != 200 or not isinstance(data, dict):
                    break
                ok = True
                batch = data.get("jobPostings") or []
                for p in batch:
                    key = str(p.get("externalPath") or "") if isinstance(p, dict) else ""
                    if isinstance(p, dict) and key not in seen_paths:
                        seen_paths.add(key)
                        postings.append(p)
                total = data.get("total")
                if len(batch) < WORKDAY_PAGE or (isinstance(total, int) and (page + 1) * WORKDAY_PAGE >= total):
                    break
        jobs: list[Job] = []
        for p in postings:
            path = str(p.get("externalPath") or "")
            if not path:
                continue
            desc, posted, loc, country = "", _workday_posted(p.get("postedOn")), str(p.get("locationsText") or ""), ""
            st, det = _get(http, f"{origin}/wday/cxs/{tenant}/{site}{path}", headers={"Accept": "application/json"})
            info = det.get("jobPostingInfo") if st == 200 and isinstance(det, dict) else None
            if isinstance(info, dict):
                desc = _text(info.get("jobDescription"))
                posted = _iso_date(info.get("startDate")) or posted
                loc = str(info.get("location") or loc)
                country = str((info.get("country") or {}).get("descriptor") or "")
            jobs.append(_job(
                row, "workday", title=str(p.get("title") or ""), url=f"{origin}/{site}{path}",
                location=_join(loc, country) if country and country not in loc else loc,
                posted=posted, description=desc, remote=_remote_from(loc, p.get("locationsText")),
                extra={"req_id": (p.get("bulletFields") or [""])[0] if p.get("bulletFields") else ""},
            ))
        _count(stats, ok=ok, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Workable
def fetch_workable(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Workable public widget API (one response, details=true gives descriptions)."""
    token = str(row.get("token") or "")
    try:
        status, data = _get(http, f"https://apply.workable.com/api/v1/widget/accounts/{token}?details=true")
        if status != 200 or not isinstance(data, dict):
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        for j in data.get("jobs") or []:
            if not isinstance(j, dict):
                continue
            locs = [l for l in (j.get("locations") or []) if isinstance(l, dict) and not l.get("hidden")]
            if locs:
                loc = "; ".join(_join(l.get("city"), l.get("region"), l.get("country")) for l in locs)
            else:
                loc = _join(j.get("city"), j.get("state"), j.get("country"))
            tele = j.get("telecommuting")
            jobs.append(_job(
                row, "workable", title=str(j.get("title") or ""),
                url=str(j.get("url") or j.get("shortlink") or j.get("application_url") or ""),
                location=loc, posted=_iso_date(j.get("published_on") or j.get("created_at")),
                description=_text(j.get("description")),
                remote=True if tele is True else (_remote_from(loc) or (False if tele is False else None)),
                extra={"department": j.get("department") or "", "employment_type": j.get("employment_type") or "",
                       "shortcode": j.get("shortcode") or ""},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Recruitee
def fetch_recruitee(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Recruitee public offers API."""
    token = str(row.get("token") or "")
    try:
        status, data = _get(http, f"https://{token}.recruitee.com/api/offers/")
        if status != 200 or not isinstance(data, dict):
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        for o in data.get("offers") or []:
            if not isinstance(o, dict):
                continue
            loc = str(o.get("location") or "") or _join(o.get("city"), o.get("country"))
            remote = o.get("remote")
            desc = "\n".join(x for x in (_text(o.get("description")), _text(o.get("requirements"))) if x)
            jobs.append(_job(
                row, "recruitee", title=str(o.get("title") or ""),
                url=str(o.get("careers_url") or f"https://{token}.recruitee.com/o/{o.get('slug', '')}"),
                location=loc, posted=_iso_date(o.get("published_at") or o.get("created_at")),
                description=desc,
                remote=True if remote is True else (_remote_from(loc) or (False if remote is False else None)),
                extra={"department": o.get("department") or "", "employment_type": o.get("employment_type_code") or ""},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Personio
def _parse_xml(data: Any) -> ET.Element:
    """Parse feed XML safely: DOCTYPE/ENTITY declarations are refused."""
    root = safe_fromstring(data)
    if root is None:
        raise ET.ParseError("refused or malformed XML")
    return cast(ET.Element, root)


def _child(el: ET.Element, name: str) -> str:
    """Text of the first child named ``name`` ('' if absent)."""
    c = el.find(name)
    return (c.text or "").strip() if c is not None and c.text else ""


def fetch_personio(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Personio XML feed (https://{token}.jobs.personio.de/xml)."""
    token = str(row.get("token") or "")
    try:
        status, data = _get(http, f"https://{token}.jobs.personio.de/xml", as_json=False)
        if status != 200 or not isinstance(data, str) or "<workzag-jobs" not in data:
            _count(stats, ok=False)
            return []
        root = _parse_xml(data)
        jobs: list[Job] = []
        for pos in root.findall("position"):
            pid = _child(pos, "id")
            offices = [_child(pos, "office")] + [(o.text or "").strip() for o in pos.findall("additionalOffices/office")]
            loc = "; ".join(dict.fromkeys(o for o in offices if o))
            parts = []
            for d in pos.findall("jobDescriptions/jobDescription"):
                parts.append(_text(_child(d, "name")))
                parts.append(_text(_child(d, "value")))
            jobs.append(_job(
                row, "personio", title=_child(pos, "name"),
                url=f"https://{token}.jobs.personio.de/job/{pid}" if pid else "",
                location=loc, posted=_iso_date(_child(pos, "createdAt")),
                description="\n".join(p for p in parts if p), remote=_remote_from(loc),
                extra={"department": _child(pos, "department"), "employment_type": _child(pos, "employmentType"),
                       "seniority": _child(pos, "seniority"), "schedule": _child(pos, "schedule")},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except (ET.ParseError, Exception):  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Teamtailor
_TT_NS = {"tt": "https://teamtailor.com/locations"}


def fetch_teamtailor(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Teamtailor RSS feed (https://{token}.teamtailor.com/jobs.rss)."""
    token = str(row.get("token") or "")
    try:
        status, data = _get(http, f"https://{token}.teamtailor.com/jobs.rss", as_json=False)
        if status != 200 or not isinstance(data, str) or "<rss" not in data:
            _count(stats, ok=False)
            return []
        root = _parse_xml(data)
        jobs: list[Job] = []
        for item in root.iter("item"):
            locs = []
            for l in item.iter("{https://teamtailor.com/locations}location"):
                city = (l.findtext("{https://teamtailor.com/locations}city") or "").strip()
                country = (l.findtext("{https://teamtailor.com/locations}country") or "").strip()
                name = (l.findtext("{https://teamtailor.com/locations}name") or "").strip()
                locs.append(_join(city or name, country))
            loc = "; ".join(dict.fromkeys(x for x in locs if x))
            rs = (item.findtext("remoteStatus") or "").strip().lower()
            remote: bool | None = None
            if rs:  # observed values: fully / hybrid / none
                remote = rs in ("fully", "full", "remote")
            elif _remote_from(loc):
                remote = True
            jobs.append(_job(
                row, "teamtailor", title=(item.findtext("title") or ""),
                url=(item.findtext("link") or "").strip(), location=loc,
                posted=_rfc822_date(item.findtext("pubDate")),
                description=_text(item.findtext("description")), remote=remote,
                extra={"department": (item.findtext("{https://teamtailor.com/locations}department") or "").strip(), "remote_status": rs},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except (ET.ParseError, Exception):  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- BambooHR
def _bamboo_loc(loc: Any) -> str:
    """Join city/state/country from a BambooHR location object."""
    if not isinstance(loc, dict):
        return ""
    return _join(loc.get("city"), loc.get("state") or loc.get("province"),
                 loc.get("country") or loc.get("addressCountry"))


def fetch_bamboohr(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """BambooHR careers list + per-posting detail (descriptions live in the detail)."""
    token = str(row.get("token") or "")
    try:
        base = f"https://{token}.bamboohr.com/careers"
        status, data = _get(http, f"{base}/list", headers={"Accept": "application/json"})
        if status != 200 or not isinstance(data, dict):
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        for r in (data.get("result") or [])[: MAX_PAGES * 20]:
            if not isinstance(r, dict) or not r.get("id"):
                continue
            jid = str(r["id"])
            loc = _bamboo_loc(r.get("atsLocation")) or _bamboo_loc(r.get("location"))
            desc, posted, remote = "", "", r.get("isRemote")
            st, det = _get(http, f"{base}/{jid}/detail", headers={"Accept": "application/json"})
            opening = ((det.get("result") or {}).get("jobOpening")) if st == 200 and isinstance(det, dict) else None
            if isinstance(opening, dict):
                desc = _text(opening.get("description"))
                posted = _iso_date(opening.get("datePosted"))
                loc = _bamboo_loc(opening.get("atsLocation")) or _bamboo_loc(opening.get("location")) or loc
                if remote is None:
                    remote = opening.get("isRemote")
            jobs.append(_job(
                row, "bamboohr", title=str(r.get("jobOpeningName") or ""), url=f"{base}/{jid}",
                location=loc, posted=posted, description=desc,
                remote=True if remote in (True, "1", 1) else (_remote_from(loc)),
                extra={"department": r.get("departmentLabel") or "", "employment_type": r.get("employmentStatusLabel") or ""},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "workday": fetch_workday,
    "workable": fetch_workable,
    "recruitee": fetch_recruitee,
    "personio": fetch_personio,
    "teamtailor": fetch_teamtailor,
    "bamboohr": fetch_bamboohr,
}
