# 07 — Roadmap

> Turns the findings into a sequence a solo founder can actually walk, without getting
> fragmented. Each block is small and shippable. Do them in order — later blocks assume
> earlier ones. Estimates are "focused solo-founder" time, not calendar time.

## The rule for this roadmap
**One block at a time. Land it (PR merged, verified live) before starting the next.**
That's the anti-fragmentation discipline from your own `fable-harness-plan.md`. Don't
open six branches for six blocks.

---

## WEEK 1 — Stop the bleeding (the 2 P0s + the 1 live misconfig)
The system isn't actually running as believed. Fix that first.

- [ ] **B1 · Revive the worker** (`04-OPS` P0). Add `on_startup`/`on_shutdown` to `WorkerSettings` populating `ctx['db']` + `ctx['enqueue']`. Add a test asserting `on_startup` fills `ctx['db']` so the suite can't be green while prod is dead. **Then read live worker logs** to confirm crons fire. *~half day.*
- [ ] **B2 · Connection pool** (`02-DATA` / `04-OPS` P0). Swap the single connection for `psycopg_pool.AsyncConnectionPool` with reconnect-on-`OperationalError`. *~1 day.* Closes the concurrency ceiling *and* the no-self-heal outage risk.
- [ ] **B3 · Cookie `Secure` on prod** (`01-SECURITY` P1). Set `JOB360_ENV=prod` on Railway today; unify the env check on `APP_ENV`/`RAILWAY_ENVIRONMENT`. *~1 hour.*

**Exit criteria:** worker logs show crons running; app survives a simulated DB blip; session cookie has `Secure` in prod. **Now the app actually does what you thought it did.**

---

## WEEK 2 — Make the data layer trustworthy
- [ ] **B4 · Transactional migrations** (`02-DATA` P1). Wrap each migration body in `BEGIN…COMMIT`; record the migration inside the same tx. Removes the "half-failed migration bricks boot" risk.
- [ ] **B5 · Cascade/orphan cleanup + `setval` after id-copy** (`02-DATA` P1). Explicit child deletes in purge + user-delete; `setval` the identity sequence after any id-copy migration.
- [ ] **B6 · Correctness bugs**: purge on `last_seen_at` not `first_seen`; one canonical ISO timestamp format; collapse whitespace in `normalized_key()` (re-run dedup + UNIQUE tests, rule #1).

**Exit criteria:** a migration can fail without corrupting the schema; no orphan rows after a purge; no duplicate catalog rows from whitespace.

---

## WEEK 3 — Close the compliance blockers (the two that matter)
- [ ] **B7 · Real erasure on delete** (`05` P0 + closes `02` orphans + `01` resurrection). Hard-delete/anonymise ALL user rows (profile, CV text, embeddings, actions, feed, channels, tokens); make resurrection impossible; document backup retention. *One fix, three findings.*
- [ ] **B8 · Decide the scraping question** (`05` P0-legal). Either drop the ToS-violating scrapers (LinkedIn/Glassdoor/Indeed) and lean on licensed/API sources, or write a dated, explicit risk-acceptance with a migration plan. **Make it a conscious decision, on paper.**

**Exit criteria:** "delete me" truly erases; the scraping stance is a written decision, not an accident.

---

## WEEK 4 — Telemetry, consent, and the harness fixes
- [ ] **B9 · Sentry `send_default_pii=False` + `before_send` scrubber** (`01`/`04`/`05`). One line + a scrubber; three findings.
- [ ] **B10 · Cookie-consent banner gating PostHog + disable session recording** on auth/profile pages (`05` P1).
- [ ] **B11 · Real privacy/terms + subprocessor list** (`05` P1). Template or a cheap service; publish the subprocessor list (LLM providers, Resend, Railway, R2, PostHog, Sentry).
- [ ] **B12 · Harness: tighten git allowlist + fix `/debug`** (`06` P1). Remove `Bash(git:*)`/`push`/`merge` from allow; rewrite or delete the broken `debug` skill; add gitleaks to `.pre-commit-config.yaml`.

**Exit criteria:** no PII to third parties without a defensible basis; policies are real; no unsupervised push path; no broken skill in the roster.

---

## MONTH 2 — Hardening & enterprise-sale readiness (batch, lower urgency)
Pick these up once Weeks 1–4 land. None are blockers; all raise the floor.

- [ ] Security hardening batch (`01` P2): Redis-backed rate limits, login timing-safe compare, `defusedxml`, CSRF Origin-check, email+IP lockout.
- [ ] Frontend polish (`03` P2): auth-page error-string fix (P1 — pull this earlier if quick), dashboard error state, `aria-invalid`/`role="alert"` a11y, harden E2E bypass behind a real secret.
- [ ] Ops (`04` P2): write `docs/RUNBOOK-backups.md`, add a `/readyz` DB-backed uptime check + a Sentry error-rate alert, bump Node 20→22, commit `railway.json`, set worker `job_timeout`.
- [ ] Compliance (`05` P2): audit logging of sensitive actions, breach-notification runbook, Dependabot, MFA option, status page.
- [ ] Harness (`06`): codify `.claude/agents/` reviewer definitions (biggest harness upgrade), wire verify into the gate, trim CLAUDE.md to ~200 lines, run `fewer-permission-prompts`.

---

## What NOT to do (so you don't fragment)
- ❌ Don't wire in more tools/MCP servers/skills — you have plenty. The wins above are **consolidation and enforcement**, not new machinery.
- ❌ Don't start Week 2 before Week 1 is verified live.
- ❌ Don't chase P2 polish while a P0 blocker is open.
- ❌ Don't let PR count climb again — one block, one PR, merged, then next.

## Re-audit
After Week 1–3 blocks land, re-run the same 6-agent sweep to confirm the P0s are closed
and nothing regressed. The audit is repeatable — that's the point.

**Bottom line:** ~4 focused weeks moves you from C+ "strong prototype" to a defensible
"enterprise-ready" posture. The first week alone — reviving the worker, pooling the DB,
fixing the cookie — closes the gap between what you *think* is running and what *is*.
