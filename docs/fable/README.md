# Fable Docs — Job360 Enterprise Production-Grade Audit & Plan

> Orchestrated by Claude (Fable 5) on 2026-07-11. Six specialist sub-agents (Opus +
> Sonnet) swept every corner of the codebase, ops, compliance, and the Claude Code
> harness. Fable judged their findings, cut the noise, and wrote this folder.
>
> **Purpose:** one place that tells a solo founder — honestly — what is *missing* to be
> enterprise production-grade, and exactly how to fix it, across the full lifecycle.
> Not bureaucracy. Only what genuinely makes the app strong and sellable.

## How to read this folder

Read **`00-EXECUTIVE-SUMMARY.md` first.** It has the top blockers and the one-page
scorecard. Then go to the area doc you care about. Each doc follows the same shape:

- **What I saw** — the real finding, with `file:line` evidence.
- **Why it matters** — the concrete failure / attack / legal scenario.
- **The fix** — specific and minimal, sized for a solo founder.
- **Priority** — P0 (blocker, do now) · P1 (serious, weeks) · P2 (hardening, later).

## The documents

| File | Covers |
|---|---|
| `00-EXECUTIVE-SUMMARY.md` | Top blockers, scorecard, the order to fix things in. Start here. |
| `01-SECURITY.md` | Auth, sessions, IDOR/multi-tenant, injection, secrets, CV-upload safety. |
| `02-DATA-AND-DB.md` | Migrations, dedup, purge/retention, tenant isolation, SQLite↔Postgres drift. |
| `03-FRONTEND-AND-AUTH-UI.md` | Next.js 16 traps, middleware guard, E2E bypass prod-safety, XSS, secret leakage. |
| `04-OPS-AND-RELIABILITY.md` | CI/CD, backups, monitoring/alerting, worker/scheduler, timeouts, deploy safety. |
| `05-COMPLIANCE-AND-LEGAL.md` | UK-GDPR, PII, data-subject rights, subprocessors, the scraping legal risk. |
| `06-HARNESS-AND-WORKFLOW.md` | Skills, hooks, workflows, loops, CLAUDE.md, memory — how you run Claude Code. |
| `07-ROADMAP.md` | The sequenced 30/60/90-day plan that turns the fixes into a path, not a pile. |
| `08-GAPS-NOT-YET-AUDITED.md` | The honest boundary — dimensions NOT swept (performance, cost, test-quality, …) that a true A still needs. |
| `09-PRODUCTION-SIGNALS.md` | What your REAL live Sentry + PostHog show — worker invisible to Sentry, funnel not instrumented, launch-day bug already fixed. |
| `PROGRESS.md` | Live tracker: fixes shipped (coded + verified) vs pending vs "needs you". |

## Priority legend

- **P0** — a blocker. Security hole, data-loss risk, legal exposure, or it stops an
  enterprise customer / triggers a regulator today. Fix before anything else.
- **P1** — serious gap. Won't sink you this week, but a real customer or incident exposes it.
- **P2** — hardening / polish. Do it once P0/P1 are clear.

## The one principle behind all of it

> Enterprise-grade isn't more features. It's **fewer surprises**: nothing irreversible
> happens without a guardrail, no PII moves without a reason you can defend, and every
> failure is caught by a machine before a customer feels it. Solo founders win this by
> automating the guardrails — not by working harder.
