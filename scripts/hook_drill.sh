#!/usr/bin/env bash
# hook_drill.sh — make the local Stop gate and verify-stamp.sh go RED on purpose.
#
#   bash scripts/hook_drill.sh           every case must behave; exit 0 = PASS
#   bash scripts/hook_drill.sh --blind   NEGATIVE CONTROL: drills a stub gate that
#                                        allows everything; MUST exit non-zero, or
#                                        the drill cannot see a broken gate.
#
# Each case runs in a throwaway git repo, never in this checkout. CI runs it in the
# `Chain wires (harness)` job (ci.yml) and drill_registry.py runs it with --blind as its
# negative control, so the hooks cannot change without someone watching this go red.
set -uo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
GATE="$HERE/.claude/hooks/stop-quality-gate.sh"
VSTAMP="$HERE/.claude/hooks/verify-stamp.sh"
RSTAMP="$HERE/.claude/hooks/review-stamp.sh"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
if [ "${1:-}" = "--blind" ]; then
  printf '#!/usr/bin/env bash\nexit 0\n' > "$WORK/gate.sh"
  printf '#!/usr/bin/env bash\nexit 0\n' > "$WORK/vstamp.sh"
  GATE="$WORK/gate.sh"; VSTAMP="$WORK/vstamp.sh"
fi
unset CI GITHUB_ACTIONS
FAILS=0
_ok()  { echo "  ok   $1"; }
_bad() { echo "  FAIL $1"; FAILS=$((FAILS + 1)); }

_repo() {  # fresh repo with one committed app file and one doc under backend/
  R="$WORK/r$1"; mkdir -p "$R/backend/src" "$R/frontend" "$R/.claude"; cd "$R" || exit 1
  git init -q
  echo 'x = 1' > backend/src/app.py; echo 'doc' > backend/README.md; echo '{}' > frontend/package.json
  grep -E '^\.claude/(gate|review|verify)-stamp$' "$HERE/.gitignore" > .gitignore  # as in the real repo
  git add -A; git -c user.email=d@d -c user.name=d commit -q -m init
}
_fp() { { git rev-parse HEAD; git status --porcelain; git diff; git diff --cached; } | git hash-object --stdin; }
_gate_stamp() { git add -A -- backend frontend; _fp > .claude/gate-stamp; }
_stop() { printf '{"session_id":"drill","stop_hook_active":%s}' "${1:-false}" | bash "$GATE" 2>&1; }
_evidence() { head -c 400 /dev/zero | tr '\0' 'e' > "$WORK/ev$1.txt"; echo "$WORK/ev$1.txt"; }

echo "[hook-drill] (a) app-code diff, gate+review stamps, no verify stamp -> block"
_repo a; echo 'x = 2' > backend/src/app.py; _gate_stamp; bash "$RSTAMP" >/dev/null
case "$(_stop)" in *'"decision": "block"'*verify-stamp.sh*) _ok "blocked, names verify-stamp.sh" ;; *) _bad "did not block" ;; esac

echo "[hook-drill] (b) app-code diff with all three stamps -> allow"
_repo b; echo 'x = 3' > backend/src/app.py; sleep 1; EV="$(_evidence b)"
_gate_stamp; bash "$VSTAMP" "$EV" >/dev/null 2>&1; bash "$RSTAMP" >/dev/null
OUT="$(_stop)"; [ -z "$OUT" ] && _ok "allowed silently" || _bad "blocked with all stamps: $OUT"

echo "[hook-drill] (c) docs-only diff under backend/ -> gate + review stamps, no verify stamp needed"
_repo c; echo 'more doc' >> backend/README.md
case "$(_stop)" in *'"decision": "block"'*) _ok "no gate stamp: blocked" ;; *) _bad "no gate stamp: allowed" ;; esac
_gate_stamp
case "$(_stop)" in *'"decision": "block"'*) _ok "gate stamp only: still blocked (no review)" ;; *) _bad "gate stamp only: allowed without a review" ;; esac
bash "$RSTAMP" >/dev/null
OUT="$(_stop)"; [ -z "$OUT" ] && _ok "gate + review stamps: allowed (no verify stamp needed)" || _bad "gate + review stamps: blocked: $OUT"

echo "[hook-drill] (d) 8 consecutive blocks, the 9th allows with NOT proven"
_repo d; echo 'x = 4' > backend/src/app.py
BLOCKS=0; _stop false | grep -q '"decision": "block"' && BLOCKS=1
for _ in 2 3 4 5 6 7 8; do _stop true | grep -q '"decision": "block"' && BLOCKS=$((BLOCKS + 1)); done
NINTH="$(_stop true)"
[ "$BLOCKS" = 8 ] && _ok "blocks 1-8 blocked" || _bad "only $BLOCKS of 8 blocked"
case "$NINTH" in *'NOT proven'*) case "$NINTH" in *'"decision"'*) _bad "9th still blocks" ;; *) _ok "9th allows, says NOT proven" ;; esac ;; *) _bad "9th: $NINTH" ;; esac
case "$(_stop false)" in *'"decision": "block"'*) _ok "a new turn starts a new chain" ;; *) _bad "new turn not blocked" ;; esac

echo "[hook-drill] (e) verify-stamp.sh refuses thin or stale evidence"
_repo e; printf 'too small' > "$WORK/small.txt"; sleep 1; echo 'x = 5' > backend/src/app.py
bash "$VSTAMP" "$WORK/small.txt" >/dev/null 2>&1 && _bad "accepted a 9-byte file" || _ok "refused a 9-byte file"
EV="$(_evidence e)"; touch -d '2 hours ago' "$EV"
bash "$VSTAMP" "$EV" >/dev/null 2>&1 && _bad "accepted stale evidence" || _ok "refused evidence older than the code"
[ -f .claude/verify-stamp ] && _bad "a refusal still wrote the stamp" || _ok "no stamp after refusals"
bash "$VSTAMP" "$WORK/missing.txt" >/dev/null 2>&1 && _bad "accepted a missing file" || _ok "refused a missing file"
sleep 1; touch "$EV"; bash "$VSTAMP" "$EV" >/dev/null 2>&1 && [ "$(cat .claude/verify-stamp)" = "$(_fp)" ] \
  && _ok "fresh 400-byte evidence: stamp matches the tree" || _bad "fresh evidence refused or stamp wrong"

if [ "$FAILS" -eq 0 ]; then echo "[hook-drill] PASS"; exit 0; fi
echo "[hook-drill] FAIL: $FAILS case(s)"; exit 1
