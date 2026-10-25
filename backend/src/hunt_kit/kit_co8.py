"""Public Telegram channel reader for the hunt kit (career-ops telegram-channel). Same contract as kit_ats_eu.py:
``fetch(row, http, stats) -> list[Job]``, never raises, network only through the ``http`` callable, no file IO at import,
standard library only. Nothing here ranks or scores a job.

Board token (companies.json ``board`` / ``--companies-extra "ats:token"``):
  telegramchannel  the handle of a PUBLIC channel (5-32 letters, digits or underscores), read from t.me/s/<handle>

Endpoints, params and field names are adapted from the MIT-licensed career-ops provider adapter (see NOTICE.md); the code is
original. The container this was written in could not reach t.me, so the parser is tested on fixtures shaped like the
reference's test data only. Every request stays on t.me; a vacancy link on any other host is kept as data, never opened.

Telegram: a channel is not a board, so a post becomes a job only when it names an identifiable employer AND links to a vacancy
page the candidate can open (providers/telegram-channel.mjs:4-24). The job ``url`` is the post's t.me permalink (a page on the
board's own host); the vacancy link it carries travels in ``extra.apply_url`` and is never fetched. Private channels, channels
with the preview switched off and pages whose markup no longer parses fail the board; they are never an empty board.

NOT IN REFERENCE and left out: a location for Telegram.
"""
# ruff: noqa: E501
from __future__ import annotations

import html
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qsl, urlsplit

try:  # shared helpers live in the sibling kit_ats_eu.py (and kit_ats.py behind it)
    import kit_ats_eu  # type: ignore[import-not-found]  # sibling file, not a package module
except ImportError:  # loaded by file path: put this folder on sys.path first
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import kit_ats_eu  # noqa: F401
from kit_ats_eu import (  # noqa: E402
    MAX_PAGES,
    Http,
    Job,
    _job,
    _Node,
    _page_st,
    _tree,
    _wrap,
)

# --------------------------------------------------------------------------- Telegram channels
# Borrowed from career-ops providers/telegram-channel.mjs:4-6 and :405 (GET https://t.me/s/<handle>, 20 posts a page newest first,
# older ones with ?before=<oldest id>), :33 (handle pattern), :34 (at most 10 pages), :35 (a 30-day window), :270-324 (a post is a
# .tgme_widget_message_wrap holding data-post="<handle>/<id>", the body in .tgme_widget_message_text split at <br>, links read from
# the body's anchors, <time datetime>; service messages and media-only posts are skipped), :312-323 and :417-423 (no preview, or posts
# that no longer parse, fail the board), :425-426 (a page that re-serves the same ids ends it), :145-186 (the employer from a labelled
# line, "Title @ Employer", "Title | Employer" with exactly one pipe, "at/в Employer" on line two, or a bare name under a hashtag
# line), :109-118 and :60-89 (what reads as a name), :200-251 (a link counts only as an https vacancy page that is not Telegram,
# social, a shortener or a form; the employer's own page beats a board mirror; two of one kind make a digest), :333-362 (the post).
_TG_HANDLE = re.compile(r"[A-Za-z0-9_]{5,32}")
_TG_WINDOW = timedelta(days=30)
_TG_NON_APPLY = re.compile(r"(^|\.)(t\.me|telegram\.me|telegram\.org|telegra\.ph|telega\.in|vk\.com|vk\.cc|max\.ru|ok\.ru|facebook\.com|fb\.com|instagram\.com|youtube\.com|youtu\.be|x\.com|twitter\.com|tiktok\.com|threads\.net|store\.steampowered\.com|play\.google\.com|apps\.apple\.com|substack\.com|medium\.com|habr\.com|github\.com)$", re.I)
_TG_SHORT = re.compile(r"(^|\.)(goo\.gl|bit\.ly|t\.co|cjl\.ist|clck\.ru|tglink\.io|tinyurl\.com|cutt\.ly|is\.gd|ow\.ly|rb\.gy|surl\.li)$", re.I)
_TG_FORM = re.compile(r"(^|\.)(forms\.gle|docs\.google\.com|forms\.yandex\.ru|typeform\.com|tally\.so|airtable\.com|notion\.site|notion\.so)$", re.I)
_TG_BOARDS = re.compile(r"(^|\.)(getmatch\.ru|finder\.work|geekjob\.ru|geeklink\.io|linkedin\.com|hh\.ru|career\.habr\.com|djinni\.co|indeed\.com|glassdoor\.com|weworkremotely\.com|remoteok\.com|arbeitnow\.com|cryptojobslist\.com|relocate\.me|lemon\.io)$", re.I)
_TG_EVIDENCE = re.compile(r"/(vacanc|job|position|opening|career|apply|hiring)|[-_/](?!(?:19|20)\d{2}(?=[/?#]|$))\d{4,}(?=[/?#]|$)|/j/[A-Z0-9]{6,}(?=[/?#]|$)|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)
_TG_TRACK = re.compile(r"^(utm_|ref$|referrer$|source$|src$|s$|from$|campaign$|fbclid$|gclid$|yclid$|mc_|_hs|igshid$|trk$)", re.I)
_TG_LISTING = re.compile(r"^/(jobs|vacancies|careers|career|vacancy|vakansii|job)$", re.I)
_TG_LOCALE = re.compile(r"^(/[a-z]{2}(-[a-z]{2})?)?$", re.I)
_TG_LABEL = re.compile(r"^[\W_]{0,3}\s*(?:компания|компанія|company|работодатель|employer|hiring company)\s*[:：]\s*(.{2,80})$", re.I)
_TG_ANON = re.compile(r"(?:название скрыто|компания скрыта|name hidden|undisclosed|confidential|\bour client\b|наш(?:его|им|ему)? клиент|для клиента)", re.I)
_TG_PLACE = re.compile(r"\b(remote|удал[её]нк\w*|hybrid|onsite|office|офис|full[- ]?time|part[- ]?time|москва|спб|berlin|london|germany|europe|usa)\b", re.I)
_TG_DATE = re.compile(r"\b(19|20)\d{2}\b|\b(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\b", re.I)
_TG_ZW = re.compile("[​-‍﻿]")
_TG_TAGS = re.compile(r"^#\S+(?:\s+#\S+)*$")
_TG_ROLE_WORDS = (r"(?<!\w)(senior|middle|junior|lead|principal|staff|head|chief|intern|trainee|engineer|developer|manager|analyst|designer|"
                  r"architect|specialist|consultant|director|recruiter|scientist|product\s+owner|qa|tester|devops|sre|инженер|разработчик|менеджер|"
                  r"специалист|аналитик|директор|архитектор|рекрутер|стажер|стажёр|тестировщик|тимлид|владелец|руководитель|маркетолог|программист)(?!\w)")
_TG_ROLE = re.compile(_TG_ROLE_WORDS, re.I)
_TG_ROLE_ONLY = re.compile("^" + _TG_ROLE_WORDS + "$", re.I)
_TG_LINE_CAP = 500
_TG_TITLE_CAP = 120


def _plausible(name: str, from_title: bool) -> str:
    """Does ``name`` read as an employer, not as the place, contract or date the same patterns also capture? (mjs:109-118)"""
    n = _TG_ZW.sub("", name).strip()
    if len(n) < 2 or len(n) > 60 or len(n.split()) > 4 or re.search(r"[,:;!?]", n) or n.endswith("."):
        return ""
    if _TG_PLACE.search(n) or _TG_ANON.search(n) or (from_title and (_TG_DATE.search(n) or re.search(r"\d", n))):
        return ""
    return n if any(ch.isupper() for ch in n) else ""


def _employer(lines: list[str]) -> str:
    """The employer a post names, or '' (mjs:145-186). Nothing is guessed from the channel name or a link host."""
    for ln in lines[:40]:
        m = _TG_LABEL.match(ln)
        if m:
            return _plausible(re.sub(r"\s*[|(].*$", "", m.group(1)), False)
    first, second = (lines + ["", ""])[0], (lines + ["", ""])[1]
    m = re.match(r"^.{3,140}?\s+@\s+(.{2,60})$", first)
    if m:
        return _plausible(m.group(1), True)
    m = re.match(r"^.{3,140}?\s+\|\s+([^|]{2,60})$", first) if first.count("|") == 1 else None
    if m:
        pipe = _plausible(m.group(1), True)
        if pipe and not _TG_ROLE_ONLY.match(pipe):
            return pipe
    m = re.match(r"^(?:в|at)\s+([^—–,(]{2,60}?)\s*(?:[—–]|$)", second)
    if m:
        return _plausible(m.group(1), True)
    if _TG_TAGS.match(first) and second:
        tag = _plausible(second, False)
        if tag and not _TG_ROLE.search(tag):
            return tag
    return ""


def _classify(href: str) -> str:
    """'employer' (a vacancy page on the employer's own https host), 'board' (a known board's vacancy page) or '' (mjs:200-216)."""
    try:
        u = urlsplit(href)
        port = u.port
    except ValueError:
        return ""
    host = (u.hostname or "").lower().removeprefix("www.")
    if u.scheme != "https" or "." not in host or u.username or port not in (None, 443) or len(href) > 2000:
        return ""
    if _TG_NON_APPLY.search(host) or _TG_SHORT.search(host) or _TG_FORM.search(host):
        return ""
    path = u.path.rstrip("/")
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "board" if "/jobs/view/" in path else ""
    identifying = any(not _TG_TRACK.match(k) for k, _v in parse_qsl(u.query, keep_blank_values=True))
    if (_TG_LOCALE.match(path) or _TG_LISTING.match(path)) and not identifying:
        return ""
    if not _TG_EVIDENCE.search(path) and not identifying:
        return ""
    return "board" if _TG_BOARDS.search(host) else "employer"


def _vacancy_key(href: str) -> str:
    u = urlsplit(href)
    keep = sorted((k, v) for k, v in parse_qsl(u.query, keep_blank_values=True) if not _TG_TRACK.match(k))
    return f"{(u.hostname or '').lower().removeprefix('www.')}{u.path.rstrip('/')}?{keep}"


def _apply_link(hrefs: list[str]) -> tuple[str, str] | None:
    """(url, kind) of the one vacancy link a post carries; a post with two of one kind is a digest and yields None (mjs:231-251)."""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for h in hrefs:
        kind = _classify(h)
        if kind and _vacancy_key(h) not in seen:
            seen.add(_vacancy_key(h))
            found.append((h, kind))
    if not found or any(sum(1 for _h, k in found if k == kind) > 1 for kind in ("employer", "board")):
        return None
    return next((x for x in found if x[1] == "employer"), found[0])


def _tg_lines(node: _Node) -> list[str]:
    """The text of a post body, one entry per <br> line, blank ones dropped, each line and the count capped."""
    lines: list[str] = []
    cur: list[str] = []
    stack = [iter(node.kids)]
    while stack:
        for k in stack[-1]:
            if isinstance(k, str):
                cur.append(k)
            elif k.tag == "br":
                lines.append("".join(cur))
                cur = []
            elif k.tag not in ("script", "style"):
                stack.append(iter(k.kids))
                break
        else:
            stack.pop()
    lines.append("".join(cur))
    clean = (" ".join(_TG_ZW.sub("", x).split())[:_TG_LINE_CAP] for x in lines[:200])
    return [x for x in clean if x][:80]


def _tg_post(wrap: _Node, channel: str) -> dict[str, Any] | None:
    """One .tgme_widget_message_wrap -> {id, lines, hrefs, when}, or None for a service message, a media-only post or another channel."""
    msg = text = when = None
    service = False
    stack = [iter(wrap.kids)]
    while stack:
        for k in stack[-1]:
            if not isinstance(k, _Node):
                continue
            if k.has_class("service_message"):
                service = True
            if msg is None and k.attrs.get("data-post"):
                msg = k.attrs["data-post"]
            if text is None and k.has_class("tgme_widget_message_text") and not k.has_class("js-message_reply_text"):
                text = k  # a quoted reply's text is not this post's text
                continue  # not looking inside the body
            if when is None and k.tag == "time" and k.attrs.get("datetime"):
                when = k.attrs["datetime"]
            stack.append(iter(k.kids))
            break
        else:
            stack.pop()
    handle, _, pid = (msg or "").partition("/")
    if service or text is None or handle.lower() != channel.lower() or not re.fullmatch(r"\d{1,12}", pid):
        return None
    hrefs: list[str] = []
    todo = [text]
    while todo and len(hrefs) < 50:
        n = todo.pop()
        for k in n.kids:
            if isinstance(k, _Node):
                if k.tag == "a" and k.attrs.get("href"):
                    hrefs.append(html.unescape(k.attrs["href"]))  # t.me double-encodes query ampersands
                todo.append(k)
    lines = _tg_lines(text)
    return {"id": int(pid), "lines": lines, "hrefs": hrefs, "when": when} if lines else None


def _tg_page(page: str, channel: str) -> tuple[list[dict[str, Any]], bool, int]:
    """(posts, no_preview, text-bearing posts the markup carries). The last two let the caller tell a dead page from a quiet one."""
    posts: list[dict[str, Any]] = []
    seen: set[int] = set()
    stack = [iter(_tree(page).kids)]
    while stack:  # wraps do not nest, and unclosed ones in a hostile page must not make each re-walk everything below
        for k in stack[-1]:
            if isinstance(k, _Node):
                if k.has_class("tgme_widget_message_wrap"):
                    try:
                        p = _tg_post(k, channel)
                    except Exception:  # noqa: BLE001, S112 - one bad post must not lose the page
                        p = None
                    if p and p["id"] not in seen:
                        seen.add(p["id"])
                        posts.append(p)
                else:
                    stack.append(iter(k.kids))
                    break
        else:
            stack.pop()
    low = page.lower()
    no_preview = not posts and '<meta property="og:title" content="telegram: contact @' in low
    return posts, no_preview, max(0, low.count("tgme_widget_message_text") - low.count("service_message"))


def _tg_when(value: str | None) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _tg_job(row: dict[str, Any], channel: str, post: dict[str, Any]) -> Job | None:
    lines = post["lines"]
    company = _employer(lines)
    link = _apply_link(post["hrefs"])
    if not company or not link or _TG_ANON.search("\n".join(lines[:3])):
        return None
    title = lines[0]
    if _TG_TAGS.match(title):  # a hashtag-first post needs a later line for its title
        low = company.lower()
        better = next((x for x in lines[1:] if x.lower() != low and _employer(["", x]).lower() != low and not _TG_TAGS.match(x)
                       and x not in post["hrefs"] and not re.match(r"https?://", x, re.I)), "")
        if not better:
            return None
        title = better
    if len(title) > _TG_TITLE_CAP:
        cut = title[:_TG_TITLE_CAP]
        title = (cut[:cut.rfind(" ")] if cut.rfind(" ") > _TG_TITLE_CAP // 3 else cut) + "…"
    when = _tg_when(post["when"])
    permalink = f"https://t.me/{channel}/{post['id']}"
    j = _job(row, "telegramchannel", title=title, url=permalink, posted=when.strftime("%Y-%m-%d") if when else "",
             description="\n".join(lines)[:4000] + f"\n\nSource: {permalink}\nApply: {link[0]}",
             extra={"apply_url": link[0], "apply_kind": link[1], "post_id": post["id"]})
    j["company"] = company
    return j


def _telegramchannel(row: dict[str, Any], http: Http) -> list[Job] | None:
    """A public Telegram channel through t.me/s/<handle>, newest first, back to the 30-day window or 10 pages."""
    channel = str(row.get("token") or "").strip().removeprefix("@")
    if not _TG_HANDLE.fullmatch(channel):
        return None
    cutoff = datetime.now(timezone.utc) - _TG_WINDOW
    jobs: list[Job] = []
    before: int | None = None
    for pg in range(MAX_PAGES):
        _st, page = _page_st(http, f"https://t.me/s/{channel}" + ("" if before is None else f"?before={before}"))
        if page is None:
            if pg == 0:
                return None
            break
        posts, no_preview, text_posts = _tg_page(page, channel)
        if pg == 0 and (no_preview or (not posts and text_posts)):
            return None  # no public preview, or the markup changed: a failed board, never an empty one
        if not posts:
            break
        oldest = min(p["id"] for p in posts)
        if before is not None and oldest >= before:
            break  # t.me re-served the same page
        reached = False
        for p in posts:
            try:
                when = _tg_when(p["when"])
                if when is not None and when < cutoff:
                    reached = True
                    continue
                j = _tg_job(row, channel, p)
                if j:
                    jobs.append(j)
            except Exception:  # noqa: BLE001, S112 - one bad post must not lose the channel
                continue
        if reached:
            break
        before = oldest
    return jobs


ATS_FETCHERS: dict[str, Callable[[dict[str, Any], Http, dict[str, Any]], list[Job]]] = {
    "telegramchannel": _wrap(_telegramchannel),
}
