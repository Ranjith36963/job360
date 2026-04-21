"""Pillar 2 Batch 2.9 — hard-coded FX rates for salary normalisation.

Rates are rough annual averages (as of 2026-Q1). The plan explicitly holds
live FX rates out of scope — salary comparisons are deliberately coarse
because we're grading a match, not a payroll run.

Unknown currencies return 1.0 (treated as already-GBP) which is the safe
degraded behaviour: better to over-include in a £-expected range than to
silently drop a candidate because we couldn't classify their currency.
"""
from __future__ import annotations

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
