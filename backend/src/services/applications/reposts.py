"""Repost among the jobs THIS user brought (feedback slice 5, feature 18).

ADAPTED from career-ops ``detect-reposts.mjs``: the same title at the same
company under a different link, first seen 1..90 days ago, is a probable
repost of one opening. Mirrors :268-281 (title identity key), :443-449 (the
same link is the same posting, not a repost), :457-458 (the day window).
Facts only - Job360 never skips, ranks or recommends on it (rule M1).

Scope is the caller's own ``applications`` rows (rule 12). Never
``jobs.first_seen_at``: that column is catalog-wide and would leak another
user's bring.
"""

from __future__ import annotations

import logging
import unicodedata
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from src.core import settings
from src.repositories.database import JobDatabase
from src.utils.logger import safe_log_value

log = logging.getLogger("job360.reposts")

LABEL = "among jobs you brought"


def title_identity_key(title: str) -> str:
    """detect-reposts.mjs:268-281 - NFD, drop combining marks, lower, every
    non-alphanumeric char to a space, split, de-duplicate, sort, join. No words
    -> the stripped lower-case title. A comparison key only; ``Job.normalized_key``
    (hard rule 1) is not touched."""
    raw = title or ""
    decomposed = unicodedata.normalize("NFD", raw)
    stripped = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").lower()
    spaced = "".join(ch if ch.isalnum() else " " for ch in stripped)
    words = sorted(set(spaced.split()))
    if not words:
        return raw.strip().lower()
    return " ".join(words)


def role_core(title: str, location: str = "") -> str:
    """The role name with trailing location noise removed, case-folded, spaces
    collapsed. "AI Engineer I - Zonda - Glasgow, Scotland, United Kingdom" ->
    "ai engineer i - zonda". Split on " - " (no regex on caller text): drop
    trailing segments that contain a comma or equal ``location``, then one
    trailing "(...)" group. A comparison key only; nothing here is stored."""
    parts = [" ".join(seg.split()) for seg in (title or "").split(" - ")]
    loc = " ".join((location or "").casefold().split())
    while len(parts) > 1 and (not parts[-1] or "," in parts[-1] or parts[-1].casefold() == loc):
        parts.pop()
    core = " - ".join(parts)
    if core.endswith(")") and "(" in core:
        head = core[: core.rindex("(")].strip()
        if head:
            core = head
    return " ".join(core.casefold().split())


def role_words(title: str, location: str = "") -> str:
    """``role_core`` with every non-letter/digit read as a space: "Graduate AI Engineer- PE Firm-CTO Office"
    and "Graduate AI Engineer - PE Firm - CTO Office" give the same words (prod 2026-11-17). A character
    scan, no regex on caller text. A comparison key only."""
    core = role_core(title, location)
    return " ".join("".join(ch if ch.isalnum() else " " for ch in core).split())


# Trailing words that do not tell two employers apart (2026-11-18 16:13 run: "Lloyds Bank" vs "Lloyds Banking
# Group", "Stratos" vs "STRATOS Technologies", "Evnek" vs "Evnek Technologies"). The hunt kit's copy
# (hunt_kit.py COMPANY_STEM_DROP) must stay equal: tests/test_hunt_dedupe_employer.py checks both keys agree.
COMPANY_STEM_DROP = frozenset({
    "ltd", "limited", "inc", "llc", "plc", "gmbh", "ag", "sa", "pvt", "private", "corp", "corporation", "co",
    "company", "holdings", "group", "bank", "banking", "technologies", "technology", "tech", "labs", "lab",
    "solutions", "systems", "services", "software", "consulting", "consultancy", "digital", "india", "uk", "us",
    "usa", "global", "international",
})


def _alnum_words(text: str) -> list[str]:
    """NFD, combining marks dropped, case-folded letter/digit runs (a character scan, no regex on caller text)."""
    decomposed = unicodedata.normalize("NFD", text or "")
    plain = "".join(ch for ch in decomposed if unicodedata.category(ch) != "Mn").casefold()
    return "".join(ch if ch.isalnum() else " " for ch in plain).split()


def company_stem(company: str) -> str:
    """The employer's name without a leading "the" and trailing legal / sector words (COMPANY_STEM_DROP); at
    least one word stays. "Lloyds Banking Group" and "Lloyds Bank" -> "lloyds". A comparison key only."""
    words = _alnum_words(company)
    if len(words) > 1 and words[0] == "the":
        words = words[1:]
    while len(words) > 1 and words[-1] in COMPANY_STEM_DROP:
        words.pop()
    return " ".join(words)


def role_key(title: str, location: str = "") -> str:
    """The role words of a duplicate key. Only place text is dropped: trailing " - " segments that hold a comma
    or name only the place (the location's words, remote, hybrid...), then a last "(...)" that names only the
    place. A qualifier stays: "Software Engineer (Backend)" and "(Frontend)" are two roles (pattern audit
    finding 6: a pattern hints, Claude judges). A character scan, no regex on caller text."""
    place = set(_alnum_words(location)) | _PLACE_NOISE

    def only_place(text: str) -> bool:
        return all(w in place for w in _alnum_words(text))

    parts = [" ".join(seg.split()) for seg in (title or "").split(" - ")]
    while len(parts) > 1 and ("," in parts[-1] or only_place(parts[-1])):
        parts.pop()
    core = " - ".join(parts)
    if core.endswith(")") and "(" in core:
        cut = core.rindex("(")
        if core[:cut].strip() and only_place(core[cut + 1:-1]):
            core = core[:cut]
    return " ".join(_alnum_words(core))


def company_title_key(company: str, title: str, location: str = "") -> str:
    """``company_stem | role_key | place words`` (the place is :func:`_place_key`, "" when the location names
    none): the key the hunt kit's ``--known-ct`` and bring_job's duplicate guard share, so a run and Job360
    call the same job known. Compare with :func:`known_keys_match`, never ``==``: "Citi | AI Engineer" in
    London and in Pune are two jobs."""
    return company_stem(company) + "|" + role_key(title, location) + "|" + " ".join(_place_key(location))


def known_keys_match(a: str, b: str) -> bool:
    """Two :func:`company_title_key` values name the same job: the same company and role, and the same place
    when both name one (a side with no place, e.g. "Remote" or an old known-list row, matches on the rest)."""
    head_a, _, place_a = a.rpartition("|")
    head_b, _, place_b = b.rpartition("|")
    return head_a == head_b and (not place_a or not place_b or place_a == place_b)


# Words that name no place: a work arrangement or filler ("Remote (UK)" is the place "uk").
_PLACE_NOISE = frozenset({
    "remote", "hybrid", "onsite", "on", "site", "office", "based", "in", "the", "and", "or", "of", "from", "home",
    "wfh", "anywhere", "worldwide", "global", "flexible", "work", "only", "fully", "partly", "partially", "area",
})


def _place_key(location: str) -> tuple[str, ...]:
    """The first segment of ``location`` (split on , ; | / and brackets) that names a place: its words,
    case-folded, without ``_PLACE_NOISE``. "Pune, Maharashtra, India" -> ("pune",); "Remote" -> (). A
    character scan, no regex on caller text. A comparison key only."""
    spaced = "".join("," if ch in ",;|/()[]" else ch for ch in (location or "").casefold())
    for segment in spaced.split(","):
        words = tuple(w for w in "".join(ch if ch.isalnum() else " " for ch in segment).split()
                      if w not in _PLACE_NOISE)
        if words:
            return words
    return ()


def same_place(a: str, b: str) -> bool:
    """True when two job locations name the same place: the same first place segment (usually the city),
    or, when neither names a place, the same words ("Remote" and "Remote"; two blanks). A shared country
    alone ("Pune, India" vs "Bengaluru, India") is not the same place (prod 2026-11-18, application 1034)."""
    ka, kb = _place_key(a), _place_key(b)
    if ka or kb:
        return ka == kb
    return " ".join((a or "").casefold().split()) == " ".join((b or "").casefold().split())


async def open_same_role(
    db: JobDatabase, user_id: str, *, title: str, location: str = "", exclude_application_id: int,
    company: str = "",
) -> list[int]:
    """Ids of THIS user's OPEN applications (not ``exclude_application_id``) whose ``role_words`` equal
    this title's AND whose location is :func:`same_place`, whatever company label they carry, when the
    role has at least ``POSSIBLE_DUPLICATE_MIN_WORDS`` words (a short title like "AI Engineer" says
    nothing) or the row is at the same :func:`company_stem` (a qualifier apart, finding 6: "Software
    Engineer (Backend)" vs "(Frontend)" is a hint, never a refusal). At most ``POSSIBLE_DUPLICATE_MAX_IDS``,
    oldest first. Scoped by user (rules 12/25). Never raises: a failure is logged and returns []; a bring
    must never fail on it.

    Why: open_duplicate_of matches the whole company key, and an agent that writes the agency's client
    into the label ("Robert Walters (PE firm client)") slips past it - prod 2026-11-17, the same role
    brought four times. The place (2026-11-18): a generic title ("AI/ML Engineer", application 1034 in
    Pune) named 10 open jobs in other cities, so the warning was noise and the run read past it."""
    try:
        want, stem = role_words(title, location), company_stem(company) if company else ""
        long_enough = len(want.split()) >= settings.POSSIBLE_DUPLICATE_MIN_WORDS
        if not want or not (long_enough or stem):
            return []
        cur = await db._db.execute(
            "SELECT id, status, job_company, job_title, job_location FROM applications "
            "WHERE user_id = ? AND id <> ? ORDER BY id",
            (user_id, exclude_application_id),
        )
        found: list[int] = []
        for r in await cur.fetchall():
            if str(r["status"]) in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES:
                continue
            have_loc = str(r["job_location"] or "")
            if not long_enough and company_stem(str(r["job_company"] or "")) != stem:
                continue
            if role_words(str(r["job_title"] or ""), have_loc) == want and same_place(have_loc, location):
                found.append(int(r["id"]))
                if len(found) >= settings.POSSIBLE_DUPLICATE_MAX_IDS:
                    break
        return found
    except Exception:  # noqa: BLE001 - a bring must never fail on an advisory check
        log.warning("same_role_check_failed", extra={"user_id": safe_log_value(user_id)}, exc_info=True)
        return []


async def open_duplicate_of(
    db: JobDatabase, user_id: str, *, company: str, title: str, location: str = ""
) -> Optional[int]:
    """The id of THIS user's earliest OPEN application (status not in
    ``APPLICATION_FOLLOW_UP_CLOSED_STATUSES``) at the same company (rule-1
    company key) with the same :func:`role_key`, or with the same
    :func:`company_title_key` (another label for the same employer), else None.
    Only place text is trimmed: a qualifier ("(Backend)" vs "(Frontend)") is
    another role, which bring_job's ``possible_duplicate`` hint leaves to the
    assistant (finding 6). Scoped by user (rules 12/25). The same rule-1
    company with exactly the same title is skipped: that is a re-bring, which
    the normal path handles."""
    from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

    want_company, want_core = normalized_company_key(company or ""), role_key(title, location)
    if not want_company or not want_core:
        return None
    want_key = company_title_key(company, title, location)
    cur = await db._db.execute(
        "SELECT id, status, job_company, job_title, job_location FROM applications WHERE user_id = ? ORDER BY id",
        (user_id,),
    )
    for r in await cur.fetchall():
        if str(r["status"]) in settings.APPLICATION_FOLLOW_UP_CLOSED_STATUSES:
            continue
        have_title, have_company = str(r["job_title"] or ""), str(r["job_company"] or "")
        same_company = normalized_company_key(have_company) == want_company
        if same_company and have_title.strip().casefold() == (title or "").strip().casefold():
            continue
        if same_company and role_key(have_title, str(r["job_location"] or "")) == want_core:
            return int(r["id"])
        if known_keys_match(company_title_key(have_company, have_title, str(r["job_location"] or "")), want_key):
            return int(r["id"])
    return None


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def repost_of(
    db: JobDatabase,
    user_id: str,
    *,
    company: str,
    title: str,
    apply_url: str,
    exclude_application_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> Optional[dict[str, Any]]:
    """The earliest of this user's own applications that is the same title at
    the same company under ANOTHER job link, 1..REPOST_WINDOW_DAYS days ago;
    else None. Never raises: a failure is logged and returns None (a bring
    must never fail on it)."""
    try:
        from src.services.applications import spine  # noqa: PLC0415
        from src.services.applications.job_checks import canonical_url, is_job_specific_url  # noqa: PLC0415
        from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

        wanted = canonical_url(apply_url or "")
        if not wanted or not is_job_specific_url(apply_url or ""):
            return None
        want_company = normalized_company_key(company or "")
        want_title = title_identity_key(title or "")
        if not want_company or not want_title:
            return None
        floor = (now or datetime.now(timezone.utc)) - timedelta(days=settings.REPOST_WINDOW_DAYS + 1)
        cur = await db._db.execute(
            "SELECT a.id, a.job_company, a.job_title, a.job_url, a.created_at, j.apply_url AS catalog_url "
            "FROM applications a LEFT JOIN jobs j ON j.id = a.job_id "
            "WHERE a.user_id = ? AND a.created_at >= ? ORDER BY a.id",
            (user_id, floor.isoformat()),
        )
        zone = await spine.user_zone(db, user_id)
        today = (now or datetime.now(timezone.utc)).astimezone(zone).date()
        best: Optional[tuple[datetime, dict[str, Any]]] = None
        for r in await cur.fetchall():
            row = dict(r)
            if exclude_application_id is not None and int(row["id"]) == exclude_application_id:
                continue
            if normalized_company_key(str(row["job_company"] or "")) != want_company:
                continue
            if title_identity_key(str(row["job_title"] or "")) != want_title:
                continue
            # This user's OWN link for the application (the shared catalog url may
            # belong to someone else's bring and must not mask a repost); fall
            # back to the catalog url only when the application has no link.
            own = str(row["job_url"] or "") or str(row["catalog_url"] or "")
            own_key = canonical_url(own)
            # Non-empty and job-specific; the same link is the same posting.
            if not own_key or own_key == wanted or not is_job_specific_url(own):
                continue
            created = _parse(row["created_at"])
            if created is None:
                continue
            first_seen = created.astimezone(zone).date()
            days = (today - first_seen).days
            if not settings.REPOST_MIN_SPAN_DAYS <= days <= settings.REPOST_WINDOW_DAYS:
                continue
            if best is None or created < best[0]:
                best = (created, {
                    "label": LABEL, "first_seen": first_seen.isoformat(), "days_since": days,
                    "application_id": int(row["id"]), "window_days": settings.REPOST_WINDOW_DAYS,
                })
        return best[1] if best else None
    except Exception:  # noqa: BLE001 - a bring must never fail on a repost check
        log.warning("repost_check_failed", extra={"user_id": safe_log_value(user_id)}, exc_info=True)
        return None
