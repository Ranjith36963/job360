"""The words of the daily email.

Plain text only, deliberately. The transport today is Apprise ``resend://``,
which sends text; HTML lands with the direct-Resend-API change, and the text
part must exist and be good regardless — a text part is required for
deliverability and it is what the strictest readers actually see.

The register is fixed and it is not a style preference:

* **No exclamation marks, no all-caps, no currency in the subject.** That is
  scam grammar, and 95% of job seekers have met a fake offer with email as the
  most common channel. Quiet is credible.
* **The verdict comes first, the list second.** A list is a decision the reader
  must make; leading with "2 worth your time" makes the email an answer instead
  of a task.
* **We say what we threw away.** Unexplained silence is *ambiguous rejection* —
  worse for a job seeker than a clear no. It also makes our filter legible, so
  a reader who disagrees can go and change a preference rather than quietly
  concluding we are useless.
* **We write on empty days too.** "Nothing good today" costs one email and buys
  the trust that makes the good days believable.

Pinned by ``tests/test_digest_email_body.py``.
"""
from __future__ import annotations

from typing import Sequence

from src.services.delivery.decision_card import DecisionCard


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def render_digest_subject(*, shown: int, considered: int) -> str:
    """Subject line. States what arrived; claims nothing else.

    ``considered`` is accepted (and currently unused) so the subject can never
    be built from a different number than the body. Passing it here keeps the
    two renderers reading from one call site.
    """
    del considered  # body states the funnel; the subject stays deliberately bare
    if shown <= 0:
        return "Job360 — nothing worth your time today"
    return f"Job360 — {_plural(shown, 'job')} worth a look"


def _card_lines(card: DecisionCard, *, indent: str) -> list[str]:
    """What one job SAYS — the single definition both send modes render from.

    Split out of ``_render_card`` for wiring.md W-17: the instant path used to
    hand-roll its own body (``"Job360 match: {title}\\n{apply_url}"``), so the
    default notify mode shipped no score, no verdict, no reason and a link
    straight to the employer, while the digest said all four. Two users on
    different settings received different products.

    Everything after the heading line is indented by ``indent`` so the digest can
    keep its numbered layout while a single-job email reads flat.
    """
    lines: list[str] = []

    where = " · ".join(p for p in (card.location, card.salary) if p)
    if where:
        lines.append(f"{indent}{where}")

    if card.is_judged:
        # The score AND the words that justify it, never the number alone.
        lines.append(f"{indent}Fit {card.primary_score}/100 ({card.verdict})")
        if card.reason:
            lines.append(f"{indent}Why: {card.reason}")
    else:
        # Honest about which engine spoke. No invented reason.
        lines.append(f"{indent}Keyword match {card.primary_score}/100 — not yet reviewed")

    # ALWAYS our own page, never the employer's apply_url (W-18). The click has
    # to be able to come back: attribution, the staleness guard and the
    # "this one closed" message all live on that page.
    lines.append(f"{indent}{card.url}")
    return lines


def _render_card(card: DecisionCard, index: int) -> str:
    """One job, as a small block. Numbered so a reply can name it later."""
    return "\n".join(
        [f"{index}. {card.title} — {card.company}", *_card_lines(card, indent="   ")]
    )


def render_instant_subject(card: DecisionCard) -> str:
    """Subject for a single-job instant alert.

    Leads with the role, because that is what makes someone open it. The score
    rides along only when the job was actually judged — putting a keyword number
    in the subject would dress a guess up as an assessment.
    """
    head = f"{card.title} at {card.company}"
    if card.is_judged:
        return f"Job360 — {head} ({card.primary_score}/100)"
    return f"Job360 — {head}"


def render_instant_text(card: DecisionCard, unsubscribe: str | None = None) -> str:
    """Body for a single-job instant alert — same facts as the digest states.

    Rendered from :func:`_card_lines`, so instant and digest cannot drift apart:
    change what a job says in one place and both modes follow.

    ``unsubscribe`` is the one-line exit (W-23). Optional so a caller that has no
    user context still renders a valid body, but every real send passes it: an email
    with no way out gets marked as spam instead.
    """
    lines = [f"{card.title} — {card.company}", *_card_lines(card, indent=""), ""]
    if unsubscribe:
        lines.extend(["—", unsubscribe])
    return "\n".join(lines)


def render_digest_text(
    cards: Sequence[DecisionCard],
    *,
    considered: int,
    dropped_reasons: Sequence[str],
    unsubscribe: str | None = None,
) -> str:
    """Render the whole email body.

    ``considered`` is how many jobs we looked at on this user's behalf;
    ``dropped_reasons`` are the human-readable reasons the rest did not make it.
    Both exist to turn our filtering from a silent black box into a stated,
    checkable claim.
    """
    shown = len(cards)
    dropped = max(considered - shown, 0)
    out: list[str] = []

    if shown:
        out.append(f"{_plural(shown, 'job')} worth your time today.")
    else:
        out.append("Nothing worth your time today.")

    # The honest no. Skipped when nothing was dropped — printing "dropped 0"
    # reads as the machine talking to itself.
    if dropped > 0:
        line = f"We checked {considered} and dropped {dropped}"
        reasons = [r for r in dropped_reasons if r and r.strip()]
        if reasons:
            line += ": " + ", ".join(reasons)
        out.append(line + ".")
    elif not shown:
        out.append(f"We checked {considered}.")

    if shown:
        out.append("")
        for i, card in enumerate(cards, start=1):
            out.append(_render_card(card, i))
            out.append("")
    else:
        out.append("")
        out.append("We would rather send you nothing than send you noise.")

    # W-23 — every outbound email carries its own exit. A recipient who cannot find
    # one presses "spam" instead, and that costs the sending domain far more than
    # the unsubscribe ever would.
    if unsubscribe:
        out.append("")
        out.append("—")
        out.append(unsubscribe)

    return "\n".join(out).rstrip() + "\n"
