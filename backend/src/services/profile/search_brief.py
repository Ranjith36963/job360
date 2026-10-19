"""SEARCH BRIEF (owner decision 2026-10-12) - where THIS user's assistant searches.

The hunt recipe is written once for every user and names no country. The
countries come only from the user's own setup answers, the per-country
``user_info.right_to_work`` records (``remote_ok``, ``onsite``,
``needs_sponsorship``; country ``"ANY"`` = every other country). This module
turns those records into one row per place to search, each with a plain
"counts if" line, plus the GAPS: an answer the hunt needs that is missing.

Why it exists: on 2026-10-12 a hunt for "remote worldwide" came back all UK.
The free-text ``preferred_locations`` ("Any country where the employer sponsors
me") was not a place, so the search fell back to where the user lives. A brief
built from structured answers is the same for every assistant (even a small
model) and can be tested; free text cannot.

Pure: no I/O, no country list of our own, never a guess. Job360 still never
searches (VISION rule 4) - this only restates the user's answers.
"""
from __future__ import annotations

from typing import Any, Optional

from src.services.profile import job_priority
from src.services.profile.answer_match import resolve_answer
from src.services.profile.user_info import ANY_COUNTRY, ONSITE_VALUES, USER_INFO_ANSWERS_PATH

RIGHT_TO_WORK_PATH = "user_info.right_to_work"
_NO_SPONSORSHIP_AUTH = ("citizen", "permanent_resident")
WHERE_TO_SEARCH_ASK = (
    "Where should I search? Name each country (or say 'every other country'), and for each one: "
    "is remote OK, and is on-site OK - yes, only if they sponsor your visa, or no?"
)


def _needs_sponsorship(rec: dict[str, Any]) -> Optional[bool]:
    """The record's own answer; else read off ``work_authorization``; else None."""
    flag = rec.get("needs_sponsorship")
    if isinstance(flag, bool):
        return flag
    auth = rec.get("work_authorization")
    if auth in _NO_SPONSORSHIP_AUTH:
        return False
    if auth == "needs_sponsorship":
        return True
    return None


def _name(country: str) -> str:
    return "any other country" if country == ANY_COUNTRY else country


def _remote_line(country: str, work_from: list[str]) -> str:
    where = " or ".join([*work_from, "anywhere"])
    return f"fully remote with an employer in {_name(country)}; the ad must let you work from {where}"


def _onsite_line(country: str, onsite: str, needs: bool) -> str:
    place = f"on-site or hybrid in {_name(country)}"
    if onsite == "only_if_sponsored":
        return f"{place}, only when the ad says it sponsors a visa"
    if needs:
        return f"{place}; you need a visa there, so an ad that refuses sponsorship is out"
    return place


def work_from_question(residence: str, country: str) -> str:
    """The one question asked when a remote job would mean working from a country
    the user does not live in. The wording is the key an approved answer is matched on."""
    return f"You live in {residence}. Would you work from {country} for a remote job there?"


_YES = frozenset({"yes", "y", "true"})
_NO = frozenset({"no", "n", "false"})


def work_from_topic(country: str) -> str:
    """The fixed topic of the work-from-country gap; an answer saved with it closes the gap."""
    return f"work_from.{country}"


def _approved_answer(answers: Any, question: str, topic: str = "") -> tuple[str, Optional[str]]:
    """``(reply, note)``: the user's approved answer for the gap (its ``topic``, else the
    question wording or an alias), lower-cased; ``""`` when none or ambiguous (note says why)."""
    rec, note = resolve_answer(answers, topic=topic or None, question=question)
    return (str(rec.get("answer") or "").strip().lower().rstrip(".!") if rec else ""), note


def build_search_brief(
    right_to_work: Any, residence_country: Any = None, answers: Any = None,
) -> dict[str, Any]:
    """``{"rows": [...], "work_from": [...], "residence_country": ..., "gaps": [...]}``
    from the stored ``user_info.right_to_work`` value.

    ``residence_country`` (``user_info.contact``) and ``answers``
    (``user_info.answers``) catch a remote job in a country the user does not
    live in: an ad that lets you work from IN means working from IN. For each
    ``work_from`` country other than where they live, ask once; an approved
    "no" drops that country from ``work_from``; no approved answer = a gap.

    A row = one place the user said they would work (``remote_ok`` true, or
    ``onsite`` yes / only_if_sponsored); real countries first, ``ANY`` last.
    ``work_from`` = the countries where the user needs no sponsorship - where a
    remote job must let them work from. A gap = one question to ask the user
    once, then save to ``user_info.right_to_work``; never filled in for them.
    A record with only visa facts (neither ``remote_ok`` nor ``onsite``) is not
    a place; a record with one of the two gets the other asked.
    """
    records = right_to_work.get("countries") if isinstance(right_to_work, dict) else None
    records = [r for r in (records or []) if isinstance(r, dict) and isinstance(r.get("country"), str)]
    records.sort(key=lambda r: r["country"] == ANY_COUNTRY)
    work_from = [r["country"] for r in records if r["country"] != ANY_COUNTRY and _needs_sponsorship(r) is False]

    residence = residence_country.strip().upper() if isinstance(residence_country, str) else ""

    rows: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    plan: list[dict[str, Any]] = []
    remote_countries = {r["country"] for r in records if r.get("remote_ok") is True}
    if residence:
        for country in list(work_from):
            if country == residence or country not in remote_countries:
                continue
            question = work_from_question(residence, country)
            topic = work_from_topic(country)
            reply, note = _approved_answer(answers, question, topic)
            if reply in _NO:
                work_from.remove(country)
            elif reply not in _YES:  # no answer, or an unclear one: ask again, never infer consent
                gaps.append({
                    "path": USER_INFO_ANSWERS_PATH, "country": country, "ask": question, "topic": topic,
                    **({"note": note} if note else {}),
                })
    for rec in records:
        country, remote, onsite = rec["country"], rec.get("remote_ok"), rec.get("onsite")
        if not isinstance(remote, bool) and onsite in ONSITE_VALUES:
            # half answered: a missing remote_ok is a question, never a "no"
            gaps.append({
                "path": RIGHT_TO_WORK_PATH, "country": country,
                "ask": f"Remote jobs with an employer in {_name(country)}: OK or not?",
            })
        if remote is not True and onsite not in ("yes", "only_if_sponsored"):
            continue  # not a place they said they would work: never searched
        needs = _needs_sponsorship(rec)
        counts_if: list[str] = []
        priorities: dict[str, Optional[str]] = {}
        if remote is True:
            line = _remote_line(country, work_from)
            lvl = job_priority.level_of(rec, job_priority.REMOTE)
            if lvl != job_priority.OFF:  # the user switched this place off: never searched
                counts_if.append(line)
                priorities[job_priority.REMOTE] = lvl
                plan.append({"country": country, "kind": job_priority.REMOTE, "counts_if": line, "priority": lvl})
        if onsite in ("yes", "only_if_sponsored"):
            if needs is None and onsite == "yes":
                gaps.append({
                    "path": RIGHT_TO_WORK_PATH, "country": country,
                    "ask": f"Do you need visa sponsorship to work in {_name(country)}?",
                })
            line = _onsite_line(country, onsite, needs is not False)
            lvl = job_priority.level_of(rec, job_priority.ONSITE)
            if lvl != job_priority.OFF:
                counts_if.append(line)
                priorities[job_priority.ONSITE] = lvl
                plan.append({"country": country, "kind": job_priority.ONSITE, "counts_if": line, "priority": lvl})
        if onsite not in ONSITE_VALUES:
            gaps.append({
                "path": RIGHT_TO_WORK_PATH, "country": country,
                "ask": f"On-site in {_name(country)}: yes, only if they sponsor your visa, or no?",
            })
        if not counts_if:
            continue  # every place of this record is switched off
        rows.append({
            "country": country, "remote_ok": remote is True, "onsite": onsite or "",
            "needs_sponsorship": needs, "counts_if": counts_if, "priority": priorities,
        })
    if not rows:
        gaps.append({"path": RIGHT_TO_WORK_PATH, "country": None, "ask": WHERE_TO_SEARCH_ASK})
    return {
        "rows": rows, "work_from": work_from, "residence_country": residence or None, "gaps": gaps,
        # Owner decision 2026-10-19 - the user's own job priority: search in this order and
        # spend about effort_pct of the hunt on each place. priority null = not chosen.
        "plan": job_priority.effort_split(plan),
    }
