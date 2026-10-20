"""Job-site feeds, set A (part 1 of 3), for the Job360 hunt kit: Djinni, WUZZUF, InfoJobs and Instaffo.
JobiDanmark, remote.com, WORK180 and two per-company ATS readers (Deel and Dover) follow in the next parts.

Feed contract is the same as ``kit_regional.FEEDS``: ``fetch_x(queries, http, stats, *, days, countries) -> list[Job]``,
never raises, networking only through the ``http`` callable the core passes in. A country-gated feed runs ONLY when one of
its countries is in the user's ``countries`` (or "*", the ``--visa-onsite`` value: then it runs everywhere it can and the CORE
keeps only ads that say they sponsor a visa) and sends no request otherwise. Djinni is not gated: it lists a candidate-residence area per ad that is carried into ``location``. Nothing here ranks
or scores. No logins, no cookies. Standard library only.

Every ad names its own employer; the board name is never put in ``company``. Each site has a small request cap, and the first
answer that is not HTTP 200 ends that site's run (a refused request repeated only holds the refusal up). The container this
was written in could not reach any of these hosts, so every parser is tested on fixtures shaped like the references' data only.

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
from urllib.parse import urlencode, urlsplit

try:
    import kit_regional as _kr  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_regional as _kr
import kit_xml as _kx  # type: ignore[import-not-found]  # sibling file (this folder is on sys.path by now)

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
DJINNI_PAGES = 3
WUZZUF_PAGE = 20
WUZZUF_BATCH = 10
INFOJOBS_PAGES = 4
INFOJOBS_DETAIL_CAP = 8
INSTAFFO_DETAIL_CAP = 10

_WS = re.compile(r"\s+")


def _pause(seconds: float) -> None:
    if seconds > 0:
        time.sleep(seconds)


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


def _d(value: Any) -> dict[str, Any]:
    """``value`` when it is a dict, else {} (so nested JSON reads cannot fail on null or a list)."""
    return value if isinstance(value, dict) else {}


def _json(raw: str) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None


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
_LOC = re.compile(r"<loc>\s*([^<\s]+)\s*</loc>", re.I)


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
    index = _list_get(http, stats, "instaffo", budget, INSTAFFO_INDEX, as_json=False)
    # the sub-sitemap and every job loc are pinned to https://jobs.instaffo.com (rebuilt from host + path): a value read from
    # the sitemap never picks the host that is fetched or emitted
    sub = next((u for u in (_pinned(loc, INSTAFFO_HOST) for loc, _ in parse_sitemap(index)) if u and "sitemap-jobs" in urlsplit(u).path), "")
    if not sub:
        return []
    entries = [(pu, lm) for pu, lm in ((_pinned(loc, INSTAFFO_HOST), lm) for loc, lm in
                                       parse_sitemap(_list_get(http, stats, "instaffo", budget, sub, as_json=False)))
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

FEEDS: dict[str, Callable[..., list[Job]]] = {
    "djinni": fetch_djinni,
    "wuzzuf": fetch_wuzzuf,
    "infojobs": fetch_infojobs,
    "instaffo": fetch_instaffo,
}
