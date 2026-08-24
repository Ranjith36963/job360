# Job360 — Production Readiness Evaluation Report

> ## ⚠️ CLOSED RECORD — 2026-04-26, scored 2026-06-21. NOT a current assessment.
>
> **The 5.4/10 is withdrawn, not updated.** It was measured before Step 3, the
> LLM matcher (engine #4), two-pass extraction, the #10–#15 audit fixes, and
> before the product went live. Several of its lowest marks are now provably
> wrong: ops scored 2-3 against a system that has since shipped to Railway with
> Sentry, nightly DB backups and 30 CI workflows; compliance scored ~1 against
> a codebase that now has GDPR export and hard-delete (`api/routes/auth.py`),
> a privacy policy and terms.
>
> **The 10-gate rubric in §II is still the right rubric.** Re-run it and write
> a NEW dated report; do not edit this one. A score labelled "today" stays
> quotable however many warnings sit above it, which is why the number is gone
> from the header rather than re-captioned.

> **Dated record**, written 2026-04-26 against `main @ 106768f` (post-Step-1.6). It called itself a living document and was never lived up to — the score was updated once in eight months.
>
> **Author's stance.** Job360 is a **UK startup** that will accept CVs (PII) from real users and be shared on LinkedIn — not a portfolio piece. Every gate threshold is calibrated to *"can we accept user #1 without legal, security, or reputational damage,"* not *"is the code clean."*
>
> **Aggregate score as of 2026-06-21: 5.4 / 10** — engineering-strong (~7-8), ops-weak (~2-3), compliance-absent (~1). Recorded for history only; see the banner above for why two of those three marks no longer hold.

---

## Table of Contents

- [Part I — The 10 Production Gates](#part-i--the-10-production-gates-rubric)
  - [Gate 1 — Functional Correctness](#gate-1--functional-correctness)
  - [Gate 2 — Performance](#gate-2--performance)
  - [Gate 3 — Security](#gate-3--security)
  - [Gate 4 — Reliability](#gate-4--reliability)
  - [Gate 5 — Data Integrity](#gate-5--data-integrity)
  - [Gate 6 — Compliance & Legal (UK)](#gate-6--compliance--legal-uk)
  - [Gate 7 — Ops & Deployment](#gate-7--ops--deployment)
  - [Gate 8 — Developer Experience](#gate-8--developer-experience)
  - [Gate 9 — ML / AI Subsystems](#gate-9--ml--ai-subsystems)
  - [Gate 10 — User Experience](#gate-10--user-experience)
- [Part II — Job360 Current State Scoring](#part-ii--job360-current-state-scoring)
- [Part III — Gap Inventory (operational backlog)](#part-iii--gap-inventory-operational-backlog)
- [Part IV — Anti-Patterns This Codebase Must Avoid](#part-iv--anti-patterns-this-codebase-must-avoid)
- [Part V — Manual Testing Playbook](#part-v--manual-testing-playbook)
- [Part VI — `make verify-production` Aggregate Spec](#part-vi--make-verify-production-aggregate-spec)
- [Part VII — Industry References](#part-vii--industry-references)
- [Part VIII — Roadmap to Production-Ready](#part-viii--roadmap-to-production-ready)

---

# Part I — The 10 Production Gates (rubric)

Each gate has a **definition**, a **why-it-matters** statement, **green/yellow/red thresholds**, and **how-to-measure**. A gate is "green" only when *every* threshold in the green column is met. Yellow = at least one threshold is in the yellow band. Red = at least one threshold is in the red band.

> **Scoring convention.** Each gate is scored 0-10. Aggregate = arithmetic mean across all 10 gates. Production-ready = aggregate ≥ 8.0 *AND* no individual gate < 6.0. Gates 3 (Security), 5 (Data), 6 (Compliance) are **launch blockers** — any score < 7 in these halts launch regardless of aggregate.

---

## Gate 1 — Functional Correctness

**Definition.** Every code path runs as documented; tests prove it; the system can be re-built from a clean checkout.

**Why it matters.** A passing test suite is the floor of "the code does what we say it does." A failing or absent suite means every release is a coin flip.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Backend pytest pass rate | ≥99.5% on full tree (no `--ignore`) | 95-99.5% OR `--ignore` declared in commit | <95% OR scope drift |
| Frontend test count | ≥30 unit + ≥5 E2E | ≥10 unit | 0 |
| Mutation testing | Cosmic-Ray / mutmut score ≥ 60% | 30-60% | absent / <30% |
| Migration round-trip | up→down→up clean, row counts preserved | up→down works once | down absent |
| Seed-restore | dump→restore on fresh `:memory:` boots clean | works with manual fixup | broken |
| Build reproducibility | `docker build` byte-identical twice | mostly identical | random failures |

**How to measure.**
```bash
# Backend
cd backend && python -m pytest tests/ -q -p no:randomly --tb=short
# Frontend (post-Step-2)
cd frontend && npm run test:unit && npm run test:e2e
# Mutation
cd backend && python -m mutmut run --paths-to-mutate=src/services
# Migration round-trip
python -c "import asyncio; from migrations.runner import up, down; asyncio.run(up('/tmp/x.db')); asyncio.run(down('/tmp/x.db')); asyncio.run(up('/tmp/x.db'))"
# Seed-restore
sqlite3 backend/data/jobs.db .dump > /tmp/dump.sql && sqlite3 :memory: < /tmp/dump.sql
```

**Industry baselines.** Google testing-on-the-toilet docs cite >70% line coverage as the floor; Stripe internal CI gate is "no failing tests on main, ever"; the standard Continuous Delivery book defines green-build discipline as the deploy precondition.

---

## Gate 2 — Performance

**Definition.** Latency, throughput, payload size, and rendering speed all fall within targets *under realistic load*.

**Why it matters.** A slow product loses users in the first 3 seconds. Mobile users on 3G are the actual production environment, not your localhost.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| API p95 latency `/jobs?limit=50` | <200ms warm, <500ms cold | <500ms warm | >500ms warm |
| API p99 latency | <800ms warm | <1500ms | >1500ms |
| Frontend Lighthouse Performance | ≥90 mobile, ≥95 desktop | ≥80 mobile | <80 mobile |
| Lighthouse Accessibility | ≥95 | ≥90 | <90 |
| Lighthouse Best Practices | ≥95 | ≥90 | <90 |
| Lighthouse SEO | ≥95 | ≥90 | <90 |
| Time-to-Interactive (mobile, slow 3G) | <2.5s | <4s | >4s |
| Initial JS bundle (gzipped) | <200 KB | <350 KB | >350 KB |
| Database query p95 | <50ms | <200ms | >200ms |
| Background-task wall-clock | per-source <30s | <60s | >60s |

**How to measure.**
```bash
# API load test
hey -n 1000 -c 10 -H "Cookie: job360_session=..." http://localhost:8000/api/jobs?limit=50
# Lighthouse (CI-driven)
npx lighthouse http://localhost:3000/dashboard --view --preset=mobile
# Bundle size
cd frontend && npm run build && du -sh .next/static/chunks/*.js | sort -h
# DB EXPLAIN
sqlite3 backend/data/jobs.db "EXPLAIN QUERY PLAN SELECT * FROM jobs WHERE staleness_state='active' LIMIT 50"
```

**Industry baselines.** Google "Web Vitals" defines LCP <2.5s / FID <100ms / CLS <0.1 as "good" for the 75th-percentile user; Vercel's production checklist requires Lighthouse ≥90 across all 4 dimensions before shipping; Stripe API publishes p99 <100ms as their internal target.

---

## Gate 3 — Security (LAUNCH BLOCKER)

**Definition.** OWASP Top 10 covered; secrets are rotated; auth is hardened; rate limits prevent abuse.

**Why it matters.** A single SQL injection / XSS / IDOR / leaked secret can end the company. A stolen user CV is reportable to the ICO within 72 hours and you'll spend 3 weeks dealing with the fallout.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| OWASP ASVS Level 1 coverage | 100% items pass | ≥90% | <90% |
| Dependency vulnerabilities | 0 high/critical from `pip-audit` + `npm audit --production` | ≤2 high with mitigation notes | any unmitigated critical |
| Secret-scan | `gitleaks detect` zero findings | only test-fixture secrets | real secret in history |
| Security headers | CSP + X-Frame-Options + HSTS + X-Content-Type-Options + Referrer-Policy all present | ≥3 of 5 | <3 of 5 |
| CORS | explicit allow-list, methods + headers enumerated | wildcard methods only | wildcard origins |
| Auth — password hash | argon2id with t=3, m=64MiB, p=4 (or stronger) | bcrypt cost ≥12 | MD5/SHA1/plaintext |
| Session cookie flags | HttpOnly + Secure + SameSite=Lax + ≤30d expiry | missing one flag | missing 2+ |
| IDOR audit | every per-user route gated by `require_user`, scoped by `user.id` | manual spot-check passes | any route accepts user_id from URL/body |
| Rate limiting | per-user + per-IP global + per-endpoint custom | per-user only | none |
| Input validation | every Pydantic `str` has `max_length`; URL fields validated | ≥70% have bounds | unbounded strings reach DB/LLM |
| SQL injection | 100% parameterized queries; `ruff S608` clean | 1-2 string-formatted with safe inputs | any unsafe interpolation |
| XSS | `dangerouslySetInnerHTML` only on sanitized HTML; CSP nonce-based | CSP `script-src 'self'` | inline scripts allowed |

**How to measure.**
```bash
# Dependency scan
cd backend && pip-audit --strict
cd frontend && npm audit --audit-level=high --production
# Secret scan
gitleaks detect --source . --no-banner
# Headers smoke (post-Step-4)
curl -I http://localhost:8000/api/jobs | grep -E 'content-security-policy|x-frame-options|strict-transport-security'
# Static security analysis
cd backend && bandit -r src
# IDOR audit
grep -rn "user_id.*request" backend/src/api/routes/  # any from URL/body = bug
```

**Industry baselines.** OWASP ASVS Level 1 is the *minimum* for any application processing PII; OWASP Top 10 (2021) is the universal floor; Stripe security blog publishes their layered approach (CSP, SubResource Integrity, signed cookies); GitHub's security advisory database is the standard for dependency triage.

---

## Gate 4 — Reliability

**Definition.** The system stays up under realistic failure modes (provider outages, network blips, DB locks, restart cycles).

**Why it matters.** Reliability is the SLO you actually have, not the one you wish for. Without explicit budgets + retries + idempotency + graceful shutdown, every restart loses in-flight work.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Defined SLOs | published `/docs/slo.md` with availability + latency targets | informal targets | none |
| Error budget tracking | monthly burn-rate dashboard | manual checks | none |
| Worker task retries | exponential backoff, max-retries declared, DLQ on permanent failure | retries without DLQ | no retries |
| Worker timeouts | per-task explicit timeout | global default | none |
| Circuit breakers | per-source state machine, half-open probe | global breaker | none |
| Idempotency keys | every state-mutating endpoint accepts `Idempotency-Key` | per-action only | none |
| Graceful shutdown | SIGTERM drains in-flight requests within 10s; ARQ worker finishes current task | basic SIGTERM | force-kill only |
| Health endpoints | `/livez` (process up) + `/readyz` (deps responding) split | single `/health` with deps | hardcoded OK |
| Cascading failure protection | global kill-switch when >N% sources down | per-source breakers | none |
| Restart safety | clean restart with no data loss | 1-2 dropped tasks | restart corrupts state |

**How to measure.**
```bash
# Chaos test: kill API mid-request
kill -TERM $(pgrep -f "uvicorn") && curl http://localhost:8000/api/jobs  # should drain not crash
# Worker DLQ verification
arq src.workers.settings.WorkerSettings --check  # task config dump
# Breaker probe
python -c "from src.services.circuit_breaker import default_registry; print(default_registry().get('arbeitnow').state)"
# Health endpoint differentiation
curl -s http://localhost:8000/livez && curl -s http://localhost:8000/readyz
```

**Industry baselines.** Google SRE Book chapters 3-5 (SLOs, error budgets, eliminating toil) are the industry standard; Stripe API publishes idempotency-key headers on every POST; Heroku's `SIGTERM` 30-second drain pattern is the cloud-native default.

---

## Gate 5 — Data Integrity (LAUNCH BLOCKER)

**Definition.** Data is never corrupted, lost, or unrecoverable; backups exist and have been tested.

**Why it matters.** Losing a user's CV upload, scoring history, or pipeline state once is unrecoverable trust damage. "We had a database issue" is a PR death-spiral.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Backup automation | nightly DB dump → S3/equivalent, encrypted at rest | weekly manual | none |
| Backup restore | tested monthly, RTO < 1h documented | tested once | never tested |
| Migration up/down/up cycle | every migration round-trips clean | up only tested | down missing |
| Foreign key enforcement | `PRAGMA foreign_keys=ON` on every connection | at app boot only | off |
| Dedup audit | zero `(user_id, job_id)` collisions in `user_actions` + `applications` | 1-2 historical | active collisions |
| Ghost-detection writer | nightly job marks `staleness_state='expired'` per state machine | manual run | absent (Step 1.5 deferral) |
| Per-tenant isolation | every per-user query joined on `user.id` | most | any cross-tenant leak |
| Schema drift detection | `migrations.runner status` + CI check on every PR | local only | no check |
| Encryption at rest | DB volume + backup encrypted | DB only | neither |
| Data retention | per-table TTL documented + enforced | jobs auto-purge >30d (current) | unlimited |

**How to measure.**
```bash
# Round-trip test
for v in 0001 0002 ... 0011; do
  python -m migrations.runner up && python -m migrations.runner down "$v" && python -m migrations.runner up
done
# Dedup audit
sqlite3 backend/data/jobs.db "SELECT user_id, job_id, COUNT(*) FROM user_actions GROUP BY user_id, job_id HAVING COUNT(*) > 1"
# Backup-restore drill
./scripts/backup_db.sh && ./scripts/restore_db.sh /tmp/test_restore.db
# Tenant leak audit
grep -rn "WHERE.*user_id" backend/src/repositories/database.py | grep -v "user_id = ?"  # any literal user_id = bug
```

**Industry baselines.** AWS Well-Architected Reliability pillar requires automated backups + tested restore; Heroku's "12 Factor App" (factor 6 — processes) requires backing services to be attached resources; the "3-2-1 backup rule" (3 copies, 2 media, 1 offsite) is the SRE consensus.

---

## Gate 6 — Compliance & Legal (UK) (LAUNCH BLOCKER)

**Definition.** UK GDPR + ICO registration + ASA marketing rules + AI-Act CV-scoring disclosure all satisfied *before* user #1 uploads.

**Why it matters.** Processing personal data without ICO registration is a criminal offence (£40 registration is mandatory). Misleading marketing can be challenged by ASA. The EU AI Act (and UK equivalent) require disclosure when an algorithm scores a person's CV. Each is non-negotiable.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| ICO registration | completed (controller registered, fee paid) | application submitted | not done |
| Privacy notice | published, GDPR Art. 13 compliant, lawful basis stated | draft | absent |
| Terms of Service | published, signed by user on register | draft | absent |
| Cookie banner | consent before non-essential cookies fire | informational only | none |
| Right to erasure (Art. 17) | `DELETE /api/profile/me` cascades to all per-user tables | partial cascade | absent |
| Right to access (Art. 15) | export endpoint returns all user data | per-table only | absent |
| Right to portability (Art. 20) | JSON Resume export (Step 1.5 ✅) | partial | absent |
| Data retention policy | per-table TTL + auto-purge documented | jobs only | none |
| LLM provider disclosure | privacy notice names Gemini/Groq/Cerebras + their data policies | mentioned | absent |
| AI-Act CV-scoring disclosure | UI shows "your CV is scored by an algorithm" + opt-out | mention in privacy | absent |
| ASA marketing copy | every claim substantiated, no "best" / "fastest" without proof | most claims OK | unbacked claims |
| LICENSE | OSI-approved license at repo root | informal terms | none |
| SECURITY.md | vulnerability disclosure policy | email contact | none |

**How to measure.**
- Manual: visit `https://ico.org.uk/for-organisations/data-protection-fee` → pay £40 → store certificate at `docs/legal/ico_certificate.pdf`
- Manual: walk the privacy notice with a non-technical friend; can they explain back what data you collect?
- Code: `grep -r "lawful_basis" docs/legal/` returns hits
- Code: `curl -X DELETE -H "Cookie: session..." /api/profile/me` then verify all tables purged

**Industry baselines.** ICO's "Data Protection Fee" guide is the UK statutory requirement; GDPR Articles 5-22 are the EU/UK personal-data baseline; ASA CAP Code is the marketing-claim authority; the EU AI Act (passed March 2024) classifies CV-scoring as "high-risk" requiring transparency.

---

## Gate 7 — Ops & Deployment

**Definition.** Code can be built, deployed, observed, and rolled back without manual SSH.

**Why it matters.** Manual deploys = irreproducible state. No observability = no incident response. No rollback = first bug becomes a 2-hour outage.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| CI matrix | lint + type-check + test + build run on every PR + main push | partial | none |
| Containerization | multi-stage Dockerfile, `.dockerignore`, optional `docker-compose.yml` | single-stage Dockerfile | none |
| Deploy config | platform manifest (Vercel/Railway/Fly/K8s) + IaC (Terraform/Pulumi) | platform-only | manual |
| Secret management | external manager (Doppler/Vault/AWS Secrets Manager); rotation policy | env file with audit | env file in `.env` |
| Process management | systemd / PM2 / supervisord with restart-on-crash | manual restart | bare process |
| Zero-downtime deploy | blue/green or rolling, smoke test before traffic switch | rolling restart | full downtime |
| Rollback | one command, < 5 min RTO | manual git revert + redeploy | git revert + pray |
| Smoke test post-deploy | automated 5-endpoint hit + JSON-LD validation | manual click | none |
| Logging | structured JSON, centralized aggregator (Datadog/Loki/Logtail) | local rotating file (current) | print-based |
| Metrics | Prometheus `/metrics` + Grafana dashboards | custom dashboard | none |
| Tracing | OpenTelemetry spans, propagated across services | request-id only | none |
| Error tracking | Sentry (or equivalent) with source maps | logs only | none |
| Alerting | PagerDuty/equivalent on (a) error rate >2%, (b) p95 >1s 5min, (c) 5xx spike | manual log scan | none |

**How to measure.**
```bash
# CI presence
ls -la .github/workflows/*.yml  # expect ≥3 files (PR, main, scheduled)
# Docker reproducibility
docker build -t job360:test backend/ && docker build -t job360:test2 backend/ && docker images | grep job360 | awk '{print $3}' | sort -u | wc -l  # expect 1
# Deploy smoke
curl https://job360.app/livez && curl https://job360.app/readyz
# Metrics
curl https://job360.app/metrics | grep -c '^http_request_duration_seconds'  # expect non-zero
```

**Industry baselines.** 12-Factor App (factors 1-12) is the cloud-native baseline; Vercel's deployment lifecycle docs are the SaaS gold standard; Google SRE Book chapters 12-13 (effective troubleshooting, postmortems) require traces + logs + metrics; Stripe's "5 ways we made our API more reliable" blog post is a canonical reference.

---

## Gate 8 — Developer Experience

**Definition.** A new contributor (you, in 6 months; or a friend) can boot the system + ship a fix in <30 min without asking questions.

**Why it matters.** DX *is* velocity. Every minute spent fighting setup is a minute not shipping. For a one-person startup that may add a co-founder later, DX is recruiter capital.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Time-to-first-run (TTFR) | <30 min from clone → green test suite | <60 min | >60 min |
| README quality | quickstart, architecture diagram, contribution guide | quickstart only | bare or stale |
| Doc completeness | README, CONTRIBUTING, ARCHITECTURE, ADRs/ for decisions | first 3 only | only README |
| Pre-commit hooks | ruff + format + EOF + YAML + bandit + commit-msg | first 5 | first 2 |
| Type checking | mypy --strict on backend, tsc --noEmit on frontend, both in CI | local only | absent |
| Lint enforcement | ruff + eslint + jsx-a11y + import-order | partial | local only |
| Test discoverability | `pytest -m fast` for smoke; `make test` for full | full only | none |
| Conventional commits | enforced via commit-msg hook | followed manually | inconsistent |
| ADRs (Architecture Decision Records) | every irreversible decision documented | informal notes | none |
| Onboarding doc | `docs/onboarding.md` walks day-1-of-contribution | scattered | none |

**How to measure.**
```bash
# TTFR test (timed walkthrough on a fresh machine)
time bash setup.sh && time make test
# Doc inventory
ls -la *.md docs/*.md
# Hook coverage
yq '.repos[].hooks[].id' .pre-commit-config.yaml | sort -u
```

**Industry baselines.** Stripe's "5-minute setup" mantra; GitHub's `CONTRIBUTING.md` template; Conventional Commits 1.0 spec; Michael Nygard's ADR pattern (his "Documenting Architecture Decisions" 2011 post is the canonical reference); Vercel/Next.js docs as the gold standard for reading flow.

---

## Gate 9 — ML / AI Subsystems

**Definition.** Every AI/ML touch point is resilient, cost-capped, version-pinned, and gracefully degrades.

**Why it matters.** LLM costs scale super-linearly with users; provider outages happen weekly; prompt drift silently degrades quality. Without controls, one viral LinkedIn post becomes a £500 surprise bill.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Provider fallback chain | ≥3 providers with auto-failover | 2 providers | 1 provider |
| Cost cap | per-key spend ceiling, alert at 80% | per-day budget | none |
| Prompt versioning | every prompt has version + git history | prompts in code | inline strings |
| Hallucination guard | structured output (Pydantic) + validation + fallback to "unknown" | basic try/except | unchecked LLM output |
| Model version pinning | exact model ID, no `latest` | major version | floats with API |
| Embedding lazy-import | heavy deps imported only when `SEMANTIC_ENABLED=true` (current ✅) | partial | top-level import |
| Feature flag default | new AI features default OFF until validated | OFF for some | ON by default |
| Privacy disclosure | users informed CV is sent to LLM, named providers | mentioned | undisclosed |
| Eval suite | golden CV/job set with regression check on prompt changes | smoke test | none |
| Cost telemetry | tokens-in/tokens-out per request logged + aggregated | logged only | absent |

**How to measure.**
```bash
# Provider fallback drill
unset GEMINI_API_KEY && python -c "from src.services.profile.llm_provider import LLMProvider; LLMProvider().extract_skills('test')"  # should fall through to Groq
# Cost cap
grep -r "monthly_budget\|cost_cap\|spend_limit" backend/src/services/  # expect non-zero hits
# Lazy-import audit
JOB360_ENV=test python -c "import sys; from src.api.main import app; assert 'sentence_transformers' not in sys.modules"
# Eval suite
ls backend/evals/*.py
```

**Industry baselines.** OpenAI's production-readiness guide (cost-cap + structured-output + fallback); Anthropic's API best-practices doc (idempotency, exponential backoff, structured outputs via tool-use); LangChain's eval framework as the open-source baseline; Bridgewater's "principles" on cost-control as a forcing function.

---

## Gate 10 — User Experience

**Definition.** A first-time visitor on a low-end mobile device on a different network reaches first-value in <60s without help.

**Why it matters.** For a startup, UX *is* the product. The friend you DM the link to is the only metric that matters. They give you ~10 seconds before deciding if Job360 looks polished or janky.

| Threshold | 🟢 Green | 🟡 Yellow | 🔴 Red |
|---|---|---|---|
| Time-to-first-value (TTFV) | <60s anonymous → scored job visible | <2 min | >2 min |
| Mobile-first | every flow works on iPhone SE viewport (375px) | minor breakage | broken layouts |
| Accessibility (WCAG 2.1 AA) | keyboard nav + screen reader + color contrast all pass | most | broken |
| Cold-start UX | tested on incognito + different network + clean cache | tested locally | only on dev machine |
| 5-friend dogfood | 3+ of 5 complete golden path unassisted | 1-2 | 0 |
| Loading states | skeleton matches final layout, no CLS | spinner | blank |
| Error states | typed errors, toast feedback, retry affordance | generic banner | silent fail |
| Empty states | "your first action here" guidance | message only | empty |
| 4xx/5xx handling | 401→login redirect, 429→retry-after toast, 500→error page | partial | generic |
| LinkedIn share preview | Open Graph + Twitter cards + JobPosting JSON-LD | OG only | default preview |
| First-impression delight | one micro-interaction (animation, surprise) per page | bland | broken |

**How to measure.**
```bash
# TTFV stopwatch
# (manual: open https://job360.app in incognito, time to first scored JobCard)
# Mobile viewport
npx playwright test --project=mobile-chrome
# A11y
npx @axe-core/cli https://job360.app/dashboard
# Share preview
curl -s https://job360.app/jobs/1 | grep -E 'og:title|og:description|og:image|application/ld\+json'
```

**Industry baselines.** Google PageSpeed Insights "Field Data" (Chrome User Experience Report) is the source-of-truth; Nielsen Norman Group usability heuristics (10 principles since 1994); the "5-user test" from Jakob Nielsen's research is the dogfood floor; WCAG 2.1 AA is the legal accessibility minimum in UK (Equality Act 2010).

---

# Part II — Job360 Current State Scoring

## §II.A Score history

| Date | Aggregate | Gate scores (1-10) | Trigger | Notes |
|---|---|---|---|---|
| 2026-04-26 | **5.4** | 1:7, 2:5, 3:6, 4:5, 5:6, 6:1, 7:2, 8:7, 9:5, 10:5 | initial baseline | post-Step-1.6, pre-Step-2 |
| 2026-04-26 | **6.5** | 1:8, 2:6, 3:7, 4:5, 5:6, 6:1, 7:2, 8:8, 9:5, 10:7 | Step 2 merged @ `9868877`, tag `step-2-green` | Cohorts A+B+C+D+E + R-1..R-4 hotfix; gates 1/8/10 closed; gate 3 +1 from JSON-LD XSS fix |
| _(append after each batch merges)_ | | | | |

> **Update protocol.** After every Step-N green merge, append a row. Use the same gate/scoring methodology. Trajectory > absolute score.

## §II.B Per-gate scoring (today)

### Gate 1 — Functional Correctness — **7 / 10**

**Evidence.** Backend pytest 1056p / 0f / 3s under `--ignore=tests/test_main.py`; full tree 1087p/0f/17s (test_main.py has 17 live-HTTP-leak skips). All 12 migrations bidirectional (`backend/migrations/`). 600 tests fully hermetic via in-memory fixtures (`backend/tests/conftest.py:111-127`). **Gaps:** Frontend 0 tests (closing in Step 2 Cohort A). No mutation testing. Build reproducibility untested (no Docker yet).

### Gate 2 — Performance — **5 / 10**

**Evidence.** DB has 5 core indexes + WAL + 5s busy_timeout + LEFT-JOIN-once for jobs+enrichment (no N+1, `database.py:629`). **Gaps:** No load test ever run. No Lighthouse benchmark. No bundle size budget. Frontend dashboard double-fetches (Step 2 audit). No `/metrics` endpoint. p95 latency unmeasured.

### Gate 3 — Security — **6 / 10** (LAUNCH BLOCKER)

**Evidence.** Argon2id (t=3, m=64MiB, p=4) at `passwords.py:13` — OWASP-tier. Session cookies HttpOnly + SameSite=lax + Secure-on-prod. SESSION_SECRET fail-closed (`auth_deps.py:36-42`). 100% parameterized SQL. IDOR closed across all 7 per-user route files (Batch 3.5 + 3.5.1 + 3.5.2). **Critical gaps (red):** No security headers middleware (`api/main.py:39-62`). CORS `allow_methods=["*"]` + `allow_headers=["*"]`. Pydantic `str` fields with no `max_length` (DoS vector). No global API rate limiting. No `bandit` in pre-commit. No dependency vulnerability scan in CI.

### Gate 4 — Reliability — **5 / 10**

**Evidence.** Per-source circuit breakers (300s cooldown, half-open probe, `circuit_breaker.py:39`). Send_notification ledger with idempotency_key (Batch 2). FastAPI lifespan-based shutdown. **Gaps:** ARQ worker tasks have no explicit timeouts / retries / DLQ in `workers/settings.py`. No global cascade kill-switch (50 × 300s = 4.16h worst-case). `/health` returns hardcoded OK with no DB/Redis ping. No `/livez` vs `/readyz` split. No defined SLOs. No idempotency keys on most state-mutating endpoints (only `mark_ledger_*`).

### Gate 5 — Data Integrity — **6 / 10** (LAUNCH BLOCKER)

**Evidence.** All 12 migrations bidirectional + tested individually. Multi-tenant isolation verified (Batch 3.5 IDOR + Batch 3.5.2 multi-user profiles). Salary bounds enforced (10k-500k, `models.py:69-72`). Auto-purge >30d (`database.py`). **Gaps:** No DB backup script. No restore-drill ever performed. Ghost-detection writer deferred (Step 1.5 carry-over). No URL scheme validation on `apply_url`. No FK enforcement check (`PRAGMA foreign_keys=ON` not asserted on every connection). No encryption-at-rest (SQLite file is plaintext on disk).

### Gate 6 — Compliance & Legal — **1 / 10** (LAUNCH BLOCKER)

**Evidence.** JSON Resume export shipped (Article 20, Step 1.5). **Critical gaps:** ICO registration not done (£40, mandatory, criminal offence to process PII without it). Privacy notice absent. Terms absent. Cookie banner absent. Right-to-erasure absent. LLM provider disclosure absent. AI-Act CV-scoring disclosure absent. LICENSE absent. SECURITY.md absent.

> **Hard blocker.** Score 1 means the legal floor isn't there. Cannot accept user #1 until Gate 6 reaches ≥7.

### Gate 7 — Ops & Deployment — **2 / 10**

**Evidence.** Pre-commit hooks active (ruff + format + EOF + YAML + large-file). Structured logging available (JSON formatter exists, not active by default). `health.py` exists. Custom telemetry dataclasses (`utils/telemetry.py`). `log_rotation_check.py`. **Critical gaps:** `.github/workflows/` empty (zero CI). No Dockerfile / docker-compose / dockerignore. No deploy config (Vercel/Railway/Fly/K8s/Terraform). No secret manager. No process manager. No Prometheus / Sentry / OpenTelemetry. No DB backup script. No alerting. No rollback procedure.

### Gate 8 — Developer Experience — **7 / 10**

**Evidence.** TTFR via setup.sh ~15-20 min (audit-confirmed). README + CONTRIBUTING + ARCHITECTURE + STATUS + CLAUDE all present. 21 CLAUDE.md rules. 14 docs/ files. Pre-commit hooks. Conventional commits used (history shows `feat(...)`, `fix(...)`, `chore(...)`). Test discoverability good (`pytest -m fast`, `make test`). **Gaps:** No mypy/pyright in CI. No `eslint-plugin-jsx-a11y`. No ADRs/. No commit-msg hook enforcement. Documentation has minor staleness (per audit history).

### Gate 9 — ML / AI Subsystems — **5 / 10**

**Evidence.** 3-provider fallback chain (Gemini → Groq → Cerebras, `llm_provider.py:18-53`). Pydantic structured output for enrichment (`job_enrichment_schema.py`). Embedding/chromadb fully lazy when `SEMANTIC_ENABLED=false` (default). `ENRICHMENT_ENABLED` defaults off. **Gaps:** No cost cap or per-key spend tracking. No prompt versioning (prompts inline). Models pinned via env var but no audit log. No eval suite for prompt regression. No CV→LLM privacy disclosure (overlaps with Gate 6).

### Gate 10 — User Experience — **5 / 10**

**Evidence.** Landing page strong value prop (Step 2 audit confirmed `app/page.tsx:141-152`). Dashboard stats strip (`dashboard/page.tsx:317-337`). Loading skeletons match grid (`JobList.tsx:12-37`). Empty state in JobList. Error state on JobDetail (`[id]/page.tsx:163-179`). Mobile responsive Navbar + dashboard. **Gaps:** Step 2 audit found 40+ frontend gaps — no test stack, no auth guard, ScoreRadar fragile prop names, ~10 enrichment fields hidden, no version history UI, no logout button, no shared error/empty components, no SEO/OG/JSON-LD, no JobPosting structured data. TTFV unmeasured. No 5-friend dogfood done.

## §II.C The verdict

**Job360 today is `5.4 / 10`.** Engineering depth is real (auth, IDOR, SQL safety, hermetic tests, lazy imports, indexes, WAL, migration safety) — those reach the production bar. What's missing is the *containing system*: CI, Docker, secrets manager, monitoring, backups, GDPR docs, security headers, ML cost caps, frontend test floor.

**Production-ready threshold: aggregate ≥ 8.0 with no individual gate < 6.0.** Gates 6 (1 → ≥7), 7 (2 → ≥7), 4 (5 → ≥7), 9 (5 → ≥7), and 10 (5 → ≥7) are the gap. **Realistic path:** Step 2 closes Gate 10. Step 3 closes parts of Gate 4 + Gate 8. Step 4 closes Gates 4 + 7. Step 5 closes Gate 6. Estimated 4-6 weeks of focused execution to launch-ready.

---

# Part III — Gap Inventory (operational backlog)

Every concrete gap from the 14 Step-2 audits + the 3 production audits + STATUS.md, tagged for action. Severity scale: **P0** (launch blocker), **P1** (must fix pre-merge of next batch), **P2** (follow-up batch), **P3** (nit / nice-to-have).

## §III.A Security gaps (Gate 3)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| S-01 | No Content-Security-Policy header | `backend/src/api/main.py:39-62` | P0 | Step 4 | 30min |
| S-02 | No X-Frame-Options header | `backend/src/api/main.py:39-62` | P0 | Step 4 | 5min |
| S-03 | No Strict-Transport-Security header | `backend/src/api/main.py:39-62` | P0 | Step 4 | 5min |
| S-04 | No X-Content-Type-Options header | `backend/src/api/main.py:39-62` | P1 | Step 4 | 5min |
| S-05 | No Referrer-Policy header | `backend/src/api/main.py:39-62` | P1 | Step 4 | 5min |
| S-06 | CORS `allow_methods=["*"]` overly permissive | `backend/src/api/main.py:43-50` | P1 | Step 4 | 10min |
| S-07 | CORS `allow_headers=["*"]` overly permissive | `backend/src/api/main.py:43-50` | P1 | Step 4 | 10min |
| S-08 | Pydantic `raw_text` no max_length (DoS) | `backend/src/api/models.py:35` | P1 | Step 4 | 5min |
| S-09 | Pydantic `raw_text` no max_length (DoS) — 2nd | `backend/src/api/models.py:134` | P1 | Step 4 | 5min |
| S-10 | Pydantic `summary_text` no max_length | `backend/src/api/models.py:140` | P1 | Step 4 | 5min |
| S-11 | Pydantic `experience_text` no max_length | `backend/src/api/models.py:141` | P1 | Step 4 | 5min |
| S-12 | All other unbounded `str` fields in models.py | `backend/src/api/models.py` (sweep) | P1 | Step 4 | 1h |
| S-13 | No global API rate limiting (`slowapi`) | new middleware | P1 | Step 4 | 2h |
| S-14 | No `bandit` in pre-commit | `.pre-commit-config.yaml` | P1 | Step 4 | 10min |
| S-15 | No `pip-audit` in CI | `.github/workflows/backend.yml` | P0 | Step 4 | 15min |
| S-16 | No `npm audit` in CI | `.github/workflows/frontend.yml` | P0 | Step 4 | 15min |
| S-17 | No `gitleaks` secret-scan in CI | `.github/workflows/security.yml` | P0 | Step 4 | 30min |
| S-18 | No URL scheme validation on `apply_url` | `backend/src/repositories/database.py:154-199` | P1 | Step 3 | 30min |
| S-19 | No CV file-size limit (multipart upload) | `backend/src/api/routes/profile.py` | P0 | Step 4 | 30min |
| S-20 | No CV MIME-type whitelist | `backend/src/api/routes/profile.py` | P1 | Step 4 | 30min |

## §III.B Reliability gaps (Gate 4)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| R-01 | No worker task timeouts | `backend/src/workers/settings.py:88-96` | P0 | Step 4 | 1h |
| R-02 | No worker retry policy | `backend/src/workers/settings.py:88-96` | P1 | Step 4 | 1h |
| R-03 | No dead-letter queue | `backend/src/workers/settings.py` | P1 | Step 4 | 2h |
| R-04 | `/health` returns hardcoded OK | `backend/src/api/routes/health.py:16` | P0 | Step 4 | 1h |
| R-05 | Missing `/livez` endpoint | `backend/src/api/routes/health.py` | P1 | Step 4 | 30min |
| R-06 | Missing `/readyz` (DB+Redis ping) | `backend/src/api/routes/health.py` | P0 | Step 4 | 1h |
| R-07 | No global cascade kill-switch | `backend/src/services/scheduler.py` | P2 | Step 5 | 4h |
| R-08 | No defined SLOs | new `docs/slo.md` | P2 | Step 5 | 2h |
| R-09 | No idempotency keys on POST endpoints | per-route headers | P2 | Step 5 | 1d |
| R-10 | No SIGTERM drain loop on FastAPI | `backend/src/api/main.py:26-36` | P1 | Step 4 | 2h |
| R-11 | ARQ SIGTERM handler not wired | `backend/src/workers/settings.py` | P1 | Step 4 | 1h |

## §III.C Data integrity gaps (Gate 5)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| D-01 | No DB backup script | new `scripts/backup_db.sh` | P0 | Step 4 | 2h |
| D-02 | No restore drill | new `docs/runbooks/restore.md` | P0 | Step 4 | 1h |
| D-03 | Ghost-detection writer absent | `backend/src/services/ghost_detection.py::transition` is pure; no caller | P1 | Step 1.5 carry → Step 3 | 3h |
| D-04 | `PRAGMA foreign_keys=ON` not enforced per-conn | `backend/src/repositories/database.py:25-30` | P1 | Step 4 | 30min |
| D-05 | No encryption-at-rest for SQLite | infra | P2 | Step 4 | 2h |
| D-06 | Migration up/down/up CI check missing | `.github/workflows/backend.yml` | P1 | Step 4 | 1h |
| D-07 | No dedup audit query in monitoring | `scripts/audit_dedup.py` | P3 | Step 5 | 30min |

## §III.D Compliance gaps (Gate 6 — LAUNCH BLOCKERS)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| C-01 | ICO registration not done | external | P0 | Step 5 | 15min + £40 |
| C-02 | No `docs/legal/privacy.md` | new | P0 | Step 5 | 1d |
| C-03 | No `docs/legal/terms.md` | new | P0 | Step 5 | 1d |
| C-04 | No `docs/legal/cookie_policy.md` | new | P0 | Step 5 | 4h |
| C-05 | No `docs/legal/data_retention.md` | new | P0 | Step 5 | 2h |
| C-06 | No cookie consent banner | frontend new | P0 | Step 5 | 4h |
| C-07 | `DELETE /api/profile/me` cascades absent | `backend/src/api/routes/profile.py` | P0 | Step 5 | 4h |
| C-08 | `GET /api/profile/me/export` (Article 15) absent | new route | P0 | Step 5 | 4h |
| C-09 | LLM provider disclosure absent | `docs/legal/privacy.md` + UI banner | P0 | Step 5 | 2h |
| C-10 | AI-Act CV-scoring disclosure absent | UI + privacy notice | P0 | Step 5 | 2h |
| C-11 | LICENSE absent | repo root | P1 | Step 4 | 5min |
| C-12 | SECURITY.md absent | repo root | P1 | Step 4 | 30min |
| C-13 | CODE_OF_CONDUCT.md absent | repo root | P3 | Step 5 | 15min |
| C-14 | ASA marketing audit on landing page | `frontend/src/app/page.tsx` | P1 | Step 5 | 2h |

## §III.E Ops gaps (Gate 7)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| O-01 | `.github/workflows/` empty | new files | P0 | Step 4 | 1d |
| O-02 | No backend Dockerfile | new `backend/Dockerfile` | P0 | Step 4 | 4h |
| O-03 | No frontend Dockerfile | new `frontend/Dockerfile` | P0 | Step 4 | 4h |
| O-04 | No `.dockerignore` (backend + frontend) | new | P0 | Step 4 | 30min |
| O-05 | No `docker-compose.yml` for local dev | new | P1 | Step 4 | 2h |
| O-06 | No deploy platform config | `vercel.json` / `railway.toml` / `fly.toml` | P0 | Step 4 | 1d |
| O-07 | No secret manager integration | external | P1 | Step 5 | 1d |
| O-08 | No process manager config | systemd / PM2 | P2 | Step 5 | 2h |
| O-09 | No Prometheus `/metrics` endpoint | `backend/src/api/routes/metrics.py` | P1 | Step 4 | 4h |
| O-10 | No Sentry SDK | backend + frontend | P1 | Step 4 | 4h |
| O-11 | No OpenTelemetry tracing | backend | P2 | Step 5 | 1d |
| O-12 | No alerting config | external (PagerDuty / Better Stack) | P1 | Step 5 | 4h |
| O-13 | No automated smoke test post-deploy | `scripts/post_deploy_smoke.sh` | P1 | Step 4 | 2h |
| O-14 | No rollback procedure documented | `docs/runbooks/rollback.md` | P1 | Step 4 | 2h |
| O-15 | Logging not centralized (file only) | external aggregator | P2 | Step 5 | 4h |

## §III.F Developer Experience gaps (Gate 8)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| X-01 | No mypy in CI | `.github/workflows/backend.yml` | P1 | Step 4 | 30min |
| X-02 | No pyright/tsc in CI | `.github/workflows/frontend.yml` | P1 | Step 4 | 30min |
| ~~X-03~~ | ~~No `eslint-plugin-jsx-a11y`~~ | ~~`frontend/eslint.config.mjs`~~ | ~~P1~~ | ~~Step 2~~ | ✅ **closed Step 2 @ `9868877`** |
| X-04 | No ADRs/ directory | new `docs/adr/` | P2 | Step 5 | ongoing |
| X-05 | No commit-msg hook (conventional commits) | `.pre-commit-config.yaml` | P2 | Step 4 | 30min |
| X-06 | No `docs/onboarding.md` walkthrough | new | P3 | Step 5 | 2h |
| X-07 | No CodeOwners file | `.github/CODEOWNERS` | P3 | Step 5 | 15min |

## §III.G ML/AI gaps (Gate 9)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| M-01 | No LLM cost cap | `backend/src/services/profile/llm_provider.py` | P0 | Step 4 | 4h |
| M-02 | No per-key spend tracking | `backend/src/services/profile/llm_provider.py` | P1 | Step 4 | 2h |
| M-03 | No prompt versioning | `backend/src/services/profile/cv_parser.py` | P2 | Step 5 | 4h |
| M-04 | No eval suite for prompts | new `backend/evals/` | P2 | Step 5 | 1d |
| M-05 | LLM cost telemetry not aggregated | `backend/src/utils/telemetry.py` | P2 | Step 5 | 2h |
| M-06 | No CV→LLM privacy disclosure UI | `frontend/src/components/profile/CVUpload.tsx` | P0 | Step 5 | 1h |

## §III.H Frontend / UX gaps (Gate 10)

> **Note:** These overlap with the Step 2 plan (`docs/step_2_plan.md`). Listed here for completeness; closure tracked in Step 2.

| # | Gap | Severity | Step | Effort |
|---|---|---|---|---|
| ~~U-01~~ | ~~Zero frontend tests~~ | ~~P0~~ | ~~Step 2 (Cohort A)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-02~~ | ~~No `error.tsx` / `not-found.tsx` / `middleware.ts`~~ | ~~P0~~ | ~~Step 2 (Cohort A)~~ | ✅ **closed Step 2 @ `9868877`** (R-3 hotfix) |
| ~~U-03~~ | ~~No auth guard (silent 401)~~ | ~~P0~~ | ~~Step 2 (Cohort A+C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-04~~ | ~~No logout button~~ | ~~P0~~ | ~~Step 2 (Cohort C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-05~~ | ~~ScoreRadar prop-name fragility~~ | ~~P1~~ | ~~Step 2 (Cohort B)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-06~~ | ~~JobCard / JobDetail enrichment fields hidden (~10)~~ | ~~P0~~ | ~~Step 2 (Cohort B+C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-07~~ | ~~FilterPanel missing 8 controls + hybrid toggle~~ | ~~P0~~ | ~~Step 2 (Cohort B)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-08~~ | ~~Profile version history UI absent~~ | ~~P0~~ | ~~Step 2 (Cohort C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-09~~ | ~~JSON Resume export button absent~~ | ~~P0~~ | ~~Step 2 (Cohort C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-10~~ | ~~ESCO display layer absent~~ | ~~P1~~ | ~~Step 2 (Cohort C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-11~~ | ~~Apply CTA doesn't sync to pipeline~~ | ~~P0~~ | ~~Step 2 (Cohort C)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-12~~ | ~~No SEO / OG / JSON-LD JobPosting~~ | ~~P0~~ | ~~Step 2 (Cohort D)~~ | ✅ **closed Step 2 @ `9868877`** (R-1 XSS hotfix bonus) |
| ~~U-13~~ | ~~No sitemap.xml / robots.txt~~ | ~~P1~~ | ~~Step 2 (Cohort D)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-14~~ | ~~No TanStack Query caching~~ | ~~P1~~ | ~~Step 2 (Cohort D)~~ | ✅ **closed Step 2 @ `9868877`** |
| ~~U-15~~ | ~~No 429 retry-after toast~~ | ~~P1~~ | ~~Step 2 (Cohort D)~~ | ✅ **closed Step 2 @ `9868877`** |

## §III.I Performance gaps (Gate 2)

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| P-01 | No load test ever run | `wrk` or `hey` script | P1 | Step 4 | 2h |
| P-02 | No Lighthouse benchmark in CI | `.github/workflows/lighthouse.yml` | P1 | Step 4 | 2h |
| P-03 | No bundle size budget | `frontend/next.config.ts` | P1 | Step 4 | 1h |
| P-04 | No `EXPLAIN QUERY PLAN` audit | new `scripts/explain_queries.py` | P2 | Step 5 | 4h |
| P-05 | Dashboard double-fetch (Step 2 audit) | `backend/src/api/routes/jobs.py` | P2 | post-Step-2 backend follow-up | 2h |
| P-06 | No query-result caching layer | Redis cache | P2 | Step 5 | 1d |

---

## §III.K API timeout matrix

> **Why this section exists.** Timeouts are not one knob — they're four layers, each with its own failure mode. A request that hangs at any layer holds an event-loop slot, blocks downstream work, and burns LLM/DB connections. Without explicit deadlines at every layer, a single slow upstream (e.g., Indeed at 60s) cascades into queue starvation.

| Layer | What it bounds | Current state | Target | Risk if absent |
|---|---|---|---|---|
| **HTTP client (aiohttp)** | Outbound calls to job sources, LLM providers, Apprise | `REQUEST_TIMEOUT=30` set globally in `backend/src/core/settings.py`; per-source override absent | Per-source `total + connect + sock_read` timeouts | Single hung source blocks the whole `asyncio.gather()` |
| **FastAPI request** | Inbound API request lifetime | None — Uvicorn defaults to no per-request timeout | 30s default, 5s for `/livez`/`/readyz`, 60s for upload routes | Slow client / pathological query holds worker indefinitely |
| **DB query (SQLite/aiosqlite)** | Single SQL statement | `busy_timeout=5000ms` set (`database.py:27`) — but only for *lock acquisition*, not query execution | Per-query deadline via `asyncio.wait_for()` wrapper at the repo layer | Pathological query (missing index, full scan) blocks event loop |
| **LLM call (Gemini/Groq/Cerebras)** | Provider response | Provider-default (~60s); no explicit override in `llm_provider.py` | 25s with retry-on-timeout fallback to next provider | Hung LLM call cascades into request timeout, holds quota |
| **Background task (ARQ)** | Worker job lifetime | None — ARQ default ~30 min | Per-task explicit `timeout` in `WorkerSettings.functions` | OOM-stuck task holds worker slot forever |

| # | Gap | File:line | Severity | Step | Effort |
|---|---|---|---|---|---|
| T-01 | No per-source HTTP timeout overrides | `backend/src/sources/base.py` | P1 | Step 4 | 1h |
| T-02 | No FastAPI request-lifetime timeout middleware | `backend/src/api/main.py` | P0 | Step 4 | 1h |
| T-03 | No per-query DB deadline wrapper | `backend/src/repositories/database.py` | P1 | Step 4 | 2h |
| T-04 | LLM calls use provider default (~60s) | `backend/src/services/profile/llm_provider.py:18-53` | P0 | Step 4 | 1h |
| T-05 | LLM retry-on-timeout falls through to next provider | `backend/src/services/profile/llm_provider.py` | P1 | Step 4 | 2h |
| T-06 | ARQ worker tasks have no timeout (covered as R-01) | `backend/src/workers/settings.py:88-96` | P0 | Step 4 | 1h |
| T-07 | No graceful per-source timeout in scheduler tick | `backend/src/services/scheduler.py` | P2 | Step 4 | 2h |
| T-08 | No ChromaDB query timeout | `backend/src/services/vector_index.py` | P2 | Step 4 | 1h |

**How to measure (post-Step-4).**
```bash
# T-02 smoke: pathological client that stalls mid-stream
python scripts/timeout_smoke.py --layer=fastapi --target=/api/jobs --stall=60s
# T-04 smoke: mock Gemini hanging
GEMINI_MOCK_LATENCY=120 python -c "from src.services.profile.llm_provider import LLMProvider; LLMProvider().extract_skills('x')"
# Should fall through to Groq within 25s, not 120s
```

**Industry baseline.** AWS API Gateway default is 29s (TCP keepalive); Cloudflare Workers cap at 30s; Stripe webhooks expect 200 within 10s. The "30 seconds at every layer" rule is the cloud-native consensus — anything longer is a slow-leak waiting to be a queue-starvation incident.

---

## §III.L Failure scenario catalog

> **Why this section exists.** Mechanisms (retries, breakers, DLQs) are necessary but not sufficient. The right question is *"when X dies, what specifically happens?"* Each scenario below names a single concrete failure, the **expected behaviour**, the **current behaviour**, and the **gap to close**.

### Scenario F-01 — Redis dies (ARQ broker unreachable)

- **Trigger:** Network partition or Redis container OOM.
- **Expected:** ARQ producer at `enqueue_job()` returns a typed `BrokerUnavailableError`; HTTP request returns `503 Service Unavailable` with `Retry-After: 30`; user sees "search queued — try again in 30s" toast; existing search jobs continue from in-memory state until restart.
- **Current:** ARQ producer raises a generic exception; FastAPI returns 500; user sees "Search failed" with no retry guidance.
- **Gap:** F-01 — typed `BrokerUnavailableError` + `503 + Retry-After` mapping in `enqueue_job()` and the global exception handler. **Severity P1 / Step 4 / 2h.**

### Scenario F-02 — Gemini quota exhausted

- **Trigger:** Free-tier daily quota exceeded (15 RPM / 1500 RPD on Flash).
- **Expected:** `LLMProvider` catches `QuotaExceededError`, immediately tries Groq, then Cerebras; response succeeds with logged `provider_used: groq`; cost telemetry counts the failed Gemini call; user sees no error.
- **Current:** Fallback chain exists at `llm_provider.py:18-53` and works for generic exceptions, but doesn't differentiate quota-exceeded from rate-limit from network error — all collapse into "try next."
- **Gap:** F-02 — typed `QuotaExceededError` / `RateLimitError` / `NetworkError` separation in `llm_provider.py`; emit telemetry tagged by error class. **Severity P2 / Step 5 / 2h.**

### Scenario F-03 — All 3 LLM providers down

- **Trigger:** Coincident outage (rare but observed during global Anthropic / OpenAI incidents).
- **Expected:** `LLMProvider` raises typed `AllProvidersExhaustedError`; CV parsing route returns `503` with "service degraded — your CV will be parsed when providers recover" message; CV bytes stored, retried by background job after 5 min.
- **Current:** Raises generic `RuntimeError`; CV upload returns 500; user must re-upload.
- **Gap:** F-03 — typed exception + degraded-mode persistence (store CV blob, retry queue). **Severity P1 / Step 4 / 4h.**

### Scenario F-04 — ChromaDB index corrupted

- **Trigger:** Power loss mid-write or disk-full during embedding upsert.
- **Expected:** `VectorIndex.query()` catches the exception, logs a structured warning, and `retrieve_for_user()` falls back to keyword-only retrieval; UI shows hybrid mode unavailable; nightly job rebuilds the index.
- **Current:** `is_hybrid_available()` at `retrieval.py:124-130` does a try/except probe — partially handles this, but no rebuild trigger.
- **Gap:** F-04 — alert on `hybrid_unavailable` metric; nightly rebuild script. **Severity P2 / Step 5 / 4h.**

### Scenario F-05 — SQLite locked > 5s (busy_timeout exhausted)

- **Trigger:** Long-running write (e.g., bulk insert during search) blocks reads beyond `busy_timeout=5000ms`.
- **Expected:** Read path returns `503` with retry-after 1s (eventually consistent for jobs catalog); write path retries via `asyncio.sleep(jitter) + retry` up to 3 times.
- **Current:** WAL mode (`database.py:26`) makes this rare for read-heavy load. But concurrent writes from API + ARQ worker can serialize — no explicit retry on `OperationalError`.
- **Gap:** F-05 — repo-layer retry-with-jitter on `OperationalError: database is locked`. **Severity P1 / Step 4 / 2h.**

### Scenario F-06 — ARQ worker OOM (memory leak / large CV batch)

- **Trigger:** Worker process exceeds container memory limit and is OOM-killed by the kernel.
- **Expected:** ARQ marks the job as failed; supervisor restarts the worker; in-flight job moves to DLQ for inspection; alert fires.
- **Current:** No DLQ (R-03), no supervisor (O-08), no alert (O-12). The job is silently lost.
- **Gap:** F-06 — closed by R-03 + O-08 + O-12. **Severity P0 / Step 4 / included.**

### Scenario F-07 — Source returns 403 Forbidden / IP-banned

- **Trigger:** LinkedIn / Indeed scraper hits anti-bot system; receives 403 + Cloudflare challenge.
- **Expected:** Circuit breaker opens after 5 consecutive 403s; source skipped for 300s cooldown; alert fires after 3 consecutive cooldown cycles (15 min); operator notified to rotate user-agent / proxy.
- **Current:** Per-source breaker exists (`circuit_breaker.py:39`) and trips on consecutive failures; no alert wiring; no operator playbook.
- **Gap:** F-07 — alert on `breaker_open_count > 3` per source; runbook for `docs/runbooks/source_403.md`. **Severity P2 / Step 5 / 2h.**

### Scenario F-08 — User uploads 50MB CV (or malicious file)

- **Trigger:** Adversarial or accidental large upload; or a `.exe` renamed `.pdf`.
- **Expected:** Multipart parser rejects > 5MB at the FastAPI layer; MIME-type sniffing rejects non-PDF/DOCX; user sees "file too large" or "unsupported type" toast; no parser CPU consumed.
- **Current:** No size limit, no MIME whitelist (S-19, S-20). pdfplumber would happily try to parse 50MB and exhaust memory.
- **Gap:** F-08 — closed by S-19 + S-20. **Severity P0 / Step 4 / included.**

### Scenario F-09 — Session-cookie HMAC fails (tampering / secret rotation)

- **Trigger:** Attacker forges a cookie OR `SESSION_SECRET` rotates and old cookies are invalidated.
- **Expected:** `auth_deps.require_user` raises `401`; cookie cleared from response; user sees "session expired — please log in"; redirect to `/login?next=...`.
- **Current:** `itsdangerous` raises `BadSignature` which is caught and returns 401 — but no cookie-clear, no redirect signal in the body.
- **Gap:** F-09 — wire `Set-Cookie: job360_session=; Max-Age=0` on 401; frontend middleware reads this signal and routes to login. **Severity P1 / Step 2 / 30min.**

### Scenario F-10 — Disk full (`backend/data/`)

- **Trigger:** Logs + DB + ChromaDB exceed disk quota.
- **Expected:** Log rotation kicks in (active per `log_rotation_check.py`); DB writes return `OperationalError`; alert fires at 80% usage; auto-purge of `jobs > 30d` runs to free space.
- **Current:** Log rotation OK; auto-purge OK; **no disk monitoring**, no 80% alert. First sign of trouble = a write failure in production.
- **Gap:** F-10 — `df` check in `/readyz`; alert on disk > 80% via Prometheus + Alertmanager. **Severity P1 / Step 4 / 2h.**

### Scenario F-11 — Migration applied on prod but rollback needed

- **Trigger:** Step-N migration ships but breaks prod read pattern; need to revert without data loss.
- **Expected:** `python -m migrations.runner down <version>` runs the paired `.down.sql`; data backfill (if any) restores; app boots on the older schema; postmortem documents the trigger.
- **Current:** All 12 migrations have paired `.down.sql` (✅, audit-confirmed). No rollback drill ever performed; no documented runbook.
- **Gap:** F-11 — quarterly rollback drill; `docs/runbooks/migration_rollback.md`. **Severity P1 / Step 4 / 1h.**

### Scenario F-12 — Frontend deploy succeeds, backend deploy fails (version skew)

- **Trigger:** Vercel deploys frontend; Railway backend deploy errors mid-rollout.
- **Expected:** Frontend feature-detects new fields on `JobResponse`; gracefully renders without them when backend is on older version; user sees no broken UI.
- **Current:** Frontend reads typed fields directly; missing field = `undefined` rendered (or crash on null deref).
- **Gap:** F-12 — defensive nullish-coalescing on every new-field render; backend exposes `/api/version` for diagnosis. **Severity P2 / Step 4 / 2h.**

### Failure scenario summary

| # | Scenario | Severity | Step | Effort |
|---|---|---|---|---|
| F-01 | Redis dies | P1 | Step 4 | 2h |
| F-02 | Gemini quota exhausted | P2 | Step 5 | 2h |
| F-03 | All 3 LLM providers down | P1 | Step 4 | 4h |
| F-04 | ChromaDB index corrupted | P2 | Step 5 | 4h |
| F-05 | SQLite locked > 5s | P1 | Step 4 | 2h |
| F-06 | ARQ worker OOM | P0 | Step 4 | included (R+O) |
| F-07 | Source 403/IP-banned | P2 | Step 5 | 2h |
| F-08 | 50MB / malicious upload | P0 | Step 4 | included (S-19/20) |
| ~~F-09~~ | ~~Cookie HMAC fails~~ | ~~P1~~ | ~~Step 2~~ | ✅ **closed Step 2 @ `9868877`** (R-2 + R-3 hotfix) |
| F-10 | Disk full | P1 | Step 4 | 2h |
| F-11 | Migration rollback drill | P1 | Step 4 | 1h |
| F-12 | Version skew (FE/BE) | P2 | Step 4 | 2h |

> **Reading:** 12 named failure scenarios, 8 actionable items (4 closed by other gaps in §III). The point isn't perfection — it's that *each scenario has a name and a code path*. When the on-call playbook says "F-04 fired," everyone knows what to do.

---

## §III.J Gap totals

| Gate | P0 | P1 | P2 | P3 | Total |
|---|---|---|---|---|---|
| Security | 7 | 11 | 0 | 0 | 18 |
| Reliability | 3 | 5 | 3 | 0 | 11 |
| Data | 2 | 3 | 2 | 1 | 8 |
| Compliance | 9 | 4 | 1 | 0 | 14 |
| Ops | 5 | 7 | 3 | 0 | 15 |
| Dev-Experience | 0 | 3 | 2 | 2 | 7 |
| ML/AI | 2 | 1 | 3 | 0 | 6 |
| UX | 9 | 6 | 0 | 0 | 15 |
| Performance | 0 | 3 | 2 | 0 | 5 |
| Timeouts (§III.K) | 3 | 2 | 3 | 0 | 8 |
| Failure scenarios (§III.L) | 0 | 6 | 4 | 0 | 10 (4 covered by other gaps) |
| **Total** | **40** | **51** | **23** | **3** | **117** |

> **Reading (post-Step-2 @ `9868877`):** Step 2 closed 16 items (U-01..U-15 + X-03 + F-09). Remaining: **24 P0 / 47 P1 / 23 P2 / 3 P3 = 97 items**. P0 distribution now: Compliance 9 (legal floor), Security 7 (headers + audits), Ops 5 (CI/Docker/deploy/health), Reliability 3 (worker timeouts + readyz), Timeouts 3 (FastAPI + LLM + worker). Realistic launch path: ~3-4 weeks of focused execution remaining.

---

# Part IV — Anti-Patterns This Codebase Must Avoid

These are the failure modes that look fine on day 1 and cause incident on day 30. The Step-1 bombshell (zero-valued dim fields shipped behind a green test suite) is example #1 of this codebase already living through one. Don't repeat the others.

### 1. "Tests pass therefore production-ready"

**Why it bites.** A passing test suite proves *what was tested*, not *what works*. Step 1's bombshell shipped 1,018 green tests around a serializer that hard-coded zeros. Tests checked field-presence, not value-presence.

**Prevention.** CLAUDE.md rule #21 (already in place). Add value-presence assertions in Cohort B of every batch. Treat aggregate score in Part II as the actual gate, not test count.

### 2. "Secret in `.env` is fine for prod"

**Why it bites.** No rotation policy, no audit log, no per-environment isolation, no revocation when an employee leaves. First credential leak = full system compromise.

**Prevention.** Step 4 must wire a secret manager (Doppler / Vault / AWS Secrets Manager). All secrets ≥30-day rotation. Audit log on every `Get` operation.

### 3. "No CI because we trust ourselves"

**Why it bites.** A regression slips into main on Friday; you don't notice until Monday morning; meanwhile, a friend opened the page on Sunday and saw a broken radar. First-impression damage is unrecoverable.

**Prevention.** Step 4 must establish `.github/workflows/{backend,frontend,security}.yml`. PR checks must run before merge. Branch protection on main.

### 4. "Health endpoint always returns OK"

**Why it bites.** Load balancer keeps routing traffic to a broken backend; you discover via user reports, not alerts. A bad ARQ worker keeps "looking healthy" while no jobs are processed.

**Prevention.** Split into `/livez` (process up) + `/readyz` (DB + Redis + ARQ ping). LB routes only to `/readyz=200`. Step 4.

### 5. "Manual deploy via SSH"

**Why it bites.** Irreproducible state drift. Friday-evening hotfix that doesn't make it into git. Week 3 bug where prod and dev diverge mysteriously.

**Prevention.** Every deploy goes through CI. No SSH on prod hosts. Use platform deploy hooks (Vercel push-to-deploy, Railway GitHub integration, Fly `flyctl deploy`).

### 6. "We'll add observability later"

**Why it bites.** "Later" = post-incident, when you've already lost the data you needed. A user reports "my CV upload broke" 3 days ago; you have logs for 2 days.

**Prevention.** Sentry + Prometheus + structured JSON logs ship in Step 4, not Step 5. Even minimal observability beats none.

### 7. "GDPR can wait until we have users"

**Why it bites.** Legal exposure starts at user #1. The £40 ICO fee + a privacy notice + a delete-my-data endpoint take 1 day total. Skipping creates an asymmetric risk: pay £40 to be safe, OR pay £8,500 (max ICO fine + bad-PR damage) to discover the rule.

**Prevention.** Gate 6 is a launch blocker. Step 5 ships ICO + privacy + terms + cookie banner + DELETE endpoint *before* any non-you user is invited.

### 8. "Lighthouse 90 means we're fast"

**Why it bites.** Lighthouse desktop on your dev machine ≠ mobile on 3G in a London commuter train. The 75th-percentile user has 3-second TTI; you tested 0.5s and feel proud.

**Prevention.** Lighthouse CI runs in `--preset=mobile` with throttling. Field data via Web Vitals beats lab data. Step 4.

### 9. "Only one provider for LLM/email/payments"

**Why it bites.** Single point of failure. Gemini outage = no CV parsing. SES regional issue = no notifications. Stripe ban = no revenue.

**Prevention.** Job360 already has 3-provider LLM chain (✅). Email needs primary (SES) + fallback (Mailgun/SendGrid). Step 5.

### 10. "Migration up only — we'll never roll back"

**Why it bites.** Every prod incident eventually demands one. "Just roll back the migration" is the calmest sentence in any postmortem; "we don't have a down migration" is the worst.

**Prevention.** CLAUDE.md rule (proposed): every up migration MUST have a tested down. CI checks both. Job360 has all 12 today (✅) — keep this discipline.

---

# Part V — Manual Testing Playbook

This is the **golden path** the user walks after every batch merges. Time-box: <15 minutes. Executes the full user journey end-to-end. If any step breaks, the batch is not green regardless of what `make verify-*` says.

## §V.A Pre-test setup (1 min)

1. Open Chrome **incognito**, no autofill, clear cache
2. Resize to iPhone SE viewport (375×667) via DevTools
3. Throttle network to "Fast 3G" via DevTools → Network
4. Open Console + Network panels — leave open throughout

## §V.B Golden path (10 min)

| Step | Action | Expected | Fail-flag |
|---|---|---|---|
| 1 | Land on `/` | Hero + value prop visible above fold; CTAs render | Layout broken / no CTA |
| 2 | Click "Get Started" | Routes to `/register` | 404 or redirect loop |
| 3 | Register with `test+TIMESTAMP@example.com` / 12-char password | 200 + redirect to `/dashboard` (or `/profile` for first-time) | 4xx/5xx, blank page |
| 4 | Upload sample CV (PDF, ~200KB) | Skeleton → parsed CV view; LLM fields populated | Spinner forever / silent fail |
| 5 | Verify ESCO normalised skills shown (post-Step-2) | "Python (raw: py, python3)" visible | Raw skills only |
| 6 | Click "Run Search" | Status badge "Running…" → progress dots | No progress feedback |
| 7 | Wait <30s → see ≥10 JobCards | Cards render with score, dim badges, salary, staleness | Empty list / zeros |
| 8 | Open highest-scored JobCard | JobDetail with full radar (8 dims, all >0), enrichment fields, ApplyButton | Zero radar / redirect-only Apply |
| 9 | Click "Save to Pipeline" | Toast confirms; redirect to KanbanBoard | No feedback / pipeline empty |
| 10 | Drag (or button-advance) to "Interview" | Card moves; toast confirms | Drag broken / state lost |
| 11 | Open `/profile` | ESCO + version history drawer + JSON Resume button visible | Missing UI |
| 12 | Click "History" → restore an older version | Toast + page re-renders with old data | No revert / no toast |
| 13 | Click "Export JSON Resume" | `resume.json` downloads with valid schema | No download / corrupt JSON |
| 14 | Click logout | Routes to `/login`, session cookie cleared | Still logged in |
| 15 | Visit `/dashboard` (anonymous) | Redirected to `/login?next=/dashboard` (middleware) | Renders empty / silent 401 |
| 16 | Re-login | Lands on dashboard with prior state preserved | State lost |

## §V.C Edge-case probes (4 min)

| # | Probe | Expected |
|---|---|---|
| E-01 | Open on a different network (phone hotspot) | Loads in <3s; no CORS errors |
| E-02 | View source on `/jobs/1` | `<script type="application/ld+json">` with `JobPosting` |
| E-03 | Paste `https://job360.app/jobs/1` into LinkedIn composer | Preview shows job title + description + image |
| E-04 | Hit `POST /api/search` 4 times rapidly | 4th returns 429 + sonner toast "rate limit" |
| E-05 | Upload malformed CV (.txt with garbage) | Graceful error toast, no 500 |
| E-06 | Trip a source breaker (manually) | Dashboard shows source as "degraded", not failure |
| E-07 | Toggle theme button | Switches dark↔light; persists across reload |
| E-08 | Tab away for 1 hour, return | Dashboard re-fetches (stale-while-revalidate) |

## §V.D Friend dogfood (separate session, post-Step-2)

Find 5 people. Send them the URL. Observe (don't help). Note:
- How many seconds to "first scored job" (target <60)
- Where they pause / hover / look confused
- What questions they ask
- 3 of 5 must complete the golden path unassisted

**One canonical question:** "What does this app do?" — if they can't explain it back after 30s on the landing page, your value prop is broken.

---

# Part VI — `make verify-production` Aggregate Spec

This is the eventual single command that proves "we are launch-ready." Lands in **Step 4**. Composition:

```makefile
# ---------------------------------------------------------------------------
# Production-readiness aggregate gate.
#
# Returns 0 only when all 10 gates are green. Each sub-gate is a
# separate make target so failures are diagnosable.
# ---------------------------------------------------------------------------

verify-production: \
  verify-functional \
  verify-performance \
  verify-security \
  verify-reliability \
  verify-data \
  verify-compliance \
  verify-ops \
  verify-devexp \
  verify-ml \
  verify-ux
	@echo "==> verify-production: ALL 10 GATES GREEN"
	@mkdir -p .claude
	@git rev-parse HEAD > .claude/production-verified.txt

verify-functional:
	@echo "==> Gate 1: pytest + vitest + e2e + migration round-trip"
	cd backend && python -m pytest tests/ -q -p no:randomly --tb=short
	cd frontend && npm run test:unit && npm run test:e2e
	bash scripts/migration_roundtrip.sh

verify-performance:
	@echo "==> Gate 2: load test + Lighthouse mobile + bundle size"
	bash scripts/load_test.sh
	npx lighthouse http://localhost:3000/dashboard --preset=mobile --quiet --chrome-flags='--headless' --output=json --output-path=/tmp/lh.json
	python scripts/check_lighthouse.py /tmp/lh.json --min-perf=90 --min-a11y=95
	bash scripts/check_bundle_size.sh --max-kb=200

verify-security:
	@echo "==> Gate 3: pip-audit + npm audit + gitleaks + bandit + headers"
	cd backend && pip-audit --strict
	cd frontend && npm audit --audit-level=high --production
	gitleaks detect --no-banner
	cd backend && bandit -r src
	bash scripts/check_security_headers.sh

verify-reliability:
	@echo "==> Gate 4: SLO doc + worker timeouts + healthz split"
	@test -f docs/slo.md
	python scripts/check_worker_config.py
	bash scripts/check_health_endpoints.sh

verify-data:
	@echo "==> Gate 5: backup-restore drill + FK enforcement + dedup audit"
	bash scripts/backup_restore_drill.sh
	python scripts/check_fk_enforcement.py
	python scripts/audit_dedup.py

verify-compliance:
	@echo "==> Gate 6: ICO + privacy + terms + cookie + erasure + LICENSE"
	@test -f docs/legal/ico_certificate.pdf
	@test -f docs/legal/privacy.md
	@test -f docs/legal/terms.md
	@test -f docs/legal/cookie_policy.md
	@test -f docs/legal/data_retention.md
	@test -f LICENSE
	@test -f SECURITY.md
	bash scripts/check_erasure_endpoint.sh

verify-ops:
	@echo "==> Gate 7: CI present + Docker reproducible + smoke + rollback"
	@test -d .github/workflows && test "$$(ls .github/workflows/*.yml | wc -l)" -ge 3
	docker build -t job360-test backend/ && docker build -t job360-test2 backend/
	bash scripts/post_deploy_smoke.sh staging
	@test -f docs/runbooks/rollback.md

verify-devexp:
	@echo "==> Gate 8: TTFR + docs + lint + type-check"
	@test -f CONTRIBUTING.md && test -f ARCHITECTURE.md && test -f docs/onboarding.md
	cd backend && python -m mypy --strict src
	cd frontend && npm run type-check && npm run lint

verify-ml:
	@echo "==> Gate 9: cost cap + provider fallback + lazy imports"
	python scripts/check_llm_cost_cap.py
	python scripts/check_llm_fallback_chain.py
	python scripts/check_lazy_imports.py

verify-ux:
	@echo "==> Gate 10: Playwright value-presence + a11y + mobile + share-preview"
	cd frontend && npm run test:e2e -- --grep "value-presence|a11y|mobile|share-preview"
```

---

# Part VII — Industry References

Every threshold + practice in this report cites a public source.

| Topic | Reference |
|---|---|
| SLOs, error budgets, postmortems | Google SRE Book, chapters 3-5 + 13-15 — `https://sre.google/sre-book/` |
| 5 reliability pillars | AWS Well-Architected Framework — `https://aws.amazon.com/architecture/well-architected/` |
| Application security (ASVS Level 1-3) | OWASP Application Security Verification Standard — `https://owasp.org/www-project-application-security-verification-standard/` |
| Top 10 web app risks | OWASP Top 10 (2021) — `https://owasp.org/Top10/` |
| Cloud-native app principles | The Twelve-Factor App — `https://12factor.net/` |
| API design (idempotency, versioning) | Stripe API best-practices blog — `https://stripe.com/blog/idempotency` |
| Production-readiness checklist | Vercel production checklist — published in their docs |
| Web performance baseline | Google Web Vitals — `https://web.dev/vitals/` |
| Accessibility minimum | WCAG 2.1 AA + UK Equality Act 2010 |
| Architecture Decision Records | Michael Nygard, "Documenting Architecture Decisions" (2011) |
| Conventional commits | `https://www.conventionalcommits.org/en/v1.0.0/` |
| GDPR (EU + UK) | UK ICO guides — `https://ico.org.uk/for-organisations/` |
| Data Protection Fee | UK ICO £40 registration — `https://ico.org.uk/for-organisations/data-protection-fee/` |
| AI governance | EU AI Act (2024) + UK AI white paper |
| Marketing claims | UK ASA CAP Code — `https://www.asa.org.uk/codes-and-rulings/advertising-codes.html` |
| Backup discipline | "3-2-1 backup rule" — SRE consensus |
| Containerization | Docker official multi-stage build docs |
| Health check pattern | Kubernetes liveness vs readiness probe docs |
| Usability heuristics | Nielsen Norman Group, "10 Usability Heuristics" |
| Eval framework for LLMs | LangChain Eval + OpenAI evals + Anthropic API best-practices |

---

# Part VIII — Roadmap to Production-Ready

The single one-page table the user can pin. Maps every gate's gap-closure to the canonical step from `docs/harness/ExecutionOrder.md`.

| Step | Surface | Gate(s) closed | Aggregate score post-step (estimated) |
|---|---|---|---|
| **Step 0** ✅ | Pre-flight (env, seed, baseline) | 8 (DX) | 4.2 → 4.5 |
| **Step 1** ✅ | Engine→API seam | 1 (Functional) | 4.5 → 5.0 |
| **Step 1.5** ✅ | Stabilisation + S3-MVP | 1, 5 (partial) | 5.0 → 5.3 |
| **Step 1.6** ✅ | Generator/reviewer contract | 8 (DX) | 5.3 → 5.4 |
| **Step 2** (next) | API→UI Seam | 1 (frontend tests), 8 (a11y eslint), 10 (UX) | 5.4 → 6.5 |
| **Step 3** | New endpoints (versions, ledger, dedup browse, settings UI) | 4 (partial — idempotency), 8 (ADRs), 9 (cost telemetry), 10 (settings + drag-drop) | 6.5 → 7.0 |
| **Step 4** | Ops hardening (CI, Docker, secrets, observability, security headers, health split, worker timeouts, backups, smoke, rollback) | **2, 3, 4, 5, 7, 8 fully**, 9 (cost cap) | 7.0 → 8.2 |
| **Step 5** | Launch readiness (ICO, privacy, terms, cookie banner, erasure, AI disclosure, alerting, friend dogfood) | **6 fully**, 9 (eval suite), 10 (5-friend test) | 8.2 → 9.0 |

**Production-ready milestone: end of Step 4** (aggregate ≥ 8 + no gate < 6 *except* Gate 6 which closes in Step 5). **Public launch milestone: end of Step 5** (all 10 gates ≥ 7, ICO certificate filed, friend cohort confirmed).

---

## Closing note

This report is **deliberately uncomfortable**. A 5.4/10 score on the day after Step 1.6 ships isn't a failing grade — it's an honest map. The engineering pillars (auth, IDOR, SQL safety, hermetic tests, lazy imports, migration discipline) are at production-tier already; this is rare for a one-person startup pre-launch. What's missing is the *containing system* (CI, ops, compliance, observability, frontend) — and that's exactly what Steps 2-5 are scoped to deliver.

**Update protocol after every batch:**
1. Append a row to §II.A score history
2. Mark closed gaps in §III with `~~strikethrough~~` (don't delete — trail of progress)
3. Update §VIII roadmap if any gate slips between steps
4. Re-run Part V manual playbook end-to-end; capture findings

Production isn't a milestone — it's a state the system maintains. This report is the rubric for staying there.

_Written 2026-04-26 against `main @ 106768f`. Living document — last update timestamp at top._
