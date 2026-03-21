from src.profile.models import UserProfile, UserPreferences, CVData
from src.profile.domain_detector import detect_domains


def _make_profile(titles=None, skills=None, cv_skills=None, cv_text=""):
    return UserProfile(
        cv_data=CVData(
            raw_text=cv_text,
            skills=cv_skills or [],
            job_titles=[],
        ),
        preferences=UserPreferences(
            target_job_titles=titles or [],
            additional_skills=skills or [],
        ),
    )


def test_detect_technology():
    profile = _make_profile(
        titles=["Software Engineer"],
        skills=["Python", "Docker", "Kubernetes"],
    )
    domains = detect_domains(profile)
    assert "Technology" in domains


def test_detect_data_ai():
    profile = _make_profile(
        titles=["AI Engineer"],
        skills=["PyTorch", "TensorFlow", "LangChain", "RAG"],
    )
    domains = detect_domains(profile)
    assert "Data & AI" in domains


def test_detect_healthcare():
    profile = _make_profile(
        titles=["Clinical Nurse Specialist"],
        skills=["NHS", "Patient Care", "CQC"],
    )
    domains = detect_domains(profile)
    assert "Healthcare" in domains


def test_detect_finance():
    profile = _make_profile(
        titles=["Financial Analyst"],
        skills=["ACCA", "IFRS", "FP&A", "Budgeting"],
    )
    domains = detect_domains(profile)
    assert "Finance" in domains


def test_detect_multi_domain():
    """Users with cross-domain skills should get multiple domains."""
    profile = _make_profile(
        titles=["Data Engineer"],
        skills=["Python", "Docker", "Kubernetes", "Spark", "Airflow", "Snowflake"],
    )
    domains = detect_domains(profile)
    assert "Technology" in domains
    assert "Data & AI" in domains


def test_detect_empty_profile():
    profile = _make_profile()
    domains = detect_domains(profile)
    assert domains == []


def test_detect_from_cv_text():
    """Domain can be detected from raw CV text even without structured skills."""
    profile = _make_profile(
        titles=["Marketing Manager"],
        cv_text="Experience with SEO, PPC, Google Analytics, and content marketing strategies.",
    )
    domains = detect_domains(profile)
    assert "Marketing" in domains


def test_detect_legal():
    profile = _make_profile(
        titles=["Corporate Solicitor"],
        skills=["GDPR", "M&A", "Corporate Law", "Due Diligence"],
    )
    domains = detect_domains(profile)
    assert "Legal" in domains


def test_detect_education():
    profile = _make_profile(
        titles=["Secondary Teacher"],
        skills=["QTS", "Safeguarding", "Curriculum"],
    )
    domains = detect_domains(profile)
    assert "Education" in domains
