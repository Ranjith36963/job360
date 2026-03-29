"""Tests for the validation/QA system."""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

from src.validation.checker import (
    ValidationResult,
    SourceConfidence,
    validate_job,
    aggregate_by_source,
    _title_similarity,
    _desc_similarity,
    _date_to_bucket,
    _extract_title,
    _extract_date,
)
from src.validation.report import (
    generate_validation_report,
    generate_validation_json,
)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _make_result(source="greenhouse", url_alive=1.0, title_match=0.9,
                 date_accurate=0.8, description_match=0.7,
                 match_score=50, **kw) -> ValidationResult:
    return ValidationResult(
        job_id=kw.get("job_id", 1),
        source=source,
        title=kw.get("title", "ML Engineer"),
        company=kw.get("company", "TestCo"),
        apply_url="https://example.com/job/1",
        match_score=match_score,
        url_alive=url_alive,
        title_match=title_match,
        date_accurate=date_accurate,
        description_match=description_match,
        actual_status_code=200,
    )


# ---------------------------------------------------------------------------
# Title similarity
# ---------------------------------------------------------------------------

class TestTitleSimilarity:
    def test_exact_match(self):
        assert _title_similarity("ML Engineer", "ML Engineer") >= 0.9

    def test_substring_match(self):
        # Sites often append company name
        assert _title_similarity("ML Engineer", "ML Engineer | Google Careers") >= 0.8

    def test_no_match(self):
        assert _title_similarity("ML Engineer", "Marketing Manager") <= 0.5

    def test_empty(self):
        assert _title_similarity("", "ML Engineer") == 0.0
        assert _title_similarity("ML Engineer", "") == 0.0


# ---------------------------------------------------------------------------
# Description similarity
# ---------------------------------------------------------------------------

class TestDescSimilarity:
    def test_skip_short_stored(self):
        assert _desc_similarity("short", "long body text here") == -1.0

    def test_good_match(self):
        text = "We are looking for a machine learning engineer with Python experience."
        assert _desc_similarity(text, text) >= 0.8

    def test_substring_containment(self):
        stored = "We need a Python developer for our AI team working on NLP problems."
        body = "Header. " + stored + " Footer with more text about the company."
        assert _desc_similarity(stored, body) == 1.0

    def test_no_match(self):
        # Truly unrelated descriptions with no keyword overlap
        assert _desc_similarity(
            "Kubernetes cluster orchestration with Terraform infrastructure provisioning.",
            "Nursery assistant caring for toddlers ages two through five daily.",
        ) < 0.5


# ---------------------------------------------------------------------------
# Date to bucket
# ---------------------------------------------------------------------------

class TestDateToBucket:
    def test_today(self):
        today = datetime.now(timezone.utc).isoformat()
        assert _date_to_bucket(today) == 0

    def test_old_date(self):
        old = (datetime.now(timezone.utc) - timedelta(days=10)).isoformat()
        assert _date_to_bucket(old) >= 4

    def test_invalid_date(self):
        assert _date_to_bucket("not-a-date") == -1


# ---------------------------------------------------------------------------
# HTML extraction
# ---------------------------------------------------------------------------

class TestExtractTitle:
    def test_title_tag(self):
        assert _extract_title("<html><title>ML Engineer | Google</title></html>") == "ML Engineer | Google"

    def test_h1_tag(self):
        assert _extract_title("<html><h1>Data Scientist</h1></html>") == "Data Scientist"

    def test_no_title(self):
        assert _extract_title("<html><body>No title here</body></html>") == ""


class TestExtractDate:
    def test_json_ld(self):
        html = '{"datePosted": "2026-03-28"}'
        assert _extract_date(html) == "2026-03-28"

    def test_no_date(self):
        assert _extract_date("<html><body>No date</body></html>") is None


# ---------------------------------------------------------------------------
# Validation result confidence
# ---------------------------------------------------------------------------

class TestValidationResult:
    def test_full_confidence(self):
        r = _make_result(url_alive=1.0, title_match=1.0, date_accurate=1.0, description_match=1.0)
        assert r.confidence == 1.0

    def test_zero_confidence(self):
        r = _make_result(url_alive=0.0, title_match=0.0, date_accurate=0.0, description_match=0.0)
        assert r.confidence == 0.0

    def test_skipped_checks_excluded(self):
        r = _make_result(url_alive=1.0, title_match=1.0, date_accurate=-1.0, description_match=-1.0)
        # Only URL (0.30) and title (0.25) contribute
        assert r.confidence > 0.9


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

class TestAggregation:
    def test_single_source(self):
        results = [
            _make_result(source="greenhouse", url_alive=1.0, title_match=0.9),
            _make_result(source="greenhouse", url_alive=1.0, title_match=0.8, job_id=2),
        ]
        sources = aggregate_by_source(results)
        assert len(sources) == 1
        assert sources[0].source == "greenhouse"
        assert sources[0].jobs_checked == 2
        assert sources[0].url_score == 1.0
        assert sources[0].title_score == 0.85

    def test_multiple_sources(self):
        results = [
            _make_result(source="greenhouse"),
            _make_result(source="ashby", job_id=2),
        ]
        sources = aggregate_by_source(results)
        assert len(sources) == 2


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

class TestReport:
    def test_markdown_report_structure(self):
        results = [_make_result(), _make_result(source="ashby", job_id=2)]
        sources = aggregate_by_source(results)
        md = generate_validation_report(results, sources)
        assert "# Job360 Validation Report" in md
        assert "Overall Benchmark" in md
        assert "Per-Source Results" in md
        assert "greenhouse" in md

    def test_json_report_structure(self):
        results = [_make_result()]
        sources = aggregate_by_source(results)
        data = generate_validation_json(results, sources)
        assert "overall_confidence" in data
        assert "per_source" in data
        assert "greenhouse" in data["per_source"]
        assert "per_job" in data
        assert len(data["per_job"]) == 1

    def test_empty_results(self):
        md = generate_validation_report([], [])
        assert "0 jobs checked" in md
        data = generate_validation_json([], [])
        assert data["overall_confidence"] == 0.0
