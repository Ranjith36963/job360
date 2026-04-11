"""Save/load user profile to data/user_profile.json."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, fields
from pathlib import Path

from src.core.settings import DATA_DIR
from src.services.profile.models import CVData, UserPreferences, UserProfile

logger = logging.getLogger("job360.profile.storage")

PROFILE_PATH = DATA_DIR / "user_profile.json"


def save_profile(profile: UserProfile) -> Path:
    """Save a UserProfile to JSON."""
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = asdict(profile)
    PROFILE_PATH.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
    logger.info("Profile saved to %s", PROFILE_PATH)
    return PROFILE_PATH


def load_profile() -> UserProfile | None:
    """Load a UserProfile from JSON, or return None if not found."""
    if not PROFILE_PATH.exists():
        logger.debug("No profile file at %s", PROFILE_PATH)
        return None
    try:
        data = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
        cv_fields = {f.name for f in fields(CVData)}
        cv_data = {k: v for k, v in data.get("cv_data", {}).items() if k in cv_fields}
        cv = CVData(**cv_data)
        pref_fields = {f.name for f in fields(UserPreferences)}
        pref_data = {k: v for k, v in data.get("preferences", {}).items() if k in pref_fields}
        prefs = UserPreferences(**pref_data)
        return UserProfile(cv_data=cv, preferences=prefs)
    except json.JSONDecodeError as e:
        logger.error("Corrupt profile at %s — JSON parse error: %s", PROFILE_PATH, e)
        return None
    except Exception as e:
        logger.error("Failed to load profile at %s: %s", PROFILE_PATH, e)
        return None


def profile_exists() -> bool:
    """Check if a user profile file exists."""
    return PROFILE_PATH.exists()
