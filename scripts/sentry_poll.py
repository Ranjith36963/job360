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

WHAT "NEW" MEANS FOR A ROLLBACK (2026-10-20, FC-009; ported from PR #728).
"First seen in the window" is not "caused by this deploy". post-merge-watch.yml
rolled production back 5 times on noise (#722 #724 #725 on 2026-10-03: the OLD
container's `ClientDisconnect` / `event loop blocked` on its way out, 2 events and
0 users each; #910 #911 on 2026-10-19: a BACKEND rollback for a frontend-test-only
commit). `decide()` is the pure rule that now separates a regression from noise:

    since    issues first seen BEFORE the deploy went live are not its fault
    users    an issue touching >= min_users users ALWAYS trips
    noise    known deploy-swap titles are ignored below a flood size
    events   anything else trips at >= min_events
    project  each issue is mapped to the service it came from (backend or
             frontend), so a frontend error never rolls the backend back; an
             issue in neither project is listed and ignored

Unreadable counts or a missing project fail TOWARD the alarm. Ignored issues are
listed, never hidden. The DEFAULTS KEEP THE OLD BEHAVIOUR (every new issue trips,
no project filter) because external-health.yml shares this script for a nightly
24 h poll. post-merge-watch.yml sets SENTRY_PRESET=post-merge-watch, which loads
WATCH_PRESET below; any single knob can still be overridden by its env var:

    SENTRY_SINCE                 ISO-8601 instant (the moment the deploy went live)
    SENTRY_MIN_EVENTS            SENTRY_MIN_USERS
    SENTRY_NOISE_TITLES          comma list, case-insensitive substrings
    SENTRY_NOISE_ESCALATE_EVENTS a noise-titled issue still trips at this many events
    SENTRY_SERVICE_PROJECTS      "backend=python,fastapi;frontend=javascript,nextjs"
                                 substrings matched against project slug/platform
    SENTRY_SERVICES_OUT          file to write the tripped services to (comma list)

CONTRACT (identical to product_assertions.py so the workflow shape is shared):
    exit 0  nothing new (after the filters above)
    exit 1  new unresolved issues -> raise
    exit 2  the poller itself is broken -> fail LOUD, never silently green

    python scripts/sentry_poll.py --drill            # offline fixture worlds
    python scripts/sentry_poll.py --drill --blind    # negative control: must exit 1
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Mapping
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

# The post-merge watch's values, in ONE place, exercised by --drill. 5 / 20 are
# judgment, not measurement (#728); the project substrings are INFERRED from the
# backend's short ids (PYTHON-FASTAPI-*) and the frontend's @sentry/nextjs SDK.
WATCH_PRESET: dict[str, str] = {
    "SENTRY_MIN_EVENTS": "5",
    "SENTRY_MIN_USERS": "1",
    "SENTRY_NOISE_TITLES": "ClientDisconnect,event loop blocked",
    "SENTRY_NOISE_ESCALATE_EVENTS": "20",
    "SENTRY_SERVICE_PROJECTS": "backend=python,fastapi;frontend=javascript,nextjs,node",
}
PRESETS = {"post-merge-watch": WATCH_PRESET}


def _env_int(env: Mapping[str, str], name: str, default: int) -> int:
    """Read an int knob; a malformed value is a LOUD config error, never a silent default."""
    raw = env.get(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError as exc:
        raise ValueError(f"{name}={raw!r} is not an integer") from exc


def _service_projects(raw: str) -> tuple[tuple[str, tuple[str, ...]], ...]:
    out = []
    for part in raw.split(";"):
        if not part.strip():
            continue
        name, sep, subs = part.partition("=")
        needles = tuple(x.strip().lower() for x in subs.split(",") if x.strip())
        if not sep or not name.strip() or not needles:
            raise ValueError(f"SENTRY_SERVICE_PROJECTS part {part!r} is not service=a,b")
        out.append((name.strip(), needles))
    return tuple(out)


@dataclass(frozen=True)
class Knobs:
    """The trip rule's parameters. Defaults reproduce the pre-FC-009 behaviour."""

    min_events: int = 1
    min_users: int = 1
    noise_titles: tuple[str, ...] = ()
    noise_escalate_events: int = 20
    # () = no project filter and no attribution (the nightly poll's behaviour).
    service_projects: tuple[tuple[str, tuple[str, ...]], ...] = ()

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> Knobs:
        env = dict(os.environ if environ is None else environ)
        preset = env.get("SENTRY_PRESET", "").strip()
        if preset:
            if preset not in PRESETS:
                raise ValueError(f"SENTRY_PRESET={preset!r} is not one of {sorted(PRESETS)}")
            env = {**PRESETS[preset], **{k: v for k, v in env.items() if v.strip()}}
        raw = env.get("SENTRY_NOISE_TITLES", "")
        return cls(
            min_events=_env_int(env, "SENTRY_MIN_EVENTS", 1),
            min_users=_env_int(env, "SENTRY_MIN_USERS", 1),
            noise_titles=tuple(t.strip().lower() for t in raw.split(",") if t.strip()),
            noise_escalate_events=_env_int(env, "SENTRY_NOISE_ESCALATE_EVENTS", 20),
            service_projects=_service_projects(env.get("SENTRY_SERVICE_PROJECTS", "")),
        )


@dataclass
class Decision:
    """`tripped` justify a rollback (with the service each came from); `ignored` say why not."""

    tripped: list[tuple[dict, str, str]] = field(default_factory=list)
    ignored: list[tuple[dict, str]] = field(default_factory=list)

    @property
    def services(self) -> list[str]:
        return sorted({svc for _, _, svc in self.tripped})


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


ALL = "all"  # an issue whose project cannot be read: every rollback service


def service_of(issue: dict, knobs: Knobs) -> str | None:
    """Which service an issue came from: a service name, ALL (unreadable), or None (neither)."""
    if not knobs.service_projects:
        return ALL
    proj = issue.get("project")
    hay = " ".join(
        str(x).lower()
        for x in (
            proj.get("slug", "") if isinstance(proj, dict) else "",
            proj.get("platform", "") if isinstance(proj, dict) else "",
            issue.get("platform", ""),
        )
        if x
    )
    if not hay.strip():
        return ALL
    for name, needles in knobs.service_projects:
        if any(n in hay for n in needles):
            return name
    return None


def classify(issue: dict, knobs: Knobs, since: datetime | None) -> tuple[bool, str, str]:
    """(trips?, why, service) for ONE issue. Pure - no I/O.

    FAIL TOWARD THE ALARM: an unreadable count, timestamp or project can never be
    the reason an issue is ignored. Ignoring needs positive evidence of noise.
    """
    svc = service_of(issue, knobs)
    if svc is None:
        proj = issue.get("project")
        slug = proj.get("slug", "?") if isinstance(proj, dict) else "?"
        return False, f"project '{slug}' is neither the backend nor the frontend", ""
    events = _count(issue.get("count"))
    users = _count(issue.get("userCount"))
    if events is None or users is None:
        return True, "counts unreadable - failing toward the alarm", svc
    if since is not None:
        first = parse_instant(str(issue.get("firstSeen", "")))
        if first is not None and first < since:
            return False, f"first seen {first:%H:%M:%S}Z, before the deploy went live", svc
    if knobs.min_users > 0 and users >= knobs.min_users:
        return True, f"affects {users} user(s)", svc
    title = str(issue.get("title", "")).lower()
    noisy = next((n for n in knobs.noise_titles if n in title), None)
    if noisy is not None:
        if events >= knobs.noise_escalate_events:
            return True, f"'{noisy}' flood: {events} events >= {knobs.noise_escalate_events}", svc
        return False, (f"known deploy noise '{noisy}': {events} event(s) < "
                       f"{knobs.noise_escalate_events}, 0 users"), svc
    if events >= knobs.min_events:
        return True, f"{events} events >= {knobs.min_events}", svc
    return False, f"{events} event(s), {users} users: below min_events={knobs.min_events}", svc


def decide(issues: list[dict], knobs: Knobs, since: datetime | None = None) -> Decision:
    """Split Sentry's new issues into the ones that trip a rollback and the ones that do not."""
    out = Decision()
    for it in issues:
        trips, why, svc = classify(it, knobs, since)
        if trips:
            out.tripped.append((it, why, svc))
        else:
            out.ignored.append((it, why))
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
    params = {
        "query": f"is:unresolved firstSeen:-{WINDOW_HOURS}h",
        "statsPeriod": f"{WINDOW_HOURS}h",
        "limit": str(MAX_REPORTED),
    }
    if os.getenv("SENTRY_PRESET", "").strip():
        # The watch filters by first-seen AFTER the fetch, so ask for the newest
        # first-seen issues first: pre-deploy issues must not fill the page and
        # crowd out a new one (2026-10-20). The nightly poll keeps its order.
        params["sort"] = "new"
    q = urllib.parse.urlencode(params)
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
    return report(issues, knobs, since, os.getenv("SENTRY_SERVICES_OUT", "").strip())


def report(issues: list[dict], knobs: Knobs, since: datetime | None, services_out: str = "") -> int:
    """Print the verdict for `issues` and return the exit code. The part --drill runs."""
    verdict = decide(issues, knobs, since)
    if len(issues) >= MAX_REPORTED:
        print(f"::warning::Sentry returned {len(issues)} issues = SENTRY_MAX_REPORTED; "
              "the list may be truncated.")
    if knobs != Knobs() or since is not None:
        print(f"_Filters: min_events={knobs.min_events} min_users={knobs.min_users} "
              f"noise={list(knobs.noise_titles)} escalate_at={knobs.noise_escalate_events} "
              f"projects={[n for n, _ in knobs.service_projects] or 'any'} "
              f"since={since.isoformat() if since else 'none'}_\n")

    def table(rows: list[tuple[dict, str, str]], why_col: str) -> None:
        print(f"| issue | project | events | users | first seen | culprit | {why_col} |")
        print("|---|---|---|---|---|---|---|")
        for it, why, svc in rows:
            title = str(it.get("title", "?")).replace("|", "\\|")[:90]
            print(
                f"| [{it.get('shortId','?')}]({it.get('permalink','')}) {title} | {svc or '-'} "
                f"| {it.get('count','?')} | {it.get('userCount','?')} "
                f"| {str(it.get('firstSeen',''))[:19]} | `{str(it.get('culprit',''))[:40]}` "
                f"| {why} |"
            )

    if verdict.ignored:
        print(f"**{len(verdict.ignored)} new issue(s) IGNORED** (below the trip bar - listed, "
              "never hidden):\n")
        table([(it, why, "") for it, why in verdict.ignored], "why ignored")
        print()
    if services_out:
        Path(services_out).write_text(",".join(verdict.services), encoding="utf-8")
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
    """Offline fixture worlds, through the SAME preset + report() the watch uses.

    `blind` swaps the rule for "ignore everything"; the must-trip worlds then fail,
    which is how this drill proves it can still go red (drill_registry negative).
    """
    import contextlib
    import io
    import tempfile

    recorded = json.loads(FIXTURE.read_text(encoding="utf-8"))["issues"]
    knobs = Knobs.from_env({"SENTRY_PRESET": "post-merge-watch"})
    live = parse_instant("2026-10-03T14:59:00Z")  # the moment /api/version showed the sha
    be = {"slug": "python-fastapi", "platform": "python"}
    fe = {"slug": "javascript-nextjs", "platform": "javascript-nextjs"}

    def issue(title: str, count: int, users: int, first: str = "2026-10-03T15:05:00Z",
              project: dict | None = None) -> dict:
        return {"shortId": "X-1", "title": title, "count": str(count), "userCount": users,
                "firstSeen": first, "culprit": "", "project": be if project is None else project}

    def world(its: list[dict]) -> tuple[int, str]:
        """(exit code, services written) of report() for one world."""
        if blind:
            knobs_ = Knobs(min_events=10**9, min_users=0, noise_titles=("",),
                           noise_escalate_events=10**9, service_projects=(("x", ("\0",)),))
        else:
            knobs_ = knobs
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "svc")
            with contextlib.redirect_stdout(io.StringIO()):
                code = report(its, knobs_, live, out)
            return code, Path(out).read_text(encoding="utf-8")

    with_project = [{**i, "project": be} for i in recorded]
    cases: list[tuple[str, bool]] = [
        ("recorded 2026-10-03 swap noise (3 issues, 2 events, 0 users) -> no rollback",
         world(with_project) == (0, "")),
        ("noise BEFORE the deploy went live (50 events, first seen 14:00) -> no rollback",
         world([issue("KeyError: 'id'", 50, 0, first="2026-10-03T14:00:00Z")]) == (0, "")),
        ("new backend error after the deploy, 1 user -> rollback backend",
         world([issue("ValueError: boom", 2, 1)]) == (1, "backend")),
        ("new backend error after the deploy, 5 events 0 users -> rollback backend",
         world([issue("KeyError: 'id'", 5, 0)]) == (1, "backend")),
        ("new FRONTEND error -> rollback frontend only (the #911 shape)",
         world([issue("TypeError: x is undefined", 3, 1, project=fe)]) == (1, "frontend")),
        ("an issue in neither project -> listed, no rollback",
         world([issue("Boom", 99, 9, project={"slug": "side-project", "platform": "go"})]) == (0, "")),
        ("a ClientDisconnect flood (20 events) -> rollback",
         world([issue("ClientDisconnect", 20, 0)]) == (1, "backend")),
        ("unreadable counts fail toward the alarm",
         world([{"title": "?", "count": "n/a", "userCount": None, "project": be}])[0] == 1),
        ("missing project fails toward the alarm, all services",
         world([issue("Boom", 9, 1, project={})]) == (1, ALL)),
    ]
    print("DRILL - sentry_poll must ignore deploy noise and still trip on real errors, "
          "on the right service." + ("  [BLIND: rule swapped for 'ignore everything']" if blind else ""))
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
