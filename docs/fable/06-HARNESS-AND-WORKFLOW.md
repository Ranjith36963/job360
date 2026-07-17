# 06 — Claude Code Harness & Workflow

> Source: harness sweep (Opus). How you *run* Claude Code — skills, hooks, settings,
> loops, CLAUDE.md, memory. **Headline:** your architecture is strong (gate-stamp is
> better than most pro setups; memory + model-economy discipline are real). The risk is
> concentrated in the **permission allowlist** and **two stale skills** — not the design.

## Per-skill verdict

| Skill | Verdict | One concrete fix |
|---|---|---|
| **verify-job360** | **Excellent — your crown jewel.** 15+ hard-won gotchas, evidence discipline, "the screenshot is the proof." | Wire it into a real gate (gap P3), not only a soft Stop reminder. |
| **worker** | Good but **dormant** (loop-era; ralph disabled). Reads as live. | Add a banner: "LOOP DISABLED 2026-06-21 — runs only when the owner explicitly starts a worker." |
| **integrator** | Good but dormant + very long (52 dense lines). | Same banner; trim overnight-throttle prose. |
| **scout** | Good — tight, genuinely read-only, evidence-required. | Fine; add the dormancy note. |
| **health** | Good — clear GREEN/AMBER/RED thresholds, cron-wired. | **Verify the cron still runs post-loop-disable** — a health check that silently stopped is worse than none. |
| **debug** | **BROKEN / stale.** Every import points at the pre-refactor tree (`src.profile.storage`, `src.filters.skill_matcher`, `src.notifications.slack_notify`) — all gone. Every target crashes on first run. | Rewrite the 4 snippets to `src.services.*` / `src.repositories.*`, or delete it. Right now it looks authoritative and fails — worse than nothing. |
| **implement** | Weak / redundant — no Job360 specifics; overlaps global superpowers skills. | Delete it, or inject teeth: link the hard-rules index + the five-surface source rule (#8) + the canonical test command. |
| **sync** | OK — genuinely Job360-specific, useful (docs drift heavily here). | Add `frontend/`+`backend/` CLAUDE.md counts + the rule-count (28) to the scan. |
| **commit** (global) | Good but two drifts: Co-Authored-By line doesn't match your global `Claude Fable 5` + `Claude-Session:` rule; trufflehog uses old v2 syntax and silently SKIPs if not installed. | Fix the trailer; update to `trufflehog git file://.`; make a missing scanner a **blocking WARN**, not a silent skip. |

---

## P1 — `git push` / `git merge` / `git:*` are auto-approved (unsupervised push is possible)
> **STATUS: FIXED** — `9fdef61`. `settings.json` permits read-only git only; no push/merge/`git:*`.
- **What I saw:** `settings.json:6-7` allow-lists `Bash(git push:*)` and `Bash(git merge:*)` with no prompt. `settings.local.json:110` adds `Bash(git:*)` — which greenlights *every* git subcommand unsupervised: `reset --hard`, `clean -fdx`, `checkout`, `rebase`, `push`. The deny list only blocks force-push. The worker/integrator skills *say* "never push," but that's a soft instruction, not a guard.
- **Why it matters:** this is **the biggest unsupervised-write vector now that ralph-loop is off** — the same class of failure (committing to main / wiping trees unsupervised) that got the loop disabled. A future automated session, or a prompt-injected instruction, could push or hard-reset with zero human check.
- **Fix (P1 — highest value-per-effort in the whole harness):** remove `Bash(git:*)`, `Bash(git push:*)`, `Bash(git merge:*)` from the allowlist. Require a prompt for `push`/`merge`/`reset`/`clean`. Keep read-only git (`status`/`log`/`diff`) allowed.

## P2 — No secret-scan on the actual commit path
> **STATUS: FIXED** — `6abce80`. gitleaks wired into `.pre-commit-config.yaml`.
- **What I saw:** `.pre-commit-config.yaml` has ruff + whitespace + large-files but **no gitleaks/trufflehog/detect-secrets**. Secret scanning lives only in the manual `/commit` skill, and only if the tools happen to be installed (silently SKIPs otherwise). A key pasted into a `.py` or `.md` sails through a plain `git commit`.
- **Fix:** add `gitleaks` to `.pre-commit-config.yaml` (one line), and/or a `PreToolUse` secret-scan hook mirroring `commit-gate.sh`.

## P3 — The gate is enforced, but verification is not
> **STATUS: OPEN (accepted)** — tooling polish, not a product defect.
- **What I saw:** `commit-gate.sh` is genuinely clever — blocks `git commit` unless a fresh `agent-gate.sh` stamp matches the exact tree, so "test → sneak an edit → commit" is impossible. But `agent-gate.sh` runs pytest + api-types drift only — **not** verify-job360. The Stop reminder to run verify is non-blocking. So your strongest asset is the one thing nothing enforces.
- **Fix:** fold a lightweight verify assertion into the gate, or make the reminder a **blocking** Stop hook keyed to backend/frontend `src` diffs (it already computes that diff).

## P4 — `commit-gate.sh` bypass surfaces
> **STATUS: OPEN (accepted)** — tooling polish.
- **What I saw:** it matches only the literal `git commit` on the Bash tool. A commit via `gh`, an MCP git server, or any non-Bash path isn't intercepted. It shells `python -c` and **exits 0 (allows) on parse failure** if `python` isn't on the hook's PATH.
- **Fix:** fail-closed on parse error; add `gh`/MCP paths to the matcher or document them as uncovered.

## P5 — `settings.local.json` is a 191-line junk drawer
> **STATUS: OPEN (accepted)** — tooling polish.
- **What I saw:** accreted one-off allows — `taskkill`, `rm -rf data/{logs,exports,reports}/*`, `mv jobs.db*`, dead `mcp__chrome-devtools__*` (not in your current servers), disabled-loop generator paths. Low risk, but nobody can eyeball what's actually permitted.
- **Fix:** run the global **`fewer-permission-prompts`** skill to regenerate a clean minimal allowlist; delete the dead MCP + generator entries.

## P6 — CLAUDE.md is ~500 lines (best practice ≈ 200)
> **STATUS: FIXED (conservatively)** — `966d394`. Pure history removed (commit hashes, benchmark numbers, source-count play-by-play, backlog notes); ALL 28 hard rules and every load-bearing invariant kept verbatim. Deliberately conservative: silently losing a rule costs far more than the tokens saved.
- **What I saw:** most bulk is historical narrative that duplicates `docs/IMPLEMENTATION_LOG.md`. Rule-count drift: header says "28", worker skill references "27". A 500-line context tax on every session.
- **Fix:** move phase/batch history to the log; keep the Hard-Rules index + Quick Orientation + Commands; reconcile 27/28.

---

## What Anthropic-internal-grade would add (pragmatic, no fragmentation)
Ranked by value-per-effort. All are **consolidation and enforcement of what you already have** — not new machinery to babysit:

1. **`.claude/agents/` reviewer definitions (currently absent).** Your skills already dispatch "Sonnet subagents, lens R1 conventions / R2 history / R3 bugs" — but as inline prose re-specified every time. Codify them once as 2-3 agent files (model pinned per your economy). Biggest gap vs a top setup, and it *reduces* fragmentation.
2. **Secret-scan pre-commit hook** (gitleaks) — closes P2 permanently, one line.
3. **Tighten the git permission surface** (P1) — highest-risk, lowest-effort.
4. **Fix or delete the `debug` skill** — a broken skill in the roster is a landmine.
5. **Wire verify into the gate** (P3) — you built the best verify skill I've seen in a repo; enforce it.
6. **Skip the "more tools" trap.** You already have code-review, security-review, coderabbit, superpowers, codex globally. A solo founder does NOT need to wire them all in — that's fragmentation. The wins above are consolidation, not new systems.

## What's genuinely strong (keep)
- Memory hygiene (rich linked `MEMORY.md`, used well).
- The **gate-stamp mechanism** — better than most professional setups.
- Model-economy discipline actually reflected in the skills (every dispatch names a model).
- ralph-loop properly contained (denied in settings + kill-switch removed).

**Verdict:** The architecture is sound. Fix the git allowlist (P1) and the broken `debug` skill this week; batch the rest. The theme: **enforce and consolidate what you already built — don't add more.**
