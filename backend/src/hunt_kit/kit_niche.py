"""Remote, startup and niche job boards for the Job360 hunt kit.

Same contract as ``kit_feeds.py``: each ``fetch_*`` takes ``(queries, http, stats, *, days,
countries)``, never raises, and bumps ``stats``. ``FEEDS`` maps the source name to the fetcher.
Only boards with a public JSON API or RSS/Atom feed: no key, no login, no cookie. Nothing here
ranks or scores; ``queries`` is a plain keyword match, done locally when the board has no search.
Networking goes only through the ``http`` callable the core passes in. Standard library only.

The Polish boards (nofluffjobs, justjoin, solidjobs) keep every job when PL is one of the user's
countries (or the core passes "*": on-site abroad counts, or --any-location) and only the remote
jobs otherwise.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable
from urllib.parse import urlencode

try:  # same folder, on sys.path when the kit runs
    import kit_feeds as _kf  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _ilu
    from pathlib import Path as _Path

    _spec = _ilu.spec_from_file_location("kit_feeds", _Path(__file__).with_name("kit_feeds.py"))
    if _spec is None or _spec.loader is None:
        raise ImportError("kit_feeds.py not found next to this file")
    _kf = _ilu.module_from_spec(_spec)
    _spec.loader.exec_module(_kf)

Job = dict[str, Any]
_Fetch = Callable[..., list[Job]]
MAX_PAGES: int = _kf.MAX_PAGES
_feed: Callable[[_Fetch], _Fetch] = _kf._feed
_get: Callable[..., Any] = _kf._get
_job: Callable[..., Job] = _kf._job
_keep: Callable[..., list[Job]] = _kf._keep
_date: Callable[[Any], str] = _kf._date
_queries: Callable[[list[str]], list[str]] = _kf._queries
safe_fromstring: Callable[[Any], Any] = _kf.safe_fromstring
strip_html: Callable[[Any], str] = _kf.strip_html

_PREFIX = re.compile(r"<(/?)([A-Za-z_][\w.-]*):")  # "<job:company>" -> "<job_company>": no unbound prefixes
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9._~-]*")


# --------------------------------------------------------------------------- helpers
def _s(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _mode(value: Any) -> bool | None:
    """Work-arrangement text to the ``remote`` flag: remote -> True, any other stated mode -> False."""
    text = _s(value).lower()
    return None if not text else text == "remote"


def _where(*parts: Any, remote: bool | None = None) -> str:
    """Comma-joined distinct place parts; ``Remote`` goes first when the job is remote."""
    out = [p for p in (_s(x) for x in parts) if p]
    if remote and not any(p.lower() == "remote" for p in out):
        out.insert(0, "Remote")
    return ", ".join(dict.fromkeys(out))


def _list(body: Any, *keys: str) -> list[Any]:
    """``body[k1][k2]...`` when it is a list, else []. A bare list body is returned as is."""
    for k in keys:
        body = body.get(k) if isinstance(body, dict) else None
    return body if isinstance(body, list) else []


def _items(xml_text: Any) -> list[dict[str, str]]:
    """Every RSS ``<item>`` as {child tag: text}; namespace prefixes become ``prefix_``."""
    root = safe_fromstring(_PREFIX.sub(r"<\1\2_", xml_text)) if isinstance(xml_text, str) else None
    rows: list[dict[str, str]] = []
    for it in root.iter("item") if root is not None else []:
        row: dict[str, str] = {}
        for child in it:
            row.setdefault(child.tag.split("}")[-1], (child.text or "").strip())
        rows.append(row)
    return rows


def _split_at(raw: str) -> tuple[str, str]:
    """'Role at Company' split on the LAST ' at '; company '' when there is none."""
    title, sep, company = raw.rpartition(" at ")
    return (title.strip(), company.strip()) if sep and title.strip() and company.strip() else (raw, "")


def _walk(http: Any, stats: Any, url_for: Callable[[int], str], parse: Callable[[Any], tuple[list[Job], bool]],
          *, first: int = 1, pages: int = MAX_PAGES, **kw: Any) -> list[Job]:
    """Fetch up to ``pages`` pages; ``parse`` returns (jobs, more pages exist)."""
    jobs: list[Job] = []
    for n in range(first, first + pages):
        got, more = parse(_get(http, stats, url_for(n), **kw))
        jobs += got
        if not got or not more:
            break
    return jobs


def _one(url: str, parse: Callable[[Any], list[Job]], *, text: bool = False) -> Callable[..., list[Job]]:
    """Fetcher for a board whose whole list is a single request, keyword-matched locally."""
    @_feed
    def fetch(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
        return _keep(parse(_get(http, stats, url, as_json=not text)), queries, days)
    return fetch


def _unique(jobs: list[Job]) -> list[Job]:
    by_url = {j["url"]: j for j in reversed(jobs)}  # first sighting wins
    return [j for j in jobs if by_url[j["url"]] is j]


def _poland(jobs: list[Job], countries: list[str]) -> list[Job]:
    """PL is one of the user's countries, or "*" (on-site abroad counts / --any-location): keep all;
    otherwise only the remote jobs."""
    have = {c.upper() for c in countries}
    return jobs if "PL" in have or "*" in have else [j for j in jobs if j["remote"]]


# ------------------------------------------------------------------------------ 4dayweek
# Borrowed from freehire internal/ingest/sources/fourdayweek.go:35 (GET /api/v2/jobs?page=N&limit=100;
# v2 because robots.txt allows /api/v2, see :15-21), :64-82 (data[] fields), :101-117 (page walk on
# has_more), :125-176 (company.name, url or /job/<slug>, primary location, work_arrangement). Also
# career-ops providers/4dayweek.mjs:114-119 (location "city, country" + "Remote").
def parse_4dayweek(body: Any) -> tuple[list[Job], bool]:
    out: list[Job] = []
    for r in _list(body, "data"):
        locs = [x for x in r.get("locations") or [] if isinstance(x, dict)]
        loc = next((x for x in locs if x.get("is_primary")), locs[0] if locs else {})
        arr, slug = r.get("work_arrangement"), _s(r.get("slug"))
        out.append(_job(
            title=r.get("title"), company=(r.get("company") or {}).get("name"),
            url=r.get("url") or (f"https://4dayweek.io/job/{slug}" if _SLUG.fullmatch(slug) else ""),
            location=_where(loc.get("city"), loc.get("country"), remote=arr == "remote"),
            posted=_date(r.get("posted_at")), description=r.get("description"), source="feed:4dayweek",
            board="4dayweek", remote=_mode(arr),
            extra={"level": r.get("level"), "category": r.get("category"),
                   "tags": [s.get("name") for s in r.get("stack") or [] if isinstance(s, dict)]}))
    return out, bool(isinstance(body, dict) and body.get("has_more"))


@_feed
def fetch_4dayweek(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """4 Day Week: first pages of 100, keyword-matched locally."""
    jobs = _walk(http, stats, lambda n: f"https://4dayweek.io/api/v2/jobs?page={n}&limit=100", parse_4dayweek)
    return _keep(jobs, queries, days)


# ---------------------------------------------------------------------------- Landing.jobs
# Borrowed from career-ops providers/landingjobs.mjs:17 (GET /api/v1/jobs, one JSON array), :49-62
# (company from the /at/<slug>/ part of the url), :103-111 (first locations[] "city, country_code"
# + Remote when remote, published_at else created_at).
def parse_landingjobs(body: Any) -> list[Job]:
    out: list[Job] = []
    for r in body if isinstance(body, list) else []:
        if not isinstance(r, dict) or not _s(r.get("url")).startswith("https://landing.jobs/"):
            continue
        segs = [x for x in _s(r["url"]).split("/")[3:] if x]
        first = next((x for x in r.get("locations") or [] if isinstance(x, dict)), {})
        remote = r.get("remote") is True
        out.append(_job(
            title=r.get("title"), url=r["url"], source="feed:landingjobs", board="landingjobs",
            company=re.sub(r"[-_]+", " ", segs[1]).title() if len(segs) > 1 and segs[0] == "at" else "",
            location=_where(first.get("city"), first.get("country_code"), remote=remote),
            posted=_date(r.get("published_at") or r.get("created_at")), remote=remote or None,
            extra={"tags": r.get("tags") or [], "type": r.get("type")}))
    return out


fetch_landingjobs = _one("https://landing.jobs/api/v1/jobs", parse_landingjobs)


# ------------------------------------------------------------------------------ Get on Board
# Borrowed from career-ops providers/getonbrd.mjs:35-37 (GET /api/v0/categories/<cat>/jobs), :24 and
# :178 (programming category; per_page=100, expand[]=company, page=N), :116-159 (attributes.title,
# links.public_url, company.data.attributes.name, remote/countries, published_at epoch seconds),
# :192 (a short page is the last). machine-learning-ai is the second category the file names (:13-18).
def parse_getonbrd(body: Any) -> tuple[list[Job], bool]:
    out: list[Job] = []
    rows = _list(body, "data")
    for r in rows:
        a = r.get("attributes") if isinstance(r, dict) else None
        if not isinstance(a, dict):
            continue
        countries = a.get("countries")
        names = [countries] if isinstance(countries, str) else [c for c in countries or [] if isinstance(c, str)]
        remote = a.get("remote") is True
        company = (((a.get("company") or {}).get("data") or {}).get("attributes") or {}).get("name")
        out.append(_job(
            title=a.get("title"), company=company,
            url=(r.get("links") or {}).get("public_url"), location=_where(*names, remote=remote),
            posted=_date(a.get("published_at")), source="feed:getonbrd", board="getonbrd",
            remote=True if remote else None))
    return out, len(rows) >= 100


@_feed
def fetch_getonbrd(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """Get on Board: programming and machine-learning-ai, 3 pages of 100 each, keyword-matched locally."""
    jobs: list[Job] = []
    for cat in ("programming", "machine-learning-ai"):
        jobs += _walk(http, stats, lambda n, c=cat: (  # type: ignore[misc]
            f"https://www.getonbrd.com/api/v0/categories/{c}/jobs?per_page=100&expand%5B%5D=company&page={n}"),
            parse_getonbrd, pages=3)
    return _keep(_unique(jobs), queries, days)


# ---------------------------------------------------------------------------------- Manfred
# Borrowed from career-ops providers/manfred.mjs:24 and :89-91 (GET /api/v2/public/offers?lang=EN, one
# JSON array; lang is required), :165-191 (only status ACTIVE; position, id + slug -> /ofertas-empleo/
# <id>/<slug>, company.name; no date: updatedAt is not a posting date), :130-141 (locations, else
# Remote/Hybrid from remotePercentage).
def parse_manfred(body: Any) -> list[Job]:
    out: list[Job] = []
    for r in body if isinstance(body, list) else []:
        if (not isinstance(r, dict) or r.get("status") != "ACTIVE" or not _SLUG.fullmatch(_s(r.get("slug")))
                or not isinstance(r.get("id"), int) or r["id"] <= 0):
            continue
        pct = r.get("remotePercentage")
        pct = pct if isinstance(pct, (int, float)) else None
        place = _where(*[x for x in r.get("locations") or [] if isinstance(x, str)]) or (
            "" if pct is None else "Remote" if pct >= 100 else "Hybrid" if pct > 0 else "")
        out.append(_job(
            title=r.get("position"), company=(r.get("company") or {}).get("name"),
            url=f"https://www.getmanfred.com/ofertas-empleo/{r['id']}/{r['slug'].strip()}",
            location=place, source="feed:manfred", board="manfred",
            remote=None if pct is None else pct >= 100,
            extra={"salary_from": r.get("salaryFrom"), "salary_to": r.get("salaryTo"), "currency": r.get("currency")}))
    return out


fetch_manfred = _one("https://www.getmanfred.com/api/v2/public/offers?lang=EN", parse_manfred)


# -------------------------------------------------------------------------------- The Hub
# Borrowed from career-ops providers/thehub.mjs:35 (GET /api/v2/jobsandfeatured?page=N&countryCode=EU,
# 15 per page), :135-163 (jobs.docs, jobs.pages), :169-182 (a second isRemote=true pass, merged by url),
# :81-111 (id -> /jobs/<id>, company.name, location.address or locality + country, "Remote" when isRemote).
# The feed has no posting date.
def parse_thehub(body: Any) -> tuple[list[Job], bool]:
    docs = _list(body, "jobs", "docs")
    out: list[Job] = []
    for r in docs:
        loc = r.get("location") if isinstance(r.get("location"), dict) else {}
        remote = r.get("isRemote") is True
        jid = _s(r.get("id"))
        out.append(_job(
            title=r.get("title"), company=(r.get("company") or {}).get("name"),
            url=f"https://thehub.io/jobs/{jid}" if _SLUG.fullmatch(jid) else "",
            location=_where(_s(loc.get("address")) or _where(loc.get("locality"), loc.get("country")), remote=remote),
            source="feed:thehub", board="thehub", remote=True if remote else None))
    return out, len(docs) >= 15


@_feed
def fetch_thehub(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """The Hub (Nordic and EU startups): countryCode=EU, then the remote-only list, 3 pages each."""
    jobs: list[Job] = []
    for scope in ("countryCode=EU", "isRemote=true"):
        jobs += _walk(http, stats, lambda n, s=scope: (  # type: ignore[misc]
            f"https://thehub.io/api/v2/jobsandfeatured?page={n}&{s}"), parse_thehub, pages=3)
    return _keep(_unique(jobs), queries, days)


# -------------------------------------------------------------------------------- Remotli
# Borrowed from career-ops providers/remotli.mjs:59-82 (GET /api/jobs?page=N&limit=50&remote=all; the
# server caps limit at 50), :178-231 (rows are {jobs, companies}; only status active; applyUrl when https,
# else /jobs/<slug>; company; location + allLocations; description; publishedAt else createdAt),
# :304-321 (pagination.totalPages, a short page ends the walk).
def parse_remotli(body: Any) -> tuple[list[Job], bool]:
    rows = _list(body, "jobs")
    out: list[Job] = []
    for row in rows:
        j = row.get("jobs") if isinstance(row, dict) else None
        if not isinstance(j, dict) or _s(j.get("status")).lower() != "active":
            continue
        slug = _s(j.get("slug"))
        apply = _s(j.get("applyUrl"))
        board = f"https://remotli.ch/jobs/{slug}" if _SLUG.fullmatch(slug) else ""
        url = apply if apply.startswith("https://") else board
        place = _where(j.get("location"), *[x for x in j.get("allLocations") or [] if isinstance(x, str)])
        out.append(_job(
            title=j.get("title"), company=j.get("company") or (row.get("companies") or {}).get("name"),
            url=url, location=place, posted=_date(j.get("publishedAt") or j.get("createdAt")),
            description=j.get("description"), source="feed:remotli", board="remotli",
            remote=True if "remote" in place.lower() else None,
            extra={"salary_min": j.get("salaryMin"), "salary_max": j.get("salaryMax"),
                   "currency": j.get("salaryCurrency")}))
    return out, len(rows) >= 50


@_feed
def fetch_remotli(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """Remotli (remote roles at Swiss companies): first pages of 50, keyword-matched locally."""
    jobs = _walk(http, stats, lambda n: f"https://remotli.ch/api/jobs?page={n}&limit=50&remote=all", parse_remotli)
    return _keep(jobs, queries, days)


# ------------------------------------------------------------------------ a16z speedrun talent
# Borrowed from career-ops providers/a16z-speedrun-talent.mjs:26 (GET /api/v1/jobs?page=N, 0-based, 50 per
# page; the q param searches server-side, :160-161), :98-131 (title, url, company, location + Remote when
# remote, published_at), :197-202 (stop on an empty page or total_pages). The source param is the kit's name.
def parse_speedrun(body: Any) -> tuple[list[Job], bool]:
    rows = _list(body, "jobs")
    out: list[Job] = []
    for r in rows:
        remote = r.get("remote") is True
        out.append(_job(
            title=r.get("title"), company=r.get("company"), url=r.get("url"),
            location=_where(r.get("location"), remote=remote), posted=_date(r.get("published_at")),
            source="feed:speedrun", board="speedrun", remote=True if remote else None))
    return out, len(rows) >= 50


@_feed
def fetch_speedrun(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """a16z speedrun talent network: one server-side search per query, 3 pages each."""
    jobs: list[Job] = []
    for q in _queries(queries) or [""]:
        qs = urlencode({"source": "hunt-kit", **({"q": q} if q else {})})
        jobs += _walk(http, stats, lambda n, qs=qs: (  # type: ignore[misc]
            f"https://speedrun-talent-network.com/api/v1/jobs?page={n}&{qs}"), parse_speedrun, first=0, pages=3)
    return _keep(_unique(jobs), queries, days, search=False)


# ------------------------------------------------------------------------------- RSS boards
# Borrowed from career-ops providers/jobspresso.mjs:12 (GET /?feed=job_feed) and :103-127 (RSS items:
# title, link, pubDate, job_listing:company, job_listing:location).
def parse_jobspresso(xml_text: Any) -> list[Job]:
    return [_job(title=r.get("title"), company=r.get("job_listing_company"), url=r.get("link"),
                 location=r.get("job_listing_location"), posted=_date(r.get("pubDate")), remote=True,
                 source="feed:jobspresso", board="jobspresso") for r in _items(xml_text)]  # a remote-only board


fetch_jobspresso = _one("https://jobspresso.co/?feed=job_feed", parse_jobspresso, text=True)


# Borrowed from career-ops providers/nodesk.mjs:12 (GET /remote-jobs/index.xml), :86-96 (title is
# "Role at Company"), :110-136 (link, pubDate; the feed has no location tag, so "Remote" comes from the
# feed path /remote-jobs/).
def parse_nodesk(xml_text: Any) -> list[Job]:
    out = []
    for r in _items(xml_text):
        title, company = _split_at(r.get("title", ""))
        out.append(_job(title=title, company=company, url=r.get("link"), location="Remote", remote=True,
                        posted=_date(r.get("pubDate")), source="feed:nodesk", board="nodesk"))
    return out


fetch_nodesk = _one("https://nodesk.co/remote-jobs/index.xml", parse_nodesk, text=True)


# Borrowed from career-ops providers/cryptocurrencyjobs.mjs:22 (GET /index.xml; "listings are 100% remote",
# :6), :47-55 (title is "Role at Company"), :63-100 (link else guid, pubDate, description, location Remote).
def parse_cryptojobs(xml_text: Any) -> list[Job]:
    out = []
    for r in _items(xml_text):
        title, company = _split_at(r.get("title", ""))
        out.append(_job(title=title, company=company, url=r.get("link") or r.get("guid"), location="Remote",
                        remote=True, posted=_date(r.get("pubDate")), description=r.get("description"),
                        source="feed:cryptojobs", board="cryptojobs"))
    return out


fetch_cryptojobs = _one("https://cryptocurrencyjobs.co/index.xml", parse_cryptojobs, text=True)


# Borrowed from career-ops providers/pythonorg.mjs:17 (GET /jobs/feed/rss/), :85-146 (title is
# "Role, Company" split on the last comma; link else guid; location is the first line of description).
def parse_pythonjobs(xml_text: Any) -> list[Job]:
    out = []
    for r in _items(xml_text):
        title, sep, company = r.get("title", "").rpartition(",")
        if not sep or not title.strip() or not company.strip():
            continue
        desc = r.get("description", "")
        place = re.split(r"\n|<", desc, maxsplit=1)[0].strip()
        out.append(_job(title=title, company=company, url=r.get("link") or r.get("guid"), location=place,
                        posted=_date(r.get("pubDate")), description=desc, source="feed:pythonjobs",
                        board="pythonjobs", remote=True if "remote" in place.lower() else None))
    return out


fetch_pythonjobs = _one("https://www.python.org/jobs/feed/rss/", parse_pythonjobs, text=True)


# Borrowed from career-ops providers/larajobs.mjs:16 (GET /feed), :101-127 (link, pubDate, job:company else
# dc:creator, job:location).
def parse_larajobs(xml_text: Any) -> list[Job]:
    return [_job(title=r.get("title"), company=r.get("job_company") or r.get("dc_creator"), url=r.get("link"),
                 location=r.get("job_location"), posted=_date(r.get("pubDate")), source="feed:larajobs",
                 board="larajobs") for r in _items(xml_text)]


fetch_larajobs = _one("https://larajobs.com/feed", parse_larajobs, text=True)


# Borrowed from career-ops providers/startup-jobs.mjs:55-56 and :73-80 (GET startup.jobs/feeds/jobs; the
# JSON API needs a token, so the RSS only), :100-107 (title "Role at Company", rows without " at " are
# skipped, leading "bei " dropped), :110-117 (location is the last non-empty description line before " · "),
# :203-268 (link, pubDate, description).
def parse_startupjobs(xml_text: Any) -> list[Job]:
    out = []
    for r in _items(xml_text):
        title, company = _split_at(r.get("title", ""))
        if not company:
            continue
        desc = strip_html(r.get("description"))
        lines = [x.strip() for x in desc.split("\n") if x.strip()]
        place = lines[-1].split(" · ")[0].strip() if lines else ""
        out.append(_job(title=title, company=re.sub(r"^bei\s+", "", company, flags=re.I), url=r.get("link"),
                        location=place, posted=_date(r.get("pubDate")), description=desc,
                        source="feed:startupjobs", board="startupjobs",
                        remote=True if place.lower().startswith("remote") else None))
    return out


fetch_startupjobs = _one("https://startup.jobs/feeds/jobs", parse_startupjobs, text=True)


# Borrowed from career-ops providers/higheredjobs.mjs:16-18 (GET /rss/categoryFeed.cfm?catID=68, Higher
# Education), :96-106 (description is "Institution (City, ST)"), :121-146 (link, title, pubDate).
def parse_higheredjobs(xml_text: Any) -> list[Job]:
    out = []
    for r in _items(xml_text):
        text = r.get("description", "")
        open_at = text.rfind(" (")
        close_at = text.rfind(")")
        company, place = text, ""
        if open_at > 0:
            company, place = text[:open_at], text[open_at + 2:close_at if close_at > open_at else None]
        out.append(_job(title=r.get("title"), company=company, url=r.get("link"), location=place,
                        posted=_date(r.get("pubDate")), source="feed:higheredjobs", board="higheredjobs"))
    return out


fetch_higheredjobs = _one("https://www.higheredjobs.com/rss/categoryFeed.cfm?catID=68", parse_higheredjobs, text=True)


# -------------------------------------------------------------------------------- Poland
# Borrowed from career-ops providers/nofluffjobs.mjs:9-10 (POST /api/search/posting, job url
# /pl/job/<url>), :59-105 (query params sort/pageTo/pageSize/salaryCurrency/region/language and the empty
# criteriaSearch body), :146-153 (content-type application/infiniteSearch+json), :38-52 (fullyRemote +
# location.places city), :107-127 (title, name, url, posted epoch ms), :160-162 (totalPages).
def parse_nofluffjobs(body: Any) -> tuple[list[Job], bool]:
    out: list[Job] = []
    for r in _list(body, "postings"):
        if not isinstance(r, dict) or not _s(r.get("url")):
            continue
        loc: dict[str, Any] = r["location"] if isinstance(r.get("location"), dict) else {}
        remote = r.get("fullyRemote") is True or loc.get("fullyRemote") is True
        ms = r.get("posted")
        cities = [_s(p.get("city")) or _s(p.get("province")) or _s((p.get("country") or {}).get("name"))
                  for p in loc.get("places") or [] if isinstance(p, dict)]
        out.append(_job(
            title=r.get("title"), company=r.get("name"), url="https://nofluffjobs.com/pl/job/" + _s(r["url"]),
            location=_where(*cities, remote=remote), posted=_date(ms / 1000 if isinstance(ms, (int, float)) else None),
            source="feed:nofluffjobs", board="nofluffjobs", remote=True if remote else None))
    return out, bool(out)


@_feed
def fetch_nofluffjobs(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """No Fluff Jobs (Poland): newest 5 pages of 20, keyword-matched locally; remote only unless PL is yours."""
    empty = ["country", "withSalaryMatch", "city", "more", "employment", "requirement", "salary", "jobPosition",
             "applicationStatus", "province", "company", "id", "category", "keyword", "jobLanguage", "seniority"]
    body = json.dumps({"criteriaSearch": {k: [] for k in empty}, "pageSize": 20, "withSalaryMatch": True}).encode()
    jobs = _walk(http, stats, lambda n: "https://nofluffjobs.com/api/search/posting?" + urlencode({
        "sort": "newest", "withSalaryMatch": "true", "pageTo": n, "pageSize": 20, "salaryCurrency": "PLN",
        "salaryPeriod": "month", "region": "pl", "language": "pl-PL"}), parse_nofluffjobs, method="POST",
        data=body, headers={"Accept": "application/json", "Content-Type": "application/infiniteSearch+json"})
    return _keep(_poland(_unique(jobs), countries), queries, days)


# Borrowed from career-ops providers/justjoin.mjs:9-10 (GET /api/candidate-api/offers, job url
# /job-offer/<slug>), :62-78 (from, itemsCount, cityRadius, currency, orderBy, sortBy, keywordType,
# isPromoted), :42-55 (workplaceType + city), :80-99 (slug, title, companyName, publishedAt),
# :121-124 (meta.next.cursor is the next "from").
def parse_justjoin(body: Any) -> tuple[list[Job], Any]:
    out: list[Job] = []
    for r in _list(body, "data"):
        if not isinstance(r, dict) or not _SLUG.fullmatch(_s(r.get("slug"))):
            continue
        wp = _s(r.get("workplaceType"))
        cities = [_s(x.get("city")) for x in r.get("locations") or [] if isinstance(x, dict)] + [r.get("city")]
        out.append(_job(
            title=r.get("title"), company=r.get("companyName"),
            url="https://justjoin.it/job-offer/" + r["slug"].strip(),
            location=_where(*cities, remote=_mode(wp)), posted=_date(r.get("publishedAt")),
            source="feed:justjoin", board="justjoin", remote=_mode(wp)))
    cursor = (((body.get("meta") or {}).get("next") or {}).get("cursor")) if isinstance(body, dict) else None
    return out, cursor


@_feed
def fetch_justjoin(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """Just Join IT (Poland): newest offers by cursor, up to MAX_PAGES of 100; remote only unless PL is yours."""
    jobs: list[Job] = []
    cursor: Any = 0
    for _ in range(MAX_PAGES):
        qs = urlencode({"from": cursor, "itemsCount": 100, "cityRadius": 30, "currency": "pln",
                        "orderBy": "descending", "sortBy": "publishedAt", "keywordType": "any", "isPromoted": "true"})
        got, cursor = parse_justjoin(_get(http, stats, f"https://justjoin.it/api/candidate-api/offers?{qs}"))
        jobs += got
        if not got or cursor is None:
            break
    return _keep(_poland(_unique(jobs), countries), queries, days)


# Borrowed from career-ops providers/solidjobs.mjs:4-8 (GET /public-api/offers/<division>; division it),
# :60-80 (jobs[]: title, url, company, locations as a list; no date).
# freehire internal/ingest/sources/solidjobs.go:17-19,34-36 (the endpoint REQUIRES a ?campaign=<lowercase-alnum-dash>
# query param; it is echoed into every posting url), :51-61 (jobOfferKey, isRemote, validFrom, description inline).
SOLIDJOBS_URL = "https://solid.jobs/public-api/offers/it?campaign=job360"


def parse_solidjobs(body: Any) -> list[Job]:
    out: list[Job] = []
    for r in _list(body, "jobs"):
        places = r.get("locations")
        places = [places] if isinstance(places, str) else [x for x in places or [] if isinstance(x, str)]
        remote = True if r.get("isRemote") is True or any(p.lower() == "remote" for p in places) else None
        out.append(_job(title=r.get("title"), company=r.get("company"), url=r.get("url"),
                        location=_where(*places), posted=_date(r.get("validFrom")),
                        description=r.get("description"), source="feed:solidjobs", board="solidjobs",
                        remote=remote))
    return out


@_feed
def fetch_solidjobs(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """SolidJobs (Poland, IT division): one request, keyword-matched locally; remote only unless PL is yours."""
    jobs = parse_solidjobs(_get(http, stats, SOLIDJOBS_URL))
    return _keep(_poland(jobs, countries), queries, days)


# ----------------------------------------------------------------- PowerToFly, JobStash
# Borrowed from freehire internal/ingest/sources/powertofly.go:20 (GET /api/v1/jobs/?page=N), :60-84 (data[],
# meta.next_page), :92-112 (id -> /jobs/detail/<id>, company.name, city/state/country titles else
# location_regions; "location" is the work arrangement, Onsite/Hybrid/Remote). No posting date.
def _title(place: Any) -> str:
    return _s(place.get("title")) if isinstance(place, dict) else ""


def parse_powertofly(body: Any) -> tuple[list[Job], bool]:
    out: list[Job] = []
    for r in _list(body, "data"):
        mode = _mode(r.get("location"))
        out.append(_job(
            title=r.get("title"), company=(r.get("company") or {}).get("name"),
            url=f"https://powertofly.com/jobs/detail/{r['id']}" if isinstance(r.get("id"), int) and r["id"] else "",
            location=_where(*[_title(r.get(k)) for k in ("city", "state", "country")])
            or _where(*(r.get("location_regions") or [])),
            description=r.get("description"), source="feed:powertofly", board="powertofly", remote=mode,
            extra={"employment_type": r.get("employment_type")}))
        out[-1]["location"] = _where(out[-1]["location"], remote=mode)
    nxt = (body.get("meta") or {}).get("next_page") if isinstance(body, dict) else None
    return out, nxt is not None


@_feed
def fetch_powertofly(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """PowerToFly: first pages of the open list, keyword-matched locally."""
    jobs = _walk(http, stats, lambda n: f"https://powertofly.com/api/v1/jobs/?page={n}", parse_powertofly)
    return _keep(jobs, queries, days)


# Borrowed from freehire internal/ingest/sources/jobstash.go:20 and :24 (GET middleware.jobstash.xyz/
# jobs/list?page=N&limit=200), :78-94 (total, data[]; stop when total is reached), :102-129 (shortUUID,
# organization.name, url else jobstash.xyz/jobs/<shortUUID>/details, location, locationType, timestamp in
# epoch milliseconds), :134+ (description with responsibilities, requirements, benefits).
def parse_jobstash(body: Any) -> tuple[list[Job], bool]:
    rows = _list(body, "data")
    out: list[Job] = []
    for r in rows:
        uid = _s(r.get("shortUUID"))
        text = "\n".join([_s(r.get("description"))] + [f"{h}:\n- " + "\n- ".join(map(str, r[k]))
                         for h, k in (("Responsibilities", "responsibilities"), ("Requirements", "requirements"),
                                      ("Benefits", "benefits")) if isinstance(r.get(k), list) and r[k]])
        ts = r.get("timestamp")
        mode = _mode(r.get("locationType"))
        out.append(_job(
            title=r.get("title"), company=(r.get("organization") or {}).get("name"),
            url=r.get("url") or (f"https://jobstash.xyz/jobs/{uid}/details" if _SLUG.fullmatch(uid) else ""),
            location=_where(r.get("location"), remote=mode), description=text,
            posted=_date(ts / 1000 if isinstance(ts, (int, float)) else None),
            source="feed:jobstash", board="jobstash", remote=mode))
    total = body.get("total") if isinstance(body, dict) else None
    return out, not isinstance(total, int) or len(rows) < total


@_feed
def fetch_jobstash(queries: list[str], http: Any, stats: Any, *, days: int, countries: list[str]) -> list[Job]:
    """JobStash (Web3): first pages of 200, keyword-matched locally."""
    jobs = _walk(http, stats, lambda n: f"https://middleware.jobstash.xyz/jobs/list?page={n}&limit=200", parse_jobstash)
    return _keep(jobs, queries, days)


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "4dayweek": fetch_4dayweek,
    "landingjobs": fetch_landingjobs,
    "getonbrd": fetch_getonbrd,
    "manfred": fetch_manfred,
    "thehub": fetch_thehub,
    "remotli": fetch_remotli,
    "speedrun": fetch_speedrun,
    "jobspresso": fetch_jobspresso,
    "nodesk": fetch_nodesk,
    "cryptojobs": fetch_cryptojobs,
    "pythonjobs": fetch_pythonjobs,
    "larajobs": fetch_larajobs,
    "startupjobs": fetch_startupjobs,
    "nofluffjobs": fetch_nofluffjobs,
    "justjoin": fetch_justjoin,
    "solidjobs": fetch_solidjobs,
    "powertofly": fetch_powertofly,
    "jobstash": fetch_jobstash,
    "higheredjobs": fetch_higheredjobs,
}
