"""European and other ATS board fetchers for the hunt kit: Emply, Factorial, Jobylon, Manatal, PeopleForce,
TalentAdore, TalentHR, TalentLyft, Traffit, Trakstar. Same contract as kit_ats_more.py: ``fetch(row, http, stats) -> list[Job]``,
never raises, network only through the ``http`` callable, no file IO at import, standard library only. Nothing here ranks
or scores a job.

Board tokens (companies_ats_eu.json ``board`` / ``--companies-extra "ats:token"``):
  emply        tenant slug of <slug>.career.emply.com, or the career-site host when it runs on the customer's own domain
               (midtjob.dk, job.paychex.eu)
  factorial    careers host, e.g. muffin.factorial.it or highbras.factorialhr.com.br
  jobylon      any word (e.g. ``jobylon:all``): Jobylon has ONE global jobs sitemap, no per-company board
  manatal      career-page slug of careers-page.com/<slug>
  peopleforce  tenant of <tenant>.peopleforce.io
  talentadore  feed token from the career-site widget (ats.talentadore.com/positions/<token>/json)
  talenthr     tenant slug of jobs.talenthr.io/<slug>
  talentlyft   tenant of <tenant>.talentlyft.com
  traffit      tenant of <tenant>.traffit.com
  trakstar     tenant of <tenant>.hire.trakstar.com

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only. Where a site gives the job title only on the posting page (Factorial,
PeopleForce, TalentHR, TalentLyft, Jobylon), the page is fetched only for cards whose title passes the run's ``title_ok``
test, up to a per-board cap; the other cards of Factorial and PeopleForce keep their listing title and no description,
and the three sources that have no title on the listing (TalentHR, TalentLyft, Jobylon) do not list a job they did not open.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterator
from urllib.parse import quote, urljoin, urlsplit

try:  # shared safe-XML helper (same folder)
    from kit_xml import safe_fromstring  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: find the sibling file
    import importlib.util as _xilu
    from pathlib import Path as _XPath

    _xspec = _xilu.spec_from_file_location("kit_xml", _XPath(__file__).with_name("kit_xml.py"))
    if _xspec is None or _xspec.loader is None:
        raise ImportError("kit_xml.py not found next to this file")
    _xmod = _xilu.module_from_spec(_xspec)
    _xspec.loader.exec_module(_xmod)
    safe_fromstring = _xmod.safe_fromstring

try:  # shared helpers live in the sibling kit_ats.py
    from kit_ats import (  # type: ignore[import-not-found]
        MAX_PAGES,
        Http,
        Job,
        _count,
        _get,
        _iso_date,
        _job,
        _join,
        _remote_from,
        _rfc822_date,
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
    _count, _get, _iso_date, _job, _join = _mod._count, _mod._get, _mod._iso_date, _mod._job, _mod._join
    _remote_from, _rfc822_date, _text, _title_ok = _mod._remote_from, _mod._rfc822_date, _mod._text, _mod._title_ok

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}
_HTML = {"Accept": "text/html"}
_XML = {"Accept": "application/xml, text/xml, application/rss+xml"}

# Detail-page requests per board (the kit's own cap; the references fan out over every posting).
FACTORIAL_DETAIL_CAP = 25
PEOPLEFORCE_DETAIL_CAP = 25
TALENTHR_DETAIL_CAP = 25
TALENTLYFT_DETAIL_CAP = 25
JOBYLON_DETAIL_CAP = 25
EMPLY_PAGE = 100
TRAFFIT_PAGE = 100


# --------------------------------------------------------------------------- shared helpers
def _label(s: str) -> bool:
    """A DNS-style label: letters, digits, hyphens; not starting or ending with a hyphen."""
    return bool(re.fullmatch(r"[A-Za-z0-9-]{1,80}", s)) and s[0] != "-" and s[-1] != "-"


def _slug(s: str) -> bool:
    """A path slug: letters, digits, dot, underscore, hyphen."""
    return bool(re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", s))


def _https(url: Any, host: str | None = None) -> str:
    """The URL (fragment dropped) when it is an absolute https link (on ``host`` when given), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
    except ValueError:
        return ""
    ok = p.scheme == "https" and p.netloc and (host is None or (p.hostname or "").lower() == host)
    return p._replace(fragment="").geturl() if ok else ""


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


_BODY_CAP = 50_000  # characters of a description read at all: a longer one is cut before any scan
_OPEN_BLOCK = re.compile(r"<(script|style)(?=[\s/>])", re.I)  # a tag name only counts when the next character ends it
_CLOSE_BLOCK = {"script": re.compile(r"</script", re.I), "style": re.compile(r"</style", re.I)}


def _strip_blocks(html: str) -> str:
    """Drop every <script>/<style> element in ONE linear pass: each search resumes where the last one stopped,
    so no stretch of text is scanned twice. A tag that never closes drops the rest of the text."""
    out: list[str] = []
    pos = 0
    while True:
        m = _OPEN_BLOCK.search(html, pos)
        if m is None:
            out.append(html[pos:])
            break
        out.append(html[pos:m.start()])
        close = _CLOSE_BLOCK[m.group(1).lower()].search(html, m.end())
        if close is None:
            break  # never closed: the tail is script/style text
        gt = html.find(">", close.end())
        if gt < 0:
            break
        pos = gt + 1
    return "".join(out)


def _body(value: Any) -> str:
    """Plain text of an HTML description with script and style blocks dropped first (freehire runs sanitizeHTML)."""
    if not value:
        return ""
    s = _strip_blocks(str(value)[:_BODY_CAP])
    gt = s.rfind(">") + 1
    s = s[:gt] + s[gt:].split("<", 1)[0]  # cut at the first '<' that has no '>' after it, so none is left for _text's tag regexes to rescan
    return str(_text(s))


def _words(s: str) -> str:
    """Hyphen/underscore slug -> spaced words (used only to test a title the page has not given yet)."""
    return " ".join(re.split(r"[-_]+", s)).strip()


def _page_st(http: Http, url: str, *, once: bool = False) -> tuple[int, str | None]:
    """(status, text of a 200 HTML/XML page or None). ``once`` sends the request a single time (no 429/5xx retry)."""
    st, page = _get(http, url, as_json=False, headers=_HTML, **({"once": True} if once else {}))
    return st, (page if st == 200 and isinstance(page, str) else None)


def _page(http: Http, url: str, *, once: bool = False) -> str | None:
    """The text of a 200 HTML/XML page, else None."""
    return _page_st(http, url, once=once)[1]


def _open_details(row: dict[str, Any], http: Http, items: list[tuple[str, str]], cap: int) -> dict[str, str]:
    """Open posting pages for (url, title-to-test) items: only titles that pass ``title_ok``, at most ``cap`` requests,
    and the first refusal (429, 403, 0, any other non-200) ends the run (repeating a refused request only holds the
    refusal up, so each request goes out once, with no 429 backoff). A 404/410 is one posting gone, so it is skipped and the next card is tried. Returns url -> page."""
    out: dict[str, str] = {}
    tried = 0
    for url, test in items:
        if tried >= cap:
            break
        if not _title_ok(row, test):
            continue
        tried += 1
        st, page = _page_st(http, url, once=True)
        if page is None:
            if st in (404, 410):
                continue
            break
        out[url] = page
    return out


def _epoch_date(value: Any) -> str:
    """Unix seconds -> 'YYYY-MM-DD', '' when not a positive number."""
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0:
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y-%m-%d")
        except (OverflowError, OSError, ValueError):
            return ""
    return ""


# --- a small forgiving HTML tree (html.parser), enough to find a tag, a class, a link or a script
_VOID = frozenset({"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source", "track", "wbr"})
_BLOCK = frozenset({"p", "div", "br", "li", "ul", "ol", "h1", "h2", "h3", "h4", "h5", "h6", "tr", "section"})


class _Node:
    def __init__(self, tag: str, attrs: dict[str, str]) -> None:
        self.tag, self.attrs = tag, attrs
        self.kids: list[_Node | str] = []

    def has_class(self, name: str) -> bool:
        return name in self.attrs.get("class", "").split()

    def text(self) -> str:
        """Visible text with whitespace collapsed (scripts and styles skipped)."""
        parts: list[str] = []

        def go(n: _Node) -> None:
            for k in n.kids:
                if isinstance(k, str):
                    parts.append(k)
                elif k.tag not in ("script", "style"):
                    go(k)
        go(self)
        return " ".join("".join(parts).split())

    def inner_html(self) -> str:
        """Child markup as a string (scripts and styles dropped), fed to ``_text`` for the description."""
        out: list[str] = []
        for k in self.kids:
            if isinstance(k, str):
                out.append(html.escape(k, quote=False))
            elif k.tag not in ("script", "style"):
                attrs = "".join(f' {a}="{html.escape(v)}"' for a, v in k.attrs.items())
                out.append(f"<{k.tag}{attrs}>" + ("" if k.tag in _VOID else k.inner_html() + f"</{k.tag}>"))
        return "".join(out)

    def raw_text(self) -> str:
        """Text exactly as the parser delivered it (a script's body)."""
        return "".join(k for k in self.kids if isinstance(k, str))


_MAX_DEPTH = 200  # open-element cap: unclosed <p>/<li>/<option> in a hostile page must not make the walkers recurse or the end-tag scan grow without bound


class _Tree(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {})
        self._stack = [self.root]

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = _Node(tag, {k: v or "" for k, v in attrs})
        self._stack[-1].kids.append(n)
        if tag not in _VOID and len(self._stack) < _MAX_DEPTH:
            self._stack.append(n)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._stack[-1].kids.append(_Node(tag, {k: v or "" for k, v in attrs}))

    def handle_endtag(self, tag: str) -> None:
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                return

    def handle_data(self, data: str) -> None:
        self._stack[-1].kids.append(data)


def _tree(page: str) -> _Node:
    t = _Tree()
    try:
        t.feed(page)
        t.close()
    except Exception:  # noqa: BLE001 - a malformed page yields what was read so far
        pass
    return t.root


def _walk(node: _Node) -> Iterator[_Node]:
    for k in node.kids:
        if isinstance(k, _Node):
            yield k
            yield from _walk(k)


def _links(root: _Node, base: str, host: str, test: Callable[[str], bool]) -> dict[str, str]:
    """{absolute https URL on ``host`` (query and fragment dropped): longest anchor text} in first-seen order."""
    out: dict[str, str] = {}
    for a in _walk(root):
        href = a.attrs.get("href", "") if a.tag == "a" else ""
        if not href or not test(href):
            continue
        try:
            u = urlsplit(urljoin(base, href))
        except ValueError:
            continue
        if u.scheme != "https" or (u.hostname or "").lower() != host:
            continue
        url = u._replace(query="", fragment="").geturl()
        txt = a.text()
        if url not in out or len(txt) > len(out[url]):
            out[url] = txt
    return out


def _next_data(page: str) -> Any:
    """The JSON object after the __NEXT_DATA__ marker, cut by brace matching that skips braces inside strings
    (freehire talenthr.go:70 uses bracketSlice for the same reason: a ``</script>`` may sit inside the JSON text)."""
    i = page.find("__NEXT_DATA__")
    start = page.find("{", i) if i >= 0 else -1
    if start < 0:
        return None
    depth, in_str, k = 0, False, start
    while k < len(page):
        c = page[k]
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
                try:
                    return json.loads(page[start:k + 1])
                except (ValueError, RecursionError):  # deeply nested JSON must cost only this page
                    return None
        k += 1
    return None


def _ld_posting(root: _Node) -> dict[str, Any] | None:
    """The first schema.org JobPosting in the page's ld+json scripts (bare object, array, or @graph)."""
    def pick(d: Any, depth: int = 0) -> dict[str, Any] | None:
        if depth > 12:
            return None
        if isinstance(d, list):
            return next((p for p in (pick(x, depth + 1) for x in d) if p), None)
        if isinstance(d, dict):
            t = d.get("@type")
            if (isinstance(t, str) and t.lower() == "jobposting") or (isinstance(t, list) and "JobPosting" in t):
                return d
            return pick(d.get("@graph"), depth + 1)
        return None
    for s in _walk(root):
        if s.tag == "script" and s.attrs.get("type", "").lower() == "application/ld+json":
            try:
                found = pick(json.loads(s.raw_text(), strict=False))
            except (ValueError, RecursionError):
                continue
            if found:
                return found
    return None


def _ld_places(posting: dict[str, Any], *, region: bool) -> str:
    """jobLocation (one Place or a list) -> 'Locality[, Region], Country' per place, distinct, joined by '; '."""
    raw = posting.get("jobLocation")
    out: list[str] = []
    for pl in raw if isinstance(raw, list) else [raw]:
        a = pl.get("address") if isinstance(pl, dict) else None
        if not isinstance(a, dict):
            continue
        c = a.get("addressCountry")
        c = c.get("name") if isinstance(c, dict) else c
        lab = _join(a.get("addressLocality"), a.get("addressRegion") if region else "", c)
        if lab and lab not in out:
            out.append(lab)
    return "; ".join(out)


def _local(tag: Any) -> str:
    return str(tag).rsplit("}", 1)[-1]


def _sitemap_locs(root: ET.Element, kind: str) -> list[str]:
    """<loc> of every <url> (kind 'url') or <sitemap> (kind 'sitemap') child, namespaces ignored."""
    out: list[str] = []
    for el in root:
        if _local(el.tag) == kind:
            loc = next((c.text.strip() for c in el if _local(c.tag) == "loc" and c.text), "")
            if loc:
                out.append(loc)
    return out


# --------------------------------------------------------------------------- Emply
# Borrowed from freehire internal/ingest/sources/emply.go:43-44,118 (POST https://<host>/api/integration/vacancy/get-page),
# :127 and :69 (body {"count","offset","filters":[]}; filters is REQUIRED, the endpoint answers 500 without it, :19-21),
# :56-61 (a board with a dot is already a host, a bare slug is <slug>.career.emply.com), :72-77 and :134,143 (answer
# {"vacancies":[...],"count":N}; page by offset until the count is read), :85 (talentPool = the standing
# "unsolicited application" entry, not a vacancy), :156-179 (read the English text, else the first one with a body),
# :183-219 (shortId required; URL externalCseAdLink else https://<host>/ad/<titleAsUrl|ad>/<shortId>/<lang>; location,
# published, full HTML body per translation). The reference demands the distinct ids equal count (:147-150); the kit keeps a partial board.
_EMPLY_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")


def _emply_host(token: str) -> str:
    t = token.strip().lower()
    host = t if "." in t else (f"{t}.career.emply.com" if _label(t) else "")
    return host if _EMPLY_HOST.fullmatch(host) else ""


def _emply_translation(v: dict[str, Any]) -> dict[str, Any]:
    trs = [t for t in (v.get("translations") or []) if isinstance(t, dict)]
    body = [t for t in trs if str(t.get("content") or "").strip()]
    english = next((t for t in body if str(t.get("languageKey") or "").lower().startswith("en")), None)
    return english or (body[0] if body else (trs[0] if trs else {}))


def _emply(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Emply career-site vacancy API (keyless POST; token = slug or the career-site host)."""
    host = _emply_host(str(row.get("token") or ""))
    if not host:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    offset = 0
    for page in range(MAX_PAGES):
        body = {"count": EMPLY_PAGE, "offset": offset, "filters": []}
        st, data = _get(http, f"https://{host}/api/integration/vacancy/get-page", method="POST", data=json.dumps(body).encode(), headers=_POST)
        batch = data.get("vacancies") if isinstance(data, dict) else None
        if st != 200 or not isinstance(batch, list):
            if page:
                break  # a hiccup after page 0 keeps what we have
            return None
        total = data.get("count") if isinstance(data.get("count"), int) else None
        fresh = 0
        for v in batch:
            if not isinstance(v, dict):
                continue
            vid = str(v.get("id") or v.get("shortId") or "")
            if vid in seen:
                continue
            seen.add(vid)
            fresh += 1
            short = str(v.get("shortId") or "").strip()
            if v.get("talentPool") is True or not short:
                continue
            tr = _emply_translation(v)
            title = " ".join(str(tr.get("title") or "").split()) or " ".join(str(v.get("title") or "").split())
            if not title:
                continue
            url = _https(v.get("externalCseAdLink"))
            if not url:
                url = f"https://{host}/ad/{quote(str(v.get('titleAsUrl') or 'ad'), safe='')}/{quote(short, safe='')}"
                lang = str(tr.get("languageKey") or "").partition("-")[0].lower()
                url += f"/{quote(lang, safe='')}" if lang else ""
            place = " ".join(str(v.get("location") or "").split())
            jobs.append(_job(row, "emply", title=title, url=url, location=place, posted=_iso_date(v.get("published")),
                             description=_body(tr.get("content")), remote=_remote_from(place), extra={"short_id": short}))
        offset += len(batch)
        if not batch or not fresh or (total is not None and offset >= total):
            break
    return jobs


# --------------------------------------------------------------------------- Factorial
# Borrowed from freehire internal/ingest/sources/factorial.go:14-19,34-38 (board = careers host; GET https://<host>/),
# :45 (job links contain "/job_posting/"), :31 (id = trailing "-<digits>" of the URL), :54-79 (detail page: title in the h1,
# description in div.styledText, no publish date), :92-106 (location = the metadata row that has a comma and no currency
# symbol), :85 and :111-122 (work-mode label such as "Ibrido (Milano, Lombardia, Italia)"), :162-170 (metadata rows are
# li.border-gray-50). The title fallback for a card whose page is not opened (longest anchor text) is NOT IN REFERENCE.
# Only Factorial's own domains (those the reference names or tests: factorial.it, factorialhr.com / .pt / .com.br; the
# other factorialhr country domains are NOT IN REFERENCE and so not read). factorial.io, .co, .xyz ... belong to other companies.
_FACTORIAL_HOST = re.compile(r"(?:[a-z0-9-]+\.)+(?:factorial\.it|factorialhr\.(?:com|pt|com\.br))")
_FACTORIAL_ID = re.compile(r"-(\d+)/?$")
_FACTORIAL_ROW_MAX = 300  # a real metadata row is short; a hostile one must not be scanned at length
_FACTORIAL_MODES = {"ibrido": "hybrid", "híbrido": "hybrid", "hibrido": "hybrid", "hybrid": "hybrid", "hibride": "hybrid",
                    "da remoto": "remote", "remoto": "remote", "remota": "remote", "remote": "remote", "en remoto": "remote",
                    "teletrabajo": "remote", "in sede": "onsite", "presencial": "onsite", "presenziale": "onsite",
                    "on-site": "onsite", "on site": "onsite", "onsite": "onsite", "no escritório": "onsite", "no local": "onsite"}


def _factorial_location(rows: list[str]) -> tuple[str, str]:
    """(location, work mode) out of the unlabeled metadata rows."""
    for r in rows:
        r = r.strip()
        if not r or any(c in r for c in "€$£₹¥"):
            continue
        r = r[:_FACTORIAL_ROW_MAX]
        lp = r.rfind("(")  # "<mode> (<place, region>)": the last "(" opens the group, the row ends with ")", no nested parentheses
        if lp > 0 and r.endswith(")") and r[:lp].strip():
            inner = r[lp + 1:-1]
            if "," in inner and "(" not in inner and ")" not in inner:
                return inner.strip(), _FACTORIAL_MODES.get(r[:lp].strip().lower(), "")
        if "," in r:
            return r, ""
    return "", ""


def _factorial(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Factorial (FactorialHR) career site: server-rendered job list, one page per opened job."""
    host = str(row.get("token") or "").strip().lower()
    if not _FACTORIAL_HOST.fullmatch(host):
        return None
    base = f"https://{host}/"
    root = _page(http, base)
    if root is None:
        return None
    cards = {u: t for u, t in _links(_tree(root), base, host, lambda h: "/job_posting/" in h).items() if _FACTORIAL_ID.search(urlsplit(u).path)}
    pages = _open_details(row, http, [(u, t or _words(urlsplit(u).path.rsplit("/", 1)[-1])) for u, t in cards.items()], FACTORIAL_DETAIL_CAP)
    jobs: list[Job] = []
    for url, anchor in cards.items():
        jid = _FACTORIAL_ID.search(urlsplit(url).path)
        title, place, mode, desc = anchor, "", "", ""
        if url in pages:
            t = _tree(pages[url])
            h1 = next((n.text() for n in _walk(t) if n.tag == "h1"), "")
            rows = [n.text() for n in _walk(t) if n.tag == "li" and n.has_class("border-gray-50")]
            place, mode = _factorial_location(rows)
            body = next((n for n in _walk(t) if n.tag == "div" and n.has_class("styledText")), None)
            desc = _body(body.inner_html()) if body else ""
            title = h1 or anchor
        if not title or not jid:
            continue
        jobs.append(_job(row, "factorial", title=title, url=url, location=place, description=desc,
                         remote=True if mode == "remote" else _remote_from(place), extra={"job_id": jid.group(1), "work_mode": mode}))
    return jobs


# --------------------------------------------------------------------------- Jobylon
# Borrowed from freehire internal/ingest/sources/jobylon.go:44 (global sitemap index https://emp.jobylon.com/sitemap.xml),
# :46 (the child whose URL contains "sitemap-jobs" lists every job), :51 (a job URL has /jobs/<digits>; /companies/ pages are
# skipped), :114-140 (each posting page carries a schema.org JobPosting ld+json: title, hiringOrganization.name = the
# employer, description, datePosted, jobLocation; a posting with no title or no employer is dropped, :124), :147-162
# (jobLocation may be one Place or a list; places joined with "; "). Newest-first ordering (highest id first) and testing
# the URL slug against ``title_ok`` before opening a page are NOT IN REFERENCE (the reference opens every new posting).
def _jobylon(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Jobylon global jobs sitemap + one ld+json posting page per wanted job (token ignored)."""
    index = safe_fromstring(_page(http, "https://emp.jobylon.com/sitemap.xml"))
    if index is None:
        return None
    sub = next((u for u in _sitemap_locs(index, "sitemap") if "sitemap-jobs" in u and _https(u, "emp.jobylon.com")), "")
    listing = safe_fromstring(_page(http, sub)) if sub else None
    if listing is None:
        return None
    found: dict[int, str] = {}
    for loc in _sitemap_locs(listing, "url"):
        m = re.search(r"/jobs/(\d+)", loc)
        if m and _https(loc, "emp.jobylon.com"):
            found.setdefault(int(m.group(1)), loc)
    items = [(u, _words(u.rstrip("/").rsplit("/", 1)[-1])) for _, u in sorted(found.items(), reverse=True)]
    pages = _open_details(row, http, items, JOBYLON_DETAIL_CAP)
    jobs: list[Job] = []
    for u, _t in items:
        p = _ld_posting(_tree(pages[u])) if u in pages else None
        if not p:
            continue
        title, company = html.unescape(str(p.get("title") or "")).strip(), html.unescape(str((p.get("hiringOrganization") or {}).get("name") or "") if isinstance(p.get("hiringOrganization"), dict) else "").strip()
        if not title or not company:
            continue
        place = _ld_places(p, region=True)
        jid = re.search(r"/jobs/(\d+)", u)
        j = _job(row, "jobylon", title=title, url=u, location=place, posted=_iso_date(p.get("datePosted")),
                 description=_body(html.unescape(str(p.get("description") or ""))), remote=_remote_from(place),
                 extra={"job_id": jid.group(1) if jid else ""})
        j["company"] = company  # the employer named in the ad, never the board
        jobs.append(j)
    return jobs


# --------------------------------------------------------------------------- Manatal
# Borrowed from freehire internal/ingest/sources/manatal.go:33 (GET https://open.api.manatal.com/open/v3/career-page/<slug>/jobs/,
# a DRF page {count,next,previous,results}), :61-75 (follow the top-level "next" link until null), :44-55 (hash, position_name,
# description HTML, country/state/city, location_display, is_remote), :87 (location_display else city, state, country), :95
# (remote = is_remote or "remote" in the location), :83-86 (a posting with no hash is dropped; organization_name is a department,
# never the employer), :36 and :90 (posting page https://www.careers-page.com/<slug>/job/<hash>), :98 (no publish date).
def _manatal(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Manatal career-page JSON feed (token = career-page slug)."""
    slug = str(row.get("token") or "").strip()
    if not _slug(slug):
        return None
    nxt = f"https://open.api.manatal.com/open/v3/career-page/{quote(slug, safe='')}/jobs/"
    jobs: list[Job] = []
    for page in range(MAX_PAGES):
        st, data = _get(http, nxt, headers=_JSON)
        results = data.get("results") if isinstance(data, dict) else None
        if st != 200 or not isinstance(results, list):
            if page:
                break
            return None
        for p in results:
            h = str((p or {}).get("hash") or "").strip() if isinstance(p, dict) else ""
            title = str(p.get("position_name") or "").strip() if h else ""
            if not h or not title:
                continue
            place = str(p.get("location_display") or "").strip() or _join(p.get("city"), p.get("state"), p.get("country"))
            jobs.append(_job(row, "manatal", title=title, url=f"https://www.careers-page.com/{quote(slug, safe='')}/job/{quote(h, safe='')}",
                             location=place, description=_body(p.get("description")),
                             remote=True if p.get("is_remote") is True else _remote_from(place),
                             extra={"employment_type": str(p.get("contract_details") or "")}))
        nxt = _https(data.get("next"), "open.api.manatal.com")
        if not nxt:
            break
    return jobs


# --------------------------------------------------------------------------- PeopleForce
# Borrowed from freehire internal/ingest/sources/peopleforce.go:59 (listing https://<board>.peopleforce.io/careers?page=N),
# :162,205-229 (job cards are links to /careers/v/<digits>-<slug>; the title is the anchor text, the detail h1 is the generic
# "Work at <Company>", :17-20), :170-201 (stop when the page's own pagination nav names no page above this one; a
# page beyond the end is clamped to the last page, :85-90), :135-148 (detail: description = div.col-lg-8, else tw-col-lg-8),
# :231-257 (a <dl> sidebar pairs dt/dd: "Location", "Work type"). peopleforce.io answers 429 to bursts (:44-49), hence the
# small detail cap and the stop on the first non-200. Listing-only fallback for cards whose page is not opened is NOT IN REFERENCE.
_PF_ID = re.compile(r"/careers/v/(\d+)")
_PF_PAGE = re.compile(r"[?&]page=(\d+)")


def _pf_has_next(root: _Node, current: int) -> bool:
    return any(int(m.group(1)) > current for a in _walk(root) if a.tag == "a" for m in [_PF_PAGE.search(a.attrs.get("href", ""))] if m)


def _peopleforce(row: dict[str, Any], http: Http) -> list[Job] | None:
    """PeopleForce career site (<tenant>.peopleforce.io/careers, paged HTML)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    host = f"{tenant}.peopleforce.io"
    cards: dict[str, str] = {}
    for n in range(1, MAX_PAGES + 1):
        page = _page(http, f"https://{host}/careers?page={n}", once=True)
        if page is None:
            if n > 1:
                break
            return None
        root = _tree(page)
        found = _links(root, f"https://{host}/careers", host, lambda h: bool(_PF_ID.search(h)))
        for u, label in found.items():
            cards.setdefault(u, label)
        if not found or not _pf_has_next(root, n):
            break
    details = _open_details(row, http, [(u, t) for u, t in cards.items() if t], PEOPLEFORCE_DETAIL_CAP)
    jobs: list[Job] = []
    for url, title in cards.items():
        m = _PF_ID.search(url)
        if not title or not m:
            continue
        place, wtype, desc = "", "", ""
        if url in details:
            t = _tree(details[url])
            fields: dict[str, str] = {}
            for dl in (n for n in _walk(t) if n.tag == "dl"):
                label = ""
                for c in dl.kids:
                    if isinstance(c, _Node) and c.tag == "dt":
                        label = c.text()
                    elif isinstance(c, _Node) and c.tag == "dd" and label:
                        fields[label], label = c.text(), ""
            col = next((n for cls in ("col-lg-8", "tw-col-lg-8") for n in _walk(t) if n.has_class(cls)), None)
            place, wtype = fields.get("Location", ""), fields.get("Work type", "")
            desc = _body(col.inner_html()) if col else ""
        jobs.append(_job(row, "peopleforce", title=title, url=url, location=place, description=desc, remote=_remote_from(place),
                         extra={"job_id": m.group(1), "work_type": wtype}))
    return jobs


# --------------------------------------------------------------------------- TalentAdore
# Borrowed from freehire internal/ingest/sources/talentadore.go:17 (GET https://ats.talentadore.com/positions/<token>/json?v=2;
# v=2 makes the dates RFC3339, :12-16), :34-56 (jobs[]: job_token, name, link, description_html, description_text,
# start_date, updated, city, county, country), :67-69 (a job with no job_token is skipped), :71 (location = city, county,
# country), :72-77 (description_html, else description_text), :86 (posted = start_date, else updated). The reference uses the
# configured company; falling back to the feed's top-level "company" when the row has no real name appears only in the
# reference's test fixture (talentadore_test.go:15-16): NOT IN REFERENCE as a documented field.
def _talentadore(row: dict[str, Any], http: Http) -> list[Job] | None:
    """TalentAdore Hire positions JSON feed (token = the widget's data-url token)."""
    token = str(row.get("token") or "").strip()
    if not _label(token):
        return None
    st, data = _get(http, f"https://ats.talentadore.com/positions/{quote(token, safe='')}/json?v=2", headers=_JSON)
    items = data.get("jobs") if isinstance(data, dict) else None
    if st != 200 or not isinstance(items, list):
        return None
    feed_company = str(data.get("company") or "").strip()
    name = str(row.get("name") or "").strip()
    jobs: list[Job] = []
    for j in items:
        jt = str((j or {}).get("job_token") or "").strip() if isinstance(j, dict) else ""
        title, url = str(j.get("name") or "").strip() if jt else "", _https(j.get("link"), "ats.talentadore.com") if jt else ""
        if not jt or not title or not url:
            continue
        place = _join(j.get("city"), j.get("county"), j.get("country"))
        job = _job(row, "talentadore", title=title, url=url, location=place,
                   posted=_iso_date(j.get("start_date") or j.get("updated")),
                   description=_body(j.get("description_html") or j.get("description_text")), remote=_remote_from(place),
                   extra={"job_token": jt})
        if feed_company and name in ("", token):
            job["company"] = feed_company
        jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- TalentHR
# Borrowed from freehire internal/ingest/sources/talenthr.go:27 (https://jobs.talenthr.io/<board>, a Next.js app) and :70
# (the data is the __NEXT_DATA__ script, cut with bracketSlice), :31-44 (listing props.pageProps.positions[]: id, slug), :95 (posting page
# https://jobs.talenthr.io/<board>/<slug>/<id>), :46-62 and :108-127 (the page's props.pageProps.data: job_position_title,
# job_description, is_remote, employment_type, employment_status_name, city, region, country, location_name, publish_date),
# :131-141 (location = city, region, country, else location_address, else location_name), :147-157 (work mode remote /
# hybrid / onsite, else is_remote), :125 (publish_date "YYYY-MM-DD HH:MM:SS", else created_at). A listing gives no title:
# only postings whose slug passes ``title_ok`` are opened (the kit's own cap), and only opened postings are listed.
def _talenthr(row: dict[str, Any], http: Http) -> list[Job] | None:
    """TalentHR career site (token = tenant slug of jobs.talenthr.io/<slug>)."""
    board = str(row.get("token") or "").strip()
    if not _slug(board):
        return None
    base = f"https://jobs.talenthr.io/{quote(board, safe='')}"
    page = _page(http, base)
    data = _next_data(page) if page is not None else None
    positions = (((data or {}).get("props") or {}).get("pageProps") or {}).get("positions") if isinstance(data, dict) else None
    if not isinstance(positions, list):
        return None
    items: list[tuple[str, str]] = []
    for p in positions:
        pid, slug = (str(p.get("id") or "").strip(), str(p.get("slug") or "").strip()) if isinstance(p, dict) else ("", "")
        if pid in ("", "0") or not _slug(slug) or not pid.isdigit():
            continue
        items.append((f"{base}/{quote(slug, safe='')}/{pid}", _words(re.sub(r"-\d+$", "", slug))))
    pages = _open_details(row, http, items, TALENTHR_DETAIL_CAP)
    jobs: list[Job] = []
    for url, _t in items:
        d = _next_data(pages[url]) if url in pages else None
        j = (((d or {}).get("props") or {}).get("pageProps") or {}).get("data") if isinstance(d, dict) else None
        if not isinstance(j, dict) or not str(j.get("job_position_title") or "").strip():
            continue
        place = _join(j.get("city"), j.get("region"), j.get("country")) or str(j.get("location_address") or "").strip() or str(j.get("location_name") or "").strip()
        mode = str(j.get("employment_type") or "").strip().lower()
        remote = True if mode == "remote" or j.get("is_remote") is True else _remote_from(place)
        jobs.append(_job(row, "talenthr", title=str(j["job_position_title"]).strip(), url=url, location=place,
                         posted=_iso_date(j.get("publish_date") or j.get("created_at")), description=_body(j.get("job_description")),
                         remote=remote, extra={"job_id": url.rsplit("/", 1)[-1], "work_mode": mode,
                                               "employment_status": str(j.get("employment_status_name") or "")}))
    return jobs


# --------------------------------------------------------------------------- TalentLyft
# Borrowed from freehire internal/ingest/sources/talentlyft.go:32 (GET https://<board>.talentlyft.com/sitemap.xml), :80-85
# (posting URLs end /jobs/<slug>-<code>, code = last hyphen segment of 3+ letters/digits; the root and section pages are
# skipped; a query string or fragment is ignored), :52-74 (each posting page carries a schema.org JobPosting ld+json: title,
# description, datePosted, employmentType, jobLocation), :60-63 (location = addressLocality, addressCountry), :64-69 (company =
# the configured one). A sitemap gives no title: only URLs whose slug passes ``title_ok`` are opened (the kit's own cap),
# and only opened postings are listed.
_TL_ID = re.compile(r"/jobs/.+-([A-Za-z0-9]{3,})$")


def _talentlyft(row: dict[str, Any], http: Http) -> list[Job] | None:
    """TalentLyft career site (sitemap.xml, then one ld+json posting page per wanted job)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    host = f"{tenant}.talentlyft.com"
    sm = safe_fromstring(_page(http, f"https://{host}/sitemap.xml"))
    if sm is None:
        return None
    items: list[tuple[str, str]] = []
    seen: set[str] = set()
    for loc in _sitemap_locs(sm, "url"):
        u = _https(loc, host)
        m = _TL_ID.search(urlsplit(u).path) if u else None
        if not m or u in seen:
            continue
        seen.add(u)
        items.append((u, _words(urlsplit(u).path.rsplit("/", 1)[-1][: -len(m.group(1)) - 1])))
    pages = _open_details(row, http, items, TALENTLYFT_DETAIL_CAP)
    jobs: list[Job] = []
    for u, _t in items:
        p = _ld_posting(_tree(pages[u])) if u in pages else None
        title = html.unescape(str((p or {}).get("title") or "")).strip()
        if not p or not title:
            continue
        a = _ld_places(p, region=False)
        et = p.get("employmentType")
        jobs.append(_job(row, "talentlyft", title=title, url=u, location=a, posted=_iso_date(p.get("datePosted")),
                         description=_body(html.unescape(str(p.get("description") or ""))), remote=_remote_from(a),
                         extra={"employment_type": ", ".join(et) if isinstance(et, list) else str(et or "")}))
    return jobs


# --------------------------------------------------------------------------- Traffit
# Borrowed from freehire internal/ingest/sources/traffit.go:73 (GET https://<board>.traffit.com/public/an/list/?limit=100&offset=N),
# :26 (the endpoint clamps a page to 100), :33-49 (answer {count, items[]}: advertId, advertPublishId, title, name, url,
# description HTML, geolocation, validStart), :79-90 (stop on an empty page or once count items are read), :97-108 (id =
# advertId else advertPublishId, none = skip; title else name), :110-116 and :52-68 (geolocation is a JSON STRING with
# locality, region1, country; null or unparsable = no location), :125 (validStart = Unix seconds).
def _traffit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Traffit public advert list (token = tenant of <tenant>.traffit.com)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    jobs: list[Job] = []
    fetched = 0
    for page in range(MAX_PAGES):
        st, data = _get(http, f"https://{tenant}.traffit.com/public/an/list/?limit={TRAFFIT_PAGE}&offset={page * TRAFFIT_PAGE}", headers=_JSON)
        items = data.get("items") if isinstance(data, dict) else None
        if st != 200 or not isinstance(items, list):
            if page:
                break
            return None
        for it in items:
            if not isinstance(it, dict):
                continue
            aid = it.get("advertId") or it.get("advertPublishId")
            title = str(it.get("title") or "").strip() or str(it.get("name") or "").strip()
            url = _https(it.get("url"), f"{tenant}.traffit.com")
            if not aid or isinstance(aid, bool) or not title or not url:
                continue
            place = ""
            geo = it.get("geolocation")
            if isinstance(geo, str) and geo:
                try:
                    g = json.loads(geo)
                except ValueError:
                    g = None
                if isinstance(g, dict):
                    place = _join(g.get("locality"), g.get("region1"), g.get("country"))
            jobs.append(_job(row, "traffit", title=title, url=url, location=place, posted=_epoch_date(it.get("validStart")),
                             description=_body(it.get("description")), remote=_remote_from(place), extra={"advert_id": str(aid)}))
        fetched += len(items)
        count = data.get("count") if isinstance(data.get("count"), int) else 0
        if not items or fetched >= count:
            break
    return jobs


# --------------------------------------------------------------------------- Trakstar Hire
# Borrowed from freehire internal/ingest/sources/trakstar.go:43 (GET https://<board>.hire.trakstar.com/jobfeeds/<board>, one RSS
# document, body inline, no paging), :32-40 (item: title, link, pubDate, description, and the namespace
# https://recruiterbox.com/rss/job/ elements locationCity, locationState, locationCountry), :53-54 (id = last path segment of
# the link, http upgraded to https), :57-58 (description is entity-unescaped HTML), :59 (remote = "remote" in the city), :60 (pubDate).
_TRAKSTAR_NS = "{https://recruiterbox.com/rss/job/}"


def _trakstar(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Trakstar Hire (Recruiterbox) RSS job feed (token = tenant of <tenant>.hire.trakstar.com)."""
    tenant = str(row.get("token") or "").strip().lower()
    if not _label(tenant):
        return None
    st, feed = _get(http, f"https://{tenant}.hire.trakstar.com/jobfeeds/{tenant}", as_json=False, headers=_XML)
    root = safe_fromstring(feed) if st == 200 else None
    if root is None or _local(root.tag) != "rss":
        return None
    jobs: list[Job] = []
    for item in root.iter("item"):
        def f(name: str, _it: ET.Element = item) -> str:
            el = _it.find(name)
            return (el.text or "").strip() if el is not None else ""
        link = _https(f("link").replace("http://", "https://", 1), f"{tenant}.hire.trakstar.com")
        title = f("title")
        if not link or not title:
            continue
        place = _join(f(_TRAKSTAR_NS + "locationCity"), f(_TRAKSTAR_NS + "locationState"), f(_TRAKSTAR_NS + "locationCountry"))
        jobs.append(_job(row, "trakstar", title=title, url=link, location=place, posted=_rfc822_date(f("pubDate")),
                         description=_body(html.unescape(f("description"))),
                         remote=True if "remote" in f(_TRAKSTAR_NS + "locationCity").lower() else None,
                         extra={"job_id": link.rstrip("/").rsplit("/", 1)[-1]}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "emply": _wrap(_emply),
    "factorial": _wrap(_factorial),
    "jobylon": _wrap(_jobylon),
    "manatal": _wrap(_manatal),
    "peopleforce": _wrap(_peopleforce),
    "talentadore": _wrap(_talentadore),
    "talenthr": _wrap(_talenthr),
    "talentlyft": _wrap(_talentlyft),
    "traffit": _wrap(_traffit),
    "trakstar": _wrap(_trakstar),
}
