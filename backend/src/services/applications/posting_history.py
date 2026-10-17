"""History checks on one brought job: what THIS user's own records say about it.

Three more ``checks`` rows beside job_checks.py, computed from the caller's own
applications only (rule 12) and never from another user's bring or the shared
catalog:

* ``company_history`` - your earlier applications to this company: answered,
  silent past the window, or no history. Reuses feedback_numbers.company_cards
  (career-ops company-history.mjs:348-437). A rejection is an answer, not silence.
* ``similar_ad`` - an ad you brought in the repost window whose TEXT is
  near-identical to this one under another link, title or company: the same
  opening re-posted, or re-posted by an agency with the employer removed.
  Python port of career-ops fingerprint-core.mjs (MIT): 64-bit SimHash over
  3-token shingles of the normalised text (:36-87), similarity = 1 - hamming/64
  (:139-145), threshold 0.92 (:30), bodies under 200 characters never match (:26).
* ``posting_signs`` - signs that a posting may not be actively worked, listed as
  evidence with a count, never a verdict. Criteria and the two-sign convergence
  follow freehire internal/job/ghost/classify.go:1-13, :82-90 and
  internal/job/jobreality (classify.go:21 old = 90 days; dictionary.go:10-29 the
  talent-pool phrases). ADAPTED: one user, so there is no level and no
  "likely"; the user judges.

Product rule 4: Job360 never ranks, skips or recommends on these. Facts only;
the word "ghost" is never shown. Never raises: a failure yields no rows.
"""

from __future__ import annotations

import functools
import hashlib
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping, Optional

from src.core import settings
from src.repositories.database import JobDatabase
from src.utils.logger import safe_log_value

log = logging.getLogger("job360.posting_history")

# ── SimHash (fingerprint-core.mjs) ───────────────────────────────────────────

_TAG_RE = re.compile(r"<[^<>]*>")  # no "<" inside: linear on "<<<<..." (CodeQL py/polynomial-redos)
_ENTITY_RE = re.compile(r"&[a-z#0-9]+;", re.IGNORECASE)
_URL_RE = re.compile(r"https?://\S+")
_NON_WORD_RE = re.compile(r"[\W_]+")


def normalize_ad_text(text: str) -> str:
    """fingerprint-core.mjs:43-52 - tags, entities and URLs out, lower case, every
    run of non-letters/digits (unicode-aware) to one space."""
    s = str(text or "").lower()
    s = _TAG_RE.sub(" ", s)
    s = _ENTITY_RE.sub(" ", s)
    s = _URL_RE.sub(" ", s)
    return _NON_WORD_RE.sub(" ", s).strip()


@functools.lru_cache(maxsize=512)
def fingerprint(text: str) -> Optional[int]:
    """fingerprint-core.mjs:61-87 - the 64-bit SimHash, or None when the text is too
    short (under SIMILAR_AD_MIN_TEXT characters, or under 3 tokens) to compare.

    Same bits as the reference: each shingle's first 8 SHA-1 bytes as a 64-bit
    big-endian number; an output bit is set when more shingles have it set than
    not. Counted per column over the binary strings (C-level ``count``), cached."""
    normalized = normalize_ad_text(text)
    if len(normalized) < settings.SIMILAR_AD_MIN_TEXT:
        return None
    tokens = normalized.split(" ")
    if len(tokens) < 3:
        return None
    rows = [
        format(int.from_bytes(hashlib.sha1(
            f"{tokens[i]} {tokens[i + 1]} {tokens[i + 2]}".encode(), usedforsecurity=False,
        ).digest()[:8], "big"), "064b")
        for i in range(len(tokens) - 2)
    ]
    half = len(rows)
    bits = "".join("1" if 2 * col.count("1") > half else "0" for col in zip(*rows))
    return int(bits, 2)


def similarity(a: Optional[int], b: Optional[int]) -> float:
    """fingerprint-core.mjs:139-145 - 1 - hamming/64; 0 when either side is missing."""
    if a is None or b is None:
        return 0.0
    return 1 - bin(a ^ b).count("1") / 64


# ── posting signs (freehire ghost/jobreality) ────────────────────────────────

# freehire internal/job/jobreality/dictionary.go:10-29 (EN + RU), matched as
# lower-case substrings; specific multi-word phrases so "data pipeline" never trips it.
_TALENT_POOL_PHRASES = (
    "always hiring", "always looking for", "we are always", "we're always", "talent community",
    "talent pool", "talent network", "future opportunities", "future openings", "general application",
    "open application", "ongoing recruitment",
    "всегда в поиске", "постоянно ищем", "постоянно в поиске", "кадровый резерв", "будущие вакансии",
)
POSTING_SIGN_CODES = ("old_ad", "reposted", "talent_pool_wording", "not_on_employer_site", "company_silent")


def talent_pool_phrase(description: str) -> Optional[str]:
    low = str(description or "").lower()
    return next((p for p in _TALENT_POOL_PHRASES if p in low), None)


def _check(base: list[dict[str, Any]], code: str) -> dict[str, Any]:
    return next((c for c in base if c.get("code") == code), {})


def posting_signs(
    base: list[dict[str, Any]], description: str, company: Optional[dict[str, Any]],
    similar: Optional[dict[str, Any]], repost: Optional[dict[str, Any]],
) -> dict[str, Any]:
    """The ``posting_signs`` row from the other checks. Each sign is one fact; two or
    more together (POSTING_SIGNS_CONVERGE) are worth a look, never a reason on their own."""
    signs: list[dict[str, str]] = []
    age = _check(base, "ad_age_days").get("value")
    if isinstance(age, int) and age >= settings.POSTING_SIGN_OLD_DAYS:
        signs.append({"code": "old_ad", "reason": f"The newest date on the ad is {age} days old."})
    if _check(base, "reposted").get("value") is True or repost or (similar and similar.get("same_company")):
        signs.append({"code": "reposted", "reason": "The same opening appears to have been posted again."})
    phrase = talent_pool_phrase(description)
    if phrase:
        signs.append({"code": "talent_pool_wording",
                      "reason": f'The ad says "{phrase}": it may be a standing pool, not one opening.'})
    if _check(base, "apply_host").get("value") == "job_board":
        signs.append({"code": "not_on_employer_site",
                      "reason": "The apply link is a job board, not the employer's own careers site."})
    if company and company.get("label") in ("silent-on-you", "mixed"):
        signs.append({"code": "company_silent",
                      "reason": "An earlier application of yours to this company had no answer past the window."})
    count, total = len(signs), len(POSTING_SIGN_CODES)
    if count == 0:
        reason = f"0 of {total} signs: nothing in your records or the ad suggests the posting is stale."
    elif count < settings.POSTING_SIGNS_CONVERGE:
        reason = (f"{count} of {total} signs. One sign alone says little (a hard-to-fill role "
                  "also stays up a long time).")
    else:
        reason = (f"{count} of {total} signs that this posting may not be actively worked. Facts, not a "
                  "verdict: tell the user and let them decide; never skip a job because of it.")
    return {
        "code": "posting_signs", "ok": False if count >= settings.POSTING_SIGNS_CONVERGE else None,
        "value": count, "of": total, "signs": signs, "reason": reason,
    }


# ── the async rows ───────────────────────────────────────────────────────────


def _parse(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


async def company_card(db: JobDatabase, user_id: str, company: str, exclude_id: int) -> Optional[dict[str, Any]]:
    """This user's company card for ``company``, the application ``exclude_id`` left out."""
    from src.services.applications import feedback_numbers, spine  # noqa: PLC0415

    if not str(company or "").strip():
        return None
    facts = await feedback_numbers.load(db, user_id, None)
    facts.apps = [a for a in facts.apps if int(a["id"]) != exclude_id]
    facts.events.pop(exclude_id, None)
    zone = await spine.user_zone(db, user_id)
    cards = feedback_numbers.company_cards(facts, datetime.now(timezone.utc), zone, company)["cards"]
    return cards[0] if cards else None


def company_history_row(company: str, card: Optional[dict[str, Any]]) -> dict[str, Any]:
    if card is None:
        return {"code": "company_history", "ok": None, "value": "no-history", "applications": 0, "facts": [],
                "reason": f"You have not applied to {company or 'this company'} before."}
    facts = card.get("facts") or []
    answered = [f for f in facts if f.get("kind") == "answered"]
    silent = [f for f in facts if f.get("kind") == "silent"]
    parts = [f"You applied to {card.get('company') or company} {card['applications']} time(s) before"]
    if answered:
        outcomes = ", ".join(f"{f['outcome']} on {f['on']}" for f in answered[:3])
        parts.append(f"{len(answered)} answered ({outcomes})")
    if silent:
        parts.append(f"{len(silent)} with no answer after {settings.COMPANY_SILENCE_WINDOW_DAYS}+ days")
    reason = "; ".join(parts) + ". Facts, not a verdict (a rejection is an answer)."
    return {"code": "company_history", "ok": None, "value": card.get("label"),
            "applications": card["applications"], "facts": facts[:10], "reason": reason}


async def similar_ads(
    db: JobDatabase, user_id: str, *, application_id: int, company: str, apply_url: str, description: str,
    now: Optional[datetime] = None,
) -> dict[str, Any]:
    """The ``similar_ad`` row: this user's own brings in the repost window whose ad text
    is near-identical, excluding this application and the same job link."""
    from src.services.applications.job_checks import canonical_url  # noqa: PLC0415
    from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

    window = settings.REPOST_WINDOW_DAYS
    mine = fingerprint(description)
    if mine is None:
        return {"code": "similar_ad", "ok": None, "value": None, "matches": [],
                "reason": "The ad text is too short to compare with your other ads."}
    floor = (now or datetime.now(timezone.utc)) - timedelta(days=window + 1)
    cur = await db._db.execute(
        "SELECT a.id, a.job_company, a.job_title, a.job_url, a.created_at, a.job_description_snapshot "
        "FROM applications a WHERE a.user_id = ? AND a.id != ? AND a.created_at >= ? ORDER BY a.id DESC LIMIT ?",
        (user_id, application_id, floor.isoformat(), settings.SIMILAR_AD_MAX_ROWS),
    )
    own_link = canonical_url(apply_url or "")
    want_company = normalized_company_key(company or "")
    matches: list[dict[str, Any]] = []
    for r in await cur.fetchall():
        row = dict(r)
        if own_link and canonical_url(str(row.get("job_url") or "")) == own_link:
            continue  # the same link is the same posting, not a re-post
        score = similarity(mine, fingerprint(str(row.get("job_description_snapshot") or "")))
        if score < settings.SIMILAR_AD_THRESHOLD:
            continue
        created = _parse(row.get("created_at"))
        matches.append({
            "application_id": int(row["id"]), "company": row.get("job_company") or "",
            "title": row.get("job_title") or "", "similarity": round(score, 2),
            "same_company": normalized_company_key(str(row.get("job_company") or "")) == want_company,
            "first_seen": created.date().isoformat() if created else None,
        })
    matches.sort(key=lambda m: (-m["similarity"], m["application_id"]))
    if not matches:
        return {"code": "similar_ad", "ok": True, "value": False, "matches": [],
                "reason": f"No ad you brought in the last {window} days has near-identical text."}
    top = matches[0]
    pct = round(top["similarity"] * 100)
    where = f'"{top["title"]}" at {top["company"]}, brought {top["first_seen"]} (application {top["application_id"]})'
    why = ("probably the same opening posted again" if top["same_company"]
           else "the same ad under another company name (an agency or a job site re-post)")
    return {"code": "similar_ad", "ok": False, "value": True, "same_company": top["same_company"],
            "matches": matches[: settings.SIMILAR_AD_MAX_LISTED],
            "reason": f"The text is {pct}% the same as {where}: {why}. Tell the user; never skip on it."}


async def history_checks(
    db: JobDatabase, user_id: str, app_row: Mapping[str, Any], base: list[dict[str, Any]],
    description: Optional[str] = None,
) -> list[dict[str, Any]]:
    """``company_history``, ``similar_ad`` and ``posting_signs`` for one of the caller's
    applications. ``base`` is job_checks.checks_for_application's rows. Never raises."""
    try:
        from src.services.applications import reposts  # noqa: PLC0415

        app_id = int(app_row["id"])
        company = str(app_row.get("job_company") or "")
        url = str(app_row.get("job_url") or "")
        text = description if description is not None else str(app_row.get("job_description_snapshot") or "")
        card = await company_card(db, user_id, company, app_id)
        similar = await similar_ads(db, user_id, application_id=app_id, company=company, apply_url=url,
                                    description=text)
        repost = await reposts.repost_of(db, user_id, company=company, title=str(app_row.get("job_title") or ""),
                                         apply_url=url, exclude_application_id=app_id)
        return [
            company_history_row(company, card),
            similar,
            posting_signs(base, text, card, similar if similar.get("value") else None, repost),
        ]
    except Exception:  # noqa: BLE001 - a bring or a read must never fail on these facts
        log.warning("history_checks_failed", extra={"user_id": safe_log_value(user_id)}, exc_info=True)
        return []
