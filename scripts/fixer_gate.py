#!/usr/bin/env python3
"""THE FIXER GATE — one place decides whether `pr-repair.yml` is dispatched.

MEASURED 2026-10-18/19 (the runaway this file exists to stop)
-------------------------------------------------------------
`pr-repair.yml` was dispatched 141 times in 3.7 days, 71 times between
22:47Z and 01:22Z. Every one came from `finding-watch.yml`, which rang the bell
on EVERY event: each completed check suite (semgrep, code scanning, one per
GitHub Actions workflow -- 6 to 9 per push), each review comment, each push.
PR #891 got 27 dispatches for 6 head commits.

The design said "a duplicate dispatch is harmless": pr-repair's concurrency
group `pr-repair-<pr>` cancels extra QUEUED runs. It does cancel them (48 of the
71, 0 jobs), but it keeps ONE pending run, and that run always starts the
second the running one ends (11 of 11 pairs, 1-2 s apart). So every busy PR got
a second and third real ~35-minute run: 4 on the SAME head the previous run had
just judged, 3 on a PR that had merged in the meantime. Of 20 runs that started,
0 pushed a fix and 9 said "found nothing to change".

The per-PR busy check was left out because a pr-repair run did not say which
PR it repairs (the runs API exposes no inputs, and `head_branch` is always
`main`). pr-repair now carries a `run-name` that does:

    PR repair #<pr> head=<head sha> findings=<fingerprint> attempt=<n>

and this file reads it, in three modes.

1. THE GATE (`decide`, called by finding-watch.yml before every dispatch)
  a. A pr-repair run for this PR is not `completed` (queued, in_progress,
     waiting, requested, pending, or a status GitHub adds later) -> SKIP.
  b. A run that is not completed and whose title names NO PR (an old-style
     run) -> SKIP. "I could not tell" must never read as "nobody is busy".
  c. A completed run for this PR ended `success` on this exact head sha with
     the same findings fingerprint, and the PR's attempt count has not gone
     DOWN since that run was dispatched -> SKIP: nothing changed since it
     judged them. A new push, a new finding, or the owner removing the
     `autofix:attempt-*` labels (the documented "give it another go") makes
     it dispatch again. `failure` does not block: pr-repair also fails on
     refusals and infrastructure (draft at run time, missing token, a gh
     error), and real repeated failures are bounded by the attempt ceiling.
     `cancelled` (queue-cancelled or a timeout) does not block either.
     Why "not gone down" and not "equal": the title records the count AT
     DISPATCH, and a run that starts work adds one, so "equal" would wave
     the very repeat this rule exists to stop straight through.
  d. Otherwise -> DISPATCH.

2. THE DUPLICATE CHECK (`duplicate_of`, the first step of pr-repair.yml).
   Main's pr-repair.yml runs for every dispatch, even one rung by an OLD copy
   of finding-watch on a PR branched before the gate existed. So a run asks,
   before any work: is another run for my PR active and dispatched before me?
   Or did a run for my PR finish `success` AFTER I was queued, on the head the
   PR still has (and, when both are known, with my findings)? Then I am the
   pending duplicate the concurrency group kept: stop green, count nothing.

3. THE WIRING CHECK (`wiring_problems`, part of --drill). Every
   `gh workflow run pr-repair.yml` in .github/workflows must sit in a run block
   that called this gate and checked its decision first; pr-repair.yml must
   carry the run-name this file parses and run the duplicate check.

USAGE
    fixer_gate.py --pr 891 --sha <40 hex> --attempt 1 \\
        --triage-json triage.json --runs-json repair-runs.json
        -> {"decision": "dispatch"|"skip", "reason", "findings"}
    fixer_gate.py --self-run-id <id> --pr 891 --head-sha <sha> \\
        --head-date <iso> [--findings <fp>] --runs-json runs.json
        -> {"duplicate": true|false, "reason"}
Both exit 0 with a decision and 2 on bad input.
    fixer_gate.py --drill            must exit 0
    fixer_gate.py --drill --blind    negative control: every refusal and the
                                     wiring check switched off -> must exit 1
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# A run is "done" only when GitHub says `completed`. Everything else is active.
DONE = "completed"
# The one conclusion that means "a fixer judged this head and finished". See
# rule 1c for why `failure` and `cancelled` are not in it.
JUDGED = {"success"}

_PR = re.compile(r"(?<![0-9A-Za-z])#([0-9]+)(?![0-9])")
_HEAD = re.compile(r"\bhead=([0-9a-f]{7,40})\b")
_FINDINGS = re.compile(r"\bfindings=([0-9a-f]{12}|none)\b")
_ATTEMPT = re.compile(r"\battempt=([0-9]+)\b")
_SHA = re.compile(r"^[0-9a-f]{40}$")
_FP = re.compile(r"^([0-9a-f]{12}|none)$")


def fingerprint(triage: dict) -> str:
    """Stable id of the FIXABLE findings in a `repairable.py` triage result.

    Thread node ids and red check names, sorted. The same open findings give
    the same value on every sweep; a new thread or a new red check changes it.
    """
    threads = sorted(str(i) for i in (triage.get("threads") or {}).get("fixable_ids") or [])
    checks = sorted(str(c) for c in (triage.get("checks") or {}).get("fixable") or [])
    if not threads and not checks:
        return "none"
    blob = json.dumps({"threads": threads, "checks": checks}, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def title_of(run: dict) -> str:
    return str(run.get("display_title") or "")


def _grab(rx: re.Pattern[str], run: dict) -> str | None:
    m = rx.search(title_of(run))
    return m.group(1) if m else None


def run_pr(run: dict) -> int | None:
    got = _grab(_PR, run)
    return int(got) if got else None


def decide(pr: int, sha: str, findings: str, runs: list[dict], attempt: int = 0,
           blind: bool = False) -> tuple[bool, str]:
    """(dispatch?, reason). Pure: no network, no clock. `blind` switches the
    refusals off -- it exists only for the drill's negative control."""
    if not blind:
        for run in runs:
            status = run.get("status")
            if status == DONE:
                continue
            rid = run.get("id", "?")
            other = run_pr(run)
            if other is None:
                return False, (f"pr-repair run {rid} is {status or 'not completed'} and its "
                               f"title names no PR ({title_of(run)[:60]!r}), so it may be this "
                               f"one: waiting rather than starting a second fixer")
            if other == pr:
                return False, f"pr-repair run {rid} for PR #{pr} is already {status}"
        for run in runs:
            if run.get("status") != DONE or run_pr(run) != pr:
                continue
            head, fp = _grab(_HEAD, run), _grab(_FINDINGS, run)
            if not head or fp is None or not sha.startswith(head) or fp != findings:
                continue
            if run.get("conclusion") not in JUDGED:
                continue
            then = _grab(_ATTEMPT, run)
            if then is not None and attempt < int(then):
                continue  # the attempt labels were removed since: another go
            return False, (f"pr-repair run {run.get('id', '?')} already ran on head "
                           f"{sha[:12]} with these findings ({findings}) and ended "
                           f"{run.get('conclusion')}; nothing changed since")
    return True, (f"no pr-repair run queued or running for PR #{pr}, and none has judged "
                  f"head {sha[:12]} with findings {findings}")


def duplicate_of(pr: int, self_id: int, head: str, head_date: str, findings: str | None,
                 runs: list[dict], blind: bool = False) -> tuple[bool, str]:
    """(am I a duplicate?, reason) for a pr-repair run about to start work."""
    me = next((r for r in runs if r.get("id") == self_id), None)
    if blind or me is None:
        return False, ("not a duplicate" if me else
                       f"run {self_id} is not in the run list; cannot compare, so starting")
    mine = (str(me.get("created_at") or ""), self_id)
    for run in runs:
        rid = run.get("id")
        if rid == self_id or run_pr(run) != pr:
            continue
        if run.get("status") != DONE:
            if (str(run.get("created_at") or ""), rid or 0) < mine:
                return True, f"duplicate of run {rid} (PR #{pr}, {run.get('status')}, dispatched first)"
            continue
        if run.get("conclusion") not in JUDGED or str(run.get("updated_at") or "") <= mine[0]:
            continue
        r_head, r_fp = _grab(_HEAD, run), _grab(_FINDINGS, run)
        saw_head = head.startswith(r_head) if r_head else str(run.get("created_at") or "") >= head_date
        if saw_head and (findings is None or r_fp == findings):
            return True, (f"duplicate of run {rid}: it finished after this run was queued, "
                          f"having judged head {head[:12]}")
    return False, "no earlier active run for this PR and no run judged this head after I was queued"


# ── WIRING ──────────────────────────────────────────────────────────────────
_DISPATCH = "gh workflow run pr-repair.yml"
_BLOCK = re.compile(r"^\s*(?:-\s+)?run:\s")


def _code(line: str) -> bool:
    return not line.lstrip().startswith("#")


def wiring_problems(texts: dict[str, str], blind: bool = False) -> list[str]:
    """Every pr-repair dispatch must be gated; pr-repair must carry the title."""
    if blind:
        return []
    bad: list[str] = []
    gated = 0
    for name, text in sorted(texts.items()):
        lines = text.splitlines()
        for i, line in enumerate(lines):
            if _DISPATCH not in line or not _code(line):
                continue
            j = i
            while j > 0 and not _BLOCK.match(lines[j]):
                j -= 1
            block = [ln for ln in lines[j:i] if _code(ln)]
            if any("scripts/fixer_gate.py" in ln for ln in block) and \
                    any('!= "dispatch"' in ln for ln in block):
                gated += 1
            else:
                bad.append(f"{name}:{i + 1} dispatches pr-repair.yml without a fixer_gate.py "
                           f"decision earlier in the same run block")
    if gated == 0:
        bad.append("no gated `gh workflow run pr-repair.yml` found at all (finding-watch.yml)")
    if _DISPATCH in texts.get("pr-shepherd.yml", ""):
        bad.append("pr-shepherd.yml dispatches pr-repair.yml; it must go through finding-watch.yml")
    repair = texts.get("pr-repair.yml", "")
    if not re.search(r"(?m)^run-name: .*PR repair #\$\{\{ inputs\.pr \}\} head=.*findings=.*attempt=",
                     repair):
        bad.append("pr-repair.yml lost the run-name this gate parses (PR repair #<pr> head= findings= attempt=)")
    if "--self-run-id" not in repair:
        bad.append("pr-repair.yml no longer runs the start-of-run duplicate check (--self-run-id)")
    return bad


def _workflows(root: Path) -> dict[str, str]:
    return {p.name: p.read_text(encoding="utf-8")
            for p in sorted((root / ".github" / "workflows").glob("*.yml"))}


def _title(pr: int, sha: str, findings: str, attempt: int = 0) -> str:
    """Exactly what pr-repair.yml's `run-name` renders."""
    return f"PR repair #{pr} head={sha} findings={findings} attempt={attempt}"


def drill(blind: bool = False) -> int:
    """Prove the gate can say NO. With --blind it must fail (exit 1)."""
    A, B = "a" * 40, "b" * 40
    fp1, fp2 = "0123456789ab", "ba9876543210"

    def run(i: int, pr: int | None, status: str, concl: str | None = None,
            sha: str = A, fp: str = fp1, attempt: int = 0, created: str = "",
            updated: str = "") -> dict:
        title = _title(pr, sha, fp, attempt) if pr is not None else \
            "PR repair (fix conflicts + red CI on a PR branch)"
        return {"id": i, "status": status, "conclusion": concl, "display_title": title,
                "created_at": created, "updated_at": updated}

    cases: list[tuple[str, list[dict], str, str, int, bool]] = [
        # (what, runs, sha, findings, attempt now, want_dispatch) -- negative
        # controls first: a gate that only says "skip" is as broken as one that never does.
        ("no pr-repair runs at all", [], A, fp1, 0, True),
        ("another PR is busy (per-PR, not global)", [run(1, 13, "in_progress")], A, fp1, 0, True),
        ("#123 busy is not #12 busy", [run(1, 123, "queued")], A, fp1, 0, True),
        ("same PR, new head after a run", [run(1, 12, DONE, "success")], B, fp1, 1, True),
        ("same head, a NEW finding", [run(1, 12, DONE, "success")], A, fp2, 1, True),
        ("same key but the run was cancelled", [run(1, 12, DONE, "cancelled")], A, fp1, 1, True),
        ("same key, failure -> re-dispatch allowed", [run(1, 12, DONE, "failure")], A, fp1, 1, True),
        ("same key, attempt labels removed -> another go",
         [run(1, 12, DONE, "success", attempt=2)], A, fp1, 0, True),
        ("same PR queued (the #891 burst)", [run(1, 12, "queued")], A, fp1, 0, False),
        ("same PR in progress", [run(1, 12, "in_progress")], A, fp1, 0, False),
        ("same PR waiting", [run(1, 12, "waiting")], A, fp1, 0, False),
        ("same PR, status GitHub adds later", [run(1, 12, "brand_new_status")], A, fp1, 0, False),
        ("same PR, status missing", [{"id": 1, "display_title": _title(12, A, fp1)}], A, fp1, 0, False),
        ("active run naming no PR: fail closed", [run(1, None, "in_progress")], A, fp1, 0, False),
        ("same key judged, run counted +1 since (#894)",
         [run(1, 12, DONE, "success", attempt=0)], A, fp1, 1, False),
        ("same key judged, no-work run counted nothing",
         [run(1, 12, DONE, "success", attempt=1)], A, fp1, 1, False),
        ("short head sha in the title still matches",
         [{"id": 1, "status": DONE, "conclusion": "success",
           "display_title": _title(12, A[:12], fp1, 0)}], A, fp1, 1, False),
    ]
    bad = 0
    print("fixer_gate.py --drill" + (" --blind (negative control: MUST fail)" if blind else ""))
    for what, runs, sha, fp, att, want in cases:
        got, _ = decide(12, sha, fp, runs, attempt=att, blind=blind)
        ok = got == want
        bad += 0 if ok else 1
        print("  %-4s gate: %-52s %s" % ("ok" if ok else "FAIL", what,
                                         ("dispatch" if got else "skip")
                                         + ("" if ok else f"  WANTED {'dispatch' if want else 'skip'}")))

    # ── the start-of-run duplicate check (pr-repair.yml's first step) ───────
    T0, T1, T2, T3 = "2026-10-19T00:00:00Z", "2026-10-19T00:10:00Z", "2026-10-19T00:20:00Z", \
        "2026-10-19T00:40:00Z"
    me = run(9, 12, "in_progress", sha="unknown", created=T1)
    me["display_title"] = "PR repair #12 head=unknown findings=unknown attempt=unknown"
    dup_cases: list[tuple[str, list[dict], str | None, bool]] = [
        # (what, other runs, my findings, want_duplicate) -- negatives first.
        ("alone", [], None, False),
        ("another PR is active", [run(1, 13, "in_progress", created=T0)], None, False),
        ("a LATER run for my PR is pending", [run(10, 12, "pending", created=T2)], None, False),
        ("my PR's run finished BEFORE I was queued",
         [run(1, 12, DONE, "success", created=T0, updated=T0)], None, False),
        ("it finished after, but on an older head (it pushed)",
         [run(1, 12, DONE, "success", sha=B, created=T0, updated=T2)], None, False),
        ("it finished after, but with other findings",
         [run(1, 12, DONE, "success", fp=fp2, created=T0, updated=T2)], fp1, False),
        ("it finished after, but failed", [run(1, 12, DONE, "failure", created=T0, updated=T2)],
         None, False),
        ("head=unknown run, started before this head",
         [{"id": 1, "status": DONE, "conclusion": "success", "created_at": T0, "updated_at": T3,
           "display_title": "PR repair #12 head=unknown findings=unknown attempt=unknown"}],
         None, False),
        ("an EARLIER run for my PR is still active", [run(1, 12, "in_progress", created=T0)],
         None, True),
        ("it finished after I was queued, same head (the 11/11 pairs)",
         [run(1, 12, DONE, "success", created=T0, updated=T2)], None, True),
        ("same, findings known on both sides and equal",
         [run(1, 12, DONE, "success", created=T0, updated=T2)], fp1, True),
        ("head=unknown run that started on this head",
         [{"id": 1, "status": DONE, "conclusion": "success", "created_at": T2, "updated_at": T3,
           "display_title": "PR repair #12 head=unknown findings=unknown attempt=unknown"}],
         None, True),
    ]
    for what, others, my_fp, want in dup_cases:
        got, _ = duplicate_of(12, 9, A, T1, my_fp, [me, *others], blind=blind)
        ok = got == want
        bad += 0 if ok else 1
        print("  %-4s dup:  %-52s %s" % ("ok" if ok else "FAIL", what,
                                         ("duplicate" if got else "start")
                                         + ("" if ok else f"  WANTED {'duplicate' if want else 'start'}")))

    # fingerprint: order-free, and it moves when a finding is added.
    t1 = {"threads": {"fixable_ids": ["T2", "T1"]}, "checks": {"fixable": ["verify / backend"]}}
    t2 = {"threads": {"fixable_ids": ["T1", "T2"]}, "checks": {"fixable": ["verify / backend"]}}
    t3 = {"threads": {"fixable_ids": ["T1", "T2", "T3"]}, "checks": {"fixable": ["verify / backend"]}}
    fp_ok = bool(fingerprint(t1) == fingerprint(t2) != fingerprint(t3)
                 and fingerprint({}) == "none" and _FP.match(fingerprint(t1)))
    bad += 0 if fp_ok else 1
    print("  %-4s %-58s" % ("ok" if fp_ok else "FAIL", "fingerprint is order-free and moves on a new finding"))

    # ── wiring: the real tree is clean, and a removed gate line goes RED ────
    texts = _workflows(ROOT)
    real = wiring_problems(texts, blind=blind)
    for p in real:
        print(f"  FAIL wiring: {p}")
    bad += 1 if real else 0
    print("  %-4s %-58s" % ("ok" if not real else "FAIL", "wiring: every pr-repair dispatch is gated (real tree)"))
    fw = texts.get("finding-watch.yml", "")
    mutants = {
        "finding-watch without its fixer_gate.py call": {
            **texts, "finding-watch.yml": "\n".join(
                ln for ln in fw.splitlines() if "scripts/fixer_gate.py" not in ln)},
        "pr-shepherd dispatching pr-repair again": {
            **texts, "pr-shepherd.yml": texts.get("pr-shepherd.yml", "") +
            '\n      - run: gh workflow run pr-repair.yml -f pr="$n"\n'},
        "pr-repair without its run-name": {
            **texts, "pr-repair.yml": re.sub(r"(?m)^run-name:.*$", "",
                                             texts.get("pr-repair.yml", ""))},
    }
    for what, mutated in mutants.items():
        caught = bool(wiring_problems(mutated, blind=blind))
        bad += 0 if caught else 1
        print("  %-4s wiring mutant: %-44s %s" % ("ok" if caught else "FAIL", what,
                                                  "caught" if caught else "MISSED"))

    # ── REPLAY OF THE RECORDED BURST ──────────────────────────────────────
    # PR #891, 2026-10-18 23:51Z..00:43Z: the 27 dispatch requests finding-watch
    # made (minutes after the first, head sha the PR had then), with a fixer run
    # lasting 31 minutes as the first one did. The old doorbell dispatched all
    # 27. The gate must dispatch at most one per busy period: 2.
    heads = [(0.0, "64a9a5e"), (15.3, "31a040d"), (36.2, "26d3726"), (42.6, "7f19f36"), (47.2, "7cff030")]
    asks = [0.0, 3.2, 3.5, 4.5, 16.2, 16.2, 17.2, 17.9, 18.3, 18.6, 20.6, 40.0, 41.4, 42.6,
            42.8, 43.0, 44.9, 45.1, 45.4, 46.5, 47.5, 47.8, 49.8, 51.0, 52.2, 52.2, 52.3]
    bad += _replay("PR #891, 27 recorded requests", 891, heads, asks, fp1, 31.0, 2, blind)
    # PR #894, 01:17Z..01:18Z: 3 requests on bcb0a9c; the fixer found nothing at
    # 01:49 and the queued duplicate then ran AGAIN on the same head. With the
    # gate: 1 dispatch, and a later sweep on the same head + findings is refused.
    bad += _replay("PR #894, 3 requests + sweeps after it judged", 894,
                   [(0.0, "bcb0a9c")], [0.0, 0.75, 1.5, 40.0, 70.0], fp1, 33.0, 1, blind)

    total = len(cases) + len(dup_cases) + 2 + len(mutants) + 2
    print(f"\n{total - bad}/{total}")
    return 1 if bad else 0


def _replay(what: str, pr: int, heads: list[tuple[float, str]], asks: list[float],
            fp: str, run_minutes: float, want_max: int, blind: bool) -> int:
    """Each real run adds one attempt label when it starts, as pr-repair does."""
    runs: list[dict] = []
    ends: list[float] = []
    dispatched = attempts = 0
    for t in asks:
        sha = [s for at, s in heads if at <= t][-1].ljust(40, "0")
        for r, end in zip(runs, ends):
            if end <= t:
                r["status"], r["conclusion"] = DONE, "success"
        go, _ = decide(pr, sha, fp, runs, attempt=attempts, blind=blind)
        if go:
            dispatched += 1
            runs.append({"id": len(runs) + 1, "status": "in_progress", "conclusion": None,
                         "display_title": _title(pr, sha, fp, attempts)})
            ends.append(t + run_minutes)
            attempts += 1
    ok = 1 <= dispatched <= want_max
    print("  %-4s replay: %-50s %d dispatch(es) for %d requests%s" % (
        "ok" if ok else "FAIL", what, dispatched, len(asks),
        "" if ok else f"  WANTED 1..{want_max}"))
    return 0 if ok else 1


def _load(path: str | None) -> object:
    return json.loads(Path(path or "").read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--drill", action="store_true", help="prove the gate can say no")
    ap.add_argument("--blind", action="store_true",
                    help="with --drill: switch the refusals off; the drill MUST then fail")
    ap.add_argument("--pr", help="PR number")
    ap.add_argument("--sha", help="gate: the PR's current head sha (40 hex)")
    ap.add_argument("--attempt", default="0", help="gate: autofix:attempt-N count now")
    ap.add_argument("--triage-json", help="gate: output of `scripts/repairable.py --threads-json ...`")
    ap.add_argument("--runs-json", help="JSON array of pr-repair workflow runs "
                    "(id, status, conclusion, display_title, created_at, updated_at)")
    ap.add_argument("--self-run-id", help="duplicate check: this run's id")
    ap.add_argument("--head-sha", help="duplicate check: the PR's head sha now")
    ap.add_argument("--head-date", help="duplicate check: that commit's committer date")
    ap.add_argument("--findings", default="", help="duplicate check: inputs.findings (may be empty)")
    args = ap.parse_args(argv)

    if args.drill:
        return drill(blind=args.blind)

    try:
        if not (args.pr and args.pr.isdigit()):
            raise ValueError(f"--pr must be a number, got {args.pr!r}")
        runs = _load(args.runs_json)
        if not isinstance(runs, list):
            raise ValueError("--runs-json must be a JSON array")
        if args.self_run_id is not None:
            if not args.self_run_id.isdigit():
                raise ValueError("--self-run-id must be a number")
            if not (args.head_sha and _SHA.match(args.head_sha)):
                raise ValueError("--head-sha must be a 40-character hex sha")
            if not re.match(r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ$", args.head_date or ""):
                raise ValueError("--head-date must be an ISO time like 2026-10-19T00:00:00Z")
            fp = args.findings if _FP.match(args.findings or "") else None
            dup, reason = duplicate_of(int(args.pr), int(args.self_run_id), args.head_sha,
                                       args.head_date, fp, runs)
            print(json.dumps({"duplicate": dup, "reason": reason}))
            return 0
        if not (args.sha and _SHA.match(args.sha)):
            raise ValueError("--sha must be a 40-character hex sha")
        if not args.attempt.isdigit():
            raise ValueError("--attempt must be a number")
        triage = _load(args.triage_json)
        if not isinstance(triage, dict):
            raise ValueError("--triage-json must be a JSON object")
    except (OSError, ValueError) as exc:
        print(f"fixer_gate: refusing to decide on bad input: {exc}", file=sys.stderr)
        return 2

    findings = fingerprint(triage)
    go, reason = decide(int(args.pr), args.sha, findings, runs, attempt=int(args.attempt))
    print(json.dumps({"decision": "dispatch" if go else "skip", "reason": reason,
                      "findings": findings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
