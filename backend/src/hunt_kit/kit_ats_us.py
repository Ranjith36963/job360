"""US mid-market ATS board fetchers for the hunt kit: Bullhorn, CareerPlug, Hireology, HiringThing, HRM Direct,
iSolved Hire, Paycom, Paycor, JobScore, RecruiterFlow. Same contract as kit_ats.py: ``fetch(row, http, stats) -> list[Job]``,
never raises, network only through the ``http`` callable, no file IO at import, standard library only. Nothing here
ranks or scores a job.

Board tokens (companies_ats_us.json ``board`` / ``--companies-extra "ats:token"``):
  bullhorn     "<cls>:<corpToken>" (cls = the numeric cluster in public-rest<cls>.bullhornstaffing.com)
  careerplug   subdomain from <board>.careerplug.com
  hireology    slug from careers.hireology.com/<slug>
  hiringthing  the full careers HOST, e.g. skijapan.hiringthing.com or <tenant>.prismhr-hire.com (white-labelled)
  hrmdirect    subdomain from <board>.hrmdirect.com
  isolvedhire  subdomain from <board>.isolvedhire.com
  paycom       32-character client key from paycomonline.net/v4/ats/web.php/portal/<key>/...
  paycor       32-character clientId from recruitingbypaycor.com/career/CareerHome.action?clientId=<id>
  jobscore     company shortcode from careers.jobscore.com/careers/<slug>
  recruiterflow  path segment from recruiterflow.com/<board>/jobs (a board is a recruiting agency)

Endpoints and field names are adapted from the MIT-licensed freehire adapters (internal/ingest/sources/<name>.go, see
NOTICE.md); each reader cites its file:line. Anything the reference does not state is marked NOT IN REFERENCE. Pages are read
with ``html.parser`` into a small tree; a regex runs over a whole page only where the reference itself does (Paycom, RecruiterFlow).
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from itertools import islice
from typing import Any, Callable, Iterator
from urllib.parse import parse_qs, quote, urljoin, urlsplit

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
_ANCHOR_TEXT_CAP = 2000  # characters of text read per anchor
ID_ONLY_CAP = 40  # iSolved Hire and Paycom list ids only: a posting needs its detail request to have a title, so cap those
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,78}[A-Za-z0-9])?")
_HOST = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+")
_KEY32 = re.compile(r"[A-Za-z0-9]{32}")
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


def _clean(raw: Any, escaped: bool = False) -> str:
    """Plain text of an HTML description: entities decoded once, script/style dropped, tags removed.

    ``escaped`` is for a description whose markup itself arrives entity-encoded (CareerPlug ld+json); only then is it
    unescaped before the tags are stripped. Otherwise a literal ``&lt;5 years`` must survive the tag stripper.
    """
    text = str(raw or "")[:_CLEAN_CAP]
    return str(_text(_drop_blocks(html.unescape(text) if escaped else text)))


def _split(host: str, href: str) -> Any:
    """urlsplit of ``href`` resolved against the board host, or None when the href is malformed (e.g. ``https://[::1/x``)."""
    try:
        return urlsplit(urljoin(f"https://{host}/", href))
    except ValueError:
        return None


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
        self._open: dict[str, int] = {}  # open-element count per tag: a stray end tag returns at once instead of climbing

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
                self._close_to(o)
        n = _Node(tag, a, self.cur)
        self.cur.kids.append(n)
        if tag not in _VOID:
            self.cur = n
            self._open[tag] = self._open.get(tag, 0) + 1

    def _close_to(self, target: _Node) -> None:
        """Close open elements up to and including ``target`` (each element closes once, so this is linear overall)."""
        while self.cur is not self.root:
            n = self.cur
            self.cur = n.parent or self.root
            self._open[n.tag] -= 1
            if n is target:
                return

    def handle_endtag(self, tag: str) -> None:
        if tag in _VOID:
            return
        if tag == "script" and self._ld is not None:
            self.ld.append("".join(self._ld))
            self._ld = None
        if self._open.get(tag, 0) <= 0:
            return
        n: _Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self._close_to(n)

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


def _by_class(root: _Node, cls: str, tag: str | None = None) -> _Node | None:
    return _first(root, lambda n: (tag is None or n.tag == tag) and cls in n.classes())


def _by_id(root: _Node, ident: str) -> _Node | None:
    return _first(root, lambda n: n.attrs.get("id") == ident)


def _inner_html(node: _Node) -> str:
    """Re-serialised children (tags without attributes, text escaped): enough for ``_text``."""
    out: list[str] = []
    for ev, x in _walk(node):
        if x is node:
            continue
        if ev == "text":
            out.append(html.escape(x, quote=False))
        else:
            out.append(f"<{x.tag}>" if ev == "open" else f"</{x.tag}>")
    return "".join(out)


def _plain(node: _Node) -> str:
    """Whitespace-collapsed text content of a node."""
    return " ".join("".join(x for ev, x in _walk(node) if ev == "text").split())


def _anchors(root: _Node) -> Iterator[tuple[str, str]]:
    """(href, text) of each <a href> in document order, in one pass: text goes to the innermost open anchor and folds into
    its parent anchor on close, each buffer capped, so deeply nested anchors cannot make every anchor re-walk its subtree."""
    out: list[list[Any]] = []
    stack: list[list[Any]] = []  # [slot in out or -1 for a non-href anchor, text parts, characters held]

    def add(top: list[Any], text: str) -> None:
        if top[2] < _ANCHOR_TEXT_CAP:
            text = text[:_ANCHOR_TEXT_CAP]
            top[1].append(text)
            top[2] += len(text)

    for ev, x in _walk(root):
        if ev == "open" and x.tag == "a":
            slot = -1
            if x.attrs.get("href") and len(out) < _ANCHOR_CAP:
                slot = len(out)
                out.append([x.attrs["href"], ""])
            stack.append([slot, [], 0])
        elif ev == "text" and stack:
            add(stack[-1], x)
        elif ev == "close" and x.tag == "a" and stack:
            slot, parts, _ = stack.pop()
            text = "".join(parts)[:_ANCHOR_TEXT_CAP]
            if slot >= 0:
                out[slot][1] = " ".join(text.split())
            if stack:
                add(stack[-1], text)
    return iter([(h, t) for h, t in out])


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
            u = _split(host, href)
            m = _CP_ID.search(u.path) if u else None
            if u is None or not m or (u.hostname or "").lower() != host:
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
                place, posted, desc = _ld_place(ld), _day(ld.get("datePosted")), _clean(ld.get("description"), escaped=True)
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
        url = _https(a.get("career-site-url"), "careers.hireology.com") or f"https://careers.hireology.com/{slug}/{d['id']}/description"
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
        u = _split(host, href)
        m = _HT_ID.match(u.path) if u else None
        if u is None or not m or (u.hostname or "").lower() != host:
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


# --------------------------------------------------------------------------- HRM Direct
# Borrowed from freehire internal/ingest/sources/hrmdirect.go: board = tenant subdomain, host <board>.hrmdirect.com (:15-16, :124); the
# listing is https://<host>/employment/job-openings.php?search=true (:128), which renders the WHOLE board in one page, no pagination
# (:18-24); a posting is an <a> to job-opening.php whose query carries numeric req and req_loc (:231-242, regex :223), keyed by the
# pair req-req_loc (:150), url rebuilt as .../employment/job-opening.php?req=<req>&req_loc=<loc> (:162), the link text is the title
# (:161); the job page is read inside div.reqResult: h2 title, td.viewFieldName / td.viewFieldValue pairs for "Location" with the ":"
# stripped, body in div.jobDesc (:194-211, :248-274); Location is "city, state" with empty parts dropped (:279-287); remote from the
# location text only (:204); no publish date on the page (:216). Pages are windows-1252 (:176-183): the kit's http decodes utf-8, then
# cp1252. NOT IN REFERENCE: a posting past DETAIL_CAP (or not searched) keeps the listing title, with no location or description.
def _hrm_field(posting: _Node, label: str) -> str:
    """The td.viewFieldValue that follows the td.viewFieldName whose text is ``label:``."""
    for n in islice((x for x in _nodes(posting) if x.tag == "td" and "viewFieldName" in x.classes()), _ANCHOR_CAP):
        if _plain(n).rstrip(":").strip() == label and n.parent:
            sibs = [k for k in n.parent.kids if isinstance(k, _Node)]
            i = sibs.index(n) + 1
            return _plain(sibs[i]) if i < len(sibs) and "viewFieldValue" in sibs[i].classes() else ""
    return ""


def _hrmdirect(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HRM Direct career site (<board>.hrmdirect.com): one 'all openings' listing page, detail page per posting."""
    board = str(row.get("token") or "").lower()
    if not _LABEL.fullmatch(board):
        return None
    host = f"{board}.hrmdirect.com"
    base = f"https://{host}/employment/"
    body = _page(http, base + "job-openings.php?search=true")
    if body is None:
        return None
    order: list[tuple[str, str]] = []
    titles: dict[tuple[str, str], list[str]] = {}
    for href, text in _anchors(_parse(body).root):
        u = _split(host, href)
        if u is None:
            continue
        if (u.hostname or "").lower() != host or u.path.rsplit("/", 1)[-1] != "job-opening.php":
            continue
        q = parse_qs(u.query)
        key = ((q.get("req") or [""])[0], (q.get("req_loc") or [""])[0])
        if not (key[0].isdigit() and key[1].isdigit()):
            continue
        if key not in titles:
            titles[key] = []
            order.append(key)
        titles[key].append(text)
    jobs: list[Job] = []
    details = 0
    for key in order:
        url = f"{base}job-opening.php?req={key[0]}&req_loc={key[1]}"
        title, place, desc = _listing_title(titles[key]), "", ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dp = _page(http, url)
            posting = _by_class(_parse(dp).root, "reqResult") if dp else None
            h2 = _first(posting, lambda n: n.tag == "h2") if posting else None
            if posting is not None and h2 is not None and _plain(h2):
                title = _plain(h2)
                place = _commas(*_hrm_field(posting, "Location").split(","))
                d = _by_class(posting, "jobDesc", "div")
                desc = _text(_inner_html(d)) if d is not None else ""
        if title:
            jobs.append(_job(row, "hrmdirect", title=title, url=url, location=place, description=desc, remote=_remote_from(place),
                             extra={"req": key[0], "req_loc": key[1]}))
    return jobs


# --------------------------------------------------------------------------- iSolved Hire
# Borrowed from freehire internal/ingest/sources/isolvedhire.go: board = tenant subdomain, host <board>.isolvedhire.com (:15-17, :36);
# postings are enumerated from https://<host>/sitemap.xml (:87), the /jobs/<id> <loc> entries, id = digits, /jobs/<id> and
# /jobs/<id>.html de-duplicated (:46-49, :125-130); a sitemap whose first 512 bytes hold "typed the url for this website incorrectly"
# or "career site has been disabled" is a board the platform no longer serves (:51-84, :98-106); each posting is read from
# https://<host>/jobs/<id> schema.org JobPosting ld+json (:144-173): title, description (HTML-unescaped), datePosted ("2006-01-02
# 15:04:05", date part used, :157-162), hiringOrganization.name, jobLocation.address; company = the configured one first (:168); remote
# from the location text (:171). The reference's second host family, applicantpro.com (:39-42), is not read here. NOT IN REFERENCE: the
# sitemap carries no titles, so only the first ID_ONLY_CAP postings get their detail request.
_ISO_LOC = re.compile(r"<loc>([^<]*)</loc>", re.I)
_ISO_ID = re.compile(r"/jobs/(\d+)")
_ISO_GONE = ("typed the url for this website incorrectly", "career site has been disabled")


def _isolvedhire(row: dict[str, Any], http: Http) -> list[Job] | None:
    """iSolved Hire career site (<board>.isolvedhire.com): ids from sitemap.xml, schema.org JobPosting per posting."""
    board = str(row.get("token") or "").lower()
    if not _LABEL.fullmatch(board):
        return None
    host = f"{board}.isolvedhire.com"
    st, sm = _get(http, f"https://{host}/sitemap.xml", as_json=False, headers={"Accept": "application/xml"})
    if st != 200 or not isinstance(sm, str) or any(n in sm[:512].lower() for n in _ISO_GONE):
        return None
    ids: list[str] = []
    seen_ids: set[str] = set()
    for loc in _ISO_LOC.findall(sm):
        m = _ISO_ID.search(urlsplit(html.unescape(loc.strip())).path)
        if m and m.group(1) not in seen_ids:
            seen_ids.add(m.group(1))
            ids.append(m.group(1))
    jobs: list[Job] = []
    for jid in ids[:ID_ONLY_CAP]:
        url = f"https://{host}/jobs/{jid}"
        dp = _page(http, url)
        ld = _ld_posting(_parse(dp)) if dp else None
        if not ld or not str(ld.get("title") or "").strip():
            continue
        place = _ld_place(ld)
        jobs.append(_job(row, "isolvedhire", title=str(ld["title"]), url=url, location=place, posted=_day(ld.get("datePosted")),
                         description=_clean(ld.get("description"), escaped=True), remote=_remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- Paycom
# Borrowed from freehire internal/ingest/sources/paycom.go: board = 32-hex client key (:22-29, :53); GET
# https://www.paycomonline.net/v4/ats/web.php/portal/<key>/jobs/1 (:40, :53) whose shell embeds the per-portal session JWT
# ("sessionJWT":"...", :149-155, "not a secret key", :149) and the regional host portal-applicant-tracking.<region>.paycomonline.net
# (:157-166); a page containing "Job board does not exist." is a retired key (:44-47, :60); with headers Authorization: <jwt> (no
# "Bearer") and Locale: en-US (:68), GET <mantle>/api/ats/company-name -> companyName (:70-76); POST
# <mantle>/api/ats/job-posting-previews/search with {skip, take:50 (:41), filtersForQuery:{distanceFrom:0, workEnvironments:[],
# positionTypes:[], educationLevels:[], categories:[], travelTypes:[], shiftTypes:[], otherFilters:[], keywordSearchText:"", location:"",
# sortOption:""}} (:89-97, :168-188), response {jobPostingPreviewsCount, jobPostingPreviews:[{jobId}]} (:91-96), stop on an empty page or
# when ids >= count (:100-111); GET <mantle>/api/ats/job-postings/<id> -> jobPosting {jobId, jobTitle, location, remoteType, description
# (HTML-escaped), startDate} (:118-130); url <portal>/<key>/jobs/<jobId> (:138); remote = remoteType "remote" or the location (:143).
# The JWT is only ever sent to the paycomonline.net host read from the page. NOT IN REFERENCE: previews carry no title (the reference
# reads only jobId), so only the first ID_ONLY_CAP postings are fetched and paging stops once enough ids are known.
_PC_JWT = re.compile(r'"sessionJWT":"([^"]+)"')
_PC_HOST = re.compile(r"portal-applicant-tracking\.[a-z0-9-]+\.paycomonline\.net")
_PC_FILTERS = {"distanceFrom": 0, "workEnvironments": [], "positionTypes": [], "educationLevels": [], "categories": [],
               "travelTypes": [], "shiftTypes": [], "otherFilters": [], "keywordSearchText": "", "location": "", "sortOption": ""}


def _paycom(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Paycom ATS portal: session JWT from the portal page, then the portal's own previews search and posting detail."""
    key = str(row.get("token") or "")
    if not _KEY32.fullmatch(key):
        return None
    portal = f"https://www.paycomonline.net/v4/ats/web.php/portal/{key}"
    shell = _page(http, f"{portal}/jobs/1")
    if shell is None or "Job board does not exist." in shell:
        return None
    jwt, host = _PC_JWT.search(shell), _PC_HOST.search(shell)
    if not jwt or not host:
        return None
    mantle = "https://" + host.group(0)
    auth = {"Authorization": jwt.group(1), "Locale": "en-US", "Accept": "application/json"}
    st, cn = _get(http, f"{mantle}/api/ats/company-name", headers=auth)
    name = str(cn.get("companyName") or "").strip() if st == 200 and isinstance(cn, dict) else ""
    ids: list[str] = []
    for page in range(MAX_PAGES):
        body = json.dumps({"skip": page * 50, "take": 50, "filtersForQuery": _PC_FILTERS}).encode()
        st, data = _get(http, f"{mantle}/api/ats/job-posting-previews/search", method="POST", data=body,
                        headers=dict(auth, **{"Content-Type": "application/json"}))
        previews = data.get("jobPostingPreviews") if isinstance(data, dict) else None
        if st != 200 or not isinstance(previews, list):
            if page:
                break
            return None
        if not previews:
            break
        for p in previews:
            jid = p.get("jobId") if isinstance(p, dict) else None
            if isinstance(jid, (int, str)) and not isinstance(jid, bool) and str(jid) and str(jid) not in ids:
                ids.append(str(jid))
        count = data.get("jobPostingPreviewsCount")
        if len(ids) >= ID_ONLY_CAP or (isinstance(count, int) and len(ids) >= count):
            break
    jobs: list[Job] = []
    for jid in ids[:ID_ONLY_CAP]:
        st, det = _get(http, f"{mantle}/api/ats/job-postings/{quote(jid, safe='')}", headers=auth)
        p = det.get("jobPosting") if st == 200 and isinstance(det, dict) else None
        if not isinstance(p, dict) or not str(p.get("jobId") or "") or not str(p.get("jobTitle") or "").strip():
            continue
        place = str(p.get("location") or "").strip()
        is_remote = str(p.get("remoteType") or "").lower() == "remote"
        j = _job(row, "paycom", title=str(p["jobTitle"]), url=f"{portal}/jobs/{p['jobId']}", location=place, posted=_day(p.get("startDate")),
                 description=_clean(p.get("description"), escaped=True), remote=True if is_remote else _remote_from(place))
        if name:
            j["company"] = name
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- Paycor
# Borrowed from freehire internal/ingest/sources/paycor.go: board = 32-hex clientId (:13-15); listing
# https://recruitingbypaycor.com/career/CareerHome.action?clientId=<id> (:40, :93-96), every open posting on one page; a posting is an
# <a> whose path ends in JobIntroduction.action with the same clientId (case-insensitive) and a non-empty id (:128-144), de-duplicated by
# id (:110-126), url rebuilt as .../career/JobIntroduction.action?clientId=<id>&id=<id> (:98-104); fields from the posting page's element
# ids gnewtonJobPosition (title), gnewtonJobLocationInfo (location), gnewtonJobDescriptionText (description) (:69-78), a cell's leading
# <b>Label:</b> is dropped (:146-176); a page without the title cell is a filled or withdrawn posting and is dropped (:58-62, :69-71);
# remote from the location text (:87-89); no publication date (:29). NOT IN REFERENCE: a posting past DETAIL_CAP (or not searched)
# keeps the listing link text as its title, with no location or description.
def _paycor_cell(root: _Node, ident: str) -> str:
    cell = _by_id(root, ident)
    if cell is None:
        return ""
    parts: list[str] = []
    leading = True
    for kid in cell.kids:
        text = _plain(kid) if isinstance(kid, _Node) else " ".join(kid.split())
        if not text:
            continue
        if leading:
            leading = False
            if isinstance(kid, _Node) and kid.tag == "b" and text.endswith(":"):
                continue
        parts.append(text)
    return " ".join(" ".join(parts).split())


def _paycor(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Paycor Recruiting career site: one CareerHome listing, labelled cells on each JobIntroduction page."""
    cid = str(row.get("token") or "").lower()
    if not _KEY32.fullmatch(cid):
        return None
    body = _page(http, f"https://recruitingbypaycor.com/career/CareerHome.action?clientId={cid}")
    if body is None:
        return None
    order: list[str] = []
    titles: dict[str, list[str]] = {}
    for href, text in _anchors(_parse(body).root):
        u = _split("recruitingbypaycor.com/career", href)
        if u is None:
            continue
        if u.path.rsplit("/", 1)[-1] != "JobIntroduction.action" or (u.hostname or "").lower() not in ("recruitingbypaycor.com", "www.recruitingbypaycor.com"):
            continue
        q = parse_qs(u.query)
        pid = (q.get("id") or [""])[0]
        if (q.get("clientId") or [""])[0].lower() != cid or not re.fullmatch(r"[A-Za-z0-9]{1,64}", pid):
            continue
        if pid not in titles:
            titles[pid] = []
            order.append(pid)
        titles[pid].append(text)
    jobs: list[Job] = []
    details = 0
    for pid in order:
        url = f"https://recruitingbypaycor.com/career/JobIntroduction.action?clientId={cid}&id={pid}"
        title, place, desc = _listing_title(titles[pid]), "", ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dp = _page(http, url)
            root = _parse(dp).root if dp else None
            if root is not None:
                title = _paycor_cell(root, "gnewtonJobPosition")  # empty = filled or withdrawn: dropped
                place = _paycor_cell(root, "gnewtonJobLocationInfo")
                cell = _by_id(root, "gnewtonJobDescriptionText")
                desc = _text(_inner_html(cell)) if cell is not None else ""
        if title:
            jobs.append(_job(row, "paycor", title=title, url=url, location=place, description=desc, remote=_remote_from(place)))
    return jobs


# --------------------------------------------------------------------------- JobScore
# Borrowed from freehire internal/ingest/sources/jobscore.go: board = company shortcode (:9-12, :53); one keyless call
# https://careers.jobscore.com/jobs/<board>/feed.json?sort=date&limit=1000 returns every posting (:22-24, :56); envelope
# {company_name, jobs:[{id, title, detail_url, description, city, state, country, location, remote, job_type, experience_level,
# opened_date}]} (:31-50); a posting with no id is dropped (:80); url = detail_url (:86); location = "City, State", else country, else
# the free-text location (:99-115); remote string "Yes | ..." = remote, "Yes" with "some" = hybrid, "No ..." = onsite (:117-133);
# opened_date RFC3339 or a date (:93); company = the configured one, else the feed's company_name (:63-66). The feed may be polled at
# most hourly (:16-17): one request per run. Employment type and experience level are kept as the board states them (:139-172 map them
# onto freehire vocabularies, which the kit does not use).
def _jobscore(row: dict[str, Any], http: Http) -> list[Job] | None:
    """JobScore public per-company feed.json (everything inline, one request)."""
    board = str(row.get("token") or "").lower()
    if not _LABEL.fullmatch(board):
        return None
    st, feed = _get(http, f"https://careers.jobscore.com/jobs/{quote(board, safe='')}/feed.json?sort=date&limit=1000", headers=_JSON)
    items = feed.get("jobs") if isinstance(feed, dict) else None
    if st != 200 or not isinstance(items, list):
        return None
    company = str(feed.get("company_name") or "").strip() if not row.get("name") else ""
    jobs: list[Job] = []
    for j in items:
        if not isinstance(j, dict) or not str(j.get("id") or "").strip():
            continue
        url, title = _https(j.get("detail_url"), "careers.jobscore.com"), str(j.get("title") or "").strip()
        if not url or not title:
            continue
        place = _commas(j.get("city"), j.get("state")) or str(j.get("country") or "").strip() or str(j.get("location") or "").strip()
        rem = str(j.get("remote") or "").strip().lower()
        mode = ("hybrid" if "some" in rem else "remote") if rem.startswith("yes") else ("onsite" if rem.startswith("no") else "")
        job = _job(row, "jobscore", title=title, url=url, location=place, posted=_day(j.get("opened_date")),
                   description=_clean(j.get("description")), remote={"remote": True, "onsite": False}.get(mode),
                   extra={"work_mode": mode, "employment_type": str(j.get("job_type") or ""),
                          "experience_level": str(j.get("experience_level") or "")})
        if company:
            job["company"] = company
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- RecruiterFlow
# Borrowed from freehire internal/ingest/sources/recruiterflow.go: board = the agency's path segment (:11-13), listing
# https://recruiterflow.com/<board>/jobs (:86); the whole board is a bare JS assignment "window.jobsList = {...};" in the page, found by
# bracket matching (:15-20, :91); its "department" key is a list of 2-element [name, items[]] groups (:65-81), flattened (:99-103); item
# fields apply_link, details (the location text), employment_type, job_id, job_name, last_opened, remote_type (:55-63); the posting url is
# https://recruiterflow.com/<apply_link> (:112); the description is the posting page's schema.org JobPosting ld+json (:121-126);
# location = details (:133), company = the configured one (:132), date = last_opened (:138), remote = remote_type or "remote" in
# name/details (:135). A board is a recruiting agency, not an employer (:12-14). NOT IN REFERENCE: remote_type values other than
# "Remote"; a posting past DETAIL_CAP keeps its listing fields and has no description.
_RF_KEY = re.compile(r"window\.jobsList\s*=\s*")


def _json_slice(s: str, start: int) -> str:
    """The balanced ``{...}`` object starting at the first '{' at or after ``start`` (strings and escapes respected), else ''."""
    i = s.find("{", start)
    if i < 0:
        return ""
    depth, in_str, k = 0, False, i
    while k < len(s):
        c = s[k]
        if in_str:
            if c == "\\":
                k += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                return s[i:k + 1]
        k += 1
    return ""


def _recruiterflow(row: dict[str, Any], http: Http) -> list[Job] | None:
    """RecruiterFlow agency board (recruiterflow.com/<board>/jobs): the job list is embedded in the page as window.jobsList."""
    board = str(row.get("token") or "")
    if not _LABEL.fullmatch(board):
        return None
    page = _page(http, f"https://recruiterflow.com/{quote(board, safe='')}/jobs")
    m = _RF_KEY.search(page) if page else None
    if page is None or m is None:
        return None
    try:
        groups = json.loads(_json_slice(page, m.end()) or "null").get("department")
    except (ValueError, AttributeError):
        return None
    if not isinstance(groups, list):
        return None
    items = [it for g in groups if isinstance(g, list) and len(g) == 2 and isinstance(g[1], list) for it in g[1] if isinstance(it, dict)]
    jobs: list[Job] = []
    details = 0
    for it in items:
        link, title = str(it.get("apply_link") or "").strip().lstrip("/"), str(it.get("job_name") or "").strip()
        url = _https(urljoin("https://recruiterflow.com/", link), "recruiterflow.com") if link else ""
        if not (url and title and it.get("job_id")):
            continue
        desc = ""
        if details < DETAIL_CAP and _title_ok(row, title):
            details += 1
            dp = _page(http, url)
            ld = _ld_posting(_parse(dp)) if dp else None
            desc = _clean(ld.get("description")) if ld else ""
        place = str(it.get("details") or "").strip()
        rt = str(it.get("remote_type") or "").strip().lower()
        jobs.append(_job(row, "recruiterflow", title=title, url=url, location=place, posted=_day(it.get("last_opened")), description=desc,
                         remote=True if rt == "remote" else _remote_from(title, place),
                         extra={"employment_type": str(it.get("employment_type") or ""), "job_id": it["job_id"]}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "bullhorn": _wrap(_bullhorn),
    "careerplug": _wrap(_careerplug),
    "hireology": _wrap(_hireology),
    "hiringthing": _wrap(_hiringthing),
    "hrmdirect": _wrap(_hrmdirect),
    "isolvedhire": _wrap(_isolvedhire),
    "paycom": _wrap(_paycom),
    "paycor": _wrap(_paycor),
    "jobscore": _wrap(_jobscore),
    "recruiterflow": _wrap(_recruiterflow),
}
