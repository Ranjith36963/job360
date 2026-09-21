"""Profile extraction orchestrator.

Every profile input (CV, LinkedIn, GitHub, preferences) is read ONCE, by plain
deterministic code, into the one shared ``CVData``. When the user later changes
ANY input, the read re-runs from the STORED raw inputs (``cv.raw_text``,
``cv.linkedin_raw_text``, ``cv.github_repos_brief``, ``preferences.about_me``) —
no re-upload, no network re-fetch — producing a refreshed CVData and a new
profile-version id.

DECISION 28 (2026-09-21) — this used to run a second, LLM pass over each of the
four inputs, plus two curation passes (merge duplicates, suggest adjacent
skills). Six paid model calls, on Job360's key, guessing at the user's career.
They are gone. Job360 has no brain of its own: it keeps the raw text and the
structure it can prove, and the user's own agent reads that through
``get_profile`` and writes the structured fields back through ``update_profile``.

What that removes with the passes: the input-hash cost cache
(``EXTRACTOR_VERSION``, ``_input_hash``, ``stale_extraction_inputs``) — there is
no paid call left to skip — and the soft-empty/partial retry logic that existed
because rate-limited free-tier providers answered ``{}`` without raising.

Each step no-ops when its input is missing, so this is safe to call with a
partial profile, and it never touches the network.
"""

from __future__ import annotations

import copy
import logging
import re

from src.services.profile.cv_parser import (
    _maybe_normalise_skills_via_esco,
    deterministic_cv_fields,
)
from src.services.profile.extraction_quality import (
    needs_escalation,
    score_extraction,
)
from src.services.profile.github_enricher import deterministic_github_fields
from src.services.profile.linkedin_parser import (
    deterministic_linkedin_fields,
    enrich_cv_from_linkedin,
    merge_linkedin_fields,
)
from src.services.profile.models import CVData, UserProfile
from src.services.profile.preferences import (
    deterministic_about_me_fields,
    merge_cv_and_preferences,
)
from src.services.profile.seniority import infer_experience_level

logger = logging.getLogger("job360.profile.two_pass")


def _merge_str_list(dst: list[str], src: list[str]) -> None:
    """Append items from ``src`` not already in ``dst`` (case-insensitive),
    preserving ``dst`` order then ``src`` order. Mutates ``dst`` in place."""
    seen = {s.lower() for s in dst}
    for s in src or []:
        if isinstance(s, str) and s.strip() and s.lower() not in seen:
            dst.append(s)
            seen.add(s.lower())


def _norm_for_dedup(s: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace — so 'The Complete
    Python Bootcamp' and 'the complete python bootcamp!' compare equal."""
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def dedup_by_containment(items: list[str]) -> list[str]:
    """Collapse fragments + exact duplicates in a free-text list (certifications,
    education) while preserving order.

    An entry is dropped when its normalized form equals (a later exact dup) or is
    a proper substring of another entry's normalized form — i.e. it's a line-wrap
    fragment of a more complete entry ('The Complete Python Bootcamp' inside 'The
    Complete Python Bootcamp from Zero to Hero in Python (Udemy, 2024)'). Distinct
    entries are always kept. Structural (containment), never a keyword list.
    """
    cleaned = [it for it in items if it and it.strip()]
    norms = [_norm_for_dedup(it) for it in cleaned]
    drop: set[int] = set()
    for i, na in enumerate(norms):
        if not na:
            drop.add(i)
            continue
        for j, nb in enumerate(norms):
            if i == j or j in drop:
                continue
            if na == nb:
                if i > j:          # exact dup — keep the earlier
                    drop.add(i)
                    break
            elif na in nb:         # a fragment of a more complete entry
                drop.add(i)
                break
    return [it for k, it in enumerate(cleaned) if k not in drop]


def dedup_fuzzy(items: list[str], threshold: int = 85) -> list[str]:
    """Collapse near-identical free-text entries (spelling / punctuation / word-
    order / an extra date suffix) that exact + containment dedup miss — e.g. a
    certification listed in British vs American spelling. Uses RapidFuzz
    ``token_set_ratio`` (lazy-imported per rule #16); keeps the LONGER, more
    complete form of each matched group. General fuzzy-similarity algorithm, NOT a
    synonym vocabulary (rule #28).

    NOTE: intended for certifications, NOT education — different degrees at the
    same institution ("MSc AI" vs "MSc Data Science — Uni X") score higher than a
    real degree duplicate, so fuzzy merging education would collapse DISTINCT
    qualifications. Education uses containment-only dedup.
    """
    cleaned = [it for it in items if it and it.strip()]
    if len(cleaned) < 2:
        return cleaned
    try:
        from rapidfuzz import fuzz  # noqa: PLC0415 — heavy dep, lazy (rule #16)
    except ImportError:
        return cleaned
    kept: list[str] = []
    kept_norms: list[str] = []
    for it in cleaned:
        ni = _norm_for_dedup(it)
        match = None
        for k, nk in enumerate(kept_norms):
            if fuzz.token_set_ratio(ni, nk) >= threshold:
                match = k
                break
        if match is None:
            kept.append(it)
            kept_norms.append(ni)
        elif len(it) > len(kept[match]):   # keep the more complete form
            kept[match] = it
            kept_norms[match] = ni
    return kept


def reset_cv_owned_fields(cv: CVData) -> None:
    """Clear everything the CV owns, so a NEW upload cannot inherit the old one.

    Call this the moment a DIFFERENT CV replaces the current one — before the
    new ``raw_text`` is parsed.

    WHY THIS EXISTS. The upload route only replaces ``cv_data.raw_text``; every
    other field stays, and the re-read then UNIONS its lists into what is
    already there — correct when re-reading the SAME CV, wrong when the CV has
    been swapped. That produced, in production, a single profile holding the
    FIRST person's name/headline/location/summary and BOTH people's skills,
    roles, companies and education. A real profile went 104 skills -> 152 after
    a different person's CV was uploaded, while ``name`` still read the original
    owner. Tailored CVs and cover letters are written from this profile, so the
    wrong name reaches an employer.

    Mutates IN PLACE: the route holds ``profile.cv_data``, so rebinding a new
    object here would be silently discarded.

    Deliberately NOT cleared: ``raw_text`` (the caller is about to overwrite it), and everything
    sourced from a DIFFERENT input the user did not re-upload — ``linkedin_*``,
    ``github_*`` and ``about_me_inferred_skills``. Wiping those would silently
    delete work, which is the opposite failure.
    """
    # EVERY CV-OWNED FIELD, DERIVED FROM THE DATACLASS — scalars AND collections.
    #
    # THIS LIST HAS NOW DRIFTED THREE TIMES, always the same way: a shelf is
    # added, wired into the merge and into a reader, and nobody adds it here.
    #   * 2026-08-07  career_domain
    #   * 2026-08-10  cv_experience_level, cv_right_to_work, cv_projects
    #   * 2026-08-15  cv_positions  <- found by running the real function
    #
    # The 2026-08-10 round fixed only the SCALAR half by deriving it from
    # ``dataclasses.fields``; the collections stayed hand-listed, so
    # ``cv_positions`` (a ``list[dict]``, invisible to a scalar-only loop)
    # survived every clear and every CV swap. Measured on the shipped code: a
    # profile cleared through this function kept
    # ``[{'company': 'ACME Ltd', 'title': 'Senior Engineer', ...}]`` while name,
    # summary, skills and job_titles were all correctly emptied — so the
    # previous owner's dated history was still being handed out as this
    # profile's experience.
    #
    # Half a derivation is not a derivation. Resetting each field to the value a
    # FRESH ``CVData()`` has removes the concept of a list entirely: a shelf
    # added tomorrow is covered the moment it is declared, whatever its type.
    # Collections are cleared IN PLACE, never rebound. Rebinding is visible
    # through ``cv`` and looks equivalent, but it orphans any reference taken
    # before the call — and there is a test pinning that identity
    # (test_two_pass.py::test_reset_mutates_in_place_so_the_profile_keeps_its_object),
    # which caught exactly this when the first version of the derivation used a
    # blanket setattr. Scalars have no identity to preserve, so they are simply
    # assigned their default.
    pristine = CVData()
    for name in _cv_owned_fields():
        default = getattr(pristine, name)
        current = getattr(cv, name)
        if isinstance(current, (list, dict)) and isinstance(default, (list, dict)):
            current.clear()
        else:
            setattr(cv, name, copy.deepcopy(default))


# Scalar CVData fields nothing but the upload route may write, each with the
# reason it is somebody else's to own. They are excluded from the reset below
# because clearing them would throw away a receipt, not a reading of the CV.
_NOT_CV_OWNED_SCALARS: dict[str, str] = {
    "raw_text": "the source document itself — written by the upload route",
    "cv_filename": "upload receipt, stamped by the API route",
    "cv_uploaded_at": "upload receipt, stamped by the API route",
    "linkedin_filename": "upload receipt, stamped by the API route",
    "linkedin_uploaded_at": "upload receipt, stamped by the API route",
    "github_connected_at": "connection receipt, stamped by the API route",
}


def _cv_owned_fields() -> tuple[str, ...]:
    """EVERY CVData field the CV owns — scalars, lists and dicts alike.

    NO type filter: a reset that only understands strings leaves every list and
    dict behind, which is precisely the defect this replaced (``cv_positions``
    survived a full profile clear and reached the matcher).

    Ownership is decided by PREFIX, so a shelf added tomorrow is covered the day
    it is declared — the one property that stops this drifting a fourth time.
    """
    import dataclasses as _dc

    out: list[str] = []
    for f in _dc.fields(CVData):
        if f.name in _NOT_CV_OWNED_SCALARS:
            continue
        if f.name.startswith(("linkedin_", "github_", "about_me_")):
            continue
        out.append(f.name)
    return tuple(out)


async def run_two_pass_extraction(profile: UserProfile) -> UserProfile:
    """Re-read every stored profile input, deterministically, in place.

    Runs entirely from data already on the profile — never re-reads a file and
    never hits the network. Returns the same ``profile`` object for convenience.

    Still ``async`` because every caller is an async route and the signature is
    part of five route handlers' call path; nothing inside awaits I/O any more.
    """
    cv = profile.cv_data
    prefs = profile.preferences

    # Each input follows the SAME shape:
    #     stored raw ──▶ deterministic_X(raw) ──▶ merged into the ONE CVData
    # Deterministic = STRUCTURE only (CLAUDE.md rule #28). MEANING — the roles,
    # the dates, the skills stated only in prose — is the user's agent's to
    # write, through update_profile (decision 28). We never guess it here and we
    # never clear what the agent wrote.

    # ── (1) CV ── raw = cv.raw_text ────────────────────────────────────
    if cv.raw_text:
        det_cv = deterministic_cv_fields(cv.raw_text)
        # ESCO normalisation runs HERE, not only in ``cv_data_from_text``.
        # It used to ride the CV LLM merge, and that merge is gone — so
        # without this line the web upload path (``_capture_cv_raw`` +
        # this function) produced ``cv_skills_esco = {}`` forever while the
        # CLI path still filled it, and ``reset_cv_owned_fields`` went on
        # clearing the map on every CV swap. A no-op when
        # ``ESCO_SKILL_NORMALISATION_ENABLED`` is off, which is the default.
        det_skills, det_esco = _maybe_normalise_skills_via_esco(
            list(det_cv.get("skills", []))
        )
        _merge_str_list(cv.skills, det_skills)
        cv.cv_skills_esco.update(det_esco)
        if not cv.summary and det_cv.get("summary"):
            cv.summary = det_cv["summary"]

    # ── (2) LinkedIn ── raw = cv.linkedin_raw_text ─────────────────────
    if cv.linkedin_raw_text:
        det_li = deterministic_linkedin_fields(cv.linkedin_raw_text)
        enrich_cv_from_linkedin(cv, merge_linkedin_fields(det_li))

    # ── (3) GitHub ── raw = cv.github_repos_brief ──────────────────────
    if cv.github_repos_brief:
        _merge_str_list(
            cv.github_skills_inferred, deterministic_github_fields(cv.github_repos_brief)
        )

    # ── (4) Preferences ── raw = prefs.about_me ────────────────────────
    if prefs.about_me:
        _merge_str_list(
            cv.about_me_inferred_skills, deterministic_about_me_fields(prefs.about_me)
        )

    # Collapse line-wrap fragments + cross-source duplicates in the free-text
    # lists so the profile shows each certification / qualification ONCE (a CV +
    # LinkedIn both list the same cert in slightly different words, and PDF wraps
    # split one cert across two lines — both inflated the counts and read as junk).
    # Certs: containment (drop line-wrap fragments) + fuzzy (collapse spelling /
    # punctuation variants like Visualisation/Visualization). Education: containment
    # ONLY — fuzzy would merge DIFFERENT degrees at the same institution.
    #
    # These are the LAST of the de-duplication that survives: the two LLM
    # curation passes that used to follow (merge entries that MEAN the same
    # thing, suggest adjacent skills) were an opinion about the user's career,
    # which is exactly what Job360 no longer has. Both helpers below are
    # punctuation-structural — no vocabulary, no judgement (rule #28).
    cv.certifications = dedup_fuzzy(dedup_by_containment(cv.certifications))
    cv.education = dedup_by_containment(cv.education)

    # Clean the LinkedIn skill list the same way the CV list is cleaned.
    #
    # It never was, and a real profile showed the cost: "RAG" AND
    # "Retrieval-Augmented Generation", "LoRA" AND "Low-Rank Adaptation", plus a
    # stray "LLM'S". One capability counted two or three times inflates the skill
    # count without adding capability. Both helpers are punctuation-structural,
    # no vocabulary, so this stays rule-#28 safe and works for any profession.
    if cv.linkedin_skills:
        from src.services.profile.cv_parser import (  # noqa: PLC0415
            _det_collapse_acronyms,
            _strip_possessive,
        )

        cleaned = [_strip_possessive(s) for s in cv.linkedin_skills]
        cv.linkedin_skills = _det_collapse_acronyms([s for s in cleaned if s])

    # ── Fold the freshly-extracted CV skills/titles into preferences ──
    # (Was done in the CV upload route; lives here now so the SINGLE extractor
    # owns the whole job and the route doesn't have to extract anything.)
    if cv.skills or cv.job_titles:
        profile.preferences = merge_cv_and_preferences(
            cv.skills, cv.job_titles, prefs
        )

    # ── Seniority inference (2026-08-07) — a FACT read off dated CV/LinkedIn
    # positions, never a guessed preference; see seniority.py's module
    # docstring for why that distinction is what makes this legal under
    # product design rule #29. Runs LAST, after the merges above, so it reads
    # the fully-populated cv_positions/linkedin_positions — which, since
    # decision 28, is whatever the user's AGENT wrote there. Never raises: a
    # failed inference just leaves the field empty and the shelf stays silent.
    try:
        # Dated job TITLES are the stronger, structural signal, so they win.
        # ``cv_experience_level`` is the fallback for a career whose titles
        # carry no seniority word — also agent-written now.
        from_titles = infer_experience_level(cv.cv_positions, cv.linkedin_positions)
        inferred = from_titles or (cv.cv_experience_level or "").strip()
        # ONLY WRITE A VALUE, NEVER A BLANK.
        #
        # Both inputs are agent-written since decision 28 — our own read of a
        # CV produces no dated positions and no stated level. An unconditional
        # assignment therefore blanked this field on EVERY profile save for
        # every existing user: touch one preference, `_extract_save_trigger`
        # runs, and a stored "senior" became "". It is not in the overlay, so
        # it would have been gone for good. An empty inference now leaves the
        # stored answer alone — rule #29, an empty shelf stays silent rather
        # than overwriting a filled one.
        if inferred:
            profile.preferences.experience_level_inferred = inferred
    except Exception as exc:  # noqa: BLE001 - inference must never cost a save
        logger.warning("seniority inference failed (non-fatal): %s", exc)

    # ── THE UNIVERSAL GATE ────────────────────────────────────────────────
    # Grade what the profile now holds against the document it came from. This
    # is deliberately the LAST thing the extractor does, because it judges the
    # finished profile — not any one parser.
    #
    # Why it exists: extraction was being fixed one CV at a time, and every fix
    # was a patch for a layout we happened to have seen. Layouts are unbounded;
    # that road has no end. The product's job is to NOTICE when a profile is
    # thin rather than silently hand someone a profile that says nothing.
    #
    # It now grades the JOINT result — our structural read plus whatever the
    # user's agent has written — and it re-runs on every save, so a thin score
    # right after an upload is the expected state, not a defect: it is the
    # signal that the agent has not filled the profile in yet.
    #
    # Never raises and never blocks a save: a scoring failure must not cost a
    # user their upload. The score is advisory — carried on the profile so the
    # API can show it, and logged so a low score is visible in prod.
    try:
        score = score_extraction(
            cv.raw_text or "",
            list(cv.skills or []),
            job_titles=list(cv.job_titles or []),
            summary=cv.summary or "",
            certifications=list(cv.certifications or []),
        )
        cv.extraction_score = score.as_dict()
        if needs_escalation(score):
            logger.warning(
                "extraction scored %s (%.2f) for this profile - coverage %.0f%%, "
                "precision %.0f%%. Problems: %s",
                score.verdict, score.overall, score.coverage * 100,
                score.precision * 100, "; ".join(score.problems[:3]),
            )
        else:
            logger.info("extraction scored %s (%.2f)", score.verdict, score.overall)
    except Exception as exc:  # noqa: BLE001 - scoring must never cost an upload
        logger.warning("extraction scoring failed (non-fatal): %s", exc)

    return profile
