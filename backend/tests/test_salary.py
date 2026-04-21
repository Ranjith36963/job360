"""Pillar 2 Batch 2.9 — tests for the salary normaliser."""
from __future__ import annotations

import pytest

from src.services.job_enrichment_schema import SalaryBand, SalaryFrequency
from src.services.salary import normalize_salary
from src.core.fx import to_gbp


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------


def test_fx_gbp_to_gbp_is_identity():
    assert to_gbp(50000, "GBP") == 50000.0


def test_fx_usd_to_gbp_scales():
    # Rate is 0.79 — 100k USD ≈ 79k GBP
    assert to_gbp(100_000, "USD") == pytest.approx(79_000, rel=1e-6)


def test_fx_unknown_currency_passes_through():
    """Safer to leave unknown codes at 1.0 than to silently drop a candidate."""
    assert to_gbp(50000, "XYZ") == 50000.0


def test_fx_none_currency_passes_through():
    assert to_gbp(50000, None) == 50000.0


# ---------------------------------------------------------------------------
# Salary normalisation — happy path
# ---------------------------------------------------------------------------


def test_normalize_gbp_annual_round_trip():
    band = SalaryBand(min=60000, max=90000, currency="GBP", frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) == (60000, 90000)


def test_normalize_hourly_scales_to_annual():
    band = SalaryBand(min=50, max=80, currency="GBP", frequency=SalaryFrequency.HOURLY)
    # 50 × 2080 = 104000, 80 × 2080 = 166400
    assert normalize_salary(band) == (104_000, 166_400)


def test_normalize_daily_scales_to_annual():
    band = SalaryBand(min=300, max=400, currency="GBP", frequency=SalaryFrequency.DAILY)
    # 300 × 260 = 78_000, 400 × 260 = 104_000
    assert normalize_salary(band) == (78_000, 104_000)


def test_normalize_monthly_scales_to_annual():
    band = SalaryBand(min=5000, max=8000, currency="GBP", frequency=SalaryFrequency.MONTHLY)
    assert normalize_salary(band) == (60_000, 96_000)


def test_normalize_usd_annual_converts_to_gbp():
    band = SalaryBand(min=100_000, max=150_000, currency="USD",
                      frequency=SalaryFrequency.ANNUAL)
    result = normalize_salary(band)
    # 100k × 0.79 = 79_000, 150k × 0.79 = 118_500
    assert result == (79_000, 118_500)


# ---------------------------------------------------------------------------
# Salary normalisation — edge cases
# ---------------------------------------------------------------------------


def test_normalize_returns_none_when_both_bounds_missing():
    band = SalaryBand(currency="GBP", frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) is None


def test_normalize_mirrors_single_point_band_min_only():
    band = SalaryBand(min=60000, currency="GBP", frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) == (60_000, 60_000)


def test_normalize_mirrors_single_point_band_max_only():
    band = SalaryBand(max=80000, currency="GBP", frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) == (80_000, 80_000)


def test_normalize_swapped_bounds_are_corrected():
    band = SalaryBand(min=90000, max=60000, currency="GBP",
                      frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) == (60_000, 90_000)


def test_normalize_accepts_dict_input():
    """Tolerate plain dicts from the DB-loaded JSON path."""
    result = normalize_salary({
        "min": 60000, "max": 90000, "currency": "GBP", "frequency": "annual",
    })
    assert result == (60_000, 90_000)


def test_normalize_dict_with_enum_instance():
    result = normalize_salary({
        "min": 5000, "max": 8000, "currency": "GBP",
        "frequency": SalaryFrequency.MONTHLY,
    })
    assert result == (60_000, 96_000)


def test_normalize_unknown_frequency_treated_as_annual():
    """Silent degradation — don't drop a candidate because the LLM emitted
    'unknown' for frequency."""
    band = SalaryBand(min=60000, max=90000, currency="GBP",
                      frequency=SalaryFrequency.UNKNOWN)
    assert normalize_salary(band) == (60_000, 90_000)


def test_normalize_missing_currency_defaults_to_gbp():
    """Unknown currency → treated as GBP (same as the FX identity case)."""
    band = SalaryBand(min=60000, max=90000, frequency=SalaryFrequency.ANNUAL)
    assert normalize_salary(band) == (60_000, 90_000)


def test_normalize_rejects_non_gbp_target_currency():
    band = SalaryBand(min=60000, max=90000, currency="GBP",
                      frequency=SalaryFrequency.ANNUAL)
    with pytest.raises(NotImplementedError):
        normalize_salary(band, to_currency="USD")


def test_normalize_rejects_non_annual_target():
    band = SalaryBand(min=60000, max=90000, currency="GBP",
                      frequency=SalaryFrequency.ANNUAL)
    with pytest.raises(NotImplementedError):
        normalize_salary(band, to_annual=False)


def test_normalize_euros_to_gbp():
    band = SalaryBand(min=50_000, max=70_000, currency="EUR",
                      frequency=SalaryFrequency.ANNUAL)
    # 50k × 0.86 = 43_000, 70k × 0.86 = 60_200
    assert normalize_salary(band) == (43_000, 60_200)
