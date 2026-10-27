#!/usr/bin/env bash
# verify-stamp.sh <evidence-file> — records "the change was RUN like a user would"
# (Anthropic's /verify or /run: browser screenshot, real HTTP call + DB row, migration
# on a scratch DB, one real MCP tool call) for the CURRENT tree.
#
# It refuses unless the evidence file exists, holds at least 200 bytes, and is NEWER
# than every changed file under backend/ and frontend/: evidence written before the
# last edit proves nothing about the code as it is now.
#
# Honest limit: this is friction plus a visible record, not proof. It cannot tell
# real output from a made-up file. CI is the referee.
# Same fingerprint as scripts/agent-gate.sh and review-stamp.sh; any later edit
# invalidates the stamp.
set -euo pipefail
EV="${1:-}"
MIN_BYTES=200
_refuse() { echo "[verify-stamp] REFUSED: $1" >&2; exit 1; }
[ -n "$EV" ] || _refuse "usage: bash .claude/hooks/verify-stamp.sh <evidence-file> (the saved output or screenshot path from /verify or /run)"
[ -f "$EV" ] || _refuse "evidence file '$EV' does not exist"
SIZE="$(wc -c < "$EV" | tr -d ' ')"
[ "$SIZE" -ge "$MIN_BYTES" ] || _refuse "evidence file '$EV' is $SIZE bytes; at least $MIN_BYTES are needed (paste the real output, not a summary)"
EV_ABS="$(cd "$(dirname "$EV")" && pwd)/$(basename "$EV")"

ROOT="$(git rev-parse --show-toplevel)"
cd "$ROOT"
while IFS= read -r -d '' f; do
  [ -f "$f" ] || continue                   # deleted files have no mtime
  [ "$ROOT/$f" = "$EV_ABS" ] && _refuse "evidence file '$EV' is inside backend/ or frontend/; save it elsewhere"
  [ "$EV_ABS" -nt "$f" ] || _refuse "evidence file '$EV' is not newer than $f; re-run the check on the current code and save fresh output"
done < <(git diff -z --name-only HEAD -- backend frontend
         git ls-files -z --others --exclude-standard -- backend frontend)

git add -A -- backend frontend
FP="$({ git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin)"
mkdir -p .claude
printf '%s' "$FP" > .claude/verify-stamp
echo "[verify-stamp] recorded for tree ${FP:0:12} (evidence: $EV, $SIZE bytes)"
