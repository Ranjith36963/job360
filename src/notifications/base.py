"""Base notification channel abstraction for Job360."""

import logging
from abc import ABC, abstractmethod

from src.models import Job

logger = logging.getLogger("job360.notifications")


class NotificationChannel(ABC):
    """Abstract base class for all notification channels."""

    name: str = "unknown"

    @abstractmethod
    def is_configured(self) -> bool:
        """Return True if this channel has the required config to send."""
        ...

    @abstractmethod
    async def send(self, jobs: list[Job], stats: dict, **kwargs) -> None:
        """Send notification with the given jobs and stats."""
        ...


def format_salary(job: Job) -> str:
    """Shared salary formatting used across all notification channels."""
    if job.salary_min and job.salary_max:
        return f"£{int(job.salary_min):,}-£{int(job.salary_max):,}"
    if job.salary_min:
        return f"£{int(job.salary_min):,}+"
    if job.salary_max:
        return f"Up to £{int(job.salary_max):,}"
    return "N/A"


def get_all_channels() -> list[NotificationChannel]:
    """Return instances of all notification channel classes."""
    from src.notifications.email_notify import EmailChannel
    from src.notifications.slack_notify import SlackChannel
    from src.notifications.discord_notify import DiscordChannel
    return [EmailChannel(), SlackChannel(), DiscordChannel()]


def get_configured_channels() -> list[NotificationChannel]:
    """Return only channels that are configured and ready to send."""
    return [ch for ch in get_all_channels() if ch.is_configured()]
