"""Save/load user profile to data/user_profile.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from pathlib import Path

from src.config.settings import DATA_DIR
from src.profile.models import (
    CVData, UserPreferences, UserProfile,
    WorkExperience, StructuredEducation, Project,
)

logger = logging.getLogger("job360.profile.storage")

PROFILE_PATH = DATA_DIR / "user_profile.json"


def save_profile(profile: UserProfile) -> Path:
    """Save a UserProfile to JSON."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    PROFILE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info(f"Profile saved to {PROFILE_PATH}")
    return PROFILE_PATH


def load_profile() -> UserProfile | None:
    """Load a UserProfile from JSON, or return None if not found."""
    if not PROFILE_PATH.exists():
        return None
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        cv_fields = {f.name for f in fields(CVData)}
        cv_data = {k: v for k, v in data.get("cv_data", {}).items() if k in cv_fields}
        # Reconstruct nested dataclass lists from JSON dicts
        cv_data["work_experiences"] = [
            WorkExperience(**e) for e in cv_data.get("work_experiences", [])
        ]
        cv_data["structured_education"] = [
            StructuredEducation(**e) for e in cv_data.get("structured_education", [])
        ]
        cv_data["projects"] = [
            Project(**e) for e in cv_data.get("projects", [])
        ]
        cv = CVData(**cv_data)
        pref_fields = {f.name for f in fields(UserPreferences)}
        pref_data = {k: v for k, v in data.get("preferences", {}).items() if k in pref_fields}
        prefs = UserPreferences(**pref_data)
        return UserProfile(cv_data=cv, preferences=prefs)
    except Exception as e:
        logger.warning(f"Failed to load profile: {e}")
        return None


def profile_exists() -> bool:
    """Check if a user profile file exists."""
    return PROFILE_PATH.exists()
