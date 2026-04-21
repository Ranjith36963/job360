"""Pillar 2 Batch 2.9 — salary normalisation.

`normalize_salary()` turns a `JobEnrichment.salary` (nested Pydantic
`SalaryBand` OR any dict with min/max/currency/frequency keys) into a
comparable `(min_gbp_annual, max_gbp_annual)` tuple.

Returns `None` when there is no actionable salary signal. Callers interpret
that as a neutral 5/10 band in `salary_score()` — per the research report's
recommendation to avoid punishing jobs for missing pay info.
"""
from __future__ import annotations

from typing import Any, Optional

from src.core.fx import to_gbp


# Annualisation factors — simple workplace averages, not payroll-precise.
_FREQUENCY_ANNUAL: dict[str, int] = {
    "hourly": 2080,     # 40 h × 52 weeks
    "daily": 260,       # 5 days × 52 weeks
    "weekly": 52,
    "monthly": 12,
    "annual": 1,
    "unknown": 1,       # treat as already annual — safer than dropping
}


def _pick(obj: Any, key: str, default=None):
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
        salary: a `SalaryBand` model OR a dict with `min/max/currency/frequency`.
        to_annual: must be True for now — weekly/hourly bands are always
            rolled up. Kept as a parameter for future extension.
        to_currency: only `"GBP"` currently supported (rates in `core.fx`).

    Returns:
        `(min_gbp_annual, max_gbp_annual)` as ints. If only one of
        min/max is known, the other mirrors it (single-point band). If
        both are None, returns None — callers use the neutral band in
        `salary_score()`.
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
    # SalaryFrequency is a str-enum whose value is already the lower-case
    # string. Dicts will carry either the string or the enum directly.
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

    # Guarantee min <= max even if the upstream had them swapped.
    if min_gbp > max_gbp:
        min_gbp, max_gbp = max_gbp, min_gbp

    return (min_gbp, max_gbp)
