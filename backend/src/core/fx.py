"""Pillar 2 Batch 2.9 — hard-coded FX rates for salary normalisation.

Rates are rough annual averages (as of 2026-Q1). The plan explicitly holds
live FX rates out of scope — salary comparisons are deliberately coarse
because we're grading a match, not a payroll run.

Unknown currencies return 1.0 (treated as already-GBP) which is the safe
degraded behaviour: better to over-include in a £-expected range than to
silently drop a candidate because we couldn't classify their currency.
"""
from __future__ import annotations

from typing import Any, Optional, cast

# ISO 4217 → multiplier to GBP. A payroll system this is not.
_RATES_TO_GBP: dict[str, float] = {
    "GBP": 1.0,
    "USD": 0.79,
    "EUR": 0.86,
    "CAD": 0.58,
    "AUD": 0.52,
    "CHF": 0.91,
    "SEK": 0.075,
    "NOK": 0.075,
    "DKK": 0.115,
    "JPY": 0.0053,
    "INR": 0.0095,
    "SGD": 0.59,
    "HKD": 0.101,
    "PLN": 0.20,
    "CZK": 0.034,
    "NZD": 0.47,
    "ZAR": 0.043,
    "AED": 0.21,
}


def to_gbp(amount: float | int, currency: str | None) -> float:
    """Convert `amount` in `currency` to GBP. Unknown codes pass through."""
    if amount is None:
        raise ValueError("amount must not be None")
    if not currency:
        return float(amount)
    rate = _RATES_TO_GBP.get(currency.upper(), 1.0)
    return float(amount) * rate


def is_known_currency(currency: str | None) -> bool:
    """True when `currency` has a real rate in `_RATES_TO_GBP`.

    `to_gbp()` deliberately passes unknown codes through at rate 1.0 — safe
    for the internal salary_score band-overlap heuristic (over-including a
    candidate beats silently dropping one), but NOT safe for a user-facing
    figure: a PEN or BRL amount rendered at 1:1 as GBP is not a rough
    estimate, it is a wrong number. Callers that display converted amounts
    (rather than just ranking with them) should check this first and leave
    the figure unset when it is False. `None`/empty currency is treated as
    already-GBP (matches `to_gbp`'s and `normalize_salary`'s default), so it
    counts as "known".
    """
    if not currency:
        return True
    return currency.upper() in _RATES_TO_GBP


# ── Salary normalisation (moved here by slice 5, #483) ─────────────────────
# `normalize_salary` lived in `src/services/salary.py`, which went with the
# sourcing era. Its one remaining reader is the notification decision card
# (`services/delivery/decision_card.py`), and its whole job is "turn a stated
# band into annual GBP" — this module's subject — so it moved here rather than
# keeping a one-function module alive for it.

# Annualisation factors — simple workplace averages, not payroll-precise.
_FREQUENCY_ANNUAL: dict[str, int] = {
    "hourly": 2080,     # 40 h x 52 weeks
    "daily": 260,       # 5 days x 52 weeks
    "weekly": 52,
    "monthly": 12,
    "annual": 1,
    "unknown": 1,       # treat as already annual — safer than dropping
}


def _pick(obj: Any, key: str, default: Any = None) -> Any:
    """Tolerate both Pydantic models and plain dicts."""
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def normalize_salary(
    salary: Any,
    *,
    to_annual: bool = True,
    to_currency: str = "GBP",
) -> Optional[tuple[int, int]]:
    """Normalise a salary band to annual GBP integers.

    Args:
        salary: any object or dict carrying `min` / `max` / `currency` /
            `frequency` keys.
        to_annual: must be True for now — weekly/hourly bands are always
            rolled up. Kept as a parameter for future extension.
        to_currency: only `"GBP"` currently supported (rates above).

    Returns:
        `(min_gbp_annual, max_gbp_annual)` as ints. If only one of min/max is
        known, the other mirrors it (single-point band). If both are None,
        returns None — an absent salary is honest, an invented one is not.
    """
    if to_annual is False:
        raise NotImplementedError("Only annual normalisation is supported")
    if to_currency.upper() != "GBP":
        raise NotImplementedError("Only GBP target currency is supported")

    raw_min = _pick(salary, "min")
    raw_max = _pick(salary, "max")
    if raw_min is None and raw_max is None:
        return None

    currency = _pick(salary, "currency") or "GBP"
    raw_frequency = _pick(salary, "frequency") or "annual"
    # A str-enum's value is already the lower-case string; dicts carry either.
    frequency_str = (
        raw_frequency.value if hasattr(raw_frequency, "value") else str(raw_frequency)
    )
    factor = _FREQUENCY_ANNUAL.get(frequency_str.lower(), 1)

    def _convert(v: float | int | None) -> int | None:
        if v is None:
            return None
        return int(round(to_gbp(float(v) * factor, currency)))

    min_gbp = _convert(raw_min)
    max_gbp = _convert(raw_max)

    # Backfill the missing bound so downstream overlap math has a full band.
    if min_gbp is None:
        min_gbp = max_gbp
    if max_gbp is None:
        max_gbp = min_gbp

    # Both bounds are non-None here: we returned early when raw_min and
    # raw_max were both None, and the backfill above mirrors the survivor.
    lo = cast(int, min_gbp)
    hi = cast(int, max_gbp)

    # Guarantee min <= max even if the upstream had them swapped.
    if lo > hi:
        lo, hi = hi, lo

    return (lo, hi)
