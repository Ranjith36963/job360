"""Validate and normalize user preferences form data."""

from __future__ import annotations

import logging
import re
from dataclasses import replace
from typing import Any

from src.services.profile.models import UserPreferences

logger = logging.getLogger("job360.profile.preferences")


# ── Preferences deterministic pass (Pass 1) — STRUCTURE only, NO LLM ─
#
# Structural label words a user might type in their free-text blurb to introduce
# an explicit list (e.g. "Skills: X, Y" / "Tech stack: A, B"). These are
# DOCUMENT-STRUCTURE markers — the same kind the CV deterministic pass keys off
# ("Skills"/"Summary" headings) — NOT a skill vocabulary. CLAUDE.md rule #28 bans
# hardcoded *skill* lists, not section labels. Free prose with no such marker
# yields nothing here; the LLM pass (llm_infer_from_about_me) handles prose.
_ABOUT_ME_SKILL_MARKERS = (
    "skills", "technical skills", "core skills", "key skills",
    "technologies", "tech stack", "stack", "tools", "tooling",
    "proficient in", "experienced with", "expertise", "specialties",
    "specialities", "competencies", "core competencies",
)
_ABOUT_ME_SPLIT_RE = re.compile(r"[,;|/•·‧]+|\s+and\s+")


# ── Preference sanitizer ─────────────────────────────────────────────────────
#
# WHY (2026-08-08, found on a live smoke test). A user's `additional_skills`
# held 131 entries, of which 21+ were NOT skills: CV job titles, company names,
# locations, date ranges, and whole experience-bullet sentences ("achieving 95%
# response accuracy…"). An older extraction merge dumped CV content into the
# user's preference boxes; that merge is now clean, but the pollution is frozen
# in stored rows AND the frontend autosaves it straight back on every form
# touch, so it never self-heals. `additional_skills` feeds search keywords, the
# LLM judge, embeddings and skill tiering (at the top 3.0 weight), so every
# match is scored against "June 2025 – September 2025" as if it were a skill.
#
# This drops ONLY what provably cannot be — or need not be — a user-typed extra
# skill. Three families, all safe:
#   1. ALREADY-EXTRACTED SKILLS — an entry that is a verbatim skill already in
#      an extracted shelf (cv.skills / linkedin_skills / github_*). This is the
#      big one: on the live profile 77 of 103 entries were exact duplicates of
#      the CV skills. Dropping them is ZERO-LOSS — the skill still shows under
#      its source shelf; only the mislabelled "Added by you" copy goes.
#   2. VERBATIM CV CONTENT — an entry equal to a CV job title / company /
#      location / education line, or contained in an experience bullet. Dropping
#      it is ZERO-LOSS BY CONSTRUCTION: matching still sees that content via
#      `cv_data`, so removing the duplicate from the preference box changes no
#      ranking. This is the rule that catches the sentence fragments.
#   3. STRUCTURAL NON-SKILLS — a date range, a string ending in "." , one
#      carrying a "NN%" metric, or absurdly long (>60 chars). No skill tag looks
#      like these.
# NO skill vocabulary or denylist is used (rule #28) — only structure and
# CV-duplication. A borderline real multi-word skill survives untouched.
# Any standalone 4-digit year (1900-2099) — a skill tag never carries one, so
# a year is a reliable date/tenure signal. Catches "June 2025 – September 2025",
# "October 2024 – January 2025", "(2024)".
_DATE_RANGE = re.compile(r"(19|20)\d{2}")
_METRIC = re.compile(r"\d{1,3}\s?%")


def _is_section_header(entry: str) -> bool:
    """An ALL-CAPS multi-word phrase is a CV section header, not a skill tag.

    'PROFESSIONAL EXPERIENCE', 'TECHNICAL SKILLS', 'WORK HISTORY' — skill tags
    are Title Case ('Machine Learning') or short acronyms ('CI/CD', 'IoT'),
    never a multi-word all-caps phrase. STRUCTURE check, not a denylist (#28):
    it keys off casing + word-count, not a fixed vocabulary. Single tokens
    ('AI', 'RUL', 'IoT') are left alone.
    """
    e = entry.strip()
    letters = [c for c in e if c.isalpha()]
    return (
        len(e.split()) >= 2
        and bool(letters)
        and all(c.isupper() for c in letters)
    )


def _is_structural_non_skill(entry: str) -> bool:
    e = entry.strip()
    if len(e) > 60:
        return True
    if e.endswith("."):
        return True
    if _DATE_RANGE.search(e):
        return True
    if _METRIC.search(e):
        return True
    if _is_section_header(e):
        return True
    return False


def _cv_get(cv_data: Any, name: str, default: Any) -> Any:
    """Read a field off a CVData OR a dict — defensively, never raising."""
    if isinstance(cv_data, dict):
        return cv_data.get(name, default)
    return getattr(cv_data, name, default)


# Every EXTRACTED skill shelf. A skill sitting in one of these was captured by
# extraction — it is not something the user "added on top". Having it ALSO in
# `additional_skills` (the "Added by you" box) is pure duplication.
_EXTRACTED_SKILL_SHELVES = (
    "skills",                 # CV skills
    "linkedin_skills",        # LinkedIn skills
    "github_llm_skills",      # GitHub skills the LLM read
    "github_skills_inferred", # GitHub skills from structure
    "cv_skills_esco",         # CV skills mapped to ESCO
)


def _extracted_skills_index(cv_data: Any) -> set[str]:
    """Case-folded set of every skill already captured in an EXTRACTED shelf.

    Dropping an `additional_skills` entry that appears here is ZERO-LOSS: the
    skill still lives in its source shelf (shown under CV/LinkedIn/GitHub skills
    and fed to matching/tiering from there), so only the mislabelled "Added by
    you" copy goes. This is the rule that removes the bulk of the live pollution
    — 77 of one user's 103 entries were exact duplicates of his CV skills.
    Entries may be plain strings OR ``{name|skill|label: ...}`` dicts (ESCO).
    """
    idx: set[str] = set()
    for field in _EXTRACTED_SKILL_SHELVES:
        for v in (_cv_get(cv_data, field, []) or []):
            if isinstance(v, str) and v.strip():
                idx.add(v.strip().casefold())
            elif isinstance(v, dict):
                name = v.get("name") or v.get("skill") or v.get("label") or ""
                if isinstance(name, str) and name.strip():
                    idx.add(name.strip().casefold())
    return idx


def _cv_content_index(cv_data: Any) -> tuple[set[str], set[str]]:
    """Case-folded exact CV strings, plus experience prose to substring-match.

    Returns (exact_set, prose_lines). `cv_data` may be a CVData or a dict — read
    defensively so the sanitizer never raises on an odd shape.
    """
    def g(name: str, default: Any) -> Any:
        return _cv_get(cv_data, name, default)

    exact: set[str] = set()

    def _add_loc(value: str) -> None:
        """Add a location and each comma/pipe-split part ('Stevenage, UK'
        -> 'Stevenage' + 'United Kingdom' as standalone polluted chips)."""
        if not (isinstance(value, str) and value.strip()):
            return
        exact.add(value.strip().casefold())
        for part in re.split(r"[,;/|]", value):
            if part.strip():
                exact.add(part.strip().casefold())

    for field in ("job_titles", "companies", "education"):
        for v in (g(field, []) or []):
            if isinstance(v, str) and v.strip():
                exact.add(v.strip().casefold())
    _add_loc(g("location", ""))

    # Dated work history holds the same content in a nested shape — a chip equal
    # to a position's title / company / location is CV content, not a user skill.
    for field in ("cv_positions", "linkedin_positions"):
        for pos in (g(field, []) or []):
            if not isinstance(pos, dict):
                continue
            for key in ("title", "company"):
                v = pos.get(key)
                if isinstance(v, str) and v.strip():
                    exact.add(v.strip().casefold())
            _add_loc(pos.get("location") or "")

    prose: set[str] = set()
    exp = g("experience_text", "") or ""
    if isinstance(exp, str):
        prose.update(ln.strip().casefold() for ln in exp.splitlines() if ln.strip())
    for pos in (g("cv_positions", []) or []):
        if isinstance(pos, dict):
            for b in (pos.get("bullets") or []):
                if isinstance(b, str) and b.strip():
                    prose.add(b.strip().casefold())
    return exact, prose


# Grammatical leading verbs that begin an experience bullet ("Improving X",
# "Delivered Y"). This is a STRUCTURE check, not a skill denylist (rule #28):
# a skill tag is a noun phrase and never starts with a clause verb. Catches the
# comma-split bullet fragments ("improving AI response", "delivering context-
# aware solutions") that no structural date/period/metric rule would.
_CLAUSE_VERBS = frozenset({
    "improving", "improved", "achieving", "achieved", "delivering", "delivered",
    "enhancing", "enhanced", "ensuring", "ensured", "accelerating", "accelerated",
    "reducing", "reduced", "developing", "developed", "designing", "designed",
    "engineering", "engineered", "integrating", "integrated", "collaborating",
    "collaborated", "building", "built", "architecting", "architected",
    "streamlining", "streamlined", "leading", "managing", "managed", "creating",
    "created", "implementing", "implemented", "cutting", "enabling", "driving",
})


def _is_prose_clause(entry: str) -> bool:
    words = entry.split()
    return len(words) > 1 and words[0].casefold() in _CLAUSE_VERBS


def _clean_skill_list(
    items: list[str], exact: set[str], prose: set[str], extracted: set[str]
) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if not isinstance(raw, str):
            continue
        e = raw.strip()
        if not e:
            continue
        ef = e.casefold()
        if ef in extracted:
            continue  # already in an extracted skill shelf — duplicate, and
            #            ZERO-LOSS to drop: the skill still shows under its
            #            source (CV/LinkedIn/GitHub). This is the main rule.
        if ef in exact:
            continue  # verbatim CV job-title/company/location/education
        if ef in seen:
            continue  # duplicate within the box itself
        if ef in prose:
            continue  # IS a whole experience bullet (exact, not substring —
            #            a real skill can appear inside a bullet, so substring
            #            matching would wrongly eat PyTorch/Docker/etc.)
        if _is_structural_non_skill(e):
            continue  # date / ends-with-period / metric / over-long
        if _is_prose_clause(e):
            continue  # verb-led bullet fragment
        seen.add(ef)
        out.append(e)
    return out


def sanitize_preferences(
    prefs: UserPreferences, cv_data: Any
) -> UserPreferences:
    """Strip extraction pollution from the user-typed preference boxes.

    Only `additional_skills` and `target_job_titles` can be polluted (they are
    the two the old merge wrote into). Everything else is passed through
    untouched via ``replace``. Safe to run on every save — clean input is a
    no-op.
    """
    exact, prose = _cv_content_index(cv_data)
    extracted = _extracted_skills_index(cv_data)
    clean_skills = _clean_skill_list(
        list(prefs.additional_skills or []), exact, prose, extracted
    )
    # target_job_titles: a preference for roles the user WANTS, so an entry
    # equal to one of their PAST roles (cv.job_titles) is pollution, not a
    # target. Structural junk is dropped too; verbatim past-role match is the
    # main rule.
    clean_titles = [
        t for t in (prefs.target_job_titles or [])
        if isinstance(t, str) and t.strip()
        and t.strip().casefold() not in exact
        and not _is_structural_non_skill(t)
    ]
    return replace(
        prefs,
        additional_skills=clean_skills,
        target_job_titles=clean_titles,
    )


def deterministic_about_me_fields(about_me: str) -> list[str]:
    """Pass 1 for preferences over ``about_me`` — STRUCTURE only, NO LLM.

    Pulls items the user explicitly listed after a structural label such as
    ``"Skills:"`` / ``"Technologies:"``. No skill vocabulary and no semantic
    guessing (CLAUDE.md rule #28) — a free-prose blurb with no such marker
    returns ``[]``; the LLM pass (``llm_infer_from_about_me``) mines prose.

    Mirrors the CV/LinkedIn deterministic passes so every input has a real,
    independent deterministic half that feeds the same merge.
    """
    if not about_me or not about_me.strip():
        return []
    out: list[str] = []
    seen: set[str] = set()
    for raw_line in about_me.splitlines():
        line = raw_line.strip()
        if ":" not in line:
            continue
        label, _, rest = line.partition(":")
        if label.strip().lower() not in _ABOUT_ME_SKILL_MARKERS:
            continue
        for tok in _ABOUT_ME_SPLIT_RE.split(rest):
            t = tok.strip().strip(".").strip()
            if t and len(t) <= 40 and t.lower() not in seen:
                out.append(t)
                seen.add(t.lower())
    return out


# ── Preferences LLM pass (Pass 2) — mine free-text about_me ─────────

_ABOUT_ME_SYSTEM = (
    "You read a job-seeker's free-text 'about me' blurb and name the concrete "
    "professional skills it implies. You return JSON only and never invent "
    "skills the text does not support."
)

_ABOUT_ME_PROMPT = """Read this job-seeker's free-text "about me" and extract the concrete
professional SKILLS it implies — tools, methods, domains, specialities.

Return JSON: {{"skills": ["Skill One", "Skill Two", ...]}}

Rules:
- Only skills the text actually supports. Do not invent.
- Individual items, not categories.
- Extract BOTH named tools AND the higher-level capabilities they imply —
  "production GenAI systems" → "Production GenAI"; "cloud-scale deployments" →
  "Cloud Deployment"; "vector databases (ChromaDB, FAISS)" → "Vector Databases",
  "ChromaDB", "FAISS".
- Domain-agnostic: technical OR non-technical (e.g. "Stakeholder Management", "HIPAA Compliance", "Welding").

ABOUT ME:
---
{about_me}
---"""


# NOTE (CLAUDE.md rule #28): the deterministic about_me skill scanner that used
# to live here was removed — it relied on hardcoded skill-term vocabularies.
# Mining the free-text about_me for skills is the LLM's job (llm_infer_from_about_me).


async def llm_infer_from_about_me(about_me: str) -> list[str]:
    """Pass 2 for preferences — mine the free-text ``about_me`` for skills the
    structured form never captured.

    Returns ``[]`` (never raises) on blank input or provider failure. Blank
    input never calls the LLM (cost guard).
    """
    if not about_me or not about_me.strip():
        return []
    prompt = _ABOUT_ME_PROMPT.format(about_me=about_me.strip())
    try:
        from src.services.profile.llm_provider import llm_extract  # noqa: PLC0415
        result = await llm_extract(prompt, system=_ABOUT_ME_SYSTEM)
    except Exception as e:  # noqa: BLE001 — never crash the pass
        logger.warning("about_me LLM skill inference failed: %s", e)
        return []

    raw = result.get("skills") if isinstance(result, dict) else None
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for s in raw:
        if isinstance(s, str) and s.strip() and s.strip().lower() not in seen:
            out.append(s.strip())
            seen.add(s.strip().lower())
    return out


def _split_and_clean(value: str) -> list[str]:
    """Split a comma-separated string and strip whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def validate_preferences(data: dict[str, Any]) -> UserPreferences:
    """Convert a raw form dict into a validated UserPreferences."""
    # Handle both list and comma-separated string inputs
    def to_list(key: str) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, str):
            return _split_and_clean(val)
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        return []

    return UserPreferences(
        target_job_titles=to_list("target_job_titles"),
        additional_skills=to_list("additional_skills"),
        excluded_skills=to_list("excluded_skills"),
        preferred_locations=to_list("preferred_locations"),
        industries=to_list("industries"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        work_arrangement=data.get("work_arrangement", ""),
        experience_level=data.get("experience_level", ""),
        negative_keywords=to_list("negative_keywords"),
        about_me=data.get("about_me", ""),
    )


def merge_cv_and_preferences(
    cv_skills: list[str],
    cv_titles: list[str],
    prefs: UserPreferences,
) -> UserPreferences:
    """Merge CV-extracted data with user preferences. Preferences take priority."""
    # target_job_titles = the ROLES THE USER WANTS, their own typed list only. It
    # must NOT absorb past CV job titles — folding them in surfaced things like
    # "AI Solutions Engineer – R&D Department" and near-duplicate intern titles
    # ("AI/ML Engineer Intern" vs "AI / ML intern") as "Roles you're targeting",
    # which is wrong and erodes trust. Past titles live on cv.job_titles and inform
    # search separately. (cv_titles stays in the signature for API stability.)
    merged_titles = []
    seen_titles = set()
    for title in prefs.target_job_titles:
        if title.lower() not in seen_titles:
            merged_titles.append(title)
            seen_titles.add(title.lower())

    # additional_skills = the user's OWN extras only ("Skills beyond what your CV
    # contains"). It must NOT absorb cv_skills: the tiering scores every
    # additional_skills item as user_declared (3.0), so folding the whole CV in
    # here forced every skill into the Primary tier (Secondary/Tertiary always
    # empty) and stuffed the preferences box with the entire CV. cv_skills already
    # live on cv.skills and are read independently by the tiering
    # (collect_evidence_from_profile) and domain_classifier. Dedup the user's own
    # list, minus excluded. (cv_skills stays in the signature for API stability.)
    excluded = {s.lower() for s in prefs.excluded_skills}
    merged_skills = []
    seen_skills = set()
    for skill in list(prefs.additional_skills):
        key = skill.lower()
        if key not in seen_skills and key not in excluded:
            merged_skills.append(skill)
            seen_skills.add(key)

    # dataclasses.replace, NOT a hand-listed constructor.
    #
    # This function only derives two fields (titles and skills); everything else
    # is meant to pass through untouched. Listing the pass-through fields by
    # hand meant every field added to UserPreferences afterwards was silently
    # dropped here — and this runs on EVERY profile save once a user has any
    # extracted data.
    #
    # Measured 2026-08-08: `needs_visa` True -> False and `preferred_workplace`
    # 'remote' -> None on every save. A user who ticked "I need visa
    # sponsorship" had it reset before it reached the database, so the visa
    # scoring gate went dark for exactly the person who asked for it. No error,
    # no log. `experience_level_inferred` (added the same week) was being lost
    # the same way.
    #
    # `replace` copies every field the dataclass has, so a field added tomorrow
    # cannot be forgotten here. The bug class is closed, not just its instance.
    return replace(
        prefs,
        target_job_titles=merged_titles,
        additional_skills=merged_skills,
    )
