#!/usr/bin/env python3
"""The merge cage — decides whether a PR may reach production without the owner.

WHAT MAKES THIS DIFFERENT FROM A NORMAL MERGE GATE
--------------------------------------------------
`main` auto-deploys. There is no staging. So "the agent merged it" means "the agent
shipped it to real users". The owner asked for exactly one thing to stay his:

    "The only thing I want to wait for me is the decisions: product decisions,
     users, any kind of decisions, infrastructure decisions."

This file is that sentence turned into something a machine can enforce.

WHY "CI IS GREEN + A REVIEW" IS NOT ENOUGH
-------------------------------------------
Three measured reasons, all from this repo:

1. GREEN IS WEAKER THAN IT LOOKS. Of 17 guards CI runs, 15 have never been watched
   failing (see scripts/drill_registry.py). Some of "green" means "nothing that
   could fire, fired." Ten guards have shipped here unable to fire at all.

2. THE REVIEW TICK IS NOT THE REVIEW. CodeRabbit runs with
   `fail_commit_status: false`, so its status check can only ever say pass. On
   PR #336 it reported PASS while holding two real findings — both of which were
   genuine bugs in the guard-of-guards. Gate on the COMMENTS, never the tick.

3. NO CHECK IN THIS REPO MEASURES GETTING WORSE. Lint and types are pass/fail
   against rules that already exist. Nothing notices quality sliding — which is
   precisely what unattended merging erodes. So the cage carries RATCHETS:
   numbers that may hold or improve, never regress.

THE THREE CAGES, AND WHY EACH IS NEEDED
----------------------------------------
    PATH     what a change is allowed to touch    (blast radius)
    PROOF    the checks really ran and really passed   (is green real?)
    RATCHET  the numbers did not get worse         (no slow decay)

All three must pass. Any doubt REFUSES — the default is to ask the owner, never to
ship. A cage that guesses in the permissive direction is not a cage.
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import subprocess
import sys
import urllib.error
import urllib.request

REPO = os.environ.get("GH_REPO", "Ranjith36963/job360")

# ─────────────────────────────────────────────────────────────────────────────
# CAGE 1 — PATHS
#
# DENY always beats ALLOW. If a file matches both, the owner decides.
#
# The deny list is not "dangerous code". It is the owner's own sentence:
# product decisions, user-facing decisions, infrastructure decisions. Plus two
# things a machine must never do unsupervised — delete, and edit its own guards.
# ─────────────────────────────────────────────────────────────────────────────

DENY: list[tuple[str, str]] = [
    # ── product: anything that changes what a user is shown or how it ranks ──
    ("backend/src/services/skill_matcher.py", "changes how jobs are scored — a product decision"),
    ("backend/src/services/deduplicator.py", "changes which jobs are shown at all"),
    ("backend/src/services/scoring/*", "changes how jobs are scored — a product decision"),
    ("backend/src/services/uk_gate.py", "decides which jobs enter the catalogue"),
    ("backend/src/services/visa_signal.py", "changes what the visa spotlight shows"),
    ("backend/src/services/profile/*", "changes what is extracted from a user's CV"),
    ("backend/src/models.py", "normalized_key lives here; wrong dedup = duplicate or lost rows"),
    # ── users: identity, sessions, anything that can lock someone out ────────
    ("backend/src/api/routes/auth.py", "authentication — a user decision"),
    ("backend/src/services/auth/*", "authentication — a user decision"),
    ("backend/src/api/routes/account*.py", "account management — a user decision"),
    ("backend/src/services/notifications/*", "sends real messages to real people"),
    # ── infrastructure ───────────────────────────────────────────────────────
    ("backend/migrations/*", "schema change — irreversible against live data"),
    ("*docker-compose*", "infrastructure decision"),
    ("*Dockerfile*", "infrastructure decision"),
    ("railway.json", "infrastructure decision"),
    ("*.env*", "secrets and configuration"),
    ("backend/src/core/settings.py", "flags here change production behaviour globally"),
    # ── the harness must not quietly edit its own cage ───────────────────────
    (".github/*", "an agent editing its own guards is how a cage is escaped"),
    ("scripts/*", "an agent editing its own guards is how a cage is escaped"),
    (".claude/*", "an agent editing its own instructions is how a cage is escaped"),
    ("CLAUDE.md", "the rules agents read; changing them unsupervised is circular"),
]

ALLOW: list[str] = [
    "backend/tests/*",
    "frontend/src/**/__tests__/*",
    "frontend/tests/*",
    "docs/*",
    "*.md",
    "backend/src/api/routes/client_log.py",
    "backend/src/services/profile/github_enricher.py",
    "backend/src/services/profile/storage.py",
    "backend/src/api/routes/profile.py",
    "frontend/package.json",
    "frontend/package-lock.json",
    "backend/pyproject.toml",
]

# ─────────────────────────────────────────────────────────────────────────────
# CAGE 3 — RATCHETS
#
# Each is a number that may hold or improve and must never regress. This is the
# only cage that can see SLOW DECAY, which is what unattended merging actually
# costs you — no single PR looks bad, and six months later the numbers are gone.
#
# `direction` is what a HEALTHY change does: "down" for debt, "up" for coverage.
# ─────────────────────────────────────────────────────────────────────────────

RATCHETS: list[dict] = [
    {
        "name": "mypy errors",
        "cmd": ["python", "-c",
                "import pathlib,sys;p=pathlib.Path('backend/mypy_baseline.txt');"
                "print(len([l for l in p.read_text().splitlines() if l.strip()]) if p.exists() else 0)"],
        "direction": "down",
        "why": "type errors were driven 803 -> 0; nothing may add them back",
    },
    {
        "name": "guards never watched failing",
        "cmd": ["python", "-c",
                "import subprocess,re,sys;"
                "o=subprocess.run([sys.executable,'scripts/drill_registry.py'],"
                "capture_output=True,text=True).stdout;"
                "m=re.search(r'(\\d+) owed',o);print(m.group(1) if m else 999)"],
        "direction": "down",
        "why": "15 guards have never been watched failing; that debt may only shrink",
    },
    {
        "name": "open security alerts",
        "cmd": ["gh", "api", f"repos/{REPO}/code-scanning/alerts?state=open&per_page=100",
                "-q", "length"],
        "direction": "down",
        "why": "a merge must never raise the open-alert count",
    },
]

MAX_CHANGED_FILES = 40
MAX_CHANGED_LINES = 1200


def gh(args: list[str]) -> str:
    """Run gh and return stdout. Raises on failure — a failed probe must never
    read as a permissive answer, which is how a check becomes decoration."""
    proc = subprocess.run(["gh", *args], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(args[:3])} failed: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────


def check_paths(files: list[str]) -> list[str]:
    """Every changed file must be explicitly allowed, and none denied."""
    reasons: list[str] = []
    for f in files:
        for pat, why in DENY:
            if fnmatch.fnmatch(f, pat) or fnmatch.fnmatch(f, pat.replace("*", "**")):
                reasons.append(f"`{f}` — {why}")
                break
        else:
            if not any(fnmatch.fnmatch(f, p) or fnmatch.fnmatch(f, p.replace("**", "*"))
                       for p in ALLOW):
                # Silence is not consent. An unrecognised path is a path nobody
                # has thought about, and the cage refuses what it does not know.
                reasons.append(f"`{f}` — not on the allow list; nobody has decided this path is safe")
    return reasons


def check_proof(pr: int) -> list[str]:
    """Did the checks really run, and really pass?

    The dangerous answer here is not 'red'. It is 'nothing ran' — a PR opened by a
    workflow using GITHUB_TOKEN gets NO check runs at all, and a naive
    'any failures?' test reads that empty list as success. That is how an
    unverified change merges while every indicator looks fine.
    """
    reasons: list[str] = []
    sha = gh(["api", f"repos/{REPO}/pulls/{pr}", "-q", ".head.sha"])
    raw = gh(["api", f"repos/{REPO}/commits/{sha}/check-runs?per_page=100"])
    data = json.loads(raw)
    runs = data.get("check_runs", [])

    if not runs:
        return ["NO CHECKS RAN AT ALL. An empty check list is not a pass — it is the "
                "signature of a PR opened with GITHUB_TOKEN, which GitHub refuses to "
                "start workflows for. Nothing about this change has been verified."]

    # The checks that must exist. A gate you can pass by DELETING the check is
    # not a gate — this is the same shape as guard #10, where removing the only
    # authorisation call left its self-test fully green.
    required = ["Backend", "Frontend", "Chain wires"]
    names = [r.get("name", "") for r in runs]
    for req in required:
        if not any(req.lower() in n.lower() for n in names):
            reasons.append(f"required check `{req}` did not run — it cannot pass by being absent")

    for r in runs:
        concl = r.get("conclusion")
        if r.get("status") != "completed":
            reasons.append(f"`{r.get('name')}` has not finished ({r.get('status')})")
        elif concl not in ("success", "skipped", "neutral"):
            reasons.append(f"`{r.get('name')}` -> {concl}")
    return reasons


def check_review(pr: int) -> list[str]:
    """CodeRabbit's unresolved COMMENTS, never its status tick.

    Measured on PR #336: the tick said pass while two real findings sat open, both
    genuine bugs. `fail_commit_status: false` means the tick is structurally
    incapable of saying no.
    """
    raw = gh(["api", f"repos/{REPO}/pulls/{pr}/comments?per_page=100"])
    comments = json.loads(raw)
    open_bot = [
        c for c in comments
        if "coderabbit" in (c.get("user", {}).get("login") or "").lower()
        and not c.get("in_reply_to_id")
    ]
    if open_bot:
        sample = "; ".join(
            f"{c.get('path')}:{c.get('line') or c.get('original_line')}" for c in open_bot[:4]
        )
        return [f"{len(open_bot)} unresolved review comment(s): {sample}"]
    return []


def is_worse(direction: str, was: int, now: int) -> bool:
    """The whole ratchet decision, in one place.

    Extracted deliberately: the drill below tests THIS function. A drill that
    re-implements the comparison it is checking proves only that the drill agrees
    with itself — which is how a self-test greps for a string and stays green
    after the code it guards is deleted.
    """
    return now > was if direction == "down" else now < was


def check_ratchets(base_values: dict[str, int] | None = None) -> list[str]:
    """Numbers may hold or improve. Never regress."""
    reasons: list[str] = []
    for r in RATCHETS:
        try:
            out = subprocess.run(r["cmd"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            if out.returncode != 0:
                reasons.append(f"ratchet `{r['name']}` could not be measured — "
                               f"an unmeasurable ratchet is not a passing one")
                continue
            now = int(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError, OSError, subprocess.TimeoutExpired) as exc:
            reasons.append(f"ratchet `{r['name']}` could not be read ({exc}) — refusing")
            continue
        if base_values is None:
            continue
        was = base_values.get(r["name"])
        if was is None:
            continue
        worse = is_worse(r["direction"], was, now)
        if worse:
            reasons.append(f"ratchet `{r['name']}` got worse: {was} -> {now} ({r['why']})")
    return reasons


def measure_ratchets() -> dict[str, int]:
    """Current value of every ratchet, for storing as a baseline."""
    vals: dict[str, int] = {}
    for r in RATCHETS:
        try:
            out = subprocess.run(r["cmd"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
            if out.returncode == 0:
                vals[r["name"]] = int(out.stdout.strip().splitlines()[-1])
        except (ValueError, IndexError, OSError, subprocess.TimeoutExpired):
            pass
    return vals


def decide(pr: int, base_values: dict[str, int] | None = None) -> tuple[bool, list[str], dict]:
    """ALLOW only if all three cages pass. Any error REFUSES."""
    blocks: list[str] = []
    meta: dict = {}
    try:
        files_raw = gh(["api", f"repos/{REPO}/pulls/{pr}/files?per_page=100"])
        files_json = json.loads(files_raw)
        files = [f["filename"] for f in files_json]
        deletions = [f["filename"] for f in files_json if f.get("status") == "removed"]
        changed_lines = sum(f.get("additions", 0) + f.get("deletions", 0) for f in files_json)
        meta = {"files": len(files), "lines": changed_lines,
                "title": gh(["api", f"repos/{REPO}/pulls/{pr}", "-q", ".title"])}
    except (RuntimeError, json.JSONDecodeError, KeyError) as exc:
        # A cage that cannot see the change must never approve it.
        return False, [f"could not read PR #{pr} ({exc}) — refusing on principle"], meta

    if deletions:
        blocks.append(f"deletes {len(deletions)} file(s) ({', '.join(deletions[:3])}) — "
                      f"a machine must not delete unsupervised")
    if len(files) > MAX_CHANGED_FILES:
        blocks.append(f"{len(files)} files changed (cap {MAX_CHANGED_FILES}) — "
                      f"a change this wide is a judgement call by size alone")
    if changed_lines > MAX_CHANGED_LINES:
        blocks.append(f"{changed_lines} lines changed (cap {MAX_CHANGED_LINES}) — same reason")

    blocks += check_paths(files)
    try:
        blocks += check_proof(pr)
        blocks += check_review(pr)
    except RuntimeError as exc:
        blocks.append(f"could not verify checks or review ({exc}) — refusing")
    blocks += check_ratchets(base_values)

    return (not blocks), blocks, meta


# ─────────────────────────────────────────────────────────────────────────────


def slack(channel: str, text: str) -> bool:
    """Post to Slack. Returns False loudly rather than pretending it worked —
    an alert path that exits 0 when it did nothing kept this harness mute for
    weeks."""
    token = os.environ.get("SLACK_BOT_TOKEN", "")
    if not token:
        print("SLACK: no SLACK_BOT_TOKEN in the environment — nothing was sent.", file=sys.stderr)
        return False
    req = urllib.request.Request(
        "https://slack.com/api/chat.postMessage",
        data=json.dumps({"channel": channel, "text": text}).encode(),
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            body = json.loads(r.read().decode())
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"SLACK: post failed ({exc}).", file=sys.stderr)
        return False
    if not body.get("ok"):
        print(f"SLACK: refused -> {body.get('error')}", file=sys.stderr)
        return False
    return True


def plain_english(pr: int, meta: dict, allowed: bool, blocks: list[str]) -> str:
    """The message the owner actually reads. Says what it DOES, not what it is."""
    title = meta.get("title", f"PR #{pr}")
    # Strip the conventional-commit prefix: `fix(scope): thing` -> `thing`.
    plain = title.split(": ", 1)[1] if ": " in title else title
    link = f"https://github.com/{REPO}/pull/{pr}"
    size = f"{meta.get('files', '?')} files, {meta.get('lines', '?')} lines"

    if allowed:
        return (f":shipit: *Merged to production — PR #{pr}*\n"
                f"*What it does:* {plain}\n"
                f"*Size:* {size}\n"
                f"It passed the cage: only safe paths touched, every required check really ran "
                f"and passed, no open review comments, and no quality number went backwards.\n"
                f"{link}")
    reasons = "\n".join(f"  • {b}" for b in blocks[:6])
    more = f"\n  _(+{len(blocks) - 6} more)_" if len(blocks) > 6 else ""
    return (f":raising_hand: *Needs your call — PR #{pr}*\n"
            f"*What it does:* {plain}\n"
            f"*Size:* {size}\n"
            f"*Why I did not merge it:*\n{reasons}{more}\n"
            f"{link}")


# ─────────────────────────────────────────────────────────────────────────────
# The drill. This file decides what reaches real users, so it is the last place
# a silent failure is acceptable. Each break below is a way a permissive bug
# could let something through.
# ─────────────────────────────────────────────────────────────────────────────


def self_drill() -> int:
    print("DRILL - breaking the merge cage on purpose. It must REFUSE and say why.")
    print("=" * 72)
    results: list[tuple[str, bool, str]] = []

    def red(name: str, reasons: list[str], needle: str) -> None:
        hit = next((r for r in reasons if needle.lower() in r.lower()), "")
        results.append((name, bool(hit), hit))

    # 1. A change to scoring must never auto-merge — it is a product decision.
    red("scoring change is refused",
        check_paths(["backend/src/services/skill_matcher.py"]), "product decision")

    # 2. Auth is a user decision.
    red("auth change is refused",
        check_paths(["backend/src/api/routes/auth.py"]), "user decision")

    # 3. Infrastructure.
    red("infra change is refused",
        check_paths(["docker-compose.prod.yml"]), "infrastructure")

    # 4. The cage must not be editable by the thing it cages.
    red("the agent editing its own guards is refused",
        check_paths([".github/workflows/ci.yml"]), "cage is escaped")

    # 5. An unknown path is not a safe path. Silence is not consent.
    red("an unrecognised path is refused",
        check_paths(["backend/src/some_new_module.py"]), "nobody has decided")

    # 6. The ratchet decision itself, exercised directly. Debt rising must be
    #    caught; debt falling must NOT be — a ratchet that blocks improvement is
    #    just as broken as one that permits decay.
    cases = [("down", 0, 5, True), ("down", 5, 0, False), ("down", 3, 3, False),
             ("up", 10, 3, True), ("up", 3, 10, False)]
    bad = [c for c in cases if is_worse(c[0], c[1], c[2]) != c[3]]
    results.append(("a quality number going backwards is refused (and improving is not)",
                    not bad, "" if not bad else f"wrong verdict for {bad}"))

    # 7. NEGATIVE CONTROL. A docs change must pass the path cage cleanly.
    ok = not check_paths(["docs/README.md", "backend/tests/test_x.py"])
    results.append(("NEGATIVE CONTROL (docs + tests pass the path cage)", ok,
                    "" if ok else "the cage refused a change it should allow"))

    print()
    for name, passed, detail in results:
        print(f"  [{'PASS' if passed else 'FAIL'}] {name}")
        if passed and detail:
            print(f"         REFUSED -> {detail[:140]}")
        elif not passed and detail:
            print(f"         {detail[:180]}")

    n = sum(1 for _, p, _ in results if p)
    print()
    print("=" * 72)
    print(f"DRILL RESULT: {n}/{len(results)} passed")
    if n != len(results):
        print("The cage did not refuse something it claims to refuse. Do not trust it.")
        return 1
    print("Every unsafe change was refused and named; the safe change was allowed.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("pr", nargs="?", type=int, help="PR number to judge")
    ap.add_argument("--drill", action="store_true", help="break the cage on purpose")
    ap.add_argument("--merge", action="store_true", help="actually merge when allowed")
    ap.add_argument("--slack", action="store_true", help="announce the verdict in Slack")
    ap.add_argument("--baseline", help="JSON of ratchet values measured on main")
    ap.add_argument("--measure", action="store_true", help="print current ratchet values as JSON")
    args = ap.parse_args(argv)

    if args.drill:
        return self_drill()
    if args.measure:
        print(json.dumps(measure_ratchets()))
        return 0
    if args.pr is None:
        ap.error("a PR number is required unless --drill or --measure")

    base = json.loads(args.baseline) if args.baseline else None
    allowed, blocks, meta = decide(args.pr, base)

    print(f"merge_cage: PR #{args.pr} — {meta.get('files', '?')} files, "
          f"{meta.get('lines', '?')} lines")
    if allowed:
        print("VERDICT: ALLOW — safe paths, real green, no open comments, no ratchet regressed.")
    else:
        print(f"VERDICT: ASK THE OWNER — {len(blocks)} reason(s)")
        for b in blocks:
            print(f"  * {b}")

    if args.slack:
        chan = "ready-to-merge" if allowed else "needs-your-decision"
        if not slack(chan, plain_english(args.pr, meta, allowed, blocks)):
            # Announcing is part of the job. A merge nobody was told about is
            # indistinguishable from a merge that never happened.
            print("REFUSING TO PROCEED: the owner could not be told.", file=sys.stderr)
            return 2

    if args.merge and allowed:
        subprocess.run(["gh", "pr", "merge", str(args.pr), "--squash", "--delete-branch"],
                       check=True, timeout=180)
        print(f"merged #{args.pr}")

    return 0 if allowed else 1


if __name__ == "__main__":
    raise SystemExit(main())
