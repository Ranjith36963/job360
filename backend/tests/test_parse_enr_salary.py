"""F6 — `_parse_enr_salary` must return TRUE annual GBP, not a raw number
wearing a `_gbp` suffix.

Direct unit tests on the decoder (no DB/HTTP needed — pure function).
Covers the four cases called out in the fix: GBP/annual passthrough,
USD/monthly conversion, hourly annualisation, and an unresolvable currency
(fields must come back unset, never a fabricated 1:1 number).
"""
from __future__ import annotations

import json

from src.api.routes.jobs import _parse_enr_salary


def _blob(**overrides) -> str:
    payload = {"min": 60000, "max": 90000, "currency": "GBP", "frequency": "annual"}
    payload.update(overrides)
    return json.dumps(payload)


def test_gbp_annual_passes_through_unchanged():
    """Already-annual GBP: numbers must be untouched (this is the
    behaviour the existing test_api.py integration test pins)."""
    smin, smax, period, currency = _parse_enr_salary(
        _blob(min=70000, max=90000, currency="GBP", frequency="annual")
    )
    assert (smin, smax, period, currency) == (70000, 90000, "annual", "GBP")


def test_usd_monthly_converts_to_annual_gbp():
    """This is the bug: a $5,000/month US job must NOT render as '£5k'.
    5000 * 12 months * 0.79 USD-GBP rate = 47_400."""
    smin, smax, period, currency = _parse_enr_salary(
        _blob(min=5000, max=7000, currency="USD", frequency="monthly")
    )
    # 5000*12=60_000 * 0.79 = 47_400 ; 7000*12=84_000 * 0.79 = 66_360
    assert (smin, smax, period, currency) == (47_400, 66_360, "annual", "USD")


def test_hourly_annualises():
    smin, smax, period, currency = _parse_enr_salary(
        _blob(min=25, max=40, currency="GBP", frequency="hourly")
    )
    # 25*2080=52_000 ; 40*2080=83_200
    assert (smin, smax, period, currency) == (52_000, 83_200, "annual", "GBP")


def test_unknown_currency_leaves_amount_fields_unset():
    """PEN/BRL (and anything else not in core.fx's rate table) have no
    real conversion rate. `to_gbp()` would pass them through at 1:1 for the
    internal scoring heuristic — fine for ranking, not fine to show a user
    as a fact. No number must beat a wrong number: amounts unset, currency
    still echoed back so the caller knows what the ad actually said."""
    smin, smax, period, currency = _parse_enr_salary(
        _blob(min=3000, max=5000, currency="PEN", frequency="monthly")
    )
    assert (smin, smax, period, currency) == (None, None, None, "PEN")


def test_missing_blob_returns_all_none():
    assert _parse_enr_salary(None) == (None, None, None, None)
    assert _parse_enr_salary("") == (None, None, None, None)


def test_malformed_json_returns_all_none():
    assert _parse_enr_salary("{not json") == (None, None, None, None)


def test_no_amounts_returns_none_but_still_honest_about_currency():
    """min and max both absent → normalize_salary() returns None; currency
    is still echoed (it's informational, not a number that can be wrong)."""
    smin, smax, period, currency = _parse_enr_salary(
        json.dumps({"currency": "EUR", "frequency": "annual"})
    )
    assert (smin, smax, period, currency) == (None, None, None, "EUR")
