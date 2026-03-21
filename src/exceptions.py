"""Job360 custom exception hierarchy."""


class Job360Error(Exception):
    """Base exception for all Job360 errors."""


class SourceError(Job360Error):
    """Error fetching jobs from a source."""

    def __init__(self, source_name: str, message: str):
        self.source_name = source_name
        super().__init__(f"[{source_name}] {message}")


class ScoringError(Job360Error):
    """Error during job scoring."""


class ProfileError(Job360Error):
    """Error loading or parsing user profile."""


class DatabaseError(Job360Error):
    """Error with database operations."""
