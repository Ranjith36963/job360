"""Job-site feeds, set A, for the Job360 hunt kit: remote.com, Djinni, WORK180, WUZZUF, InfoJobs, Instaffo, JobiDanmark.
Also two per-company ATS readers, Deel and Dover, because both only exist per employer board.

Feed contract is the same as ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. A country-gated feed runs ONLY when one of
its countries is in the user's ``countries`` (or "*", the ``--visa-onsite`` value: then it runs everywhere it can and the CORE
keeps only ads that say they sponsor a visa) and sends no request otherwise. remote.com and Djinni are not gated: the first is
a remote-jobs board, the second lists a candidate-residence area per ad that is carried into ``location``. Nothing here ranks
or scores. No logins, no cookies. Standard library only.

Every ad names its own employer; the board name is never put in ``company``. Each site has a small request cap, and the first
answer that is not HTTP 200 ends that site's run (a refused request repeated only holds the refusal up). The container this
was written in could not reach any of these hosts, so every parser is tested on fixtures shaped like the references' data only.

ATS side: ``ATS_FETCHERS`` holds ``deel`` and ``dover`` with the ``fetch(row, http, stats)`` contract of ``kit_ats_more``.

Skipped on purpose (see the integration notes): Gusto, Gradcracker and JobLeads (freehire serves them only to a Chrome TLS
fingerprint, which urllib cannot imitate) and Consider (needs a Set-Cookie session the kit's http helper cannot carry).
Endpoints, params, headers and field names follow the MIT-licensed freehire references named above each reader (see
NOTICE.md); the code is original.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import sys
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit

try:
    import kit_regional as _kr  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_regional as _kr
import kit_xml as _kx  # type: ignore[import-not-found]  # sibling file (this folder is on sys.path by now)

try:
    import kit_ats as _ka  # type: ignore[import-not-found]
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats as _ka

Job = dict[str, Any]
Stats = dict[str, Any]
HttpFn = Callable[..., "tuple[int, Any]"]

_feed: Callable[[Callable[..., list[Job]]], Callable[..., list[Job]]] = _kr._feed
_keep: Callable[..., list[Job]] = _kr._keep
_queries: Callable[[list[str]], list[str]] = _kr._queries
_date: Callable[[Any], str] = _kr._date
_made: Callable[..., Job] = _kr._made
_unique: Callable[[list[Job]], list[Job]] = _kr._unique
_ld_posting: Callable[[str], dict[str, Any]] = _kr._ld_posting
_posting_place: Callable[[dict[str, Any]], tuple[str, str]] = _kr._posting_place
_posting_company: Callable[[dict[str, Any]], str] = _kr._posting_company
strip_html: Callable[[Any], str] = _kr.strip_html
_Budget = _kr._Budget
_LD = _kr._LD

SITE_MAX_REQUESTS = 25  # hard cap per site per run
PAUSE_S = 0.6  # between requests to the HTML sites (freehire djinni.go:33-37: a fast burst is answered 403)
WORK180_PAUSE_S = 5.0  # freehire work180.go:60-67: robots.txt Crawl-delay 5 for every user agent
RDC_PAGES = 5  # remote.com listing pages (20 postings each)
RDC_DETAIL_CAP = 10
DJINNI_PAGES = 3
W180_DETAIL_CAP = 8
WUZZUF_PAGE = 20
WUZZUF_BATCH = 10
INFOJOBS_PAGES = 4
INFOJOBS_DETAIL_CAP = 8
INSTAFFO_DETAIL_CAP = 10
JD_PAGES = 3
JD_DETAIL_CAP = 10
DOVER_DETAIL_CAP = 25
DOVER_PAGES = 3

_WS = re.compile(r"\s+")


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


def _now() -> float:
    return time.monotonic()


def _wanted(countries: list[str], *codes: str) -> list[str]:
    """The given ISO codes the user named (in the user's order), then, for "*", the rest (in the order given).

    The core passes ``countries + ["*"]`` for ``--visa-onsite``, so the user's own markets come first: a fetcher that runs out of
    requests then misses another market, never the user's (same rule as ``kit_asia._wanted``)."""
    have = [c.upper() for c in countries]
    out = [c for c in dict.fromkeys(have) if c in codes]
    if "*" in have:  # --visa-onsite: run everywhere; the core keeps only ads that sponsor a visa
        out += [c for c in codes if c not in out]
    return out


def _hit(text: str, queries: list[str]) -> bool:
    """Plain keyword test (not ranking): any query whose every word occurs in ``text``; no queries = everything."""
    qs = [q.strip().lower() for q in queries or [] if q and q.strip()]
    if not qs:
        return True
    low = text.lower()
    return any(all(w in low for w in q.split()) for q in qs)


_SITEMAP_MAX = 10_000_000  # a sitemap can list tens of thousands of postings; the 2 MB page default would drop it


def _fetch(http: HttpFn, stats: Stats, name: str, budget: Any, url: str, **kw: Any) -> Any:
    """``kit_regional._fetch`` with ``once=True``: the http helper sends the request exactly once. Without it a 429 is retried
    (4 s, 8 s) and a 502/503/504 once, which breaks "the first answer that is not HTTP 200 ends the run" and the WORK180 pacing."""
    kw.setdefault("once", True)
    return _kr._fetch(http, stats, name, budget, url, **kw)


def _pinned(url: Any, host: str, *, scheme_relative: bool = False) -> str:
    """``url`` rebuilt on ``https://<host>`` (path and query only), or "" unless it is https (or, for a ``//host/...`` link,
    scheme-relative) with EXACTLY this host: no other host, no userinfo, no port. A value read from a page never picks the host."""
    raw = str(url or "").strip()
    if scheme_relative:
        if not raw.startswith("//" + host + "/"):
            return ""
        raw = "https:" + raw
    if "\\" in raw or any(c.isspace() for c in raw):
        return ""
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        return ""
    if u.scheme != "https" or (u.hostname or "").lower() != host or u.username is not None or u.password is not None or port is not None:
        return ""
    if not u.path.startswith("/") or u.path.startswith("//"):
        return ""
    return "https://" + host + u.path + ("?" + u.query if u.query else "")


def _list_get(http: HttpFn, stats: Stats, name: str, budget: Any, url: str, **kw: Any) -> Any:
    """A listing/search request: the body on HTTP 200; ANY other answer ends this site's run."""
    body = _fetch(http, stats, name, budget, url, **kw)
    if body is None:
        budget.left = 0
    return body


def _days_ok(job: Job, days: int) -> bool:
    return bool(_keep([job], [], days, search=False))


# ------------------------------------------------------------------ Next.js / RSC flight primitives
# Borrowed from freehire internal/ingest/sources/nextflight.go:117-152 (bracketSlice: the balanced run after a key,
# counting brackets only outside JSON strings), :154-165 (text rows "<id>:T<hexlen>,<bytes>", id and length in lowercase hex,
# content sliced by BYTE length), :80-85 (nextFlightPush regexp) and :86-110 (chunks are JS strings, concatenated).
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,("(?:[^"\\]|\\.)*")\]\)')
_ROW = re.compile(rb"[^0-9a-f]([0-9a-f]+):T([0-9a-f]+),")


def _bracket(s: str, key: str, open_: str, close: str) -> str:
    """The balanced ``open_...close`` text after the first ``key`` ('' when absent); brackets inside strings do not count."""
    at = s.find(key)
    if at < 0:
        return ""
    start = s.find(open_, at)
    if start < 0:
        return ""
    depth, in_str, i = 0, False, start
    while i < len(s):
        c = s[i]
        if in_str:
            if c == "\\":
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == open_:
            depth += 1
        elif c == close:
            depth -= 1
            if depth == 0:
                return s[start:i + 1]
        i += 1
    return ""


def _flight_rows(flight: str) -> dict[str, str]:
    """Text rows of a flight stream keyed by id (content cut by its declared byte length)."""
    raw = flight.encode("utf-8", "replace")
    rows: dict[str, str] = {}
    pos = 0
    while True:
        m = _ROW.search(raw, pos)
        if not m:
            break
        try:
            n = int(m.group(2), 16)
        except ValueError:
            pos = m.end()
            continue
        end = m.end() + n
        if end > len(raw):  # a declared length past the buffer is hostile or cut off: skip the row, never copy the tail
            pos = m.end()
            continue
        rows[m.group(1).decode()] = raw[m.end():end].decode("utf-8", "replace")
        pos = end  # rows never overlap, so the scan (and the copying) stays linear in the body
    return rows


def _decode_flight(page: str) -> str:
    """The flight stream a Next.js HTML page inlines: every self.__next_f.push([1,"..."]) chunk, JS-decoded and joined."""
    out: list[str] = []
    for m in _PUSH.finditer(page or ""):
        try:
            chunk = json.loads(m.group(1))
        except ValueError:
            continue
        if isinstance(chunk, str):
            out.append(chunk)
    return "".join(out)


def _job_posting_node(data: Any) -> dict[str, Any]:
    """The schema.org JobPosting inside a decoded JSON document (object, array or @graph), else {}."""
    nodes = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
    for n in nodes:
        t = n.get("@type") if isinstance(n, dict) else None
        if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
            return dict(n)
    return {}


def _flight_job_posting(flight: str) -> dict[str, Any]:
    """The first JobPosting carried in a flight text row (rows tried in id order), else {} (freehire nextflight.go:59-77)."""
    rows = _flight_rows(flight)
    for rid in sorted(rows):
        try:
            data = json.loads(rows[rid], strict=False)
        except ValueError:
            continue
        node = _job_posting_node(data)
        if node:
            return node
    return {}


def _d(value: Any) -> dict[str, Any]:
    """``value`` when it is a dict, else {} (so nested JSON reads cannot fail on null or a list)."""
    return value if isinstance(value, dict) else {}


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


# --------------------------------------------------------------------------------------- remote.com
# Borrowed from freehire internal/ingest/sources/remotedotcom.go:38 (listing https://remote.com/jobs/all?page=N, 20 a page),
# :41 (posting https://remote.com/jobs/<company slug>/<posting slug>), :53 (header "RSC: 1" makes both answer the flight, not
# HTML), :245-254 (the listing inlines a "jobsData" object; the balanced {} after the key), :66-71 and :90-125 (jobs[]: status,
# slug, title, publishedAt, employmentType, seniority, companyProfile{name,slug}, workplaceLocation{type,city,country{name}},
# hiringLocation{type,timezone{name},includedLocations[{type,value{name}}]}), :281-283 (usable = status "published" + slug + a
# company name), :320-357 (location: a hybrid/on-site desk wins; a remote ad says who may apply: global = "Worldwide", a location
# set = the names, a timezone = "<name> timezone"), :259-270 and :257-270 (the body is a schema.org JobPosting in a flight TEXT
# ROW of the posting page), :388-403 (compensation is in MINOR units; the kit does not use it). freehire internal/ingest/sources/AGENTS.md:263 measured
# ~4,100 of 5,745 postings as employers that run their own ATS: those still show here with remote.com's link.
# NOT IN REFERENCE: a keyword parameter (the listing is read newest pages first and filtered here), and that the listing is
# ordered newest first.
RDC_LIST = "https://remote.com/jobs/all?page=%d"
RDC_JOB = "https://remote.com/jobs/%s/%s"
RDC_HEADERS = {"RSC": "1"}


def _rdc_place(p: dict[str, Any]) -> str:
    w = p.get("workplaceLocation")
    if isinstance(w, dict) and w.get("type") and w.get("type") != "remote":
        country = w.get("country")
        parts = [str(w.get("city") or "").strip(), str(country.get("name") or "").strip() if isinstance(country, dict) else ""]
        text = ", ".join(x for x in parts if x)
        if text:
            return text
    h = p.get("hiringLocation")
    if not isinstance(h, dict):
        return ""
    if h.get("type") == "global":
        return "Worldwide"
    if h.get("type") == "location":
        names: list[str] = []
        for loc in h.get("includedLocations") or []:
            v = loc.get("value") if isinstance(loc, dict) else None
            nm = str(v.get("name") or "").strip() if isinstance(v, dict) else ""
            if nm and nm not in names:
                names.append(nm)
        return ", ".join(names)
    tz = h.get("timezone")
    if h.get("type") == "timezone" and isinstance(tz, dict) and tz.get("name"):
        return f"{tz['name']} timezone"
    return ""


def parse_remotedotcom_listing(flight: Any) -> tuple[list[Job], int, int]:
    """(jobs, totalPages, raw rows on the page) from one listing flight: published postings with an employer, no body yet."""
    raw = _bracket(flight if isinstance(flight, str) else "", '"jobsData":', "{", "}")
    data = _json(raw) if raw else None
    if not isinstance(data, dict):
        return [], 0, 0
    out: list[Job] = []
    rows = data.get("jobs")
    for p in rows if isinstance(rows, list) else []:
        if not isinstance(p, dict):
            continue
        prof = _d(p.get("companyProfile"))
        slug, name = str(p.get("slug") or "").strip(), str(prof.get("name") or "").strip()
        title = _WS.sub(" ", str(p.get("title") or "")).strip()
        if p.get("status") != "published" or not (slug and name and title and prof.get("slug")):
            continue
        w = p.get("workplaceLocation")
        wtype = str(w.get("type") or "") if isinstance(w, dict) else ""
        extra: dict[str, Any] = {}
        if p.get("employmentType"):
            extra["employment_type"] = str(p["employmentType"])
        if isinstance(p.get("seniority"), list) and p["seniority"]:
            extra["seniority"] = [str(x) for x in p["seniority"] if x]
        out.append(_made("remotedotcom", "remote.com", title=title, company=name,
                         url=RDC_JOB % (quote(str(prof["slug"]), safe=""), quote(slug, safe="")), place=_rdc_place(p), country="",
                         posted=_date(str(p.get("publishedAt") or "")), remote=True if wtype == "remote" else None,
                         extra=extra))
    pages = data.get("totalPages")
    return out, pages if isinstance(pages, int) and not isinstance(pages, bool) else 0, len(rows) if isinstance(rows, list) else 0


@_feed
def fetch_remotedotcom(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """remote.com's public board (RSC flight): a few listing pages, then the body of each listed ad the queries match."""
    budget = _Budget(SITE_MAX_REQUESTS)
    qs = _queries(queries)
    seen: list[Job] = []
    for page in range(1, RDC_PAGES + 1):
        flight = _list_get(http, stats, "remotedotcom", budget, RDC_LIST % page, as_json=False, headers=RDC_HEADERS)
        jobs, total, rows = parse_remotedotcom_listing(flight)
        seen += jobs
        if not rows or (total and page >= total):  # the RAW page decides: a page of filtered-out rows is not the end
            break
    seen = _unique(seen)
    out: list[Job] = []
    details = 0
    for j in seen:
        if not (_days_ok(j, days) and _hit(f"{j['title']} {j['company']} {j['location']}", qs)):
            continue
        if details < RDC_DETAIL_CAP and budget.left > 0:
            details += 1
            flight = _fetch(http, stats, "remotedotcom", budget, j["url"], as_json=False, headers=RDC_HEADERS)
            desc = str(_flight_job_posting(flight).get("description") or "") if isinstance(flight, str) else ""
            if strip_html(desc):
                j["description"] = strip_html(desc)
                j["extra"].pop("title_only", None)
        out.append(j)
    return out


# ------------------------------------------------------------------------------------------ Djinni
# Borrowed from freehire internal/ingest/sources/djinni.go:26 (guest listing https://djinni.co/jobs/?page=N), :60-68 and :80-92
# (each page embeds ONE application/ld+json block that is an ARRAY of full schema.org JobPostings, description included, so
# there is no detail request), :37 and :61-67 (pages are paced ~0.6 s: a fast crawl is answered 403), :77-79 (past the end a page
# 302s back to /jobs/, so the same ads reappear: the walk stops when a page adds nothing new), :101-118 (identifier, url, title,
# description (plain text), datePosted (zone-less ISO), employmentType, jobLocationType TELECOMMUTE = remote,
# hiringOrganization.name, applicantLocationRequirements.address.addressCountry / addressRegion), :136-144 (an ad needs an
# identifier, a url and an employer; the applicant area is a country, else a macro-region, absent = worldwide).
# NOT IN REFERENCE: a keyword parameter (the newest pages are read and filtered here), the per-ad countries gate (none is stated).
DJINNI_LIST = "https://djinni.co/jobs/?page=%d"


def parse_djinni_page(page: Any) -> list[Job]:
    """Every usable JobPosting in the page's ld+json blocks (an array in one block, or lone objects)."""
    out: list[Job] = []
    for m in _LD.finditer(page if isinstance(page, str) else ""):
        data = _json(m.group(1).strip())
        if data is None:
            try:
                data = json.loads(m.group(1), strict=False)
            except ValueError:
                continue
        for p in data if isinstance(data, list) else [data]:
            t = p.get("@type") if isinstance(p, dict) else None
            if not (t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t)):
                continue
            org = p.get("hiringOrganization")
            company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
            url, title = _pinned(p.get("url"), "djinni.co"), _WS.sub(" ", str(p.get("title") or "")).strip()
            if not (p.get("identifier") and url and company and title):
                continue
            area = (p.get("applicantLocationRequirements") or {}).get("address") if isinstance(p.get("applicantLocationRequirements"), dict) else None
            area_txt = ""
            if isinstance(area, dict):
                area_txt = str(area.get("addressCountry") or area.get("addressRegion") or "").strip()
            remote = str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"
            out.append(_made("djinni", "Djinni", title=title, company=company, url=url, place=area_txt or ("Worldwide" if remote else ""),
                             country="", posted=_date(str(p.get("datePosted") or "")), description=p.get("description") or "",
                             remote=True if remote else None, extra={"applicant_area": area_txt} if area_txt else None))
    return out


@_feed
def fetch_djinni(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Djinni (CEE IT board): the newest few listing pages, filtered by the user's queries here."""
    budget = _Budget(SITE_MAX_REQUESTS)
    jobs: list[Job] = []
    seen_urls: set[str] = set()
    for page in range(1, DJINNI_PAGES + 1):
        if page > 1:
            _pause(PAUSE_S)
        body = _list_get(http, stats, "djinni", budget, DJINNI_LIST % page, as_json=False)
        fresh = [j for j in parse_djinni_page(body) if j["url"] not in seen_urls]
        if not fresh:
            break
        seen_urls.update(j["url"] for j in fresh)
        jobs += fresh
    return _keep(jobs, queries, days)


# ------------------------------------------------------------------------------------------ WORK180
# Borrowed from freehire internal/ingest/sources/work180.go:49 (origin https://work180.com), :54-58 (boards: au = /en-au,
# uk = /en-gb, us = /en-us; en-au also carries New Zealand, :26-27), :145 (the region's jobs sitemap
# /<locale>/jobs-sitemap.xml names every posting), :135 (posting path /<locale>/for-women/employer/<employer slug>/job/<id>/<slug>),
# :152-181 (only https work180.com URLs under the board's own locale count; id > 0), :162 (newest = highest id first), :60-67
# (robots.txt Crawl-delay 5: posting pages are paced 5 s apart), :186-187 and :217-219 (the first 403/429 ends the run's page
# reads), :311-355 (the page's schema.org JobPosting: identifier.value must equal the sitemap id, title, description, datePosted,
# hiringOrganization.name, jobLocation[].address.name, jobLocationType TELECOMMUTE, employmentType), :323-325 and :301-309 (the
# page's RSC record under "job":{"id":"<id>"} says expired / applicationType EXTERNAL + applicationLink = the employer's own ATS
# page, kept as extra.apply_url only: the job url stays on work180.com), :376-411 (the record is found in the joined self.__next_f.push chunks).
# freehire work180_test.go fixtures: testdata/work180_detail_au.html, work180_jobs_sitemap_au.xml.
# NOT IN REFERENCE: nothing else; the sitemap states no employer NAME, so the title/employer slug in the URL is what the user's
# queries are matched against before a page is spent, and the employer name comes from the page.
W180_BASE = "https://work180.com"
W180_BOARDS = {  # ISO country -> board locale
    "AU": "en-au", "NZ": "en-au", "GB": "en-gb", "US": "en-us",
}
_W180_PATH = re.compile(r"^/(en-[a-z]{2})/for-women/employer/([^/]+)/job/([0-9]+)(?:/([^/]*))?/?$")
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


def parse_work180_sitemap(xml: Any, locale: str) -> list[dict[str, Any]]:
    """Posting entries (id, url, employer slug, title slug) of one region's jobs sitemap, newest id first."""
    out: dict[int, dict[str, Any]] = {}
    for m in _LOC.finditer(xml if isinstance(xml, str) else ""):
        u = urlsplit(html.unescape(m.group(1)))
        if u.scheme != "https" or (u.hostname or "").lower() != "work180.com":
            continue
        pm = _W180_PATH.match(u.path)
        if not pm or pm.group(1) != locale:
            continue
        pid = int(pm.group(3))
        if pid > 0 and pid not in out:
            out[pid] = {"id": pid, "url": W180_BASE + u.path, "employer": pm.group(2), "slug": pm.group(4) or ""}
    return [out[k] for k in sorted(out, reverse=True)]


def _w180_record(page: str, pid: str) -> dict[str, Any]:
    """The posting's own record from the page's RSC payload: the object after ``"job":`` whose id is this posting's."""
    flight = _decode_flight(page)
    marker = '"job":{"id":' + json.dumps(pid)
    i = flight.find(marker)
    if i < 0:
        return {}
    try:
        rec, _ = json.JSONDecoder().raw_decode(flight[i + len('"job":'):])
    except ValueError:
        return {}
    return rec if isinstance(rec, dict) and str(rec.get("id")) == pid else {}


def _plain_https(link: str) -> bool:
    """True for an https URL with a host and no userinfo, port or whitespace (the employer's own page, not a trick URL)."""
    if not re.match(r"https://[^\s]+$", link):
        return False
    try:
        u = urlsplit(link)
        return bool(u.hostname) and "@" not in u.netloc and u.port is None
    except ValueError:
        return False


def parse_work180_posting(page: Any, entry: dict[str, Any]) -> Job | None:
    """One WORK180 posting page to a Job, or None (no JobPosting for this id, expired, no employer/title/body)."""
    text = page if isinstance(page, str) else ""
    p = _ld_posting(text)
    pid = str(entry["id"])
    ident = p.get("identifier")
    if not p or not isinstance(ident, dict) or str(ident.get("value") or "").strip() != pid:
        return None
    rec = _w180_record(text, pid)
    if rec.get("expired") not in (None, ""):
        return None
    emp = rec.get("employer")
    company = _posting_company(p) or (str(emp.get("name") or "").strip() if isinstance(emp, dict) else "")
    title = _WS.sub(" ", str(p.get("title") or "")).strip()
    desc = p.get("description") or ""
    if not (company and title and strip_html(desc)):
        return None
    url = entry["url"]  # always the work180.com link (checked against the sitemap): a job URL never leaves the board's host
    link = str(rec.get("applicationLink") or "").strip()
    extra = {"listed_on": entry["url"]}
    if str(rec.get("applicationType") or "").upper() == "EXTERNAL" and _plain_https(link):
        extra["apply_url"] = link  # the employer's own page, carried as data only; an EMAIL application names a mail relay
    places: list[str] = []
    locs = p.get("jobLocation")
    for loc in locs if isinstance(locs, list) else [locs]:
        a = loc.get("address") if isinstance(loc, dict) else None
        if isinstance(a, dict):
            nm = str(a.get("name") or "").strip() or ", ".join(x for x in (str(a.get("addressLocality") or "").strip(), str(a.get("addressRegion") or "").strip()) if x)
            if nm and nm not in places:
                places.append(nm)
    remote = str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"
    return _made("work180", "WORK180", title=title, company=company, url=url, place="; ".join(places), country="",
                 posted=_date(str(p.get("datePosted") or "")), description=desc, remote=True if remote else None,
                 extra=extra)


@_feed
def fetch_work180(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """WORK180 (employers endorsed for supporting women): the region's jobs sitemap, then a few posting pages (paced 5 s)."""
    locales = list(dict.fromkeys(W180_BOARDS[c] for c in _wanted(countries, *W180_BOARDS)))
    if not locales:
        return []
    qs = _queries(queries)
    budget = _Budget(SITE_MAX_REQUESTS)
    jobs: list[Job] = []
    last: list[float] = []  # monotonic time the last work180.com request was sent

    def paced(url: str, **kw: Any) -> tuple[int, Any]:
        """EVERY request to work180.com (sitemaps included, and the first one of a second region) waits until the
        robots.txt Crawl-delay has passed since the previous one."""
        if last:
            gap = WORK180_PAUSE_S - (_now() - last[0])
            if gap > 0:
                _pause(gap)
        last[:] = [_now()]  # request starts are what the Crawl-delay spaces
        return http(url, **kw)

    for locale in locales:
        if budget.left <= 0:
            break
        xml = _list_get(paced, stats, "work180", budget, f"{W180_BASE}/{locale}/jobs-sitemap.xml", as_json=False,
                        max_bytes=_SITEMAP_MAX)
        spent = 0
        for entry in parse_work180_sitemap(xml, locale):
            if spent >= W180_DETAIL_CAP or budget.left <= 0:
                break
            if not _hit((entry["slug"] + " " + entry["employer"]).replace("-", " "), qs):
                continue
            spent += 1
            job = parse_work180_posting(_fetch(paced, stats, "work180", budget, entry["url"], as_json=False), entry)
            if job:
                jobs.append(job)
    return _keep(_unique(jobs), queries, days)


# ------------------------------------------------------------------------------------------ WUZZUF
# Borrowed from freehire internal/ingest/sources/wuzzuf.go:41-42 (search POST https://api.wuzzuf.net/api/search/job, job
# documents GET https://api.wuzzuf.net/api/job?filter[other][ids]=<id,id,...>&page[size]=n), :43 (public page = https://wuzzuf.net/
# + uri), :95-102 (search body startIndex, pageSize, longitude "0", latitude "0", query, searchFilters; the API refuses a body
# without longitude/latitude), :111-131 (a search row is an id plus computedFields[company_name]; an empty company = the employer
# hid it, such a row is dropped), :142-181 (document: attributes status, title, description, requirements, uri, postedAt,
# location{country{name,code},city,area}, workplaceArrangement.displayedName, workExperienceYears.min, workTypes), :52 and
# :286-300 (10 ids a request), :314-364 (status must be "active"; uri path shape; description + "Requirements"; place = area, city,
# country), :376-380 (workplace On-site/Hybrid/Remote), :368-374 (postedAt is "MM/DD/YYYY HH:MM:SS" Cairo time). freehire internal/ingest/sources/AGENTS.md:116-117
# (wuzzuf.net sits behind a Cloudflare challenge, api.wuzzuf.net does not; the API is strict JSON:API: Accept and Content-Type
# must be application/vnd.api+json) and :123 (385 named IT postings: 367 Egypt, 5 Saudi Arabia, 2 UAE and a few elsewhere).
# NOT IN REFERENCE: that a non-empty ``query`` is honoured (freehire always sends "" and filters by a role), so no role filter is
# sent here; the posting date is kept as the Cairo calendar date.
WUZZUF_SEARCH = "https://api.wuzzuf.net/api/search/job"
WUZZUF_JOBS = "https://api.wuzzuf.net/api/job"
WUZZUF_SITE = "https://wuzzuf.net/"
WUZZUF_HEADERS = {"Accept": "application/vnd.api+json", "Content-Type": "application/vnd.api+json"}
WUZZUF_COUNTRIES = ("EG", "SA", "AE")
_WUZZUF_ID = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")
_WUZZUF_URI = re.compile(r"^(?:[a-z]+/)+[a-z0-9]+(?:-[a-z0-9]+)*$")
_WUZZUF_MODES = {"On-site": False, "Hybrid": None, "Remote": True}


def parse_wuzzuf_search(payload: Any) -> list[tuple[str, str]]:
    """(posting id, employer) per search row; rows with an unreadable id or a hidden employer are dropped."""
    out: list[tuple[str, str]] = []
    for r in (payload.get("data") if isinstance(payload, dict) else None) or []:
        if not isinstance(r, dict) or not _WUZZUF_ID.match(str(r.get("id") or "")):
            continue
        company = ""
        for f in ((r.get("attributes") or {}).get("computedFields") or []) if isinstance(r.get("attributes"), dict) else []:
            if isinstance(f, dict) and f.get("name") == "company_name" and isinstance(f.get("value"), list) and f["value"]:
                company = _WS.sub(" ", str(f["value"][0] or "")).strip()
        if company:
            out.append((str(r["id"]), company))
    return out


def parse_wuzzuf_docs(payload: Any, employers: dict[str, str], allowed: set[str] | None) -> list[Job]:
    """Job documents to Jobs: active, a valid page path, an employer from the search, and (when ``allowed``) a wanted country."""
    out: list[Job] = []
    for d in (payload.get("data") if isinstance(payload, dict) else None) or []:
        a = d.get("attributes") if isinstance(d, dict) else None
        if not isinstance(a, dict) or str(d.get("id")) not in employers:
            continue
        title, uri = _WS.sub(" ", str(a.get("title") or "")).strip(), str(a.get("uri") or "")
        if a.get("status") != "active" or not title or not _WUZZUF_URI.match(uri):
            continue
        loc = _d(a.get("location"))
        country = _d(loc.get("country"))
        code = str(country.get("code") or "").strip().upper()
        if allowed is not None and code not in allowed:
            continue
        parts = [str(_d(loc.get(k)).get("name") or "").strip() for k in ("area", "city")]
        place = ", ".join(dict.fromkeys(x for x in parts if x))
        body = str(a.get("description") or "")
        if str(a.get("requirements") or "").strip():
            body += "<h3>Requirements</h3>" + str(a["requirements"])
        m = re.match(r"(\d{2})/(\d{2})/(\d{4})", str(a.get("postedAt") or ""))
        wa = a.get("workplaceArrangement")
        mode = str(wa.get("displayedName") or "") if isinstance(wa, dict) else ""
        extra: dict[str, Any] = {"country_code": code}
        yrs = (a.get("workExperienceYears") or {}).get("min") if isinstance(a.get("workExperienceYears"), dict) else None
        if isinstance(yrs, int) and not isinstance(yrs, bool) and yrs > 0:
            extra["years_min"] = yrs
        out.append(_made("wuzzuf", "WUZZUF", title=title, company=employers[str(d["id"])], url=WUZZUF_SITE + uri, place=place,
                         country=str(country.get("name") or "").strip(), posted=f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else "",
                         description=body, remote=_WUZZUF_MODES.get(mode), extra=extra))
    return out


@_feed
def fetch_wuzzuf(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """WUZZUF (Egypt, some Saudi Arabia and UAE) through api.wuzzuf.net: one search per query, then the job documents."""
    wanted = _wanted(countries, *WUZZUF_COUNTRIES)
    if not wanted:
        return []
    allowed = None if "*" in {c.upper() for c in countries} else set(wanted)
    budget = _Budget(SITE_MAX_REQUESTS)
    jobs: list[Job] = []
    for q in _queries(queries):
        body = json.dumps({"startIndex": 0, "pageSize": WUZZUF_PAGE, "longitude": "0", "latitude": "0", "query": q,
                           "searchFilters": {}}).encode()
        hits = parse_wuzzuf_search(_list_get(http, stats, "wuzzuf", budget, WUZZUF_SEARCH, method="POST", data=body, headers=WUZZUF_HEADERS))
        for lo in range(0, len(hits), WUZZUF_BATCH):
            batch = hits[lo:lo + WUZZUF_BATCH]
            url = WUZZUF_JOBS + "?" + urlencode({"filter[other][ids]": ",".join(h[0] for h in batch), "page[size]": len(batch)})
            docs = _list_get(http, stats, "wuzzuf", budget, url, headers={"Accept": WUZZUF_HEADERS["Accept"]})
            jobs += parse_wuzzuf_docs(docs, dict(batch), allowed)
    return _keep(_unique(jobs), queries, days)  # NOT IN REFERENCE that the endpoint honours `query`: filter locally


# ----------------------------------------------------------------------------------------- InfoJobs
# Borrowed from freehire internal/ingest/sources/infojobs.go:26-27 (IT category listing
# https://www.infojobs.net/ofertas-trabajo/informatica-telecomunicaciones?page=N), :168-191 (the page embeds
# window.__INITIAL_PROPS__ = JSON.parse("<JSON string literal>"): the literal is DOUBLE encoded, decode the string, then the
# document), :42-63 (props.offers[]: code, title, companyName, link, city, publishedAt, teleworking, description = a truncated
# teaser; props.overview.totalElements), :144-146 (stop on an empty page or when totalElements is reached), :151-166 and :67-71 (the
# full body is props.offer.description of the offer's detail page), :193-205 (teleworking "Solo teletrabajo" = remote, "Híbrido",
# "Presencial"), :207-226 (an offer needs code, link and company; the URL is "https:" + link without its ?tracking query).
# NOT IN REFERENCE: any category other than IT, a keyword parameter, and whether the site answers a script without a challenge
# (the reference's client is a plain HTTP client, so none is claimed). Offers are matched to the queries here.
INFOJOBS_LIST = "https://www.infojobs.net/ofertas-trabajo/informatica-telecomunicaciones?page=%d"
IJ_HOST = "www.infojobs.net"
_IJ_MARK = re.compile(r"window\.__INITIAL_PROPS__\s*=\s*JSON\.parse\(")


def _infojobs_props(page: Any) -> dict[str, Any]:
    """The decoded __INITIAL_PROPS__ document of a page ({} when missing or unreadable)."""
    if not isinstance(page, str):
        return {}
    m = _IJ_MARK.search(page)
    if not m:
        return {}
    try:
        inner, _ = json.JSONDecoder().raw_decode(page[m.end():])
        doc = json.loads(inner) if isinstance(inner, str) else None
    except ValueError:
        return {}
    return doc if isinstance(doc, dict) else {}


def parse_infojobs_list(page: Any) -> tuple[list[Job], int, int]:
    """(jobs, overview.totalElements, rows on the page); the description is the teaser and the job is marked truncated."""
    props = _infojobs_props(page)
    out: list[Job] = []
    for o in props.get("offers") or []:
        if not isinstance(o, dict):
            continue
        code, link, company = str(o.get("code") or "").strip(), str(o.get("link") or "").strip(), str(o.get("companyName") or "").strip()
        title = _WS.sub(" ", str(o.get("title") or "")).strip()
        if not (code and link and company and title):
            continue
        tele = str(o.get("teleworking") or "").strip()
        url = _pinned(re.split(r"[?#]", link, maxsplit=1)[0], IJ_HOST, scheme_relative=True)
        if not url:  # only a //www.infojobs.net/... link: an off-host link is never emitted nor fetched
            continue
        out.append(_made("infojobs", "InfoJobs", title=title, company=company, url=url,
                         place=str(o.get("city") or "").strip(), country="Spain", posted=_date(str(o.get("publishedAt") or "")),
                         description=o.get("description"), remote=True if tele == "Solo teletrabajo" else None,
                         extra={"truncated": True, "offer_code": code} | ({"workplace": tele} if tele else {})))
    ov = props.get("overview")
    total = ov.get("totalElements") if isinstance(ov, dict) else 0
    rows = props.get("offers")
    return out, total if isinstance(total, int) and not isinstance(total, bool) else 0, len(rows) if isinstance(rows, list) else 0


@_feed
def fetch_infojobs(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """InfoJobs (Spain), IT category only, only when ES is one of the user's countries; full text for the matching offers."""
    if not _wanted(countries, "ES"):
        return []
    budget = _Budget(SITE_MAX_REQUESTS)
    jobs: list[Job] = []
    read = 0
    for page in range(1, INFOJOBS_PAGES + 1):
        if page > 1:
            _pause(PAUSE_S)
        got, total, rows = parse_infojobs_list(_list_get(http, stats, "infojobs", budget, INFOJOBS_LIST % page, as_json=False))
        jobs += got
        read += rows
        if not rows or (total and read >= total):
            break
    kept = _keep(_unique(jobs), queries, days)
    for j in kept[:INFOJOBS_DETAIL_CAP]:
        if budget.left <= 0:
            break
        _pause(PAUSE_S)
        offer = _infojobs_props(_fetch(http, stats, "infojobs", budget, j["url"], as_json=False)).get("offer")
        full = strip_html(offer.get("description")) if isinstance(offer, dict) else ""
        if full:
            j["description"] = full
            j["extra"].pop("truncated", None)
            j["extra"].pop("title_only", None)
    return kept


# ------------------------------------------------------------------------------------------ Instaffo
# Borrowed from freehire internal/ingest/sources/instaffo.go:42-44 (sitemap index https://jobs.instaffo.com/sitemap.xml, the
# sub-sitemap whose URL contains "sitemap-jobs"; a bare "jobs" matches every sub-sitemap), :117-120 (a posting is a canonical
# /de/job/<slug> URL whose slug ends in 12 hex digits; the /en/ twin of each ad is dropped), :65-96 (detail mapping: the page
# loc is the URL, an employer is required, description is html-unescaped), :105-115 (each page carries a schema.org JobPosting
# ld+json: title, description, datePosted, jobLocation, hiringOrganization.name), :75-82 and :98-103 (Instaffo never retires an
# ad from its sitemap, so an ad older than 365 days is dropped).
# NOT IN REFERENCE: what the slug contains besides the hex id (the slug words are matched to the queries before a page is
# spent, so an ad whose slug does not name the role is missed), the order of the sitemap (newest <lastmod> first is used when the
# sitemap states one) and whether the German-only gate (DE) is the whole market.
INSTAFFO_HOST = "jobs.instaffo.com"
INSTAFFO_INDEX = "https://jobs.instaffo.com/sitemap.xml"
_INSTAFFO_ID = re.compile(r"/de/job/([^/?#]+-[0-9a-f]{12})$")


def parse_sitemap(xml: Any) -> list[tuple[str, str]]:
    """(loc, lastmod) of every <url>/<sitemap> entry (parsed with kit_xml.safe_fromstring: linear, no DOCTYPE/entities)."""
    root = _kx.safe_fromstring(xml) if isinstance(xml, (str, bytes)) else None
    out: list[tuple[str, str]] = []
    if root is None:
        return out
    for el in root.iter():
        if str(el.tag).rsplit("}", 1)[-1].lower() not in ("url", "sitemap"):
            continue
        loc = lm = ""
        for child in el:
            tag = str(child.tag).rsplit("}", 1)[-1].lower()
            if tag == "loc" and not loc:
                loc = (child.text or "").strip()
            elif tag == "lastmod" and not lm:
                lm = (child.text or "").strip()
        if loc:
            out.append((loc, lm))
    return out


def parse_instaffo_posting(page: Any, loc: str, days_max: int = 365) -> Job | None:
    """One Instaffo job page to a Job; None without a JobPosting, an employer, a datePosted or when it is over a year old."""
    p = _ld_posting(page if isinstance(page, str) else "")
    company = _posting_company(p)
    posted = _date(str(p.get("datePosted") or ""))
    if not (p and company and posted and _days_ok({"title": "x", "url": "x", "posted": posted}, days_max)):
        return None
    place, country = _posting_place(p)
    return _made("instaffo", "Instaffo", title=_WS.sub(" ", str(p.get("title") or "")).strip(), company=company, url=loc, place=place,
                 country=country, posted=posted, description=html.unescape(str(p.get("description") or "")))


@_feed
def fetch_instaffo(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """Instaffo (Germany), only when DE is one of the user's countries: job sitemap, then the pages whose slug names a wanted role."""
    if not _wanted(countries, "DE"):
        return []
    budget = _Budget(SITE_MAX_REQUESTS)
    index = _list_get(http, stats, "instaffo", budget, INSTAFFO_INDEX, as_json=False, max_bytes=_SITEMAP_MAX)
    # the sub-sitemap and every job loc are pinned to https://jobs.instaffo.com (rebuilt from host + path): a value read from
    # the sitemap never picks the host that is fetched or emitted
    sub = next((u for u in (_pinned(loc, INSTAFFO_HOST) for loc, _ in parse_sitemap(index)) if u and "sitemap-jobs" in urlsplit(u).path), "")
    if not sub:
        return []
    entries = [(pu, lm) for pu, lm in ((_pinned(loc, INSTAFFO_HOST), lm) for loc, lm in
                                       parse_sitemap(_list_get(http, stats, "instaffo", budget, sub, as_json=False,
                                                  max_bytes=_SITEMAP_MAX)))
               if pu and _INSTAFFO_ID.search(pu)]
    entries.sort(key=lambda e: e[1], reverse=True)
    qs = _queries(queries)
    jobs: list[Job] = []
    for loc, _ in entries:
        if len(jobs) >= INSTAFFO_DETAIL_CAP or budget.left <= 0:
            break
        m = _INSTAFFO_ID.search(loc)
        if not m or not _hit(re.sub(r"-[0-9a-f]{12}$", "", m.group(1)).replace("-", " "), qs):
            continue
        _pause(PAUSE_S)
        job = parse_instaffo_posting(_fetch(http, stats, "instaffo", budget, loc, as_json=False), loc)
        if job:
            jobs.append(job)
    return _keep(_unique(jobs), queries, days)


# ---------------------------------------------------------------------------------------- JobiDanmark
# Borrowed from freehire internal/ingest/sources/jobdanmark.go:30 and :33 (POST https://jobdanmark.dk/api/jobsearch/search/<page>,
# page 1-based), :51-58 (body jobTypes [], filters [], locationMode "Text", distance 50: the unfiltered catalogue, freshest first),
# :60-75 (response items[] title, companyName, companyAddress, url (site-relative /job/<slug>), publishedDate DD-MM-YYYY; totalPages),
# :103-109 (stop on an empty page or at totalPages), :121-135 (an item needs title, employer and url; query and fragment are
# stripped from the url), :154-173 (the detail page's schema.org JobPosting gives the description and a date-only datePosted),
# :135 (the list date wins only when the page has none). NOT IN REFERENCE: a keyword filter in ``filters`` (the newest pages are
# read and matched here on title and employer), and the Content-Type header (freehire's PostJSON client sends JSON).
JD_HOST = "jobdanmark.dk"
JD_BASE = "https://jobdanmark.dk"
JD_SEARCH = "https://jobdanmark.dk/api/jobsearch/search/%d"
_DDMMYYYY = re.compile(r"^(\d{2})-(\d{2})-(\d{4})$")


def parse_jobdanmark_items(payload: Any) -> tuple[list[dict[str, str]], int, int]:
    """(items with title/company/address/url/posted, totalPages, raw rows on the page) of one search page.

    Only a site-relative path ("/job/<slug>") is accepted and rebuilt on https://jobdanmark.dk; any absolute URL or other form
    ("//host/x", ".evil.net/x", "@evil.net/x") is dropped."""
    out: list[dict[str, str]] = []
    rows = payload.get("items") if isinstance(payload, dict) else None
    for it in rows if isinstance(rows, list) else []:
        if not isinstance(it, dict):
            continue
        path = re.split(r"[?#]", str(it.get("url") or "").strip(), maxsplit=1)[0].rstrip("/")
        title, company = _WS.sub(" ", str(it.get("title") or "")).strip(), str(it.get("companyName") or "").strip()
        if not (title and company and path.startswith("/") and not path.startswith("//")):
            continue
        url = _pinned(JD_BASE + path, JD_HOST)
        if not url:
            continue
        m = _DDMMYYYY.match(str(it.get("publishedDate") or "").strip())
        out.append({"title": title, "company": company, "address": str(it.get("companyAddress") or "").strip(),
                    "url": url, "posted": f"{m.group(3)}-{m.group(2)}-{m.group(1)}" if m else ""})
    tp = payload.get("totalPages") if isinstance(payload, dict) else 0
    return out, tp if isinstance(tp, int) and not isinstance(tp, bool) else 0, len(rows) if isinstance(rows, list) else 0


@_feed
def fetch_jobdanmark(queries: list[str], http: HttpFn, stats: Stats, *, days: int, countries: list[str]) -> list[Job]:
    """JobiDanmark (Denmark), only when DK is one of the user's countries: newest search pages, detail page for the matches."""
    if not _wanted(countries, "DK"):
        return []
    budget = _Budget(SITE_MAX_REQUESTS)
    qs = _queries(queries)
    body = json.dumps({"jobTypes": [], "filters": [], "locationMode": "Text", "distance": 50}).encode()
    items: list[dict[str, str]] = []
    for page in range(1, JD_PAGES + 1):
        got, total, rows = parse_jobdanmark_items(_list_get(http, stats, "jobdanmark", budget, JD_SEARCH % page, method="POST", data=body,
                                                      headers={"Content-Type": "application/json"}))
        items += got
        if not rows or (total and page >= total):  # the RAW page decides, not the kept items
            break
    jobs: list[Job] = []
    details = 0
    for it in items:
        if not _hit(f"{it['title']} {it['company']}", qs):
            continue
        desc, posted = "", it["posted"]
        if details < JD_DETAIL_CAP and budget.left > 0:
            details += 1
            _pause(PAUSE_S)
            p = _ld_posting(str(_fetch(http, stats, "jobdanmark", budget, it["url"], as_json=False) or ""))
            desc = str(p.get("description") or "")
            posted = _date(str(p.get("datePosted") or "")) or posted
        jobs.append(_made("jobdanmark", "JobiDanmark", title=it["title"], company=it["company"], url=it["url"], place=it["address"],
                          country="Denmark", posted=posted, description=desc))
    return _keep(_unique(jobs), queries, days, search=False)


# ---------------------------------------------------------------------------------------------- ATS
_JSON_HDR = {"Accept": "application/json"}
_HTML_HDR = {"Accept": "text/html"}
_SLUG_OK = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,100}$")


# Deel. Borrowed from freehire internal/ingest/sources/deel.go:23 (board page https://jobs.deel.com/<orgSlug>), :35-44 (one GET
# carries the org's whole catalogue in the Next.js flight; `"jobPostings":` is a JSON array; org name and text rows are read from
# the same stream), :72-84 (posting: id, title, richtextDescription = "$N" reference to flight text row N or inline HTML,
# createdAt, job.jobLocations[].location.name), :93-107 (`"careerPageSettings":` object, preferredOrganizationName), :26 (public
# page https://jobs.deel.com/<slug>/job-details/<id>/overview), :113-135 (toJob: company = the org name, else the configured one;
# remote from title + location text). NOT IN REFERENCE: nothing else.
def _deel(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Deel ATS board (token = org slug from jobs.deel.com/<slug>): the flight stream carries every posting."""
    slug = str(row.get("token") or "").strip()
    if not _SLUG_OK.match(slug):
        return None
    st, page = _ka._get(http, f"https://jobs.deel.com/{quote(slug, safe='')}", as_json=False, headers=_HTML_HDR)
    if st != 200 or not isinstance(page, str):
        return None
    flight = _decode_flight(page)
    arr = _json(_bracket(flight, '"jobPostings":', "[", "]") or "null")
    if not isinstance(arr, list):
        return None
    settings = _json(_bracket(flight, '"careerPageSettings":', "{", "}") or "null")
    org = str(settings.get("preferredOrganizationName") or "").strip() if isinstance(settings, dict) else ""
    rows = _flight_rows(flight)
    jobs: list[Job] = []
    for p in arr:
        pid = str(p.get("id") or "") if isinstance(p, dict) else ""
        title = _WS.sub(" ", str(p.get("title") or "")).strip() if pid else ""
        if not (pid and title):
            continue
        desc = str(p.get("richtextDescription") or "")
        if desc.startswith("$"):
            desc = rows.get(desc[1:], "")
        job_o = p.get("job")
        places = [str(((jl or {}).get("location") or {}).get("name") or "").strip()
                  for jl in (job_o.get("jobLocations") or [] if isinstance(job_o, dict) else []) if isinstance(jl, dict)]
        place = ", ".join(dict.fromkeys(x for x in places if x))
        job = _ka._job(row, "deel", title=title, url=f"https://jobs.deel.com/{quote(slug, safe='')}/job-details/{quote(pid, safe='')}/overview",
                       location=place, posted=_ka._iso_date(p.get("createdAt")), description=strip_html(desc),
                       remote=_ka._remote_from(title, place))
        job["company"] = org or job["company"]
        jobs.append(job)
    return jobs


# Dover. Borrowed from freehire internal/ingest/sources/dover.go:15 (API root https://app.dover.com/api/v1), :19 (page size 300),
# :145-158 (GET /careers-page/<client id>/jobs?limit=300&offset=0 -> {next, results[]}, follow the response's own "next" URL; keep
# is_published and not is_sample), :98-100 (GET /careers-page-slug/<slug> answers {"id": client id}), :161-165 (apply URL
# https://app.dover.com/apply/<slug>/<job id>), :210-221 (results[]/detail fields: id, title, locations[], workplace_type, active,
# is_private, visa_support), :113-121 (locations[].location_option.display_name / location_type) and :187-193 (COUNTRY entries carry
# the country), :210-227 (detail GET /inbound/application-portal-job/<id>: user_provided_description, compensation.employment_type),
# :87 (a detail that is inactive or private drops the ad), :280-298 (equity / visa notes are added to the description; company =
# the board's company), pacer.go:409-411 (~2 requests a second, so the kit pauses 0.5 s). Not used: compensation (shown only when
# open_to_sharing_comp; the kit has no pay field).
# NOT IN REFERENCE: nothing else.
DOVER_PAUSE_S = 0.5


def _dover_places(locs: Any) -> str:
    names: list[str] = []
    for loc in locs if isinstance(locs, list) else []:
        opt = loc.get("location_option") if isinstance(loc, dict) else None
        nm = str(opt.get("display_name") or "").strip() if isinstance(opt, dict) else ""
        if nm and nm not in names:
            names.append(nm)
    return ", ".join(names)


def _dover(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Dover career page (token = slug from app.dover.com/jobs/<slug>): resolve the client id, list, then detail (capped)."""
    slug = str(row.get("token") or "").strip()
    if not _SLUG_OK.match(slug):
        return None
    base = "https://app.dover.com/api/v1"
    st, res = _ka._get(http, f"{base}/careers-page-slug/{quote(slug, safe='')}", headers=_JSON_HDR)
    cid = str(res.get("id") or "") if st == 200 and isinstance(res, dict) else ""
    if not _SLUG_OK.match(cid):
        return None
    listed: list[dict[str, Any]] = []
    nxt = f"{base}/careers-page/{quote(cid, safe='')}/jobs?limit=300&offset=0"
    for page in range(DOVER_PAGES):
        if page:
            _pause(DOVER_PAUSE_S)
        st, res = _ka._get(http, nxt, headers=_JSON_HDR)
        if st != 200 or not isinstance(res, dict) or not isinstance(res.get("results"), list):
            if page:
                break  # a hiccup after the first page keeps what we have
            return None
        listed += [r for r in res["results"] if isinstance(r, dict) and r.get("is_published") and not r.get("is_sample") and r.get("id")]
        nxt = str(res.get("next") or "")
        if not nxt.startswith("https://app.dover.com/"):
            break
    jobs: list[Job] = []
    details = 0
    for r in listed:
        title, jid = _WS.sub(" ", str(r.get("title") or "")).strip(), str(r["id"])
        if not title:
            continue
        mode = str(r.get("workplace_type") or "").strip().lower()
        place, desc, remote = _dover_places(r.get("locations")), "", True if mode == "remote" else None
        if details < DOVER_DETAIL_CAP and _ka._title_ok(row, title):
            details += 1
            _pause(DOVER_PAUSE_S)
            dst, d = _ka._get(http, f"{base}/inbound/application-portal-job/{quote(jid, safe='')}", headers=_JSON_HDR)
            if dst == 200 and isinstance(d, dict):
                if d.get("active") is False or d.get("is_private") is True:
                    continue  # closed or private: not an open ad
                desc = strip_html(d.get("user_provided_description"))
                place = _dover_places(d.get("locations")) or place
                notes = []
                comp = _d(d.get("compensation"))
                if comp.get("offers_equity"):
                    notes.append("This role offers equity compensation.")
                if d.get("visa_support"):
                    notes.append("The employer sponsors a work visa for this role.")
                desc = "\n\n".join(x for x in [desc, *notes] if x)
        jobs.append(_ka._job(row, "dover", title=title, url=f"https://app.dover.com/apply/{quote(slug, safe='')}/{quote(jid, safe='')}",
                             location=place, description=desc, remote=remote))
    return jobs


Http = Callable[..., "tuple[int, Any]"]


def _wrap_ats(body: Callable[[dict[str, Any], Http], list[Job] | None]) -> Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]:
    """Turn ``body(row, http) -> jobs | None`` (None = the board failed) into a never-raising fetcher that counts."""
    def fetch(row: dict[str, Any], http: Http, stats: dict[str, Any]) -> list[Job]:
        try:
            jobs = body(row, http)
        except Exception:  # noqa: BLE001 - fetchers must never raise
            jobs = None
        _ka._count(stats, ok=jobs is not None, jobs=len(jobs or []))
        return jobs or []
    fetch.__doc__ = body.__doc__
    return fetch


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "deel": _wrap_ats(_deel),
    "dover": _wrap_ats(_dover),
}

FEEDS: dict[str, Callable[..., list[Job]]] = {
    "remotedotcom": fetch_remotedotcom,
    "djinni": fetch_djinni,
    "work180": fetch_work180,
    "wuzzuf": fetch_wuzzuf,
    "infojobs": fetch_infojobs,
    "instaffo": fetch_instaffo,
    "jobdanmark": fetch_jobdanmark,
}
