# 08 — Gaps Not Yet Audited (the honest boundary)

> An audit is only trustworthy if it says what it did NOT look at. The first six docs
> swept security, data, frontend, ops, compliance, and the harness. These dimensions
> were **not** swept — so an "A" claim isn't complete until they are. Listed by how much
> they actually gate a solo-dev "A."

## GATES an A (do these before claiming enterprise-grade)

### Performance & scale — NOT audited
- No load test, no query-plan review, no N+1 sweep, no caching audit.
- **Why it matters:** the single-connection P0 (`02`/`04`) is a *symptom*; nobody has measured what happens at 100 concurrent users. "Works for me" is not a grade.
- **To close:** run a load test (k6/Locust) against a staging deploy; profile the slowest 5 endpoints; check for N+1 in the feed/jobs queries; confirm the connection-pool fix actually holds under load.

### Cost economics — NOT audited
- No review of LLM spend (Groq/Cerebras/Gemini/OpenAI per profile change + judge), API costs (paid aggregators), or infra cost at scale.
- **Why it matters:** a solo SaaS dies from a surprise bill as easily as a bug. The two-pass extraction re-runs *all* LLM passes on any profile change (noted in your own CLAUDE.md as "gate behind a flag later if expensive") — that's an uncapped cost vector.
- **To close:** add per-user LLM-call rate/cost caps; a monthly cost dashboard; alerting on spend spikes; confirm no unbounded re-extraction loop.

### Test-suite quality — only touched, not audited
- The suite was **green while the worker was dead** (`04` P0) — because tests faked `ctx`. That's a testing-philosophy gap: tests that mock the exact thing that's broken.
- **Why it matters:** a test suite that can't catch a dead cron isn't protecting you. Coverage numbers hide these blind spots.
- **To close:** audit what's mocked vs real; add "does it boot as deployed" smoke tests (the synthetic-live suite is a start); measure branch coverage on the critical paths (auth, scoring, dispatch), not line coverage overall.

## Matters for GROWTH, less for grade

### Email deliverability — NOT audited
- Resend is wired, but SPF/DKIM/DMARC alignment on `job360.uk` wasn't checked. If magic-link emails land in spam, signups silently fail and you'd never know.
- **To close:** verify DMARC/DKIM/SPF records; send test mails to Gmail/Outlook/Yahoo; monitor bounce/complaint rates.

### Dependency & supply-chain — shallow
- Only "add Dependabot" was noted. No lockfile CVE audit, no review of the ~300 MB semantic stack's transitive deps.
- **To close:** enable Dependabot + `pip-audit`/`npm audit` in CI as a (non-blocking at first) job.

### Observability depth — thin
- Alerting is up/down only (`04`). No structured request tracing, no per-user error attribution, no latency histograms.
- **To close:** the Sentry error-rate alert (already in roadmap) + basic latency tracking on the top endpoints.

### Product & UX quality — NOT audited
- Onboarding flow, mobile/responsive behaviour, empty/loading/error states beyond the dashboard, first-run experience — none reviewed. This is "is it good to *use*," separate from "is it correct."
- **To close:** a dedicated UX pass (can be its own 6-agent sweep) once the engineering blockers are closed.

## Explicitly out of scope (fine to skip for now)
- SEO / marketing site, i18n/localization, native mobile apps, advanced analytics. None gate a solo-dev "A" at this stage.

## Also note: Compliance doc is partial
`05-COMPLIANCE-AND-LEGAL.md`'s agent hit the session token limit; Fable finished the
verification by hand. It's honest and evidence-checked, but less exhaustive than the
other five — treat it as "the big rocks," not the full legal review.

---

## What this means for the grade
The first six docs + the fixes in `PROGRESS.md` get the **code areas** genuinely toward
A. But a *complete* "solo-dev A" also needs the three GATING dimensions above —
**performance, cost, and test-quality** — swept and fixed. They're not in the current
docs because the six agents weren't pointed at them.

**Recommended next audit round:** one 3-agent sweep — performance, cost, test-quality —
after the connection-pool and erasure fixes land (so the load test measures the *fixed*
system, not the broken one). Until then, this folder is honest about covering ~75% of
what a true A requires, not 100%.
