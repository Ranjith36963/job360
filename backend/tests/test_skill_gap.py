"""Skill-gap: given the user's skills and a job's required skills, split into
matched / missing / transferable. Powers the 'Skill Analysis' panel on the job
page (was an empty shell — nothing computed it). Deterministic, rapidfuzz for
near-matches."""

from src.services.skill_gap import compute_skill_gap


def test_exact_matches_and_missing():
    gap = compute_skill_gap(["Python", "AWS", "Docker"], ["Python", "AWS", "Kubernetes"])
    assert set(gap["matched"]) == {"Python", "AWS"}
    assert gap["missing"] == ["Kubernetes"]


def test_case_insensitive_match():
    gap = compute_skill_gap(["python"], ["Python"])
    assert gap["matched"] == ["Python"]
    assert gap["missing"] == []


def test_fuzzy_near_match_counts_as_matched():
    # "PostgreSQL" vs "Postgres" — near-identical, should count as having it.
    gap = compute_skill_gap(["Postgres"], ["PostgreSQL"])
    assert gap["matched"] == ["PostgreSQL"]


def test_transferable_shares_a_token_with_a_missing_requirement():
    # User has "React Native", job wants plain "React": not an exact match, but
    # clearly adjacent → surfaced as transferable, and React stays missing.
    gap = compute_skill_gap(["React Native"], ["React"])
    assert gap["missing"] == ["React"]
    assert "React Native" in gap["transferable"]


def test_empty_required_is_all_empty():
    gap = compute_skill_gap(["Python"], [])
    assert gap["matched"] == [] and gap["missing"] == [] and gap["transferable"] == []


def test_no_user_skills_all_missing():
    gap = compute_skill_gap([], ["Python", "AWS"])
    assert set(gap["missing"]) == {"Python", "AWS"}
    assert gap["matched"] == []
