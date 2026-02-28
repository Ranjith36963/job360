from datetime import datetime, timezone, timedelta
from src.models import Job
from src.filters.skill_matcher import score_job, check_visa_flag, _recency_score


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
    score = score_job(job)
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
    job_match = _make_job(title="ML Engineer", description="Python role")
    job_no_match = _make_job(title="Chef", description="Python role")
    assert score_job(job_match) > score_job(job_no_match)


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
    job_few = _make_job(description="Python developer role")
    job_many = _make_job(
        description="Python PyTorch TensorFlow LangChain RAG LLM NLP Deep Learning AWS Docker"
    )
    assert score_job(job_many) > score_job(job_few)


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
    score = score_job(job)
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
