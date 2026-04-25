---
# Reviewer verdict — v1.
#
# Required: every batch reviewed by the reviewer worktree MUST produce a
# fully-filled copy of this file at the branch root as
# .claude/reviewer-verdict.md.
#
# YAML front-matter is machine-parseable by `make verify-batch`.
#
# scripts/review_batch.sh populates `verification_commands_run` automatically.
# The human (or reviewer agent) fills in `verdict`, `issues`,
# `sentinel_claim_verified`, and `scope_matches_generator_claim`.

verdict: PENDING        # APPROVED | CHANGES_REQUESTED | BLOCKED
sentinel_claim_verified: pending   # yes | no | stale
scope_matches_generator_claim: pending   # yes | no
issues: []
# issues format:
#   - file: backend/src/foo.py
#     line: 42
#     severity: P0    # P0 (block) | P1 (must fix pre-merge) | P2 (follow-up) | P3 (nit)
#     note: short description of the problem and suggested fix

verification_commands_run: []
# verification_commands_run format (auto-populated by review_batch.sh):
#   - command: ruff check
#     exit_code: 0
#   - command: pytest tests/
#     exit_code: 1
---

# Reviewer Verdict — <batch name / SHA>

## Summary

<!-- 2–4 sentences. State the overall judgment and the single most
     important reason for it. -->

## Sentinel verification

<!-- Compare the generator's claimed counts against your re-run.
     Format:
       Generator claim: 1086p / 0f / 4s under --ignore=tests/test_main.py
       Reviewer re-run: 1086p / 0f / 4s under --ignore=tests/test_main.py
       Verdict: matches → sentinel_claim_verified: yes
                differs → sentinel_claim_verified: stale (BLOCK)
                scope mismatch → scope_matches_generator_claim: no (BLOCK)
-->

## Issues found

<!-- Prose expansion of the YAML `issues` list above. Group by severity.
     For each P0/P1, link to the file:line and explain the impact + the
     fix you suggest. -->

## Decision rationale

<!-- Why APPROVED / CHANGES_REQUESTED / BLOCKED. Tie back to the
     verification_commands_run results — if pytest exited non-zero,
     the verdict cannot be APPROVED. -->
