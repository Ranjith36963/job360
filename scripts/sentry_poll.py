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

WHAT "NEW" MEANS FOR A ROLLBACK (added 2026-10-03, FC-008). "First seen in the
window" is not "caused by this deploy": every container swap makes the OLD container
log `ClientDisconnect` on /api/mcp and `event loop blocked for 3.1s` on its way out,
and each is a brand-new Sentry issue with 2 events and 0 users. post-merge-watch.yml
read them as a regression and rolled production back three times in one afternoon
(#722, #724, #725). `decide()` is the pure rule that now separates a REGRESSION from
deploy noise. Every knob is an env var; the DEFAULTS BELOW KEEP THE OLD BEHAVIOUR
(every new issue trips) because external-health.yml shares this script for a nightly
24h poll where a 2-event error must still be seen. post-merge-watch.yml opts in to
the stricter values in its own header.

    SENTRY_MIN_EVENTS            ignore an issue with fewer events than this AND
                                 fewer users than SENTRY_MIN_USERS        (default 1)
    SENTRY_MIN_USERS             an issue touching >= this many users ALWAYS trips,
                                 whatever its title or event count; 0 disables (default 1)
    SENTRY_NOISE_TITLES          comma list of case-insensitive title substrings that
                                 are known deploy shutdown/startup noise     (default empty)
    SENTRY_NOISE_ESCALATE_EVENTS a noise-titled issue still trips once it has this
                                 many events - a flood is not noise          (default 20)
    SENTRY_SINCE                 ISO-8601 instant; issues first seen BEFORE it are
                                 ignored (they predate the deploy under watch)

CONTRACT (identical to product_assertions.py so the workflow shape is shared):
    exit 0  nothing new (after the filters above)
    exit 1  new unresolved issues -> raise
    exit 2  the poller itself is broken -> fail LOUD, never silently green

    python scripts/sentry_poll.py --drill            # offline: recorded 2026-10-03 noise
    python scripts/sentry_poll.py --drill --blind    # negative control: must exit 1
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

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

FIXTURE = Path(__file__).resolve().parent / "fixtures" / "sentry_deploy_noise.json"


def _env_int(name: str, default: int) -> int:
    """Read an int knob; a malformed value is a LOUD config error, never a silent default."""
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


@dataclass(frozen=True)
class Knobs:
    """The trip rule's parameters. Defaults reproduce the pre-FC-008 behaviour."""

    min_events: int = 1
    min_users: int = 1
    noise_titles: tuple[str, ...] = ()
    noise_escalate_events: int = 20

    @classmethod
    def from_env(cls) -> "Knobs":
        raw = os.getenv("SENTRY_NOISE_TITLES", "")
        return cls(
            min_events=_env_int("SENTRY_MIN_EVENTS", 1),
            min_users=_env_int("SENTRY_MIN_USERS", 1),
            noise_titles=tuple(t.strip().lower() for t in raw.split(",") if t.strip()),
            noise_escalate_events=_env_int("SENTRY_NOISE_ESCALATE_EVENTS", 20),
        )


@dataclass
class Decision:
    """`tripped` are the issues that justify a rollback; `ignored` carry the reason why not."""

    tripped: list[tuple[dict, str]] = field(default_factory=list)
    ignored: list[tuple[dict, str]] = field(default_factory=list)


def _count(value: object) -> int | None:
    """Sentry sends `count` as a STRING ("2") and `userCount` as an int. None = unreadable."""
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def parse_instant(value: str) -> datetime | None:
    """ISO-8601 -> aware datetime (naive input is taken as UTC); None if unparseable."""
    try:
        dt = datetime.fromisoformat(str(value).strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def classify(issue: dict, knobs: Knobs, since: datetime | None) -> tuple[bool, str]:
    """(trips?, why) for ONE issue. Pure - no I/O.

    FAIL TOWARD THE ALARM: an unreadable count or timestamp can never be the reason
    an issue is ignored. Ignoring needs positive evidence of noise.
    """
    events = _count(issue.get("count"))
    users = _count(issue.get("userCount"))
    if events is None or users is None:
        return True, "counts unreadable - failing toward the alarm"
    if since is not None:
        first = parse_instant(str(issue.get("firstSeen", "")))
        if first is not None and first < since:
            return False, f"first seen {first:%H:%M}Z, before the deploy under watch"
    if knobs.min_users > 0 and users >= knobs.min_users:
        return True, f"affects {users} user(s)"
    title = str(issue.get("title", "")).lower()
    noisy = next((n for n in knobs.noise_titles if n in title), None)
    if noisy is not None:
        if events >= knobs.noise_escalate_events:
            return True, f"'{noisy}' flood: {events} events >= {knobs.noise_escalate_events}"
        return False, (f"known deploy noise '{noisy}': {events} event(s) < "
                       f"{knobs.noise_escalate_events}, 0 users")
    if events >= knobs.min_events:
        return True, f"{events} events >= {knobs.min_events}"
    return False, f"{events} event(s), {users} users: below min_events={knobs.min_events}"


def decide(issues: list[dict], knobs: Knobs, since: datetime | None = None) -> Decision:
    """Split Sentry's new issues into the ones that trip a rollback and the ones that do not."""
    out = Decision()
    for it in issues:
        trips, why = classify(it, knobs, since)
        (out.tripped if trips else out.ignored).append((it, why))
    return out


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

    try:
        knobs = Knobs.from_env()
        since_raw = os.getenv("SENTRY_SINCE", "").strip()
        since = parse_instant(since_raw) if since_raw else None
        if since_raw and since is None:
            raise ValueError(f"SENTRY_SINCE={since_raw!r} is not an ISO-8601 instant")
    except ValueError as exc:
        print(f"::error::bad poller configuration: {exc}")
        return 2

    verdict = decide(issues, knobs, since)
    if len(issues) >= MAX_REPORTED:
        print(f"::warning::Sentry returned {len(issues)} issues = SENTRY_MAX_REPORTED; "
              "the list may be truncated.")
    if knobs != Knobs() or since is not None:
        print(f"_Filters: min_events={knobs.min_events} min_users={knobs.min_users} "
              f"noise={list(knobs.noise_titles)} escalate_at={knobs.noise_escalate_events} "
              f"since={since.isoformat() if since else 'none'}_\n")

    def table(rows: list[tuple[dict, str]], why_col: str) -> None:
        print(f"| issue | events | users | first seen | culprit | {why_col} |")
        print("|---|---|---|---|---|---|")
        for it, why in rows:
            title = str(it.get("title", "?")).replace("|", "\\|")[:90]
            print(
                f"| [{it.get('shortId','?')}]({it.get('permalink','')}) {title} "
                f"| {it.get('count','?')} | {it.get('userCount','?')} "
                f"| {str(it.get('firstSeen',''))[:16]} | `{str(it.get('culprit',''))[:40]}` "
                f"| {why} |"
            )

    if verdict.ignored:
        print(f"**{len(verdict.ignored)} new issue(s) IGNORED** (below the trip bar - listed, "
              "never hidden):\n")
        table(verdict.ignored, "why ignored")
        print()
    if not verdict.tripped:
        print("Nothing new crossed the trip bar.")
        return 0

    print(f"**{len(verdict.tripped)} new unresolved issue(s).** Nothing else in the harness "
          "reads Sentry, so without this they would sit unseen — as a 7-day feed "
          "outage once did, fully described, from its first minute.\n")
    table(verdict.tripped, "why it trips")
    print(
        "\nEach row is a real exception raised in production. Diagnose the top one "
        "first: event count is a proxy for how many users hit it."
    )
    return 1


def drill(blind: bool = False) -> int:
    """Offline self-test over the RECORDED 2026-10-03 noise plus negative controls.

    `blind` swaps the rule for one that ignores everything; the 'must trip' cases then
    fail, which is how this drill proves it can still go red (drill_registry negative).
    """
    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))["issues"]
    knobs = Knobs(min_events=5, min_users=1, noise_titles=("clientdisconnect", "event loop blocked"),
                  noise_escalate_events=20)
    since = parse_instant("2026-10-03T14:49:36Z")

    def issue(title: str, count: int, users: int, first: str = "2026-10-03T15:01:00Z") -> dict:
        return {"shortId": "X-1", "title": title, "count": str(count), "userCount": users,
                "firstSeen": first, "culprit": ""}

    def rule(its: list[dict]) -> Decision:
        if blind:
            return Decision(ignored=[(i, "blind") for i in its])
        return decide(its, knobs, since)

    cases: list[tuple[str, bool]] = [
        ("recorded: the 3 real noise issues do NOT trip", not rule(recorded).tripped),
        ("recorded: all 3 are reported as ignored, not dropped", len(rule(recorded).ignored) == 3),
        ("control: an error touching 1 user MUST trip", bool(rule([issue("ValueError: x", 2, 1)]).tripped)),
        ("control: a noise title with users>0 MUST trip",
         bool(rule([issue("ClientDisconnect", 2, 1)]).tripped)),
        ("control: a ClientDisconnect flood (20 events) MUST trip",
         bool(rule([issue("ClientDisconnect", 20, 0)]).tripped)),
        ("control: an 'event loop blocked' flood MUST trip",
         bool(rule([issue("event loop blocked for 3.13s", 25, 0)]).tripped)),
        ("control: a non-noise error with 5 events, 0 users MUST trip",
         bool(rule([issue("KeyError: 'id'", 5, 0)]).tripped)),
        ("control: unreadable counts fail toward the alarm",
         bool(rule([{"title": "?", "count": "n/a", "userCount": None}]).tripped)),
        ("control: a mid-level noise burst (7 events) stays ignored",
         not rule([issue("ClientDisconnect", 7, 0)]).tripped),
        ("since: an issue first seen before the deploy is ignored",
         not rule([issue("KeyError: 'id'", 50, 0, first="2026-10-03T14:00:00Z")]).tripped),
    ]
    print("DRILL - sentry_poll.decide() must ignore deploy noise and still trip on real errors."
          + ("  [BLIND: rule swapped for 'ignore everything']" if blind else ""))
    for name, ok in cases:
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    failed = [n for n, ok in cases if not ok]
    print(f"DRILL RESULT: {len(cases) - len(failed)}/{len(cases)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    if "--drill" in sys.argv[1:]:
        sys.exit(drill(blind="--blind" in sys.argv[1:]))
    try:
        sys.exit(main())
    except Exception as exc:  # noqa: BLE001 - the checker must never pass while broken
        print(f"::error::sentry_poll crashed: {type(exc).__name__}: {exc}")
        sys.exit(2)
