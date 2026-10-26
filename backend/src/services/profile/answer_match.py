"""Match a saved answer (``user_info.answers``) to a question or a gap.

Two keys, no word lists: a stable ``topic`` the assistant chose when it saved the
answer (``work_from.IN``), else the question wording with case and punctuation
ignored. Only APPROVED answers match. An answer with ``company`` matches only
that company's lookup (same rule as the kit's ``approved_text``).

Borrowed (reference code, read 2026-10-15): trackly-cli
``answer-resolution.md:47-62`` (canonical key first, scopes narrow to broad, the
narrower wins, never widen scope, aliases/wording only a fallback, ambiguity
fails closed) and job-apply-plugin ``job-apply-store.py:134-148`` (NFKC +
lowercase + non-word to space) and ``:452-471`` (match the normalized question
OR stored aliases, same scope).
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Callable, Optional

TOPIC_MAX_CHARS = 60
TOPIC_PATTERN = re.compile(r"^[A-Za-z0-9_.]+$")
# trackly-cli answer-resolution.md:145 "Truthfulness certification is always per-run and never
# reusable": a consent / certification answer is asked on every form, never matched.
NEVER_REUSED_TOPIC_PREFIXES = ("consent.", "certify.")
_NON_ALNUM = re.compile(r"[\W_]+", re.UNICODE)
# Owner 2026-10-26: a sponsorship / right-to-work answer is true for ONE country (Palantir UK was filled
# "needs sponsorship: Yes" from a US H1B answer saved for Cadence). A saved answer whose topic is in one
# of these namespaces matches only a job in the country its topic names (``sponsorship.GB.yes_no``);
# ``remote`` / ``any`` segments mean every country; a topic naming no country matches no other job.
COUNTRY_BOUND_TOPIC_PREFIXES = ("sponsorship.", "right_to_work.")
_EVERY_COUNTRY = frozenset({"remote", "any"})


def normalize_question(text: Any) -> str:
    """NFKC, casefold, collapse every non-alphanumeric run to one space, strip."""
    return _NON_ALNUM.sub(" ", unicodedata.normalize("NFKC", str(text or "")).casefold()).strip()


def fits_country(rec: dict[str, Any], country: Optional[str]) -> bool:
    """False when ``rec`` is a country-bound answer (:data:`COUNTRY_BOUND_TOPIC_PREFIXES`) for another
    country, or for no named country while the job is not that employer's own (company-scoped) one."""
    topic = str(rec.get("topic") or "").casefold()
    if not topic.startswith(COUNTRY_BOUND_TOPIC_PREFIXES):
        return True
    parts = topic.split(".")[1:]
    if _EVERY_COUNTRY & set(parts):
        return True
    codes = {p.upper() for p in parts if len(p) == 2 and p.isalpha()}
    if codes:
        return bool(country) and str(country).upper() in codes
    return bool(str(rec.get("company") or "").strip())  # one employer's own wording, scoped by company


def _in_scope(rec: dict[str, Any], company_key: Optional[str]) -> bool:
    scoped = str(rec.get("company") or "").strip()
    if not scoped:
        return True
    if not company_key:
        return False
    from src.services.applications.kit import normalized_company_key  # noqa: PLC0415

    return normalized_company_key(scoped) == company_key


def _wordings(rec: dict[str, Any]) -> set[str]:
    aliases = rec.get("aliases")
    texts = [rec.get("question"), *(aliases if isinstance(aliases, list) else [])]
    return {n for n in (normalize_question(t) for t in texts) if n}


def _pick(cands: list[dict[str, Any]]) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """One candidate, none, or (ambiguous) none plus a note: two candidates that
    disagree are never resolved by a guess."""
    if len(cands) > 1 and len({normalize_question(c.get("answer")) for c in cands}) > 1:
        return None, "ambiguous: more than one saved answer matches; ask the user which one is right"
    return (cands[0] if cands else None), None


def resolve_answer(
    answers: Any, *, topic: Optional[str] = None, question: Optional[str] = None,
    company_key: Optional[str] = None, country: Optional[str] = None,
) -> tuple[Optional[dict[str, Any]], Optional[str]]:
    """``(record, note)``. Only approved, in-scope answers. Order: the ``topic``
    (canonical key), then the question wording or an alias; within each, the
    company-scoped answer (narrower) before the unscoped one. Two disagreeing
    candidates at one level = ``(None, note)``; nothing matching = ``(None, None)``."""
    usable = [
        r for r in (answers if isinstance(answers, list) else [])
        if isinstance(r, dict) and r.get("approved") is True and _in_scope(r, company_key)
        and fits_country(r, country)
        and not str(r.get("topic") or "").casefold().startswith(NEVER_REUSED_TOPIC_PREFIXES)
    ]
    want = normalize_question(question)
    stages: list[Callable[[dict[str, Any]], bool]] = []
    if topic:
        stages.append(lambda r: r.get("topic") == topic)
    if want:
        stages.append(lambda r: want in _wordings(r))
    for test in stages:
        for scoped in (True, False):
            cands = [r for r in usable if test(r) and bool(str(r.get("company") or "").strip()) is scoped]
            if cands:
                return _pick(cands)
    return None, None


def find_answer(
    answers: Any, *, topic: Optional[str] = None, question: Optional[str] = None,
    company_key: Optional[str] = None, country: Optional[str] = None,
) -> Optional[dict[str, Any]]:
    """The approved answer record from :func:`resolve_answer`, else ``None``. Never a guess."""
    return resolve_answer(answers, topic=topic, question=question, company_key=company_key, country=country)[0]
