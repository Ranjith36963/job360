"""Five more ATS board fetchers for the hunt kit: Avature, PageUp, Comeet, HiBob, Freshteam. Same contract as
kit_ats.py / kit_ats_more.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job. The company on every job is the
row's ``name`` (the employer), never the board vendor.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  avature    career-site host, e.g. jobs.ea.com or koch.avature.net (row may add ``path``, the SearchJobs path)
  pageup     numeric institution id from careers.pageuppeople.com/<id>/... , e.g. 513; US tenants add the locale: 1216/en-us
  comeet     <slug>/<companyUID> from www.comeet.com/jobs/<slug>/<companyUID>, e.g. overwolf/B1.001
  hibob      tenant from <tenant>.careers.hibob.com
  freshteam  tenant from <tenant>.freshteam.com

Request budget per board: Avature up to AVATURE_PAGES listing pages (6 postings each, no detail requests); PageUp one
listing request plus up to DETAIL_CAP detail requests; Comeet two requests; HiBob one; Freshteam up to FRESHTEAM_PAGES listing pages plus up to DETAIL_CAP detail requests.
Detail requests are spent only on titles that pass ``row["title_ok"]`` (see ``_title_ok`` in kit_ats.py). The first
non-200 answer ends the board (a failure after the first page keeps what was read). A refused detail request
(any non-200 except Freshteam's 404/410, which only means that posting is gone) ends the detail requests for the board and
keeps every listed job, the rest with listing fields only.

Endpoints and field names are adapted from the MIT-licensed freehire adapters and career-ops providers (see NOTICE.md);
each reader below carries its own file:line citations.
Dayforce is deliberately not read: its listing POST needs a cookie-persisting client (freehire dayforce.go:34-47) and the kit's
transport has no cookie jar, so jobs.dayforcehcm.com links are reported as unsupported.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

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
        _title_ok,
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
    _count, _get, _iso_date, _job, _text, _title_ok = _mod._count, _mod._get, _mod._iso_date, _mod._job, _mod._text, _mod._title_ok

DETAIL_CAP = 25  # detail requests per board (same number as PAYLOCITY_DETAIL_CAP in kit_ats_more.py)
AVATURE_PAGES = MAX_PAGES  # 6 postings per page, so at most 60 postings per board
FRESHTEAM_PAGES = MAX_PAGES
_JSON = {"Accept": "application/json"}
_HTML = {"Accept": "text/html"}
_XHR = {"Accept": "application/json", "X-Requested-With": "XMLHttpRequest"}
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?")
_SEG = re.compile(r"[A-Za-z0-9_-]{1,80}")
_ID = re.compile(r"[A-Za-z0-9_.-]{1,80}")
_HOST = re.compile(r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.)+[A-Za-z]{2,24}")
_CULTURE = re.compile(r"[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})?")
_VOID = {"br", "hr", "img", "input", "meta", "link", "wbr"}
_BLOCK = {"p", "div", "br", "li", "ul", "ol", "tr", "table", "h1", "h2", "h3", "h4", "h5", "h6"}
_MONTHS = {m: i + 1 for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split())}


def _https(url: Any, host: str | None = None) -> str:
    """The URL when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    # userinfo ("u:p@host") and backslashes ("evil.com\\@host", which browsers read as a path) hide the real host from a reader
    ok = p.scheme == "https" and p.netloc and "@" not in p.netloc and "\\" not in p.geturl() and (host is None or (p.hostname or "").lower() == host)
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
_AVATURE_ARTICLE = re.compile(r'<article class="article article--(?:result|jobs)\b')
_AVATURE_CARD_MAX = 20_000  # characters of one card scanned; a broken page cannot make the scans quadratic
_AVATURE_TITLE = (
    re.compile(r'article__header__text__title[^>]{0,200}>\s*<a\b[^>]{0,500}?href="([^"]{0,500}/JobDetail/[^"]{1,500})"[^>]{0,500}>([\s\S]{0,2000}?)</a>'),
    re.compile(r'<a\b[^>]{0,500}?class="link"[^>]{0,500}?href="([^"]{0,500}/JobDetail/[^"]{1,500})"[^>]{0,500}>([\s\S]{0,2000}?)</a>'),
    re.compile(r'<a\b[^>]{0,500}?href="([^"]{0,500}/JobDetail/[^"]{1,500})"[^>]{0,500}>([\s\S]{0,2000}?)</a>'),
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
    starts = [m.start() for m in _AVATURE_ARTICLE.finditer(page)]
    cards = []
    for i, st in enumerate(starts):
        end = starts[i + 1] if i + 1 < len(starts) else len(page)
        close = page.find("</article>", st, min(end, st + _AVATURE_CARD_MAX))
        cards.append(page[st:close + 10] if close >= 0 else page[st:min(end, st + _AVATURE_CARD_MAX)])
    host = origin.removeprefix("https://")
    for block in cards:
        m = next((x for x in (r.search(block) for r in _AVATURE_TITLE) if x), None)
        if not m:
            continue
        title = _clean(m.group(2))
        href = html.unescape(m.group(1))
        url = _https(href if re.match(r"https?://", href, re.I) else origin + (href if href.startswith("/") else "/" + href), host)
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


# --------------------------------------------------------------------------- PageUp
# Borrowed from freehire pageup.go:56 (GET https://careers.pageuppeople.com/<instID>/cw/en/search/?page-items=1000 returns a JSON
# envelope whose "results" is an HTML fragment), :41-43 (the X-Requested-With: XMLHttpRequest header is required, else the URL
# 302s to the full HTML page), :47 (job-link href shape /<instID>/cw/en/job/<id>/<slug>), :128-181 (the a.job-link anchor starts
# a job; the location and summary that follow before the next job-link belong to it; the summary is class jobs-summary or
# summary at :175, or a bare <p> with no class at :179), :83 and :99-127 (a job with no summary gets one detail request on the
# listed URL with the XHR header; the description is the #job-details element), :106-108 (board 889 always fetches the detail),
# :22 (posted date is not exposed). career-ops providers/pageup.mjs:166-168 supplies the host lock (careers.pageuppeople.com
# only; tenant and site must match the board); its id-only job URL (:180-183) is NOT used, the listed href with its slug is
# (that is what freehire fetches). pageup.mjs:38-43 PAGE_SIZE 100 and its paging are NOT used.
# NOT IN REFERENCE: paging (the kit asks once; a board over 1000 postings is cut to its first 1000), the ``<id>/<lang>`` token.
_PAGEUP_ALWAYS_DETAIL = {"889"}


class _PageupList(HTMLParser):
    """Walks a PageUp results fragment in document order (freehire pageup.go:147-176)."""

    def __init__(self, board: str, site: str) -> None:
        super().__init__(convert_charrefs=True)
        self.jobs: list[dict[str, str]] = []
        self.cur: dict[str, str] | None = None
        self.anchor = False
        self.cap: list[Any] | None = None  # [field, tag, depth]
        self.buf: list[str] = []
        self.rx = re.compile(rf"^/{re.escape(board)}/{re.escape(site)}/[^/]+/job/(\d+)(?:/|$)")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        cls = (a.get("class") or "").split()
        if tag == "a" and "job-link" in cls:
            m = self.rx.match(urlsplit(a.get("href") or "").path)
            if m:
                self.cur = {"id": m.group(1), "path": urlsplit(a.get("href") or "").path, "title": "", "location": "", "summary": ""}
                self.jobs.append(self.cur)
                self.anchor = True
            return
        if self.cur is None or tag in _VOID:
            return
        if self.cap:
            if tag == self.cap[1]:
                self.cap[2] += 1
            return
        field = ""
        if "location" in cls and not self.cur["location"]:
            field = "location"
        elif ("jobs-summary" in cls or "summary" in cls) and not self.cur["summary"]:
            field = "summary"
        elif tag == "p" and not a.get("class") and not self.cur["summary"]:
            field = "summary"
        if field:
            self.cap, self.buf = [field, tag, 1], []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            self.anchor = False
        if self.cap and tag == self.cap[1]:
            self.cap[2] -= 1
            if self.cap[2] == 0 and self.cur is not None:
                self.cur[self.cap[0]] = _clean(" ".join(self.buf))
                self.cap = None

    def handle_data(self, data: str) -> None:
        if self.anchor and self.cur is not None:
            self.cur["title"] += data
        elif self.cap:
            self.buf.append(data)


class _ById(HTMLParser):
    """Text of the element with a given id, block tags kept as line breaks."""

    def __init__(self, want: str) -> None:
        super().__init__(convert_charrefs=True)
        self.want, self.tag, self.depth, self.parts = want, "", 0, []  # type: ignore[var-annotated]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.depth:
            if tag == self.tag:
                self.depth += 1
            if tag in _BLOCK:
                self.parts.append("\n")
        elif dict(attrs).get("id") == self.want and tag not in _VOID:
            self.tag, self.depth = tag, 1

    def handle_endtag(self, tag: str) -> None:
        if self.depth and tag == self.tag:
            self.depth -= 1
        if self.depth and tag in _BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self.depth:
            self.parts.append(data)


def _pageup(row: dict[str, Any], http: Http) -> list[Job] | None:
    """PageUp People careers site: one search request (XHR envelope), detail pages only for short-summary titles."""
    board, _, lang = str(row.get("token") or "").strip().partition("/")
    lang = lang or str(row.get("lang") or "en")
    if not re.fullmatch(r"\d{1,8}", board) or not _CULTURE.fullmatch(lang):
        return None
    origin, site = "https://careers.pageuppeople.com", "cw"
    st, data = _get(http, f"{origin}/{board}/{site}/{lang}/search/?page-items=1000", headers=_XHR)
    frag = data.get("results") if isinstance(data, dict) else None
    if st != 200 or not isinstance(frag, str):
        return None
    p = _PageupList(board, site)
    p.feed(frag)
    p.close()
    jobs: list[Job] = []
    seen: set[str] = set()
    details = 0
    detail_open = True  # a refused detail request ends the detail requests for this board; listed jobs are kept
    for a in p.jobs:
        title = _clean(a["title"])
        if not title or a["id"] in seen:
            continue
        seen.add(a["id"])
        url = origin + a["path"]  # the href as listed, slug included: that is the URL the detail XHR was seen to answer
        desc = a["summary"]
        if (not desc or board in _PAGEUP_ALWAYS_DETAIL) and detail_open and details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dst, det = _get(http, url, headers=_XHR)
            if dst != 200:
                detail_open = False  # 403, 429, 5xx: stop asking this host; this job keeps its listing fields
            body = det.get("results") if isinstance(det, dict) else None
            if dst == 200 and isinstance(body, str):  # a failing detail only costs the description
                q = _ById("job-details")
                q.feed(body)
                q.close()
                desc = _text("".join(q.parts)) or desc
        jobs.append(_job(row, "pageup", title=title, url=url, location=a["location"], description=desc,
                         remote=_remote("", a["location"])))
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


# --------------------------------------------------------------------------- Freshteam
# Borrowed from freehire freshteam.go:34 and :42 (board = tenant subdomain; GET https://<tenant>.freshteam.com/jobs?page=N lists
# the postings), :39-43 (page until a page adds no new links), :101 (id pattern: the /jobs/<12-char id> segment), :59-98 (each
# job page is server-rendered with a schema.org JobPosting ld+json block: title, description, datePosted, jobLocation.address,
# remote as the string "true"/"false"), :66 (a 404/410 detail drops the posting), :111-118 (the ld+json struct). The listing
# carries no title worth trusting in freehire's fixtures, so the title comes from the link text, else the URL slug.
# NOT IN REFERENCE: spending detail requests only on titles passing title_ok (freehire fetches every posting); postings past
# DETAIL_CAP or rejected by title_ok keep the listing title and link, with no description. Cap of 10 listing pages (freehire: 100).
_FT_ID = re.compile(r"/jobs/([A-Za-z0-9_-]{12})(?:[/?#]|$)")
_A_OPEN = re.compile(r"<a(?=[\s>])", re.I)
_HREF = re.compile(r"""\bhref=["']([^"']+)["']""", re.I)
_A_CLOSE = re.compile(r"</a\b", re.I)
_SCRIPT_OPEN = re.compile(r"<script(?=[\s>])", re.I)
_LD_TYPE = re.compile(r"""\btype=["']application/ld\+json["']""", re.I)
_SCRIPT_CLOSE = re.compile(r"</script\b[^>]*>", re.I)
_TAG_MAX, _TEXT_MAX = 2000, 500  # an opening tag or an anchor's text longer than this is not a real one


def _ft_links(body: str) -> list[tuple[str, str]]:
    """(href, inner html) of each anchor, found in one linear pass: every scan is bounded, so broken HTML cannot go quadratic."""
    out: list[tuple[str, str]] = []
    pos = 0
    for m in _A_OPEN.finditer(body):
        if m.start() < pos:
            continue
        end = body.find(">", m.end(), m.end() + _TAG_MAX)
        if end < 0:
            continue
        h = _HREF.search(body, m.end(), end)
        close = _A_CLOSE.search(body, end + 1, end + 1 + _TEXT_MAX)
        if not h or not close:
            continue
        out.append((h.group(1), body[end + 1:close.start()]))
        pos = close.end()
    return out


def _ld_blocks(page: str) -> list[str]:
    """The text of each ld+json script, in one linear pass (a missing closing tag ends the search, as no later one can close either)."""
    out: list[str] = []
    pos = 0
    for m in _SCRIPT_OPEN.finditer(page):
        if m.start() < pos:
            continue
        end = page.find(">", m.end(), m.end() + _TAG_MAX)
        if end < 0 or not _LD_TYPE.search(page, m.end(), end):
            continue
        c = _SCRIPT_CLOSE.search(page, end + 1)
        if not c:
            break
        out.append(page[end + 1:c.start()])
        pos = c.end()
    return out


def _ld_posting(page: str) -> dict[str, Any] | None:
    """The first schema.org JobPosting in the page's ld+json blocks (top level, a list, or an @graph)."""
    for raw in _ld_blocks(page):
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        stack = [data]
        while stack:
            node = stack.pop(0)
            if isinstance(node, list):
                stack += node
            elif isinstance(node, dict):
                kind = node.get("@type")
                if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                    return node
                if isinstance(node.get("@graph"), list):
                    stack += node["@graph"]
    return None


def _freshteam(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Freshteam careers: paged /jobs listing, then a JobPosting ld+json detail page for the titles worth reading."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _LABEL.fullmatch(tenant):
        return None
    host = f"{tenant}.freshteam.com"
    seen: dict[str, dict[str, str]] = {}
    for page in range(1, FRESHTEAM_PAGES + 1):
        st, body = _get(http, f"https://{host}/jobs?page={page}", as_json=False, headers=_HTML)
        if st != 200 or not isinstance(body, str):
            if page > 1:
                break
            return None
        fresh = 0
        for href, inner in _ft_links(body):
            url = _https(urljoin(f"https://{host}/", html.unescape(href)), host)
            m = _FT_ID.search(urlsplit(url).path) if url else None
            if not m or m.group(1) in seen:
                continue
            slug = urlsplit(url).path.split("/")[3:4]
            title = _clean(inner) or (slug[0].replace("-", " ").strip() if slug else "")
            seen[m.group(1)] = {"url": url, "title": title}
            fresh += 1
        if not fresh:
            break
    jobs: list[Job] = []
    details = 0
    detail_open = True  # a refused detail request ends the detail requests for this board; listed jobs are kept
    for jid, card in seen.items():
        title, desc, place, posted, remote = card["title"], "", "", "", None
        if detail_open and details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            st, det = _get(http, card["url"], as_json=False, headers=_HTML)
            if st in (404, 410):
                continue  # the posting is gone
            if st != 200:
                detail_open = False  # 403, 429, 5xx: stop asking this host; this job keeps its listing fields
            ld = _ld_posting(det) if st == 200 and isinstance(det, str) else None
            if ld:
                title = str(ld.get("title") or "").strip() or title
                desc = _text(html.unescape(str(ld.get("description") or "")))
                loc = ld.get("jobLocation")
                addr = (loc[0] if isinstance(loc, list) and loc else loc)
                addr = addr.get("address") if isinstance(addr, dict) else None
                if isinstance(addr, dict):
                    place = _commas(addr.get("addressLocality"), addr.get("addressRegion"), addr.get("addressCountry"))
                posted = _iso_date(ld.get("datePosted"))
                remote = True if str(ld.get("remote") or "").strip().lower() == "true" else _remote("", place)
        if title:
            jobs.append(_job(row, "freshteam", title=title, url=card["url"], location=place, posted=posted, description=desc,
                             remote=remote, extra={"id": jid}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "avature": _wrap(_avature),
    "pageup": _wrap(_pageup),
    "comeet": _wrap(_comeet),
    "hibob": _wrap(_hibob),
    "freshteam": _wrap(_freshteam),
}
