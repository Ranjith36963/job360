from datetime import datetime, timezone
from src.models import Job
from src.notifications.report_generator import generate_markdown_report, generate_html_report


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        location="London, UK",
        salary_min=70000,
        salary_max=100000,
        apply_url="https://example.com/job",
        source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        description="AI role",
        match_score=85,
        visa_flag=True,
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_markdown_report_contains_jobs():
    jobs = [
        _make_job(title="AI Engineer", match_score=90),
        _make_job(title="ML Engineer", company="Revolut", match_score=75),
    ]
    stats = {"total_found": 50, "new_jobs": 2, "per_source": {"reed": 30, "adzuna": 20}}
    report = generate_markdown_report(jobs, stats)
    assert "AI Engineer" in report
    assert "ML Engineer" in report
    assert "90" in report


def test_markdown_report_sorted_by_score():
    jobs = [
        _make_job(title="Low Score Job", match_score=30),
        _make_job(title="High Score Job", match_score=95, company="Top"),
    ]
    stats = {"total_found": 2, "new_jobs": 2, "per_source": {}}
    report = generate_markdown_report(jobs, stats)
    high_pos = report.index("High Score Job")
    low_pos = report.index("Low Score Job")
    assert high_pos < low_pos


def test_markdown_report_shows_visa_flag():
    jobs = [_make_job(visa_flag=True)]
    stats = {"total_found": 1, "new_jobs": 1, "per_source": {}}
    report = generate_markdown_report(jobs, stats)
    assert "VISA" in report.upper() or "visa" in report.lower()


def test_markdown_report_empty_jobs():
    report = generate_markdown_report([], {"total_found": 0, "new_jobs": 0, "per_source": {}})
    assert "No new jobs" in report or "0" in report


def test_html_report_generates_html():
    jobs = [_make_job()]
    stats = {"total_found": 1, "new_jobs": 1, "per_source": {"reed": 1}}
    html = generate_html_report(jobs, stats)
    assert "<html" in html or "<table" in html
    assert "AI Engineer" in html


def test_html_report_has_apply_links():
    jobs = [_make_job(apply_url="https://apply.example.com/123")]
    stats = {"total_found": 1, "new_jobs": 1, "per_source": {}}
    html = generate_html_report(jobs, stats)
    assert "https://apply.example.com/123" in html
