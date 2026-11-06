"""Who wrote an inbox item (owner report #14, 2026-11-05) - a person, a job alert, or a robot.

A recruiter's reply ("Richard Samson replied about agentic AI roles") sat in the same flat list
as months of job-alert digests. ``sender_kind`` lets whats_new list the people first.

Deterministic and pure: the sender string the assistant stored, read with ``str`` methods only
(no model, no regex on caller text). The closed lists below are senders, not job judgements:
Job360 still never reads mail or ranks a job (VISION rule 4). ``List-Unsubscribe`` is not stored
on an inbox item, so it is not read here.

Order of the checks: a relay that carries one person's own message (LinkedIn messages, Indeed's
employer mail) is a person; a listed job-alert address, local part, alert-only host or bulk local-part
word is an alert (never a whole board domain: recruiters write from it too); a
no-reply address is automated; anything else is a person who wrote to the user.
"""
from __future__ import annotations

PERSON, ALERT, AUTOMATED = "person", "alert", "automated"
SENDER_KINDS = (PERSON, ALERT, AUTOMATED)

# Addresses that forward ONE person's own words (a recruiter's InMail, an employer's reply).
RELAY_ADDRESSES = frozenset({
    "messages-noreply@linkedin.com", "inmail-hit-reply@linkedin.com", "hit-reply@linkedin.com",
})
RELAY_DOMAINS = frozenset({"indeedemail.com"})
# Job-alert and newsletter senders, listed EXACTLY (review of #1519): a whole board domain is never an
# alert, because a recruiter writes from it too (richard@reed.co.uk, jane@dice.com). An address is an
# alert only by its exact address, its exact local part, a bulk word in its local part, or an
# alert-only host (the full host, never its parent domain).
ALERT_ADDRESSES = frozenset({
    "jobalerts-noreply@linkedin.com", "jobs-listings@linkedin.com", "jobs-noreply@linkedin.com",
    "newsletters-noreply@linkedin.com", "alert@indeed.com", "noreply@glassdoor.com",
})
ALERT_LOCALS = frozenset({"jobs-noreply", "jobs-listings", "jobs-digest", "job-digest", "jobmail", "jobmatch"})
ALERT_HOSTS = frozenset({"jobalert.indeed.com", "match.indeed.com"})
# Words in the local part (split on - _ . +) that name a bulk alert or a newsletter.
ALERT_LOCAL_WORDS = frozenset({
    "alert", "alerts", "jobalert", "jobalerts", "newsletter", "newsletters", "digest",
})
# Local parts (separators removed) that never take a reply.
_NO_REPLY_MARKS = ("noreply", "donotreply", "dontreply")
_NO_REPLY_LOCALS = frozenset({"mailerdaemon", "postmaster", "bounce", "bounces", "notification", "notifications"})
_SEPARATORS = "-_.+"


def address_of(sender: str) -> str:
    """The lowercase address in ``Name <a@b>`` or ``a@b``; "" when there is none."""
    text = (sender or "").strip()
    start = text.rfind("<")
    if start != -1:
        end = text.find(">", start)
        text = text[start + 1:end] if end != -1 else text[start + 1:]
    text = text.strip().strip('"').lower()
    return text if "@" in text else ""


def _local_words(local: str) -> list[str]:
    """The local part cut at each separator, one character at a time."""
    words: list[str] = []
    cur: list[str] = []
    for ch in local:
        if ch in _SEPARATORS:
            if cur:
                words.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    if cur:
        words.append("".join(cur))
    return words


def _in_domains(domain: str, domains: frozenset[str]) -> bool:
    """Exact domain or a subdomain of one in ``domains`` (label by label, never a substring)."""
    labels = domain.split(".")
    return any(".".join(labels[i:]) in domains for i in range(len(labels) - 1))


def is_alert_sender(sender: str) -> bool:
    """The strict bulk-alert test: True only for a listed address, local part or alert-only host, or a
    bulk word in the local part. A relay of one person's message, or a bare name, is never an alert."""
    addr = address_of(sender)
    if not addr:
        return False
    local, _, domain = addr.rpartition("@")
    if addr in RELAY_ADDRESSES or _in_domains(domain, RELAY_DOMAINS):
        return False
    return (
        addr in ALERT_ADDRESSES or local in ALERT_LOCALS or domain in ALERT_HOSTS
        or any(w in ALERT_LOCAL_WORDS for w in _local_words(local))
    )


def sender_kind(sender: str) -> str:
    """``person`` | ``alert`` | ``automated`` for one stored sender string. Fails safe: anything
    not proven a bulk alert or a no-reply address is a person."""
    addr = address_of(sender)
    if not addr:
        return PERSON  # a bare display name: someone wrote; never hidden on a guess
    local, _, domain = addr.rpartition("@")
    if addr in RELAY_ADDRESSES or _in_domains(domain, RELAY_DOMAINS):
        return PERSON
    if is_alert_sender(sender):
        return ALERT
    joined = "".join(_local_words(local))
    if any(mark in joined for mark in _NO_REPLY_MARKS) or joined in _NO_REPLY_LOCALS:
        return AUTOMATED
    return PERSON
