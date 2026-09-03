# Post-Application Features — the "after you apply" career co-pilot
<!-- doc: PLAN -->

> **⚠️ SOURCING ERA — superseded 2026-09-03.** Job360 no longer sources, ranks or recommends jobs. The seeker's own AI agent does that; Job360 is the agent's memory (profile, artifact versions, typed events, receipts). Read [`VISION.md`](VISION.md) first — it wins over anything below. This file is kept as history only. Interview prep, mock interview, follow-up writing and outreach drafting are the agent's job (rule 5); Job360 only stores what happened.

> **PLAN — not a description of today's code.** Written to be built, possibly never built or since changed. Verify against code before trusting. <!-- banner: auto -->

> **The idea in one line:** Once a user applies (with their tailored CV + cover letter), Job360 already knows *everything* about them and the job — so it can carry them through the rest of the journey: **interview prep → mock interview → skill-gap → follow-up email → outreach.** Each feature is "one more prompt + one more screen" using data we already store.

---

## Why this is basically free to build

By the time someone applies, we already have **everything**:
- 4 profile inputs (CV, LinkedIn, GitHub, preferences) + the extracted `CVData` (skills/experience/education)
- the **AI-optimized CV**, the **user-edited/applied CV**, and the **cover letter**
- the **job description** + the judge's **"why it fits" reason**

So we know the whole picture — the person *and* the job. Every feature below is just a new *use* of that data. No new data collection.

**The journey this completes:**
`find → match → apply (tailored CV+cover) → interview prep → mock → skill-gap → follow-up → outreach → offer = win`

---

## The 5 features

### 1. Interview prep ⭐ (start here)
- **What:** likely interview questions for **that specific job**, each with a **strong answer built from the user's real experience** (STAR-style where relevant).
- **Data:** job description + `CVData` + applied CV + judge's fit reason.
- **Where:** the job / Kanban application card → "Prep for interview".
- **Guardrail:** answers come from **true** experience — never invent achievements.

### 2. Mock interview
- **What:** practice mode — the AI **asks the questions** (from #1), the user answers (type or speak), the AI **scores + coaches** ("good, but quantify the impact").
- **Data:** the interview questions (#1) + the CV.
- **Where:** application card → "Practice interview" (chat-style screen).
- **Guardrail:** feedback is coaching, not scripting — don't hand them fake answers to memorize.
- *(Salary/comp questions naturally live here + in #1 — no separate "offer" feature.)*

### 3. Skill-gap
- **What:** compares the **job's required skills vs the user's skills** → "you're missing Docker + Kubernetes → here's what to learn, or how to spin what you already have."
- **Data:** job requirements (already extracted) + user skills (already extracted) — the gap is **computable today**.
- **Where:** job page + application card → "Am I a fit?".
- **Guardrail:** honest but encouraging — turn a gap into a next step, not a rejection.
- *(Sneaky-good: turns a "no" into a reason to keep using the tool.)*

### 4. Follow-up email
- **What:** after the interview, generate a tailored **thank-you / follow-up email**.
- **Data:** the job + company + the user's application.
- **Where:** application card → "Send follow-up" (draft → edit → copy/send).
- **Guardrail:** a **draft the user edits** — never auto-send.

### 5. Outreach (the one optional add)
- **What:** a warm **LinkedIn message or email to a recruiter / someone at the company** asking for a referral or a quick chat.
- **Data:** the job + company + the user's CV.
- **Where:** job page + application card → "Reach out".
- **Guardrail:** a **draft the user edits** — never auto-send.
- **Why it matters:** most jobs are landed through **people, not applications**. Helping users *reach out* (not just *apply*) is a real edge.

---

## What we deliberately DROPPED — and why

**Offer / salary negotiation.** Cut on purpose:
- The **offer IS the win** — it means the tool worked. It's the *goal*, not a feature to build.
- **Salary talk already lives inside interview prep + mock** (#1/#2) — no separate feature needed.
- **Negotiation is too personal** — automating it feels robotic and could give bad, one-size advice on a high-stakes personal decision.

> Discipline note: more features ≠ better. A **complete journey people actually finish** beats a pile of half-features.

---

## Shared design (same as the tailored-CV feature)

- **Guardrails across all 5:** never fabricate · always editable (drafts, esp. #4/#5) · **paid/capped** (each = an LLM call — ties into the paywall) · privacy (per-user data stays per-user).
- **Optional learning loop:** mock-interview feedback + edited follow-ups/outreach can feed the **per-user learning** layer (see `peruser_cv_coverletter.md`) — learn the user's tone/style over time.
- **Reuse what's built:** `services/profile/llm_provider.py` (Cerebras-first), the judge's `reason`, the applications/Kanban pipeline, the ARQ worker for async generation.
- **Frontend home:** these are *post-apply*, so the natural home is the **Kanban application card** (`frontend/src/app/pipeline`), with entry points also on the **job detail page** (`jobs/[id]`).

---

## Build sequence
1. **Interview prep** (#1) — the foundation; everything else leans on it.
2. **Mock interview** (#2) — replays #1's questions with scoring.
3. **Skill-gap** (#3) — pure data (job skills − user skills); no interview needed, useful even pre-apply.
4. **Follow-up email** (#4) — small, high-value, draft-and-edit.
5. **Outreach** (#5) — optional, same draft-and-edit pattern.

---

## One-line summary
> **After you apply, the tool keeps going:** interview prep → mock → skill-gap → follow-up → outreach — all built from data we already store, each "one more prompt + one screen", all draft-and-edit, all paid/capped. **Offer/salary is intentionally out** (it's the win, and it's too personal). This turns Job360 from a *job finder* into a *full career co-pilot*.
