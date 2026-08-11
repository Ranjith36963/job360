"""Two-pass profile extraction orchestrator.

Goal: every profile input (CV, LinkedIn, GitHub, preferences) goes through a
deterministic pass (plain code) AND an LLM enhance pass, both merged into one
``CVData``. When the user later changes ANY input, both passes re-run from the
STORED raw inputs (``cv.raw_text``, ``cv.linkedin_raw_text``,
``cv.github_repos_brief``, ``preferences.about_me``) — no re-upload, no network
re-fetch — producing a refreshed CVData and a new profile-version id.

Each pass gracefully no-ops when its input is missing or the LLM provider chain
is unavailable, so this is safe to call with partial profiles and offline.

Heavy/LLM imports stay module-local-friendly; the four enhance helpers are
imported at module top so tests can monkeypatch them by name.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from typing import Any

from src.services.profile.cv_parser import (
    deterministic_cv_fields,
    llm_cv_fields_from_text,
)
from src.services.profile.extraction_quality import (
    needs_escalation,
    score_extraction,
)
from src.services.profile.github_enricher import (
    deterministic_github_fields,
    llm_infer_github_skills,
)
from src.services.profile.linkedin_parser import (
    deterministic_linkedin_fields,
    enrich_cv_from_linkedin,
    llm_linkedin_fields,
    merge_linkedin_fields,
)
from src.services.profile.models import CVData, UserProfile
from src.services.profile.preferences import (
    deterministic_about_me_fields,
    llm_infer_from_about_me,
    merge_cv_and_preferences,
)
from src.services.profile.seniority import infer_experience_level

logger = logging.getLogger("job360.profile.two_pass")


async def _none() -> None:
    """Placeholder coroutine for an input that is absent.

    gather() needs a coroutine in every slot so the results unpack positionally;
    this keeps the call site readable instead of building the list conditionally.
    """
    return None


EXTRACTOR_VERSION = "4"
"""Bump this whenever an LLM extraction PROMPT changes in a way that should
re-read inputs the system has already seen.

WHY IT EXISTS (measured on a live profile, 2026-08-08). The cost cache keyed on
the INPUT alone: same CV text -> skip the paid pass. Correct for cost, wrong for
correctness — because it silently means **a prompt improvement never reaches an
existing user**. Their extraction is frozen at whatever the prompt produced the
last time their input changed, and re-uploading the same CV cannot fix it (the
text is identical, so the hash still matches).

Proof: the CV prompt already instructs the LLM to mine skills demonstrated in
prose ("inside a project, an experience bullet...", ``cv_parser`` RULE 9). A real
profile's CV names predictive maintenance, fraud detection, IoT, RUL and
multi-agent workflow in its experience bullets, yet none reached ``cv.skills`` —
the improved prompt had never run over that CV, because the hash matched.

Including this version in the digest makes a prompt change invalidate the cache
exactly once per user, which is the intended cost: pay again only when the
extractor genuinely got better.

THE FAILURE MODE IS SILENT IN BOTH DIRECTIONS, so bump on any prompt change.
Seven new LinkedIn section prompts shipped on 2026-08-09 while this still read
"2". Every existing user's LinkedIn hash therefore still matched, the pass was
skipped as a cache hit, and the new sections could never populate for anyone who
had already uploaded — which was every user. Nothing errored; the shelves simply
stayed empty and looked like profiles that genuinely had no honors or patents.

Version log — 1: input-only hash (pre-2026-08-08). 2: prose-mining CV prompt.
3: the seven LinkedIn section prompts (honors, publications, patents,
organizations, test_scores, recommendations, interests) + the contact block.
4: the CV ``right_to_work`` prompt field.
"""


def _input_hash(raw: Any) -> str:
    """Stable fingerprint of one extraction input AND the extractor that read it.

    Accepts whatever the four inputs actually are — CV/LinkedIn text are strings,
    ``github_repos_brief`` may be a list of dicts — so a non-string is serialised
    with sorted keys to keep the digest stable across runs (Python dict order is
    insertion-ordered, and a re-fetch could reorder it without the content
    changing, which would look like a change and re-bill the user).

    ``EXTRACTOR_VERSION`` is folded in so improving a prompt re-reads inputs the
    system has already seen — see the note on that constant. Old stored digests
    were computed WITHOUT it, so they simply stop matching and every profile
    re-extracts once, which is the desired behaviour.
    """
    if not isinstance(raw, str):
        raw = json.dumps(raw, sort_keys=True, default=str)
    payload = f"v{EXTRACTOR_VERSION}\x00{raw}"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _pass_produced_data(key: str, res: Any) -> bool:
    """Did an LLM pass return anything worth caching?

    A pass that returned an empty result from a NON-empty input is almost always
    a soft failure (a rate-limited provider answering with `{}` instead of
    raising), and caching it freezes that emptiness forever. So only a pass that
    yielded usable data records its hash; an empty one re-runs next time.

    Shapes differ per input: the CV pass returns a ``CVData``; the others return
    dicts or lists. This reads the fields that actually matter for each.
    """
    if res is None:
        return False
    if key == "cv":
        # llm_cv_fields_from_text returns a CVData; skills OR titles is enough.
        return bool(getattr(res, "skills", None) or getattr(res, "job_titles", None))
    if key == "linkedin":
        # A dict of {skills, positions, education, certifications}.
        d = res if isinstance(res, dict) else {}
        return bool(d.get("skills") or d.get("positions") or d.get("education"))
    # github / about_me return a list[str] of inferred skills.
    return bool(res)


def _already_read(cv: CVData, key: str, raw: Any) -> bool:
    """True when a paid LLM pass has already absorbed this exact input.

    The result of that pass is merged into ``cv``, so skipping the call loses
    nothing — the data is already in the fields. Returns False for empty input
    so the "no input" path stays the caller's existing ``if raw`` check.
    """
    return bool(raw) and cv.llm_input_hashes.get(key) == _input_hash(raw)


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
    other field stays. ``_merge_cv_llm_into`` (below) then fills empty scalars
    ONLY and UNIONS the lists — both correct when RE-RUNNING extraction over the
    same CV, both wrong when the CV has been swapped. Together they produced, in
    production, a single profile holding the FIRST person's name/headline/
    location/summary and BOTH people's skills, roles, companies and education. A
    real profile went 104 skills -> 152 after a different person's CV was
    uploaded, while ``name`` still read the original owner. Tailored CVs and
    cover letters are generated from this profile, so the wrong name reaches an
    employer.

    Mutates IN PLACE: the route holds ``profile.cv_data``, so rebinding a new
    object here would be silently discarded.

    The field list below must stay in lockstep with ``_merge_cv_llm_into`` —
    they are two halves of one contract: "what the CV owns". Deliberately NOT
    cleared: ``raw_text`` (the caller is about to overwrite it), and everything
    sourced from a DIFFERENT input the user did not re-upload — ``linkedin_*``,
    ``github_*`` and ``about_me_inferred_skills``. Wiping those would silently
    delete work, which is the opposite failure.
    """
    # Scoring-semantic — these reach SearchConfig and change what matches.
    cv.skills.clear()
    cv.job_titles.clear()
    cv.companies.clear()
    cv.education.clear()
    cv.certifications.clear()
    cv.cv_industries.clear()
    cv.cv_languages.clear()
    cv.cv_skills_esco.clear()
    cv.summary = ""
    cv.experience_text = ""

    # Identity / display — the fields that put the wrong name on a tailored CV.
    cv.name = ""
    cv.headline = ""
    cv.location = ""
    cv.achievements.clear()

    # Classified FROM the CV, so it belongs to the CV. ``None`` (not "") is the
    # documented "not classified" sentinel on CVData.
    cv.career_domain = None

    # Regenerated wholesale from the merged skill set on every two-pass run, but
    # cleared so a failed re-extract cannot leave suggestions computed from a
    # different person's skills.
    cv.suggested_skills.clear()

    # MUST be cleared with the fields it guards. The hash means "the LLM's
    # reading of this input is already merged into the fields above" — and we
    # just erased those fields. Leaving it would make re-uploading the SAME CV
    # (a real thing users do after a bad parse) skip the LLM pass and keep the
    # wiped, deterministic-only profile forever. Only the CV key: LinkedIn,
    # GitHub and about_me data deliberately survives a CV swap, so their
    # hashes must survive too.
    cv.llm_input_hashes.pop("cv", None)


def _merge_cv_llm_into(cv: CVData, llm_cv: CVData) -> None:
    """Merge the CV-OWNED fields of an LLM result into the live ``cv``.

    Unions the list fields and fills empty scalars. Deliberately does NOT touch
    ``linkedin_*`` / ``github_*`` / ``about_me_inferred_skills`` — those belong
    to the other passes and must survive a CV re-parse.
    """
    _merge_str_list(cv.skills, llm_cv.skills)
    _merge_str_list(cv.job_titles, llm_cv.job_titles)
    _merge_str_list(cv.companies, llm_cv.companies)
    _merge_str_list(cv.education, llm_cv.education)
    _merge_str_list(cv.certifications, llm_cv.certifications)
    _merge_str_list(cv.achievements, llm_cv.achievements)
    _merge_str_list(cv.cv_industries, llm_cv.cv_industries)
    _merge_str_list(cv.cv_languages, llm_cv.cv_languages)
    # Fill empty scalars only — never overwrite a value the user already has.
    if not cv.name and llm_cv.name:
        cv.name = llm_cv.name
    if not cv.headline and llm_cv.headline:
        cv.headline = llm_cv.headline
    if not cv.location and llm_cv.location:
        cv.location = llm_cv.location
    if not cv.summary and llm_cv.summary:
        cv.summary = llm_cv.summary
    if not cv.experience_text and llm_cv.experience_text:
        cv.experience_text = llm_cv.experience_text
    # ADAPTER-PARITY FIX (2026-08-07). Every OTHER CV-owned scalar in this
    # function was merged; `career_domain` was not, so even once the prompt
    # asks for it (cv_parser._CV_PROMPT) and the schema adapter surfaces it
    # (schemas.cv_schema_to_cvdata), the value stopped here — one layer above
    # the fix, same shape as the `cv_positions` miss just below. Fill-if-
    # empty like every scalar above: a re-run that classifies differently
    # must not clobber a domain the user's profile already carries.
    if not cv.career_domain and llm_cv.career_domain:
        cv.career_domain = llm_cv.career_domain
    # ESCO skill-normalisation map (Step-1.5 S1.5-D). `reset_cv_owned_fields`
    # has always cleared `cv_skills_esco` on a CV swap — treating it as CV-
    # owned — but nothing here ever copied it back in, so fixing the adapter
    # (schemas.cv_schema_to_cvdata) alone still left the live profile at
    # cv_skills_esco={} after a real re-extraction. Union rather than fill-
    # once: a later pass can resolve a skill the first pass didn't (ESCO
    # turned on, or a borderline cosine match), and dropping earlier
    # resolved entries would silently regress them.
    cv.cv_skills_esco.update(llm_cv.cv_skills_esco)
    # Structured, dated experience (Pillar-1 audit 2026-08-07). Every OTHER
    # CV-owned field was merged here; cv_positions was not, so a re-extraction
    # produced dated positions and then threw them away one layer above the
    # adapter that had just been fixed to emit them. Measured on a live CV:
    # "CV LLM pass: positions=3" followed by "merged: cv_positions 0".
    #
    # Replace rather than union: this is ONE ordered record of the same career
    # read from the same CV, so a union would duplicate every role on each
    # re-extraction. A non-empty result wins; an empty one never clobbers what
    # is already there (same guard as the GitHub/LinkedIn raw fields).
    if llm_cv.cv_positions:
        cv.cv_positions = list(llm_cv.cv_positions)


async def run_two_pass_extraction(profile: UserProfile) -> UserProfile:
    """Run deterministic + LLM enhance passes over all four inputs, in place.

    Re-runs entirely from data already stored on the profile — never re-reads a
    file or re-hits the GitHub API. Returns the same ``profile`` object for
    convenience. Never raises: a failing LLM pass is logged and skipped.
    """
    cv = profile.cv_data
    prefs = profile.preferences

    # Every input below follows the SAME shape (the user's diagram):
    #     raw ──┬─ deterministic_X(raw) ─▶ det-output ──┐
    #           └─ await llm_X(raw) ──────▶ llm-output ──┴─▶ merge into the
    #                                                        ONE shared CVData
    # The two passes are INDEPENDENT — both read the same raw input, neither
    # feeds the other — so whatever one pass misses the other can still catch.
    # Deterministic = STRUCTURE only (CLAUDE.md rule #28); LLM = meaning.

    # ── N2 — run the FOUR LLM passes concurrently ──────────────────────
    # The four inputs (CV / LinkedIn / GitHub / about_me) are independent: none
    # reads what another produced. They used to be four sequential `await`s, so
    # the caller waited for the SUM of four network round-trips. This route is
    # awaited inline by the profile-upload endpoints, so that sum was wall-clock
    # time the user spent watching a spinner.
    #
    # Only the CALLS are parallel. The merges below still run one at a time, in
    # exactly the original order, because they all mutate the same CVData and a
    # later merge can depend on what an earlier one wrote. Same inputs, same
    # merge sequence, same output — just without the queuing.
    #
    # return_exceptions=True keeps the existing contract that one failing LLM
    # pass never sinks the others: each result is unpacked below with the same
    # "log it and keep the deterministic result" handling it had before.
    async def _safe(coro: Any, label: str) -> Any:
        try:
            return await coro
        except Exception as e:  # noqa: BLE001 — deterministic result still stands
            logger.warning("two_pass: %s LLM pass skipped: %s", label, e)
            return None

    # ── Skip any pass whose input we have already paid to read ─────────
    # Changing ONE preference re-runs this whole function, so without this an
    # untouched CV is sent to a paid model again on every edit. The hash says
    # the previous result is already merged into `cv`, so the call adds cost
    # and latency and returns what we already have.
    _inputs = {
        "cv": cv.raw_text,
        "linkedin": cv.linkedin_raw_text,
        # GitHub input is the repo briefs (which now carry README excerpts) PLUS
        # the self-authored bio + profile README. Any of the three changing must
        # re-trigger the pass, so all three fold into the one cache key.
        "github": {
            "repos": cv.github_repos_brief,
            "bio": cv.github_bio,
            "readme": cv.github_profile_readme,
        },
        "about_me": prefs.about_me,
    }
    _cached = {k: _already_read(cv, k, v) for k, v in _inputs.items()}
    if any(_cached.values()):
        logger.info(
            "two_pass: reusing unchanged input(s) %s - skipping those LLM calls",
            ", ".join(sorted(k for k, v in _cached.items() if v)),
        )

    # The GitHub pass now has input when EITHER the repo briefs OR the
    # self-authored bio / profile README are present.
    _has_github = bool(cv.github_repos_brief or cv.github_bio or cv.github_profile_readme)

    _llm_cv_res, _llm_li_res, _llm_gh_res, _llm_pr_res = await asyncio.gather(
        _safe(llm_cv_fields_from_text(cv.raw_text), "CV")
        if cv.raw_text and not _cached["cv"] else _none(),
        _safe(llm_linkedin_fields(cv.linkedin_raw_text), "LinkedIn")
        if cv.linkedin_raw_text and not _cached["linkedin"] else _none(),
        _safe(llm_infer_github_skills(
            cv.github_repos_brief, bio=cv.github_bio,
            profile_readme=cv.github_profile_readme), "GitHub")
        if _has_github and not _cached["github"] else _none(),
        _safe(llm_infer_from_about_me(prefs.about_me), "about_me")
        if prefs.about_me and not _cached["about_me"] else _none(),
    )

    # RETRY A SOFT-EMPTY PASS ONCE, SEQUENTIALLY.
    #
    # The four passes above run concurrently in one gather. On free-tier keys
    # that means up to four simultaneous LLM calls, and the providers
    # rate-limit: one call gets a valid but EMPTY answer while the same input
    # extracts fine when it runs alone (measured on Rohith — 0 positions via the
    # concurrent API path, 7 in isolation). So any pass that RAN but produced no
    # usable data is retried here one at a time, with no concurrent contention.
    # Bounded: at most one extra call per empty input, and only when the input
    # was non-empty and not cached.
    _retryable = [
        ("cv", cv.raw_text, lambda: llm_cv_fields_from_text(cv.raw_text)),
        ("linkedin", cv.linkedin_raw_text, lambda: llm_linkedin_fields(cv.linkedin_raw_text)),
        ("github", _has_github, lambda: llm_infer_github_skills(
            cv.github_repos_brief, bio=cv.github_bio,
            profile_readme=cv.github_profile_readme)),
        ("about_me", prefs.about_me, lambda: llm_infer_from_about_me(prefs.about_me)),
    ]
    _results = {"cv": _llm_cv_res, "linkedin": _llm_li_res,
                "github": _llm_gh_res, "about_me": _llm_pr_res}
    for _k, _raw, _call in _retryable:
        if _raw and not _cached[_k] and not _pass_produced_data(_k, _results[_k]):
            retried = await _safe(_call(), f"{_k} (retry)")
            if _pass_produced_data(_k, retried):
                logger.info("two_pass: %s pass was empty under load — sequential "
                            "retry recovered it", _k)
                _results[_k] = retried
    _llm_cv_res, _llm_li_res = _results["cv"], _results["linkedin"]
    _llm_gh_res, _llm_pr_res = _results["github"], _results["about_me"]

    # Record ONLY a pass that PRODUCED USABLE DATA, not merely one that did not
    # raise.
    #
    # THE BUG THIS FIXES (found by the journey simulation 2026-08-05). `_safe`
    # returns None only on an EXCEPTION. But a rate-limited free-tier provider
    # often returns a valid, EMPTY result — `{"positions": [], "skills": []}` —
    # without raising. The old check (`_res is not None`) recorded that empty
    # result's hash as "done", so the cache then skipped re-extraction FOREVER,
    # freezing a profile with zero positions and zero LinkedIn skills. Measured
    # on Rohith: the LLM extracts 7 positions in isolation, but his stored
    # profile had 0, and every re-upload logged "reusing unchanged linkedin —
    # skipping" and kept the empty result. A soft failure is exactly the case
    # you MOST want to retry, and it was the one case being cached.
    #
    # So the hash is recorded only when the pass yielded something worth keeping.
    # An input that genuinely contains nothing will re-run cheaply next time —
    # far better than freezing a broken extraction.
    for _key, _res in (
        ("cv", _llm_cv_res), ("linkedin", _llm_li_res),
        ("github", _llm_gh_res), ("about_me", _llm_pr_res),
    ):
        if _res is not None and _pass_produced_data(_key, _res):
            cv.llm_input_hashes[_key] = _input_hash(_inputs[_key])

    # ── ① CV ── raw = cv.raw_text ──────────────────────────────────────
    if cv.raw_text:
        det_cv = deterministic_cv_fields(cv.raw_text)           # det-CV-output
        _merge_str_list(cv.skills, det_cv.get("skills", []))
        if not cv.summary and det_cv.get("summary"):
            cv.summary = det_cv["summary"]
        if _llm_cv_res is not None:                          # llm-CV-output
            _merge_cv_llm_into(cv, _llm_cv_res)                  # → MERGED CV

    # ── ② LinkedIn ── raw = cv.linkedin_raw_text ───────────────────────
    if cv.linkedin_raw_text:
        det_li = deterministic_linkedin_fields(cv.linkedin_raw_text)  # det-LI-output
        llm_li: dict[str, Any] = _llm_li_res or {}                   # llm-LI-output
        merged_li = merge_linkedin_fields(det_li, llm_li)            # → MERGED LI
        # `_llm_li_res is None` means the cost cache skipped the LinkedIn LLM
        # pass because the raw text was unchanged. The five LLM-only sections
        # must then be left alone — overwriting them with the empty lists a
        # deterministic-only merge produces wiped real user data on every
        # subsequent save (measured 2026-08-08).
        enrich_cv_from_linkedin(cv, merged_li, llm_ran=_llm_li_res is not None)

    # ── ③ GitHub ── raw = cv.github_repos_brief ────────────────────────
    if cv.github_repos_brief:
        det_gh = deterministic_github_fields(cv.github_repos_brief)  # det-GH-output
        _merge_str_list(cv.github_skills_inferred, det_gh)
        if _llm_gh_res is not None:                                        # llm-GH-output
            _merge_str_list(cv.github_llm_skills, _llm_gh_res)             # → MERGED GH

    # ── ④ Preferences ── raw = prefs.about_me ──────────────────────────
    if prefs.about_me:
        det_pr = deterministic_about_me_fields(prefs.about_me)   # det-PR-output
        _merge_str_list(cv.about_me_inferred_skills, det_pr)
        if _llm_pr_res is not None:                                # llm-PR-output
            _merge_str_list(cv.about_me_inferred_skills, _llm_pr_res)  # → MERGED PR

    # Collapse line-wrap fragments + cross-source duplicates in the free-text
    # lists so the profile shows each certification / qualification ONCE (a CV +
    # LinkedIn both list the same cert in slightly different words, and PDF wraps
    # split one cert across two lines — both inflated the counts and read as junk).
    # Certs: containment (drop line-wrap fragments) + fuzzy (collapse spelling /
    # punctuation variants like Visualisation/Visualization). Education: containment
    # ONLY — fuzzy would merge DIFFERENT degrees at the same institution.
    cv.certifications = dedup_fuzzy(dedup_by_containment(cv.certifications))
    cv.education = dedup_by_containment(cv.education)

    # Clean the LinkedIn skill list the same way the CV list is cleaned.
    #
    # It never was, and a real profile showed the cost: "RAG" AND
    # "Retrieval-Augmented Generation", "LoRA" AND "Low-Rank Adaptation", plus a
    # stray "LLM'S". One capability counted two or three times inflates the skill
    # count without adding capability — which makes a profile look broad and
    # match everything weakly, the opposite of what the user wants. Both helpers
    # are punctuation-structural, no vocabulary, so this stays rule-#28 safe and
    # works for any profession.
    if cv.linkedin_skills:
        from src.services.profile.cv_parser import (  # noqa: PLC0415
            _det_collapse_acronyms,
            _strip_possessive,
        )

        cleaned = [_strip_possessive(s) for s in cv.linkedin_skills]
        cv.linkedin_skills = _det_collapse_acronyms([s for s in cleaned if s])

    # LLM curation — the reasoning layer. The deterministic + fuzzy passes can't
    # safely merge entries that mean the same thing but are worded very
    # differently ("Master of Science…" vs "Master's degree…"; "AI/ML Engineer
    # Intern" vs "AI / ML intern"). The LLM reasons about meaning and returns ONE
    # clean title per real qualification/role. Each no-ops on <2 items and keeps
    # the original on any failure, so this never loses data.
    from src.services.profile.llm_curate import (  # noqa: PLC0415
        llm_merge_duplicates,
        llm_suggest_adjacent_skills,
    )

    cv.education = await llm_merge_duplicates(cv.education, "education")
    cv.job_titles = await llm_merge_duplicates(cv.job_titles, "roles")
    cv.certifications = await llm_merge_duplicates(cv.certifications, "certifications")

    # Adjacent-skill SUGGESTIONS (opt-in, never auto-counted) from the full set of
    # skills the user actually has across all sources.
    _all_skills = list(
        dict.fromkeys(
            (cv.skills or [])
            + (cv.linkedin_skills or [])
            + (cv.github_llm_skills or [])
            + list((cv.github_languages or {}).keys())
            + (prefs.additional_skills or [])
        )
    )
    cv.suggested_skills = await llm_suggest_adjacent_skills(_all_skills)

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
    # product design rule #29. Runs LAST, after both merges above, so it
    # reads the fully-populated cv_positions/linkedin_positions and writes
    # onto whichever preferences object is now live (the merged one above,
    # or the original if that merge did not run). Never raises — a failed
    # inference just leaves the field empty and the dimension stays neutral.
    try:
        # Dated job TITLES are the stronger, structural signal, so they win.
        # But they answer nothing when no title carries a seniority word, and
        # the dimension then went dark for that user. The CV LLM read the whole
        # document and already stated a level — fall back to it rather than to
        # nothing.
        from_titles = infer_experience_level(cv.cv_positions, cv.linkedin_positions)
        profile.preferences.experience_level_inferred = (
            from_titles or (cv.cv_experience_level or "").strip()
        )
    except Exception as exc:  # noqa: BLE001 - inference must never cost a save
        logger.warning("seniority inference failed (non-fatal): %s", exc)

    # ── THE UNIVERSAL GATE ────────────────────────────────────────────────
    # Grade what we just produced against the document it came from. This is
    # deliberately the LAST thing the extractor does, after BOTH passes and the
    # merge, because it judges the finished profile — not any one parser.
    #
    # Why it exists: extraction was being fixed one CV at a time, and every fix
    # was a patch for a layout we happened to have seen. Layouts are unbounded;
    # that road has no end. A parser cannot be made perfect for every document,
    # so the product's job is to NOTICE when it did badly rather than silently
    # hand someone a profile that will never match a job.
    #
    # Never raises and never blocks a save: a scoring failure must not cost a
    # user their upload. The score is advisory — it is carried on the profile so
    # the API can show it, and logged loudly so a low score is visible in prod.
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
