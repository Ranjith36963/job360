"""Which job site a page is, and whether it says the application went — pure, no browser.

docs/product/BROWSER_AGENT.md "Site playbooks". ``site_for`` names the applicant
system from the page's own address (and its frames: Greenhouse and iCIMS forms
often sit in an iframe on a company page), so the assistant gets that site's
short tips with every look. ``outcome_for`` reads the page after a submit:
only a POSITIVE confirmation counts as sent (ApplYourself src/apply/fill.py:69-79,
"matching none of these means unconfirmed, never failed"); a site's own
refusal text says the click did not take (fill.py:109-124).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional, Sequence
from urllib.parse import urlsplit


@dataclass(frozen=True)
class Site:
    name: str
    hosts: tuple[str, ...]  # a host matches when it IS one of these or ends with "." + one
    tips: tuple[str, ...]
    deny_hosts: tuple[str, ...] = ()


# Host lists: ApplYourself src/discovery/sources/ats/registry.py:56-95 (greenhouse, lever,
# ashby, workday); ats-autofill skills/fill-app/workday.md:8 (myworkdaysite); offeros
# apps/extension/src/lib/autofill/recipes.ts:48-53 (icims, never login.icims.com); PINGx969
# packages/field-detector/src/adapters.ts:323-325 (smartrecruiters). A suffix match on the
# host, never a substring of the URL (registry.py:24-26).
SITES: tuple[Site, ...] = (
    Site("greenhouse", ("greenhouse.io",), (
        "Upload the CV with the Attach button and check upload_confirmed; a file box set directly can fail silently.",
        "Dropdowns are react-select: choose by the exact option text, then read the value back.",
        "An invisible reCAPTCHA is normal here; only a visible challenge is a CAPTCHA.",
    )),
    Site("lever", ("lever.co",), (
        "Location: type, then choose a suggestion from the list; typed text alone is rejected on submit.",
        "After the CV upload Lever re-fills fields from the CV: look again and fix what it changed.",
        "A cookie banner can cover the first fields: close it first.",
        "Ticking a box or focusing location can raise a CAPTCHA: that is Needs you.",
    )),
    Site("ashby", ("ashbyhq.com",), (
        "There is no <form>: fields load late, so look again if the list is short.",
        "Yes/No questions are two buttons; pressed=true on a button means it is chosen.",
        "\"Your form needs corrections\" or \"Missing entry for required field\" after submit means it did NOT go.",
        "One application per email per company: a second job at the same company can merge. Ask the user.",
    )),
    Site("workday", ("myworkdayjobs.com", "myworkdaysite.com"), (
        "Many pages: fill one, read every value back, then Save and Continue. Errors only show after Save.",
        "A sign-in or create-account page is Needs you (account_needed); never make an account.",
    )),
    Site("smartrecruiters", ("smartrecruiters.com", "smartr.me"), (
        "Fields sit inside shadow DOM: they are listed normally; look again after each page change.",
    )),
    Site("icims", ("icims.com",), (
        "The form is often inside an iframe; refs work across frames as usual.",
        "A sign-in page is Needs you; never make an account.",
    ), deny_hosts=("login.icims.com",)),
)


def _host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _matches(site: Site, host: str) -> bool:
    if not host or host in site.deny_hosts:
        return False
    return any(host == h or host.endswith("." + h) for h in site.hosts)


def site_for(page_url: str, frame_urls: Sequence[str] = ()) -> Optional[Site]:
    """The applicant system this page is: the page's own host first, then its frames in order."""
    for url in (page_url, *frame_urls):
        host = _host(url)
        for site in SITES:
            if _matches(site, host):
                return site
    return None


# Positive acknowledgements only (ApplYourself src/apply/fill.py:69-79; the Ashby line is
# its verbatim wording). Extend, never invert: no match means "not confirmed", not "failed".
_CONFIRMED = re.compile(
    r"thank you for applying|thanks for applying|"
    r"application (?:has been |was )?(?:successfully )?(?:submitted|received|sent)|"
    r"your application was (?:successfully )?(?:submitted|sent)|"
    r"we(?:'ve| have) received your application",
    re.IGNORECASE,
)
# The site saying it did not take the click (ApplYourself src/apply/fill.py:109-124, Ashby's measured
# wording). Only ever reopens a submit; absence proves nothing. Reported as these fixed phrases, never
# the page's own words: page text stays inside the untrusted marker.
_REFUSED: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("Your form needs corrections", re.compile(r"your form needs corrections", re.IGNORECASE)),
    ("Missing entry for required field", re.compile(r"missing entry for required field", re.IGNORECASE)),
    ("Please try again when they're finished", re.compile(r"please try again when they", re.IGNORECASE)),
    ("Error verifying your application", re.compile(r"error verifying your application", re.IGNORECASE)),
)


@dataclass(frozen=True)
class Outcome:
    confirmed: bool
    refused: tuple[str, ...]


def outcome_for(site: Optional[Site], url: str, text: str) -> Outcome:
    """What the page says about a submit. Refusal wins over confirmation: a page can still carry
    an old "thank you" line while it re-renders the form with an error."""
    refused = tuple(label for label, pattern in _REFUSED if pattern.search(text or ""))
    path = urlsplit(url).path.rstrip("/") if url else ""
    # Lever answers its native form POST with a 302 to /thanks (ApplYourself fill.py:166-173).
    lever_thanks = site is not None and site.name == "lever" and path.endswith("/thanks")
    confirmed = not refused and (lever_thanks or bool(_CONFIRMED.search(text or "")))
    return Outcome(confirmed, refused)
