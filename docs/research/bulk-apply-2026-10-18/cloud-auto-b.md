<!-- doc: FROZEN | written 2026-10-18: bulk-apply teardown (6 Sonnet readers, Haiku quote checks, Sonnet build list); code reads + REA captures + web reads, labelled per claim -->
# Cluster cloud-auto-b: Massive, AIApply, Wobo, Sonara (QUOTE-CHECKED 2026-10-18)

Tags on every claim:
- `[ok <file>:<line>]` = quote or close paraphrase found in the named file under scratchpad/ (bulk/raw, refs, refs/raw, round2/raw). Paths are relative to scratchpad/.
- `[ok-paraphrase]` = the substance is in the file but the wording differs; the wording is corrected here to the file's text.
- `[unverifiable]` = no copy on disk. Either a live page read that was not saved, or a third-party page. Do not cite as verbatim until re-read.
- `[wrong]` = contradicted by the on-disk capture. Deleted from this file; listed in the deleted log at the end.

Source ranking: AIApply = best documented, but its help-centre pages are NOT on disk; the on-disk AIApply sources are aiapply.co/answers (round2/raw/aia-answers.txt), its terms (round2/raw/aia-terms.txt), and round-1 notes (refs/aiapply.md). Wobo = good (refs/raw/wobo_*.txt, round2/raw/wobo-terms.txt, wobo-privacy.txt). Massive = thin (refs/massive.md notes, bulk/raw/massive_terms.txt). Sonara = defunct per 3rd-party sources; the homepage is on disk, the how-it-works page is NOT (the fetch failed).

No new REA call was made in this check. "REA" appears only where a round-1 note records a real REA evidence id.

---
## 1. AIAPPLY (aiapply.co "Auto Apply")

### 1 How jobs enter the batch
- [unverifiable] "Continuously, while your queue is in the Applying status. There's no schedule or batch window, the system searches for new matches against your criteria and submits applications in the background, up to your daily limit." (help-centre setup article; not on disk).
  - On-disk equivalent [ok round2/raw/aia-answers.txt:108]: "submits applications in the background, up to the daily limit of 70."
  - CONFLICT: the help centre says the cap is user-set 10-100 (section 2); aiapply.co/answers says a fixed daily limit of 70 [ok round2/raw/aia-answers.txt:90, 92, 102, 108].
- [ok refs/aiapply.md, round-1 note] Homepage: "20M+ Jobs. All in One Place." Source of jobs = AIApply's own aggregated board.
- [unverifiable] "We scan thousands of sources daily" and "Continuous daily job scanning" (landing copy; not on disk).
- [unverifiable] Setup form: "After purchasing your subscription, complete the form. It captures your preferences and the resume to be used." (help centre).
- [ok refs/aiapply.md, round-1 REA ev_af15750b] /auto-apply steps: "Upload your resume or LinkedIn profile", "Set your job criteria", "Let AI scan and match jobs", "Generate and submit applications automatically", "Track and manage your applications".
- [unverifiable] Criteria field list (desired titles, experience level, work area, relocation, salary, excluded companies, work authorisation, rejection reasons tab). The on-disk aia-answers.txt covers include/exclude roles, locations, companies and match score [ok refs/aiapply.md, round-1 REA ev_af15750b nodes 1545/1560].
- [ok round2/raw/aia-answers.txt:108] "The most compatible jobs are prioritized first, so fit scores tend to be highest in the early days and may decrease slightly as th[e queue progresses]". (Corrected: the cited wording "prioritised ... gradually widens the range" is not on disk; "gradually widens" is [unverifiable].)
- [unverifiable] Queue status "Done: the queue has been completed and all purchased applications have been submitted (depending on the plan: 100, 250, 600 applications, etc.)".
- Three modes: [ok round2/raw/aia-answers.txt:90] "You can run it in Auto Mode (fully automated), Hybrid Mode (high-fit jobs auto-submitted, lower-fit jobs need approval), or Review Mode (you approve every application)." [ok round2/raw/aia-answers.txt:96] "In Hybrid Mode, jobs with a fit score of 75%+ are submitted automatically, while lower-fit jobs are held for your approval."
  - Help-centre wording "Auto: applies to all matching jobs automatically, with no input from you" / "requires your approval for every job suggestion" = [unverifiable] verbatim.
- [unverifiable] "Press the right arrow to apply, or the left arrow to decline" (dashboard help). "Job Search Board: Aggregates listings and lets you apply (or auto-apply) with a click" (homepage FAQ, not on disk).

### 2 Caps and pacing
- [unverifiable] "Your daily limit. You set this in Job Preferences -> Application volume, anywhere from 10 to 100 applications per day." CONTRADICTED in substance by [ok round2/raw/aia-answers.txt:102] "up to the daily limit of 70". Treat the 10-100 range as unverified.
- [unverifiable] Queue status "Daily Limit Reached ... The queue pauses until the limit resets ... resets automatically each day".
- [ok round2/raw/aia-terms.txt:87] Terms s11A: "we may queue, batch, space, delay, reject or cap requests/outputs/submissions per hour/day/account" and "temporarily restrict specific providers/destinations at our discretion" (the file says "temporarily restrict specific providers/des[tinations]").
- Per-company/per-site cap, jitter, quiet hours, time windows: NOT FOUND.
- [unverifiable] Pause: "pause your queue at any time from the Your Jobs tab using the 'Pause' button. To resume, click 'Start'."

### 3 Form engine
- [unverifiable] "multi-step processes, custom questions, additional information, extended forms. The AI uses your Answer Library, profile data and saved preferences to fill these in." On-disk: [ok round2/raw/aia-answers.txt:98 and :183] says assessment details include "the login credentials needed to access" a company portal, and the inbox holds them.
- [ok round2/raw/aia-terms.txt:97] Terms s15: "we transmit your application data, CV and contact details to the relevant applicant tracking system (ATS), job board or recruitment platform". [ok round2/raw/aia-terms.txt:97] "Some platforms restrict automation or AI assistance; you are responsible for compliance."
- Named ATS list: NOT FOUND.
- [unverifiable] Answer Library: "stores all the answers used for application questions ... By editing an answer in an application ... The next time that question appears in an application, the updated answer will be used." (help centre).
- [unverifiable] Salary logic: "When a job posting publicly advertises its salary range in USD, we may adjust the amount we submit ... We never go below the number you set."
- [unverifiable] Standard identity, DOB, and EEO fields "only when a form requires it / them".
- [unverifiable] Resume: tailored-resume add-on; cover letter "included only when the application requires one".
- [unverifiable] "credentials for any employer accounts created during the application" (Review tab). Closest on-disk: [ok round2/raw/aia-answers.txt:98] "the application details will include the login credentials needed to access it".
- [unverifiable] "Job Fit" view inaccuracies "do not affect what's submitted to employers".
- Field-finding method (DOM vs vision), file upload, dropdowns: NOT FOUND.

### 4 Review modes / what user sees
- [unverifiable] Notification with "Edit before submission" button; statuses "Preparing to Apply" (editable) -> "Applying Now" (locked).
- [unverifiable] "the resume and cover letter used, plus any questions answered" (Applied -> job -> Review tab).
- [ok round2/raw/aia-answers.txt:96] "After applications are submitted, you can also review the exact resume, cover letter, and any application questions used by opening the" [job record].
- [ok refs/aiapply.md, round-1 REA ev_af15750b node 1590] "Monitor every application in your dashboard, see exactly where you've applied, when it happened, and which version of your resume was used."
- [unverifiable] Quick Review swipe queue; decline reasons "Expired, Company, Location, Salary, Job Title, Seniority, Your Skillset".
- [ok round2/raw/aia-terms.txt:97] Terms: "Where you approve a match before we apply, whether by applying manually or in Review mode, you authorise that submission individually. Where you select Hybrid mode or Auto mode, you authorise us at the point of enablement" (the file's wording has a truncated tail; the first sentence is verbatim).
- Practice mode for applications: NOT FOUND.

### 5 Failure handling
- [unverifiable] "Some companies use application systems we can't drive automatically yet. Those listings are skipped and no credit is charged." "You can still apply to a 'not supported' job manually."
- [unverifiable] "A credit is only counted when an application is successfully submitted. If something fails ... that attempt doesn't spend a credit." Retry policy: NOT FOUND.
- [unverifiable] Queue stall states: `Idle: User Inactivity`, `Idle: No Jobs Found`, `Daily Limit Reached`, `Paused by the System` ("Missing required information such as your full name; Invalid or unreadable resume; Other blocking checks"), `Inactive Subscription`.
- [ok round2/raw/aia-terms.txt:95] Terms s14: "Employers/platforms may use AI/bot-detection; we do not guarantee any outcome." CAPTCHA handling: NOT FOUND.
- [unverifiable] Dedicated AIApply mailbox "so employers and job sites can send confirmations, one-time passwords, and login links to an inbox we manage".
- [ok round2/raw/aia-terms.txt:99] Terms s16: "authorise us to store and use access tokens only to perform actions you request." Password storage method: NOT FOUND.

### 6 Proof and reporting
- [unverifiable] Inbox categories (Application confirmation, Interview invite, ..., OTP or verification, EEO form, Other) and "Application confirmations arrive in one daily batch".
- Screenshots: NOT FOUND. Daily summary email: NOT FOUND.
- [unverifiable] Landing counters "Applications Submitted 142 / Job Matches Found 387" (demo data; not on disk).

### 7 Dedup and safety
- [unverifiable] "Will Auto Apply apply to the same job twice? No. Auto Apply tracks every job it has already submitted to and won't apply to the same posting again."
- [ok refs/aiapply.md, round-1 note, web read /auto-apply FAQ] "Why do I sometimes see the same job more than once? ... Auto Apply recognizes this and prioritizes new opportunities". (Corrected: the earlier wording "prioritizes new and active postings" is [unverifiable].)
- [unverifiable] Excluded-company entry rule and "companies you've already applied to directly" blacklist entry.
- Kill switch: [ok round2/raw/aia-terms.txt:85] Terms list "misuse automation" as a prohibited use. [ok round2/raw/aia-terms.txt:82] "Auto-Apply is a non-refundable product. Due to the nature of this service and the resources involved, refunds are not available after applications have been submitted."

### 8 Data model (inferred only from UI)
- Job states in UI and queue statuses: [unverifiable] (help centre). Resume after crash, locks, table names: NOT FOUND.

### 9 Pricing tied to volume
- [unverifiable] Plans "$49/month - 100 applications; $99/month - 250 applications; $199/quarter - 750 applications"; add-ons "$12/month". The on-disk aia-answers.txt has no prices [checked].
- [ok refs/aiapply.md, round-1 web read /auto-apply FAQ] "Each credit is one job application. Buy credits as needed, they never expire." CONFLICTS with the help-centre rollover/loss claim (unverified). Treat as unresolved.
- [ok round2/raw/aia-answers.txt:106] "You cannot repurchase the exact same subscription whil[e]..." (top-ups exist via a "Buy More Credits" button).
- [ok round2/raw/aia-answers.txt:185] "you can wait for the subscription to renew or purchase additional credits".
- [3rd-party, ok round2/raw/aia-vs-loopcv.txt, not used here] Tsenta page says AIApply charges $12 on top for resume and cover letter (refs/aiapply.md).

### Job360 takeaways (AIApply) [analysis, not a claim]
Three modes keyed on a numeric fit score (75%+, ok aia-answers.txt:96), queue status enum, editable window before lock (wording unverified), per-app review record (resume + letter + answers, ok aia-answers.txt:96), counted only on success (unverified), rejection-reason enum (unverified). Ideas only: store and policy, not doers.

---
## 2. MASSIVE (app.usemassive.com; usemassive.com is now an AI-data expert network)

Evidence base is thin: the app is login-gated. The round-1 REA attempt was blocked (`target_not_allowed`, refs/massive.md). Other reads are web research.

### 1 Entry
- [ok refs/massive.md, web read app.usemassive.com] "Massive finds & applies you to the best new jobs every day"
- [unverifiable] "Just set it and forget it!" (not in any saved file; deleted from the claim).
- [ok refs/massive.md] "Get applied up to 200 great jobs every month."
- [ok refs/massive.md] Steps "Fill Out Profile, Get Job Matches, Sit Back" (corrected from "Sit Back & Relax", which is [unverifiable]).
- [ok refs/massive.md] FAQ: "You can manually choose the jobs you get applied to, or turn on Autopilot".
- [ok round2/raw/massive-privacy-excerpt.txt:8] "Hand-vetted companies you won’t find anywhere else." (the file name is misleading; the text is the app landing copy).
- [ok bulk/raw/massive_terms.txt:47] Terms 1.1: "Jobs that may be relevant to you, prepare application materials (including resumes, cover letters, and responses to application questions), and submit applications directly into Employer applicant tracking systems or other platforms."
- [unverifiable] Terms 1.1 questionnaire sentence (the word "questionnaire" appears in massive_terms.txt, but the exact sentence was not confirmed; treat as unverified).
- [unverifiable] "pulls listings from LinkedIn and Indeed as well as from company career pages" (3rd-party, scoutify; page not on disk).

### 2 Caps/pacing
- [ok refs/massive.md] "Get applied up to 200 great jobs every month."
- [ok bulk/raw/massive_terms.txt:55] Terms 1.2(b): "Massive does not guarantee the timing, order, prioritization, or successful delivery of any job application." (exact phrase found on that line).
- Per-day, per-company, jitter, quiet hours: NOT FOUND.
- [ok refs/massive.md, 3rd-party Tsenta blog] "Passive $49/mo 50 jobs, Massive+ $99/mo 200 jobs (Workday, iCIMS)."

### 3 Form engine
- [unverifiable] "Fills out the application on your behalf" (landing; not on disk). [unverifiable] "Our goal is to answer questions as good (if not better!) than you, with 100% accuracy."
- [ok refs/massive.md] features "Custom Resumes for each job", "Contact the Hiring Team: Creates a personalized message to send to the hiring team", "Visa Sponsorship Filters".
- [ok refs/massive.md, FAQ] "we generate a custom resume and cover letter... you can preview it before it's sent".
- [ok bulk/raw/massive_terms.txt:47] Terms 1.1 ATS submission (see section 1 above).
- Which ATS: NOT FOUND (3rd-party mentions Workday and iCIMS only).
- [unverifiable] "they can't detect that your application came through Massive. Our system is designed to submit applications naturally" (landing FAQ, not on disk). Not to copy in any case.

### 4 Review modes
- [ok refs/massive.md] Manual pick vs Autopilot.
- [ok bulk/raw/massive_terms.txt:47] "Massive may, in its sole discretion, provide you a period of time to review, edit, or decline one or more proposed applications before submission (a 'Review Period')."; "may be submitted automatically without additional notice or approval from you, including where no Review Period is offered or where a Review Period expires without action by you."
  - Design read: auto-submit on review timeout = a safety anti-pattern for Job360.

### 5 Failure
- [ok bulk/raw/massive_terms.txt:55] Terms 1.2(a): "certain Employers or third-party platforms may use CAPTCHA, bot-prevention mechanisms, or other tools that may prevent Massive from successfully preparing or submitting applications". (The file has "bot- prevention"; whitespace only.)
- CAPTCHA solving method: NOT FOUND. Retry or needs-you queue: NOT FOUND.
- [ok bulk/raw/massive_terms.txt:48] Terms: "Email Proxy Services: Massive may provide you with a unique, Massive-managed email address for the purpose of submitting job applications". ATS password storage: NOT FOUND.

### 6 Proof/reporting
- [unverifiable] "We show you exactly what we submit on your behalf, including the full application, your resume, and cover letter." (landing). [ok refs/massive.md] FAQ: "We show you exactly what we submit... full application, resume, and cover letter."
- [ok bulk/raw/massive_terms.txt:49] Gmail integration: "for the purpose of sending job- application correspondence from your own Google email address"; "fetch new messages from your inbox via the Gmail history feed, subscribe your inbox to Gmail’s push-notification service, and modify labels of messages Massive processes."
- [ok round2/raw/massive-privacy-excerpt.txt:3] "matched via the unique recipient address Massive generated for the application, or via reply-thread linkage".
- [unverifiable] "Dashboard sorts replies from proxy address" (3rd-party scoutify).
- Screenshots and daily email: NOT FOUND.

### 7 Dedup/safety
- Official dedup: NOT FOUND.
- [unverifiable] Trustpilot complaints (3rd-party, scoutify, rating 1.7/43): "applying for the same exact job over ONE HUNDRED times"; "Applying multiple weeks after the role is posted"; "copies words from the job description inventing that you have expertise". Treat as claims. Lesson for Job360: never invent profile facts, dedupe by job id, and age-limit postings.

### 8 Data model
- NOT FOUND. Only the "Review Period" concept and the proxy-email thread linkage above.

### 9 Pricing
- Landing shows no price (refs/massive.md). [ok bulk/raw/massive_terms.txt:147] "Massive offers subscription plans for Search, Magic Resume, and Auto Apply (and bundles of the foregoing options, in certain cases)".
- [unverifiable] "All payments are nonrefundable." (Massive terms do not show this exact phrase on disk; the nonrefund wording was found as "nonrefundable" in massive_terms.txt, so treat the exact sentence as unverified.)
- [unverifiable, 3rd-party scoutify] ~$59/mo or ~$117/quarter, 4-day trial. Conflicts with Tsenta's $49/$99 figures. Unverified.

---
## 3. WOBO (wobo.ai)

### 1 Entry
- [ok refs/raw/wobo_faqs.txt; refs/raw/wobo_home.txt] "Wobo scans company career pages daily and finds the roles that actually match your experience" (found in refs/raw/wobo_*.txt).
- [ok refs/raw/wobo_home.txt; refs/raw/wobo_pricing.txt] "3M+" jobs scanned (the file shows "3M+").
- [ok refs/raw/wobo_faqs.txt:59-60] Q: "Does Wobo use "Easy Apply" or apply directly on company websites?" A: "...lies directly on company websites. We focus on quality over quantity and avoid "Easy Apply" options to ensure your application gets the attention it deserves." (source uses double quotes).
- [ok refs/raw/wobo_how-wobo-works.txt:64] Lists "Lever, Greenhouse, BambooHR, and SmartRecruiters".
- [ok refs/raw/wobo_faqs.txt] "No, Wobo needs all the necessary information in your profile before it can start searching for jobs."
- [ok refs/raw/wobo_how-wobo-works.txt:54] "Every job Wobo finds is assigned a similarity score (0-100), indicating how well the opportunity matches your persona."
- [ok refs/raw/wobo_ai-job-application.txt:75] "Each role gets a similarity score so only the strongest matches move forward."
- [ok refs/raw/wobo_pricing.txt:47] "Swipe to apply — you're in control." (free/unlimited: user picks). Autopilot: Wobo picks (pricing page): [unverifiable wording].
- [ok refs/raw/wobo_faqs.txt] "Absolutely! You have full control over your job queue. You can cancel any jobs you don't want to apply for."
- [ok refs/raw/wobo_how-wobo-works.txt:58] "uses extensive research to pinpoint the best times to apply, including optimal days of the week and times of the day based on the employer's location."
- [ok refs/raw/wobo_faqs.txt] "applications typically start within a day or two."

### 2 Caps
- [ok refs/raw/wobo_home.txt; refs/raw/wobo_pricing.txt] "5 jobs a day. Wobo applies for you." (Free).
- Unlimited/Autopilot "Unlimited": [ok refs/raw/wobo_faqs.txt] "paid plans include unlimited applications."
- Per-company, jitter, quiet hours: NOT FOUND (only the best-time-to-apply claim above).

### 3 Form engine
- [ok refs/raw/wobo_ai-job-application.txt:78] "For each matched role, Wobo fills out the application, answers screening questions using your persona, tailors your resume to the role, and generates a cover letter in your..." (truncated in file).
- [ok refs/raw/wobo_ai-job-application.txt:303] "24+ criteria per application".
- [ok round2/raw/wobo-*.txt, persona text] "Your Wobo Persona captures your writing voice: the tone and stories that make your applications sound like you wrote them".
- [ok refs/raw/wobo_faqs.txt:82] "Free users apply with their uploaded resume as-is." Paid plans rewrite the summary and bullets "while keeping your titles, employers, and dates exactly as you entered them" [ok refs/raw/wobo_faqs.txt:82].
- [ok round2/raw/wobo-terms.txt] Section 5.2 includes "operate a dedicated application email address and process routine messages, verification codes, and application communications;" and "create an ATS account in your name where required" (the account-creation phrase is in round2/raw/wobo-terms.txt).
- [ok refs/raw/wobo_faqs.txt:61-62] Q: "How does Wobo deal with AI traps in job descriptions?" A: "Wobo's advanced AI analyzes job descriptions thoroughly, including any potential AI traps..."
- Field-detect method, uploads, multi-page, dropdowns: NOT FOUND.

### 4 Review modes
- [ok refs/raw/wobo_pricing.txt:55] "Try Autopilot Free — 5 Days".
- [ok refs/raw/wobo_ai-job-application.txt] "every answer Wobo gave, every cover letter it wrote, and every resume it tailored" (dashboard copy; the corrected wording is from this file).
- [ok round2/raw/wobo-terms.txt:52] "...automatic submissions and whenever Wobo requests a periodic recertification." (Full sentence beginning "You must confirm or update the material Candidate Inputs and automation settings shown by Wobo before first enabling" is [unverifiable] as a verbatim quote; the tail is on disk.)
- Pre-submit edit step: NOT FOUND.
- Feedback loop ("AI Persona learns from every signal"): [ok round2/raw/wobo-ai-job-search.txt] (file text, not re-verified line by line).

### 5 Failure
- [ok round2/raw/wobo-terms.txt:165] "Wobo may proportionately limit a feature, pause applications, suspend an account, ..." The condition "if false info" is [unverifiable].
- CAPTCHA, login walls, retries: NOT FOUND.
- [ok round2/raw/wobo-terms.txt:36] "Wobo does not ask you to give us the password to a third-party social or professional account."
- Password storage for ATS accounts: NOT FOUND.

### 6 Proof
- Tracker dashboard (Applied/Reviewing) with answers and letters: [unverifiable] (landing copy, not on disk as a verbatim line).
- [ok round2/raw/wobo-privacy.txt:22] "Dedicated application email Sender, recipient, subject, body, attachments, verification links or codes, and employer communications sent to the Wobo-provided application address."
- Screenshots and daily email: NOT FOUND.

### 7 Dedup/safety
- [ok refs/raw/wobo_faqs.txt:58] Blacklist: "Go to the "Account" section of your Dashboard and add any companies you want to exclude." (source uses double quotes).
- Already-applied check: NOT FOUND.
- [ok round2/raw/wobo-terms.txt] "revoke this authority prospectively by disabling application services" (Section 5.1). Revoke does not withdraw sent applications (from the same terms; not re-verified line by line).

### 8 Data model
- NOT FOUND (Persona, approved answers, job queue, tracker implied).

### 9 Pricing
- [ok refs/raw/wobo_pricing.txt] Free: "5 jobs a day. Wobo applies for you." Unlimited: user picks. Autopilot: "Try Autopilot Free — 5 Days".
- [ok refs/raw/wobo_pricing.txt] "apply with uploaded resume as-is" (free tier).
- Dollar prices ($34.99 Unlimited, $44.99 Autopilot): [unverifiable] on the saved pricing file (the numbers are not on disk in refs/raw/wobo_pricing.txt; they appear only in other 3rd-party files).

### Job360 takeaways (Wobo) [analysis, not a claim]
Limited-agent authorization list (round2/raw/wobo-terms.txt 5.2) and the recertification gate (5.1); best-time-to-apply; persona-as-voice (we store, agent writes); company blacklist; free/unlimited/autopilot split by "who chooses".

---
## 4. SONARA (sonara.ai) - status check

- [ok round2/raw/sonara-home.txt] Homepage (HTTP 200 at the time of the capture): "We continuously scan millions of openings to find your top matches."; "Wake up to your best matches"; "Submit 10x as many applications with less effort than one manual application."
- [ok round2/raw/sonara-home.txt] "/faq returned 404".
- [unverifiable] "/pricing ... 404 (round 1)" (round2/raw/sonara2-pricing.txt exists; its status not re-checked here).
- DELETED: "web read https://www.sonara.ai/how-it-works (HTTP 200) renders only 'Let's set you up for success!...'". [wrong] The saved fetch of that URL is `HTTP 000` (round2/raw/sonara2-how-it-works.txt), and sonara-home.txt says "how-it-works returned empty markdown". The step text is not on disk.
- [ok refs/raw/sonara_6a1919.txt:21-22] Third-party (Scale.jobs blog, competitor): "Sonara.ai, an AI-driven platform, shut down in February 2024." (text spans two lines in the file).
- [ok refs/raw/sonara_83d77a.txt:128] LoopCV (3rd-party): "multiple reports suggest the service appears to have shut down around 2024, and its current availability is uncertain." (the file's exact wording; prior "REA ev_3c6011bb" label was [wrong]: that evidence id belongs to round2/raw/aiapply-coaches-rea.txt, an AIApply capture, not a Sonara or LoopCV read. Removed.)
- [ok bulk/raw/6f-sonara-alternatives-780.txt:46] Third-party: "The original startup shut down on February 1, 2024 after it could not raise more funding".
- [ok refs/raw/sonara_6a1919.txt:15] Third-party: "We apply to 30 jobs for you every day" (file text: "30 jobs for you").
- [ok refs/raw/sonara_6a1919.txt:53] "900 applications through Sonara.ai. 5.4% interview rate." (Scale.jobs, competitor).
- [ok bulk/raw/6f-sonara-alternatives-780.txt:49] "$2.95 trial that renews at $23.95 every four weeks" (3rd-party reviews, 2026). "$71.40" annual figure: [ok in the 6f-sonara file set; 3rd-party].
- [ok refs/raw/sonara_6a1919.txt:143-144] "Application Process / Automated submissions with time limits" (3rd-party comparison table).
- "trial $2.95 = 10 applications or 14 days": [unverifiable] "10 applications" (the 14-day trial is [ok bulk/raw/6f-sonara-alternatives-780.txt]).
- DELETED: "All you have to do is hit submit" as a verified homepage line. [wrong] The on-disk homepage capture has no such sentence; the homepage says "We find jobs for you" and "We apply for you". The review-then-submit inference is unverified and dropped.
- Items 2-8: NOT FOUND (no official docs readable).
- VERDICT: unverified / likely defunct. A marketing shell is live on the homepage capture; the product login was not tested. Do not rely.

---
## Cross-product comparison for Job360 design [analysis; each row inherits the tags above]

| Topic | AIApply | Wobo | Massive |
|---|---|---|---|
| Batch entry | continuous queue (help centre unverified; on-disk: aia-answers.txt:108) | daily scan, queue, swipe or autopilot | daily matches, manual pick or Autopilot |
| Modes | Auto / Hybrid (75%+ fit, ok aia-answers.txt:96) / Review | Swipe / Autopilot | manual / Autopilot + optional Review Period (auto-submits on expiry, ok massive_terms.txt:47) |
| Daily cap | 70 per aia-answers.txt:102 (help centre says 10-100: unverified, conflict) | free 5/day (ok) | none stated |
| Total cap | credits (prices unverified) | none paid (ok wobo_pricing) | 200/mo (ok refs/massive.md) |
| Proof | per-app review record (ok aia-answers.txt:96), inbox categories (unverified) | tracker + answers (partly unverified) | submitted copy + proxy inbox (ok) |
| Dedup | tracks submitted postings (unverified; refs/aiapply.md has the repost-dedup FAQ) | not stated | complaints of 100x dupes (3rd-party, unverified) |
| Fail charge | no credit on fail (unverified) | n/a | n/a |

What NOT to copy: CAPTCHA/bot-evasion ("submit naturally ... can't detect", unverified), auto-submit on review expiry (ok massive_terms.txt:47), proxy-email identity (ok massive_terms.txt:48), storing employer-site passwords, inventing profile claims.

---
## Deleted in quote check (2026-10-18)

- Sonara how-it-works "HTTP 200 ... renders only 'Let's set you up...'" [wrong]: fetch returned HTTP 000 (round2/raw/sonara2-how-it-works.txt).
- Sonara "All you have to do is hit submit" as homepage text [wrong]: not on disk.
- LoopCV "REA ev_3c6011bb..." label for refs/raw/sonara_83d77a.txt [wrong]: the id belongs to round2/raw/aiapply-coaches-rea.txt.

## Tally

- Mechanical pass: 187 quote fragments split at sentence and ellipsis boundaries (scratchpad/bulk/check3/qc2.py, frags2.json). Found on disk verbatim: 82. Not found verbatim: 105. Most of the 105 are AIApply help-centre quotes with no saved copy; each was then checked by hand against the saved files, and those with a close on-disk match were re-tagged [ok-paraphrase] or [ok] with the file wording.
- Final tags on claim lines: about 87 [ok], 1 [ok-paraphrase], about 54 [unverifiable], 3 [wrong] (deleted; listed above).
- Counts are approximate (grep counts also match the legend and this tally).
