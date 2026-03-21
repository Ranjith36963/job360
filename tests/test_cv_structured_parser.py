"""Tests for structured CV parsing — work experience, education, projects, seniority."""

from src.profile.models import CVData, WorkExperience, StructuredEducation, Project
from src.profile.cv_structured_parser import (
    _parse_date,
    _duration_months,
    _parse_experience_section,
    _parse_education_section,
    _parse_projects_section,
    _compute_seniority,
    _extract_inline_skills,
    enhance_cv_data,
)


# ── Date parsing ──────────────────────────────────────────────────────


class TestDateParsing:
    def test_month_year(self):
        assert _parse_date("January 2020") == (2020, 1)

    def test_short_month_year(self):
        assert _parse_date("Mar 2022") == (2022, 3)

    def test_mm_slash_yyyy(self):
        assert _parse_date("06/2019") == (2019, 6)

    def test_yyyy_mm(self):
        assert _parse_date("2021-11") == (2021, 11)

    def test_bare_year(self):
        result = _parse_date("2020")
        assert result is not None
        assert result[0] == 2020

    def test_present(self):
        result = _parse_date("Present")
        assert result is not None
        assert result[0] >= 2024

    def test_current(self):
        result = _parse_date("Current")
        assert result is not None

    def test_empty_string(self):
        assert _parse_date("") is None

    def test_garbage(self):
        assert _parse_date("not a date at all") is None


class TestDurationMonths:
    def test_one_year(self):
        assert _duration_months((2020, 1), (2021, 1)) == 12

    def test_partial_year(self):
        assert _duration_months((2020, 6), (2021, 3)) == 9

    def test_same_month(self):
        assert _duration_months((2020, 6), (2020, 6)) == 0

    def test_none_start(self):
        assert _duration_months(None, (2021, 1)) == 0

    def test_none_end(self):
        assert _duration_months((2020, 1), None) == 0


# ── Experience parsing ────────────────────────────────────────────────


class TestExperienceParsing:
    def test_title_at_company(self):
        text = (
            "Software Engineer at Google\n"
            "Jan 2020 - Dec 2022\n"
            "Built microservices using Python and Docker.\n"
        )
        results = _parse_experience_section(text)
        assert len(results) == 1
        assert results[0].title == "Software Engineer"
        assert results[0].company == "Google"
        assert results[0].duration_months == 35  # Jan 2020 to Dec 2022

    def test_title_dash_company(self):
        text = (
            "Data Scientist - Amazon\n"
            "Mar 2021 - Present\n"
            "Applied machine learning to product recommendations.\n"
        )
        results = _parse_experience_section(text)
        assert len(results) == 1
        assert results[0].title == "Data Scientist"
        assert results[0].company == "Amazon"
        assert results[0].duration_months > 0

    def test_multiple_entries(self):
        text = (
            "Senior Engineer at Meta\n"
            "Jun 2022 - Present\n"
            "Leading the AI team.\n"
            "\n"
            "Software Engineer at Startup\n"
            "Jan 2019 - May 2022\n"
            "Full-stack development with React and Node.js.\n"
        )
        results = _parse_experience_section(text)
        assert len(results) == 2
        assert results[0].title == "Senior Engineer"
        assert results[1].title == "Software Engineer"

    def test_inline_skills_extracted(self):
        text = (
            "Data Engineer at Spotify\n"
            "2020 - 2023\n"
            "Built data pipelines using Python, Spark, and Airflow.\n"
            "Deployed on AWS with Docker and Kubernetes.\n"
        )
        results = _parse_experience_section(text)
        assert len(results) == 1
        skills = results[0].skills_used
        assert "Python" in skills
        assert "Spark" in skills or "AWS" in skills

    def test_empty_text(self):
        assert _parse_experience_section("") == []

    def test_short_text(self):
        assert _parse_experience_section("Too short") == []


# ── Education parsing ─────────────────────────────────────────────────


class TestEducationParsing:
    def test_bsc_degree(self):
        text = "BSc Computer Science, University of Manchester, 2018, First Class"
        results = _parse_education_section(text)
        assert len(results) == 1
        assert results[0].degree == "BSc"
        assert results[0].year == 2018
        assert "First" in results[0].grade

    def test_msc_degree(self):
        text = "MSc Data Science\nUniversity of Edinburgh\n2020\nDistinction"
        results = _parse_education_section(text)
        assert len(results) >= 1
        assert any(e.degree == "MSc" for e in results)

    def test_phd(self):
        text = "PhD in Machine Learning, University of Oxford, 2023"
        results = _parse_education_section(text)
        assert len(results) == 1
        assert results[0].degree == "PhD"
        assert results[0].year == 2023

    def test_pgce(self):
        text = "PGCE Secondary Education, University of Bristol, 2019"
        results = _parse_education_section(text)
        assert len(results) == 1
        assert results[0].degree == "PGCE"

    def test_multiple_qualifications(self):
        text = (
            "MSc Artificial Intelligence, Imperial College, 2021, Distinction\n"
            "\n"
            "BSc Mathematics, UCL, 2019, 2:1\n"
        )
        results = _parse_education_section(text)
        assert len(results) == 2

    def test_a_levels(self):
        text = "A-Levels: Maths (A*), Physics (A), Chemistry (A)"
        results = _parse_education_section(text)
        assert len(results) >= 1
        assert any("Level" in e.degree or "A" in e.degree for e in results)

    def test_empty_text(self):
        assert _parse_education_section("") == []

    def test_institution_extraction(self):
        text = "BSc Computer Science at University of Leeds 2020"
        results = _parse_education_section(text)
        assert len(results) == 1
        assert "University" in results[0].institution


# ── Project parsing ───────────────────────────────────────────────────


class TestProjectParsing:
    def test_basic_project(self):
        text = (
            "Job Search Automation\n"
            "Built a Python tool that scrapes 48 job boards and scores matches.\n"
            "Uses Docker, PostgreSQL, and React for the dashboard.\n"
        )
        results = _parse_projects_section(text)
        assert len(results) >= 1
        assert results[0].name == "Job Search Automation"
        assert "Python" in results[0].technologies

    def test_project_with_url(self):
        text = (
            "Portfolio Website\n"
            "https://example.com/portfolio\n"
            "Built with React and TypeScript.\n"
        )
        results = _parse_projects_section(text)
        assert len(results) >= 1
        assert "https://example.com/portfolio" in results[0].url

    def test_empty_text(self):
        assert _parse_projects_section("") == []


# ── Seniority computation ────────────────────────────────────────────


class TestSeniority:
    def test_entry_from_title(self):
        assert _compute_seniority(12, ["Junior Developer"]) == "entry"

    def test_senior_from_title(self):
        assert _compute_seniority(24, ["Senior Software Engineer"]) == "senior"

    def test_lead_from_title(self):
        assert _compute_seniority(36, ["Head of Engineering"]) == "lead"

    def test_executive_from_title(self):
        assert _compute_seniority(60, ["CTO"]) == "executive"

    def test_mid_from_experience_only(self):
        assert _compute_seniority(36, []) == "mid"

    def test_entry_from_experience_only(self):
        assert _compute_seniority(12, []) == "entry"

    def test_senior_from_experience_only(self):
        assert _compute_seniority(84, []) == "senior"

    def test_title_overrides_experience(self):
        """Even with 10 years, a 'Junior' title signals entry level."""
        assert _compute_seniority(120, ["Junior Analyst"]) == "entry"

    def test_highest_seniority_wins(self):
        """Multiple titles — highest seniority wins."""
        assert _compute_seniority(
            60, ["Junior Developer", "Senior Engineer"]
        ) == "senior"


# ── Inline skill extraction ──────────────────────────────────────────


class TestInlineSkills:
    def test_extracts_python(self):
        skills = _extract_inline_skills("Built services in Python and Docker")
        assert "Python" in skills
        assert "Docker" in skills

    def test_extracts_nhs(self):
        skills = _extract_inline_skills("Worked within NHS standards and CQC compliance")
        assert "NHS" in skills
        assert "CQC" in skills

    def test_empty_text(self):
        assert _extract_inline_skills("") == []


# ── Integration: enhance_cv_data ──────────────────────────────────────


class TestEnhanceCVData:
    def test_full_enhancement(self):
        cv = CVData(
            raw_text="test",
            job_titles=["Software Engineer"],
            skills=["Python", "Docker"],
        )
        sections = {
            "experience": (
                "Software Engineer at Google\n"
                "Jan 2019 - Dec 2023\n"
                "Built microservices using Python and Docker.\n"
            ),
            "education": (
                "MSc Computer Science, University of Oxford, 2018, Distinction\n"
            ),
        }
        result = enhance_cv_data(cv, sections)
        assert len(result.work_experiences) == 1
        assert result.work_experiences[0].duration_months > 0
        assert len(result.structured_education) == 1
        assert result.structured_education[0].degree == "MSc"
        assert result.total_experience_months > 0
        assert result.computed_seniority != ""

    def test_empty_sections(self):
        cv = CVData(raw_text="test")
        result = enhance_cv_data(cv, {})
        assert result.work_experiences == []
        assert result.structured_education == []
        assert result.total_experience_months == 0

    def test_seniority_from_experience_and_title(self):
        cv = CVData(raw_text="test", job_titles=["Senior Data Engineer"])
        sections = {
            "experience": (
                "Senior Data Engineer at Spotify\n"
                "2018 - 2023\n"
                "Led the data platform team.\n"
            ),
        }
        result = enhance_cv_data(cv, sections)
        assert result.computed_seniority == "senior"
