"""Profile extraction orchestrator — deterministic-only, since decision 28.

DECISION 28 (2026-09-21) deleted all six LLM passes that used to sit inside
this pipeline (CV / LinkedIn / GitHub / preferences model calls, plus the two
curation passes). ``two_pass.py`` is now ~370 lines of plain, offline,
deterministic code: it reads whatever raw text/briefs are stored on the
profile, runs structure-only extraction over them, dedups the free-text
lists, folds CV skills into preferences, infers seniority from dated
positions, and scores the result. Nothing here calls a model, so nothing here
needs mocking.

What this file tests:
  * the two free-text dedup helpers (``dedup_by_containment`` / ``dedup_fuzzy``)
  * ``merge_cv_and_preferences`` keeping the user's own extras separate from
    CV-extracted skills
  * each input's deterministic pass in isolation (CV / LinkedIn / GitHub /
    about_me)
  * the skill-tiering evidence collector picking up the surviving LEGACY
    shelves (``github_llm_skills`` / ``about_me_inferred_skills``) that old
    profiles still carry, even though nothing writes them any more
  * the orchestrator (``run_two_pass_extraction``) wiring all four inputs
    together deterministically
  * ``reset_cv_owned_fields`` — the CV-replacement bug fix, including the
    in-place-mutation identity contract
  * THE LOAD-BEARING INVARIANT OF DECISION 28: re-running the extractor must
    never clear a field the user's own agent wrote (``cv_positions``,
    ``linkedin_positions``, ``github_llm_skills``) — those fields are no
    longer this module's to write OR to take away.

Everything that tested LLM behaviour — the four model passes, the input-hash
cost cache, the concurrent-gather retry logic, ``_pass_produced_data`` /
``_cv_pass_is_partial``, the two curation passes, "the LLM pass fills X",
provider-failure handling — is gone along with the code it tested. The suite
still runs fully offline (rule #4); there was never anything to mock here to
begin with once the model calls left.
"""

from __future__ import annotations

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile

# ── Free-text dedup helpers — certifications / education ────────────


class TestCertEducationDedup:
    def test_dedup_by_containment_drops_fragments_and_exact_dupes(self):
        """TRUST: certifications showed the same cert three times — the full form
        plus two line-wrap fragments ('The Complete Python Bootcamp' + 'From Zero
        to Hero in Python'). Drop any entry that is a normalized substring of a
        longer one, and collapse exact dupes. Structural, no keyword list."""
        from src.services.profile.two_pass import dedup_by_containment

        items = [
            "The Complete Python Bootcamp from Zero to Hero in Python (Udemy, March 2024)",
            "The Complete Python Bootcamp",
            "From Zero to Hero in Python",
            "AWS Certified AI Practitioner",
            "AWS Certified AI Practitioner",  # exact dup
        ]
        out = dedup_by_containment(items)
        assert "The Complete Python Bootcamp from Zero to Hero in Python (Udemy, March 2024)" in out
        assert "The Complete Python Bootcamp" not in out
        assert "From Zero to Hero in Python" not in out
        assert out.count("AWS Certified AI Practitioner") == 1

    def test_dedup_fuzzy_collapses_spelling_variants(self):
        """GENERAL (all profiles): near-identical certs that differ only by
        spelling / punctuation / an extra date suffix collapse to one — e.g. the
        Accenture cert in British vs American spelling. Fuzzy token similarity,
        not a synonym list."""
        from src.services.profile.two_pass import dedup_fuzzy

        items = [
            "Accenture North America – Data Analytics and Visualisation Job Simulation (Forage, March 2024)",
            "Accenture North America - Data Analytics and Visualization Job Simulation",
        ]
        out = dedup_fuzzy(items, threshold=85)
        assert len(out) == 1
        # keeps the longer / more complete form
        assert "Forage" in out[0]

    def test_dedup_fuzzy_keeps_genuinely_different_entries(self):
        """GUARD (must hold for all profiles): distinct certs that merely share an
        issuer/word must NOT be merged — the threshold sits safely above them."""
        from src.services.profile.two_pass import dedup_fuzzy

        items = [
            "AWS Certified AI Practitioner",
            "AWS Certified Solutions Architect",
            "The Complete Python Bootcamp",
            "Python for Data Science Bootcamp",
        ]
        assert dedup_fuzzy(items, threshold=85) == items

    def test_dedup_by_containment_keeps_distinct_entries(self):
        """Distinct certs/degrees must all survive — dedup only removes fragments
        and exact dupes, never genuinely different entries."""
        from src.services.profile.two_pass import dedup_by_containment

        items = ["MSc Artificial Intelligence", "BEng Computer Science", "AWS Certified"]
        assert dedup_by_containment(items) == items


class TestMergeCvAndPreferences:
    def test_cv_skills_do_not_pollute_additional_skills(self):
        """BUG (empty tiers + stuffed preferences box): merge folded ALL cv skills
        into preferences.additional_skills, which the tiering scores as
        user_declared (3.0) → every skill hits Primary and Secondary/Tertiary stay
        empty; and the 'Skills beyond your CV' box shows the whole CV. additional_
        skills must stay the USER's extras only — cv skills live on cv.skills."""
        from src.services.profile.models import UserPreferences
        from src.services.profile.preferences import merge_cv_and_preferences

        prefs = UserPreferences(additional_skills=["Terraform"])
        merged = merge_cv_and_preferences(["Python", "Docker", "RAG"], [], prefs)
        assert merged.additional_skills == ["Terraform"]
        assert "Python" not in merged.additional_skills
        assert "Docker" not in merged.additional_skills

    def test_tiers_populate_when_sources_differ(self):
        """End-to-end: with additional_skills = user extras only, the three tiers
        actually split — a user-declared extra → Primary, a CV-only skill →
        Secondary, a GitHub-language-only skill → Tertiary."""
        from src.services.profile.models import CVData, UserPreferences, UserProfile
        from src.services.profile.skill_tiering import (
            collect_evidence_from_profile,
            tier_skills_by_evidence,
        )

        profile = UserProfile(
            cv_data=CVData(skills=["CvOnlySkill"], github_languages={"GhLangSkill": 100_000}),
            preferences=UserPreferences(additional_skills=["UserExtra"]),
        )
        primary, secondary, tertiary = tier_skills_by_evidence(
            collect_evidence_from_profile(profile)
        )
        assert "UserExtra" in primary
        assert "CvOnlySkill" in secondary
        assert "GhLangSkill" in tertiary


class TestCvDeterministicPass:
    def test_splits_parenthetical_tools(self):
        """'OCR (Tesseract)' and 'Python (Pandas, NumPy)' should yield the outer
        term AND each tool inside the parentheses as separate skills."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "Skills\nOCR (Tesseract)\nPython (Pandas, NumPy, Matplotlib)\n\nExperience\nx"
        out = deterministic_cv_fields(text)
        s = set(out["skills"])
        assert {"OCR", "Tesseract", "Python", "Pandas", "NumPy", "Matplotlib"}.issubset(s)

    def test_strips_category_label_prefix(self):
        """CV skill lines like 'Cloud & MLOps: AWS (Bedrock, SageMaker)' should
        drop the category label and keep the real skills (incl. inner tools)."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = (
            "Skills\n"
            "Cloud & MLOps: AWS (Bedrock, SageMaker) • Docker\n"
            "AI Automation Tools: n8n • Zapier\n\n"
            "Experience\nx"
        )
        out = deterministic_cv_fields(text)
        s = set(out["skills"])
        assert {"AWS", "Bedrock", "SageMaker", "Docker", "n8n", "Zapier"}.issubset(s)
        assert "Cloud & MLOps: AWS" not in s
        assert "AI Automation Tools: n8n" not in s

    def test_extracts_skills_section_lines(self):
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = (
            "John Doe\nSenior Engineer\n\n"
            "Skills\nPython\nDjango\nAWS\n\n"
            "Experience\nDid things at a company\n"
        )
        out = deterministic_cv_fields(text)
        assert "Python" in out["skills"]
        assert "Django" in out["skills"]
        assert "AWS" in out["skills"]
        # The 'Experience' body must NOT leak into skills.
        assert "Did things at a company" not in out["skills"]

    def test_splits_comma_separated_skills(self):
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "Skills:\nPython, Django, AWS, Docker\n\nEducation\nBSc\n"
        out = deterministic_cv_fields(text)
        assert {"Python", "Django", "AWS", "Docker"}.issubset(set(out["skills"]))

    def test_no_skills_section_returns_empty(self):
        from src.services.profile.cv_parser import deterministic_cv_fields

        out = deterministic_cv_fields("John Doe\nSome prose, no skills header.\n")
        assert out["skills"] == []

    # ── Live-profile bug repro (Ranjith CV): experience/company/dates/
    #    sentences/fragments leaking into the skills list ───────────────

    def test_stops_at_professional_experience_heading(self):
        """BUG: 'PROFESSIONAL EXPERIENCE' is not an exact heading in the stop set,
        so the skills capture never stopped and swallowed the whole experience
        section — company names, dates, and full sentences became 'skills'."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = (
            "CORE SKILLS\n"
            "Python • Docker • RAG\n"
            "PROFESSIONAL EXPERIENCE\n"
            "Calnex Solutions | June 2025 – September 2025 | Stevenage\n"
            "AI Solutions Engineer\n"
            "Architected containerised assistant achieving 95% response accuracy.\n"
        )
        s = deterministic_cv_fields(text)["skills"]
        assert {"Python", "Docker", "RAG"}.issubset(set(s))
        # None of the experience junk may appear as a skill.
        assert "PROFESSIONAL EXPERIENCE" not in s
        assert "Calnex Solutions" not in s
        assert not any("2025" in x for x in s)          # dates
        assert not any(x.endswith(".") for x in s)       # sentence fragments
        assert not any("Stevenage" in x for x in s)      # location

    def test_rejoins_wrapped_bulleted_skill(self):
        """BUG: a PDF line-wrap inside a bulleted skills line split one skill in
        two — a long line wrapping mid-item ('… • Audio\\nProcessing • …') produced
        'Audio' and 'Processing' as separate skills. Real wraps happen on long
        lines (page margin), which is what the merge heuristic keys on."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = (
            "CORE SKILLS\n"
            "Generative AI • Computer Vision • Speech Recognition • Audio\n"  # long → wraps
            "Processing • Deep Learning • Transfer\n"
            "Learning • NLP\n"
            "Education\nBSc\n"
        )
        s = deterministic_cv_fields(text)["skills"]
        assert "Audio Processing" in s
        assert "Transfer Learning" in s
        assert "Audio" not in s and "Processing" not in s
        assert "Transfer" not in s and "Learning" not in s

    def test_one_skill_per_line_not_merged(self):
        """Guard: a one-per-line skills list (short lines, no bullets/labels) must
        NOT be merged by the wrap heuristic — each short line stays its own skill."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "Skills\nPython\nDjango\nAWS\nDocker\n\nExperience\nx\n"
        s = deterministic_cv_fields(text)["skills"]
        assert {"Python", "Django", "AWS", "Docker"}.issubset(set(s))
        assert "Python Django" not in s

    def test_does_not_split_compound_slash_skills(self):
        """BUG: '/' was a delimiter, so 'CI/CD Pipelines' and 'AI/ML' were split
        into 'CI'+'CD Pipelines' and 'AI'+'ML'. Slash-compounds stay whole."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "CORE SKILLS\nCI/CD Pipelines • AI/ML • TCP/IP\n\nEducation\nBSc\n"
        s = deterministic_cv_fields(text)["skills"]
        assert "CI/CD Pipelines" in s
        assert "AI/ML" in s
        assert "TCP/IP" in s
        assert "CI" not in s and "CD Pipelines" not in s

    def test_captures_summary_section(self):
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "Summary\nExperienced ML engineer with 5 years.\n\nSkills\nPython\n"
        out = deterministic_cv_fields(text)
        assert "Experienced ML engineer" in out["summary"]

    def test_empty_text_returns_empty_fields(self):
        from src.services.profile.cv_parser import deterministic_cv_fields

        out = deterministic_cv_fields("")
        assert out["skills"] == []
        assert out["summary"] == ""

    def test_non_standard_skill_heading_is_captured(self):
        """Headings are matched by STEM, not exact string — so 'TOOLS &
        TECHNOLOGIES' / 'Core Technical Skills' work, not just 'Skills'."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "TOOLS & TECHNOLOGIES\nNmap, Burp Suite, Wireshark\n\nExperience\nx"
        out = deterministic_cv_fields(text)
        assert {"Nmap", "Burp Suite", "Wireshark"}.issubset(set(out["skills"]))
        text2 = "Core Technical Skills\nPython, FastAPI, Snowflake\n\nEducation\nBSc"
        out2 = deterministic_cv_fields(text2)
        assert {"Python", "FastAPI", "Snowflake"}.issubset(set(out2["skills"]))

    def test_prose_ending_in_period_is_not_a_skill_heading(self):
        """A wrapped summary line containing a stem word ('...technology
        organisation.') must NOT be mistaken for a skills heading."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = (
            "Summary\nSeeking a role within a UK technology organisation.\n"
            "TECHNICAL SKILLS\nPython, PyTorch, Docker\n\nExperience\nx"
        )
        out = deterministic_cv_fields(text)
        assert {"Python", "PyTorch", "Docker"}.issubset(set(out["skills"]))

    def test_long_prose_token_is_dropped(self):
        """Structural guard: a >5-word phrase is prose, not a skill."""
        from src.services.profile.cv_parser import deterministic_cv_fields

        text = "Skills\nProduction Python for data engineering and analytics, Docker\n\nExperience\nx"
        out = deterministic_cv_fields(text)
        assert "Docker" in out["skills"]
        assert "Production Python for data engineering and analytics" not in out["skills"]


# ── Preferences deterministic pass — structure-only, no LLM ─────────


class TestAboutMeDeterministicPass:
    def test_extracts_explicitly_listed_skills_after_marker(self):
        from src.services.profile.preferences import deterministic_about_me_fields

        text = "AI engineer.\nSkills: Python, Docker, LangChain\nLooking for remote roles."
        out = deterministic_about_me_fields(text)
        assert {"Python", "Docker", "LangChain"}.issubset(set(out))

    def test_handles_technologies_and_bullet_markers(self):
        from src.services.profile.preferences import deterministic_about_me_fields

        text = "Technologies: PyTorch • TensorFlow • Kubernetes"
        out = deterministic_about_me_fields(text)
        assert {"PyTorch", "TensorFlow", "Kubernetes"}.issubset(set(out))

    def test_pure_prose_with_no_marker_returns_empty(self):
        """No skill vocabulary — free prose yields nothing. Since decision 28
        nothing reads this prose for meaning any more; it stays untouched for
        the user's own agent to read off ``get_profile``."""
        from src.services.profile.preferences import deterministic_about_me_fields

        out = deterministic_about_me_fields(
            "I love building production GenAI systems and shipping fast."
        )
        assert out == []

    def test_blank_returns_empty(self):
        from src.services.profile.preferences import deterministic_about_me_fields

        assert deterministic_about_me_fields("") == []
        assert deterministic_about_me_fields("   ") == []


# ── GitHub deterministic pass — topics + language from stored briefs ─


class TestGithubDeterministicPass:
    def test_surfaces_topics_cleaned(self):
        from src.services.profile.github_enricher import deterministic_github_fields

        briefs = [
            {"name": "a", "description": "x", "topics": ["machine-learning", "rag"]},
            {"name": "b", "description": "y", "topics": ["rag", "fraud-detection"]},
        ]
        out = deterministic_github_fields(briefs)
        assert "machine learning" in out  # hyphen → space
        assert "rag" in out and out.count("rag") == 1  # deduped across repos
        assert "fraud detection" in out

    def test_empty_or_topicless_returns_empty(self):
        from src.services.profile.github_enricher import deterministic_github_fields

        assert deterministic_github_fields([]) == []
        assert deterministic_github_fields([{"name": "a", "description": "x", "topics": []}]) == []

    def test_surfaces_repo_language(self):
        """Diagnosis fix: the repo's primary language is a structural GitHub API
        field (not a keyword list, rule #28) and MUST surface as a deterministic
        skill — previously the brief dropped it, starving both passes."""
        from src.services.profile.github_enricher import deterministic_github_fields

        briefs = [
            {"name": "a", "language": "Python", "description": "x", "topics": ["rag"]},
            {"name": "b", "language": "TypeScript", "description": "y", "topics": []},
        ]
        out = deterministic_github_fields(briefs)
        assert "Python" in out
        assert "TypeScript" in out
        assert "rag" in out  # topics still surface alongside language

    def test_language_deduped_against_topics_and_case(self):
        """A language already present as a topic isn't emitted twice."""
        from src.services.profile.github_enricher import deterministic_github_fields

        briefs = [
            {"name": "a", "language": "Python", "description": "", "topics": ["python"]},
            {"name": "b", "language": "Python", "description": "", "topics": []},
        ]
        out = deterministic_github_fields(briefs)
        assert sum(1 for s in out if s.lower() == "python") == 1


# ── LinkedIn deterministic pass — structure-only, no LLM ─────────────


class TestLinkedInDeterministicPass:
    def test_extracts_top_skills(self):
        """Since decision 28 this parser has exactly one layer — deterministic —
        so there is no LLM call left to guard against; it's a plain read."""
        from src.services.profile import linkedin_parser

        text = _linkedin_text()
        out = linkedin_parser.deterministic_linkedin_fields(text)

        assert "Kubernetes" in out["skills"]
        assert out["raw_text"] == text

    def test_non_linkedin_text_returns_empty_skills(self):
        from src.services.profile.linkedin_parser import deterministic_linkedin_fields

        out = deterministic_linkedin_fields("just some random text, not a profile")
        assert out["skills"] == []

    def test_header_lines_do_not_leak_into_skills(self):
        """Diagnosis fix: LinkedIn's 2-column PDF de-wrap bleeds the header
        (name / headline / location) into the Top-Skills sidebar body. Those are
        already captured structurally as header fields, so they MUST NOT appear as
        skills. Structural filter (drop verbatim header lines), not a keyword
        denylist — rule #28 safe."""
        from src.services.profile.linkedin_parser import deterministic_linkedin_fields

        text = (
            "Jane Dev\n"
            "Student at Hertfordshire University\n"
            "Luton, England, United Kingdom\n"
            "linkedin.com/in/janedev\n"
            "Summary\nI build things.\n"
            "Experience\nSenior Engineer at Acme\n"
            "Top Skills\n"
            "Kubernetes\n"
            "Docker\n"
            # --- de-wrap bleed: the header repeats inside the sidebar body ---
            "Jane Dev\n"
            "Student at Hertfordshire University\n"
            "Luton, England, United Kingdom\n"
        )
        out = deterministic_linkedin_fields(text)
        skills_lower = {s.lower() for s in out["skills"]}
        assert "kubernetes" in skills_lower
        assert "docker" in skills_lower
        assert "jane dev" not in skills_lower
        assert "student at hertfordshire university" not in skills_lower
        assert "luton, england, united kingdom" not in skills_lower

    def test_identity_block_bled_into_top_skills_is_dropped(self):
        """REAL LinkedIn layout (the actual Pavan bug): the 'header' section is
        EMPTY and the column de-wrap appends name/headline/location to the END of
        the Top-Skills body. Detect the name via the person's OWN email + profile
        URL (structural identity, never a skill vocabulary — rule #28 safe) and
        drop that trailing identity block."""
        from src.services.profile.linkedin_parser import deterministic_linkedin_fields

        text = (
            "Contact\n"
            "pavanalakunta58@gmail.com\n"
            "www.linkedin.com/in/pavan-\nalakunta (LinkedIn)\n"
            "Top Skills\n"
            "Pandas (Software)\n"
            "AWS SageMaker\n"
            "Applied Machine Learning\n"
            "Pavan Alakunta\n"
            "Student at University of Hertfordshire\n"
            "Luton, England, United Kingdom\n"
            "Experience\n"
            "Mathematics Tutor\n"
        )
        out = deterministic_linkedin_fields(text)
        sl = {s.lower() for s in out["skills"]}
        assert "pandas (software)" in sl
        assert "aws sagemaker" in sl
        assert "applied machine learning" in sl
        assert "pavan alakunta" not in sl
        assert "student at university of hertfordshire" not in sl
        assert "luton, england, united kingdom" not in sl


# ── Skill tiering — legacy two-pass shelves still contribute evidence ─
#
# github_llm_skills is our own deleted LLM's output (decision 28): the stored
# values are KEPT (reversible) but no longer read as a skill anywhere — the
# one skill list (skill_tiering.profile_skills). about_me_inferred_skills is
# filled by the deterministic about-me parse, so it still counts.


class TestSkillTieringNewSources:
    def test_github_llm_skill_is_kept_but_not_read(self):
        from src.services.profile.skill_tiering import collect_evidence_from_profile

        prof = UserProfile(cv_data=CVData(github_llm_skills=["LangChain"]))
        ev = {e.name: e.sources for e in collect_evidence_from_profile(prof)}
        assert "LangChain" not in ev
        assert prof.cv_data.github_llm_skills == ["LangChain"]  # stored data untouched

    def test_about_me_skill_becomes_evidence(self):
        from src.services.profile.skill_tiering import collect_evidence_from_profile

        prof = UserProfile(cv_data=CVData(about_me_inferred_skills=["Stakeholder Management"]))
        ev = {e.name: e.sources for e in collect_evidence_from_profile(prof)}
        assert "about_me_llm" in ev["Stakeholder Management"]

    def test_new_sources_have_positive_weights(self):
        from src.services.profile.skill_tiering import _SOURCE_WEIGHTS

        assert "github_llm" not in _SOURCE_WEIGHTS
        assert _SOURCE_WEIGHTS.get("about_me_llm", 0) > 0


# ── Orchestrator — run the deterministic pass over all four inputs ──


def _linkedin_text():
    """Minimal text that passes _looks_like_linkedin (URL + 3 headings)."""
    return (
        "Jane Dev\nSenior Engineer\n"
        "linkedin.com/in/janedev\n"
        "Summary\nI build things.\n"
        "Experience\nSenior Engineer at Acme\n"
        "Skills\nKubernetes\n"
    )


class TestTwoPassOrchestrator:
    @pytest.mark.asyncio
    async def test_extracts_all_four_sources_deterministically(self):
        """Every one of the four stored raw inputs goes through its own
        deterministic pass and lands on the merged CVData. No mocking needed:
        since decision 28 there is no model call anywhere in this path."""
        from src.services.profile import two_pass

        cv = CVData(
            raw_text="Skills\nPython\nDjango\n\nExperience\nWorked somewhere\n",
            linkedin_raw_text=_linkedin_text(),
            github_repos_brief=[
                {"name": "rag", "description": "rag app", "topics": ["llm"], "language": "Python"}
            ],
        )
        prefs = UserPreferences(about_me="Skills: Stakeholder Management, Roadmapping")
        prof = UserProfile(cv_data=cv, preferences=prefs)

        out = await two_pass.run_two_pass_extraction(prof)
        c = out.cv_data

        # CV deterministic pass landed.
        assert "Python" in c.skills and "Django" in c.skills
        # LinkedIn deterministic pass merged its Skills sidebar.
        assert "Kubernetes" in c.linkedin_skills
        # GitHub deterministic pass merged the repo's language and topic.
        assert "Python" in c.github_skills_inferred
        assert "llm" in c.github_skills_inferred
        # Preferences deterministic pass read the explicit "Skills:" marker.
        assert "Stakeholder Management" in c.about_me_inferred_skills
        assert "Roadmapping" in c.about_me_inferred_skills

    @pytest.mark.asyncio
    async def test_empty_profile_is_noop(self):
        from src.services.profile import two_pass

        prof = UserProfile()
        out = await two_pass.run_two_pass_extraction(prof)
        assert out.cv_data.skills == []
        assert out.cv_data.github_llm_skills == []
        assert out.cv_data.about_me_inferred_skills == []

    @pytest.mark.asyncio
    async def test_cv_pass_does_not_wipe_github_and_linkedin(self):
        """A CV re-parse (e.g. after an unrelated preferences edit re-triggers
        the whole extraction) must preserve LinkedIn/GitHub fields the other
        inputs already set."""
        from src.services.profile import two_pass

        cv = CVData(
            raw_text="Skills\nPython\n",
            linkedin_skills=["Existing LI Skill"],
            github_skills_inferred=["Existing GH Skill"],
        )
        prof = UserProfile(cv_data=cv)

        out = await two_pass.run_two_pass_extraction(prof)
        assert "Existing LI Skill" in out.cv_data.linkedin_skills
        assert "Existing GH Skill" in out.cv_data.github_skills_inferred
        assert "Python" in out.cv_data.skills

    @pytest.mark.asyncio
    async def test_extraction_never_clears_fields_the_agent_wrote(self):
        """THE LOAD-BEARING PROMISE OF DECISION 28.

        ``cv_positions``, ``linkedin_positions`` and ``github_llm_skills`` are
        not written by any pass in this file any more — they are written by
        the user's own agent through ``update_profile``. The extractor still
        re-runs on every save (module docstring), reading the SAME stored raw
        text again each time. That re-run must never touch structure it did
        not write: it has no way to tell "the agent wrote this on purpose"
        from "a stale value nobody needs", so the only safe rule is to leave
        it alone unconditionally.
        """
        from src.services.profile import two_pass

        positions = [
            {"company": "Acme", "title": "ML Engineer", "dates": "2023 - 2024",
             "location": "London", "bullets": ["Built pipelines"]},
        ]
        cv = CVData(
            raw_text="Skills\nPython\n\nExperience\nML Engineer at Acme\n",
            linkedin_raw_text=_linkedin_text(),
            cv_positions=list(positions),
            linkedin_positions=list(positions),
            github_llm_skills=["LangChain"],
        )
        profile = UserProfile(cv_data=cv, preferences=UserPreferences())

        out = await two_pass.run_two_pass_extraction(profile)

        assert out.cv_data.cv_positions == positions
        assert out.cv_data.linkedin_positions == positions
        assert out.cv_data.github_llm_skills == ["LangChain"]


class TestAboutMeInferredSkillsAreRecomputed:
    """``about_me_inferred_skills`` is the ONE exception to the fill-if-present
    rule the class above pins: unlike ``cv_positions`` / ``linkedin_positions``
    / ``github_llm_skills``, nothing about it is agent-written. It is a
    structural parse of ``preferences.about_me`` — an input the USER typed and
    can still edit — so it must be RECOMPUTED from the CURRENT about_me on
    every run, not merged onto whatever was there before.

    CodeRabbit (PR #608): the old code called ``_merge_str_list`` here, which
    is append-only. Editing about_me from "Skills: Python" to "Skills: Go"
    left both Python and Go on the shelf, and clearing about_me entirely left
    the stale list forever — the opposite of what an append-only merge is
    supposed to protect.
    """

    @pytest.mark.asyncio
    async def test_changing_about_me_leaves_only_the_new_skills(self):
        from src.services.profile import two_pass

        cv = CVData(about_me_inferred_skills=["Python"])
        prefs = UserPreferences(about_me="Skills: Go")
        profile = UserProfile(cv_data=cv, preferences=prefs)

        out = await two_pass.run_two_pass_extraction(profile)

        assert out.cv_data.about_me_inferred_skills == ["Go"], (
            "Python survived a change to about_me — the shelf was merged, "
            "not recomputed"
        )

    @pytest.mark.asyncio
    async def test_clearing_about_me_clears_the_derived_skills(self):
        from src.services.profile import two_pass

        cv = CVData(about_me_inferred_skills=["Python"])
        prefs = UserPreferences(about_me="")
        profile = UserProfile(cv_data=cv, preferences=prefs)

        out = await two_pass.run_two_pass_extraction(profile)

        assert out.cv_data.about_me_inferred_skills == [], (
            "clearing about_me must clear the skills derived from it — they "
            "are not agent-written and have no reason to survive"
        )


# ── CV REPLACEMENT — a new upload must not inherit the previous CV ──────────


class TestCvReplacementResetsCvOwnedFields:
    """Uploading a DIFFERENT CV must replace the CV-derived profile, not blend it.

    Found in production 2026-07-27. The upload route sets only
    ``profile.cv_data.raw_text`` and leaves every other field in place; the
    old (now-deleted) enhance merge then filled empty scalars ONLY and UNIONED
    the lists. So a second CV produced one profile holding:

        * the FIRST person's name / headline / location / summary
        * BOTH people's skills, roles, companies, education

    Measured: a real profile went from 104 skills to 152 after a different
    person's CV was uploaded, while `name` still read the original owner.

    That matters beyond tidiness — tailored CVs and cover letters are generated
    from this profile, so the wrong name goes out to an employer.

    ``reset_cv_owned_fields`` itself doesn't care whether decision 28's
    deterministic pass or the old LLM merge writes the new values afterwards
    — its only job is clearing every CV-owned field to a pristine ``CVData``'s
    default, in place. That contract is unchanged and is what this class
    tests.
    """

    def _cv_from_first_upload(self) -> CVData:
        cv = CVData(
            raw_text="OLD CV TEXT",
            name="Alice Anderson",
            headline="Staff Data Engineer",
            location="Manchester, UK",
            summary="Ten years of data platform work.",
            experience_text="Built pipelines at OldCorp.",
            skills=["Airflow", "Spark", "Python"],
            job_titles=["Data Engineer"],
            companies=["OldCorp"],
            education=["BSc Computer Science"],
            certifications=["AWS Solutions Architect"],
            achievements=["Cut pipeline cost 40%"],
            cv_industries=["Energy"],
            cv_languages=["English"],
            cv_skills_esco={"Airflow": "http://esco/airflow"},
            career_domain="data",
            # NOT CV-owned — these come from other inputs and must SURVIVE.
            linkedin_skills=["Public Speaking"],
            linkedin_industry="Utilities",
            linkedin_raw_text="LINKEDIN TEXT",
            github_languages={"Python": 900},
            github_llm_skills=["LangChain"],
            github_repos_brief=[{"name": "etl-tools"}],
            about_me_inferred_skills=["Mentoring"],
        )
        return cv

    def test_reset_clears_every_cv_derived_field(self):
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = self._cv_from_first_upload()
        reset_cv_owned_fields(cv)

        # Identity must not survive — this is the bug that put the wrong name
        # on someone else's tailored CV.
        assert cv.name == ""
        assert cv.headline == ""
        assert cv.location == ""
        assert cv.summary == ""
        assert cv.experience_text == ""

        # Nor may the previous CV's substance survive to be UNIONED with the new
        # one. This is the assertion a "name updated" check would have missed.
        assert cv.skills == []
        assert cv.job_titles == []
        assert cv.companies == []
        assert cv.education == []
        assert cv.certifications == []
        assert cv.achievements == []
        assert cv.cv_industries == []
        assert cv.cv_languages == []
        assert cv.cv_skills_esco == {}
        assert cv.career_domain is None

    def test_reset_preserves_everything_the_cv_does_not_own(self):
        """LinkedIn / GitHub / about-me are separate inputs the user did not re-upload.

        Wiping them would silently delete work — the opposite failure.
        """
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = self._cv_from_first_upload()
        reset_cv_owned_fields(cv)

        assert cv.linkedin_skills == ["Public Speaking"]
        assert cv.linkedin_industry == "Utilities"
        assert cv.linkedin_raw_text == "LINKEDIN TEXT"
        assert cv.github_languages == {"Python": 900}
        assert cv.github_llm_skills == ["LangChain"]
        assert cv.github_repos_brief == [{"name": "etl-tools"}]
        assert cv.about_me_inferred_skills == ["Mentoring"]

    def test_reset_mutates_in_place_so_the_profile_keeps_its_object(self):
        """The route holds `profile.cv_data`; a rebind would be silently lost."""
        from src.services.profile.two_pass import reset_cv_owned_fields

        cv = self._cv_from_first_upload()
        skills_obj = cv.skills
        reset_cv_owned_fields(cv)
        assert cv.skills is skills_obj, "must clear the list, not rebind it"
