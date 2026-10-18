"""ATS board fetchers for the hunt kit: Workday, Workable, Recruitee, Personio, Teamtailor, BambooHR,
iCIMS, Oracle Recruiting Cloud, SuccessFactors, Taleo, Jobvite, Eightfold.

Each fetcher takes ``(row, http, stats)`` where ``row`` is a companies.json row
(``name``, ``ats``, ``token``; Workday also ``tenant``, ``wd``, ``site``) and returns
``list[Job]``. They never raise: any network or parse problem returns ``[]``.

Networking goes only through the ``http`` callable passed in (urllib.parse only builds URLs).
Importing this module does no network and no file IO. Standard library only.

Endpoint patterns are adapted from the MIT-licensed career-ops providers
(santifer/career-ops). Nothing here ranks or scores a job.
"""
# ruff: noqa: E741, E501
from __future__ import annotations

import html
import json
import re
import sys
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
from urllib.parse import quote, unquote, urljoin, urlsplit, urlunsplit

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


# --------------------------------------------------------------------------- iCIMS
# Port of career-ops providers/icims.mjs (MIT): public hosted-portal search pages; the
# list cards carry title/location/url but no date, the detail page carries JSON-LD.
ICIMS_DETAIL_CAP = 20  # detail-page requests per board (the rest come back undated)
_ICIMS_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9-]*", re.I)
_ICIMS_HREF_RE = re.compile(r"""href=["']([^"']*/jobs/\d+/[^"'/]+/job[^"']*)["']""", re.I)
_ICIMS_H3_RE = re.compile(r"<h3\b[^>]*>\s*([\s\S]*?)</h3>", re.I)
_ICIMS_LOC_RE = re.compile(
    r"""<span\b[^>]*class=["'][^"']*(?<![\w-])field-label(?![\w-])[^"']*["'][^>]*>\s*Location\s*</span>"""
    r"""\s*<span\b[^>]*>\s*([\s\S]*?)</span>""", re.I)
_ICIMS_LD_RE = re.compile(
    r"""<script\b[^>]*(?<![\w-])type=["']application/ld\+json["'][^>]*>([\s\S]*?)</script>""", re.I)
_ICIMS_COUNTRY = {"US": "United States", "CA": "Canada"}


def _icims_origin(row: dict[str, Any]) -> str:
    """'https://careers-acme.icims.com' from the board token ('' if the token is unusable)."""
    token = str(row.get("token") or "").strip().lower()
    if token.endswith(".icims.com"):
        token = token[: -len(".icims.com")]
    if not _ICIMS_TOKEN_RE.fullmatch(token):
        return ""
    return f"https://{token}.icims.com"


def _icims_cards(page: str, origin: str) -> list[dict[str, str]]:
    """Parse one search-results page into {title, url, location} cards (same-origin links only)."""
    cards: list[dict[str, str]] = []
    for card in str(page).split("iCIMS_JobCardItem")[1:]:
        m = _ICIMS_HREF_RE.search(card)
        t = _ICIMS_H3_RE.search(card)
        if not m or not t:
            continue
        url = urljoin(origin + "/", html.unescape(m.group(1)).strip())
        parts = urlsplit(url)
        if f"{parts.scheme}://{parts.netloc}".lower() != origin:  # a portal never links off-host
            continue
        title = _text(t.group(1)).replace("\n", " ").strip()
        if not title:
            continue
        loc = _ICIMS_LOC_RE.search(card)
        cards.append({"title": title, "url": f"{origin}{parts.path}",
                      "location": _text(loc.group(1)).replace("\n", " ").strip() if loc else ""})
    return cards


def _icims_nodes(page: str) -> list[Any]:
    """Flatten every JSON-LD block (object, array or @graph) of a detail page."""
    nodes: list[Any] = []
    for raw in _ICIMS_LD_RE.findall(str(page)):
        try:
            data = json.loads(html.unescape(raw) if "&quot;" in raw else raw)
        except ValueError:
            continue
        if isinstance(data, list):
            nodes.extend(data)
        elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
            nodes.extend(data["@graph"])
        else:
            nodes.append(data)
    return [n for n in nodes if isinstance(n, dict)]


def _icims_posting(nodes: list[Any]) -> dict[str, Any]:
    """The JobPosting node (first with a datePosted/description if none is typed)."""
    fallback: dict[str, Any] = {}
    for n in nodes:
        t = n.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            return cast("dict[str, Any]", n)
        if not fallback and (n.get("datePosted") or n.get("jobLocation")):
            fallback = n
    return fallback


def _icims_location(posting: dict[str, Any]) -> str:
    """'Locality, Region, Country' from the first usable jobLocation address (UNAVAILABLE dropped)."""
    places = posting.get("jobLocation")
    for place in places if isinstance(places, list) else [places]:
        addr = place.get("address") if isinstance(place, dict) else None
        if not isinstance(addr, dict):
            continue

        def clean(v: Any) -> str:
            s = str(v or "").strip()
            return "" if s.lower() == "unavailable" else s

        country = clean(addr.get("addressCountry"))
        loc = _join(clean(addr.get("addressLocality")), clean(addr.get("addressRegion")),
                    _ICIMS_COUNTRY.get(country.upper(), country))
        if loc:
            return loc
    return ""


def fetch_icims(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """iCIMS hosted portal: paged HTML search list + per-posting JSON-LD detail (date, description)."""
    try:
        origin = _icims_origin(row)
        if not origin:
            _count(stats, ok=False)
            return []
        cards: list[dict[str, str]] = []
        seen: set[str] = set()
        prev_first = ""
        for page in range(MAX_PAGES):
            status, body = _get(http, f"{origin}/jobs/search?ss=1&pr={page}&in_iframe=1", as_json=False,
                                headers={"Accept": "text/html", "Accept-Language": "en-US,en;q=0.9"})
            if status != 200 or not isinstance(body, str):
                if page == 0:
                    _count(stats, ok=False)
                    return []
                break  # a later page failing keeps what we already have
            found = _icims_cards(body, origin)
            if not found or found[0]["url"] == prev_first:  # past the last page, or the same page again
                break
            prev_first = found[0]["url"]
            for c in found:
                if c["url"] not in seen:
                    seen.add(c["url"])
                    cards.append(c)
        jobs: list[Job] = []
        for i, c in enumerate(cards):
            loc, posted, desc, remote, extra = c["location"], "", "", None, {}
            if i < ICIMS_DETAIL_CAP:
                st, det = _get(http, f"{c['url']}?in_iframe=1", as_json=False, headers={"Accept": "text/html"})
                posting = _icims_posting(_icims_nodes(det)) if st == 200 and isinstance(det, str) else {}
                if posting:
                    posted = _iso_date(posting.get("datePosted"))
                    raw = str(posting.get("description") or "")
                    desc = _text(html.unescape(raw) if "<" not in raw and "&lt;" in raw else raw)
                    loc = loc if loc and loc.lower() not in ("n/a", "na") else _icims_location(posting)
                    if str(posting.get("jobLocationType") or "").upper() == "TELECOMMUTE":
                        remote = True
                    et = posting.get("employmentType")
                    extra = {"employment_type": ", ".join(et) if isinstance(et, list) else str(et or "")}
            jobs.append(_job(row, "icims", title=c["title"], url=c["url"], location=loc, posted=posted,
                             description=desc, remote=remote or _remote_from(loc), extra=extra))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Oracle Recruiting Cloud
# Board row: {"name": "JPMorgan Chase", "ats": "oraclecloud", "token": "jpmc",
#             "host": "jpmc.fa.oraclecloud.com", "siteNumber": "CX_1001", "lang": "en",
#             "locationId": "300000000289360"}   # lang (default "en") and locationId (optional) are optional
ORACLE_HOST_RE = re.compile(r"^[a-z0-9-]+\.fa\.(?:[a-z0-9-]+\.)?(?:ocs\.)?oraclecloud(?:[1-9][0-9]?)?\.com$", re.I)
ORACLE_PAGE = 200
ORACLE_PAGES = MAX_PAGES  # hard cap (the reference caps at 25; the kit cap is stricter)
_ORACLE_FACETS = "LOCATIONS%3BWORK_LOCATIONS%3BWORKPLACE_TYPES%3BTITLES%3BCATEGORIES%3BORGANIZATIONS%3BPOSTING_DATES%3BFLEX_FIELDS"
_ORACLE_EXPAND = "requisitionList.workLocation,requisitionList.secondaryLocations"
_SAFE_SEG_RE = re.compile(r"[A-Za-z0-9_-]+")


def _oracle_site(row: dict[str, Any]) -> tuple[str, str, str, str] | None:
    """(host, siteNumber, lang, locationId) from a row, or None when the host is not an ORC host."""
    host = str(row.get("host") or "").strip().lower()
    site = str(row.get("siteNumber") or row.get("site") or "CX_1").strip()
    lang = str(row.get("lang") or "en").strip()
    loc_id = str(row.get("locationId") or "").strip()
    if not ORACLE_HOST_RE.fullmatch(host):
        return None
    if not _SAFE_SEG_RE.fullmatch(site) or not _SAFE_SEG_RE.fullmatch(lang):
        return None
    if loc_id and not (loc_id.isascii() and loc_id.isdigit()):
        return None
    return host, site, lang, loc_id


def _oracle_url(host: str, site: str, loc_id: str, offset: int) -> str:
    """Requisitions API URL; limit/offset are set in the finder AND top level (some tenants honour one)."""
    parts = [f"siteNumber={site}", f"facetsList={_ORACLE_FACETS}", f"limit={ORACLE_PAGE}",
             "sortBy=POSTING_DATES_DESC", f"offset={offset}"]
    if loc_id:
        parts.append(f"locationId={loc_id}")
    return (f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions?onlyData=true"
            f"&expand={_ORACLE_EXPAND}&finder=findReqs;{','.join(parts)}&limit={ORACLE_PAGE}&offset={offset}")


def _oracle_location(req: dict[str, Any]) -> tuple[str, bool | None]:
    """(location, remote) from PrimaryLocation, else the expanded workLocation; WorkplaceTypeCode adds a hint."""
    base = str(req.get("PrimaryLocation") or "").strip()
    wl = req.get("workLocation")
    if not base and isinstance(wl, list) and wl and isinstance(wl[0], dict):
        base = _join(wl[0].get("TownOrCity"), wl[0].get("Region"), wl[0].get("Country"))
    wt = req.get("WorkplaceTypeCode")
    hint = "Remote" if wt == "ORA_REMOTE" else ("Hybrid" if wt == "ORA_HYBRID" else "")
    remote = True if wt == "ORA_REMOTE" else _remote_from(base)
    return " · ".join(x for x in (base, hint) if x), remote


def fetch_oraclecloud(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Oracle Recruiting Cloud (Fusion Candidate Experience) public requisitions API (GET, no auth)."""
    try:
        site = _oracle_site(row)
        if site is None:
            _count(stats, ok=False)
            return []
        host, site_no, lang, loc_id = site
        jobs: list[Job] = []
        seen: set[str] = set()
        total: int | None = None
        got_any = False
        for page in range(ORACLE_PAGES):
            offset = page * ORACLE_PAGE
            status, data = _get(http, _oracle_url(host, site_no, loc_id, offset))
            if status != 200 or not isinstance(data, dict):
                if page == 0:
                    _count(stats, ok=False)
                    return []
                break
            items = data.get("items")
            item = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
            lst = item.get("requisitionList")
            lst = lst if isinstance(lst, list) else []
            got_any = True
            if total is None and isinstance(item.get("TotalJobsCount"), int) and not isinstance(item.get("TotalJobsCount"), bool):
                total = item["TotalJobsCount"]
            for req in lst:
                if not isinstance(req, dict):
                    continue
                rid = str(req.get("Id") if req.get("Id") is not None else (req.get("RequisitionNumber") or "")).strip()
                if not _SAFE_SEG_RE.fullmatch(rid):
                    rid = ""
                ext = str(req.get("ExternalURL") or "").strip()
                url = ext if ext.startswith("https://") else (
                    f"https://{host}/hcmUI/CandidateExperience/{lang}/sites/{site_no}/job/{rid}" if rid else "")
                if not url or url in seen:
                    continue
                seen.add(url)
                loc, remote = _oracle_location(req)
                jobs.append(_job(
                    row, "oraclecloud", title=str(req.get("Title") or ""), url=url, location=loc,
                    posted=_iso_date(req.get("PostedDate")),
                    description=_text(req.get("ShortDescriptionStr")), remote=remote,
                    extra={"workplace_type": req.get("WorkplaceTypeCode") or "", "req_id": rid},
                ))
            # hasMore is unreliable (JPMC says false on every page): an empty page ends the walk; a short
            # page only ends it when the tenant gives no total (Amex serves 200, 199, 54 of 454).
            if not lst:
                break
            if total is not None:
                if offset + ORACLE_PAGE >= total:
                    break
            elif len(lst) < ORACLE_PAGE:
                break
        _count(stats, ok=got_any, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- SuccessFactors
# Port (re-written in Python) of career-ops providers/successfactors.mjs (MIT).
# Row: {"name": "ZF", "ats": "successfactors", "token": "https://jobs.zf.com",
#       "variant": "csb" (optional: skip the tile endpoint), "locales": ["de_DE"] (optional)}
# ``token`` is the career-site URL; a trailing /search/ or /go/<Cat>/<id>/ is stripped.
SF_CSB_PAGE = 10  # fixed by the /services/recruiting/v1/jobs API
SF_CSB_MAX_LOCALES = 4
SF_CSB_DEFAULT_LOCALES = ("de_DE", "en_US")
SF_CSB_LOCALE_PRIORITY = ("de_DE", "en_US", "en_GB", "en_EN")
SF_CSB_MONTH_FIRST = ("en_US", "es_US")
_SF_TILE_RE = re.compile(r'<li class="job-tile job-id-(\d+)\b[\s\S]*?</li>')
_SF_URL_RE = re.compile(r'data-url="([^"]+)"')
_SF_TITLE_RE = re.compile(r'class="jobTitle-link[^"]*"[^>]*>([\s\S]*?)</a>')
_SF_CITY_RE = re.compile(r'id="[^"]*-section-city-value">([\s\S]*?)</div>')
_SF_LOCALE_RE = re.compile(r"locale=([a-z]{2}_[A-Z]{2})\b")


def _sf_config(row: dict[str, Any]) -> dict[str, str] | None:
    """Origin/base URLs for a SuccessFactors career site, or None if the token is not a URL."""
    raw = str(row.get("api") or row.get("token") or "").strip()
    if raw and "://" not in raw:
        raw = "https://" + raw
    try:
        u = urlsplit(raw)
    except ValueError:
        return None
    if u.scheme not in ("http", "https") or not u.netloc:
        return None
    if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?(?::\d{1,5})?", u.netloc):
        return None  # no userinfo, spaces, brackets: host[:port] only
    if not re.fullmatch(r"(?:/[A-Za-z0-9._~%!$'()*+,;=:@-]*)*/?", u.path):
        return None  # brand prefix must be plain path segments
    if any(seg in (".", "..") for seg in u.path.split("/")):
        return None
    path = re.sub(r"/go/[^/]+/\d+(?:/\d+)?/?$", "", u.path, flags=re.I)
    path = re.sub(r"/(?:search|tile-search-results|services/recruiting/v1/jobs)/?$", "", path, flags=re.I)
    path = path.rstrip("/")
    origin = f"{u.scheme}://{u.netloc}"
    base = origin + path
    return {"origin": origin, "base": base, "tile": f"{base}/tile-search-results/",
            "jobs_api": f"{base}/services/recruiting/v1/jobs", "search": f"{base}/search/"}


def _sf_clean(s: Any) -> str:
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", str(s)))).strip()


def _sf_city_from_slug(data_url: str, title: str) -> str:
    """RMK paths are /job/{City-Words}-{Title-Words}-{code}/{id}/: strip the title off the slug."""
    m = re.search(r"/job/([^/]+)/", unquote(data_url))
    words = re.findall(r"[^\W_]+", title.lower())
    if not m or not words:
        return ""
    slug = m.group(1).lower()
    anchor = r"[\W_]+".join(re.escape(w) for w in words[:2])
    hit = re.search(anchor, slug)
    if not hit or hit.start() <= 0:
        return ""
    return " ".join(w.capitalize() for w in re.split(r"[\W_]+", slug[: hit.start()]) if w)


def _sf_parse_tiles(page: str, origin: str) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for t in _SF_TILE_RE.finditer(page):
        block = t.group(0)
        um, tm = _SF_URL_RE.search(block), _SF_TITLE_RE.search(block)
        title = _sf_clean(tm.group(1)) if tm else ""
        if not um or not title:
            continue
        path = html.unescape(um.group(1))
        cm = _SF_CITY_RE.search(block)
        city = _sf_clean(cm.group(1)) if cm else _sf_city_from_slug(path, title)
        url = path if re.match(r"https?://", path, re.I) else origin + (path if path.startswith("/") else "/" + path)
        out.append({"id": t.group(1), "title": title, "url": url, "location": city})
    return out


def _sf_csb_date(raw: Any, locale: str = "") -> str:
    """CSB dates follow the query locale: en_US M/D/YY, de_DE D.M.YY, fr_FR D/M/YYYY, 2025. 07. 03."""
    if not isinstance(raw, str):
        return ""
    s = raw.strip()
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", s):
        a, b, y = m.groups()
        mo, d = (a, b) if (not locale or locale in SF_CSB_MONTH_FIRST) else (b, a)
    elif m := re.fullmatch(r"(\d{1,2})\.(\d{1,2})\.(\d{2,4})", s):
        d, mo, y = m.groups()
    elif m := re.fullmatch(r"(\d{4})[.\-/]\s*(\d{1,2})[.\-/]\s*(\d{1,2})\.?", s):
        y, mo, d = m.groups()
    else:
        return ""
    yi = int(y) + (2000 if int(y) < 100 else 0)
    try:
        return date(yi, int(mo), int(d)).isoformat()
    except ValueError:
        return ""


def _sf_locales(page: Any) -> list[str]:
    """Locales advertised by the language switcher on {base}/search/ (priority-ordered)."""
    found = sorted(set(_SF_LOCALE_RE.findall(page if isinstance(page, str) else "")))
    pr = SF_CSB_LOCALE_PRIORITY
    found.sort(key=lambda x: (pr.index(x) if x in pr else len(pr), x))
    return found[:SF_CSB_MAX_LOCALES]


def _sf_text(data: Any) -> str:
    return data.decode("utf-8", "replace") if isinstance(data, (bytes, bytearray)) else (data if isinstance(data, str) else "")


def _sf_csb(row: dict[str, Any], cfg: dict[str, str], http: Http) -> tuple[list[Job], bool]:
    """CSB JSON API: POST {jobs_api}, 10/page, one pass per advertised locale. Returns (jobs, any_ok)."""
    raw_loc = row.get("locales")
    locales = [x for x in raw_loc if isinstance(x, str) and re.fullmatch(r"[a-z]{2}_[A-Z]{2}", x)] if isinstance(raw_loc, list) else []
    if not locales:
        st, page = _get(http, cfg["search"], as_json=False, headers={"Accept": "text/html"})
        locales = (_sf_locales(_sf_text(page)) if st == 200 else []) or list(SF_CSB_DEFAULT_LOCALES)
    hdrs = {"Content-Type": "application/json", "Accept": "application/json"}
    jobs: list[Job] = []
    seen: set[str] = set()
    any_ok = False
    for locale in locales[:SF_CSB_MAX_LOCALES]:
        for pg in range(MAX_PAGES):
            body = json.dumps({"keywords": "", "locale": locale, "location": "", "pageNumber": pg,
                               "sortBy": "date"}).encode()
            status, data = _get(http, cfg["jobs_api"], method="POST", data=body, headers=hdrs)
            if status != 200 or not isinstance(data, dict):
                break
            any_ok = True
            results = data.get("jobSearchResult") or []
            for item in results:
                r = item.get("response") if isinstance(item, dict) else None
                if not isinstance(r, dict):
                    continue
                jid = str(r.get("id") or "")
                title = _sf_clean(r.get("unifiedStandardTitle") or r.get("jobTitle") or "")
                if not jid or not title or jid in seen:
                    continue
                seen.add(jid)
                slug = html.unescape(str(r.get("unifiedUrlTitle") or r.get("urlTitle") or "job"))
                slug = re.sub(r"-{2,}", "-", re.sub(r"[?#&]+", "-", slug))
                locs = r.get("jobLocationShort")
                parts = [_sf_clean(p) for p in (locs if isinstance(locs, list) else [locs] if locs else [])]
                loc = " / ".join(dict.fromkeys(p for p in parts if p))
                jobs.append(_job(row, "successfactors", title=title, url=f"{cfg['base']}/job/{slug}/{jid}-{locale}",
                                 location=loc, posted=_sf_csb_date(r.get("unifiedStandardStart"), locale),
                                 remote=_remote_from(loc), extra={"locale": locale}))
            total = data.get("totalJobs")
            if len(results) < SF_CSB_PAGE or (isinstance(total, int) and (pg + 1) * SF_CSB_PAGE >= total):
                break
    return jobs, any_ok


def fetch_successfactors(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """SAP SuccessFactors Recruiting Marketing site: tile-search fragment, else CSB JSON API."""
    try:
        cfg = _sf_config(row)
        if cfg is None:
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        ok = False
        if str(row.get("variant") or "").lower() != "csb":
            seen: set[str] = set()
            startrow = 0
            for _ in range(MAX_PAGES):
                status, data = _get(http, f"{cfg['tile']}?startrow={startrow}", as_json=False,
                                    headers={"Accept": "text/html"})
                if status != 200:
                    break
                ok = True
                tiles = _sf_parse_tiles(_sf_text(data), cfg["origin"])
                fresh = 0
                for t in tiles:
                    if t["id"] in seen:
                        continue
                    seen.add(t["id"])
                    fresh += 1
                    jobs.append(_job(row, "successfactors", title=t["title"], url=t["url"],
                                     location=t["location"], remote=_remote_from(t["location"])))
                if not fresh:
                    break  # empty page, or the server ignored the offset
                startrow += len(tiles)
        if not jobs:  # empty-shell signature of CSB tenants (or explicit variant)
            csb_jobs, csb_ok = _sf_csb(row, cfg, http)
            jobs, ok = csb_jobs, ok or csb_ok
        _count(stats, ok=ok, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Taleo
# Port of career-ops providers/taleo.mjs (MIT): Taleo Enterprise Edition public career section.
TALEO_HOST_RE = re.compile(r"[a-z0-9-]+\.taleo\.net", re.I | re.A)
TALEO_SECTION_RE = re.compile(r"[a-z0-9][a-z0-9._-]*", re.I | re.A)
TALEO_LANG_RE = re.compile(r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})?", re.I | re.A)
TALEO_DETAIL_LIMIT = 25  # detail pages fetched per board when row["fetch_details"] is set
TALEO_DETAIL_MAX = 100
_TALEO_PORTAL_RES = (
    re.compile(r"[?&]portal=([0-9]+)", re.I),
    re.compile(r"""["']portal(?:Id)?["']?\s*[:=]\s*["']?([0-9]+)""", re.I),
    re.compile(r"""name=["']portal["'][^>]*value=["']([0-9]+)""", re.I),
    # freehire internal/ingest/sources/taleo.go:72-74: the shell's JS config block says `portalNo: '101430233'`
    re.compile(r"""portalNo["']?\s*[:=]\s*["']?([0-9]+)""", re.I),
)
_TALEO_LDJSON_RE = re.compile(
    r"""<script\b[^>]*type\s*=\s*["']application/ld\+json["'][^>]*>([\s\S]*?)</script>""", re.I)
_TALEO_DATE_FORMATS = ("%Y-%m-%d", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d %b %Y", "%d %B %Y",
                       "%m/%d/%Y", "%d/%m/%Y")


def _taleo_portal(page: Any) -> str:
    """Portal id out of the jobsearch.ftl shell ('' when the section is private/unavailable)."""
    for rx in _TALEO_PORTAL_RES:
        m = rx.search(str(page or ""))
        if m:
            return m.group(1)
    return ""


def _taleo_headings(page: Any) -> list[str]:
    """Column labels from the shell's jobs table header (or any th/label), icon/action dropped."""
    page = str(page or "")
    m = re.search(r"""<table\b[^>]*id=["']jobs["'][^>]*>[\s\S]*?<thead\b[^>]*>([\s\S]*?)</thead>""", page, re.I)
    src = m.group(1) if m else page
    out: list[str] = []
    for h in re.finditer(r"<(?:th|label)[^>]*>([\s\S]*?)</(?:th|label)>", src, re.I):
        label = _text(h.group(1))
        if label and not re.fullmatch(r"(?:icons?|actions?)", label, re.I):
            out.append(label)
    return out


def _taleo_cell(value: Any) -> str:
    """A requisitionList column cell as text (string, number, list, or {value|text|label|...})."""
    if value is None:
        return ""
    if isinstance(value, (str, int, float)) and not isinstance(value, bool):
        return _text(str(value))
    if isinstance(value, list):
        return ", ".join(x for x in (_taleo_cell(v) for v in value) if x)
    if isinstance(value, dict):
        for k in ("value", "text", "label", "name", "displayValue"):
            if value.get(k) is not None:
                return _taleo_cell(value[k])
    return ""


def _taleo_location(value: str) -> str:
    """Location cells are sometimes a JSON array string ('["AB-Calgary","AB-Edmonton"]'): join it."""
    s = (value or "").strip()
    if s.startswith("["):
        try:
            arr = json.loads(s)
        except ValueError:
            return value
        if isinstance(arr, list) and all(isinstance(v, str) for v in arr):
            return "; ".join(x for x in (_text(v) for v in arr) if x)
    return value


def _taleo_posted(value: str) -> str:
    """'Oct 17, 2026' / '2026-10-17' / '17-Oct-2026' -> YYYY-MM-DD; '' when unknown."""
    s = (value or "").strip()
    if not s:
        return ""
    iso = _iso_date(s)
    if iso:
        return iso
    for fmt in _TALEO_DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return ""


def _taleo_index(headings: list[str], patterns: tuple[str, ...]) -> int:
    for i, h in enumerate(headings):
        if any(re.search(p, h, re.I) for p in patterns):
            return i
    return -1


def _taleo_row(req: dict[str, Any], headings: list[str]) -> tuple[str, str, str]:
    """(title, location, posted) of one requisition, columns matched by the shell's headings."""
    cols = req.get("column")
    values = [_taleo_cell(c) for c in cols] if isinstance(cols, list) else []
    positional = not headings

    def idx(match: int, default_pos: int) -> int:
        return match if match >= 0 else (default_pos if positional else -1)

    def at(i: int) -> str:
        return values[i] if 0 <= i < len(values) else ""

    t_i = idx(_taleo_index(headings, (r"requisition\s*title", r"job\s*title", r"^title$")), 0)
    l_i = idx(_taleo_index(headings, (r"location", r"city")), 1)
    p_i = idx(_taleo_index(headings, (r"posting\s*date", r"date\s*posted", r"posted")), 2)
    title = at(t_i) if t_i >= 0 else _taleo_cell(req.get("title") or req.get("jobTitle"))
    loc = at(l_i) if l_i >= 0 else _taleo_cell(req.get("location") or req.get("locationsColumns"))
    return title, _taleo_location(loc), _taleo_posted(at(p_i))


def _taleo_description(page: Any) -> str:
    """Plain-text JobPosting.description from the detail page's JSON-LD ('' when absent)."""
    def find(node: Any) -> dict[str, Any] | None:
        if isinstance(node, list):
            for item in node:
                hit = find(item)
                if hit:
                    return hit
            return None
        if not isinstance(node, dict):
            return None
        t = node.get("@type")
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            return node
        return find(node["@graph"]) if node.get("@graph") else None

    for m in _TALEO_LDJSON_RE.finditer(str(page or "")):
        try:
            posting = find(json.loads(m.group(1)))
        except (ValueError, RecursionError):
            continue
        desc = posting.get("description") if posting else None
        if isinstance(desc, str) and desc.strip():
            if re.search(r"%[0-9a-f]{2}", desc, re.I):
                desc = unquote(desc)
            text = _text(desc)
            if text:
                return text
    return ""


# NOT IN REFERENCE: own fallback, page shape from kearney.taleo.net 2026-10-18.
# Some sections 302 jobsearch.ftl to joblist.ftl, whose shell carries no portal id but does carry the FIRST page
# of the list (10 rows, whatever the total) in <input id="initialHistory" value="...">, a '!|!'-delimited token
# stream. A row starts with id,title,id,title,id x5 then schedule, location, more-locations flag; the posting date
# ("Oct 13, 2026") is followed by the requisition number that jobdetail.ftl?job= takes.
_TALEO_HIST_RE = re.compile(r"""id=["']initialHistory["'][^>]*value=["']([^"']*)["']""", re.I)
_TALEO_NB_RE = re.compile(r"""id=["']listRequisition\.nbElements["'][^>]*value=["'](\d+)["']""", re.I)
_TALEO_DATE_TOKEN = re.compile(r"[A-Z][a-z]{2} \d{1,2}, \d{4}")


def _taleo_inline(page: Any) -> tuple[list[tuple[str, str, str, str]], int]:
    """(title, requisition number, location, posted) of the rows inline in a joblist.ftl shell, and the
    shell's own total ('listRequisition.nbElements'; 0 when absent)."""
    text = str(page or "")
    m = _TALEO_HIST_RE.search(text)
    n = _TALEO_NB_RE.search(text)
    total = int(n.group(1)) if n else 0
    if not m:
        return [], total
    tok = [unquote(t) for t in html.unescape(m.group(1)).split("!|!")]
    out: list[tuple[str, str, str, str]] = []
    i = 0
    while i + 12 < len(tok):
        t = tok[i]
        if not (t.isdigit() and tok[i + 3] == tok[i + 1] and all(tok[i + k] == t for k in (2, 4, 5, 6, 7, 8))):
            i += 1
            continue
        title, loc, more = _text(tok[i + 1]), tok[i + 10], tok[i + 11] == "true"
        j = i + 12
        extra = ""
        while j < len(tok) and j < i + 30 and not _TALEO_DATE_TOKEN.fullmatch(tok[j]):
            if more and tok[j].startswith(", "):
                extra = tok[j]
            j += 1
        if j + 1 < len(tok) and _TALEO_DATE_TOKEN.fullmatch(tok[j]) and tok[j + 1].strip():
            where = _text(loc + extra)
            out.append((title, tok[j + 1].strip(), where, _taleo_posted(tok[j])))
            i = j + 2
        else:
            i += 1
    return out, total


def _taleo_enrich(row: dict[str, Any], http: Http, jobs: list[Job]) -> None:
    """Fill descriptions from the detail pages when row['fetch_details'] is set (first N postings)."""
    if row.get("fetch_details") is not True:
        return
    try:
        limit = int(row.get("detail_limit") or TALEO_DETAIL_LIMIT)
    except (TypeError, ValueError, OverflowError):
        limit = TALEO_DETAIL_LIMIT
    limit = max(1, min(limit, TALEO_DETAIL_MAX))
    for j in jobs[:limit]:
        try:  # best-effort enrichment: a bad detail page must never drop the listing
            st, det = _get(http, j["url"], as_json=False, headers={"Accept": "text/html"})
            if st == 200:
                j["description"] = _taleo_description(det)
        except Exception:  # noqa: BLE001, S112
            continue


def fetch_taleo(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Taleo Enterprise Edition: GET jobsearch.ftl for the portal id + column headings, then replay
    the anonymous POST /careersection/rest/jobboard/searchjobs (pageNo 1..MAX_PAGES).
    Row: tenant + section (+ optional lang, fetch_details, detail_limit). Detail pages (JSON-LD
    description) are fetched only when row['fetch_details'] is set, for the first N postings."""
    try:
        tenant = str(row.get("tenant") or row.get("token") or "").strip().lower()
        section = str(row.get("section") or "").strip()
        lang = str(row.get("lang") or "en").strip() or "en"
        host = f"{tenant}.taleo.net"
        if (not tenant or host == "tre.taleo.net" or not TALEO_HOST_RE.fullmatch(host)
                or not TALEO_SECTION_RE.fullmatch(section) or not TALEO_LANG_RE.fullmatch(lang)):
            _count(stats, ok=False)
            return []
        base = f"https://{host}/careersection"
        shell_url = f"{base}/{section}/jobsearch.ftl?lang={lang}"
        st, page = _get(http, shell_url, as_json=False, headers={"Accept": "text/html"})
        portal = _taleo_portal(page) if st == 200 else ""
        if not portal and st == 200:
            inline, inline_total = _taleo_inline(page)  # no portal id: keep the first page the shell carries
            if inline:
                if inline_total > len(inline):
                    print(f"note: taleo {tenant}: no portal id; read {len(inline)} of {inline_total} jobs from the page",
                          file=sys.stderr)
                found = [_job(row, "taleo", title=t, url=f"{base}/{section}/jobdetail.ftl?job={quote(rid, safe='')}&lang={lang}",
                             location=loc, posted=posted, remote=_remote_from(loc), extra={"req_id": rid})
                        for t, rid, loc, posted in inline]
                _taleo_enrich(row, http, found)
                _count(stats, ok=True, jobs=len(found))
                return found
        if not portal:  # private, unavailable, or no public portal id
            _count(stats, ok=False)
            return []
        headings = _taleo_headings(page)
        api = f"{base}/rest/jobboard/searchjobs?lang={lang}&portal={portal}"
        hdrs = {"Content-Type": "application/json", "Accept": "application/json, text/javascript, */*; q=0.01",
                "X-Requested-With": "XMLHttpRequest"}
        jobs: list[Job] = []
        seen: set[str] = set()
        ok = False
        reported = 0
        for page_no in range(1, MAX_PAGES + 1):
            body = json.dumps({
                "multilineEnabled": False,
                "sortingSelection": {"sortBySelectionParam": "5", "ascendingSortingOrder": "true"},
                "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
                "filterSelectionParam": {"searchFilterSelections": []},
                "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
                "pageNo": page_no,
            }).encode()
            st, data = _get(http, api, method="POST", data=body, headers=hdrs)
            if st != 200 or not isinstance(data, dict):
                break
            reqs = data.get("requisitionList")
            if not isinstance(reqs, list):
                if data:  # unexpected shape (empty object = no jobs)
                    break
                ok = True
                break
            ok = True
            for r in reqs:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("contestNo") or "").strip() or str(r.get("jobId") or "").strip()
                if not rid or rid in seen:
                    continue
                title, loc, posted = _taleo_row(r, headings)
                if not title:
                    continue
                seen.add(rid)
                jobs.append(_job(
                    row, "taleo", title=title,
                    url=f"{base}/{section}/jobdetail.ftl?job={quote(rid, safe='')}&lang={lang}",
                    location=loc, posted=posted, remote=_remote_from(loc),
                    extra={"req_id": rid},
                ))
            paging = data.get("pagingData")
            total = paging.get("totalCount") if isinstance(paging, dict) else None
            try:
                total_n = int(str(total).strip()) if total is not None and str(total).strip() else None
            except ValueError:
                total_n = None
            reported += len(reqs)
            if not reqs or (total_n is not None and reported >= total_n):
                break
        if ok:
            _taleo_enrich(row, http, jobs)
        _count(stats, ok=ok, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Jobvite
# Ported (re-written in Python) from career-ops providers/jobvite.mjs (MIT). The old JSON API
# (jobs.jobvite.com/api/company/{slug}/jobs) is retired upstream (302 to search.jobvite.com?invalid=1),
# so this uses the public XML feed, which is keyed on an opaque companyEId rather than the slug.
JOBVITE_BOARD = "https://jobs.jobvite.com"
JOBVITE_FEED = "https://app.jobvite.com/CompanyJobs/Xml.aspx"
JOBVITE_MAX_JOBS = MAX_PAGES * 50  # the feed is one document; cap what we keep from it
_JV_SLUG_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,80}")
_JV_EID_RE = re.compile(r"companyEId\s*[:=]\s*['\"]([A-Za-z0-9_-]{4,40})['\"]")


def _jobvite_tag(block: str, name: str) -> str:
    """Text of the first <name>..</name> in a <job> block, CDATA unwrapped ('' if absent). Linear indexOf scan."""
    open_, close = f"<{name}>", f"</{name}>"
    start = block.find(open_)
    if start == -1:
        return ""
    frm = start + len(open_)
    end = block.find(close, frm)
    if end == -1:
        return ""
    value = block[frm:end].strip()
    if value.startswith("<![CDATA[") and value.endswith("]]>"):
        value = value[len("<![CDATA["):-len("]]>")].strip()
    return value


def _jobvite_date(value: str) -> str:
    """Jobvite 'M/D/YYYY' -> 'YYYY-MM-DD'; '' if unparseable."""
    try:
        return datetime.strptime(value.strip(), "%m/%d/%Y").strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _jobvite_url(*candidates: str) -> str:
    """First candidate that is a valid http(s) URL, upgraded to https; '' if none."""
    for c in candidates:
        if not c:
            continue
        try:
            p = urlsplit(html.unescape(c).strip())
        except ValueError:
            continue
        if p.scheme in ("http", "https") and p.netloc:
            return urlunsplit(("https",) + tuple(p)[1:])
    return ""


def _jobvite_parse(xml: str) -> list[tuple[str, str, str, str, str, str, str]]:
    """Cursor-scan <job> blocks -> (id, title, url, location, posted, description, category)."""
    out = []
    cursor = 0
    while True:
        start = xml.find("<job>", cursor)
        if start == -1:
            break
        frm = start + len("<job>")
        end = xml.find("</job>", frm)
        if end == -1:
            break  # unterminated final block
        cursor = end + len("</job>")
        block = xml[frm:end]
        title = html.unescape(_jobvite_tag(block, "title")).strip()
        if not title:
            continue
        url = _jobvite_url(_jobvite_tag(block, "detail-url"), _jobvite_tag(block, "apply-url"))
        if not url:
            continue
        out.append((
            _jobvite_tag(block, "id"), title, url,
            html.unescape(_jobvite_tag(block, "location")).strip(),
            _jobvite_date(_jobvite_tag(block, "date")),
            _text(_jobvite_tag(block, "description")),
            html.unescape(_jobvite_tag(block, "category")).strip(),
        ))
        if len(out) >= JOBVITE_MAX_JOBS:
            break
    return out


def fetch_jobvite(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Jobvite public XML feed. Row: token = vanity slug; optional company_eid skips the board lookup."""
    token = str(row.get("token") or "")
    try:
        eid = str(row.get("company_eid") or row.get("eid") or "").strip()
        if not eid:
            if not _JV_SLUG_RE.fullmatch(token):
                _count(stats, ok=False)
                return []
            # fr=true&nl=1 stops branded boards redirecting away before the page that carries companyEId
            status, page = _get(http, f"{JOBVITE_BOARD}/{token}?fr=true&nl=1", as_json=False)
            m = _JV_EID_RE.search(page) if status == 200 and isinstance(page, str) else None
            if not m:
                _count(stats, ok=False)
                return []
            eid = m.group(1)
        status, data = _get(http, f"{JOBVITE_FEED}?c={quote(eid, safe='')}", as_json=False)
        if status != 200 or not isinstance(data, str) or "<result" not in data:
            _count(stats, ok=False)
            return []
        jobs: list[Job] = []
        for jid, title, url, loc, posted, desc, category in _jobvite_parse(data):
            jobs.append(_job(
                row, "jobvite", title=title, url=url, location=loc, posted=posted,
                description=desc, remote=_remote_from(loc),
                extra={"department": category, "req_id": jid, "company_eid": eid},
            ))
        _count(stats, ok=True, jobs=len(jobs))
        return jobs
    except Exception:  # noqa: BLE001
        _count(stats, ok=False)
        return []


# --------------------------------------------------------------------------- Eightfold
# Port of career-ops providers/eightfold.mjs (MIT). Row: {name, ats: "eightfold", tenant, domain?}.
_EF_TENANT_RE = re.compile(r"^[a-z0-9-]+$", re.I)
EIGHTFOLD_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/151.0.0.0 Safari/537.36")  # career-ops user-agent.mjs:22-23
EIGHTFOLD_PAGE = 10  # the API caps a page at 10 rows whatever `num` says


def _ef_epoch_date(value: Any) -> str:
    """Epoch SECONDS (t_create / postedTs) -> 'YYYY-MM-DD'; '' when missing or not positive."""
    try:
        n = float(value)
        if n <= 0 or n != n or n == float("inf"):
            return ""
        return datetime.fromtimestamp(n, tz=timezone.utc).strftime("%Y-%m-%d")
    except (TypeError, ValueError, OverflowError, OSError):
        return ""


def _ef_id(*raws: Any) -> str:
    """First usable str/int id (bools and objects are not ids)."""
    for r in raws:
        if isinstance(r, (str, int)) and not isinstance(r, bool) and str(r).strip():
            return str(r).strip()
    return ""


def _ef_location(p: dict[str, Any]) -> str:
    """Flat `location` plus `locations[]`, deduped, joined with ' · '."""
    parts: list[str] = []
    many = p.get("locations")
    cands = [p.get("location")] + (many if isinstance(many, list) else [])
    for c in cands:
        if isinstance(c, str) and c.strip() and c.strip() not in parts:
            parts.append(c.strip())
    return " · ".join(parts)


def _ef_url(host: str, domain: str, p: dict[str, Any], pid: str) -> str:
    """canonicalPositionUrl when it is https, else the tenant /careers?pid= link, else ''."""
    canon = p.get("canonicalPositionUrl")
    if isinstance(canon, str) and canon.strip().lower().startswith("https://"):
        return canon.strip()
    if not pid:
        return ""
    q = f"pid={quote(pid, safe='')}" + (f"&domain={quote(domain, safe='')}" if domain else "")
    return f"https://{host}/careers?{q}"


def _ef_page(data: Any, pcsx: bool) -> tuple[list[Any], Any] | None:
    """(positions, count) from a v2 body, or from a PCSX body (nested under `data`); None if unrecognised."""
    if pcsx:
        data = data.get("data") if isinstance(data, dict) else None
    if not isinstance(data, dict) or not isinstance(data.get("positions"), list):
        return None
    count = data.get("count")
    return data["positions"], (count if isinstance(count, (int, float)) and not isinstance(count, bool) else None)


def fetch_eightfold(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
    """Eightfold per-tenant public JSON API (zero auth): /api/apply/v2/jobs, falling back to /api/pcsx/search."""
    tenant = str(row.get("tenant") or row.get("token") or "").strip().lower()
    domain = str(row.get("domain") or "").strip()
    try:
        if not _EF_TENANT_RE.fullmatch(tenant):
            _count(stats, ok=False)
            return []
        host = f"{tenant}.eightfold.ai"
        dq = f"domain={quote(domain, safe='')}&" if domain else ""
        # NOT IN REFERENCE (live, owner's PC 2026-10-18): PCSX answers 422 "domain: Missing data for required
        # field" without one. career-ops takes it from careers_url; a row without one tries <tenant>.com.
        pq = f"domain={quote(domain or tenant + '.com', safe='')}&"
        # career-ops eightfold.mjs:392 sends a browser-like User-Agent on every call; its header (:44-48) says
        # several tenants WAF-403 anything else. Ours would be read as a bot.
        hdr = {"Accept": "application/json", "User-Agent": EIGHTFOLD_UA}
        api = "v2"
        jobs: list[Job] = []
        seen: set[str] = set()
        for page in range(MAX_PAGES):
            start = page * EIGHTFOLD_PAGE
            urls = {
                "v2": f"https://{host}/api/apply/v2/jobs?{dq}start={start}&num={EIGHTFOLD_PAGE}",
                "pcsx": f"https://{host}/api/pcsx/search?{pq}start={start}",
            }
            status, data = _get(http, urls[api], headers=hdr)
            if status == 403 and api == "v2" and page == 0:
                # a migrated tenant refuses the classic endpoint; probe PCSX once and stay on it
                status, data = _get(http, urls["pcsx"], headers=hdr)
                if status == 200 and _ef_page(data, True) is not None:
                    api = "pcsx"
            if status != 200:
                if jobs:  # 429 / hiccup mid-walk: keep what we have
                    break
                _count(stats, ok=False)
                return []
            parsed = _ef_page(data, api == "pcsx")
            if parsed is None:
                if jobs:
                    break
                _count(stats, ok=False)
                return []
            positions, total = parsed
            for p in positions:
                if not isinstance(p, dict):
                    continue
                title = next((v.strip() for v in (p.get("name"), p.get("posting_name")) if isinstance(v, str) and v.strip()), "")
                pid = _ef_id(p.get("id"), p.get("position_id"))
                url = _ef_url(host, domain, p, pid)
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                loc = _ef_location(p)
                posted = _ef_epoch_date(p.get("t_create") or p.get("postedTs") or p.get("creationTs")) \
                    or _ef_epoch_date(p.get("t_update"))
                jobs.append(_job(
                    row, "eightfold", title=title, url=url, location=loc, posted=posted,
                    remote=_remote_from(loc),
                    extra={"department": p.get("department") or "", "business_unit": p.get("business_unit") or "",
                           "requisition_id": _ef_id(p.get("ats_job_id"), p.get("atsJobId"), p.get("display_job_id"), p.get("displayJobId")),
                           "api": api},
                ))
            if len(positions) < EIGHTFOLD_PAGE:
                break
            if total is not None and start + EIGHTFOLD_PAGE >= total:
                break
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
    "icims": fetch_icims,
    "oraclecloud": fetch_oraclecloud,
    "successfactors": fetch_successfactors,
    "taleo": fetch_taleo,
    "jobvite": fetch_jobvite,
    "eightfold": fetch_eightfold,
}
