from datetime import datetime, timezone, timedelta
from src.models import Job
from src.services.skill_matcher import (
    score_job, check_visa_flag, _recency_score, _text_contains,
    _negative_penalty, detect_experience_level, salary_in_range,
    _location_score, _foreign_location_penalty, JobScorer,
)
from src.services.profile.models import SearchConfig


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="Test Co",
        apply_url="https://example.com",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK",
        description="",
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_high_match_scores_above_70():
    """With an AI/ML SearchConfig, a matching job should score >= 70."""
    config = SearchConfig(
        job_titles=["AI Engineer", "ML Engineer"],
        primary_skills=["Python", "PyTorch", "TensorFlow", "LangChain", "RAG"],
        secondary_skills=["Hugging Face", "LLM fine-tuning", "NLP"],
        tertiary_skills=["Docker", "Kubernetes"],
    )
    scorer = JobScorer(config)
    job = _make_job(
        title="AI Engineer",
        location="London, UK",
        description=(
            "We need an AI Engineer skilled in Python, PyTorch, TensorFlow, "
            "LangChain, RAG pipelines, LLM fine-tuning, NLP, Deep Learning, "
            "Neural Networks, Computer Vision, Hugging Face Transformers, "
            "AWS SageMaker, Docker, Kubernetes, FastAPI, ChromaDB."
        ),
    )
    score = scorer.score(job)
    assert score >= 70, f"Expected >= 70, got {score}"


def test_low_match_scores_below_30():
    job = _make_job(
        title="Marketing Manager",
        location="New York, US",
        description="Looking for marketing manager with SEO and social media skills.",
    )
    score = score_job(job)
    assert score < 30, f"Expected < 30, got {score}"


def test_title_match_contributes_points():
    """With explicit JobScorer config, title match beats non-match."""
    config = SearchConfig(job_titles=["ML Engineer"], primary_skills=["Python"])
    scorer = JobScorer(config)
    job_match = _make_job(title="ML Engineer", description="Python role")
    job_no_match = _make_job(title="Chef", description="Python role")
    assert scorer.score(job_match) > scorer.score(job_no_match)


def test_location_match_contributes_points():
    uk_job = _make_job(title="Developer", location="London, UK", description="Python developer")
    us_job = _make_job(title="Developer", location="San Francisco, US", description="Python developer")
    assert score_job(uk_job) > score_job(us_job)


def test_remote_location_gets_points():
    remote_job = _make_job(title="Developer", location="Remote", description="Python developer")
    us_job = _make_job(title="Developer", location="San Francisco, US", description="Python developer")
    assert score_job(remote_job) > score_job(us_job)


def test_visa_flag_detected():
    job = _make_job(description="We offer visa sponsorship for the right candidate.")
    assert check_visa_flag(job) is True


def test_visa_flag_right_to_work():
    job = _make_job(description="Must have the right to work in the UK.")
    assert check_visa_flag(job) is True


def test_visa_flag_not_detected():
    job = _make_job(description="Standard Python developer role. No special requirements.")
    assert check_visa_flag(job) is False


def test_score_range_0_to_100():
    for desc in ["", "Python AI LLM RAG PyTorch TensorFlow" * 20, "marketing SEO sales"]:
        job = _make_job(description=desc)
        score = score_job(job)
        assert 0 <= score <= 100, f"Score {score} out of range"


def test_more_skills_higher_score():
    """With explicit config, jobs matching more skills score higher."""
    config = SearchConfig(
        primary_skills=["Python", "PyTorch", "TensorFlow", "LangChain"],
        secondary_skills=["RAG", "LLM", "NLP", "Deep Learning"],
        tertiary_skills=["AWS", "Docker"],
    )
    scorer = JobScorer(config)
    job_few = _make_job(description="Python developer role")
    job_many = _make_job(
        description="Python PyTorch TensorFlow LangChain RAG LLM NLP Deep Learning AWS Docker"
    )
    assert scorer.score(job_many) > scorer.score(job_few)


# ---- Recency scoring tests ----


def test_recency_today_gets_full_points():
    """A job posted today should score higher than same job posted 30 days ago."""
    today = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    job_today = _make_job(title="AI Engineer", location="London, UK", date_found=today)
    job_old = _make_job(title="AI Engineer", location="London, UK", date_found=old)
    assert score_job(job_today) > score_job(job_old)


def test_recency_old_job_gets_zero():
    """A job older than 7 days should get 0 recency points."""
    old_date = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    assert _recency_score(old_date) == 0


def test_recency_3_day_old_job():
    """A job 3 days old should get 8 recency points."""
    date_3d = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()
    assert _recency_score(date_3d) == 8


def test_recency_invalid_date_no_crash():
    """Invalid or empty date_found should not crash, just return 0."""
    assert _recency_score("") == 0
    assert _recency_score("not-a-date") == 0
    assert _recency_score("2025-13-99") == 0


def test_score_can_reach_100():
    """A perfect job (exact title + many skills + UK location + today's date) should hit 100."""
    config = SearchConfig(
        job_titles=["AI Engineer"],
        # Need enough primary skills to hit the 40-point cap (14 × 3 = 42 capped to 40)
        primary_skills=[
            "Python", "PyTorch", "TensorFlow", "LangChain", "RAG",
            "Hugging Face", "LLM fine-tuning", "NLP", "Deep Learning",
            "Neural Networks", "Computer Vision", "SageMaker", "Docker", "Kubernetes",
        ],
        secondary_skills=["ChromaDB", "FastAPI"],
        tertiary_skills=[],
    )
    scorer = JobScorer(config)
    job = _make_job(
        title="AI Engineer",
        location="London, UK",
        date_found=datetime.now(timezone.utc).isoformat(),
        description=(
            "We need an AI Engineer skilled in Python, PyTorch, TensorFlow, "
            "LangChain, RAG pipelines, LLM fine-tuning, NLP, Deep Learning, "
            "Neural Networks, Computer Vision, Hugging Face Transformers, "
            "AWS SageMaker, Docker, Kubernetes, FastAPI, ChromaDB."
        ),
    )
    score = scorer.score(job)
    assert score == 100, f"Expected 100, got {score}"


def test_recency_score_tiers():
    """Direct unit test of _recency_score at each tier boundary."""
    now = datetime.now(timezone.utc)
    assert _recency_score((now - timedelta(days=0)).isoformat()) == 10
    assert _recency_score((now - timedelta(days=1)).isoformat()) == 10
    assert _recency_score((now - timedelta(days=2)).isoformat()) == 8
    assert _recency_score((now - timedelta(days=3)).isoformat()) == 8
    assert _recency_score((now - timedelta(days=4)).isoformat()) == 6
    assert _recency_score((now - timedelta(days=5)).isoformat()) == 6
    assert _recency_score((now - timedelta(days=6)).isoformat()) == 4
    assert _recency_score((now - timedelta(days=7)).isoformat()) == 4
    assert _recency_score((now - timedelta(days=8)).isoformat()) == 0


# ---- Per-profile scoring tests ----


def test_score_with_explicit_java_profile():
    """JobScorer with a Java profile should favour Java jobs over AI jobs."""
    config = SearchConfig(
        job_titles=["Software Engineer", "Full Stack Developer"],
        primary_skills=["Java", "Spring Boot", "React"],
        secondary_skills=["MySQL", "Docker", "Kubernetes"],
        tertiary_skills=["Git", "Jenkins"],
        locations=["Manchester"],
        core_domain_words={"software", "full", "stack"},
        supporting_role_words={"engineer", "developer"},
    )
    scorer = JobScorer(config)
    java_job = _make_job(
        title="Software Engineer",
        location="Manchester",
        description="Java Spring Boot developer with MySQL and Docker",
    )
    ai_job = _make_job(
        title="AI Engineer",
        location="London, UK",
        description="Python PyTorch TensorFlow LangChain RAG",
    )
    assert scorer.score(java_job) > scorer.score(ai_job)


def test_same_job_different_profiles_different_scores():
    """The same job description should score differently for different profiles."""
    job = _make_job(
        title="Full Stack Developer",
        location="London",
        description="Java Spring Boot React Python Django AWS Docker Kubernetes",
    )
    java_config = SearchConfig(
        job_titles=["Software Engineer"],
        primary_skills=["Java", "Spring Boot"],
        secondary_skills=["React", "MySQL"],
        tertiary_skills=["Git"],
        locations=["London"],
        core_domain_words={"software"},
        supporting_role_words={"engineer"},
    )
    python_config = SearchConfig(
        job_titles=["Python Developer"],
        primary_skills=["Python", "Django"],
        secondary_skills=["AWS", "Docker"],
        tertiary_skills=["Git"],
        locations=["London"],
        core_domain_words={"python"},
        supporting_role_words={"developer"},
    )
    java_score = JobScorer(java_config).score(job)
    python_score = JobScorer(python_config).score(job)
    # Both should score > 0 since the job has both Java and Python
    assert java_score > 0
    assert python_score > 0


# ---- Word-boundary matching tests ----


def test_word_boundary_python_no_monty():
    """'Python' should NOT match 'Monty Python' — word boundary prevents it."""
    assert _text_contains("Monty Python fan club", "Python") is True  # Python IS a word here
    assert _text_contains("expert in Python programming", "Python") is True


def test_word_boundary_nlp_no_helpline():
    """'NLP' should not match inside 'helpline'."""
    assert _text_contains("NLP engineer role", "NLP") is True
    assert _text_contains("call our helpline", "NLP") is False


def test_word_boundary_ai_standalone():
    """'AI' should match as a standalone word but not inside 'FAIR'."""
    assert _text_contains("AI research lab", "AI") is True
    assert _text_contains("FAIR research lab", "AI") is False


def test_word_boundary_ml_standalone():
    """'ML' should match standalone but not inside 'HTML'."""
    assert _text_contains("ML engineer needed", "ML") is True
    assert _text_contains("HTML developer needed", "ML") is False


# ---- Negative keyword tests ----


def test_negative_penalty_empty_defaults():
    """Module-level _negative_penalty returns 0 because NEGATIVE_TITLE_KEYWORDS
    is empty by design. Users must set negative_keywords in their preferences.
    """
    assert _negative_penalty("Sales Engineer") == 0
    assert _negative_penalty("AI Engineer") == 0


def test_jobscorer_negative_penalty_dynamic():
    """JobScorer uses user-configured negative keywords, not hardcoded defaults."""
    from src.services.profile.models import SearchConfig
    from src.services.skill_matcher import JobScorer

    config = SearchConfig(negative_title_keywords=["sales", "marketing"])
    scorer = JobScorer(config)

    # User's own negatives are penalized
    assert scorer._negative_penalty("Sales Engineer") == 30
    assert scorer._negative_penalty("Marketing Manager") == 30
    # Non-matching titles are not penalized
    assert scorer._negative_penalty("AI Engineer") == 0
    assert scorer._negative_penalty("Cardiology Consultant") == 0


def test_sales_engineer_scores_below_threshold():
    """A 'Sales Engineer' should score below MIN_MATCH_SCORE (30)."""
    job = _make_job(
        title="Sales Engineer",
        location="London, UK",
        description="Looking for a sales engineer to sell our software products.",
    )
    score = score_job(job)
    assert score < 30, f"Expected < 30, got {score}"


def test_marketing_manager_scores_below_threshold():
    job = _make_job(
        title="Marketing Manager",
        location="New York, US",
        description="Looking for marketing manager with SEO and social media skills.",
    )
    score = score_job(job)
    assert score < 30, f"Expected < 30, got {score}"


# ---- Experience level detection tests ----


def test_detect_senior():
    assert detect_experience_level("Senior ML Engineer") == "senior"


def test_detect_junior():
    assert detect_experience_level("Junior Data Scientist") == "junior"


def test_detect_lead():
    assert detect_experience_level("Lead AI Engineer") == "lead"


def test_detect_principal():
    assert detect_experience_level("Principal Research Scientist") == "principal"


def test_detect_no_level():
    assert detect_experience_level("AI Engineer") == ""


# ---- Location scoring tests ----


def test_greater_london_gets_points():
    """'Greater London' should get full location points."""
    from src.services.skill_matcher import LOCATION_WEIGHT
    assert _location_score("Greater London") == LOCATION_WEIGHT


def test_city_of_london_gets_points():
    from src.services.skill_matcher import LOCATION_WEIGHT
    assert _location_score("City of London") == LOCATION_WEIGHT


def test_scotland_gets_points():
    """Scotland should get location points via alias to UK."""
    from src.services.skill_matcher import LOCATION_WEIGHT
    assert _location_score("Scotland") == LOCATION_WEIGHT


def test_remote_gets_points():
    from src.services.skill_matcher import LOCATION_WEIGHT
    assert _location_score("Remote") == LOCATION_WEIGHT - 2


def test_wfh_gets_points():
    from src.services.skill_matcher import LOCATION_WEIGHT
    assert _location_score("Work from home") == LOCATION_WEIGHT - 2


# ---- Salary range tests ----


def test_salary_in_range_matching():
    job = _make_job(salary_min=50000, salary_max=80000)
    assert salary_in_range(job) is True


def test_salary_in_range_too_low():
    job = _make_job(salary_min=10000, salary_max=20000)
    assert salary_in_range(job) is False


def test_salary_in_range_no_salary():
    job = _make_job()
    assert salary_in_range(job) is False


# ---- Foreign location penalty tests ----


def test_foreign_penalty_us_location():
    assert _foreign_location_penalty("New York, US") == 15


def test_foreign_penalty_india():
    assert _foreign_location_penalty("Bangalore, India") == 15


def test_foreign_penalty_empty_location():
    """Empty location should get no penalty (might be UK)."""
    assert _foreign_location_penalty("") == 0


def test_foreign_penalty_uk_location():
    assert _foreign_location_penalty("London, UK") == 0


def test_foreign_penalty_remote():
    assert _foreign_location_penalty("Remote") == 0


def test_foreign_penalty_unknown_location():
    """A location with no known indicators should get no penalty."""
    assert _foreign_location_penalty("Somewhere nice") == 0


def test_us_ai_job_scores_lower_than_uk():
    """A US-based AI job should score materially lower than the same UK job."""
    uk_job = _make_job(title="AI Engineer", location="London, UK",
                       description="Python PyTorch LLM RAG")
    us_job = _make_job(title="AI Engineer", location="San Francisco, CA",
                       description="Python PyTorch LLM RAG")
    uk_score = score_job(uk_job)
    us_score = score_job(us_job)
    assert uk_score - us_score >= 15, f"UK={uk_score}, US={us_score}"


# ---- Partial title scoring tests ----


def test_title_score_no_hardcoded_ai_bias():
    """Module-level _title_score must not use hardcoded AI/ML keywords.

    For domain-agnostic scaling, ANY title that's not in JOB_TITLES should
    return 0 — regardless of whether it contains AI buzzwords.
    """
    from src.services.skill_matcher import _title_score
    # "Technical Program Manager" has no match in JOB_TITLES → 0
    assert _title_score("Technical Program Manager") == 0
    # "AI Workspace Coordinator" also has no match — no AI word boost allowed
    assert _title_score("AI Workspace Coordinator") == 0
    # "Cardiology Consultant" (medical) also 0 — no domain favouritism
    assert _title_score("Cardiology Consultant") == 0


def test_jobscorer_title_score_works_for_medical_user():
    """JobScorer must work for non-tech users using their actual titles."""
    from src.services.profile.models import SearchConfig
    from src.services.skill_matcher import JobScorer

    config = SearchConfig(
        job_titles=["Cardiology Consultant", "Cardiologist"],
        primary_skills=["Echocardiography"],
        secondary_skills=[],
        tertiary_skills=[],
    )
    scorer = JobScorer(config)

    # Medical title should score WELL for medical user
    assert scorer._title_score("Cardiology Consultant") > 0
    # AI title should score 0 for medical user — no hardcoded AI bonus
    assert scorer._title_score("AI Engineer") == 0


# ---- Visa negation regression tests (F-001, F-008) ----


def test_visa_no_sponsorship_not_flagged():
    """'No sponsorship available' should NOT flag as visa sponsorship."""
    job = _make_job(description="No sponsorship available. Must have right to work.")
    assert check_visa_flag(job) is False


def test_visa_company_sponsored_benefits():
    """'Company-sponsored benefits' should NOT flag as visa sponsorship."""
    job = _make_job(description="Company-sponsored benefits include health insurance.")
    assert check_visa_flag(job) is False


# ---- JobScorer negative penalty word boundary regression test (F-009) ----


def test_jobscorer_negative_penalty_word_boundary():
    """JobScorer._negative_penalty should use word boundaries, not substring."""
    config = SearchConfig(
        negative_title_keywords=["sales"],
    )
    scorer = JobScorer(config)
    assert scorer._negative_penalty("Wholesale Manager") == 0
    assert scorer._negative_penalty("Sales Engineer") == 30


# ---- Foreign location penalty regression tests (F-029, F-036) ----


def test_london_ontario_foreign_penalty():
    """'London, Ontario' should be penalised as a foreign location."""
    assert _foreign_location_penalty("London, Ontario") == 15


def test_london_uk_no_penalty():
    """Plain 'London' and 'London, UK' should NOT be penalised."""
    assert _foreign_location_penalty("London") == 0
    assert _foreign_location_penalty("London, UK") == 0
