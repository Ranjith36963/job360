<!-- doc: FROZEN -->
> **⚠️ CLOSED / SUPERSEDED (2026-07-23).** This is a historical snapshot. The
> current verified status of every finding lives in **[docs/harness/fable/AUDIT-2026-07-23-FULL-REVERIFY.md](docs/harness/fable/AUDIT-2026-07-23-FULL-REVERIFY.md)** — the fable backlog
> is closed there (92 of 106 fixed; the rest are owner decisions or scheduled audit
> areas). Do NOT treat any item below as still-open without checking that doc first.

# Fable Harness Plan — how Ranjith runs Claude Code, and how to run it better

> Written 2026-07-11 by Claude (Fable 5), based on real observed sessions — not theory.
> Plain English on purpose. Re-read this monthly. Check boxes as you go.

---

## 1. Where you are today (honest snapshot)

**Strong (top ~5% of Claude Code users):**
- Elite `CLAUDE.md` — 28 numbered hard rules, gotchas, phase history. Sessions start smart.
- Real guardrails built: commit gate (`scripts/agent-gate.sh` + hook), 6 CI/monitoring
  workflows, self-verifying encrypted backups, persistent memory notes.
- "Prove it works" mindset — `/verify-job360`, live smoke, evidence over claims.
- You learn from disasters and write them down (ralph-loop, CI patterns, Postgres flakiness).

**Weak (the gap that will burn you):**
- You approve big irreversible actions you can't audit (merged a 135-file PR to
  production `main` with one word). Your safety net is Claude checking Claude.
- Automation turned on before its blast radius was understood (ralph-loop wiped
  worktrees + committed to main; real-account cookie chosen over a throwaway).
- Work piles up: 5+ open PRs at once; a PR sat until it grew 8 merge conflicts.
- Fundamentals gap: git merge/conflicts, sessions/cookies, secrets, what CI actually runs.

**The one-line diagnosis:** great *director* of Claude Code, weak *auditor* of it.
This plan closes the auditor gap without losing the director strength.

---

## 2. The rule above all rules

> **Before any irreversible action (merge, delete, deploy, secret, account, money):
> make Claude answer two questions first —
> "Explain what this does like I'm not a coder" and "What happens if it's wrong?"**

You already do this sometimes ("what will they do"). Do it **every** time.
If Claude can't answer both simply, the action waits.

---

## 3. Phase 1 — Safety habits (start today, takes zero setup)

- [ ] **The two-question ritual** (section 2) before every merge/delete/deploy/secret.
- [ ] **Plain-English diff summary before any merge.** Say: *"summarize this PR in
      10 plain lines: what changes, what could break."* Read it. Then decide.
- [ ] **Throwaway credentials by default.** Test accounts, test keys, test cookies.
      Real credentials only when a fake genuinely can't work — and say so out loud.
- [ ] **When Claude offers 2 options, ask "why / what's the risk of each" before picking.**
      (You picked the riskier 135-file merge path without asking what could go wrong.)
- [ ] Add to `CLAUDE.md` "How to talk to me": *"Before any irreversible action, first
      tell me in plain words what it does and what happens if it's wrong — then wait."*

**Done when:** a month passes with no irreversible action taken un-explained.

---

## 4. Phase 2 — Ship discipline (this week, then always)

- [ ] **Drain the open PRs.** #22, #23 (drafts), #27, #29, #30, #31 — for each one:
      merge it, or close it and note why. Target: **≤ 2 open PRs, ever.**
- [ ] **One stream of work at a time.** Land it before starting the next loop.
- [ ] **PR size cap: ~20 files.** Bigger than that → ask Claude to split it.
      You cannot audit what you cannot read. (The 135-file PR is the lesson.)
- [ ] **No PR older than 7 days.** Stale PRs grow conflicts (8 of them, last time).
      Weekly: rebase-or-merge-or-close every open PR.
- [ ] **Delete merged branches + stale worktrees** as part of landing, not "later".

**Done when:** open-PR list fits on one screen and nothing is older than a week.

---

## 5. Phase 3 — Close the understanding gap (2–4 weeks, ~2h each)

Learn these five, deeply enough to *check* Claude's work — not to write code:

- [ ] **Git:** branch, merge, conflict, what a merge to `main` actually does, revert.
- [ ] **Sessions & cookies:** what a session cookie is, why it's a login key,
      HttpOnly, expiry. (You handed one over without knowing what it was.)
- [ ] **Secrets & env vars:** where they live (.env, GitHub secrets, Railway),
      why values never go in code, how rotation works.
- [ ] **Your CI:** what each of the 6 workflows runs, in one sentence each
      (ci, ci-offline, uptime, synthetic-live, live-e2e, db-backup).
- [ ] **PR mechanics:** review → checks → merge; what `MERGEABLE/DIRTY/CLEAN` mean.

**Method — teach-back:** end sessions with *"give me a 5-line plain recap: what
changed, what could break, what I should understand."* File what you learn in
memory. You retain what you can re-explain.

**Done when:** you can explain last week's merge to a friend without Claude's help.

---

## 6. Phase 4 — The automation ladder (month 2)

Your pattern: powerful automation first, understanding later (ralph-loop). Invert it.

- [ ] **Inventory what exists.** One table: every workflow, hook, skill, cron —
      one sentence each on what it does + what it can break. If you can't fill a
      row, that automation is frozen until you can.
- [ ] **Rule: no NEW automation until every EXISTING one is explainable in one sentence.**
- [ ] **Blast-radius note before enabling anything:** worst case, what does this
      delete / overwrite / send / spend? Written down first, three lines is enough.
- [ ] **Kill switches documented** next to each automation (you did this for
      ralph-loop — after it burned you; do it *before*, for the rest).
- [ ] Autonomous loops stay off until the above is done. Then re-enable one at a
      time, watching the first week of runs.

**Done when:** the inventory table exists and has no empty rows.

---

## 7. Ongoing rhythm (15 minutes, weekly)

Every week, ask Claude for a **harness health check**:

1. Open PRs — count, ages, oldest one's plan. (Target ≤2, none >7 days.)
2. Did all scheduled workflows run green this week? (uptime, synthetic-live,
   live-e2e, db-backup.) Any red → why, in plain words.
3. Any automation added this week? Is its inventory row filled in?
4. `SMOKE_SESSION` cookie still valid? (Smoke failing with a login redirect =
   time to re-copy the cookie and reset the secret.)
5. One thing learned → one line into memory.

---

## 8. Keep doing (don't fix what isn't broken)

- `CLAUDE.md` upkeep and numbered hard rules — this is your superpower.
- Memory notes after every burn and every lesson.
- The commit gate — never bypass it out of convenience.
- Demanding proof over claims ("run it, show me") — your best instinct.
- Model economy (right model for the job) — already disciplined.

---

## 9. How you'll know it worked (60-day scorecard)

| Metric | Today | Target |
|---|---|---|
| Open PRs | 6+ | ≤ 2 |
| Oldest open PR | weeks | < 7 days |
| Irreversible actions taken un-explained | common | 0 |
| Automations you can't explain in one sentence | several | 0 |
| Real credentials used where a throwaway would do | yes | 0 |
| Fundamentals you can teach back (of 5) | ~1 | 5 |

**If you do only one thing from this file: section 2. The two questions.**
