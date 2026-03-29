"""LLM provider registry — 6 free OpenAI-compatible providers.

All providers use the ``openai`` library with custom ``base_url``.
Only providers with API keys configured via environment variables are active.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class ProviderConfig:
    """Configuration for a single LLM provider."""

    name: str
    base_url: str
    model: str
    api_key_env: str
    rpm: int
    priority: int  # lower = higher priority
    headers: dict[str, str] = field(default_factory=dict)

    @property
    def api_key(self) -> str:
        return os.getenv(self.api_key_env, "")

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)


# Ordered by priority for JD batch processing (speed + RPD capacity).
# Verified against live provider docs, March 2026.
PROVIDERS: list[ProviderConfig] = [
    ProviderConfig(
        name="groq",
        base_url="https://api.groq.com/openai/v1",
        model="llama-3.3-70b-versatile",
        api_key_env="GROQ_API_KEY",
        rpm=30,
        priority=1,
    ),
    ProviderConfig(
        name="cerebras",
        base_url="https://api.cerebras.ai/v1",
        model="gpt-oss-120b",  # llama-3.3-70b deprecated Feb 2026
        api_key_env="CEREBRAS_API_KEY",
        rpm=30,
        priority=2,
    ),
    ProviderConfig(
        name="gemini",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        model="gemini-2.5-flash-lite",  # Lite for batch (1K RPD); Flash for CV
        api_key_env="GEMINI_API_KEY",
        rpm=15,
        priority=3,
    ),
    ProviderConfig(
        name="deepseek",
        base_url="https://api.deepseek.com/v1",
        model="deepseek-chat",  # V3.2, free 30 days then cheap
        api_key_env="DEEPSEEK_API_KEY",
        rpm=30,
        priority=4,
    ),
    ProviderConfig(
        name="openrouter",
        base_url="https://openrouter.ai/api/v1",
        model="meta-llama/llama-3.3-70b-instruct:free",
        api_key_env="OPENROUTER_API_KEY",
        rpm=20,
        priority=5,
        headers={
            "HTTP-Referer": "https://github.com/Ranjith36963/job360",
            "X-Title": "Job360",
        },
    ),
    ProviderConfig(
        name="sambanova",
        base_url="https://api.sambanova.ai/v1",
        model="Meta-Llama-3.3-70B-Instruct",
        api_key_env="SAMBANOVA_API_KEY",
        rpm=20,
        priority=6,  # Only 20 RPD — emergency fallback
    ),
]

# For CV parsing, Gemini Flash (not Lite) gives highest quality for a single call.
CV_PREFERRED_PROVIDER = "gemini"
CV_PREFERRED_MODEL = "gemini-2.5-flash"


def get_configured_providers() -> list[ProviderConfig]:
    """Return only providers whose API keys are set, sorted by priority."""
    return sorted(
        [p for p in PROVIDERS if p.is_configured],
        key=lambda p: p.priority,
    )
