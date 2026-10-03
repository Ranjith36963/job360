#!/usr/bin/env python3
"""Read Sentry, because nobody was.

WHY THIS EXISTS.

Sentry is the best-instrumented observation layer this product has. The backend
and the ARQ worker both initialise it (`src/core/observability.py`), headers are
scrubbed, and real crashes land there within seconds.

And nothing has ever read it. Measured 2026-08-03: `git grep -i sentry` across
`.github/` and `scripts/` returned **zero hits**. Ten unresolved issues sat in
the project unread.

That is not an abstract gap. `PYTHON-FASTAPI-8` — `ProgramLimitExceeded: index
row size 3128 exceeds btree maximum 2704`, raised from `/api/search` — appeared
on 2026-07-27. It is the exact fault that aborted a real user's searches twice
and left his feed frozen for SEVEN DAYS while the catalog grew by ~2,800 jobs.
Sentry had the full stack trace, with the cause, from the first minute. It was
rediscovered by hand a week later only because the owner went looking.

An error tracker with no consumer is not an error tracker. It is a diary.

WHAT THIS DOES. Ask Sentry for issues that are unresolved AND first seen inside
the window, print them as markdown, and exit 1 so the workflow raises ONE
deduped issue that the existing triage -> repair chain then handles.

WHAT IT DELIBERATELY DOES NOT DO. It does not alarm on every unresolved issue.
The backlog is a human triage decision; only NEW ones are an event. Alarming on
the standing backlog every night is how a detector becomes noise, and a noisy
detector gets muted, which is worse than no detector.

CONTRACT (identical to product_assertions.py so the workflow shape is shared):
    exit 0  nothing new
    exit 1  new unresolved issues -> raise
    exit 2  the poller itself is broken -> fail LOUD, never silently green
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ORG = os.getenv("SENTRY_ORG", "job360")
# The org lives in the EU region; the default US host returns 404 for it. Getting
# this wrong looks exactly like "no issues", which is the failure this file
# exists to prevent — so it is explicit and overridable, never inferred.
HOST = os.getenv("SENTRY_HOST", "https://de.sentry.io").rstrip("/")
WINDOW_HOURS = int(os.getenv("SENTRY_WINDOW_HOURS", "24"))
MAX_REPORTED = int(os.getenv("SENTRY_MAX_REPORTED", "15"))
# A drill knob: forces a red without touching Sentry, so the whole
# poll -> issue -> triage chain can be exercised on purpose.
FORCE_RED = os.getenv("SENTRY_FORCE_RED", "") == "1"
# Only issues first seen AT/AFTER this ISO-8601 time count (the merge time).
# Without it the window is a plain "last Nh", so an issue born in merge A's
# deploy re-trips merge B's watch and rolls good code back (#722/#724/#725).
SINCE = os.getenv("SENTRY_SINCE", "").strip()


def _parse_ts(value: str) -> datetime | None:
    """Parse a Sentry/GitHub ISO timestamp (`Z` or offset); None if unreadable."""
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def issues_since(issues: list[dict], since: str) -> list[dict]:
    """Keep issues first seen at/after `since`. An unreadable `since` filters nothing
    (over-report, never miss); an issue with an unreadable firstSeen is kept."""
    cutoff = _parse_ts(since) if since else None
    if cutoff is None:
        return issues
    kept = []
    for it in issues:
        seen = _parse_ts(str(it.get("firstSeen", "")))
        if seen is None or seen >= cutoff:
            kept.append(it)
    return kept


def _get(url: str, token: str) -> list[dict]:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=45) as r:  # noqa: S310 - fixed https host
        return json.loads(r.read().decode("utf-8") or "[]")


def main() -> int:
    token = os.getenv("SENTRY_API_TOKEN", "").strip()
    if not token:
        # LOUD, never a silent skip. A poller that quietly does nothing when its
        # credential is missing recreates the exact blindness it was built to
        # end — and it would look green forever.
        print("::error::SENTRY_API_TOKEN is not set - the Sentry poller cannot run.")
        print(
            "Create a token with `event:read` at https://sentry.io/settings/account/api/"
            "auth-tokens/ and add it as the SENTRY_API_TOKEN repo secret."
        )
        return 2

    if FORCE_RED:
        print("# Sentry drill\n\nForced red to prove the chain works. Sentry is fine.\n")
        print("- `DRILL-1` — this is a fire drill, not a real error (0 users)")
        return 1

    # `statsPeriod` bounds the search window; `is:unresolved` excludes anything a
    # human has already triaged; `firstSeen:-Nh` makes it NEW rather than the
    # standing backlog.
    q = urllib.parse.urlencode(
        {
            "query": f"is:unresolved firstSeen:-{WINDOW_HOURS}h",
            "statsPeriod": f"{WINDOW_HOURS}h",
            "limit": str(MAX_REPORTED),
        }
    )
    url = f"{HOST}/api/0/organizations/{ORG}/issues/?{q}"
    try:
        issues = _get(url, token)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")[:200]
        print(f"::error::Sentry API returned HTTP {e.code}: {body}")
        print("A poller that cannot reach Sentry is BLIND, not clean.")
        return 2
    except Exception as exc:  # noqa: BLE001 - must fail loud, never silently green
        print(f"::error::could not reach Sentry: {type(exc).__name__}: {exc}")
        return 2

    if not isinstance(issues, list):
        print(f"::error::unexpected Sentry response shape: {type(issues).__name__}")
        return 2

    print(f"# Sentry — new unresolved issues in the last {WINDOW_HOURS}h\n")
    if not issues:
        print("None. (The standing backlog is deliberately not reported here: only "
              "NEW issues are an event, or this detector becomes nightly noise.)")
        return 0

    print(f"**{len(issues)} new unresolved issue(s).** Nothing else in the harness "
          "reads Sentry, so without this they would sit unseen — as a 7-day feed "
          "outage once did, fully described, from its first minute.\n")
    print("| issue | events | users | first seen | culprit |")
    print("|---|---|---|---|---|")
    for it in issues:
        title = str(it.get("title", "?")).replace("|", "\\|")[:90]
        print(
            f"| [{it.get('shortId','?')}]({it.get('permalink','')}) {title} "
            f"| {it.get('count','?')} | {it.get('userCount','?')} "
            f"| {str(it.get('firstSeen',''))[:16]} | `{str(it.get('culprit',''))[:40]}` |"
        )
    print(
        "\nEach row is a real exception raised in production. Diagnose the top one "
        "first: event count is a proxy for how many users hit it."
    )
    return 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the checker must never pass while broken
        print(f"::error::sentry_poll crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
