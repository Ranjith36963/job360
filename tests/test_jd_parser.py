from src.filters.jd_parser import detect_job_type, parse_jd, ParsedJD


def test_detect_full_time():
    assert detect_job_type("Full-time Python Developer role") == "Full-time"


def test_detect_full_time_no_hyphen():
    assert detect_job_type("This is a full time position") == "Full-time"


def test_detect_part_time():
    assert detect_job_type("Part-time office administrator") == "Part-time"


def test_detect_contract():
    assert detect_job_type("6-month contract Data Engineer") == "Contract"


def test_detect_contractor():
    assert detect_job_type("Looking for an experienced contractor") == "Contract"


def test_detect_permanent():
    assert detect_job_type("Permanent position with benefits") == "Permanent"


def test_detect_perm():
    assert detect_job_type("Perm role, competitive salary") == "Permanent"


def test_detect_fixed_term():
    assert detect_job_type("Fixed term contract, 12 months") == "Fixed Term"


def test_detect_ftc():
    assert detect_job_type("FTC covering maternity leave") == "Fixed Term"


def test_detect_freelance():
    assert detect_job_type("Freelance graphic designer needed") == "Freelance"


def test_detect_none():
    assert detect_job_type("Senior AI Engineer at DeepMind") == ""


def test_fixed_term_over_contract():
    """Fixed-term should take priority over contract (more specific)."""
    assert detect_job_type("Fixed-term contract position") == "Fixed Term"


def test_empty_string():
    assert detect_job_type("") == ""


# ── Structured JD parsing tests ───────────────────────────────────────


class TestParseJDSections:
    def test_extracts_required_from_section(self):
        jd = (
            "About the Role\n"
            "We are looking for a backend engineer.\n\n"
            "Requirements\n"
            "- 5+ years of experience with Python\n"
            "- Strong knowledge of Docker and Kubernetes\n"
            "- Experience with AWS or Azure\n\n"
            "Nice to Have\n"
            "- Experience with Kafka\n"
            "- Knowledge of Terraform\n"
        )
        result = parse_jd(jd)
        assert "Python" in result.required_skills
        assert "Docker" in result.required_skills
        assert "Kafka" in result.preferred_skills

    def test_extracts_preferred_from_desirable_section(self):
        jd = (
            "Essential\n"
            "- Python, SQL, PostgreSQL\n\n"
            "Desirable\n"
            "- React, TypeScript\n"
        )
        result = parse_jd(jd)
        assert "Python" in result.required_skills
        assert "SQL" in result.required_skills
        assert "React" in result.preferred_skills

    def test_extracts_experience_years(self):
        jd = "We need someone with 5+ years of experience in software engineering."
        result = parse_jd(jd)
        assert result.experience_years == 5

    def test_extracts_experience_years_alternative(self):
        jd = "Minimum 3 years of relevant experience required."
        result = parse_jd(jd)
        assert result.experience_years == 3

    def test_no_experience_years(self):
        jd = "Junior role, no experience required. Python and SQL needed."
        result = parse_jd(jd)
        assert result.experience_years is None

    def test_extracts_qualifications(self):
        jd = (
            "Requirements\n"
            "- BSc in Computer Science or related field\n"
            "- ACCA or CIMA qualification preferred\n"
        )
        result = parse_jd(jd)
        assert "BSc" in result.qualifications or "ACCA" in result.qualifications

    def test_detects_salary_mention(self):
        jd = "Salary: £65,000 - £85,000 per annum. Python and AWS required."
        result = parse_jd(jd)
        assert result.salary_mentioned is True

    def test_no_salary_mention(self):
        jd = "Great opportunity for a Python developer. Docker experience needed."
        result = parse_jd(jd)
        assert result.salary_mentioned is False

    def test_seniority_senior(self):
        jd = "Senior Software Engineer - 5+ years experience with Python."
        result = parse_jd(jd)
        assert result.seniority_signal == "senior"

    def test_seniority_entry(self):
        jd = "Graduate Software Developer. No experience required. Training provided."
        result = parse_jd(jd)
        assert result.seniority_signal == "entry"

    def test_seniority_lead(self):
        jd = "Head of Engineering. Leading a team of 20 engineers."
        result = parse_jd(jd)
        assert result.seniority_signal == "lead"

    def test_empty_description(self):
        result = parse_jd("")
        assert result.required_skills == []
        assert result.preferred_skills == []
        assert result.experience_years is None

    def test_short_description(self):
        result = parse_jd("Short desc")
        assert result == ParsedJD()


class TestInlineClassification:
    def test_inline_required_signal(self):
        jd = (
            "We are looking for someone who must have experience with "
            "Python and Docker. Knowledge of React is desirable."
        )
        result = parse_jd(jd)
        assert "Python" in result.required_skills
        assert "Docker" in result.required_skills

    def test_inline_preferred_signal(self):
        jd = (
            "Essential: Python, SQL. "
            "Nice to have: Experience with Kubernetes and Terraform."
        )
        result = parse_jd(jd)
        assert "Python" in result.required_skills

    def test_no_signals_defaults_to_required(self):
        jd = "This role needs Python, Docker, and AWS expertise for building services."
        result = parse_jd(jd)
        assert "Python" in result.required_skills
        assert "Docker" in result.required_skills


class TestParseJDRealWorld:
    def test_nhs_healthcare_jd(self):
        jd = (
            "Band 6 Clinical Nurse Specialist\n\n"
            "Requirements\n"
            "- Registered Nurse (RGN) with active NMC registration\n"
            "- Experience within NHS acute care settings\n"
            "- CQC compliance knowledge\n\n"
            "Desirable\n"
            "- MSc in Clinical Practice\n"
            "- PRINCE2 qualification\n"
        )
        result = parse_jd(jd)
        assert "NHS" in result.required_skills
        assert "CQC" in result.required_skills
        assert any("MSc" in q for q in result.qualifications)

    def test_finance_jd(self):
        jd = (
            "Financial Analyst - £55,000 per annum\n\n"
            "Must Have\n"
            "- ACCA or CIMA qualified\n"
            "- 3+ years experience in FP&A\n"
            "- Advanced Excel and SAP\n\n"
            "Nice to Have\n"
            "- Power BI or Tableau\n"
        )
        result = parse_jd(jd)
        assert result.salary_mentioned is True
        assert result.experience_years == 3
        assert "Excel" in result.required_skills
        assert "SAP" in result.required_skills

    def test_tech_jd_with_responsibilities(self):
        jd = (
            "Senior Data Engineer\n\n"
            "What you'll do\n"
            "- Design and build data pipelines\n"
            "- Optimize ETL workflows\n\n"
            "Requirements\n"
            "- 5+ years of experience with Python and SQL\n"
            "- Kubernetes, Docker, Airflow\n"
            "- AWS or GCP cloud experience\n"
        )
        result = parse_jd(jd)
        assert result.experience_years == 5
        assert "Python" in result.required_skills
        assert result.seniority_signal == "senior"
        assert len(result.responsibilities) > 0


# ── TDD: Fix 5 — Salary extraction from JD text ──


class TestSalaryExtraction:
    def test_gbp_range_extracted(self):
        """£60,000-£80,000 should extract as salary_min=60000, salary_max=80000."""
        jd = "Salary: £60,000 - £80,000 per annum. Python developer role."
        result = parse_jd(jd)
        assert result.salary_min == 60000, f"Expected 60000, got {result.salary_min}"
        assert result.salary_max == 80000, f"Expected 80000, got {result.salary_max}"

    def test_gbp_single_value(self):
        """£45,000 per annum should extract as salary_min=45000."""
        jd = "Offering £45,000 per annum plus benefits."
        result = parse_jd(jd)
        assert result.salary_min == 45000

    def test_k_notation(self):
        """£50k-£70k should extract correctly."""
        jd = "Competitive salary of £50k - £70k depending on experience."
        result = parse_jd(jd)
        assert result.salary_min == 50000, f"Expected 50000, got {result.salary_min}"
        assert result.salary_max == 70000, f"Expected 70000, got {result.salary_max}"

    def test_no_salary_returns_none(self):
        """JD without salary mention should return None."""
        jd = "We are looking for a Python developer to join our team."
        result = parse_jd(jd)
        assert result.salary_min is None
        assert result.salary_max is None

    def test_salary_mentioned_still_works(self):
        """salary_mentioned bool should still work alongside extraction."""
        jd = "Salary: £60,000 - £80,000 per annum."
        result = parse_jd(jd)
        assert result.salary_mentioned is True
