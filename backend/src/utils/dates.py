"""Normalise whatever an upstream calls a date into ISO-8601, honestly.

WHY THIS EXISTS.

Every source stored its upstream's date field verbatim into `jobs.posted_at`,
which is an unconstrained TEXT column. So the same column held three different
things at once:

    arbeitnow / himalayas   '1785750903'    Unix epoch seconds, as a string
    reed                    '27/07/2026'    UK day-first
    everyone else           '2026-07-27...' ISO-8601

The frontend calls `new Date()` on that value. For the first two it gets NaN,
and because every comparison against NaN is false, `timeAgo()` falls through all
four of its guards to the final `return` and renders **"Posted NaNw ago"** —
which is what a real user saw on the live dashboard on 2026-08-03, on 5 of the
25 cards on his screen. Nothing threw. Sentry logged zero events.

The second half of the bug was worse than the first: we then stamped
`date_confidence='high'` on those values, purely because the field was present.
We certified as trustworthy a value we could not parse. Anything downstream that
believed that flag was being lied to by our own code.

THE RULE THIS ENCODES: **presence is not validity.** A date field is only
"high" confidence if it actually parsed. If it did not, we say so and store
nothing, because a null date renders as "Seen 2d ago" (honest, from crawler
time) while an unparseable one renders as NaN.

Deliberately stdlib-only and side-effect free, so every source can call it on
the hot path without a dependency or an exception risk.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

# Epoch seconds for 2001-09-09 .. 2286-11-20 — wide enough for any real posting,
# narrow enough that a 4-digit year or a millisecond value cannot be mistaken
# for it.
_EPOCH_S_MIN, _EPOCH_S_MAX = 1_000_000_000, 9_999_999_999
_ISO_PREFIX = re.compile(r"^\d{4}-\d{2}-\d{2}")
_DMY = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$")


def normalize_posted_at(raw: Any) -> tuple[str | None, str]:
    """Return ``(iso_or_none, confidence)`` for one upstream date value.

    ``confidence`` is ``"high"`` ONLY when the value actually parsed. Callers
    must pass both results straight through to ``Job(posted_at=...,
    date_confidence=...)`` rather than deriving confidence from presence — that
    derivation is the bug this function exists to end.
    """
    if raw is None or raw == "":
        return None, "low"

    # Already ISO — the overwhelmingly common case, so check it first and cheap.
    if isinstance(raw, str) and _ISO_PREFIX.match(raw.strip()):
        return raw.strip(), "high"

    s = str(raw).strip()

    # Unix epoch, seconds or milliseconds. Arrives as an int from JSON or as a
    # digit-string; both are the same bug from the UI's point of view.
    if s.isdigit():
        n = int(s)
        if len(s) == 13:          # milliseconds
            n //= 1000
        if _EPOCH_S_MIN <= n <= _EPOCH_S_MAX:
            try:
                return datetime.fromtimestamp(n, tz=timezone.utc).isoformat(), "high"
            except (OverflowError, OSError, ValueError):
                return None, "low"
        # A bare number outside the plausible epoch range is not a date at all.
        return None, "low"

    # Day-first European / UK format. Deliberately NOT month-first: this repo is
    # UK-facing and reed sends DD/MM/YYYY. Guessing the other way would silently
    # swap days and months for the first 12 days of every month — a wrong date
    # that parses is worse than one that does not.
    m = _DMY.match(s)
    if m:
        d, mo, y = (int(x) for x in m.groups())
        try:
            return datetime(y, mo, d, tzinfo=timezone.utc).isoformat(), "high"
        except ValueError:
            return None, "low"

    # Anything else the stdlib recognises (RFC-3339 with 'Z', etc.).
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00")).isoformat(), "high"
    except ValueError:
        pass

    # Unrecognised. Say so — never store it and never call it high confidence.
    return None, "low"
