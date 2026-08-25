"""Make a stored profile string safe to send upstream AS A SEARCH QUERY.

Why this exists
---------------
The nightly catalog cron asks every job board for the union of every user's
profile `job_titles`. Those strings come from CVs, so some of them carry the
damage of however the CV was decoded. Measured against production 2026-08-24 —
3 complete profiles, 13 titles, lengths 6-44 — exactly one defect appears, and it
appears in every profile that has it:

    AI Solutions Engineer � R&D Department

`�` is U+FFFD REPLACEMENT CHARACTER. It is not a character anyone typed: it
is what a decoder writes when the original bytes were already lost. No job advert
anywhere contains it, so any query carrying it is guaranteed to match nothing.

The cost is measurable. Running findwork twice against the same key and network,
changing only the question, gives 9 jobs on a neutral query and 0 on the
production query.

What this does NOT do
---------------------
It does not guess what the lost character was. "AI Solutions Engineer � R&D
Department" was probably an em dash, but "probably" has no place in a repair —
the damaged byte is replaced with a SPACE and the surrounding words are kept, so
every word the user actually wrote still reaches the upstream. Nothing is
truncated on a hunch about where the "real" title ends.

It is also NOT a taste filter. There is no list here of titles considered too
vague, too junior, or too much like a CV heading. The only things removed are
characters that cannot appear in a real advert: U+FFFD and control characters.
Judging which ROLES are worth searching for would be exactly the hand-typed,
unbounded list the project forbids.
"""

from __future__ import annotations

import re
import unicodedata

#: U+FFFD, written by a decoder when the original bytes are unrecoverable.
REPLACEMENT_CHAR = "�"

_WHITESPACE_RUN = re.compile(r"\s+")


def _is_control(ch: str) -> bool:
    """True for C0/C1 control characters, which no advert contains either.

    Uses the Unicode category rather than a range literal so it stays correct
    for characters outside Latin-1.
    """
    return unicodedata.category(ch).startswith("C")


def clean_query_text(raw: str) -> str:
    """Return ``raw`` with undecodable characters removed, or ``""`` if nothing is left.

    Args:
        raw: a stored profile string about to be sent upstream as a query.

    Returns:
        The same words with U+FFFD and control characters replaced by spaces and
        whitespace collapsed. An empty string when the input held no usable
        text at all, which the caller should treat as "do not send this".
    """
    if not raw:
        return ""
    swapped = "".join(
        " " if (ch == REPLACEMENT_CHAR or _is_control(ch)) else ch for ch in raw
    )
    collapsed = _WHITESPACE_RUN.sub(" ", swapped).strip()
    # A string with no letters cannot be a role. This is a structural test, not a
    # judgement about which roles are worth searching for.
    if not any(ch.isalpha() for ch in collapsed):
        return ""
    return collapsed


def needs_cleaning(raw: str) -> bool:
    """True when ``raw`` would be changed by :func:`clean_query_text`.

    Exists so the caller can LOG what it repaired. A silent repair would hide the
    fact that a profile is carrying damaged text, which is the condition that let
    a guaranteed-empty query run nightly without anyone noticing.
    """
    return bool(raw) and clean_query_text(raw) != raw.strip()
