"""Two-pass profile extraction — deterministic pass + LLM enhance pass.

Covers the NEW pieces added for the user-side profile improvement goal:
  * preferences LLM pass — mine free-text ``about_me`` for skills
  * CV deterministic pass — no-LLM field grab from CV text
  * the orchestrator that re-runs both passes for all inputs from stored data

All LLM calls are mocked (rule #4 — suite runs offline).
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.services.profile.models import CVData, UserPreferences, UserProfile

# ── Preferences LLM pass (Pass 2) — mine about_me ───────────────────


class TestPreferencesLlmPass:
    @pytest.mark.asyncio
    async def test_infers_skills_from_about_me(self):
        from src.services.profile.preferences import llm_infer_from_about_me

        captured = {}

        async def fake_llm(prompt, system=""):
            captured["prompt"] = prompt
            return {"skills": ["Stakeholder Management", "Roadmapping"]}

        text = "I'm a product lead who loves stakeholder management and roadmapping."
        with patch("src.services.profile.llm_provider.llm_extract", new=fake_llm):
            skills = await llm_infer_from_about_me(text)

        assert "stakeholder" in captured["prompt"].lower()
        assert "Stakeholder Management" in skills

    @pytest.mark.asyncio
    async def test_blank_about_me_skips_llm(self):
        from src.services.profile.preferences import llm_infer_from_about_me

        called = False

        async def fake_llm(prompt, system=""):
            nonlocal called
            called = True
            return {"skills": ["nope"]}

        with patch("src.services.profile.llm_provider.llm_extract", new=fake_llm):
            skills = await llm_infer_from_about_me("   ")

        assert skills == []
        assert called is False

    @pytest.mark.asyncio
    async def test_llm_failure_returns_empty(self):
        from src.services.profile.preferences import llm_infer_from_about_me

        async def boom(prompt, system=""):
            raise RuntimeError("no provider")

        with patch("src.services.profile.llm_provider.llm_extract", new=boom):
            skills = await llm_infer_from_about_me("some real text here")

        assert skills == []


# ── LinkedIn LLM skills pass (fixes 2-column "Top Skills" loss) ─────


class TestLinkedInLlmSkills:
    @pytest.mark.asyncio
    async def test_extracts_skills_from_raw_text(self):
        from src.services.profile.linkedin_parser import llm_infer_linkedin_skills

        async def fake(prompt, system=""):
            assert "LangGraph" in prompt
            return {"skills": ["LangGraph", "Systems Design", "Multi-agent Systems", "RLHF"]}

        with patch("src.services.profile.llm_provider.llm_extract", new=fake):
            sk = await llm_infer_linkedin_skills("Top Skills\nLangGraph\nSystems Design\nRLHF")
        assert "LangGraph" in sk and "RLHF" in sk

    @pytest.mark.asyncio
    async def test_blank_skips_llm(self):
        from src.services.profile.linkedin_parser import llm_infer_linkedin_skills

        called = False

        async def fake(prompt, system=""):
            nonlocal called
            called = True
            return {"skills": ["x"]}

        with patch("src.services.profile.llm_provider.llm_extract", new=fake):
            sk = await llm_infer_linkedin_skills("   ")
        assert sk == [] and called is False

    @pytest.mark.asyncio
    async def test_failsafe_returns_empty(self):
        from src.services.profile.linkedin_parser import llm_infer_linkedin_skills

        async def boom(prompt, system=""):
            raise RuntimeError("no provider")

        with patch("src.services.profile.llm_provider.llm_extract", new=boom):
            sk = await llm_infer_linkedin_skills("real linkedin text")
        assert sk == []


# ── CV deterministic pass (Pass 1) — no-LLM field grab ──────────────


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


# ── Preferences deterministic pass (Pass 1) — structure-only, no LLM ─


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
        """No skill vocabulary — free prose yields nothing (the LLM pass mines it)."""
        from src.services.profile.preferences import deterministic_about_me_fields

        out = deterministic_about_me_fields(
            "I love building production GenAI systems and shipping fast."
        )
        assert out == []

    def test_blank_returns_empty(self):
        from src.services.profile.preferences import deterministic_about_me_fields

        assert deterministic_about_me_fields("") == []
        assert deterministic_about_me_fields("   ") == []


# ── GitHub deterministic pass (Pass 1) — topics from stored briefs ──


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


# ── LinkedIn deterministic pass (Pass 1) — structure-only, no LLM ───


class TestLinkedInDeterministicPass:
    def test_extracts_top_skills_without_calling_llm(self):
        from src.services.profile import linkedin_parser

        text = _linkedin_text()  # defined below
        called = False

        async def boom(prompt, system=""):
            nonlocal called
            called = True
            return {}

        with patch("src.services.profile.llm_provider.llm_extract", new=boom):
            out = linkedin_parser.deterministic_linkedin_fields(text)

        assert called is False  # deterministic pass must NOT touch the LLM
        assert "Kubernetes" in out["skills"]
        assert out["raw_text"] == text

    def test_non_linkedin_text_returns_empty_skills(self):
        from src.services.profile.linkedin_parser import deterministic_linkedin_fields

        out = deterministic_linkedin_fields("just some random text, not a profile")
        assert out["skills"] == []


# ── Skill tiering — new two-pass sources contribute evidence ────────


class TestSkillTieringNewSources:
    def test_github_llm_skill_becomes_evidence(self):
        from src.services.profile.skill_tiering import collect_evidence_from_profile

        prof = UserProfile(cv_data=CVData(github_llm_skills=["LangChain"]))
        ev = {e.name: e.sources for e in collect_evidence_from_profile(prof)}
        assert "github_llm" in ev["LangChain"]

    def test_about_me_skill_becomes_evidence(self):
        from src.services.profile.skill_tiering import collect_evidence_from_profile

        prof = UserProfile(cv_data=CVData(about_me_inferred_skills=["Stakeholder Management"]))
        ev = {e.name: e.sources for e in collect_evidence_from_profile(prof)}
        assert "about_me_llm" in ev["Stakeholder Management"]

    def test_new_sources_have_positive_weights(self):
        from src.services.profile.skill_tiering import _SOURCE_WEIGHTS

        assert _SOURCE_WEIGHTS.get("github_llm", 0) > 0
        assert _SOURCE_WEIGHTS.get("about_me_llm", 0) > 0


# ── Orchestrator — run both passes over all four inputs ─────────────


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
    async def test_enhances_all_four_sources(self, monkeypatch):
        from src.services.profile import two_pass

        cv = CVData(
            raw_text="Skills\nPython\nDjango\n\nExperience\nWorked somewhere\n",
            linkedin_raw_text=_linkedin_text(),
            github_repos_brief=[{"name": "rag", "description": "rag app", "topics": ["llm"]}],
        )
        prefs = UserPreferences(about_me="I lead product and love stakeholder management")
        prof = UserProfile(cv_data=cv, preferences=prefs)

        async def fake_cv(text, section_hint=""):
            return CVData(raw_text=text, skills=["FastAPI"], job_titles=["Engineer"])

        # LinkedIn now forks into two independent halves: a pure-deterministic
        # dict and an LLM dict. Both go through enrich_cv_from_linkedin.
        def fake_li_det(text):
            return {"skills": ["Kubernetes"], "summary": "", "industry": "",
                    "headline": "", "raw_text": text}

        async def fake_li_llm(text):
            return {"positions": [{"title": "SRE"}], "education": [], "certifications": [],
                    "languages": [], "projects": [], "volunteer": [], "courses": [],
                    "skills": ["LangGraph", "Systems Design"]}

        async def fake_gh(brief):
            return ["LangChain"]

        async def fake_about(text):
            return ["Stakeholder Management"]

        monkeypatch.setattr(two_pass, "llm_cv_fields_from_text", fake_cv)
        monkeypatch.setattr(two_pass, "deterministic_linkedin_fields", fake_li_det)
        monkeypatch.setattr(two_pass, "llm_linkedin_fields", fake_li_llm)
        monkeypatch.setattr(two_pass, "llm_infer_github_skills", fake_gh)
        monkeypatch.setattr(two_pass, "llm_infer_from_about_me", fake_about)

        out = await two_pass.run_two_pass_extraction(prof)
        c = out.cv_data

        # Deterministic CV pass landed.
        assert "Python" in c.skills and "Django" in c.skills
        # LLM CV pass enhanced.
        assert "FastAPI" in c.skills
        assert "Engineer" in c.job_titles
        # LinkedIn lane merged BOTH halves: deterministic (Kubernetes) + LLM
        # (LangGraph, Systems Design) skills, and the LLM position title.
        assert "Kubernetes" in c.linkedin_skills
        assert "LangGraph" in c.linkedin_skills and "Systems Design" in c.linkedin_skills
        assert "SRE" in c.job_titles
        # GitHub LLM pass merged.
        assert "LangChain" in c.github_llm_skills
        # GitHub deterministic pass merged the repo topic ("llm").
        assert "llm" in c.github_skills_inferred
        # Preferences LLM pass.
        assert "Stakeholder Management" in c.about_me_inferred_skills

    @pytest.mark.asyncio
    async def test_cv_llm_failure_keeps_deterministic_skills(self, monkeypatch):
        from src.services.profile import two_pass

        prof = UserProfile(cv_data=CVData(raw_text="Skills\nPython\nRust\n\nEducation\nBSc\n"))

        async def boom(text, section_hint=""):
            raise RuntimeError("no LLM key")

        monkeypatch.setattr(two_pass, "llm_cv_fields_from_text", boom)
        out = await two_pass.run_two_pass_extraction(prof)
        # Deterministic skills survive even though the LLM pass blew up.
        assert "Python" in out.cv_data.skills
        assert "Rust" in out.cv_data.skills

    @pytest.mark.asyncio
    async def test_empty_profile_is_noop(self):
        from src.services.profile import two_pass

        prof = UserProfile()
        out = await two_pass.run_two_pass_extraction(prof)
        assert out.cv_data.skills == []
        assert out.cv_data.github_llm_skills == []
        assert out.cv_data.about_me_inferred_skills == []

    @pytest.mark.asyncio
    async def test_cv_llm_does_not_wipe_github_and_linkedin(self, monkeypatch):
        """A CV re-parse must preserve LinkedIn/GitHub fields set by other passes."""
        from src.services.profile import two_pass

        cv = CVData(
            raw_text="Skills\nPython\n",
            linkedin_skills=["Existing LI Skill"],
            github_skills_inferred=["Existing GH Skill"],
        )
        prof = UserProfile(cv_data=cv)

        async def fake_cv(text, section_hint=""):
            return CVData(raw_text=text, skills=["NewSkill"])

        monkeypatch.setattr(two_pass, "llm_cv_fields_from_text", fake_cv)
        out = await two_pass.run_two_pass_extraction(prof)
        assert "Existing LI Skill" in out.cv_data.linkedin_skills
        assert "Existing GH Skill" in out.cv_data.github_skills_inferred


class TestReextractAndRescore:
    @pytest.mark.asyncio
    async def test_runs_extract_then_save_then_rescore(self, monkeypatch):
        import src.services.rescore as rescore_mod
        from src.services.profile import storage, two_pass

        calls = []
        prof = UserProfile(cv_data=CVData(raw_text="x"))

        monkeypatch.setattr(storage, "load_profile", lambda uid: prof)

        def fake_save(p, uid, source_action="user_edit"):
            calls.append(("save", source_action))

        monkeypatch.setattr(storage, "save_profile", fake_save)

        async def fake_run(p):
            calls.append(("extract", None))
            return p

        monkeypatch.setattr(two_pass, "run_two_pass_extraction", fake_run)

        async def fake_rescore(uid, db_path=None):
            calls.append(("rescore", uid))
            return {"rescored": 3}

        monkeypatch.setattr(rescore_mod, "rescore_user_feed", fake_rescore)

        result = await two_pass.reextract_and_rescore("user-1")

        assert [c[0] for c in calls] == ["extract", "save", "rescore"]
        # The new profile version is stamped with the two-pass audit label.
        assert ("save", "two_pass_reextract") in calls
        assert result["reextracted"] is True
        assert result["rescore"] == {"rescored": 3}

    @pytest.mark.asyncio
    async def test_no_profile_is_noop(self, monkeypatch):
        from src.services.profile import storage, two_pass

        monkeypatch.setattr(storage, "load_profile", lambda uid: None)
        result = await two_pass.reextract_and_rescore("ghost")
        assert result["reextracted"] is False
        assert result["reason"] == "no_profile"
