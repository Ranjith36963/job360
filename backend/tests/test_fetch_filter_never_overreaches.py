"""The fetch-time skip must never drop what the door would admit.

`_is_uk_or_remote` is a cheap pre-filter, not the door. Rule #30 says ONE
chokepoint decides what enters the catalog. A pre-filter is only legitimate while
it stays strictly WEAKER than that chokepoint — the moment it drops something the
door would have kept, it becomes a second, hidden door with no logging.

WHY THIS FILE EXISTS
--------------------
A first version of the rewritten filter asked the gate about whatever string it
was handed, and four callers hand it an ad DESCRIPTION. The gate splits on `/`,
so the closing tag `</li>` produced the segment `li` — and `li`, `br`, `td`,
`tr`, `th`, `hr` are all real entries in `foreign_admin.txt` (Liechtenstein,
Brazil, Chad, Turkey, Thailand, Croatia). Every HTML job ad read as a foreign
country and would have been dropped at fetch, before anything could log it.

That is the exact shape this repo keeps hitting: a check that answers confidently
about a different question than the one asked. So this test does not check that
the filter "works" — it checks the INVARIANT that makes a pre-filter safe.
"""

from __future__ import annotations

import pytest

from src.services.uk_gate import check_uk
from src.sources.base import _is_uk_or_remote

# Descriptions and location strings that a UK-eligible job really can carry.
# Each MUST survive the fetch filter, because the door would admit them.
MUST_SURVIVE = [
    "<li>Senior Engineer</li><li>Manchester</li>",
    "<p>Great role</p><br/>Based in Leeds",
    "Remote (UK)",
    "London",
    "Manchester, Remote",
    "Hybrid - Bristol / Bath",
    "United Kingdom",
    "",
    "Remote - UK/Europe timezone",
    "<div><ul><li>Python</li><li>FastAPI</li></ul></div>",
]

# Bare location strings that unambiguously name somewhere else. Dropping these
# early is the whole point of the pre-filter.
SHOULD_DROP = [
    "Bangalore, India",
    "Toronto, Canada",
    "Sydney, Australia",
]


@pytest.mark.parametrize("text", MUST_SURVIVE)
def test_the_filter_never_drops_something_uk_eligible(text: str) -> None:
    assert _is_uk_or_remote(text) is True, (
        f"The fetch filter dropped {text!r}. Nothing may be dropped here that the "
        "door would have admitted — a pre-filter that overreaches is a second, "
        "silent door. If this is markup or prose, hand it to check_uk instead."
    )


@pytest.mark.parametrize("text", SHOULD_DROP)
def test_the_filter_still_earns_its_place(text: str) -> None:
    """A filter that drops nothing is dead weight — pin that it still bites."""
    assert _is_uk_or_remote(text) is False, (
        f"{text!r} names a foreign place and should be skipped before scoring."
    )


@pytest.mark.parametrize("text", MUST_SURVIVE + SHOULD_DROP)
def test_every_drop_is_one_the_door_also_refuses(text: str) -> None:
    """The invariant, stated directly: dropped => the door refuses it too.

    This is the assertion that would have caught the HTML bug. It does not care
    what the filter's own reasoning is; it only cares that the filter never
    disagrees with the chokepoint in the dangerous direction.
    """
    if _is_uk_or_remote(text):
        return  # kept — the door gets its say, nothing to prove here
    verdict = check_uk(location=text, title="Engineer", description=text, source="test")
    assert verdict.allowed is False, (
        f"The fetch filter dropped {text!r}, but the door would have ADMITTED it "
        f"(reason={getattr(verdict, 'reason', None)!r}). That row is now lost with "
        "no log line anywhere. Narrow the filter, do not widen the door."
    )
