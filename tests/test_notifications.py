import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch, MagicMock

from src.models import Job
from src.notifications.slack_notify import (
    is_slack_configured,
    _build_payload,
    send_slack,
)
from src.notifications.discord_notify import (
    is_discord_configured,
    _build_embeds,
    send_discord,
)


def _make_job(**overrides):
    defaults = dict(
        title="AI Engineer",
        company="DeepMind",
        location="London, UK",
        salary_min=70000,
        salary_max=100000,
        apply_url="https://example.com/job",
        source="greenhouse",
        date_found=datetime.now(timezone.utc).isoformat(),
        description="AI role requiring Python and PyTorch",
        match_score=85,
        visa_flag=True,
    )
    defaults.update(overrides)
    return Job(**defaults)


SAMPLE_STATS = {"total_found": 50, "new_jobs": 5, "per_source": {"reed": 30, "adzuna": 20}}


# ---------- Slack tests ----------


def test_slack_not_configured_when_empty():
    with patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", ""):
        assert is_slack_configured() is False


def test_slack_configured_when_set():
    with patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/T/B/X"):
        assert is_slack_configured() is True


def test_slack_payload_has_blocks():
    jobs = [_make_job(match_score=90), _make_job(title="ML Engineer", match_score=60, company="Revolut")]
    payload = _build_payload(jobs, SAMPLE_STATS)
    assert "blocks" in payload
    blocks = payload["blocks"]
    assert len(blocks) >= 3  # header + stats + divider + jobs


def test_slack_payload_contains_job_info():
    jobs = [_make_job(title="Senior AI Engineer", company="Anthropic")]
    payload = _build_payload(jobs, SAMPLE_STATS)
    text = str(payload)
    assert "Senior AI Engineer" in text
    assert "Anthropic" in text


def test_slack_payload_score_colors():
    jobs = [
        _make_job(match_score=80),
        _make_job(match_score=55, company="Mid"),
        _make_job(match_score=30, company="Low"),
    ]
    payload = _build_payload(jobs, SAMPLE_STATS)
    text = str(payload)
    assert "🟢" in text  # high score
    assert "🟡" in text  # mid score
    assert "🔴" in text  # low score


def test_slack_payload_overflow_message():
    jobs = [_make_job(company=f"Company{i}") for i in range(15)]
    payload = _build_payload(jobs, SAMPLE_STATS)
    text = str(payload)
    assert "5 more" in text


@pytest.mark.asyncio
async def test_send_slack_skips_when_not_configured():
    with patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", ""):
        await send_slack([_make_job()], SAMPLE_STATS)  # should not raise


@pytest.mark.asyncio
async def test_send_slack_skips_empty_jobs():
    with patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", "https://hooks.slack.com/test"):
        await send_slack([], SAMPLE_STATS)  # should not raise


@pytest.mark.asyncio
async def test_send_slack_posts_webhook():
    mock_resp = AsyncMock()
    mock_resp.status = 200

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))

    with patch("src.notifications.slack_notify.SLACK_WEBHOOK_URL", "https://hooks.slack.com/test"), \
         patch("src.notifications.slack_notify.aiohttp.ClientSession", return_value=mock_session):
        await send_slack([_make_job()], SAMPLE_STATS)
        mock_session.post.assert_called_once()


# ---------- Discord tests ----------


def test_discord_not_configured_when_empty():
    with patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", ""):
        assert is_discord_configured() is False


def test_discord_configured_when_set():
    with patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/123/abc"):
        assert is_discord_configured() is True


def test_discord_embed_structure():
    jobs = [_make_job()]
    payload = _build_embeds(jobs, SAMPLE_STATS)
    assert "embeds" in payload
    assert len(payload["embeds"]) == 1
    embed = payload["embeds"][0]
    assert "title" in embed
    assert "description" in embed
    assert "fields" in embed
    assert embed["color"] == 0x1A73E8


def test_discord_embed_contains_job_info():
    jobs = [_make_job(title="GenAI Engineer", company="OpenAI")]
    payload = _build_embeds(jobs, SAMPLE_STATS)
    desc = payload["embeds"][0]["description"]
    assert "GenAI Engineer" in desc
    assert "OpenAI" in desc


def test_discord_embed_score_colors():
    jobs = [
        _make_job(match_score=80),
        _make_job(match_score=55, company="Mid"),
        _make_job(match_score=30, company="Low"),
    ]
    payload = _build_embeds(jobs, SAMPLE_STATS)
    desc = payload["embeds"][0]["description"]
    assert "🟢" in desc
    assert "🟡" in desc
    assert "🔴" in desc


def test_discord_embed_overflow_message():
    jobs = [_make_job(company=f"Company{i}") for i in range(15)]
    payload = _build_embeds(jobs, SAMPLE_STATS)
    desc = payload["embeds"][0]["description"]
    assert "5 more" in desc


def test_discord_embed_salary_formatting():
    jobs = [_make_job(salary_min=60000, salary_max=90000)]
    payload = _build_embeds(jobs, SAMPLE_STATS)
    desc = payload["embeds"][0]["description"]
    assert "60,000" in desc
    assert "90,000" in desc


@pytest.mark.asyncio
async def test_send_discord_skips_when_not_configured():
    with patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", ""):
        await send_discord([_make_job()], SAMPLE_STATS)


@pytest.mark.asyncio
async def test_send_discord_skips_empty_jobs():
    with patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"):
        await send_discord([], SAMPLE_STATS)


@pytest.mark.asyncio
async def test_send_discord_posts_webhook():
    mock_resp = AsyncMock()
    mock_resp.status = 204

    mock_session = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    mock_session.post = MagicMock(return_value=AsyncMock(__aenter__=AsyncMock(return_value=mock_resp), __aexit__=AsyncMock(return_value=False)))

    with patch("src.notifications.discord_notify.DISCORD_WEBHOOK_URL", "https://discord.com/api/webhooks/test"), \
         patch("src.notifications.discord_notify.aiohttp.ClientSession", return_value=mock_session):
        await send_discord([_make_job()], SAMPLE_STATS)
        mock_session.post.assert_called_once()
