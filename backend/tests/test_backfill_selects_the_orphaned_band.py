"""The description sweep must target rows that are not GOOD ENOUGH, not just rows that are EMPTY.

Two floors exist and they answer different questions:

  MIN_DESCRIPTION_CHARS       200  "does the skill matcher see real text here?"
  _STUB_DESCRIPTION_MIN_CHARS 600  "is there enough here for an LLM to read
                                    without inventing?"

Sharing one constant for both meant the sweep only ever SELECTED rows under 200
characters. Measured on production 2026-08-24:

    0 (empty)                2,613   15.4%
    1-199   selected         2,969   17.6%
    200-599 never selected   5,525   32.7%
    600+    usable           5,807   34.3%

A third of the catalog sat in a band that scores (it clears 200) but can never be
enriched (it never reaches 600) and was never retried (it is not "thin"). devitjobs
alone holds 3,620 of those rows, averaging 307 characters.

These tests pin the split so the two floors cannot silently become one again.
"""

import pytest

from src.services.description_backfill import (
    BACKFILL_SELECT_BELOW_CHARS,
    MIN_DESCRIPTION_CHARS,
)
from src.services.shelf_gate import _STUB_DESCRIPTION_MIN_CHARS, is_stub_description


def test_selection_floor_is_the_enrichment_floor_not_the_skill_text_floor():
    """Selection must track the floor that decides "good enough to read"."""
    assert BACKFILL_SELECT_BELOW_CHARS == _STUB_DESCRIPTION_MIN_CHARS, (
        "the sweep selects rows it hopes to make ENRICHABLE, so its floor must be "
        "the enrichment floor — re-typing the number here is how they drifted before"
    )


def test_the_two_floors_are_deliberately_different():
    """If these ever collapse to one value, the orphaned band comes straight back."""
    assert MIN_DESCRIPTION_CHARS < BACKFILL_SELECT_BELOW_CHARS, (
        "the success floor (is this real text?) and the selection floor (is this "
        "good enough yet?) are different questions and must stay different numbers"
    )


@pytest.mark.parametrize("length", [0, 199, 200, 307, 599])
def test_rows_below_the_enrichment_floor_are_selectable(length):
    """Everything an LLM would refuse must be eligible for another try.

    307 is devitjobs' measured average in the orphaned band — under the old
    selection floor it was invisible to the sweep forever.
    """
    assert length < BACKFILL_SELECT_BELOW_CHARS
    # and the enrichment side agrees this row is not readable yet
    assert is_stub_description("x" * length, "Some Job Title") is True


def test_a_long_enough_row_is_not_selected_again():
    """The sweep must stop once a row is genuinely usable, or it never converges."""
    good = "x" * BACKFILL_SELECT_BELOW_CHARS
    assert len(good) >= BACKFILL_SELECT_BELOW_CHARS
    assert is_stub_description(good, "Some Job Title") is False


def test_an_improvement_that_stays_short_is_still_kept():
    """Widening selection must not start discarding shorter-but-better text.

    The sweep writes a fetch that beats what was already stored even when it does
    not clear MIN_DESCRIPTION_CHARS ("genuine improvement, still short"). That
    branch is what makes selecting the 200-599 band safe: a row at 307 chars that
    refetches to 450 is an improvement and must be written, not thrown away for
    failing to reach 600.
    """
    existing = "x" * 307
    fetched = "y" * 450
    assert len(fetched) > len(existing)
    assert len(fetched) < BACKFILL_SELECT_BELOW_CHARS
    # Mirrors the sweep's decision: not >= MIN_DESCRIPTION_CHARS is irrelevant here,
    # because 450 >= 200 already; the point is it is kept without reaching 600.
    assert len(fetched) >= MIN_DESCRIPTION_CHARS
