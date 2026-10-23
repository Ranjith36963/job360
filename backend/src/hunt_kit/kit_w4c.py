"""Boards C for the hunt kit: CleverStaff, FriendWork and joinup.ch. Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  cleverstaff  the tenant alias of the public list (``doit-software1``)
  friendwork   the career-portal token of jobs.friend.work (a readable alias or a 32-hex hash)
  joinup       any word (``joinup:all``): joinup.ch is ONE national board, only its newest page is read

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. All three give the description inline or none at all, so no posting page
is ever opened and every request stays on the board's own host.

NOT built (written up in README and NOTICE):
  Eluta (eluta.ca) and Talent.com: the only reference is ApplyPilot sites.yaml:59-65, which holds a search URL template and
  nothing else. NOT IN REFERENCE: the result markup, the field names, the paging, or whether the page is served to a plain
  request. A link on either host is reported as unsupported (hunt_kit.board_from_job_url).
"""
# ruff: noqa: E501
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _page_st,
    _remote_from,
    _wrap,
)

_JSON = {"Accept": "application/json"}


def _text(value: Any) -> str:
    """One line of plain text; non-strings are not text."""
    return " ".join(value.replace("�", "").split()) if isinstance(value, str) else ""


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is https on exactly ``host``, else ''."""
    try:
        p = urlsplit(url.strip())
        return p._replace(fragment="").geturl() if p.scheme == "https" and (p.hostname or "").lower() == host else ""
    except ValueError:
        return ""


def _epoch_date(value: Any) -> str:
    """Epoch milliseconds (or seconds, below 1e12) -> YYYY-MM-DD; '' for anything else (joinup.mjs:16-27 treats <= 0 as missing)."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value <= 0:
        return ""
    ms = value * 1000 if value < 1_000_000_000_000 else value
    try:
        return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (OverflowError, OSError, ValueError):
        return ""


def _mode(value: Any) -> str:
    """freehire helpers.go:155-166 workplaceTypeMode: remote | hybrid | on-site/onsite/on site/office."""
    return {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite",
            "office": "onsite"}.get(str(value or "").strip().lower(), "")


# --------------------------------------------------------------------------- CleverStaff
# Borrowed from freehire internal/ingest/sources/cleverstaff.go:19 (GET https://cleverstaff.net/hr/public/getAllOpenVacancy?alias=
# <board>, one request returns the tenant's whole open list), :39-44 (status "ok" on success; anything else fails the board, as
# cleverstaff_test.go non-ok-status does), :47-58 (vacancyId, localId, position, descr HTML inline, employmentType, workCondition,
# status, clientName, dc epoch-ms), :21-23 (public URL https://cleverstaff.net/i/vacancy-<localId>), :61-66 (employmentType
# fullEmployment / underemployment / projectWork), :80-97 (a vacancy with no vacancyId, localId or position is dropped, and so is a
# closed / canceled / cancelled one), :108-116 (the employer is the configured company; only a hub tenant uses each vacancy's own
# clientName) and cleverstaff_test.go:82-117 (doit-software1, Founding Engineer, remote, full_time).
_CS_TYPE = {"fullEmployment": "full_time", "underemployment": "part_time", "projectWork": "contract"}
_CS_CLOSED = {"closed", "canceled", "cancelled"}
_CS_ALIAS = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def _cleverstaff(row: dict[str, Any], http: Http) -> list[Job] | None:
    """CleverStaff tenant (one request, descriptions inline)."""
    alias = str(row.get("token") or "").strip()
    if not _CS_ALIAS.fullmatch(alias):
        return None
    st, data = _get(http, "https://cleverstaff.net/hr/public/getAllOpenVacancy?alias=" + quote(alias, safe=""), headers=_JSON)
    objs = data.get("objects") if isinstance(data, dict) else None
    if st != 200 or not isinstance(data, dict) or data.get("status") != "ok" or not isinstance(objs, list):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for v in objs:
        try:
            if not isinstance(v, dict):
                continue
            vid, local, title = _text(v.get("vacancyId")), _text(v.get("localId")), _text(v.get("position"))
            if not vid or not local or not title or vid in seen or str(v.get("status") or "") in _CS_CLOSED:
                continue
            if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", local):
                continue
            seen.add(vid)
            mode = _mode(v.get("workCondition"))
            j = _job(row, "cleverstaff", title=title, url="https://cleverstaff.net/i/vacancy-" + local,
                     posted=_epoch_date(v.get("dc")), description=_body(v.get("descr")),
                     remote=True if mode == "remote" else None,
                     extra={"job_id": vid, "work_mode": mode, "employment_type": _CS_TYPE.get(str(v.get("employmentType") or ""), "")})
            if row.get("hub") is True and _text(v.get("clientName")):
                j["company"] = _text(v.get("clientName"))
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- FriendWork
# Borrowed from freehire internal/ingest/sources/friendwork.go:23 (GET https://api.friend.work/Publication/CareerPortal?token=
# <board>; one unpaginated response holds the tenant's whole published list, :13-16), :28 (portal origin https://jobs.friend.work),
# :44-48 (company.name is the tenant's own name), :52-66 (publishedJobId, title, description HTML inline, salaryFrom/To, currency,
# city, address, expYearsMin, employmentType, workplaceType, publishDateTime, dateCreated, externalPublicationUrl), :89-108 (a
# publication with no id or title is dropped; the URL is the portal origin plus externalPublicationUrl, else
# <origin>/<token>/<id>; location is city, else address; the date is publishDateTime, else dateCreated), :76-81 (Fulltime /
# Halftime / Internship), :84-87 (currency enum 0 RUB, 1 USD, 2 EUR, 3 UAH, 4 KZT, 5 BYN, 6 AED, 7 UZS; salary is per month) and
# friendwork_test.go:30-60 and :145-170 (the token fallback URL and address fallback). The API meters simultaneity (:10-12), so
# the kit sends this one request alone.
_FW_TYPE = {"Fulltime": "full_time", "Halftime": "part_time", "Internship": "internship"}
_FW_CUR = {0: "RUB", 1: "USD", 2: "EUR", 3: "UAH", 4: "KZT", 5: "BYN", 6: "AED", 7: "UZS"}
_FW_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
_FW_HOST = "jobs.friend.work"


def _fw_amount(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _friendwork(row: dict[str, Any], http: Http) -> list[Job] | None:
    """FriendWork career portal (one request, descriptions inline)."""
    token = str(row.get("token") or "").strip()
    if not _FW_TOKEN.fullmatch(token):
        return None
    st, data = _get(http, "https://api.friend.work/Publication/CareerPortal?token=" + quote(token, safe=""), headers=_JSON)
    pubs = data.get("publications") if isinstance(data, dict) else None
    if st != 200 or not isinstance(pubs, list):
        return None
    co = data.get("company") if isinstance(data, dict) else None
    tenant = _text(co.get("name")) if isinstance(co, dict) else ""
    jobs: list[Job] = []
    seen: set[int] = set()
    for p in pubs:
        try:
            if not isinstance(p, dict):
                continue
            pid, title = p.get("publishedJobId"), _text(p.get("title"))
            if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0 or not title or pid in seen:
                continue
            ext = p.get("externalPublicationUrl")
            url = _on(f"https://{_FW_HOST}{ext}", _FW_HOST) if isinstance(ext, str) and ext.startswith("/") and not ext.startswith("//") else ""
            if not url:
                url = f"https://{_FW_HOST}/{token}/{pid}"
            seen.add(pid)
            place = _text(p.get("city")) or _text(p.get("address"))
            mode = _mode(p.get("workplaceType"))
            extra: dict[str, Any] = {"job_id": str(pid), "work_mode": mode,
                                     "employment_type": _FW_TYPE.get(str(p.get("employmentType") or ""), "")}
            code = p.get("currency")
            lo, hi = _fw_amount(p.get("salaryFrom")), _fw_amount(p.get("salaryTo"))
            cur = _FW_CUR.get(code) if isinstance(code, int) and not isinstance(code, bool) else None
            if cur and (lo or hi):
                extra.update({"salary_min": lo or None, "salary_max": hi or None, "salary_currency": cur, "salary_period": "month"})
            j = _job(row, "friendwork", title=title, url=url, location=place,
                     posted=_iso_date(p.get("publishDateTime")) or _iso_date(p.get("dateCreated")),
                     description=_body(p.get("description")),
                     remote=True if mode == "remote" else _remote_from(place), extra=extra)
            if not str(row.get("name") or "").strip() and tenant:
                j["company"] = tenant
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- joinup.ch
# Borrowed from career-ops providers/joinup.mjs:4-12 (the browse page server-renders the newest page of results into __NEXT_DATA__
# at props.pageProps.serverState.initialResults.jobs.results[0].hits[]; the full board sits behind a runtime Typesense key, so only
# that page is read), :9 (hit fields title|headline, startup, slug, location, created), :10 (public URL https://joinup.ch/job/
# <slug>), :16-27 (created is an ISO string or an epoch; a non-positive value is missing), :29 (BROWSE_URL), :49-51 and :56-60 (a
# missing or unparseable __NEXT_DATA__ fails the board rather than reporting it empty), :61-68 (a hit needs a slug and a title or
# headline; company is startup; location is a string or location.name / location.city). The reference gives no description.
_JU_URL = "https://joinup.ch/browse/jobs"
_JU_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]{0,199}")
_NEXT_ID = 'id="__next_data__"'


def _next_data(page: str) -> Any:
    """The JSON inside the __NEXT_DATA__ script, found with plain searches (no pattern spans the page); None if absent."""
    low = page.lower()
    i = low.find(_NEXT_ID)
    if i < 0:
        return None
    start = low.rfind("<script", 0, i)  # the tag that carries the id: nothing between its name and the id may open or close a tag
    gt = low.find(">", i)
    if start < 0 or gt < 0 or "<" in low[start + 1:i] or ">" in low[start:i]:
        return None
    end = low.find("</script", gt)
    if end < 0:
        return None
    try:
        return json.loads(page[gt + 1:end])
    except (ValueError, RecursionError):
        return None


def _ju_place(value: Any) -> str:
    if isinstance(value, str):
        return _text(value)
    if isinstance(value, dict):
        return _text(value.get("name")) or _text(value.get("city"))
    return ""


def _joinup(row: dict[str, Any], http: Http) -> list[Job] | None:
    """joinup.ch newest results page (token ignored)."""
    page = _page_st(http, _JU_URL)[1]
    data = _next_data(page) if page else None
    try:
        res = data["props"]["pageProps"]["serverState"]["initialResults"]["jobs"]["results"]  # type: ignore[index]
        hits = res[0]["hits"]
    except (KeyError, IndexError, TypeError):
        return None
    if not isinstance(hits, list):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for h in hits:
        try:
            if not isinstance(h, dict):
                continue
            slug, title = h.get("slug"), _text(h.get("title")) or _text(h.get("headline"))
            if not isinstance(slug, str) or not _JU_SLUG.fullmatch(slug) or not title or slug in seen:
                continue
            seen.add(slug)
            place = _ju_place(h.get("location"))
            created = h.get("created")
            j = _job(row, "joinup", title=title, url="https://joinup.ch/job/" + quote(slug, safe=""), location=place,
                     posted=_epoch_date(created) if isinstance(created, (int, float)) else _iso_date(created),
                     remote=_remote_from(place), extra={"job_id": slug})
            j["company"] = _text(h.get("startup")) or str(row.get("name") or "")
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "cleverstaff": _wrap(_cleverstaff),
    "friendwork": _wrap(_friendwork),
    "joinup": _wrap(_joinup),
}
