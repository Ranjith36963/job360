#!/usr/bin/env bash
#
# review_batch.sh <branch>
#
# Step-1.6 generator/reviewer contract — wrapper that runs the mandatory
# verification gate for a batch and writes results into the reviewer's
# .claude/reviewer-verdict.md (verification_commands_run YAML field).
#
# Mandatory steps (each fails loud, but the script continues so every
# step is captured before exiting non-zero):
#   1. ruff check          (backend lint)
#   2. mypy --strict       (backend type-check)
#   3. pytest full tree    (no --ignore unless generator declared scope)
#   4. npm run build       (frontend)
#
# Pre-step setup:
#   - Fresh tmp dir (TMPDIR overridden) cleared before pytest
#   - JOB360_DB=:memory: forces in-memory SQLite for any test that
#     respects the env var
#
# Idempotent: every run replaces the verification_commands_run YAML
# block; never appends. Safe to re-run from any worktree.
#
# Usage:
#   bash scripts/review_batch.sh <branch>
#
# Example:
#   bash scripts/review_batch.sh step-2-batch
#
set -u -o pipefail

BRANCH="${1:-}"
if [ -z "$BRANCH" ]; then
  echo "ERROR: branch arg required. Usage: bash scripts/review_batch.sh <branch>" >&2
  exit 2
fi

# Resolve project root from any worktree.
PROJECT_ROOT="$(git rev-parse --show-toplevel)"
cd "$PROJECT_ROOT"

VERDICT_PATH="$PROJECT_ROOT/.claude/reviewer-verdict.md"
TEMPLATE_PATH="$PROJECT_ROOT/.claude/templates/v1/reviewer-verdict.md"

# Bootstrap: create reviewer-verdict.md from template if missing.
if [ ! -f "$VERDICT_PATH" ]; then
  if [ ! -f "$TEMPLATE_PATH" ]; then
    echo "ERROR: template missing at $TEMPLATE_PATH" >&2
    exit 2
  fi
  mkdir -p "$(dirname "$VERDICT_PATH")"
  cp "$TEMPLATE_PATH" "$VERDICT_PATH"
  echo "==> bootstrapped $VERDICT_PATH from template v1"
fi

# Fresh tmp + memory DB for hermetic pytest.
SCRATCH_DIR="$(mktemp -d -t job360-review-XXXXXX)"
trap 'rm -rf "$SCRATCH_DIR"' EXIT
export TMPDIR="$SCRATCH_DIR"
export JOB360_DB=":memory:"
export ARQ_TEST_MODE="1"

echo "==> reviewing branch: $BRANCH"
echo "==> project root:     $PROJECT_ROOT"
echo "==> tmp scratch:      $SCRATCH_DIR"
echo "==> verdict file:     $VERDICT_PATH"
echo

# Capture each step's exit code without short-circuiting on failure.
declare -a CMDS
declare -a CODES
ANY_FAILED=0

run_step() {
  local label="$1"
  local cmd="$2"
  echo "==> [$label] $cmd"
  set +e
  bash -c "$cmd"
  local rc=$?
  set -e
  CMDS+=("$label")
  CODES+=("$rc")
  if [ "$rc" -ne 0 ]; then
    ANY_FAILED=1
    echo "    FAILED with exit $rc"
  fi
  echo
}

run_step "ruff check"      "cd backend && python -m ruff check src tests"
run_step "mypy --strict"   "cd backend && python -m mypy --strict src 2>&1 || true; cd backend && python -m mypy --strict src"
run_step "pytest full"     "cd backend && python -m pytest tests/ -q -p no:randomly --tb=short"
run_step "npm run build"   "cd frontend && npm run build"

# Auto-populate verification_commands_run via Python (atomic rewrite).
PYTHON_CMD="$(command -v python3 || command -v python)"
if [ -z "$PYTHON_CMD" ]; then
  echo "ERROR: neither python3 nor python on PATH — cannot rewrite verdict file" >&2
  exit 2
fi
"$PYTHON_CMD" - "$VERDICT_PATH" "${CMDS[@]}" "::" "${CODES[@]}" <<'PY'
import sys, pathlib, re

verdict_path = pathlib.Path(sys.argv[1])
rest = sys.argv[2:]
sep = rest.index("::")
cmds = rest[:sep]
codes = rest[sep + 1:]
assert len(cmds) == len(codes), "command/code length mismatch"

text = verdict_path.read_text(encoding="utf-8")

# Build replacement YAML block.
lines = ["verification_commands_run:"]
if not cmds:
    lines = ["verification_commands_run: []"]
else:
    for c, rc in zip(cmds, codes):
        lines.append(f"  - command: {c}")
        lines.append(f"    exit_code: {int(rc)}")
new_block = "\n".join(lines)

# Replace the verification_commands_run block (from the field through the
# next top-level YAML key OR the closing '---').
pattern = re.compile(
    r"^verification_commands_run:.*?(?=^[a-zA-Z_][a-zA-Z0-9_]*:|^---\s*$)",
    re.MULTILINE | re.DOTALL,
)
replacement = new_block + "\n"
if pattern.search(text):
    text = pattern.sub(replacement, text, count=1)
else:
    # Field absent — inject before closing '---'.
    text = re.sub(r"^---\s*$", new_block + "\n---", text, count=1, flags=re.MULTILINE)

tmp = verdict_path.with_suffix(verdict_path.suffix + ".tmp")
tmp.write_text(text, encoding="utf-8")
tmp.replace(verdict_path)
print(f"==> wrote {len(cmds)} step(s) into {verdict_path.name}")
PY

echo
if [ "$ANY_FAILED" -eq 1 ]; then
  echo "==> FAIL: at least one verification step exited non-zero. Reviewer cannot mark APPROVED until each is fixed or explicitly waived."
  exit 1
fi
echo "==> PASS: all verification steps exited 0. Reviewer may now fill in verdict + issues + sentinel comparison."
exit 0
