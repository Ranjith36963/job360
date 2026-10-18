<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# CLUSTER oss-bots: bulk / auto-apply, read from code (quote-checked)

Sources: clones in /var/tmp/refs (ApplyPilot @4a8d521, Auto_job_applier_linkedIn @e0b2401, get_jobs @a78db87, Jobs_Applier_AI_Agent_AIHawk @ef69f5e). HEADs re-checked this round with `git log -1` (commit ids match). No REA calls were made. AIHawk old code: web read only (raw in bulk/raw/aihawk_wayback_20251225.txt). ApplyPilot is AGPL-3.0 (LICENSE; README.md:11; pyproject.toml:6): ideas only, copy nothing. Paths below are relative to each repo; ApplyPilot paths are under src/applypilot/.

Markers: [ok] = quote or cite found at the stated file:line in this round. [unverifiable] = not checked, negative claim not fully grepped, or design opinion. Wrong claims were fixed in place or deleted; the list is at the end.

Cross-cutting finding: none of the four has a "batch" object [unverifiable: negative claim]. Each is a loop over a work source that stops on a count, on an empty queue, or on a site-side cap. ApplyPilot is the only one with a real queue table [ok: database.py:93 `url TEXT PRIMARY KEY`, a jobs table]. The other three keep state in CSV (GodsScion) or per-site tables (get_jobs).

---

## 1. ApplyPilot (Pickle-Pixel/ApplyPilot)

### 1.1 How jobs enter the batch
- Queue = the `jobs` table, picked by SQL, not by user. `apply/launcher.py:130-142`: `WHERE tailored_resume_path IS NOT NULL AND (apply_status IS NULL OR apply_status = 'failed') AND (apply_attempts IS NULL OR apply_attempts < ?) AND fit_score >= ? ... ORDER BY fit_score DESC, url LIMIT 1`. One row per acquire. [ok]
- A job is only eligible after stages discover, enrich, score, tailor, cover, pdf (`pipeline.py:35`: `STAGE_ORDER = ("discover", "enrich", "score", "tailor", "cover", "pdf")`). Apply is a separate command (`config.py:196`: `3: ["apply"]`, tier 3). [ok]
- User pick: `--url` apply to one URL (`launcher.py:106-116`, matches `url = ? OR application_url = ? OR ... LIKE`). Requires `tailored_resume_path IS NOT NULL`. [ok]
- Batch size: `--limit/-l` (`cli.py:147`), default 1 (`cli.py:235`: `effective_limit = limit if limit is not None else (0 if continuous else 1)`). CORRECTED: this line is cli.py:235, not cli.py:196. `--continuous/-c` = run forever (`cli.py:151`). Limit is split across workers (`launcher.py:743-747`: `base = effective_limit // workers`, remainder to first workers). [ok]
- Min score default 7: `config.py:165` `"min_score": 7`. [ok]
- Scheduling: none built in. Continuous mode polls the DB when empty: `config.py:168` `"poll_interval": 60`, `_stop_event.wait(timeout=POLL_INTERVAL)` (`launcher.py:593`). No cron, no time windows. [ok]
- Preflight (`cli.py:204-208`): refuses if no row has `tailored_resume_path IS NOT NULL AND applied_at IS NULL` ("No tailored resumes ready"). [ok]

### 1.2 Caps and pacing
- NOT FOUND: any per-day, per-company, per-site cap, any delay between jobs, any jitter, any quiet hours. `grep -rn "daily\|per day\|sleep\|jitter\|delay" apply/*.py` finds the 3s Chrome start sleep (`chrome.py:249` `time.sleep(3)`), the poll wait, a comment at `launcher.py:592`, and a 0.5s loop sleep at `launcher.py:725` (`time.sleep(0.5)`). CORRECTED: the 0.5s sleep at launcher.py:725 was missing. Jobs run back to back. [ok]
- Retry cap: `config.py:166` `"max_apply_attempts": 3`. Permanent failure sets attempts to 99: `launcher.py:189` `attempts = 99 if permanent else "COALESCE(apply_attempts, 0) + 1"`. [ok]
- Per-job timeout: `config.py:169` `"apply_timeout": 300`. The code uses `proc.wait(timeout=300)` (`launcher.py:444`) AFTER the stdout loop finishes, and the read loop (`launcher.py:393-442`) has no clock, so a hung agent that keeps streaming is not cut at 300s. Lesson: put the deadline on the whole run. [ok]
- Parallelism: `--workers/-w` (`cli.py`), one Chrome per worker on CDP port `BASE_CDP_PORT + worker_id` (`chrome.py:20-21`; `BASE_CDP_PORT = 9222` at chrome.py:21). [ok]

### 1.3 Form engine
- Not a coded engine. One Claude Code CLI process per job, driving Playwright MCP over CDP: `launcher.py:325-344` builds `claude --model M -p --mcp-config F --permission-mode bypassPermissions --no-session-persistence --disallowedTools mcp__gmail__draft_email,... --output-format stream-json`. [ok]
- MCP servers: `"@playwright/mcp@latest"` (`launcher.py:73`) with `--cdp-endpoint=http://localhost:{port}` (`_make_mcp_config`, `launcher.py:66`), and `@gongrzhe/server-gmail-autoauth-mcp` (`launcher.py:80`). [ok]
- Gmail write tools: the `--disallowedTools` list (`launcher.py:330-340`) disallows draft, modify, delete, download-attachment, label and filter tools. CORRECTED: the earlier note said the agent "can only read email". That is wrong. `mcp__gmail__send_message`-style sending is not in the list, and `send_email` (used by the email-only path, prompt.py:565) is not disallowed. So sending is open. [ok]
- Model default: CLI `--model` default "haiku" (`cli.py:150`); launcher default "sonnet" (`launcher.py:298`). They disagree. [ok]
- The prompt (`apply/prompt.py:516-622`) is the whole engine. Its `== ... ==` sections in that function are JOB (518), FILES (524), RESUME TEXT (528), COVER LETTER TEXT (531), APPLICANT PROFILE (534), YOUR MISSION (537), NEVER DO THESE (544), STEP-BY-STEP (560, 12 steps), RESULT CODES (587), BROWSER EFFICIENCY (596), FORM TRICKS (604), WHEN TO GIVE UP (618). HARD RULES (`prompt.py:211`), SALARY (`:153`), SCREENING QUESTIONS (`:174`), LOCATION CHECK (`:112`) and CAPTCHA (`:226`) are separate prompt-builder functions in the same file. [ok]
- Sites: any URL. Special cases only by prompt text: "Multi-page forms (Workday, Taleo, iCIMS): snapshot each new page, fill all fields, click Next/Continue" (`prompt.py:599`); `"Upload your resume" pre-fill page (Workday, Lever, etc.): This is NOT the application form yet` (`prompt.py:606`). [ok]
- Fields: agent snapshot, then "Fill ALL fields in ONE browser_fill_form call" (`prompt.py:600`). Dropdown: "browser_click to open it, then browser_click the option" (`prompt.py:608`). Honeypot: "skip them" (`prompt.py:613`). Checkbox (609), phone digits (610), validation-error snapshot+screenshot (612): [unverifiable, not checked line by line]. [ok 600, 608, 613]
- Prefill trap: "Check ALL pre-filled fields. ATS systems parse your resume and auto-fill -- it's often WRONG." (`prompt.py:579`). [ok]
- File upload: PDF copied to a clean filename `{name_slug}_Resume.pdf` (name with spaces replaced by underscores) in `APPLY_WORKER_DIR/current`, "recruiters see the filename" (`prompt.py:451-458`). CORRECTED: the earlier schematic `{Full_Name}_Resume.pdf` is the same thing with the slug. "ALWAYS upload fresh -- delete any existing resume first" (`prompt.py:577`). Cover letter PDF optional (`prompt.py:460-477`). [ok]
- Custom questions / LLM answers: rules by type. Hard facts from profile, "No guessing" (`prompt.py:175`). Skills: "answer YES" if same domain (`prompt.py:181`), risky, we must not copy: it invents claims. Open-ended: "Write 2-3 sentences. Be specific to THIS job." (`prompt.py:183`). Salary decision tree with floor, midpoint of posted range, hourly = annual/2080 (`prompt.py:153-162`). EEO: "Decline to self-identify" (`prompt.py:185`). [ok]
- Answer memory: NOT FOUND. Nothing stores answers between jobs; the profile JSON is the only memory (`profile.json`, `wizard/init.py:178` writes it). [ok]
- Cover letter per job: yes, generated by stage `cover` (`scoring/cover_letter.py`), validated by `scoring/validator.py` with modes strict / normal / lenient (`validator.py:9-11`), banned words list and fabrication check. If none, prompt tells agent to write "2 factual sentences" when required (`prompt.py:490-494`). [ok]
- Email-only jobs: step 4 (`prompt.py:564-566`): agent calls `send_email` with the resume attached and outputs RESULT:APPLIED. Note the disallowed list does not include `send_email`, so this write path is open. [ok]
- Location eligibility checked before filling (`prompt.py:112-120`), RESULT:FAILED:not_eligible_location. [ok]

### 1.4 Review modes
- Fully auto by default. Only guard: `--dry-run` (`cli.py:152`). In dry run the prompt says "IMPORTANT: Do NOT click the final Submit/Apply button. Review the form, verify all fields, then output RESULT:APPLIED with a note that this was a dry run" (`prompt.py:512`). So a dry run returns APPLIED and `launcher.py:611-612` marks the row `applied` in the DB (`elif result == "applied": mark_result(..., "applied")`). A dry run pollutes the queue (bug to avoid). [ok]
- Normal mode: "BEFORE clicking Submit/Apply, take a snapshot and review EVERY field on the page" (`prompt.py:514`). Self-review by the agent, no human step. [ok]
- `--gen --url` writes a prompt file and prints the claude command to run by hand (`launcher.py:213-247`, `gen_prompt`). That is the only "approve" path, and it is a debug tool. [ok]
- User sees before: nothing. After: Rich live table (`apply/dashboard.py`) and logs. [ok]

### 1.5 Failure handling
- Result protocol: the agent prints one result line, parsed by substring (`launcher.py:466-498`): `for result_status in ["APPLIED", "EXPIRED", "CAPTCHA", "LOGIN_ISSUE"]: if f"RESULT:{result_status}" in output`. CORRECTED: the earlier quote `f"RESULT:{x}"` was paraphrase; the code variable is `result_status`. FAILED lines are parsed separately with `out_line.split("RESULT:FAILED:")`. The parse runs over the whole transcript, so an echoed prompt example could false-match (the prompt itself lists the codes). [ok]
- `PROMOTE_TO_STATUS = {"captcha", "expired", "login_issue"}` (`launcher.py:484`; CORRECTED: the code has spaces after the commas). No result line gives `failed:no_result_line` (`launcher.py:498`). Timeout gives `failed:timeout` (`launcher.py:505`). [ok]
- Permanent set, never retried (`launcher.py:522-531`): `expired, captcha, login_issue, not_eligible_location, not_eligible_salary, already_applied, account_required, not_a_job_application, unsafe_permissions, unsafe_verification, sso_required, site_blocked, cloudflare_blocked, blocked_by_cloudflare`; prefixes `site_blocked, cloudflare, blocked_by` (`launcher.py:530`). [ok]
- Manual list: `manual_ats` in `config/sites.yaml:9-10` (`ibegin.tcsapps.com`). `acquire_job` (`launcher.py:148-158`) marks it `apply_status='manual', apply_error='manual ATS'` and returns None. That is their "needs you" bucket, and it is one config list. There is no queue UI for it. [ok]
- Blocked sites list: `blocked.sites` and `url_patterns` applied inside the SQL (`launcher.py:118-129`; `sites.yaml:13-25`). [ok]
- SSO: never sign in. `blocked_sso` list (`sites.yaml:28-33`: accounts.google.com, login.microsoftonline.com, okta.com, auth0.com, sso.cisco.com). Prompt step 5a: "STOP. Output RESULT:FAILED:sso_required" (`prompt.py:569`). [ok]
- Login wall (`prompt.py:571-576`): sign in with `{personal['email']} / {personal.get('password', '')}`, then "Try sign up with same email and password" (`prompt.py:573`, CORRECTED: capital T), then reads email for the code, then "RESULT:FAILED:login_issue. Do not loop." (`prompt.py:576`). [ok]
- Accounts/passwords storage: one shared job-site password typed into the wizard (`wizard/init.py:104`, `Prompt.ask("Job site password (used for login walls during auto-apply)", password=True, default="")`), written in plaintext to `~/.applypilot/profile.json` (`config.py:95` for the path, `init.py:178` for the write), then pasted into the LLM prompt (`prompt.py:571`). Same password for every employer site. We will not copy this. [ok]
- CAPTCHA (named, we will not copy): CapSolver REST API (`https://api.capsolver.com`, `prompt.py:229`; key `CAPSOLVER_API_KEY` written by `wizard/init.py:317`) via `browser_evaluate` fetch (`prompt.py:226-409`): detect hCaptcha / Turnstile / reCAPTCHA by DOM, `createTask` (`prompt.py:233`) with `*TaskProxyLess` types (`prompt.py:321-325`, e.g. `HCaptchaTaskProxyLess`), poll `getTaskResult` (`prompt.py:337`), max 10 polls (`prompt.py:348`), inject token into the page. Fallback: audio challenge, solve text puzzles, else RESULT:CAPTCHA (`prompt.py:414-417`). Chrome flags `--use-fake-device-for-media-stream`, `--deny-permission-prompts` (`chrome.py:229-233`). [ok]
- Safety refusals the agent is told to emit: camera/mic/location (`unsafe_permissions`), selfie/ID/biometric (`unsafe_verification`), freelancer marketplaces (`not_a_job_application`), payment/SSN/bank "NEVER enter" (`prompt.py:545-552`, `:550`). [ok]
- Sessions: each worker Chrome profile is cloned from the user's real Chrome profile on first run, so existing logins and cookies are reused (`chrome.py:100-159`); password saving disabled in prefs (`chrome.py:178`). [ok]

### 1.6 Proof and reporting
- Screenshots: the agent takes `browser_take_screenshot` "to check results" (`prompt.py:597`). NOT FOUND: any saved receipt, screenshot file, or confirmation capture tied to a job row. [ok for prompt line; absence unverifiable]
- Per-job transcript: `LOG_DIR/claude_{ts}_w{worker_id}_{site}.txt` (`launcher.py:456-457`) and `worker-{id}.log` (`launcher.py:357`) with tool call lines (`launcher.py:390-429`). Token and cost stats from the `result` message: `cost_usd, turns, input_tokens...` (`launcher.py:431-438`); only summed into the live dashboard (`total_cost`), not stored in the DB. [ok]
- DB fields written: `applied_at, apply_status, apply_error, apply_attempts, agent_id, last_attempted_at, apply_duration_ms, apply_task_id` (`database.py:123-132`). `verification_confidence` exists (`database.py:132`, schema; `:182` migration) and is never written (grep: only schema, column list and migration lines). `apply_task_id` is passed in as `task_id` and no caller sets it (`launcher.py:612` passes none). [ok]
- Dashboard: terminal Rich table, `WorkerState` (`dashboard.py:22-37`: status, job_title, company, score, start_time, actions, last_action, jobs_applied, jobs_failed, jobs_done, total_cost; the note's list was a subset). Last `MAX_EVENTS = 8` events (`dashboard.py:45`). HTML dashboard `view.py` is a job list with an "Apply" link (`view.py:177-179`), not an apply report. [ok]
- Email/daily summary: NOT FOUND. Final line: `Done: {total_applied} applied, {total_failed} failed (${cost})` (`launcher.py:784-787`). [ok]

### 1.7 Dedup and safety
- Job dedup: by URL primary key. `store_jobs` (`database.py:329-363`) catches `sqlite3.IntegrityError` (`database.py:358`) and counts duplicates. No cross-site or same-company dedup [unverifiable: negative]. [ok]
- Already-applied: only the agent's own detection (`already_applied` is in the permanent set, `launcher.py:525`) plus `apply_status='applied'` excluded from the SELECT by the `IS NULL OR 'failed'` filter (`launcher.py:130-142`). [ok]
- Stop rules: Ctrl+C once kills current claude process(es) (`_sigint_handler`, `launcher.py:695-713`); twice sets `_stop_event` (`launcher.py:50` `_stop_event = threading.Event()`), kills all and raises KeyboardInterrupt. Lock release: `release_lock` (`launcher.py:199`). [ok]
- Kill switch beyond that: NOT FOUND (no pause flag in DB or config checked per job) [unverifiable: negative]. [unverifiable]
- Blacklists: site and URL pattern lists only (`sites.yaml`). No company blacklist [unverifiable: negative]. [ok for the lists]
- Dangerous design points to avoid: `--permission-mode bypassPermissions` (`launcher.py:325-344`) with page content as prompt input (page text can steer an agent with Playwright and email-read tools); credentials in the prompt; auto sign-up. [ok]

### 1.8 Data model and concurrency
- One table `jobs`, `url TEXT PRIMARY KEY` (`database.py:93`). Item states in `apply_status`: `NULL` (queued), `in_progress`, `applied`, `failed`, `manual`, plus promoted `expired`, `captcha`, `login_issue` (`launcher.py:153, 162, 181-196, 484`). So the state is a free-text column, not an enum. [ok]
- Claim: `BEGIN IMMEDIATE` (`launcher.py:104`), SELECT one row, then `UPDATE jobs SET apply_status = 'in_progress', agent_id = ?, last_attempted_at = ?` with `worker-{id}` (`launcher.py:159-167`). WAL mode and `busy_timeout=10000` (`database.py:46-47`). Thread-local connections (`database.py:17`). [ok]
- Crash resume: NOT FOUND. A process killed mid-job leaves `in_progress` forever: the SELECT only takes `NULL` or `failed`, and `reset_failed` (`launcher.py:275-290`) explicitly excludes `in_progress` (`AND apply_status != 'in_progress'`). Graceful Ctrl+C calls `release_lock` (`launcher.py:199`). Lesson: store a lease with an expiry. [ok]
- No run or batch table. A "run" exists only as process memory (`applied`, `failed` counters, `launcher.py:566-567`). [ok]
- Utility: `--mark-applied URL`, `--mark-failed URL --fail-reason`, `--reset-failed` (`cli.py:156-159`; `launcher.py:250-290`). Manual mark sets `apply_attempts = 99` (`launcher.py:269`). [ok]

### 1.9 Pricing/limits tied to volume
- Free, AGPL-3.0. Real cost is the Claude Code CLI usage per application (cost_usd is shown live). README: Gemini free tier "15 RPM / 1M tokens/day" for scoring and tailoring (`README.md:93`). No price table. [ok]

---

## 2. AIHawk (feder-cr/Jobs_Applier_AI_Agent_AIHawk, "what it became")

- Current repo: not a job tool any more. `README.md:12` (clone): "Open-source, self-hosted alternative to OpenAI Dots, Grok Bot. Built to be undetectable by anti-bot systems." [ok] The repo now holds `invisible_engine_dots/` (an agent project). The old bulk-apply files are absent: find finds no `*easy*apply*` and no `*job_manager*` file in the clone [ok]. The claim that git history starts 2026-09-30 with 418 commits and that no such file was ever added (`git log --diff-filter=A`) was from round 2 and was NOT re-run this round (rule: no git) [unverifiable].
- Last pre-pivot state: web read https://web.archive.org/web/20251225043626/https://github.com/feder-cr/Jobs_Applier_AI_Agent_AIHawk (`bulk/raw/aihawk_wayback_20251225.txt`). Quotes: "AIHawk: The first Jobs Applier AI Web Agent" [ok, title line]; "due to copyright considerations, we have removed all third-party provider plugins from this repository" [ok]; "AIHawk aims to easy job hunt process by automating the job application process. Utilizing artificial intelligence, it enables users to apply for multiple jobs in a tailored way." [ok]. Snapshot files: .github, assets, data_folder, data_folder_example, src, config.py, main.py, requirements.txt and others (the note's list was a subset) [ok]. 62 commits; release v11.15.2024; AGPL-3.0 [ok]. Raw files (`main.py`, `config.py`, `README.md`) were NOT archived on Wayback (404), so no code was read [ok].
- Third-party description (ApplyPilot's README, not first-hand): `ApplyPilot/README.md:81` "| Auto-apply | Full form navigation + submission | LinkedIn Easy Apply only | Click, type, repeat |" [ok, CORRECTED: the path is ApplyPilot/README.md, not AIHawk's README]. "Basic filtering" (line 79) and "Template-based" (line 80) for scoring and resume [ok]. Treat as hearsay.
- All nine questions (queue, caps, form engine, review, failures, proof, dedup, data model, pricing): NOT FOUND in anything first-hand. Do not borrow anything from AIHawk bulk apply. [unverifiable: negative]
- REMOVED (wrong): the claim that press headlines such as "A reporter used AI to apply to 2843 jobs" (TechCrunch slug) exist in the Wayback page title list. The AIHawk Wayback raw file does not contain them; "2843" appears only in other raw pages (ai-job-search-tools, is-jobgpt-legit, sonara-alternatives), so the attribution was wrong.
- Useful in the new repo only as a pattern for agent-side safety: approval gate and per-server permissions. Files exist: `invisible_engine_dots/tests/dots/test_approval_matrix.py`, `test_permissions.py`, `test_spend.py` [ok, existence only]. Not read in depth; NOT a bulk-apply source.

---

## 3. GodsScion/Auto_job_applier_linkedIn (MIT, v26.01.20.5.08)

Repo facts: MIT (`LICENSE` line 1); VERSION `26.01.20.5.08` [ok]; README: "A free, open-source tool that automates job applications on LinkedIn" [ok].

### 3.1 How jobs enter
- LinkedIn search, not a queue. For each term in `search_terms` (`config/search.py:22`, 10 default terms [ok]) it opens `https://www.linkedin.com/jobs/search/?keywords={term}` (`runAiBot.py:1172`) and applies filters (`runAiBot.py:1172-1176`). `randomize_search_order` (`search.py:31`) shuffles. [ok]
- Filters (`search.py:51-72`): `sort_by, date_posted ("Past week"), salary, easy_apply_only = True, experience_level, job_type, on_site, companies, location, industry, job_function, job_titles, benefits, commitments, under_10_applicants, in_your_network, fair_chance_employer`. [ok] `pause_after_filters = True` (`search.py:78`) shows a dialog so the user can edit the filters (`runAiBot.py:320`, "Please check your results"). [ok]
- Batch size: `switch_number = 30` (`search.py:28`) applications per search term, then it moves to the next term (`runAiBot.py:1180` `while current_count < switch_number`). `current_count += 1` only on a successful submit (`runAiBot.py:1442`). [ok]
- Scheduling: `run_non_stop` (`settings.py:39`, "Beta"): after each full pass it sleeps 300s twice, "Sleeping for 10 min..." (`runAiBot.py:1483-1487`). `cycle_date_posted` walks `["Any time", "Past month", "Past week", "Past 24 hours"]` (`runAiBot.py:1521-1522`), and `alternate_sortby` flips the sort each cycle (`settings.py:43-45`; `runAiBot.py:1525`). `run_non_stop` is forced False when `run_in_background` (`runAiBot.py:61-62`). No clock-time scheduler. [ok]
- Control panel `app.py`: Flask on 127.0.0.1; `POST /api/run` (`app.py:399`) spawns `runAiBot.py` as a subprocess (`app.py:163`); `POST /api/stop` (`app.py:430`) kills the process group (`app.py:186-216`); `GET /api/status` (`app.py:442`), `GET /api/logs` (`app.py:451`), `GET/POST /api/config` (`app.py:337`, `:347`). Settings persisted to `user_config.json` (README.md:44); the `config/_overrides.py` write path was not checked [unverifiable]. [ok]

### 3.2 Caps and pacing
- LinkedIn's daily cap is detected, not self-imposed. `external_apply` reads `artdeco-inline-feedback__message` (`runAiBot.py:987`) and, if it contains "exceeded the daily application limit", sets `dailyEasyApplyLimitReached` (`runAiBot.py:984-988`). Then `apply_to_jobs` stops (`runAiBot.py:1433-1435`) and `run` skips further cycles (`runAiBot.py:1473-1474`, `1531-1532`). The code comment says the cap is "roughly 25 applications a day" (`config/search.py:118`). Only seen in `external_apply`, which runs when the job is NOT easy-apply; if the cap message appears elsewhere it is missed. [ok]
- Click pacing: `click_gap = 1` (`config/settings.py:73`) -> `buffer()` random sleeps (`modules/helpers.py:239-255`): docstring says 0.6-1.0 s for speed 1, 1.0-1.8 s for speed 2, 1.8 s to speed for 3+. [ok] `human_type` types char by char, "Roughly 1 key in 40 gets a longer 'thinking' pause" (`modules/helpers.py:258-266`). [ok]
- NOT FOUND: per-company cap, per-hour cap, quiet hours, fixed delay between applications beyond click_gap. [unverifiable: negative]
- Anti-detection: `undetected_chromedriver` when `auto_manage_driver = True` (`modules/open_chrome.py:20-21`, `:90-93`), `--headless=new` when `run_in_background` (`modules/open_chrome.py:78`), `keep_screen_awake` presses shift-right via pyautogui (`runAiBot.py:1197`). [ok]
- README claim: "Can apply to 100+ jobs in under an hour" (`README.md:11`). [ok]

### 3.3 Form engine (LinkedIn Easy Apply only)
- Detection: tries 5 locators in order (`easy_apply_locators`, `runAiBot.py:1030-1037`), clicks and confirms by the modal `jobs-easy-apply-modal` (`runAiBot.py:1125`); a new tab means external. [ok]
- External jobs are not applied when `easy_apply_only = True` (`config/search.py:55`; `runAiBot.py:985-994`). If off, it opens the link, records `application_link = driver.current_url` (`runAiBot.py:1003`) and logs "External job links collected" (`runAiBot.py:1541`). [ok]
- Multi-page: loop pressing Next/Review. `next_counter >= 15` (`runAiBot.py:1339`) triggers manual help (`pause_at_failed_question`, alert at 1342) or a stuck failure (1346-1348). Buttons are searched inside the modal only (comment 1018-1020) [unverifiable: comment not re-read]. [ok]
- Field types in `answer_questions` (`runAiBot.py:666`, def line confirmed), each found via `div[@data-test-form-element]` (`runAiBot.py:671`):
  - `<select>` (`runAiBot.py:687-750`): answer from label keywords, `match_answer_to_option` (`runAiBot.py:489`, whole-word mapping 489-524). If no honest match, leaves it and records it in `unanswered_questions` ("Never guess", `runAiBot.py:741-747`). [ok]
  - radio (`runAiBot.py:753-805`): same, veteran/disability/work-auth by label. [ok]
  - text (`runAiBot.py:808-902`): label rules for experience (only the TOTAL, not per-skill, 821-826 [unverifiable]), phone, street, email (left to LinkedIn prefill, 829-834), city/location, name parts, notice period, salary/CTC, linkedin, website, headline, zip, country. [ok except 821-826]
  - textarea (`runAiBot.py:905-931`): `linkedin_summary` for "summary" (`runAiBot.py:913`), `cover_letter` for "cover" (`runAiBot.py:914`), else AI or unanswered. [ok]
  - checkbox (`runAiBot.py:934-960`): never ticked. Attestation words list `attestation_terms` (`runAiBot.py:455-460`: 'certify', 'certifies', 'certification', 'attest', 'attestation', ..., 'eligible', 'eligibility'). The comment at 948-952 explains why: it used to tick every box. [ok]
- Work-authorization three-way split: `work_authorization_answer` (`runAiBot.py:472-487`): sponsorship -> `require_visa` (`runAiBot.py:476`), authorized -> `legally_authorized` (`runAiBot.py:477-484`), citizenship -> `us_citizenship` (`runAiBot.py:478`; defaults `config/questions.py:30-44`). [ok]
- File upload: `modal.find_element(By.NAME, "file").send_keys(abs(resume))` (`runAiBot.py:653-657`), once per job (`useNewResume` flag, `runAiBot.py:84`, `:1358`, `:1439`). Default `default_resume_path = "all resumes/default/resume.pdf"` (`config/questions.py:24`). One resume for everything; `generated_resume_path = "all resumes/" # (In Development)` (`config/settings.py:54`). No cover-letter file upload; cover letter is a text field answer from `cover_letter` (`config/questions.py:104`). [ok]
- LLM answers (optional, `use_AI = False` default, `config/secrets.py:30`): providers openai / gemini / deepseek via LangChain [unverifiable: provider list not re-checked]; `llm_model = "gpt-4o-mini"` (`config/secrets.py:44`). `answer_question(...)` is called only when the rule-based answer is empty, for text (`runAiBot.py:876-885`) and textarea (`runAiBot.py:915-927`). Prompt: `ai_answer_prompt` in `modules/ai/prompts.py:34-49` ("If it is a yes/no question, reply with exactly Yes or No", "at most 350 characters" at `prompts.py:41`). Inputs: the question, job description, and `user_information_all` (`config/questions.py:110`). AI is NOT used for select/radio (those stay rule-based). [ok]
- `extract_skills` from the job description (`runAiBot.py:1287-1293`, `:1289`) is stored in the CSV, not used to decide. CORRECTED: it runs only when `use_AI` is on (`runAiBot.py:1287`: `if use_AI and description != "Unknown"`). [ok]

### 3.4 Review modes
- `pause_before_submit = True` (`config/questions.py:130`): a `pyautogui.confirm` (`runAiBot.py:1386`) with buttons "Disable Pause", "Discard Application", "Submit Application". "Disable Pause" turns off the stop for the rest of the run (`runAiBot.py:1388`). [ok]
- `pause_at_failed_question = True` (`config/questions.py:137`): blocks with an alert until the user fills the field ("Help Needed", `runAiBot.py:1342`). [ok]
- `stop_before_submit = False` (`config/settings.py:83`): dry run, fills to Review and discards the draft via `StoppedBeforeSubmit` (`runAiBot.py:1382-1384`), counted as skip not failure (`runAiBot.py:1407-1413`). [ok]
- All pauses are forced off when not an interactive terminal (`runAiBot.py:68-72`). [ok]
- Before the user sees: the Review page in the real browser. After: console summary + CSV. [unverifiable: UX description]

### 3.5 Failure handling
- Login: uses `config/secrets.py` username/password in plaintext (`secrets.py:22-23`, "example_password") or the saved Chrome profile; else manual login retry (`runAiBot.py:179-220`, `login_LN`). Guest or temp profile when `safe_mode = True` (`config/settings.py:86`; `modules/open_chrome.py:85-89`). [ok]
- CAPTCHA/checkpoint: NOT FOUND (no detection; it only waits on the user's browser). [unverifiable: negative]
- Failures: `failed_job()` (`runAiBot.py:1054`) writes a row to `all excels/all_failed_applications_history.csv` with columns `Job ID, Job Link, Resume Tried, Date listed, Date Tried, Assumed Reason, Stack Trace, External Job link, Screenshot Name` (`runAiBot.py:1054-1069`; exact list confirmed). Counts: `failed_count`, `skip_count`. [ok]
- Skip vs fail distinction: `UnansweredQuestions` -> skip, with a message to add answers to `config/questions.py` and re-run (`runAiBot.py:1415-1420`; message text not re-read [unverifiable]); `StoppedBeforeSubmit` -> skip (`runAiBot.py:1407-1413`); other exceptions -> failed + `discard_job()` (`runAiBot.py:1422-1429`, not re-read [unverifiable]). `discard_job` (`runAiBot.py:1129-1145`, not re-read [unverifiable]).
- Retry: none per job; a job rejected is added to `rejected_jobs` set for the run. [unverifiable]
- Browser crash: `NoSuchWindowException/WebDriverException` stop the run (`runAiBot.py:1460-1462`, `1535-1536`). [ok]

### 3.6 Proof and reporting
- Screenshots only on failure/manual help: `screenshot(driver, job_id, failedAt)` (`runAiBot.py:1076-1086`) saves `logs/screenshots/{job_id} - {failedAt} - {timestamp}.png` (colons replaced by dots). [ok] No success screenshot [unverifiable: negative].
- Applied CSV `all excels/all_applied_applications_history.csv`: 18 columns (`runAiBot.py:1100`: Job ID, Title, Company, Work Location, Work Style, About Job, Experience required, Skills required, HR Name, HR Link, Resume, Re-posted, Date Posted, Date Applied, Job Link, External Job link, Questions Found, Connect Request). [ok]
- Panel `GET /applied-jobs` (`app.py:285`) serves the CSV as JSON; `PUT /applied-jobs/<job_id>` (`app.py:301`) stamps "Date Applied" manually. History page at `/history` (`app.py:264`). [ok]
- End of run summary: counts of easy applied, external links, failed, skipped, plus a list of "Questions randomly answered" (`runAiBot.py:1541-1550`). A "time saved" line: `timeSaved = (easy_applied_count * 80) + (external_jobs_count * 20) + (skip_count * 10)` (`runAiBot.py:1566`), with 60 s added when it is above zero (`runAiBot.py:1569`), plus a sponsor ask (`runAiBot.py:1570-1571`). CORRECTED: the earlier shorthand omitted the +60. [ok]

### 3.7 Dedup and safety
- Already applied: loaded from the CSV first column (`get_applied_job_ids`, 225-238 [unverifiable]), LinkedIn card "Applied" footer (388-391 [unverifiable]), the `jobs-s-apply__application-link` check (1206 [unverifiable]).
- Blacklists: `about_company_bad_words` / `about_company_good_words` (`config/search.py:88`) matched against the About Company text (`check_blacklist`, `runAiBot.py:527-547`); `blacklisted_companies` set per run; `bad_words` in the description (`config/search.py:94`, e.g. "US Citizen", "No C2C"); `security_clearance` / `clearance_terms` (`config/search.py:123`, `runAiBot.py:454`, `:624`); `current_experience` skip when required years > yours, +2 with masters (`runAiBot.py:629-633`, `found_masters = 2` at `:631`). The default value of `current_experience` was not confirmed [unverifiable]. Regex `extract_years_of_experience` ignores values >12 (`runAiBot.py:552`). [ok except default]
- Sponsorship filter: `skip_non_sponsoring_jobs` and `skip_jobs_without_sponsorship` (`config/search.py:100`; `runAiBot.py:610-620`): offered phrases win over refusal phrases, strict mode `skip_jobs_without_sponsorship`. Silence = apply by default. [ok]
- Whole-word matching `find_bad_word` instead of substring: comment at `runAiBot.py:621-623` says substring 'clearance' matched "clearance sale" and 'secret' matched "secretary". [ok]
- Description unreadable -> skip, not apply (`runAiBot.py:638-644`). [ok]
- Kill switch: `/api/stop` kills the process group (`app.py:186-216`: `os.killpg(..., SIGTERM)` then SIGKILL); no in-bot flag. [ok]
- Never guesses: select, radio, text, checkbox all stop rather than invent (comments at `runAiBot.py:691-693`, `760-762`, `887-891`, `948-952`). [ok]

### 3.8 Data model
- No DB. State = two append-only CSVs + in-memory sets (`applied_jobs`, `rejected_jobs`, `blacklisted_companies`) + counters (`runAiBot.py:100-109`, `1164-1166`). Resume after crash = re-read the applied CSV; failed rows are re-tried next run since only the applied file feeds `applied_jobs`. Concurrency: single process; `app.py` guards with `_bot_lock` (`app.py:403`) and a PID file. [ok]

### 3.9 Pricing/limits
- Free, MIT (LICENSE; README "free, open-source"). Sponsor popups: `pyautogui.alert("Please consider sponsoring this project at: ...", ...)` (`runAiBot.py:1496`) and in the end summary (`runAiBot.py:1571`). The only volume limit is LinkedIn's own ~25/day Easy Apply cap (`config/search.py:118`). [ok]

---

## 4. loks666/get_jobs (Java, PolyForm Noncommercial; commercial use banned: README.md:38 "禁止任何形式的商业使用")

### 4.1 How jobs enter
- Platforms: Boss (zhipin), Liepin, 51job, Zhilian. README note (`README.md:37`): "boss仍然建议手动在boss端投递，使用本项目在PC兜底使用。" [ok]
- Trigger: user presses Start in the Next.js GUI: `POST /api/boss/start` (`BossController.java:88`), `POST /api/boss/stop` (`:127`), `GET /api/boss/status` (`:173`; CORRECTED: status is GET, not POST), progress over SSE `GET /api/boss/stream` (`BossController.java:43`). Same shape for 51job (`/api/51job/start|stop|status|stream`, `JobController.java:382-441`). The backend runs the worker inside `playwrightManager.callOnPlaywright(boss::execute)` (`BossJobService.java:98`). [ok]
- Work source: search URL built from config. `BossConfig` (`boss/BossConfig.java:16-125`): `keywords[]` (`:47`), `cityCode[]` (`:52`), `industry, experience, jobType, salary[], degree, scale, stage`, `sayHi`, `enableAI`, `filterDeadHR`, `sendImgResume`, `allowRepeatChat` (`:109`), `expectedSalary`, `waitTime`, `deadStatus`, `debugger`. [ok] Loop: city x keyword (`Boss.java:111` `public int execute()`, loop lines 111-124 and 227-229 [unverifiable]). [ok]
- Per keyword, scroll to load the whole list: up to 300 scroll rounds (`Boss.java:254`, `for (int i = 0; i < 300; i++)`), footer plus "stable tries" exit (`Boss.java:254-316`; "stable tries" phrase not re-read). Then iterate every card. [ok]
- 51job: pages 1..`DEFAULT_MAX_PAGE = 50` (`Job51.java:54`, `:209`). [ok]
- Scheduling: manual only. The only `@Scheduled` annotations are SSE heartbeats `@Scheduled(fixedRate = 30000)` (`BossController.java:200`, `JobController.java:184`) and a 3s login monitor `@Scheduled(fixedDelay = 3000)` (`PlaywrightManager.java:1758`). No cron. [ok]

### 4.2 Caps and pacing
- Boss: `wait_time` seconds between jobs, random in [wait_time/2, wait_time], default 10 (`DEFAULT_WAIT_TIME_SECONDS = 10`, `Boss.java:1762`; `pauseBetweenJobs` `Boss.java:1790-1802`). Debug mode uses the max. Comment at `Boss.java:1785`: "上一轮实测 7 分钟连刷 297 个岗位详情，直接把 Boss 风控触发了" (7 minutes, 297 job details, triggered Boss's risk control). [ok]
- Boss daily cap: NOT FOUND in Boss.java [unverifiable: negative]. [unverifiable]
- 51job daily cap detected: toast text list `"今日投递太多", "您今日投递太多", "休息一下明天再来", "达到上限", "次数过多"` (`Job51.java:616`; `:636` not re-read), checked 10 times x 200ms after clicking batch deliver (`Job51.java:329-338`), sets `reachedDailyLimit` and stops. Empty jobId collection also counts as "limit or layout change" (`Job51.java:761-766`: `reachedDailyLimit = true`). [ok]
- Zhilian: `checkIsLimit()` (`ZhiLian.java:491`) reads `//div[@class='a-job-apply-workflow']` (`ZhiLian.java:494`) and looks for "达到上限" (`ZhiLian.java:497`). CORRECTED: the earlier CSS-style selector `div.a-job-apply-workflow` was not the code's selector. Liepin: no cap text found by grep [unverifiable: negative]. [ok]
- Pacing per page for 51job: sleeps 1-3s between steps (`Job51.java:211-270` region, not re-read) [unverifiable]. No jitter beyond that. [unverifiable]

### 4.3 Form engine
- Boss is not a form. "Apply" = open the job's detail page via "更多详情" and then the chat button. CORRECTED: the earlier text said the detail page is opened in a new tab and 立即沟通 is clicked there. The code comment at `Boss.java:1001` says: "目前Boss无法通过新标签页打开立即沟通按钮，所以只能点击更多详情，然后从更多详情里打开聊天按钮" (Boss cannot open the 立即沟通 button in a new tab, so it clicks 更多详情 and opens chat from there). Selectors: `a.more-job-btn` (`Boss.java:1017`) -> chat button `a.btn-startchat, a.op-btn-chat` (`Boss.java:1035`); input `div#chat-input.chat-input[contenteditable='true'], textarea.input-area` (`Boss.java:1083`); send `div.send-message, button[type='send'].btn-send, button.btn-send` (`Boss.java:1139`, CORRECTED: the earlier selector was truncated). Whole flow `resumeSubmission` at `Boss.java:1004-1147`. [ok]
- Optional image resume after the greeting: `sendImageResume` (`Boss.java:1280-1319`), waits up to 45s (`IMAGE_SEND_CONFIRM_TIMEOUT_SECONDS = 45`, `Boss.java:81`) for the image to appear in chat. Image is made by `BossResumeImageService`. [ok]
- AI greeting (`enableAI`): `generateAiMessage` sends `introduce`, keyword, job name, JD to an OpenAI-compatible endpoint (`AiService.java:41-58`, `temperature 0.5`). Reply "false" alone = reject this job (`isAiRejectResponse`, `Boss.java:1736-1739`, regex `(?i)^[*`'"\s]*false[*`'"。.!！\s]*$` at `Boss.java:1738`). On error falls back to `sayHi` (`Boss.java:1727-1729`). So the same LLM call does fit-check and writes the message. Default prompt asks for a greeting "不超过60字" (under 60 Chinese characters, `Boss.java:1741-1748`). Custom prompt via `String.format(prompt, introduce, keyword, jobName, jd, sayHi)` (`Boss.java:1713-1715`). [ok]
- Repeat chat: if the button says "继续沟通" (continue chat) the job is skipped unless `allowRepeatChat` (`Boss.java:1047-1052`); then a random line from `repeatChatMessages` (`BossConfig.java:17-33`, 6 default follow-ups; `randomRepeatChatMessage`) is sent (`Boss.java:1047-1078`). [ok]
- 51job and Zhilian: tick each result's checkbox on the page (`checkbox.evaluate("el => el.click()")`, `Job51.java:276`) and click the site's own "batch deliver" button (`div.tabs_in` -> `button.p_but`, `Job51.java:322-323`; second button clicked at `:329`). Then closes a success dialog and a "需要到企业招聘平台单独申请" (must apply on the company's own site) dialog (`handleSeparateDeliveryDialog`, `Job51.java:471`). Zhilian also has "deliver similar jobs" (`ZhiLian.java:431`). This is the one true bulk-apply in the cluster, and it is the site's own feature, not a form fill. [ok]
- File upload, dropdowns, custom questions, answer memory, per-job cover letter: NOT FOUND for Boss (chat greeting only; resume is whatever is on the Boss profile or the image). [unverifiable: negative]

### 4.4 Review modes
- Fully auto once started. `debugger` flag = "调试模式：仅遍历不投递" (browse, do not send) (`Boss.java:1010-1012`). [ok] No per-job approval, no pre-send preview [unverifiable: negative]. The user sees the live progress stream (`JobProgressMessage` types, `dto/JobProgressMessage.java:48`) and the list page (`GET /api/boss/list`, `BossAnalyticsController.java:59`). [ok]

### 4.5 Failure handling
- Login: QR scan by the user; cookie stored and reused ("大部分平台每周仅需扫码一次", README.md:62). Login monitor thread polls every 3s and is paused during a run (`pauseBossMonitoring`, `BossJobService.java:71`; `PlaywrightManager.java:1758`). If not logged in: "请先登录Boss直聘" and no run (`BossJobService.java:60-63`, message at `:61`). [ok]
- Cookies stored in SQLite `cookie(platform, cookie_value TEXT, remark, ...)` with a unique index per platform (`CookieService.java:39-48`: `CREATE UNIQUE INDEX IF NOT EXISTS idx_cookie_platform ON cookie(platform)`), plaintext [ok]. No passwords stored at all (QR login) [unverifiable: negative].
- Security check (Geetest slider): detected by URL (`Boss.java:1811-1819`: `/web/user/safe/verify-slider`, `/web/passport/zp/verify`, `/web/passport/zp/security`, `security-check`). The run PAUSES and waits for the human: "请手动完成Boss直聘滑块验证，通过后在控制台回车继续…" (`Boss.java:1827-1828`), blocks on `System.in.read()` (`Boss.java:1830`); times out at 5 minutes (`Boss.java:1838`; `waitForSliderVerify` at `:1821`). No solver. This is the pattern we want (hand over to the user), but console Enter is clunky. [ok]
- Per-keyword errors: caught, skip to next keyword with a progress warning (`Boss.java:477-482`). [ok]
- Stop: `shouldStop` flag checked at many points; watchdog thread force-resets state after 15s if the worker is stuck inside a Playwright call (`FORCE_RESET_GRACE_MS = 15_000L`, `BossJobService.java:127-150`). [ok]
- Re-entrancy: `isRunning` guard (`BossJobService.java:41-44`) returns a "already running" message [unverifiable: exact message text not re-read]. CORRECTED: the field is `private volatile boolean isRunning` (`BossJobService.java:33`). [ok]
- Browser died: `playwrightManager.ensureReady()` relaunches (`BossJobService.java:50`). [ok]

### 4.6 Proof and reporting
- Per-job row in `boss_data` with `delivery_status`; `GET /api/boss/stats` (`BossAnalyticsController.java:26`) and `GET /api/boss/list` (`:59`) with filters (statuses, location, experience, degree, minK, maxK, keyword, filterHeadhunter, page/size). [ok]
- Raw job-detail JSON appended to a file (`appendRawJson`, `Boss.java:380`). [ok]
- WeCom (企业微信) webhook bot: `HOOK_URL` (`README.md:58`, `README.md:110`, read by `Bot.java:38`). `Bot` has no caller outside its own file (grep `Bot\b` across `src` returns no other users), so the advertised "实时通知" does not fire at this commit. [ok]
- No screenshots, no receipts. Proof of send = DB flag only (no check of the chat afterwards beyond the image-resume check). [unverifiable: negative]
- Cost: README.md:120 says a Boss day with `gpt-5-nano` costs "大约在0.06美元 (6美分)左右" (about 6 cents). CORRECTED: the earlier quote "约 0.06 美元" was not verbatim. [ok]

### 4.7 Dedup and safety
- DB dedup on insert only: `existsBossJob(encryptId, encryptUserId)` (`BossService.java:631`, called at `Boss.java:567`) then `insertBossJob`. It does NOT skip rows already `已投递` before sending; the skip comes from the page (button says "继续沟通" after a prior chat), `Boss.java:1047-1052`. [ok for the call; "does NOT skip" unverifiable]
- Blacklists table `boss_blacklist(type company|recruiter|job, value)` (`BlacklistEntity.java:14` `@TableName("boss_blacklist")`, type comment at `:24`). Contains-match on job name at `Boss.java:418`: `blackJobs.stream().anyMatch(jobName::contains)`. CORRECTED: the earlier pseudo-quote `anyMatch(x::contains)` was not verbatim. Company and recruiter matches were not re-read [unverifiable]. [ok]
- Filters: `filterDeadHR` skips when the HR's active text contains "年" (a year), `Boss.java:424-428` (`boolean hrInactiveByYear = bossActive != null && bossActive.contains("年")`); matching rows are stored as `已过滤`. [ok]
- Auto-blacklist from chats: `updateBlacklistFromChats` (`Boss.java:136`) reads the chat list and adds a company if the last message contains words like 不, 感谢, 但, 遗憾, 需要本, 对不 and not 不是/不生 (`Boss.java:184-186`). It is defined but never called (grep for the name finds only its definition). Dead code at this commit. [ok]
- Salary filter `isSalaryNotExpected` (`Boss.java:1576`) is also defined and never called in the run path (grep finds only the definition); `containsDeadStatus` (`Boss.java:1696`) is also unused. So the README's "目标薪资" filter is not enforced during apply (verified by grep, not by running). [ok]
- Kill switch: stop endpoint (see 4.5). `debugger` = dry run. [ok]

### 4.8 Data model
- SQLite via MyBatis-Plus (`db/getjobs.db`). README.md:93: "本项目不会自动建表， **没有数据库文件无法启动**" (no auto table creation; the app will not start without the database file). CORRECTED: the earlier quote was a paraphrase with different punctuation. [ok]
- Relevant tables: `boss_data` (`BossService.java:577` `CREATE TABLE boss_data_new (` with columns id, encrypt_id, encrypt_user_id, company_name, job_name, salary, ...; the full column list was partly re-read [ok for the first columns]), `boss_blacklist`, `boss_config`, `cookie`, `job51_data` (`Job51Service.java:253` `CREATE TABLE IF NOT EXISTS job51_data (`, with `delivered INTEGER DEFAULT 0`), `liepin_data`, `zhilian_data`. [ok] Schema strings in the note are schematic.
- Item states in `boss_data.delivery_status` (free text): `未投递` (new) and `已过滤` (filtered) (`Boss.java:560`: `entity.setDeliveryStatus(filtered ? "已过滤" : "未投递")`), `已投递` (sent) at `Boss.java:1171`, `投递失败` (failed) at `Boss.java:1186`. [ok]
- No run/batch table, no queue table: the "queue" is the search results page itself, re-scraped each run. Resume after crash = re-run; already-chatted jobs are skipped by the page's button text. [unverifiable: negative]
- Concurrency: one run per platform (`volatile boolean isRunning`, `BossJobService.java:33`), one Playwright thread (`callOnPlaywright`, `BossJobService.java:95-98`). The comment explains that running on ForkJoinPool with the login monitor caused `net::ERR_ABORTED` (`BossJobService.java:95-97`). [ok]

### 4.9 Pricing/limits
- Free for non-commercial use (PolyForm Noncommercial 1.0.0, `LICENSE:1`; `README.md:38`). No volume pricing. README ad slot ("广告位招租") and QQ group support [unverifiable: not re-grepped]. Cost = your own LLM key. [ok]

---

## What to take (ideas, not code) for Job360 bulk (store tool, not a do tool)

Design proposals. Their factual anchors are checked above. [unverifiable as design opinion]

1. State machine with a lease: ApplyPilot's `in_progress` has no expiry and strands rows after a crash (`launcher.py:159-167`; `reset_failed` excludes it at `launcher.py:275-290`). Our queue item needs `claimed_at/lease_until` and a resume rule. [ok anchor]
2. Terminal reason vocabulary: ApplyPilot's `PERMANENT_FAILURES` set (`launcher.py:522-531`) is a good list of blocked reasons to record as typed events (expired, login, sso, captcha, not_eligible_location, already_applied, unsafe_*). Our agent reports them; we store them. We do not solve captchas. [ok anchor]
3. Hand-over on a human check: get_jobs pauses on the Geetest page and waits (`Boss.java:1821-1843`); GodsScion pauses on unanswered questions and lets you fill them in the browser (`runAiBot.py:1342`). Both map to our `ask_user` / "needs you" queue. [ok anchor]
4. "Never guess" answers: GodsScion leaves selects, radios, text and attestation checkboxes unanswered rather than invent (`runAiBot.py:741-747`, `760-762`, `887-891`, `948-952`), reports them as `unanswered_questions`, then skips the job. Matches hard line 4. Also their rule that years of experience answers only the TOTAL question, not per-skill (`runAiBot.py:821-826`, not re-read [unverifiable]). [ok anchor]
5. Dry run that does not pollute: GodsScion counts `stop_before_submit` as skip (`runAiBot.py:1407-1413`); ApplyPilot's dry run records `applied` (`prompt.py:512` tells the agent to report APPLIED; `launcher.py:611-612` records it). CORRECTED: the earlier cite was `launcher.py:512`, which is a prompt file line. Our dry run must write a `practice` event, never `applied`. [ok anchor]
6. Skip vs fail vs needs-you are three outcomes (GodsScion `runAiBot.py:1407-1429`). Keep three. [ok anchor]
7. Caps are discovered from the site's message in GodsScion (`runAiBot.py:984-988`) and 51job (`Job51.java:616`), not self-imposed. For us: record `daily_cap_reached` as an event and stop the batch; expose a user-set cap in settings checked by `check_submit`. [ok anchor]
8. Per-run summary and a per-item receipt: all four lack real proof (no success screenshot). That is where our receipts beat them. [unverifiable: negative across four repos]
9. Description-unreadable -> skip (`runAiBot.py:638-644`). [ok anchor]
10. Do not copy: plaintext shared password in prompt (`ApplyPilot prompt.py:571`), CapSolver (`prompt.py:226-409`), `bypassPermissions` with web content (`launcher.py:325-344`), auto sign-up, "answer YES to any tool in my domain" (`prompt.py:181`), auto-ticking agreements (`runAiBot.py:948-952` shows the defect), undetected-driver stealth (`open_chrome.py:20-21`), email send without a gate (`prompt.py:565`), dead "notification" and "salary filter" features (get_jobs `Boss.java:1576`, `:1696`, and the WeCom Bot class). [ok anchor]

---

## Quote-check log (this round)

- Items checked: 720 quoted strings and 193 file:line citations extracted from the previous version (scripts in bulk/check/). Each citation resolved to a file in its clone; every cited file exists.
- Wrong claims fixed or removed (19, one of them deleted outright): cli.py:196 -> 235; "can only read email" (Gmail) corrected, send_email is open; `f"RESULT:{x}"` -> `f"RESULT:{result_status}"`; PROMOTE_TO_STATUS spacing; "no sleep except 3s" missed launcher.py:725; README.md:81 cited to AIHawk instead of ApplyPilot/README.md; press-headline claim removed (not in the AIHawk Wayback raw); Boss "new tab" flow replaced with 更多详情 flow per Boss.java:1001; Boss send selector made exact; ZhiLian selector made exact; `POST /api/boss/status` -> GET; `volatile isRunning` -> `private volatile boolean isRunning`; `anyMatch(x::contains)` -> exact; README DB sentence made exact; "约 0.06 美元" -> "大约在0.06美元"; `launcher.py:512` -> prompt.py:512; timeSaved +60 added; extract_skills gated on use_AI; "Try sign up" capitalization.
- Not re-run this round: git history claims (no-git rule), Boss.java:111-124/227-229 loop detail, Job51 1-3s pacing, GodsScion 821-826 and 1415-1429 detail, Liepin cap absence.
