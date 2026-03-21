"""Tests for controlled skill inference."""

import pytest

from src.profile.skill_graph import (
    SKILL_RELATIONSHIPS,
    infer_skills,
    get_inference_details,
)
from src.profile.keyword_generator import generate_search_config
from src.profile.models import UserProfile, CVData, UserPreferences


def test_graph_not_empty():
    assert len(SKILL_RELATIONSHIPS) > 50


def test_bidirectional_aws():
    """AWS -> Azure exists AND Azure -> AWS exists."""
    aws_related = {r[0].lower() for r in SKILL_RELATIONSHIPS.get("aws", [])}
    assert "azure" in aws_related
    azure_related = {r[0].lower() for r in SKILL_RELATIONSHIPS.get("azure", [])}
    assert "aws" in azure_related


def test_infer_aws_gives_azure_gcp():
    inferred = infer_skills(["AWS"])
    inferred_lower = {s.lower() for s in inferred}
    assert "azure" in inferred_lower
    assert "gcp" in inferred_lower
    assert "cloud computing" in inferred_lower


def test_infer_docker_gives_kubernetes():
    inferred = infer_skills(["Docker"])
    inferred_lower = {s.lower() for s in inferred}
    assert "kubernetes" in inferred_lower


def test_infer_react_gives_nextjs():
    inferred = infer_skills(["React"])
    inferred_lower = {s.lower() for s in inferred}
    assert "next.js" in inferred_lower


def test_excludes_existing_skills():
    """Should not infer skills already in the list."""
    inferred = infer_skills(["AWS", "Azure"])
    inferred_lower = {s.lower() for s in inferred}
    assert "aws" not in inferred_lower
    assert "azure" not in inferred_lower


def test_empty_input():
    assert infer_skills([]) == []


def test_unknown_skills():
    """Unknown skills should not cause errors."""
    result = infer_skills(["UnknownTechXYZ"])
    assert result == []


def test_threshold_filters():
    """Higher threshold should return fewer results."""
    low_threshold = infer_skills(["AWS"], threshold=0.5)
    high_threshold = infer_skills(["AWS"], threshold=0.9)
    assert len(low_threshold) >= len(high_threshold)


def test_threshold_zero_returns_all():
    result = infer_skills(["AWS"], threshold=0.0)
    assert len(result) > 0


def test_get_inference_details():
    details = get_inference_details(["Docker"])
    assert len(details) > 0
    for d in details:
        assert "skill" in d
        assert "confidence" in d
        assert "inferred_from" in d
        assert d["confidence"] >= 0.7


def test_infer_python_ecosystem():
    inferred = infer_skills(["Django"])
    inferred_lower = {s.lower() for s in inferred}
    assert "python" in inferred_lower
    assert "rest api" in inferred_lower


def test_integration_search_config_inferred_in_tertiary():
    """Inferred skills should appear in tertiary, not primary or secondary."""
    profile = UserProfile(
        cv_data=CVData(
            raw_text="I am an engineer",
            skills=["AWS", "Python", "Docker"],
            job_titles=["DevOps Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["DevOps Engineer"],
        ),
    )
    config = generate_search_config(profile)
    primary_lower = {s.lower() for s in config.primary_skills}
    secondary_lower = {s.lower() for s in config.secondary_skills}
    tertiary_lower = {s.lower() for s in config.tertiary_skills}

    # Azure should be inferred from AWS and placed in tertiary
    assert "azure" in tertiary_lower
    assert "azure" not in primary_lower
    assert "azure" not in secondary_lower

    # Kubernetes inferred from Docker
    assert "kubernetes" in tertiary_lower


# ── ESCO-derived skill relationship tests ──


class TestESCOSkillRelationships:
    """Tests for ESCO-derived cross-domain skill inference."""

    def test_graph_expanded(self):
        assert len(SKILL_RELATIONSHIPS) > 150

    def test_cissp_infers_infosec(self):
        inferred = infer_skills(["CISSP"])
        inferred_lower = {s.lower() for s in inferred}
        assert "information security" in inferred_lower

    def test_soc_infers_siem(self):
        inferred = infer_skills(["SOC"])
        inferred_lower = {s.lower() for s in inferred}
        assert "siem" in inferred_lower
        assert "incident response" in inferred_lower

    def test_nebosh_infers_health_safety(self):
        inferred = infer_skills(["NEBOSH"])
        inferred_lower = {s.lower() for s in inferred}
        assert "health and safety" in inferred_lower

    def test_tableau_infers_data_viz(self):
        inferred = infer_skills(["Tableau"])
        inferred_lower = {s.lower() for s in inferred}
        assert "data visualization" in inferred_lower
        assert "business intelligence" in inferred_lower

    def test_esg_infers_sustainability(self):
        inferred = infer_skills(["ESG"])
        inferred_lower = {s.lower() for s in inferred}
        assert "sustainability" in inferred_lower

    def test_haccp_infers_food_safety(self):
        inferred = infer_skills(["HACCP"])
        inferred_lower = {s.lower() for s in inferred}
        assert "food safety" in inferred_lower

    def test_hplc_infers_analytical_chemistry(self):
        inferred = infer_skills(["HPLC"])
        inferred_lower = {s.lower() for s in inferred}
        assert "analytical chemistry" in inferred_lower

    def test_cnc_infers_manufacturing(self):
        inferred = infer_skills(["CNC"])
        inferred_lower = {s.lower() for s in inferred}
        assert "manufacturing" in inferred_lower

    def test_rics_infers_surveying(self):
        inferred = infer_skills(["RICS"])
        inferred_lower = {s.lower() for s in inferred}
        assert "surveying" in inferred_lower

    def test_cips_infers_procurement(self):
        inferred = infer_skills(["CIPS"])
        inferred_lower = {s.lower() for s in inferred}
        assert "procurement" in inferred_lower

    def test_safeguarding_infers_child_protection(self):
        inferred = infer_skills(["Safeguarding"])
        inferred_lower = {s.lower() for s in inferred}
        assert "child protection" in inferred_lower

    def test_bidirectional_esg(self):
        """ESG -> Sustainability AND Sustainability -> ESG."""
        esg_related = {r[0].lower() for r in SKILL_RELATIONSHIPS.get("esg", [])}
        assert "sustainability" in esg_related
        sus_related = {r[0].lower() for r in SKILL_RELATIONSHIPS.get("sustainability", [])}
        assert "esg" in sus_related

    def test_rpa_infers_process_automation(self):
        inferred = infer_skills(["RPA"])
        inferred_lower = {s.lower() for s in inferred}
        assert "process automation" in inferred_lower

    def test_private_equity_infers_modelling(self):
        inferred = infer_skills(["Private Equity"])
        inferred_lower = {s.lower() for s in inferred}
        assert "financial modelling" in inferred_lower


def test_integration_inferred_in_relevance_keywords():
    """Inferred skills should also appear in relevance_keywords."""
    profile = UserProfile(
        cv_data=CVData(
            raw_text="I am an engineer",
            skills=["AWS"],
            job_titles=["Cloud Engineer"],
        ),
        preferences=UserPreferences(
            target_job_titles=["Cloud Engineer"],
        ),
    )
    config = generate_search_config(profile)
    rel_lower = {k.lower() for k in config.relevance_keywords}
    assert "azure" in rel_lower
