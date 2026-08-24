"""One matched job, in the form a human can make a decision about.

This module is the SINGLE definition of "what this job says" — the number, the
verdict word, the reason, and where the click goes. Both the dashboard API and
the delivery email read it, so the email cannot drift from the screen.

Why it exists
-------------
"Which number is a job's score?" used to be answered in two places:

* SQL ranks the feed by ``COALESCE(llm_fit_score, score)``
  (``services/feed.py:90,104``).
* React badges the judge score only when *both* ``llm_fit_score`` and
  ``llm_verdict`` are non-null (``frontend/.../JobCard.tsx:98-99``).

Those predicates differ. They agree today only because ``llm_matcher`` writes
and clears all three judge columns atomically — parity by coincidence. A third
copy inside an email builder is how a coincidence becomes a bug at 8am in
someone's inbox, so there is one implementation and everything calls it.

Design rule pinned by ``tests/test_decision_card.py``:
**the number shown is the number ranked by.** A badge that disagrees with the
sort order teaches the user that the ranking is arbitrary, which is the exact
opposite of what this product sells.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional, Tuple


@dataclass(frozen=True)
class DecisionCard:
    """Everything the user needs to decide "is this worth my evening?".

    Frozen because a card is a snapshot taken at send time. An email cannot be
    edited after it leaves; pretending the object is live invites code that
    mutates it and then wonders why the inbox disagrees with the website.
    """

    job_id: int
    title: str
    company: str
    url: str
    """A link to OUR page for this job — never the employer's ``apply_url``."""

    primary_score: int
    """The number to display. Equals the number the feed is ranked by."""

    is_judged: bool
    """True when the LLM judge produced BOTH a score and a verdict word."""

    keyword_score: int
    """The recall signal, kept as a secondary number (the dashboard's "kw" pill)."""

    verdict: Optional[str] = None
    reason: Optional[str] = None
    location: Optional[str] = None
    salary: Optional[str] = None


def _clean(value: Any) -> Optional[str]:
    """Return a trimmed string, or None for anything that means "absent".

    ``None``, ``""`` and ``"   "`` all collapse to ``None`` so a template never
    prints the four characters ``None`` or renders an empty badge. This is rule
    #29's habit applied to delivery: an empty field says nothing rather than
    saying a blank thing.
    """
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def resolve_primary_score(
    feed_score: Optional[int],
    llm_fit_score: Optional[int],
    llm_verdict: Optional[str],
) -> Tuple[int, bool]:
    """Return ``(primary_score, is_judged)`` for one feed row.

    A row counts as judged only when the judge left BOTH a score and a verdict
    word. A score with no words cannot be explained to the user, and an
    unexplained number is the thing this product refuses to send.

    Note the deliberate ``is not None`` rather than a truthiness check: a judge
    score of ``0`` is a real and strongly negative verdict. ``llm_fit_score or
    feed_score`` would silently discard it.
    """
    verdict = _clean(llm_verdict)
    if llm_fit_score is not None and verdict is not None:
        return int(llm_fit_score), True
    return int(feed_score or 0), False


def build_decision_card(
    row: Mapping[str, Any],
    *,
    site_base_url: str,
) -> DecisionCard:
    """Build a card from a feed row joined against the shared job catalog.

    ``row`` must carry the per-user columns (``score`` and the three ``llm_*``
    fields live on ``user_feed``, not on ``jobs``) — a query that reads only the
    catalog cannot produce a card, which is precisely the bug the old digest
    had: it selected four columns from ``jobs`` and shipped a bare link.

    ``site_base_url`` is injected rather than read from settings so the caller
    owns the environment and tests need no monkeypatching.
    """
    feed_score = row.get("score")
    primary_score, is_judged = resolve_primary_score(
        feed_score=feed_score,
        llm_fit_score=row.get("llm_fit_score"),
        llm_verdict=row.get("llm_verdict"),
    )

    job_id = int(row["job_id"])

    return DecisionCard(
        job_id=job_id,
        title=_clean(row.get("title")) or "Untitled role",
        company=_clean(row.get("company")) or "Unknown company",
        # Always our own page. Attribution, the staleness guard and the
        # anti-scam story all depend on the click landing here first; the
        # employer's apply_url is reached from that page, where we can still
        # say "this one closed".
        url=f"{site_base_url.rstrip('/')}/jobs/{job_id}",
        primary_score=primary_score,
        is_judged=is_judged,
        keyword_score=int(feed_score or 0),
        # Only carried when the row is genuinely judged, so a half-written row
        # can never leak a verdict word next to a keyword score.
        verdict=_clean(row.get("llm_verdict")) if is_judged else None,
        reason=_clean(row.get("llm_reason")) if is_judged else None,
        location=_clean(row.get("location")),
        salary=_clean(row.get("salary")),
    )
