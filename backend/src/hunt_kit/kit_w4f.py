"""Single-employer and small-platform boards for the hunt kit, wave 4 part F: Telegram.org jobs, BairesDev, Vention
(Wagtail pages API) and Northstone recruiting-brand sites. Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  telegramcareers  ``telegram.org`` (one employer, one page, no other token is read)
  bairesdev        ``talent.bairesdev.com`` (one employer, one talent site, no other token is read)
  vention          the Wagtail host, ``join.ventionteams.com``
  northstone       ``<host>/<section>``, e.g. ``www.revun.com/careers`` or ``vetted.enzrossi.com/positions``

Endpoints, params and field names are adapted from the MIT-licensed freehire source adapters (see NOTICE.md); the code is
original. The container this was written in could not reach any of these hosts, so every parser is tested on fixtures
shaped like the references' test data only. Telegram and Vention carry the whole posting in the page they read. BairesDev
and Northstone open one request set per posting, only for a posting whose title passes the run's ``title_ok`` test, up to 25
per board, and the first refusal ends a board's detail requests.

Deviations from the references, each on purpose:
  Telegram: the page publishes no per-role link, and the kit identifies a job by its URL, so each role gets the page link
  with its title slug as a fragment (``https://telegram.org/jobs#content-moderator``); the reference shares one bare URL.
  BairesDev: no referrer code is appended to the apply link (bairesdev.go:48-52, :71-80 is the referral programme; the kit
  links back to the plain posting).
  Vention: a ``html_url`` that is not https on the board's own host is replaced by ``/job-openings/<slug>`` on the host.

NOT built: Deutsche Bahn's db.jobs search service (career-ops deutschebahn.mjs:14-45, a custom results endpoint whose
searchId is pinned per portal); the Avature tenant behind it is added as an ``avature`` row in companies.json instead.
"""
# ruff: noqa: E501
from __future__ import annotations

import base64
import binascii
import html
import json
import re
import sys
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode, urlsplit

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
    _ld_places,
    _ld_posting,
    _links,
    _Node,
    _open_details,
    _page_st,
    _remote_from,
    _title_ok,
    _tree,
    _walk,
    _wrap,
)

_JSON = {"Accept": "application/json"}

DETAIL_CAP = 25  # detail requests per board (the kit's own cap; the references open every posting)
VENTION_PAGE = 20  # vention.go:27 page size
VENTION_PAGES = 100  # vention.go:28 bounds the walk

_HOST = re.compile(r"[a-z0-9][a-z0-9.-]{1,120}\.[a-z]{2,24}")
_SLUG = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,119}")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").replace("�", "").split())


def _on(url: str, host: str) -> str:
    """The URL (fragment dropped) when it is https on exactly ``host``, else ''."""
    try:
        p = urlsplit(url.strip())
        return p._replace(fragment="").geturl() if p.scheme == "https" and (p.hostname or "").lower() == host else ""
    except ValueError:
        return ""


def _token(row: dict[str, Any]) -> str:
    return str(row.get("token") or "").strip().lower()


def _slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


# --------------------------------------------------------------------------- Telegram.org jobs
# Borrowed from freehire internal/ingest/sources/telegramcareers.go:12-15 (telegram.org/jobs is one hand-maintained page; each
# role is an <h3> title followed by its description markup inside #dev_page_content; no per-role URL, date or location), :21
# (https://telegram.org/jobs), :37-42 (a page without that element is a failure), :46-64 (walk the content's children: an <h3>
# opens a role and every node until the next <h3> is its body; the id is the title slug), :58-59 (no location is stated, so none
# is invented). A role with no title or a repeated slug is skipped.
_TG_HOST = "telegram.org"


def _telegramcareers(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Telegram.org/jobs: the single jobs page split on its <h3> headings; the page carries every description."""
    if _token(row) != _TG_HOST:
        return None
    _, text = _page_st(http, f"https://{_TG_HOST}/jobs")
    if text is None:
        return None
    content = next((n for n in _walk(_tree(text)) if n.attrs.get("id") == "dev_page_content"), None)
    if content is None:
        return None
    roles: list[tuple[str, list[Any]]] = []
    for k in content.kids:
        if isinstance(k, _Node) and k.tag == "h3":
            roles.append((_clean(k.text()), []))
        elif roles:
            roles[-1][1].append(k)
    jobs: list[Job] = []
    seen: set[str] = set()
    for title, kids in roles:
        try:
            slug = _slugify(title)
            if not title or not slug or slug in seen:
                continue
            seen.add(slug)
            box = _Node("div", {})
            box.kids = kids
            jobs.append(_job(row, "telegramcareers", title=title, url=f"https://{_TG_HOST}/jobs#{slug}",
                             description=_body(box.inner_html()), extra={"job_id": slug}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- BairesDev
# Borrowed from freehire internal/ingest/sources/bairesdev.go:11-17 (no ATS board; the public talent site embeds the open list in
# a Duda job widget's base64 config; the same posting is listed once per location under a shared id), :41 (https://talent.
# bairesdev.com/), :43-45 (JobPosting endpoint https://applicants.bairesdev.com/api/JobPosting?JobPostingId=<id>: title,
# description teaser, datePosted, jobLocationType, hiringOrganization.name) and :46-49 (full description at /api/Job?JobOfferId=
# <id>, jobResults[0].description), :86-87 (the data-widget-config attribute) and :88 (apply link /job/<career>/<id>/apply),
# :118-160 (decode the config; jobList[].page_item_url and jobTitle; dedupe by job id and union the locations), :163-171 (the
# location is the last segment of "role | Remote Work | location"), :186-217 (hydrate each id: a title-less answer is skipped;
# full description falls back to the teaser; datePosted has no zone, so the first 10 characters are the date; TELECOMMUTE =
# remote; company from hiringOrganization.name else BairesDev). The widget link's own host is ignored: the apply link and both
# endpoints are always on applicants.bairesdev.com.
_BD_LIST = "https://talent.bairesdev.com/"
_BD_API = "https://applicants.bairesdev.com"
_BD_CFG = re.compile(r'data-widget-config="([A-Za-z0-9+/=]{1,3000000})"')
_BD_APPLY = re.compile(r"/job/(\d{1,12})/(\d{1,12})/apply")


def _bd_refs(page: str) -> list[dict[str, str]] | None:
    m = _BD_CFG.search(page)
    if not m:
        return None
    try:
        cfg = json.loads(base64.b64decode(m.group(1)).decode("utf-8", "replace"))
    except (ValueError, binascii.Error, RecursionError):
        return None
    rows = cfg.get("jobList") if isinstance(cfg, dict) else None
    if not isinstance(rows, list):
        return None
    refs: dict[str, dict[str, str]] = {}
    for r in rows:
        try:
            am = _BD_APPLY.search(urlsplit(str(r.get("page_item_url") or "")).path)
            if not am:
                continue
            parts = _clean(r.get("jobTitle")).split("|")
            loc = parts[-1].strip() if len(parts) >= 3 and parts[-1].strip().lower() != "remote work" else ""
            ref = refs.setdefault(am.group(2), {"career": am.group(1), "id": am.group(2), "title": parts[0].strip(), "location": ""})
            if loc and loc not in ref["location"]:
                ref["location"] = f"{ref['location']}, {loc}" if ref["location"] else loc
        except Exception:  # noqa: BLE001, S112 - one bad row must not lose the board
            continue
    return list(refs.values())


def _bairesdev(row: dict[str, Any], http: Http) -> list[Job] | None:
    """BairesDev talent site: the widget config lists the postings; each is hydrated from the applicants API (capped)."""
    if _token(row) != "talent.bairesdev.com":
        return None
    _, text = _page_st(http, _BD_LIST)
    refs = _bd_refs(text) if text is not None else None
    if not refs:
        return None
    jobs: list[Job] = []
    tried, full_ok = 0, True
    for ref in refs:
        if tried >= DETAIL_CAP:
            break
        if not _title_ok(row, ref["title"]):
            continue
        tried += 1
        st, p = _get(http, f"{_BD_API}/api/JobPosting?{urlencode({'JobPostingId': ref['id']})}", headers=_JSON, once=True)
        if not isinstance(p, dict) or st != 200:
            if st in (404, 410):
                continue
            break  # the first refusal ends the detail requests
        try:
            title = _clean(p.get("title"))
            if not title:
                continue  # bairesdev.go:192-194 no live posting under this id
            desc = ""
            if full_ok:
                st2, d = _get(http, f"{_BD_API}/api/Job?{urlencode({'JobOfferId': ref['id']})}", headers=_JSON, once=True)
                res = d.get("jobResults") if st2 == 200 and isinstance(d, dict) else None
                if isinstance(res, list) and res and isinstance(res[0], dict):
                    desc = str(res[0].get("description") or "")
                elif st2 not in (200, 404, 410):
                    full_ok = False  # refused: the teaser is kept for the rest
            org = p.get("hiringOrganization")
            name = _clean(org.get("name")) if isinstance(org, dict) else ""
            remote = str(p.get("jobLocationType") or "").upper() == "TELECOMMUTE"
            j = _job(row, "bairesdev", title=title, url=f"{_BD_API}/job/{ref['career']}/{ref['id']}/apply", location=ref["location"],
                     posted=_iso_date(p.get("datePosted")), description=_body(desc or str(p.get("description") or "")),
                     remote=True if remote else _remote_from(ref["location"]), extra={"job_id": ref["id"]})
            j["company"] = name or "BairesDev"
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Vention
# Borrowed from freehire internal/ingest/sources/vention.go:12-15 (join.ventionteams.com, the iTechArt rebrand; a keyless Wagtail
# JSON API carries each vacancy inline, so no detail request), :27-28 (page size 20, at most 100 pages), :54 (GET https://<host>/
# api/v2/pages/?type=join_vacancy.VacancyPage&fields=*&limit=20&offset=<n>), :56-78 (the first page failing fails the board, a
# later one ends it; stop on an empty page or once offset+20 reaches meta.total_count), :80-100 (a page needs an id and a title;
# location is city.name, city.country.name; description = responsibilities, required_skills, about, benefits as HTML,
# entity-unescaped; remote when work_format is remote or the location says so; first_published_at is the date), :104-109 (URL =
# meta.html_url, else https://<host>/job-openings/<meta.slug>) and :113-125 (work_format remote / *hybrid* / *onsite* / *office*).
_VENTION_MODES = (("remote", "remote"), ("hybrid", "hybrid"), ("onsite", "onsite"), ("office", "onsite"))


def _vention(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Vention Wagtail host: the paged VacancyPage list; every field of a posting is inline."""
    host = _token(row)
    if not _HOST.fullmatch(host):
        return None
    jobs: list[Job] = []
    seen: set[int] = set()
    for page in range(VENTION_PAGES):
        q = urlencode({"type": "join_vacancy.VacancyPage", "fields": "*", "limit": VENTION_PAGE, "offset": page * VENTION_PAGE})
        st, data = _get(http, f"https://{host}/api/v2/pages/?{q}", headers=_JSON)
        items = data.get("items") if st == 200 and isinstance(data, dict) else None
        if not isinstance(items, list):
            if page:
                break
            return None
        for it in items:
            try:
                ident = it.get("id") if isinstance(it, dict) else None
                title = _clean(it.get("title")) if isinstance(it, dict) else ""
                if not isinstance(ident, int) or isinstance(ident, bool) or not ident or not title or ident in seen:
                    continue
                seen.add(ident)
                meta = it.get("meta") if isinstance(it.get("meta"), dict) else {}
                slug = str(meta.get("slug") or "")
                url = _on(str(meta.get("html_url") or ""), host) or (
                    f"https://{host}/job-openings/{quote(slug, safe='')}" if _SLUG.fullmatch(slug) else "")
                if not url:
                    continue
                city_raw = it.get("city")
                city: dict[str, Any] = city_raw if isinstance(city_raw, dict) else {}
                ctry_raw = city.get("country")
                ctry: dict[str, Any] = ctry_raw if isinstance(ctry_raw, dict) else {}
                place = ", ".join(x for x in (_clean(city.get("name")), _clean(ctry.get("name"))) if x)
                fmt = str(it.get("work_format") or "").lower()
                mode = next((v for k, v in _VENTION_MODES if k in fmt), "")
                raw = html.unescape("".join(str(it.get(k) or "") for k in ("responsibilities", "required_skills", "about", "benefits")))
                jobs.append(_job(row, "vention", title=title, url=url, location=place, posted=_iso_date(meta.get("first_published_at")),
                                 description=_body(raw), remote=True if mode == "remote" else _remote_from(place),
                                 extra={"job_id": str(ident), "work_mode": mode}))
            except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
                continue
        meta_all = data.get("meta") if isinstance(data, dict) else None
        total = meta_all.get("total_count") if isinstance(meta_all, dict) else None
        if not items or (isinstance(total, int) and not isinstance(total, bool) and (page + 1) * VENTION_PAGE >= total):
            break
    return jobs


# --------------------------------------------------------------------------- Northstone
# Borrowed from freehire internal/ingest/sources/northstone.go:11-20 (recruiting-brand sites on one server-rendered template; the
# board is "<host>/<section>"; the listing is https://<host>/<section>/ and its anchors /<section>/<slug> lead to detail pages
# carrying a JobPosting ld+json), :37-40 (the trailing slash is required), :59 and :183-193 (the detail URL ends in a slash),
# :66-82 (a page without a JobPosting, or without identifier.value, is skipped; the id is identifier.value), :84-95
# (TELECOMMUTE = remote; company = hiringOrganization.name else the row's; description entity-escaped HTML; datePosted), :124-134
# (location from jobLocation, else the applicantLocationRequirements names) and :160-174 (a posting link is exactly one segment
# under the section). A custom brand host has no vendor domain, so a pasted link cannot be matched to this reader.
def _northstone_board(token: str) -> tuple[str, str] | None:
    host, _, section = token.strip("/").partition("/")
    return (host, section) if _HOST.fullmatch(host) and _SLUG.fullmatch(section) else None


def _northstone(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Northstone brand site: the section listing's single-segment links, then each page's JobPosting ld+json (capped)."""
    board = _northstone_board(_token(row))
    if board is None:
        return None
    host, section = board
    base = f"https://{host}/{section}/"
    _, text = _page_st(http, base)
    if text is None:
        return None
    pre = f"/{section}/".lower()

    def is_job(href: str) -> bool:
        try:
            path = urlsplit(href).path.lower()
        except ValueError:
            return False
        if not path.startswith(pre):
            return False
        rest = path[len(pre):].rstrip("/")
        return bool(rest) and "/" not in rest

    found = {(u if u.endswith("/") else u + "/"): t for u, t in _links(_tree(text), base, host, is_job).items()}
    pages = _open_details(row, http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    seen: set[str] = set()
    for url in found:
        try:
            ld = _ld_posting(_tree(pages[url])) if url in pages else None
            if ld is None:
                continue
            raw_id = ld.get("identifier")
            ident = _clean(raw_id.get("value") if isinstance(raw_id, dict) else raw_id)
            title = _clean(ld.get("title"))
            if not ident or not title or ident in seen:
                continue
            seen.add(ident)
            place = _ld_places(ld, region=True)
            if not place and isinstance(ld.get("applicantLocationRequirements"), list):
                place = ", ".join(_clean(c.get("name")) for c in ld["applicantLocationRequirements"] if isinstance(c, dict) and _clean(c.get("name")))
            remote = str(ld.get("jobLocationType") or "").upper() == "TELECOMMUTE"
            j = _job(row, "northstone", title=title, url=url, location=place, posted=_iso_date(ld.get("datePosted")),
                     description=_body(html.unescape(str(ld.get("description") or ""))), remote=True if remote else _remote_from(place),
                     extra={"job_id": ident})
            org = ld.get("hiringOrganization")
            name = _clean(org.get("name")) if isinstance(org, dict) else ""
            if name:
                j["company"] = name
            jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS = {
    "bairesdev": _wrap(_bairesdev),
    "northstone": _wrap(_northstone),
    "telegramcareers": _wrap(_telegramcareers),
    "vention": _wrap(_vention),
}
