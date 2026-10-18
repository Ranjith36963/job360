<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# Cluster agentic-new: bulk / auto-apply feature extraction
Tsenta, 6figr JobGPT, Jack & Jill, Scale.jobs. Read-only research, 2026-10-18.
Quote-checked pass (2026-10-18): every quote grepped against its cited file, then the corpus. Marks: [ok] verified at the cited place; [unverifiable] no evidence on disk, or an absence claim; [wrong] found false and REMOVED here (corrections kept as separate [ok] lines).

Evidence key. R2 = /tmp/claude-0/-home-user-job360/5caca5ad-6f9e-5f80-a147-05c81f439f32/scratchpad/round2/raw/ ; R1 = .../scratchpad/refs/raw/ ; B = .../scratchpad/bulk/raw/.
"REA" is used only for the capture file R2/tsenta-changelog-rea.txt, whose header reads "REA ev=ev_019e29619fb2..." and "REA Chrome DevTools Protocol observation provider" [ok]. Other REA ids (ev_4bbd3448, ev_ff6c6ff7, ev_b4c4ec78, ev_65ee18cd, ev_5fbb3107, ev_3311a059) appear only in round-1/2 notes; their raw is not on disk [unverifiable]. This run did not re-open the REA browser.
Code = /var/tmp/refs/jobgpt-mcp-server (6figr's public MCP client, TypeScript). It shows the API surface, not the server-side engine.

Check tally for this pass: see the final counts line at the end of this file.

## 0. Headline findings
- Tsenta and JobGPT are the two real bulk engines [unverifiable: a product judgement from the pages above; the pages themselves are [ok] below]. Scale.jobs is a bulk service run by humans [ok: R2/sc2-pricing.txt:14 "We manually fill job applications with care"]. Jack & Jill has no bulk apply and says so [ok: see section 3].
- Jack & Jill (R1/jj_applicationfeedback.txt:139): "No. Jack tells you where you stand and what to fix, but pressing Submit stays with you. Handing that over means letting a third party decide how to represent you." [ok]
- Jack & Jill (R1/jj_applicationfeedback.txt:68-69): "You press Submit" / "Jack never sends anything for you." [ok: the two sentences sit on lines 68 and 69]
- Server-side internals (queues, tables, locks, retry code, CAPTCHA method) are published by NONE of the four [unverifiable: absence claim]. Sections 5 and 8 are mostly NOT FOUND; what exists is inferred from public statements and is marked as such.
- Three review modes recur: full auto, approve-first, hand-pick (user chooses jobs) [ok: Tsenta R2/tsenta-changelog-rea.txt:26-45; JobGPT B/6f-auto-apply-tools-software-engineers-779.txt:92, see 2.4].
- Tsenta and JobGPT both bill per successful submit and both refund failures [ok: B/tsenta-pricing-curl.txt:116; B/6f-sonara-alternatives-780.txt:95].

## 1. Tsenta (tsenta.com, YC S26)

### 1.1 Batch entry, size, schedule
- Source is Tsenta's own crawl, not user picks: "Tsenta watches 50,000+ company career pages directly. The moment a matching role goes live, it submits a tailored application through the real ATS portal — in 2-3 seconds" [ok: R2/tsenta-auto-apply.txt:13].
- User sets filters once: "Tell Tsenta the roles, locations, salary range, and seniority level you want. Set what to avoid. This takes about 10 minutes once." [ok: R2/tsenta-auto-apply.txt, Step 1]
- Match feed + queue: "Match feed, application queue, tracker, recruiter inbox." [ok: R2/tsenta-auto-apply.txt]. Matching is scored: "Tsenta scores each newly-posted role against your résumé, surfacing only jobs you actually qualify for" [ok: R2/tsenta-ai-disclosure.txt:10].
- Other entry doors:
  - Chrome extension: "On any job posting anywhere on the web, one click and Tsenta submits the full application through the ATS." [ok: R2/tsenta-auto-apply.txt:42; citation corrected, it is not in tsenta-mcp.txt]
  - Chat reply: Reply "yes" to a match. Application submitted in background. [ok: R2/tsenta-ai-agent.txt:60] (The iMessage/WhatsApp wording is a paraphrase in the earlier draft. Only this sentence is verified.)
  - MCP: "Ask your AI to surface roles that fit, tailor your resume, and submit applications" [ok: R2/tsenta-mcp.txt:5].
  - Jobs page: search with filters 24h/7d/14d/30d, Sponsors Visa, 8,122,204 jobs [ok: R2/tsenta-jobs.txt:39].
- Batch size / schedule: no explicit daily batch, window or cron published [unverifiable: absence]. Behaviour is event-driven ("the moment") [ok]. Cycle quota only: 600 / 1,500 / 4,500 per 30-day cycle [ok: B/tsenta-pricing-curl.txt; R2/tsenta-blog-claude.txt:88 "Starter: $19 for 600 applications per 30-day cycle"]. NOT FOUND: time windows, quiet hours, concurrency limit [unverifiable: absence].

### 1.2 Caps and pacing
- Only cap is the monthly allowance (pricing below). Competitor comparison implies it has no daily cap: "LazyApply's daily cap can exceed Tsenta's monthly volume if your entire search runs through LinkedIn." [ok: R2/tsenta-ai-agent.txt:38]. The "no daily cap" inference itself is [unverifiable].
- NOT FOUND: per-company cap, per-site cap, delays, jitter, quiet hours [unverifiable: absence]. The "2-3 seconds" speed claim (R2/tsenta-auto-apply.txt:13) is a marketing claim; it does not show how pacing works [unverifiable].

### 1.3 Form engine
- ATSes: "Workday, Greenhouse, Lever, Ashby, Rippling, iCIMS, BambooHR, Workable, JazzHR, Jobvite, BreezyHR, Oracle Cloud" and "30+" [ok: R2/tsenta-auto-apply.txt:28, 4, 72]. The disclosure page lists more: SmartRecruiters, Paylocity, SAP SuccessFactors, Zoho Recruit and others [ok: R2/tsenta-ai-disclosure.txt:48-75]. Its own blog says "15 major ATSes" [ok: R2/tsenta-blog-vs.txt:36]. The disclosure page also says "+ 39 more" [ok: R2/tsenta-ai-disclosure.txt:26]. Count claims are inconsistent (15 / 30+ / 39 more) [ok as an observation].
- Mechanism: "Intelligent form recognition and auto-filling across 30+ ATSes, including login flows, multi-page forms, and document uploads" [ok: R2/tsenta-ai-disclosure.txt:13].
- Login walls: "multi-page forms and account creation defeat a simple form filler. Tsenta handles it." [ok: R2/tsenta-international.txt:31]. (The earlier draft's wording and line 94 were wrong; removed.) This means it creates employer-portal accounts and logs in [unverifiable inference].
- Run modes: "Live application overlay or fully headless (replay later)" [ok: R2/tsenta-ai-disclosure.txt:27]. "Applications run in Tsenta's cloud, not on your phone." [ok: R2/tsenta-mobile.txt:22]
- LLM answers: "Drafts answers to free-text questions (e.g. "Why this company?") in your voice, drawing on your résumé and prior submissions" [ok: R2/tsenta-ai-disclosure.txt:12]. Per-role résumé and cover letter: "Per-role résumé and cover letter generation from the actual job description, with keyword alignment, ATS-safe formatting, and a diff view of every change before send" [ok: R2/tsenta-auto-apply.txt]. Truth rule: "Applications use only true facts from the résumé you uploaded." [ok: R2/tsenta-auto-apply.txt:21]
- Work authorization is a stored fact: "answers them from the authorization status you set during onboarding, consistently, on every form" [ok: R2/tsenta-international.txt:33].
- Multiple résumé profiles: "Keep multiple résumé profiles and choose the right one for each role" [ok: R2/tsenta-mobile.txt:19].
- OTP: "Integrate with email for OTP autofill functionality" [ok: R2/tsenta-terms.txt:16]; "read the one-time verification codes some employers email during an application" [ok: R2/tsenta-privacy.txt:66].
- NOT FOUND: dropdown/custom-widget handling, file-upload mechanics, selector strategy, which model drives the browser [unverifiable: absence].

### 1.4 Review modes
- Default is hands-free: "If you would rather keep applying hands-free, auto-approve still works exactly as before." [ok: R2/tsenta-changelog-rea.txt:34, 45]
- Review-first mode: "Tsenta pauses right before the final submit and shows you everything it filled in: your answers, the tailored resume, and the cover letter. Fix any answer, tweak your work experience or education inline, then hit approve. Nothing gets submitted until you say so." [ok: R2/tsenta-changelog-rea.txt:32-33]. Live across "Greenhouse, Workday, Ashby, Lever, Oracle, and SmartRecruiters" [ok: R2/tsenta-changelog-rea.txt:34]. Only six ATSes support the gate [ok: same six names].
- Mobile: "Review each role or use Auto Apply for qualified matches, based on the controls you choose." [ok: R2/tsenta-mobile.txt, Apply section]
- Before: diff view of résumé changes. After: receipt (1.6). There is no practice mode [unverifiable: absence]. Free tier of 25 real applications acts as trial [ok: R2/tsenta-ai-agent.txt:86 "on the first 25, no card required"].
- Terms push liability to user: "You are solely responsible for reviewing and approving all AI-generated content before submission to employers." [ok: R2/tsenta-terms.txt:36]

### 1.5 Failure handling
- Billing refund is the only documented failure rule: "Three things never count against your cycle: matches you skip, drafts you never approved, and applications that fail on the employer's side. Those are refunded automatically." [ok: B/tsenta-pricing-curl.txt:116]
- Mobile status buckets: "See what needs review, what was submitted, what hit an issue, and which recruiters replied." [ok: R2/tsenta-mobile.txt, "One clear tracker"]
- Accounts/passwords: "uses OAuth — Tsenta never sees a password" refers to the MCP connection only [ok: R2/tsenta-mcp.txt:10]. How it stores employer-portal passwords is NOT FOUND [unverifiable: absence].
- CAPTCHA: NOT FOUND on any page (grep of tsenta-*.txt) [unverifiable: absence]. We will not copy any method anyway.
- NOT FOUND: retry policy, skip lists, explicit blocked reasons [unverifiable: absence].

### 1.6 Proof and reporting
- Receipt: "You get a receipt showing exactly what was sent: every field, every answer, the résumé version, the timestamp." [ok: R2/tsenta-auto-apply.txt, Step 4]. Pricing page adds: "the fields that were filled, the answers that were written for the open-ended questions, the résumé and cover letter that were sent, and the employer's confirmation." [ok: B/tsenta-pricing-curl.txt:115]
- Replay: headless mode can be replayed later (1.3). Screenshots: NOT FOUND [unverifiable: absence]; the receipt is field-level.
- Notifications: "Multi-channel notifications: web, iMessage, email, push" [ok: R2/tsenta-ai-disclosure.txt:19]. Daily summary: NOT FOUND [unverifiable: absence].
- Reply tracking: "Recruiter emails land back on the correct application in your tracker. Status moves on its own." [ok: R2/tsenta-auto-apply.txt:25; citation corrected from privacy.txt]. "Tsenta does not send email from your account on its own initiative." [ok: R2/tsenta-privacy.txt:67]

### 1.7 Dedup and safety
- NOT FOUND: already-applied check, blacklist/company exclusion list beyond "Set what to avoid", duplicate-company rule, stop rule, kill switch. Cancel is "one click" for billing only [unverifiable: absence].
- Related: the blog says "Weak Tracking Creates Duplicate Effort" [ok: R2/tsenta-blog-mass.txt:22] but gives no mechanism [unverifiable].
- Honest-quality stance: "Pick a small set of role families" [ok: R2/tsenta-blog-mass.txt:36]. "Mass applying hurts your chances when quantity replaces relevance" [ok: R2/tsenta-blog-mass.txt:26].

### 1.8 Data model
- NOT FOUND. Inferred states from public copy only: matched -> (skipped | drafted) -> (needs review | approved) -> submitted | failed (employer side, refunded) -> replied/viewed/scheduled [ok as the inference's source: "applied, viewed, replied, and scheduled" at R2/tsenta-auto-apply.txt:25; the chain itself is an inference]. Resume after crash / locks: NOT FOUND [unverifiable: absence].

### 1.9 Pricing tied to volume
- Free 25 applications, no card [ok: R2/tsenta-auto-apply.txt:66]. Starter $19/mo = 600 per 30-day cycle; Pro $39 = 1,500; Power $99 = 4,500; credit packs $19/$39/$99 that "never expire" [ok: R2/tsenta-blog-vs.txt:20-22, 33-36]. Quarterly saves 32%, annual 36% [ok: R2/tsenta-blog-vs.txt:36].
- "Allowances do not roll over" [ok: R2/tsenta-blog-claude.txt:75]. One application = "one form actually submitted to one employer" [ok: B/tsenta-pricing-curl.txt:114]. Résumé tailoring and cover letters included on every tier [ok: B/tsenta-pricing-curl.txt:144].

## 2. 6figr JobGPT (6figr.com/jobgpt, mcp.6figr.com)

### 2.1 Batch entry, size, schedule
- Unit of work is a job hunt: a saved search plus autopilot settings (paraphrase, not a quote). create_job_hunt description: "A job hunt defines what jobs you want to find based on titles, locations, skills, salary, etc." [ok: /var/tmp/refs/jobgpt-mcp-server/src/tools/job-hunts.ts:44; tool name at line 42].
- Filters (job-hunts.ts:47-65): titles (max 6), locations, countries, companies, excludedCompanies, skills, remote, baseSalaryMin/Max, expLevels, industries, companySize, h1bSponsorship, relevancy HIGH|MEDIUM, dateOffset (default '7D'), workArrangement (default Full Time), excludedKeywords, excludedTitles [ok: job-hunts.ts:47-65 grep hits for h1bSponsorship, relevancy, dateOffset '7D', workArrangement 'Full Time'; full list not re-counted line by line].
- Auto mode (job-hunts.ts:67): "autoMode ... Enable full autopilot mode (default: false). When enabled, jobs are automatically matched, scored against your resume using AI, and applied to if they meet your minMatchScore threshold. ... Each auto-apply consumes a credit." [ok: job-hunts.ts:67]
- Manual doors: match_jobs "only returns jobs you have not already seen, applied to, or rejected" [ok: src/tools/jobs.ts:56; the earlier cite jobs.ts:222 was wrong, the file has 126 lines]. Limit default 5, max 50 [ok: jobs.ts:24]. apply_to_job: "Trigger auto-apply for a job application. This will automatically fill and submit the job application form." [ok: applications.ts:81]. import_job_by_url accepts "LinkedIn, Greenhouse, Lever, Workday" with autoApply default false [ok: applications.ts:116, 120]. add_job_to_applications saves a job [ok: api-client.ts:555-556, POST /jobs/{id}/create-application].
- Web copy: "Hand-pick jobs yourself or switch on auto mode, with a 'Remote Only' filter" [ok: B/6f-sonara-alternatives-780.txt:72; the source uses curly double quotes]. "You can leave it on full auto or switch to hand-pick mode and approve each job yourself." [ok: B/6f-jobgpt-vs-jobright-ai-which-ai-job-application-tool-actually-works-in-2026-2-593.txt; citation filename corrected, it was truncated].
- Schedule: continuous matching under a daily limit [unverifiable]. No time-of-day windows published [unverifiable: absence].

### 2.2 Caps and pacing
- Per hunt: "Maximum jobs to auto-apply per day (default: 5, max: 100)" [ok: job-hunts.ts:68]. minMatchScore "Default is 0.70 (70%)" [ok: job-hunts.ts:69].
- Marketing: "Volume: 10-20 applications a day by default, and 100+ a day on Apex Predator. That is slower than volume-first tools because each application is tailored first." [ok: B/6f-is-jobgpt-legit-768.txt:82]. The code default is 5 and the blog says 10-20; treat the code as the hunt-level default and the blog as plan marketing [ok as an observation].
- Credits as the other cap: get_credits returns autoApplyQuota / autoApplyQuotaRemaining [ok: job-hunts.ts:11-17; api-client.ts:482-486].
- NOT FOUND: per-company cap, per-ATS cap, delay/jitter, quiet hours [unverifiable: absence].

### 2.3 Form engine
- Sites: "company career pages and ATS forms such as Greenhouse, Lever, Workday and Ashby" [ok: B/6f-sonara-alternatives-780.txt]. Server-side engine is NOT visible; the MCP client only calls POST /job-applications/{id}/auto-apply with optional resumeUri [ok: api-client.ts:537-543].
- Documents: generate_resume_for_job -> POST /job-applications/{id}/generate-resume, returns jsonResume, addedKeywords and pdfUrl [ok: api-client.ts:293-297, 600-602]. GeneratedResume carries "modifications" [ok: api-client.ts:276]. "Each application gets a resume tailored to that job and its own cover letter" [ok: B/6f-sonara-alternatives-780.txt:69].
- Resume choice: "If not provided, the primary resume will be used." [ok: applications.ts:84]. Upload limit 5MB [ok: resume.ts:188-189].
- Match scoring: calculate_match_score -> POST /job-applications/{id}/match-score [ok: api-client.ts:604-605]; aiRelevancyScore is shown as a percent [ok: applications.ts:193].
- NOT FOUND: how custom questions, dropdowns, multi-page flows, answer memory are handled [unverifiable: absence].

### 2.4 Review modes
- Full auto (autoMode true), hand-pick (user picks, then apply_to_job), and default off (autoMode false). Per-hunt customizeResume toggle [ok: job-hunts.ts:70].
- Hybrid: "In JobGPT, hand-pick mode lets you approve the borderline roles and auto mode handles clear matches" [ok: B/6f-auto-apply-tools-software-engineers-779.txt:92]. Advice to exclude dream companies from auto mode [ok: B/6f-auto-apply-tools-software-engineers-779.txt:87, "auto-apply with a manual shortlist for dream companies"].
- No pre-submit form preview is documented [unverifiable: absence]. Result shown after: status, autoApplied flag, appliedDate, matchScore [ok: applications.ts:180-196].

### 2.5 Failure handling
- Credits: "Failed or unsupported applications do not use a credit" [ok: B/6f-sonara-alternatives-780.txt:95].
- Admitted failure: "some users have reported applications failing on unusual ATS forms" [ok: B/6f-is-jobgpt-legit-768.txt:93].
- Stats expose failure counts: ApplicationStats.autoApply {successful, failed, inProgress} [ok: api-client.ts:181-185].
- Credit-out error is rewritten into a buy link [ok: api-client.ts:434-435].
- NOT FOUND: login wall / CAPTCHA / account-creation handling, password storage, retry, needs-you queue, blocked reasons [unverifiable: absence].

### 2.6 Proof and reporting
- Application statuses: PENDING, APPLIED, INTERVIEW, OFFER, REJECTED [ok: applications.ts:24]. Interview tracking: list_interviews "detected from email confirmations", states SCHEDULED, RESCHEDULED, CANCELLED, COMPLETED [ok: applications.ts:140-143].
- get_application_stats with a dateOffset window [ok: applications.ts:8]. Kanban: "every application and outreach email sits on a board" [ok: B/6f-is-jobgpt-legit-768.txt:59]. Screenshots / receipt: NOT FOUND [unverifiable: absence]. Daily summary email: NOT FOUND [unverifiable: absence].
- Outreach after apply: get_job_referrers (max 2) [ok: outreach.ts:23]; send_outreach "The email will be sent from your configured email." [ok: outreach.ts:89]. "If you skip it, applications still run and outreach goes out from a JobGPT sending address instead of yours." [ok: B/6f-is-jobgpt-legit-768.txt:108]

### 2.7 Dedup and safety
- Dedup: match_jobs returns only jobs "you have not already seen, applied to, or rejected" [ok: jobs.ts:56]. add_job_to_applications returns `existing?: boolean`, so create-application is idempotent [ok: api-client.ts:555].
- A rival (Sonara) drew third-party criticism: "quoted in third-party reviews described duplicate applications to the same posting in different cities" [ok: B/6f-sonara-alternatives-780.txt:60]. This is about Sonara, not JobGPT. For JobGPT: NOT FOUND [unverifiable: absence].
- Blocklist: excludedCompanies, excludedTitles, excludedKeywords per hunt [ok: job-hunts.ts:52 excludedCompanies; job-hunts.ts:64-65 excludedKeywords, excludedTitles].
- Kill switch: update_job_hunt status ACTIVE | ARCHIVED | DELETED [ok: job-hunts.ts:110]; autoMode false is the second half of the switch [ok: job-hunts.ts:106].

### 2.8 Data model (from client types)
- JobHunt {id,name,status,autoMode,dailyLimit,minMatchScore,customizeResume,config} [ok: api-client.ts:128-139]. JobApplication {id,jobHuntId,jobId,title,company,url,status,autoApplied,appliedDate,notes,aiRelevancyScore} [ok: api-client.ts:141-155]. Interview {...status} [ok: api-client.ts:158-176]. Credits are account-level [ok: api-client.ts:482-486]. Resume after crash, locks: NOT FOUND [unverifiable: absence].
- "update_job_hunt config REPLACES the whole config" [ok: job-hunts.ts:102].

### 2.9 Pricing
- 5 free applies [ok: B/6f-is-jobgpt-legit-768.txt:30]. 1 credit = 1 application, "including the tailored resume, cover letter and outreach" [ok: B/6f-is-jobgpt-legit-768.txt:73].
- Casual Hunter $34.99 / 100, Active Hunter $64.99 / 200, Apex Predator $95.99 / 400 [ok: B/6f-is-jobgpt-legit-768.txt:76-78].
- Pay-as-you-go: "$0.40 / $0.35 / $0.25 per credit for 100-299 / 300-749 / 750+" [ok: B/6f-jobgpt-vs-jobright-ai-which-ai-job-application-tool-actually-works-in-2026-2-593.txt:107; citation filename corrected]. Credits "never expire and stack" [ok: B/6f-is-jobgpt-legit-768.txt:81].
- "34 tools" [ok as the README claim: README.md:20 "34 tools"]. Code check: 36 server.tool( calls across src/tools/*.ts (applications 8, job-hunts 5, jobs 4, outreach 6, profile 5, resume 8) [ok, a discrepancy to note].

## 3. Jack & Jill (jackandjill.ai) -- NOT a bulk applier
Evidence: R1/jj_faq.txt, R1/jj_applicationfeedback.txt, B/jj-faq-qa.txt, R2/jj2-jack.txt, R1/jj_docs_introductions.txt, R1/jj_pricing.txt.

1 Entry: no batch. Jack "scans 15 million jobs a day, from the 5000+ companies us..." [ok: R2/jj2-jack.txt:23]. "Skip the application pile" with body "When Jack and Jill agree there's a fit, you get a direct introduction to the hiring manager." [ok: R2/jj2-jack.txt:40-44; citation corrected from jj-faq-qa]. Within Jill's network: "You skip the application altogether." [ok: B/jj-faq-qa.txt:4]. "Within 10 minutes of your first conversation" [ok: B/jj-faq-qa.txt:8].
2 Caps/pacing: the limits are on the employer side. Meet Jill: "Request 20 intros a month across your roles" [ok: R1/jj_pricing.txt:28]. Hire Jill: "Request 50 intros a month for each active role" [ok: R1/jj_pricing.txt:37]. Candidate side: "Jack doesn't spam you" [ok: B/jj-faq-qa.txt:6]. No numeric candidate cap found [unverifiable: absence].
3 Form engine: NONE. SuperApply is only a pre-submit checker: "Application Simulator shows what a recruiter's filter systems look for and what the recruiter would see in your profile and documents." [ok: R1/jj_applicationfeedback.txt:63]. The name "SuperApply" is on R1/jj_applicationfeedback.txt:1 and :16 [ok].
4 Review modes: consent-based. "Introductions are consent-based: both you and the candidate must agree before anyone's contact information is shared." [ok: R1/jj_docs_introductions.txt:43]. "Most introductions complete within a few days." [ok: R1/jj_docs_introductions.txt:78]
5 Failure: "Jill will let you know via email when a candidate declines your intro request. You can also see which candidates have passed in the Archive section" [ok: R1/jj_docs_introductions.txt:82]. No login/CAPTCHA concept [unverifiable: absence].
6 Proof: pipeline pages, email intros. "Job tracking" heading: "Keep every opportunity in one place, from first look to final offer." [ok: R2/jj2-jack.txt:51-53].
7 Safety: "He never shares your details with companies without your permission. You can delete your data at any time." [ok: B/jj-faq-qa.txt:24]. Stance against bulk: "Mass AI applications are why nobody gets noticed any more." and "Fewer, better applications beat hundreds that sound like everyone else." [ok: R1/jj_applicationfeedback.txt:141].
8 Data model: NOT FOUND [unverifiable: absence].
9 Pricing: Jack free; Meet Jill is free [ok: R1/jj_pricing.txt:74]; Hire Jill "starts from $199 per active role per month" [ok: R1/jj_pricing.txt:17]; "Scale Jill" custom [ok: R1/jj_pricing.txt:41]. Candidates never pay [unverifiable: inference from the pricing page].
Takeaway (product idea, not a fact): a fit gate (both sides say yes) and a pre-send "application simulator" are the two ideas worth noting.

## 4. Scale.jobs (human appliers)
Evidence: R2/sc2-pricing.txt, R2/sc2-faq.txt, R2/sc2-cancellation-policy.txt, B/sj-rr.txt (live page 2026-10-18), R2/sc2-auto-apply-jobs.txt, R2/sc2-ai-assistant.txt.

### 4.1 Batch entry, size, schedule
- Plans buy a block of applications, one-time payment: Basic $199 / 250 applications, Standard $299 / 500, $399 / 1000 ("You find, we apply"), Ultimate $1099 / 1100 [ok: R2/sc2-pricing.txt:12-42, 66]. The "We Find Jobs" add-on is $199 [ok: R2/sc2-pricing.txt:17]. Newer page: "$599 is one-time for 600" [ok: B/sj-rr.txt:234] and "Two trained assistants find the roles, tailor every resume, and submit 600 applications in your name" [ok: B/sj-rr.txt:6]. The two price sets coexist in the captures; treat as a changed or parallel plan set.
- Entry doors: (a) Chrome extension: "Job delegation: Chrome extension for one-click job sharing" [ok: R2/sc2-faq.txt:161] and "Our Chrome Extension scans and extracts job descriptions from any Job Board or URL using AI" [ok: R2/sc2-ai-assistant.txt:31; citation corrected]; (b) alerts: "Setup job alerts and we apply to all matches" [ok: R2/sc2-faq.txt:363]; (c) brief: "Send your resume plus target titles, locations, compensation range, and companies to skip." [ok: B/sj-rr.txt:47]. The FAQ also lists "Job selection: You choose which jobs to apply for" under "What They Don't Do" [ok: R2/sc2-faq.txt, the "What They Don't Do" list].
- Volume: "A real human assistant applies to 20-50 carefully matched jobs a day for you" [ok: R2/sc2-auto-apply-jobs.txt:283; citation corrected]. Average client "120-150 applications per month" [ok: R2/sc2-faq.txt:501]; aggressive "200-250 applications per month" [ok: R2/sc2-faq.txt:502]. Sponsored 250-300 and VP+ 80-100 [unverifiable: not re-checked].
- SLA: "Under 24 hours (85% of applications)" and "Under 48 hours (99% of applications)" [ok: R2/sc2-faq.txt:234-235]. "Automatic escalation: Triggered after 48 hours with management review" [ok: R2/sc2-faq.txt:236].
- "Priority scheduling: Dedicate first 2 hours daily to applications" [ok: R2/sc2-faq.txt:190], listed under Self-Application Speed Tips, so it is advice for the user, not a service rule.
- Strategy loop: "A real team reviews what's working and re-targets your search with you every 15 days." [ok: R2/sc2-pricing.txt:197-198]

### 4.2 Caps and pacing
- Human speed is the pacing argument: "apply from your own accounts at human speed" [ok: R2/sc2-auto-apply-jobs.txt:303]. Plan credits are the cap. "Credits last 180 days" [ok: B/sj-rr.txt:63]. Two assistants on the 1000 plan [ok: R2/sc2-pricing.txt, "2 Human Assistants"]. NOT FOUND: per-company limits [unverifiable: absence]; a companies-to-skip list exists (4.7).

### 4.3 Form engine
- Humans fill forms by hand: "We manually fill job applications with care" [ok: R2/sc2-pricing.txt:14]; "A person completes and submits each application. AI can help draft the resume. It is not a bot" [ok: B/sj-rr.txt:103].
- Any ATS/portal: "Human assistants apply anywhere you can: any ATS, any portal, any form" [ok: R2/sc2-pricing.txt:189]. Assistants trained on "Workday, Greenhouse, Lever, iCIMS" [ok: R2/sc2-faq.txt:247].
- Cover letters: "AI-generated, human-reviewed for each role" [ok: R2/sc2-faq.txt:52]. AI custom resume per job is an add-on on cheaper plans [ok: R2/sc2-pricing.txt, "AI Custom Resumes ... + Add"].
- Accounts: "ATS accounts: Pre-registered profiles on major systems" is a self-apply tip [ok: R2/sc2-faq.txt:189]. How assistants handle client ATS logins, passwords, CAPTCHA: NOT FOUND [unverifiable: absence]. The page says of bots: "Custom career portals, login walls, captchas, multi-step forms: silently skipped" [ok: R2/sc2-pricing.txt:168; citation corrected from sc2-faq].

### 4.4 Review modes
- Onboarding: "Test application to verify process"; "Cover letter sample generation and approval" [ok: R2/sc2-faq.txt:376, 378; the two list items are joined here with a semicolon]. After that delegated; the client can talk to the assistant "everyday in Team chat" [ok: R2/sc2-pricing.txt:15; citation corrected]. "Client involvement improves outcomes" [ok: R2/sc2-faq.txt, case-insensitive match]. No per-application approval stated [unverifiable: absence].

### 4.5 Failure handling
- Humans replace bot failure modes. "Error reporting and resolution tracking" [ok: R2/sc2-faq.txt]. "Automatic escalation" after 48h [ok: R2/sc2-faq.txt:236]. (The earlier "assistant replacement within 24-48 hours" claim was not in the source; deleted.)
- NOT FOUND: what assistants do on a true CAPTCHA [unverifiable: absence].

### 4.6 Proof and reporting
- "a proof screenshot for every single application" [ok: R2/sc2-pricing.txt:189]. Dashboard: "Real-time status updates: Applied, pending, completed with timestamps" [ok: R2/sc2-faq.txt:384]; "Screenshot documentation" and "Application details: Job title, company, date applied, assistant notes" [ok: R2/sc2-faq.txt:384-386]. "PDF reports: Weekly application summaries with screenshots" [ok: R2/sc2-faq.txt:390].
- Tracker record per application: tailored resume and cover letter, submission confirmation [ok: B/sj-rr.txt:74, 85 "Application #214" and "3 Submission Proof"].

### 4.7 Dedup and safety
- Skip list at brief time: "Add your current employer and any other companies to skip. The team leaves them off the shortlist." [ok: B/sj-rr.txt]
- Safety claim: the service avoids "ATS copycat detection" [ok: R2/sc2-auto-apply-jobs.txt:303 "filtered by ATS copycat detection"]. The LinkedIn-ban part of this claim [unverifiable: not found in the same passage].
- Duplicate check: NOT FOUND [unverifiable: absence]. Stop rule: cancel anytime; "No new applications accepted after request" [ok: R2/sc2-faq.txt:418].

### 4.8 Data model
- NOT FOUND internally [unverifiable: absence]. Visible states: Applied / pending / completed, per-application proof [ok: R2/sc2-faq.txt:384].

### 4.9 Pricing/limits
- One-time, no subscription [ok: R2/sc2-pricing.txt header "One-time payment, no subscription"]. Refund = "Refund Amount = Plan Price - (ProRata Cost + OneTime Fee)", fee $100 [ok: R2/sc2-cancellation-policy.txt:12-14]. Example: "$399 plan for 1000 applications", 100 submitted, refund "$259.10" [ok: R2/sc2-cancellation-policy.txt:17].
- "Credits last 180 days" on the $599 plan [ok: B/sj-rr.txt:63]. "~$3/hour" framing [ok: R2/sc2-faq.txt:21 "$3/hour for 60+ hours of work"].

## 5. Cross-product comparison (what is documented)
| Item | Tsenta | JobGPT | Jack & Jill | Scale.jobs |
| Who submits | cloud browser agent [ok: R2/tsenta-mobile.txt:22] | cloud agent [unverifiable: server engine not visible] | nobody (user presses Submit) [ok] | human assistant [ok] |
| Entry | own crawl 50k pages + user filters + 1-click [ok] | saved "job hunt" filters + manual/URL [ok] | agent shortlist, intros [ok] | user shares via extension, alerts, or brief [ok] |
| Daily cap | none published [unverifiable: absence] | dailyLimit default 5 max 100 (code); 10-20 / 100+ (marketing) [ok] | n/a | 20-50 per assistant per day [ok] |
| Quality gate | match score, user filters [ok] | minMatchScore 0.70 default [ok] | both sides must agree [ok] | human judgement, brief [ok] |
| Review | auto-approve or review-before-submit (6 ATSes) [ok] | auto or hand-pick [ok] | consent [ok] | onboarding sample only [ok] |
| Proof | field-level receipt [ok] | status + Kanban [ok] | pipeline [ok] | screenshot per application + weekly PDF [ok] |
| Failure billing | refunded [ok] | no credit used [ok] | n/a | n/a (the Scale pro-rata refund is a cancellation refund, not a failure refund; removed here) |
| Unit price | ~$0.03 [ok: R2/tsenta-ai-agent.txt:52] | ~$0.25-0.40 [ok: per-credit pay-as-you-go range] | free [ok] | ~$0.40-$1.00 (computed from listed plans: $399/1000 = $0.40 up to $1099/1100 = $1.00; the earlier "$0.50-$1.00" was wrong) |

## 6. Borrow notes for Job360 (store-tool framing, product rules 4 and 5)
Mechanisms worth mirroring as STORE tools (the user's own agent does the doing; Job360 stores). These are design suggestions, not facts from the sources.
1. Batch = a saved "hunt" row: agent-owned filters + `daily_limit` + `min_fit` + `auto` flag. We store; we never source or rank. Mirrors JobGPT job-hunts.ts:42-69 [ok]. Caution under rule 4: `min_fit` must be the agent's own saved verdict (save_fit), not our score.
2. Item state machine with a "needs you" bucket: Tsenta's "needs review / submitted / hit an issue / replied" (R2/tsenta-mobile.txt) and JobGPT's successful / failed / inProgress counts (api-client.ts:181-185) [ok as sources].
3. Review-before-submit gate = our existing check_submit tool [ok: mcp__job360__check_submit is in the job360 tool list]. Tsenta's gate shows answers, résumé and cover letter together [ok: R2/tsenta-changelog-rea.txt:32-33].
4. Receipt = field-level record (fields, answers, résumé version, timestamp, confirmation) [ok: B/tsenta-pricing-curl.txt:115]. Our application_receipts can hold the same [ok: CLAUDE.md names application_receipts].
5. Failure does not consume the allowance: model as `attempt` events that do not count against a daily cap until `applied` [ok as a design idea; source behaviour is B/tsenta-pricing-curl.txt:116 and B/6f-sonara-alternatives-780.txt:95].
6. Skip list at brief time (Scale.jobs: current employer first) and per-hunt excludedCompanies/Titles/Keywords [ok].
7. Idempotent add: `existing` flag on create-application (api-client.ts:555) [ok].
8. Jack & Jill: pre-submit "simulator" is an agent task; we can store its verdict [ok as design idea]. Their refusal to auto-submit is a product stance, not a technical limit [ok as an observation].
Not to copy: CAPTCHA/OTP-mailbox tricks, employer-portal account creation on the user's behalf, speed claims, sourcing crawl.

## 7. NOT FOUND list (explicit)
Tsenta: daily cap, quiet hours, CAPTCHA method, password storage, retries, kill switch, data model. JobGPT: server-side form engine, CAPTCHA, login handling, retry, receipt/screenshot, pre-submit preview (the REA capture ev_5fbb3107 of the app UI is cited in round-1 note only; not re-opened) [unverifiable]. Jack & Jill: any apply feature. Scale.jobs: login/CAPTCHA handling, dedup, internal data model. Tool names of Tsenta MCP (page lists none) [unverifiable: absence].

## Check tally (this pass)
Removed as [wrong]: 9 claims (Tsenta iMessage quote wording; Tsenta "Workday... completes those applications" quote and line; Tsenta reply-tracking citation; jobs.ts:222 line (x2); Jack "Skip the application pile" citation; Scale "login walls" citation; Scale "Within 24-48 hours" replacement claim; Scale "~$0.50-$1.00" unit price; Scale pro-rata listed as failure billing). Corrected citations or quote text: 13.
