"""Ten more ATS board readers for the hunt kit: Betterteam, CATS (catsone), ClinchTalent, Crelate, eRecruiter,
gr8people, HR-Manager, iSmartRecruit, Loxo and Mindsight. Same contract as kit_ats.py: ``fetch(row, http, stats) ->
list[Job]``, never raises, network only through the ``http`` callable, no file IO at import, standard library only.
Nothing here ranks or scores a job. Every request is a plain public page or keyless API call (no key, login or cookie).

Board tokens (companies.json ``board`` / ``token``):
  betterteam    tenant subdomain from <tenant>.betterteam.com
  catsone       careers host, e.g. sphereinc.catsone.com (or a custom domain mapped to CATS)
  clinch        career-site host, e.g. careers.withwaymo.com
  crelate       <portalSlug>:<organizationId> from jobs.crelate.com/portal/<slug>
  erecruiter    the 32-hex cfg from the company's skk.erecruiter.pl widget script
  gr8people     the tenant careers host, e.g. etrade.gr8people.com or batesville.workgr8.com
  hrmanager     customer alias from api.hr-manager.net/jobportal.svc/<alias>/
  ismartrecruit readable tenant id "<domain>_<code>" (the kit wraps it into the widget token)
  loxo          <host>/<slug>, e.g. fitnext.app.loxo.co/fitnext or app.loxo.co/<slug>
  mindsight     tenant path segment from oportunidades.mindsight.com.br/<slug>

Endpoints, selectors and field names are adapted from the MIT-licensed freehire adapters
(internal/ingest/sources/<name>.go, commit in NOTICE.md); each reader below cites the lines it follows. Anything the
reference does not state is marked NOT IN REFERENCE.
"""
# ruff: noqa: E501
from __future__ import annotations

import base64
import html
import json
import re
import time
from datetime import datetime, timezone
from html.parser import HTMLParser
from typing import Any, Callable, Iterator
from urllib.parse import quote, urlencode, urljoin, urlsplit

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
        _remote_from,
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
    _remote_from, _title_ok = _mod._remote_from, _mod._title_ok

_JSON = {"Accept": "application/json"}
_HTML = {"Accept": "text/html"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}

# Detail-page requests per board (the rest keep their listing fields, no description). The references fetch every
# detail with a worker pool; the kit keeps a small request budget instead.
BETTERTEAM_DETAIL_CAP = 25
CATSONE_DETAIL_CAP = 25
CLINCH_DETAIL_CAP = 10
ERECRUITER_DETAIL_CAP = 25
ERECRUITER_LIST_PAGES = 5
GR8PEOPLE_PAGES = 5
ISMART_DETAIL_CAP = 25
LOXO_DETAIL_CAP = 25
MINDSIGHT_DETAIL_CAP = 25
LOXO_SHARED_HOST_GAP = 0.35  # seconds between requests to app.loxo.co (loxo.go:205-207)


def _sleep(seconds: float) -> None:
    time.sleep(seconds)


# --------------------------------------------------------------------------- tiny HTML tree
_VOID = frozenset("area base br col embed hr img input link meta param source track wbr".split())
_BLOCK = frozenset("p div br li ul ol h1 h2 h3 h4 h5 h6 tr table section article hr".split())
_SKIP_TEXT = frozenset(("script", "style"))


class _Node:
    __slots__ = ("tag", "attrs", "kids", "parent", "depth")

    def __init__(self, tag: str, attrs: dict[str, str], parent: _Node | None) -> None:
        self.tag, self.attrs, self.parent = tag, attrs, parent
        self.depth: int = parent.depth + 1 if parent is not None else 0
        self.kids: list[Any] = []  # _Node or str

    def has_class(self, cls: str) -> bool:
        return cls in (self.attrs.get("class") or "").split()


MAX_TREE_DEPTH = 200  # bounds every recursive walk and the end-tag parent scan on hostile or broken HTML


class _Tree(HTMLParser):
    """Lenient tree builder: unmatched end tags are ignored, unclosed tags close at the end. Nesting stops at
    MAX_TREE_DEPTH: deeper tags are kept as leaves of the deepest open node instead of descending."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Node("#root", {}, None)
        self.cur = self.root

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        n = _Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur)
        self.cur.kids.append(n)
        if tag not in _VOID and n.depth < MAX_TREE_DEPTH:
            self.cur = n

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.cur.kids.append(_Node(tag, {k.lower(): (v or "") for k, v in attrs}, self.cur))

    def handle_endtag(self, tag: str) -> None:
        n: _Node | None = self.cur
        while n is not None and n.tag != tag:
            n = n.parent
        if n is not None and n.parent is not None:
            self.cur = n.parent

    def handle_data(self, data: str) -> None:
        self.cur.kids.append(data)


def _parse(page: Any) -> _Node:
    t = _Tree()
    t.feed(page if isinstance(page, str) else "")
    t.close()
    return t.root


def _walk(n: _Node) -> Iterator[_Node]:
    yield n
    for k in n.kids:
        if isinstance(k, _Node):
            yield from _walk(k)


def _find(n: _Node, pred: Callable[[_Node], bool]) -> _Node | None:
    for x in _walk(n):
        if pred(x):
            return x
    return None


def _by_class(n: _Node, cls: str, tag: str | None = None) -> _Node | None:
    return _find(n, lambda x: x.has_class(cls) and (tag is None or x.tag == tag))


def _by_id(n: _Node, ident: str) -> _Node | None:
    return _find(n, lambda x: x.attrs.get("id") == ident)


def _own_text(n: _Node | None) -> str:
    """textContent (scripts and styles excluded), whitespace-collapsed; '' for a missing node."""
    if n is None:
        return ""
    parts: list[str] = []

    def rec(x: _Node) -> None:
        if x.tag in _SKIP_TEXT:
            return
        for k in x.kids:
            rec(k) if isinstance(k, _Node) else parts.append(k)

    rec(n)
    return " ".join("".join(parts).split())


def _body_text(n: _Node | None) -> str:
    """Readable description text (block tags become line breaks; scripts and styles dropped)."""
    if n is None:
        return ""
    out: list[str] = []

    def rec(x: _Node) -> None:
        if x.tag in _SKIP_TEXT:
            return
        brk = x.tag in _BLOCK
        if brk:
            out.append("\n")
        for k in x.kids:
            rec(k) if isinstance(k, _Node) else out.append(k)
        if brk:
            out.append("\n")

    rec(n)
    s = "".join(out)
    return "\n".join(" ".join(line.split()) for line in s.split("\n") if line.strip())


def _meta(root: _Node, prop: str) -> str:
    n = _find(root, lambda x: x.tag == "meta" and x.attrs.get("property") == prop)
    return (n.attrs.get("content") or "") if n else ""


def _json_scripts(root: _Node, **match: str) -> list[Any]:
    """JSON.parse of every <script> whose attributes match (unparseable blocks are skipped)."""
    out: list[Any] = []
    for x in _walk(root):
        if x.tag == "script" and all(x.attrs.get(k.rstrip("_")) == v for k, v in match.items()):
            try:
                out.append(json.loads("".join(k for k in x.kids if isinstance(k, str))))
            except (ValueError, RecursionError):  # deeply nested JSON raises RecursionError, not ValueError
                continue
    return out


def _bracket_slice(s: str, key: str, open_: str, close: str) -> str:
    """The balanced open..close run after the first ``key`` in ``s`` (depth counted outside JSON strings), else ''.
    Mirrors freehire nextflight.go:117-150."""
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


def _html_text(value: Any) -> str:
    """Plain text of an HTML fragment with <script>/<style> dropped (the references run sanitizeHTML on every body)."""
    if not value:
        return ""
    try:
        return _body_text(_parse(str(value)))
    except Exception:  # one bad ad must not cost the whole board
        return ""


def _commas(*parts: Any) -> str:
    """Comma-join the non-empty parts (freehire joinNonEmpty: helpers.go:245)."""
    return ", ".join(str(p).strip() for p in parts if p and str(p).strip())


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


def _token(row: dict[str, Any]) -> str:
    return str(row.get("token") or row.get("board") or "").strip()


def _on_host(url: str, host: str) -> bool:
    """True only for an https URL on exactly ``host`` (a portal never links off-host; mirrors kit_ats iCIMS)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    return parts.scheme == "https" and parts.netloc.lower() == host.lower()


def _page(http: Http, url: str) -> tuple[int, str | None]:
    """GET a page as text: (status, text) with text None on any failure."""
    st, page = _get(http, url, as_json=False, headers=_HTML)
    return st, page if st == 200 and isinstance(page, str) else None


# --------------------------------------------------------------------------- Betterteam
# Borrowed from freehire internal/ingest/sources/betterteam.go:34 (GET https://<board>.betterteam.com/ lists every
# posting, one page), :36-39 (hrefs "/<slug>-<id>"), :89 (btJobIDPattern ^/([a-z0-9][a-z0-9-]*-\d+)$ on the URL path only),
# :64 (each job page carries a schema.org JobPosting ld+json), :71 (remote from the location text only, never the
# title), :76-82 (title, description, datePosted, jobLocation.address locality/region/country joined by ", ").
# Betterteam's listing shows no title in the reference, so details are not title-gated (NOT IN REFERENCE: the anchor text).
_BT_ID = re.compile(r"^/([a-z0-9][a-z0-9-]*-\d+)$")
_LABEL = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,78}[A-Za-z0-9])?")


def _ld_posting(root: _Node) -> dict[str, Any] | None:
    """First schema.org JobPosting in an application/ld+json block (top level, array or @graph): jobPostingNode, jsonld.go:22-66."""
    for blob in _json_scripts(root, type_="application/ld+json"):
        stack: list[Any] = [blob]
        while stack:
            x = stack.pop()  # DFS in document order: children go on reversed, pop from the end (O(n))
            if isinstance(x, list):
                stack.extend(reversed(x))
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return x
                if isinstance(x.get("@graph"), list):
                    stack.extend(reversed(x["@graph"]))
    return None


def _schema_place(loc: Any) -> str:
    """schema.org jobLocation (one Place or a list) -> "City, Region, Country" (schema.go:43-45)."""
    places = loc if isinstance(loc, list) else [loc]
    out: list[str] = []
    for p in places:
        a = p.get("address") if isinstance(p, dict) else None
        if isinstance(a, dict):
            out.append(_commas(a.get("addressLocality"), a.get("addressRegion"), a.get("addressCountry")))
    return " | ".join(x for x in out if x)


def _betterteam(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Betterteam career site: listing page, then one JobPosting ld+json page per job."""
    board = _token(row)
    if not _LABEL.fullmatch(board):
        return None
    base = f"https://{board}.betterteam.com/"
    _, page = _page(http, base)
    if page is None:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    details = 0
    for a in _walk(_parse(page)):
        href = a.attrs.get("href") if a.tag == "a" else ""
        if not href:
            continue
        try:  # one malformed href must not lose the board
            path = urlsplit(href).path
            path = path if path.startswith("/") else "/" + path
            m = _BT_ID.match(path)
            url = urljoin(base, href)
        except ValueError:
            continue
        if not m or url in seen or not _on_host(url, f"{board}.betterteam.com"):
            continue
        seen.add(url)
        if details >= BETTERTEAM_DETAIL_CAP:
            break
        details += 1
        _, det = _page(http, url)
        if det is None:
            continue  # freehire drops a posting whose page fails (betterteam.go:59-61)
        try:
            p = _ld_posting(_parse(det))
        except Exception:  # noqa: BLE001, S112 - one bad page drops only its own posting
            continue
        if not p or not str(p.get("title") or "").strip():
            continue
        loc = _schema_place(p.get("jobLocation"))
        desc = _html_text(html.unescape(str(p.get("description") or "")))
        jobs.append(_job(row, "betterteam", title=str(p["title"]), url=url, location=loc,
                         posted=_iso_date(p.get("datePosted")), description=desc,
                         remote=True if "remote" in loc.lower() else None,
                         extra={"id": m.group(1)}))
    return jobs


# --------------------------------------------------------------------------- CATS (catsone)
# Borrowed from freehire internal/ingest/sources/catsone.go:37 (GET https://<board>/careers; it redirects to the
# portal /careers/<portalId>), :92 (rows are <a class="table-row">), :78 (catsoneJobIDPattern /careers/\d+/jobs/(\d+);
# a row whose href has no id is skipped), :103 (href resolved against https://<board>/careers, de-duplicated by URL),
# :110-111 (title = .title-cell, location = the cell with data-label="Location"), :63 (description = .job-description of
# the /careers/<portalId>/jobs/<id> page). No pagination: the table lists every open job. Where freehire drops a
# posting whose detail fetch fails (:58-60), the kit does the same for a fetched detail, and keeps the listing-only row
# for a detail it never asked for (cap or title gate; NOT IN REFERENCE).
_CATS_ID = re.compile(r"/careers/\d+/jobs/(\d+)")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")


def _catsone(row: dict[str, Any], http: Http) -> list[Job] | None:
    """CATS career portal: the /careers table lists every job; detail pages hold the description."""
    board = _token(row)
    if not _HOST.fullmatch(board):
        return None
    base = f"https://{board}/careers"
    _, page = _page(http, base)
    if page is None:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    details = 0
    for a in _walk(_parse(page)):
        if a.tag != "a" or not a.has_class("table-row"):
            continue
        href = a.attrs.get("href", "")
        m = _CATS_ID.search(href)
        if not m:
            continue
        try:  # one malformed href must not lose the board
            url = urljoin(base, href)
        except ValueError:
            continue
        if url in seen or not _on_host(url, board):
            continue
        seen.add(url)
        title = _own_text(_by_class(a, "title-cell"))
        loc = _own_text(_find(a, lambda x: x.attrs.get("data-label") == "Location"))
        desc = ""
        if details < CATSONE_DETAIL_CAP and _title_ok(row, title):
            details += 1
            _, det = _page(http, url)
            if det is None:
                continue  # catsone.go:58-60: a failed detail drops just this posting
            try:
                desc = _body_text(_by_class(_parse(det), "job-description"))
            except Exception:  # noqa: BLE001, S112 - one bad page drops only its own posting
                continue
        jobs.append(_job(row, "catsone", title=title, url=url, location=loc, description=desc,
                         remote=True if "remote" in loc.lower() else None, extra={"id": m.group(1)}))
    return [j for j in jobs if j["title"]]


# --------------------------------------------------------------------------- ClinchTalent
# Borrowed from freehire internal/ingest/sources/clinch.go:44 (GET https://<board>/sitemap.xml), :53-56 and :104-111
# (the posting is the sitemap <loc> whose path has "/jobs/<slug>"; other sitemap URLs are marketing pages), :77
# (posted = <lastmod>), :115-124 (trailing "-<uuid>" is the stable id), :61 (description from div.job-description of
# the detail page, best effort), :51-64 and :84-93 (the first AWS-WAF challenge latches detail fetching off for the rest of the run;
# the kit latches on any non-200 and caps details at CLINCH_DETAIL_CAP).
# NOT IN REFERENCE: freehire splits the slug's place tail with its internal/dict/location city dictionary, which is not
# in the reference tree. The kit only recognises a trailing country name from a short list (_CLINCH_COUNTRIES) and
# leaves the city words in the title; with no country match the location stays empty.
_UUID_TAIL = re.compile(r"-([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$")
_CLINCH_COUNTRIES = tuple(sorted((
    "united-states", "united-kingdom", "united-arab-emirates", "canada", "poland", "germany", "france", "ireland",
    "netherlands", "spain", "italy", "india", "australia", "singapore", "japan", "brazil", "mexico", "sweden",
    "switzerland", "israel", "portugal", "denmark", "norway", "finland", "belgium", "austria", "romania", "ukraine",
), key=len, reverse=True))


def _clinch_split(slug: str) -> tuple[str, str]:
    clean = _UUID_TAIL.sub("", slug)
    country = next((c for c in _CLINCH_COUNTRIES if clean.endswith("-" + c) and len(clean) > len(c) + 1), "")
    words = clean[: -len(country) - 1] if country else clean
    return " ".join(w[:1].upper() + w[1:] for w in words.split("-") if w), " ".join(w.capitalize() for w in country.split("-"))


def _clinch(row: dict[str, Any], http: Http) -> list[Job] | None:
    """ClinchTalent / career-pages.com site: postings come from sitemap.xml; descriptions are best effort."""
    board = _token(row)
    if not _HOST.fullmatch(board):
        return None
    _, xml = _page(http, f"https://{board}/sitemap.xml")
    if xml is None:
        return None
    tree = safe_fromstring(xml.lstrip("\ufeff").strip())
    if tree is None:
        return None
    jobs: list[Job] = []
    details, latched = 0, False
    for url_el in tree.iter():
        if not url_el.tag.endswith("}url") and url_el.tag != "url":
            continue
        loc = lastmod = ""
        for c in url_el:
            name = c.tag.rsplit("}", 1)[-1]
            if name == "loc":
                loc = (c.text or "").strip()
            elif name == "lastmod":
                lastmod = (c.text or "").strip()
        if not _on_host(loc, board):  # a sitemap <loc> off the board (or a non-https scheme) is never fetched or emitted
            continue
        i = loc.find("/jobs/")
        slug = loc[i + 6:].strip("/") if i >= 0 else ""
        if not slug:
            continue
        title, place = _clinch_split(slug)
        if not title:
            continue
        desc = ""
        if not latched and details < CLINCH_DETAIL_CAP and _title_ok(row, title):
            details += 1
            st, det = _get(http, loc, as_json=False, headers=_HTML)
            if st == 200 and isinstance(det, str):
                try:
                    desc = _body_text(_by_class(_parse(det), "job-description", "div"))
                except Exception:  # noqa: BLE001 - descriptions are best effort; one bad page costs only its own
                    desc = ""
            else:
                latched = True
        m = _UUID_TAIL.search(slug)
        jobs.append(_job(row, "clinch", title=title, url=loc, location=place, posted=_iso_date(lastmod), description=desc,
                         remote=_remote_from(place), extra={"id": m.group(1) if m else slug}))
    return jobs


# --------------------------------------------------------------------------- Crelate
# Borrowed from freehire internal/ingest/sources/crelate.go:60-68 (board "<portalSlug>:<organizationId>"; ONE call
# GET https://jobs.crelate.com/api/candidateportal/GetAllJobs?requestEnvelope={"Locations":null,"OrganizationId":"<org>",
# "SearchText":null,"Tags":null}), :35-47 (Jobs[] with Id, Title, CompanyName, Description, City, State, Country, Url, JobCode,
# LastPostedOnDate; IsError flags a portal error), :94-97 (company = the posting's CompanyName, else the configured
# name: a portal fronts many client companies), :111-117 (job page https://jobs.crelate.com/portal/<slug>/job/<JobCode>,
# JobCode falling back to Url minus its leading "/"), :121-131 (location "City, State", else Country). No paging.
_CRELATE_SLUG = re.compile(r"[A-Za-z0-9_-]+")
_CRELATE_ORG = re.compile(r"[A-Za-z0-9-]{8,64}")


def _crelate(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Crelate candidate portal: one GetAllJobs call returns every published posting."""
    slug, _, org = _token(row).partition(":")
    if not (_CRELATE_SLUG.fullmatch(slug) and _CRELATE_ORG.fullmatch(org)):
        return None
    envelope = json.dumps({"Locations": None, "OrganizationId": org, "SearchText": None, "Tags": None}, separators=(",", ":"))
    st, data = _get(http, "https://jobs.crelate.com/api/candidateportal/GetAllJobs?" + urlencode({"requestEnvelope": envelope}), headers=_JSON)
    if st != 200 or not isinstance(data, dict) or data.get("IsError") or not isinstance(data.get("Jobs"), list):
        return None
    jobs: list[Job] = []
    for j in data["Jobs"]:
        if not isinstance(j, dict) or not str(j.get("Id") or "").strip():
            continue
        code = str(j.get("JobCode") or "") or str(j.get("Url") or "").lstrip("/")
        place = _commas(j.get("City"), j.get("State")) or str(j.get("Country") or "").strip()
        job = _job(row, "crelate", title=str(j.get("Title") or ""), url=f"https://jobs.crelate.com/portal/{slug}/job/{quote(code, safe='')}",
                   location=place, posted=_iso_date(j.get("LastPostedOnDate")), description=_html_text(j.get("Description")),
                   remote=_remote_from(place), extra={"id": str(j["Id"]), "country": str(j.get("Country") or "")})
        employer = str(j.get("CompanyName") or "").strip()
        if employer:
            job["company"] = employer  # the employer named in the ad, never the portal
        if job["title"] and code:
            jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- eRecruiter
# Borrowed from freehire internal/ingest/sources/erecruiter.go:60 (GET https://skk.erecruiter.pl/GetHtml.ashx?cfg=<cfg>&
# grid=rows&pn=<page>, a JSONP body ({"htm":"<tr…>"})), :124-133 (strip the optional ";" and the parentheses, JSON-decode),
# :144-160 and :171-176 (rows are <tr skkResult='offer'> carrying offerId, externalJobOfferId, externalJobOfferRegionId, comId; a row
# without offerId is skipped), :171-196 (title = td.skk_positionName, city = last td), :148-156 (the other <tr> is a marker
# whose tr="<n>" attribute is the total; stop when seen >= total or a page has no rows), :91 (detail
# Offer.aspx?oid=<offerId>&cfg=<cfg>&ejoId=<extId>&ejorId=<regionId>&comId=<comId>), :200-218 (:200-218: #JobTitle, #WorkPlace with its
# "Miejsce pracy:" label cut at the first ":", body = #offCont minus #JobTitle/#WorkPlace/#Clause, else #t1, #Opportunities,
# #CompanyDescription). The kit walks at most ERECRUITER_LIST_PAGES pages, fetches details for ERECRUITER_DETAIL_CAP
# title-gated rows, and keeps the list-row title/city for the rest (NOT IN REFERENCE: freehire drops rows with no detail).
_CFG = re.compile(r"[0-9A-Fa-f]{32}")
_ER_DROP = frozenset(("JobTitle", "WorkPlace", "Clause"))


def _er_rows(body: str) -> tuple[list[dict[str, str]], int]:
    s = body.strip()
    s = s[:-1].strip() if s.endswith(";") else s
    s = s[1:] if s.startswith("(") else s
    s = s[:-1] if s.endswith(")") else s
    if not s.strip():
        return [], -1
    htm = json.loads(s).get("htm")
    rows: list[dict[str, str]] = []
    total = -1
    for tr in _walk(_parse("<table>" + (htm if isinstance(htm, str) else "") + "</table>")):
        if tr.tag != "tr":
            continue
        if tr.attrs.get("skkresult") != "offer":
            if tr.attrs.get("tr", "").isdigit():
                total = int(tr.attrs["tr"])
            continue
        oid = tr.attrs.get("offerid", "")
        if not oid:
            continue
        tds = [x for x in _walk(tr) if x.tag == "td"]
        title = next((_own_text(td) for td in tds if td.has_class("skk_positionName")), "")
        rows.append({"oid": oid, "ejo": tr.attrs.get("externaljobofferid", ""), "ejor": tr.attrs.get("externaljobofferregionid", ""),
                     "com": tr.attrs.get("comid", ""), "title": title, "city": _own_text(tds[-1]) if tds else ""})
    return rows, total


def _er_detail(root: _Node) -> tuple[str, str, str]:
    title = _own_text(_by_id(root, "JobTitle"))
    place = _own_text(_by_id(root, "WorkPlace"))
    if ":" in place:
        place = place.split(":", 1)[1].strip()
    cont = _by_id(root, "offCont")
    if cont is not None:
        for x in list(_walk(cont)):
            if x.attrs.get("id") in _ER_DROP and x.parent is not None:
                x.parent.kids = [k for k in x.parent.kids if k is not x]
        body = _body_text(cont)
    else:
        body = "\n".join(t for t in (_body_text(_by_id(root, i)) for i in ("t1", "Opportunities", "CompanyDescription")) if t)
    return title, place, body


def _erecruiter(row: dict[str, Any], http: Http) -> list[Job] | None:
    """eRecruiter Polska career board (skk.erecruiter.pl): JSONP list pages, then Offer.aspx details."""
    cfg = _token(row)
    if not _CFG.fullmatch(cfg):
        return None
    listed: list[dict[str, str]] = []
    total, seen = -1, 0
    for page in range(1, ERECRUITER_LIST_PAGES + 1):
        st, body = _get(http, f"https://skk.erecruiter.pl/GetHtml.ashx?cfg={cfg}&grid=rows&pn={page}", as_json=False, headers=_HTML)
        if st != 200 or not isinstance(body, str):
            if page == 1:
                return None
            break
        rows, t = _er_rows(body)
        total = t if total < 0 <= t else total
        if not rows:
            break
        listed += rows
        seen += len(rows)
        if 0 <= total <= seen:
            break
    jobs: list[Job] = []
    details = 0
    for r in listed:
        url = (f"https://skk.erecruiter.pl/Offer.aspx?oid={r['oid']}&cfg={cfg}&ejoId={r['ejo']}&ejorId={r['ejor']}&comId={r['com']}")
        title, place, desc = r["title"], r["city"], ""
        if details < ERECRUITER_DETAIL_CAP and _title_ok(row, title):
            details += 1
            st, det = _get(http, url, as_json=False, headers=_HTML)
            if st != 200 or not isinstance(det, str):
                continue  # erecruiter.go:92-95: an unreachable detail drops the posting
            t2, p2, desc = _er_detail(_parse(det))
            title, place = t2 or title, p2 or place
            if not desc:
                continue  # erecruiter.go:106-108: a body-less offer is closed
        if title:
            jobs.append(_job(row, "erecruiter", title=title, url=url, location=place, description=desc, remote=_remote_from(place),
                             extra={"id": r["oid"]}))
    return jobs


# --------------------------------------------------------------------------- gr8people
# Borrowed from freehire internal/ingest/sources/gr8people.go:119 (GET https://<board>/jobs; the board is the whole
# careers host on gr8people.com or workgr8.com), :41 (the page's __NEXT_DATA__ embeds the anonymous session JWT, regex
# "token"\s*:\s*"(eyJ[^"]+)"), :146-147 (POST https://<board>/graphql with Authorization: Bearer <token>), :37 and :176-186
# (operationName searchJobs, variables query "", start 0, first 50, filters {}, after omitted on the first page),
# :48-66 (the searchJobs query text, copied verbatim), :153-165 (stop on no nodes or hasNextPage false; only page 0
# failing fails the board), :97-110 (results.nodes[] with key, title, descriptionHTML, workplaceType, postedOn, positionType
# {name}, primaryPlace{name}, places.nodes[].name), :86-99 (location = distinct place names joined "; ", else
# primaryPlace), :202 (job URL https://<board>/jobs/<key>). The kit caps the walk at GR8PEOPLE_PAGES pages.
_GR8_TOKEN = re.compile(r'"token"\s*:\s*"(eyJ[^"]+)"')
_GR8_QUERY = """query searchJobs($query: String, $start: Int, $first: Int, $after: String, $filters: JobPostingSearchFiltersInput) {
  searchJobs: searchJobPostings(query: $query, start: $start, first: $first, after: $after, filters: $filters) {
    results {
      nodes {
        key
        title
        descriptionHTML
        workplaceType
        postedOn
        positionType { name }
        primaryPlace { name }
        places { nodes { name } }
      }
      pageInfo { hasNextPage endCursor }
      totalCount
    }
  }
}"""


def _dict(v: Any) -> dict[str, Any]:
    """A nested GraphQL field that should be an object; anything else reads as empty."""
    return v if isinstance(v, dict) else {}


def _gr8people(row: dict[str, Any], http: Http) -> list[Job] | None:
    """gr8people career site: mint the anonymous token from /jobs, then page the tenant's GraphQL search."""
    board = _token(row).lower()
    if not _HOST.fullmatch(board):
        return None
    _, home = _page(http, f"https://{board}/jobs")
    m = _GR8_TOKEN.search(home or "")
    if not m:
        return None
    headers = dict(_POST, Authorization="Bearer " + m.group(1))
    nodes: list[dict[str, Any]] = []
    after = ""
    for page in range(GR8PEOPLE_PAGES):
        variables: dict[str, Any] = {"query": "", "start": 0, "first": 50, "filters": {}}
        if after:
            variables["after"] = after
        body = json.dumps({"operationName": "searchJobs", "query": _GR8_QUERY, "variables": variables}).encode()
        st, data = _get(http, f"https://{board}/graphql", method="POST", data=body, headers=headers)
        res = ((data.get("data") or {}).get("searchJobs") or {}).get("results") if st == 200 and isinstance(data, dict) else None
        if not isinstance(res, dict) or not isinstance(res.get("nodes"), list):
            if page == 0:
                return None
            break
        if not res["nodes"]:
            break
        nodes += [n for n in res["nodes"] if isinstance(n, dict)]
        info = res.get("pageInfo") or {}
        if not info.get("hasNextPage") or not info.get("endCursor"):
            break
        after = str(info["endCursor"])
    jobs: list[Job] = []
    for n in nodes:
        key, title = str(n.get("key") or ""), str(n.get("title") or "").strip()
        if not key or not title:
            continue
        pl_nodes = _dict(n.get("places")).get("nodes")
        names = [str(p.get("name") or "").strip() for p in (pl_nodes if isinstance(pl_nodes, list) else []) if isinstance(p, dict)]
        place = "; ".join(dict.fromkeys(x for x in names if x)) or str(_dict(n.get("primaryPlace")).get("name") or "")
        mode = str(n.get("workplaceType") or "").replace("_", "-").lower()
        pt = str(_dict(n.get("positionType")).get("name") or "").strip().lower()
        jobs.append(_job(row, "gr8people", title=title, url=f"https://{board}/jobs/{quote(key, safe='')}", location=place,
                         posted=_iso_date(n.get("postedOn")), description=_html_text(n.get("descriptionHTML")),
                         remote=True if mode == "remote" or "remote" in place.lower() else None,
                         extra={"work_mode": mode, "employment_type": {"full time": "full_time", "full-time": "full_time", "part time": "part_time", "part-time": "part_time"}.get(pt, "")}))
    return jobs


# --------------------------------------------------------------------------- HR-Manager
# Borrowed from freehire internal/ingest/sources/hrmanager.go:35 (GET https://api.hr-manager.net/jobportal.svc/<alias>/
# positionlist/json/?incads=1&take=2000: ONE keyless call, advertisement HTML inline), :54-56 (alias [A-Za-z0-9_-]+),
# :64-90 (Items[]: Id, Name, ProjectType, AdvertisementUrl, Published, Created, WorkPlace, PositionLocationMultiSelection
# [].Name, Department{City,Country}, Advertisements[].Introduction+Content), :127-150 (the listing must prove itself whole:
# TransactionStatus.StatusCode 0, CustomerAlias equal to the board, PositionCountList equal to the rows read, at most 2
# postings short of PositionCountSearch, ids distinct; otherwise the board fails), :105 and :49 (ProjectType OpenApplication is the
# standing unsolicited-application entry, skipped), :190-201 (location = WorkPlace, else the picklist names joined "; ",
# else Department.City), :167-175 and :206-218 (timestamps are .NET /Date(<ms>+0200)/; Published, else Created). The employer is the
# customer named in the board row. NOT IN REFERENCE: nothing beyond the Go file; the freehire "Countries" normaliser is
# replaced by the raw Department.Country text in extra.country.
_HRM_ALIAS = re.compile(r"[A-Za-z0-9_-]+")
_HRM_DATE = re.compile(r"^/Date\((-?\d+)(?:[+-]\d{4})?\)/$")
_HRM_TAKE, _HRM_MAX_HIDDEN = 2000, 2


def _hrm_date(v: Any) -> str:
    m = _HRM_DATE.match(str(v or ""))
    if not m or int(m.group(1)) <= 0:
        return ""
    try:
        return datetime.fromtimestamp(int(m.group(1)) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except (ValueError, OverflowError, OSError):
        return ""  # hrmanager.go parseEpochMillis drops only the bad date


def _hrmanager(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HR-Manager job-portal API (Danish ATS): the whole customer listing in one request."""
    alias = _token(row)
    if not _HRM_ALIAS.fullmatch(alias):
        return None
    st, d = _get(http, f"https://api.hr-manager.net/jobportal.svc/{alias}/positionlist/json/?incads=1&take={_HRM_TAKE}", headers=_JSON)
    if st != 200 or not isinstance(d, dict) or not isinstance(d.get("Items"), list):
        return None
    ts, total, in_list = d.get("TransactionStatus"), d.get("PositionCountSearch"), d.get("PositionCountList")
    if not isinstance(ts, dict) or ts.get("StatusCode") != 0 or not isinstance(total, int) or not isinstance(in_list, int):
        return None
    items = [i for i in d["Items"] if isinstance(i, dict)]
    ids = [i.get("Id") for i in items]
    if (str(d.get("CustomerAlias") or "").lower() != alias.lower() or total > _HRM_TAKE or len(items) != in_list
            or len(items) > total or total - len(items) > _HRM_MAX_HIDDEN or len(set(map(str, ids))) != len(ids)):
        return None
    jobs: list[Job] = []
    for it in items:
        title = " ".join(str(it.get("Name") or "").split())
        url = str(it.get("AdvertisementUrl") or "")
        jid = it.get("Id")
        if it.get("ProjectType") == "OpenApplication" or not isinstance(jid, int) or jid <= 0 or not title or not url.startswith("https://"):
            continue
        body = ""
        for ad in it.get("Advertisements") or []:
            if isinstance(ad, dict) and str(ad.get("Content") or "").strip():
                body = str(ad.get("Introduction") or "") + str(ad.get("Content"))
                break
        dep: dict[str, Any] = it["Department"] if isinstance(it.get("Department"), dict) else {}
        names = [str(x.get("Name") or "").strip() for x in (it.get("PositionLocationMultiSelection") or []) if isinstance(x, dict)]
        place = str(it.get("WorkPlace") or "").strip() or "; ".join(dict.fromkeys(n for n in names if n)) or str(dep.get("City") or "").strip()
        jobs.append(_job(row, "hrmanager", title=title, url=url, location=place, posted=_hrm_date(it.get("Published")) or _hrm_date(it.get("Created")),
                         description=_html_text(body), remote=_remote_from(place), extra={"id": str(jid), "country": str(dep.get("Country") or "")}))
    return jobs


# --------------------------------------------------------------------------- iSmartRecruit
# Borrowed from freehire internal/ingest/sources/ismartrecruit.go:61-63 (the board "<domain>_<code>" is wrapped as
# tenantId = "E7p" + rawbase64(board) + "Q9e"; GET https://app.ismartrecruit.com/openJobWebsite?tenantId=<tenant>&view=grid&
# col=2&lang=en server-renders EVERY posting, no paging), :104 and :119-120 (cards are <a class="jobListing_Data"> with
# title in .ui-panel-title and location in .ui-panelgrid-cell), :129-134 (the href's x=<token> decodes, after stripping
# the E7p/Q9e envelope, to "<domain>_<id>_W_<lang>"; the numeric id is the dedup id and a card without one is skipped),
# :89 (the detail page's og:description holds the body; a failed detail only costs the description), :92-94 (og:title as
# title fallback). Detail requests are capped at ISMART_DETAIL_CAP and title-gated (NOT IN REFERENCE).
_ISMART_X = re.compile(r"[?&]x=([^&]+)")
_ISMART_ID = re.compile(r"_(\d+)_W_")
_ISMART_BOARD = re.compile(r"[A-Za-z0-9._-]{3,120}")


def _ismart_id(href: str) -> str:
    m = _ISMART_X.search(href)
    tok = m.group(1) if m else ""
    if not (tok.startswith("E7p") and tok.endswith("Q9e")):
        return ""
    mid = tok[3:-3]
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            dec = decode(mid + "=" * (-len(mid) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        found = _ISMART_ID.search(dec)
        if found:
            return found.group(1)
    return ""


def _ismartrecruit(row: dict[str, Any], http: Http) -> list[Job] | None:
    """iSmartRecruit open job website: one grid page lists every posting."""
    board = _token(row)
    if not _ISMART_BOARD.fullmatch(board):
        return None
    tenant = "E7p" + base64.b64encode(board.encode()).decode().rstrip("=") + "Q9e"
    _, page = _page(http, f"https://app.ismartrecruit.com/openJobWebsite?tenantId={tenant}&view=grid&col=2&lang=en")
    if page is None:
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    details = 0
    for a in _walk(_parse(page)):
        if a.tag != "a" or not a.has_class("jobListing_Data"):
            continue
        href = a.attrs.get("href", "")
        jid = _ismart_id(href)
        if not jid or jid in seen or not href.startswith("https://app.ismartrecruit.com/"):
            continue
        seen.add(jid)
        title, place, desc = _own_text(_by_class(a, "ui-panel-title")), _own_text(_by_class(a, "ui-panelgrid-cell")), ""
        if details < ISMART_DETAIL_CAP and _title_ok(row, title):
            details += 1
            _, det = _page(http, href)
            if det is not None:
                root = _parse(det)
                desc = _html_text(_meta(root, "og:description"))
                title = title or _meta(root, "og:title").strip()
        if title:
            jobs.append(_job(row, "ismartrecruit", title=title, url=href, location=place, description=desc,
                             remote=_remote_from(place), extra={"id": jid}))
    return jobs


# --------------------------------------------------------------------------- Loxo
# Borrowed from freehire internal/ingest/sources/loxo.go:18-24 (the board is "<host>/<slug>": an agency subdomain such as
# fitnext.app.loxo.co, bare app.loxo.co or a pod such as pod4.app.loxo.co), :44 (GET https://<board>), :96 and :134-140 (each
# posting is linked /job/<base64>, regex /job/([A-Za-z0-9=_-]+)(?:$|[/?#]); links resolved against the board URL and
# de-duplicated), :101-118 (the base64 decodes to "<agency_id>-<slug>", the stable id; the raw token when it does not decode),
# :71-74 (title = og:title, else the document <title> minus a trailing " | <agency>"), :124-146 (description = the "description"
# of the first <script type="application/json"> block), :150-161 (location = the og:description text between "Location:" and
# "Salary:"), :168-182 (on a hub board, an agency hosting many clients, the client is split off the title at " — " or " @ ";
# the ASCII " - " is never a delimiter; otherwise the agency name), :205-207 and :222-231 (app.loxo.co is rate limited: 350 ms between
# requests; subdomains and pods are not). A failed detail drops that posting (:56-60).
_LOXO_ID = re.compile(r"/job/([A-Za-z0-9=_-]+)(?:$|[/?#])")
_LOXO_BOARD = re.compile(r"[A-Za-z0-9.-]+/[A-Za-z0-9_.-]+")


def _loxo_id(href: str) -> str:
    m = _LOXO_ID.search(href)
    if not m:
        return ""
    tok = m.group(1)
    for decode in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            dec = decode(tok + "=" * (-len(tok) % 4)).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if "-" in dec and dec.isprintable():
            return dec
    return tok


def _loxo(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Loxo agency careers board: the page links every /job/<base64>; each detail page carries the role."""
    board = _token(row)
    if not _LOXO_BOARD.fullmatch(board):
        return None
    base = f"https://{board}"
    shared = board.split("/", 1)[0].lower() == "app.loxo.co"
    _, page = _page(http, base)
    if page is None:
        return None
    links: dict[str, str] = {}  # id -> URL (freehire dedups by URL; the kit also drops the ?t=<cache-buster> twin)
    for a in _walk(_parse(page)):
        href = a.attrs.get("href", "") if a.tag == "a" else ""
        if href and _LOXO_ID.search(href) and _loxo_id(href):
            url = urljoin(base, href)
            if _on_host(url, board.split("/", 1)[0]):
                links.setdefault(_loxo_id(href), url)
    jobs: list[Job] = []
    for jid, url in list(links.items())[:LOXO_DETAIL_CAP]:
        if shared:
            _sleep(LOXO_SHARED_HOST_GAP)
        _, det = _page(http, url)
        if det is None:
            continue
        root = _parse(det)
        title = _meta(root, "og:title").strip()
        if not title:
            doc = _own_text(_find(root, lambda x: x.tag == "title"))
            title = doc.rsplit(" | ", 1)[0].strip() if " | " in doc else doc
        desc = ""
        for blob in _json_scripts(root, type_="application/json"):
            if isinstance(blob, dict) and blob.get("description"):
                desc = str(blob["description"])
                break
        if not title and not desc:
            continue  # not a rendered job page
        company = ""
        if row.get("hub"):
            for sep in (" — ", " @ "):
                i = title.rfind(sep)
                if i >= 0 and title[i + len(sep):].strip() and title[:i].strip():
                    company, title = title[i + len(sep):].strip(), title[:i].strip()
                    break
        og = _meta(root, "og:description")
        place = ""
        if "Location:" in og:
            place = og.split("Location:", 1)[1].split("Salary:", 1)[0].strip()
        job = _job(row, "loxo", title=title, url=url, location=place, description=_html_text(desc), remote=_remote_from(place), extra={"id": jid})
        if company:
            job["company"] = company
        if job["title"]:
            jobs.append(job)
    return jobs


# --------------------------------------------------------------------------- Mindsight
# Borrowed from freehire internal/ingest/sources/mindsight.go:13 (every tenant lives at https://oportunidades.mindsight.com.br/
# <slug>), :53 (the page embeds __NEXT_DATA__; the balanced {...} after that key), :60 (props.pageProps.publicJobPostings[]:
# id, name, status, work_model, country, state, city, external_publication_start_at, created_at), :17 and :79-81 (a post with no id
# or a status other than IN_PROGRESS is dropped), :81 (job page https://oportunidades.mindsight.com.br/<slug>/<id>), :86-96 (the
# detail page's __NEXT_DATA__ props.pageProps.jobPosting.description; a failed detail only costs the description), :106-109 (posted =
# publication start, else created_at), :118 (location "city, state, country"), :127-138 (work_model REMOTE / HYBRID /
# IN_PERSON). Details are title-gated and capped (NOT IN REFERENCE).
_MS_SLUG = re.compile(r"[A-Za-z0-9_-]{1,80}")
_MS_BASE = "https://oportunidades.mindsight.com.br"


def _ms_next(page: str | None) -> Any:
    raw = _bracket_slice(page or "", "__NEXT_DATA__", "{", "}")
    try:
        return json.loads(raw) if raw else None
    except ValueError:
        return None


def _mindsight(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Mindsight (Brazil) career page: listing from __NEXT_DATA__, description from each job page."""
    slug = _token(row)
    if not _MS_SLUG.fullmatch(slug):
        return None
    _, page = _page(http, f"{_MS_BASE}/{slug}")
    data = _ms_next(page)
    try:
        posts = data["props"]["pageProps"]["publicJobPostings"]
    except (TypeError, KeyError):
        return None
    if not isinstance(posts, list):
        return None
    jobs: list[Job] = []
    details = 0
    for p in posts:
        if not isinstance(p, dict) or not isinstance(p.get("id"), int) or p["id"] <= 0 or p.get("status") not in (None, "", "IN_PROGRESS"):
            continue
        title = str(p.get("name") or "").strip()
        if not title:
            continue
        url, desc = f"{_MS_BASE}/{slug}/{p['id']}", ""
        if details < MINDSIGHT_DETAIL_CAP and _title_ok(row, title):
            details += 1
            _, det = _page(http, url)
            d = _ms_next(det)
            try:
                desc = _html_text(d["props"]["pageProps"]["jobPosting"]["description"])
            except (TypeError, KeyError):
                desc = ""
        mode = {"REMOTE": "remote", "HYBRID": "hybrid", "IN_PERSON": "onsite"}.get(str(p.get("work_model") or "").strip().upper(), "")
        jobs.append(_job(row, "mindsight", title=title, url=url, location=_commas(p.get("city"), p.get("state"), p.get("country")),
                         posted=_iso_date(p.get("external_publication_start_at")) or _iso_date(p.get("created_at")),
                         description=desc, remote=True if mode == "remote" else None,
                         extra={"id": str(p["id"]), "work_mode": mode, "country": str(p.get("country") or "")}))
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "betterteam": _wrap(_betterteam),
    "catsone": _wrap(_catsone),
    "clinch": _wrap(_clinch),
    "crelate": _wrap(_crelate),
    "erecruiter": _wrap(_erecruiter),
    "gr8people": _wrap(_gr8people),
    "hrmanager": _wrap(_hrmanager),
    "ismartrecruit": _wrap(_ismartrecruit),
    "loxo": _wrap(_loxo),
    "mindsight": _wrap(_mindsight),
}
