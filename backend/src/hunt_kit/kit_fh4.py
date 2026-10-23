"""Board readers for the hunt kit, freehire batch A (branch wave4/fh4): Scalis, Senior (Portal de Talentos) and Profession.hu.
Same contract as kit_ats_eu.py: ``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http``
callable, no file IO at import, standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  scalis      the tenant subdomain of <tenant>.scalis.ai (``boldbusiness``)
  senior      the tenant subdomain of <tenant>.portaldetalentos.senior.com.br (``intelbras``)
  profession  a Profession.hu IT category sitemap name: ``itdev`` or ``itops`` (the only two the reference reads)

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only. Senior and Profession open detail requests only for postings whose title passes
the run's ``title_ok`` test, up to 25 per board, and the first refusal ends a board's detail requests; Senior lists a posting
it did not open without a body, Profession (a sitemap states no title) lists only a posting it opened.

NOT built (written up in README and NOTICE):
  Gusto Hiring (freehire gusto.go:22-24) and JobLeads (jobleads.go:19-23): Cloudflare answers both only to a Chrome TLS
  fingerprint, which urllib cannot imitate. jobs.gusto.com and jobleads.com are reported as unsupported.
Left out of Profession.hu (NOT carried by the kit's job shape): the experience, education and English-level facets
(profession.go:510-620) and the section headings (profession.go:421-434); the body text itself is complete.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import json
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
    _POST,
    _SITEMAP_MAX,
    Http,
    Job,
    _body,
    _get,
    _iso_date,
    _job,
    _label,
    _ld_posting,
    _Node,
    _open_details,
    _page_st,
    _remote_from,
    _sitemap_locs,
    _title_ok,
    _tree,
    _walk,
    _words,
    _wrap,
    safe_fromstring,
)
from kit_sites_a import (  # type: ignore[import-not-found]  # noqa: E402  # sibling file, not a package module
    _bracket,
    _decode_flight,
    _flight_rows,
)

_XML = {"Accept": "application/xml, text/xml, application/rss+xml"}

DETAIL_CAP = 25  # detail requests per board (the references open every posting)
SCALIS_PAGES = 60  # 10 postings a page and the server clamps the page size (scalis.go:28-39); the measured tenant holds 5 pages
SENIOR_PAGES = 10  # 50 vacancies a page (senior.go:15)


def _clean(value: Any) -> str:
    return " ".join(html.unescape(str(value or "")).replace("�", "").split())


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is a plain https link on exactly ``host`` (no userinfo, no odd port), else ''."""
    try:
        p = urlsplit(str(url or "").strip())
        if p.scheme != "https" or (p.hostname or "").lower() != host or p.username is not None or p.password is not None:
            return ""
        return p._replace(fragment="").geturl() if p.port in (None, 443) else ""
    except ValueError:
        return ""


def _d(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _l(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


# --------------------------------------------------------------------------- Scalis
# Borrowed from freehire internal/ingest/sources/scalis.go:10-17 (the board is the tenant subdomain <board>.scalis.ai; the listing is
# a Next.js App Router page whose flight inlines a complete object per posting, so no detail request), :51-52 (GET
# https://<board>.scalis.ai/jobs?page=N&limit=10&sortBy=SORT_BEST_MATCH), :155-165 (the payload opens `"initialData":{"results"`;
# cut by bracket matching), :61 (an empty results list ends the walk), :100-115 (description and descriptionHtml are "$<id>"
# references into the flight's text rows, HTML preferred, plain text as the fallback), :124-150 (id, title, employment, workplace,
# skills, locations[].city/country, createdAt), :133-139 (company is NOT read: it is a back-reference string after the first
# posting; the configured name is used), :175-179 (location = "city, country" per place), :186 (posting URL
# https://<board>.scalis.ai/job/<id>), :171-173 (a posting with no id is dropped), :205-215 (workplace REMOTE / HYBRID / ON_SITE),
# :222-228 (employment FULL_TIME / CONTRACTOR / TEMPORARY). Not used: salary (the kit has no pay field).
# The reference fails a board that reaches its 500-page ceiling and one whose references never resolve (:68, :89); the kit keeps
# what a later failed page left it, and stops at SCALIS_PAGES.
def _scalis(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Scalis tenant: GET the paged listing, read the flight; the body rides in the same stream."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board):
        return None
    host = f"{board}.scalis.ai"
    jobs: list[Job] = []
    seen: set[str] = set()
    for page in range(1, SCALIS_PAGES + 1):
        st, text = _page_st(http, f"https://{host}/jobs?page={page}&limit=10&sortBy=SORT_BEST_MATCH")
        if text is None:
            if page > 1:
                break  # a refusal ends the board with what it already holds
            return None
        flight = _decode_flight(text)
        raw = _bracket(flight, '"initialData":{"results"', "{", "}")
        try:
            results = _d(json.loads(raw, strict=False) if raw else None).get("results")
        except (ValueError, RecursionError):
            results = None
        if not isinstance(results, list):
            if page > 1:
                break
            return None
        if not results:
            break
        rows = _flight_rows(flight)
        fresh = 0
        for p in results:
            try:
                p = _d(p)
                pid = str(p.get("id") or "").strip()
                title = _clean(p.get("title"))
                if not pid or pid in seen:
                    continue
                seen.add(pid)
                fresh += 1
                url = _on(f"https://{host}/job/{quote(pid, safe='')}", host)
                if not title or not url:
                    continue
                desc = ""
                for field in (p.get("descriptionHtml"), p.get("description")):
                    s = field if isinstance(field, str) else ""
                    desc = rows.get(s[1:], "") if s.startswith("$") else s
                    if desc:
                        break
                places = [_clean(", ".join(x for x in (_clean(_d(loc).get("city")), _clean(_d(loc).get("country"))) if x))
                          for loc in _l(p.get("locations"))]
                place = ", ".join(dict.fromkeys(x for x in places if x))
                mode = {"REMOTE": "remote", "HYBRID": "hybrid", "ON_SITE": "onsite"}.get(str(p.get("workplace") or "").strip().upper(), "")
                kind = {"FULL_TIME": "full_time", "CONTRACTOR": "contract", "TEMPORARY": "contract"}.get(
                    str(p.get("employment") or "").strip().upper(), "")
                jobs.append(_job(row, "scalis", title=title, url=url, location=place, posted=_iso_date(p.get("createdAt")),
                                 description=_body(desc), remote=True if mode == "remote" else _remote_from(title, place),
                                 extra={"job_id": pid, "work_mode": mode, "employment_type": kind}))
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        if not fresh:
            break  # a page that adds nothing new is the end (the server may repeat the last page)
    return jobs


# --------------------------------------------------------------------------- Senior (Portal de Talentos)
# Borrowed from freehire internal/ingest/sources/senior.go:9-16 (the board is the tenant subdomain; the keyless API is POST-only on
# https://platform.senior.com.br/t/senior.com.br/bridge/1.0/anonymous/rest/hcm/careersmanagercandidate), :70-82 (POST
# /actions/getProfileIdBySubdomain {"subdomain": board} -> {"profileId"}; none = a bad board), :86-112 (POST /queries/searchVacancies
# {"page","size":50,"filter":"","match":{"companies":[{"id": profileId}],"localizations":[]}}, page is 0-based, stop when
# page+1 >= totalPages), :34-48 (contents[].vacancy: id, title, localization.city/province/country, jobModel[],
# publication.startDate), :116-127 (POST /queries/findVacancyById {"id"} -> vacancy.about.description), :135 (posting page
# https://<board>.portaldetalentos.senior.com.br/vacancy/<id>), :146-159 (jobModel REMOTE / HYBRID / IN_PERSON). The reference
# drops a vacancy whose detail failed (:117-127); the kit lists it without a body, as Gupy is listed.
_SENIOR_BASE = "https://platform.senior.com.br/t/senior.com.br/bridge/1.0/anonymous/rest/hcm/careersmanagercandidate"
_SENIOR_HOST = "platform.senior.com.br"


def _senior_post(http: Http, path: str, body: dict[str, Any], *, once: bool = False) -> tuple[int, Any]:
    kw: dict[str, Any] = {"once": True} if once else {}
    st, res = _get(http, _SENIOR_BASE + path, method="POST", data=json.dumps(body).encode(), headers=_POST, **kw)
    return st, res


def _senior(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Senior Portal de Talentos tenant: resolve the profile id, page the vacancy search, open a capped number of details."""
    board = str(row.get("token") or "").strip().lower()
    if not _label(board) or urlsplit(_SENIOR_BASE).hostname != _SENIOR_HOST:
        return None
    st, res = _senior_post(http, "/actions/getProfileIdBySubdomain", {"subdomain": board})
    profile = _d(res).get("profileId") if st == 200 else None
    if not isinstance(profile, str) or not profile.strip():
        return None
    listed: dict[str, dict[str, Any]] = {}
    for page in range(SENIOR_PAGES):
        st, res = _senior_post(http, "/queries/searchVacancies", {
            "page": page, "size": 50, "filter": "", "match": {"companies": [{"id": profile}], "localizations": []}})
        contents = _d(res).get("contents") if st == 200 else None
        if not isinstance(contents, list):
            if page > 0:
                break
            return None
        fresh = 0
        for c in contents:
            v = _d(_d(c).get("vacancy"))
            vid = str(v.get("id") or "").strip()
            if vid and vid not in listed:
                listed[vid] = v
                fresh += 1
        total = _d(res).get("totalPages")
        if not fresh or not isinstance(total, int) or isinstance(total, bool) or page + 1 >= total:
            break
    host = f"{board}.portaldetalentos.senior.com.br"
    bodies: dict[str, str] = {}
    tried = 0
    for vid, v in listed.items():
        if tried >= DETAIL_CAP:
            break
        if not _title_ok(row, _clean(v.get("title"))):
            continue
        tried += 1
        st, d = _senior_post(http, "/queries/findVacancyById", {"id": vid}, once=True)
        if st != 200:
            if st in (404, 410):
                continue
            break  # a refusal ends the detail requests
        bodies[vid] = str(_d(_d(_d(d).get("vacancy")).get("about")).get("description") or "")
    jobs: list[Job] = []
    for vid, v in listed.items():
        try:
            title = _clean(v.get("title"))
            url = _on(f"https://{host}/vacancy/{quote(vid, safe='')}", host)
            if not title or not url:
                continue
            loc = _d(v.get("localization"))
            place = ", ".join(dict.fromkeys(x for x in (_clean(loc.get("city")), _clean(loc.get("province")), _clean(loc.get("country"))) if x))
            models = [str(m).strip().upper() for m in _l(v.get("jobModel"))]
            mode = "remote" if "REMOTE" in models else "hybrid" if "HYBRID" in models else "onsite" if "IN_PERSON" in models else ""
            jobs.append(_job(row, "senior", title=title, url=url, location=place,
                             posted=_iso_date(_d(v.get("publication")).get("startDate")), description=_body(bodies.get(vid, "")),
                             remote=True if mode == "remote" else None, extra={"job_id": vid, "work_mode": mode}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Profession.hu
# Borrowed from freehire internal/ingest/sources/profession.go:12-37 (a Hungarian multi-employer catalogue; the board is one of
# the platform's category sitemaps and only the two IT ones are read), :151 (the sitemap index
# https://www.profession.hu/sitemap-listings-index-hu.xml), :247 and :261-283 (the index lists one sitemap per category, named
# sitemap-listings-<category>-hu.xml), :295 and :222-228 (only itdev and itops are crawled), :157 (a posting URL path is
# /allas/<slug>-<id>), :229-240 (the category sitemap lists the postings; a category the index does not name is a board error),
# :342-380 (each posting page is opened; a taken-down posting answers 200 with no JobPosting block and a page with no title,
# employer or body is skipped; title, hiringOrganization.name and datePosted come from the ld+json), :394-418 (the body is read
# from the page sections #tasks, #requirements, #other, #offer, #workplace_extras, not from the flattened ld+json description),
# :440-474 and :553-559 (the address line .address-data: a badge ahead of "•" gives Hibrid / Távmunka / Remote /
# Országos lefedettség; digits are dropped from the place; no badge = onsite), :524-548 (employmentType: Teljes munkaidő /
# Részmunkaidő / Diákmunka; both schedules ticked names neither). The posting page is the sitemap URL itself, on www.profession.hu only.
_PF_HOST = "www.profession.hu"
_PF_INDEX = f"https://{_PF_HOST}/sitemap-listings-index-hu.xml"
_PF_CATEGORIES = ("itdev", "itops")
_PF_CATEGORY = re.compile(r"/sitemap-listings-([a-z0-9-]{1,40})-hu\.xml$")
_PF_ID = re.compile(r"^/allas/([^/]{1,200})-(\d{1,12})(?:/|$)")
_PF_SECTIONS = ("tasks", "requirements", "other", "offer", "workplace_extras")
_PF_BADGES = {"Hibrid": "hybrid", "Távmunka / Remote": "remote", "Országos lefedettség": ""}
_PF_DIGITS = re.compile(r"\d+")


def _pf_sitemap(http: Http, url: str, kind: str) -> list[str] | None:
    st, xml = _get(http, url, as_json=False, headers=_XML, max_bytes=_SITEMAP_MAX)
    root = safe_fromstring(xml) if st == 200 and isinstance(xml, str) else None
    return None if root is None else _sitemap_locs(root, kind)


def _pf_first(root: _Node) -> tuple[dict[str, _Node], _Node | None]:
    """(first element per body-section id, first element with class address-data) in one walk."""
    found: dict[str, _Node] = {}
    address: _Node | None = None
    for n in _walk(root):
        i = n.attrs.get("id", "")
        if i in _PF_SECTIONS and i not in found:
            found[i] = n
        if address is None and n.has_class("address-data"):
            address = n
    return found, address


def _pf_place(line: str) -> tuple[str, str]:
    """(work mode, place) from the address line: the badge before "•", the place without its digits."""
    line = " ".join(line.split())
    badge, sep, rest = line.partition("•")
    badge, rest = badge.strip(), rest.strip()
    if badge in _PF_BADGES:
        return _PF_BADGES[badge], " ".join(_PF_DIGITS.sub(" ", rest).split()) if sep else ""
    return "onsite", " ".join(_PF_DIGITS.sub(" ", line).split())


def _pf_kind(field: Any) -> str:
    ticked = {x.strip() for x in str(field or "").split(",")}
    full, part = "Teljes munkaidő" in ticked, "Részmunkaidő" in ticked
    if full and part:
        return ""
    return "full_time" if full else "part_time" if part else "internship" if "Diákmunka" in ticked else ""


def _profession(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Profession.hu IT category: index -> category sitemap -> a capped number of posting pages (ld+json and body sections)."""
    board = str(row.get("token") or "").strip().lower()
    if board not in _PF_CATEGORIES:
        return None  # the general-population categories are deliberately not read (profession.go:222-228)
    maps = _pf_sitemap(http, _PF_INDEX, "sitemap")
    target = next((u for u in maps or [] if _on(u, _PF_HOST) and (m := _PF_CATEGORY.search(urlsplit(u).path)) and m.group(1) == board), "")
    locs = _pf_sitemap(http, _on(target, _PF_HOST), "url") if target else None
    if locs is None:
        return None
    found: dict[str, tuple[str, str]] = {}  # posting id -> (url, words of the slug)
    for loc in locs:
        u = _on(loc, _PF_HOST)
        m = _PF_ID.match(urlsplit(u).path) if u else None
        if m and m.group(2) not in found:
            found[m.group(2)] = (u, _words(m.group(1)))
    ids = {u: pid for pid, (u, _w) in found.items()}
    pages = _open_details(row, http, [(u, w) for u, w in found.values()], DETAIL_CAP)
    jobs: list[Job] = []
    for u, page in pages.items():
        try:
            root = _tree(page)
            p = _ld_posting(root)
            sections, address = _pf_first(root)
            org = _clean(_d(_d(p).get("hiringOrganization")).get("name"))
            title = _clean(_d(p).get("title"))
            desc = "\n\n".join(b for b in (_body(sections[i].inner_html()) for i in _PF_SECTIONS if i in sections) if b)
            if not p or not (title and org and desc):
                continue  # a taken-down posting (no JobPosting), or one without a title, employer or body
            mode, place = _pf_place(address.text() if address else "")
            j = _job(row, "profession", title=title, url=u, location=place, posted=_iso_date(p.get("datePosted")), description=desc,
                     remote=True if mode == "remote" else None,
                     extra={"job_id": ids[u], "work_mode": mode, "employment_type": _pf_kind(p.get("employmentType"))})
            j["company"] = org
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "profession": _wrap(_profession),
    "scalis": _wrap(_scalis),
    "senior": _wrap(_senior),
}
