"""Three bugs the first real-world run of the link path found (2026-09-19),
each pinned at the unit it lives in. No sockets, no DB.

1. ``peer_verdict`` — aiohttp releases a small page's connection before the
   fetcher can read the socket peer; "unknown peer" used to be ``ssrf_denied``
   (7 of 15 fetches of a public Ashby ad). Unknown + something approved for
   this hop = accepted; unknown + nothing approved = still denied; a readable
   peer still has to be one the resolver approved.
2. ``_undouble_html`` — a JSON-LD description that is entity-escaped HTML
   (LinkedIn) came out as "pstrongAI Engineer/strong".
3. ``URL_FETCH_MIN_DESCRIPTION_CHARS`` — a JS-rendered board (Greenhouse
   job-boards) gave the heuristic rung 331 chars of page chrome, which was
   handed to the form as the ad.
"""
from __future__ import annotations

from src.core import settings
from src.services.fetch import extract
from src.services.fetch.fetcher import peer_verdict

PUBLIC = frozenset({"93.184.216.34"})


# ── 1. the peer check ─────────────────────────────────────────────────────────


def test_unknown_peer_is_accepted_when_this_hop_approved_an_address():
    assert peer_verdict(None, PUBLIC) is None


def test_unknown_peer_is_still_denied_when_nothing_was_approved():
    assert peer_verdict(None, frozenset()) is not None


def test_a_readable_peer_must_still_be_approved():
    assert peer_verdict("93.184.216.34", PUBLIC) is None
    assert peer_verdict("127.0.0.1", PUBLIC) is not None
    assert peer_verdict("93.184.216.35", PUBLIC) is not None


# ── 2. double-encoded JSON-LD ────────────────────────────────────────────────


def _json_ld_page(description: str) -> str:
    import json

    posting = {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": "AI Engineer",
        "hiringOrganization": {"@type": "Organization", "name": "Change Digital"},
        "description": description,
    }
    return f"<html><head><script type='application/ld+json'>{json.dumps(posting)}</script></head><body></body></html>"


def test_entity_escaped_json_ld_description_reads_as_words_not_tag_names():
    page = _json_ld_page("&lt;p&gt;&lt;strong&gt;AI Engineer (LLMs)&lt;/strong&gt;&lt;/p&gt;&lt;p&gt;Build things.&lt;/p&gt;")
    out = extract.extract_job_fields(page, max_depth=3, budget_s=2.0)
    assert out.description == "AI Engineer (LLMs) Build things."
    assert "pstrong" not in out.description


def test_real_html_json_ld_description_is_unchanged():
    page = _json_ld_page("<p><strong>AI Engineer</strong></p><p>Build things.</p>")
    out = extract.extract_job_fields(page, max_depth=3, budget_s=2.0)
    assert out.description == "AI Engineer Build things."


def test_plain_text_mentioning_an_escaped_bracket_is_not_double_unescaped():
    page = _json_ld_page("Use x &lt; y in the code and &amp;amp; stays literal.")
    out = extract.extract_job_fields(page, max_depth=3, budget_s=2.0)
    # one unescape by the tag-stripper: "&lt;" -> "<" (then scrubbed), "&amp;amp;" -> "&amp;"
    assert "&amp;" in out.description
    assert "pstrong" not in out.description


def test_block_boundaries_become_word_boundaries_and_inline_tags_do_not():
    page = _json_ld_page("<p><strong>AI Engineer (LLMs)</strong></p><p>This is exciting.</p><ul><li>One</li><li>Two</li></ul>")
    out = extract.extract_job_fields(page, max_depth=3, budget_s=2.0)
    assert out.description == "AI Engineer (LLMs) This is exciting. One Two"
    inline = _json_ld_page("<p>The <b>A</b>I team</p>")
    assert extract.extract_job_fields(inline, max_depth=3, budget_s=2.0).description == "The AI team"


def test_short_json_ld_strings_are_unescaped_once():
    import json

    posting = {
        "@context": "https://schema.org", "@type": "JobPosting",
        "title": "AI &amp; ML Engineer", "description": "<p>Build things that matter to people.</p>",
        "hiringOrganization": {"@type": "Organization", "name": "Change Digital &ndash; Digital &amp; Tech"},
    }
    page = f"<html><head><script type='application/ld+json'>{json.dumps(posting)}</script></head></html>"
    out = extract.extract_job_fields(page, max_depth=3, budget_s=2.0)
    assert out.title == "AI & ML Engineer"
    assert out.company == "Change Digital – Digital & Tech"


# ── 3. junk pre-fill from a JS-rendered page ─────────────────────────────────


_CHROME = "<html><head><title>Job Description | Board</title></head><body><a>Skip to Content</a><p>Jump to the top of the page. Are you a returning user?</p></body></html>"


def test_short_heuristic_description_is_dropped(monkeypatch):
    monkeypatch.setattr(settings, "URL_FETCH_MIN_DESCRIPTION_CHARS", 400)
    out = extract.extract_job_fields(_CHROME, max_depth=3, budget_s=2.0)
    assert out.description == ""
    assert "description" not in out.found


def test_the_cap_does_not_touch_a_structured_description(monkeypatch):
    monkeypatch.setattr(settings, "URL_FETCH_MIN_DESCRIPTION_CHARS", 10_000)
    out = extract.extract_job_fields(_json_ld_page("<p>Short but the site's own answer.</p>"), max_depth=3, budget_s=2.0)
    assert out.description == "Short but the site's own answer."
    assert out.source_hint == "json_ld"
