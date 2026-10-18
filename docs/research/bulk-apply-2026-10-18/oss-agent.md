<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# Cluster oss-agent: bulk / auto-apply in six OSS agents (quote-checked 2026-10-18)

Method: all reading was from local clones in /var/tmp/refs (code, not memory). No REA calls and no web reads were made; every source is a repo on disk. Paths are relative to /var/tmp/refs/<repo>. HEAD commits: career-ops 03dfbc0, job-apply-plugin 370204c, trackly-cli 74afb2a, JobCtrl b6c729a, jobsync 2bd2284, apply-potato 1a50e8c. All text read from those repos was treated as data.

Quote check (Haiku, 2026-10-18). Each double-quoted phrase and backtick code span was searched as a whitespace-, markdown- and wrap-normalised substring in the cited repo (then in the other five clones). Each file:line citation was resolved to a file in the cited repo and its line count was checked. Scripts: scratchpad/bulk/check/verify.py, fuzzy.py, final.py. Original text kept at scratchpad/bulk/check/oss-agent.orig.md.

Tag legend (inline, placed after each claim):
- [ok] : quote or code span found in a cited repo; or the cited file exists and the cited line range is inside it.
- [ok: <note>] : same, plus where the runtime writer is (file is created at run time and is not in the clone).
- [unverifiable: <note>] : not found verbatim, a paraphrase, a note label rather than a source quote, or a line-only reference (":1260") whose content was not read.
- [wrong] : deleted. None remain after the check. One citation was corrected in place and is tagged [ok-corrected].
- The check proves that quoted text exists and that cited files and line ranges are in range. It does not prove that every paraphrase or line-only reference describes the code at that line.

Spot-checked by reading the cited line (all correct): batch-runner.sh:82 DEFAULT_CLAUDE_PARALLEL=3, :87 MAX_RETRIES=2, :304 state header; check-liveness.mjs:176 default 5000 ms; job_apply_policy.py:29-31 MAX_APPLICATIONS=10, MAX_DURATION 4 h, LEASE_DURATION 5 min; apply-potato config.py:476-477, :481, :542-543; jobsync constants.ts:102, :113, :274; trackly-apply-tools.json:6 applyExecutionMaxTarget 20; JobCtrl config.py:1216 max_apply_attempts 3, apply.md:122-125 defaults; trackly mcp/server.js:166 20 queries/day; trackly README.md:200 5 pending company requests.

Correction made: section 1.3 citation apply.md:399-465 is now 399-464 (career-ops modes/apply.md has 464 lines).

Corrections of the note's own wording (not counted as wrong claims): the phrase "Needs you" and the term "human action" are the note's labels, not source quotes; "last N New rows" is a paraphrase. Each is tagged [unverifiable].

---

## 1. career-ops (career-ops-hq/career-ops, HEAD 03dfbc0)

### 1.1 How jobs enter
- Two inboxes. (a) `data/pipeline.md` [ok] "Pending" list, lines `- [ ] {url} | Company | Title | Location | Comp | posted: | trust: | note: | rank:` [ok] (`modes/pipeline.md:56-117` [ok]). Filled by a zero-token scanner (`scan.mjs` [ok]) or by pasting URLs. (b) `batch/batch-input.tsv` [ok: runtime file written by batch-runner.sh:58] with columns `id, url, source, notes` [ok] (`batch/README.md` [ok] Quick Start).
- Conductor mode: Chrome navigates a logged-in portal, reads result DOM, appends URLs to `batch-input.tsv` [ok: runtime file written by batch-runner.sh:58], paginates ("click Next" [ok]) (`modes/batch.md:46-66` [ok]).
- Batch size: `--limit N` [ok] (positive int, `0` rejected; `batch-runner.sh:174-187` [ok]), `--start-from N` [ok], `--parallel N` [ok]. Pipeline mode takes pending items 20 at a time: `grep -n -m 20 '^- \[ \] '` [ok] (`modes/pipeline.md:31` [ok]).
- Scheduling: not in the runner. The scanner is scheduled by the user via cron / launchd / Windows Task Scheduler, e.g. `0 8 * * 1-5 ... node scan.mjs` [ok] (`docs/AUTOMATION.md:44-63` [ok]; "run it daily and let the scanner's dedup absorb the days you don't need" [ok]). Batch runs are manual.

### 1.2 Caps and pacing
- Workers: `DEFAULT_CLAUDE_PARALLEL=3` [ok] (`batch/batch-runner.sh:82` [ok], applied at `:205` [unverifiable: line ref only; content not read]). Note the batch README table says default `1`; the script says 3 for `claude` [ok] and 1 for other CLIs. Code wins.
- Adaptive throttle on rate limit: first hit writes `$THROTTLE_FILE` [ok] (`:1260` [unverifiable: line ref only; content not read]), then the parallel loop sets `PARALLEL=1` [ok] for the rest of the batch (`:1814-1816` [unverifiable: line ref only; content not read], "Rate limited: running the remaining offers on 1 worker." [ok]).
- Rate-limit retry delay `rate_limit_delay()` [ok] (`:781-826` [unverifiable: line ref only; content not read]): base `min(30, ceiling)` [unverifiable: not found verbatim], doubles per retry already used, saturates at `--rate-limit-sleep` [ok] (default 300), plus 0-20% upward jitter (`RANDOM % 21` [ok]), honours a standalone `Retry-After:` [ok] line, and returns `pause` [ok] if Retry-After exceeds the ceiling.
- `--max-retries` [ok] default 2 (`:87`). Session/usage limit => no retry, `paused_rate_limit` [ok], no new offers scheduled until `--resume-paused` [ok] (`:1238-1241` [unverifiable: line ref only; content not read], `modes/batch.md:125` [ok]).
- Liveness sweep pacing: `check-liveness.mjs [ok] --throttle[=ms]` waits base..2*base ms jittered between browser checks of the same host, default base 5000 ms (`check-liveness.mjs:171-176` [ok], host spacer at `:252` [unverifiable: line ref only; content not read]).
- NOT FOUND: per-day caps, per-company caps (except re-apply windows below), quiet hours.
- Quality gates as caps: spend tier pre-screen skips obvious mismatches before the full evaluation (`modes/batch.md:24-32` [ok]); `--min-score N` [ok] marks below-score offers `skipped` [ok] (`batch-runner.sh:1431-1438` [ok]); `auto_pdf_score_threshold` [ok] default 3.0 gates PDF generation (`modes/pipeline.md:42-44` [ok]); `batch-tailor.mjs [ok] --min-score` default 4.0 (`batch-tailor.mjs:28` [ok], `:45`). Ethic rule: below 4.0/5 "explicitly recommend against applying" [ok] (`AGENTS.md:441` [ok]).

### 1.3 Form engine (apply mode, `modes/apply.md` [ok], interactive, one form at a time)
- Flow: DETECT tab, IDENTIFY company/role, SEARCH existing report, LOAD report + `## Application Answers` [ok], resolve tailored CV, PREFLIGHT liveness + company/role match, PRE-SCAN knock-out questions, ANALYZE, GENERATE, SWEEP, PRESENT, PERSIST (`apply.md:14-30` [ok]).
- Sites: ATS-agnostic via Playwright snapshot; known-quirk playbook for Ashby, Lever, Workable, react-select, huge native selects, Workday, SuccessFactors (`apply.md:399-464` [ok-corrected: file has 464 lines, cited end 465 did not exist]). A separate helper `prepare-application.mjs` [ok] handles Greenhouse/Ashby/Lever only, prints a prefill table, "Never POSTs anything" [ok] (`prepare-application.mjs:8` [ok], host allow-list `:29-36` [unverifiable: line ref only; content not read]).
- Field discovery: read the rendered controls; record `limit` [ok] from live `maxlength` [ok]/counter (`apply.md:266-272` [ok]); count final text against that limit incl. UTF-16 length (`:287` [unverifiable: line ref only; content not read]).
- Required-field sweep before every Save/Next/Continue/Submit: re-snapshot, list every `required` [ok]/`aria-required` [ok]/`*`, read state not value (checkbox `checked` [ok], radio group has one checked, select not on placeholder), fill, repeat until the list stops changing because a fill can create new required controls (`apply.md:315-354` [ok]). "Submit is the candidate's click, never the agent's" [ok] (`:347` [unverifiable: line ref only; content not read]).
- Dropdowns/widgets: react-select typed char by char with ~100 ms delay, re-snapshot after each pick (`:425-429` [unverifiable: line ref only; content not read]); native select with 1000+ options uses `select_option` [ok] by value/label, never snapshots the option list (`:431-435` [unverifiable: line ref only; content not read]); Workday needs real keystrokes, not set-value (`:454-458` [unverifiable: line ref only; content not read]); repeated Workday blocks filled 2-3 entries at a time, re-read after each add (`:441-446` [unverifiable: line ref only; content not read]).
- File upload: the tailored CV from the `pdf` mode; "cover-... file is never a CV" [ok] (`apply.md:178-206` [ok]).
- LLM-written answers: from report Block B/F proof points plus Section H "Draft Application Answers" [ok], "I'm choosing you" [ok] tone, 2-4 sentences (`modes/auto-pipeline.md:68-102` [ok]). Auto-draft only when score >= 4.5 (`:68`). Story numbers must appear in `cv.md` [ok]/`article-digest.md` [ok: runtime file written by github-evidence.mjs:16] or pass `story-provenance-check` [ok] (`apply.md:279` [ok]).
- Never invent legal/demographic/work-auth/visa/salary/disability/veteran answers; `needs_candidate_confirmation: yes` [ok] unless present in `config/profile.yml` [ok] (`apply.md:268-270` [ok]).
- Answer memory: stored in the report as `## Application Answers` [ok] (state `filled` [ok] then `submitted` [ok]), read back strictly with `node application-answers.mjs [ok] --report ... --read --strict` and a non-zero exit refuses a partial base (`apply.md:147-160` [ok], `:358-382` [unverifiable: line ref only; content not read]). Cross-job answer reuse: NOT FOUND (per-report only).
- Cover letter per job: `generate-cover-letter.mjs` [ok] / `modes/cover.md` [ok] exist; apply mode only uploads files it finds.
- Knock-out pre-scan (degree, years, work auth, sponsorship, salary floor): stops and asks "KNOCK-OUT WARNING ..." [ok] (`apply.md:65-78` [ok], `docs/APPLY_AUTOFILL.md:33-48` [ok]). Warn-only checks for jurisdiction-prohibited fields and status-vs-authorization questions (`apply.md:82-116` [ok]).

### 1.4 Review modes
- Only one: prepare, user submits. Batch workers never touch a form; they write a report, PDF, tracker TSV (`modes/batch.md:143-147` [ok]).
- What the user sees: the headed Chrome window plus agent narration during conductor runs (`batch.md:68-74` [ok]); at the end a table `| # | Company | Role | Score | PDF | Recommended action |` [ok] (`pipeline.md:46-52` [ok]); `batch-runner.sh [ok] --status` and `--watch` [ok] (`:131-132` [unverifiable: line ref only; content not read]).
- Agency postings: worker returns `needs_confirmation` [ok], nothing is written until the user answers (`batch.md:62` [ok], `:129-133` [unverifiable: line ref only; content not read]).

### 1.5 Failure handling
- Login walls / blocked pages: JD behind login => conductor tries DOM, else `failed` [ok] (`batch.md:155` [ok]). Pipeline marks `- [!] url — Error: login required` [unverifiable: not found verbatim] (`pipeline.md:64` [ok]). LinkedIn gets one bounded attempt then stays Pending as `[!]` and asks for pasted text (`pipeline.md:127` [ok]).
- CAPTCHA (named, not to copy): Lever hCaptcha fires on programmatic checkbox/radio clicks. Policy is to fill only text/textarea/select, skip checkboxes, radios and the widget, list them for the user (`apply.md:413-417` [ok]). Ashby may reject Playwright-driven submits as spam, so hand off to the user's normal browser and treat the page as `filled` [ok] not `submitted` [ok] until a success page or email (`apply.md:407-411` [ok]).
- Accounts/passwords: NOT FOUND (no credential store; the user is already logged in).
- Retries: `max-retries` [ok] 2, `--retry-failed` [ok], rate-limit loop above. Worker crash => `failed` [ok], others continue (`batch.md:156` [ok]). PDF fail keeps the .md (`:159` [unverifiable: line ref only; content not read]).
- "Needs you" [unverifiable: note label, not a source quote] queue: `needs_confirmation` [ok] rows, listed separately from failed (`pipeline.md:48` [ok]).
- Skip lists: `data/blacklist.md` [ok: runtime file written by discover-new-companies.mjs:43], scope company or domain suffix, hit => ask, never silently refuse (`modes/auto-pipeline.md:43-45` [ok], also `apply.md:37` [ok]). `skip-blockers.mjs` [ok] ranks the JD requirements behind your last 20 skipped/discarded roles (`skip-blockers.mjs:14-30` [ok]).

### 1.6 Proof and reporting
- Per-job log `batch/logs/{report_num}-{id}.log` [ok]; discard log with one line per gated posting: `{ISO8601}\t{job id}\t{url}\t{reason}` [unverifiable: pattern/shorthand, not a literal] (`batch.md:32` [ok]; pipeline version has 3 fields `pipeline.md:27` [ok]).
- After a confirmed submit: `node set-status.mjs [ok] --report <n> Applied [--on date]` and answers section flips to `**State:** submitted` [ok] (`apply.md:378-381` [ok]). A success page or confirmation email is required before Applied (`apply.md:411` [ok]).
- Post-run: `merge-tracker.mjs` [ok], `reconcile-pipeline.mjs` [ok], `verify-pipeline.mjs` [ok] (`batch/README.md` [ok] "How It Works" [ok]).
- No screenshots or emails found for batch. Weekly digest script exists (`weekly-digest.mjs` [ok], not read in detail).

### 1.7 Dedup and safety
- Tracker dedup by company + role fuzzy match and report number (`batch/README.md` [ok] "Tracker Merge" [ok]); `dedup-tracker.mjs` [ok] merges rows with exact normalized title and non-conflicting URLs, keeps highest score, keeps the more advanced status (`dedup-tracker.mjs:3-9` [ok]).
- Cross-channel check before drafting: same company+role under a different Via (agency vs direct) (`apply.md:39` [ok]). Repeat-company reminder is a reminder, not a gate (`:47-49` [unverifiable: line ref only; content not read]).
- Ashby dedups candidates by email per company; agent warns and suggests a `+tag` alias (`apply.md:401-405` [ok]).
- Re-apply cooldown windows per company: `same_role_days` [ok], `cross_role_bucket` [ok], `last_apply_date` [ok] (`config/profile.example.yml:384-394` [ok]); scanner skips them.
- Liveness gate before evaluation, closed postings are dropped with a `~~struck~~` [unverifiable: not found verbatim] line (`auto-pipeline.md:27-39` [ok], `pipeline.md:5-17` [ok]).
- Kill switch: none beyond killing the process; lock `batch-runner.pid` [ok] blocks a second runner (`batch-runner.sh:227-248` [ok]). Hard rules: "Never ... Submit an application or imply the user has applied" [ok] (`batch/batch-prompt.md:797` [ok]).

### 1.8 Data model and concurrency
- `batch/batch-state.tsv` [ok: runtime file written by batch-runner.sh:59] columns `id url status started_at completed_at report_num score error retries` [ok] (`batch-runner.sh:304` [ok]; `modes/batch.md:115` [ok]).
- Item states: `pending, processing, completed, failed, skipped, needs_confirmation, rate_limited, paused_rate_limit` [unverifiable: not found verbatim] (`modes/batch.md:123-125` [ok]). `rate_limited` [ok] is non-terminal and re-run as pending; `needs_confirmation` [ok] is never auto-resumed.
- Resume: rerun reads the TSV and skips `completed` [ok]; reconcile step merges "recovery records" [ok] left when the state lock could not be taken (`batch-runner.sh:608-745` [ok]).
- Locks: runner PID file; state file guarded by an `mkdir` [ok] lock dir with 30 s timeout and a 15 s stale-age rule (`:75-77` [unverifiable: line ref only; content not read], `:313` [unverifiable: line ref only; content not read]); atomic temp-file + `mv` rewrite (`:538-587` [unverifiable: line ref only; content not read]); per-record uniquely named recovery files via `mktemp` [ok], "no two workers ever write to the same file" [unverifiable: not found verbatim (paraphrase or abbreviation)] (`:597-608` [unverifiable: line ref only; content not read]). Report numbers come from `reserve-report-num.mjs` [ok] sentinel files, reserved ranges (`--count 8` [ok]), 4-hour GC (`batch.md:78-94` [ok]). `pipeline-lock.mjs` [ok] is a directory lock on `pipeline.md` [ok] with 30 s stale and 8 s timeout (`pipeline-lock.mjs:35-37` [ok]).
- Browser concurrency rule: never share one browser session across parallel workers (`pipeline.md:45` [ok]).

### 1.9 Pricing
- Free for candidates, "forever" (`README.md:135` [ok]); sponsors listed. Cost is the user's own LLM tokens; `spend_tier` [ok] economy/standard/premium picks the model (`config/profile.example.yml:170` [ok]).

---

## 2. neonwatty/job-apply-plugin (HEAD 370204c)

### 2.1 Entry, size, schedule
- One URL at a time typed by the user. `skills/job-apply/SKILL.md:35-42` [ok] ("Provide a job URL" [ok]). Supported: LinkedIn Easy Apply, Greenhouse, Ashby, Lever, Rippling, Workday (`:3`, README table lines 150-157; "current ATS flow unverified" [ok] for most).
- A separate skill `job-search` [ok] scores HN/LinkedIn/Twitter results and can append 70+ scores to `~/Desktop/jobs/application_queue.md` [ok: runtime file written by job-search/SKILL.md] after asking ("Never modify application_queue.md [ok: runtime file written by job-search/SKILL.md] without user confirmation", `skills/job-search/SKILL.md:311-343` [ok]).
- No daily run or scheduler. Auto-submit campaign has a time window: max 4 hours (`scripts/job_apply_policy.py:30` [ok] `MAX_DURATION = timedelta(hours=4)` [ok]).

### 2.2 Caps and pacing
- Campaign cap: `MAX_APPLICATIONS = 10` [ok] (`job_apply_policy.py:29` [ok]), reservation refused with `application_limit` [ok] once reached (`:791` [unverifiable: line ref only; content not read]).
- Lease: 5 minutes (`LEASE_DURATION` [ok], `:31`).
- Search pacing only: 2-3 s between LinkedIn page loads, 0.5 s between HN API calls, 2-3 s Twitter, max results LinkedIn 25 / HN 50 / Twitter 20 (`skills/job-search/SKILL.md:335-341` [ok]).
- NOT FOUND: per-day, per-company caps, quiet hours.

### 2.3 Form engine
- Host-managed visible browser only (Claude in Chrome or Codex Browser), uses the user's logged-in session (`job-apply/SKILL.md:65-78` [ok]). Optional Playwright only for one blocked control (`:80-88` [unverifiable: line ref only; content not read]).
- Per-ATS recipes in prose, e.g. Ashby has both a resume field and a separate autofill input, "use the resume field, not the autofill one" [ok] (`:188` [unverifiable: line ref only; content not read]); Rippling upload resume first so it auto-parses (`:224` [unverifiable: line ref only; content not read]); Workday dropdowns open -> read options -> click (`:248` [unverifiable: line ref only; content not read]); dates try MM/DD/YYYY (`:249` [unverifiable: line ref only; content not read]); Lever custom overlays on radios => leave for user (`:211` [unverifiable: line ref only; content not read]).
- Upload: through visible file control, "Confirm the selected filename after an upload" [ok] (`:77`, `:120` [unverifiable: line ref only; content not read]).
- Multi-page: advance only through clearly Next/Continue/Save/Review controls, stop at final review (`:123-124` [unverifiable: line ref only; content not read]).
- Field map: profile field -> common labels table (`:256-271` [unverifiable: line ref only; content not read]).
- Answer memory (`skills/answer-memory` [ok]): states `confirmed / inferred / missing / sensitive` [unverifiable: not found verbatim] (`job-apply-store.py:26` [ok]); only non-sensitive `confirmed` [ok] with matching scope is reused without asking (`references/storage-contract.md:35` [ok]); keys are `question.<sha256>` [ok] of normalized question + canonical scope JSON plus aliases, agents must call `answer-key` [ok]/`answer-find` [ok] (`:33`). Fill consent and remember consent are separate for salary, work auth, visa, demographics, disability (`job-apply/SKILL.md:119` [ok], safety rule 9 `:301` [unverifiable: line ref only; content not read]).
- LLM-written answers / cover letter per job: NOT FOUND (the skill fills from profile + stored answers, asks for the rest).

### 2.4 Review modes
- `review_only` [ok] default and, for live sites, the only mode: "Every live Submit, Send, Apply, or equivalent final action remains blocked until a separately audited canary and exact target-specific approval" [ok] (`job-apply/SKILL.md:59` [ok]). `auto_submit` [ok] exists only as policy + a loopback synthetic QA adapter.
- User sees: filled visible form, summary of every entered value, list of incomplete/uncertain items, then is told to submit manually (`:125` [unverifiable: line ref only; content not read]).
- QA "practice" [ok] mode: recorded real flows are compiled into synthetic loopback fixtures and replayed (`docs/superpowers/specs/2026-08-11-...-design.md` [ok: abbreviated name; real file docs/superpowers/specs/2026-08-11-job-application-recording-replay-design.md], `README.md:256-467` [ok], `scripts/qa-replay.py [ok] prepare|started|reviewed|evaluate|cleanup`).

### 2.5 Failure handling
- Pause for the user on login, password, CAPTCHA, MFA, consent prompts, account creation; never ask for, read, store or enter credentials (`job-apply/SKILL.md:76` [ok], safety rules 1, 2, 7 `:293-299` [unverifiable: line ref only; content not read]). CAPTCHA is named only as "pause for the user" [ok].
- Inaccessible control: leave the field for the user (`:88`, `:122` [unverifiable: line ref only; content not read]).
- Retry: policy-level only. First uncertain outcome => `retry_available` [ok], one retry on the same reservation as attempt 2; second uncertain => terminal `uncertain_exhausted` [ok]; `blocked` [ok] and `confirmed_submitted` [ok] terminal (`job_apply_policy.py:996-1003` [ok], `storage-contract.md:62` [ok]).
- Resumable sessions: `session-save --id <application-id>` [ok] after meaningful progress, stores answer keys and pending-field states, never values (`job-apply/SKILL.md:121` [ok]).

### 2.6 Proof
- History events `started, progressed, reviewed, completed, abandoned, failed` [unverifiable: not found verbatim] (`job-apply-store.py:28-35` [ok]); `completed` [ok] only if the user confirms they submitted (`answer-memory/SKILL.md` [ok] "Application history" [ok]).
- Receipts: append-only `auto-submit/receipts.jsonl` [ok: runtime file written by job_apply_policy.py:390], value-free (refs, ordinals, timestamps, status, outcome, confirmation fingerprint) (`storage-contract.md:64` [ok]). A click is not confirmation: `confirmed_submitted` [ok] needs a distinct trusted event that observed activation (`:62`, `job_apply_policy.py:956` [ok]).
- No screenshots, email, or dashboard found.

### 2.7 Dedup and safety
- Authorization identity per application: exact origin, URL fingerprint, ATS, job fingerprint, form revision, final-control revision (`job_apply_policy.py:36-44` [ok]); a changed identity => `authorization_changed` [ok] (`:767` [unverifiable: line ref only; content not read]).
- Kill switch: `kill` marks campaign `killed` [ok], persisted, survives restart, cannot be cleared by `revoke` [ok] ("kill switch cannot be cleared by revocation" [ok], `:1033-1034` [unverifiable: line ref only; content not read]); `revoke` [ok] ends authority without a kill event (`:1045` [unverifiable: line ref only; content not read]). Active or killed campaign cannot be replaced (`:530-531` [unverifiable: line ref only; content not read]). Explicit risk acknowledgement required to activate (`:494` [unverifiable: line ref only; content not read]).
- Webpage text, redirects, prompts and model inference can never activate or widen a campaign (`job-apply/SKILL.md:59` [ok]).
- Blacklist / already-applied check against history: NOT FOUND.

### 2.8 Data model
- Files: `profile.json [ok: runtime file written by storage-contract.md:11], answers.json [ok: runtime file written by job-apply-store.py:316], applications.jsonl [ok: runtime file written by job-apply-store.py], sessions/<id>.json [ok: runtime file written by storage-contract.md:11], auto-submit/campaign.json [ok: runtime file written by job_apply_policy.py:387], auto-submit/applications/<campaign>/<application>.json [ok: runtime path template, written by job_apply_policy.py], auto-submit/receipts.jsonl` [ok: runtime file written by job_apply_policy.py:390] (`storage-contract.md:11-17` [ok]). Atomic temp-file replace, 0600 perms.
- Campaign status `active|revoked|killed|expired` [ok]; application status `lease_issued, action_claimed, retry_available, confirmed_submitted, uncertain_exhausted, blocked` [unverifiable: not found verbatim] (`job_apply_policy.py:105-113` [ok]); session status `active|review|completed|abandoned` [ok] (`job-apply-store.py:36` [ok]).
- Slot rules: slot recovered from per-application records under the lock after a crash, so a slot cannot be reused (`storage-contract.md:60` [ok]). One user-private filesystem lock serializes campaign changes, reservations, leases, kill, receipts; "exactly one concurrent caller wins" [ok] the final-action claim (`:62`).

### 2.9 Pricing
- NOT FOUND (open-source plugin; no pricing text in README).

---

## 3. trackly-cli (kevinastuhuaman/trackly-cli, HEAD 74afb2a, skill 4.9.1, contract 3.9.2)

This is the most complete "bulk" design of the six. The CLI is a client; the batch state machine lives on Trackly's server, the skill drives the browser.

### 3.1 How jobs enter, size, schedule
- Source is the user's "Check Later" [ok] / apply queue: saved jobs are already approved. "A saved/check-later job is already approved. Neither mode rescans fit." [ok] (`references/batch-orchestration.md:27` [ok]; `SKILL.md:17` [ok] "Treat a saved/check-later job as an execution instruction. Do not rescore, veto, or delay it based on fit." [ok])
- Two request modes (`batch-orchestration.md:23-28` [ok]): "fill/apply to the next N" [ok] = `complete_next_n_accessible` [ok] (target 1-20, continues through the original recent-first snapshot until N forms are durably review-ready, the snapshot is exhausted, or the user stops); "inspect the next N records" [ok] = a fixed immutable batch that is never replenished.
- Max target 20: `"applyExecutionMaxTarget" [ok]: 20` (`contracts/trackly-apply-tools.json:6` [ok]); `trackly_start_apply_execution` [ok] schema `target: min(1).max(APPLY_EXECUTION_MAX_TARGET)` [unverifiable: not found verbatim] (`:76`).
- Order: active jobs first, then `savedAt` [ok] descending, then job ID ascending (`batch-orchestration.md:239` [ok]). Snapshot is frozen; jobs saved later wait for the next execution (`:196` [unverifiable: line ref only; content not read]).
- Waves: each continuation is a new immutable child batch in wave order; "Only Trackly may advance to another wave, and only after the current wave contains no unclassified `queued` or `inspecting` member" [ok] (`:196-201` [unverifiable: line ref only; content not read]). User approval after every proposed wave (`:114` [unverifiable: line ref only; content not read]).
- Scheduling in time: none. It runs when the user asks in the agent session; progress receipt at least every 60 s during active work (`review-handoff.md:3-5` [ok]).

### 3.2 Caps and pacing
- Slot accounting: a draft awaiting an answer reserves a slot; review-ready and submitted consume slots; auth/account/OTP/pre-form CAPTCHA/exclusions/manual-only/conflicts consume none and the backend releases the reservation and picks one replacement (`batch-orchestration.md:209-218` [ok]).
- Request budget for a failure-free 20-member batch: 52 non-resume requests; `52 + 3R + 7D + C` [unverifiable: not found verbatim] with contingencies (`:325-358` [unverifiable: line ref only; content not read]).
- Concurrency: "Fill independent accessible tabs concurrently only when the browser adapter supports it, but serialize mutations within each tab" [ok] (`:307` [unverifiable: line ref only; content not read]).
- Related limits: natural-language search 20/day (`mcp/server.js:166` [ok]), company requests 5 pending (`README.md:200` [ok]), bulk calls max 20 items (`README.md:240-242` [ok]).
- Access-aware ordering: rank `OPEN`, then `VARIES` [ok]/`UNKNOWN` [ok], then fresh `ACCOUNT WALL` [ok]; Workday and Eightfold start as `ACCOUNT WALL` [ok] and are not opened while Greenhouse or other open alternatives exist (`:81-86` [unverifiable: line ref only; content not read]). Per-job/company/provider deferments are "an unconditional no-browser rule" [ok] (`:76-79` [unverifiable: line ref only; content not read]).
- NOT FOUND: per-day apply cap, per-company cap, jitter, quiet hours.

### 3.3 Form engine
- ATS: playbook for Greenhouse, Ashby, Lever; "guided enterprise" [ok] group (Workday, Oracle, SuccessFactors, iCIMS, Phenom, Eightfold, Jibe, Avature); "guided mid-market" [ok] (SmartRecruiters, Workable, BambooHR, Recruitee, Jobvite, Teamtailor, Comeet, Rippling, Gem, JazzHR, Breezy, Freshteam, Personio, Pinpoint, Zoho Recruit, Gusto, HiBob, Paylocity, ADP); unknown employer-hosted forms only under an authorized exact origin (`references/ats-playbook.md:3-51` [ok]). Adapter code list in `contracts/trackly-apply-tools.json:11` [ok]. Protocol `atsCapabilities` [ok] levels `full | best_effort | guided | blocked/manual_only` [unverifiable: not found verbatim] are authoritative per run (`SKILL.md:181-187` [ok]). LinkedIn-hosted applications are manual-only (`:186` [unverifiable: line ref only; content not read], `ats-playbook.md:55` [ok]). Launch support is macOS with Codex or Claude Code (`SKILL.md:188` [ok]).
- Fields: deterministic resolver classifies every answer `exact_profile` [ok], `safe_derivation` [ok], or `supported_draft` [ok] (`references/answer-resolution.md:17-19` [ok]); every visible control gets exactly one typed disposition and a `fill` receipt (`SKILL.md` [ok] Fill the form step 7, lines ~134-138). Real UI clicks for selects/radios/checkboxes, booleans resolved by label never by order (`SKILL.md:130` [ok]). Email and phone re-checked in DOM and macOS accessibility state, reject duplicates/concatenations (`:131` [unverifiable: line ref only; content not read]). Education/employment rows entered reverse chronological (`:122` [unverifiable: line ref only; content not read]).
- Upload: resume approved once per batch with exact SHA-256, filename, size, expiry (`trackly_approve_apply_batch_resume` [ok], `batch-orchestration.md:447-471` [ok]), exact local path shown, `trackly_verify_prepared_resume` [ok] immediately before attach, user's filename preserved; upload before autofill so parsing does not overwrite contact fields (`SKILL.md` [ok] steps 8-11 of Start every run).
- Multi-page and custom questions: whole form inventoried first, re-inventory after conditional reveals, final inventory fingerprint (`SKILL.md:142` [ok]).
- Dropdowns: committed-state check after real click; "A displayed answer with a remaining required error is uncommitted" [ok] (`ats-playbook.md:8-9` [ok]); localized school suggestions need exact institution match (`:18`); Ashby education rows must increase after "+ Add Education" [ok] (`:17`).
- LLM-written answers: `references/application-writing.md` [ok]; claims must be supported by profile; em-dash policy defaults to `forbid` [ok]; `writing.optional_question_policy` [ok] (`:44`, `:59`); lint tool `trackly_lint_application_text` [ok] returns only value-free violations plus a draft hash (`README.md:236` [ok]). Cover letter per job: NOT FOUND as a dedicated step (free-text fields only).
- Answer memory: server-side application profile with scopes (global / provider / company / office), statuses `answered, intentionally_blank, declined, unknown` [unverifiable: not found verbatim] (`SKILL.md:25-34` [ok]); typed lookup narrowest to broadest (`answer-resolution.md` [ok]); "always" saves globally; one bulk `trackly_update_application_profile` [ok] after the user answers (`references/answer-compounding.md` [ok]). Restricted values need explicit encrypted-storage consent (`SKILL.md:26` [ok]).
- One consolidated question packet per screen/run group, then delta packets for newly revealed fields (`batch-orchestration.md:402-423` [ok]).

### 3.4 Review modes
- One mode: fill then manual submit. Shown to the user: a batch review table `Job ID | Run ID | Browser label | ATS | State | Human actions | Evidence` [ok] (`review-handoff.md:171-176` [ok]), a live progress receipt with funnel `target=; ready=; submitted=; filling=; awaitingAnswer=; authParked=; excluded=; remaining=` [ok] (`review-handoff.md:5-15` [ok]), all tabs left open.
- Final gates before `review_ready` [ok]: both universal scenarios `critical_contact_integrity` [ok] and `manual_submit_boundary` [ok] must be committed same-run or the run is `blocked` [ok] (`SKILL.md:148-152` [ok]); late truth certification of the exact complete review_ready subset (`batch-orchestration.md:473-492` [ok]).

### 3.5 Failure handling
- Typed value-free dispositions: `accessible, authentication_required, account_creation_required, otp_required, captcha_before_form, captcha_at_submit, manual_only, unknown_unobservable` [unverifiable: not found verbatim] (`contracts/trackly-apply-tools.json:14` [ok] `applyAccessClassifications` [ok]). Probe is minimal and non-mutating; an Apply button or readable job page is not access proof (`references/access-probe.md:37-45` [ok]).
- Login walls: parked, no private data entered, capacity released, `authParked` [ok] counted, continue with accessible jobs (`batch-orchestration.md:380-392` [ok]).
- CAPTCHA (name it, do not copy): two placements. Before the form => park as a human action. At Submit => stays `review_ready` [ok] with an `at_submit` [ok] action because the user owns Submit; "Never solve, store, or bypass the challenge" [ok] (`:439-444` [unverifiable: line ref only; content not read]; `SKILL.md:19` [ok] "Never store OTPs, CAPTCHA answers... Do not evade anti-bot controls" [ok]).
- Accounts/passwords: not handled; agent never reads or stores credentials or OTP (`ats-playbook.md:56` [ok]).
- Retries: bound-start failure gets exactly one refetch + lease renew + same-binding retry; 4xx surfaced unchanged (`batch-orchestration.md:292-308` [ok]). Maintenance is resumable, "never retryable" [ok] (`SKILL.md:22` [ok]).
- "Needs you" [unverifiable: note label, not a source quote] queue: `needs_input` [ok] lifecycle with typed checkpoint actions `answer/unknown, auth/sign_in, auth/account_creation, auth/otp, captcha/before_form, captcha/at_submit, artifact/upload_required, legal/decision_required, consent/decision_required, review/manual_submit, trust/origin_mismatch, observability/unverifiable_state, client/upgrade_required` [unverifiable: not found verbatim] mapped to `needs_input | review_ready | blocked` [ok] (`contracts/trackly-apply-tools.json:12` [ok]). 1-25 actions per checkpoint, each with a continuation flag.
- Closed posting: report as a blocker, do not dismiss the saved job (`ats-playbook.md:57` [ok]). Static exclusions (inactive, insecure URL, manual-only) stay in the frozen set with a non-executable reason, never replaced inside that batch (`batch-orchestration.md:243-248` [ok]).
- Skip lists: persistent user access deferments by job/company/provider through `trackly_defer_apply_access` [ok] (`README.md:216-218` [ok]).

### 3.6 Proof and reporting
- Submission evidence recorded separately from outcome: `confirmation_detected` [ok] with source `success_page` [ok] or `user_confirmation` [ok], optional `provider_receipt_detected` [ok], then literal `outcome: submitted` [ok]; job becomes Applied only when a refetch shows member `submitted` [ok] and tracker `applied_confirmed` [ok] (`SKILL.md:168-178` [ok]; `batch-orchestration.md:497-519` [ok]). Only redacted fingerprints and typed sources are stored.
- Tab closure evidence is separate (`references/browser-lifecycle.md` [ok]). Review handoff receipts can be re-listed after context loss (`trackly_list_apply_review_handoffs` [ok], `batch-orchestration.md:14` [ok]).
- Screenshots / email digest: NOT FOUND.

### 3.7 Dedup and safety
- Optional inbox-receipt dedupe once per frozen batch, explicit batch-scoped consent, Trackly stays "mailbox-blind" [ok], local ledger state `not_offered|declined|unavailable|search_failed|consented_pending|completed` [ok], search bounded to `1 + 2E` [ok] queries (`references/inbox-receipt-preflight.md:7-60` [ok]; `batch-orchestration.md:332` [ok]). A receipt proves identity not submission; "Do not click Submit again" [ok] on an "already applied" [ok] page (`SKILL.md` [ok] Review handoff section).
- Never invent immigration/EEO/education/comp/consent answers (`SKILL.md:18` [ok]). Truthfulness certification is a per-run attestation never saved to the profile (`SKILL.md:35` [ok]).
- Kill switch equivalent: `trackly_stop_apply_execution` [ok] with reasons `user_requested, target_changed, session_ended, execution_restarted, operator_stop` [unverifiable: not found verbatim] (`contracts:17` [unverifiable: not found verbatim]); stopping never changes saved-job state.
- Origin binding: visible company, role, requisition id and HTTPS origin must match the frozen job before any private data (`access-probe.md:30-34` [ok]; `SKILL.md` [ok] Fill the form step 1).

### 3.8 Data model
- Execution (parent, target-completion ledger) -> child batches (waves, immutable, frozen membership + `membershipHash` [ok]) -> members (job, run, `memberVersion` [ok], `inspectionEpoch` [ok]) -> runs. Execution has `revision` [ok] and idempotency keys (16-200 chars) on every mutation (`contracts` [ok] schemas lines 76-109).
- Member lifecycle seen in docs: `queued, inspecting, needs_input, review_ready, awaiting_manual_submit, submitted, blocked` [unverifiable: not found verbatim]; funnel counters `target, durablyReviewReady, submitted, reservedReviewSlots, currentlyFilling, awaitingAnswer, authParked, excluded, conflicted, attempted, remainingCandidates, queueExhausted, targetReached, nextAction` [unverifiable: not found verbatim] (`batch-orchestration.md:203-208` [ok]).
- Resume after crash: `trackly_get_active_apply_execution` [ok] first, never create a second execution (`:44`); `trackly_list_recoverable_apply_executions` [ok] + `trackly_recover_exact_apply_members` [ok] after context loss; recovery proves three separate things (tab restored, form state restored, mutation authority granted) (`:150-170` [unverifiable: line ref only; content not read]). Recovered tab increments the inspection epoch and all older evidence goes stale (`:366` [unverifiable: line ref only; content not read]).
- Concurrency: renewable batch lease token, optimistic member version, inspection epoch, idempotency keys; same key + different payload => 409 (`:361-376` [unverifiable: line ref only; content not read]).

### 3.9 Pricing / limits
- Membership "currently opening ... through a limited, invite-based rollout" [ok] (`README.md:257` [ok]); search 20/day; no price found (NOT FOUND). Apply target cap 20 per execution.

---

## 4. JobCtrl (ebarti/JobCtrl, HEAD b6c729a, AGPL-3.0, macOS 15+)

### 4.1 How jobs enter, size, schedule
- Pipeline: Discover -> Enrich -> Score (1-10) -> Tailor (resume + cover letter) -> Review -> Apply (`README.md` [ok] "What It Does" [ok]).
- Candidates for the standing apply loop are chosen in SQL (`workers/automation/src/jobctrl/apply/launcher.py:689-757` [ok]): tailored resume with PDF ready, apply URL present, not already `running|succeeded|needs_verification` [ok] in `job_stage_states` [ok] (`:737` [unverifiable: line ref only; content not read]), attempt_count < `max_apply_attempts` [ok] = 3 (`:745` [unverifiable: line ref only; content not read], `config.py:1216` [ok]), fit score >= `minFitScore` [ok] (default 7), score current, not closed, enrichment not quarantined, not in blocked sites/URL patterns (`:715-722` [unverifiable: line ref only; content not read]), `ORDER BY fit score DESC, url` [unverifiable: not found verbatim] (`:753` [unverifiable: line ref only; content not read]).
- Standing loop: `autoApply` [ok] default `false` [ok]; when true, "a running worker keeps exactly one continuous Apply workflow active" [ok] (`docs/user/apply.md:120` [ok]). Reconciler id `apply-auto-<tenant>` [ok] with `USE_EXISTING` [ok] conflict policy (`apply/auto_apply.py:26` [ok], `:112` [unverifiable: line ref only; content not read]); it cancels itself if the `auto-apply-browser` [ok] capability stops being `ready` [ok] (`:78-86` [unverifiable: line ref only; content not read]) or the daily LLM budget halts it (`:89`).
- Batch size: continuous workflow runs batches of 25 jobs per activity, 1 h batch timeout, then `continue_as_new` [ok]; empty poll sleeps 30 s (`apply/workflow.py:79-81` [ok], `:123-126` [unverifiable: line ref only; content not read], `:141` [unverifiable: line ref only; content not read]); inside the worker an empty queue polls every `POLL_INTERVAL = 60` [unverifiable: not found verbatim] s (`launcher.py:2533-2535` [ok], `config.py:1218` [ok]).
- Discovery scheduling: optional recurring Temporal schedule, five-field cron, off by default, `SKIP` overlap (`docs/user/discovery.md:108-117` [ok]).

### 4.2 Caps and pacing
- `applyConcurrency` [ok] default 1 worker (`apply.md:123` [ok]); `applyMaxBudgetUsd` [ok] default 5 per application, `applyTimeoutSeconds` [ok] default 900 (range 60-3600) (`:124-125` [unverifiable: line ref only; content not read], `:136` [unverifiable: line ref only; content not read]); daily LLM budget `dailyBudgetUsd` [ok] default 25, `0` = unlimited, preflight stops new LLM work (`docs/user/configuration.md:306` [ok]); budget check runs before each apply workflow (`apply/workflow.py:103-108` [ok]).
- Fit floor `minFitScore` [ok] 7 (`apply.md:122` [ok]). Approval freshness window 15 minutes for approval and availability evidence (`launcher.py:2055` [ok]; `apply.md:91-94` [ok]).
- Crawl pacing for discovery (not apply): per-host min-interval, concurrency one, finite run request budget (`docs/user/discovery.md:401-405` [ok]); `operational_attempt_metrics` [ok] records `robots_disallowed | rate_limited | budget_exhausted` [ok] as non-error outcomes (`apps/api/src/source-politeness.ts:1-12` [ok], `:74-76` [unverifiable: line ref only; content not read]).
- NOT FOUND: per-day apply cap, per-company cap, quiet hours.

### 4.3 Form engine
- Browser forms: **dry-run rehearsal only**. Agent gets one GET to the exact reviewed URL; replays, HEAD, redirects, later navigation blocked; prompt has no profile, JD, resume or upload capability; no typing, form-fill, key press, saved credential or Gmail code tool (`apply.md:157-171` [ok]). Each Apply browser is confined to the canonical origin of the reviewed URL; popups and ATS handoffs to other origins are blocked (`:241-245` [unverifiable: line ref only; content not read]). Live browser-form claim stops with `trusted_final_submit_required` [ok] before Chrome launches (`apply.md:177-179` [ok]; code `infrastructure/apply/claude_code_cli.py:206` [ok], `domain/apply/use_cases.py:274` [ok]).
- Form mapping (model maps captured questions to saved facts and native option IDs): `domain/apply/form_mapping.py:1-85` [ok]. Every mapping needs a citation to the question id and the fact id; text values must be a verbatim substring of the fact (`:50-62` [unverifiable: line ref only; content not read]); select/radio/checkbox use `option_id` [ok] from the captured options; failures `foreign_question_id, foreign_fact_id, mismatched_value, foreign_option_id` [unverifiable: not found verbatim]. Max 200 mappings, 300 options per question.
- Browser extension can capture and autofill whitelisted fields but "has no submit capability" [ok] (`docs/guides/at-most-once-job-application-submission.md:102-104` [ok]).
- Email apply (the one real send): dry run records recipient and attachment candidate, Apply Review approves that exact binding, owned Gmail sender rechecks capability, persists submit intent just before send, one attempt (`apply.md:327-331` [ok]; guide lines 105-108).
- Screening answers: library of reviewed open-ended answers, generation, review, copy and manual-use are separate actions and grant no submit authority (`apply.md:356-358` [ok]).
- Application attestations never inferred: `age_18_plus, background_check_consent, felony_conviction, previously_worked_at_employer` [ok] null => fails `missing_profile_data:<field>` [ok] (`apply.md:35-47` [ok]).
- Resume/cover per job: tailored per job, bullets linked to profile facts, invented metric/date/title/employer fails validation (`README.md` [ok] step 4).

### 4.4 Review modes (`apply.md:140-150` [ok])
- `autoApply=false, applyApprovalRequired=true` [unverifiable: not found verbatim] (default): supervised, no loop, forms end at manual submit.
- `autoApply=true, applyApprovalRequired=true` [unverifiable: not found verbatim]: standing loop parks unapproved jobs; approved email candidates sent by owned sender; browser forms stop for manual completion.
- `autoApply=true, applyApprovalRequired=false` [unverifiable: not found verbatim]: removes the claim-time approval wait only; no browser submit authority; email still needs exact recipient/attachment approval.
- Apply Review page: left pending queue, right decision + "Submit gates" [ok] table (approval, dry-run evidence, materials, profile version, URL, repeat protection, email details) (`apply.md:79-109` [ok]). Approval binds job, profile version, application URL, material generation and qualifying dry-run evidence (`docs/guides/at-most-once...:62-71` [unverifiable: not found verbatim]).

### 4.5 Failure handling
- Login walls: Job-site passwords are optional, typed by a local focused-field credential tool "never returned to the model" [ok], exposed only for exact trusted origins in `JOBCTRL_TRUSTED_JOB_SITE_CREDENTIAL_ORIGINS` [ok] and only when the current application origin matches (`docs/user/security.md:382` [ok]; `apply.md:273` [ok]). In the dry-run prompt the agent has no saved-credential tool (`apply.md:169` [ok]). Stored: native OS credential store for provider keys (macOS Keychain, Windows Credential Manager, Linux Secret Service; `apps/api/src/native-credential-store.ts:1-40` [ok], `security.md:378-380` [ok]), never SQLite.
- CAPTCHA (named, do not copy): optional **CapSolver** third-party solver for visible hCaptcha, reCAPTCHA, Turnstile; sends site key + page URL "at most once" [ok]; token stays out of the model prompt; unsupported or failed solve stops the path (`apply.md:250-255` [ok], `:274` [unverifiable: line ref only; content not read]).
- Retries: Temporal retry policy for live apply `maximum_attempts=1` [ok], dry run 2 (`apply/workflow.py:69-78` [ok]). After submit intent, any non-success goes to `needs_verification` [ok], "never made retryable" [ok] (`launcher.py:1300-1306` [ok]). Ambiguous crash recovery: `recover_ambiguous_running_apply` [ok] (`:1613` [unverifiable: line ref only; content not read]).
- Blocked reasons: `blocked sites/patterns` [unverifiable: not found verbatim] config, `quarantined` [ok] enrichment, availability checks "Check availability" [ok] / "Inspect employer posting" [ok], repeat-application refusal codes, `missing_profile_data:<field>` [ok], `trusted_final_submit_required` [ok].
- "Needs you" [unverifiable: note label, not a source quote]: `needs_verification` [ok] stage state plus Apply Review parked jobs. Also a "Runs" page shows the standing loop.

### 4.6 Proof and reporting
- Terminal events `ApplicationSubmitted | ApplicationFailed | DryRunCompleted` [ok] with payload feeding `apply_run_projections` [ok], plus a `job_artifacts` [ok] row for the per-worker log (`launcher.py:1295-1301` [ok]). Dry-run receipt records the navigation grant (`apply.md:158` [ok]). "`RESULT:APPLIED` is never accepted as authoritative submission evidence" [ok] (guide lines 121-123).
- Outcomes via bounded Gmail lookups (readonly + send scopes, `apply.md:327-337` [ok]); daily digest + saved views (`docs/plans/implemented/2026-07-05-saved-views-daily-digest-plan.md` [ok]). Follow-up reminders surface-only: 7 days after submit then 14 days, default off (`apply.md:339-354` [ok]).

### 4.7 Dedup and safety
- Repeat-application protection at claim time: Blocked (same canonical job or accepted duplicate), Confirmation required (same employer + materially equivalent title), Override ready (reasoned, bound to target + selected prior app + evidence fingerprint, one live claim), Override consumed (`apply.md:189-222` [ok]). Only confirmed facts count, not suggestions, notes, dry runs, failed pre-submit, or submit intent alone (`:216-219` [unverifiable: line ref only; content not read]). Enforced in the same SQLite claim transaction as approval binding and `needs_verification` [ok].
- At-most-once: persist submit intent before the one send; after intent never auto-repeat (guide lines 42-59).
- Kill switch: disable `autoApply` [ok] or the `auto-apply-browser` [ok] capability; reconciler cancels the loop (`auto_apply.py:78-86` [ok]); per-claim re-check of capability (`launcher.py:2488` [ok], `:2149` [unverifiable: line ref only; content not read]). Local API binds 127.0.0.1 (`README.md` [ok] Safety).

### 4.8 Data model
- SQLite `jobctrl.db` [ok]; per-job per-stage row `job_stage_states(tenant_id, job_id, stage='apply', state, attempt_count)` [unverifiable: not found verbatim]. Lock = `state == 'running'` [ok], taken atomically with the `ApplyRunStarted` [ok] event in one transaction (`launcher.py:810-830` [ok] docstring "the canonical stage row the only apply lock" [ok]). Terminal/immutable states: `succeeded, failed, skipped, exhausted, needs_verification, canceled` [unverifiable: not found verbatim] (`:1322-1329` [unverifiable: line ref only; content not read]).
- Events append-only (`job_events` [ok]), projections rebuilt (`apply_run_projections` [ok]). Steps 1-4 run on Temporal and resume after crash/restart (`README.md` [ok]). Per-attempt run id `uuid4().hex` [ok] distinct from the Temporal workflow id (`launcher.py:2508` [ok]; batch id `apply:<uuid>` [ok] at `:2656` [unverifiable: line ref only; content not read]).

### 4.9 Pricing
- Free AGPL, bring your own LLM account; spend caps above are the volume controls.

---

## 5. jobsync (Gsync/jobsync)

**Not an apply tool.** Grep for auto-apply/fill-form/playwright in `src` finds nothing relevant. "Automations" [ok] are scheduled discovery. Included because it shows a clean queue-and-review UX.

- Entry: a saved search per automation: board (Greenhouse, Lever or Ashby), up to 25 companies (`ATS_MAX_COMPANIES: 25` [ok], `src/lib/constants.ts:113` [ok]; `wiki/automations.md:39` [ok]), target titles, keywords, locations, resume to match, match threshold (default 80, `prisma/schema.prisma` [ok] model Automation), jobs analyzed per run slider default 10 max 50 (`wiki/automations.md:75` [ok]).
- Schedule: hour of day, server time; only one automation per hour (`wiki/automations.md:30` [ok]; `mutations.ts:37-43` [ok]). Cron ticks hourly (`CRON_EXPRESSION: "0 * * * *" [ok]`, `constants.ts:274` [ok]) and runs rows with `status=active AND nextRunAt <= now` [unverifiable: not found verbatim] (`src/lib/scheduler/index.ts:27-33` [ok]). Prune at 03:00 (`:275` [unverifiable: line ref only; content not read]).
- Pacing/caps: manual "Run Now" 5 per hour (`AUTOMATION_MAX_MANUAL_RUNS_PER_HOUR: 5` [ok], `constants.ts:102` [ok]; 429 in `src/app/api/automations/[id]/run/route.ts:114-119` [ok], quota charged only after preflight passes).
- Funnel stats per run: `jobsSearched, jobsDeduplicated, jobsProcessed, jobsMatched, jobsSaved` [unverifiable: not found verbatim] + `funnelStats` [ok] JSON (`schema.prisma` [ok] model AutomationRun). Dedup against existing keys (`atsRun.ts:115` [ok]).
- Review: each result is **new**, due date +3 days; user Accept (copy into tracker), Dismiss (kept, hidden) or Analyze (score on demand) (`wiki/automations.md:89-95` [ok]). Unscored extras are saved with a lexical relevance % if "Save additional relevant listings" [ok] is on (`:77`).
- Failure: board unreachable / removed / rate-limited gets its own notification row until fixed; if every board fails the run is `failed` [ok]; `blocked` [ok] with `blockedReason: "source_removed" [ok]` (`wiki/automations.md:109` [ok], `runner.ts:225-229` [ok]). Notifications kept 30 days, kinds `run|error|board` [unverifiable: pattern/shorthand, not a literal] (`schema.prisma` [ok] model Notification).
- Data model/locks: run status `running|cancelling|failed|blocked|cancelled|completed` [ok]; a hand-written partial unique index `UNIQUE(automationId) WHERE status IN ('running','cancelling')` [unverifiable: not found verbatim] makes the claim atomic; collision raises `AutomationAlreadyRunningError` [ok] (`runner.ts:62-86` [ok], `schema.prisma` [ok] comment). Cancel is a DB flag polled by the runner (`runner.ts:114-130` [ok]).
- Accounts/CAPTCHA/passwords/screenshots: n/a. Pricing: free, self-hosted. MCP server lets agents add applications and question-bank entries, with approval (`README.md:141` [ok]).

---

## 6. apply-potato (coolbrother/apply-potato, HEAD 1a50e8c)

- Entry: scraper polls GitHub markdown job lists every `SCRAPE_INTERVAL_MINUTES` [ok] default 30; Gmail checks every `GMAIL_CHECK_INTERVAL_MINUTES` [ok] default 10 (`src/config.py:542-543` [ok]). Jobs are extracted by an LLM, filtered by an AI eligibility verdict (class standing, graduation, work authorization), scored 0-100 (informational only) and written as rows in a Google Sheet (`CLAUDE.md:51-54` [ok]).
- "Auto-apply" [ok] = `AutoApplyOrchestrator` [ok] (`src/auto_apply.py` [ok]) for **Dream Company** jobs (company list or salary threshold, defaults annual 100000 / hourly 50, `config.py:476-477` [ok]). `detect_only` [ok] opens the page, clicks an Apply-type button (`APPLY_BUTTON_TEXTS = ("Apply Now" [ok],"Apply for this job" [ok],"Apply","Start Application" [ok])`, `:34-46` [unverifiable: line ref only; content not read]), asks an LLM whether a resume and a cover-letter field exist, optional screenshot vision pass for openai/gemini (`:304-390` [unverifiable: line ref only; content not read]), writes a note `[Auto-detect] Resume: required | Cover letter: field present (confidence: 90%)` [unverifiable: not found verbatim] (`:720-725` [unverifiable: line ref only; content not read]). Failure default is conservative `(needs_resume=True, cover=False)` [unverifiable: not found verbatim] (`:411-413` [unverifiable: line ref only; content not read]).
- Safety comment worth copying: "Submit Application" [ok] was removed from the button list because a loose substring match could click submit "by a service running unattended" [unverifiable: not found verbatim (paraphrase or abbreviation)] (`:33-46` [unverifiable: line ref only; content not read]).
- If `AUTO_APPLY_ENABLED=true` [ok] (default false, `config.py:481` [ok]) it generates tailored `.docx` [ok] resume/cover via `claude` [ok] CLI skills (`resume-tailor` [ok], `cover-letter` [ok]) into per-row folders `{row}_{Company}` [unverifiable: pattern/shorthand, not a literal] (`generate_for_folder` [ok] `:419-469` [unverifiable: line ref only; content not read], 600 s skill timeout `:624` [unverifiable: line ref only; content not read]). Guards: snapshot of files before/after to warn if the skill wrote outside the output dir (`:73-105` [unverifiable: line ref only; content not read]) and an identity check that the generated resume has the same first name (`:107-153` [unverifiable: line ref only; content not read]).
- Form fill is outside the repo: `scripts/update_filled_forms.py` [ok] says "Called by the fill-form skill after successfully filling each form tab" [ok] and `scripts/get_apply_urls.py` [ok] hands the skill JSON with `row, company, position, url, folder, resume, cover_letter` [unverifiable: not found verbatim]; `--auto N` [unverifiable: not found verbatim] selects the last N rows whose status is `new` or empty and added within `job_age_limit_days` [ok] (`scripts/get_apply_urls.py:104-110` [ok]). The fill-form skill itself is NOT FOUND in the repo (no `.claude` [ok] dir). Batch input is therefore "last N New rows" [unverifiable: paraphrase; not a source quote] or explicit row numbers.
- Records: `data/filled_forms.json` [ok: runtime file written by update_filled_forms.py:3] entries `{row, company, position, filled_at}` [unverifiable: pattern/shorthand, not a literal], deduped per row per day (`update_filled_forms.py:51-56` [ok]). Status in the Sheet only becomes Applied from Gmail confirmation email detection (README features).
- Reporting: `daily_summary.py` [ok] Discord post at 09:00 and 17:00 windowed since last run: jobs discovered, dream jobs, docs ready, forms filled not yet submitted, applied, rejections named, emails needing review, running totals and a season funnel (`scripts/daily_summary.py:1-34` [ok]). Separate Discord webhook for fill-form run summaries (`config.py:74` [ok]). Dream-company alert via Discord.
- Failure/needs-you queue: `data/needs_review.json` [ok: runtime file written by needs_review.py:7], an append-only log of status emails not attributable to a row, reasons `ambiguous` [ok] or no-match, deduped on `message_id` [ok] and "Deliberately a log, not a retry queue" [ok] (`src/needs_review.py:1-30` [ok]).
- Dedup: `src/deduplication.py` [ok] (identity dedup, requisition match, applied-company skip tests `tests/test_applied_company_skip.py` [ok]; not read line by line).
- Accounts/CAPTCHA/passwords: NOT FOUND. Pricing: NOT FOUND (self-hosted; user brings OpenAI/Gemini key and Google Cloud project).

---

## 7. Cross-cutting patterns worth knowing for Job360 (observations, no design here)

1. Every one of the six refuses the final click on a browser form. The mechanisms differ: a hard rule in the prompt (career-ops, trackly, job-apply-plugin), a transport-locked agent plus a code-level error `trusted_final_submit_required` [ok] (JobCtrl), or no form code at all (jobsync, apply-potato). Job360 already stores receipts, so the borrowable part is the evidence ladder (success page / user confirmation / provider receipt as separate, typed proofs; Trackly `batch-orchestration.md:497-519` [unverifiable: not found verbatim], job-apply-plugin `job_apply_policy.py:956` [unverifiable: not found verbatim]).
2. State machines worth mirroring as data: career-ops item states (`modes/batch.md:123-125` [unverifiable: not found verbatim]), Trackly member lifecycle + typed human-action codes (`contracts:12` [unverifiable: not found verbatim]), job-apply-plugin application states with one retry then `uncertain_exhausted` [ok] (`job_apply_policy.py:996-1003` [unverifiable: not found verbatim]), JobCtrl `needs_verification` [ok] that is never retryable (`launcher.py:1300-1306` [unverifiable: not found verbatim]).
3. Crash safety patterns: atomic temp-file rewrite + mkdir lock + per-record recovery files (career-ops), partial unique index for single active run (jobsync), SQL claim in one transaction with event (JobCtrl), slot recovery from per-application files (job-apply-plugin), server-owned idempotency keys + optimistic versions + lease token (Trackly).
4. Caps found with real numbers: Trackly target 1-20 per execution; job-apply-plugin 10 applications / 4 h / 5-min lease; career-ops 3 workers default, `--limit` [ok], retry 2, 300 s rate-limit ceiling; JobCtrl 1 worker, 25-job batches, 3 attempts per job, $5 per application, $25 per day, 900 s timeout; jobsync 25 companies, 5 manual runs/hour, 1 automation per hour. No product has a per-day application cap, a per-company cap (only cooldown windows in career-ops), or quiet hours: NOT FOUND in all six.
5. Duplicate protection ideas: career-ops re-apply windows and cross-channel (agency) check, JobCtrl claim-time repeat protection with reasoned one-use override, Trackly optional inbox-receipt preflight (mailbox stays with the agent, not the vendor), Ashby email-per-company dedup warning.
6. CAPTCHA/login handling to note and not copy: CapSolver third-party solver (JobCtrl), hCaptcha trigger on Lever clicks (career-ops workaround = skip checkboxes), saved job-site password typed by a local credential tool (JobCtrl). Safer pattern used by the other four: classify the wall as a typed "human action" [unverifiable: note term, not a source quote], park the item, continue with the rest.
