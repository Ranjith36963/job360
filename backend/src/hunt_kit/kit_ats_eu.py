"""European and other ATS board fetchers for the hunt kit, part one: Emply, Manatal, TalentAdore, Traffit, Trakstar.
Factorial, Jobylon, PeopleForce, TalentHR and TalentLyft (the readers that open posting pages) follow in the next slice.
Same contract as kit_ats_more.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the
``http`` callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies_ats_eu.json ``board`` / ``--companies-extra "ats:token"``):
  emply        tenant slug of <slug>.career.emply.com, or the career-site host when it runs on the customer's own domain
               (midtjob.dk, job.paychex.eu)
  manatal      career-page slug of careers-page.com/<slug>
  talentadore  feed token from the career-site widget (ats.talentadore.com/positions/<token>/json)
  traffit      tenant of <tenant>.traffit.com
  trakstar     tenant of <tenant>.hire.trakstar.com

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any, Callable
from urllib.parse import quote, urlsplit

try:  # shared safe-XML helper (same folder)
    from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _xilu
    from pathlib import Path as _XPath

    _xspec = _xilu.spec_from_file_location("kit_xml", _XPath(__file__).with_name("kit_xml.py"))
    if _xspec is None or _xspec.loader is None:
        raise ImportError("kit_xml.py not found next to this file")
    _xmod = _xilu.module_from_spec(_xspec)
    _xspec.loader.exec_module(_xmod)
    safe_fromstring = _xmod.safe_fromstring

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
        _iso_date,
        _job,
        _join,
        _remote_from,
        _rfc822_date,
        _text,
    )
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_ats", _Path(__file__).with_name("kit_ats.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_ats.py not found next to this file")
    _mod = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_mod)
    MAX_PAGES, Http, Job = _mod.MAX_PAGES, _mod.Http, _mod.Job
    _count, _get, _iso_date, _job, _join = _mod._count, _mod._get, _mod._iso_date, _mod._job, _mod._join
    _remote_from, _rfc822_date, _text = _mod._remote_from, _mod._rfc822_date, _mod._text

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_XML = {"Accept": "application/xml, text/xml, application/rss+xml"}

EMPLY_PAGE = 100
TRAFFIT_PAGE = 100


# --------------------------------------------------------------------------- shared helpers
def _label(s: str) -> bool:
    """A DNS-style label: letters, digits, hyphens; not starting or ending with a hyphen."""
    return bool(re.fullmatch(r"[A-Za-z0-9-]{1,80}", s)) and s[0] != "-" and s[-1] != "-"


def _slug(s: str) -> bool:
    """A path slug: letters, digits, dot, underscore, hyphen."""
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", s))


def _https(url: Any, host: str | None = None) -> str:
    """The URL (fragment dropped) when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p._replace(fragment="").geturl() if ok else ""


def _wrap(body: Callable[[dict[str, Any], Http], list[Job] | None]) -> Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]:
    """Turn ``body(row, http) -> jobs | None`` (None = the board failed) into a never-raising fetcher that counts."""
    def fetch(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
        try:
            jobs = body(row, http)
        except Exception:  # noqa: BLE001 - fetchers must never raise
            jobs = None
        _count(stats, ok=jobs is not None, jobs=len(jobs or []))
        return jobs or []
    fetch.__doc__ = body.__doc__
    return fetch


_BODY_CAP = 50_000  # characters of a description read at all: a longer one is cut before any scan
_OPEN_BLOCK = re.compile(r"<(script|style)(?=[\s/>])", re.I)  # a tag name only counts when the next character ends it
_CLOSE_BLOCK = {"script": re.compile(r"</script", re.I), "style": re.compile(r"</style", re.I)}


def _strip_blocks(html: str) -> str:
    """Drop every <script>/<style> element in ONE linear pass: each search resumes where the last one stopped,
    so no stretch of text is scanned twice. A tag that never closes drops the rest of the text."""
    out: list[str] = []
    pos = 0
    while True:
        m = _OPEN_BLOCK.search(html, pos)
        if m is None:
            out.append(html[pos:])
            break
        out.append(html[pos:m.start()])
        close = _CLOSE_BLOCK[m.group(1).lower()].search(html, m.end())
        if close is None:
            break  # never closed: the tail is script/style text
        gt = html.find(">", close.end())
        if gt < 0:
            break
        pos = gt + 1
    return "".join(out)


def _body(value: Any) -> str:
    """Plain text of an HTML description with script and style blocks dropped first (freehire runs sanitizeHTML)."""
    if not value:
        return ""
    s = _strip_blocks(str(value)[:_BODY_CAP])
    gt = s.rfind(">") + 1
    s = s[:gt] + s[gt:].split("<", 1)[0]  # cut at the first '<' that has no '>' after it, so none is left for _text's tag regexes to rescan
    return str(_text(s))


def _epoch_date(value: Any) -> str:
    """Unix seconds -> 'YYYY-MM-DD', '' when not a positive number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


# --------------------------------------------------------------------------- Emply
# Borrowed from freehire internal/ingest/sources/emply.go:43-44,118 (POST https://<host>/api/integration/vacancy/get-page),
# :127 and :69 (body {"count","offset","filters":[]}; filters is REQUIRED, the endpoint answers 500 without it, :19-21),
# :56-61 (a board with a dot is already a host, a bare slug is <slug>.career.emply.com), :72-77 and :134,143 (answer
# {"vacancies":[...],"count":N}; page by offset until the count is read), :85 (talentPool = the standing
# "unsolicited application" entry, not a vacancy), :156-179 (read the English text, else the first one with a body),
# :183-219 (shortId required; URL externalCseAdLink else https://<host>/ad/<titleAsUrl|ad>/<shortId>/<lang>; location,
# published, full HTML body per translation). The reference demands the distinct ids equal count (:147-150); the kit keeps a partial board.
_EMPLY_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")


def _emply_host(token: str) -> str:
    t = token.strip().lower()
    host = t if "." in t else (f"{t}.career.emply.com" if _label(t) else "")
    return host if _EMPLY_HOST.fullmatch(host) else ""


def _emply_translation(v: dict[str, Any]) -> dict[str, Any]:
    trs = [t for t in (v.get("translations") or []) if isinstance(t, dict)]
    body = [t for t in trs if str(t.get("content") or "").strip()]
    english = next((t for t in body if str(t.get("languageKey") or "").lower().startswith("en")), None)
    return english or (body[0] if body else (trs[0] if trs else {}))


def _emply(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Emply career-site vacancy API (keyless POST; token = slug or the career-site host)."""
    host = _emply_host(str(row.get("token") or ""))
    if not host:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    offset = 0
    for page in range(MAX_PAGES):
        body = {"count": EMPLY_PAGE, "offset": offset, "filters": []}
        st, data = _get(http, f"https://{host}/api/integration/vacancy/get-page", method="POST", data=json.dumps(body).encode(), headers=_POST)
        batch = data.get("vacancies") if isinstance(data, dict) else None
        if st != 200 or not isinstance(batch, list):
            if page:
                break  # a hiccup after page 0 keeps what we have
            return None
        total = data.get("count") if isinstance(data.get("count"), int) else None
        fresh = 0
        for v in batch:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("id") or v.get("shortId") or "")
            if vid in seen:
                continue
            seen.add(vid)
            fresh += 1
            short = str(v.get("shortId") or "").strip()
            if v.get("talentPool") is True or not short:
                continue
            tr = _emply_translation(v)
            title = " ".join(str(tr.get("title") or "").split()) or " ".join(str(v.get("title") or "").split())
            if not title:
                continue
            url = _https(v.get("externalCseAdLink"))
            if not url:
                url = f"https://{host}/ad/{quote(str(v.get('titleAsUrl') or 'ad'), safe='')}/{quote(short, safe='')}"
                lang = str(tr.get("languageKey") or "").partition("-")[0].lower()
                url += f"/{quote(lang, safe='')}" if lang else ""
            place = " ".join(str(v.get("location") or "").split())
            jobs.append(_job(row, "emply", title=title, url=url, location=place, posted=_iso_date(v.get("published")),
                             description=_body(tr.get("content")), remote=_remote_from(place), extra={"short_id": short}))
        offset += len(batch)
        if not batch or not fresh or (total is not None and offset >= total):
            break
    return jobs


# --------------------------------------------------------------------------- Manatal
# Borrowed from freehire internal/ingest/sources/manatal.go:33 (GET https://open.api.manatal.com/open/v3/career-page/<slug>/jobs/,
# a DRF page {count,next,previous,results}), :61-75 (follow the top-level "next" link until null), :44-55 (hash, position_name,
# description HTML, country/state/city, location_display, is_remote), :87 (location_display else city, state, country), :95
# (remote = is_remote or "remote" in the location), :83-86 (a posting with no hash is dropped; organization_name is a department,
# never the employer), :36 and :90 (posting page https://www.careers-page.com/<slug>/job/<hash>), :98 (no publish date).
def _manatal(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Manatal career-page JSON feed (token = career-page slug)."""
    slug = str(row.get("token") or "").strip()
    if not _slug(slug):
        return None
    nxt = f"https://open.api.manatal.com/open/v3/career-page/{quote(slug, safe='')}/jobs/"
    jobs: list[Job] = []
    for page in range(MAX_PAGES):
        st, data = _get(http, nxt, headers=_JSON)
        results = data.get("results") if isinstance(data, dict) else None
        if st != 200 or not isinstance(results, list):
            if page:
                break
            return None
        for p in results:
            h = str((p or {}).get("hash") or "").strip() if isinstance(p, dict) else ""
            title = str(p.get("position_name") or "").strip() if h else ""
            if not h or not title:
                continue
            place = str(p.get("location_display") or "").strip() or _join(p.get("city"), p.get("state"), p.get("country"))
            jobs.append(_job(row, "manatal", title=title, url=f"https://www.careers-page.com/{quote(slug, safe='')}/job/{quote(h, safe='')}",
                             location=place, description=_body(p.get("description")),
                             remote=True if p.get("is_remote") is True else _remote_from(place),
                             extra={"employment_type": str(p.get("contract_details") or "")}))
        nxt = _https(data.get("next"), "open.api.manatal.com")
        if not nxt:
            break
    return jobs


# --------------------------------------------------------------------------- TalentAdore
# Borrowed from freehire internal/ingest/sources/talentadore.go:17 (GET https://ats.talentadore.com/positions/<token>/json?v=2;
# v=2 makes the dates RFC3339, :12-16), :34-56 (jobs[]: job_token, name, link, description_html, description_text,
# start_date, updated, city, county, country), :67-69 (a job with no job_token is skipped), :71 (location = city, county,
# country), :72-77 (description_html, else description_text), :86 (posted = start_date, else updated). The reference uses the
# configured company; falling back to the feed's top-level "company" when the row has no real name appears only in the
# reference's test fixture (talentadore_test.go:15-16): NOT IN REFERENCE as a documented field.
def _talentadore(row: dict[str, Any], http: Http) -> list[Job] | None:
    """TalentAdore Hire positions JSON feed (token = the widget's data-url token)."""
    token = str(row.get("token") or "").strip()
    if not _label(token):
        return None
    st, data = _get(http, f"https://ats.talentadore.com/positions/{quote(token, safe='')}/json?v=2", headers=_JSON)
    items = data.get("jobs") if isinstance(data, dict) else None
    if st != 200 or not isinstance(items, list):
        return None
    feed_company = str(data.get("company") or "").strip()
    name = str(row.get("name") or "").strip()
    jobs: list[Job] = []
    for j in items:
        jt = str((j or {}).get("job_token") or "").strip() if isinstance(j, dict) else ""
        title, url = str(j.get("name") or "").strip() if jt else "", _https(j.get("link"), "ats.talentadore.com") if jt else ""
        if not jt or not title or not url:
            continue
        place = _join(j.get("city"), j.get("county"), j.get("country"))
        job = _job(row, "talentadore", title=title, url=url, location=place,
                   posted=_iso_date(j.get("start_date") or j.get("updated")),
                   description=_body(j.get("description_html") or j.get("description_text")), remote=_remote_from(place),
                   extra={"job_token": jt})
        if feed_company and name in ("", token):
            job["company"] = feed_company
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- Traffit
# Borrowed from freehire internal/ingest/sources/traffit.go:73 (GET https://<board>.traffit.com/public/an/list/?limit=100&offset=N),
# :26 (the endpoint clamps a page to 100), :33-49 (answer {count, items[]}: advertId, advertPublishId, title, name, url,
# description HTML, geolocation, validStart), :79-90 (stop on an empty page or once count items are read), :97-108 (id =
# advertId else advertPublishId, none = skip; title else name), :110-116 and :52-68 (geolocation is a JSON STRING with
# locality, region1, country; null or unparsable = no location), :125 (validStart = Unix seconds).
def _traffit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Traffit public advert list (token = tenant of <tenant>.traffit.com)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    jobs: list[Job] = []
    fetched = 0
    for page in range(MAX_PAGES):
        st, data = _get(http, f"https://{tenant}.traffit.com/public/an/list/?limit={TRAFFIT_PAGE}&offset={page * TRAFFIT_PAGE}", headers=_JSON)
        items = data.get("items") if isinstance(data, dict) else None
        if st != 200 or not isinstance(items, list):
            if page:
                break
            return None
        for it in items:
            if not isinstance(it, dict):
                continue
            aid = it.get("advertId") or it.get("advertPublishId")
            title = str(it.get("title") or "").strip() or str(it.get("name") or "").strip()
            url = _https(it.get("url"), f"{tenant}.traffit.com")
            if not aid or isinstance(aid, bool) or not title or not url:
                continue
            place = ""
            geo = it.get("geolocation")
            if isinstance(geo, str) and geo:
                try:
                    g = json.loads(geo)
                except ValueError:
                    g = None
                if isinstance(g, dict):
                    place = _join(g.get("locality"), g.get("region1"), g.get("country"))
            jobs.append(_job(row, "traffit", title=title, url=url, location=place, posted=_epoch_date(it.get("validStart")),
                             description=_body(it.get("description")), remote=_remote_from(place), extra={"advert_id": str(aid)}))
        fetched += len(items)
        count = data.get("count") if isinstance(data.get("count"), int) else 0
        if not items or fetched >= count:
            break
    return jobs


# --------------------------------------------------------------------------- Trakstar Hire
# Borrowed from freehire internal/ingest/sources/trakstar.go:43 (GET https://<board>.hire.trakstar.com/jobfeeds/<board>, one RSS
# document, body inline, no paging), :32-40 (item: title, link, pubDate, description, and the namespace
# https://recruiterbox.com/rss/job/ elements locationCity, locationState, locationCountry), :53-54 (id = last path segment of
# the link, http upgraded to https), :57-58 (description is entity-unescaped HTML), :59 (remote = "remote" in the city), :60 (pubDate).
_TRAKSTAR_NS = "{https://recruiterbox.com/rss/job/}"


def _trakstar(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Trakstar Hire (Recruiterbox) RSS job feed (token = tenant of <tenant>.hire.trakstar.com)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    st, feed = _get(http, f"https://{tenant}.hire.trakstar.com/jobfeeds/{tenant}", as_json=False, headers=_XML)
    root = safe_fromstring(feed) if st == 200 else None
    if root is None or _local(root.tag) != "rss":
        return None
    jobs: list[Job] = []
    for item in root.iter("item"):
        def f(name: str, _it: ET.Element = item) -> str:
            el = _it.find(name)
            return (el.text or "").strip() if el is not None else ""
        link = _https(f("link").replace("http://", "https://", 1), f"{tenant}.hire.trakstar.com")
        title = f("title")
        if not link or not title:
            continue
        place = _join(f(_TRAKSTAR_NS + "locationCity"), f(_TRAKSTAR_NS + "locationState"), f(_TRAKSTAR_NS + "locationCountry"))
        jobs.append(_job(row, "trakstar", title=title, url=link, location=place, posted=_rfc822_date(f("pubDate")),
                         description=_body(html.unescape(f("description"))),
                         remote=True if "remote" in f(_TRAKSTAR_NS + "locationCity").lower() else None,
                         extra={"job_id": link.rstrip("/").rsplit("/", 1)[-1]}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "emply": _wrap(_emply),
    "manatal": _wrap(_manatal),
    "talentadore": _wrap(_talentadore),
    "traffit": _wrap(_traffit),
    "trakstar": _wrap(_trakstar),
}
