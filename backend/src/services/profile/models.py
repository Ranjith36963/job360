"""Core dataclasses for user profile and dynamic search configuration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CVData:
    raw_text: str = ""
    # Scoring-semantic fields — these flow into SearchConfig and influence matching
    skills: list[str] = field(default_factory=list)
    job_titles: list[str] = field(default_factory=list)
    companies: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    summary: str = ""
    experience_text: str = ""
    # Display-only fields — used by CV viewer for highlighting, NOT for scoring
    name: str = ""
    headline: str = ""
    location: str = ""
    achievements: list[str] = field(default_factory=list)
    # The universal extraction gate's verdict on THIS profile (coverage,
    # precision, completeness, input health, overall, problems). Written by
    # run_two_pass_extraction after both passes merge. Advisory: it never
    # blocks a save, it tells the product when a profile is too thin to match
    # anything so it can escalate instead of failing silently.
    extraction_score: dict[str, Any] = field(default_factory=dict)
    # Which inputs the paid LLM passes have ALREADY read, as {input: sha256}.
    # Keys: "cv", "linkedin", "github", "about_me".
    #
    # WHY. Every profile change re-runs the whole extraction, and each run makes
    # a paid LLM call per input. Change one preference and we re-read an
    # unchanged CV, again, at full price. The only guard was a blunt
    # PROFILE_EXTRACT_MAX_PER_HOUR rate limit, which caps the bleeding rather
    # than stopping it — and punishes the user with a 429 for our waste.
    #
    # We store the input's hash, NOT the LLM's output, because the output is
    # already here: every pass merges into this same CVData. So an unchanged
    # hash means "that call's result is already in these fields" and the call
    # can simply be skipped. A hash is only recorded after a pass SUCCEEDS —
    # otherwise one provider outage would permanently skip that input.
    llm_input_hashes: dict[str, str] = field(default_factory=dict)
    # LinkedIn-sourced data
    linkedin_positions: list[dict[str, Any]] = field(default_factory=list)
    linkedin_skills: list[str] = field(default_factory=list)
    linkedin_industry: str = ""
    # The LinkedIn "About" section, in the person's own words.
    #
    # It was already parsed, and then thrown away in the common case: the merge
    # only wrote it into ``summary`` when the CV had none
    # (``enrich_cv_from_linkedin``'s fill-if-empty rule), so anyone whose CV has
    # a professional summary — most people — lost their LinkedIn About entirely.
    # Meanwhile ``embeddings.py`` had been reading a ``linkedin_summary`` field
    # for months. It never existed, so that read returned "" on every profile,
    # forever, and getattr-with-a-default cannot fail loudly.
    #
    # Now it has a shelf of its own. The two texts are different documents, not
    # duplicates: a CV summary is written for recruiters and is heavily edited;
    # a LinkedIn About is looser, first-person, and states motivation and
    # direction that a CV omits — exactly the prose the LLM judge reads best.
    linkedin_summary: str = ""
    # The LinkedIn HEADLINE — the tagline under the person's name.
    #
    # Empty on every two-column export until 2026-08-11, and for a structural
    # reason: LinkedIn's "Save to PDF" flattens the left rail (Contact, Top
    # Skills, Certifications) FIRST, so the name and headline land in the middle
    # of the text rather than at the top. ``_split_sections`` looks for a header
    # block before the first heading, finds nothing, and returns 0 characters.
    # The deterministic pass is structure-only by design, and this layout
    # defeats structure — so the headline is read by the LLM instead (rule #28
    # safe: prose comprehension, not a keyword table).
    #
    # It is worth its own shelf rather than filling ``headline``, which the CV
    # owns. On a real profile the two say different things: the CV's was
    # "AI/ML Engineer | Generative AI Specialist" while LinkedIn's names the
    # stack AND states "Open to AI/ML Engineer Roles UK" — an availability,
    # role and location claim that appears in no other input.
    linkedin_headline: str = ""
    # Two-pass extraction — the raw text pdfplumber pulled from the LinkedIn
    # "Save to PDF" export. Stored so the LLM pass can re-run on any profile
    # change WITHOUT the user re-uploading the file (the temp file is deleted
    # after the first parse). Empty when no LinkedIn PDF was ever uploaded.
    linkedin_raw_text: str = ""
    # Batch 1.5 — expanded LinkedIn sections (Languages, Projects,
    # Volunteer Experience, Courses). All are LinkedIn-sourced display
    # fields: they inform the CV viewer and feed relevance keywords
    # but do NOT contribute to ``skills`` — they're separate signals
    # so downstream can opt-in rather than polluting primary tiering.
    linkedin_languages: list[dict[str, Any]] = field(default_factory=list)
    linkedin_projects: list[dict[str, Any]] = field(default_factory=list)
    linkedin_volunteer: list[dict[str, Any]] = field(default_factory=list)
    linkedin_courses: list[dict[str, Any]] = field(default_factory=list)
    # ── LinkedIn sections the parser SPLIT but nobody read (2026-08-09) ──
    #
    # ``_SECTION_HEADINGS`` recognises 20 headings; only 11 had an extractor and
    # a shelf. The other seven were split out purely so they acted as
    # boundaries, then discarded — the same "fetched and thrown away" shape as
    # GitHub's identity block.
    #
    # Each of these is real matching evidence on the profiles that carry it:
    #   honors        — awards, the achievement claim a CV usually buries
    #   publications  — research output; decisive for research/ML roles
    #   patents       — the strongest single technical credibility signal
    #   organizations — professional bodies (BCS, IEEE) => domain + seniority
    #   test_scores   — IELTS/TOEFL/GRE; language proficiency, and UK-visa
    #                   relevant, which no other input states
    #   recommendations — other people's PROSE about this person's work. Third-
    #                   party evidence, the LinkedIn analogue of a GitHub README
    #   interests     — companies and groups followed => industry preference
    #
    # JSON blob, so no migration: ``storage._filter_fields`` drops unknown keys
    # and old rows load with empty lists.
    linkedin_honors: list[dict[str, Any]] = field(default_factory=list)
    linkedin_publications: list[dict[str, Any]] = field(default_factory=list)
    linkedin_patents: list[dict[str, Any]] = field(default_factory=list)
    linkedin_organizations: list[dict[str, Any]] = field(default_factory=list)
    linkedin_test_scores: list[dict[str, Any]] = field(default_factory=list)
    linkedin_recommendations: list[dict[str, Any]] = field(default_factory=list)
    linkedin_interests: list[str] = field(default_factory=list)
    # STRUCTURED contact block. The LinkedIn PDF's "Contact" section carries
    # email, phone, the profile URL and personal websites — parsed today only to
    # confirm the file IS a LinkedIn export, then dropped. ``location`` matters
    # most: it is a place claim the UK eligibility gate can reason about (#30),
    # and LinkedIn states it even when a CV does not.
    linkedin_contact: dict[str, Any] = field(default_factory=dict)
    # GitHub-sourced data
    github_languages: dict[str, int] = field(default_factory=dict)
    github_topics: list[str] = field(default_factory=list)
    github_skills_inferred: list[str] = field(default_factory=list)
    # Batch 1.2 — skills inferred from GitHub dependency-file parsing
    # (requirements.txt / package.json / Cargo.toml / etc.). Kept
    # separate from github_skills_inferred so downstream can audit
    # where a skill came from (language signal vs declared dependency).
    github_frameworks: list[str] = field(default_factory=list)
    # Two-pass extraction — a compact list of {name, description, topics}
    # for the user's public repos. Stored so the GitHub LLM pass can re-run
    # offline (no re-fetch) on a later profile change.
    github_repos_brief: list[dict[str, Any]] = field(default_factory=list)
    # Two-pass extraction — skills the LLM inferred by reading repo prose
    # (names/descriptions/topics) that the hard-coded language/topic lookup
    # tables can't recognise (e.g. "LangChain", "RAG"). Separate field so
    # skill-tiering can weight this signal independently.
    github_llm_skills: list[str] = field(default_factory=list)
    # "Read 100% of GitHub" (2026-08-05) — the two richest SELF-AUTHORED prose
    # signals on a profile. Stored so the GitHub LLM pass can re-read them
    # offline on a later profile change (mirrors github_repos_brief). Both are
    # prose fed only to the LLM pass (rule #28 safe — never a hardcoded map),
    # and persist automatically via the asdict() blob, no migration.
    #   github_bio: the /users/{u} identity block (bio + name/company/blog/
    #               location/hireable) — the developer describing themselves.
    #   github_profile_readme: the {u}/{u} special-repo README — the portfolio
    #               page GitHub renders at the top of a profile.
    github_bio: str = ""
    github_profile_readme: str = ""
    # STRUCTURED GitHub identity (2026-08-09): name, company, location, blog,
    # twitter, hireable, account_created_at, followers, public_repos.
    #
    # All of these were ALREADY fetched in the same /users/{u} request and then
    # flattened into ``github_bio`` as one sentence — fine for a human or an LLM
    # prompt, useless to anything that must compare, filter or score. You cannot
    # match on a sentence.
    #
    # Two are matching-grade: ``hireable`` is GitHub's own "open to work" flag,
    # and ``location`` is a place claim the UK gate reasons about. ``hireable``
    # is TRI-STATE (True/False/None) for the same reason visa status is —
    # "never said" is not "not looking" (rule #31).
    #
    # Kept as a dict so new identity fields need no migration: profiles store as
    # a JSON blob and ``storage._filter_fields`` drops unknown keys, so old rows
    # load with {}.
    github_identity: dict[str, Any] = field(default_factory=dict)
    # CV experience, STRUCTURED (2026-08-06). The CV LLM prompt has always
    # asked for {company, title, dates, location, bullets} per role, but the
    # adapter flattened it into unpaired ``job_titles`` + ``companies`` lists
    # and threw ``dates``/``location`` away entirely — so nothing downstream
    # could tell WHICH title was held at WHICH company, for HOW LONG, or how
    # RECENTLY. That is why no skill-recency signal exists anywhere in the
    # engine. Each entry: {company, title, dates, location, bullets: [str]}.
    # Mirrors ``linkedin_positions`` (the only previously-structured source);
    # JSON blob, no migration (storage._filter_fields drops unknown keys, so
    # old rows load with an empty list).
    cv_positions: list[dict[str, Any]] = field(default_factory=list)
    # ── Upload receipts (2026-08-08) ────────────────────────────────────────
    # WHAT the user gave us and WHEN. Found on a live smoke test: after
    # uploading, the only feedback was a small tick the owner said he "didn't
    # catch", and nothing anywhere named the file — so a user could not tell
    # WHICH CV was on file, whether a re-upload had actually replaced it, or
    # when any of it happened. GitHub had no confirmation at all.
    #
    # Storing the original filename is consistent with what is already kept:
    # the full CV text (far more sensitive) lives in ``raw_text``. Keeping the
    # receipt inside this same blob means the existing account-deletion and
    # version-snapshot paths carry it automatically — no separate lifecycle.
    # JSON blob, so no migration (storage._filter_fields drops unknown keys;
    # old rows load with empty strings and the UI falls back to "uploaded").
    # Timestamps are ISO-8601 UTC strings, matching the rest of storage.
    cv_filename: str = ""
    cv_uploaded_at: str = ""
    linkedin_filename: str = ""
    linkedin_uploaded_at: str = ""
    # GitHub has no file — the handle IS the receipt, alongside when it was
    # connected and how many repos were read (len(github_repos_brief)).
    github_connected_at: str = ""
    # Batch 1.1 — archetype classification (CareerDomain enum value).
    # Optional; None means "LLM did not classify".
    #
    # This comment used to claim it was "consumed by archetype-aware scoring".
    # That was false for months: no scorer, judge or vector read it, and the
    # claim sent readers hunting for code that did not exist. As of 2026-08-09 it
    # genuinely is consumed — ``embeddings.build_profile_embedding_text`` and
    # ``llm_matcher.profile_to_matcher_text``. There is still no archetype
    # WEIGHTING; if one is added, say so here then, not before.
    career_domain: Optional[str] = None
    # Batch 1.x.1 (review fix #1) — CV-extracted fields that the
    # CVSchema already parses but the original adapter silently
    # dropped. Separate from ``linkedin_*`` equivalents so the JSON
    # Resume export distinguishes CV-stated languages/industries from
    # LinkedIn-stated ones.
    # RENAMED from ``industries`` (2026-08-09). ``UserPreferences`` declares an
    # ``industries`` too, and the two mean opposite things: this one is a FACT
    # extracted from the CV (where the person has worked), the other is a
    # PREFERENCE the user typed (where they want to work). Sharing one name meant
    # every name-keyed tool in the project silently merged them, and rule #29
    # turns on exactly that distinction — a fact may be inferred, a preference
    # never may. No migration needed: profiles store as a JSON blob and
    # ``storage._filter_fields`` drops unknown keys, so old rows load with [].
    cv_industries: list[str] = field(default_factory=list)
    cv_languages: list[str] = field(default_factory=list)
    # The CV's own statement of seniority, as the LLM read it.
    #
    # ``cv_parser``'s prompt has always asked for this ("One of: intern, junior,
    # mid, senior, lead, principal, director — infer from experience duration
    # and roles") and ``CVSchema`` has always declared it. The adapter never
    # passed it on, so it was paid for on every CV parse and dropped.
    #
    # That left ``experience_level_inferred`` depending solely on
    # ``seniority.infer_experience_level``, which reads dated job TITLES — so a
    # CV whose titles carry no seniority word produced no level at all and the
    # seniority dimension sat at its neutral fallback.
    #
    # A FACT about the document, like ``skills`` — NOT a preference. It fills
    # the scoring seam only where the titles found nothing, and it must never
    # reach a gate that deletes jobs (rule #29; see tests/test_prefilter_wiring).
    cv_experience_level: str = ""
    # Work authorisation, as the CV states it — the one UK-specific fact a CV
    # carries that nothing here read. Rule #30 refuses jobs the user cannot
    # take because of WHERE they are and rule #31 treats sponsorship as a
    # spotlight, but both act on the JOB. The user side had only the
    # ``needs_visa`` boolean, which almost nobody ticks.
    #
    # TRI-STATE like the job-side signal, for the same reason: "" means the CV
    # never said it, which is the opposite fact from "needs sponsorship".
    # Free text, because a UK CV states this a dozen ways and forcing an enum
    # would make the extractor guess. Never inferred from nationality or
    # place of study — that would be discrimination dressed up as a feature.
    cv_right_to_work: str = ""
    # Projects stated on the CV: {name, description, technologies, dates}.
    #
    # A Projects heading was already recognised — as a section BOUNDARY, to
    # stop a skills block (cv_parser lines 268/322/373) — and then thrown
    # away. Exactly the 'split it out, then drop it' shape as the seven
    # LinkedIn sections, one input over.
    #
    # For a junior or career-changing candidate this is often the strongest
    # evidence they have: the CV lists two short roles but five real builds.
    # GitHub already contributes ``github_repos_brief`` for people who push
    # code publicly; this is the same signal for the people who do not.
    cv_projects: list[dict[str, Any]] = field(default_factory=list)
    # Step-1.5 S1.5-D — ESCO normalisation map populated by
    # ``cv_parser._llm_result_to_cvdata`` when ``SEMANTIC_ENABLED=true`` and
    # the ESCO index is on disk. Maps the *canonical* skill label (which
    # also replaces the entry in ``skills``) → its ESCO concept URI. Empty
    # when ESCO is off / unavailable — gracefully matches the pre-ESCO
    # behaviour. ProfileResponse expansion surfaces this as
    # ``skill_provenance``.
    cv_skills_esco: dict[str, str] = field(default_factory=dict)
    # Two-pass extraction — skills the LLM mined from the user's free-text
    # ``preferences.about_me``. Stored on CVData (the skill container) so
    # skill-tiering can weight this "user's own words" signal. Empty when
    # about_me is blank or the LLM pass is unavailable.
    about_me_inferred_skills: list[str] = field(default_factory=list)
    # LLM-suggested ADJACENT skills (neighbours of what the user already has, e.g.
    # PyTorch → TensorFlow/Keras). These are SUGGESTIONS the user opts into — they
    # are NEVER counted in tiering/scoring, so matching only uses real skills.
    suggested_skills: list[str] = field(default_factory=list)

    @classmethod
    def from_json_resume(cls, data: dict[str, Any]) -> CVData:
        """Batch 1.8b — inverse of ``to_json_resume``. Build a CVData
        from a JSON Resume–shaped dict.

        Closes the plan §4.8 interop goal without a breaking rename:
        callers that want to import a third-party JSON Resume export
        (from jsonresume.org tooling, for example) get a canonical
        loader that maps the standard root keys back onto the
        existing CVData field layout. Unknown root keys are ignored.
        Missing keys default to empty collections.
        """
        if not isinstance(data, dict):
            return cls()
        basics = data.get("basics") or {}
        location_obj = basics.get("location") if isinstance(basics, dict) else None
        loc = location_obj.get("address", "") if isinstance(location_obj, dict) else ""

        linkedin_positions = [
            {
                "title": w.get("position", "") or "",
                "company": w.get("name", "") or "",
                "start": w.get("startDate", "") or "",
                "end": w.get("endDate", "") or "",
                "description": w.get("summary", "") or "",
            }
            for w in (data.get("work") or [])
            if isinstance(w, dict)
        ]

        edu_lines: list[str] = []
        for e in data.get("education") or []:
            if not isinstance(e, dict):
                continue
            inst = e.get("institution", "") or ""
            deg = e.get("studyType", "") or e.get("area", "") or ""
            entry = deg if deg else inst
            if deg and inst:
                entry = f"{deg} - {inst}"
            if entry:
                edu_lines.append(entry)

        skills: list[str] = []
        for s in data.get("skills") or []:
            if isinstance(s, dict):
                nm = s.get("name", "")
                if nm:
                    skills.append(nm)
            elif isinstance(s, str):
                skills.append(s)

        linkedin_languages = [
            {
                "language": lang.get("language", "") or "",
                "proficiency": lang.get("fluency", "") or "",
            }
            for lang in (data.get("languages") or [])
            if isinstance(lang, dict) and lang.get("language")
        ]

        linkedin_projects = [
            {
                "title": p.get("name", "") or "",
                "description": p.get("description", "") or "",
                "start": p.get("startDate", "") or "",
                "end": p.get("endDate", "") or "",
                "url": p.get("url", "") or "",
            }
            for p in (data.get("projects") or [])
            if isinstance(p, dict) and p.get("name")
        ]

        linkedin_volunteer = [
            {
                "role": v.get("position", "") or "",
                "organisation": v.get("organization", "") or "",
                "cause": v.get("cause", "") or "",
                "start": v.get("startDate", "") or "",
                "end": v.get("endDate", "") or "",
                "description": v.get("summary", "") or "",
            }
            for v in (data.get("volunteer") or [])
            if isinstance(v, dict)
        ]

        certs = [
            c.get("name", "") if isinstance(c, dict) else str(c)
            for c in (data.get("certificates") or [])
            if (isinstance(c, dict) and c.get("name")) or isinstance(c, str)
        ]

        meta = data.get("meta") or {}
        if not isinstance(meta, dict):
            meta = {}

        return cls(
            name=basics.get("name", "") if isinstance(basics, dict) else "",
            headline=basics.get("label", "") if isinstance(basics, dict) else "",
            summary=basics.get("summary", "") if isinstance(basics, dict) else "",
            location=loc,
            linkedin_positions=linkedin_positions,
            education=edu_lines,
            skills=skills,
            certifications=certs,
            linkedin_languages=linkedin_languages,
            linkedin_projects=linkedin_projects,
            linkedin_volunteer=linkedin_volunteer,
            linkedin_industry=meta.get("industry", "") if isinstance(meta, dict) else "",
            github_frameworks=list(meta.get("github_frameworks") or []),
            github_topics=list(meta.get("github_topics") or []),
            github_languages=dict(meta.get("github_languages") or {}),
            career_domain=meta.get("career_domain") if isinstance(meta, dict) else None,
        )

    def to_json_resume(self) -> dict[str, Any]:
        """Batch 1.8 — return a JSON Resume canonical-schema dict.

        Additive export (read-only). Does NOT rename existing fields,
        so callers that depend on the raw dataclass layout keep
        working. Schema follows https://jsonresume.org/schema/: root
        keys ``basics`` / ``work`` / ``education`` / ``skills`` /
        ``languages`` / ``projects`` / ``volunteer`` / ``certificates``.
        Custom provenance (``career_domain``, ``github_frameworks``)
        rides under the ``meta`` key — reserved in the schema for
        extensions.
        """
        return {
            "basics": {
                "name": self.name,
                "label": self.headline,
                "summary": self.summary,
                "location": {"address": self.location} if self.location else {},
            },
            "work": [
                {
                    "name": pos.get("company", ""),
                    "position": pos.get("title", ""),
                    "startDate": pos.get("start", ""),
                    "endDate": pos.get("end", ""),
                    "summary": pos.get("description", ""),
                }
                for pos in self.linkedin_positions
            ],
            "education": [{"institution": line} for line in self.education],
            "skills": [{"name": s, "level": "", "keywords": []} for s in self.skills],
            "languages": [
                {"language": lang.get("language", ""), "fluency": lang.get("proficiency", "")}
                for lang in self.linkedin_languages
            ],
            "projects": [
                {
                    "name": p.get("title", ""),
                    "description": p.get("description", ""),
                    "startDate": p.get("start", ""),
                    "endDate": p.get("end", ""),
                    "url": p.get("url", ""),
                }
                for p in self.linkedin_projects
            ],
            "volunteer": [
                {
                    "organization": v.get("organisation", ""),
                    "position": v.get("role", ""),
                    "startDate": v.get("start", ""),
                    "endDate": v.get("end", ""),
                    "summary": v.get("description", ""),
                }
                for v in self.linkedin_volunteer
            ],
            "certificates": [{"name": c} for c in self.certifications],
            "meta": {
                "career_domain": self.career_domain,
                "github_languages": self.github_languages,
                "github_topics": self.github_topics,
                "github_frameworks": self.github_frameworks,
                "industry": self.linkedin_industry,
            },
        }

    @property
    def highlights(self) -> list[str]:
        """All terms to highlight in the CV viewer (scoring-safe aggregation)."""
        result = []
        if self.name:
            result.append(self.name)
        if self.headline:
            result.append(self.headline)
        if self.location:
            result.append(self.location)
        result.extend(self.skills)
        result.extend(self.job_titles)
        result.extend(self.companies)
        result.extend(self.achievements)
        return result


@dataclass
class UserPreferences:
    target_job_titles: list[str] = field(default_factory=list)
    additional_skills: list[str] = field(default_factory=list)
    excluded_skills: list[str] = field(default_factory=list)
    preferred_locations: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    salary_min: Optional[float] = None
    salary_max: Optional[float] = None
    work_arrangement: str = ""  # "remote", "hybrid", "onsite", or ""
    experience_level: str = ""
    # A FACT read off the CV/LinkedIn dated job titles, NOT a preference the
    # user typed — see `services/profile/seniority.py`'s module docstring for
    # why that distinction lets this exist under product rule #29 (an empty
    # PREFERENCE means "don't care"; this is extraction, like skills). Kept
    # on a separate field so it can never be mistaken for something the user
    # stated. Populated by `services.profile.seniority.infer_experience_level`
    # during two-pass extraction; read ONLY as a fallback when
    # `experience_level` above is empty — see
    # `scoring_dimensions.resolve_experience_level`, the single seam every
    # scoring call site must go through instead of reading either field
    # directly.
    experience_level_inferred: str = ""
    negative_keywords: list[str] = field(default_factory=list)
    about_me: str = ""
    github_username: str = ""
    # Pillar 2 Batch 2.9 — multi-dimensional scoring inputs.
    # `needs_visa` gates the visa scorer — when False the dim returns 0
    # (no reward for something the user doesn't need).
    needs_visa: bool = False

    # Values the workplace scorer can actually match. A CLOSED set, because the
    # job side of the comparison (`JobEnrichment.workplace_type`) is an enum —
    # anything outside it can never match anything (rule #30's closed-set test).
    _WORKPLACE_VALUES = frozenset({"remote", "hybrid", "onsite"})

    @property
    def preferred_workplace(self) -> Optional[str]:
        """The enum form of ``work_arrangement``, DERIVED — not stored.

        This was a second stored field holding the same answer, bridged into
        place by the profile route. The user was answering one question ("do you
        go in?") and the system kept two boxes for it, which is exactly how they
        drifted: `cli.py`'s `setup-profile` sets `work_arrangement` and has never
        set `preferred_workplace`, so that entry point produced a divergent
        profile from the day it was written. A copy kept in step by discipline
        eventually is not.

        THE SENTINEL THIS FIXES. The form seeds itself at "any" and offers it as
        a real option, but "any" is not one of the three values the scorer
        knows, so `workplace_score` fell through every branch to 0 — while an
        EMPTY preference returns the neutral 3. Choosing "I don't mind" scored
        WORSE than saying nothing, on every job in the feed. That is rule #29
        inverted: a stated "don't care" became a penalty.

        The allowlist below is what makes that unrepresentable. Anything that is
        not a matchable value — "any", "", whitespace, a future sentinel nobody
        has invented yet — becomes None, which the scorer already treats as "no
        preference, score neutral". Empty stays empty; it never guesses.
        """
        value = (self.work_arrangement or "").strip().lower()
        return value if value in self._WORKPLACE_VALUES else None


@dataclass
class UserProfile:
    cv_data: CVData = field(default_factory=CVData)
    preferences: UserPreferences = field(default_factory=UserPreferences)

    @property
    def is_complete(self) -> bool:
        has_cv = bool(self.cv_data.raw_text)
        has_prefs = bool(self.preferences.target_job_titles or self.preferences.additional_skills)
        return has_cv or has_prefs


@dataclass
class SearchConfig:
    # EVIDENCE list — everything the profile knows about the roles this person
    # has held or wants. Never filtered, never capped. This is what the SCORER
    # matches a job title against; it is NOT what we send to a job board.
    job_titles: list[str] = field(default_factory=list)
    # QUERY list — the cleaned, ranked, capped subset of `job_titles` we are
    # willing to put in an HTTP request to Reed/Adzuna/LinkedIn/etc.
    #
    # WHY THE SPLIT (2026-08-13). `job_titles` was doing both jobs at once, so
    # raw CV strings leaked straight into query strings: real profiles produced
    # searches for "AI Solutions Engineer - R&D Department", "Software
    # Development Engineer in Test (SDET)" and the bare word "Intern" — no
    # posting on any board carries those as its title, so the requests came
    # back near-empty and the API budget was spent on nothing. Splitting the
    # field means query hygiene costs the scorer exactly zero: `job_titles` is
    # byte-identical to what it was before.
    #
    # Built by `keyword_generator._build_search_titles`. Empty on a
    # default/no-profile config — consumers fall back to `job_titles` via
    # `BaseJobSource.search_titles`.
    search_titles: list[str] = field(default_factory=list)
    primary_skills: list[str] = field(default_factory=list)
    secondary_skills: list[str] = field(default_factory=list)
    tertiary_skills: list[str] = field(default_factory=list)
    relevance_keywords: list[str] = field(default_factory=list)
    negative_title_keywords: list[str] = field(default_factory=list)
    locations: list[str] = field(default_factory=list)
    visa_keywords: list[str] = field(default_factory=list)
    core_domain_words: set[str] = field(default_factory=set)
    supporting_role_words: set[str] = field(default_factory=set)
    search_queries: list[str] = field(default_factory=list)

    @classmethod
    def from_defaults(cls) -> SearchConfig:
        """Return a minimal SearchConfig with no domain assumptions.

        When no user profile exists, we use empty skill lists rather than
        hardcoded AI/ML keywords. The user MUST upload a CV or set preferences
        for meaningful job matching.
        """
        from src.core.keywords import LOCATIONS, VISA_KEYWORDS

        return cls(
            job_titles=[],
            search_titles=[],
            primary_skills=[],
            secondary_skills=[],
            tertiary_skills=[],
            relevance_keywords=[],
            negative_title_keywords=[],
            locations=list(LOCATIONS),
            visa_keywords=list(VISA_KEYWORDS),
            core_domain_words=set(),
            supporting_role_words=set(),
            search_queries=[],
        )
