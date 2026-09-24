"""One definition of "your skills" (skill_tiering.profile_skills).

Measured on the owner's live profile 2026-09-23: the CV card said 81, the
grouped list said 131 (79 + 13 + 45), and the application page said "of 117" —
three numbers for one person, because each surface read different fields. The
fixture below is that profile's REAL stored shelves (skills only, no contact
data), junk included. Every surface must now read ONE list, and the junk that
comes from STRUCTURE (GitHub config-file languages, topic slugs, dependency
names, our own deleted LLM's leftovers) must be gone by SOURCE TYPE — there is
no word list anywhere (rule #28). What is left that is still wrong ("Data",
"Vector") is the agent's to prune through ``preferences.excluded_skills``.
"""
from __future__ import annotations

from typing import Any

import pytest

from tests.test_mcp_server import _mcp_client, _mint_token, _payload

# ── The owner's real stored shelves (user_profiles row, 2026-09-23) ──────────

OWNER_CV_SKILLS = [
    "Generative AI", "Foundation Models", "Multimodal AI", "LLM Fine-Tuning", "RAG",
    "Agentic AI", "Computer Vision", "Speech Recognition", "Audio Processing",
    "Deep Learning", "Supervised Learning", "Neural Networks", "Prompt Engineering",
    "Transfer Learning", "Random Forest", "XGBoost", "Model Optimisation", "NLP", "n8n",
    "AI workflows/agents", "Zapier", "Lovable", "AI app builder", "PyTorch", "TensorFlow",
    "Scikit-learn", "Keras", "LangChain", "Hugging Face Transformers", "OpenAI API",
    "Gemini API", "AWS", "Bedrock", "SageMaker", "S3", "CloudWatch", "Docker",
    "Kubernetes", "CI/CD Pipelines", "Model Deployment", "Model Monitoring",
    "Model Versioning", "Scalable AI Infrastructure", "ChromaDB", "FAISS", "OpenSearch",
    "Redis Caching", "Vector Embeddings", "Data Pipeline Architecture", "Python",
    "Pandas", "NumPy", "Matplotlib", "ETL Pipelines", "Data Preprocessing",
    "Feature Engineering", "EDA", "OCR", "Tesseract", "AI Ethics",
    "Bias Detection & Mitigation", "Model Explainability", "AI Safety",
    "Compliance Frameworks", "Data Governance", "Security Best Practices", "Git/GitHub",
    "System Architecture", "Algorithm Development", "Linux", "Agile Methodologies",
    "Process Automation", "Jupyter Notebooks", "Retrieval Augmented Generation",
    "AWS Bedrock", "AWS SageMaker", "AWS S3", "AWS CloudWatch", "Git", "GitHub",
    "RAG (Retrieval Augmented Generation)",
]
OWNER_LINKEDIN_SKILLS = [
    "LangGraph", "Systems Design", "Multi-agent Systems", "Vector databases", "RLHF",
    "AI evaluation frameworks", "Chroma DB", "Redis", "RAG Pipelines", "Vector",
    "Large Language Models", "Video Understanding", "Data",
]
OWNER_GITHUB_LANGUAGES = {
    "Python": 19282710, "TypeScript": 11905929, "Shell": 163428, "JavaScript": 195750,
    "CSS": 139422, "Makefile": 41238, "Dockerfile": 21759, "Batchfile": 9462,
    "PowerShell": 4179, "HTML": 564285, "Procfile": 150, "Dart": 354624,
    "PLpgSQL": 58005, "Just": 3471, "Jupyter Notebook": 2690556,
}
OWNER_GITHUB_LLM_SKILLS = [
    "FastAPI", "Next.js", "Tailwind CSS", "Framer Motion", "lucide-react", "Vitest",
    "Playwright", "Serwist", "Capacitor", "LangGraph", "MCP", "RAG", "AI automation",
    "Groq", "OpenRouter", "Gemini", "Human-in-the-Loop", "Gmail", "Slack", "n8n",
    "GPT-4o", "React", "semantic search", "Cloudflare Workers", "A/B testing",
    "GDPR compliance", "CAN-SPAM compliance", "emotional valence",
    "self-healing systems", "Flutter", "OpenAI", "Llama 3.3", "Fraud detection",
    "Machine learning", "Natural Language Processing", "Data visualization",
    "Human-in-the-loop systems", "PWA (Progressive Web App)", "AI-powered CRM",
]
OWNER_GITHUB_FRAMEWORKS = [
    "react-dom", "react", "@capacitor/haptics", "@capacitor/app", "valibot",
    "@capacitor/core", "@capacitor/status-bar", "workbox-window", "motion",
    "@capacitor/filesystem", "next", "@capacitor/preferences", "clsx", "tailwind-merge",
    "@supabase/supabase-js", "lucide-react", "tailwindcss-animate",
    "@capacitor/splash-screen", "class-variance-authority", "openai", "itsdangerous",
    "python-multipart", "apscheduler", "aioimaplib", "mypy", "playwright", "httpx",
    "pytest", "aiosmtplib", "aiohttp", "pytest-asyncio", "aiosqlite", "lxml", "fastapi",
    "email-validator", "pydantic-settings", "beautifulsoup4", "pydantic", "uvicorn",
    "pytest-cov", "ruff", "python-dotenv", "dnspython", "slack-bolt",
    "prometheus-client", "langchain-core", "datasets", "langgraph-checkpoint-sqlite",
    "slack-sdk",
]
OWNER_GITHUB_TOPICS = [
    "agent", "customer-support", "gmail", "hitl", "human-in-the-loop", "langgraph",
    "langsmith", "llm", "mcp", "slack",
]
OWNER_GITHUB_SKILLS_INFERRED = [
    "TypeScript", "Jupyter Notebook", "HTML", "Dart", "JavaScript", "Shell", "CSS",
    "PLpgSQL", "Makefile", "Dockerfile", "Batchfile", "PowerShell", "Just", "Procfile",
    "agent", "customer support", "gmail", "hitl", "human in the loop", "langgraph",
    "langsmith", "llm", "mcp", "slack", "Python",
]
OWNER_SUGGESTED_SKILLS = [
    "JAX", "Streamlit", "Flask", "Ray", "Dask", "LightGBM", "CatBoost", "H2O.ai",
    "Apache Kafka", "Airflow", "MLflow",
]

# Entries that are NOT skills by SOURCE TYPE and must reach no surface. Built
# from the shelves themselves (minus anything that ALSO sits on a real skill
# shelf, e.g. "RAG" is on the CV too) — never a hand-typed word list.
_SIGNIFICANT = {"Python", "TypeScript", "Jupyter Notebook", "HTML", "Dart", "JavaScript"}


def _owner_profile(excluded: list[str] | None = None) -> Any:
    from src.services.profile.models import CVData, UserPreferences, UserProfile

    return UserProfile(
        cv_data=CVData(
            raw_text="owner cv text",
            skills=list(OWNER_CV_SKILLS),
            linkedin_skills=list(OWNER_LINKEDIN_SKILLS),
            github_languages=dict(OWNER_GITHUB_LANGUAGES),
            github_llm_skills=list(OWNER_GITHUB_LLM_SKILLS),
            github_frameworks=list(OWNER_GITHUB_FRAMEWORKS),
            github_topics=list(OWNER_GITHUB_TOPICS),
            github_skills_inferred=list(OWNER_GITHUB_SKILLS_INFERRED),
            suggested_skills=list(OWNER_SUGGESTED_SKILLS),
        ),
        preferences=UserPreferences(excluded_skills=list(excluded or [])),
    )


def _non_skill_entries() -> set[str]:
    """Casefolded entries that only live on non-skill shelves."""
    real = {s.casefold().replace(" ", "") for s in OWNER_CV_SKILLS + OWNER_LINKEDIN_SKILLS}
    real |= {s.casefold().replace(" ", "") for s in _SIGNIFICANT}
    pool = (
        OWNER_GITHUB_LLM_SKILLS + OWNER_GITHUB_FRAMEWORKS + OWNER_GITHUB_TOPICS
        + OWNER_GITHUB_SKILLS_INFERRED + OWNER_SUGGESTED_SKILLS
        + [k for k in OWNER_GITHUB_LANGUAGES if k not in _SIGNIFICANT]
    )
    return {p.casefold().replace(" ", "") for p in pool} - real


def _key(name: str) -> str:
    return name.casefold().replace(" ", "")


_AD = {
    "title": "AI Engineer",
    "company": "Owner Fixture Ltd",
    "location": "London",
    "apply_url": "https://jobs.example/owner/1",
    "description": "You will build RAG with Python and LangGraph on AWS. Data matters; an llm mindset.",
}


async def _four_surfaces(authenticated_async_context, user_id: str, profile: Any) -> dict[str, Any]:
    """Seed ``profile`` and read all four surfaces through the real routes."""
    from src.api.mcp_server import mcp_runtime
    from src.services.profile.storage import save_profile

    save_profile(profile, user_id, source_action="cv_upload")
    async with authenticated_async_context() as client:
        prof = await client.get("/api/profile")
        assert prof.status_code == 200, prof.text
        bring = await client.post("/api/jobs/bring", json=_AD)
        assert bring.status_code == 200, bring.text
        app_id = int(bring.json()["application_id"])
        align = await client.get(f"/api/applications/{app_id}/alignment")
        assert align.status_code == 200, align.text
    token = await _mint_token(authenticated_async_context)
    async with mcp_runtime():
        async with _mcp_client(token) as mcp:
            agent = _payload(await mcp.call_tool("get_profile", {}))
    return {"profile": prof.json(), "alignment": align.json(), "agent": agent}


# ── the pure definition ──────────────────────────────────────────────────────


def test_owner_profile_has_one_list_without_structural_junk() -> None:
    from src.services.profile.skill_tiering import profile_skills

    rows = profile_skills(_owner_profile())
    names = {_key(r["name"]) for r in rows}
    assert rows, "the owner has skills"
    assert all(r["sources"] for r in rows), "every skill keeps its source"
    # Nothing that only sits on a non-skill shelf survives: LLM leftovers,
    # topics, dependency names, config-file languages, old suggestions.
    leaked = names & _non_skill_entries()
    assert leaked == set(), leaked
    for junk in ("emotionalvalence", "gdprcompliance", "procfile", "batchfile", "just", "llm", "llama3.3"):
        assert junk not in names, junk
    # The significant languages DO count, tagged as GitHub.
    by_key = {_key(r["name"]): r for r in rows}
    assert "github_lang" in by_key["typescript"]["sources"]
    # The RAG triple is one skill; "AWS"/"AWS Bedrock"/"Bedrock" are three CV
    # strings the structural read cannot tell apart (the agent's to prune).
    assert "rag" in names
    assert "retrievalaugmentedgeneration" not in names
    assert "rag(retrievalaugmentedgeneration)" not in names
    assert {"aws", "awsbedrock", "bedrock"} <= names
    # "Data" / "Vector" are LinkedIn line-wrap fragments — still present
    # until the agent excludes them (fixed at the parser in a later PR).
    assert {"data", "vector"} <= names


def test_language_share_is_a_live_setting(monkeypatch) -> None:
    from src.core import settings
    from src.services.profile.skill_tiering import profile_skills

    def langs() -> set[str]:
        return {
            r["name"] for r in profile_skills(_owner_profile()) if "github_lang" in r["sources"]
        }

    assert "Procfile" not in langs() and "Shell" not in langs()
    monkeypatch.setattr(settings, "GITHUB_LANGUAGE_MIN_SHARE", 0.0)
    assert {"Procfile", "Batchfile", "Just", "Shell"} <= langs()
    monkeypatch.setattr(settings, "GITHUB_LANGUAGE_MIN_SHARE", 0.5)
    assert langs() == {"Python"}  # the top language always counts


def test_empty_profile_is_silent() -> None:
    from src.services.profile.models import UserProfile
    from src.services.profile.skill_tiering import profile_skills

    assert profile_skills(UserProfile()) == []


# ── the four surfaces agree ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_every_surface_reads_the_same_count(authenticated_async_context, fixture_user_id):
    from src.services.profile.skill_tiering import profile_skills

    expected = len(profile_skills(_owner_profile()))
    got = await _four_surfaces(authenticated_async_context, fixture_user_id, _owner_profile())
    prof, align, agent = got["profile"], got["alignment"], got["agent"]

    header = prof["summary"]["skills_count"]
    grouped = {_key(s) for group in prof["skills_by_source"].values() for s in group}
    assert header == expected
    assert len(grouped) == expected
    assert align["skills_total"] == expected
    assert agent["skills_count"] == expected
    assert len(agent["skills"]) == expected
    assert {_key(r["name"]) for r in agent["skills"]} == grouped
    # The CV card's own list is the CV share of the same list.
    assert len(prof["cv_detail"]["skills"]) == len(prof["skills_by_source"]["cv"])

    # No structural junk anywhere.
    junk = _non_skill_entries()
    assert grouped & junk == set()
    assert {_key(s) for s in align["skills_in_ad"] + align["skills_not_in_ad"]} & junk == set()
    assert "llm" not in {_key(s) for s in align["skills_in_ad"]}
    # Our own LLM's leftovers are not shown either.
    assert "ai_suggestions" not in prof
    assert "llm_skills" not in prof["github_detail"]
    assert "skills_inferred" not in prof["github_detail"]
    # Their own labelled shelves stay visible.
    assert prof["github_detail"]["frameworks"] == OWNER_GITHUB_FRAMEWORKS
    assert set(prof["github_temporal"]["topics"]) == set(OWNER_GITHUB_TOPICS)


@pytest.mark.asyncio
async def test_excluded_skill_leaves_every_surface(authenticated_async_context, fixture_user_id):
    from src.services.profile.skill_tiering import profile_skills

    before = len(profile_skills(_owner_profile()))
    # "data" in lower case: exclusion is casefold; "AWS Bedrock" is a CV skill.
    got = await _four_surfaces(
        authenticated_async_context, fixture_user_id, _owner_profile(excluded=["data", "AWS Bedrock"])
    )
    prof, align, agent = got["profile"], got["alignment"], got["agent"]

    for surface in (
        {_key(s) for group in prof["skills_by_source"].values() for s in group},
        {_key(s) for s in prof["cv_detail"]["skills"]},
        {_key(s) for s in align["skills_in_ad"] + align["skills_not_in_ad"]},
        {_key(r["name"]) for r in agent["skills"]},
    ):
        assert "data" not in surface and "awsbedrock" not in surface
    assert prof["summary"]["skills_count"] == before - 2
    assert align["skills_total"] == before - 2
    assert agent["skills_count"] == before - 2
    # The ad says "Data" — an excluded skill is never counted as a match.
    assert "Data" not in align["skills_in_ad"]
    assert "Python" in align["skills_in_ad"]


def test_agent_is_told_how_to_prune() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[1] / "src" / "api" / "mcp_server.py").read_text(encoding="utf-8")
    assert src.count("preferences.excluded_skills") >= 2  # get_profile + update_profile docstrings


def test_no_skill_word_list_in_the_definition() -> None:
    """Rule #28: the one list cuts by SOURCE, never by a vocabulary of ours."""
    import inspect

    from src.services.profile import skill_tiering

    src = inspect.getsource(skill_tiering.profile_skill_evidence) + inspect.getsource(
        skill_tiering.profile_skills
    )
    for banned in ("SKILL_TERMS", "_TO_SKILL", "DENYLIST", "denylist", "BLOCKLIST"):
        assert banned not in src
