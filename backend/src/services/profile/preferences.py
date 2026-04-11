"""Validate and normalize user preferences form data."""

from __future__ import annotations

from src.services.profile.models import UserPreferences


def _split_and_clean(value: str) -> list[str]:
    """Split a comma-separated string and strip whitespace."""
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


def validate_preferences(data: dict) -> UserPreferences:
    """Convert a raw form dict into a validated UserPreferences."""
    # Handle both list and comma-separated string inputs
    def to_list(key: str) -> list[str]:
        val = data.get(key, [])
        if isinstance(val, str):
            return _split_and_clean(val)
        if isinstance(val, list):
            return [str(v).strip() for v in val if str(v).strip()]
        return []

    return UserPreferences(
        target_job_titles=to_list("target_job_titles"),
        additional_skills=to_list("additional_skills"),
        excluded_skills=to_list("excluded_skills"),
        preferred_locations=to_list("preferred_locations"),
        industries=to_list("industries"),
        salary_min=data.get("salary_min"),
        salary_max=data.get("salary_max"),
        work_arrangement=data.get("work_arrangement", ""),
        experience_level=data.get("experience_level", ""),
        negative_keywords=to_list("negative_keywords"),
        about_me=data.get("about_me", ""),
    )


def merge_cv_and_preferences(
    cv_skills: list[str],
    cv_titles: list[str],
    prefs: UserPreferences,
) -> UserPreferences:
    """Merge CV-extracted data with user preferences. Preferences take priority."""
    # Combine titles: user prefs first, then CV-extracted
    merged_titles = list(prefs.target_job_titles)
    seen_titles = {t.lower() for t in merged_titles}
    for title in cv_titles:
        if title.lower() not in seen_titles:
            merged_titles.append(title)
            seen_titles.add(title.lower())

    # Combine skills: user prefs first, then CV skills, minus excluded
    excluded = {s.lower() for s in prefs.excluded_skills}
    merged_skills = []
    seen_skills = set()
    for skill in list(prefs.additional_skills) + cv_skills:
        key = skill.lower()
        if key not in seen_skills and key not in excluded:
            merged_skills.append(skill)
            seen_skills.add(key)

    return UserPreferences(
        target_job_titles=merged_titles,
        additional_skills=merged_skills,
        excluded_skills=prefs.excluded_skills,
        preferred_locations=prefs.preferred_locations,
        industries=prefs.industries,
        salary_min=prefs.salary_min,
        salary_max=prefs.salary_max,
        work_arrangement=prefs.work_arrangement,
        experience_level=prefs.experience_level,
        negative_keywords=prefs.negative_keywords,
        about_me=prefs.about_me,
        github_username=prefs.github_username,
    )
