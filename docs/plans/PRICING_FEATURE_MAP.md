# Pricing — Feature Map (what each paid API unlocks)

**Researched 2026-06-25** (verified against each API's live docs). Maps the real data
fields of the two paid providers to concrete Pro/Max features. Companion to
`PRICING_FINAL_DECISION.md`. Sources at end.

## The clean split
- **Fantastic Jobs enriches the JOB** (salary, visa, seniority, skills, apply link) → **Pro**.
- **TheirStack enriches the COMPANY** (tech stack, funding, size, hiring manager) → **Max**.
- Story: Pro = *smarter jobs*; Max = *smarter about the companies behind them*.

## CUMULATIVE RULE (important — 2026-06-25 correction)
Tiers stack: **Max = Free + Pro + TheirStack**, NOT "TheirStack instead of Fantastic
Jobs." Max users still get all Fantastic Jobs data + features (incl. one-click direct
apply). Nobody loses a feature by paying more. Direct-apply therefore lives in **both
Pro and Max**.

## Verified corrections (be honest about these)
- **AI CV tailoring is NOT built** (backend audit confirmed — no route exists). Do not
  list it as a real tier feature until built.
- **VERIFIED 2026-07-05: Fantastic Jobs does NOT have Indeed.** Confirmed from
  fantastic.jobs/about + its RapidAPI listing. Its job boards are ONLY **LinkedIn,
  Wellfound, Y Combinator** (+ 200k ATS career sites across 54 platforms). Indeed,
  Glassdoor, ZipRecruiter are absent. **Indeed/Glassdoor come only from TheirStack
  (Max).** ⇒ Pro promise must say "live LinkedIn + company career sites", NOT "Indeed".
- **Overlap caveat (Fable):** FJ's ATS list (Greenhouse, Lever, Ashby, Workable,
  SmartRecruiters, Personio, Recruitee, Rippling…) overlaps Job360's existing 47 free
  sources. FJ's UNIQUE add = LinkedIn + Wellfound + YC + a bigger company list on the
  same ATS. Run a dedup trial before buying — true cost is per-UNIQUE-job, not $1/1k.
- **Direct/easy apply exists in BOTH APIs** (FJ `direct_apply`, TS `easy_apply` +
  resolved apply URL) — it is not a unique FJ moat.

## Full Free/Pro/Max feature allocation (code-audited)
See the two tables in this session's summary; canonical allocation:
- **Free (all $0-cost):** 47 free boards, keyword score (E1), CV upload+parse+prefs,
  like/apply, capped feed (top-20/day), basic tracker (≤50 jobs), duplicates view,
  ghost filter, 1 weekly digest email, full account mgmt.
- **Pro (+ Fantastic Jobs):** live LinkedIn + 200k career sites, all AI scoring
  (E2/E3/E4 verdicts), 8-D radar, one-click direct apply, smart filters (salary/
  seniority/remote/visa), hide agencies, AI summary + skill-match %, LinkedIn+GitHub
  profile enrichment, version history/diff/restore, full Kanban tracker (unlimited),
  all notification channels + rules + digests, CSV/JSON-Resume export.
- **Max (+ TheirStack, cumulative):** Indeed+Glassdoor+344k sources, minute-fresh
  instant alerts, company tech-stack match, funding/size/revenue/industry filters,
  hiring-manager contact card, "company scaling" alerts, tech-stack explorer,
  direct-employer-only, unlimited + priority ranking.

## FREE (no paid data)
46 free boards, keyword scoring, capped daily matches, no notifications. $0 cost.

## PRO — Fantastic Jobs (~£14.99/mo, hourly LinkedIn/Indeed/ATS)

| Feature | API field | Note |
|---|---|---|
| One-click direct apply | `direct_apply` | apply without leaving the product |
| Salary floor filter | `ai_salary_min/max_value`, `ai_salary_currency` | LLM-recovers salary from free text |
| Visa-sponsorship-only mode | `ai_visa_sponsorship` | **feeds existing `VISA_WEIGHT` dim** |
| "Matches your level" search | `ai_experience_level` (0-2/2-5/5-10/10+) | maps to CV seniority |
| Remote precision filter | `ai_work_arrangement` + office-days | Remote Solely / OK / Hybrid |
| Hide recruitment agencies | `removeAgency` / `org_linkedin_recruitment_agency_derived` | strips agency reposts |
| AI 2-line job summary | `ai_core_responsibilities` + `ai_requirements_summary` | instant TL;DR per card |
| "3 of 5 skills matched" | `ai_key_skills` vs CV skills | **set-diff, respects rule #28** |
| Company basic card | `org_linkedin_headcount`, `industry`, `founded` | "know the company" panel |
| Notifications + higher caps | (Job360 existing levers) | email/Slack/Discord |

**Cheap-to-build note:** `ai_visa_sponsorship`, `ai_salary`, `ai_experience_level`,
`ai_key_skills` map directly onto Job360's existing scoring dims + ESCO CV extraction —
read a richer field, no new logic.

## MAX — TheirStack (~£29-39/mo, minute-fresh, 344k sources) — the anchor

Features the cheaper API **cannot** provide (no technographics, funding, or hiring-team):

| Feature | API capability | Note |
|---|---|---|
| **Jobs at companies using YOUR tech stack** | `company_technology_slug_or/and/not` | killer feature — ties to ESCO CV skills |
| Well-funded startup filter | `funding_stage_or`, `min_funding_usd`, `company_investors_or` | "Series A/B, YC-backed" |
| Company size/stage targeting | `min/max_employee_count` | "50-500 person companies" |
| Direct-employer-only mode | `company_type=direct_employer` | hard agency strip |
| Hiring-manager contact card | `hiring_team[]` (name, role, LinkedIn) | cold-message vs blind-apply |
| "Company is scaling" alert | `num_jobs_last_30_days` + Hiring Signals | apply before the rush |
| Revenue/industry precision | `industry_id_or` + `min_revenue_usd` | "fintech, $50M+ revenue" |
| Tech-stack explorer per company | Technographics API (confidence-scored) | skills-gap prep before applying |
| Minute-fresh instant alerts | ~10-min ATS polling cadence | speed = top-tier flex |
| All engines + priority ranking | (Job360 existing) | + unlimited searches |

## Cost sanity (per the pricing research)
- Fantastic Jobs: ~$1/1,000 jobs (self-serve, RapidAPI — clones existing JSearch wiring).
- TheirStack: Starter $59 (1.5k credits) → Pro $169 (10k credits, $0.0169/job). 1 credit/job,
  3/company-or-technographics lookup. Cheap enough to gate behind Max, not Free.

## Build notes
- Both need the **plan-aware `_build_sources()`** lever (Free skips paid sources).
- New source classes: `ActiveJobsDBSource` (Fantastic, clones `JSearchSource`) and
  `TheirStackSource`. Each touches the 5 load-bearing surfaces (CLAUDE.md rule #8).
- Enrichment fields flow into existing scoring dims — don't rebuild, just read.

## Sources
**Fantastic Jobs:** developer.fantastic.jobs/api/~schemas · fantastic.jobs/api · fantastic.jobs/about · apify.com/fantastic-jobs/career-site-job-listing-api
**TheirStack:** theirstack.com/en/docs/api-reference/jobs/search_jobs_v1 · theirstack.com/en/docs/datasets/options/job · theirstack.com/en/docs/api-reference/companies/technographics_v1 · theirstack.com/en/pricing · theirstack.com/en/hiring-signals
**Flagged unverified:** exact RapidAPI tier names/rate-limits for Fantastic Jobs (JS-rendered); Glassdoor fields (marketing copy, not in schema); TheirStack "every minute" is monitoring-loop language, real cadence is tiered (10min/hourly/daily).
