"""Visa sponsorship as a THREE-state signal: sponsors / no / unknown.

WHY THREE. `jobs.visa_flag` is a bool, so "this ad says it will not sponsor"
and "this ad never mentions visas" are the same value — False. For a candidate
who needs sponsorship those are opposite facts: one is a dead end, the other is
a question worth asking. A boolean cannot say "I don't know", so the product
could only ever offer a hard filter, and a hard filter over a mostly-unknown
column hides far more than it removes.

WHY IT MATTERS RIGHT NOW. Measured on prod 2026-08-07: LLM enrichment had a
visa verdict for 422 of 5,067 jobs (8%). Meanwhile 1,847 job descriptions state
their visa position in plain text — devitjobs alone emits a structured
"Visa sponsorship available" trailer on 1,800 UK listings. Reading the text we
already store costs nothing and takes the answerable share from 8% to ~36%.

WHY NOT A FILTER (the product rule). Visa ON is a SPOTLIGHT, not a wall:
sponsors are guaranteed into the feed and emphasised, everything else still
shows, and every card carries its badge. Hiding "unknown" would hide two
thirds of the catalog on the strength of a phrase an employer simply did not
write. This mirrors design rule #29 — what the user filled sharpens the
ranking; it never silently deletes the catalog.

Detection is deliberately deterministic (no LLM): the phrases are formulaic UK
recruitment boilerplate, and a regex over stored text is free and repeatable.
"""
from __future__ import annotations

import re
from enum import Enum
from typing import Optional


class VisaStatus(str, Enum):
    SPONSORS = "sponsors"
    NO_SPONSORSHIP = "no_sponsorship"
    UNKNOWN = "unknown"


# NEGATIVE first — precedence is load-bearing. "We cannot offer visa
# sponsorship" contains "visa sponsorship", so any positive-first design reads
# a refusal as an offer. Every phrase below was taken from live UK ad text.
_NO = re.compile(
    r"\bno\s+(?:visa\s+)?sponsorship\b"
    r"|\bcannot\s+(?:offer|provide|support)\s+(?:visa\s+)?sponsorship\b"
    r"|\bunable\s+to\s+(?:offer|provide|sponsor)\b"
    r"|\bnot\s+(?:able\s+to\s+)?sponsor\b"
    r"|\bsponsorship\s+is\s+not\s+(?:available|offered|provided)\b"
    r"|\bwithout\s+(?:the\s+need\s+for\s+)?sponsorship\b"
    r"|\bmust\s+(?:already\s+)?have\s+(?:the\s+)?right\s+to\s+work\b"
    r"|\bright\s+to\s+work\s+in\s+the\s+uk\s+(?:is\s+)?(?:required|essential)\b",
    re.IGNORECASE,
)

# POSITIVE. "tier 2" is deliberately ABSENT: measured on prod it matched
# "leading a team of Tier 1 and Tier 2 support representatives" — a support-
# tier, not the visa route. A signal that fires on the wrong sentence is worse
# than no signal, because it puts a confident badge on a wrong answer.
_YES = re.compile(
    r"\bvisa\s+sponsorship\s+(?:is\s+)?available\b"
    r"|\bsponsorship\s+(?:is\s+)?(?:available|offered|provided)\b"
    r"|\bwe\s+(?:can\s+)?sponsor\b"
    r"|\bskilled\s+worker\s+visa\b"
    r"|\bcertificate\s+of\s+sponsorship\b"
    r"|\blicen[cs]ed\s+sponsor\b"
    r"|\bvisa\s+sponsorship\s+(?:offered|provided|supported)\b",
    re.IGNORECASE,
)


def detect_visa_status(
    description: Optional[str],
    title: Optional[str] = "",
    *,
    enrichment_value: Optional[str] = None,
) -> VisaStatus:
    """Classify an ad's visa position.

    ``enrichment_value`` (the LLM's verdict, when one exists) wins: it read the
    whole ad with context. Text detection fills the much larger gap where no
    enrichment exists yet.
    """
    if enrichment_value:
        ev = enrichment_value.strip().lower()
        if ev in ("yes", "sponsors", "true"):
            return VisaStatus.SPONSORS
        if ev in ("no", "none", "false"):
            return VisaStatus.NO_SPONSORSHIP

    text = f"{title or ''}\n{description or ''}"
    if not text.strip():
        return VisaStatus.UNKNOWN
    # Refusal beats offer — see the precedence note above.
    if _NO.search(text):
        return VisaStatus.NO_SPONSORSHIP
    if _YES.search(text):
        return VisaStatus.SPONSORS
    return VisaStatus.UNKNOWN
