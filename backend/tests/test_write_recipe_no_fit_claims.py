"""People hunt, 2026-11-19 09:08 run: the fresh reviewer stored 8 of 9 drafts weak for claims the profile does not hold:
"the ... role looks like a fit", "interested in the Junior Python ... role", a city the profile never names, "a role
Haystack is hiring for" (the ad was for a partner), and an internal "(recipient evidence: ...)" note left in a draft.
The write recipe's own example said "the Data Engineer role fits that". It now says how to name the role and forbids
each of these.
"""
from __future__ import annotations

from src.api.routes.recipes import load_recipe


def _flat(text: str) -> str:
    return " ".join(text.split()).lower()


def test_the_write_recipe_names_the_role_without_claiming_fit():
    write = _flat(load_recipe("write").text)
    assert "fits that" not in write and "looks like a fit" not in write.replace('never "looks like a fit"', "")
    assert 'i saw the <role> role' in write
    assert 'never "looks like a fit"' in write
    assert "a city or a narrower title the profile does not name" in write
    assert "who posted it as the ad says" in write
    assert "never leave a note, evidence or a url of yours in the message" in write
