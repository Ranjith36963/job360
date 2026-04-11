"""Keyword configuration for Job360.

All AI/ML default lists have been removed (2026-04-09). The system now
requires a user profile (CV upload or manual preferences) — there are no
domain-biased defaults to fall back on.

Only LOCATIONS and VISA_KEYWORDS remain because they are domain-agnostic
geography/immigration data that apply to any profession.
"""

# ---------------------------------------------------------------------------
# Empty defaults — system requires user profile for meaningful matching.
# These names are kept for backward compatibility with imports elsewhere.
# ---------------------------------------------------------------------------

JOB_TITLES: list[str] = []
PRIMARY_SKILLS: list[str] = []
SECONDARY_SKILLS: list[str] = []
TERTIARY_SKILLS: list[str] = []
RELEVANCE_KEYWORDS: list[str] = []
NEGATIVE_TITLE_KEYWORDS: list[str] = []


# ---------------------------------------------------------------------------
# UK locations (domain-agnostic geography — applies to any profession)
# ---------------------------------------------------------------------------

LOCATIONS = [
    "UK",
    "United Kingdom",
    "London",
    "Greater London",
    "City of London",
    "Cambridge",
    "Manchester",
    "Edinburgh",
    "Birmingham",
    "Bristol",
    "Hertfordshire",
    "Hatfield",
    "Leeds",
    "Glasgow",
    "Belfast",
    "Oxford",
    "Reading",
    "Southampton",
    "Nottingham",
    "Sheffield",
    "Liverpool",
    "England",
    "Scotland",
    "Wales",
    "Remote",
    "Hybrid",
]


# ---------------------------------------------------------------------------
# Visa sponsorship keywords (domain-agnostic — applies to any profession
# requiring right-to-work in the UK)
# ---------------------------------------------------------------------------

VISA_KEYWORDS = [
    "visa sponsorship",
    "sponsorship",
    "right to work",
    "work permit",
    "visa",
    "sponsored",
    "tier 2",
    "skilled worker visa",
]
