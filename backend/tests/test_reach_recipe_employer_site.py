"""People hunt, 2026-11-19 runs (Kits orders, 02:05): with free tools only, Sprouts and Clay rarely agree, so few people
were proven; the owner already ruled that the employer's own team page proves a person. The 02:21 run also handed
its outreach helper a free-text brief, then recorded one whole-note row per review (all stored weak,
claim_not_in_quote), and sent `searched` clay 0 for companies where Clay never ran. The reach recipe now says each.
"""
from __future__ import annotations

from src.api.routes.recipes import load_recipe


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def test_the_employer_site_comes_before_the_lookups():
    reach = _flat(load_recipe("reach").text)
    site = reach.index("the employer's own site")
    assert site < reach.index("then the free lookups"), "the employer's site is the first source after warm contacts"
    assert "is proven by (a)" in reach[site:site + 300]


def test_the_review_rows_and_the_searched_record_are_spelled_out():
    reach = _flat(load_recipe("reach").text)
    assert "the doctor's row outreach review pasted in" in reach
    assert "one row per sentence about the recipient" in reach and "not one row for a whole note" in reach
    assert "leave out a source you did not run" in reach
