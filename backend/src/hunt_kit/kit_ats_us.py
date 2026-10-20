"""US mid-market ATS board fetchers for the hunt kit: Bullhorn, CareerPlug, Hireology, HiringThing (HRM Direct, iSolved Hire,
Paycom, Paycor, JobScore and RecruiterFlow follow in a later slice). Same contract as kit_ats.py: ``fetch(row, http, stats) -> list[Job]``,
never raises, network only through the ``http`` callable, no file IO at import, standard library only. Nothing here
ranks or scores a job.

Board tokens (companies_ats_us.json ``board`` / ``--companies-extra "ats:token"``):
  bullhorn     "<cls>:<corpToken>" (cls = the numeric cluster in public-rest<cls>.bullhornstaffing.com)
  careerplug   subdomain from <board>.careerplug.com
  hireology    slug from careers.hireology.com/<slug>
  hiringthing  the full careers HOST, e.g. skijapan.hiringthing.com or <tenant>.prismhr-hire.com (white-labelled)

Endpoints and field names are adapted from the MIT-licensed freehire adapters (internal/ingest/sources/<name>.go, see
NOTICE.md); each reader cites its file:line. Anything the reference does not state is marked NOT IN REFERENCE. Pages are read
with ``html.parser`` into a small tree; no reader here runs a regex over a whole page.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterator
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
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
    _count, _get, _job, _remote_from, _text, _title_ok = _mod._count, _mod._get, _mod._job, _mod._remote_from, _mod._text, _mod._title_ok

_JSON = {"Accept": "application/json"}
_HTML = {"Accept": "text/html"}
DETAIL_CAP = 25  # detail-page requests per board (the rest keep the listing fields; each reader's note says what that loses)
_CLEAN_CAP = 200_000  # characters of a description that _clean looks at (hostile pages cannot make it slow)
_ANCHOR_CAP = 5000  # anchors read per page
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,78}[A-Za-z0-9])?")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_GENERIC = {"apply", "apply now", "learn more", "view", "view job", "view details", "details", "read more", "more"}


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


def _drop_blocks(text: str) -> str:
    """``text`` without <script>/<style> blocks, in one linear pass. An unclosed opener drops the rest."""
    low = text.lower()
    out: list[str] = []
    pos = 0
    while True:
        starts = []
        for tag in ("script", "style"):
            i = low.find("<" + tag, pos)
            while i != -1 and not (low[i + len(tag) + 1:i + len(tag) + 2] in (">", "/") or low[i + len(tag) + 1:i + len(tag) + 2].isspace()):
                i = low.find("<" + tag, i + 1)
            if i != -1:
                starts.append((i, tag))
        if not starts:
            out.append(text[pos:])
            return "".join(out)
        i, tag = min(starts)
        out.append(text[pos:i])
        j = low.find("</" + tag, i)
        while j != -1 and not (low[j + len(tag) + 2:j + len(tag) + 3] in (">", "/") or low[j + len(tag) + 2:j + len(tag) + 3].isspace()):
            j = low.find("</" + tag, j + 1)
        if j == -1:
            return "".join(out)
        k = low.find(">", j)
        if k == -1:
            return "".join(out)
        pos = k + 1


def _clean(raw: Any) -> str:
    """Plain text of an HTML description: entities decoded once, script/style dropped, tags removed."""
    return str(_text(_drop_blocks(html.unescape(str(raw or "")[:_CLEAN_CAP]))))


def _page(http: Http, url: str) -> str | None:
    """The body of a 200 text response, else None."""
    st, body = _get(http, url, as_json=False, headers=_HTML)
    return body if st == 200 and isinstance(body, str) else None


def _https(url: Any, host: str | None = None) -> str:
    """The URL when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p.geturl() if ok else ""


def _day(value: Any) -> str:
    """First 10 characters of an ISO date or datetime when they form a date, else ''."""
    s = str(value or "").strip()[:10]
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return ""
    return s


def _commas(*parts: Any) -> str:
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


# --------------------------------------------------------------------------- a small HTML tree
_VOID = {"br", "hr", "img", "input", "meta", "link", "area", "base", "col", "embed", "source", "track", "wbr", "param"}


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.kids: list[Any] = []  # _Node | str

    def classes(self) -> set[str]:
        return set(self.attrs.get("class", "").split())


class _Tree(HTMLParser):
    """Builds a _Node tree and collects schema.org ld+json blocks. Tolerates unclosed and stray tags."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root
        self.ld: list[str] = []
        self._ld: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "script" and "ld+json" in a.get("type", "").lower():
            self._ld = []
        if tag == "a":  # HTML: a start <a> closes an open <a> (keeps unclosed anchors from nesting)
            o: _Node | None = self.cur
            for _ in range(64):  # bounded walk: a hostile page cannot make every <a> climb a deep chain
                if o is None or o.tag == "a":
                    break
                o = o.parent
            if o is not None and o.tag != "a":
                o = None
            if o is not None and o.parent is not None:
                self.cur = o.parent
        n = _Node(tag, a, self.cur)
        self.cur.kids.append(n)
        if tag not in _VOID:
            self.cur = n

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag == "script" and self._ld is not None:
            self.ld.append("".join(self._ld))
            self._ld = None
        n: _Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data: str) -> None:
        if self._ld is not None:
            self._ld.append(data)
        elif self.cur.tag not in ("script", "style"):
            self.cur.kids.append(data)


def _parse(page: str) -> _Tree:
    t = _Tree()
    t.feed(page)
    t.close()
    return t


def _walk(root: _Node) -> Iterator[tuple[str, Any]]:
    """Document-order events ("open", node) / ("text", str) / ("close", node); iterative, so deep nesting is harmless."""
    stack: list[tuple[_Node, int]] = [(root, 0)]
    yield "open", root
    while stack:
        node, i = stack.pop()
        if i < len(node.kids):
            stack.append((node, i + 1))
            kid = node.kids[i]
            if isinstance(kid, str):
                yield "text", kid
            else:
                yield "open", kid
                stack.append((kid, 0))
        elif node.tag not in _VOID:
            yield "close", node


def _nodes(root: _Node) -> Iterator[_Node]:
    return (x for ev, x in _walk(root) if ev == "open")


def _first(root: _Node, pred: Callable[[_Node], bool]) -> _Node | None:
    return next((n for n in _nodes(root) if pred(n)), None)


def _plain(node: _Node) -> str:
    """Whitespace-collapsed text content of a node."""
    return " ".join("".join(x for ev, x in _walk(node) if ev == "text").split())


def _anchors(root: _Node) -> Iterator[tuple[str, str]]:
    seen = 0
    for n in _nodes(root):
        if n.tag == "a" and n.attrs.get("href"):
            seen += 1
            if seen > _ANCHOR_CAP:
                return
            yield n.attrs["href"], _plain(n)


def _ld_posting(tree: _Tree) -> dict[str, Any] | None:
    """The first schema.org JobPosting object in the page's ld+json blocks (also inside a list or an @graph)."""
    for raw in tree.ld:
        try:
            data = json.loads(raw)
        except ValueError:
            continue
        queue = [data]
        while queue:
            x = queue.pop(0)
            if isinstance(x, list):
                queue.extend(x)
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return x
                if isinstance(x.get("@graph"), list):
                    queue.extend(x["@graph"])
    return None


def _ld_place(p: dict[str, Any]) -> str:
    """"city, region, country" from a JobPosting's jobLocation (one Place or a list of them)."""
    loc = p.get("jobLocation")
    if isinstance(loc, list):
        loc = loc[0] if loc else None
    addr = loc.get("address") if isinstance(loc, dict) else None
    if not isinstance(addr, dict):
        return ""
    country = addr.get("addressCountry")
    if isinstance(country, dict):
        country = country.get("name")
    return _commas(addr.get("addressLocality"), addr.get("addressRegion"), country)


def _listing_title(texts: list[str]) -> str:
    """The first anchor text that is not a generic button label ('' if none)."""
    return next((t for t in texts if t and t.lower() not in _GENERIC), "")


# --------------------------------------------------------------------------- Bullhorn
# Borrowed from freehire internal/ingest/sources/bullhorn.go: board "<cls>:<corpToken>" (:15-17, :91-97); GET
# https://public-rest<cls>.bullhornstaffing.com/rest-services/<corpToken>/search/JobOrder (:99) with query=(isOpen:1),
# fields=id,title,publicDescription,address(city,state,countryName),dateAdded,employmentType (:41, :105-106), sort=-dateAdded
# (:107), count=500 (:32, :108), start=<offset> (:109); envelope {total, start, count, data[]} (:51-56), stop on an empty page or
# start+len>=total (:120); a JobOrder with no id is dropped (:131); location = "City, State", else the country (:77-88); dateAdded is
# epoch milliseconds (:63); the API has no human job page, so the url is the REST entity reference
# https://public-rest<cls>.bullhornstaffing.com/rest-services/<corpToken>/entity/JobOrder/<id> (:22-24, :136); the company is the
# configured staffing agency, the API names no employer (:12-13, :138). Page cap is the kit's own (MAX_PAGES; reference 40, :35);
# a failure after page 0 keeps what was read.
_BH_FIELDS = "id,title,publicDescription,address(city,state,countryName),dateAdded,employmentType"


def _bullhorn(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Bullhorn public REST search/JobOrder (500 per page); the url is the REST entity, Bullhorn has no job page."""
    cls, _, corp = str(row.get("token") or "").partition(":")
    if not (re.fullmatch(r"\d{1,4}", cls) and re.fullmatch(r"[A-Za-z0-9]{1,64}", corp)):
        return None
    base = f"https://public-rest{cls}.bullhornstaffing.com/rest-services/{corp}"
    jobs: list[Job] = []
    for page in range(MAX_PAGES):
        start = page * 500
        q = f"query={quote('(isOpen:1)')}&fields={quote(_BH_FIELDS)}&sort=-dateAdded&count=500&start={start}"
        st, data = _get(http, f"{base}/search/JobOrder?{q}", headers=_JSON)
        rows = data.get("data") if isinstance(data, dict) else None
        if st != 200 or not isinstance(rows, list):
            if page:
                break
            return None
        for o in rows:
            if not isinstance(o, dict) or not o.get("id"):
                continue
            addr = o.get("address")
            a: dict[str, Any] = addr if isinstance(addr, dict) else {}
            place = _commas(a.get("city"), a.get("state")) or str(a.get("countryName") or "").strip()
            ms = o.get("dateAdded")
            posted = ""
            if isinstance(ms, (int, float)) and not isinstance(ms, bool) and ms > 0:
                try:
                    posted = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date().isoformat()
                except (OverflowError, OSError, ValueError):
                    posted = ""
            jobs.append(_job(row, "bullhorn", title=str(o.get("title") or ""), url=f"{base}/entity/JobOrder/{o['id']}",
                             location=place, posted=posted, description=_clean(o.get("publicDescription")),
                             remote=_remote_from(place), extra={"employment_type": str(o.get("employmentType") or "")}))
        total = data.get("total") if isinstance(data, dict) else None
        if not rows or (isinstance(total, int) and start + len(rows) >= total):
            break
    return [j for j in jobs if j["title"]]


# --------------------------------------------------------------------------- CareerPlug
# Borrowed from freehire internal/ingest/sources/careerplug.go: board = account subdomain, host <board>.careerplug.com (:37); listing
# https://<host>/jobs, then /jobs?page=N (:50-55) until a page adds no new /jobs/<id> link (:45-49; id regex :106); each posting is
# read from its page's schema.org JobPosting ld+json (:71-102): title, description (HTML-unescaped), datePosted (:99),
# hiringOrganization.name preferred over the configured company (:93), jobLocation.address (:121-134); remote only from the location
# text (:96-98). Page cap is the kit's own (MAX_PAGES; reference 100, :34). NOT IN REFERENCE: a posting past DETAIL_CAP (or whose title
# the user does not search) keeps the listing link text as its title, with no description.
_CP_ID = re.compile(r"/jobs/(\d+)")


def _careerplug(row: dict[str, Any], http: Http) -> list[Job] | None:
    """CareerPlug career site (<board>.careerplug.com): paged listing, schema.org JobPosting on each posting page."""
    board = str(row.get("token") or "").lower()
    if not _LABEL.fullmatch(board):
        return None
    host = f"{board}.careerplug.com"
    order: list[str] = []
    titles: dict[str, list[str]] = {}
    for page in range(1, MAX_PAGES + 1):
        body = _page(http, f"https://{host}/jobs" + (f"?page={page}" if page > 1 else ""))
        if body is None:
            if page > 1:
                break
            return None
        new = 0
        for href, text in _anchors(_parse(body).root):
            u = urlsplit(urljoin(f"https://{host}/", href))
            m = _CP_ID.search(u.path)
            if not m or (u.hostname or "").lower() != host:
                continue
            if m.group(1) not in titles:
                titles[m.group(1)] = []
                order.append(m.group(1))
                new += 1
            titles[m.group(1)].append(text)
        if not new:
            break
    jobs: list[Job] = []
    details = 0
    for jid in order:
        url = f"https://{host}/jobs/{jid}"
        title, company, place, posted, desc = _listing_title(titles[jid]), "", "", "", ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dp = _page(http, url)
            ld = _ld_posting(_parse(dp)) if dp else None
            if ld:
                title = str(ld.get("title") or title)
                org = ld.get("hiringOrganization")
                company = str(org.get("name") or "") if isinstance(org, dict) else ""
                place, posted, desc = _ld_place(ld), _day(ld.get("datePosted")), _clean(ld.get("description"))
        if title:
            j = _job(row, "careerplug", title=title, url=url, location=place, posted=posted, description=desc, remote=_remote_from(place))
            if company:
                j["company"] = company
            jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- Hireology
# Borrowed from freehire internal/ingest/sources/hireology.go: board = tenant slug (:9); one call to
# https://api.hireology.com/v1/careers/<slug> (:17, :31), JSON:API {data:[{id, attributes}]} (:26-29); attributes name, remote,
# job-description (HTML inline, no detail fetch), locations[] (strings), status, career-site-url (:62-69); only status "Open" with an id
# is kept (:38); url = career-site-url else https://careers.hireology.com/<slug>/<id>/description (:44); remote = the remote flag or
# "remote" in the location (:49); company is the configured one (:46).
def _hireology(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Hireology public JSON:API (api.hireology.com/v1/careers/<slug>), description inline."""
    slug = str(row.get("token") or "").lower()
    if not _LABEL.fullmatch(slug):
        return None
    st, data = _get(http, f"https://api.hireology.com/v1/careers/{quote(slug, safe='')}", headers=_JSON)
    items = data.get("data") if isinstance(data, dict) else None
    if st != 200 or not isinstance(items, list):
        return None
    jobs: list[Job] = []
    for d in items:
        a = d.get("attributes") if isinstance(d, dict) else None
        if not isinstance(a, dict) or not d.get("id") or str(a.get("status") or "").lower() != "open":
            continue
        raw_locs = a.get("locations")
        locs: list[Any] = raw_locs if isinstance(raw_locs, list) else []
        place = ", ".join(str(x).strip() for x in locs if x and str(x).strip())
        url = _https(a.get("career-site-url")) or f"https://careers.hireology.com/{slug}/{d['id']}/description"
        title = str(a.get("name") or "").strip()
        if title:
            jobs.append(_job(row, "hireology", title=title, url=url, location=place, description=_clean(a.get("job-description")),
                             remote=True if a.get("remote") is True else _remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- HiringThing
# Borrowed from freehire internal/ingest/sources/hiringthing.go: board = the full careers HOST, white-labelled under the vendor's and
# resellers' domains (:15-22, :65); the whole board is one listing page at the site root (:24-27, :69); a posting link is
# /job/<id>[/<slug>] matched on the path (:154-174), de-duplicated by id, links off the board's host dropped (:91-114, host check
# :104); each posting page inlines the job record as JSON in data-react-props of the element whose data-react-class is
# "HiringThing.Components.ApplyButtonGroup" (:176-180, :275-290), envelope {jobObj:{table:{title, html_description, posted_at,
# location, location_info.country, remote}}} (:182-200, :257-273); location is kept verbatim (:135-137); remote is the record's own
# flag, not the location text (:139-143). Salary fields (:202-255) are not part of the kit's job contract. NOT IN REFERENCE: a posting
# past DETAIL_CAP (or not searched) keeps the listing link text as its title, with no description.
_HT_CLASS = "HiringThing.Components.ApplyButtonGroup"
_HT_ID = re.compile(r"^/job/(\d+)(?:/|$)")


def _ht_record(root: _Node) -> dict[str, Any] | None:
    n = _first(root, lambda x: x.attrs.get("data-react-class") == _HT_CLASS)
    try:
        env = json.loads(n.attrs.get("data-react-props", "")) if n else None
    except ValueError:
        return None
    obj = env.get("jobObj") if isinstance(env, dict) else None
    table = obj.get("table") if isinstance(obj, dict) else None
    return table if isinstance(table, dict) else None


def _hiringthing(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HiringThing career site (any white-label host): one listing page, job record in each posting page."""
    host = str(row.get("token") or "").strip().lower()
    if not _HOST.fullmatch(host) or len(host) > 120:
        return None
    body = _page(http, f"https://{host}/")
    if body is None:
        return None
    order: list[str] = []
    titles: dict[str, list[str]] = {}
    urls: dict[str, str] = {}
    for href, text in _anchors(_parse(body).root):
        u = urlsplit(urljoin(f"https://{host}/", href))
        m = _HT_ID.match(u.path)
        if not m or (u.hostname or "").lower() != host:
            continue
        jid = m.group(1)
        if jid not in titles:
            titles[jid], urls[jid] = [], f"https://{host}{u.path}"
            order.append(jid)
        titles[jid].append(text)
    jobs: list[Job] = []
    details = 0
    for jid in order:
        title, place, posted, desc, remote, country = _listing_title(titles[jid]), "", "", "", None, ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dp = _page(http, urls[jid])
            rec = _ht_record(_parse(dp).root) if dp else None
            if rec:
                title = str(rec.get("title") or title)
                place, posted, desc = str(rec.get("location") or "").strip(), _day(rec.get("posted_at")), _clean(rec.get("html_description"))
                remote = True if rec.get("remote") is True else None
                info = rec.get("location_info")
                country = str(info.get("country") or "") if isinstance(info, dict) else ""
        if title:
            jobs.append(_job(row, "hiringthing", title=title, url=urls[jid], location=place, posted=posted, description=desc,
                             remote=remote, extra={"country": country}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "bullhorn": _wrap(_bullhorn),
    "careerplug": _wrap(_careerplug),
    "hireology": _wrap(_hireology),
    "hiringthing": _wrap(_hiringthing),
}
