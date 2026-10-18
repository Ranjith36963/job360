<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# Cluster copilots: bulk / auto-apply research (quote-checked 2026-10-18)

Read session-lessons first. Read-only; no edits to /home/user/job360; no git.

Method of this check:
- Code quotes: grepped repo-wide in /var/tmp/refs/freehire (HEAD 073d7c3916cff1323dbbaa23a05a3ebea496311d). Path and line given per claim.
- Web quotes: whitespace-normalised substring match against saved captures in bulk/raw/, round2/raw/, refs/raw/ and refs/*.md. Capture file named per claim.
- Tags: [ok] verified at the stated location. [ok, corrected] the original quote was not verbatim; the exact text is now given. [unverifiable] not present in any saved source, or the line/absence was not checked. [wrong] quote not in the source; the quote is deleted.
- No REA calls were made in this check (checker only). The evidence ids ev_fff448eb (jobright) and ev_8b5e2880 (huntr) appear in the saved captures bulk/raw/jr-autofill.txt:1 and bulk/raw/huntr-autofill.txt:1.

Raw captures: /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/bulk/raw/ and .../round2/raw/.

## Headline finding

Two products in this cluster have a verified queued or bulk apply path:
1. **Simplify Autopilot** (help-center captures): per-job send, parallel queue, no review step, weekly credits. [ok]
2. **freehire autoapply** (source, /var/tmp/refs/freehire): the only one with a visible data model. Review-first, per-user daily allowance. [ok]

Jobright's own copy says "apply to hundreds of jobs each day" (autofill page) and describes a daily scan plus one-click apply (blog). [ok] Careerflow, Huntr, Teal and Kickresume: the pages read show autofill only, user submits. [ok for pages read] Absence beyond those pages is NOT proven. [unverifiable]

---

## 1. freehire autoapply (/var/tmp/refs/freehire)

### 1.1 Entry, batch, scheduling
- One job per request. `func (h *assistantHandlers) PostJobAutoApply(c *fiber.Ctx) error {` at internal/api/handler/auto_apply_enqueue.go:150. [ok]
- "Cookie-only (mw.cookie, never mw.key)" at auto_apply_enqueue.go:138. [ok] "a fresh entry can end in a REAL submitted job application" at :141-142 (line-wrapped in source). [ok]
- Gates: "the job must be a Greenhouse posting, the caller must already have a base CV, the pair must not already be applied to, and the caller must have auto-apply allowance left today" (comment, :143-146, line-wrapped). [ok]
- Allowlist is a map literal, multi-line, at auto_apply_enqueue.go:26-33: `"greenhouse": true,` `"ashby": true,` `"workable": true,` `"lever": true,` `"recruitee": true,`. [ok, corrected: the one-line form in the earlier draft was not verbatim]
- "Only Greenhouse can currently SUBMIT one" at auto_apply_enqueue.go:20. [ok]
- "an Ashby/Workable/Lever/Recruitee attempt still tailors and gets reviewed like any other, then parks with an honest, named reason" at auto_apply_enqueue.go:21-22. [ok, corrected: the earlier draft said "tailor and get reviewed"]
- Timer: "a timer that fires every 5 minutes and takes one attempt per entry" at internal/application/autoapply/runner.go:97. [ok]
- Defaults at internal/platform/config/autoapply.go: `envInt("AUTO_APPLY_BATCH_SIZE", 20)` (:59), LeaseSeconds 300 (:60), MaxAttempts 3 (:61), Concurrency 2 (:62), MaxPerRun 200 (:63), `envInt("AUTO_APPLY_CALL_TIMEOUT_SECONDS", 720)` (:64). [ok]
- Lease floor: `if floor := int(a.CallTimeout.Seconds()); a.LeaseSeconds < floor {` at autoapply.go:78. [ok]
- Event: "Triggered by event `auto-apply/submit` (data: `queueId`)" at openspec/changes/auto-apply-inngest-orchestration/proposal.md:27. [ok] `const EventSubmit = "auto-apply/submit"` at internal/application/autoapplyorchestrate/orchestrate.go:37. [ok]
- NOT FOUND: per-company cap, per-site cap, inter-application delay, quiet hours. [unverifiable: absence, not exhaustively searched]

### 1.2 Caps and pacing
- Plan block, actual lines plan.go:222-226: `FeatureAutoApply: {` / `pro: tierAllowance{daily: 3},` / `ultra: tierAllowance{daily: 100, unlimited: true},` / `enforce: true,`. [ok, corrected: the earlier pseudo-syntax was not verbatim]
- "Free gets no metered feature at all" at internal/ai/plan/plan.go:138. [ok]
- Ultra rationale: "it guards the HOST (each attempt is a browser)" at plan.go:~219-220, line-wrapped. [ok]
- Pricing label `'auto-apply': 'Unattended applications',` at web/src/routes/pricing/+page.svelte:68. [ok]
- Charge at enqueue: `ref := "job:" + strconv.FormatInt(jobID, 10)` at auto_apply_enqueue.go:301; `d, err := h.plans.Consume(c.Context(), userID, plan.FeatureAutoApply, ref)` at :302. [ok] chargeAutoApply at :296. [ok]
- "the honest answer to 'we could not tell whether you may' is to say so" at auto_apply_enqueue.go:295. [ok]
- NOT FOUND: per-company / per-site / delay / quiet-hours caps. [unverifiable: absence]

### 1.3 Form engine
- "This package is chromedp, in-process — not a Python/Patchright sidecar." at internal/api/atsapply/AGENTS.md:10. [ok]
- Flow: "captcha short-circuit → fetch the platform's schema via" at AGENTS.md:239 (source uses →). [ok, corrected: arrows are the source's own; the later steps were not re-checked]
- "The DOM decides what exists; required is the union of DOM and API." at AGENTS.md:68. [ok]
- "Only the résumé file field resolves; everything else does not." at AGENTS.md:144. [ok]
- Multi field: "A `Multi` field (a checkbox group taking several answers) only ever resolves at most one" at AGENTS.md:157 (wrapped). [ok, corrected: earlier draft dropped the middle clause]
- The fill/submit path is "the least-verified part of this" package: AGENTS.md:221. [ok]
- Sensitive terms, internal/api/atsapply/sensitive.go:18-25 (`var sensitiveTerms`): "salary", "compensation", "sponsor", "visa", "authoriz", "right to work", "gender", "race", "ethnic", "veteran", "disab", "demographic", "religio", "national origin", "date of birth", "genetic". [ok] (The list also has "sexual orientation". Path corrected: it is internal/api/atsapply/sensitive.go, not application/autoapply.)
- workAuthorizationTerms, sensitive.go:49: `{"authoriz", "right to work", "sponsor", "visa"}`. [ok]
- Drafting grounding: "agent_inferred atom is excluded, not merely unpreferred" at internal/api/atsapply/grounding.go:24. [ok, corrected: "never an agent_inferred atom" was not in the source] Also AGENTS.md:214 "An `agent_inferred` atom can never reach a draft." [ok]
- `ResolveWithDrafting` at internal/api/atsapply/resolve.go:364 (comment). [ok]
- `isCoverLetterTextField` at resolve.go:353. [ok]
- Browser-use markers: "`CONFIRMED: <text>` / `UNCONFIRMED`" and "`PARKED: <reason>`" at AGENTS.md:49-50. [ok]
- Spend cap: browseruse_fill.go:208 says the cap is per "INVOCATION — NOT a calendar day". [ok, corrected: the earlier draft's wording was a paraphrase]
- `AUTO_APPLY_BROWSERUSE_ENFORCE` at config/autoapply.go:47 (comment). [ok]
- Flow `captcha short-circuit → ... → if Plan.FullyResolved(), fillAndSubmit; else StatusParked`: `Plan.FullyResolved()` at internal/api/atsapply/domscan.go:89 (comment) and `StatusParked` in client_test.go. [ok for the names; the full chain order is not verified beyond AGENTS.md:239]

### 1.4 Review modes
- "`Store.Claim` only ever returns a reviewed-and-approved entry." at internal/application/autoapply/AGENTS.md:49. [ok, corrected: the earlier draft's quote was not verbatim]
- `q.tailored_cv_id IS NOT NULL AND q.review_decision = 'approved'` as a literal in ClaimAutoApplyBatch. [wrong: not in internal/platform/db/auto_apply_queue.sql.go; deleted]
- Claim lock: `FOR UPDATE OF q SKIP LOCKED` at internal/platform/db/auto_apply_queue.sql.go:46 and :131. [ok, corrected: the earlier draft added "LIMIT $2" which was not on that line]
- Approve: `WHERE id = $1 AND review_decision IS NULL` at auto_apply_queue.sql.go:19; `ApproveAutoApplyReview` at :15 and :27. [ok]
- "persisted verbatim — never recomputed" at internal/application/autoapply/preview.go:25. [ok]
- "without spending an LLM call" at preview.go:48. [ok]
- Migration `0139_auto_apply_queue_resolved_preview.sql` exists. [ok]
- Status precedence: "declined is checked first" and "A tailoring failure is checked LAST of the operational states" at internal/application/autoapply/status.go:56-61. [ok] Status constants at status.go:13-36 (tailoring, pending_review, blocked, declined, tailor_failed). [ok]
- UI progress: "3-stage progress view: Tailoring, Review, Submitted" at web/src/lib/autoApplyProgress.ts:4. [ok]
- Pause is local: "It has no effect on cmd/auto-apply's claim/process behavior" at web/src/lib/autoApplyPauseStorage.ts:3; `const KEY_PREFIX = 'hire.autoApplyPaused:';` at :11. [ok]
- Pause and review are in the web UI, not the queue. [ok]

### 1.5 Failure handling
- Outcome enum: StatusApplied, StatusParked, StatusUnconfirmed, StatusCaptchaRefused, in internal/application/autoapply/runner.go (const block, ~:59-83). [ok]
- "**`Park` is not a retry.** A parked attempt needs new data, not another try" at internal/application/autoapply/AGENTS.md:15. [ok]
- Unconfirmed is dead-lettered at once: `return rn.deadLetterImmediately(ctx, c, "submission unconfirmed: neither a confirmation nor a refusal was seen")` at runner.go:~274-275. [ok]
- "forces a dead-letter (maxAttempts=1) rather than spending the run's configured retry budget" at runner.go (deadLetterImmediately, ~:300-318). [ok]
- `const captchaMaxAttempts = 20` at runner.go:100. [ok]
- Captcha markers: `captchaRefusalMarkers` at internal/api/atsapply/fill.go:63-69: "error verifying your", "could not verify", "couldn't verify", "unable to verify", "verification failed". [ok]
- `challengeVisibleJS` (fill.go:87-95) checks `hcaptcha.com` and `recaptcha`. [ok]
- Migration 0157 comments: "an invisible hCaptcha is a coin toss no browser configuration improves" (:4-5) and "measured 2026-09-10 over eight probe runs against a live Lever" (:5). [ok]
- "one pass" as a success count. [wrong: not in source; deleted]
- Stealth: `stealthAllocatorOptions` at internal/api/atsapply/browser.go:23; the flag `disable-blink-features=AutomationControlled` at internal/platform/browser/browser.go:38 and atsapply/AGENTS.md:255. [ok] "only" as the sole anti-detection measure. [unverifiable]
- Datacenter IP risk "unaddressed". [unverifiable: not in atsapply/AGENTS.md]
- Login walls / passwords: NOT FOUND. [unverifiable: absence]

### 1.6 Proof and reporting
- Success event: `EventSource: appevent.SourceAutoApply,` at cmd/auto-apply/store.go:93. [ok]
- Nudge kinds: migration `0144_application_nudges_auto_apply_kinds.sql` exists; `nudge_auto_apply_submitted` / `_blocked` / `_failed` in web/src/lib/notificationTarget.ts:19. [ok]
- Dedup: "episode_key is the idempotency key together with (user_id, job_id, kind)" at migrations/0083_application_nudges.sql:10. [ok, corrected: the earlier draft listed the key in a different order]
- Run log: `log.Printf("auto-apply done: applied=%d blocked=%d failed=%d dead_lettered=%d; ...` at cmd/auto-apply/main.go:185. [ok]
- Screenshots / receipts: NOT FOUND. [unverifiable: absence]

### 1.7 Dedup and safety
- `UNIQUE (user_id, job_id)` at migrations/0116_auto_apply_queue.sql:42. [ok]
- Already-applied pre-check: `h.queries.GetUserJobApplied(` at auto_apply_enqueue.go:177. [ok]
- Declined pair: `const autoApplyAlreadyDeclinedMessage = "this auto-apply attempt was already declined"` at auto_apply_enqueue.go:37. [ok]
- Eligibility env: `return os.Getenv("AUTO_APPLY_ELIGIBILITY_ENFORCE") == "1"` (autoApplyEligibilityEnforce, auto_apply_enqueue.go:~89). [ok]
- Blocker scope: "eligibilityBlocker reports the first unmet work-authorization or location-and-work-mode" at auto_apply_enqueue.go:97. [ok, corrected: earlier draft's quote was a paraphrase]
- "never manufactures a blocker from an absent field" at internal/application/autoapply/AGENTS.md:71 (not at enqueue). [ok, citation corrected]
- Submit transaction: `qtx.LockJobForApply` (cmd/auto-apply/store.go:86), `MarkJobApplied` (migration 0116 comment, store.go:71), `qtx.DeleteAutoApplyEntry` (store.go:97). [ok]
- Accepted gap: "**Scope of that guarantee, found by a PR review pass**" at openspec/changes/auto-apply-worker/design.md:117. [ok]
- Kill switch: no auto-apply-specific one found (grep "kill switch" hits only unrelated code). [ok within grep scope]

### 1.8 Data model
- Columns by migration: 0128 review, 0139 resolved_preview, 0140 preview_attempts / preview_failed_at, 0154 tailor_failed_at, 0157 captcha_attempts. [ok]
- `auto_apply_queue_claimable_idx` at migrations/0116_auto_apply_queue.sql:47. [ok] Its WHERE clause (failed_at / blocked_at): [unverifiable: not checked]
- Lease, no reaper: "a dead worker's rows return without a reaper process" at migrations/0116_auto_apply_queue.sql:5. [ok]
- Pool: `outbox.RunPool` named in docs/superpowers/specs/2026-09-02-auto-apply-worker-design.md:71 ("internal/outbox.RunPool over auto_apply_queue"). [ok, corrected: the package is internal/platform/outbox; the design doc's path is internal/outbox]

### 1.9 Pricing
- Pro 3/day and Ultra 100/day: plan.go:223-224. [ok]
- Dollar prices: NOT FOUND in the pricing svelte (labels only). [ok, absence within that file]

---

## 2. Simplify Autopilot (help.simplify.jobs captures)

Sources: bulk/raw/simp-autopilot.txt, simp-credits.txt, simp-pricing.txt (also round2/raw/).

1. Entry: "Open the dropdown next to Apply and choose Autopilot" [ok simp-autopilot.txt]. "Send several jobs to Autopilot back to back and follow all of them from the Autopilot tab." [ok simp-autopilot.txt]. "Autopilot isn't offered on a job you've already applied to." [ok simp-autopilot.txt]. "a specific set of application systems" [ok simp-autopilot.txt; the list itself NOT FOUND]. Batch size and schedule: NOT FOUND. [ok absence in these captures]
2. Caps: "You get 20 Autopilot credits a week" [ok simp-credits.txt]. "no rollover" [ok simp-credits.txt]. "buy more ... yet" as a coming item: [unverifiable]. Per-company, delays, quiet hours: NOT FOUND. [ok absence]
3. Engine: "opens the company's application, fills it in from your profile, tailors your resume if you asked it to, and submits it" [ok simp-pricing.txt]. "works on far more application systems than Autopilot does" [ok simp-credits.txt]. "follows the tailoring settings you chose when you turned Autopilot on" [ok simp-*.txt, quote corrected to the source's wording].
4. Review: "Choosing Autopilot means submitting. There's no review step in between." [ok simp-autopilot.txt]. "Autopilot prepares and submits applications without a review step." [ok simp-pricing.txt]
5. Failures: "If it isn't confident about an answer, it fails the application rather than guess. No credit is used" [ok simp-autopilot.txt; the source continues ", and you can finish it yourself with Copilot."]. "a question your profile doesn't cover, or a form that doesn't look the way it expected" [ok simp-credits.txt]. "we let you know" [ok simp-credits.txt]. Captcha, login: NOT FOUND. [ok absence]
6. Proof: "Then follow it in the Autopilot tab." [ok]. Screenshots, receipt email: NOT FOUND. [ok absence]
7. Safety: "deliberately cautious" [ok simp-credits.txt]. Kill switch, blacklist: NOT FOUND. [ok absence]
8. Data model: NOT FOUND. [ok absence]
9. Pricing: "Simplify+ includes 20 auto-applies a week" [ok simp-pricing.txt]. "1 Week" [ok simp-pricing.txt]. The dollar figures $19.99 / $39.99 / $89.99: found only in third-party captures (ai-job-search-tools-845.html, sonara-alternatives-780), not confirmed in simp-pricing.txt. [unverifiable for Simplify]
- Jobright blog (2025-08-09) "Lacks background auto-apply agent" [ok bulk/raw/web-reads-2026-10-18.txt]; superseded by the help center. [ok as the doc's own reading]

## 3. Jobright Agent / autofill

Sources: bulk/raw/jr-autofill.txt (evidence id ev_fff448eb, bulk/raw/jr-autofill.txt:1), bulk/raw/web-reads-2026-10-18.txt. https://jobright.ai/auto-apply returned 404 (doc statement, not re-checked). [unverifiable]

1. Entry: "Every day, the Agent scans over 400,000 new job postings" [ok web-reads]. "only the most promising matches make it to your dashboard" [ok web-reads]. "customizes your resume, writes a cover letter, fills forms, and applies" [ok web-reads]. This is daily sourcing and ranking. Job360 rule 4 forbids it; it is noted here only as competitor behaviour.
2. Caps: "You can apply to hundreds of jobs each day without re-entering information" [ok jr-autofill.txt:77-84]. Turbo $39.99/mo and "2-4 autofill credits/day": [unverifiable: third-party search summaries only].
3. Engine: "on thousands of ATS platforms with just 1-click" [ok jr-autofill.txt:77]. Answer memory and LLM questions: NOT FOUND. [ok absence]
4. Review: "You stay in control, but the Agent takes on the busywork." [ok web-reads]. Review mode details: NOT FOUND. [ok absence]
5. Failure: NOT FOUND. [ok absence]
6. Proof: "tracks each application's status, sends timely follow-up nudges, and updates your plan based on results" [ok web-reads].
7. Dedup: NOT FOUND. [ok absence]
8. Data model: NOT FOUND. [ok absence]
9. Pricing: "Free / $29.99" [ok web-reads, older blog].
- "the tool says '200 applications submitted'" (atsverification review). [unverifiable: no saved capture contains it]
- "Unreviewed applications carry the tool's mistakes" (same review). [unverifiable]
- Recruiters "can often see your other applications". [unverifiable]
- Lesson to copy (third-party critique): show per-item outcome, not a total. [unverifiable: depends on the removed review quotes; kept as opinion only]

## 4. Careerflow

Sources: bulk/raw/web-reads-2026-10-18.txt (help.careerflow.ai autofill article); refs/careerflow.md.

- No batch, no auto-apply: "Once the fields are autofilled, review the information for accuracy, and submit your application" [ok web-reads].
- Trigger: "hovering over the floating icon activates the Autofill" [ok, corrected; the earlier draft's wording was a paraphrase]. Data: "responses from onboarding questions" [ok web-reads].
- ATS list: "Greenhouse, Lever, Uber Career Page Job Form, Workable Job Forms, Okta Job Forms, Rippling Job Forms, BreezyHR, JazzHR, JobScore, Jobvite, PinpointHQ, and SmartRecruiters." [ok web-reads]
- "Power User Tip: combining Autofill, AI Resume Tailoring, and Cover Letter Management" [unverifiable: not in any saved capture; deleted from the copy list]
- "75+ portals" [ok refs/careerflow.md]
- Pricing: "Premium $8" and "Premium Plus $19" [ok refs/careerflow.md]. "$23.99" / "$44.99" third-party [ok third-party captures, conflicting, not used].
- "Follow-Up Emailer" [ok refs/careerflow.md].
- Axes 1, 2, 4, 5, 6, 7, 8: NOT FOUND for any batch. [ok absence in pages read]

## 5. Huntr

Sources: bulk/raw/huntr-autofill.txt (evidence id ev_8b5e2880, bulk/raw/huntr-autofill.txt:1).

- "All it takes is one-click of Huntr’s Job Application Autofill tool for you to completely fill out a job application form" [ok huntr-autofill.txt:80; source uses a curly apostrophe].
- "apply to hundreds of jobs each day" [ok huntr-autofill.txt:85, :143].
- "It’s better to autofill job applications when you’re trying to apply to many jobs" [ok huntr-autofill.txt:106; curly apostrophes].
- "If applying to your dream job, you can autofill the repetitive information, and manually answer questions" [ok huntr-autofill.txt:106].
- Pricing: "You can autofill an unlimited number of job applications" [ok huntr-autofill.txt:110]. "The first hundred jobs added to your Kanban board are free, a $40 monthly subscription is needed for anything above that." [ok huntr-autofill.txt:110, :179].
- Autofill saves the job to the Kanban and saves LinkedIn jobs (doc cites :107-:108). [unverifiable: those lines not checked]
- "It's rare that ... can't complete the entire application" (doc cited a region). [unverifiable: not found]
- Bulk apply: NO. Verdict rests on the autofill page text. [ok for the page read; beyond it NOT proven]

## 6. Teal

Source: bulk/raw/teal-web-read.txt (Apify web-fetch; tealhq.com refused curl).

- "Only thing they could work on is reading different types of job boards for the autofill." [ok teal-web-read.txt, customer review]
- "40+ boards" [ok refs/teal.md]. Pricing "$13/wk", "$29/30d", "$79/90d" [ok refs/teal.md].
- Tracker statuses (bookmarked → negotiating) and 40+ email templates: [unverifiable: not re-checked in this pass]
- No bulk apply found. [ok for pages read]

## 7. Kickresume

- Blog title "5 Best AI Auto-Apply Tools for Jobs" [ok refs/raw/kick_ai-career-bot.txt]. It is a roundup of other products. No Kickresume auto-apply product found. [ok absence in the kick captures]

---

## What to copy into job360 (store tools, not do tools; rules 4 and 5)

Job360 does not source, rank or drive browsers. The user's own agent applies. What transfers is the state machine and safety contract, which job360 can store and enforce through MCP (`check_submit`, `record_application`, `ask_user`, receipts):

1. Per-item state derived from facts with strict precedence: freehire `DeriveStatus`, internal/application/autoapply/status.go:56-61 ("declined is checked first"). Statuses per status.go:13-36. [ok]
2. Park is not a retry: internal/application/autoapply/AGENTS.md:15. Separate counters per reason (attempts, captcha_attempts, preview_attempts; migrations 0140 and 0157). [ok]
3. Ambiguous submit freezes, never retries: runner.go deadLetterImmediately path (~:274-275, ~:300-318). [ok]
4. Review-first snapshot stored verbatim: preview.go:25. [ok]
5. Sensitive-question gate: internal/api/atsapply/sensitive.go:18-25 and :49. [ok]
6. Daily allowance charged at enqueue, idempotent by job ref: auto_apply_enqueue.go:296-302. [ok]
7. Simplify's credit-on-success and visible failure list, "fails rather than guesses" (simp-autopilot.txt). [ok]
8. Queue dedup: migrations/0116_auto_apply_queue.sql:42; auto_apply_enqueue.go:177. [ok]
9. Eligibility gate in shadow first: auto_apply_enqueue.go:~89 (AUTO_APPLY_ELIGIBILITY_ENFORCE). [ok]
10. Do not copy: captcha retry loops (captchaMaxAttempts = 20, runner.go:100), stealth flags, cloud browser agents, daily job scanning and ranking (Jobright), credits tied to our own sourcing. [ok as the doc's judgement]

## Gaps for a later read (not claimed)
- Simplify Autopilot supported-ATS list and the "Getting started with Autopilot" article body: not fetched.
- Jobright Agent real behaviour (jobright.ai/ai-agent, help center): not fetched; REA browser did not answer in round 1-2 and was not retried in this checker pass.
- Careerflow, Huntr, Teal, Kickresume: no bulk apply on the pages read. Absence beyond those pages is NOT proven. [unverifiable]
- Quotes tagged [unverifiable] above are not claimed.
