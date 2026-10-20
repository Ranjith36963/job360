"""Four more ATS readers for the hunt kit: OpenCATS, Odoo, recruiting-solutions.org and TeamEx. Same contract as
kit_ats.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at
import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies JSON ``board``, or ``--companies-extra "ats:token"``):
  opencats             portal root: host or host/path, e.g. atscareers.g4s.com or careers.boomit.pt/careers
  odoo                 careers host of an Odoo recruitment site, e.g. jobs.amewaregroup.com
  recruitingsolutions  platform tenant key (the customerId header), e.g. riv-prod
  teamex               none (one global marketplace feed): any placeholder token works

Endpoints, params and field names are adapted from the MIT-licensed freehire adapters (internal/ingest/sources/<name>.go,
see NOTICE.md). Where the kit differs from the reference it says so next to the reader: the kit never fans out, so every
detail-page reader has its own small request cap (``*_DETAIL_CAP``) and a ``title_ok`` gate (kit_ats._title_ok).
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
from html.parser import HTMLParser
from typing import Any, Callable, Iterator
from urllib.parse import urljoin, urlsplit

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

_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_HTMLH = {"Accept": "text/html"}
_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")
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

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = _N(tag, {k: v or "" for k, v in attrs}, self._stack[-1])
        self._stack[-1].kids.append(n)
        if tag not in _VOID:
            self._stack.append(n)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].kids.append(_N(tag, {k: v or "" for k, v in attrs}, self._stack[-1]))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
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


def _node_text(root: _N) -> str:
    """Plain text of a subtree: block tags break lines, script/style are dropped (the references sanitise the body)."""
    out: list[str] = []
    stack: list[Any] = [root]
    while stack:
        x = stack.pop()
        if isinstance(x, str):
            out.append(x)
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


def _inline(n: _N | None) -> str:
    """The text of an element with every run of whitespace collapsed to one space."""
    return " ".join(_node_text(n).split()) if n is not None else ""


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
        if n.tag in ("td", "th") and "location" in _inline(n).lower():
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
            if url:
                links.setdefault(url, _inline(n))
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
        best = max(bodies, key=lambda b: len(_node_text(b)))
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


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "opencats": _wrap(_opencats),
    "odoo": _wrap(_odoo),
    "recruitingsolutions": _wrap(_recruitingsolutions),
    "teamex": _wrap(_teamex),
}
