#!/usr/bin/env python3
"""Decide whether the harness should SPEAK — announce the transition, not the state.

WHY THIS EXISTS
---------------
`.github/actions/slack` can now send. That is the easy half, and on its own it is
a liability. A sender wired to "post when the run is red" produces a channel that
gets muted in a week, and a muted channel is indistinguishable from the nine
empty ones we started with.

THE NUMBERS THAT DECIDE THIS (measured, not assumed).
`gh run list --limit 400` on 2026-08-17 — 400 real runs spanning 3.61 days:

    posting EVERY failure ................ 62.0 messages/week
    posting only TRANSITIONS ............. 19.4 messages/week   (-69%)

and the single line that makes the case:

    synthetic-live was 15 runs, 15 failures — 29.0 msg/week of pure repetition,
    every one of them saying the same thing he already knew. Transitions: 0.

But the same measurement kills the naive version of this idea, so read on:

    Security scanning: 1.9 failures/week -> 3.9 TRANSITIONS/week.

A FLAPPING check produces MORE transitions than failures, because each recovery
is a transition too. Transition-logic is not automatically quieter; it is quieter
for STUCK checks and louder for FLAPPING ones. That is why routing (below) does
the other half of the work, and why "we added transition logic" is not by itself
a claim that the volume target is met.

THE FOUR CELLS
--------------
    green -> red    ANNOUNCE   news; something is wrong that was not before
    red   -> red    SILENT     noise; he already knows, this is the muting agent
    red   -> green  ANNOUNCE   relief; this is how he stops worrying
    green -> green  SILENT     uptime ran 142/142 green here — must cost nothing

WHERE THE PREVIOUS STATE LIVES, AND WHY
---------------------------------------
An open GitHub issue whose title is exactly `Loop RED: <key>`.

This is not a new invention: `.github/workflows/uptime.yml` already does exactly
this and is the one loop in this repo with a working volume rule. Its notify job
opens one issue on failure, says "already open - staying quiet" on repeat, and
closes it on recovery. Reusing that means the Slack voice and the issue tracker
cannot disagree about whether an incident is open.

The alternatives, and why each is worse HERE:

  * `gh run list` previous conclusion — REJECTED, and it is the trap the brief
    warned about. A workflow's last conclusion moves for reasons that have
    nothing to do with the monitored condition: a cancelled run, a re-run, an
    infra flake, a different trigger, a run on another branch. Worse, one run
    carries ONE conclusion while a workflow can check several independent things,
    so N conditions would collapse into one bit. Every drill dispatch would also
    read as a transition. It would make the channel noisier than no logic at all.
  * a repo variable — REJECTED. Invisible (nobody browses repo variables), no
    history, no comment thread, needs a write scope on every detector, and when
    two workflows race the last writer silently wins.
  * a file in the repo — REJECTED. A state write per incident means a commit per
    incident, and on this repo a push to main auto-deploys in 2m14s.

The issue wins on one further point that matters more than any of the above: it
is READABLE BY THE OWNER. If the Slack message is wrong he can see the state that
produced it, and close it by hand.

THE FAILURE THIS FILE REFUSES TO HAVE
-------------------------------------
If the previous state cannot be read, this exits NON-ZERO and decides nothing.

That is deliberate and it is the whole reason this is a script and not three
lines of bash. The tempting `|| echo ""` fallback picks a default, and BOTH
defaults are catastrophic in the quiet way this repo keeps getting caught by:

    assume "absent" -> every run looks like green->red -> spam -> he mutes it
    assume "open"   -> every run looks like red->red   -> silence -> mute again

Either way the channel dies and every run stays green while it happens. That is
the shape of all ten dead guards: a confident answer to a question that was never
actually asked.

USAGE
    python scripts/slack_transition.py --key uptime --state failing
    python scripts/slack_transition.py --key uptime --state ok --marker open
    python scripts/slack_transition.py --self-check
    python scripts/slack_transition.py --drill
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Default home for "a loop ran", per the ROUTING block in
# .github/actions/slack/action.yml. Promoting a message out of a log-* channel
# needs a reason you can say in one sentence.
DEFAULT_CHANNEL = "log-github-ci"

MARKER_PREFIX = "Loop RED: "


class MarkerUnreadable(RuntimeError):
    """We could not determine the PREVIOUS state. Not a state. An absence of one."""


@dataclass(frozen=True)
class Decision:
    send: bool
    kind: str  # opened | recovered | still_failing | still_ok
    severity: str  # critical | warn | info
    reason: str


def marker_title(key: str) -> str:
    """The issue title that IS the state. Must match uptime.yml's `title` exactly."""
    return f"{MARKER_PREFIX}{key}"


# ─────────────────────────────────────────────────────────────────────────────
# THE DECISION. Pure: no clock, no network, no environment. Every branch is
# reachable from a unit test, which is the only reason the truth table below can
# be trusted.
# ─────────────────────────────────────────────────────────────────────────────
def decide(*, failing: bool, marker_open: bool, severity: str = "critical") -> Decision:
    if failing and not marker_open:
        # green -> red. NEWS.
        return Decision(True, "opened", severity, "newly failing — this is the transition worth interrupting him for")
    if failing and marker_open:
        # red -> red. NOISE. The muting agent. This branch is the entire point.
        return Decision(False, "still_failing", "info", "still failing and already announced — repeating it is how a channel becomes unread")
    if not failing and marker_open:
        # red -> green. RELIEF.
        return Decision(True, "recovered", "info", "recovered — he was told it broke, so he must be told it is fixed")
    # green -> green. SILENCE.
    return Decision(False, "still_ok", "info", "still healthy and nothing was open — silence is the correct output")


# ─────────────────────────────────────────────────────────────────────────────
# READING THE PREVIOUS STATE
# ─────────────────────────────────────────────────────────────────────────────
def read_marker(key: str, gh_bin: str | None = None) -> bool:
    """True if the incident issue for `key` is currently OPEN.

    Raises MarkerUnreadable rather than guessing. See the module docstring: a
    guess here silently destroys the channel in one direction or the other.
    """
    gh = gh_bin or os.environ.get("SLACK_TRANSITION_GH") or "gh"
    if shutil.which(gh) is None:
        raise MarkerUnreadable(
            f"cannot read the previous state: '{gh}' is not on PATH, so the marker "
            f"issue '{marker_title(key)}' could not be looked up. Refusing to guess."
        )
    try:
        proc = subprocess.run(
            [gh, "issue", "list", "--state", "open", "--limit", "200", "--json", "number,title"],
            capture_output=True,
            # text=True alone decodes with the machine's locale and dies on an
            # em-dash; issue titles in this repo are full of them. There is a
            # guard for this (scripts/encoding_guard.py).
            encoding="utf-8",
            errors="replace",
            timeout=60,
            cwd=str(ROOT),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise MarkerUnreadable(f"cannot read the previous state — marker lookup failed to run: {exc}") from exc

    if proc.returncode != 0:
        raise MarkerUnreadable(
            f"cannot read the previous state — marker lookup exited {proc.returncode}: "
            f"{(proc.stderr or '').strip()[:300]}"
        )
    try:
        issues = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError as exc:
        raise MarkerUnreadable(f"cannot read the previous state — marker lookup returned unparseable JSON: {exc}") from exc

    want = marker_title(key)
    return any(str(i.get("title", "")) == want for i in issues)


# ─────────────────────────────────────────────────────────────────────────────
# SELF-CHECK — the property, asserted against the real functions
# ─────────────────────────────────────────────────────────────────────────────
def self_check() -> list[str]:
    """Return a list of failures. Empty list == the volume rule holds."""
    bad: list[str] = []

    cells = [
        # (failing, marker_open, expect_send, expect_kind)
        (True, False, True, "opened"),
        (True, True, False, "still_failing"),
        (False, True, True, "recovered"),
        (False, False, False, "still_ok"),
    ]
    for failing, marker_open, want_send, want_kind in cells:
        d = decide(failing=failing, marker_open=marker_open)
        arrow = f"{'red' if marker_open else 'green'}->{'red' if failing else 'green'}"
        if d.send != want_send:
            bad.append(f"{arrow}: send={d.send}, expected {want_send} ({d.kind})")
        if d.kind != want_kind:
            bad.append(f"{arrow}: kind={d.kind!r}, expected {want_kind!r}")

    # The load-bearing one: an unreadable marker must RAISE, never resolve to a
    # state. This is checked here — not only in pytest — so the drill can break
    # it and watch this go red.
    try:
        read_marker("drill-probe", gh_bin="definitely-not-a-real-binary-xyz")
    except MarkerUnreadable:
        pass
    except Exception as exc:  # noqa: BLE001 - any other error is still a wrong shape
        bad.append(f"unreadable marker raised {type(exc).__name__}, expected MarkerUnreadable")
    else:
        bad.append(
            "an unreadable marker returned a state instead of raising — the notifier "
            "would now guess, and guessing either spams or goes mute"
        )
    return bad


# ─────────────────────────────────────────────────────────────────────────────
# THE DRILL — break it on purpose, demand RED. Plus a negative control.
#
# Mutates a COPY in a temp dir. It never writes to the real tree: an earlier
# checker in this repo restored the real file in a `finally`, which Ctrl-C does
# not run, so an interrupted drill left the repo holding the exact defect the
# checker existed to catch.
# ─────────────────────────────────────────────────────────────────────────────
DRILLS: list[tuple[str, str, str, str]] = [
    (
        "red->red starts announcing (the spam bug)",
        'return Decision(False, "still_failing"',
        'return Decision(True, "still_failing"',
        "red",
    ),
    (
        "red->green goes silent (he never learns it recovered)",
        'return Decision(True, "recovered"',
        'return Decision(False, "recovered"',
        "red",
    ),
    (
        "an unreadable marker is guessed as 'no incident open' (the dead-guard shape)",
        "raise MarkerUnreadable(\n            f\"cannot read the previous state: '{gh}' is not on PATH",
        "return False  # noqa\n        raise MarkerUnreadable(\n            f\"cannot read the previous state: '{gh}' is not on PATH",
        "red",
    ),
    (
        "NEGATIVE CONTROL: reword a human-readable reason string",
        "still failing and already announced",
        "still broken and already mentioned",
        "quiet",
    ),
]


def run_drill() -> int:
    src = Path(__file__).resolve()
    original = src.read_text(encoding="utf-8")
    passed = 0
    failed: list[str] = []

    with tempfile.TemporaryDirectory(prefix="slack-transition-drill-") as tmp:
        for name, anchor, replacement, expect in DRILLS:
            # An anchor that no longer matches is itself a dead guard — guard #2
            # in this repo was a sed range whose anchor had been deleted, which
            # silently fed 82 wrong lines and stayed green. So: no match => LOUD.
            if anchor not in original:
                failed.append(f"{name}: ANCHOR NOT FOUND — this drill has rotted and is testing nothing")
                continue
            mutant_src = original.replace(anchor, replacement, 1)
            if mutant_src == original:
                failed.append(f"{name}: mutation changed nothing")
                continue

            mutant = Path(tmp) / "mutant.py"
            mutant.write_text(mutant_src, encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(mutant), "--self-check"],
                capture_output=True,
                encoding="utf-8",
                errors="replace",
                timeout=120,
            )
            went_red = proc.returncode != 0
            if expect == "red" and went_red:
                passed += 1
                print(f"  [RED as required] {name}")
            elif expect == "quiet" and not went_red:
                passed += 1
                print(f"  [stayed quiet   ] {name}")
            elif expect == "red":
                failed.append(f"{name}: broke it on purpose and the check STAYED GREEN")
            else:
                failed.append(
                    f"{name}: a cosmetic change tripped the check — a checker that "
                    f"fires at any change is not a checker\n{proc.stdout}"
                )

    # And the real file must still be clean, or the drill proved nothing.
    live = self_check()
    if live:
        failed.append("the UNMUTATED file does not pass its own self-check: " + "; ".join(live))
    else:
        passed += 1
        print("  [baseline green ] unmutated file passes self-check")

    print()
    if failed:
        print(f"DRILL FAILED — {passed} ok, {len(failed)} problem(s):")
        for f in failed:
            print(f"  ! {f}")
        return 1
    print(f"DRILL PASSED — {passed}/{passed} (3 deliberate breaks went red, 1 negative control stayed quiet)")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Announce the transition, not the state.")
    p.add_argument("--key", help="identifies the condition, e.g. 'uptime'. Becomes the marker issue title.")
    p.add_argument("--state", choices=("failing", "ok"), help="what the check just measured")
    p.add_argument(
        "--marker",
        choices=("open", "absent"),
        help="previous state, injected. Omit to read it from the marker issue via gh.",
    )
    p.add_argument("--channel", default=DEFAULT_CHANNEL, help=f"Slack channel (default {DEFAULT_CHANNEL})")
    p.add_argument("--severity", default="critical", choices=("critical", "warn", "info"))
    p.add_argument("--self-check", action="store_true", help="assert the volume rule holds")
    p.add_argument("--drill", action="store_true", help="break it on purpose and demand RED")
    args = p.parse_args(argv)

    if args.drill:
        return run_drill()

    if args.self_check:
        bad = self_check()
        if bad:
            print("SELF-CHECK FAILED:")
            for b in bad:
                print(f"  ! {b}")
            return 1
        print("self-check ok — the four cells hold and an unreadable marker refuses to guess")
        return 0

    if not args.key or not args.state:
        p.error("--key and --state are required unless --self-check/--drill")

    if args.marker is not None:
        marker_open = args.marker == "open"
    else:
        try:
            marker_open = read_marker(args.key)
        except MarkerUnreadable as exc:
            # Loud, and NO decision on stdout. A caller parsing stdout must get
            # nothing rather than a plausible-looking guess.
            print(f"::error::{exc}", file=sys.stderr)
            print(f"SLACK TRANSITION UNDECIDABLE — {exc}", file=sys.stderr)
            return 3

    d = decide(failing=args.state == "failing", marker_open=marker_open, severity=args.severity)
    out = {
        "send": d.send,
        "kind": d.kind,
        "severity": d.severity,
        "channel": args.channel,
        "key": args.key,
        "marker_open": marker_open,
        "reason": d.reason,
    }
    print(json.dumps(out, indent=2))

    gh_out = os.environ.get("GITHUB_OUTPUT")
    if gh_out:
        with open(gh_out, "a", encoding="utf-8") as fh:
            fh.write(f"send={'true' if d.send else 'false'}\n")
            fh.write(f"kind={d.kind}\n")
            fh.write(f"severity={d.severity}\n")
            fh.write(f"channel={args.channel}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
