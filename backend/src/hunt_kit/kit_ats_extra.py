"""Three more ATS board fetchers for the hunt kit: Avature, Comeet, HiBob. Same contract as
kit_ats.py / kit_ats_more.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job. The company on every job is the
row's ``name`` (the employer), never the board vendor.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  avature    career-site host, e.g. jobs.ea.com or koch.avature.net (row may add ``path``, the SearchJobs path)
  comeet     <slug>/<companyUID> from www.comeet.com/jobs/<slug>/<companyUID>, e.g. overwolf/B1.001
  hibob      tenant from <tenant>.careers.hibob.com

Request budget per board: Avature up to AVATURE_PAGES listing pages (6 postings each, no detail requests); Comeet two
requests; HiBob one. The first non-200 answer ends the board (a failure after the first page keeps what was read).

Endpoints and field names are adapted from the MIT-licensed freehire adapters and career-ops providers (see NOTICE.md);
each reader below carries its own file:line citations.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
from typing import Any, Callable
from urllib.parse import quote, urlsplit

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
        _iso_date,
        _job,
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
    _count, _get, _iso_date, _job, _text = _mod._count, _mod._get, _mod._iso_date, _mod._job, _mod._text

AVATURE_PAGES = MAX_PAGES  # 6 postings per page, so at most 60 postings per board
_JSON = {"Accept": "application/json"}
_HTML = {"Accept": "text/html"}
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}")
_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _https(url: Any, host: str | None = None) -> str:
    """The URL when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p.geturl() if ok else ""


def _clean(s: Any) -> str:
    """Tags dropped, entities decoded, whitespace collapsed to single spaces."""
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", " ", str(s or "")))).strip()


def _commas(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _mode(value: Any) -> str:
    """Workplace-type enum to remote/hybrid/onsite, '' when unknown (freehire helpers.go:155-166)."""
    v = str(value or "").strip().lower()
    return {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite", "office": "onsite"}.get(v, "")


def _remote(mode: str, *texts: Any) -> bool | None:
    """True for a remote work mode or a location that says remote, else None (unknown)."""
    if mode == "remote" or any(t and "remote" in str(t).lower() for t in texts):
        return True
    return None


def _company(row: dict[str, Any], fallback: str = "") -> str:
    return str(row.get("name") or fallback or row.get("token") or "")


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


# --------------------------------------------------------------------------- Avature
# Borrowed from career-ops providers/avature.mjs:11 (GET {origin}/careers/SearchJobs?jobOffset=N, a server-rendered page of
# <article class="article--result"> cards), :39 (PAGE_SIZE 6; jobOffset advances by 6), :48-50 and :260-270 (if page 1 shows
# nothing new, retry it once with the bare ``offset`` key and keep whichever advances), :71 (an explicit SearchJobs path is
# honoured), :136 (ARTICLE_PATTERN, ``article--result`` or ``article--jobs``), :163-166 (title anchor: header anchor, then
# class="link", then any JobDetail anchor), :89-98 (``Posted DD-Mon-YYYY``), :103-109 (location when the tenant renders one),
# :276-277 (stop on a page with nothing new or fewer than 6 cards).
# NOT IN REFERENCE: freehire avature.go:42-62 reads the sitemap and fetches every JobDetail page (hundreds of requests per
# board); this reader takes the career-ops listing route to keep the budget small, so jobs carry no description.
_AVATURE_ARTICLE = re.compile(r'<article class="article article--(?:result|jobs)\b[^"]*"[\s\S]*?</article>')
_AVATURE_TITLE = (
    re.compile(r'article__header__text__title[^>]*>\s*<a[^>]*href="([^"]*/JobDetail/[^"]+)"[^>]*>([\s\S]*?)</a>'),
    re.compile(r'<a[^>]*class="link"[^>]*href="([^"]*/JobDetail/[^"]+)"[^>]*>([\s\S]*?)</a>'),
    re.compile(r'<a[^>]*href="([^"]*/JobDetail/[^"]+)"[^>]*>([\s\S]*?)</a>'),
)
_AVATURE_LOC = (
    re.compile(r"list-item-location[^>]*>([\s\S]*?)</span>", re.I),
    re.compile(r'class="[^"]*\blocation\b[^"]*"[^>]*>([\s\S]*?)</(?:span|div|li)>', re.I),
    re.compile(r"glyphicon-map-marker[\s\S]{0,80}?>([^<]{2,60})<", re.I),
    re.compile(r"icon-address[^>]*></span>([^<]{2,80})<", re.I),
)
_AVATURE_POSTED = re.compile(r"Posted\s+(\d{1,2})-([A-Za-z]{3})-(\d{4})")


def _avature_page(page: str, origin: str) -> tuple[list[dict[str, str]], int]:
    """(parsed postings, raw card count) of one SearchJobs page."""
    out: list[dict[str, str]] = []
    cards = _AVATURE_ARTICLE.findall(page)
    for block in cards:
        m = next((x for x in (r.search(block) for r in _AVATURE_TITLE) if x), None)
        if not m:
            continue
        title = _clean(m.group(2))
        href = html.unescape(m.group(1))
        url = _https(href if re.match(r"https?://", href, re.I) else origin + (href if href.startswith("/") else "/" + href))
        if not title or not url:
            continue
        idm = re.search(r"/JobDetail/[^/]*/(\d+)", url)
        loc = next((_clean(x.group(1)) for x in (r.search(block) for r in _AVATURE_LOC) if x), "")
        pm = _AVATURE_POSTED.search(block)
        mon = _MONTHS.get(pm.group(2).lower()) if pm else None
        posted = f"{pm.group(3)}-{mon:02d}-{int(pm.group(1)):02d}" if pm and mon else ""
        out.append({"id": idm.group(1) if idm else url, "title": title, "url": url, "location": loc, "posted": _iso_date(posted)})
    return out, len(cards)


def _avature(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Avature career site: server-rendered SearchJobs listing, 6 postings per page, no detail requests."""
    host = str(row.get("token") or "").strip().lower()
    if not _HOST.fullmatch(host):
        return None
    path = str(row.get("path") or "/careers/SearchJobs")
    if not re.fullmatch(r"/[A-Za-z0-9_/-]{0,80}SearchJobs", path):
        return None
    origin = f"https://{host}"
    seen: set[str] = set()
    jobs: list[Job] = []
    key = "jobOffset"
    healed = False
    for page in range(AVATURE_PAGES):
        st, body = _get(http, f"{origin}{path}?{key}={page * 6}", as_json=False, headers=_HTML)
        if st != 200 or not isinstance(body, str):
            if page:
                break  # a hiccup after the first page keeps what we have
            return None
        items, cards = _avature_page(body, origin)
        fresh = [a for a in items if a["id"] not in seen]
        if not fresh and page == 1 and not healed:
            healed = True  # the primary key was inert: retry this page once with the bare ``offset`` key
            st2, body2 = _get(http, f"{origin}{path}?offset=6", as_json=False, headers=_HTML)
            if st2 == 200 and isinstance(body2, str):
                items2, cards2 = _avature_page(body2, origin)
                fresh = [a for a in items2 if a["id"] not in seen]
                if fresh:
                    key, cards = "offset", cards2
        for a in fresh:
            seen.add(a["id"])
            jobs.append(_job(row, "avature", title=a["title"], url=a["url"], location=a["location"], posted=a["posted"],
                             remote=_remote("", a["location"])))
        if not fresh or cards < 6:
            break
    return jobs


# --------------------------------------------------------------------------- Comeet
# Borrowed from freehire comeet.go:75-91 (board "<slug>/<companyUID>"; GET https://www.comeet.com/jobs/<slug>/<uid> embeds the
# public read token; then GET https://www.comeet.co/careers-api/2.0/company/<uid>/positions?token=<token>&details=true returns
# every position fully populated), :148 (token pattern: "token":"<20+ alphanumerics>"), :41-56 (location: city, state, else
# country, else name), :115-124 (description = the named detail sections, unlabeled for "Description"), :131 and :141
# (workplace_type enum, time_updated). career-ops providers/comeet.mjs:123 supplies the url order (url_active_page, then
# url_comeet_hosted_page, https only), :14 and :25 the host lock (the API host is www.comeet.co under /careers-api/), and
# :136-142 appending "Remote" to a location when location.is_remote is set and the name does not already say so.
_COMEET_TOKEN = re.compile(r'"token"\s*:\s*"([A-Za-z0-9]{20,})"')


def _comeet(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Comeet careers: scrape the page token, then one careers-api call returns every position with its text."""
    board = str(row.get("token") or "").strip()
    slug, _, uid = board.partition("/")
    if not (_ID.fullmatch(slug) and _ID.fullmatch(uid)):
        return None
    st, page = _get(http, f"https://www.comeet.com/jobs/{quote(slug, safe='')}/{quote(uid, safe='')}", as_json=False, headers=_HTML)
    m = _COMEET_TOKEN.search(page) if st == 200 and isinstance(page, str) else None
    if not m:
        return None
    st, data = _get(http, f"https://www.comeet.co/careers-api/2.0/company/{quote(uid, safe='')}/positions?token={m.group(1)}&details=true", headers=_JSON)
    if st != 200 or not isinstance(data, list):
        return None
    jobs: list[Job] = []
    for p in data:
        if not isinstance(p, dict) or not str(p.get("uid") or ""):
            continue
        title = str(p.get("name") or "").strip()
        url = _https(p.get("url_active_page")) or _https(p.get("url_comeet_hosted_page"))
        if not title or not url:
            continue
        raw_loc = p.get("location")
        loc: dict[str, Any] = raw_loc if isinstance(raw_loc, dict) else {}
        place = _commas(loc.get("city"), loc.get("state")) or str(loc.get("country") or "").strip() or str(loc.get("name") or "").strip()
        if loc.get("is_remote") and "remote" not in place.lower():
            place = _commas(place, "Remote")
        parts = []
        for s in p.get("details") or []:
            if isinstance(s, dict) and str(s.get("value") or "").strip():
                name = str(s.get("name") or "")
                parts.append((f"{name}\n" if name and name.lower() != "description" else "") + _text(s.get("value")))
        mode = _mode(p.get("workplace_type"))
        j = _job(row, "comeet", title=title, url=url, location=place, posted=_iso_date(p.get("time_updated")),
                 description="\n\n".join(parts), remote=_remote(mode, place), extra={"work_mode": mode})
        j["company"] = _company(row, str(p.get("company_name") or ""))
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- HiBob
# Borrowed from freehire hibob.go:23-25 (GET https://<tenant>.careers.hibob.com/api/job-ad; the posting link is
# https://<tenant>.careers.hibob.com/jobs/<id>), :15-17 and :53 (the Referer header https://<tenant>.careers.hibob.com/jobs is
# required, else the API answers 401), :35-47 (jobAdDetails[]: id, title, site, country, description, responsibilities,
# requirements, benefits, publishedAt, workspaceType), :96-115 (the four body fields become one description, each under its
# heading). One request, no detail fetches.
def _hibob(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HiBob careers module: one keyless job-ad call (needs the tenant Referer) with every description inline."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _LABEL.fullmatch(tenant):
        return None
    st, data = _get(http, f"https://{tenant}.careers.hibob.com/api/job-ad", headers={**_JSON, "Referer": f"https://{tenant}.careers.hibob.com/jobs"})
    ads = data.get("jobAdDetails") if isinstance(data, dict) else None
    if st != 200 or not isinstance(ads, list):
        return None
    jobs: list[Job] = []
    for ad in ads:
        if not isinstance(ad, dict):
            continue
        jid, title = str(ad.get("id") or "").strip(), str(ad.get("title") or "").strip()
        if not jid or not title:
            continue
        parts = []
        for heading, key in (("", "description"), ("Responsibilities", "responsibilities"), ("Requirements", "requirements"), ("Benefits", "benefits")):
            body = _text(html.unescape(str(ad.get(key) or "")))
            if body:
                parts.append(f"{heading}\n{body}" if heading else body)
        mode = _mode(ad.get("workspaceType"))
        place = _commas(ad.get("site"), ad.get("country"))
        jobs.append(_job(row, "hibob", title=title, url=f"https://{tenant}.careers.hibob.com/jobs/{quote(jid, safe='')}", location=place,
                         posted=_iso_date(ad.get("publishedAt")), description="\n\n".join(parts), remote=_remote(mode, place),
                         extra={"work_mode": mode}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "avature": _wrap(_avature),
    "comeet": _wrap(_comeet),
    "hibob": _wrap(_hibob),
}
