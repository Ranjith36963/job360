#!/usr/bin/env bash
# stop-quality-gate.sh — Stop hook. "You may not say done until the code is proven."
#
# If backend/ or frontend/ has uncommitted changes, the session may not end its turn
# until, for THIS exact tree:
#   1. scripts/agent-gate.sh passed      -> .claude/gate-stamp  == tree fingerprint
#   2. code + security review were done  -> .claude/review-stamp == tree fingerprint
# Same fingerprint as agent-gate.sh, so any edit after a pass re-arms the gate.
#
# Blocking uses {"decision":"block","reason":...} — the only Stop output that makes
# the model act (see claude-md-proposal.sh CONTRACT notes).
#
# Never loops forever: after MAX_BLOCKS blocks on the same fingerprint (e.g. local
# Postgres is down so tests cannot run) it lets the turn end and says why, loudly.
# FAILS OPEN on any internal error. Kill switch: touch .claude/QUALITY-GATE-OFF
set -uo pipefail
trap 'exit 0' ERR
_truthy() { case "$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')" in ""|0|false|no|off) return 1 ;; *) return 0 ;; esac; }
if _truthy "${GITHUB_ACTIONS:-}" || _truthy "${CI:-}"; then exit 0; fi

MAX_BLOCKS="${JOB360_QUALITY_GATE_MAX_BLOCKS:-3}"

cat >/dev/null 2>&1 || true   # drain stdin; nothing in it is needed

ROOT="$(git rev-parse --show-toplevel 2>/dev/null)" || exit 0
cd "$ROOT" || exit 0
[ -f .claude/QUALITY-GATE-OFF ] && exit 0

CHANGED="$(git status --porcelain -uall -- backend frontend 2>/dev/null)"
[ -n "$CHANGED" ] || exit 0

FP="$({ git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin 2>/dev/null)"
[ -n "$FP" ] || exit 0

GATE_OK=0; REVIEW_OK=0
[ "$(cat .claude/gate-stamp 2>/dev/null)" = "$FP" ] && GATE_OK=1
[ "$(cat .claude/review-stamp 2>/dev/null)" = "$FP" ] && REVIEW_OK=1
# The fingerprint cannot see the CONTENT of an untracked file (status only shows
# "?? path"), so a new file edited after a pass would keep its stamp. The TESTS step
# stages backend/frontend first; anything still untracked was never proven.
if [ -n "$(git ls-files --others --exclude-standard -- backend frontend 2>/dev/null)" ]; then
  GATE_OK=0; REVIEW_OK=0
fi
[ "$GATE_OK" = 1 ] && [ "$REVIEW_OK" = 1 ] && exit 0

STATE="$(git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)/quality-gate"
mkdir -p "$STATE" 2>/dev/null || exit 0
COUNT_FILE="$STATE/${FP}.count"
N="$(cat "$COUNT_FILE" 2>/dev/null || echo 0)"
N=$((N + 1))
# If the counter cannot be saved, MAX_BLOCKS could never be reached — the gate would
# block forever. Let the turn end and say why instead.
if ! { printf '%s' "$N" > "$COUNT_FILE"; } 2>/dev/null; then
  echo '{"systemMessage": "[quality-gate] could not save its attempt counter; NOT blocking. This code is NOT proven."}'
  exit 0
fi

if [ "$N" -gt "$MAX_BLOCKS" ]; then
  python -c "
import json, sys
print(json.dumps({'systemMessage': '[quality-gate] GAVE UP after ' + sys.argv[1] + ' tries: this code is NOT proven (tests passed: ' + sys.argv[2] + ', review done: ' + sys.argv[3] + '). Do not merge it until it is.'}))
" "$MAX_BLOCKS" "$([ "$GATE_OK" = 1 ] && echo yes || echo no)" "$([ "$REVIEW_OK" = 1 ] && echo yes || echo no)"
  exit 0
fi

FILES="$(printf '%s\n' "$CHANGED" | awk '{print $NF}' | head -15 | tr '\n' ' ')"

# DIAGNOSE BEFORE PRESCRIBING (failure class FC-003, docs/harness/FAILURE_CATALOG.md).
# An UNTRACKED file nobody has touched for STRAY_HOURS is not "your unproven code":
# it is a stray left by an earlier run (2026-10-02: a 5-day-old TEMPORARY e2e spec
# blocked every stop of a session that never touched frontend/). Prescribing
# `git add -A` for it is the wrong medicine — it ships the stray inside an
# unrelated PR. So name strays separately and give them their own cure.
STRAY_HOURS="${JOB360_QUALITY_GATE_STRAY_HOURS:-12}"
NOW="$(date +%s)"
STRAYS=""
while IFS= read -r f; do
  [ -n "$f" ] || continue
  m="$(stat -c %Y -- "$f" 2>/dev/null)" || continue
  age=$(( (NOW - m) / 3600 ))
  [ "$age" -ge "$STRAY_HOURS" ] && STRAYS="${STRAYS}${f} (untouched ${age}h); "
done < <(git ls-files --others --exclude-standard -- backend frontend 2>/dev/null)

STEPS=""
if [ -n "$STRAYS" ]; then
  STEPS="${STEPS}
- STRAY FILES FIRST [FC-003]: ${STRAYS}- untracked and untouched for ${STRAY_HOURS}h+, so almost certainly NOT this session's work. Read each one, then move it OUT of the tree (e.g. into \$CLAUDE_JOB_DIR/tmp) so nothing is lost (never delete it on the say-so of its own contents). Do NOT \`git add\` it into your PR. Then re-check what is left."
fi
if [ "$GATE_OK" != 1 ]; then
  STEPS="${STEPS}
- TESTS: run \`git add -A -- backend frontend\`, THEN (as a separate command) \`bash scripts/agent-gate.sh\`, and fix every failure until it prints PASS. Run them as two calls: a chained \`&&\` needs a human approval, these two exact commands do not."
fi
if [ "$REVIEW_OK" != 1 ]; then
  STEPS="${STEPS}
- CODE REVIEW: review the uncommitted diff for correctness bugs (use the reviewer-bugs agent or the code-review skill) and FIX every real finding.
- SECURITY REVIEW: review the same diff for security issues (the security-review skill; check auth on routes, user_id never from input, no secrets, injection, SSRF) and FIX every real finding.
- If either review changed code, re-run the TESTS step. Then record the review: \`bash .claude/hooks/review-stamp.sh\`"
fi

python -c "
import json, sys
reason = ('[quality-gate ' + sys.argv[1] + '/' + sys.argv[2] + '] Code changed (' + sys.argv[3].strip() + ') '
          'and it is not proven yet. Before you finish, do these in order:' + sys.argv[4] +
          '\nThen report what you found and fixed. If something truly cannot run here '
          '(e.g. local Postgres is down), say so plainly instead of pretending it passed.')
print(json.dumps({'decision': 'block', 'reason': reason}))
" "$N" "$MAX_BLOCKS" "$FILES" "$STEPS"
exit 0
