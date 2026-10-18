<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# Bulk apply: what to build from the competitors (for the owner)

Written 2026-10-18. Read-only research. Nothing in /home/user/job360 was edited and no git was run.
You said "borrow and copy everything". This list does that inside your own rules: your AI does the
submitting, Job360 stores, gates, counts and proves (product rule 4 and 5, VISION.md:13-21).

## How to trust this list

- Evidence = the checked teardowns in `scratchpad/bulk/*.md` (6 files, each already quote-checked), plus
  I re-opened the sources for every item in the top 15 and grepped the quoted words.
- Path shorthand. `J:` = /home/user/job360/backend/src. `R1` = scratchpad/refs/raw. `R2` = scratchpad/round2/raw.
  `B` = scratchpad/bulk/raw. Repos = /var/tmp/refs/<repo>. Trackly files are under
  `trackly-cli/skills/trackly-apply/`.
- No `mcp__rea__` call was made in this pass, so nothing here is claimed from REA. Every source is a
  saved web read (earlier Apify/web captures) or a cloned repo. (One capture file is *named*
  `tsenta-changelog-rea.txt`; I cite it by path only.)
- Tags: a quote with `file:line` was found there. "NOT FOUND" = no competitor publishes it (absence in the
  pages/repos read, not proof of absence everywhere).
- The Job360 contract (batch queue + per-company cap) is already visible in the working tree on
  2026-10-18 (`J:services/profile/assistant_settings.py:734`, `J:recipes/batch.md`). I cannot tell from here
  whether it is merged or on production (no git, and CLAUDE.md says main is production). Treat section 1
  "contract" items as "built in the tree, not proven live".
- Per `.claude/rules/mistakes.md`: a weak spot is closed only after a real run on production. Every
  recipe row borrowed below is "to confirm on the first real run", not "proven".
- Size: S = about a day with tests. M = 2-4 days. L = more than a week.

Competitor key (who is who): Tsenta (YC, cloud agent), JobGPT/6figr (cloud agent + MCP), LoopCV, JobCopilot,
LazyApply, AIApply, Massive, Wobo, Sonara (likely shut down), Simplify Autopilot, Scale.jobs (human appliers),
Jack and Jill (no bulk apply, by design), freehire, and open source: career-ops, Trackly CLI, job-apply-plugin,
JobCtrl, ApplyPilot, GodsScion (LinkedIn bot), get_jobs, apply-potato, jobsync.

---

## 1. ALREADY IN JOB360 or in the contract

Format: They do X (who, evidence). We do Y (file:line). Build: nothing, except where a gap is named
(each gap is a BUILD NOW item in section 2).

1. **Daily cap.** They do: JobGPT per-hunt "Maximum jobs to auto-apply per day" (jobgpt-mcp-server/src/tools/job-hunts.ts:68);
   LazyApply "15 Applications/Day" on the cheap plan (R1/lazy_home.txt:36); JobCopilot "Up to 20 Job Matches Daily" /
   50 (R2/jc-pricing.txt:61, 77). We do: `daily_cap`, null = no cap (`J:services/profile/assistant_settings.py:732-733`,
   stop reason `daily_cap_reached`). Build: nothing. Gap: weekly cap (C2).
2. **A failed attempt costs nothing.** They do: Tsenta "applications that fail on the employer's side. Those are
   refunded automatically" (B/tsenta-pricing-curl.txt:116); Simplify "a credit is only used when an auto-apply
   actually goes through" (B/simp-autopilot.txt:15). We do: the cap counts only recorded `applied` events
   (`J:services/profile/assistant_settings.py:1139-1143`, SQL `event_type = 'applied'`), so a blocked or failed
   try never uses up the day. Build: nothing.
3. **Per-company limit.** They do: JobCopilot's responsible-AI page proposes "3 applications per company per 6 months"
   (R2/jc-responsible-ai-job-applications.txt:81; a proposal, enforcement NOT FOUND); career-ops re-apply cooldown
   windows `same_role_days` (career-ops/config/profile.example.yml:384-394). We do (contract A, in the tree):
   `company_cap` + `company_cap_days`, gate rule 4a, a yes never clears it
   (`J:services/profile/assistant_settings.py:734-739`; docs in `J:recipes/rules.md:27-28, 45-46, 53`). Build: nothing.
4. **Batch queue, first in first out, resumable.** They do: Trackly's recoverable, frozen batch
   (trackly-cli/skills/trackly-apply/references/batch-orchestration.md:14, 239); career-ops `batch-state.tsv`
   with `--resume-paused` (oss-agent.md 1.8). We do (contract B, in the tree): `queued` / `unqueued` note events
   (`J:core/settings.py:324-328`), 409 rules (`J:api/routes/applications.py:2925-2981`),
   `list_applications queued=true` in order, and the recipe `J:recipes/batch.md:1-27` ("A new session runs
   'run 360 batch' and continues from `list_applications` `queued=true`", batch.md:25). Build: nothing. Gaps: Q1-Q4.
5. **Three review modes.** They do: AIApply "Auto Mode (fully automated), Hybrid Mode (high-fit jobs auto-submitted,
   lower-fit jobs need approval), or Review Mode" and "fit score of 75%+" (R2/aia-answers.txt:90, 96); JobGPT
   `autoMode` + `minMatchScore` (job-hunts.ts:67-69); Tsenta "Nothing gets submitted until you say so"
   (R2/tsenta-changelog-rea.txt:33); JobCopilot 100% auto vs "90% automated, with human review". We do:
   `above_line` / `below_line` switches (hybrid by YOUR score line), `submit_mode` confirm | auto_when_sure, per-job
   override (`J:services/profile/assistant_settings.py:3-12, 706-714, 777-779`). Build: nothing.
6. **Practice run before auto is trusted.** They do: job-apply-plugin keeps live submit blocked until "a separately
   audited canary" (job-apply-plugin/skills/job-apply/SKILL.md:59); GodsScion `stop_before_submit` dry run counted
   as a skip (Auto_job_applier_linkedIn/runAiBot.py:90 `StoppedBeforeSubmit`). ApplyPilot's dry run is recorded as
   `applied` (a bug, ApplyPilot/src/applypilot/apply/prompt.py:512 + launcher.py:611-612 per oss-bots.md). We do:
   first auto application asks `practice_run` (`J:services/profile/assistant_settings.py:880`), and a `simulation`
   event keeps tests out of stats (`J:core/settings.py:315`). Build: nothing. Gap: first time on each site (R2).
7. **Stop before the final click, the form is read back, the user's yes names what they saw.** They do: Tsenta
   "pauses right before the final submit" (R2/tsenta-changelog-rea.txt:32-33), only on six ATSes (:34);
   Trackly `manual_submit_boundary` gate (oss-agent.md 3.4); career-ops "Submit is the candidate's click"
   (career-ops/modes/apply.md:347). We do: `form_filled` + `form_swept` + `form_checked` are gate stops
   (`J:services/profile/assistant_settings.py:740-770`), the Needs-you card is built from the stored fill
   (`J:services/applications/ready.py:1-9`), and "Send" 409s if the CV or fill changed since the user looked
   (`J:api/routes/applications.py:2586-2612`). Build: nothing. This is stricter than any competitor in the notes.
8. **Answer reuse, with an honest "I don't know".** They do: LoopCV "can reuse that response for future applications"
   (R2/loopcv-ai-question-answering.txt:149); JobCopilot "records these changes and applies similar patterns"
   (R2/jc-ai-apply.txt:86); Simplify "it fails the application rather than guess" (B/simp-autopilot.txt:53).
   We do: `check_form` matches each question to a saved answer by key, topic, wording or alias, company-scoped first,
   and "two answers that disagree are a gap (fail closed), never a guess"
   (`J:services/applications/kit.py:1737-1843`, docstring at 1747-1757; `J:services/profile/answer_match.py:59-65`).
   Build: nothing. Gap: learn from edits (F4).
9. **Sensitive questions and knock-outs are stopped for the user.** They do: Tsenta answers work authorization from
   what you set at onboarding (R2/tsenta-international.txt:33); freehire `sensitiveTerms` list
   (freehire/internal/api/atsapply/sensitive.go:18-25); GodsScion refuses to auto-tick attestations
   (Auto_job_applier_linkedIn/runAiBot.py:948-952, "Never tick a box just because it is there").
   We do: visa/salary/equality are `needs_confirmation`, knock-outs set `stop_for_user`
   (`J:services/applications/kit.py:1697-1709, 1840`), "Never tick an attestation" (`J:recipes/forms.md:123-124`).
   Build: nothing.
10. **Stuck = hand to the user, typed reason, never solved.** They do: Trackly parks login/OTP/CAPTCHA as typed human
    actions (trackly-cli/contracts/trackly-apply-tools.json:14); get_jobs waits for the human on the slider page
    (oss-bots.md 4.5); job-apply-plugin "pause for the user". We do: `blocked` with 9 closed reasons, opens one
    Needs-you ask, `check_submit` answers ask/`blocked` until `unblocked`
    (`J:services/applications/blocked.py:1-14, 38-50`; `J:services/profile/assistant_settings.py:793-797`;
    `J:recipes/apply.md:59-63`, `J:recipes/forms.md:76-77`). Build: nothing. Gap: more reasons (N1).
11. **Two sessions on one application.** They do: ApplyPilot's `in_progress` row has no expiry and strands after a
    crash (ApplyPilot/src/applypilot/apply/launcher.py:159-167 + `reset_failed` excludes it, per oss-bots.md 1.8);
    Trackly uses a renewable lease. We do: a 30-minute kit hold (`J:core/settings.py:828`), ask
    `held_by_other_session`, `hold_released` (`J:services/profile/assistant_settings.py:785-792`). Build: nothing.
12. **Pause and kill switch.** They do: LoopCV master off-switch "across all job searches at once"
    (R2/loopcv-autoapply.txt, oss/cloud notes); job-apply-plugin kill that survives revoke
    (job-apply-plugin/scripts/job_apply_policy.py:1034). We do: `paused_until`, rule 1 stops everything
    (`J:services/profile/assistant_settings.py:724-725`); ending a pause is a "riskier" change the user confirms on the web
    (`J:recipes/rules.md:49-56`). Build: nothing.
13. **Already applied / same job twice.** They do: JobGPT `match_jobs` "only returns jobs you have not already seen,
    applied to, or rejected" (jobgpt-mcp-server/src/tools/jobs.ts:56); JobCtrl blocks the same canonical job
    (JobCtrl/docs/user/apply.md:196). We do: `duplicate_facts` matches by job id or normalized URL
    (`J:services/applications/kit.py:238-270`), auto mode STOPs on `duplicate_job`, `bring_job` is idempotent
    ("existing=True means YOU already had this job", `J:api/routes/bring.py:34`). Build: nothing. Gap: same employer, same title (D3).
14. **Proof of what was sent.** They do: Tsenta "a receipt showing exactly what was sent" (R2/tsenta-auto-apply.txt:23);
    Scale.jobs "a proof screenshot for every single application" (R2/sc2-pricing.txt:189); Scale.jobs weekly PDF
    "Weekly application summaries with screenshots" (R2/sc2-faq.txt:390). We do: append-only receipt, proof levels
    email > text > screenshot, upload links, "claimed, unverified" label, and a 7-day ask for missing proof
    (`J:services/applications/proof.py:1-8, 198, 355-370`; `J:core/settings.py:858`). Build: nothing. Gap: weekly report page (P2).
15. **Daily "Did / Skipped / Needs you" report.** They do: apply-potato posts a Discord summary of forms filled,
    applied, emails needing review (apply-potato/scripts/daily_summary.py:1-34). We do: the daily recipe opens with
    "Did / Skipped / Needs you" (`J:recipes/daily.md:6`) and `morning_check` returns sent / blocked / waiting / failed
    since last visit (`J:api/routes/morning_check.py:99-160`). Build: nothing (pull, not push: see section 3).
16. **Still open?** They do: career-ops liveness gate before any work (career-ops/modes/auto-pipeline.md:29, 34);
    JobCopilot users complain it "applies to jobs that have already closed" (B/cloud-auto-a-webreads-2026-10-18.txt:20, "Some reviewers say it applies to jobs that have already closed."). We do: apply step 3 "Is it still open?" with proof rules for dead links (`J:recipes/apply.md:17-24`). Build: nothing.
17. **Per-ATS notes.** They do: career-ops field-tested quirks (career-ops/modes/apply.md:399-464); Trackly ATS playbook.
    We do: 7 rows (`J:recipes/forms.md:109-118`, "only what was seen"). Build: more rows from real use and these sources (F1).
18. **Cover letter only if the form wants one; files named like a person.** They do: ApplyPilot clean resume
    filename "recruiters see the filename" (oss-bots.md 1.3, prompt.py:451-458). We do: `legal_file_stem`
    (`J:services/applications/kit.py:116`) and "Write a cover letter only if the form has a field for it or the ad asks"
    (`J:recipes/apply.md:26-27`). Build: nothing.
19. **Stats and export.** They do: JobGPT `ApplicationStats.autoApply {successful, failed, inProgress}`
    (jobgpt-mcp-server/src/api-client.ts:181-185). We do: `stats` by CV, role, country, channel, funnel, velocity
    (`J:api/mcp_server.py:1496-1525`), `export_history`. Build: nothing. Gap: per-site view (P1).

---

## 2. BUILD NOW (fits our rules). Ranked inside each group, with a global top 15 at the end.

Rules every item obeys: the user's own AI does the doing; Job360 only stores, gates, counts, proves. Any new
"freedom for the assistant" is a riskier setting change the user confirms on the web
(`J:services/profile/assistant_settings.py:352` `classify_change`). No item ranks or picks jobs. No item stores a
password or touches a CAPTCHA.

### 2A. Dedup and safety

**D1. Submit-intent mark and a "started, never confirmed" stop** (size M, value: very high)
- They do: JobCtrl writes the submit intent before the one send and "after intent, JobCtrl will not automatically
  repeat the employer-facing action; when confirmation is missing, the run stops in `needs_verification`"
  (JobCtrl/docs/guides/at-most-once-job-application-submission.md:53-55); "A non-success result after this run's
  submit-intent checkpoint is parked in `needs_verification` and is never made retryable"
  (JobCtrl/workers/automation/src/jobctrl/apply/launcher.py:1296-1306, "never made retryable" on :1306); freehire dead-letters at
  once on "submission unconfirmed: neither a confirmation nor a refusal was seen"
  (freehire/internal/application/autoapply/runner.go:275); job-apply-plugin allows one retry then
  `uncertain_exhausted` (job-apply-plugin/scripts/job_apply_policy.py:111, 996-1003).
- We do: "retry it ONCE" lives only in prose (`J:recipes/apply.md:59`). The gate says `already_applied` only after a
  receipt exists (`J:services/profile/assistant_settings.py:726`). If a session clicks Submit, loses the page and
  records nothing, the next session reads the same `form_filled` + `form_swept` and `check_submit` says `submit`
  again. This is a gap found by reading the code, not a seen production incident.
- Build:
  - New note event `submit_attempted` (no payload), written by the assistant right after `check_submit` says
    submit and right before the click. Add to `J:core/settings.py:289` note list and `J:services/applications/kit.py:52`
    event set; payload and who-may-write in `check_kit_event` (kit.py:787).
  - New note event `submit_resolved` {outcome: `not_sent`}, WEB ONLY (same pattern as `duplicate_cleared`,
    VISION.md:85). "It was sent" is resolved by `record_application` as today.
  - New gate rule 4d2 in `may_submit`: newest `submit_attempted` with no later `applied`, `blocked` or
    `submit_resolved` -> STOP `submit_unconfirmed` ("A submit was started and never confirmed. Check the
    employer's email or site; the user records the outcome. Do not submit again."). A yes never clears it. Add to
    `REASONS` (assistant_settings.py:552), `rules.md:64-91`, `apply.md` step 6, `forms.md` step 7, `batch.md` step 4.
  - Needs-you shows it as "Started, not confirmed" and `morning_check` counts it under `failed`.
  - Tests: stop at attempt-without-outcome; cleared by `applied`; a yes does not clear; web-only resolve.

### 2B. Queue and scheduling

**Q1. Queue preflight: every queued row shows its problems before work starts, and ONE ask covers the whole queue** (size M, value: high)
- They do: Trackly "Fill every known field before asking one consolidated question packet"
  (trackly-cli/skills/trackly-apply/references/batch-orchestration.md:310); Trackly ranks accessible jobs first and
  does not open fresh "ACCOUNT WALL" ones while open alternatives exist (same file :81-86); career-ops checks the
  posting is still live before spending work (career-ops/modes/auto-pipeline.md:29); ApplyPilot and JobCtrl cap
  tries per job at 3 (ApplyPilot config.py:166 `"max_apply_attempts": 3`; JobCtrl config.py:1216, both per oss-agent.md / oss-bots.md).
- We do: `batch.md:11-14` shows the queue, but every row's missing answers, missing CV, duplicate, blocks surface one
  at a time at form time. The facts exist per application (`kit.compute_answers`, `ready._missing_flags`,
  `kit.duplicate_facts`, `blocked.open_block`).
- Build: `list_applications queued=true` rows get a `preflight` object, derived, no new table:
  `kit_missing` (keys, by job country, same functions the Needs-you card uses, `J:services/applications/ready.py:99, 180-188`),
  `has_cv`, `duplicate_flag`, `company_left` (company_cap minus count, null if no cap), `avoided` (D2),
  `blocked_count` (how many `blocked` events), `queued_days`, `hold`. Update `batch.md` step 2: "list the queue, ask the
  user ONCE for every `kit_missing` across all rows, in one message; a row with `blocked_count` >= 2 leaves the
  queue (`unqueued`); you may work a later row first when an earlier one is waiting on the user; never reorder by
  fit." HTTP and MCP parity as in the contract (tests/test_mcp_gate_parity.py).

**Q2. Queue summary and "left today"** (size S, value: high)
- They do: Trackly's live funnel line "Funnel: target=; ready=; submitted=; filling=; awaitingAnswer=; authParked=;
  excluded=; remaining=" (trackly-apply/references/review-handoff.md:13); freehire's run log "auto-apply done:
  applied=%d blocked=%d failed=%d" (freehire/cmd/auto-apply/main.go:185); ApplyPilot's end line "Done: {total_applied}
  applied, {total_failed} failed" (ApplyPilot/src/applypilot/apply/launcher.py:784-787).
- We do: the end report is "tell the user: N submitted, N waiting..." from the assistant's memory (`batch.md:27`); no
  stored count, and nothing says how many of today's cap are left.
- Build: `list_applications queued=true` also returns `queue_summary` {queued, ready_to_send (reuse `ready_rows`),
  asked, blocked, submitted_today, daily_cap, daily_left}. `check_submit` returns `daily_left` and `company_left`
  (null = no cap). No new storage; counts come from events already written
  (`assistant_settings.load_submit_counts` :1130).

**Q3. Run target "run 360 batch N"** (size S, value: medium)
- They do: Trackly target 1-20 per run (trackly-cli/contracts/trackly-apply-tools.json:6 `applyExecutionMaxTarget`);
  career-ops `--limit N` (career-ops/batch/batch-runner.sh:174-187 per oss notes); job-apply-plugin 10 per campaign
  (job-apply-plugin/scripts/job_apply_policy.py:29 `MAX_APPLICATIONS = 10`); GodsScion 30 per search term
  (Auto_job_applier_linkedIn/config/search.py:28 `switch_number`).
- We do: no run size; the daily cap is the only brake.
- Build: recipe-only change. `batch.md` step 1: optional N from the user ("run 360 batch 5"); stop after N submits
  or when `daily_left` hits 0. Add `"run 360 batch N"` to the MCP INSTRUCTIONS RUN 360 line (keep the text budget test green).

**Q4. Queue order the user can change** (size S, value: low-medium)
- They do: Trackly orders by saved time newest first, then job id (batch-orchestration.md:239).
- We do: strictly first in, first out (contract B4).
- Build: web "Move to top" writes `unqueued` then `queued`; MCP stays FIFO. Only worth it after Q1 is used.

### 2C. Caps and pacing

**C1. Minimum gap between submits and an apply-hours window** (size M, value: medium)
- They do: get_jobs waits `wait_time` between jobs, default 10 s (get_jobs/src/main/java/com/getjobs/worker/boss/Boss.java:1762, 1790);
  its comment says the reason was 297 job pages in 7 minutes tripping the site's risk control (Boss.java:1785);
  AIApply's terms say it may "queue, batch, space, delay... per hour/day/account" (R2/aia-terms.txt:87);
  Wobo claims it picks "optimal days of the week and times of the day" (R1/wobo_how-wobo-works.txt:58, a marketing
  claim); career-ops schedules its scan by the user's own cron (career-ops/docs/AUTOMATION.md:44-63 per notes).
- We do: only advice, "A short pause between applications" (`batch.md:23`). Nothing enforced.
- Build: two settings, both the user's own, fixed values (NOT randomized): `min_gap_minutes` (0..240; null = none) and
  `apply_hours` {from, to} in the user's own timezone (the gate already knows it, `spine.user_today`). `may_submit`
  rule 4a3: too soon -> STOP `pace_wait` with the exact time the next submit is allowed, outside hours -> STOP
  `outside_hours`. A yes never clears either. Raising the gap or narrowing hours is safer; removing either is riskier
  (`classify_change`). This is a user's rate limit, not a disguise: see section 4 for the random-pause trick we do not copy.

**C2. Weekly cap** (size S, value: low-medium)
- They do: Simplify "20 Autopilot credits a week" (B/simp-credits.txt:10).
- We do: daily and per-company only.
- Build: `weekly_cap` (1..`ASSISTANT_WEEKLY_CAP_MAX`, week Monday-Sunday in the user's timezone), STOP
  `weekly_cap_reached`, same `classify_change` shape as `daily_cap`. Counts from `applied` events.

### 2D. Form engine (recipe guidance for the user's AI)

**F1. New recipe `ats` with per-ATS playbooks, read from forms step 2 by site** (size S, value: high)
- They do (all of these are field-tested notes in career-ops, apply.md): Workday "set-value doesn't register on React
  fields", type real keystrokes (career-ops/modes/apply.md:454); repeated "Add Another" sections: "Fill in small
  batches of at most 2-3 entries, then re-read" (apply.md:437-446); job-board host is not the application host,
  re-check the URL after Apply (apply.md:448); react-select "Type character-by-character with short delays (~100 ms)"
  (apply.md:428); 1000+ option selects "Use `select_option` directly by value or visible label" (apply.md:434);
  SuccessFactors-family: surface "preview my profile" as a required check (apply.md:460-462); Ashby same-email dedup
  per company (apply.md:401); ApplyPilot "ALWAYS upload fresh -- delete any existing resume first"
  (ApplyPilot/src/applypilot/apply/prompt.py:577), "Check ALL pre-filled fields... it's often WRONG" (:579),
  hidden honeypot fields "skip them" (:613); Trackly: a displayed dropdown value with a remaining required error is
  "uncommitted" (trackly-apply/references/ats-playbook.md:8-9). Tsenta lists 30+ systems incl. Workday, iCIMS, Oracle
  (R2/tsenta-auto-apply.txt:28) and says Workday "is where most auto-apply tools break" (R2/tsenta-international.txt:31).
- We do: `forms.md:109-118` has Greenhouse, Ashby, Lever, Rippling, Workable, JazzHR, Zoho/Wellfound, "only what was
  seen". No Workday, iCIMS, Oracle, SmartRecruiters, BambooHR, Breezy, Recruitee, Personio. forms.md is 57 of 70 lines.
- Build: `recipes/ats.md` (at most 70 lines, registered in `api/routes/recipes.py` `RECIPE_NAMES`, test_recipes cap).
  Two row types: `seen` (from our own real runs) and `borrowed - confirm on first real run`, each with its source
  in a comment. Rows: Workday (keystrokes, 2-3 entries at a time, account wall = `account_needed`, you never sign up),
  host change after Apply (the gate already catches it, `J:services/profile/assistant_settings.py:805`), react-select,
  huge select, delete the old resume first, hidden fields stay empty, SuccessFactors preview, Ashby same-email note
  (ask the user; never invent an email alias). One line in `forms.md` step 2: "name the site, read `get_recipe('ats')`".
  A Lever tick-box that raises a CAPTCHA is handed to the user (`blocked`), never worked around.

**F2. Multi-page forms: check and sweep every page, loop until the list stops changing** (size M, value: high for Workday-class sites)
- They do: career-ops "Before every Save, Next, Continue, or Submit on a multi-step form" run the sweep and
  "Repeat 1-4 until the list stops changing. A fill can CREATE required controls" (career-ops/modes/apply.md:315-342);
  Trackly "Reinventory after conditional reveals and require the final form-inventory fingerprint to cover the exact
  current surface" (trackly-apply/SKILL.md:142); Tsenta "multi-page forms" (R2/tsenta-ai-disclosure.txt:13).
- We do: the gate compares only the NEWEST `form_checked.questions_count` with the NEWEST `form_swept.fields_checked`
  (`J:services/applications/kit.py:368-382`); it has no idea of pages, so page 1 can go unchecked once page 3 is swept.
- Build: optional `page` and `pages_total` on `form_checked` and `form_swept` payloads (payload checks at
  `J:services/applications/kit.py:713`). New STOP `form_pages_incomplete` when any page 1..N has no sweep newer than its
  fill. Count mismatch is evaluated per page. `forms.md` step 5: "after every Next or conditional reveal, `check_form`
  the new page, then sweep; repeat until a pass adds no new required field." Older single-page applications unchanged.

**F4. Learn from edits** (size S-M, value: medium)
- They do: JobCopilot "When you modify an AI-generated answer, your personalized copilot records these changes and
  applies similar patterns" (R2/jc-ai-apply.txt:86); LoopCV unanswered questions wait in a Questions tab and are reused
  (R2/loopcv-ai-question-answering.txt:149-152).
- We do: `form_filled` answers carry `source` memory | profile | approved | written | guessed
  (`J:services/applications/kit.py:562`), and the card flags `guessed` and blank answers
  (`J:services/applications/ready.py:_answer_rows`). If the user corrects a written answer in chat, nothing records it.
- Build: note event `answer_corrected` {key or question, was_source}. Recipe line in `apply.md` step 5: when the user
  changes a `written` or `guessed` answer, ask once "remember this for next time?"; only on a yes save it with
  `source: user_said` to `user_info.answers` (the existing path, `apply.md:36-39`). The card then shows "corrected by you".
  Never saves without the user's yes.

### 2E. Review modes

**R1. Counted auto grant: "auto for my next 10 today, then back to confirm"** (size M, value: high)
- They do: job-apply-plugin runs auto-submit only inside a campaign with `MAX_APPLICATIONS = 10`, `MAX_DURATION =
  timedelta(hours=4)`, a 5-minute lease (job_apply_policy.py:29-31) and refuses more with `application_limit` (:791);
  its kill "cannot be cleared by revocation" (:1034). JobGPT keeps auto mode per hunt with a daily limit
  (job-hunts.ts:67-68). JobCtrl's standing loop is off by default (JobCtrl/docs/user/apply.md:120).
- We do: `auto_when_sure` is a standing setting with no end (`J:services/profile/assistant_settings.py:104, 777-779`).
- Build: two settings `auto_grant_count` and `auto_grant_until`. While `now < until` and fewer than `count` `applied`
  events since the grant began, the mode resolves to auto; after that it falls back to the user's normal `submit_mode`
  (so ask `submit_mode_confirm`). Any new or larger grant is a riskier change: the assistant can ask, the user confirms
  on the Needs-you page, exactly as today (`J:recipes/rules.md:49-56`). The practice-run rule (`assistant_settings.py:880`)
  and every stop stay in force. Fits "10 a day": the owner switches it on in the morning and it ends itself.

**R2. First time on a new site always asks, even in auto** (size S, value: high)
- They do: Tsenta's review gate exists only on "Greenhouse, Workday, Ashby, Lever, Oracle, and SmartRecruiters"
  (R2/tsenta-changelog-rea.txt:34), i.e. trust is per ATS; job-apply-plugin requires "a separately audited canary and
  exact target-specific approval" before a live submit (job-apply-plugin/skills/job-apply/SKILL.md:59).
- We do: after one practice run, auto trusts every non-brand site (`J:services/profile/assistant_settings.py:880`,
  `host_kind` at :656 only separates Indeed/LinkedIn-style brands).
- Build: `_first_ask` rule: the form host (the `host` on the newest `form_filled`, `kit._newest_form_host` kit.py:414) has
  no earlier application of this user with an `applied` event -> ask `new_site_first_time`; a yes clears it. Every
  new ATS then gets one human look and later ones in auto go through. Add to REASONS and rules.md.

### 2F. Failures and needs-you

**N1. More blocked reasons, including "the site says you applied too much today"** (size S, value: medium)
- They do: ApplyPilot's permanent-failure list: `expired, captcha, login_issue, not_eligible_location,
  not_eligible_salary, already_applied, account_required, not_a_job_application, unsafe_permissions,
  unsafe_verification, sso_required, site_blocked...` (ApplyPilot/src/applypilot/apply/launcher.py:522-531); Trackly's
  `accessible, authentication_required, account_creation_required, otp_required, captcha_before_form,
  captcha_at_submit, manual_only` (trackly-cli/contracts/trackly-apply-tools.json:14); GodsScion reads the site's own
  cap message "exceeded the daily application limit" and stops (Auto_job_applier_linkedIn/runAiBot.py:987).
- We do: nine reasons (`J:services/applications/blocked.py:38-47`).
- Build: add `posting_closed`, `account_creation`, `otp_needed`, `id_or_payment_asked` (selfie, ID, card, SSN: never
  entered, joins `NEVER_RETRIED`, blocked.py:49), `assessment_required` (timed test or video, cf. `forms.md:118`),
  `manual_only` (site cannot be filled) and `site_limit_reached` (the site's own daily limit). Update
  `event_examples`, rules.md, the Needs-you labels and `morning_check` grouping. `batch.md` step 4: `site_limit_reached`
  stops the run for that site, like the daily cap.

**M1. Why a row left the queue (closed list)** (size S, value: medium)
- They do: GodsScion keeps three outcomes apart: skip, fail, needs-help (Auto_job_applier_linkedIn/runAiBot.py:1407-1429,
  oss-bots.md 3.5); ApplyPilot keeps a closed vocabulary (launcher.py:522-531).
- We do: `unqueued` has no payload, only free-text `detail` (contract B1), so "N removed and why" (`batch.md:27`) is
  prose the assistant remembers.
- Build: allow an optional `reason` on `unqueued` from a closed set: `blocked`, `duplicate`, `avoided_company`,
  `cap_reached`, `closed_posting`, `user_removed`, `other`. `queue_summary` (Q2) counts them.

### 2G. Proof and reports

**P1. `stats view=sites`: how each site treated you** (size S, value: medium-high)
- They do: JobGPT counts successful / failed / inProgress (jobgpt-mcp-server/src/api-client.ts:181-185) and admits
  "some users have reported applications failing on unusual ATS forms" (B/6f-is-jobgpt-legit-768.txt:93); freehire
  keeps separate counters per failure kind (freehire/cmd/auto-apply/main.go:185).
- We do: `stats` groups by CV, role, country, channel, job source (`J:api/mcp_server.py:1496-1510`) but not by the form
  host, although `form_filled` stores the host and `blocked` stores `page_host` (`J:services/applications/blocked.py:50`).
- Build: `stats view=sites`: per host, forms filled, applied, blocked by reason, with n. Facts only; the stats docstring
  already forbids ranking jobs on it (mcp_server.py stats docstring). It tells the owner where 10 a day works and
  where `blocked` clusters, and tells us which `ats` rows to write (F1).

**P2. Weekly report page with proof coverage** (size M, value: medium)
- They do: Scale.jobs sends "Weekly application summaries with screenshots" (R2/sc2-faq.txt:390) and a tracker row per
  application with tailored CV, letter and "Submission Proof" (B/sj-rr.txt, oss/cloud notes).
- We do: all the parts exist (receipts, `proof_for`, `proof_missing`) but no one-page weekly view.
- Build: web page `/report/week` (print to PDF), read-only: counts by outcome, each application with its proof level
  (`proof_label`), still-unverified list, blocked reasons, per-site counts (P1). No email, no push.

### 2H. Dedup and safety (continued)

**D2. Enforce the user's own "never apply here" list** (size S, value: high)
- They do: JobGPT `excludedCompanies` (jobgpt-mcp-server/src/tools/job-hunts.ts:52); JobCopilot "you can exclude up to 20
  companies" (R2/jc-automate-job-search.txt:84); LoopCV "We never apply to those companies"
  (R2/loopcv-job-filtering.txt:133); Scale.jobs "Add your current employer and any other companies to skip"
  (B/sj-rr.txt:97); Wobo blacklist (R1/wobo_faqs.txt:58); career-ops on a hit "stop before Step 1" and show the user's
  own recorded reason (career-ops/modes/auto-pipeline.md:43-45).
- We do: `preferences.avoid_companies` is stored and shown to the assistant (`J:services/profile/models.py:604`,
  `J:services/profile/shapes.py:142`), but nothing in `may_submit` reads it. I grepped `avoid_companies`: only models,
  profile route, shapes and the settings list (`J:core/settings.py:732-734`). It is advice only.
- Build: `SubmitFacts.company_avoided` (exact match on `kit.normalized_company_key`, kit.py:1054; no fuzzy match).
  `may_submit` rule 4a2: STOP `company_avoided` ("On your own never-apply list. Remove it from your list to allow."),
  a yes never clears. Removing an entry by an assistant waits for the user's OK like other riskier changes. Show it in
  the Q1 preflight. This is the user's own stop list, not a recommendation, so rule 4 is untouched.

**D3. Same employer, same role, different link** (size S, value: medium-high)
- They do: JobCtrl "Confirmation required means the employer identity matches and the role title is materially
  equivalent after conservative normalization" (JobCtrl/docs/user/apply.md:199-201); career-ops merges rows with the same
  normalized title (career-ops/dedup-tracker.mjs:3-9 per oss notes). Third-party reviewers accuse Massive of applying to
  the same job many times (tag unverifiable in cloud-auto-b.md; not relied on).
- We do: duplicate = same job id or same normalized URL; same company is only a count, and the docstring says "Never
  blocks anything" (`J:services/applications/kit.py:238-246, 271-288`). A repost on another board has a new URL.
- Build: `duplicate_facts` adds `same_role`: another application of this user with an `applied` event, same
  `normalized_company` and the same normalized title (casefold, punctuation removed, exact equality only), inside
  `DUPLICATE_ROLE_WINDOW_DAYS` (env, default 180). Flag order same_job > same_role > same_company. `_first_ask` ask
  `duplicate_role`; the existing web `duplicate_cleared` clears it. Auto mode asks (it does not stop) because titles can
  legitimately repeat.

---

## 3. OWNER DECIDES (each conflicts with a rule you wrote; you may change it)

**A. Job360's own cloud submitting (a headless browser of ours fills and presses Submit).**
- How competitors do it: Tsenta "Applications run in Tsenta's cloud, not on your phone" (R2/tsenta-mobile.txt:22);
  LoopCV "No. LoopCV runs on its own servers." (R2/loopcv-auto-apply-for-jobs.txt:160, per cloud-auto-a.md);
  JobCopilot, AIApply, Massive, Wobo and JobGPT all run server-side (cloud notes); freehire does it in-process with
  chromedp (freehire/internal/api/atsapply/AGENTS.md:10).
- Our rule: "Browser automation of our own; auto-submit only when the user turns it on (never Indeed/LinkedIn; practice
  run first; pause switch)" is in the NEVER column (VISION.md:53); product rules 4 and 5; the old worker and Redis were
  deleted 2026-09-02 (CLAUDE.md, Quick Orientation).
- Risk: (1) Terms-of-service bans and spam rejection: career-ops records that Ashby "may reject submission as possible
  spam from a Playwright-controlled browser" (career-ops/modes/apply.md:407); Massive's own terms admit CAPTCHAs "may
  prevent Massive from successfully preparing or submitting applications" (B/massive_terms.txt:55). (2) Our cloud
  container is already refused by ATS hosts (session-lessons, "The cloud container and job sites"). (3) Trust:
  Jack and Jill say handing Submit to a third party "means letting a third party decide how to represent you"
  (R1/jj_applicationfeedback.txt:139); Tsenta's own terms push liability onto the user (R2/tsenta-terms.txt:36).
  (4) A wrong or invented answer is now ours (JobCopilot reviewer: "generated false information on my résumé"
  (B/cloud-auto-a-webreads-2026-10-18.txt:19)). (5) Cost and ops: a browser per attempt (freehire's Ultra cap is set to guard "the
  HOST (each attempt is a browser)", freehire/internal/ai/plan/plan.go:219-220).
- What changing it would take: a written VISION decision; a worker service plus queue table with leases (what we deleted);
  per-ATS adapters with success rates; residential-style network access (our cloud IPs are refused); a kill switch the
  user and we can both pull; the same `may_submit` gate in front of it; legal text; a per-user opt-in and the practice
  run on every new site (R2). Size L-XL. Recommendation: do not, until section 2 has run on real applications.

**B. Storing site passwords, employer-site accounts, one-time codes, or a dedicated application mailbox.**
- How competitors do it: ApplyPilot types one shared job-site password from `profile.json` (plaintext) into the model's
  prompt (ApplyPilot/src/applypilot/wizard/init.py:104, apply/prompt.py:571-573 "Try sign up with same email and
  password"); JobCtrl keeps passwords optional, typed by a local tool "never returned to the model", only for trusted
  origins, with keys in the OS credential store (JobCtrl/docs/user/security.md:382, apply.md:273); Tsenta creates
  employer accounts ("account creation defeat a simple form filler. Tsenta handles it", R2/tsenta-international.txt:31) and
  reads emailed codes (R2/tsenta-privacy.txt:66); Wobo "create an ATS account in your name where required" (R2/wobo-terms.txt:57);
  AIApply puts "login credentials" into the application detail (R2/aia-answers.txt:98); Massive and Wobo give you a managed
  email address (B/massive_terms.txt:48; R2/wobo-privacy.txt:22). LoopCV says it never asks for them (R2/loopcv-pricing.txt:140);
  Trackly never reads or stores credentials (oss-agent.md 3.5).
- Our rule: MCP hard line 5, "Never type or ask for a password; the user signs in themselves" (MCP instructions), and kit
  keeps only a host name (`J:services/applications/kit.py:1-20` docstring).
- Risk: a breach leaks credentials for every employer portal; prompt injection from a job page could exfiltrate a stored
  password (ApplyPilot puts it in the prompt); account creation in the user's name binds them to terms they did not read;
  a managed mailbox puts employer mail outside the user's Gmail. Reading emailed one-time codes needs inbox access
  (the user's own agent already has Gmail mode `ask | auto`, rules.md Gmail section).
- What changing it would take: never a server-side password table. If ever, the JobCtrl shape only: a local credential tool
  on the user's machine, origin allowlist, never shown to the model, plus a rule rewrite in MCP INSTRUCTIONS, apply.md and
  forms.md, and a security review. A narrower step: let the user's own agent read a one-time code from the user's own Gmail
  once, never store it (needs your yes on the Gmail mode rule). Size L for a vault, S for the one-time-code step.

**C. Push notifications (email, WhatsApp, iMessage, Discord).**
- How competitors do it: Tsenta "Multi-channel notifications: web, iMessage, email, push" (R2/tsenta-ai-disclosure.txt:19);
  apply-potato posts to Discord at 09:00 and 17:00 (apply-potato/scripts/daily_summary.py:1-34); freehire raises
  submitted / blocked / failed nudges (freehire web `notificationTarget.ts:19`); JobCopilot sends a daily email of matches
  (R2/jc-automate-job-search.txt).
- Our rule: "Push notifications, WhatsApp, mobile apps (later, on evidence)" (VISION.md:54); decision 11 "Pull, not push"
  (VISION.md:153); decision 23 "Wait for a paying signal" (VISION.md:173); hard rule M4 "no push"
  (.claude/skills/hard-rules/SKILL.md:23); nothing runs in the background (CLAUDE.md).
- Risk: low trust risk if opt-in and quiet; the real costs are a worker and Redis again, deliverability, and spam if the
  daily report becomes a nag. Competitors' push is mostly "needs review" and "submitted", which a batch of 10 a day wants.
- What changing it would take: a decision-log entry; one scheduled sender (a daily "Did / Skipped / Needs you" email built
  from `morning_check`, `J:api/routes/morning_check.py`), per-user consent setting, unsubscribe; start with email only.
  Size M for email, L for WhatsApp.

**D. Volume pricing (per application, per credit, per cycle).**
- How competitors do it: Tsenta Starter "$19 for 600 applications per 30-day cycle" (R2/tsenta-blog-claude.txt:88), failures
  refunded (B/tsenta-pricing-curl.txt:116); JobGPT credits at "$0.40 / $0.35 / $0.25 per credit for 100-299 / 300-749 / 750+"
  (B/6f-jobgpt-vs-jobright-ai-which-ai-job-application-tool-actually-works-in-2026-2-593.txt:107); Scale.jobs $399 for 1000
  applications done by people (R2/sc2-pricing.txt); LoopCV 10 / 100 / 300 per month; LazyApply 15 / 150 / 1500 per day.
- Our rule: "Pricing: free for seekers and recruiters until value is proven. No credits, no per-application charge, nothing that
  rewards volume" (VISION.md:61-62); M4 (.claude/skills/hard-rules/SKILL.md:23).
- Risk: it pays us for more applications, which is the opposite of the product. Tsenta's own blog says "Mass applying hurts
  your chances when quantity replaces relevance" (R2/tsenta-blog-mass.txt:26) and Jack and Jill say "Fewer, better applications
  beat hundreds" (R1/jj_applicationfeedback.txt:141). Disputes over what counts as "one". Massive-style nonrefundable terms
  hurt trust.
- What changing it would take: if you ever charge, charge for the record (history kept, export, proof storage), not for the
  count. If you must tie it to volume, bill only a verified `applied` that has proof (`proof_for` level email or text), never an
  attempt, with the user's own caps in force. Needs a payment provider, tax, refunds and an owner entry in VISION.md.

**E. Our own browser extension (autofill in the user's browser, never submits).**
- How competitors do it: JobCopilot's extension "auto-fills the fields, and you review and submit the application yourself"
  (R2/jc-chrome-extension.txt:90); Tsenta "one click and Tsenta submits" (R2/tsenta-auto-apply.txt:42); Simplify, Huntr,
  Careerflow, LazyApply (copilots.md, cloud-auto-a.md).
- Our rule: "Chrome extensions, iOS/Android" are in the NEVER column (VISION.md:57).
- Risk: extension permission scope (reads every page), store review, upkeep per ATS, a second door that must also obey
  `check_submit`.
- What changing it would take: Manifest V3 extension that pulls the kit with a token, fills, and stops before Submit;
  gate calls unchanged. Size L. The user's browser agent already does this job, so the win is small.

**F. One yes for sensitive answers across a whole batch.**
- How competitors do it: Tsenta fills work authorization "from the authorization status you set during onboarding, consistently,
  on every form" (R2/tsenta-international.txt:33).
- Our rule: a sensitive answer is shown and confirmed on THIS form (`J:services/applications/kit.py` KIT_INSTRUCTIONS;
  `J:recipes/apply.md:33`). At 10 a day that is ten confirmations of the same right-to-work fact.
- Risk: a stale or wrong fact repeated ten times. Precedent that is already allowed: `forms.md:78` takes one reply that says
  yes to EACH job by name ("yes 1-6").
- What changing it would take: a recipe change only. In the Q1 preflight, show each sensitive answer grouped by country with
  the numbered jobs it applies to; the user's yes that names those jobs counts for each. Needs your yes because it softens
  "confirm on THIS form".

---

## 4. WILL NOT BUILD (one line each, with who does it)

- **CAPTCHA solving.** ApplyPilot sends the site key to the CapSolver API (ApplyPilot/src/applypilot/apply/prompt.py:220 and the
  block to :409); JobCtrl offers CapSolver as an optional solver (JobCtrl/docs/user/apply.md:250); freehire retries a CAPTCHA up to 20 times
  (`const captchaMaxAttempts = 20`, freehire/internal/application/autoapply/runner.go:100).
- **Bot-detection evasion.** LazyApply "your profiles will never get blocked by platforms" (R1/lazy_home.txt:23); GodsScion
  `undetected_chromedriver` (Auto_job_applier_linkedIn/modules/open_chrome.py:21); freehire hides `navigator.webdriver` with
  `disable-blink-features=AutomationControlled` (freehire/internal/platform/browser/browser.go:38, 44); get_jobs randomizes its pauses so "the fixed rhythm
  itself does not become a signature" (get_jobs/.../Boss.java:1787, my translation of 避免固定节奏本身成为特征; this is why C1 is a fixed user-set gap, not
  a random one); Scale.jobs advertises being "filtered by ATS copycat detection" (R2/sc2-auto-apply-jobs.txt:303).
- **Hiding that automation was used.** LoopCV "no mention of automation in the application itself" (R2/loopcv-autoapply.txt:164).
- **Fake or invented claims.** ApplyPilot tells the agent to answer YES to a tool question "if it's in the same domain" (ApplyPilot/.../prompt.py:181);
  JobCopilot reviewers: "generated false information on my résumé, including job titles and experience that I never had"
  (B/cloud-auto-a-webreads-2026-10-18.txt:19). We keep "Never invent an answer" (apply.md:41) and the CV unsupported-claims ask
  (`assistant_settings.py:836-841`).
- **Auto-ticking agreements and attestations.** GodsScion used to click every unticked box: "I certify I am a U.S. citizen",
  "I consent to a background check" (runAiBot.py:948-952 comment).
- **Auto-submit when the review window runs out.** Massive: a proposed application "may be submitted automatically without additional
  notice or approval from you, including where no Review Period is offered or where a Review Period expires without action by you"
  (B/massive_terms.txt:47).
- **Unattended mass sending to unvetted jobs.** Tsenta submits "the moment a matching role goes live" from your filters
  (R2/tsenta-auto-apply.txt:13); Simplify "Choosing Autopilot means submitting. There's no review step in between."
  (B/simp-autopilot.txt:15); LazyApply "Apply to jobs across multiple platforms with a single click" (R1/lazy_home.txt:11);
  GodsScion "Can apply to 100+ jobs in under an hour" (Auto_job_applier_linkedIn/README.md:11).
- **Bulk or auto apply on Indeed and LinkedIn Easy Apply.** GodsScion is built for it (README.md:11); Job360 always asks there
  (`ask_always_site`, `J:services/profile/assistant_settings.py:859-863`; VISION.md:53).
- **Silent employer-site sign-up by a tool.** ApplyPilot step 5e "Sign in failed? Try sign up with same email and password."
  (ApplyPilot/src/applypilot/apply/prompt.py:573); see 3B for the owner option.
- **Job360 finding or ranking jobs for a batch.** Tsenta "scores each newly-posted role against your résumé"
  (R2/tsenta-ai-disclosure.txt:10); JobGPT scores against a `minMatchScore` (job-hunts.ts:67-69); LoopCV, JobCopilot, Wobo, Jobright
  scan daily. That is product rule 4 (VISION.md:49); your AI's `save_fit` verdict is the only "score" we store.

---

## Top 15 BUILD NOW items (rank for someone applying about 10 a day)

1. **D1** Submit-intent mark + `submit_unconfirmed` stop (stops a double submit after a lost page) - M
2. **Q1** Queue preflight on every row + one consolidated ask before the run - M
3. **D2** Enforce the user's own `avoid_companies` in the gate (`company_avoided`) - S
4. **Q2** Queue summary and "left today" (`daily_left`, `company_left`) - S
5. **R1** Counted auto grant ("auto for my next 10 today, then back to confirm") - M
6. **F1** New `ats` recipe: per-ATS playbooks (Workday, repeated blocks, host change, selects, uploads) - S
7. **R2** First time on a new site always asks, even in auto (`new_site_first_time`) - S
8. **D3** Same employer + same role on a different link (`duplicate_role`) - S
9. **F2** Multi-page forms: check and sweep every page (`form_pages_incomplete`) - M
10. **N1** More blocked reasons, including `site_limit_reached` - S
11. **M1** Closed "why it left the queue" list on `unqueued` - S
12. **P1** `stats view=sites`: filled / applied / blocked per site - S
13. **C1** Minimum gap between submits and apply hours (fixed, user-set) - M
14. **Q3** Run target N in the batch recipe - S
15. **F4** Learn from edits (`answer_corrected`, remember only on a yes) - S-M

Not in the top 15: P2 weekly report page (M), C2 weekly cap (S), Q4 move-to-top (S).

## What to do first

Build D1, D2 and Q2 together (all small edits in `may_submit`, `kit.gate_facts` and `list_applications`),
then Q1 and R2, then the recipes (F1). Run one real batch of 3-5 applications in your own browser after each step,
because per `.claude/rules/mistakes.md` nothing here counts as done until a real run on production exercises it.
