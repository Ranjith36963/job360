from datetime import datetime, timedelta, timezone

import pytest

from src.models import Job
from src.services.profile.models import SearchConfig
from src.services.skill_matcher import (
    RECENCY_WEIGHT,
    TITLE_WEIGHT,
    JobScorer,
    ScoreBreakdown,
    _location_score,
    _negative_penalty,
    _recency_score,
    _text_contains,
    check_visa_flag,
    detect_experience_level,
    salary_in_range,
    score_job,
)


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
    score = scorer.score(job).match_score
    assert score >= 70, f"Expected >= 70, got {score}"


@pytest.mark.fast
def test_low_match_scores_below_30():
    job = _make_job(
        title="Marketing Manager",
        location="New York, US",
        description="Looking for marketing manager with SEO and social media skills.",
    )
    score = score_job(job)
    assert score < 30, f"Expected < 30, got {score}"


def test_title_match_contributes_points():
    """With explicit JobScorer config, title match beats non-match.

    Batch 2.2 note: config now supplies two primary_skills and the description
    names both so the skill component clears MIN_SKILL_GATE (6). Without the
    second skill the matching job would be suppressed to the gate floor and
    the test's invariant (title_match > no_title_match) would collapse.
    """
    config = SearchConfig(
        job_titles=["ML Engineer"],
        primary_skills=["Python", "Docker"],
    )
    scorer = JobScorer(config)
    job_match = _make_job(title="ML Engineer", description="Python Docker role")
    job_no_match = _make_job(title="Chef", description="Python Docker role")
    assert scorer.score(job_match).match_score > scorer.score(job_no_match).match_score


def test_location_match_contributes_points():
    """Batch 2.2 — location contribution is only observable when the gate
    passes. This test therefore uses JobScorer with a matching profile so the
    location delta between UK and US shows through.
    """
    config = SearchConfig(
        job_titles=["Developer"],
        primary_skills=["Python", "Docker"],
    )
    scorer = JobScorer(config)
    uk_job = _make_job(title="Developer", location="London, UK", description="Python Docker developer")
    us_job = _make_job(title="Developer", location="San Francisco, US", description="Python Docker developer")
    assert scorer.score(uk_job).match_score > scorer.score(us_job).match_score


def test_remote_location_gets_points():
    """Batch 2.2 — same reason as test_location_match_contributes_points."""
    config = SearchConfig(
        job_titles=["Developer"],
        primary_skills=["Python", "Docker"],
    )
    scorer = JobScorer(config)
    remote_job = _make_job(title="Developer", location="Remote", description="Python Docker developer")
    us_job = _make_job(title="Developer", location="San Francisco, US", description="Python Docker developer")
    assert scorer.score(remote_job).match_score > scorer.score(us_job).match_score


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
    """With explicit config, jobs matching more skills score higher.

    Batch 2.2 note: both jobs now use the configured title so both clear the
    title gate; the `skill_count` axis stays the only varying dimension.
    `job_few` matches two primary skills (=6 points = exactly at the skill
    gate) so both jobs land on the non-suppressed linear path — preserving
    the strict-inequality invariant.
    """
    config = SearchConfig(
        job_titles=["AI Engineer"],
        primary_skills=["Python", "PyTorch", "TensorFlow", "LangChain"],
        secondary_skills=["RAG", "LLM", "NLP", "Deep Learning"],
        tertiary_skills=["AWS", "Docker"],
    )
    scorer = JobScorer(config)
    job_few = _make_job(title="AI Engineer", description="Python PyTorch developer role")
    job_many = _make_job(
        title="AI Engineer",
        description="Python PyTorch TensorFlow LangChain RAG LLM NLP Deep Learning AWS Docker",
    )
    assert scorer.score(job_many).match_score > scorer.score(job_few).match_score


# ---- Recency scoring tests ----


def test_recency_today_gets_full_points():
    """A job posted today should score higher than same job posted 30 days ago.

    Batch 2.2 note: uses JobScorer with a matching config so both jobs clear
    the gate and recency becomes the only axis of variation.
    """
    today = datetime.now(timezone.utc).isoformat()
    old = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    config = SearchConfig(
        job_titles=["AI Engineer"],
        primary_skills=["Python", "PyTorch"],
    )
    scorer = JobScorer(config)
    job_today = _make_job(
        title="AI Engineer",
        location="London, UK",
        date_found=today,
        description="Python PyTorch role",
    )
    job_old = _make_job(
        title="AI Engineer",
        location="London, UK",
        date_found=old,
        description="Python PyTorch role",
    )
    assert scorer.score(job_today).match_score > scorer.score(job_old).match_score


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


def test_recency_far_future_date_gets_zero_not_max():
    """L2: a job dated well in the future is implausible/fabricated data and must
    NOT earn maximum freshness. Before the fix, `(now - posted).days` went
    negative and slipped through the `<= 1` gate, so a future-dated job scored
    the MAX recency band — outranking genuinely fresh jobs. It now scores 0."""
    future = (datetime.now(timezone.utc) + timedelta(days=30)).isoformat()
    assert _recency_score(future) == 0
    # A year in the future — the extreme case that most clearly should not win.
    far_future = (datetime.now(timezone.utc) + timedelta(days=365)).isoformat()
    assert _recency_score(far_future) == 0


def test_recency_slight_future_clock_skew_still_counts_as_fresh():
    """A just-posted job whose source timestamp is a few hours ahead of our clock
    (normal skew) should still score as fresh, not be penalised to 0."""
    skewed = (datetime.now(timezone.utc) + timedelta(hours=2)).isoformat()
    assert _recency_score(skewed) == RECENCY_WEIGHT


def test_score_can_reach_100():
    """A perfect job (exact title + many skills + UK location + today's date) should hit 100."""
    config = SearchConfig(
        job_titles=["AI Engineer"],
        # Need enough primary skills to hit the 40-point cap (14 × 3 = 42 capped to 40)
        primary_skills=[
            "Python",
            "PyTorch",
            "TensorFlow",
            "LangChain",
            "RAG",
            "Hugging Face",
            "LLM fine-tuning",
            "NLP",
            "Deep Learning",
            "Neural Networks",
            "Computer Vision",
            "SageMaker",
            "Docker",
            "Kubernetes",
        ],
        secondary_skills=["ChromaDB", "FastAPI"],
        tertiary_skills=[],
    )
    scorer = JobScorer(config)
    # Maxing recency under the 5-column date model requires an honest
    # (high-confidence) posted_at. Pre-Batch-1 this relied on the scorer
    # reading date_found directly — which rewarded fabrication.
    now_iso = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        title="AI Engineer",
        location="London, UK",
        date_found=now_iso,
        posted_at=now_iso,
        date_confidence="high",
        description=(
            "We need an AI Engineer skilled in Python, PyTorch, TensorFlow, "
            "LangChain, RAG pipelines, LLM fine-tuning, NLP, Deep Learning, "
            "Neural Networks, Computer Vision, Hugging Face Transformers, "
            "AWS SageMaker, Docker, Kubernetes, FastAPI, ChromaDB."
        ),
    )
    score = scorer.score(job).match_score
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
    assert scorer.score(java_job).match_score > scorer.score(ai_job).match_score


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
    java_score = JobScorer(java_config).score(job).match_score
    python_score = JobScorer(python_config).score(job).match_score
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


# ---- Rule #30 — a non-UK location is a DOOR, never a scoring penalty ----
#
# Job360 refuses foreign jobs at ONE chokepoint (`services/uk_gate.check_uk`,
# called in `main.py` before storage). The scorer must not run a second, weaker
# copy of that decision: a job that got through the door is a job we chose to
# keep, and docking it points only makes the ranking lie about why.
#
# Each case pairs a clearly-foreign location with a CONTROL location that earns
# the SAME positive location points (asserted first, so the comparison can never
# be accidentally rigged by the +10 location reward). Any remaining gap between
# the pair is a penalty and nothing else.
#
# The last pair is the sharp one: "London, Ontario" and "London" both collect
# the +10 UK-location reward, so the pair isolates the penalty on its own.

_RULE30_SKILLS = ["Python", "PyTorch", "LLM", "RAG"]
_RULE30_DESC = "Python PyTorch LLM RAG"
_RULE30_PAIRS = (
    ("Bangalore, India", "Springfield"),      # unknown-to-the-scorer control
    ("New York, US", "Springfield"),
    ("San Francisco, CA", "Springfield"),
    ("London, Ontario", "London"),            # both earn the +10 location reward
)


@pytest.mark.parametrize("foreign_location,control_location", _RULE30_PAIRS)
def test_jobscorer_gives_foreign_location_no_penalty(foreign_location, control_location):
    """JobScorer: a foreign location must not COST points versus its control."""
    assert _location_score(foreign_location) == _location_score(control_location), (
        "test rigged: the pair does not earn equal positive location points"
    )

    scorer = JobScorer(
        SearchConfig(job_titles=["AI Engineer"], primary_skills=_RULE30_SKILLS)
    )
    foreign = scorer.score(
        _make_job(title="AI Engineer", location=foreign_location, description=_RULE30_DESC)
    ).match_score
    control = scorer.score(
        _make_job(title="AI Engineer", location=control_location, description=_RULE30_DESC)
    ).match_score
    assert foreign == control, (
        f"{foreign_location!r} scored {foreign} vs {control} for {control_location!r} "
        f"— that {control - foreign}-point gap is a banned penalty"
    )


def test_score_job_gives_foreign_location_no_penalty(monkeypatch):
    """Module-level `score_job` (the no-profile fallback) must not penalise either."""
    from src.services import skill_matcher as sm

    monkeypatch.setattr(sm, "JOB_TITLES", ["AI Engineer"])
    monkeypatch.setattr(sm, "PRIMARY_SKILLS", _RULE30_SKILLS)
    foreign = score_job(
        _make_job(title="AI Engineer", location="Bangalore, India", description=_RULE30_DESC)
    )
    unknown = score_job(
        _make_job(title="AI Engineer", location="Springfield", description=_RULE30_DESC)
    )
    assert foreign == unknown, f"foreign={foreign}, unknown={unknown}"


def test_no_foreign_penalty_symbol_survives():
    """The penalty helper and its hand-typed city list must be GONE, not unused.

    A dormant list is a list somebody re-wires later. Rule #30 bans the data
    as well as the call.
    """
    from src.services import skill_matcher as sm

    assert not hasattr(sm, "_foreign_location_penalty")
    assert not hasattr(sm, "FOREIGN_INDICATORS")


def test_us_ai_job_scores_lower_than_uk():
    """A UK job outranks the same US job — by the REWARD, not by a penalty.

    The whole gap is the +10 the UK location earns for matching the user's
    LOCATIONS (rule #30: matching what the user asked for is a reward; being
    elsewhere is the door's business, never a deduction). Asserting the exact
    weight, not ">= 15", is what makes a re-introduced penalty fail here.

    Batch 2.2 note: uses JobScorer with a matching profile so both jobs clear
    the gate; empty-default module-level scoring would collapse both to the
    gate floor of 10 and the invariant would no longer hold.
    """
    from src.services.skill_matcher import LOCATION_WEIGHT

    config = SearchConfig(
        job_titles=["AI Engineer"],
        primary_skills=["Python", "PyTorch", "LLM", "RAG"],
    )
    scorer = JobScorer(config)
    uk_job = _make_job(title="AI Engineer", location="London, UK", description="Python PyTorch LLM RAG")
    us_job = _make_job(title="AI Engineer", location="San Francisco, CA", description="Python PyTorch LLM RAG")
    uk_score = scorer.score(uk_job).match_score
    us_score = scorer.score(us_job).match_score
    assert uk_score > us_score, f"UK={uk_score}, US={us_score}"
    assert uk_score - us_score == LOCATION_WEIGHT, f"UK={uk_score}, US={us_score}"


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


# ---- Foreign locations (F-029, F-036) are the DOOR's job, not the scorer's ----


def test_foreign_location_is_refused_at_the_door_not_docked_points():
    """The job the deleted penalty was doing still has an owner: the gate.

    Deliberately NOT asserted here: "London, Ontario". The door admits it
    today (`dual_site_includes_uk`) because "london" is a UK gazetteer hit —
    the escape hatch built for genuine two-site ads like "London / New York".
    Measured on the live catalog 2026-08-12: 117 of the 379 rows that were
    paying the old penalty are admitted through that escape, and the UK signal
    for several of them comes from UK hamlets named "California" and
    "New York". That is a gap in the DOOR and it needs an owner decision —
    writing an assertion here that the door does not honour would be a guard
    that has never been watched firing.
    """
    from src.services.uk_gate import check_uk

    assert check_uk("Toronto, Canada", "greenhouse").allowed is False
    assert check_uk("Bangalore, India", "greenhouse").allowed is False
    assert check_uk("London, UK", "greenhouse").allowed is True


# ---- Pillar 3 Batch 1: 5-column date model recency tests ----

from src.services.skill_matcher import recency_score_for_job  # noqa: E402


def test_recency_posted_at_high_confidence_scores_full_band():
    """High-confidence posted_at within 1 day → full 10 points."""
    today = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        date_found="2020-01-01T00:00:00+00:00",  # old first_seen
        posted_at=today,
        date_confidence="high",
    )
    assert recency_score_for_job(job) == 10


def test_recency_posted_at_medium_confidence_scores_full_band():
    """Medium-confidence posted_at should also hit the full band (parsed relative)."""
    today = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        date_found="",
        posted_at=today,
        date_confidence="medium",
    )
    assert recency_score_for_job(job) == 10


def test_recency_none_posted_at_with_low_confidence_falls_back_to_first_seen():
    """When posted_at is None and confidence is low, fall back to first_seen capped at 60%."""
    today = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        date_found=today,  # first_seen = today → raw would be 10
        posted_at=None,
        date_confidence="low",
    )
    # 10 * 0.6 = 6 (honest discovery, not honest posting)
    assert recency_score_for_job(job) == 6


def test_recency_fabricated_confidence_scores_zero():
    """Fabricated-confidence posted_at must never inflate recency."""
    today = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        date_found=today,
        posted_at=today,
        date_confidence="fabricated",
    )
    assert recency_score_for_job(job) == 0


def test_recency_no_dates_at_all_scores_zero():
    """If neither posted_at nor date_found is present, no penalty and no score."""
    job = _make_job(
        date_found="",
        posted_at=None,
        date_confidence="low",
    )
    assert recency_score_for_job(job) == 0


def test_recency_repost_backdated_treated_as_trustworthy():
    """repost_backdated dates should be trusted like high confidence."""
    today = datetime.now(timezone.utc).isoformat()
    job = _make_job(
        date_found="",
        posted_at=today,
        date_confidence="repost_backdated",
    )
    assert recency_score_for_job(job) == 10


def test_score_job_uses_recency_for_job_helper():
    """JobScorer.score must flow through recency_score_for_job so low-confidence
    sources no longer get the +10 inflation.

    Batch 2.2 note: switched from module-level score_job (which always fails
    the gate under empty default keywords) to JobScorer with a matching config
    — the integration point being tested is the wiring of
    recency_score_for_job into the linear-scoring path.
    """
    today = datetime.now(timezone.utc).isoformat()
    config = SearchConfig(
        job_titles=["Plumber"],
        primary_skills=["Plumbing", "Pipes"],
    )
    scorer = JobScorer(config)
    job_fabricated = _make_job(
        title="Plumber",
        description="Plumbing Pipes expert",
        date_found=today,
        posted_at=today,
        date_confidence="fabricated",
    )
    job_honest = _make_job(
        title="Plumber",
        description="Plumbing Pipes expert",
        date_found=today,
        posted_at=today,
        date_confidence="high",
    )
    # Both have the same non-recency components — the ONLY difference
    # must come from the recency band.
    assert scorer.score(job_honest).match_score - scorer.score(job_fabricated).match_score == 10


# ---------------------------------------------------------------------------
# Pillar 2 Batch 2.2 — Gate-pass scoring (require-either)
#
# A job is suppressed to `max(10, (title+skill) * 0.25)` ONLY when it clears
# NEITHER the title gate NOR the skill gate (each default 15 % of its max — 6
# points). A strong title alone, or strong skills alone, is enough relevance to
# score fully. This keeps location/recency from inflating a job that matches
# nothing, while not flooring real "ML Engineer"-style jobs whose list-source
# descriptions are too thin to score skills (E2E_TEST_REPORT #2).
#
# (Originally required BOTH gates — see _gate_suppressed_score history.)
# ---------------------------------------------------------------------------


class TestGatePass:
    """Require-either gate: suppress only jobs that clear neither title nor skill."""

    # ---- JobScorer.score() (dynamic path) ----

    def test_jobscorer_zero_title_strong_skills_carries(self):
        """Zero title but strong skill match → NOT suppressed; skills carry it.

        Require-either gate (E2E_TEST_REPORT #2): a strong skill signal is
        enough relevance on its own, even when the title doesn't match the
        configured target. Only a job matching NEITHER is suppressed.
        """
        config = SearchConfig(
            job_titles=["Cardiology Consultant"],  # deliberately no title match
            primary_skills=["Python", "Django", "FastAPI", "Postgres"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="AI Engineer",  # zero overlap with configured title
            location="London, UK",
            description="Python Django FastAPI Postgres expert",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title_pts=0 but skill_pts=12 (≥ gate) → not both weak → full score.
        assert scorer.score(job).match_score > 25

    def test_jobscorer_zero_skills_strong_title_carries(self):
        """Exact title match but zero skill text → NOT suppressed; title carries it.

        Require-either gate (E2E_TEST_REPORT #2): this is the real-world case
        that broke search — list-based sources return thin descriptions with no
        skill keywords, so skill_pts=0 even for a perfect title. The strong
        title alone must keep the job alive.
        """
        config = SearchConfig(
            job_titles=["AI Engineer"],
            primary_skills=["Rust", "Embedded C"],  # none will match the description
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="AI Engineer",
            location="London, UK",
            description="Looking for a strong generalist.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title_pts=40 (≥ gate), skill_pts=0 → not both weak → full score.
        assert scorer.score(job).match_score > 25

    def test_jobscorer_both_zero_location_recency_dont_rescue(self):
        """Both zero + strong location + full recency → suppressed; location/recency
        no longer combine to produce an apparent 20-point match."""
        config = SearchConfig(
            job_titles=["Cardiology Consultant"],
            primary_skills=["Echocardiography"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="Marketing Manager",
            location="London, UK",
            description="SEO and social media skills.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # Without the gate: ~0+0+10+10-0-0 = 20. With the gate: max(10, 0) = 10.
        assert scorer.score(job).match_score <= 25
        assert scorer.score(job).match_score == 10  # floor

    def test_jobscorer_both_above_gate_no_suppression(self):
        """Both gates cleared → full linear score (> gate floor)."""
        config = SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["Python", "PyTorch"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="ML Engineer",
            location="London, UK",
            description="Python PyTorch role.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title_pts=40, skill_pts=6, location=10, recency=10 → 66
        assert scorer.score(job).match_score > 25
        assert scorer.score(job).match_score >= 60

    def test_jobscorer_strong_title_thin_description_not_suppressed(self):
        """A strong title match must NOT be gate-floored just because the
        (often truncated) description lists no skills.

        Regression for E2E_TEST_REPORT #2: real "ML Engineer"-style jobs from
        list-based sources (Reed/RemoteOK return short snippets) scored exactly
        10 and were filtered out because skill_pts=0. The gate suppresses only
        jobs with NEITHER a title NOR a skill signal — a strong title alone is
        enough relevance to score fully.
        """
        config = SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["Python", "PyTorch"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="ML Engineer",  # exact title → title_pts=40 (strong)
            location="London, UK",
            description="Day-rate contract, immediate start.",  # no skills listed
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        bd = scorer.score(job)
        # title=40, skill=0. Old gate floored to 10; new gate keeps it.
        assert bd.match_score > 10, (
            f"strong-title job should not be gate-floored; got {bd.match_score}"
        )
        assert bd.match_score >= 30  # clears MIN_MATCH_SCORE → reaches the user

    def test_jobscorer_title_exactly_at_gate_passes(self):
        """title_pts exactly at the gate (6) must clear (>= semantics)."""
        config = SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["Python", "PyTorch"],
            core_domain_words={"ml"},  # title partial-match path
            supporting_role_words={"engineer"},
        )
        scorer = JobScorer(config)
        # "ml engineer" against a partial-match title → 5*core + 3*support = 8 points
        # clipped to TITLE_WEIGHT // 2 = 20 under existing partial-match logic. To
        # hit the exact-6 edge we instead use a minimal title which triggers the
        # cap-at-gate via "Ml Ops Engineer" (single core overlap, one support).
        job = _make_job(
            title="Ml Ops Engineer",  # 1*core + 1*support = 5+3 = 8 (>6 gate)
            location="London, UK",
            description="Python PyTorch role.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # Skill gate: 3+3=6 → exactly at gate. Title gate: 8 → above.
        # Both ≥ gate → no suppression. Score should be > 10 floor.
        assert scorer.score(job).match_score > 10

    def test_jobscorer_weak_title_strong_skills_carries(self):
        """title just below its gate but skills above theirs → skills carry it.

        Require-either gate (E2E_TEST_REPORT #2): only NEITHER-signal jobs are
        suppressed, so a weak title with a strong skill match scores fully.
        """
        config = SearchConfig(
            job_titles=["Cardiology Consultant"],
            primary_skills=["Python", "Docker", "Postgres"],
            core_domain_words={"engineer"},
            supporting_role_words=set(),
        )
        scorer = JobScorer(config)
        # title: "AI Engineer" → 1 core match → 5 points (< gate 6)
        job = _make_job(
            title="AI Engineer",
            location="London, UK",
            description="Python Docker Postgres role.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title_pts=5 (< gate) but skill_pts=9 (≥ gate) → not both weak → full score.
        assert scorer.score(job).match_score > 10

    def test_jobscorer_weak_skill_strong_title_carries(self):
        """skill just below its gate but exact title → title carries it.

        Require-either gate (E2E_TEST_REPORT #2): a perfect title is enough
        relevance even when only one skill keyword appears in the text.
        """
        config = SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["Python"],  # only one primary → max 3 points (< gate 6)
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="ML Engineer",
            location="London, UK",
            description="Python-only role.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title_pts=40 (≥ gate), skill_pts=3 (< gate) → not both weak → full score.
        assert scorer.score(job).match_score > 25

    def test_jobscorer_suppressed_returns_floor_10_when_gate_fails_fully(self):
        """All components zero + gate-fail → exactly the floor of 10."""
        config = SearchConfig(
            job_titles=["Something Else"],
            primary_skills=["Nothing Here"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="Marketing Manager",
            location="Moon",  # 0 location
            description="Generic role.",
            date_found="",  # 0 recency
        )
        assert scorer.score(job).match_score == 10

    # ---- Module-level score_job() (legacy path with empty defaults) ----

    def test_score_job_empty_defaults_always_fails_gate(self):
        """With the default empty keywords, score_job must suppress even UK jobs
        with full recency to ≤25 — location/recency alone cannot carry a job."""
        uk_recent_job = _make_job(
            title="Generic Role",
            location="London, UK",
            description="Generic description.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # Pre-gate: 0+0+10+10 = 20. Post-gate: floor 10.
        assert score_job(uk_recent_job) <= 25
        assert score_job(uk_recent_job) == 10

    def test_score_job_with_patched_keywords_can_pass_gate(self, monkeypatch):
        """When JOB_TITLES and PRIMARY_SKILLS contain matching entries, the
        gate passes and the full linear score is computed."""
        from src.services import skill_matcher as sm

        monkeypatch.setattr(sm, "JOB_TITLES", ["Data Scientist"])
        monkeypatch.setattr(sm, "PRIMARY_SKILLS", ["Python", "Pandas"])
        job = _make_job(
            title="Data Scientist",
            location="London, UK",
            description="Python and Pandas expertise required.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )
        # title=40, skill=6 (>= gate), location=10, recency=10 → 66
        assert score_job(job) > 25

    def test_score_job_gate_floor_10_even_with_foreign_penalty(self):
        """Gate-fail path doesn't apply penalties below 0 — floor is 10."""
        foreign_job = _make_job(
            title="Cleaner",
            location="San Francisco, CA",
            description="Nothing relevant.",
            date_found="",
        )
        # Pre-gate with penalty: 0+0+0+0-0-15 = -15 → 0. Post-gate: floor 10.
        assert score_job(foreign_job) == 10

    def test_gate_settings_exposed_at_module_level(self):
        """MIN_TITLE_GATE / MIN_SKILL_GATE must be importable from core.settings
        so ops can tune them via env vars without editing code."""
        from src.core.settings import MIN_SKILL_GATE, MIN_TITLE_GATE

        assert MIN_TITLE_GATE == 0.15
        assert MIN_SKILL_GATE == 0.15


# ---------------------------------------------------------------------------
# Step-1 B3 — ScoreBreakdown return type
#
# JobScorer.score() now returns a per-dimension ScoreBreakdown dataclass
# instead of a single int. Legacy callers (passing only `config`, no
# user_preferences/enrichment_lookup) must see the four legacy components
# populated and the four new dimension slots defaulted to 0 — so
# match_score matches the pre-Step-1 int return byte-for-byte.
# ---------------------------------------------------------------------------


class TestScoreBreakdown:
    """B3 RED tests — JobScorer.score() returns a ScoreBreakdown dataclass."""

    def _job(self):
        return _make_job(
            title="ML Engineer",
            location="London, UK",
            description="Python PyTorch role.",
            date_found=datetime.now(timezone.utc).isoformat(),
        )

    def _config(self):
        return SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["Python", "PyTorch"],
        )

    def test_score_returns_scorebreakdown_instance(self):
        scorer = JobScorer(self._config())
        result = scorer.score(self._job())
        assert isinstance(result, ScoreBreakdown)

    def test_scorebreakdown_has_all_eight_dimension_fields(self):
        scorer = JobScorer(self._config())
        breakdown = scorer.score(self._job())
        for field in (
            "title_score",
            "skill_score",
            "location_score",
            "recency_score",
            "seniority_score",
            "salary_score",
            "visa_score",
            "workplace_score",
            "match_score",
        ):
            assert hasattr(breakdown, field), f"Missing field: {field}"
            assert isinstance(
                getattr(breakdown, field), int
            ), f"{field} must be int, got {type(getattr(breakdown, field))}"

    def test_legacy_path_match_score_equals_sum_of_four_legacy_components(self):
        """When only `config` is passed (no prefs/enrichment), match_score must
        equal title + skill + location + recency — byte-identical to the
        pre-Step-1 int formula (no penalties on this job, gate passes)."""
        scorer = JobScorer(self._config())
        breakdown = scorer.score(self._job())
        # This ML job clears the gate and has no negative/foreign penalties,
        # so the linear sum matches match_score exactly.
        legacy_sum = breakdown.title_score + breakdown.skill_score + breakdown.location_score + breakdown.recency_score
        assert breakdown.match_score == legacy_sum

    def test_legacy_path_four_new_dimension_fields_all_zero(self):
        """No user_preferences + no enrichment_lookup → dim slots stay at 0
        (CLAUDE.md rule #19: legacy callers get identical behaviour)."""
        scorer = JobScorer(self._config())
        breakdown = scorer.score(self._job())
        assert breakdown.seniority_score == 0
        assert breakdown.salary_score == 0
        assert breakdown.visa_score == 0
        assert breakdown.workplace_score == 0

    def test_legacy_path_with_prefs_only_still_zero_dims(self):
        """user_preferences alone (no enrichment_lookup) → dims still 0."""
        from src.services.profile.models import UserPreferences

        prefs = UserPreferences(
            salary_min=50000,
            salary_max=80000,
            experience_level="senior",
            needs_visa=True,
            work_arrangement="remote",
        )
        scorer = JobScorer(self._config(), user_preferences=prefs)
        breakdown = scorer.score(self._job())
        assert breakdown.seniority_score == 0
        assert breakdown.salary_score == 0
        assert breakdown.visa_score == 0
        assert breakdown.workplace_score == 0

    def test_gate_suppressed_path_still_returns_scorebreakdown(self):
        """Gate-suppressed jobs must also return a ScoreBreakdown (not int)."""
        config = SearchConfig(
            job_titles=["Something Else"],
            primary_skills=["Nothing Here"],
        )
        scorer = JobScorer(config)
        job = _make_job(
            title="Marketing Manager",
            location="Moon",
            description="Generic role.",
            date_found="",
        )
        breakdown = scorer.score(job)
        assert isinstance(breakdown, ScoreBreakdown)
        assert breakdown.match_score == 10  # floor


# ═══ Dead-title-lever fix (P0-3, funnel redesign 2026-08-05) ══════════════════
# Real postings almost never equal or contain a user's CV-derived title string
# ("AI Solutions Engineer - R&D Department"), so exact/substring matching never
# fired and the partial fallback capped at 20 — measured on prod: no job ever
# scored above 8/40 on title. The fix: a job title showing BOTH a core domain
# word AND a role word ("AI Engineer", "Machine Learning Engineer") is a strong
# role match worth 30/40, +3 per extra core word, capped at TITLE_WEIGHT.


class TestDomainRoleTitleBand:
    def _scorer(self, core, support):
        cfg = SearchConfig(
            job_titles=["AI Solutions Engineer - R&D Department"],
            primary_skills=["python"],
            secondary_skills=[],
            tertiary_skills=[],
            locations=["London"],
            core_domain_words=set(core),
            supporting_role_words=set(support),
        )
        return JobScorer(cfg)

    def test_domain_plus_role_scores_strong(self):
        s = self._scorer({"ai", "ml", "learning"}, {"engineer"})
        assert s._title_score("AI Engineer (UK)") >= 30

    def test_ml_engineer_matches_via_skill_derived_core(self):
        s = self._scorer({"ai", "ml", "learning"}, {"engineer"})
        assert s._title_score("Machine Learning Engineer") >= 30

    def test_role_without_domain_scores_zero(self):
        """'Safety Engineer' has the role word but no domain word — a generic
        role title must NOT ride the strong band."""
        s = self._scorer({"ai", "ml"}, {"engineer"})
        assert s._title_score("Safety Engineer") == 0

    def test_domain_without_role_stays_in_partial_band(self):
        s = self._scorer({"ai"}, {"engineer"})
        assert 0 < s._title_score("Generative AI Newsletter") <= TITLE_WEIGHT // 2

    def test_strong_band_capped_at_title_weight(self):
        s = self._scorer({"ai", "ml", "learning", "data", "language"}, {"engineer"})
        assert s._title_score("AI ML Learning Data Language Engineer") <= TITLE_WEIGHT

    def test_exact_match_still_wins_everything(self):
        s = self._scorer({"ai"}, {"engineer"})
        assert s._title_score("AI Solutions Engineer - R&D Department") == TITLE_WEIGHT


# ═══ Skill-score length normalisation (tie-band fix, 2026-08-07) ══════════════
# Skill points are +3/+2/+1 per match. A LONGER ad contains more words and so
# accidentally contains more of the user's skills — 5,000-char corporate waffle
# saturated the same 40/40 as a tight, genuinely-matching role. Measured: 141
# of one user's jobs collapsed into the 24-29 band where junk and real matches
# were indistinguishable. Nothing is truncated; only the SCORE is made fair
# between a short ad and a long one (BM25's length normalisation).


class TestSkillLengthNormalisation:
    def _scorer(self):
        cfg = SearchConfig(
            job_titles=["ML Engineer"],
            primary_skills=["python", "pytorch", "kubernetes", "airflow"],
            secondary_skills=[],
            tertiary_skills=[],
            locations=["London"],
            core_domain_words={"ml"},
            supporting_role_words={"engineer"},
        )
        return JobScorer(cfg)

    def test_a_typical_length_ad_is_unchanged(self):
        s = self._scorer()
        # 4 skills in ~400 words (the measured prod baseline) -> full points.
        text = "python pytorch kubernetes airflow " + ("filler " * 396)
        assert s._skill_score(text) == 12

    def test_a_long_waffle_ad_is_discounted(self):
        """The measured offender: the same 4 skills sprinkled through a
        1,600-word corporate ad must NOT score like a tight 400-word match."""
        s = self._scorer()
        tight = "python pytorch kubernetes airflow " + ("filler " * 396)
        waffle = "python pytorch kubernetes airflow " + ("filler " * 1596)
        assert s._skill_score(waffle) < s._skill_score(tight)
        assert s._skill_score(waffle) <= 5  # 12 / 2.5

    def test_a_short_ad_is_never_boosted(self):
        """One-sided by design: thin descriptions are a DATA problem here
        (empty-description sources), so inflating them would reward missing
        information."""
        s = self._scorer()
        short = "python pytorch kubernetes airflow"
        assert s._skill_score(short) == 12, "short text must not be inflated"

    def test_normalisation_can_be_disabled(self):
        """B=0 restores byte-identical legacy behaviour (rule #18 shape)."""
        import src.services.skill_matcher as sm

        s = self._scorer()
        waffle = "python pytorch kubernetes airflow " + ("filler " * 1596)
        old_b = sm.SKILL_LENGTH_NORM_B
        try:
            sm.SKILL_LENGTH_NORM_B = 0.0
            assert s._skill_score(waffle) == 12
        finally:
            sm.SKILL_LENGTH_NORM_B = old_b

    def test_scorer_version_was_bumped_for_this_change(self):
        """A scoring change that does not bump SCORER_VERSION never reaches a
        single existing row — the exact failure PR #233 fixed."""
        from src.services.skill_matcher import SCORER_VERSION

        assert SCORER_VERSION >= 3
