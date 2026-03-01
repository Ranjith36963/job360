"""Shared time-bucketing utilities for dashboard, CLI, and email views."""

import re
from datetime import datetime, timezone, timedelta
from typing import Optional

import humanize

from src.config.keywords import PRIMARY_SKILLS, SECONDARY_SKILLS, TERTIARY_SKILLS

# ---------------------------------------------------------------------------
# Bucket definitions: (label, emoji_unicode, emoji_rich, max_hours, css_class)
# ---------------------------------------------------------------------------
BUCKETS = [
    ("Last 24 Hours", "\U0001f534", "[red]\u25cf[/red]", 24, "bucket-24h"),
    ("24 \u2013 48 Hours", "\U0001f7e0", "[dark_orange]\u25cf[/dark_orange]", 48, "bucket-48h"),
    ("48 \u2013 72 Hours", "\U0001f7e1", "[yellow]\u25cf[/yellow]", 72, "bucket-72h"),
    ("3 \u2013 7 Days", "\U0001f535", "[blue]\u25cf[/blue]", 168, "bucket-7d"),
]

# Date formats to try (reuses list from src/main.py:_format_date)
_DATE_FORMATS = [
    "%Y-%m-%dT%H:%M:%S%z",
    "%Y-%m-%dT%H:%M:%S.%f%z",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%dT%H:%M:%S.%f",
    "%Y-%m-%d",
    "%d/%m/%Y",
]


def parse_date_safe(date_str: str) -> Optional[datetime]:
    """Parse an ISO/multi-format date string into a timezone-aware datetime.

    Returns None if parsing fails.
    """
    if not date_str or not isinstance(date_str, str):
        return None
    date_str = date_str.strip()
    for fmt in _DATE_FORMATS:
        try:
            dt = datetime.strptime(date_str, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, AttributeError):
            continue
    return None


def get_job_age_hours(date_found: str, first_seen: str = "") -> float:
    """Return job age in hours. Falls back to first_seen if date_found is unparseable.

    Returns 999.0 for unknown dates.
    """
    now = datetime.now(timezone.utc)
    dt = parse_date_safe(date_found)
    if dt is None and first_seen:
        dt = parse_date_safe(first_seen)
    if dt is None:
        return 999.0
    delta = now - dt
    return max(delta.total_seconds() / 3600, 0)


def assign_bucket(age_hours: float) -> Optional[int]:
    """Return bucket index 0-3 based on age_hours, or None if > 7 days."""
    for i, (_, _, _, max_h, _) in enumerate(BUCKETS):
        if age_hours <= max_h:
            return i
    return None


def bucket_jobs(jobs: list[dict], min_score: int = 30) -> dict[int, list[dict]]:
    """Group job dicts into 4 time buckets, sorted by score DESC within each.

    Jobs older than 7 days or below min_score are excluded.
    """
    buckets: dict[int, list[dict]] = {i: [] for i in range(4)}
    for job in jobs:
        score = job.get("match_score", 0)
        if score < min_score:
            continue
        age = get_job_age_hours(
            job.get("date_found", ""),
            job.get("first_seen", ""),
        )
        idx = assign_bucket(age)
        if idx is not None:
            buckets[idx].append(job)
    for idx in buckets:
        buckets[idx].sort(key=lambda j: j.get("match_score", 0), reverse=True)
    return buckets


def bucket_summary_counts(bucketed: dict[int, list[dict]]) -> dict:
    """Return summary counts for bucketed jobs."""
    total = sum(len(v) for v in bucketed.values())
    return {
        "last_24h": len(bucketed.get(0, [])),
        "24_48h": len(bucketed.get(1, [])),
        "48_72h": len(bucketed.get(2, [])),
        "3_7d": len(bucketed.get(3, [])),
        "total": total,
    }


def format_relative_time(date_str: str) -> str:
    """Return human-readable relative time like '2 hours ago', 'Yesterday'."""
    dt = parse_date_safe(date_str)
    if dt is None:
        return "Unknown"
    now = datetime.now(timezone.utc)
    delta = now - dt
    if delta.total_seconds() < 0:
        return "Just now"
    return humanize.naturaltime(delta)


def extract_matched_skills(text: str) -> dict[str, list[str]]:
    """Extract matched skills from text using keyword lists.

    Returns dict with keys 'primary', 'secondary', 'tertiary'.
    """
    if not text:
        return {"primary": [], "secondary": [], "tertiary": []}
    text_lower = text.lower()
    result = {"primary": [], "secondary": [], "tertiary": []}
    for skill in PRIMARY_SKILLS:
        if skill.lower() in text_lower:
            result["primary"].append(skill)
    for skill in SECONDARY_SKILLS:
        if skill.lower() in text_lower:
            result["secondary"].append(skill)
    for skill in TERTIARY_SKILLS:
        if skill.lower() in text_lower:
            result["tertiary"].append(skill)
    return result


def score_color_hex(score: int) -> str:
    """Return hex color for a match score: green > 70, yellow 40-70, orange 30-40."""
    if score > 70:
        return "#4CAF50"
    if score >= 40:
        return "#FFC107"
    return "#FF9800"


def score_color_name(score: int) -> str:
    """Return Rich markup color name for a match score."""
    if score > 70:
        return "green"
    if score >= 40:
        return "yellow"
    return "dark_orange"
