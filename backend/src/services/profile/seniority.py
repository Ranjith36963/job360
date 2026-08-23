"""Infer a user's seniority level from their CV / LinkedIn work history.

WHY THIS IS ALLOWED (the rule #29 boundary). Product design rule #29
(`docs/product/product_design_rules.md` rule 1) says an EMPTY user field means
"don't care" — the matcher must never guess a PREFERENCE the user never
stated (a salary range, a workplace choice, an experience level they simply
never picked from the dropdown). Seniority is a different kind of thing: it
is not a want, it is a FACT already written on the CV as dated job titles
("Senior Backend Engineer, Acme Ltd, 2021-Present"). Deriving it here is the
same operation as extracting skills or education off the same document —
reading, not inventing. The one thing this module must never do is let that
reading masquerade as something the user typed: see
``UserPreferences.experience_level`` (typed) vs
``UserPreferences.experience_level_inferred`` (read off the CV) in
`services/profile/models.py`, and the resolution order in
``scoring_dimensions.resolve_experience_level`` — typed always wins,
inferred only fills a genuine blank, and an empty result from this module
leaves the seniority dimension at its documented neutral half-weight,
exactly as it behaved before this module existed.

WHY A WRONG-LOW GUESS IS WORSE THAN NO GUESS (manager review, 2026-08-07).
The first version of this module walked positions most-recent-first and
returned the first title with a tier word — which put a 3-month internship
ahead of a 3-year engineering role simply because it happened to be listed
last, and (separately) put an old internship title ahead of a title-neutral
recent role for the same reason. Measured on two real production profiles,
both came out "intern". That is actively harmful, not merely unhelpful:
`scoring_dimensions.seniority_score` has a NEGATIVE tail (down to -100% at
4+ ranks apart), so an under-called level doesn't just fail to help — it
penalises every mid/senior job for a user who never typed anything. The
redesign below fixes that with two structural rules:

  * DURATION GATES CONFIDENCE. A role has to be held for at least
    ``_SUSTAINED_MONTHS`` (6) before it is trusted to set the floor —
    whether the evidence is its title ("Intern", "Senior Consultant") or its
    own length. Internships are short by definition, so this alone stops a
    fleeting internship from outranking a multi-year role. It is applied
    uniformly (any short role, not just internships) rather than special-
    casing the word "intern" — an equally short "Senior Consultant" contract
    gets the same discount, for the same reason.
  * THE FLOOR NEVER DROPS. Across every role that DOES clear the duration
    bar, the answer is the HIGHEST rank any of them earned — never the most
    recent, never an average. A short, low-ranked role afterwards (the
    walk-back bug's exact failure mode) cannot pull the result back down;
    see `infer_experience_level`'s "signal 1+2" block.

SIGNAL ORDERING (most to least trusted — see `infer_experience_level`):

  1+2. Highest rank SUSTAINED (>= `_SUSTAINED_MONTHS`) by any role, where a
     role's own rank is the higher of (a) a seniority word in its title —
     `job_signals.detect_seniority`, the SAME data-backed detector the job
     side already uses (CLAUDE.md rule #28: no parallel word list) — and
     (b) a per-role duration-derived rank (`_years_to_rank`) when the role
     ran long enough to be informative on its own, e.g. a 3-year plainly-
     titled "Software Development Engineer" earns "mid" even though its
     title carries no tier word. Only roles that individually clear the
     duration bar can set this floor — this is where "sustained" beats
     "latest mentioned".
  3. Total accumulated duration (the SUM of every role's parseable length,
     not the calendar span, so a gap between jobs doesn't inflate it) banded
     the same way — used ONLY when no single role cleared the sustained
     bar. This is the true last resort (several short stints, none alone
     conclusive). Its band table starts at "junior", never "intern": a
     handful of short stints is too thin a sample to assert the harshest,
     most negative-penalty-prone tier with confidence, and calling a
     genuinely early-career user "junior" instead of "intern" is a much
     cheaper mistake than the reverse (rank 1 vs 0 costs a mild partial-
     credit mismatch; a false "intern" against a real mid/senior job costs
     the negative-penalty tail).
  4. If nothing anywhere parsed a usable duration, the weakest fallback:
     whatever title words exist at all, read in the given (assumed
     most-recent-first, matching how CVs/LinkedIn list roles) list order.

ON "RECENCY SHOULD TEMPER, NOT DOMINATE" — a deliberate simplification.
Recency is not implemented as literal time-decay (down-weighting an old
"Senior" title toward "mid" the further back it sits). Two reasons: first,
"the highest sustained rank wins" already makes recency irrelevant to WHICH
rank is reported once a role qualifies — decaying an old peak would only
ever pull the answer DOWN, which is exactly the failure mode point 4 above
forbids. Second, no fixture (real or hypothetical) in this codebase
currently demonstrates decay is needed, and adding it without one risks
under-crediting genuine sustained seniority — the harm this module exists to
prevent — for a benefit nothing here tests. Recency still matters in the
places it safely can: an "ongoing"/"Present" role's end date resolves to
TODAY (so a currently-held senior role is fully credited), and a strong
recent role can always set a NEW, higher floor (the max naturally lets good
recent news through). If a real case ever needs true decay, it should arrive
with the failing profile that justifies it.

Dates arrive as free text in wildly different shapes across the two sources
("2023 - 2024", "Jan 2020 - Present", or LinkedIn's separate ``start``/
``end`` strings) — parsing is best-effort: split into a start/end pair, each
read for a year (required) and a month name (optional, closed 12-name set —
structural calendar vocabulary, not a rule-#28 skill list), plus an
"ongoing" vocabulary (present/current/now). The whole function never raises:
an unparseable record is simply skipped, never a crash that costs the user
their profile save (mirrors ``two_pass.run_two_pass_extraction``'s own
never-raise contract, which is what calls this module).
"""

from __future__ import annotations

import datetime
import re
from typing import Any, Optional

from src.services.job_signals import detect_seniority
from src.services.scoring_dimensions import _USER_EXPERIENCE_RANK

# A role must run at least this long before it is trusted to set the
# "sustained" floor (signal 1+2) — see the module docstring's "DURATION
# GATES CONFIDENCE". Applied uniformly to every role regardless of title.
_SUSTAINED_MONTHS = 6

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
_ONGOING_RE = re.compile(
    r"\b(?:present|current|currently|now|ongoing|to\s+date)\b", re.IGNORECASE
)
# Splits one combined "start - end" date-range string (CV shape) into its two
# boundary texts. Whitespace around the separator is optional so both
# "2019-2022" and "Jan 2020 - Present" split correctly. maxsplit=1 so a
# stray extra dash elsewhere in the text can't over-split.
_RANGE_SPLIT = re.compile(r"\s*(?:-|–|—|\bto\b)\s*", re.IGNORECASE)

# Closed, fixed 12-name calendar vocabulary — structural date parsing, not a
# domain skill/keyword list (rule #28 governs the latter, not this).
_MONTH_RANK = {
    "jan": 1, "january": 1,
    "feb": 2, "february": 2,
    "mar": 3, "march": 3,
    "apr": 4, "april": 4,
    "may": 5,
    "jun": 6, "june": 6,
    "jul": 7, "july": 7,
    "aug": 8, "august": 8,
    "sep": 9, "sept": 9, "september": 9,
    "oct": 10, "october": 10,
    "nov": 11, "november": 11,
    "dec": 12, "december": 12,
}
_MONTH_RE = re.compile(
    r"\b(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun[e]?|jul[y]?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\b",
    re.IGNORECASE,
)

# Total-accumulated-duration fallback bands (years -> rank, ascending).
# Signal 3 ONLY (see module docstring) — deliberately never reaches rank 0
# ("intern"); the lowest band is "junior" (rank 1). Boundaries follow common
# UK career-ladder timing (junior 0-3y; mid 3-6y; senior 6-10y; staff/
# principal/director beyond a decade-plus). A numeric threshold table is
# structure, not a keyword vocabulary — rule #28 governs word lists, not
# year bands.
_YEARS_BANDS: tuple[tuple[float, int], ...] = (
    (0.0, 1),   # junior
    (3.0, 2),   # mid
    (6.0, 3),   # senior
    (10.0, 4),  # staff
    (15.0, 5),  # principal
    (20.0, 6),  # director
)

_RANK_TO_WORD = {
    0: "intern", 1: "junior", 2: "mid", 3: "senior",
    4: "staff", 5: "principal", 6: "director",
}


def _years_to_rank(years: float) -> int:
    rank = _YEARS_BANDS[0][1]
    for threshold, r in _YEARS_BANDS:
        if years >= threshold:
            rank = r
    return rank


def _parse_year_month(text: str, *, today: datetime.date) -> tuple[Optional[int], Optional[int]]:
    """Best-effort ``(year, month)`` from ONE date boundary (e.g. "Jan 2020",
    "2022", "Present"). Month is ``None`` when the text doesn't name one —
    callers fall back to a coarser year-only estimate. Never raises.
    """
    if not text or not isinstance(text, str):
        return None, None
    if _ONGOING_RE.search(text):
        return today.year, today.month
    year_m = _YEAR_RE.search(text)
    if not year_m:
        return None, None
    month = None
    month_m = _MONTH_RE.search(text)
    if month_m:
        month = _MONTH_RANK.get(month_m.group(0).lower())
    return int(year_m.group(0)), month


def _position_boundaries(pos: dict[str, Any]) -> tuple[str, str]:
    """Normalise one CV or LinkedIn position dict to ``(start_text, end_text)``.

    CV positions (``CVData.cv_positions``) carry one combined ``dates``
    string, split on the first dash-like separator; LinkedIn positions
    (``CVData.linkedin_positions``) already carry separate ``start``/``end``
    strings.
    """
    if "dates" in pos:
        dates_text = str(pos.get("dates") or "")
        if not dates_text:
            return "", ""
        parts = _RANGE_SPLIT.split(dates_text, maxsplit=1)
        if len(parts) == 2:
            return parts[0].strip(), parts[1].strip()
        return dates_text.strip(), ""
    return str(pos.get("start") or ""), str(pos.get("end") or "")


def _duration_months(
    start_y: Optional[int], start_m: Optional[int],
    end_y: Optional[int], end_m: Optional[int],
) -> Optional[int]:
    """Best-effort role length in months, or ``None`` when genuinely unknown.

    Both boundary months known -> exact inclusive month count. Only years
    known -> a coarse whole-year estimate (``end_y - start_y`` years), which
    is itself ``None`` when that diff is 0 — a same-year span with no month
    detail could be 1 month or 11, and guessing either risks a wrong
    "sustained" verdict in either direction, so it is left unknown rather
    than guessed.
    """
    if start_y is None or end_y is None:
        return None
    if start_m is not None and end_m is not None:
        return max((end_y - start_y) * 12 + (end_m - start_m) + 1, 1)
    diff_years = end_y - start_y
    return diff_years * 12 if diff_years > 0 else None


def infer_experience_level(
    cv_positions: Optional[list[dict[str, Any]]],
    linkedin_positions: Optional[list[dict[str, Any]]] = None,
    *,
    today: Optional[datetime.date] = None,
) -> str:
    """Derive the user's current seniority level from dated positions.

    Returns one of the vocabulary words `scoring_dimensions._USER_EXPERIENCE_RANK`
    already understands (e.g. "senior", "junior", "staff") or ``""`` when
    nothing could be inferred — an empty result is a legitimate, expected
    outcome (no positions, no dates anywhere, no title signal), not a
    failure. Never raises: any parsing surprise falls through to ``""``,
    which leaves the seniority dimension at its neutral half-weight exactly
    as if this module did not exist.

    See the module docstring for the full signal ordering and the reasoning
    behind the duration gate / floor-never-drops design.
    """
    try:
        today_date = today or datetime.date.today()
        positions: list[dict[str, Any]] = []
        positions.extend(cv_positions or [])
        positions.extend(linkedin_positions or [])
        if not positions:
            return ""

        parsed: list[dict[str, Any]] = []
        for pos in positions:
            if not isinstance(pos, dict):
                continue
            title = str(pos.get("title") or "").strip()
            start_text, end_text = _position_boundaries(pos)
            start_y, start_m = _parse_year_month(start_text, today=today_date)
            end_y, end_m = _parse_year_month(end_text, today=today_date)
            if start_y is not None and end_y is not None and end_y < start_y:
                start_y, start_m, end_y, end_m = end_y, end_m, start_y, start_m
            duration_months = _duration_months(start_y, start_m, end_y, end_m)
            if not title and duration_months is None:
                continue  # nothing usable in this record — skip, don't guess

            title_rank = None
            if title:
                signal = detect_seniority(title, "")
                level = signal.value.value  # SeniorityLevel is a str Enum
                if level and level != "unknown":
                    title_rank = _USER_EXPERIENCE_RANK.get(level)

            parsed.append({"title_rank": title_rank, "duration_months": duration_months})

        if not parsed:
            return ""

        # Signals 1+2 — highest rank actually SUSTAINED. A role only
        # participates once it clears `_SUSTAINED_MONTHS`; within that set,
        # its own rank is the higher of its title word and its own
        # duration-derived rank (never lower than either — max, not
        # override). The floor is the max across all qualifying roles, so a
        # later short role (however it's titled) can never pull it down.
        floor_rank: Optional[int] = None
        for p in parsed:
            months = p["duration_months"]
            if months is None or months < _SUSTAINED_MONTHS:
                continue
            duration_rank = _years_to_rank(months / 12)
            candidates = [r for r in (p["title_rank"], duration_rank) if r is not None]
            role_rank = max(candidates) if candidates else None
            if role_rank is not None and (floor_rank is None or role_rank > floor_rank):
                floor_rank = role_rank
        if floor_rank is not None:
            return _RANK_TO_WORD[floor_rank]

        # Signal 3 (last resort) — nothing individually sustained. Sum
        # whatever durations parsed across ALL roles (short stints still
        # count toward cumulative experience even though none alone earns
        # the sustained floor) and band the total. Never "intern" — see the
        # module docstring.
        total_months = sum(
            p["duration_months"] for p in parsed if p["duration_months"] is not None
        )
        if total_months > 0:
            return _RANK_TO_WORD[_years_to_rank(total_months / 12)]

        # No duration info at all anywhere — weakest possible fallback:
        # whatever title words exist, in the given (assumed most-recent-
        # first) order.
        for p in parsed:
            if p["title_rank"] is not None:
                return _RANK_TO_WORD[p["title_rank"]]
        return ""
    except Exception:  # noqa: BLE001 — a failed inference must never cost a save
        return ""
