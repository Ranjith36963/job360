from datetime import datetime, timezone
from src.models import Job
from src.filters.skill_matcher import score_job, check_visa_flag


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
