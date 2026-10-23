"""Asian ATS boards for the hunt kit: MokaHR, HRMOS, HERP, Quickin and PyjamaHR. Same contract as kit_latam.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board tokens (companies.json ``board`` / ``--companies-extra "ats:token"``):
  mokahr    "<orgId>/<siteId>" of app.mokahr.com/social-recruitment/<orgId>/<siteId>; "<orgId>/<siteId>/r1" for Moka's
            international host hire-r1.mokahr.com (a tenant of one host is unknown to the other)
  hrmos     company slug of hrmos.co/pages/<slug>/jobs (a vanity slug or an opaque numeric id)
  herp      company slug of herp.careers/v1/<slug>
  quickin   account slug of jobs.quickin.io/<slug>
  pyjamahr  tenant segment of jobs.pyjamahr.com/<slug>

Endpoints, params and field names are adapted from the MIT-licensed freehire and career-ops source adapters (see NOTICE.md);
the code is original. The container this was written in could not reach any of these hosts, so every parser is tested on
fixtures shaped like the references' test data only. HRMOS, HERP and PyjamaHR list a posting without its body: the
posting page or detail call is made only for cards whose title passes the run's ``title_ok`` test, up to 25 per board, and
the first refusal ends the board's detail requests. MokaHR and Quickin carry the whole posting in the listing.

MokaHR's app.mokahr.com answers the listing AES-128-CBC encrypted (key in the answer, IV fixed: mokahr.go:50 and :197-216,
mokahr.mjs:104-117); the standard library has no AES, so a small decryptor is built in below (checked against FIPS-197).

NOT built: HerKey (freehire herkey.go). It is one boardless search index behind an anonymous session token, whose listing
carries no body for most rows and re-lists postings of ATS boards the kit already reads; it was left out to keep this change
small; a herkey.com link is reported as unsupported.
"""
# ruff: noqa: E501
from __future__ import annotations

import base64
import html
import json
import re
import sys
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urljoin, urlsplit

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
    _https,
    _iso_date,
    _job,
    _join,
    _ld_places,
    _ld_posting,
    _links,
    _open_details,
    _text,
    _title_ok,
    _tree,
    _wrap,
)

_JSON = {"Accept": "application/json"}
_POST = {"Content-Type": "application/json", "Accept": "application/json"}

DETAIL_CAP = 25  # detail requests per board (the references fan out over every posting)
MOKAHR_PAGES = 100  # 50 postings a page: mokahr.go:44 bounds a tenant at 5,000
MOKAHR_PAGE = 50  # the listing's hard ceiling: 51 and above answer code 102 (mokahr.go:43)
MOKAHR_MAX_BYTES = 3_000_000  # one encrypted page; the pure-Python decryptor needs about 5 s for a page this size
MOKAHR_BOARD_MAX_BYTES = 6_000_000  # all encrypted pages of one board (a real page is tens of KB); the walk stops past it
HRMOS_PAGES = 50  # hrmos.go:15
HERP_GROUPS = 20  # requisition-group pages read per board
QUICKIN_PAGES = 50  # 100 postings a page (quickin.go:10-13 allows 200 pages)
PYJAMAHR_PAGES = 40  # pyjamahr.go:49 allows 500 pages


def _board(s: str) -> bool:
    """A path slug that starts with a letter or digit (so never ``.`` or ``..``)."""
    return bool(re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", s))


def _pos_id(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 0


def _mode(value: Any) -> str:
    """helpers.go:155 workplaceTypeMode."""
    return {"remote": "remote", "hybrid": "hybrid", "on-site": "onsite", "onsite": "onsite", "on site": "onsite",
            "office": "onsite"}.get(str(value or "").strip().lower(), "")


def _json_details(row: dict[str, Any], http: Http, items: list[tuple[str, str]], cap: int) -> dict[str, dict[str, Any]]:
    """(url, title-to-test) items: only titles passing ``title_ok``, at most ``cap`` requests, each sent once; a 404/410
    skips that posting, any other failure ends the run. Returns url -> object."""
    out: dict[str, dict[str, Any]] = {}
    tried = 0
    for url, test in items:
        if tried >= cap:
            break
        if not _title_ok(row, test):
            continue
        tried += 1
        st, data = _get(http, url, headers=_JSON, once=True)
        if not isinstance(data, dict):
            if st in (404, 410):
                continue
            break
        out[url] = data
    return out


# --------------------------------------------------------------------------- AES-128-CBC decryption (MokaHR only)
def _gmul(a: int, b: int) -> int:
    r = 0
    while b:
        if b & 1:
            r ^= a
        a = ((a << 1) & 0xFF) ^ (0x1B if a & 0x80 else 0)
        b >>= 1
    return r


def _sbox() -> list[int]:
    box, p, q = [0] * 256, 1, 1
    while True:  # multiply p by 3 and divide q by 3 in GF(2^8) until p is back at 1: every non-zero element is visited
        p = p ^ ((p << 1) & 0xFF) ^ (0x1B if p & 0x80 else 0)
        q ^= q << 1
        q ^= q << 2
        q ^= q << 4
        q &= 0xFF
        if q & 0x80:
            q ^= 0x09
        box[p] = q ^ _rotl(q, 1) ^ _rotl(q, 2) ^ _rotl(q, 3) ^ _rotl(q, 4) ^ 0x63
        if p == 1:
            break
    box[0] = 0x63
    return box


def _rotl(v: int, n: int) -> int:
    return ((v << n) | (v >> (8 - n))) & 0xFF


_SBOX = _sbox()
_INV = [0] * 256
for _i, _v in enumerate(_SBOX):
    _INV[_v] = _i
_M9, _M11, _M13, _M14 = ([_gmul(x, n) for x in range(256)] for n in (9, 11, 13, 14))
_UNSHIFT = [(i % 4) + 4 * (((i // 4) - (i % 4)) % 4) for i in range(16)]  # InvShiftRows on a column-major state


def _round_keys(key: bytes) -> list[list[int]]:
    w = [list(key[i:i + 4]) for i in range(0, 16, 4)]
    rcon = 1
    for i in range(4, 44):
        t = list(w[i - 1])
        if i % 4 == 0:
            t = [_SBOX[t[1]] ^ rcon, _SBOX[t[2]], _SBOX[t[3]], _SBOX[t[0]]]
            rcon = ((rcon << 1) & 0xFF) ^ (0x1B if rcon & 0x80 else 0)
        w.append([a ^ b for a, b in zip(w[i - 4], t)])
    return [sum(w[4 * r:4 * r + 4], []) for r in range(11)]


def _aes_cbc_decrypt(ct: bytes, key: bytes, iv: bytes) -> bytes | None:
    """AES-128-CBC decrypt with PKCS#7 unpadding; None when the sizes or the padding are wrong."""
    if len(key) != 16 or len(iv) != 16 or not ct or len(ct) % 16:
        return None
    rk, out, prev = _round_keys(key), bytearray(), iv
    for off in range(0, len(ct), 16):
        blk = ct[off:off + 16]
        s = [a ^ b for a, b in zip(blk, rk[10])]
        for r in range(9, 0, -1):
            s = [_INV[s[i]] ^ rk[r][n] for n, i in enumerate(_UNSHIFT)]
            t: list[int] = []
            for c in (0, 4, 8, 12):
                a0, a1, a2, a3 = s[c:c + 4]
                t += [_M14[a0] ^ _M11[a1] ^ _M13[a2] ^ _M9[a3], _M9[a0] ^ _M14[a1] ^ _M11[a2] ^ _M13[a3],
                      _M13[a0] ^ _M9[a1] ^ _M14[a2] ^ _M11[a3], _M11[a0] ^ _M13[a1] ^ _M9[a2] ^ _M14[a3]]
            s = t
        s = [_INV[s[i]] ^ rk[0][n] for n, i in enumerate(_UNSHIFT)]
        out += bytes(a ^ b for a, b in zip(s, prev))
        prev = blk
    pad = out[-1]
    if not 1 <= pad <= 16 or out[-pad:] != bytes([pad]) * pad:
        return None
    return bytes(out[:-pad])


# --------------------------------------------------------------------------- MokaHR
# Borrowed from freehire internal/ingest/sources/mokahr.go:19-21 and :66-69 (board "<orgId>/<siteId>", region "r1" = hire-r1.mokahr.com,
# a tenant of the other host is unknown), :43 and :89 (POST https://<host>/api/outer/ats-apply/website/jobs/v2, limit 50, offset
# paging), :50 and :197-216 (app.mokahr.com answers {data: base64 ciphertext, necromancer: 16-byte key}, AES-128-CBC under the fixed
# IV de7c21ed8d6f50fe; hire-r1 answers plain JSON), :138-166 (jobs: id, title, status, jobDescription, commitment, openedAt,
# locations[]), :187-195 (offsets until a short page: there is no total), :53 (two robots.txt-excluded tenants), :218-292 (only
# status "open" postings with a title and a body; URL https://<host>/social-recruitment/<org>/<site>#/job/<id>; openedAt is
# China time; commitment 全职/兼职/实习), :63-76 (city codes and country names to English), and career-ops providers/mokahr.mjs:30-43,
# :104-125 (the same endpoint, body and decryption). The listing carries the whole body, so there is no detail request.
_MOKAHR_IV = b"de7c21ed8d6f50fe"
_MOKAHR_TOKEN = re.compile(r"^([A-Za-z0-9_-]{1,80})/([1-9][0-9]{0,11})(?:/(r1))?$")
_MOKAHR_ROBOTS = {"lingjuninvest/46355", "shopee/74378"}
_MOKAHR_CITIES = {"1101": "Beijing", "3101": "Shanghai", "1201": "Tianjin", "5001": "Chongqing", "3301": "Hangzhou",
                  "4403": "Shenzhen", "4401": "Guangzhou", "5101": "Chengdu", "3502": "Xiamen", "3201": "Nanjing",
                  "4201": "Wuhan", "6101": "Xi'an", "3205": "Suzhou", "8100": "Hong Kong"}
_MOKAHR_COMMIT = {"全职": "full_time", "兼职": "part_time", "实习": "internship"}


def _mokahr_rows(data: Any) -> list[Any] | None:
    """The page's job rows from a plain or encrypted answer; None when the answer is not a successful listing."""
    if isinstance(data, dict) and isinstance(data.get("data"), str):
        key = data.get("necromancer")
        try:
            ct = base64.b64decode(data["data"], validate=True) if len(data["data"]) <= MOKAHR_MAX_BYTES else b""
            plain = _aes_cbc_decrypt(ct, key.encode("utf-8") if isinstance(key, str) else b"", _MOKAHR_IV)
            data = json.loads(plain.decode("utf-8")) if plain else None
        except (ValueError, RecursionError):
            return None
    inner = data.get("data") if isinstance(data, dict) else None
    jobs = inner.get("jobs") if isinstance(inner, dict) else None
    return jobs if isinstance(data, dict) and data.get("success") is True and isinstance(jobs, list) else None


def _mokahr(row: dict[str, Any], http: Http) -> list[Job] | None:
    """MokaHR tenant site (POST listing, 50 a page, whole posting in the row; app host answers encrypted)."""
    m = _MOKAHR_TOKEN.fullmatch(str(row.get("token") or "").strip())
    if m is None or f"{m.group(1)}/{m.group(2)}" in _MOKAHR_ROBOTS:
        return None
    org, site, host = m.group(1), int(m.group(2)), "hire-r1.mokahr.com" if m.group(3) else "app.mokahr.com"
    seen: dict[str, dict[str, Any]] = {}
    spent = 0  # encrypted bytes read so far: decryption costs seconds a megabyte, so a hostile host cannot keep it going
    for page in range(MOKAHR_PAGES):
        body = json.dumps({"siteId": site, "orgId": org, "locale": "zh-CN", "limit": MOKAHR_PAGE, "offset": page * MOKAHR_PAGE}).encode()
        st, data = _get(http, f"https://{host}/api/outer/ats-apply/website/jobs/v2", method="POST", data=body, headers=_POST)
        if st == 200 and isinstance(data, dict) and isinstance(data.get("data"), str):
            spent += len(data["data"])
            if spent > MOKAHR_BOARD_MAX_BYTES:
                break  # keep what was read; the first page can never trip this (one page is capped at MOKAHR_MAX_BYTES)
        rows = _mokahr_rows(data) if st == 200 else None
        if rows is None:
            if page:
                break  # a hiccup after page 1 keeps what we have
            return None
        for r in rows:
            rid = r.get("id") if isinstance(r, dict) else None
            if isinstance(rid, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,64}", rid) and rid not in seen:
                seen[rid] = r
        if len(rows) < MOKAHR_PAGE:  # the raw page length ends the walk, never the kept count
            break
    if not seen:
        return None
    jobs: list[Job] = []
    for rid, r in seen.items():
        try:
            title, desc = _text(r.get("title")), _body(r.get("jobDescription"))
            if r.get("status") != "open" or not title or not desc:
                continue
            places = []
            for loc in r.get("locations") or []:
                code = str(loc.get("cityId") or "")
                city = _MOKAHR_CITIES.get(code[:4]) if len(code) == 6 else None
                places.append(city or _join(_text(loc.get("cityName")), _text(loc.get("provinceName"))))
            jobs.append(_job(row, "mokahr", title=title, url=f"https://{host}/social-recruitment/{org}/{site}#/job/{rid}",
                             location="; ".join(dict.fromkeys(p for p in places if p)), posted=_iso_date(r.get("openedAt")),
                             description=desc, extra={"job_id": rid, "employment_type": _MOKAHR_COMMIT.get(str(r.get("commitment") or ""), "")}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- shared ld+json posting pages (HRMOS, HERP)
def _ld_job(row: dict[str, Any], source: str, jid: str, url: str, card_title: str, page: str | None,
            extra: dict[str, Any]) -> Job | None:
    """One job from a card (title from its link) and, when it was opened, the page's JobPosting (its title wins)."""
    ld = _ld_posting(_tree(page)) if page is not None else None
    title = _text(ld.get("title")) if ld else ""
    title = title or card_title
    if not title:
        return None
    place = ""
    if ld:
        locs = ld.get("jobLocation")
        addr = [(x.get("address") if isinstance(x, dict) else None) for x in (locs if isinstance(locs, list) else [locs])]
        place = _join(*[a for a in addr if isinstance(a, str)]) if any(isinstance(a, str) for a in addr) else _ld_places(ld, region=True)
    return _job(row, source, title=title, url=url, location=_text(place), posted=_iso_date(ld.get("datePosted")) if ld else "",
                description=_body(html.unescape(str(ld.get("description") or ""))) if ld else "",
                extra={"job_id": jid, **extra})


def _under(base: str, host: str, prefix: str, href: str) -> str:
    """The path rest after ``prefix`` when ``href`` resolves to https://<host>/<prefix>..., else ''."""
    try:
        u = urlsplit(urljoin(base, href))
    except ValueError:
        return ""
    ok = u.scheme == "https" and (u.hostname or "").lower() == host and u.path.startswith(prefix)
    return u.path[len(prefix):] if ok else ""


# --------------------------------------------------------------------------- HRMOS
# Borrowed from freehire internal/ingest/sources/hrmos.go:15-17 (at most 50 pages), :20-25 (board = company slug; the listing
# https://hrmos.co/pages/<board>/jobs pages with ?page=N), :48-53 (links are exactly /pages/<board>/jobs/<id> on hrmos.co; any
# failing page fails the walk), :67-72 and (crawl end in html.go:325-328) (a page adding no new link ends the walk), :83-125 (job
# page: schema.org JobPosting title, description, datePosted, employmentType, jobLocation[].address locality + region),
# :128-143 (the literal "jobs" segment: a looser match also takes the platform's own navigation links). The listing has the title as
# link text; the page is opened only for wanted cards.
_HRMOS_TYPES = {"FULL_TIME": "full_time", "PART_TIME": "part_time", "CONTRACTOR": "contract", "INTERN": "internship"}


def _hrmos(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HRMOS career page (paged listing, ld+json posting page for wanted cards)."""
    board = str(row.get("token") or "").strip()
    if not _board(board):
        return None
    base, prefix = f"https://hrmos.co/pages/{quote(board)}/jobs", f"/pages/{board}/jobs/"
    found: dict[str, str] = {}

    def is_job(h: str) -> bool:
        r = _under(base + "/", "hrmos.co", prefix, h)
        return bool(r) and "/" not in r  # exactly /pages/<board>/jobs/<id>, never a deeper or bare path

    for n in range(1, HRMOS_PAGES + 1):
        st, page = _get(http, f"{base}?page={n}", as_json=False, headers={"Accept": "text/html"})
        if st != 200 or not isinstance(page, str):
            if n > 1:
                break  # a refusal or hiccup after page 1 keeps what we have
            return None
        fresh = 0
        for url, text in _links(_tree(page), base + "/", "hrmos.co", is_job).items():
            if url not in found:
                found[url] = " ".join(text.split())[:200]
                fresh += 1
        if not fresh:
            break
    pages = _open_details(row, http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    for url, card_title in found.items():
        try:
            p = pages.get(url)
            ld = _ld_posting(_tree(p)) if p is not None else None
            j = _ld_job(row, "hrmos", url.rsplit("/", 1)[-1], url, card_title, p, {
                "employment_type": _HRMOS_TYPES.get(str(ld.get("employmentType") or "")) or "" if ld else ""})
            if j is not None:
                jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- HERP
# Borrowed from freehire internal/ingest/sources/herp.go:11-17 (board = slug of herp.careers/v1/<board>; the page links jobs
# directly (/v1/<board>/<id>) or via requisition groups (/v1/<board>/requisition-groups/<uuid>), one level deep), :39-60 (the
# group pages are read for more job links, de-duplicated), :87-117 (job page JobPosting: title, description, datePosted,
# jobLocation.address as a string), :134-171 (links are matched by host herp.careers and exact path under the board, never by
# substring; "top" is the board's landing page, not a job; "/apply" sublinks are not jobs).
_HERP_SKIP = {"top"}
_HERP_GROUP = re.compile(r"requisition-groups/[A-Za-z0-9_-]+")


def _herp(row: dict[str, Any], http: Http) -> list[Job] | None:
    """HERP career page (listing plus requisition-group pages, ld+json posting page for wanted cards)."""
    board = str(row.get("token") or "").strip()
    if not _board(board):
        return None
    base, prefix = f"https://herp.careers/v1/{quote(board)}", f"/v1/{board}/"

    def is_job(h: str) -> bool:
        r = _under(base + "/", "herp.careers", prefix, h)
        return bool(r) and "/" not in r and r not in _HERP_SKIP and not r.startswith("requisition-groups")

    def is_group(h: str) -> bool:
        return bool(_HERP_GROUP.fullmatch(_under(base + "/", "herp.careers", prefix, h)))

    st, page = _get(http, base, as_json=False, headers={"Accept": "text/html"})
    if st != 200 or not isinstance(page, str):
        return None
    root = _tree(page)
    found = {u: " ".join(t.split())[:200] for u, t in _links(root, base + "/", "herp.careers", is_job).items()}
    for g in list(_links(root, base + "/", "herp.careers", is_group))[:HERP_GROUPS]:
        st, gp = _get(http, g, as_json=False, headers={"Accept": "text/html"}, once=True)
        if st != 200 or not isinstance(gp, str):
            break  # a refusal ends the group pages; what was gathered stays
        for u, t in _links(_tree(gp), base + "/", "herp.careers", is_job).items():
            found.setdefault(u, " ".join(t.split())[:200])
    pages = _open_details(row, http, list(found.items()), DETAIL_CAP)
    jobs: list[Job] = []
    for url, card_title in found.items():
        try:
            j = _ld_job(row, "herp", url.rsplit("/", 1)[-1], url, card_title, pages.get(url), {})
            if j is not None:
                jobs.append(j)
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


# --------------------------------------------------------------------------- Quickin
# Borrowed from freehire internal/ingest/sources/quickin.go:10-17 (api.quickin.io, 100 a page, only publicate "published"), :20-30
# (the account slug is the board; career pages are jobs.quickin.io/<slug>), :32-47 (job fields _id, publicate, title, description,
# requirements, city, region, country, workplace_type, career_url, created_at), :57-84 (description + requirements joined, location
# city, region, country, workplace type remote/hybrid/onsite, URL career_url else jobs.quickin.io/<slug>/jobs/<id>), :91-104
# (GET /public/accounts/<slug> gives the account _id), :111-138 (GET /public/<id>/jobs?page=N&limit=100: stop at the stated pages
# count, else on a short page; a first-page failure fails the board, a later one keeps what was gathered).
_QUICKIN_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


def _quickin(row: dict[str, Any], http: Http) -> list[Job] | None:
    """Quickin careers API (account id from the slug, paged listing carrying every posting whole)."""
    board = str(row.get("token") or "").strip()
    if not _board(board):
        return None
    st, acc = _get(http, f"https://api.quickin.io/public/accounts/{quote(board)}", headers=_JSON)
    aid = acc.get("_id") if isinstance(acc, dict) else None
    if st != 200 or not isinstance(aid, str) or not _QUICKIN_ID.fullmatch(aid):
        return None
    docs: list[Any] = []
    for page in range(1, QUICKIN_PAGES + 1):
        st, data = _get(http, f"https://api.quickin.io/public/{aid}/jobs?page={page}&limit=100", headers=_JSON)
        got = data.get("docs") if isinstance(data, dict) else None
        if st != 200 or not isinstance(got, list):
            if page > 1:
                break  # a hiccup after page 1 keeps what we have
            return None
        docs += got
        pages = data.get("pages")
        if isinstance(pages, int) and not isinstance(pages, bool) and pages > 0:
            if page >= pages:
                break
        elif len(got) < 100:
            break
    jobs: list[Job] = []
    seen: set[str] = set()
    for d in docs:
        try:
            jid = d.get("_id") if isinstance(d, dict) else None
            if not isinstance(jid, str) or not _QUICKIN_ID.fullmatch(jid) or jid in seen or d.get("publicate") != "published" or not _text(d.get("title")):
                continue
            seen.add(jid)
            url = _quickin_url(d.get("career_url")) or f"https://jobs.quickin.io/{board}/jobs/{jid}"
            mode = _mode(d.get("workplace_type"))
            desc = "\n\n".join(x for x in (_body(d.get("description")), _body(d.get("requirements"))) if x)
            jobs.append(_job(row, "quickin", title=_text(d.get("title")), url=url,
                             location=_join(_text(d.get("city")), _text(d.get("region")), _text(d.get("country"))),
                             posted=_iso_date(d.get("created_at")), description=desc, remote=True if mode == "remote" else None,
                             extra={"job_id": jid, "work_mode": mode}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


def _quickin_url(value: Any) -> str:
    """career_url when it is an https link on jobs.quickin.io (the one host these postings live on), else ''."""
    u = _https(value, "jobs.quickin.io")
    return urlsplit(u)._replace(query="").geturl() if u else ""


# --------------------------------------------------------------------------- PyjamaHR
# Borrowed from freehire internal/ingest/sources/pyjamahr.go:10-20 (board = tenant segment of jobs.pyjamahr.com; the keyless listing is
# on api.pyjamahr.com, not the page's own host), :55 and :80-95 (GET /api/career/jobs/?company_slug=<board>&page=1, then the answer's
# own "next" URL until it is null), :72-78 (items: id, slug, title, location), :97-117 and :133-175 (detail GET
# /api/career/jobs/<id>/?company_slug=<board>: description, job_type, workplace_type with "_" read as "-", remote, created_at; the
# posting URL is https://jobs.pyjamahr.com/<board>/<slug>), :177-189 (FULLTIME/PARTTIME/CONTRACT/INTERN spellings). A "next" link on
# any other host, or that was already read, ends the walk: the kit follows nothing but the API's own pages.
_PJ_TYPES = {"FULLTIME": "full_time", "FULL_TIME": "full_time", "FULL-TIME": "full_time", "PARTTIME": "part_time", "PART_TIME": "part_time",
             "PART-TIME": "part_time", "CONTRACT": "contract", "TEMPORARY": "contract", "FREELANCE": "contract", "INTERN": "internship",
             "INTERNSHIP": "internship"}


def _pyjamahr(row: dict[str, Any], http: Http) -> list[Job] | None:
    """PyjamaHR tenant (api.pyjamahr.com paged listing, one detail JSON per wanted posting)."""
    board = str(row.get("token") or "").strip()
    if not _board(board):
        return None
    nxt: str | None = f"https://api.pyjamahr.com/api/career/jobs/?company_slug={quote(board)}&page=1"
    items: dict[int, dict[str, Any]] = {}
    visited: set[str] = set()
    for page in range(PYJAMAHR_PAGES):
        if not nxt or nxt in visited:
            break
        visited.add(nxt)
        st, data = _get(http, nxt, headers=_JSON)
        results = data.get("results") if isinstance(data, dict) else None
        if st != 200 or not isinstance(results, list):
            if page:
                break  # a hiccup after page 1 keeps what we have
            return None
        for it in results:
            iid = _pos_id(it.get("id")) if isinstance(it, dict) else 0
            if iid and iid not in items and isinstance(it.get("slug"), str) and re.fullmatch(r"[A-Za-z0-9_.-]{1,200}", it["slug"]) and _text(it.get("title")):
                items[iid] = it
        n = data.get("next")
        try:
            u = urlsplit(n) if isinstance(n, str) and n else None
        except ValueError:
            u = None
        nxt = n if u is not None and u.scheme == "https" and (u.hostname or "").lower() == "api.pyjamahr.com" else None
    detail_url = {i: f"https://api.pyjamahr.com/api/career/jobs/{i}/?company_slug={quote(board)}" for i in items}
    got = _json_details(row, http, [(detail_url[i], _text(it.get("title"))) for i, it in items.items()], DETAIL_CAP)
    jobs: list[Job] = []
    for i, it in items.items():
        try:
            d = got.get(detail_url[i]) or {}
            mode = _mode(str(d.get("workplace_type") or "").replace("_", "-")) or ("remote" if d.get("remote") is True else "")
            jobs.append(_job(row, "pyjamahr", title=_text(it.get("title")), url=f"https://jobs.pyjamahr.com/{board}/{it['slug']}",
                             location=_text(it.get("location")), posted=_iso_date(d.get("created_at")), description=_body(d.get("description")),
                             remote=True if mode == "remote" else None,
                             extra={"job_id": str(i), "work_mode": mode, "employment_type": _PJ_TYPES.get(str(d.get("job_type") or "").strip().upper(), "")}))
        except Exception:  # noqa: BLE001, S112 - one bad posting must not lose the board
            continue
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "herp": _wrap(_herp),
    "hrmos": _wrap(_hrmos),
    "mokahr": _wrap(_mokahr),
    "pyjamahr": _wrap(_pyjamahr),
    "quickin": _wrap(_quickin),
}
