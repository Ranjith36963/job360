"""The /trust page quotes retention numbers and replay sample rates. Pin them to
the code so the page cannot go stale (feature 6)."""
from __future__ import annotations

from pathlib import Path

from src.core import settings
from src.services.auth import magic_link
from src.services.profile import storage

_FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
PAGE = _FRONTEND / "src" / "app" / "trust" / "page.tsx"


def test_trust_page_numbers_match_settings():
    text = " ".join(PAGE.read_text(encoding="utf-8").split())  # JSX wraps lines
    assert f"newest {storage.VERSION_RETENTION} are kept" in text
    assert f"expire after {magic_link.MAGIC_LINK_TTL_MINUTES} minutes" in text
    assert settings.OAUTH_ACCESS_TOKEN_TTL_SECONDS == 3600 and "access lasts 1 hour" in text
    assert settings.OAUTH_REFRESH_TOKEN_TTL_SECONDS == 30 * 86400 and "lasts 30 days" in text
    assert f"last {settings.KIT_LINK_TTL_MINUTES} minutes" in text
    assert f"allow {settings.KIT_LINK_MAX_DOWNLOADS} downloads" in text


def test_trust_page_replay_rates_match_instrumentation():
    inst = (_FRONTEND / "src" / "instrumentation-client.ts").read_text(encoding="utf-8")
    page = " ".join(PAGE.read_text(encoding="utf-8").split())  # JSX wraps lines
    assert "replaysSessionSampleRate: 0.1" in inst and "10% of sessions" in page
    assert "replaysOnErrorSampleRate: 1.0" in inst and "every session that hits an error" in page
