#!/usr/bin/env bash
# harness_selftest.sh — tests for the harness's OWN load-bearing shell logic.
#
# The house failure mode is a guard that cannot fire. Six shipped this week.
# Everything under .github/workflows/ is shell that nothing executes outside a
# GitHub runner, so a typo in it is invisible until the loop silently stops —
# which is exactly what happened to triage's auto-authorisation (dead 15 days,
# nobody noticed) and to the blind checker's diff capture (dies on any PR over
# 800 diff lines, on Linux, silently).
#
# So the load-bearing predicates now live in scripts/ as real programs, and
# this file runs them. Run it locally:  bash scripts/harness_selftest.sh
# CI runs it on every PR (ci.yml, job `harness`).
#
# It needs only bash + git. No network, no Postgres, no npm.
set -uo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TRUST="$ROOT/scripts/triage_trusted_origin.sh"
CAPTURE="$ROOT/scripts/harness_capture_diff.sh"

PASS=0
FAIL=0

ok() {
  PASS=$((PASS + 1))
  printf '  ok   %s\n' "$1"
}

bad() {
  FAIL=$((FAIL + 1))
  printf '  FAIL %s\n' "$1"
  [ "$#" -gt 1 ] && printf '       %s\n' "$2"
  return 0
}

# ── WIRE 1: trusted-origin auto-authorisation ────────────────────────────────
# `gh issue view --json author` returns "app/github-actions"; the webhook
# payload spells the SAME bot "github-actions[bot]". Both must be accepted, or
# triage can never hand a detector's own issue to the repair loop.

echo "WIRE 1 — triage trusted-origin predicate"

trusts() { bash "$TRUST" "$1" "$2"; }

assert_trusted() {
  if trusts "$1" "$2"; then ok "trusts   author=$1  title=${2%% *}…"
  else bad "trusts   author=$1  title=${2%% *}…" "expected exit 0, got $?"; fi
}

assert_untrusted() {
  if trusts "$1" "$2"; then bad "rejects  author=$1  title=${2%% *}…" "expected exit 1, got 0 — this would AUTHORISE a write-capable agent"
  else ok "rejects  author=$1  title=${2%% *}…"; fi
}

INV="INVARIANT: paid_verdicts_are_visible violated in production"

# Both spellings of our own bot. The second one is the whole bug: it is what
# `gh issue view --json author --jq .author.login` actually returns.
assert_trusted "github-actions[bot]" "$INV"
assert_trusted "app/github-actions" "$INV"

# Every whitelisted detector prefix, in the API spelling.
assert_trusted "app/github-actions" "PRODUCT: something is wrong (not broken - wrong)"
assert_trusted "app/github-actions" "ABSENCE: something in the pipeline has stopped"
assert_trusted "app/github-actions" "WATCHDOG: a scheduled workflow has stopped running"

# A stranger — this repo is PUBLIC, anyone can open an issue.
assert_untrusted "randomperson" "$INV"

# Near-miss logins. Loosening the match to two literals must NOT become a
# substring match: "github-actions" is a name anyone can register around.
assert_untrusted "app/github-actions-helper" "$INV"
assert_untrusted "notgithub-actions[bot]" "$INV"
assert_untrusted "github-actions" "$INV"
assert_untrusted "xapp/github-actions" "$INV"
assert_untrusted "app/github-actions " "$INV"

# Right bot, WRONG title: the body is third-party text, so it must not
# auto-authorise even though our own bot opened it.
assert_untrusted "app/github-actions" "Loop 2 RED: nightly live-e2e is failing"
assert_untrusted "app/github-actions" "Loop RED: security scanning is failing"
assert_untrusted "app/github-actions" "Doc drift detected (Loop 3 doc-sync)"
# Prefix must be anchored at the start, and include the space+colon shape.
assert_untrusted "app/github-actions" "re: PRODUCT: something is wrong"
assert_untrusted "app/github-actions" "INVARIANTS are broken"

# The predicate must be REACHABLE from triage.yml — otherwise this whole file
# tests a script nothing calls, which is the failure mode it exists to end.
if grep -q 'triage_trusted_origin.sh' "$ROOT/.github/workflows/triage.yml"; then
  ok "triage.yml actually calls triage_trusted_origin.sh"
else
  bad "triage.yml actually calls triage_trusted_origin.sh" "the predicate is orphaned — triage would use its own inline copy again"
fi

# ...and it must run the COMMITTED copy. The triage agent holds Edit/Write with
# no path cage and runs BEFORE the route step in the same workspace, so reading
# the predicate off disk would let it rewrite its own authorisation check.
if grep -q 'git show "HEAD:scripts/triage_trusted_origin.sh"' "$ROOT/.github/workflows/triage.yml"; then
  ok "triage.yml runs the COMMITTED predicate, not the working-tree copy"
else
  bad "triage.yml runs the COMMITTED predicate, not the working-tree copy" "a prompt-injected triage agent could edit the file and authorise itself"
fi

# ── WIRE 2: the blind checker's diff capture ─────────────────────────────────
# `git diff … | head -800` under `set -euo pipefail`: on Linux git takes
# SIGPIPE and exits 141, pipefail promotes it, set -e kills the step. Measured
# 2026-08-15 in alpine/git 2.54.0 — rc=141, and the INVARIANTS section that
# follows it in the workflow never got written. It does NOT reproduce on
# Windows git-bash (that build exits 0 on EPIPE), which is why it hid.

echo
echo "WIRE 2 — blind-checker diff capture"

repo="$(mktemp -d)"
(
  cd "$repo" || exit 1
  git init -q .
  git config user.email t@t.t
  git config user.name t
  mkdir -p backend
  : > backend/big.txt
  git add -A
  git commit -qm base
  git branch -M main
  git checkout -qb work
  # ~600 KB: far past the 64 KB pipe buffer. A SMALL diff fits the buffer and
  # survives even the broken form, so a small fixture would prove nothing.
  pad="$(printf 'x%.0s' $(seq 1 190))"
  i=0
  while [ "$i" -lt 3000 ]; do
    echo "line $i $pad"
    i=$((i + 1))
  done > backend/big.txt
  git add -A
  git commit -qm big
) > /dev/null 2>&1

# The exact shape repair.yml uses: a redirected group under `set -euo pipefail`,
# with content AFTER the diff that must still get written.
run_capture_block() {
  (
    cd "$repo" || exit 1
    set -euo pipefail
    {
      echo "# THE DIFF under review"
      bash "$CAPTURE" 800 main...HEAD -- backend/
      echo "# INVARIANTS"
      echo "MARKER_REACHED_END"
    } > out.md
  )
}

run_capture_block
rc=$?
if [ "$rc" -eq 0 ]; then ok "big diff (>800 lines) does not kill the step (rc=0)"
else bad "big diff (>800 lines) does not kill the step" "rc=$rc — the blind checker is dead again on large PRs"; fi

if grep -q MARKER_REACHED_END "$repo/out.md" 2>/dev/null; then
  ok "the step reaches the INVARIANTS section after a big diff"
else
  bad "the step reaches the INVARIANTS section after a big diff" "truncation aborted the block — the checker would judge against an EMPTY invariant list"
fi

lines=$(wc -l < "$repo/out.md" 2>/dev/null | tr -d '[:space:]')
if [ "${lines:-0}" -gt 800 ] && [ "${lines:-0}" -lt 900 ]; then
  ok "output is bounded (~800 lines, got $lines)"
else
  bad "output is bounded (~800 lines)" "got $lines"
fi

if grep -q 'diff truncated' "$repo/out.md" 2>/dev/null; then
  ok "truncation is DECLARED, not silent"
else
  bad "truncation is DECLARED, not silent" "the checker cannot tell it is judging a fragment"
fi

# A small diff must pass through whole, with no false truncation notice.
(
  cd "$repo" || exit 1
  git checkout -q main
  git checkout -qb small
  printf 'one\ntwo\n' > backend/small.txt
  git add -A
  git commit -qm small
) > /dev/null 2>&1
small_out="$(cd "$repo" && bash "$CAPTURE" 800 main...small -- backend/ 2>&1)"
if echo "$small_out" | grep -q 'diff truncated'; then
  bad "small diff is NOT marked truncated" "false truncation notice"
else
  ok "small diff is NOT marked truncated"
fi
if echo "$small_out" | grep -q '+two'; then
  ok "small diff passes through whole"
else
  bad "small diff passes through whole" "content missing"
fi

# A REAL git failure must still be loud. `| head -800 || true` would have
# swallowed it and handed the checker an empty diff to approve.
if (cd "$repo" && bash "$CAPTURE" 800 no-such-ref...HEAD -- backend/ > /dev/null 2>&1); then
  bad "a broken git ref still fails loudly" "exited 0 on a bad ref — the checker would review an EMPTY diff and approve"
else
  ok "a broken git ref still fails loudly"
fi

# Regression guard: nobody may reintroduce the fatal pipe in a workflow.
# The second grep drops COMMENT lines (`file:503:    # NOT git diff | head…`),
# so the workflows may keep explaining the bug without tripping their own guard.
offenders="$(grep -rn 'git diff.*| *head' "$ROOT/.github/workflows/" 2>/dev/null |
  grep -vE ':[0-9]+:[[:space:]]*#' || true)"
if [ -z "$offenders" ]; then
  ok "no workflow pipes 'git diff' into 'head'"
else
  bad "no workflow pipes 'git diff' into 'head'" "$offenders"
fi

# ...and the safe helper is actually the one being called. Without this, the
# guard above passes just as happily if someone deletes the diff capture
# entirely and the checker silently reviews nothing.
for wf in repair.yml pr-repair.yml; do
  if grep -q 'harness_capture_diff.sh' "$ROOT/.github/workflows/$wf"; then
    ok "$wf captures its diff through harness_capture_diff.sh"
  else
    bad "$wf captures its diff through harness_capture_diff.sh" "the blind checker has no diff to review"
  fi
done

# PROOF (informational, not an assertion): show the old form's behaviour HERE.
# It is 141 on Linux and 0 on Windows git-bash, so asserting it would be a
# flaky guard on one platform — but printing it keeps the evidence visible.
(
  cd "$repo" || exit 1
  git checkout -q work
  set -euo pipefail
  git diff main...HEAD -- backend/ | head -800 > /dev/null
) > /dev/null 2>&1
echo "  note old form 'git diff | head -800' exits $? on this platform (141 = the bug; 0 = platform hides it)"

rm -rf "$repo"

# ── WIRE 3: the watchdog roster must match the workflows on disk ─────────────
# scripts/watchdog_check.py holds a HAND-MAINTAINED list of which workflows are
# supposed to run on a schedule. Retire a workflow, or take its `schedule:`
# away, and the watchdog keeps waiting for a run that will never come — then
# raises "WATCHDOG: a scheduled workflow has stopped running" forever.
#
# That is not merely noise any more. `WATCHDOG: ` is one of the four titles
# that auto-authorise `agent:fix` (scripts/triage_trusted_origin.sh), so a
# stale roster now hands a write-capable agent a bug that does not exist. Its
# own roster_drift() catches this — but nothing ran it outside a scheduled
# GitHub job, so it could not fail early. It runs here, on every PR.

echo
echo "WIRE 3 — watchdog roster vs. workflows on disk"

PY=""
for cand in python3 python; do
  if command -v "$cand" > /dev/null 2>&1; then
    PY="$cand"
    break
  fi
done

if [ -z "$PY" ]; then
  bad "watchdog roster matches the workflows on disk" "no python interpreter found — roster drift is UNVERIFIED"
else
  drift="$(cd "$ROOT" && "$PY" -c '
import sys
sys.path.insert(0, "scripts")
from watchdog_check import roster_drift
for line in roster_drift():
    print(line)
' 2>&1)"
  if [ -z "$drift" ]; then
    ok "watchdog roster matches the workflows on disk"
  else
    bad "watchdog roster matches the workflows on disk" "$drift"
  fi
fi

echo
echo "harness selftest: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ]
