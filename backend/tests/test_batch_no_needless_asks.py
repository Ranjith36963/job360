"""2026-11-18 (21:41 bulk apply run): two asks the run should never have sent the user. 1066 asked "leave
the optional Photo upload blank?" (an optional field the user has no fact for stays blank), and 1079 asked
"no fit has been judged... Apply or skip?" (the run judges fit itself with save_fit; the user's score line
then decides). batch.md now tells the run both, in the Asks paragraph."""
from __future__ import annotations


def _batch() -> str:
    from src.api.routes.recipes import load_recipe

    return " ".join(load_recipe("batch").text.split())


def test_batch_recipe_never_asks_about_an_optional_field():
    assert "Never ask about an optional field" in _batch()


def test_batch_recipe_judges_a_missing_fit_itself():
    batch = _batch()
    assert "never ask apply-or-skip" in batch and "`save_fit`" in batch


def test_batch_recipe_stays_within_its_line_cap():
    from src.api.routes.recipes import load_recipe

    assert len(load_recipe("batch").text.splitlines()) <= 70
