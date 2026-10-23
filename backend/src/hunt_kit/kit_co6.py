"""Four job-board readers for the hunt kit, career-ops batch A (branch wave4/co6): Agentic Engineering Jobs, Collage HR,
Flowxtra and Generalist World. Same contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises,
network only through the ``http`` callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  agenticjobs     any word (``agenticjobs:all``): agentic-engineering-jobs.com is ONE board
  collage         the job-site address of a Collage HR careers page, the last path segment of
                  secure.collage.co/jobs/<address> (a tenant id, never guessed from a company name)
  flowxtra        any word (``flowxtra:all``): app.flowxtra.com lists every company that uses Flowxtra in one feed
  generalistworld any word (``generalistworld:all``): generalist.world/jobs/ is ONE board

Endpoints, params and field names are adapted from the MIT-licensed career-ops provider adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures shaped
like the references' own comments and data only. Each board's pages are walked with ``once=True``: the first refusal (429,
403, a non-JSON answer) ends that board and keeps what was already read. No posting page is opened; every field comes from the
listing. A job link that is not https on the board's own host is dropped, so the kit never lists (or later opens) a link that
leaves the board.

NOT built (written up in README and NOTICE):
  Consider (career-ops consider.mjs:1-30 and :150-189): a VC "talent network" board whose POST to /api-boards/search-jobs is
  accepted only with a Set-Cookie session and a csrfToken scraped from the board's landing page (consider.mjs:90-133). The kit's ``http`` carries neither a cookie jar nor per-board state, so getconsider.com is reported as unsupported
  (kit_sites_a already skips it for the same reason).
"""
# ruff: noqa: E501
from __future__ import annotations

import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _join,
    _Node,
    _page_st,
    _remote_from,
    _tree,
    _walk,
    _wrap,
)

_JSON = {"Accept": "application/json"}

AGENTIC_PAGES = 25  # 50 postings a page; the API allows 30 requests / 60 s per IP (agentic-jobs.mjs:27-30), so stay under it
FLOWXTRA_PAGES = 10  # 100 postings a page; flowxtra.mjs:28-29 defaults to 3 and caps at 50


def _t(value: Any) -> str:
    """A string field, trimmed and on one line; anything else is ''."""
    return " ".join(value.split()) if isinstance(value, str) else ""


def _https_on(url: Any, ok_host: Callable[[str], bool]) -> str:
    """The URL (fragment dropped) when it is an https link, without userinfo or an odd port, on a host ``ok_host`` accepts."""
    try:
        p = urlsplit(str(url or "").strip())
        host = (p.hostname or "").lower()
        if p.scheme != "https" or not host or p.username is not None or p.password is not None or p.port not in (None, 443):
            return ""
        return p._replace(fragment="").geturl() if ok_host(host) else ""
    except ValueError:
        return ""


# --------------------------------------------------------------------------- Agentic Engineering Jobs
# Borrowed from career-ops providers/agentic-jobs.mjs:5-12 and :34-35 (GET https://agentic-engineering-jobs.com/api/v1/jobs?page=<n>,
# 1-based, no auth: the site's documented REST API), :15-20 (answer { data: [...], meta: { total, page, per_page } }), :21-26
# (posting page https://agentic-engineering-jobs.com/jobs/<slug>; location is a readable string, else the geoRegion), :27-30
# (30 requests per 60 s), :110-124 (title, companyName and a slug of [A-Za-z0-9_-] are required; description is HTML), :128-130
# (postedAt ISO date), :92-105 (salaryMin / salaryMax / salaryCurrency are kept only when they are numbers), :151-180 (a response
# without a data array is a shape change, not an empty page; stop on a short page or when page * per_page reaches meta.total).
# NOT BUILT: the reference's ISO-country name fallback (agentic-jobs.mjs:77-83 via _country.mjs): a posting with neither location nor
# geoRegion is listed without a place rather than with a bare country code.
_AGENTIC_HOST = "agentic-engineering-jobs.com"
_AGENTIC_SLUG = re.compile(r"[A-Za-z0-9_-]{1,200}")


def _agentic(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Agentic Engineering Jobs: the public REST listing, 50 postings a page, description inline."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, AGENTIC_PAGES + 1):
        st, data = _get(http, f"https://{_AGENTIC_HOST}/api/v1/jobs?page={page}", headers=_JSON, once=True)
        rows = data.get("data") if st == 200 and isinstance(data, dict) else None
        if not isinstance(rows, list):
            return jobs if page > 1 else None  # a refusal or a changed shape ends the board; earlier pages are kept
        for j in rows:
            try:
                slug, title, company = _t(j.get("slug")), _t(j.get("title")), _t(j.get("companyName"))
                if not (title and company and _AGENTIC_SLUG.fullmatch(slug)):
                    continue
                url = f"https://{_AGENTIC_HOST}/jobs/{slug}"
                if url in seen:
                    continue
                seen.add(url)
                place = _t(j.get("location")) or _t(j.get("geoRegion"))
                money = {k: j[k] for k in ("salaryMin", "salaryMax") if isinstance(j.get(k), (int, float)) and not isinstance(j.get(k), bool)}
                job = _job(row, "agenticjobs", title=title, url=url, location=place, posted=_iso_date(j.get("postedAt")),
                           description=_body(j.get("description")), remote=_remote_from(place),
                           extra={"salary": {**money, "currency": _t(j.get("salaryCurrency")).upper()}} if money else {})
                job["company"] = company
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        meta = data.get("meta") if isinstance(data.get("meta"), dict) else {}
        per_page = meta.get("per_page")
        per = per_page if isinstance(per_page, int) and not isinstance(per_page, bool) and per_page > 0 else 50
        total = meta.get("total")
        if len(rows) < per or (isinstance(total, int) and page * per >= total):
            break
    return jobs


# --------------------------------------------------------------------------- Collage HR
# Borrowed from career-ops providers/collage.mjs:6-9 (the job-site address is an explicit tenant id, never a guessed company slug),
# :10-11 and :23-24 (GET https://api.collage.co/v1/positions/<address>, one answer with every position, no paging), :17-30 (the
# address is the last path segment of https://secure.collage.co/jobs/<address>), :63-68 (a bare array or { positions: [...] }),
# :70-90 (title; url from hostedUrl, else url, else applyUrl, https only; location a string or a list joined with "; ";
# descriptionPlain plus department / commitment / employmentType; posted date from createdDate, createdAt or publishedAt).
# The reference accepts any https host in those link fields. NOT IN REFERENCE which host Collage serves them from, so the kit
# keeps only links on collage.co or a subdomain of it and drops the rest (a link that leaves the board is never listed).
_COLLAGE_ADDR = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}")


def _collage_host(host: str) -> bool:
    return host == "collage.co" or host.endswith(".collage.co")


def _collage(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Collage HR: one positions list for the tenant's job-site address."""
    addr = str(row.get("token") or "").strip()
    if not _COLLAGE_ADDR.fullmatch(addr) or ".." in addr:
        return None
    st, data = _get(http, f"https://api.collage.co/v1/positions/{quote(addr, safe='')}", headers=_JSON)
    rows = data if isinstance(data, list) else (data.get("positions") if isinstance(data, dict) else None)
    if st != 200 or not isinstance(rows, list):
        return None
    jobs: list[Job] = []
    seen: set[str] = set()
    for j in rows:
        try:
            title = _t(j.get("title"))
            url = next((u for u in (_https_on(j.get(k), _collage_host) for k in ("hostedUrl", "url", "applyUrl")) if u), "")
            if not title or not url or url in seen:
                continue
            seen.add(url)
            loc = j.get("location")
            place = "; ".join(x for x in (_t(v) for v in loc) if x) if isinstance(loc, list) else _t(loc)
            meta = [f"{label}: {_t(j.get(k))}" for k, label in (("department", "Department"), ("commitment", "Commitment"), ("employmentType", "Employment type")) if _t(j.get(k))]
            desc = "\n\n".join(x for x in (_t(j.get("descriptionPlain")), "\n".join(meta)) if x)
            posted = next((d for d in (_iso_date(j.get(k)) for k in ("createdDate", "createdAt", "publishedAt")) if d), "")
            jobs.append(_job(row, "collage", title=title, url=url, location=place, posted=posted, description=desc,
                             remote=_remote_from(place), extra={"department": _t(j.get("department"))}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Flowxtra
# Borrowed from career-ops providers/flowxtra.mjs:1-12 (one public, no-auth, cross-tenant call lists every company hosted on
# Flowxtra: a board-wide aggregator), :19-20 and :88-90 (GET https://app.flowxtra.com/api/central/jobs?status=Live&per_page=100
# &page=<n>), :4-7 and :132-137 (answer { data: { data: [...], next_page_url, ... } }; a missing inner list is a shape change; stop
# when next_page_url is empty or the page is short), :56-77 (urlJobApplay [sic] is the ready-made apply link, https on flowxtra.com
# only, else the row is dropped), :79-91 (company = name_company, falling back to the entry name; location = city_company,
# state_company, country_company, plus "Remote" when workplace is exactly "Remote"; postedAt = date_share).
_FLOWXTRA_HOST = "app.flowxtra.com"


def _flowxtra(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Flowxtra: the central live-jobs feed, 100 postings a page, every company on the platform."""
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, FLOWXTRA_PAGES + 1):
        st, data = _get(http, f"https://{_FLOWXTRA_HOST}/api/central/jobs?status=Live&per_page=100&page={page}", headers=_JSON, once=True)
        outer = data.get("data") if st == 200 and isinstance(data, dict) else None
        outer = outer if isinstance(outer, dict) else {}
        rows = outer.get("data")
        if not isinstance(rows, list):
            return jobs if page > 1 else None
        for j in rows:
            try:
                title = _t(j.get("title"))
                url = _https_on(j.get("urlJobApplay"), lambda h: h == "flowxtra.com")
                if not title or not url or url in seen:
                    continue
                seen.add(url)
                place = _join(*(_t(j.get(k)) for k in ("city_company", "state_company", "country_company")), "Remote" if j.get("workplace") == "Remote" else "")
                job = _job(row, "flowxtra", title=title, url=url, location=place, posted=_iso_date(j.get("date_share")),
                           remote=_remote_from(place))
                job["company"] = _t(j.get("name_company")) or "Flowxtra"
                jobs.append(job)
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        if not outer.get("next_page_url") or len(rows) < 100:
            break
    return jobs


# --------------------------------------------------------------------------- Generalist World
# Borrowed from career-ops providers/generalist-world.mjs:6-12 and :330-333 (one server-rendered page, https://generalist.world/jobs/,
# every open posting, no paging, no API), :14-26 (card = <a class="gw-job-card" href="/jobs/<slug>/"> with div.gw-job-company,
# div.gw-job-title, p.gw-job-description and span.gw-location), :189-199 (location falls back to the card's data-region), :185-226 (the link is the board's own /jobs/<slug>/ page, a slug of
# [A-Za-z0-9._~-] after an alphanumeric; an absolute href must be https on generalist.world or www.generalist.world with no query or fragment, else the card is dropped), :231-247 (a card with no
# title or no employer is skipped), :130-183 and :273-290 (content in comments, script, style, textarea and template is not rendered, so
# a card there is not a job), :273-300 (cards present but none usable = the markup changed; no cards and no listing container
# `gw-jobs-section` / `data-jobs-container` = not the page the parser knows; the container alone = an alive, empty board).
_GW_ORIGIN = "https://generalist.world"
_GW_SLUG = re.compile(r"^/jobs/([A-Za-z0-9][A-Za-z0-9._~-]{0,199})/?$")
_GW_REGION = {"remote": "Remote"}
_GW_SKIP = ("script", "style", "textarea", "template")


def _gw_cards(root: _Node) -> tuple[list[_Node], bool]:
    """(cards in document order, whether the listing container was seen). Rendered content only, and never looks inside a
    card, so unclosed hostile nesting stays linear."""
    cards: list[_Node] = []
    marker = False
    stack = [iter(root.kids)]
    while stack:
        for k in stack[-1]:
            if not isinstance(k, _Node) or k.tag in _GW_SKIP:
                continue
            if k.tag == "a" and k.has_class("gw-job-card"):
                cards.append(k)
                continue
            marker = marker or k.has_class("gw-jobs-section") or "data-jobs-container" in k.attrs
            stack.append(iter(k.kids))
            break
        else:
            stack.pop()
    return cards, marker


def _gw_url(href: Any) -> str:
    """The canonical posting page for a card href, or '' when it is not /jobs/<slug>/ on this site."""
    path = str(href or "").strip()
    if re.match(r"[A-Za-z][A-Za-z0-9+.-]*:", path):
        try:
            p = urlsplit(path)
        except ValueError:
            return ""
        if p.scheme != "https" or (p.hostname or "").lower() not in ("generalist.world", "www.generalist.world") or p.query or p.fragment \
                or p.username is not None or p.password is not None or p.port not in (None, 443):
            return ""
        path = p.path
    m = _GW_SLUG.match(path)
    return f"{_GW_ORIGIN}/jobs/{m.group(1)}/" if m else ""


def _generalistworld(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Generalist World: the one /jobs/ page; each card carries employer, title, place and teaser."""
    st, page = _page_st(http, f"{_GW_ORIGIN}/jobs/")
    if page is None:
        return None
    cards, marker = _gw_cards(_tree(page))
    jobs: list[Job] = []
    seen: set[str] = set()
    for card in cards:
        try:
            url = _gw_url(card.attrs.get("href"))
            found: dict[str, str] = {}
            for n in _walk(card):  # one pass over the card: the first node of each class wins
                for cls, tag, key in (("gw-job-title", "div", "title"), ("gw-job-company", "div", "company"),
                                      ("gw-location", "span", "place"), ("gw-job-description", "p", "desc")):
                    if key not in found and n.tag == tag and n.has_class(cls):
                        found[key] = n.text()
            if not (url and found.get("title") and found.get("company")) or url in seen:
                continue
            seen.add(url)
            region = str(card.attrs.get("data-region") or "").strip().lower()
            place = found.get("place") or _GW_REGION.get(region) or re.sub(r"[-_]+", " ", region).upper()
            job = _job(row, "generalistworld", title=found["title"], url=url, location=place, description=found.get("desc", ""),
                       remote=_remote_from(place))
            job["company"] = found["company"]
            jobs.append(job)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    if not jobs and (cards or not marker):
        return None  # cards that all failed, or not the page this reader knows: the markup moved
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "agenticjobs": _wrap(_agentic),
    "collage": _wrap(_collage),
    "flowxtra": _wrap(_flowxtra),
    "generalistworld": _wrap(_generalistworld),
}
