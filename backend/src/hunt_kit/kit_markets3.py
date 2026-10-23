"""Global marketplaces for the Job360 hunt kit, batch 3: EchoJobs, Torre, Tecla and Japan Dev (four feeds).

Same contract as ``kit_sites_b.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``, never
raises, networking only through the ``http`` callable the core passes in. Every ad names its own employer (the
board name is never put in ``company``; an ad with no employer is dropped). One run sends at most
``BOARDS3_MAX_REQUESTS`` requests per site; the first answer that is not HTTP 200 and a login or CAPTCHA page end
that site's run at once; only a 404/410 on an ad page skips that ad. A job link is always built by the reader on
the board's own https host (the slug or id read from a sitemap entry), never taken from a page, so no foreign or
non-https link is returned or fetched. Nothing here ranks or scores. No logins, no cookies, no keys. Standard
library only. Ads on a place that names a country the user did not choose are dropped unless they are remote
(NOT IN REFERENCE: freehire keeps every ad and filters later).

Built: EchoJobs (echojobs.go:1-48 sitemap + ld+json), Torre (torre.go sitemap + ld+json; career-ops torre.mjs:1-14
says its search API is retired and is not used), Tecla (tecla.go) and Japan Dev (japandev.go).

Not built, on purpose:
  * 4 Day Week and CryptocurrencyJobs: already read by ``kit_niche`` (feeds ``4dayweek`` and ``cryptojobs``).
  * Gradcracker (gradcracker.go:38-40): every path sits behind Cloudflare and freehire serves it only to a Chrome TLS
    fingerprint, which the kit's urllib helper cannot imitate. ``gradcracker.com`` is reported as unsupported.

Endpoints, params and field names follow the MIT-licensed freehire files named above each reader (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is
tested on fixtures shaped like the references' data only.
"""
# ruff: noqa: E501
from __future__ import annotations

import html as _html
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

try:
    import kit_sites_b as _sb  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_sites_b as _sb

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _sb._feed
_keep: Callable[..., list[Job]] = _sb._keep
_date: Callable[[Any], str] = _sb._date
_made: Callable[..., Job] = _sb._made
_unique: Callable[[list[Job]], list[Job]] = _sb._unique
_Budget: Any = _sb._Budget
_fetch: Callable[..., Any] = _sb._fetch
_read: Callable[..., list[Job]] = _sb._read
_ld_postings: Callable[[str], list[dict[str, Any]]] = _sb._ld_postings
_script_bodies: Callable[..., Any] = _sb._script_bodies
_sitemap_entries: Callable[[Any], list[tuple[str, str]]] = _sb._sitemap_entries
_sitemap_locs: Callable[[Any], list[str]] = _sb._sitemap_locs
_slug_hit: Callable[[str, list[str]], bool] = _sb._slug_hit
_place_ok: Callable[..., bool] = _sb._place_ok
_unescaped: Callable[[Any], str] = _sb._unescaped
_dict: Callable[[Any], dict[str, Any]] = _sb._dict
_SITEMAP_MAX: int = _sb._SITEMAP_MAX
_WS = re.compile(r"\s+")

BOARDS3_MAX_REQUESTS = 30  # hard cap per site per run (index + shards + detail pages)
DETAIL_CAP = 15  # ad pages read per run, per site
SHARD_CAP = 2  # sitemap shards read per run (EchoJobs and Torre list newest first)
TECLA_PAGES = 10  # tecla.go:15 allows 100 pages; the kit reads the first ten
PAUSE_S = 1.0  # between two requests to one site (tests set it to 0)
ECHOJOBS_WINDOW = timedelta(days=14)  # echojobs.go:60-64: the walk stops once a shard ages past two weeks
_EPOCH = datetime.min.replace(tzinfo=timezone.utc)


def _pause() -> None:
    _sb._pause(PAUSE_S)


def _safe(parse: Callable[[Any, str], Job | None]) -> Callable[[Any, str], Job | None]:
    """One bad posting (any exception while reading it) is dropped, never the whole board."""
    def wrapped(page: Any, url: str) -> Job | None:
        try:
            return parse(page, url)
        except Exception:  # noqa: BLE001 - per-posting isolation
            return None
    return wrapped


def _on(url: str, host: str) -> Any:
    """The split URL when it is https on exactly ``host`` (no port, no userinfo), else None."""
    try:
        u = urlsplit(url.strip())
        if u.scheme == "https" and u.hostname == host and u.netloc == host:
            return u
    except ValueError:
        pass
    return None


def _when(text: str) -> datetime:
    """A sitemap lastmod as an aware datetime; unreadable text sorts oldest."""
    try:
        d = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    except ValueError:
        return _EPOCH
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _ld_first(page: Any) -> dict[str, Any]:
    ps = _ld_postings(page) if isinstance(page, str) else []
    return ps[0] if ps else {}


def _ld_place(p: dict[str, Any]) -> str:
    """Locality, region, country of the first jobLocation (object or list), else the applicantLocationRequirements."""
    locs = p.get("jobLocation")
    first = _dict(locs[0] if isinstance(locs, list) and locs else locs)
    addr = _dict(first.get("address"))
    country = addr.get("addressCountry")
    country = country.get("name") if isinstance(country, dict) else country
    parts = [str(x).strip() for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x]
    if not parts:
        areas = p.get("applicantLocationRequirements")
        areas = areas if isinstance(areas, list) else [areas]
        parts = [str(a.get("name")).strip() for a in areas if isinstance(a, dict) and a.get("name")]
    return ", ".join(dict.fromkeys(x for x in parts if x))


def _company(p: dict[str, Any]) -> str:
    org = p.get("hiringOrganization")
    return _WS.sub(" ", str(org.get("name") or "")).strip() if isinstance(org, dict) else ""


def _telecommute(p: dict[str, Any]) -> bool:
    return str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"


def _finish(jobs: list[Job], queries: list[str], days: int, countries: list[str]) -> list[Job]:
    jobs = [j for j in jobs if _place_ok(j["location"], countries, j["remote"])]
    return _keep(_unique(jobs), queries, days)


def _details(http: HttpFn, stats: Stats, name: str, budget: Any, urls: list[str],
             parse: Callable[[Any, str], Job | None]) -> list[Job]:
    return _read(http, stats, name, budget, urls, DETAIL_CAP, _safe(parse), pause=PAUSE_S)


# ---------------------------------------------------------------------------------------- EchoJobs
# Borrowed from freehire internal/ingest/sources/echojobs.go:16-24 (the JSON API is gone; a plain GET reaches the
# sitemap and each posting page), :74-75 (https://echojobs.io/sitemap.xml), :228 (shards match
# /sitemap-jobs/N.xml, listed newest first), :60-64 and :205-208 (the walk stops once a shard's postings are older
# than two weeks), :251-257 (the slug is the last path part of /job/<slug>), :259-262 (the page is
# https://echojobs.io/job/<slug>), :270-296 and :326-337 (the page's ld+json JobPosting gives title,
# hiringOrganization.name, description (HTML-escaped: unescaped first, :344), datePosted, jobLocation address or
# applicantLocationRequirements, jobLocationType TELECOMMUTE = remote). :40-47: the employer's own apply link is not
# in the page, so the job link is the echojobs.io page. NOT IN REFERENCE: the title match on the slug before a
# page is read, the cap on shards and pages.
ECHOJOBS_SITEMAP = "https://echojobs.io/sitemap.xml"
_ECHO_SHARD = re.compile(r"^/sitemap-jobs/\d+\.xml$")
_ECHO_SLUG = re.compile(r"^/job/([A-Za-z0-9][A-Za-z0-9._~%-]*)/?$")


def echojobs_slugs(shard: Any, now: datetime) -> tuple[list[str], bool]:
    """(slugs of one job shard in file order, True when an entry is older than the window)."""
    out: list[str] = []
    stale = False
    for loc, lastmod in _sitemap_entries(shard):
        u = _on(loc, "echojobs.io")
        m = _ECHO_SLUG.match(u.path) if u and not u.query and not u.fragment else None
        if not m:
            continue
        if lastmod and _when(lastmod) != _EPOCH and _when(lastmod) < now - ECHOJOBS_WINDOW:
            stale = True
            continue
        out.append(m.group(1))
    return out, stale


def parse_echojobs_ad(page: Any, url: str) -> Job | None:
    p = _ld_first(page)
    title, company = _WS.sub(" ", str(p.get("title") or "")).strip(), _company(p)
    if not (p and title and company):
        return None
    remote = _telecommute(p)
    return _made("echojobs", "EchoJobs", title=title, company=company, url=url, place=_ld_place(p), country="",
                 posted=_date(p.get("datePosted")), description=_unescaped(p.get("description")),
                 remote=True if remote else None)


@_feed
def fetch_echojobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """EchoJobs (tech jobs aggregator): the newest sitemap shards, then the ad pages whose slug matches a query."""
    budget = _Budget(BOARDS3_MAX_REQUESTS)
    index = _fetch(http, stats, "echojobs", budget, ECHOJOBS_SITEMAP, as_json=False, max_bytes=_SITEMAP_MAX)
    shards = [loc for loc in _sitemap_locs(index) if (u := _on(loc, "echojobs.io")) and _ECHO_SHARD.match(u.path)]
    now = datetime.now(timezone.utc)
    slugs: list[str] = []
    for shard in shards[:SHARD_CAP]:
        got, stale = echojobs_slugs(_fetch(http, stats, "echojobs", budget, shard, as_json=False,
                                           max_bytes=_SITEMAP_MAX), now)
        slugs += got
        if stale or budget.left <= 0:
            break
    urls = [f"https://echojobs.io/job/{s}" for s in dict.fromkeys(slugs) if _slug_hit(s, queries)]
    return _finish(_details(http, stats, "echojobs", budget, urls, parse_echojobs_ad), queries, days, countries)


# ------------------------------------------------------------------------------------------ Torre
# Borrowed from freehire internal/ingest/sources/torre.go (torreSitemapIndexURL https://torre.ai/sitemap-index.xml;
# every child sitemap lists posting pages; torrePostRE ^https://torre.ai/post/<id>(-slug)?$; the page's ld+json
# JobPosting gives title, hiringOrganization.name, description (may be JSON-escaped or HTML-escaped: torreUnescape),
# datePosted, jobLocation[] address (city, region, country; streetAddress only when it has no digit),
# jobLocationType TELECOMMUTE = remote "Remote, <where>"). A posting with no title, employer or description text is
# not usable. career-ops providers/torre.mjs:1-14: the old search.torre.co API answers 400 to every query (retired),
# so it is not used. NOT IN REFERENCE: the title match on the slug before a page is read, the cap on pages.
TORRE_INDEX = "https://torre.ai/sitemap-index.xml"
_TORRE_POST = re.compile(r"^/post/([A-Za-z0-9]+)(?:-([^/?#]*))?$")


def torre_posts(sitemap: Any) -> list[tuple[str, str]]:
    """(page url, slug words) of every posting of one child sitemap, newest lastmod first, one per id."""
    seen: set[str] = set()
    rows: list[tuple[datetime, str, str]] = []
    for loc, lastmod in _sitemap_entries(sitemap):
        u = _on(loc, "torre.ai")
        m = _TORRE_POST.match(u.path) if u and not u.query and not u.fragment else None
        if not m or m.group(1) in seen:
            continue
        seen.add(m.group(1))
        rows.append((_when(lastmod), f"https://torre.ai{u.path}" if u else "", m.group(2) or ""))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(url, words) for _, url, words in rows]


def _torre_text(raw: Any) -> str:
    s = str(raw or "")
    if "\\" in s:
        try:
            decoded = json.loads('"' + s + '"')
            s = decoded if isinstance(decoded, str) else s
        except ValueError:
            pass
    if "&lt;p&gt;" in s or "&lt;br" in s:
        s = _html.unescape(s)
    return s


def parse_torre_ad(page: Any, url: str) -> Job | None:
    p = _ld_first(page)
    title, company = _WS.sub(" ", str(p.get("title") or "")).strip(), _company(p)
    if not (p and title and company):
        return None
    locs = p.get("jobLocation")
    parts: list[str] = []
    for loc in locs if isinstance(locs, list) else [locs]:
        a = _dict(_dict(loc).get("address"))
        street = str(a.get("streetAddress") or "").strip()
        city = str(a.get("addressLocality") or "").strip() or ("" if any(c.isdigit() for c in street) else street)
        country = a.get("addressCountry")
        country = country.get("name") if isinstance(country, dict) else country
        parts.append(", ".join(dict.fromkeys(str(x).strip() for x in (city, a.get("addressRegion"), country) if x)))
    where = "; ".join(dict.fromkeys(x for x in parts if x))
    remote = _telecommute(p)
    description = _torre_text(p.get("description"))
    if not _sb.strip_html(description):
        return None  # torre.go: a posting with no description text is not usable
    if remote:
        where = "Remote, " + (where or "Worldwide")
    return _made("torre", "Torre", title=title, company=company, url=url, place=where, country="",
                 posted=_date(p.get("datePosted")), description=description,
                 remote=True if remote else None)


@_feed
def fetch_torre(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Torre (LatAm-heavy marketplace): the first child sitemaps, then the post pages whose slug matches a query."""
    budget = _Budget(BOARDS3_MAX_REQUESTS)
    index = _fetch(http, stats, "torre", budget, TORRE_INDEX, as_json=False, max_bytes=_SITEMAP_MAX)
    children = [loc for loc in _sitemap_locs(index) if _on(loc, "torre.ai")]
    posts: list[tuple[str, str]] = []
    for child in children[:SHARD_CAP]:
        if budget.left <= 0:
            break
        posts += torre_posts(_fetch(http, stats, "torre", budget, child, as_json=False, max_bytes=_SITEMAP_MAX))
    urls = [u for u, words in dict.fromkeys(posts) if _slug_hit(words, queries)]
    return _finish(_details(http, stats, "torre", budget, urls, parse_torre_ad), queries, days, countries)


# ------------------------------------------------------------------------------------------ Tecla
# Borrowed from freehire internal/ingest/sources/tecla.go:12 (GET https://api.tecla.io/api/jobs/getPublicJobs/?page=N),
# :13 (job page https://app.tecla.io/job?id=<id>), :31-48 (data.jobs[] with id, name, company.name, createdAt,
# description; data.pagination.countPages), :62-79 (a first page that fails is an error, a later failure ends the
# walk with what was gathered, an empty page ends it, page >= countPages ends it), :81-90 (every ad is remote;
# createdAt layout 2006-01-02T15:04:05.999999999). NOT IN REFERENCE: the cap of TECLA_PAGES pages.
TECLA_LIST = "https://api.tecla.io/api/jobs/getPublicJobs/?page=%d"


def parse_tecla(body: Any) -> tuple[list[Job], int]:
    """(jobs of one list page, countPages)."""
    data = _dict(_dict(body).get("data"))
    pages = _dict(data.get("pagination")).get("countPages")
    out: list[Job] = []
    rows = data.get("jobs")
    for r in rows if isinstance(rows, list) else []:
        try:
            r = _dict(r)
            ident, title, company = r.get("id"), _WS.sub(" ", str(r.get("name") or "")).strip(), \
                _WS.sub(" ", str(_dict(r.get("company")).get("name") or "")).strip()
            if not (isinstance(ident, int) and not isinstance(ident, bool) and ident > 0 and title and company):
                continue
            out.append(_made("tecla", "Tecla", title=title, company=company, url=f"https://app.tecla.io/job?id={ident}",
                             place="Remote", country="", posted=_date(re.sub(r"\.\d+$", "", str(r.get("createdAt") or ""))),
                             description=r.get("description"), remote=True))
        except Exception:  # noqa: BLE001, S112 - one bad row never loses the page
            continue
    return out, pages if isinstance(pages, int) and not isinstance(pages, bool) else 0


@_feed
def fetch_tecla(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Tecla (remote roles for LatAm talent): the first pages of the public list, keyword-matched locally."""
    budget = _Budget(BOARDS3_MAX_REQUESTS)
    jobs: list[Job] = []
    for n in range(1, TECLA_PAGES + 1):
        if n > 1:
            _pause()
        body = _fetch(http, stats, "tecla", budget, TECLA_LIST % n)
        if body is None:
            break
        got, count = parse_tecla(body)
        jobs += got
        if not got or n >= count:
            break
    return _finish(jobs, queries, days, countries)


# -------------------------------------------------------------------------------------- Japan Dev
# Borrowed from freehire internal/ingest/sources/japandev.go:19 (sitemap index https://japan-dev.com/sitemap.xml),
# :78-92 (the first child sitemap whose URL holds /sitemaps/ lists the postings), :113-130 (a posting is
# https://japan-dev.com/jobs/<company>/<slug>, exactly three path parts, the id is the slug), :168-213 (the page's
# __NUXT_DATA__ script is a JSON table whose numbers are indexes into it: the first cell with a "job" key that
# points at an object with a title and a slug is the posting; company.name; a slug that differs from the URL's is
# not this posting), :215-238 (fields: title, location, raw_content, published_at else updated_at, remote_level,
# salary_min and salary_max in JPY a year, skills[].name, sponsors_visas, japanese_level_enum, english_level_enum,
# candidate_location), :362-374 (remote_level_partial = hybrid, _full_japan/_full_worldwide/_around_office = remote,
# _none = on-site), :376-385 and :411-446 (eligibility and language facts go in front of the description). The
# ad's own application_url (:188-190) is NOT used: the job link stays on japan-dev.com. The class-based description
# fallback (:176-181) is not read. Every ad but a worldwide-remote one is in Japan (:343-349).
JAPANDEV_INDEX = "https://japan-dev.com/sitemap.xml"
_NUXT_OPEN = re.compile(r"""<script[^>]+id=["']__NUXT_DATA__["'][^>]*>""", re.I)
_JD_LEVELS = {"not_required": "Not required", "conversational": "Conversational", "business_level": "Business level",
              "fluent": "Fluent"}


def japandev_posts(sitemap: Any) -> list[tuple[str, str]]:
    """(page url, slug) of every canonical posting URL of the posting sitemap, newest lastmod first, one per slug."""
    seen: set[str] = set()
    rows: list[tuple[datetime, str, str]] = []
    for loc, lastmod in _sitemap_entries(sitemap):
        u = _on(loc, "japan-dev.com")
        parts = u.path.strip("/").split("/") if u and not u.query and not u.fragment else []
        if len(parts) != 3 or parts[0] != "jobs" or not parts[1] or not parts[2] or parts[2] in seen:
            continue
        if not re.fullmatch(r"[A-Za-z0-9._~%-]+", parts[1]) or not re.fullmatch(r"[A-Za-z0-9._~%-]+", parts[2]):
            continue
        seen.add(parts[2])
        rows.append((_when(lastmod), f"https://japan-dev.com/jobs/{parts[1]}/{parts[2]}", parts[2]))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [(url, slug) for _, url, slug in rows]


def _jd_deref(table: list[Any], v: Any) -> Any:
    if isinstance(v, int) and not isinstance(v, bool):
        return table[v] if 0 <= v < len(table) else None
    return v


def _jd_str(table: list[Any], v: Any) -> str:
    s = _jd_deref(table, v)
    return s.strip() if isinstance(s, str) else ""


def _jd_int(table: list[Any], v: Any) -> int:
    n = _jd_deref(table, v)
    if isinstance(n, str) and n.strip().isdigit():
        n = int(n.strip())
    return n if isinstance(n, int) and not isinstance(n, bool) and n > 0 else 0


def _jd_label(raw: str, prefix: str) -> str:
    head = prefix + "_level_"
    return _JD_LEVELS.get(raw[len(head):], "") if raw.startswith(head) else ""


def parse_japandev_ad(page: Any, url: str) -> Job | None:
    body = next(_script_bodies(page, _NUXT_OPEN), None)
    try:
        table = json.loads(body) if body is not None else None
    except ValueError:
        return None
    if not isinstance(table, list):
        return None
    job: dict[str, Any] = {}
    for cell in table:
        if isinstance(cell, dict) and "job" in cell:
            cand = _jd_deref(table, cell["job"])
            if isinstance(cand, dict) and "title" in cand and "slug" in cand:
                job = cand
                break
    if not job:
        return None

    def s(key: str) -> str:
        return _jd_str(table, job.get(key))

    company = _jd_str(table, _dict(_jd_deref(table, job.get("company"))).get("name"))
    title, slug = _WS.sub(" ", s("title")), s("slug")
    if not (title and company) or (slug and slug != url.rstrip("/").rsplit("/", 1)[-1]):
        return None
    remote_level, location = s("remote_level"), s("location")
    mode = {"remote_level_partial": "hybrid", "remote_level_full_japan": "remote", "remote_level_full_worldwide": "remote",
            "remote_level_around_office": "remote", "remote_level_none": "onsite"}.get(remote_level, "")
    facts = []
    facts += {"candidate_location_anywhere": ["Applications: candidates may apply from outside Japan"],
              "candidate_location_japan_only": ["Applications: candidates must already be in Japan"]}.get(
        s("candidate_location"), [])
    facts += {"sponsors_visas_yes": ["Visa sponsorship: available"],
              "sponsors_visas_no": ["Visa sponsorship: not available"]}.get(s("sponsors_visas"), [])
    for label, key, prefix in (("Japanese", "japanese_level_enum", "japanese"), ("English", "english_level_enum", "english")):
        if _jd_label(s(key), prefix):
            facts.append(f"{label}: {_jd_label(s(key), prefix)}")
    listed = _jd_deref(table, job.get("skills"))
    names = [_jd_str(table, _dict(_jd_deref(table, x)).get("name")) for x in listed] if isinstance(listed, list) else []
    names = [n for n in dict.fromkeys(names) if n]
    if names:
        facts.append("Japan Dev tags: " + ", ".join(names))
    lo, hi = _jd_int(table, job.get("salary_min")), _jd_int(table, job.get("salary_max"))
    if lo or hi:
        facts.append("Salary: " + " - ".join(f"{x:,}" for x in (lo, hi) if x) + " JPY a year")
    text = _sb.strip_html(s("raw_content"))
    if not text:
        return None
    worldwide = remote_level == "remote_level_full_worldwide" and location.lower() == "remote"
    return _made("japandev", "Japan Dev", title=title, company=company, url=url, place=location,
                 country="" if worldwide else "Japan", posted=_date(s("published_at") or s("updated_at")),
                 description=("; ".join(facts) + ".\n\n" if facts else "") + text,
                 remote=True if mode == "remote" else None, extra={"tags": names})


@_feed
def fetch_japandev(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Japan Dev (tech jobs in Japan): the posting sitemap, then the ad pages whose slug matches a query."""
    budget = _Budget(BOARDS3_MAX_REQUESTS)
    index = _fetch(http, stats, "japandev", budget, JAPANDEV_INDEX, as_json=False, max_bytes=_SITEMAP_MAX)
    leaf = next((loc for loc in _sitemap_locs(index) if (u := _on(loc, "japan-dev.com")) and "/sitemaps/" in u.path), "")
    posts = japandev_posts(_fetch(http, stats, "japandev", budget, leaf, as_json=False, max_bytes=_SITEMAP_MAX)) \
        if leaf else []
    urls = [url for url, slug in posts if _slug_hit(slug, queries)]
    return _finish(_details(http, stats, "japandev", budget, urls, parse_japandev_ad), queries, days, countries)


FEEDS: dict[str, Callable[..., list[Job]]] = {
    "echojobs": fetch_echojobs,
    "torre": fetch_torre,
    "tecla": fetch_tecla,
    "japandev": fetch_japandev,
}
