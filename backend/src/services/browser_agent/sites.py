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
from typing import Any, Optional, Sequence
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
# packages/field-detector/src/adapters.ts:323-325 (smartrecruiters), :519-520 (teamtailor; tips from
# its e2e/mock-site/ats/teamtailor.html:11-13, inspected live by its author). A suffix match on the
# host, never a substring of the URL (registry.py:24-26).
SITES: tuple[Site, ...] = (
    Site("greenhouse", ("greenhouse.io",), (
        "Upload the CV with the Attach button and check upload_confirmed; a file box set directly can fail silently.",
        "Dropdowns are react-select: choose by the exact option text, then read the value back.",
        "Its invisible reCAPTCHA is normal and no stop; an image challenge or a tick box is (needs_you).",
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
        "Many pages (form_step says which): fill one, read every value back, then Save and Continue.",
        "Errors show only after Save: fix every field marked invalid, then Save again. Still stuck after 2 "
        "Saves: stop and record blocked.",
        "Dates are separate Month and Year boxes: type the real numbers.",
        "Work and school entries: Add one, fill it, then Add the next (the new one is the last on the page).",
        "A sign-in or create-account page is Needs you (account_needed); never make an account.",
    )),
    Site("smartrecruiters", ("smartrecruiters.com", "smartr.me"), (
        "Fields sit inside shadow DOM: they are listed normally; look again after each page change.",
    )),
    Site("icims", ("icims.com",), (
        "The form is often inside an iframe; refs work across frames as usual.",
        "A sign-in page is Needs you; never make an account.",
    ), deny_hosts=("login.icims.com",)),
    Site("teamtailor", ("teamtailor.com",), (
        "Required fields end in a hidden \"Required\": the field name may read \"First nameRequired\".",
        "Yes/No questions start with a short title before the question: answer the question itself.",
        "Consent boxes: the privacy one is needed to apply; future-job contact is the user's choice, so ask.",
    )),
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


# Any career page, agency sites included (recruitment-agency forms: NOT IN REFERENCE, so only the
# generic rules apply there).
# "Is this the form, or still the ad?": a file box, or 3+ labelled fields (offeros
# apps/extension/src/lib/autofill/recipes.ts:104-126, wrong on the safe side on purpose).
MIN_LABELLED_FIELDS = 3
_FIELD_TAGS = ("input", "select", "textarea")
_NOT_FIELD_TYPES = ("submit", "button", "image", "reset", "hidden")
# A trap field for bots: its label says to leave it empty (ats-autofill skills/fill-app/dom-toolkit.js:790).
# "Leave blank if none" is an ordinary optional field, not a trap.
# Narrower than the reference on purpose: it must say "this" / "the" field, because "Middle name
# (leave blank)" is an ordinary optional field and refusing it would block a real answer.
_HONEYPOT = re.compile(
    r"leave (?:this|the)\s+(?:field\s+|box\s+)?(?:blank|empty)(?!\s*(?:if|unless|when|for)\b)"
    r"|do ?n['o]?t fill (?:this|in)",
    re.IGNORECASE,
)


def looks_like_application(elements: Sequence[dict[str, Any]]) -> bool:
    fields = [
        e for e in elements
        if e.get("tag") in _FIELD_TAGS and e.get("type") not in _NOT_FIELD_TYPES and str(e.get("name") or "").strip()
    ]
    return any(e.get("type") == "file" for e in fields) or len(fields) >= MIN_LABELLED_FIELDS


def is_honeypot(name: str) -> bool:
    return bool(_HONEYPOT.search(name or ""))
