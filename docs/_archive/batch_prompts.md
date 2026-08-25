# Batch Prompts — Pillar 3
<!-- doc: FROZEN -->

> **Worktrees already exist at absolute paths:**
> - Generator: `C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\generator`
> - Reviewer:  `C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\reviewer`
>
> **Do NOT delete these directories between batches.** Only the branches inside them rotate — the worktree dirs are permanent.
>
> ## Per-batch kickoff (run in *your* terminal, not inside either Claude session)
>
> For each batch `N` ∈ {2, 3, 4}, from the repo root:
>
> ```bash
> cd "C:/Users/Ranjith/OneDrive/Documents/job360"
> git -C .claude/worktrees/generator checkout -B pillar3/batch-N main
> git -C .claude/worktrees/reviewer  checkout -B pillar3/batch-N-review main
> ```
>
> Then open **two separate Claude Code sessions** — one `cd`d into the generator worktree, the other into the reviewer worktree. Do NOT reuse a single session for both roles; the reviewer must start with zero context from the generator.
>
> ## Sequential contract
>
> Run batches strictly in order. Never start Batch N+1 until:
>   1. Reviewer printed `REVIEW_COMPLETE pillar3/batch-N verdict=APPROVED`
>   2. Human merged `pillar3/batch-N` into `main`
>   3. `docs/harness/IMPLEMENTATION_LOG.md` has a completion entry for batch N
>   4. `docs/CurrentStatus.md` delta-audited for sections batch N touched
>
> **Status:** Batch 1 merged 2026-04-18 as commit `31124fa`. Batch 2 is next.

---

## Batch 2 — Multi-User Delivery Layer

### 2A. Generator prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\generator
on branch pillar3/batch-2.

Batch 1 is merged at commit 31124fa on main. Do NOT start until
docs/harness/IMPLEMENTATION_LOG.md has a Batch 1 completion entry (already there).

STEP 0 — LOCK THE CLEAN BASELINE (before any code change):
  cd backend
  python -m pytest tests/ --ignore=tests/test_main.py -q > /tmp/batch2_baseline.log 2>&1
  tail -5 /tmp/batch2_baseline.log

  Record pass/fail/skip counts at the top of docs/product/plans/batch-2-plan.md
  as "POST-BATCH-1 BASELINE". Any Batch 2 regression claim compares
  against this number.

MANDATORY pre-reading:
  1. docs/harness/IMPLEMENTATION_LOG.md              (confirm Batch 1 completion
                                              entry exists; your entry
                                              gets appended at the bottom)
  2. docs/CurrentStatus.md                   (ground truth post-Batch-1)
  3. docs/product/research/pillar_3_batch_2.md       (full blueprint)
  4. docs/product/research/pillar_3_report.md        (context — auth + multi-tenant
                                              reasoning lives here)
  5. CLAUDE.md rules #1–9
  6. backend/src/api/*                       (current single-user endpoints)

SKILLS (mandatory — Batch 2 has too many irreversible design choices to
skip brainstorming):
  1. superpowers:using-superpowers
  2. superpowers:brainstorming  ← REQUIRED FIRST.  Produce
     docs/product/plans/batch-2-decisions.md covering at minimum:
       - ARQ vs Celery vs RQ for the worker queue
       - Apprise vs Novu vs per-channel SDK
       - Polling vs SSE vs websockets for the feed
       - When to migrate to postgres (now vs Batch 3 vs later)
       - Session-based vs JWT auth
     Each decision: 2-3 options, pros/cons, RECOMMENDATION + reason.
  3. superpowers:writing-plans          → docs/product/plans/batch-2-plan.md
  4. superpowers:test-driven-development
  5. superpowers:subagent-driven-development
  6. superpowers:verification-before-completion

SCOPE:
  • Auth + multi-tenant schema migration (users, sessions, org/tenant
    tables; migrate existing single-user data as tenant_id=1)
  • user_feed SSOT table + FeedService
       jobs × user match_score, action, first_seen_by_user, last_refreshed
  • ARQ worker (or chosen queue) + Apprise (or chosen notifier) for
    per-user notifications — replaces per-installation env vars
  • 99% pre-filter cascade per pillar_3_batch_2.md §"Filter cascade"
    (before scoring: location, visa, seniority, hard-exclusions)
  • Channel config UI (frontend) — per-user email/Slack/Discord/webhook
    with test-send button

HARD CONSTRAINTS (additions to CLAUDE.md #1–9):
  • Existing single-user behavior MUST keep working for tenant_id=1 during
    migration — zero-downtime
  • New tables under migrations/ with forward+reverse SQL or alembic
  • No PII in logs (email, name, CV text)
  • Rate-limit per user, not global, for ARQ jobs

SUCCESS CRITERIA:
  ☐ Baseline tests still pass (your POST-BATCH-1 number)
  ☐ New tests: auth flow, tenant isolation (tenant A cannot read tenant
    B's jobs/actions/feed), feed-service correctness, pre-filter cascade,
    ARQ job idempotency, Apprise channel test-send
  ☐ docs/product/plans/batch-2-decisions.md + batch-2-plan.md committed
  ☐ docs/harness/IMPLEMENTATION_LOG.md completion entry drafted
  ☐ CLAUDE.md updated (new tables, new auth flow, new worker, new ports)
  ☐ Tenant isolation proven by a dedicated test class

COMMIT STYLE:
  • One logical change per commit, conventional-commit prefixes
  • Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>

HANDOFF:
  Push pillar3/batch-2, print "READY_FOR_REVIEW pillar3/batch-2 @ <hash>",
  STOP.

If you hit a genuinely ambiguous design decision not covered by the
decisions doc, write to docs/plans/batch-2-open-questions.md and continue
with your best call. Do not stop and ask.
```

### 2B. Reviewer prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\reviewer
on branch pillar3/batch-2-review.

Independent auditor. NEVER edit shipping code.

STEP 1. Pull the generator's branch into THIS worktree:
  git fetch origin pillar3/batch-2 && git reset --hard origin/pillar3/batch-2

STEP 2. Read:
  1. docs/product/research/pillar_3_batch_2.md
  2. docs/product/plans/batch-2-decisions.md         (scrutinize the 5 decisions)
  3. docs/product/plans/batch-2-plan.md
  4. CLAUDE.md rules #1–9

STEP 3. Skills: superpowers:using-superpowers + coderabbit:code-reviewer

STEP 4. AUDIT CHECKLIST (in addition to Batch 1's generic checks):

  Tenant isolation (highest-risk area)
  ☐ Every new query scopes by tenant_id — grep for SELECT/UPDATE/DELETE
    touching jobs/user_actions/applications/user_feed; verify
    WHERE tenant_id = ? appears
  ☐ No endpoint returns another tenant's data even with forged IDs
    (test exists + passes)
  ☐ CSV export scoped to tenant
  ☐ ARQ queue keys include tenant_id

  Data migration
  ☐ Existing single-user data migrated to tenant_id=1, nothing lost
  ☐ Forward+reverse migration scripts both run cleanly on a fresh DB

  Secret hygiene
  ☐ No CV text, no email, no name in log strings (grep log statements)
  ☐ Per-user tokens are hashed at rest

  Decision doc
  ☐ All 5 decisions have a recommendation + justification
  ☐ Implementation matches the recommendation (if not, surprise logged)

STEP 5. Write docs/harness/reviews/batch-2-review.md (same structure as Batch 1).

STEP 6. Commit review + print
  "REVIEW_COMPLETE pillar3/batch-2 verdict=<APPROVED|CHANGES>"

Do NOT edit shipping code. Do NOT merge. Do NOT push the reviewer branch.
```

---

## Batch 3 — Tiered Polling + Source Expansion

### 3A. Generator prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\generator
on branch pillar3/batch-3.

Batch 2 must be merged first.

STEP 0 — LOCK THE CLEAN BASELINE (before any code change):
  cd backend
  python -m pytest tests/ --ignore=tests/test_main.py -q > /tmp/batch3_baseline.log 2>&1
  tail -5 /tmp/batch3_baseline.log

  Record pass/fail/skip counts at the top of docs/product/plans/batch-3-plan.md
  as "POST-BATCH-2 BASELINE".

MANDATORY pre-reading:
  1. docs/harness/IMPLEMENTATION_LOG.md
  2. docs/CurrentStatus.md
  3. docs/product/research/pillar_3_batch_3.md
  4. backend/src/main.py                     (current cron/polling flow)
  5. backend/src/sources/base.py             (rate-limit contract)
  6. CLAUDE.md rule #8 (source count assertion)

SKILLS:
  1. superpowers:using-superpowers
  2. superpowers:writing-plans → docs/product/plans/batch-3-plan.md
  3. superpowers:test-driven-development
  4. superpowers:subagent-driven-development (5 new sources are
     independent units)
  5. superpowers:verification-before-completion

SCOPE:
  • Tiered polling scheduler — replaces the broken twice-daily cron
      ATS sources  : 60s  interval
      Reed API     : 5m
      Workday API  : 15m
      RSS feeds    : 15m
      Scrapers     : 60m
  • Conditional-fetch layer — ETag / Last-Modified support where sources
    expose it (HiringCafe pattern in pillar_3_batch_3.md §Conditional)
  • ADD 5 new sources:
      - Teaching Vacancies (gov.uk API)
      - GOV.UK Apprenticeships
      - NHS Jobs XML feed (replaces the RSS-ish source)
      - Rippling ATS
      - Comeet ATS
  • DROP 3 low-value sources:
      - YC Companies (covered by HN Jobs + Ashby)
      - Nomis (UK stats, not jobs)
      - FindAJob (dead endpoint per CurrentStatus.md §13)
  • Expand ATS slug catalog 104 → 500+ from the Feashliaa repo
    (per pillar_3_report.md §Slug sourcing)
  • Replace "newly_empty" flag with circuit breakers per source

HARD CONSTRAINTS:
  • CLAUDE.md Rule #8: update `len(SOURCE_REGISTRY) == N` assertion in
    tests/test_cli.py AND the expected source set — fail to do this and
    CI blows up
  • Net source count delta: +5 −3 = +2, so registry goes 48 → 50
  • Do not reintroduce datetime.now() in new sources (Batch 1 contract)
  • Rate-limits for new sources must be tested against their published
    policies (cite the page in the test comment)

SUCCESS CRITERIA:
  ☐ Baseline + Batch 1/2 tests still pass
  ☐ 5 new sources pass mocked-HTTP tests
  ☐ Tiered scheduler tested with freezegun / time-mock
  ☐ Circuit-breaker tested (open → half-open → closed transitions)
  ☐ Slug catalog expansion: test_cli.py passes with new count
  ☐ KPI check: bucket_accuracy_24h improves (measurement script from
    Batch 1 still works)
  ☐ IMPLEMENTATION_LOG.md completion entry drafted

HANDOFF: push, print READY_FOR_REVIEW, STOP.
```

### 3B. Reviewer prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\reviewer
on branch pillar3/batch-3-review.

Independent auditor. NEVER edit shipping code.

STEP 1. Pull the generator's branch into THIS worktree:
  git fetch origin pillar3/batch-3 && git reset --hard origin/pillar3/batch-3

STEP 2. Read:
  1. docs/product/research/pillar_3_batch_3.md
  2. docs/product/plans/batch-3-plan.md
  3. CLAUDE.md rule #8

STEP 3. Skills: coderabbit:code-reviewer + superpowers:using-superpowers.

STEP 4. AUDIT CHECKLIST:
  Source-count integrity
  ☐ SOURCE_REGISTRY count matches `_build_sources()` count
  ☐ RATE_LIMITS dict has an entry for every new source
  ☐ tests/test_cli.py assertion updated AND expected source set updated

  Tiered polling
  ☐ Scheduler respects per-tier intervals
  ☐ Conditional-fetch layer issues ETag / Last-Modified headers
  ☐ Circuit breaker recovers after half-open success
  ☐ No source can starve another (fairness test)

  Dropped sources
  ☐ Deleted source files + their tests + registry entries + rate-limit
    entries (grep for dangling references)

  New sources
  ☐ Each new source has ≥3 tests (happy path, empty result, error)
  ☐ Each one honors `_is_uk_or_remote()` filter
  ☐ Each one passes `SearchConfig` through properly

STEP 5. Write docs/harness/reviews/batch-3-review.md (same structure as Batch 1).

STEP 6. Commit + print
  "REVIEW_COMPLETE pillar3/batch-3 verdict=<APPROVED|CHANGES>"
```

---

## Batch 4 — Launch Readiness

### 4A. Generator prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\generator
on branch pillar3/batch-4.

Batch 3 must be merged first.

Batch 4 is MORE OPERATIONS AND LESS CODE than earlier batches — it ships
the product-readiness layer, not new engine features.

STEP 0 — LOCK THE CLEAN BASELINE (before any code change):
  cd backend
  python -m pytest tests/ --ignore=tests/test_main.py -q > /tmp/batch4_baseline.log 2>&1
  tail -5 /tmp/batch4_baseline.log

  Record pass/fail/skip counts at the top of docs/plans/batch-4-plan.md
  as "POST-BATCH-3 BASELINE".

MANDATORY pre-reading:
  1. docs/product/research/pillar_3_batch_4.md
  2. docs/product/PRD.md                             (the "all UK white-collar
                                              domains" claim fails CAP Code
                                              rule 3.7 substantiation — fix
                                              this)
  3. docs/harness/IMPLEMENTATION_LOG.md
  4. CLAUDE.md

SKILLS:
  1. superpowers:using-superpowers
  2. superpowers:writing-plans → docs/plans/batch-4-plan.md
  3. superpowers:test-driven-development (still applies to code parts)
  4. superpowers:verification-before-completion

SCOPE:
  CODE:
    • Scope down runtime to top 10–15 sources for MVP launch
      (keep the code for others behind a feature flag — do NOT delete)
    • Freemium metering: free tier = 20 matches/day, 1 channel; paid =
      unlimited, all channels. Enforced at FeedService layer.
    • Pricing page (frontend) at /pricing
    • Amazon SES wire-up for transactional email (replace Gmail SMTP)

  NON-CODE (deliver as markdown docs in docs/compliance/):
    • ICO registration checklist (the £40 one) + draft submission
    • Privacy notice + Legitimate Interest Assessment (LIA) per
      pillar_3_batch_4.md §Privacy
    • ASA-compliant marketing copy (rewrite landing page tagline to
      pass CAP rule 3.7)
    • PRD update removing the "all UK white-collar domains" claim until
      substantiated

HARD CONSTRAINTS:
  • Freemium metering must be testable WITHOUT a real Stripe integration
    (mock the billing adapter)
  • SES credentials through env vars only — never committed
  • No "all domains" marketing claim anywhere in the repo after this
    batch (grep gate in tests)

SUCCESS CRITERIA:
  ☐ Baseline + Batch 1/2/3 tests still pass
  ☐ Freemium tests: free user hits limit → gets upgrade prompt; paid
    user has no limit
  ☐ Pricing page renders + links to Stripe test mode
  ☐ SES test email goes through (mocked in CI, real in staging)
  ☐ docs/compliance/ contains: ico_registration.md, privacy_notice.md,
    legitimate_interest_assessment.md, marketing_copy_review.md
  ☐ PRD updated
  ☐ IMPLEMENTATION_LOG.md completion entry drafted (this is the MVP-
    launch milestone entry)

HANDOFF: push, print READY_FOR_REVIEW, STOP.
```

### 4B. Reviewer prompt

```
You are running in
C:\Users\Ranjith\OneDrive\Documents\job360\.claude\worktrees\reviewer
on branch pillar3/batch-4-review.

STEP 1. Pull the generator's branch into THIS worktree:
  git fetch origin pillar3/batch-4 && git reset --hard origin/pillar3/batch-4

STEP 2. Read:
  1. docs/product/research/pillar_3_batch_4.md
  2. docs/plans/batch-4-plan.md
  3. docs/compliance/*
  4. docs/product/PRD.md

STEP 3. Skills: coderabbit:code-reviewer + superpowers:using-superpowers.

STEP 4. AUDIT CHECKLIST:
  Freemium metering
  ☐ Limit is enforced server-side, not only in the UI
  ☐ Limit reset window (per day) uses UTC, not local time
  ☐ Paid-tier bypass cannot be forged by client header

  Compliance
  ☐ ICO registration draft is complete and names a Data Controller
  ☐ Privacy notice covers: CV text, LinkedIn PDF, GitHub scraping
  ☐ LIA three-part test (purpose / necessity / balancing) is explicit
  ☐ Marketing copy has no unsubstantiated absolutes
    ("all", "every", "guaranteed", "best"). Grep the repo.

  Email/SES
  ☐ DKIM/SPF/DMARC setup documented
  ☐ Unsubscribe link present in every email template
  ☐ No PII in SES subject lines

  Source scope-down
  ☐ Feature flag exists; default = top 10-15 sources
  ☐ Deprecated sources still import-resolve (code not deleted)

STEP 5. Write docs/reviews/batch-4-review.md.

STEP 6. Commit + print
  "REVIEW_COMPLETE pillar3/batch-4 verdict=<APPROVED|CHANGES>"
```

---

## After every batch merges to `main`

(This is the human's job — generator/reviewer never do this.)

```bash
# From project root (your main terminal, NOT inside either worktree):
cd "C:/Users/Ranjith/OneDrive/Documents/job360"
git checkout main
git merge --no-ff pillar3/batch-N
# Local batch branches can stay until next kickoff; they get replaced when
# the next `git -C <worktree> checkout -B pillar3/batch-N+1 main` runs.
# The two worktree directories themselves STAY — only the branches rotate.
```

Then:

1. **Re-audit `docs/CurrentStatus.md`** for the sections Batch N touched (delta-only — don't rewrite the whole file).
2. **Confirm `docs/harness/IMPLEMENTATION_LOG.md` completion entry** is accurate (test deltas, KPI deltas, what shipped, what got deferred, surprises).
3. **Save a memory file**: `C:\Users\Ranjith\.claude\projects\C--Users-Ranjith-OneDrive-Documents-job360\memory\project_pillar3_batch_N_done.md` — merge SHA, test deltas, KPI deltas, surprises, deferred items.
4. **Update `CLAUDE.md`** if any rule, count, or load-bearing file moved.
5. **Commit the doc updates** with a `docs: post-batch-N housekeeping` commit on `main`.

---

## Session-start checklist (paste above the prompt when starting any session)

The Claude session has no idea which directory it's in. Pre-pend this verification so Claude confirms location before running anything:

```
VERIFY FIRST (before acting on any instruction below):
  pwd
  git rev-parse --show-toplevel
  git branch --show-current

The pwd MUST match the worktree path in the prompt below.
The branch MUST match `pillar3/batch-N` (generator) or
`pillar3/batch-N-review` (reviewer). If either is wrong, STOP and ask
the user to fix the worktree setup — do not proceed on the wrong branch.
```
