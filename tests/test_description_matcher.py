"""Tests for synonym-enhanced skill matching."""

import pytest
from datetime import datetime, timezone

from src.filters.description_matcher import (
    SYNONYM_GROUPS,
    SKILL_SYNONYMS,
    get_synonyms,
    text_contains_with_synonyms,
)
from src.filters.skill_matcher import JobScorer
from src.models import Job
from src.profile.models import SearchConfig


def test_ml_matches_machine_learning():
    assert text_contains_with_synonyms("Experience with Machine Learning required", "ML")


def test_machine_learning_matches_ml():
    assert text_contains_with_synonyms("ML engineer needed", "Machine Learning")


def test_js_matches_javascript():
    assert text_contains_with_synonyms("JavaScript developer", "JS")


def test_javascript_matches_js():
    assert text_contains_with_synonyms("Strong JS skills", "JavaScript")


def test_k8s_matches_kubernetes():
    assert text_contains_with_synonyms("Kubernetes experience required", "K8s")


def test_kubernetes_matches_k8s():
    assert text_contains_with_synonyms("Deploy to K8s clusters", "Kubernetes")


def test_aws_matches_amazon_web_services():
    assert text_contains_with_synonyms("Amazon Web Services certification", "AWS")


def test_no_match_returns_false():
    assert not text_contains_with_synonyms("We use PostgreSQL and Redis", "MongoDB")


def test_word_boundary_ml_not_html():
    """ML should not match inside HTML."""
    assert not text_contains_with_synonyms("Write HTML templates", "ML")


def test_case_insensitive():
    assert text_contains_with_synonyms("python developer", "Python")
    assert text_contains_with_synonyms("PYTHON developer", "python")


def test_get_synonyms():
    syns = get_synonyms("ML")
    assert "machine learning" in syns


def test_get_synonyms_unknown():
    assert get_synonyms("UnknownTechXYZ") == set()


def test_synonym_groups_not_empty():
    assert len(SYNONYM_GROUPS) > 50


def test_postgres_synonym():
    assert text_contains_with_synonyms("Using Postgres for data storage", "PostgreSQL")
    assert text_contains_with_synonyms("PostgreSQL database", "Postgres")


# ── ESCO synonym group tests ──


class TestESCOSynonyms:
    """Tests for ESCO-derived synonym groups across professional domains."""

    def test_synonym_count_expanded(self):
        assert len(SYNONYM_GROUPS) > 300

    def test_cissp(self):
        assert text_contains_with_synonyms("CISSP certified", "Certified Information Systems Security Professional")

    def test_soc(self):
        assert text_contains_with_synonyms("SOC analyst required", "Security Operations Centre")

    def test_pentest(self):
        assert text_contains_with_synonyms("Pen Testing skills", "Penetration Testing")

    def test_nebosh(self):
        assert text_contains_with_synonyms("NEBOSH qualified", "National Examination Board in Occupational Safety and Health")

    def test_cscs(self):
        assert text_contains_with_synonyms("CSCS card required", "Construction Skills Certification Scheme")

    def test_wms(self):
        assert text_contains_with_synonyms("WMS experience preferred", "Warehouse Management System")

    def test_hplc(self):
        assert text_contains_with_synonyms("HPLC method development", "High Performance Liquid Chromatography")

    def test_esg(self):
        assert text_contains_with_synonyms("ESG reporting", "Environmental Social and Governance")

    def test_dbs(self):
        assert text_contains_with_synonyms("DBS check required", "Disclosure and Barring Service")

    def test_cips(self):
        assert text_contains_with_synonyms("CIPS qualified", "Chartered Institute of Procurement and Supply")

    def test_sop(self):
        assert text_contains_with_synonyms("SOP compliance", "Standard Operating Procedure")

    def test_saas(self):
        assert text_contains_with_synonyms("SaaS platform", "Software as a Service")

    def test_fx(self):
        assert text_contains_with_synonyms("FX trading", "Foreign Exchange")
        assert text_contains_with_synonyms("Forex experience", "Foreign Exchange")

    def test_eyfs(self):
        assert text_contains_with_synonyms("EYFS curriculum", "Early Years Foundation Stage")

    def test_cnc(self):
        assert text_contains_with_synonyms("CNC machining", "Computer Numerical Control")

    def test_hazop(self):
        assert text_contains_with_synonyms("HAZOP study", "Hazard and Operability Study")

    def test_nmc(self):
        assert text_contains_with_synonyms("NMC registered", "Nursing and Midwifery Council")

    def test_b2b(self):
        assert text_contains_with_synonyms("B2B sales", "Business to Business")


def test_integration_scorer_with_synonyms():
    """JobScorer should score well when description uses abbreviations."""
    config = SearchConfig(
        job_titles=["ML Engineer"],
        primary_skills=["Machine Learning", "Python"],
        secondary_skills=["Kubernetes"],
        tertiary_skills=["PostgreSQL"],
        relevance_keywords=["ml", "python", "kubernetes", "postgresql"],
        negative_title_keywords=[],
        locations=["London"],
        visa_keywords=[],
        core_domain_words={"ml"},
        supporting_role_words={"engineer"},
        search_queries=[],
    )
    scorer = JobScorer(config)
    # Description uses abbreviations: ML, K8s, Postgres — synonyms should match
    job = Job(
        title="ML Engineer",
        company="TechCo",
        apply_url="https://example.com",
        source="test",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK",
        description="We need an ML expert with K8s deployment skills and Postgres experience. Python required.",
    )
    score = scorer.score(job)
    # Should get title match (40) + primary ML (3) + primary Python (3) + secondary K8s (2) + tertiary Postgres (1) + location (10) + recency (10)
    assert score >= 60
