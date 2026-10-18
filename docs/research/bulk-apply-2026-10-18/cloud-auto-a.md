<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# CLUSTER cloud-auto-a: LazyApply, LoopCV, JobCopilot (quote-checked 2026-10-18)

## Quote check (this pass)

Method: every double-quoted string in the earlier draft was split on ellipses and each fragment was searched (whitespace, curly-quote and HTML normalised) across all on-disk captures: round2/raw, refs/raw, bulk/raw (incl. the 2026-10-18 web reads), round2/*.md, refs/*.md. Checker and per-fragment output: /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/bulk/check2/qc2.py and results2.json. Code quotes: none (all three products are closed SaaS; no repos apply). Clones in /var/tmp/refs were not needed.

Result: 175 quoted strings, 189 fragments. 159 fragments exact. 11 matched after line breaks, bullets, a source typo or a non-breaking hyphen (kept, noted). 2 matched only in case or wording (1 corrected). 17 did not match: 8 were [wrong] and removed, 6 were corrected to the exact source text, 3 were labels or search terms and are now unquoted.

Tags: [ok] = verified on disk (file:line given where useful). [unverifiable] = no on-disk evidence (live site, unsaved web search, or REA output not on disk). [wrong] = removed. "corrected" = the quote was changed to the exact source text. A NOT FOUND statement is [ok] only as an absence from the on-disk captures. REA: no mcp__rea__ call was made in this pass; the REA browser was not re-tested, and REA ids below are checked only where the REA output file is on disk.

Source prefixes:
- R2 = /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/round2/raw/
- R1 = /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/refs/raw/
- NEW = /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/bulk/raw/cloud-auto-a-webreads-2026-10-18.txt (web reads, excerpts verbatim, taken via Apify web-fetch on 2026-10-18)
- Round-1 notes: scratchpad/refs/{loopcv,jobcopilot,lazyapply}.md

Overall verdict on evidence depth: JobCopilot and LoopCV publish enough to describe workflow, caps and review modes. LazyApply publishes almost nothing about its engine (marketing and pricing only). Internals (data model, state names, locks, retries, CAPTCHA, credential storage) are NOT FOUND for all three; only inferences are given, labelled INFERENCE.

---------------------------------------------------------------
## 1. LazyApply (lazyapply.com, "Job GPT")

Architecture: Chrome extension + web dashboard, runs in the user's own logged-in browser.
- R2 lazy2-download-extension.txt: "LazyApply is a chrome based extension, to get started install extension. Once you have installed extension it will automatically open a dashboard for you." [ok]
- R1 lazy_indeed-auto-apply-bot.txt: "Install LazyApply Chrome Extension ... Login and create your resume" [ok]
- NEW (scoutify, competitor-authored): "Because it works inside your browser, it applies through your own logged-in job board accounts." [ok]

### 1.1 How jobs enter the batch
- User picks a board, sets filters, presses start. R1 lazy_indeed-auto-apply-bot.txt: "Once you have Extension Installed, Click on Start applying button, below Indeed bot card" [ok]
- Indeed bot filters (R1 lazy_indeed-auto-apply-bot.txt, lines 9-15): "Pick total Jobs" then "You can enter total number of jobs you want to apply in one shot." [ok, corrected: the source has a line break after "Pick total Jobs", not a colon]. "Remote option", "Salary Filter", "Date Posted" [ok]. "Job title, Job location, Salary, On-Site/Remote and many more" [ok]
- Batch size: user-entered per run ("total number of jobs ... in one shot"). Marketing: "set it to automatically apply for 1000's of jobs in single click" [ok]
- Sites named: R1 lazy_home.txt:3 "platforms like Greenhouse, Dice, Indeed and Ziprecruiter" [ok]; R2 lazy2-job-application-automation.txt:9 "platform like Linkedin and Indeed" [ok]
- Own remote-job board: R2 lazy2-jobs.txt "Found 172,133 jobs" [ok]. Not shown as the apply queue source.
- Scheduling: NOT FOUND (no daily-run or time-window feature). R1 lazy_howtouse.txt:23-24 "Strategic Timing" / "Schedule applications during peak hiring hours to maximize visibility and response rates." [ok, corrected: heading and sentence are on separate lines; the sentence continues "to maximize visibility and response rates."]
- Referral emails: R1 lazy_home.txt:13-14 "Smart Referral Emails" / "Automatically send customized referral emails to relevant employees at companies you're applying to." [ok]. Mechanism NOT FOUND.

### 2. Caps and pacing
- Per-day caps by plan (R1 lazy_home.txt:34-62): "15 Applications/Day" + "1 Resume Profile" (Basic); "150 Applications/Day" + "5 Resume Profiles" (Premium); "1500 Applications/Day" + "20 Resume Profiles" (Ultimate). [ok, corrected: each pair is on separate lines in the source; values checked.]
- FAQ: "Yes, we do have limits on no of applications you can make per day depending on your plan." (R1 lazy_faq.txt) [ok]
- Per-company / per-site caps, delay, jitter, quiet hours: NOT FOUND. Only claim: R1 lazy_home.txt "Our scripts use ADVANCED AI JOB SEARCH ALGORITHMS, so your profiles will never get blocked by platforms." [ok]

### 3. Form engine
- "Job GPT automatically fills the job applications based on your information" (R1 lazy_home.txt, "Personalized Autofill") [ok]
- "Smart Apply: AI-powered application filling system that handles the tedious parts for you." (R1 lazy_howtouse.txt:13-14) [ok, corrected: the heading "Smart Apply" and the sentence are on separate lines in the source]
- Resume profiles (1/5/20 per plan) used as answer sources. Cover letters: "Create dynamic templates that adapt to different roles and companies automatically" (R1 lazy_howtouse.txt) [ok]. Generated per job vs template: NOT FOUND.
- Field detection, dropdown handling, multi-page, upload, answer memory: NOT FOUND in vendor text.
- Competitor (NEW, scoutify): "on my first 25 applications it could not even enter my First Name and/or Last Name" (reviewer quote) [ok]
- Competitor dropdown/submission complaint [wrong, removed: not in any capture]
- scale.jobs page (R2 sc2-lazyapply-alternative.txt:21): "AI auto-apply that gets stuck on date fields" [ok, from "Using LazyApply for irrelevant AI auto-apply that gets stuck on date fields?"; competitor-authored, unverified]
- Loop model (NEW, scoutify): "The extension then searches the board, opens each listing, fills in the form with its "Job GPT" engine and clicks submit, one listing after another." [ok]. INFERENCE: sequential DOM-driven loop inside the tab.

### 4. Review modes
- Vendor text describes only full auto: "Our agent handles the entire application process" (R1 lazy_home.txt) [ok]. No review-first, approve-each or practice mode found: NOT FOUND.
- After: "Track all your applications and referral emails in real-time through our analytics dashboard" (R1 lazy_home.txt) [ok]. Plan tiers "Basic Analytics" / "Advanced Analytics" (R1 lazy_home.txt:64) [ok]
- Competitor dashboard-count and proof-of-submission claims [wrong, removed: not in any capture]. Verified replacement (NEW, scoutify): "LazyApply also gives you no real way to check that an application went through." [ok]. Per-application screenshots: NOT FOUND.

### 5. Failure handling
- Login walls: extension uses the user's existing browser session (INFERENCE from architecture). Credential storage: R2 lazy2-privacy.txt:35 lists "Password" in a list of collected data categories (with Name / Username, Phone Numbers, Email Addresses, Job Titles, Age) [ok]. "Account password for LazyApply itself" is INFERENCE. Job-site passwords are not stored by design if the browser session is used (INFERENCE).
- CAPTCHA, retries, skip list, needs-you queue, blocked reasons: NOT FOUND. Competitor claim (R2 sc2-lazyapply-alternative.txt:35): "Custom career portals, login walls, captchas, multi-step forms: silently skipped." [ok]
- Account-ban risk: competitor claim [wrong, removed: not in any capture]

### 6. Proof and reporting
- Dashboard analytics only (see section 4). Resume view tracker is a separate tool: R2 lazy2-resume-tracker.txt:6 "get a personalized tracking link with our tool"; line 16 "https:// [username] .rsme.io"; line 18 "You`ll receive email notifications for each view." [ok]
- Receipts, screenshots, daily email: NOT FOUND.

### 7. Dedup and safety
- Already-applied check, blacklist, kill switch: NOT FOUND.
- Competitor one-application-per-posting claim [wrong, removed: not in any capture]

### 8. Data model
- NOT FOUND. Plans imply a per-user daily counter and N resume profiles (INFERENCE).

### 9. Pricing tied to volume
- "You will be billed annually." (R1 lazy_faq.txt:14) [ok]
- Prices: "119" / "per year" (Basic), "179" and "149" (Premium, sale), "1099" and "999" (Ultimate, sale) (R1 lazy_home.txt:34-60) [ok]. Competitor page 2026-09-30 (NEW): Basic $99/yr, Premium $149/yr, Ultimate $999/yr [ok]. The Basic price conflicts (119 vs 99); both are kept as found.
- No free trial: "No, we don't offer a free trial." (R1 lazy_faq.txt:4) [ok]
- Refund: 30-day money-back guarantee (R2 lazy2-refundpolicy.txt:20); subscribers "must have applied to fewer than 100 jobs using our service" (line 23); refunds "only granted in cases where the product demonstrably fails to function as advertised" (line 24) [ok]. Volume-tied refund gate.
- Gmail required: "Enter the email to which the LazyApply plan has to be added" / "(Must be a gmail account)" (R1 lazy_home.txt:41) [ok, corrected: line break in source]

---------------------------------------------------------------
## 2. LoopCV (loopcv.pro, incl. apply-with-claude)

Architecture: server-side scanner + server-side submitter. R2 loopcv-autoapply.txt:161: "LoopCV runs its job scanning and automation processes on its servers" [ok]. Plus an optional Chrome extension: "the Loopcv Apply Tool" (R2 loopcv-pricing.txt:140) [ok, cite corrected: the Apply Tool wording is in loopcv-pricing.txt, not loopcv-autoapply.txt]. Three apply channels (browser extension, ATS form submissions, automated emails to recruiters): REA ev_8f1b802b and round-1 note refs/loopcv.md [unverifiable: the REA output file is not on disk; the id appears only in round notes and rows2.json].

### 1. How jobs enter the batch
- Unit = a "Loop": saved job search. R2 loopcv-auto-apply-for-jobs.txt:134: "job title, location, keywords, seniority level, remote/hybrid/on-site, salary range, and any companies to exclude" [ok]
- Competitor definition, R2 aia-vs-loopcv.txt:189: "LoopCV defines a "Loop" as a single job search definition. The platform runs these searches on repeated cycles" [ok, corrected: the earlier wording "A Loop is a single job search definition ... runs these searches on repeated cycles" was not in the source]
- Loop copy: R2 loopcv-help.txt:209 "Loop duplication enhances user efficiency by enabling the replication of predefined search criteria." [ok]
- Scheduling: "scanning job boards every few hours" (R2 loopcv-auto-apply-for-jobs.txt:135) [ok]; "scans 30+ job boards daily" (R2 loopcv-autoapply.txt) [ok]. The two phrasings are inconsistent (few hours vs daily). Time windows / quiet hours: NOT FOUND.
- Matching: collect then filter. NEW (loop-limits web read, NEW line 4): "we collect 2000 jobs with the job title that you provide", "only 150 out of the 2000 are a good match with your profile", "we will only start working with the 150." [ok]
- Matching level: R2 loopcv-matching.txt "Higher job matching levels apply stricter filtering" [ok]; low recommended for juniors (NEW, loopcv-matching) [ok]. Filtered-out jobs viewable: "Rejected Matches section" (R2 loopcv-matching.txt) [ok]
- Filters: R2 loopcv-job-filtering.txt "Exclude specific companies, add must-have keywords for job postings, and remove unwanted terms" [ok]
- Manual picking: "All Matches" list; "Matches with Pending Applications" filter shows jobs labelled "Extension Supported" (R2 loopcv-linkedin-auto-apply.txt:140) [ok]. One Click Apply mode = review each match, apply with one click (R2 loopcv-manual.txt) [ok]
- Via Claude/MCP: "Apply to 10 junior frontend jobs in Berlin" as a user prompt (R2 loopcv-apply-with-claude.txt) [ok]. Batch size = what the agent asks; capped by credits.
- Batch size per scan: not fixed; limited by matches and plan credits.

### 2. Caps and pacing
- Monthly credits. NEW (loop-limits web read): "The Basic plan is free and it offers 10 applications in total (any combination of emails or form submission). The Standard plan offers 100 applications or emails per month and the Premium plan offers 300." [ok]. Matches the competitor table, R2 aia-vs-loopcv.txt:169-171: "Basic Looper Free Up to 10 applications/emails per month", "Standard Looper $19.99/mo Up to 100 per month", "Premium Looper $59.99/mo Up to 300 per month" [ok]. Done For You $89.99/mo (aia-vs-loopcv.txt:172) [ok]
- Over-limit behaviour: "If you reach this limit, LoopCV will temporarily pause sending new applications or automated emails. You can purchase additional ... credit packages" (R2 loopcv-autoapply.txt:160) [ok]
- Per-day numbers: "applies to a set number of matching jobs per day" (R2 loopcv-pricing.txt:141), no number given [ok]. Marketing: "50-200+ applications per week" (R2 loopcv-loopcv-reviews.txt:173) [ok]
- Per-company / per-site caps, jitter, delays, quiet hours: NOT FOUND.
- Pause: "You can pause or stop auto apply for any or all of your job searches instantly" (R2 loopcv-auto-apply-for-jobs.txt) [ok]

### 3. Form engine
- Auto-fill: "types your personal details, uploads your CV" (R2 loopcv-autoapply.txt) [ok]. "LoopCV uses the CV and personal details stored in your profile for every application" (same page) [ok]
- Conflict on per-job tailoring. R2 loopcv-apply-with-claude.txt:169: "LoopCV adjusts your CV and cover letter to highlight the skills and experience most relevant to each specific job posting." [ok, corrected: the earlier fragment "to highlight the skills ... for each specific job posting" dropped words that are not in the source; the page says this about the Claude-driven flow.]
- Sites: "LinkedIn, Indeed, Glassdoor, 30+ boards" (R2 loopcv-autoapply.txt) [ok]. R2 loopcv-apply-with-claude.txt:170: "Workday, Greenhouse, Lever, Ashby" [ok]. Only jobs that "support automated applications or automated emails" can be applied in-platform (R2 loopcv-manual.txt) [ok]. Jobs that "require additional steps, such as account creation or login credentials on the employer's website" must be followed "follow the provided link" (R2 loopcv-manual.txt:155-156) [ok, corrected: the full sentence is "Some job listings require additional steps, such as account creation or login credentials on the employer’s website."]
- Custom questions: collected into a "Questions tab" (R2 loopcv-autoapply.txt) [ok]. "LoopCV can reuse that response for future applications that contain the same question" (R2 loopcv-ai-question-answering.txt) [ok]
- AI answering: "AI Question Answering is available on Premium Looper and Done For You pricing plans." (R2 loopcv-ai-question-answering.txt:152) [ok]. Uses "your CV, profile, and previously answered questions"; unanswerable ones stay in the Questions tab; advice: "answering a few questions manually first" [ok]
- Email channel: "What is a variable in a email template?" (R2 loopcv-help.txt) [ok]. Custom SMTP (Gmail/Yahoo/Outlook) supported (R2 loopcv-help.txt, SMTP) [ok]. Recruiter email finder, open tracking, A/B test: REA ev_8f1b802b nodes 937/957 (refs/loopcv.md) [unverifiable: REA output not on disk]
- File upload mechanics, dropdowns, multi-page: NOT FOUND.

### 4. Review modes
- Per-loop toggle: Auto-Apply on, or "simply collect them for your review" (R2 loopcv-autoapply.txt) [ok]. Master off-switch "across all job searches at once" (same page) [ok]
- One Click Apply is default on all plans incl. free (R2 loopcv-manual.txt) [ok]. Extension path: "keep manual review on and approve the jobs you want" (R2 loopcv-linkedin-auto-apply.txt) [ok]
- Users can "Rate your job matches" to tune matching (R2 loopcv-manual.txt) [ok]
- After: "View detailed summaries of application attempts for quick review and follow-up" (R2 loopcv-exclude.txt / loopcv-linkedin-auto-apply.txt) [ok]
- Practice/dry-run mode: NOT FOUND. Per-application preview before auto-submit: NOT FOUND (review is a per-loop choice, not a per-item gate in auto mode).

### 5. Failure handling
- Login walls: the extension is "Apply Tool ... allowing Loopcv to apply on your behalf without ever asking for your username or password" (R2 loopcv-pricing.txt FAQ) [ok]. LinkedIn: "Loopcv does not store your LinkedIn password. The extension runs in your browser session" and "Loopcv will prompt you to sign in before continuing" (R2 loopcv-linkedin-auto-apply.txt) [ok]
- Unautomatable jobs: "If a job requires extra steps that can't be automated, Loopcv will still collect it so you can apply manually." (R2 loopcv-linkedin-auto-apply.txt) [ok, cite corrected: not loopcv-manual.txt]. External-site jobs get a link (R2 loopcv-manual.txt) [ok]
- Needs-you queue = Questions tab (above) + "Extension Supported" / "Matches with Pending Applications" filter [ok]
- CAPTCHA: NOT FOUND. Retries, backoff, blocked-reason codes: NOT FOUND. Server-side login storage for boards: NOT FOUND (they say the password is never asked).
- Unmet-volume diagnostics: help article lists reasons for few applications (strict filters, low-job location, matching level too high) (NEW web read, loopcv freshdesk articles) [unverifiable: only summarised, not quoted, in the web-read excerpt]

### 6. Proof and reporting
- "Every auto-applied job is logged with the date, job title, company, and source" (R2 loopcv-auto-apply-for-jobs.txt) [ok]. "How can I see my daily results?" (R2 loopcv-help.txt) [ok]
- Tracker columns: "Favorites, Applied, Interview, Saved, Screened, Offer, Withdrew, Rejected, Archive, and Hired" (R2 loopcv-job-application-tracker.txt:205) [ok]. Card shows "whether the application was submitted automatically" (same page, line 210) [ok]. Earlier REA capture ev_12f52a1ec52a6db43a2e434d7832b7921806668fe468cfe64960380a202d9bfe (file R2 loopcv-tracker-rea.txt, header) [ok]
- Email: open tracking for outreach; webhook events application.sent / application.opened (R2 loopcv-developers.txt:276-277) [ok]. Screenshots / receipts: NOT FOUND. Daily summary email: NOT FOUND.

### 7. Dedup and safety
- Exclude companies per loop: "Loopcv is smart enough to not apply by mistake to your previous employees or companies that you don't want to." (R2 loopcv-exclude.txt) [ok]. "We never apply to those companies" (R2 loopcv-job-filtering.txt:133, cite corrected from loopcv-exclude.txt) [ok]
- Master Auto-Apply switch and per-loop pause = kill switch [ok, see section 4]. Explicit "never apply twice" rule: NOT FOUND (search term). Stop rules on rejection: NOT FOUND.
- Claim "no mention of automation in the application itself" (R2 loopcv-autoapply.txt) [ok]. This is a claim, not a safety feature.

### 8. Data model
- Visible entities (from UI/API text): user, loop (job search), match (accepted) / rejected match, application (auto or manual), question (+answer), email template, tracker card with stage, credits counter.
- Webhook events (R2 loopcv-developers.txt:273-279): "job.match.found", "application.sent", "application.opened", "cv.scored", "resume.parsed" [ok]. Signature header "X-LoopCV-Signature" (line 281) [ok]. "HMAC-SHA256": the page shows "X-LoopCV-Signature: sha256=abc123..." and mentions HMAC at line 273 or nearby [ok; the HMAC word is present in loopcv-developers.txt]. REST: "GET /v1/jobs" (line 155) and "POST /v1/resume/parse" (line 195) [ok]. Earlier REA capture ev_113b7da7 (refs/loopcv.md, rows2.json) [unverifiable: REA output not on disk]
- MCP tools (R2 loopcv-developers.txt:263-267): "search_jobs", "parse_resume", "match_jobs_to_resume", "get_company_email", "apply_to_job" [ok]. "The LoopCV MCP server is currently in beta. Join the waitlist" (line 268) [ok]. Conflicts with the apply-with-claude page: "Inside your LoopCV dashboard, go to Settings, then API, and copy your personal API key." (R2 loopcv-apply-with-claude.txt:142) [ok]
- Item states for an application attempt, resume after crash, locks: NOT FOUND. "Matches with Pending Applications" implies a pending state before the extension submits (R2 loopcv-linkedin-auto-apply.txt) [ok as an implication; INFERENCE]
- Runs without the user's computer: "No. LoopCV runs on its own servers." (R2 loopcv-auto-apply-for-jobs.txt:160) [ok, corrected: the source adds "own"; the variant "runs its job scanning and automation processes on its servers" is R2 loopcv-autoapply.txt:161]

### 9. Pricing tied to volume
- "Free forever" (the pricing FAQ says "There is no time limit on the free plan", R2 loopcv-pricing.txt:138). Paid "from €9.99/month" (R2 loopcv-pricing.txt) [ok]
- Competitor-listed tiers in USD: Standard $19.99 (100/mo), Premium $59.99 (300/mo), Done For You $89.99 (R2 aia-vs-loopcv.txt:170-172). Vendor pricing grid not in our capture [unverifiable against the vendor].
- Features gated by tier: AI Question Answering only Premium Looper / Done For You (R2 loopcv-ai-question-answering.txt:152) [ok]. "priority queue placement, advanced job filters" on Pro/Premium (R2 loopcv-pricing.txt:145) [ok]
- Refund: "We offer refunds within 7 days of purchase if you have not used more than 10% of your plan's application quota." (R2 loopcv-pricing.txt:147) [ok]
- "Credit packs on request via support email": no on-disk source for the "on request" or support-email terms [unverifiable]. Credit packages exist: "You can purchase additional ... credit packages" (R2 loopcv-autoapply.txt:160) [ok]
- Monthly unused capacity is lost (competitor note): "Unused capacity each month is gone" (R2 aia-vs-loopcv.txt:184) [ok]

---------------------------------------------------------------
## 3. JobCopilot (jobcopilot.com)

Architecture: cloud "copilots" that apply on employer career sites; a separate Chrome extension autofills any site but never auto-submits.
- Vendor claim in the earlier draft, "exclusively applies to verified jobs on official company career pages" (earlier REA ev_f0fbfa65, refs/jobcopilot.md) [unverifiable as wording: the REA output is not on disk, and the phrase is not in any on-disk capture.]
- On-disk wording, third-party page R2 jc-reviews.txt:135: "It applies only to verified jobs on official company career pages" [ok, corrected: the wording is from a third-party review page, not the vendor site]. Also R2 jc-reviews.txt:117: "Applies only on official company career pages" [ok]

### 1. How jobs enter the batch
- Config per copilot (R2 jc-optimize-jobcopilot-to-auto-apply-to-jobs.txt): remote locations (worldwide or countries), on-site/hybrid (multiple cities within ONE country), job types (full-time, part-time, contractor/temp, internship), "up to 5 job titles", filters: Increase Job Match threshold (Higher/High/Highest), seniority (entry 0-2y, associate, mid-senior, director+), include keywords, exclude keywords (job description), exclude companies, monthly salary [ok]
- Source: "JobCopilot connects to the career pages of over 500,000 companies worldwide" (R2 jc-automate-job-search.txt) [ok]
- Schedule: "Every 2 hours, your search copilot looks for new jobs that match your search criteria" (R2 jc-automate-job-search.txt) [ok]. Conflict: a third-party search snippet says every 4 hours (WebSearch result 2026-10-18) [unverifiable: the search result is not saved on disk]. Competitor "posted daily" claim [wrong, removed: not in any capture]. Use the vendor's 2 hours.
- Three discovery methods: job title keywords (+ auto-suggested similar titles); Favorites: "Find more jobs like the ones you like." (R2 jc-automate-job-search.txt:72) [ok, corrected capitalisation]; Applied jobs: "analyzes the jobs you apply for and looks for new roles that closely match them" (same page) [ok]
- Disposition: "Choose whether your copilot saves jobs for you or auto-applies" (R2 jc-automate-job-search.txt) [ok]. Saved ones appear in the tracker "Saved" column.
- Daily email alert of matching jobs (R2 jc-automate-job-search.txt) [ok]
- Batch size: "Up to 20 Job Matches Daily" (Premium) and "Up to 50 Job Matches Daily" (Elite) (R2 jc-pricing.txt:61, 77) [ok]. Premium "from $0.93/day" and Elite "from $1.05/day": these are in the live web read of jobcopilot.com/pricing (NEW line 21, fetched 2026-10-18), not in R2 jc-pricing.txt [ok, cite corrected]
- Multiple copilots: "Each copilot can search up to 5 different job titles and you can create up to 3 copilots (with an Elite plan)... total of 15 automated job searches"; one country per copilot (R2 jc-automate-job-search.txt, "one country") [ok]
- Time windows: NOT FOUND.

### 2. Caps and pacing
- Daily caps: Premium 20 / Elite 50 matches (pricing, above). Older blog: "Up to 50 jobs auto-applied per day" (R2 jc-optimize-jobcopilot-to-auto-apply-to-jobs.txt:65, which reads "Up to 50 jobs a uto-applied per day" in the source with a stray space). Also R2 jc-reviews.txt:118 "Up to 50 auto-applied jobs per day" [ok, cite corrected: the exact draft wording is the optimize page's]
- Reviewer (R2 jc-reviews.txt:93): "I'm currently applying to around 50 jobs per day." [ok, source uses a curly apostrophe]
- Per-company cap: manifesto (R2 jobcopilot-responsible-rea.txt; R2 jc-responsible-ai-job-applications.txt) proposes "no more than 3 applications per company per 6 months per user", "limit to 50 applications/day", "minimum matching score threshold (e.g. 50%)", "Limit how often AI applies to the same company" [ok]. This is a framework/aspiration; whether the product enforces it: NOT FOUND.
- Exclude list max: "you can exclude up to 20 companies" (R2 jc-automate-job-search.txt) [ok]
- Delays, jitter, quiet hours: NOT FOUND.
- Expanded Career Sites cost credits: "Using auto-apply on an Expanded Career Site costs 1 credit per application." (R2 jc-auto-apply-for-more-jobs-credit-update.txt, May 25 2026); plan includes "unlimited auto-apply access across our 40 Included Career Sites" [ok]

### 3. Form engine
- Setup data (R2 jc-optimize-jobcopilot-to-auto-apply-to-jobs.txt step 3): "let our AI automatically tailor Cover Letters for each job (recommended)"; "your copilot will only apply to jobs for which you eligible legally"; "The more information you give, the more accurate your copilot will answer application questions" [ok]
- Answers: "When you modify an AI-generated answer, your personalized copilot records these changes and applies similar patterns" (R2 jc-ai-apply.txt); "most users notice significant personalization after modifying 5-10 application answers" (same page) [ok]. Writing style controls: "different writing styles across multiple copilots" (same page) [ok]
- Resume tailoring per application: "Tailor your Resume for every application" (Elite; live web read, NEW line 21, and R2 jc-pricing.txt) [ok]. "tailored sections are highlighted and the tailored resume requires manual review before it is saved" (JobCopilot reply quoted in NEW scoutify page, line 19) [ok]
- Sites: "40 Included Career Sites" covering "500,000+ companies" and "5+ million jobs globally" (R2 jc-auto-apply-for-more-jobs-credit-update.txt:77-78); Expanded = "multi-step workflows", "custom questions", "dynamic forms", "additional validation steps" (same page, bullets) [ok]. Named list of the 40: NOT FOUND (WebSearch found none; NEW line 22).
- Chrome extension (R2 jc-chrome-extension.txt:90): "On external sites, you stay in control. The extension auto-fills the fields, and you review and submit the application yourself." [ok]. "Cuts 15-20 minute forms to a few clicks" (same page) [ok]. Needs an active subscription: no on-disk source found [unverifiable]
- Field detection, upload mechanics, dropdown logic: NOT FOUND.

### 4. Review modes
- Two activation options (R2 jc-optimize-jobcopilot-to-auto-apply-to-jobs.txt step 4): "fully automate your job applications (100% automation)" [ok] OR "auto-fill applications for your to review before submitting (90% automated, with human review)" [ok, corrected: the source reads "Your copilot can auto-fill applications for your to review before submitting (90% automated, with human review)", including the source's typo]. Advice: start with review mode, then switch. Tracker: "Switch between review mode and auto-apply anytime" (R2 jc-job-application-tracker.txt) [ok]
- Review UX: pending jobs sit in the "Saved" column "for your approval before applying"; "Open saved applications to review and edit AI-generated answers"; "View detailed job descriptions without leaving the tracker" (R2 jc-job-application-tracker.txt) [ok]. Review queue = Saved column; approve = submit.
- After submit: "See a complete history of automated application steps taken by your copilot" (same page) [ok]
- Practice mode: NOT FOUND. Manifesto levels "suggest only", "apply with confirmation", "auto-apply for pre-approved jobs" (R2 jobcopilot-responsible-rea.txt; aspirational) [ok]

### 5. Failure handling
- Login walls / CAPTCHA / account storage: NOT FOUND. Because applications go to employer career pages (no board login), board credentials are not part of the design (INFERENCE from "official company career pages"). Competitor LinkedIn-account claim [wrong, removed: not in any capture]
- Ineligible: copilot applies only where work eligibility is confirmed (see section 3) [ok]
- Skip lists: exclude companies (max 20), exclude keywords (warning against generic words like "junior", "senior"; R2 jc-optimize-jobcopilot-to-auto-apply-to-jobs.txt) [ok], match threshold. "Needs you" queue: Saved column, plus extension for external sites.
- Competitor-authored complaints (NEW, scoutify, Trustpilot 2.1/5, 96 reviews, 2026-09-30): "it applies for unsolicited job roles that you did not mention anywhere in the instruction and on your resume" (reviewer); "generated false information on my résumé, including job titles and experience that I never had." (reviewer); "Some reviewers say it applies to jobs that have already closed." [ok]. "Duplicates" as a complaint [unverifiable: not in the excerpt]
- Retry/blocked reason codes: NOT FOUND.

### 6. Proof and reporting
- Kanban tracker (Saved / Applied columns auto-filled; drag stages such as Applied to Offer; "no limit" on tracked applications) (R2 jc-job-application-tracker.txt) [ok]. Step history per application [ok, see section 4].
- Daily email alert of new matches (R2 jc-automate-job-search.txt) [ok]
- No screenshots or employer-confirmation capture found: NOT FOUND. Competitor proof-via-tracker claim [wrong, removed: not in any capture]
- Hiring manager contacts: after applying, search by function, level, location; reveal email + LinkedIn using credits; "Unlocking 1 hiring manager contact = 0.25 credits" (R2 jc-auto-apply-for-more-jobs-credit-update.txt:60, dated "May 25th, 2026") [ok]. Advice: "get in touch with 3-4 people from each company" (R2 jc-contact-hiring-managers.txt:75) [ok]

### 7. Dedup and safety
- Manifesto principles (R2 jc-responsible-ai-job-applications.txt; earlier REA ev_8cc76a5f, file R2 jobcopilot-responsible-rea.txt, header present) [ok]: "Application Integrity: No mass applying, spam submissions, or duplicate applications"; "Accountability: Every AI action should be traceable and reviewable"; "Full logs for every action (job viewed, matched, applied)"; "Clear consent logs for every AI action" [ok]. "Users can see the matching rationale (e.g. "Matched 82% based on your CV and experience with Python")" [ok, corrected: the source quote runs to "...experience with Python"]. These are a published framework, "a work in progress"; implementation NOT FOUND.
- Already-applied check: applied jobs feed discovery, so applied jobs are known (R2 jc-automate-job-search.txt) [ok]. Explicit dedupe rule: NOT FOUND.
- Kill switch: switching copilot to review mode / editing config anytime ("you can edit the configuration of your copilots") [ok]. Pause button name: NOT FOUND.

### 8. Data model
- Visible entities: account, copilot (config, writing style, own CV), job match, tracker card with column, per-card step history, credits balance. States: Saved, Applied (+ drag stages). Crash resume, locks: NOT FOUND.
- Retention: "CVs, job preferences, and application data are retained while your account is active and deleted within 7 days after account closure" (R2 jc-privacy-policy.txt:93) [ok]. Hosting AWS (R2 jc-privacy-policy.txt:76) [ok]

### 9. Pricing tied to volume
- Premium from $0.93/day: 1 copilot, up to 20 matches/day; Elite from $1.05/day: 3 copilots, up to 50 matches/day, resume tailoring, hiring-manager credits. Live web read 2026-10-18 (NEW line 21) [ok]. Weekly/monthly/quarterly billing: "Weekly, Monthly and Quarterly plans available" (R2 jc-pricing.txt:88) [ok]
- Expanded Career Sites cost 1 credit per application; hiring-manager unlock 0.25 credit (R2 jc-auto-apply-for-more-jobs-credit-update.txt:60) [ok]
- Refund: "Refund requests made after this 7-day period will not be eligible for consideration." (R2 jc-terms.txt:47) [ok]
- "No free plan or trial" (competitor, NEW) [unverifiable: no competitor text on disk supports this]

---------------------------------------------------------------
## Cross-product comparison (facts only)

| Item | LazyApply | LoopCV | JobCopilot |
|---|---|---|---|
| Runs where | user's browser extension | cloud servers + optional extension | cloud copilots + separate autofill extension |
| Queue source | user starts a board run with filters | saved search "Loop" scanned every few hours/daily | copilot config scanned every 2 hours over 500k career pages |
| Review modes | full auto only found | per-loop auto vs collect + one-click | 100% auto vs autofill-then-review (Saved column) |
| Daily/monthly cap | 15 / 150 / 1500 per day | 10 total / 100 / 300 per month | 20 or 50 matches per day |
| Answer memory | profile only found | Questions tab, reuse by same/similar question, AI on top tiers | learns from user edits, writing style, 5-10 edits |
| Login handling | uses user's logged-in sessions | extension; "never asking for password" is NOT the wording: source says "without ever asking for your username or password" | career pages, none needed |
| Proof | dashboard count; "no real way to check" (competitor) | dashboard log + Kanban + email opens | tracker + step history |
| Exclusions | NOT FOUND | exclude companies/keywords per loop, matching level | exclude up to 20 companies, keywords, match threshold |

## Mechanisms worth stealing as STORE-side (Job360 stores, agent does) - suggestions, not evidence
1. Questions tab: every screening question the agent met is saved with question text, answer, source (user vs model), reuse count; unanswered ones flagged "needs you" (LoopCV R2 loopcv-ai-question-answering.txt).
2. Two disposition modes per search: collect-for-review vs auto, with a master off-switch (LoopCV autoapply FAQ; JobCopilot step 4).
3. Learn-from-edits: store the diff between agent-drafted answer and the user-edited one as an answer-memory event (JobCopilot R2 jc-ai-apply.txt).
4. Per-company frequency rule and daily cap as settings that check_submit enforces (JobCopilot manifesto numbers 3 per 6 months, 50 per day, 50% match floor; aspirational on their side).
5. Visible per-application step history, and an employer-facing proof gap: none of the three captures shows employer confirmation. Job360 receipts are stronger.
6. Credit rule for hard sites (1 credit per Workday-class application) and quota-gated refund (LoopCV 10%, LazyApply under 100 jobs) as pricing precedents.
7. Do NOT copy: LazyApply "never get blocked" evasion claim (R1 lazy_home.txt); LoopCV "no mention of automation" claim; any CAPTCHA handling (none named by any of the three).

## NOT FOUND list (explicit)
LazyApply: any scheduling, per-company cap, delays/jitter, review mode, CAPTCHA, retries, skip list, receipts, data model.
LoopCV: per-day number, delays/jitter, quiet hours, CAPTCHA, retries, blocked-reason codes, per-item approve gate in auto mode, application state machine, screenshots, daily summary email, vendor pricing grid (JS-rendered; competitor table used).
JobCopilot: the 40 Included Career Sites list, CAPTCHA, retries, login-wall handling, pause button name, confirmation capture, state machine, enforcement of manifesto limits.
Unable this run: fresh mcp__rea__ inspection (browser not re-tested, no REA call made), logins (not permitted), app dashboards (behind login).

Files written: this note (rewritten after the quote check); the check scripts and results are under /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/bulk/check2/ (qc2.py, results2.json, cite.py); raw excerpts in NEW path above.

---

## Quote-check counts
- Quoted strings checked: 175 (189 fragments)
- Exact: 159 fragments. Matched after format differences only (line breaks, bullets, source typo, non-breaking hyphen): 11 (kept). Case/wording only: 2 (1 corrected, 1 capitalisation).
- Not found: 17 fragments = 8 [wrong] removed (all competitor-authored claims: lines 35, 40 x2, 45, 51, 130, 160, 167), 6 corrected to exact text (lines 83, 84, 99, 117, 154, 192), 3 labels/search terms unquoted (lines 3, 110, 201).
- Also corrected: line 171 (loose match; the quote was truncated) and line 67 (wording).
- Other [unverifiable] items: REA ids ev_8f1b802b, ev_113b7da7, ev_f0fbfa65 (not in on-disk REA output); WebSearch 4-hour snippet; LoopCV credit-pack "on request" wording; JobCopilot "needs active subscription"; JobCopilot "No free plan or trial"; JobCopilot "duplicates" complaint; LoopCV unmet-volume diagnostics summary; LoopCV vendor pricing grid.
