"""99% pre-filter cascade — blueprint §2 "Pre-filtering is everything".

Three cheap SQL-friendly checks run BEFORE the expensive 4-component scorer.
Order is tuned for maximum elimination per stage:

    1. location + work_arrangement    (~70% eliminated)
    2. experience_level               (~50% of remainder)
    3. skill_overlap (>= 1 match)     (~60-80% of remainder)

Combined: ~95-99% of jobs eliminated before ``JobScorer.score()`` runs.

API
---
The module exposes ``passes_prefilter(profile, job) -> bool`` plus the three
stage functions for unit tests. ``FilterProfile`` is a thin dataclass so the
caller can assemble it from any source (UserProfile, DB rows, test fixtures).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from src.models import Job

# One definition of "this string names the UK", shared with the ingestion gate
# (rule #30). A second copy here would drift, and the two would disagree about
# what counts as a country — which is exactly the class of bug this module has
# just been fixed for.
from src.services.uk_gate import _UK_SELF

# Experience level ladder — lower index = more junior.
_LEVEL_ORDER = {
    "intern": 0,
    "junior": 1,
    "entry": 1,
    "mid": 2,
    "mid-level": 2,
    "senior": 3,
    "staff": 4,
    "principal": 4,
    "lead": 4,
    "director": 5,
    "vp": 6,
    "head": 5,
}


@dataclass
class FilterProfile:
    """Minimal profile shape needed by the cascade — decoupled from UserProfile."""

    preferred_locations: list[str] = field(default_factory=list)
    work_arrangement: str = ""       # 'remote', 'hybrid', 'onsite', ''
    experience_level: str = ""       # 'junior', 'mid', 'senior', ...
    skills: set[str] = field(default_factory=set)


def _lower(values: Iterable[str]) -> list[str]:
    return [v.strip().lower() for v in values if v and v.strip()]


def location_ok(profile: FilterProfile, job: Job) -> bool:
    """Stage 1: location + work arrangement.

    Rules (permissive on purpose — false positives are cheap, false negatives
    are expensive since the scorer's location band catches fine-grained cases):
    * Empty preferred_locations → pass.
    * Remote job + user accepts remote/hybrid → pass.
    * Job location substring-matches any preferred location → pass.
    """
    if not profile.preferred_locations and not profile.work_arrangement:
        return True
    loc = (job.location or "").strip().lower()
    arrangement = (profile.work_arrangement or "").strip().lower()

    # Remote jobs are universally visible to remote/hybrid candidates.
    if "remote" in loc and arrangement in ("remote", "hybrid", ""):
        return True

    if not loc and arrangement in ("remote", "hybrid"):
        # Blank location + remote preference — give it the benefit of the doubt.
        return True

    for pref in _lower(profile.preferred_locations):
        # A COUNTRY-level preference is not a constraint here. The catalogue is
        # UK-only by construction (rule #30 — `uk_gate.check_uk` refuses the
        # rest at ingestion), so "United Kingdom" says nothing this gate can
        # act on. Matching it literally is worse than useless: the test is a
        # substring one, and "united kingdom" appears in neither direction of
        # "London" — so the single most natural thing a UK user can type would
        # have deleted almost the entire feed. Reuses the gate's own set so
        # there is one definition of "names the UK", not two.
        if pref in _UK_SELF:
            return True
        if pref in loc or loc in pref:
            return True

    # A preference this gate cannot RESOLVE must not be allowed to delete jobs.
    #
    # PR #297 fixed the country case, and stopped one level too high. Measured
    # against the real predicate afterwards:
    #     "United Kingdom" -> keeps everything   (the #297 fix)
    #     "Greater London" -> keeps London       (only because one is a
    #                                             substring of the other)
    #     "West Midlands"  -> keeps NOTHING      (Birmingham is IN it)
    #     "Yorkshire"      -> keeps NOTHING      (Leeds is IN it)
    #     "South East"     -> keeps NOTHING
    # A county or region is the second most natural thing a UK user types, and
    # it emptied the entire feed.
    #
    # The gazetteer answers this from DATA, not a typed list of regions (rule
    # #30): it holds ~52k UK SETTLEMENTS and no administrative divisions, so
    # "is this preference a place I can compare against a job location?" is
    # simply "is it in the gazetteer?". A settlement ("London", "Birmingham")
    # filters normally. Anything else — a region, a county, a typo — is a
    # preference this stage cannot evaluate, so it passes and lets the scorer's
    # location band do the fine-grained work. That matches this module's stated
    # philosophy: false positives are cheap here, false negatives are not.
    if _no_resolvable_place(profile.preferred_locations):
        return True

    # If user has no preferred location list but a non-remote arrangement
    # preference, let it through (arrangement will be checked by the scorer).
    return not profile.preferred_locations


def _no_resolvable_place(preferences: list[str]) -> bool:
    """True when NONE of the stated preferences names a UK place we know.

    Degrades safely: if the gazetteer is unavailable the answer is "cannot
    resolve", which passes the job rather than deleting it. A missing data file
    must never silently empty a feed — that failure has already happened once
    (the image shipped without the gazetteer for weeks).
    """
    try:
        from src.services.uk_gate import _gazetteer, _norm

        places, _foreign, _ambiguous = _gazetteer()
    except Exception:  # noqa: BLE001 — never let the gate crash a run
        return True
    if not places:
        return True
    return not any(_norm(p) in places for p in preferences if p and p.strip())


def experience_ok(profile: FilterProfile, job: Job) -> bool:
    """Stage 2: seniority match based on title keywords.

    Heuristic — no true level signal in the scraped payload, so we look at
    title tokens. Candidate level determined once from profile; job level
    inferred per-job from title regexes.
    """
    user_level = _LEVEL_ORDER.get(
        (profile.experience_level or "").strip().lower(), None
    )
    if user_level is None:
        return True  # user hasn't declared — don't filter

    title = (job.title or "").lower()
    job_level = None
    # Highest-seniority token wins.
    for token, lvl in sorted(_LEVEL_ORDER.items(), key=lambda kv: -kv[1]):
        if token in title:
            job_level = lvl
            break

    if job_level is None:
        return True  # no signal in title — keep the job

    # SI5 — ONE-DIRECTIONAL band, matching the docstring/Blueprint §2 rule
    # "junior candidates skip senior roles — NOT the reverse." Reject only when
    # the JOB is more than one level ABOVE the user (too senior). A senior user
    # is NOT filtered out of entry/junior roles they may still want — the old
    # symmetric `abs(...) <= 1` wrongly excluded them. `job_level - user_level`:
    # positive = job more senior than user (cap at +1); negative/zero = job at or
    # below the user's level (always kept).
    return (job_level - user_level) <= 1


def skill_overlap_ok(profile: FilterProfile, job: Job, *, min_overlap: int = 1) -> bool:
    """Stage 3: at least N skills from the profile appear in title+description."""
    if not profile.skills:
        return True  # no skills declared — don't filter
    hay = f"{job.title}\n{job.description}".lower()
    skills_lower = {s.lower() for s in profile.skills if s and s.strip()}
    hits = sum(1 for s in skills_lower if s in hay)
    return hits >= min_overlap


def passes_prefilter(profile: FilterProfile, job: Job) -> bool:
    return (
        location_ok(profile, job)
        and experience_ok(profile, job)
        and skill_overlap_ok(profile, job)
    )
