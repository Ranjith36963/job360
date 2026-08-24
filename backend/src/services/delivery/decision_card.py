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


def salary_from_enrichment(raw: Any) -> Optional[str]:
    """Turn a ``job_enrichment.salary`` JSON blob into the dashboard's words.

    Parity matters at the SOURCE, not just the format. The dashboard's salary
    does not come from ``jobs.salary_min_gbp_annual`` — it comes from the
    enrichment blob, parsed by ``services.salary.normalize_salary`` and
    currency-checked by ``core.fx.is_known_currency``
    (``api/routes/jobs.py:55-81``). Reading a different column here would
    produce a number that is defensible on its own and still disagrees with the
    screen, which is the exact failure this module exists to prevent.

    Returns None for missing, malformed or unknown-currency data — the caller
    then omits the salary line rather than guessing.
    """
    if not raw:
        return None
    # Imported inside the function: `services.salary` pulls the FX table, and
    # this module is imported by the worker on every digest build (rule #16's
    # habit — keep import cost off the hot path).
    import json as _json

    from src.core.fx import is_known_currency
    from src.services.salary import normalize_salary

    try:
        obj = _json.loads(raw) if isinstance(raw, (str, bytes)) else dict(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None

    currency = obj.get("currency")
    if not isinstance(currency, str) or not currency or not is_known_currency(currency):
        return None

    normalised = normalize_salary(obj)
    if normalised is None:
        return None
    return format_salary_range(*normalised)


def format_salary_range(
    min_gbp: Optional[float], max_gbp: Optional[float]
) -> Optional[str]:
    """Format an annual GBP range the SAME way the dashboard does.

    Mirrors ``formatSalaryRange`` in ``frontend/src/components/jobs/JobCard.tsx``
    line-for-line: ``£70k–£85k``, ``£70k+``, ``up to £85k``, and a bare ``£950``
    below a thousand. If that function changes, this must change with it — the
    whole point of the card is that the email and the screen say the same words,
    and "£70k–£85k" versus "£70,000 - £85,000" is a visible difference to the
    only person who matters.

    Returns None when neither bound is known, so the caller omits the line
    rather than printing an empty one.
    """
    def fmt(n: float) -> str:
        return f"£{round(n / 1000)}k" if n >= 1000 else f"£{int(n)}"

    if not min_gbp and not max_gbp:
        return None
    if min_gbp and max_gbp:
        return fmt(min_gbp) if min_gbp == max_gbp else f"{fmt(min_gbp)}–{fmt(max_gbp)}"
    if min_gbp:
        return f"{fmt(min_gbp)}+"
    if max_gbp:
        return f"up to {fmt(max_gbp)}"
    return None


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
        # Same source and same parser as the dashboard: the enrichment blob.
        # A pre-formatted ``salary`` string wins only as a fallback, for callers
        # (and tests) that already hold one.
        salary=(
            salary_from_enrichment(row.get("enr_salary")) or _clean(row.get("salary"))
        ),
    )
