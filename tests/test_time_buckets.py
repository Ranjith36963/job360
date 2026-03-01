"""Tests for src/utils/time_buckets.py — shared bucketing utilities."""

from datetime import datetime, timezone, timedelta

from src.utils.time_buckets import (
    parse_date_safe,
    get_job_age_hours,
    assign_bucket,
    bucket_jobs,
    bucket_summary_counts,
    format_relative_time,
    extract_matched_skills,
    score_color_hex,
    score_color_name,
    BUCKETS,
)


# ---------------------------------------------------------------------------
# parse_date_safe
# ---------------------------------------------------------------------------
def test_parse_date_safe_iso_with_tz():
    dt = parse_date_safe("2026-02-28T10:00:00+00:00")
    assert dt is not None
    assert dt.year == 2026
    assert dt.tzinfo is not None


def test_parse_date_safe_iso_no_tz():
    dt = parse_date_safe("2026-02-28T10:00:00")
    assert dt is not None
    assert dt.tzinfo is not None  # should be set to UTC


def test_parse_date_safe_date_only():
    dt = parse_date_safe("2026-02-28")
    assert dt is not None
    assert dt.day == 28


def test_parse_date_safe_uk_format():
    dt = parse_date_safe("28/02/2026")
    assert dt is not None
    assert dt.month == 2


def test_parse_date_safe_invalid_returns_none():
    assert parse_date_safe("not-a-date") is None
    assert parse_date_safe("") is None
    assert parse_date_safe(None) is None


def test_parse_date_safe_microseconds():
    dt = parse_date_safe("2026-02-28T10:00:00.123456+00:00")
    assert dt is not None


# ---------------------------------------------------------------------------
# get_job_age_hours
# ---------------------------------------------------------------------------
def test_get_job_age_hours_recent():
    recent = (datetime.now(timezone.utc) - timedelta(hours=5)).isoformat()
    age = get_job_age_hours(recent)
    assert 4.5 < age < 5.5


def test_get_job_age_hours_fallback_to_first_seen():
    first_seen = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    age = get_job_age_hours("garbage-date", first_seen=first_seen)
    assert 9.5 < age < 10.5


def test_get_job_age_hours_unknown():
    age = get_job_age_hours("", first_seen="")
    assert age == 999.0


# ---------------------------------------------------------------------------
# assign_bucket
# ---------------------------------------------------------------------------
def test_assign_bucket_0_to_24h():
    assert assign_bucket(0) == 0
    assert assign_bucket(12) == 0
    assert assign_bucket(24) == 0


def test_assign_bucket_1_to_48h():
    assert assign_bucket(25) == 1
    assert assign_bucket(48) == 1


def test_assign_bucket_2_to_72h():
    assert assign_bucket(49) == 2
    assert assign_bucket(72) == 2


def test_assign_bucket_3_to_7d():
    assert assign_bucket(73) == 3
    assert assign_bucket(168) == 3


def test_assign_bucket_over_7d_none():
    assert assign_bucket(169) is None
    assert assign_bucket(999) is None


# ---------------------------------------------------------------------------
# bucket_jobs
# ---------------------------------------------------------------------------
def _make_job(hours_ago: float, score: int = 60) -> dict:
    dt = (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()
    return {"date_found": dt, "match_score": score, "title": f"Job {hours_ago}h ago"}


def test_bucket_jobs_grouping():
    jobs = [
        _make_job(5, 80),   # bucket 0
        _make_job(30, 70),  # bucket 1
        _make_job(60, 65),  # bucket 2
        _make_job(100, 50), # bucket 3
    ]
    bucketed = bucket_jobs(jobs)
    assert len(bucketed[0]) == 1
    assert len(bucketed[1]) == 1
    assert len(bucketed[2]) == 1
    assert len(bucketed[3]) == 1


def test_bucket_jobs_score_filter():
    jobs = [_make_job(5, 20), _make_job(5, 50)]
    bucketed = bucket_jobs(jobs, min_score=30)
    assert len(bucketed[0]) == 1  # only the score=50 job


def test_bucket_jobs_sort_by_score():
    jobs = [_make_job(5, 50), _make_job(10, 90), _make_job(2, 70)]
    bucketed = bucket_jobs(jobs)
    scores = [j["match_score"] for j in bucketed[0]]
    assert scores == [90, 70, 50]


def test_bucket_jobs_old_excluded():
    jobs = [_make_job(200, 90)]  # >7 days
    bucketed = bucket_jobs(jobs)
    assert all(len(v) == 0 for v in bucketed.values())


# ---------------------------------------------------------------------------
# bucket_summary_counts
# ---------------------------------------------------------------------------
def test_bucket_summary_counts():
    bucketed = {0: [1, 2, 3], 1: [4], 2: [], 3: [5, 6]}
    counts = bucket_summary_counts(bucketed)
    assert counts["last_24h"] == 3
    assert counts["24_48h"] == 1
    assert counts["48_72h"] == 0
    assert counts["3_7d"] == 2
    assert counts["total"] == 6


# ---------------------------------------------------------------------------
# format_relative_time
# ---------------------------------------------------------------------------
def test_format_relative_time_recent():
    recent = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
    result = format_relative_time(recent)
    assert "hour" in result or "minute" in result or "now" in result.lower()


def test_format_relative_time_unknown():
    assert format_relative_time("") == "Unknown"
    assert format_relative_time("garbage") == "Unknown"


# ---------------------------------------------------------------------------
# extract_matched_skills
# ---------------------------------------------------------------------------
def test_extract_matched_skills_primary():
    text = "Experience with Python and PyTorch required. Knowledge of LangChain preferred."
    skills = extract_matched_skills(text)
    assert "Python" in skills["primary"]
    assert "PyTorch" in skills["primary"]
    assert "LangChain" in skills["primary"]


def test_extract_matched_skills_secondary():
    text = "Must know Docker and Kubernetes."
    skills = extract_matched_skills(text)
    assert "Docker" in skills["secondary"]
    assert "Kubernetes" in skills["secondary"]


def test_extract_matched_skills_tertiary():
    text = "CI/CD experience and Git proficiency expected."
    skills = extract_matched_skills(text)
    assert "CI/CD" in skills["tertiary"]
    assert "Git" in skills["tertiary"]


def test_extract_matched_skills_no_match():
    skills = extract_matched_skills("Marketing manager position")
    assert skills["primary"] == []
    assert skills["secondary"] == []
    assert skills["tertiary"] == []


def test_extract_matched_skills_empty():
    skills = extract_matched_skills("")
    assert skills == {"primary": [], "secondary": [], "tertiary": []}


# ---------------------------------------------------------------------------
# score_color_hex / score_color_name
# ---------------------------------------------------------------------------
def test_score_color_hex_green():
    assert score_color_hex(80) == "#4CAF50"
    assert score_color_hex(71) == "#4CAF50"


def test_score_color_hex_yellow():
    assert score_color_hex(50) == "#FFC107"
    assert score_color_hex(40) == "#FFC107"


def test_score_color_hex_orange():
    assert score_color_hex(30) == "#FF9800"
    assert score_color_hex(35) == "#FF9800"


def test_score_color_name_green():
    assert score_color_name(80) == "green"


def test_score_color_name_yellow():
    assert score_color_name(50) == "yellow"


def test_score_color_name_orange():
    assert score_color_name(30) == "dark_orange"


# ---------------------------------------------------------------------------
# BUCKETS constant
# ---------------------------------------------------------------------------
def test_buckets_has_4_entries():
    assert len(BUCKETS) == 4
    # Each tuple has 5 elements
    for b in BUCKETS:
        assert len(b) == 5
