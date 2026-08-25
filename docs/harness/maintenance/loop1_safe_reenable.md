# Loop 1 (code-repair) — safe re-enable design
<!-- doc: LOG -->

> **Status: OFF, on purpose.** Loop 1 (the ralph-loop `worker`/`integrator` code-repair agents) is disabled — `deny: Skill(ralph-loop:ralph-loop)` in settings + kill-switch file absent. It stays off until the guardrails below are in place **and** the first runs are watched. This doc is the checklist to bring it back safely, not a green light.

---

## What went wrong (the misfire)

Loop 1 = **code-repair** (Loop 2 = verify, which is safe and already automated in CI). The `worker` (writes code) + `integrator` (merges) agents ran **unsupervised and wrote straight to `main`**, and **wiped worktrees / branches / the test DB**. Root cause in one line:

> **A writing + merging agent had no cage and no human gate.**

The read-only agents (`scout`, `health`) were never the problem — reading can't break anything. The danger was only the **write/merge** half.

---

## The rule

> **Automate READING freely. Gate every WRITE behind confinement + a human merge.**
> An agent may *look* at anything and *propose* a change (a PR). It may never *land* one.

And the load-bearing version of that rule:

> **Prompts can be ignored by an LLM; a permission/architecture boundary cannot.** So the guardrails below are *structural* (the agent physically can't reach `main`), not "we told it not to."

---

## The guardrails (the re-enable checklist)

**G1 — Agents never touch `main`. ⭐ (the #1 fix — this is the exact thing that misfired)**
- `worker`/`integrator` commit to their **own worktree branch** and **STOP**.
- **Pushing/merging to `main` is a HUMAN-only action.** No agent runs `git push origin main`, `git merge`, or `git push --force`.
- Nothing lands without a **PR + human review**.
- *Why not a blanket permission deny on "push to main"?* Because the human legitimately pushes to main — a blanket deny blocks real work. So this guardrail lives in the **agent's workflow** (it opens a PR, never merges), reinforced by G4.

**G2 — Confinement.**
- Agents work **only in disposable worktrees** under `.claude/worktrees/` — which are **expendable**.
- **Real work + the shared/test DB live OUTSIDE** the agents' reach (memory: *"keep real work outside `.claude/worktrees`"*).
- An agent wiping its own worktree = harmless. That's the point.

**G3 — Deny the truly-destructive ops** (these never block normal human work):
- `git push --force` / `-f` / `--force-with-lease` → **deny** (CLAUDE.md already forbids force-push).
- `git reset --hard`, `git branch -D`, `git worktree remove`, `rm -rf` on shared dirs → **`ask`** (prompt, don't auto-run) so a human confirms.

**G4 — Kill-switch + stop conditions.**
- Hard off-switch: the `Skill(ralph-loop:ralph-loop)` deny + deleting `.claude/ralph-loop.local.md`.
- **Stop after DoD** — agents finish their mission and STOP; no "run forever / overnight auto-claim."

**G5 — Budget cap.** An autonomous loop burns tokens; cap spend per run/night.

---

## Re-enable procedure (do NOT skip steps)

1. Implement **G1** in the `worker`/`integrator` skills (commit-to-own-branch + open-PR-only; remove every push-to-main / merge step).
2. Confirm **G2** (agents pinned to disposable worktrees; shared DB path outside).
3. Add **G3** denies/asks to `settings.local.json`.
4. Set **G4/G5** (stop-after-DoD, budget).
5. Remove the `Skill(ralph-loop:ralph-loop)` deny **only when 1–4 are done**.
6. **Watch the first 2–3 runs live.** Confirm every change arrives as a **PR**, never a direct commit to `main`, and no shared state is touched.
7. Only then let it run less-attended — still PR-gated, still budget-capped.

---

## One-line summary
> Loop 1 misfired because a **write+merge agent had no cage and no human gate**. The fix isn't "never automate" — it's *"the agent proposes (a PR), a human lands it,"* enforced structurally (confinement + PR-only), not by prompt. Re-enable only after G1–G5 are in and the first runs are watched. Loop 2 (verify=read) stays the model: it's safe because it doesn't write.
