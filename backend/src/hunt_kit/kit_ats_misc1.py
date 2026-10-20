"""Four more ATS board readers for the hunt kit: Betterteam, CATS (catsone), ClinchTalent and Crelate (eRecruiter,
gr8people, HR-Manager, iSmartRecruit, Loxo and Mindsight follow in the next slice). Same contract as kit_ats.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job. Every request is a plain public page or keyless API call
(no key, login or cookie).

Board tokens (companies.json ``board`` / ``token``):
  betterteam    tenant subdomain from <tenant>.betterteam.com
  catsone       careers host, e.g. sphereinc.catsone.com (or a custom domain mapped to CATS)
  clinch        career-site host, e.g. careers.withwaymo.com
  crelate       <portalSlug>:<organizationId> from jobs.crelate.com/portal/<slug>

Endpoints, selectors and field names are adapted from the MIT-licensed freehire adapters
(internal/ingest/sources/<name>.go, commit in NOTICE.md); each reader below cites the lines it follows. Anything the
reference does not state is marked NOT IN REFERENCE.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
import re
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

# Detail-page requests per board (the rest keep their listing fields, no description). The references fetch every
# detail with a worker pool; the kit keeps a small request budget instead.
BETTERTEAM_DETAIL_CAP = 25
CATSONE_DETAIL_CAP = 25
CLINCH_DETAIL_CAP = 10


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


def _json_scripts(root: _Node, **match: str) -> list[Any]:
    """JSON.parse of every <script> whose attributes match (unparseable blocks are skipped)."""
    out: list[Any] = []
    for x in _walk(root):
        if x.tag == "script" and all(x.attrs.get(k.rstrip("_")) == v for k, v in match.items()):
            try:
                out.append(json.loads("".join(k for k in x.kids if isinstance(k, str))))
            except ValueError:
                continue
    return out


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
            x = stack.pop(0)
            if isinstance(x, list):
                stack = x + stack
            elif isinstance(x, dict):
                t = x.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return x
                if isinstance(x.get("@graph"), list):
                    stack = x["@graph"] + stack
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
        path = urlsplit(href).path
        path = path if path.startswith("/") else "/" + path
        m = _BT_ID.match(path)
        url = urljoin(base, href)
        if not m or url in seen or not _on_host(url, f"{board}.betterteam.com"):
            continue
        seen.add(url)
        if details >= BETTERTEAM_DETAIL_CAP:
            break
        details += 1
        _, det = _page(http, url)
        if det is None:
            continue  # freehire drops a posting whose page fails (betterteam.go:59-61)
        p = _ld_posting(_parse(det))
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
        url = urljoin(base, href)
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
            desc = _body_text(_by_class(_parse(det), "job-description"))
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
                desc = _body_text(_by_class(_parse(det), "job-description", "div"))
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


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "betterteam": _wrap(_betterteam),
    "catsone": _wrap(_catsone),
    "clinch": _wrap(_clinch),
    "crelate": _wrap(_crelate),
}
