from datetime import datetime, timezone
from unittest.mock import patch

from src.models import Job
from src.notifications.base import format_salary, get_all_channels, get_configured_channels


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer", company="Test Co",
        apply_url="https://example.com", source="reed",
        date_found=datetime.now(timezone.utc).isoformat(),
        location="London, UK", description="",
    )
    defaults.update(overrides)
    return Job(**defaults)


def test_format_salary_range():
    job = _make_job(salary_min=60000, salary_max=80000)
    assert format_salary(job) == "£60,000-£80,000"


def test_format_salary_min_only():
    job = _make_job(salary_min=50000)
    assert format_salary(job) == "£50,000+"


def test_format_salary_max_only():
    job = _make_job(salary_max=90000)
    assert format_salary(job) == "Up to £90,000"


def test_format_salary_none():
    job = _make_job()
    assert format_salary(job) == "N/A"


def test_get_all_channels_returns_three():
    channels = get_all_channels()
    assert len(channels) == 3
    names = {ch.name for ch in channels}
    assert names == {"Email", "Slack", "Discord"}


def test_get_configured_channels_none_configured():
    """With no env vars, no channels should be configured."""
    with patch("src.notifications.email_notify.SMTP_EMAIL", ""), \
         patch("src.notifications.email_notify.SMTP_PASSWORD", ""), \
         patch("src.notifications.email_notify.NOTIFY_EMAIL", ""), \
         patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", ""), \
         patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", ""):
        channels = get_configured_channels()
        assert len(channels) == 0


def test_get_configured_channels_slack_only():
    """With only Slack configured, only Slack channel should be returned."""
    with patch("src.notifications.email_notify.SMTP_EMAIL", ""), \
         patch("src.notifications.email_notify.SMTP_PASSWORD", ""), \
         patch("src.notifications.email_notify.NOTIFY_EMAIL", ""), \
         patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", "https://hooks.slack.com/test"), \
         patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", ""):
        channels = get_configured_channels()
        assert len(channels) == 1
        assert channels[0].name == "Slack"
