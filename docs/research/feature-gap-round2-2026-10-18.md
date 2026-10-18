<!-- doc: FROZEN | written 2026-10-18, round 2: 28-agent workflow (Sonnet mappers/sweeps/writer/critic, Haiku checkers), REA captures + code reads. Lead re-checked B-20 (avoid_companies unread: setup.md:27 asks, no code reads), B-114 (deadline never filled: bring.py:98 only reads), B-109 (no scam facts: job_checks.py:7). -->
# Job360 gap report, round 2 (2026-10-18)

How to read this. Short sentences, plain words. "They" is a competitor. "We" is Job360.
Tags on an item: no tag means the quote was found in the competitor's own code or docs and checked. [WEB READ] means it comes only from a web page we saved, not the source. [UNVERIFIED] means we could not find the quote in anything saved, or only know the file exists. Read those two as "probably true, not proven".
Round 2 ran as a checking pass over about 45 products. Every competitor quote was searched in the saved copy. Every claim "we lack this" was re-searched in our own code. No quote was contradicted. Several verdicts changed (see section D).

---

## A. What Job360 does today (so you can confirm we read your product right)

Plain summary, by area. Each line was checked against our code or recipes.

**Profile and memory**
- You upload a CV (PDF or Word), a LinkedIn "Save to PDF" and a GitHub name. Plain code reads them. No AI.
- Your assistant reads the whole profile in one call. It sees your standing notes (up to 200 characters each) first.
- Your assistant may write only to a fixed list of fields. If one edit is refused, nothing is saved. Limits: 30 fields per call, 120 edits an hour.
- Every edit is tagged "you said it" or "the assistant guessed". Guesses show as unconfirmed.
- Sensitive answers (equality, salary, visa) are saved only if you say "remember it".
- We remember what job forms keep asking: contact, right to work, logistics, languages, equality, saved answers. Salary is kept per country and never converted.
- A saved free-text answer is reused word for word only if you approved that wording, and only for that company if it was company-specific.
- Website only: profile versions, compare two, restore, take back an assistant's edit, JSON Resume download.
- Setup is six rounds (targets, you, visa, logistics, equality, settings) and it resumes where you stopped.

**Bringing a job in**
- Your assistant pastes an ad it found (or a link we fetch safely). We store it and start a tracked application. The same title and company returns the existing one.
- Each ad comes back with plain facts: full ad or teaser, apply-link type, age, repost, pay mentioned, visa sentence if the ad says one. We never guess when the ad is silent.
- We compare with your own history only: earlier applications to that company, near-identical ad text, five "posting signs" (evidence, never a verdict).

**Hunting (we do not search or rank)**
- A search brief tells your assistant where to look, built from your own right-to-work answers.
- A downloadable "hunt kit" runs on your machine and scans company job boards and open feeds. It filters by words, place, visa, age and years. It never ranks.
- A "run 360 hunt" recipe tells your assistant what to do. No alerts. Nothing runs in the background.

**Fit**
- Your assistant saves its own verdict: a 0-100 score with a one-line reason and a source, or no score (never zero). Hard and soft gaps. Optional radar axes.
- We show where it falls against YOUR score line. The website shows which of your skills appear as words in the ad.

**Documents**
- Every CV, letter and answers document is saved as a new version, forever. We render PDF and Word in the look of your uploaded CV.
- Save-time warnings (the save always succeeds): numbers or terms not in your profile, missing current role, assistant chatter, stock phrases, PDF problems, letter without header or date.
- The assistant gives its own 0-100 ATS opinion per CV. We compute no score.
- Website: line-by-line compare, edit into a new version, copy, download.

**Applying**
- One "application kit" call gives CV, letter, stored answers with who gave them, what is missing, a 30-minute PDF link and the safety state.
- `check_form` compares the live form to memory and flags knock-out questions (years, degree, visa, salary).
- `check_submit` is the single gate: submit, ask you, or stop. It stops on paused, already applied, you said no, daily limit, form not recorded or not re-read, unknown site, wrong site. Indeed and LinkedIn always ask. The first auto-submit is a practice run. Your yes is tied to the exact CV text.
- Settings you control: score line, ask/auto above and below it, submit mode, daily cap (up to 500), pause. Riskier changes wait for your click on the website.
- CAPTCHA, login wall or broken page: recorded as blocked, you are asked, submit stays stopped. We never solve a CAPTCHA and never store a password.
- Receipt: a frozen copy of the CV, letter, answers and fields. Proof levels: email, thank-you text, screenshot, none. Asks once if no proof after 7 days.

**Tracking**
- Append-only history of every event; a wrong one is fixed by a correcting event. Each event says who wrote it.
- A "next step" code per application, follow-up dates, due and quiet lists, "what's new", full export.
- Applied jobs with no news for 45 days are marked ghosted, but only after the inbox was read.
- Questions your assistant could not answer go to a "Needs you" page. Your chat answer counts as your word.

**Email and calendar**
- We read no email. Your assistant reads your Gmail with its own connector and records events. We store how far it read.
- A confirmation email is stored as proof, not a second "applied". The same email twice records once.
- Interview time is stored with its time zone. You get a private calendar link and a one-interview .ics file.

**Outreach and contacts**
- Add people (recruiter, referral, hiring manager); the same email returns the same person. Message drafts are versions. We send nothing.
- `check_outreach` gives send, ask or stop. Caps: 10 auto-sent a day, 2 per company in 14 days. LinkedIn messages are always sent by you.
- Three automatic employer emails are gated: a 7-day check-in, a feedback request after a rejection, one follow-up.

**Interviews, offers, results**
- Recipes for prep and debrief. Stored: transcripts, story bank, round records, up to five red-flag signals with a quote, company process problems, skills tests.
- Offer events can hold amount, currency and period. Hired and offer-declined exist. No negotiation or salary benchmarks (confirmed absent).
- Stats are counts from your own events with the sample size on every rate: funnel, speed, score-band check, by company, did-a-lesson-help, patterns, skill gaps.
- Lessons are saved only on your yes and come back with your profile.

**Platform and safety**
- 32 tools over MCP, with 12 playbooks as a tool and as slash commands. Sign-in by OAuth 2.1 or personal tokens. Stale tool lists are detected. Unknown arguments are refused by name.
- Your assistant can only touch jobs you brought. Authorship comes from the token. Pages and emails are treated as data, never orders.
- Website: sign-in by emailed link or password, download everything, delete account, trust page, consent banner.

**Things in the map that are only partly true (please confirm or fix)**
1. "Pause stops all apply work": the server stops submit, outreach and auto emails. It does not stop the kit read or CV writing. The rest is the assistant's manners.
2. CV writing rules (section order, 2-line summary, bullet length, 3 skill lines, no photo) are recipe text. Code checks only numbers, terms, current roles and letter header. The filler-word list is short and not applied to CVs ("results-driven" is not in it).
3. "Salary floor never stored": there is no floor field. Setup stores min and max per country. The recipe does not say how a private floor differs.
4. The 14-day follow-up date after applying is the assistant's choice. No code sets it.
5. Auto-email: the recipient rule and the 150-word limit are not checked by code.
6. `daily.md:36` says record "applied + source" for a confirmation email. That clashes with your 2026-10-15 rule (a confirmation for an already-applied job is a note). The code does the right thing; the recipe line is wrong.
7. The verified-email gate covers saved tailored documents (`tailor.py:134,233`) but not CV/letter view and download on the application (`applications.py:1864,1879`, sign-in only).
8. Two stale comments: `core/settings.py:716` lists check times 3h-24h (code allows 1h too). `core/settings.py:76` says delivery is "email + webhook only" and cites a plan file that is not in the repo. No webhook sender exists.
9. No code reads ATS application forms. The hunt kit uses board APIs for job discovery only.
10. Trust page: screen recording covers 10% of sessions AND every session with an error, text not masked.
11. Our only emails: sign-in link, email verification, password reset. No job emails.
12. (Critic) The ad closing date is a dead field. The column exists (`repositories/database.py:105` `deadline TEXT`, `api/models.py:62` "Application deadline") and `mcp_server.py:217` returns `"deadline": job.deadline`, but nothing in `services/` or `api/routes/bring.py` writes it, and no recipe or due list says "apply by". Daily step 8 "Deadlines" (`daily.md:62`) is about outreach, not ad closing dates.
13. (Critic) On `hired`, the feedback recipe only says "congratulate them" (`feedback.md:34`). Nothing offers to close your other open applications.

---

## B. Gaps we can borrow (they fit our rules)

Ranked inside each area, best first. Most are small: a field, a check, a recipe line, or a view over data we already hold. "Recipe line" means text in a playbook, no new code.

### Profile and memory
B-1. They: check CV claims against GitHub (merged pull requests in other people's projects) (career-ops). We: GitHub import only; nothing checks a claim. Borrow: a check or recipe step comparing CV claims with what GitHub records; the assistant fetches.
B-2. They: a saved writing voice: a sample of your writing plus tone settings (Trackly; Wobo "Persona" [WEB READ]; JobCopilot sliders [WEB READ]; ai-job-search behavioural profile). We: 200-character standing notes only. Borrow: a "voice" slot (sample text, tone) the assistant reads before writing.
B-3. They: you re-confirm your inputs and automation settings before auto-submit and when asked (Wobo terms). We: practice run only; never asks you to re-check the profile. Borrow: doctor flags "profile not re-confirmed in N days".
B-4. They: a per-fact switch "do not use this" (Reactive Resume). We: an excluded-skills list only. Borrow: the same flag on any profile fact.
B-5. They: feedback on a past form answer, "how should it have answered?" (Wobo). We: receipt shows answers, read-only. Borrow: mark a past answer wrong on the receipt and give the better wording; it becomes an approved answer.
B-6. They: one place for certificates, transcripts, any career document (Careerflow). We: a closed list of document kinds. Borrow: allow more kinds.
B-7. They: a search status: actively searching, open to roles, closed (Himalayas [WEB READ]; Jack & Jill "happily employed"). We: pause only. Borrow: a status field the assistant reads.
B-8. They: a revision number so two assistants cannot overwrite each other (Trackly) [UNVERIFIED: the saved quote is about answer states, not revisions]. We: none. Borrow: `expected_revision` on profile writes.
B-9. They: offer to save your edit of an answer as the approved wording (JobCopilot "learns from every edit" [WEB READ]). We: approved only on your yes. Borrow: ask after an edit; never learn silently.
B-10. They: read extra public links (portfolio, Kaggle, Scholar) to enrich the profile (ai-job-search /expand). We: GitHub and dependency files only. Borrow (low): a recipe line; the assistant writes via update_profile.

### Bringing a job in
B-11. They: import past applications from a spreadsheet (Reactive Resume, Simplify, jobwyre template; JobCtrl does contacts). We: one ad at a time. Borrow: CSV import.
B-12. They: add many jobs in one call (10) and look a job up by link first (jobsync). We: one at a time; same title and company returns the existing job. Borrow: batch add and find-by-link.
B-13. They: pick recruiter email addresses out of the ad text (JobSpy). We: you type the email. Borrow: list addresses found in the ad as a plain fact.
B-14. They: say whether pay was read from the board or guessed from the text (JobSpy). We: pay facts carry no source. Borrow: a source flag on the pay fact.
B-15. They: optional company facts on a job: size, industry, funding, rating (get_jobs, JobSpy, Huntr [UNVERIFIED for Huntr]). We: none. Borrow: optional fields the assistant fills, only when it knows; we never look them up.
B-16. They: a default due date on a newly added job, 3 days (jobsync). We: none in code. Borrow: a default follow-up on bring.
B-17. They: keywords from the ad highlighted on the saved job (Huntr). We: skills-in-ad list only. Borrow: highlight ad keywords in the ad view.
B-18. They: distance radius filter, easy-apply filter (JobSpy, jobspy-mcp-server). We: place text and country only. Borrow (low): radius in the hunt kit.
B-19. They: map view or drive time per job (Huntr; career-ops commute plugin). We: country and remote flag only. Borrow (low).

### Hunting and skipping
B-20. They: a "do not apply" company list that acts as a gate with a reason and override (career-ops; LoopCV and Wobo exclude lists; Auto_job_applier company-text bad words). We: we store `avoid_companies` but no code reads it on bring or submit. Borrow: warn on bring and in check_submit. HIGH value, small change.
B-21. They: skips that last: skip this job, this company, or this site for good (Trackly). We: skip is logged per application with a reason. Borrow: scope on the skip.
B-22. They: counts of why jobs were thrown away (career-ops discard analytics); a quick reason after a thumbs-down (LoopCV, Wobo). We: skipped.txt is a file nobody reads. Borrow: closed skip reasons stored and counted in stats.

### Fit
B-23. They: a language check before scoring: does the ad need a language you do not hold? (ai-job-search). We: languages are stored; nothing compares. Borrow: an ad fact "language required" plus a flag.
B-24. They: more plain checks on the ad text: fixed-term wording, pay-range width, AI-screening mention (career-ops oferta-signals); "citizens only" wording, template or location-tag mismatch [UNVERIFIED, files exist only]; agency-licence pointer; "citizens only" and jurisdiction-banned wording such as salary-history questions against a dated law table (career-ops oferta-signals 11 and 12; grep found nothing for these in our backend). We: pay mentioned, age, repost, sponsorship mentioned. Borrow: add as plain facts, never a verdict.
B-25. They: compare two jobs side by side (FoundRole). We: no compare view. Borrow (low): a view of two saved fits.

### Documents
B-26. They: PDF read-back tests for columns, tables, text boxes, headers and footers (Resume-Matcher; JobShinobi). We: unreadable, garbled, contact, page count only. Borrow: add those checks. HIGH value.
B-27. They: a second reader reviews the CV: external hiring-manager audit (career-ops), reviewer agent (ai-job-search), six persona judges (JobCtrl). We: the assistant that wrote it also checks it. Borrow: a recipe step "run a separate reviewer pass and save the notes". Job360 stays AI-free.
B-28. They: compare job titles and dates in the CV with the profile; check story bank facts (career-ops cv-title-check, story-provenance-check; Resume-Matcher protected fields). We: numbers, terms, current roles. Borrow: title and date check.
B-29. They: reuse an earlier tailored CV for a similar ad (career-ops jd-similarity). We: flag near-identical ads only. Borrow: show the earlier CV version next to the flag.
B-30. They: about 55 banned filler phrases, strict/normal/lenient modes, checks on CV too (ApplyPilot). We: short list, letters/outreach/answers only, warn-only. Borrow: longer list, apply to CV, optional strict mode.
B-31. They: ask for real numbers when bullets are weak, up to 6 questions (Resume-Matcher). We: truth-only rule. Borrow: recipe step.
B-32. They: cover-letter angle questions before drafting (career-ops); a six-second clarity gate (career-ops). We: letter rules only. Borrow: two recipe lines.
B-33. They: date-dash check on the PDF text layer (ai-job-search). We: glyph check only. Borrow (small).
B-34. They: Markdown or LaTeX export, template choice, "Page n of N" footer, editable letterhead (career-ops, Reactive Resume, JobShinobi, Rezi, Kickresume, freehire; jobsync). We: PDF and Word in your own CV's look. Borrow (low): Markdown export, page footer. Template choice is a design choice you made on purpose.
B-35. They: translate a CV and verify the translation (Kickresume; JobCtrl locale variants). We: a translated CV can be saved as a version, nothing checks it. Borrow (low): a language label on a version.
B-36. They: a tailoring-strength setting and a change report (Simplify; Kickresume). We: fixed truth-only. Borrow (low): change report as a recipe line.
B-37. They: a resignation letter (Rezi, Kickresume). We: cover letter only. Borrow (trivial): one more document kind.
B-38. They: extra CV section types: certificates, committees, honours, writing [UNVERIFIED, files exist only] (Awesome-CV). We: fixed sections. Borrow (low).

### Applying
B-39. They: a hard cap per company: 3 applications per company per 6 months (JobCopilot). We: same-company in 30 days is flagged, never blocks; outreach has a company cap. Borrow: a user-set company cap in check_submit.
B-40. They: a list of sites that are always done by hand (ApplyPilot manual_ats). We: only Indeed and LinkedIn hard-coded. Borrow: your own always-ask list.
B-41. They: rules to skip honeypot fields, never grant camera/location/ID/payment, stop on pages that are not an application with a code (ApplyPilot). We: none of these. Borrow: recipe lines and a blocked reason.
B-42. They: apply by email for email-only postings (ApplyPilot). We: no recipe. Borrow: a recipe gated by check_submit.
B-43. They: account for every form control with a reason; never overwrite what the user typed (Trackly). We: read back every value; no keep-user-typing rule. Borrow: per-field result in `form_swept`; one rule line.
B-44. They: plain-language glossary for legal questions: ITAR, interview recording, non-compete (Trackly). We: knock-out words list. Borrow: extend forms recipe and knock-out list.
B-45. They: repeat-application override needs a written reason and is used once (JobCtrl). We: "not a duplicate, go ahead". Borrow: store the reason.
B-46. They: save a failure screenshot with the failure (Auto_job_applier). We: proof screenshots only. Borrow: attach one to a blocked record.
B-47. They: server-owned batch record that can be recovered; access-probe states [UNVERIFIED] (Trackly). We: per-session list. Borrow (low).
B-48. They: record a real application and replay it as a test (neonwatty; spec found, other part [UNVERIFIED]). We: per-site notes from what was seen. Borrow (low): developer test bed for the form playbooks.
B-49. They: answers converted to the unit the form asks (months or weeks, per month or year) (Auto_job_applier). We: notice period is free text. Borrow (low): store number plus unit.

### Tracker
B-50. They: search, tags, archive, favourite, saved views, sort, filters with counts (Reactive Resume, jobwyre, jobsync, Simplify, Careerflow, JobCtrl). We: status chips, in-flight, due only. Borrow: text search first, then tags and archive. HIGH value.
B-51. They: move or tag many at once (Reactive Resume). We: one at a time. Borrow: multi-select.
B-52. They: CSV or Excel export (jobsync, Simplify, JobShinobi, jobwyre; JobsPipe [WEB READ]). We: JSON only. Borrow: CSV export.
B-53. They: month calendar page in the app (Reactive Resume, jobwyre; freehire [WEB READ]). We: outbound .ics only. Borrow: a calendar view over stored interviews and follow-ups.
B-54. They: to-do tasks with deadlines per application (jobsync, Careerflow). We: notes plus one follow-up date. Borrow: a small task list.
B-55. They: extra states: waiting for referral (jobctl), on hold (jobwyre), negotiation or seen (Himalayas [WEB READ]), interview cancelled or rescheduled (6figr), reference check (jobsync `AddStageDialog.tsx:179` placeholder "Panel Interview, Reference Check…"), accepted offer and accepted another offer (Reactive Resume `stages.ts:19,22`). We: none of these. Borrow: waiting_referral, on_hold, cancelled/rescheduled events. Check against settings first.
B-56. They: your own priority A/B/C and an excitement rating (jobctl; Himalayas [WEB READ]). We: none. Borrow: stored for you; Job360 never ranks.
B-57. They: days in this stage on the row (Reactive Resume). We: next-step code and due tag. Borrow (small).
B-58. They: a different silence threshold per stage: 21/18/15/12/5 days (freehire code; page [WEB READ]). We: one flat 45-day ghost rule and an assistant-set quiet filter. Borrow: per-stage quiet counts as a pull view (no push).
B-59. They: restore from backup and undo an import (JobCtrl, jobsync). We: export only. Borrow: import from our own export.
B-60. They: weekly applications chart, goals, streaks, hours (Huntr, jobsync, freehire); a career-goal tracker and a job-search journal (Teal, web read raw/teal-web-read.txt: "Career Goal Tracker Take charge of your career with the Career Goal Tracker"); daily goals and time spent (jobsync). We: counts only; no goals, no journal (grep found nothing: goal_tracker, journal). Borrow (low): weekly count chart, a free-text search journal. Warning: `VISION.md:56` lists "Charts-heavy analytics" as not built, so keep it to counts.
B-61. They: a suggested status from mail with approve/decline (Simplify; freehire [WEB READ]). We: record at once, fix by correcting event. Borrow (low): opposite policy, only if you want it.
B-62. They: pick the widgets on your dashboard (Careerflow). We: fixed Home. Borrow (low).

### Email and calendar
B-63. They: calendar alarms the user's calendar app can ring (jobwyre reminders). We: feed has no alarm lines, always 60 minutes. Borrow: alarm lines, duration, a skipped-hour check (Reactive Resume).
B-64. They: read the user's calendar to attach interviews, and Outlook mail (career-ops, freehire). We: Gmail only; no calendar step. Borrow: a recipe step using the assistant's calendar connector.
B-65. They: emails for when the ATS breaks mid-process and when an interviewer did not call (career-ops). We: blocked record only; silence nudge days later. Borrow: two recipe templates and event types.
B-66. They: referral-request and cold-application email variants (career-ops); 40+ templates by stage incl. offer (Teal, Simplify). We: one outreach draft. Borrow: recipe variants.
B-67. They: tell receipt from invitation by tense; ignore course mail; read several mailboxes (apply-potato). We: none of these. Borrow: recipe lines; an inbox cursor per mailbox; a once-a-day Promotions-tab look that only tells you (apply-potato `CLAUDE.md:32`); link a mail to an application by headers first, body second, and cite the exact sentence (JobCtrl `outcomes-and-feedback.md:33`); file stage emails under the job (apply-potato).
B-68. They: keep a copy of each stage email with the job (apply-potato). We: message id plus feedback words. Borrow (low): store the text as proof.
B-69. They: weekly printable report with screenshots (Scale); one-page work-search report (Applendium [UNVERIFIED verbatim]). We: weekly recipe only. Borrow: printable activity report.
B-70. They: a public changelog (Tsenta, jobwyre). We: none. Borrow (small).

### People
B-71. They: import contacts from CSV, vCard, LinkedIn Connections; warm lead first before searching cold (career-ops, JobCtrl, Simplify; Careerflow [UNVERIFIED]). We: one at a time. Borrow: import, plus a "check my people first" step. Storing a whole connections list is for you to decide (decision 9).
B-72. They: relationship in your words, last-contacted date, worked-together dates; a role per job link; a reference filter (jobsync). We: name, role, email, link, found_via. Borrow: those fields.
B-73. They: calls and meetings on a contact's timeline (Careerflow). We: messages only. Borrow: call and meeting event types.
B-74. They: a company page with notes, size and links (jobsync). We: company stats only (grep 'watchlist' found nothing). Borrow (low): company notes and a watch toggle (jobsync `wiki/companies.md:16` "**Watch** (or **Unwatch**)").
B-75. They: reminders per person; a second nudge at 14 days (JobCtrl). We: one suggested follow-up at 7 days. Borrow: a per-person due date, pull only.
B-76. They: vCard export; LinkedIn note of 300 characters or less; three-search ceiling (career-ops). We: none. Borrow: recipe lines, vCard export.
B-77. They: a source policy before fetching contact info (JobCtrl). We: none. Borrow (low): recipe list of sources not to use.

### Interviews
B-78. They: a practice interview with feedback and a re-drill of weak spots (career-ops; Final Round, Rezi, Teal, Careerflow, AIApply, Jack & Jill, Simplify mostly [WEB READ]). We: prep pack once; the "practice/mock" source exists but no recipe. Borrow: a practice recipe the user's assistant runs, saved through interview_session. No scoring by us.
B-79. They: audience map, panel intel, AI-interviewer notes, coffee-chat cross-check, time-blocked plan (career-ops). We: five questions whoever interviews. Borrow: recipe sections.
B-80. They: consistency brief against the CV and letter you sent (ai-job-search). We: prep reads the sent CV; no brief. Borrow: recipe line.
B-81. They: a question bank with tags and ticks (jobsync, JobCtrl library). We: story bank only. Borrow: your own tagged bank built from your debriefs.
B-82. They: richer interview record: outcome, format, length, link, list of interviewers; a page across all jobs (jobsync, Reactive Resume). We: round, date, tags, gaps, one interviewer role. Borrow: those fields and a cross-job page.
B-83. They: spot misfit from what you said fluently in the room (career-ops). We: red flags from interviewer turns only. Borrow: recipe line.
B-84. They: a retracted-claims list that gates practice (career-ops). We: none. Borrow: store claims you retracted.

### Offers and pay
B-85. They: side-by-side offers where "unknown" is not zero (Reactive Resume; 6figr; Scale). We: figures on one event. Borrow: store base, bonus, equity, leave, deadline and show offers side by side.
B-86. They: a negotiation plan from your offers and needs (career-ops, Reactive Resume, JobCopilot, 6figr, Jack & Jill, Scale, Simplify, Final Round). We: none (confirmed absent). Borrow: a recipe built only on your stored offer data; no market numbers.
B-87. They: a contract-reading helper, never says "safe to sign" (career-ops). We: none. Borrow (low): recipe with a lawyer-questions list.
B-88. They: "salary I asked for" and figures already told to each employer (jobctl; career-ops plan). We: none. Borrow: store them.

### Stats
B-89. They: score distribution and filters on the dashboard (ApplyPilot). We: no distribution. Borrow (low).
B-90. They: save and share a funnel snapshot as an image (Simplify). We: none. Borrow (low). Warning: sharing images sits near `VISION.md:56` "Charts-heavy analytics"; check with the owner.

### Platform and assistants
B-91. They: tool hints "read only" or "destructive" so clients auto-approve reads (jobctl-claude; Reactive Resume [UNVERIFIED]). We: none. Borrow: add hints. Small, helpful.
B-92. They: choose read-only or read and write at consent (jobwyre). We: one scope. Borrow: a second scope.
B-93. They: assistant can ask how many writes it has left (jobwyre). We: refusal text only. Borrow: remaining allowance in doctor.
B-94. They: connect guide per client: Codex, ChatGPT, claude.ai, Cursor, Windsurf (jobctl); Hermes, OpenClaw, Grok (Himalayas [WEB READ]). We: one page naming five assistants. Borrow: a section per client.
B-95. They: type a one-line add in a terminal (jobctl). We: setup-profile only. Borrow (low).
B-96. They: a plain-text site summary for AI assistants, `llms.txt` (jobctl [UNVERIFIED]). We: none. Borrow (trivial).
B-97. They: a named maintenance error code (Trackly). We: none. Borrow (trivial).

### Trust, accounts, access
B-98. They: text hidden in screen recordings; operator cannot see your job data (jobwyre, Careerflow). We: Sentry replay records 10% of sessions plus every error session with text not masked, disclosed on the trust page. Borrow: mask text or switch it off. This is your call. Also Tsenta has a bug bounty page (web read https://tsenta.com/bug-bounty); we have none (grep found nothing: bounty, security.txt). Borrow (low): a security.txt.
B-99. They: passkeys and two-factor sign-in (Reactive Resume [UNVERIFIED, files exist only]; JobsPipe [WEB READ]). We: none. Borrow: both.
B-100. They: sign in with Google, GitHub or LinkedIn (jobwyre). We: link or password. Borrow: Google first.
B-101. They: list of signed-in devices with per-device sign-out (jobwyre). We: all sessions end on password change. Borrow: the list.
B-102. They: a written clause that the agent has limited, revocable authority and is not a power of attorney (Wobo). We: hard lines and trust page, no clause in Terms. Borrow: add to Terms.
B-103. They: an accessibility statement, skip link, reduced motion (jobwyre). We: none. Borrow.
B-104. They: a 90-day log of every API request (JobsPipe [WEB READ]). We: server audit log; you see an assistant feed. Borrow (low): your tool-call log.
B-105. They: sample data and a demo with a Reset button (jobwyre, JobCtrl). We: welcome card only. Borrow (low).

### Website
B-106. They: installable web app / widgets (jobwyre; the native apps below are in section C). We: no manifest, no service worker. Borrow: installable web app.
B-107. They: command bar and keyboard shortcuts (Reactive Resume, file exists). We: none. Borrow (low).
B-108. They: interface in 20 to 50+ languages (career-ops, Reactive Resume). We: English only. Borrow (low, large work).

### Critic additions (found by the completeness check)

B-109. They: scam hard stops before you share anything: any request to pay (training, equipment, placement fee, deposit), a cheque to deposit and forward, paid "tasks", reshipping, running a repo a recruiter sends, ID or bank details before a written offer, an unsolicited WhatsApp offer (Reactive Resume `job-application-manager/references/intake-checks.md:58-64`, e.g. "Any request to pay: training, equipment, certification, placement fee, a deposit"); safe take-home advice (`:66`, run in a disposable container). We: none. `job_checks.py:7` says "nothing here says ghost, scam or risk" and `check_form` knock-outs are about years, degree, visa, salary. Borrow: plain ad facts ("the ad text asks for a fee or deposit", "contact is WhatsApp only", "free-mail recruiter address") shown as facts, plus recipe stop lines and a `check_form` stop on ID or bank fields before an offer. HIGH value: a seeker's worst loss is a scam, and this fits "facts, not verdicts".
B-110. They: offer-deadline messages: "expedite" note to other employers when you hold an offer with a deadline, and a request for more time (Reactive Resume `references/messages.md:162` "Expedite (another offer has a deadline)", `:210` "Ask before the deadline"); follow-up scheduled 2 business days before the written deadline (`pipeline-rules.md:64`). We: offer events hold amount only; no deadline-aware step (B-85 stores the deadline; this adds the messages and the due date). Borrow: recipe variants plus an offer-deadline due date.
B-111. They: a script for "What is your current salary?" with the legal background, and "never state a pay figure the user does not have" (Reactive Resume `messages.md:222`, `packages/ai/src/prompts/career-system.md:9`). We: salary kept per country; no guidance on this question (grep found nothing: 'salary history' in backend/src). Borrow: forms-recipe line; the answer stays yours.
B-112. They: an "accepted" and "accepted another offer" ending, and withdrawal and rejection message templates (Reactive Resume `stages.ts:19,22`; `messages.md` section 7 "Endings: rejection, withdrawal"). We: `hired` and `offer_declined` exist; `feedback.md:34` stops at "congratulate". Borrow: on `hired`, a step that lists your other open applications and proposes `withdrawn` with your yes, plus a polite withdrawal template. Small, high value for a real seeker.
B-113. They: tag how the application started (referral, recruiter-inbound, cold) and compare reply rates by route (Reactive Resume `intake-checks.md:20-22` "Employee referral ~40%" vs inbound ~3%; `weekly-review.md:60`). We: `found_via` on contacts only; stats are by company and CV label. Borrow: a "route" on the application and a count by route (counts with sample size, as the rest of our stats).
B-114. They: an ad closing date that drives a reminder (none found in the cloned trackers; jobsync has a due date per job, B-16). We: dead field, see A-12 above. Borrow: fill `deadline` on bring when the ad states one ("apply by") and list "closing in N days" in the due view. Small, uses a column we already have. Competitor evidence: NOT FOUND.
B-115. They: an Evidence Map: each achievement or skill with source pins, freshness, and which resumes used it (JobCtrl `docs/user/product-tour.md:174` "Evidence is a master-detail workspace over canonical achievements and skills."). We: line provenance per tailored CV line, no reverse index. Borrow (low): a view listing which CV versions used each fact.

---

## C. Gaps that break our rules (not recommended, but listed)

Rule 4 = never source, rank or recommend jobs. Rule 5 = if the user's own assistant can do it, give it a store tool, not a "do" tool. Decision numbers are from `docs/product/VISION.md`.

C-1. They: find jobs, rank them, daily matches, matched feeds, "describe a job in a sentence" search, big job databases (freehire, JobsPipe, Himalayas, Indeed, Kickresume, Sonara, Massive, 6figr, LoopCV, JobCopilot, Jack & Jill, Jobright, Simplify, Wobo, Tsenta, jobspy-mcp-server, JobSpy). Why not: rule 4 and decision 1. Your assistant hunts; we store.
C-2. They: job alerts, saved searches, follow a company, digests (freehire, JobsPipe, FoundRole, Teal, LoopCV, Kickresume, apply-potato, jobsync). Why not: nothing runs in the background; decision 11 is pull, not push. Rule 4 also.
C-3. They: a company or job score from outside data: Wobo Score, company reviews, ratings, competitiveness, applicant counts, "under 10 applicants" (Wobo, Indeed, freehire, Simplify, Auto_job_applier, Himalayas). Why not: rule 4 and rule 5. Also ghost-job scores graded from eight checks and public job-age stats (freehire; `job_checks.py:7` says our checks never say "ghost", "scam" or "risk").
C-4. They: our-own match or resume score, hard blockers that cap a score, resume scorers (Rezi 23 metrics, Huntr, freehire, JobShinobi, LoopCV match rating). Why not: rule 4 and decision 5. The assistant judges; we store.
C-5. They: an AI of their own: tailoring, letters, mock interview scoring, personas, builder, headshots, roast, coaching, career map, LinkedIn optimizer, project and course advice, "Orion" (freehire, Kickresume, Rezi, Final Round, Jobright, career-ops, Careerflow, Himalayas). Why not: decision 28, Job360 has no brain of its own.
C-6. They: salary and market data: benchmarks, percentiles, market stats, filed wages, H-1B history, purchasing power, occupation demand (Mads dataset, JobCtrl, Himalayas, Jack & Jill, FoundRole, freehire, Final Round, 6figr, career-ops). Why not: rule 5 and decision 27. Your assistant can read Indeed or Himalayas itself.
C-7. They: reads your Gmail on their server, gives you an inbox address, forwarding address or proxy address (freehire, Tsenta, Massive, Wobo, AIApply, JobShinobi, Applendium, Tsenta; apply-potato). Why not: decisions 8 and 19. We read no mail.
C-8. They: push to your phone and chat apps, reminder emails, welcome and announcement emails, onboarding emails, bell, scheduled sends (Telegram/WhatsApp/iMessage/Discord/WeCom in Tsenta, freehire, Wobo, Scale.jobs, get_jobs, apply-potato, AIHawk, jobwyre, LoopCV, Careerflow, FoundRole, JobShinobi, LazyApply). Why not: decisions 11 and 23 (WhatsApp waits for a paying signal); the Slack/Discord/Telegram channels were removed 2026-08-24. B-63 and B-58 give pull versions. Also approve or reject agent actions from Telegram buttons (AIHawk `docs/guide.md:446` "Approvals arrive with Approve and Reject"); our approvals are website cards only.
C-9. They: signed webhooks, Slack app, n8n templates (LoopCV, JobsPipe). Why not: delivery was removed on purpose; pull not push.
C-10. They: live progress streamed from a running bot (get_jobs). Why not: we run no bot.
C-11. They: cloud auto-apply that submits for you, bulk apply, creates ATS accounts, stores site passwords, reads email codes (Tsenta, Wobo, Massive, LazyApply, ApplyPilot, JobCopilot). Why not: our hard lines (never type or store a password); submit happens in your assistant under your settings.
C-12. They: solve CAPTCHAs (ApplyPilot, CapSolver). Why not: hard rule, trust page.
C-13. They: browser extension for page capture and form fill (Huntr, Teal, Careerflow, Simplify, Tsenta, FoundRole, Rezi, JobCopilot, freehire, LazyApply, Applendium). Why not: `docs/product/VISION.md:57` lists "Chrome extensions, iOS/Android" in the not-building column; rule 5 (your assistant has its own browser). (Critic: re-read and confirmed.)
C-14. They: a people database: recruiter and hiring-manager emails for credits, insider connections, referral marketplace, mentor booking, introductions, Refer & Earn (JobCopilot, Jobright, LoopCV, Simplify, freehire, Jack & Jill). Why not: decision 9 (we store contacts; the assistant finds them).
C-15. They: live interview copilot, screen help, HireVue assistant, stealth mode (Final Round). Why not: undisclosed live help is against our open stance (trust page).
C-16. They: voice dictation and scored speaking (pace, filler words) (freehire, Rezi). Why not: rule 5, use your assistant's voice mode.
C-17. They: tracer links on your CV, view counters, public CV page, personal website, shared job lists, a live public numbers page (freehire, LazyApply, Kickresume, Reactive Resume). Why not: third-party tracking and public pages change our privacy promise. Owner decides.
C-18. They: Kanban board with drag and drop (Huntr, LoopCV, jobwyre, Reactive Resume, freehire, jobctl). Why not: the Kanban route was removed on purpose (`repositories/database.py:404`). The most common competitor feature. You can overturn.
C-19. They: prices, credits, plans, vouchers, referral and affiliate rewards, white-label, coach and organisation plans, sharing with a coach, multi-client dashboards (Tsenta, 6figr, JobCopilot, LazyApply, Rezi, Kickresume, Simplify, LoopCV, AIApply, Careerflow, Final Round, Scale.jobs, jobwyre, Jack & Jill). Why not: pricing decision: free until value is proven (`VISION.md:61`); single-user product.
C-20. They: convert or annualise pay (hourly times 2,080 hours), hard-coded standard answers like "18+, no felony" (JobSpy, ApplyPilot, JobCtrl, Final Round). Why not: we show pay exactly as the ad says (`job_checks.py:214,303`) and never invent a fact.
C-21. They: cross-user data: "applied and never answered" reports, per-company reply rates, global reply benchmarks, question banks from other candidates, interview process reports, community reviews (freehire, Jobright). Why not: our records are private per person; would need consent-first pooling.
C-22. They: export your tracker to Notion or Obsidian (career-ops, ai-job-search). Why not: rule 5; your assistant can do it from `export_history`.
C-23. They: employer tools and talent search (Himalayas). Why not: seeker-only today.
C-24. They: skill adjacency ("a neighbour skill counts") (freehire). Why not: removed on purpose, decision 28 note (`models.py:353`).
C-25. They: human services: expert resume review, proofreading, 1:1 coaching, humans submit your applications, perk deals (Rezi, Kickresume, Jobright, Scale.jobs). Why not: Job360 is software that stores; not a service business.
C-26. They: native iPhone and Android apps (Tsenta, Huntr, Simplify, Kickresume, Scale.jobs, Rezi). Why not: `docs/product/VISION.md:57` ("Chrome extensions, iOS/Android" not built) and `:54` ("mobile apps (later, on evidence)"); tied to push (C-8). B-106 gives an installable web app.
C-27. They: import your Indeed profile (Indeed MCP [WEB READ]). Why not: rule 5; your assistant can read it with Indeed's own connector.
C-28. They: per-CV contact masking, blurred photo (freehire). Why not: CV rule is no photo; outside the memory mission.
C-29. They: set a score line for you from past outcomes (ai-job-search). Why not: setup rule: never suggest a number. Your score line stays yours.
C-30. They: recently funded companies list; student filters (GPA, class standing); student job portal (career-ops, apply-potato, Careerflow). Why not: rule 4 (sourcing and filtering jobs).
C-31. They: block certain tools when launching the agent (ApplyPilot). Why not: we do not launch agents.
C-32. They: public hired-story wall, community (career-ops, Huntr, Final Round). Why not: outside the mission; privacy.
C-33. They: send applications at peak hours (LazyApply). Why not: nothing runs in the background.

---

## D. Where round 1 was wrong (it called these gaps; we have them)

D-1. Round 1 said no ATS sender-domain list for Gmail search. We have it: `email_senders` (`mcp_server.py:1014`; `daily.md:24-25`).
D-2. Round 1 listed "ad completeness / provenance" as a gap. We already say full ad, partial or title only (`job_checks.py:368-374`).
D-3. Round 1 listed "Connected apps list and disconnect" as a gap. We have it (`frontend/src/app/settings/connect/page.tsx:520`; revoke `oauth.py:382-395`).
D-4. A sweep note said our code had no trace of "annualised" pay. Wrong: the word is there, as a deliberate refusal in our code (`job_checks.py:214,303`: "NOTHING is annualised"). It is a deliberate refusal, not a hole.
D-5. A JobCtrl-style "needs_review" status was claimed earlier. It is not in our code. Do not assume we have one.
D-6. Interview calendar export: we have it (feed `calendar.py:108`, single `.ics` `calendar.py:149`; jobctl, jobwyre and Reactive Resume all matched as SAME).
D-7. Reopening a closed application: we have it (`status.py:121-125`, a later progress event reopens).
D-8. Add a note on an application: we have it (`frontend/src/components/applications/NoteForm.tsx:12`).
D-9. Wobo's company-career-page scan: we do scan company boards through the hunt kit (`hunt_kit.py:1`, ATS list in `companies.json`); verdict moved from THEY DO MORE to SAME.
D-10. Final Round's "one job, one Goal": our Application is the same container (SAME).
D-11. Tsenta's "several resume profiles": we keep every CV as a labelled version and count results per label (`mcp_server.py:1498`) (SAME).
D-12. Writing-style sliders (JobCopilot): our standing notes cover the same need in words (`mcp_server.py:551`; `settings.py:778`), so only the voice sample is a gap (B-2).

---

## E. Where we are ahead

1. One gate for "may I press submit" with reasons, a practice first run, and a yes tied to the exact CV text. Others have a review step; none show a reason-coded gate (Simplify "no review step", Jack & Jill "Jack never sends").
2. `check_form`: live form vs your saved answers, with knock-outs, as a tool anyone can call (Trackly and career-ops have it as playbook text only).
3. Receipts with exact frozen copy, proof levels and a one-time screenshot link.
4. Approved-answer memory with company scope and who-said-it (Wobo has the label only; Sonara users complained corrections were forgotten).
5. Lessons only on your yes, removable, with a before/after check marked "not proof of cause".
6. Stats with the sample size on every rate, a score-band check, furthest-stage funnel. Scale.jobs and others list rates without the honesty rules.
7. A blocked form opens a question and stops submit. Scale.jobs says bots silently skip these; ApplyPilot solves CAPTCHAs.
8. Self-serve delete account and download everything (Tsenta: email the founders; jobwyre: email support).
9. No AI of ours, so no profile text goes to a model vendor and nothing trains on you (Tsenta, Massive, Final Round all do).
10. Per-field change history with take-back and "you said" vs "assistant guessed" (Final Round only lets you forget entries).
11. Outreach and auto-email gates with daily and per-company caps (no one else lists caps).
12. Hunt kit you run yourself, with live check, dead-board memory and a "why did the hunt miss this" tool.
13. Stale tool list detection, unknown arguments refused by name, text budget kept under 2,000 characters.
14. Free, no credits, nothing rewards volume.
15. Real-account safeguards: authorship from the token, jobs scoped to the user, pages treated as data, no password ever.

---

## F. Coverage: what was read, what stayed blocked

Counts below are rows checked this round (WE LACK and THEY DO MORE rows first, then some others).

- **career-ops, ai-job-search, job-apply-plugin, jobspy-mcp-server, trackly-cli** (88 rows): quotes searched in the cloned repos at `/var/tmp/refs`; web quotes in saved captures. 66 ok, 6 web read, 10 unverified (file cited, no quote). REA not used.
- **jobctl-claude, JobCtrl, ApplyPilot, jobsync, apply-potato** (77 rows): 70 quotes found. jobctl calendar-export line and the "AI crawlers welcome" line are unverified. Four verdicts changed to "they do more" or "we lack".
- **get_jobs, Auto_job_applier, JobSpy, Reactive Resume, Resume-Matcher, AIHawk, open-resume, Awesome-CV** (about 50 rows): all WE LACK/THEY DO MORE rows checked. Reactive Resume REA ids cited but not found in the saved files (quotes are). Passkeys and 2FA only known as files existing.
- **freehire, JobsPipe, Himalayas MCP, Indeed MCP, Rezi, 6figr JobGPT** (85 rows): freehire read in code, specs and saved web captures. JobsPipe, Himalayas, Indeed, Rezi mainly web read. Dropped: the Rezi job-search quote (wrong wording, claim holds) and one freehire "daily 60" quote (really 40).
- **Tsenta, FoundRole, LoopCV, JobCopilot, Massive, AIApply, Wobo, Jack & Jill, Kickresume, Sonara, LazyApply, Scale.jobs** (62 + 54 rows): REA captures and web reads. Blocked or missing: Tsenta pricing capture is empty; several REA ids exist only in notes, not in a saved capture; Massive app page never captured; Sonara how-it-works and FAQ not captured; Trackly home blocked by REA ("target_not_allowed") so web read was used. 37 of the 91 apply-sites rows were not re-checked and keep round-1 values.
- **Simplify, Careerflow, Huntr, Jobright, Teal** (53 rows): 41 quotes found in saved captures, 12 unverified. Dropped: Teal "AI interview practice" (no support). Not read: Teal networking page, Huntr research reports, Simplify job matches, Careerflow mock-interview and LinkedIn pages.
- **jobwyre, Applendium, JobShinobi, Final Round AI** (68 rows): 52 ok, 4 web read, 12 unverified, 0 contradicted. Applendium home page text, JobShinobi privacy page and the jobwyre 14-day trial were not checked.
- **Our side**: every file:line cited was opened. Every "grep found nothing" was re-run over `backend/src` and `frontend/src`; hits were noise (words inside other words, SQL `translate()`, company names). Rows marked "not re-read this round" rely on the earlier map.
- **Not done**: no new competitors were researched this round. Nothing in the repo was edited; no git commands were run. Competitor repos were read in their cloned copies, not live.
- **Counts of my own**: the 115 borrow items and 33 rule-break items above merge duplicate rows across products. Some items bundle several small ones (B-24, B-41, B-55).


---

## G. Seeker needs nobody covers (first-principles check, added by the critic)

Walked the whole journey: decide to look, profile, find, judge, CV, apply, wait, email, follow up, outreach, interview, offer, negotiate, accept, start, learn. Searched our code and recipes, then the 21 cloned competitor repos under `/var/tmp/refs`.

- Decide to look and get ready: no one in the clones has a "am I ready to search" step; only status (B-7) and career maps (C-5). Not a Job360 need under rule 4.
- Ad closing date: ours is a dead field (A-12); no competitor reminder found (B-114).
- Take-home or online test: ours records `assessment` (platform, subject, score; `rules.md:673`) with no due date or safe-handling note; Reactive Resume has the safe-handling note (B-109); apply-potato files the "OA" stage (`README.md:398`).
- References (people who vouch): ours has no reference list or "reference check" stage; jobsync has the stage name only (B-55). Nobody stores who agreed to be a referee. NOT FOUND in the clones.
- Background check, right-to-work paperwork after an offer: nobody. NOT FOUND (grep 'background check' hit only form-question code).
- Accept, close the rest, resign, notice, start date: only Reactive Resume's accepted stages (B-112) and resignation letters (B-37). Start date, notice-period reminders, a first-90-days plan: NOT FOUND in any clone except as an interview question (Reactive Resume `mock-interview/references/rounds.md:73`). Open need for both us and them.
- Learning after you start: no one links the job you took back to which CV, route and answers got it. We have the receipt and `hired`; a "what worked" note on `hired` is a recipe line.
- Scams and fake offers: only Reactive Resume (B-109).

---

## H. Critic additions: what I changed in this file

- Added B-109 to B-115 (scam stops, offer-deadline messages, current-salary script, close-the-rest on hired, route-of-application counts, ad closing date, evidence map).
- Added partly-true items 12 and 13 in section A (dead closing-date field; nothing closes other applications on `hired`).
- Extended existing items with sweep rows that were missing: B-24 (citizens-only and banned-wording table), B-55 (reference check, accepted stages), B-60 (goal tracker and journal), B-67 (Promotions-tab, headers-first linking, filing), B-74 (watch toggle), B-102 (bug bounty), C-3 (ghost-job score), C-8 (Telegram approvals).
- Fixed C-13 and C-26: VISION line now read (`VISION.md:57`, `:54`). Added VISION warnings to B-60 and B-90 (`VISION.md:56` "Charts-heavy analytics" is not built).
- Checked section B "we lack" claims against our code: avoid_companies unread (confirmed), no tool hints, no VALARM, no CSV export, no search box, no manifest, no 2FA, no calendar recipe (all confirmed). No false "we lack" found.
- Coverage holes I could not close: for Huntr, Teal, Careerflow and Jobright the sweep has no rows for offers and salary (A11) and little for profile (A1) or email (A8); no saved capture covers them. They are unverified, not proven absent. Apply-sites sweeps A and B carry no per-product headings, so per-area coverage by product could not be tabulated. The 108 count in F is now 115.
