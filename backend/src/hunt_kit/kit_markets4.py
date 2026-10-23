"""Job-board readers for the hunt kit, part 4: Yourator (Taiwan), CareerViet (Vietnam) and OCC Mundial (Mexico). Same
contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  yourator    any word (``yourator:all``): yourator.co is ONE national board
  careerviet  ``all`` for the whole board, or one keyword slug (``careerviet:ke-toan``): careerviet.vn is ONE national board
  occ         one keyword slug, REQUIRED (``occ:automatizacion``): OCC has no browsable "all jobs" listing (occ.mjs:36-45)

Endpoints, params and field names are adapted from the MIT-licensed career-ops provider adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures shaped
like the references' comments only. A refusal (403/429, a Cloudflare challenge) ends the board: the first page failing fails the
board, a later page keeps what was gathered. No detail page is opened, so no posting here carries a description.

Deviations from the references, on purpose: a job link is only ever on the board's own host over https (yourator.mjs emits the
employer's ``thirdPartyUrl`` from any https host; the kit keeps the yourator.co posting page instead, so no link leaves the
board), and OCC's optional ``states`` list is not read (the token is one keyword; occ.mjs:36-45 allows a state path as well).

NOT built, and why (the host is reported as unsupported by ``--why-missed``):
  AIJobs (aijobs.net, freehire aijobs.go:17-23): a Django CSRF-protected POST that needs a cookie jar the kit's ``http`` cannot hold.
Already provided elsewhere on main (so skipped here): Get on Board and getManfred (kit_niche.py), Platsbanken (``jobtech`` in
kit_feeds.py).
"""
# ruff: noqa: E501
from __future__ import annotations

import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator
from urllib.parse import quote, urljoin, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _get,
    _job,
    _Node,
    _page_st,
    _slug,
    _tree,
    _walk,
    _wrap,
)

_JSON = {"Accept": "application/json"}

YOURATOR_PAGES = 120  # yourator.mjs:62-64: the live board was 88 pages of ~20; the walk stops on hasMore anyway
CAREERVIET_PAGES = 10  # careerviet.mjs:74: ~50 postings a page
OCC_PAGES = 3  # occ.mjs:121: the reference default (hard cap 10)

_YOURATOR_HOST = "www.yourator.co"
_CV_HOST = "careerviet.vn"
_OCC_HOST = "www.occ.com.mx"


def _line(value: Any) -> str:
    """One line of plain text from a short field (control characters and runs of blanks collapsed)."""
    return " ".join(str(value or "").replace("\x00", "").split()) if isinstance(value, (str, int)) else ""


def _own_url(raw: Any, base: str, host: str) -> str:
    """Absolute https link on exactly ``host`` (fragment dropped), else '' (never a foreign host, never plain http)."""
    try:
        u = urlsplit(urljoin(base, str(raw or "").strip()))
    except ValueError:
        return ""
    return u._replace(fragment="").geturl() if u.scheme == "https" and (u.hostname or "").lower() == host else ""


def _cards(root: _Node, pred: Callable[[_Node], bool]) -> Iterator[_Node]:
    """Nodes matching ``pred`` in document order (one walk, so linear even when a hostile page never closes a card)."""
    for n in _walk(root):
        if pred(n):
            yield n


def _own(card: _Node, pred: Callable[[_Node], bool]) -> Iterator[_Node]:
    """Descendants of ``card`` matching ``pred``, not looking inside another card: each node is visited by one card only."""
    stack = [iter(card.kids)]
    while stack:
        for k in stack[-1]:
            if isinstance(k, _Node):
                if _is_card(k):
                    continue
                if pred(k):
                    yield k
                stack.append(iter(k.kids))
                break
        else:
            stack.pop()


_CV_ID = re.compile(r"^job-item-([A-Za-z0-9]+)$")


def _is_card(n: _Node) -> bool:
    return bool(_CV_ID.match(n.attrs.get("id", ""))) or (n.has_class("card-job-offer") and bool(n.attrs.get("data-id")))


# --------------------------------------------------------------------------- Yourator
# Borrowed from career-ops providers/yourator.mjs:3-9 (GET https://www.yourator.co/api/v4/jobs?page=N, public JSON, answer
# { payload: { hasMore, jobs: [ { id, name, path, location, company: { brand }, thirdPartyUrl } ] } }), :52-58 (the default view is
# the whole board: no search parameter works, so every page is walked), :62-64 (page bound above the live 88 pages), :98-112 and
# :161-179 (path must be site-root-relative: "//host/x" and "/\\" are refused; name = title, company.brand = employer, location is
# a Taiwanese city name), :186-208 (hasMore is the only stop; a payload without a jobs array is a failed page) and :48-50 (no
# absolute date is published, so none is invented). thirdPartyUrl (:112-130) is NOT emitted: the kit lists the yourator.co page.
def _yourator(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Yourator board (paged JSON, whole board; token ignored)."""
    out: list[Job] = []
    seen: set[str] = set()
    for page in range(1, YOURATOR_PAGES + 1):
        st, data = _get(http, f"https://{_YOURATOR_HOST}/api/v4/jobs?page={page}", headers=_JSON)
        payload = data.get("payload") if isinstance(data, dict) else None
        jobs = payload.get("jobs") if isinstance(payload, dict) else None
        if st != 200 or not isinstance(jobs, list):
            if page == 1:
                return None
            break  # a refusal or a broken page ends the walk; earlier pages are kept
        for p in jobs:
            try:
                path = str(p.get("path") or "").strip() if isinstance(p, dict) else ""
                title = _line(p.get("name")) if isinstance(p, dict) else ""
                if not title or not path.startswith("/") or path.startswith(("//", "/\\")):
                    continue
                url = _own_url(path, f"https://{_YOURATOR_HOST}/", _YOURATOR_HOST)
                if not url or url in seen:
                    continue
                seen.add(url)
                co = p.get("company")
                j = _job(row, "yourator", title=title, url=url, location=_line(p.get("location")),
                         extra={"job_id": _line(p.get("id"))})
                j["company"] = _line(co.get("brand")) if isinstance(co, dict) and _line(co.get("brand")) else "?"
                out.append(j)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        if not isinstance(payload, dict) or payload.get("hasMore") is not True:
            break
    return out


# --------------------------------------------------------------------------- CareerViet
# Borrowed from career-ops providers/careerviet.mjs:61-62 (listing https://careerviet.vn/viec-lam/<slug>-vi.html, later pages
# <slug>-trang-<N>-vi.html), :183-195 (slug "tat-ca-viec-lam" for the whole board, "<keyword>-k" for a keyword; a city has no working
# URL without a keyword and only Ho Chi Minh was verified, so no city is read), :27-45 and :228-265 (a card is the node with
# id="job-item-<ID>"; title and link from its first a.job_link, employer from a.company-name's title, place from the first <li> of
# .location, the date from the "Cap nhat" (updated) <time>, never the "Han nop" deadline; a link off careerviet.vn or a malformed
# href drops that one card), :200-214 (DD-MM-YYYY), :268-280 (page 1 with cards but none parsed is a broken board, not an empty one)
# and :319-325 (a page with no new card ends the walk).
_CV_UPDATED = "Cập nhật"


def _cv_date(text: str) -> str:
    m = re.fullmatch(r"(\d{1,2})[-/](\d{1,2})[-/](\d{4})", text.strip())
    if not m:
        return ""
    try:
        return datetime(int(m.group(3)), int(m.group(2)), int(m.group(1))).strftime("%Y-%m-%d")
    except ValueError:
        return ""


def _careerviet_cards(page: str) -> list[tuple[str, dict[str, str]]]:
    root = _tree(page)
    out: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for card in _cards(root, lambda n: bool(_CV_ID.match(n.attrs.get("id", "")))):
        try:
            m = _CV_ID.match(card.attrs["id"])
            cid = m.group(1) if m else ""
            if not cid or cid in seen:
                continue
            link = next(_own(card, lambda n: n.tag == "a" and n.has_class("job_link") and bool(n.attrs.get("href"))), None)
            title = _line(link.attrs.get("title") or link.text()) if link is not None else ""
            url = _own_url(link.attrs.get("href"), f"https://{_CV_HOST}/", _CV_HOST) if link is not None else ""
            if not title or not url:
                continue
            seen.add(cid)
            comp = next(_own(card, lambda n: n.tag == "a" and n.has_class("company-name")), None)
            place = next(_own(card, lambda n: n.has_class("location")), None)
            li = next(_own(place, lambda n: n.tag == "li"), None) if place is not None else None
            posted = ""
            for n in _own(card, lambda n: n.tag == "li" and _CV_UPDATED in n.text()):
                t = next(_own(n, lambda k: k.tag == "time"), None)
                posted = _cv_date(t.text()) if t is not None else ""
                break
            out.append((cid, {"title": title, "url": url, "company": _line(comp.attrs.get("title") or comp.text()) if comp is not None else "",
                              "location": _line(li.text()) if li is not None else "", "posted": posted}))
        except Exception:  # noqa: BLE001, S112 - one bad card must not lose the page
            continue
    return out


def _careerviet(row: dict[str, Any], http: Http) -> list[Job] | None:
    """CareerViet listing pages (whole board for ``all``, else one keyword slug)."""
    tok = str(row.get("token") or "").strip().lower()
    if not _slug(tok):
        return None
    slug = "tat-ca-viec-lam" if tok == "all" else quote(tok, safe="") + "-k"
    out: list[Job] = []
    seen: set[str] = set()
    for page in range(1, CAREERVIET_PAGES + 1):
        suffix = "-vi.html" if page == 1 else f"-trang-{page}-vi.html"
        st, text = _page_st(http, f"https://{_CV_HOST}/viec-lam/{slug}{suffix}")
        if text is None:
            if page == 1:
                return None
            break
        cards = _careerviet_cards(text)
        if not cards:
            if page == 1 and re.search(r"id=[\"']job-item-[A-Za-z0-9]+[\"']", text):
                return None  # cards on the page but none read: a changed layout is a failed board, not an empty one
            break
        fresh = 0
        for cid, c in cards:
            if cid in seen:
                continue
            seen.add(cid)
            fresh += 1
            j = _job(row, "careerviet", title=c["title"], url=c["url"], location=c["location"], posted=c["posted"], extra={"job_id": cid})
            j["company"] = c["company"] or "?"
            out.append(j)
        if not fresh:
            break
    return out


# --------------------------------------------------------------------------- OCC Mundial
# Borrowed from career-ops providers/occ.mjs:9-19 (no JSON API: server-rendered search pages https://www.occ.com.mx/empleos/de-<keyword>/,
# a later page is the SLUG suffix de-<keyword>-pagina-<N>/, never a query parameter), :36-45 (a keyword is required, there is no default),
# :113-133 (slug: accents folded, lower case, other runs to a hyphen), :135-171 (an empty search says "no hay empleos que coincidan"),
# :173-198 (a card is div.card-job-offer with data-id; h2 = title, the first span of the h-[21px] row = employer, the p inside
# .no-alter-loc-text = place; the posting is /empleo/oferta/<id>/; "Empresa confidencial" is OCC's placeholder, kept as "?"),
# :250-262 (page 1 parsing to nothing is an empty search only when the board says so, else a challenge or a changed layout fails the
# board) and :284-297 (OCC answers an out-of-range page with page 1, so a page with no new id ends the walk).
def _occ_slug(s: str) -> str:
    folded = "".join(c for c in unicodedata.normalize("NFD", s) if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


def _occ_cards(page: str) -> list[tuple[str, dict[str, str]]]:
    root = _tree(page)
    out: list[tuple[str, dict[str, str]]] = []
    seen: set[str] = set()
    for card in _cards(root, lambda n: n.has_class("card-job-offer") and bool(n.attrs.get("data-id"))):
        try:
            cid = card.attrs["data-id"].strip()
            h2 = next(_own(card, lambda n: n.tag == "h2"), None)
            title = _line(h2.text()) if h2 is not None else ""
            if not re.fullmatch(r"\d{1,20}", cid) or not title or cid in seen:
                continue
            seen.add(cid)
            row_ = next(_own(card, lambda n: n.has_class("h-[21px]")), None)
            comp = next(_own(row_, lambda n: n.tag == "span"), None) if row_ is not None else None
            box = next(_own(card, lambda n: n.has_class("no-alter-loc-text")), None)
            p = next(_own(box, lambda n: n.tag == "p"), None) if box is not None else None
            company = _line(comp.text()) if comp is not None else ""
            out.append((cid, {"title": title, "url": f"https://{_OCC_HOST}/empleo/oferta/{cid}/",
                              "company": "?" if not company or company.lower() == "empresa confidencial" else company,
                              "location": _line(p.text()) if p is not None else ""}))
        except Exception:  # noqa: BLE001, S112 - one bad card must not lose the page
            continue
    return out


def _occ(row: dict[str, Any], http: Http) -> list[Job] | None:
    """OCC Mundial search pages for one keyword (token = the keyword)."""
    kw = _occ_slug(str(row.get("token") or ""))
    if not kw:
        return None
    out: list[Job] = []
    seen: set[str] = set()
    for page in range(1, OCC_PAGES + 1):
        part = "" if page == 1 else f"-pagina-{page}"
        st, text = _page_st(http, f"https://{_OCC_HOST}/empleos/de-{kw}{part}/")
        if text is None:
            if page == 1:
                return None  # a refusal or a Cloudflare challenge (403) fails the board
            break
        cards = _occ_cards(text)
        if not cards:
            if page == 1:
                plain = "".join(c for c in unicodedata.normalize("NFD", _tree(text).text()) if not unicodedata.combining(c))
                if not re.search(r"no hay empleos que coincidan", plain, re.I):
                    return None  # neither results nor OCC's empty-search copy: a challenge or a changed layout
            break
        fresh = 0
        for cid, c in cards:
            if cid in seen:
                continue
            seen.add(cid)
            fresh += 1
            j = _job(row, "occ", title=c["title"], url=c["url"], location=c["location"], extra={"job_id": cid})
            j["company"] = c["company"]
            out.append(j)
        if not fresh:
            break
    return out


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "yourator": _wrap(_yourator),
    "careerviet": _wrap(_careerviet),
    "occ": _wrap(_occ),
}
