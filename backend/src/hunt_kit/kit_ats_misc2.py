"""Eleven more ATS readers for the hunt kit: OpenCATS, Odoo, recruiting-solutions.org, TeamEx, Wantapply, werecruit, Workstream, WordPress/Yoast career sites, careers-page.com, Jobboardly and JobAppNetwork. Same contract as
kit_ats.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at
import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies JSON ``board``, or ``--companies-extra "ats:token"``):
  opencats             portal root: host or host/path, e.g. atscareers.g4s.com or careers.boomit.pt/careers
  odoo                 careers host of an Odoo recruitment site, e.g. jobs.amewaregroup.com
  recruitingsolutions  platform tenant key (the customerId header), e.g. riv-prod
  teamex               none (one global marketplace feed): any placeholder token works
  wantapply            none (one aggregator, read from the wantapply.cy mirror): any placeholder token works
  werecruit            <locale>/<tenant> from careers.werecruit.io/<locale>/<tenant>, e.g. en/idiap
  workstream           the eight hex characters of www.workstream.us/j/<board>
  wpyoast              career-site host of a WP Job Manager + Yoast SEO site, e.g. careers.theplanetgroup.com
  careerspage          tenant subdomain of <tenant>.careers-page.com
  jobboardly           tenant subdomain of <tenant>.jobboardly.com (a niche job board: each ad names its own employer)
  jobappnetwork        numeric client id from apply.jobappnetwork.com/clients/<id>

Endpoints, params and field names are adapted from the MIT-licensed freehire adapters (internal/ingest/sources/<name>.go,
see NOTICE.md). Where the kit differs from the reference it says so next to the reader: the kit never fans out, so every
detail-page reader has its own small request cap (``*_DETAIL_CAP``) and a ``title_ok`` gate (kit_ats._title_ok).
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import math
import re
from html.parser import HTMLParser
from typing import Any, Callable, Iterator
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
        _iso_date,
        _job,
        _remote_from,
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
    _count, _get, _iso_date, _job = _mod._count, _mod._get, _mod._iso_date, _mod._job
    _remote_from, _text, _title_ok = _mod._remote_from, _mod._text, _mod._title_ok

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_HTMLH = {"Accept": "text/html"}
_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")
_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?")
DETAIL_CAP = 25  # detail-page requests per board (the kit's own cap; the references fan out under a worker pool)
_LIST_CAP = MAX_PAGES * 100  # postings kept from one listing


# --------------------------------------------------------------------------- a small DOM (html.parser, no regex over markup)
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = frozenset("p div br li ul ol h1 h2 h3 h4 h5 h6 tr table section article header footer".split())


class _N:
    """One element: tag, attributes, children (elements or text strings), parent."""

    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _N | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.kids: list[Any] = []


class _Builder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _N("#root", {}, None)
        self._stack: list[_N] = [self.root]
        self._open: dict[str, int] = {}  # open elements per tag: an end tag with none open returns at once (no stack scan)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = _N(tag, {k: v or "" for k, v in attrs}, self._stack[-1])
        self._stack[-1].kids.append(n)
        if tag not in _VOID:
            self._stack.append(n)
            self._open[tag] = self._open.get(tag, 0) + 1

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].kids.append(_N(tag, {k: v or "" for k, v in attrs}, self._stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        if not self._open.get(tag):
            return
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                for gone in self._stack[i:]:
                    self._open[gone.tag] -= 1
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].kids.append(data)


def _dom(page: str) -> _N:
    b = _Builder()
    try:
        b.feed(page)
        b.close()
    except Exception:  # noqa: BLE001 - a broken page is just a smaller tree
        pass
    return b.root


def _walk(root: _N) -> Iterator[_N]:
    """Every element under (and including) ``root``, document order, without recursion."""
    stack = [root]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(k for k in reversed(n.kids) if isinstance(k, _N))


def _classes(n: _N) -> set[str]:
    return set(n.attrs.get("class", "").split())


def _node_text(root: _N, limit: int = 0) -> str:
    """Plain text of a subtree: block tags break lines, script/style are dropped (the references sanitise the body).
    ``limit`` > 0 stops reading once that many characters are collected (labels and titles need only the start)."""
    out: list[str] = []
    stack: list[Any] = [root]
    size = 0
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            out.append(x)
            size += len(x)
            if limit and size > limit:
                break
            continue
        if x.tag in ("script", "style"):
            continue
        if x.tag in _BLOCK:
            out.append("\n")
            stack.append("\n")
        stack.extend(reversed(x.kids))
    s = re.sub(r"[ \t\r\f\v]+", " ", "".join(out))
    s = "\n".join(line.strip() for line in s.split("\n"))
    return re.sub(r"\n\s*\n+", "\n", s).strip()


def _inline(n: _N | None, limit: int = 0) -> str:
    """The text of an element with every run of whitespace collapsed to one space."""
    return " ".join(_node_text(n, limit).split()) if n is not None else ""


def _first_el(n: _N) -> _N | None:
    return next((k for k in n.kids if isinstance(k, _N)), None)


def _next_el(n: _N) -> _N | None:
    if n.parent is None:
        return None
    sibs = n.parent.kids
    i = next((j for j, k in enumerate(sibs) if k is n), -1)
    return next((k for k in sibs[i + 1:] if isinstance(k, _N)), None) if i >= 0 else None


def _by_id(root: _N, ident: str) -> _N | None:
    return next((n for n in _walk(root) if n.attrs.get("id") == ident), None)


def _by_class(root: _N, cls: str, tag: str = "") -> _N | None:
    return next((n for n in _walk(root) if cls in _classes(n) and (not tag or n.tag == tag)), None)


def _itemprops(root: _N, prop: str) -> list[_N]:
    return [n for n in _walk(root) if n.attrs.get("itemprop") == prop]


def _script_text(n: _N) -> str:
    return "".join(k for k in n.kids if isinstance(k, str))


def _ld_posting(root: _N) -> dict[str, Any] | None:
    """The first schema.org JobPosting in an application/ld+json block: bare object, array of nodes or @graph."""
    for n in _walk(root):
        if n.tag != "script" or n.attrs.get("type", "").strip().lower() != "application/ld+json":
            continue
        try:
            data = json.loads(_script_text(n), strict=False)
        except ValueError:
            continue
        nodes: list[Any] = data if isinstance(data, list) else [data]
        for it in list(nodes):
            if isinstance(it, dict) and isinstance(it.get("@graph"), list):
                nodes += it["@graph"]
        for it in nodes:
            t = it.get("@type") if isinstance(it, dict) else None
            if isinstance(t, str) and t.lower() == "jobposting":
                return dict(it)
    return None


# --------------------------------------------------------------------------- shared helpers
_OPEN_BLOCK = re.compile(r"<(script|style)(?![a-z0-9_])", re.I)
_LOWER = {c: c + 32 for c in range(65, 91)}  # ASCII-only lowercasing keeps every index of the original string


def _drop_blocks(s: str) -> str:
    """``s`` without its <script>/<style> elements, in one forward pass (no backtracking regex, so hostile input stays linear).

    An element with no closing tag runs to the end of the text, as a browser reads it; an opening tag with no ``>`` anywhere after
    it is left alone and ends the scan (no later opening tag can have one either).
    """
    low = s.translate(_LOWER)
    out: list[str] = []
    i = 0
    while True:
        m = _OPEN_BLOCK.search(s, i)
        if not m:
            break
        gt = s.find(">", m.end())
        if gt < 0:
            break
        out.append(s[i:m.start()])
        close = low.find("</" + m.group(1).lower(), gt + 1)
        end = s.find(">", close) if close >= 0 else -1
        if end < 0:
            return "".join(out)  # unclosed: the rest is element content
        i = end + 1
    out.append(s[i:])
    return "".join(out)


def _body(raw: Any) -> str:
    """Plain text of an HTML fragment carried in a JSON field (entities decoded first, script/style dropped)."""
    s = _drop_blocks(html.unescape(str(raw or ""))[:200_000])
    # kit_ats._text scans forward from every '<' to a '>': a run of '<' with no '>' after it would make that quadratic. A tag with
    # no '>' after it is cut here instead (the same thing _text does to a dangling tag at the very end).
    cut = s.rfind(">") + 1
    dangling = s.find("<", cut)
    return str(_text(s[:dangling] if dangling >= 0 else s))


def _commas(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


def _https(url: Any, host: str | None = None) -> str:
    """The URL when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p.geturl() if ok else ""


def _page(http: Http, url: str) -> tuple[int, str | None]:
    """(status, text) of an HTML/text GET; text is None unless the answer was a 200 string."""
    st, page = _get(http, url, as_json=False, headers=_HTMLH)
    return st, page if st == 200 and isinstance(page, str) else None


def _ld_place(jl: Any) -> list[str]:
    """Each jobLocation (one Place or a list) as 'City, Region, Country', blanks dropped, repeats collapsed."""
    out: list[str] = []
    for pl in jl if isinstance(jl, list) else [jl]:
        a = pl.get("address") if isinstance(pl, dict) else None
        s = _commas(*(a.get(k) for k in ("addressLocality", "addressRegion", "addressCountry"))) if isinstance(a, dict) else ""
        if s and s not in out:
            out.append(s)
    return out


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


def _slug_title(slug: str) -> str:
    """A rough title from a URL slug, used only to ask the user's title gate before a detail request (never shown)."""
    return re.sub(r"[-_]+", " ", re.sub(r"^\d+-", "", slug)).strip()


def _locs(xml: str, outer: str) -> list[str]:
    """The <loc> values inside <url> (or <sitemap>) blocks of a sitemap text."""
    # One forward scan: the first <loc> after each opening <outer> tag counts. Nothing here can backtrack over an unclosed tag.
    pat = re.compile(rf"<(/?){re.escape(outer)}>|<loc>([^<]*)</loc>")
    out: list[str] = []
    inside = got = False
    for m in pat.finditer(xml[:5_000_000]):
        if m.group(2) is None:
            inside, got = not m.group(1), False
        elif inside and not got:
            got = True
            loc = html.unescape(m.group(2)).strip()
            if loc:
                out.append(loc)
                if len(out) >= 5000:
                    break
    return out


# --------------------------------------------------------------------------- OpenCATS
# Borrowed from freehire internal/ingest/sources/opencats.go:23,31,35 (GET https://<board>/index.php?m=careers&p=showAll; the board
# is a host, optionally with the path the portal is mounted under), :203-214 (a posting link carries p=showJob and ID=<digits>, in any
# order), :260-268 (the link is an <a href> whose text is the title, or a clickable row whose onclick sets location.href and whose
# first cell is the title), :237-239 and :162-167 (talent-pool "apply here / general application / open application /
# spontaneous application" entries are not positions), :222-253 (first carrier of an id wins), :48-66 (detail page for the location
# and body), :74-93 (body container: id or class descriptive, job-description, job-decription, description, in that order),
# :99-155 (location: the cell after a label containing "location", else the value cell of the first row of #detailsTable).
# Kit differences: the posting link must stay on the board's host (own check); a card past the detail cap, or whose detail failed,
# keeps its listing title and link without location or description (the reference drops a failed detail, :49-52).
_OC_ID = re.compile(r"[?&]ID=(\d+)")
_OC_CLICK = re.compile(r"""location\.href\s*=\s*['"]([^'"]{1,500})['"]""")
_OC_GENERAL = ("apply here", "general application", "open application", "spontaneous application")
_OC_BODY = ("descriptive", "job-description", "job-decription", "description")
_OC_BOARD = re.compile(r"([a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24})((?:/[A-Za-z0-9_.-]{1,40}){0,3})")


def _oc_location(root: _N) -> str:
    for n in _walk(root):
        if n.tag in ("td", "th") and "location" in _inline(n, 200).lower():
            nxt = _next_el(n)
            if nxt is not None:
                v = _inline(nxt)
                if v:
                    return v
    table = _by_id(root, "detailsTable")
    for tr in (n for n in _walk(table) if n.tag == "tr") if table is not None else ():
        label = _first_el(tr)
        nxt = _next_el(label) if label is not None else None
        return _inline(nxt) if nxt is not None else ""
    return ""


def _oc_body(root: _N) -> str:
    for name in _OC_BODY:
        n = _by_id(root, name) or _by_class(root, name)
        if n is not None:
            return _node_text(n)
    return ""


def _opencats(row: dict[str, Any], http: Http) -> list[Job] | None:
    """OpenCATS self-hosted career portal (index.php?m=careers&p=showAll) plus a capped detail page per posting."""
    tok = str(row.get("token") or "").strip().strip("/")
    head, sep, tail = tok.partition("/")
    m = _OC_BOARD.fullmatch(head.lower() + sep + tail)  # only the host is case-insensitive: the portal path may be /Careers
    if not m:
        return None
    host, base = m.group(1), f"https://{m.group(1)}{m.group(2)}/"
    _st, page = _page(http, base + "index.php?m=careers&p=showAll")
    if page is None:
        return None
    cards: list[tuple[str, str, str]] = []
    seen: set[str] = set()
    for n in _walk(_dom(page)):
        holder: _N | None
        if n.tag == "a":
            loc, holder = n.attrs.get("href", ""), n
        else:
            c = _OC_CLICK.search(n.attrs.get("onclick", ""))
            loc, holder = (c.group(1), _first_el(n)) if c else ("", None)
        idm = _OC_ID.search(loc) if "p=showJob" in loc else None
        if not idm or idm.group(1) in seen:
            continue
        title = _inline(holder)  # after the cheap checks: nested unclosed <a> tags would make this quadratic otherwise
        if not title:
            continue
        seen.add(idm.group(1))
        url = _https(urljoin(base, loc), host)
        if url and not any(g in title.lower() for g in _OC_GENERAL):
            cards.append((idm.group(1), url, title))
            if len(cards) >= _LIST_CAP:
                break
    jobs: list[Job] = []
    details = 0
    for jid, url, title in cards[:_LIST_CAP]:
        place = desc = ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            _s, det = _page(http, url)
            if det is not None:
                root = _dom(det)
                place, desc = _oc_location(root), _oc_body(root)
        jobs.append(_job(row, "opencats", title=title, url=url, location=place, description=desc,
                         remote=_remote_from(place), extra={"job_id": jid}))
    return jobs


# --------------------------------------------------------------------------- Odoo
# Borrowed from freehire internal/ingest/sources/odoo.go:28,32 (GET https://<board>/jobs, one unpaginated page), :36 and :74-80
# (job links: /jobs/<slug>-<id> or /jobs/detail/<slug>-<id>), :49-69 (detail page: schema.org JobPosting microdata; itemprop
# description is the body and a page without one is not a posting, title is the page <h1> else itemprop title, location is
# addressLocality + addressCountry, datePosted is a datetime/content attribute), :107-118 of html.go (the richest itemprop
# element wins).  Kit differences: a capped number of detail pages; postings past the cap are not listed (their title is not
# in the reference's listing read).
_ODOO_ID = re.compile(r"/jobs/(?:detail/)?[^\"?#]*?-(\d+)(?:[/?#]|$)")


def _odoo(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Odoo recruitment site (website_hr_recruitment): /jobs listing, then each posting's JobPosting microdata (capped)."""
    host = str(row.get("token") or "").strip().lower()
    if not _HOST.fullmatch(host):
        return None
    base = f"https://{host}/jobs"
    _st, page = _page(http, base)
    if page is None:
        return None
    links: dict[str, str] = {}
    for n in _walk(_dom(page)):
        href = n.attrs.get("href", "") if n.tag == "a" else ""
        if href and len(href) <= 300 and _ODOO_ID.search(href):
            url = _https(urljoin(base, href), host)
            if url and url not in links and len(links) < _LIST_CAP:
                links[url] = _inline(n, 300)
    jobs: list[Job] = []
    details = 0
    for url, anchor in list(links.items())[:_LIST_CAP]:
        if details >= DETAIL_CAP:
            break
        if not _title_ok(row, anchor):
            continue
        details += 1
        _s, det = _page(http, url)
        if det is None:
            continue
        root = _dom(det)
        bodies = _itemprops(root, "description")
        if not bodies:
            continue  # not a JobPosting page
        best = max(bodies[:10], key=lambda b: len(_node_text(b)))
        desc = _node_text(best)
        if not desc:
            continue
        h1 = next((n for n in _walk(root) if n.tag == "h1"), None)
        tp = _itemprops(root, "title")
        title = _inline(h1) or (_inline(tp[0]) if tp else "")
        place = _commas(*(_inline(p[0]) for p in (_itemprops(root, "addressLocality"), _itemprops(root, "addressCountry")) if p))
        dp = _itemprops(root, "datePosted")
        posted = _iso_date(dp[0].attrs.get("datetime") or dp[0].attrs.get("content")) if dp else ""
        idm = _ODOO_ID.search(urlsplit(url).path)
        if title:
            jobs.append(_job(row, "odoo", title=title, url=url, location=place, posted=posted, description=desc,
                             remote=_remote_from(place), extra={"job_id": idm.group(1) if idm else ""}))
    return jobs


# --------------------------------------------------------------------------- recruiting-solutions.org
# Borrowed from freehire internal/ingest/sources/recruitingsolutions.go:24-25 (POST https://production.api.recruiting-solutions.org/search,
# page size 50), :62 (header customerId = the tenant key), :68 (body count/facets/filter/search "*"/skip), :73-95 (stop on an empty page or
# once skip+50 reaches @odata.count; inactive records dropped), :20-21 and :120-138 (each posting is stored once per UI locale as
# "<id>-<locale>": dedup on the base id, prefer en_GB, then en_US), :105-118 (title, company = the operating company named on the
# record, HTML description, location city + country, link = the apply URL, datePosted, teleComuteId), :140-153 (telecommute1 hybrid,
# telecommute2 remote, telecommute3 onsite), :157-160 (the link without its locale query is the canonical URL).
_RS_API = "https://production.api.recruiting-solutions.org/search"
_RS_PAGE = 50


def _recruitingsolutions(row: dict[str, Any], http: Http) -> list[Job] | None:
    """recruiting-solutions.org Career Site Builder search API (token = tenant key sent as customerId)."""
    tenant = str(row.get("token") or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", tenant):
        return None
    best: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    skip = 0
    for page in range(MAX_PAGES):
        body = {"count": True, "facets": [], "filter": "", "search": "*", "skip": skip}
        st, data = _get(http, _RS_API, method="POST", data=json.dumps(body).encode(), headers=dict(_POST, customerId=tenant))
        recs = data.get("value") if isinstance(data, dict) else None
        if st != 200 or not isinstance(recs, list):
            if page:
                break
            return None
        if not recs:
            break
        for r in recs:
            if not isinstance(r, dict) or r.get("isActive") is not True:
                continue
            jid = str(r.get("jobId") or "")
            base, _, lang = jid.partition("-")
            if not base:
                continue
            rank = {"en_GB": 3, "en_US": 2}.get(lang, 1)
            if base not in best:
                order.append(base)
                best[base] = {**r, "_rank": rank}
            elif rank > best[base]["_rank"]:
                best[base] = {**r, "_rank": rank}
        total = data.get("@odata.count") if isinstance(data, dict) else None
        if not isinstance(total, int) or skip + _RS_PAGE >= total:
            break
        skip += _RS_PAGE
    jobs: list[Job] = []
    for base in order[:_LIST_CAP]:
        r = best[base]
        url = _https(str(r.get("link") or "").split("?", 1)[0])
        title = str(r.get("title") or "").strip()
        if not url or not title:
            continue
        loc: dict[str, Any] = r["location"] if isinstance(r.get("location"), dict) else {}
        place = _commas(loc.get("city"), r.get("country"))
        mode = {"telecommute1": "hybrid", "telecommute2": "remote", "telecommute3": "onsite"}.get(str(r.get("teleComuteId") or ""), "")
        jobs.append(_job(row, "recruitingsolutions", title=title, url=url, location=place, posted=_iso_date(r.get("datePosted")),
                         description=_body(r.get("description")), remote=True if mode == "remote" else _remote_from(place),
                         extra={"job_id": base, "work_mode": mode, "employer": str(r.get("company") or "")}))
        if str(r.get("company") or "").strip():
            jobs[-1]["company"] = str(r["company"]).strip()  # one tenant hosts several operating companies (:15-18)
    return jobs


# --------------------------------------------------------------------------- TeamEx
# Borrowed from freehire internal/ingest/sources/teamex.go:25 (POST https://api.teamex.io/api/jts/global/filter?pageSize=50&pageIndex=<n>,
# 1-based, empty JSON body), :27 (posting page https://teamex.io/job/<sequenceId>), :46-73 (data.paging.totalCount, data.data[]:
# sequenceId, title, isActive, isAnonymous, publishedDate, yearsOfExperienceMin, descriptions[{title, content}], countries[].name,
# requiredSkills[].skill.displayName, company.name), :97-118 (a failing first page fails the board, a later one ends the walk;
# stop on an empty page or once page*50 reaches totalCount), :110-112 (inactive postings skipped), :126-142 (an anonymised posting
# or one without a company name shows the marketplace label "TeamEx" :32), :144-168 (body = each section's title as a heading, then
# its content; location = the country names joined). Kit differences: the walk stops at MAX_PAGES pages; skills are kept as the
# marketplace spells them (the reference canonicalises them through its own skill dictionary).
_TEAMEX_URL = "https://api.teamex.io/api/jts/global/filter?pageSize=50&pageIndex=%d"
_TEAMEX_PAGE = 50


def _teamex(row: dict[str, Any], http: Http) -> list[Job] | None:
    """TeamEx global talent marketplace (one paged POST list; the board token is ignored)."""
    jobs: list[Job] = []
    for page in range(1, MAX_PAGES + 1):
        st, data = _get(http, _TEAMEX_URL % page, method="POST", data=b"{}", headers=_POST)
        d: Any = data.get("data") if isinstance(data, dict) else None
        items = d.get("data") if isinstance(d, dict) else None
        if st != 200 or not isinstance(items, list):
            if page > 1:
                break
            return None
        if not items:
            break
        for it in items:
            if not isinstance(it, dict) or it.get("isActive") is not True or not isinstance(it.get("sequenceId"), int):
                continue
            title = str(it.get("title") or "").strip()
            if not title:
                continue
            comp: dict[str, Any] = it["company"] if isinstance(it.get("company"), dict) else {}
            company = "TeamEx" if it.get("isAnonymous") is True else (str(comp.get("name") or "").strip() or "TeamEx")
            parts = [f"{str(s.get('title') or '').strip()}\n{_body(s.get('content'))}".strip()
                     for s in (it.get("descriptions") or []) if isinstance(s, dict)]
            place = _commas(*(c.get("name") for c in (it.get("countries") or []) if isinstance(c, dict)))
            skills = [str(((s or {}).get("skill") or {}).get("displayName") or "") for s in (it.get("requiredSkills") or [])
                      if isinstance(s, dict) and isinstance(s.get("skill"), dict)]
            extra: dict[str, Any] = {"job_id": str(it["sequenceId"]), "skills": [s for s in skills if s]}
            ymin = it.get("yearsOfExperienceMin")
            if isinstance(ymin, int) and not isinstance(ymin, bool) and ymin > 0:
                extra["years_min"] = ymin
            j = _job(row, "teamex", title=title, url=f"https://teamex.io/job/{it['sequenceId']}", location=place,
                     posted=_iso_date(it.get("publishedDate")), description="\n\n".join(p for p in parts if p),
                     remote=_remote_from(place), extra=extra)
            j["company"] = company
            jobs.append(j)
        total = d.get("paging", {}).get("totalCount") if isinstance(d.get("paging"), dict) else None
        if isinstance(total, int) and total > 0 and page * _TEAMEX_PAGE >= total:
            break
    return jobs


# --------------------------------------------------------------------------- Wantapply
# Borrowed from freehire internal/ingest/sources/wantapply.go:14-21 (the .com host sits behind a WAF that 403s non-browser clients, so
# the .cy mirror is read), :51-55 (sitemap https://wantapply.cy/sitemap.xml, loc host wantapply.cy), :63-68 and :280-293 (a vacancy is
# a single-segment path; /company/*, /jobs/* and the pages create, sign-in, sign-up, privacy-policy, terms-of-service are not),
# :206-251 (vacancy page: schema.org JobPosting ld+json; no JobPosting, or no hiringOrganization.name, drops the vacancy; the
# company is the ad's own employer), :253-269 (body: the page's <div class="Description">, else the ld+json description),
# :318-335 (jobLocation places "City, Region, Country" joined by "; "), :227-233 (jobLocationType TELECOMMUTE is remote),
# :56-60 (the .cy edge rate-caps by IP: few, slow requests). Kit differences: detail requests are capped and gated by
# the user's title test on the slug (the sitemap has no titles), vacancies past the cap are not listed, and the first 403 or
# 429 ends the board. NOT IN REFERENCE: sitemap order, or any direct employer link (the posting is the wantapply.cy page).
_WA_HOST = "wantapply.cy"
_WA_RESERVED = frozenset({"create", "sign-in", "sign-up", "privacy-policy", "terms-of-service"})


def _wantapply(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Wantapply aggregator via the wantapply.cy mirror: sitemap, then each vacancy's JobPosting (capped; token ignored)."""
    st, xml = _get(http, f"https://{_WA_HOST}/sitemap.xml", as_json=False, headers={"Accept": "application/xml, text/xml"})
    if st != 200 or not isinstance(xml, str):
        return None
    slugs: list[tuple[str, str]] = []
    for loc in _locs(xml, "url"):
        try:
            u = urlsplit(loc)
        except ValueError:
            u = None
        seg = u.path.strip("/") if u else ""
        if u and u.scheme == "https" and (u.hostname or "").lower() == _WA_HOST and seg and "/" not in seg and seg not in _WA_RESERVED:
            slugs.append((seg, f"https://{_WA_HOST}{u.path}"))
    jobs: list[Job] = []
    details = 0
    for seg, url in slugs:
        if details >= DETAIL_CAP:
            break
        if not _title_ok(row, _slug_title(seg)):
            continue
        details += 1
        st, det = _page(http, url)
        if st in (403, 429):
            break  # throttled: stop asking
        p = _ld_posting(_dom(det)) if det is not None else None
        org = p.get("hiringOrganization") if p else None
        company = str(org.get("name") or "").strip() if isinstance(org, dict) else ""
        title = str((p or {}).get("title") or "").strip()
        if not p or not company or not title:
            continue
        root = _dom(det or "")
        rich = _by_class(root, "Description", "div")
        desc = _node_text(rich) if rich is not None else ""
        place = "; ".join(_ld_place(p.get("jobLocation")))
        remote = str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"
        j = _job(row, "wantapply", title=title, url=url, location=place, posted=_iso_date(p.get("datePosted")),
                 description=desc or _body(p.get("description")), remote=True if remote else _remote_from(place),
                 extra={"slug": seg})
        j["company"] = company
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- werecruit
# Borrowed from freehire internal/ingest/sources/werecruit.go:39 (https://careers.werecruit.io), :64 and :79-85 (board is
# "<locale>/<tenant>", both required: a tenant answers a locale it is not configured for with an empty listing), :44 and :90-101 (the
# listing page assigns window.allOffers = [...], the tenant's whole list; a page without the assignment is an empty listing, not an
# error), :48-57 (Id, TitleTranslated, Url, Address_City, Address_Region, Address_State = a two-letter COUNTRY code,
# TimeTranslated, PublicationStartDate), :106-128 (a posting without Id is skipped; its own page carries the body in
# <div class="description">; location = city + region), :133-142 (Full time / Part time). Kit differences: detail pages are
# capped and gated by title_ok, a posting past the cap keeps its listing fields without a description, and the posting link
# must stay on careers.werecruit.io.
_WR_BOARD = re.compile(r"([A-Za-z]{2}(?:[-_][A-Za-z]{2})?)/([A-Za-z0-9_.-]{1,80})")
_WR_OFFERS = re.compile(r"window\.allOffers\s*=\s*")


def _werecruit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """werecruit career site (token locale/tenant): the embedded offer list, then a capped detail page per offer."""
    m = _WR_BOARD.fullmatch(str(row.get("token") or "").strip())
    if not m:
        return None
    _st, page = _page(http, f"https://careers.werecruit.io/{m.group(1)}/{quote(m.group(2), safe='')}")
    if page is None:
        return None
    mark = _WR_OFFERS.search(page)
    if not mark:
        return []  # a locale the tenant does not serve: empty, not an error (:87-94; AGENTS.md:493-494)
    try:
        offers, _end = json.JSONDecoder().raw_decode(page, mark.end())
    except ValueError:
        return None
    if not isinstance(offers, list):
        return None
    jobs: list[Job] = []
    details = 0
    for o in offers[:_LIST_CAP]:
        if not isinstance(o, dict) or not str(o.get("Id") or ""):
            continue
        url, title = _https(o.get("Url"), "careers.werecruit.io"), str(o.get("TitleTranslated") or "").strip()
        if not url or not title:
            continue
        desc = ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            _s, det = _page(http, url)
            rich = _by_class(_dom(det), "description", "div") if det is not None else None
            desc = _node_text(rich) if rich is not None else ""
        place = _commas(o.get("Address_City"), o.get("Address_Region"))
        sched = str(o.get("TimeTranslated") or "").strip().lower().replace("-", " ")
        jobs.append(_job(row, "werecruit", title=title, url=url, location=place, posted=_iso_date(o.get("PublicationStartDate")),
                         description=desc, remote=_remote_from(place),
                         extra={"job_id": str(o["Id"]), "country_code": str(o.get("Address_State") or ""),
                                "employment_type": {"full time": "full_time", "part time": "part_time"}.get(sched, "")}))
    return jobs


# --------------------------------------------------------------------------- Workstream
# Borrowed from freehire internal/ingest/sources/workstream.go:75,256 (GET https://www.workstream.us/j/<board>/positions; the board is
# the eight hex characters of /j/<board>), :106,:213-238 (the page states its own positions listing in "var searchBaseUrl = '...'";
# a single-brand employer is redirected to a brand root, so the stated URL is fetched when it differs; no such variable = not a
# career site), :109,:246-252 (var totalPages = N), :261-267 (later pages: the page query parameter), :90-101 (posting id: the
# path /j/<hex8>/<brand>/<store>/<slug>-<hex8>), :332-382 (cards: class position-card; the first <a> with a posting id gives the
# id and the title; .position-address the street address; .tag the schedule, read only when exactly one; the pay line is the text next
# to the img data-icon="rate-of-pay"), :384-393 (canonical URL: path only, no ?locale), :324-330 (body: .position-rich-text-content; no
# body = skip the posting), :313-319 (the pay line is appended to the body as "Pay: ..."), :290-304 (company = the employer account
# the board names; no posted date), :419-428 (Full-time / Part-time). The origin meters requests per IP and 410 means a retired
# employer (:51-54). Kit differences: at most MAX_PAGES listing pages and WORKSTREAM_DETAIL_CAP posting pages (title_ok gated),
# a posting past the cap keeps its card fields, and the first 403 or 429 ends the board.
WORKSTREAM_DETAIL_CAP = 15
_WS_BASE = "https://www.workstream.us"
_WS_POST = re.compile(r"^/j/[0-9a-f]{8}/[^/]+/[^/]+/[^/]+-([0-9a-f]{8})$")
_WS_SEARCH = re.compile(r"var searchBaseUrl = '([^']+)'")
_WS_PAGES = re.compile(r"var totalPages = (\d+)")


def _ws_scripts(root: _N) -> str:
    return "\n".join(_script_text(n) for n in _walk(root) if n.tag == "script")


def _ws_cards(root: _N) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    for card in _walk(root):
        if "position-card" not in _classes(card):
            continue
        c = {"id": "", "url": "", "title": "", "location": "", "pay": "", "type": ""}
        tags: list[str] = []
        for n in _walk(card):
            if n.tag == "a" and not c["id"]:
                href = n.attrs.get("href", "")
                try:
                    path = urlsplit(href).path
                except ValueError:
                    continue
                pm = _WS_POST.match(path)
                if pm:
                    c["id"], c["url"], c["title"] = pm.group(1), _WS_BASE + path, _inline(n)
            elif "position-address" in _classes(n):
                c["location"] = _inline(n)
            elif "tag" in _classes(n):
                tags.append(_inline(n))
            elif n.tag == "img" and n.attrs.get("data-icon") == "rate-of-pay":
                c["pay"] = _inline(n.parent)
        if c["id"] and c["title"]:
            c["type"] = tags[0] if len(tags) == 1 else ""
            out.append(c)
    return out


def _workstream(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Workstream hourly/frontline ATS (token = employer id): positions listing pages, then capped posting pages."""
    board = str(row.get("token") or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{8}", board):
        return None
    asked = f"{_WS_BASE}/j/{board}/positions"
    st, page = _page(http, asked)
    if page is None:
        return None
    root = _dom(page)
    scripts = _ws_scripts(root)
    sm = _WS_SEARCH.search(scripts)
    if not sm:
        return None  # not a career site
    base = urljoin(_WS_BASE + "/", sm.group(1))
    if _https(base, "www.workstream.us") != base:
        return None
    if base != asked:
        st, page = _page(http, base)
        if page is None:
            return None
        root = _dom(page)
        scripts = _ws_scripts(root)
    pm = _WS_PAGES.search(scripts)
    pages = min(int(pm.group(1)), MAX_PAGES) if pm and 1 <= int(pm.group(1)) <= 200 else MAX_PAGES
    cards: dict[str, dict[str, str]] = {}
    for n in range(1, pages + 1):
        if n > 1:
            u = urlsplit(base)
            q = [(k, v) for k, v in parse_qsl(u.query) if k != "page"] + [("page", str(n))]
            st, page = _page(http, u._replace(query=urlencode(q)).geturl())
            if page is None:
                break
            root = _dom(page)
        found = _ws_cards(root)
        if not found:
            break
        for c in found:
            cards.setdefault(c["id"], c)
    jobs: list[Job] = []
    details = 0
    stop = False
    for c in list(cards.values())[:_LIST_CAP]:
        desc = ""
        if not stop and details < WORKSTREAM_DETAIL_CAP and _title_ok(row, c["title"]):
            details += 1
            st, det = _page(http, c["url"])
            stop = st in (403, 429)
            rich = _by_class(_dom(det), "position-rich-text-content") if det is not None else None
            desc = _node_text(rich) if rich is not None else ""
            if desc and c["pay"]:
                desc += "\n\nPay: " + c["pay"]
        emp = {"Full-time": "full_time", "Part-time": "part_time"}.get(c["type"], "")
        jobs.append(_job(row, "workstream", title=c["title"], url=c["url"], location=c["location"], description=desc,
                         remote=_remote_from(c["location"]), extra={"posting_id": c["id"], "employment_type": emp, "pay": c["pay"]}))
    return jobs


# --------------------------------------------------------------------------- WordPress / Yoast career sites
# Borrowed from freehire internal/ingest/sources/wpyoast.go:37 (GET https://<board>/sitemap.xml, the Yoast sitemap index), :34-43 and
# resolveSubSitemap at sitemap.go:52-65 (the first sub-sitemap whose URL contains "job_listing"; none = no postings, not an
# error), :45 and sitemap.go:35-50 (its <url><loc> entries that are job pages), :95-101 (a job page URL has /job/<digits>-),
# :58-80 (job page: schema.org JobPosting ld+json; title HTML-unescaped, company = hiringOrganization.name else the row's, location =
# one Place "City, Region, Country", description HTML-unescaped, datePosted). Kit differences: capped detail pages gated by
# title_ok on the URL slug, postings past the cap are not listed, and every job link must stay on the board's host.
_WP_JOB = re.compile(r"/job/(\d+)-")


def _wpyoast(row: dict[str, Any], http: Http) -> list[Job] | None:
    """WordPress WP Job Manager + Yoast career site: sitemap index -> job_listing sitemap -> JobPosting per job (capped)."""
    host = str(row.get("token") or "").strip().lower()
    if not _HOST.fullmatch(host):
        return None
    _st, index = _page(http, f"https://{host}/sitemap.xml")
    if index is None:
        return None
    sub = next((u for u in _locs(index, "sitemap") if "job_listing" in u and _https(u, host)), "")
    if not sub:
        return []
    _st, jobmap = _page(http, sub)
    if jobmap is None:
        return None
    jobs: list[Job] = []
    details = 0
    for loc in _locs(jobmap, "url"):
        m = _WP_JOB.search(loc)
        url = _https(loc, host)
        if not m or not url:
            continue
        if details >= DETAIL_CAP:
            break
        if not _title_ok(row, _slug_title(urlsplit(url).path.rsplit("/job/", 1)[-1].strip("/"))):
            continue
        details += 1
        _s, det = _page(http, url)
        p = _ld_posting(_dom(det)) if det is not None else None
        title = html.unescape(str((p or {}).get("title") or "")).strip()
        if not p or not title:
            continue
        org = p.get("hiringOrganization")
        place = ", ".join(_ld_place(p.get("jobLocation"))[:1])
        j = _job(row, "wpyoast", title=title, url=url, location=place, posted=_iso_date(p.get("datePosted")),
                 description=_body(p.get("description")), remote=_remote_from(place), extra={"job_id": m.group(1)})
        if isinstance(org, dict) and str(org.get("name") or "").strip():
            j["company"] = str(org["name"]).strip()
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- careers-page.com
# Borrowed from freehire internal/ingest/sources/careerspage.go:40 (https://<board>.careers-page.com/, server-rendered HTML), :47-49
# (listing pages ?page=N from 1 until a page adds no new job link; a failing first page fails the board), :112 and :116-125 (a job
# link is /jobs/<uuid> and nothing after the id, so the /refer and /apply links of a card are not postings), :65-94 (detail page:
# schema.org JobPosting ld+json; company = hiringOrganization.name else the row's, location = one Place "City, Region,
# Country", description HTML-unescaped, datePosted), :32-37 (the host rate-limits by request volume: two workers). Kit
# differences: at most MAX_PAGES listing pages and a capped number of detail pages gated by title_ok on the link text, postings past
# the cap are not listed, the first 403 or 429 ends the board, and links must stay on the tenant's host.
_CP_JOB = re.compile(r"/jobs/([0-9a-fA-F-]{36})(?:$|[?#])")


def _careerspage(row: dict[str, Any], http: Http) -> list[Job] | None:
    """careers-page.com tenant (<tenant>.careers-page.com): paged listing, then each posting's JobPosting (capped)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _LABEL.fullmatch(tenant):
        return None
    host, base = f"{tenant}.careers-page.com", f"https://{tenant}.careers-page.com/"
    links: dict[str, tuple[str, str]] = {}
    for n in range(1, MAX_PAGES + 1):
        _st, page = _page(http, f"{base}?page={n}")
        if page is None:
            if n == 1:
                return None
            break
        before = len(links)
        for a in _walk(_dom(page)):
            href = a.attrs.get("href", "") if a.tag == "a" else ""
            m = _CP_JOB.search(href) if href and len(href) <= 300 else None
            url = _https(urljoin(base, href), host) if m else ""
            if m and url and url not in links:
                links[url] = (m.group(1), _inline(a))
        if len(links) == before:
            break
    jobs: list[Job] = []
    details = 0
    for url, (uid, anchor) in list(links.items())[:_LIST_CAP]:
        if details >= DETAIL_CAP:
            break
        if not _title_ok(row, anchor):
            continue
        details += 1
        st, det = _page(http, url)
        if st in (403, 429):
            break
        p = _ld_posting(_dom(det)) if det is not None else None
        title = str((p or {}).get("title") or "").strip()
        if not p or not title:
            continue
        org = p.get("hiringOrganization")
        place = ", ".join(_ld_place(p.get("jobLocation"))[:1])
        j = _job(row, "careerspage", title=html.unescape(title), url=url, location=place, posted=_iso_date(p.get("datePosted")),
                 description=_body(p.get("description")), remote=_remote_from(place), extra={"job_id": uid})
        if isinstance(org, dict) and str(org.get("name") or "").strip():
            j["company"] = str(org["name"]).strip()
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- Jobboardly
# Borrowed from freehire internal/ingest/sources/jobboardly.go:29 (GET https://<board>.jobboardly.com/jobs.json, the whole catalogue as
# one unpaginated array with the bodies inline; a board on its own domain redirects), :32,:87-90 (the board is one lowercase DNS
# label), :63-84 (title, arrangement, location = the work arrangement, location_limits, published_at, description.html,
# company.name and company.location, salary{schedule, minimum, maximum}, links.self), :107-131 (a posting without links.self
# or company.name is skipped; the place is company.location else the location limits; the URL is links.self, never application_link),
# :134-146 (links.self must be /jobs/<slug>), :155-206 (arrangement -> fulltime / parttime / contract / internship; salary
# schedule yearly / monthly / daily / hourly, bounds in cents, one currency). A board is a niche job board: company is the
# employer each ad names. Kit difference: at most 1000 postings are kept from one board.
_JB_PERIOD = {"yearly": "year", "monthly": "month", "daily": "day", "hourly": "hour"}
_JB_TYPE = {"fulltime": "full_time", "parttime": "part_time", "contract": "contract", "internship": "internship"}


def _jb_salary(sal: Any) -> dict[str, Any]:
    if not isinstance(sal, dict) or _JB_PERIOD.get(str(sal.get("schedule") or "")) is None:
        return {}
    lo, hi = sal.get("minimum"), sal.get("maximum")
    cur = ""
    out: dict[str, Any] = {}
    for key, m in (("salary_min", lo), ("salary_max", hi)):
        cents = m.get("cents") if isinstance(m, dict) else None
        iso = m.get("currency_iso") if isinstance(m, dict) else None
        if isinstance(cents, (int, float)) and not isinstance(cents, bool):
            try:  # json.loads accepts Infinity, NaN, 1e999 and huge integers: none of them is a salary
                amount = round(cents / 100) if math.isfinite(cents) else None
            except (OverflowError, ValueError):
                amount = None
            if amount is None:
                continue
            c = str(iso or "")
            if cur and c != cur:
                return {}
            cur, out[key] = c, amount
    if not out or not cur:
        return {}
    return {**out, "salary_currency": cur.upper(), "salary_period": _JB_PERIOD[str(sal["schedule"])]}


def _jobboardly(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Jobboardly niche job board (<tenant>.jobboardly.com/jobs.json): one request, bodies inline."""
    board = str(row.get("token") or "").strip().lower()
    if not _LABEL.fullmatch(board):
        return None
    st, data = _get(http, f"https://{board}.jobboardly.com/jobs.json", headers=_JSON)
    if st != 200 or not isinstance(data, list):
        return None
    jobs: list[Job] = []
    for p in data[:_LIST_CAP]:
        if not isinstance(p, dict):
            continue
        links, comp = p.get("links"), p.get("company")
        url = _https(links.get("self") if isinstance(links, dict) else "")
        parts = urlsplit(url).path.rstrip("/").rsplit("/", 1) if url else []
        company = str(comp.get("name") or "").strip() if isinstance(comp, dict) else ""
        title = str(p.get("title") or "").strip()
        if len(parts) != 2 or parts[0] != "/jobs" or not parts[1] or not company or not title:
            continue
        mode = str(p.get("location") or "").strip().lower()
        limits = [str(x) for x in (p.get("location_limits") or []) if x] if isinstance(p.get("location_limits"), list) else []
        place = str(comp.get("location") or "").strip() if isinstance(comp, dict) else ""
        place = place or ", ".join(limits)
        desc = p.get("description")
        j = _job(row, "jobboardly", title=title, url=url, location=place, posted=_iso_date(p.get("published_at")),
                 description=_body(desc.get("html") if isinstance(desc, dict) else ""),
                 remote=True if mode == "remote" else None,
                 extra={"job_id": parts[1], "work_mode": mode if mode in ("remote", "hybrid", "onsite") else "",
                        "employment_type": _JB_TYPE.get(str(p.get("arrangement") or ""), ""), **_jb_salary(p.get("salary"))})
        j["company"] = company
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- JobAppNetwork (talentReef)
# Borrowed from freehire internal/ingest/sources/jobappnetwork.go:30 (POST https://prod-kong.internal.talentreef.com/apply/proxy-es/
# search-en-us/posting/_search, an Elasticsearch proxy the platform's own career pages call without a key or session), :31 (posting
# page https://apply.jobappnetwork.com/clients/<clientId>/posting/<jobId>/), :89-94 (the board is a positive numeric client id),
# :127-140 (body: bool.filter of term clientId and term internalOrExternal = externalOnly, from, size 100 - both filters are
# mandatory: without the client filter the proxy answers every employer on the platform), :100-120 (walk until hits.total postings are
# read or a page is empty; a failing first page fails the board), :49-68 and :142-152 (_source: jobId, title, HTML description,
# address.city / stateOrProvince / country, createdDate; company = the employer the board names). Kit difference: at most MAX_PAGES
# pages (1000 postings). NOT IN REFERENCE: hits.total as an object ({"value": n}); the kit reads it too.
_JAN_URL = "https://prod-kong.internal.talentreef.com/apply/proxy-es/search-en-us/posting/_search"
_JAN_PAGE = 100


def _jobappnetwork(row: dict[str, Any], http: Http) -> list[Job] | None:
    """JobAppNetwork / talentReef career sites (token = numeric client id): the keyless search proxy, one client only."""
    tok = str(row.get("token") or "").strip()
    if not re.fullmatch(r"[0-9]{1,12}", tok) or int(tok) <= 0:
        return None
    cid = int(tok)
    jobs: list[Job] = []
    got = 0
    for page in range(MAX_PAGES):
        body = {"query": {"bool": {"filter": [{"term": {"clientId": cid}}, {"term": {"internalOrExternal": "externalOnly"}}]}},
                "from": page * _JAN_PAGE, "size": _JAN_PAGE}
        st, data = _get(http, _JAN_URL, method="POST", data=json.dumps(body).encode(), headers=_POST)
        hits = data.get("hits") if isinstance(data, dict) else None
        rows = hits.get("hits") if isinstance(hits, dict) else None
        if st != 200 or not isinstance(rows, list):
            if page:
                break
            return None
        if not rows:
            break
        for h in rows:
            s = h.get("_source") if isinstance(h, dict) else None
            if not isinstance(s, dict) or not isinstance(s.get("jobId"), int) or isinstance(s.get("jobId"), bool):
                continue
            title = str(s.get("title") or "").strip()
            if not title:
                continue
            a: dict[str, Any] = s["address"] if isinstance(s.get("address"), dict) else {}
            place = _commas(a.get("city"), a.get("stateOrProvince"), a.get("country"))
            jobs.append(_job(row, "jobappnetwork", title=title, url=f"https://apply.jobappnetwork.com/clients/{cid}/posting/{s['jobId']}/",
                             location=place, posted=_iso_date(s.get("createdDate")), description=_body(s.get("description")),
                             remote=_remote_from(place), extra={"job_id": str(s["jobId"])}))
        got += len(rows)
        total = hits.get("total") if isinstance(hits, dict) else None
        total = total.get("value") if isinstance(total, dict) else total
        if isinstance(total, int) and got >= total:
            break
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "opencats": _wrap(_opencats),
    "odoo": _wrap(_odoo),
    "recruitingsolutions": _wrap(_recruitingsolutions),
    "teamex": _wrap(_teamex),
    "wantapply": _wrap(_wantapply),
    "werecruit": _wrap(_werecruit),
    "workstream": _wrap(_workstream),
    "wpyoast": _wrap(_wpyoast),
    "careerspage": _wrap(_careerspage),
    "jobboardly": _wrap(_jobboardly),
    "jobappnetwork": _wrap(_jobappnetwork),
}
