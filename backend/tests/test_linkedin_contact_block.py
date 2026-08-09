"""The LinkedIn Contact block, structured instead of discarded.

That section was split out (so it bounded its neighbours) and then read by
nobody — the same "parsed and thrown away" shape as GitHub's identity block.

The host check here is deliberately NOT a substring test. CodeQL flagged
``"linkedin.com" in url`` as py/incomplete-url-substring-sanitization (high),
and it is wrong in both directions, which the tests below pin.
"""
from __future__ import annotations

from src.services.profile.linkedin_parser import (
    _extract_contact_fields,
    _is_linkedin_host,
)

# The owner's real export, verbatim — including the line-wrapped profile URL
# that a naive parser reassembles wrongly.
REAL_CONTACT = """+447442564327 (Mobile)
rahulranjith369@gmail.com
www.linkedin.com/in/ranjith-ai-
engineer (LinkedIn)
ranjith36963.github.io (Portfolio)"""


class TestContactIsStructured:
    def test_pulls_email_phone_url_and_websites_from_a_real_export(self) -> None:
        got = _extract_contact_fields(REAL_CONTACT)
        # The FULL address. A pre-existing _EMAIL_RE with a capture group once
        # shadowed the contact one, so findall returned the group and this
        # stored "rahulranjith369".
        assert got["email"] == "rahulranjith369@gmail.com"
        assert got["phone"] == "+447442564327"
        assert got["linkedin_url"] == "linkedin.com/in/ranjith-ai-engineer"
        # The portfolio site survives; the linkedin.com URL is not repeated
        # here because it already has its own field.
        assert got["websites"] == ["ranjith36963.github.io"]

    def test_absent_section_yields_nothing_not_empty_keys(self) -> None:
        assert _extract_contact_fields("") == {}
        assert _extract_contact_fields("   \n  ") == {}

    def test_a_year_is_not_a_phone_number(self) -> None:
        got = _extract_contact_fields("Member since 2019\nada@example.com")
        assert "phone" not in got
        assert got["email"] == "ada@example.com"


class TestLinkedinHostCheckIsNotASubstringMatch:
    def test_real_linkedin_hosts_are_recognised(self) -> None:
        for url in (
            "linkedin.com/in/ada",
            "www.linkedin.com/in/ada",
            "https://www.linkedin.com/in/ada",
            "https://uk.linkedin.com/in/ada",
        ):
            assert _is_linkedin_host(url) is True, url

    def test_a_lookalike_domain_is_not_linkedin(self) -> None:
        # The direction that matters for safety: a host merely CONTAINING the
        # string must not be accepted as LinkedIn.
        assert _is_linkedin_host("linkedin.com.attacker.io/in/ada") is False
        assert _is_linkedin_host("https://evil-linkedin.com.example.net") is False

    def test_a_personal_site_containing_the_word_is_kept(self) -> None:
        # The direction that matters for the user: their own site must not be
        # swallowed by a sloppy filter.
        assert _is_linkedin_host("notlinkedin.com") is False
        assert _is_linkedin_host("mylinkedin.com/portfolio") is False

    def test_a_lookalike_survives_as_a_website(self) -> None:
        got = _extract_contact_fields("ada@example.com\nnotlinkedin.com (Personal)")
        assert got["websites"] == ["notlinkedin.com"]
