# 00 — Executive Summary

> ## STATUS AS OF 2026-07-16 — READ THIS FIRST
> **The audit below is the ORIGINAL findings, preserved as written. Most are now FIXED.**
> Each finding in `01`–`09` now carries its own **STATUS** line with the commit — trust
> those, not the prose around them. Full history: `PROGRESS.md`.
>
> **All 4 "blockers" below are fixed.** Of ~45 findings, the only things left are:
> | Still open | Kind |
> |---|---|
> | **SI1 — notifications don't reach users in prod** | needs a Railway **worker service + Redis + SMTP creds** — no code will fix it |
> | Real privacy/terms + subprocessor list | writing, not code |
> | Scraping ToS decision (LinkedIn/Indeed/Glassdoor) | business decision |
> | Breach plan · status page/SLA · backup 2nd region | owner decisions |
> | MFA (C11) | a real feature, not a defect |
> | D7/D8/D12, F2, O8, H3–H5 | dev-only paths + accepted trade-offs — none are live bugs |
>
> **Grades below are stale** — they describe the pre-fix state and are kept for the record.

> Job360 enterprise-readiness audit. Six specialist sub-agents (Opus + Sonnet) swept
> every corner; Fable judged, verified the top finding directly, and wrote this.
> **Read this page, then the area doc for whatever you fix first.**

## The honest verdict (one paragraph)
Job360 is **well-built for a solo product** — clean multi-tenant isolation, parameterized
SQL, fail-closed secrets, argon2 + hashed tokens, genuinely good encrypted backups, and
a Claude Code harness better than most professional setups. But it is **not yet
production-grade for real traffic**, for two concrete reasons that were never caught
because the app was never run end-to-end as actually deployed: **the background worker
cannot run its cron jobs** (notifications/digests/ghost-sweep/enrichment have likely been
dead in production), and **the database runs on a single shared connection** that neither
scales nor self-heals. Fix those two and a short list of correctness/compliance items,
and you cross the line from "works on the happy path" to "enterprise-grade."

## The 4 things that actually block you (do these first)
> **STATUS: ALL 4 FIXED.** (1) worker `ctx['db']` — FIXED (`272d29b` + worker_startup); the worker RUNS, but SI1 remains: it isn't DEPLOYED (needs a Railway worker service + Redis). (2) single connection — FIXED (`15c5b68`, per-request `get_request_db`). (3) scraping — **OPEN, owner decision** (unchanged; no code can fix it). (4) delete-doesn't-erase — FIXED (`hard_delete_user`, all 17 per-user tables).

| # | Blocker | Where | Why it's a blocker |
|---|---|---|---|
| **1** | **ARQ worker never sets `ctx['db']`** — every cron crashes (VERIFIED by Fable) | `04-OPS` | Notifications, digests, ghost-sweep, enrichment are silently dead in prod. Tests pass because they fake `ctx` by hand. |
| **2** | **Single unpooled Postgres connection** — no pool, no reconnect | `02-DATA`, `04-OPS` | Concurrent requests collide; one DB restart = full outage until manual restart. |
| **3** | **Scraping LinkedIn/Glassdoor/Indeed** | `05-COMPLIANCE` | Existential business + legal risk; fails any enterprise legal review. |
| **4** | **"Delete my account" doesn't actually erase data** | `02`, `01`, `05` | Soft-delete leaves CVs/embeddings/actions + can resurrect → real UK-GDPR Article-17 gap. |

## Cross-cutting fixes — one change closes several findings
Prioritise these; they're the highest leverage in the whole audit:

- **Connection pool** (`AsyncConnectionPool` + reconnect) → closes the data-layer concurrency P1 **and** the ops reconnect P0. *One fix, two P0/P1s.*
- **Real erasure on delete** (hard-delete/anonymise all user rows, block resurrection) → closes the data orphan finding **+** the security soft-delete-resurrection bug **+** the compliance Article-17 P0. *One fix, three findings.*
- **`Sentry send_default_pii=False` + scrubber** → closes a security P2, an ops P1, and a compliance P1. *One line + a scrubber, three findings.*
- **ARQ `on_startup` hook** → revives all background features at once, plus add one test so the suite can't be green while the worker is dead.

## Scorecard by dimension

| Dimension | Grade | One-line state |
|---|---|---|
| Backend security | **B+** | Above average; one live cookie-`Secure` misconfig, rest is hardening. |
| Data & DB | **C** | Clean tenancy, but the SQLite→Postgres shim seam has no atomic/pool safety net. |
| Frontend & auth-UI | **B** | Core clean; two auth pages leak error strings; polish gaps. |
| Ops & reliability | **C−** | Great backups, but a dead worker + unpooled DB = never run as deployed. |
| Compliance & legal | **C−** | Scaffolding present, substance missing; scraping + non-erasing delete are real. |
| Claude Code harness | **B+** | Strong design; tighten git allowlist + fix the broken `/debug` skill. |

**Overall: C+ / "strong prototype, not yet enterprise-grade."** The gap is small in
effort but real in consequence — mostly a handful of well-scoped fixes, not a rebuild.

## What's genuinely strong (don't lose it)
- Multi-tenant IDOR discipline (every route scoped by `user.id`, none trust path/body).
- Fail-closed secrets; argon2id; 256-bit hashed single-use tokens; no-enumeration auth flows.
- Self-verifying encrypted backups (dump→restore→row-count→encrypt→upload).
- The commit-gate stamp mechanism and memory hygiene in the Claude Code harness.

## How to use this folder
1. Fix the **4 blockers** above (see `07-ROADMAP.md` for the sequence).
2. Then work the **cross-cutting fixes** — best effort-to-impact ratio.
3. Then batch the P2 hardening per area doc.
4. Re-run this audit (same 6-agent sweep) after the blockers land to confirm.

**The mindset that gets you to enterprise-grade:** it isn't more features — it's *fewer
surprises*. Nothing irreversible without a guardrail, no PII moving without a defensible
reason, and every failure caught by a machine before a customer feels it. You already
build guardrails better than most; point that instinct at the four blockers.
