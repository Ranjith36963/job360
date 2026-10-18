<!-- doc: FROZEN | written 2026-10-18 by a cloud session: 18-agent workflow (Sonnet research, Haiku quote checks), REA 6.5.0 captures + code reads -->

## Lead check against Job360 code (2026-10-18, grep on this branch)

- CONFIRMED gap: no mail privacy filter (no OTP / password-reset / redact rule in `backend/src/recipes/*.md`).
- CONFIRMED gap: `recipes/apply.md` has no Gmail check for an earlier application before applying.
- CONFIRMED gap: no offer/negotiation recipe; offers are only the `offer` event amount/currency/period (`recipes/debrief.md:58-59`).
- CONFIRMED gap: no priority/excitement on applications (none in `services/applications/*.py`).
- CONFIRMED gap: no MCP tool annotations (readOnlyHint/destructiveHint) in `api/mcp_server.py`.
- PARTLY HAVE: `recipes/daily.md:24` already searches `-in:sent -in:drafts` + `email_senders` domains; missing only the Promotions scan and a general ATS sender list.
- DIFFERS: `recipes/daily.md:30` records a rejection/offer "at once"; career-ops/apply-potato show the rejection first and never infer an offer from mail alone.

# Job360 gap analysis vs 45 reference products (2026-10-18)

Method: Job360 feature inventory (15 areas) vs verified competitor feature data. I read docs/product/VISION.md and .claude/skills/session-lessons/SKILL.md first. I spot-checked a few "ALREADY HAVE" claims in /home/user/job360/backend/src (cited file:line). Competitor evidence is as supplied by the research passes; I did not re-open any competitor source. Evidence tags: code read / REA / web read / BLOCKED. Check status: ok (quote verified by a QC pass), unchecked, unverifiable, web read.

Honest caveat: only ~40% of competitor features were quote-checked. REA was claimed only where an actual REA capture exists (Tsenta, LoopCV, JobCopilot, AIApply, Huntr, Simplify, Careerflow, Jobright, Himalayas, JobsPipe, freehire pricing, 6figr UI, jobwyre, Wobo connect page, Kickresume, Scale.jobs, Reactive Resume home). Everything labelled "web read" is web research, not REA.

Rules used for verdicts: rule 4 (never source/rank/recommend), rule 5 (store tool not do tool), decisions 8/19 (no Gmail OAuth/polling), 9 (no people DB), 11/23 (no push/WhatsApp), 13 (no charts-heavy analytics), 26 (no Keep button), 27 (no country/visa rules), 28 (no own LLM), hard-rules #28 (no hardcoded skill lists).

## 1. Feature-area matrix

### A1 Profile and memory
Job360 has: deterministic CV/LinkedIn/GitHub parse (cv_parser.py:49, linkedin_parser.py:554), update_profile with version history, answer bank with topics/aliases/states/consent (answer_match.py:8, user_info.py:632), settings + 6-round setup interview, visa/work-auth list.
Competitors doing something extra:
- Experience bank with per-claim provenance derived from the entry point, unknown fails closed to "agent_inferred", CV gate refuses it (freehire experience/AGENTS.md:13,26-27; code read; ok). Also answer bank where only candidate-provenance is "sendable" (freehire answerbank/AGENTS.md; unchecked). We have confirmed/relayed/inferred/sensitive states (borrowed from neonwatty) but no per-bullet claim atoms.
- Knowledge correction flags dependent stories; Forget leaves a suppression marker so the fact is not re-created (Reactive Resume tool-meta.ts:38; ok). We have "lessons removable by correction" (lessons.py:39) but not for profile facts (unchecked whether update_profile has a suppression marker).
- Memory write policy Ask first / Automatically / Never (Reactive Resume docs; ok). We have per-answer consent and remember-OK for sensitive only.
- Edit-learning: every user edit to an AI answer trains later answers (JobCopilot; web read).
- Skills boundary + resume_facts allow-list (ApplyPilot profile.example.json:42; unchecked).
- Github evidence check of CV claims via PRs authored (career-ops github-evidence.mjs:1-14; ok). We have github_enricher.py (repo/dependency) but no claims-vs-GitHub cross check (unchecked).
- Multiple resume profiles per plan (LazyApply; web read). VISION: multiple named profiles = later.
- Intake fingerprinting so reruns show only new documents (career-ops intake.mjs; unchecked).
- Optimistic concurrency `expectedRevision` on profile update; value states answered/intentionally_blank/declined/unknown (trackly-cli; unchecked).
- Calibrate profile from past outcomes (ai-job-search /setup, setup.md:129,172; unchecked). We return lessons via get_profile (PR #868).

### A2 Job intake
Job360 has: bring_job by link or pasted text, SSRF guard, JSON-LD/meta/body ladder (routes/bring.py:300,586; services/fetch/guard.py, extract.py), repost + similar-ad (reposts.py:3).
Extra elsewhere:
- Verbatim JD archive required in every report (career-ops AGENTS.md ~520; unchecked). We store the ad snapshot (VISION object model).
- Posting provenance: method, requestedUrl, resolvedUrl, retrievedAt, completeness, fallbackReason (Reactive Resume schema/applications/data.ts:12; unchecked); descriptionCompleteness title-only/partial/full so a score is flagged provisional (jobsync schema.prisma:481; unchecked; matchDirective.ts:20 partial-description warning).
- find_job before add_job with a transparency report of matched vs created entities (jobsync toolDescriptions.ts:6; unchecked).
- One-line natural-language add with duplicate warnings (jobctl-claude server.mjs:97; unchecked).
- CSV import/export of applications (Reactive Resume mcp-tool-names.ts:39; Simplify spreadsheet import; unchecked). We have export_history only.
- Forward-an-email ingestion address (JobShinobi; web read). Browser clippers: Huntr (REA, ok), Teal, Careerflow, Simplify, Tsenta, FoundRole.
- Save full JD text at clip time so prep survives takedown (Huntr; ok).
- Company data auto-populated on save (Huntr; unverifiable).

### A3 Finding jobs and alerts
Job360 has: hunt recipe + stdlib hunt_kit (the user's AI runs it), ATS adapters, liveness classifier, dead-board memory, --why-missed, posting_signs ghost signals; no server-side alerts.
Extra elsewhere (most are sourcing = rule 4):
- Ghost scoring with published methodology and bands; repost = 3+ sightings with 10+ day gap; first-seen stamp never moves (JobsPipe /ghost-jobs, web read). freehire two-evidence-tier ghost verdict, outcome evidence needs 2+ people (freehire ghost/classify.go; unchecked). FoundRole eight checks (web read). We already have posting_signs (posting_history.py, PR #875) with 5 facts.
- Idempotent "never alert twice" and company-mode alerts that fire once per company (JobsPipe Signals, FoundRole alerts; web read). Needs a server job = conflicts.
- Discard log analytics: why filters threw jobs away (career-ops discard-analytics.mjs; unchecked). Per-board error/warning reporting so agent can tell blocked from empty (JobSpy attrs errors; ok). hunt_kit has a run log (hunt_kit.py:1155) so partly ALREADY HAVE.
- Listing fingerprint = ATS-native triple (career-ops listing-fingerprint.mjs; unchecked). We have SimHash similar_ad.
- Auto-grown blacklist from rejection wording (get_jobs Boss.java:184; unchecked). Company blacklist preference (Wobo; web read).
- Salary_source provenance direct vs description (JobSpy model.py:209; unchecked) and pay_facts exists (job_checks.py:298).
- Pacing sized from an observed anti-bot ban (get_jobs Boss.java:1785; ok).

### A4 Fit judgement
Job360 has: save_fit (agent verdict stored, null never 0, hard/soft gaps, jd_requirements, line.side/switch), alignment, check_form knock-outs, pay_facts, visa list comparison.
Extra elsewhere:
- Fit as an evidence table per requirement (Supported/Partly/No evidence), no percentage (Reactive Resume checking-job-fit.mdx:36; ok) and requirement Importance with evidence tiers where inferred importance can never create an obligation (career-ops oferta.md:181-212; ok). We have jd_requirements and gaps; tiers unchecked.
- Eligibility gate before scoring, "silence is not permission", company-wide welcome is not role-level permission (ai-job-search 04-job-evaluation.md:9-31; unchecked). Our visa.py stays silent on unknown by decision 27, so this is a recipe-text point only.
- Scoring denominator drops unverifiable categories rather than scoring zero (freehire cvmatch/AGENTS.md; unchecked). Agent computes scores for us; only relevant to how save_fit documents score_source.
- Hard-constraint blockers with score ceilings (freehire hardconstraint.go:1-19; unchecked): CONFLICTS (decision 27 for work-auth part; a cap on the agent's score is ranking logic).
- Posting legitimacy tier kept OUT of the numeric score (career-ops Block G, _shared.md:126-141; ok). posting_signs is separate already: ALREADY HAVE.
- Pay vs filed wages (FoundRole DOL; web read) and H-1B sponsor lookup (career-ops plugin; ok) and visa history by year: CONFLICTS (decision 27, external data of ours).
- Score correction as a calibration anchor, separate from re-score (JobCtrl scoring-and-employer-analysis.md:101-111; unchecked).
- Apply Gate: apply / fix first / skip verdict from posting + resume + own outcomes, abstain when data thin (Applendium; web read). Agent can do from stats + get_profile lessons.
- Skip-blockers: which critical requirements recur across skipped roles (career-ops skip-blockers.mjs:18-19; ok). We have skill_gaps in stats (feedback_numbers.py).

### A5 Documents
Job360 has: versioned artifacts, save-time truth checks (cv_checks.py:215-560), PDF/DOCX render, CV diff, stock-phrase warn, cv_unsupported_claims.
Extra elsewhere:
- Numeric-claim guard in code (Resume-Matcher resume_preservation.py:167 _novel_numbers; ok): ALREADY HAVE (cv_checks flags unsupported numbers/terms).
- Round-trip ATS parse via pdfminer, reading-order fidelity >= 0.9 (Resume-Matcher verify_resume.py:24; ok for constant). We have "PDF problems" check; fidelity metric unchecked.
- Independent reviewer pass / adversarial HM audit (career-ops pdf.md:4-5 ok; ai-job-search apply.md; JobCtrl): all are LLM work = agent recipe. We have no review recipe (unchecked: grep not done).
- jd-similarity decision reuse | reuse-with-edits | regenerate (career-ops pdf.md ~22; unchecked): FITS AS RECIPE.
- Tailoring aggression levels Conservative/Balanced/Turbo and a change report with reasoning and "what you're genuinely missing" (Kickresume; REA ok): FITS AS RECIPE.
- Keyword coverage diagnostic of the tailored CV (career-ops keyword-match.mjs:1-12 ok; Rezi web read). alignment.py exists for skills in ad.
- Drafter fresh-conversation retries listing earlier issues (ApplyPilot tailor.py:355; ok): agent-side.
- Atomic validate+save+verify in one call with partial-failure semantics (Resume-Matcher tailor_and_verify.py:30; ok). save_artifact runs checks at save time (mcp_server.py:1051) = ALREADY HAVE in spirit.
- get_resume_format: tool tells agent the live schema before each write (Rezi README; code read, unchecked). Our update_profile "field shapes" live in get_recipe("rules"). FITS (small).
- Voice sample / anti-slop lint with value-free violations (trackly application-writing.md; ok). stock_phrases ALREADY HAVE.
- Tracer links on CV links to learn the PDF was opened (freehire docs/features.md:102-109; unchecked) and resume-view tracker (LazyApply; web read): CONFLICTS (server-side tracking of third parties, trust page; also needs hosted redirect). Owner decides.
- LaTeX source and compile check, version revert (JobShinobi, web read); we have versions forever + PDF/DOCX.
- Per-application bundles + sha256-verified archive of submitted CV (career-ops outcome.md:61-66; ok): ALREADY HAVE via receipt freezing CV/letter versions (routes/receipts.py:167).
- Resignation letter (Rezi, Kickresume, LazyApply; web read): FITS AS RECIPE, trivial, out of core.

### A6 Applying
Job360 has: get_application_kit, check_form, check_submit gate, practice run, receipts, proof, blocked record, forms.md playbooks.
Extra elsewhere:
- Access-probe states + frozen-batch recovery after context loss (trackly access-probe.md:6-26 ok; apply-tools). Recovery of "what was I doing" = ready.py/needs-you? unchecked. FITS AS RECIPE.
- Three independent state lines (employer / tracker / browser) in the hand-off (trackly review-handoff.md:23-29; ok). Recipe wording.
- Permanent vs retryable failure classification so dead ends are never retried (ApplyPilot launcher.py:522; ok). We have blocked event; classification unchecked.
- Pre-form location eligibility check before typing (ApplyPilot prompt.py:117; unchecked): covered by check_form knock-outs.
- Dry-run counts as skip not failure (GodsScion StoppedBeforeSubmit; ok). We have practice run.
- At-most-once submit: durable intent persisted before send, crash => needs_verification, never retry (JobCtrl at-most-once guide:55; ok; freehire dead-letter). We have duplicate flag + record_application; durable pre-submit intent event unchecked. FITS (store tool: an intent event).
- Repeat-application override states (Blocked / Confirmation required / Override ready / consumed) (JobCtrl apply.md:189-215; unchecked). We have duplicate flag + clear web-only (test_duplicate_clear_is_web_only).
- Approval bound to profile version, materials, URL, 15-minute freshness (JobCtrl apply.md:87-109; ok). Our check_submit uses host mismatch, form swept, unsupported claims; staleness binding unchecked. FITS.
- Bounded campaign policy: 10 apps/4 h, 5-minute lease bound to SHA-256 fingerprints, kill switch (job-apply-plugin job_apply_policy.py:29-31; ok). We have daily_cap + pause. Fingerprint-bound lease: FITS (check_submit token).
- Cancel a queued job before send; see how each screening question was answered (Wobo; web read): we have Needs-you decline and receipt.
- Optimal send timing (Wobo; web read): recipe, low value.
- CAPTCHA solving (ApplyPilot, JobCtrl once): CONFLICTS with hard line / ToS; trust page. Do not copy.
- Applications as "Mark as applied snapshot in one write" (Reactive Resume; ok): ALREADY HAVE (receipt).
- Stealth, auto-bulk 1000/day, human VAs (LazyApply, Scale.jobs): CONFLICTS (rule/pricing) or out of scope.
- Autofill extension (Simplify, Huntr, Teal, JobCopilot, Careerflow, Rezi, Jobright, Tsenta): CONFLICTS (VISION table: Chrome extensions never).
- Captured ATS application forms stored in platform vocabulary so questions map before applying (freehire applyform; code read ok on README:157): CONFLICTS (it is crawling/sourcing).

### A7 Tracker
Job360 has: spine with append-only events, replayed furthest-stage status (status.py:102), next_step state machine (next_step.py:34), follow_up_on, auto-ghost 45 d only after inbox check (ghosting.py:33), calendar feed + .ics (routes/calendar.py:64-149), Needs-you. No kanban by design.
Extra elsewhere:
- Event source trust: only mail/calendar sources count in timing math (freehire appevent.go:71-72; ok). Our events record who wrote each (VISION); whether feedback_numbers excludes self-recorded dates is unchecked. FITS (small).
- Priority A/B/C, excitement rating, waiting_referral parking status, get_due/get_planned agenda (jobctl-claude server.mjs:20,197,218; Huntr, Teal, Himalayas excitement; Careerflow): I grepped applications/*.py and core/settings.py for excitement|priority|waiting_referral and found none. Mechanisms: user priority / excitement as stored fields: FITS (store tool, user's own words) but could drift to ranking; the owner decides. waiting_referral: FITS AS RECIPE via note event.
- Closed reasons (no-response, accepted-other, withdrew) (Reactive Resume data.ts:5; ok; FoundRole Ghosted/Withdrawn). We record reasons on rejection; accepted-other / withdrew reasons unchecked.
- Stage sub-statuses Phone Screen / Technical / Onsite / Final; Negotiating (FoundRole web read; Himalayas REA negotiation stage). We have interview_session round (interview_payloads.py). Negotiating stage: unchecked.
- Daily digest of what is due (JobCtrl digest; Resend...). ALREADY HAVE via whats_new (mcp_server.py:1343) and daily.md.
- Per-job saved views (Huntr, JobCtrl, jobsync): web UI, low value.
- Actor tag agent/human/system on events (jobctl-claude README; unchecked): ALREADY HAVE ("every event says who wrote it", authorship.py).
- Tracker with notes, tasks, daily goals, timed activities (jobsync; unchecked): CONFLICTS in spirit (no gamification/coaching, A15).
- Notion/Sheets mirror (ai-job-search /notion-sync, apply-potato Sheets; unchecked): FITS AS RECIPE (agent exports with export_history).

### A8 Email and calendar
Job360 has: agent-run Gmail check with per-application cursor, sender domains, whole-email read, idempotent source (daily.md:23-40; inbox.py; PR #853), automatic employer emails gate (auto_emails.py:124), mark_inbox_checked, ics feed.
Extra elsewhere:
- Keyword/ordering rules: Noise first, Rejected decided before Offer ("unable to offer"), confidence high/medium/low, ambiguous-match, exact role alone is enough (career-ops reply-matcher.mjs:515-540, 687-693; ok). Tense rule: receipt vs invitation judged by tense, not sender (apply-potato email_categories.txt; unchecked). Rejection never auto-advances by itself (freehire classification.go:109; ok). FITS AS RECIPE (add to daily.md; we classify nothing).
- Offer/hired/declined never inferred from email without the user (ai-job-search gmail-sync.md:74-75; ok). Do we say this? daily.md grep did not show it; unchecked. FITS AS RECIPE.
- Ack email as proof and open set includes drafted rows (ai-job-search; ok): ALREADY HAVE (daily.md:34,44 confirmation = proof).
- Match: "return null unless exactly one candidate" two-stage matcher; deterministic auto-link only, LLM only suggests (apply-potato job_row_match.txt:34 ok; freehire maillink/decide.go:17-18 ok). ALREADY HAVE in daily.md ("matches to one application; unmatched becomes one ask").
- Needs-review reasons: ambiguous / untracked / no_company / all_retired, log not retry queue (apply-potato needs_review.py:9; ok). We keep one ask for unmatched; reason code is unchecked. FITS (ask carries reason).
- Staleness: oldest-first processing, only mail newer than row's last email may change it, --reprocess bypass (apply-potato check_gmail.py:587,1072; unchecked). Ours: furthest-stage wins (status.py:117-166) and cursor. ALREADY HAVE mostly; oldest-first order is in daily.md:23 ("OLDEST open application").
- Privacy filter before a model sees mail: OTP, SSN, card, bank, password reset by subject only (apply-potato email_filters.py:29; ok). FITS AS RECIPE: add skip/redact rules to daily.md. Valuable because the agent reads whole emails.
- Promotions tab triage notify-only (apply-potato promotions_scan.py:17; unchecked): FITS AS RECIPE.
- Query hints: ATS sender domains list (greenhouse.io lever.co myworkday.com ashbyhq.com smartrecruiters.com icims.com bamboohr.com), -in:sent -in:drafts (ai-job-search gmail-sync.md:49-51; ok). My grep of daily.md found no ATS-domain list. FITS AS RECIPE.
- Headers-first linking, body read after link accepted; verbatim span citation; prompt/model version persisted (JobCtrl outcomes-and-feedback.md:11-35; ok). Verbatim span: we store verbatim feedback; model-version fields unchecked.
- Inbox receipt preflight against duplicate applications, batch-scoped consent, requisition ID never sufficient alone (trackly inbox-receipt-preflight.md; ok). FITS AS RECIPE (apply recipe step 0).
- Shadow second classifier measuring disagreement (apply-potato jev_classifier; unchecked): CONFLICTS (we classify nothing).
- Interview records from confirmation emails with SCHEDULED/RESCHEDULED/CANCELLED/COMPLETED (6figr applications.ts:136-144; ok); calendar read to create interview_scheduled (freehire calsync/calendarapi.go:14; ok). Our interview datetime is recorded via record_event (VISION 6). Whether a rescheduled/cancelled event type exists: grep of settings.py for rescheduled returned nothing. FITS (event types) and recipe for calendar connector read.
- Calendar one-way ICS feed: ALREADY HAVE (jobwyre REA; ours routes/calendar.py).
- Pasted-message "Read it for me" with index-selectable proposed changes, Undo, stale proposals rejected (Reactive Resume replying-to-recruiters.mdx:8; ok). Applies to users with no Gmail connector. Our ask_user/answer_ask exists; paste path unchecked. FITS AS RECIPE.
- Drop-directory proposals contract with sha256 mailbox id (career-ops REPLY_PROPOSALS.md; ok): CONFLICTS-ish/not needed; our record_event is the single door with idempotent source.
- Gmail OAuth ourselves (JobCtrl, freehire, apply-potato, JobShinobi forwarding address, Applendium): CONFLICTS decisions 8/19.
- Multiple Gmail accounts (apply-potato; unchecked): recipe.

### A9 Outreach and networking
Job360 has: add_contact (found_via), update_contact, list_people, outreach artifact, check_outreach gate (caps 10/day, 2 per 14 days), reach.md.
Extra elsewhere:
- Warm lead first: look in saved contacts for the company before any search; 3-search ceiling; never guess a name (career-ops contacto.md:23-35,54-61; ok). FITS AS RECIPE (reach.md; list_people exists). Unchecked whether reach.md already has warm-first.
- Contact types with persona (Recruiter, HM, Peer, Interviewer, Internal Referral) (career-ops contacto.md:63-74; ok). found_via exists; contact type/role field unchecked.
- Save recruiter/interviewer from a rejection or invite so identity is not lost (career-ops contact-extract.mjs:1-12; ok). FITS AS RECIPE in feedback/daily (add_contact from email).
- LinkedIn Connections.csv join with applied/target companies for warm intros (career-ops linkedin-join.mjs:1-12; ok). User uploads export, agent joins: FITS AS RECIPE; a store tool for imported connections would be a people DB = CONFLICTS decision 9 if we hold the full list.
- Per-fact provenance on contacts (source kind, ref, confidence, confirmation) (JobCtrl contacts-and-outreach.md:18; ok). found_via is coarse. FITS.
- Draft needs claim verification before copy (JobCtrl; ok): ALREADY HAVE (cv_checks style rules apply to outreach? unchecked); rules.md "no new claims".
- Follow-up note 60-120 words, only claims from the archived submission, max two, then offer to close (ai-job-search outcome.md:98-111; ok). We have auto-email caps and voice (rules.md:314-352): ALREADY HAVE largely.
- Recruiter email extracted from JD (JobSpy util.py:121; ok): FITS AS RECIPE (agent) and bring_job could store recruiter_email from the ad; unchecked.
- Recruiter/HM finder with credits (JobCopilot, 6figr, Simplify, LoopCV email finder): CONFLICTS decision 9.
- Double-opt-in introductions agent-to-agent (Jack & Jill; web read): CONFLICTS (recruiter side later, consent-first).
- Employee referral marketplace (freehire, 6figr Refer & Earn, Simplify Talent Scout): CONFLICTS (people network).
- Send_outreach direct from user's email (6figr outreach.ts:89; ok): CONFLICTS (we gate, user presses Send).
- Networking CRM with reminders and CSV/LinkedIn import (Careerflow, Teal, Huntr contact tracker REA ok): partially HAVE (contacts + follow_up_on). Per-contact reminder is unchecked.
- LinkedIn people-search URL generation per job (ai-job-search scrape SKILL.md:185-207; ok): FITS AS RECIPE.

### A10 Interview prep
Job360 has: prep.md, debrief.md, interview_session records (round, competency tags, signals with verbatim quote, process_friction, assessment), counts only.
Extra elsewhere:
- Retracted-claims hard gate and story-provenance check during practice (career-ops practice.md:17 ok): FITS (a retracted-claims store, by lessons mechanism).
- Consistency brief: no claim in the room that is not on the paper; use the exact submitted CV (ai-job-search interview.md:26,70; ok): FITS AS RECIPE (get_application kit gives the submitted version). Check prep.md; unchecked.
- Questions ranked by earlier-stage feedback first (ai-job-search; unchecked): recipe.
- Question library bound to accepted profile evidence, none = gaps not made-up outline (JobCtrl materials-and-tailoring.md:129,182; ok): recipe tip. A 121-question library = a content product: CONFLICTS (we do not hold content? decide).
- Coffee-chat cross-reference (corroborates/contradicts) (career-ops interview-prep.md:17; ok), prep intel on interviewers: recipe.
- Weekly interview digest of recurring competency gaps (career-ops weekly-digest.mjs:3; ok): we have stats(view=friction/assessments/skill_gaps) and review.md: ALREADY HAVE.
- Misfit detection from what the candidate says fluently (career-ops patterns.md:7; ok): FITS AS RECIPE over export_history + interview_session signals.
- Mock interview / live copilot / voice scoring (Reactive Resume Practise, Rezi, Teal, Careerflow, Final Round, AIApply Interview Buddy, Jack & Jill, Simplify): rule 5 says the agent can roleplay (FITS AS RECIPE for mock, with transcript saved via debrief). Live undisclosed copilot / stealth mode (Final Round) = CONFLICTS (trust line).
- Re-drill weak spots from last debrief; Debrief rated Went well/Mixed/Didn't land (Final Round, Reactive Resume; web read/ok). Debrief assessment exists; re-drill is a recipe line.
- Interviews page with prep-question "asked" flag per round (jobsync schema.prisma:423; unchecked): FITS (small) via interview_session.
- Company-reported interview question bank (Jobright; REA unverifiable): content product, CONFLICTS.

### A11 Offer and salary
Job360 has: offer event amount/currency/period, salary_by_country, pay_facts. No comparison, no negotiation, no benchmarks. grep negotiat/benchmark in product code found nothing relevant.
Extra elsewhere (this is the largest uncovered area for the owner):
- Offer-prep contract companion: clause walk, never says "safe to sign", lawyer question list, draft negotiation email (career-ops offer-prep.md:23-35; ok): FITS AS RECIPE (offer.md recipe).
- Salary observation ledger with trust tiers contract > offer-letter > recruiter-verbal > user, and "stated" figures committed to an interviewer so later rounds do not contradict (career-ops salary-gap.mjs:1-24; ok): FITS (store tool: salary observation event with source tier; `stated` note). Strong match to rule 5.
- Negotiation talking points only from figures verbatim in CV (career-ops negotiation-roi.mjs:14; ok): recipe + existing cv_checks.
- Offers side-by-side, Unknown is not zero, no totals across mismatched currency/period (Reactive Resume comparing-job-offers.mdx:35; ok): FITS (get/compare view in stats or recipe; store has amount/currency/period so a read-only comparison is a store-side count, not advice).
- Negotiating stage and sub-status Offer Received / Negotiating / Accepted (FoundRole job-tracker; web read; Himalayas REA ok): FITS (event types).
- Salary benchmarks (Himalayas get_salary_data REA ok; Jack & Jill P50; JobCopilot; Indeed get_company_data; freehire salary_stats; JobCtrl ECB/Eurostat): CONFLICTS (external data of ours; user's AI can read Indeed/Himalayas MCP itself). Recipe: tell the agent to pull from the user's connected sources and save as a note.
- Salary lookup from user-provided dataset (ai-job-search salary_lookup.py:5; ok): FITS AS RECIPE only.
- Resignation letter generator (Rezi/Kickresume): recipe.

### A12 Feedback loop and analytics (owner priority)
Job360 has: outcome capture with verbatim feedback/reasons/stage, post-mortem, lessons returned by get_profile (lessons.py:39), funnel with ever-reached (feedback_numbers.py:434), velocity median/p75, calibration by fit band (feedback_numbers.py:330), lesson_effect, digest, patterns, skill_gaps, friction, small-sample flags, export_history, review.md weekly.
Extra elsewhere:
- Score-band calibration verdict flat/inverted/insufficient: ALREADY HAVE (feedback_numbers.py:306-330 cites calibrate.mjs).
- Funnel ever-reached from history: ALREADY HAVE (feedback_numbers.py:434; status.py:102).
- Channel yield not discrimination, ATS vendor advance rate with coverage % (career-ops patterns.md:57-80, analyze-patterns.mjs:257-359; ok). We count by found_on/channel; vendor (ATS) count unchecked. FITS (add ATS vendor dimension if job apply_url host known; computed on stored data, no ranking).
- Benchmarks with year + directional caveat; right-censored medians; no multiplier claims below n=20 (career-ops funnel-velocity.mjs:22-28; benchmarks.yml:19; ok). We have small-sample flags; right-censoring unchecked. Benchmarks = external data: CONFLICTS-lite.
- Reply-rate personal vs everyone-else with sample gate 10 and self-exclusion (freehire replyrate.go:24,37; ok): cross-user stats; privacy; CONFLICTS (needs consent-first aggregate; owner decides).
- Recommendation gate before any learning: >=3 compatible signals across >=2 jobs, no contradiction, user acceptance creates policy revision; "descriptive not causal" (JobCtrl outcomes-and-feedback.md:36-54; ok). Ours: lessons recorded only after owner yes (PR #868). The 3-signals/2-jobs threshold is a FITS tweak.
- Outcome archive with sha256-verified cleanup (career-ops outcome.md:61-72; ok): ALREADY HAVE (receipt).
- Resume-version win rate from inbox replies, CV A/B tied to opens (Applendium web read; LoopCV REA ok; Huntr Tailored vs base needs n>=10 ok; Simplify; Careerflow). We have counts by CV label (stats). Tailored-vs-base split with n floor: unchecked. FITS (count dimension).
- Strategy alerts with a stated reason per next action (Applendium; web read): FITS AS RECIPE (review.md).
- Season funnel date-driven (apply-potato daily_summary.py:27; unchecked): HAVE.
- Skip-blockers / upskill weighting reports by (5 - score) (career-ops upskill.mjs; ok): we have skill_gaps; weighting unchecked.
- One-page activity report PDF for coach/unemployment work-search (Applendium CWS; web read): FITS (web render of stored data; read-only export). Strong user value in UK (Universal Credit). Unverified need.
- Discard analytics, AI usage logging, funnel KPIs per board: not needed.
- Weekly "Interview digest": HAVE.
- Hired Wall story share (career-ops hired-share.mjs; unchecked): CONFLICTS (community, A15).
- Learning agent claims (Simplify, LoopCV, AIApply): marketing, mechanism undisclosed; do not copy.
- Feedback request email after rejection: ALREADY HAVE (auto_emails.py:124).
- Rejection-latency check: post-interview silence beyond courtesy threshold and emitted blacklist suggestion (career-ops rejection-latency.mjs, followup.md:48-66; ok). We have ghosting + silence follow-up (debrief.md). Employer response-latency memory per company (company cards in stats) partially HAVE.

### A13 Agent / MCP / CLI
Job360 has: 32-tool MCP, OAuth 2.1 DCR+PKCE, j360_ tokens, recipes, doctor, TOOLS_REV, report_missing_tool, loud unknown-arg refusal.
Extra elsewhere:
- OAuth scope chosen at consent read-only vs read+write, Connected-apps list with one-click Disconnect (jobwyre REA; unverifiable): FITS. Unchecked what routes/oauth.py exposes. The scope separation is a clear safety win.
- MCP tool annotations readOnlyHint/destructiveHint (jobctl-claude server.mjs:248, Reactive Resume tool-meta.ts:883; unchecked). grep not run for annotations in our mcp_server.py. FITS.
- Per-token rate limit 100/hour and createdVia provenance (jobsync constants.ts:151; unchecked): provenance HAVE (made_by); rate limit unchecked.
- Named expiring tokens (jobsync README:156-158), 30-day non-refreshable tokens (Rezi): unchecked on our side (api_tokens.py).
- Public/anonymous tools tier vs personal tools (Himalayas REA, freehire, FoundRole OJCP): CONFLICTS (we serve no public data).
- Claude Code plugin packaging with skills (Rezi, trackly, FoundRole; ok): our recipes are served over MCP. Plugin/skill install is FITS AS RECIPE (distribution), not product code.
- Skill version checksum + doctor (trackly): HAVE (tools_version).
- Local stdio/CLI for doctor (trackly agent doctor, JobCtrl doctor): CLI has only api and setup-profile (cli.py:23,37). Low value.
- Webhooks signed HMAC (LoopCV, JobsPipe): CONFLICTS (no worker; pull not push, decisions 11/23).
- Approvals policy gate with first-answer-wins and phone approval (AIHawk invisible_dots guide.md:350; ok): HAVE web approval; phone approval CONFLICTS.
- Per-MCP-server permission (AIHawk): n/a.
- give_feedback tool to a human (JobsPipe, web read): HAVE report_missing_tool.
- Tool descriptions treated as prompt surface and eval-tested (jobsync): FITS as test practice. Our tests/test_mcp_server.py pins list; text budget test exists (session-lessons mentions MCP text-budget).
- Agent "directive" pattern (server returns resume + instructions, agent scores, calls save_*) (jobsync matchDirective.ts): ALREADY HAVE (same shape, fit_record).

### A14 Trust, safety, privacy
Job360 has: five hard lines, data-not-instructions, ask_user, export/delete with password, trust page, SSRF guard, refusals audited.
Extra elsewhere:
- Provenance derived from entry point, fail closed (freehire): see A1.
- Untrusted-content coverage validator enforcing the rule in CI (career-ops validate-untrusted-content-coverage.mjs; unchecked): FITS (a test that every recipe carries the line).
- Import with undo snapshot; zip export excluding secrets (jobsync backup.md:34; unchecked). We have export and delete; undo unchecked.
- Redaction of sensitive text in issue forms / privacy scanner (job-apply-plugin): n/a.
- Sensitive-answer deletion guard and revoke is two-step (trackly docs; unchecked). We have remember-OK consent; two-step revoke FITS minor.
- Bias audit badge (Jack & Jill, web read) and visibility Open/Exceptional/Hidden: recruiter side later.
- Shutdown risk: users lost history when Sonara closed (WebSearch only; unchecked). We have export_history: ALREADY HAVE; also a plan-for-wind-down is a trust-page candidate.
- "Pre-vetted, no ghost listings" claims: do not copy.
- Stealth Mode (Final Round) and employers-cannot-detect claims (Massive, LoopCV): CONFLICTS with trust page; do not copy.

### A15 Other
Job360 deliberately has none of: notifications, pricing, mobile, community, coaching.
- Competitors with these: WhatsApp/iMessage/Telegram (Tsenta, freehire, AIHawk; REA unverifiable): CONFLICTS decision 23 (wait for evidence). Pricing: credits and caps (Tsenta, 6figr, LazyApply, Massive, JobCopilot, freehire): CONFLICTS (no credits; rule). Coaching/coach B2B (Careerflow, Kickresume, Jack & Jill, Scale.jobs): CONFLICTS (agent coaches in chat). Mobile apps (Himalayas, Reactive Resume, jobwyre PWA): CONFLICTS.
- Free tier with data kept after cancel, "nothing created is ever withdrawn when a plan lapses" (freehire, Huntr): aligned with ours.
- Public changelog + support repo + feature pipeline (jobwyre): FITS as a docs point (STATUS.md exists).

## 2. "Not borrowed yet" list

### 2.1 FITS and FITS AS RECIPE, ranked by value to a seeker using their own agent

Owner priority areas first (feedback loop, outreach, Gmail, interview, offer).

| # | Area | Mechanism | Who has it | Evidence (check) | Fit |
|---|---|---|---|---|---|
| 1 | A8 Gmail | Privacy filter before reading mail: skip/redact OTP, SSN, card, bank, password-reset (subject-only) | apply-potato | email_filters.py:29,47-58 (ok) | FITS AS RECIPE (daily.md) |
| 2 | A8 Gmail | Classification ordering rules in the recipe: Noise first; Rejected decided before Offer; tense decides receipt vs invitation; rejection shown, not auto-applied; offer/hired/declined never inferred from mail without the user | career-ops, apply-potato, freehire, ai-job-search | reply-matcher.mjs:687-693 (ok); email_categories.txt (unchecked); classification.go:109 (ok); gmail-sync.md:74-75 (ok) | FITS AS RECIPE |
| 3 | A8 Gmail | Query hints: ATS sender-domain list, -in:sent -in:drafts, exclude Promotions/Social/Forums, daily Promotions notify-only scan | ai-job-search, apply-potato | gmail-sync.md:49-51 (ok); promotions_scan.py:17 (unchecked); my grep of daily.md shows no ATS-domain list | FITS AS RECIPE |
| 4 | A8 Gmail | Inbox receipt preflight before applying: search the connector for an earlier application receipt; requisition ID alone never enough; match blocks the form until user says | trackly-cli | inbox-receipt-preflight.md:16-18,60-63,77-79 (ok) | FITS AS RECIPE (apply.md) |
| 5 | A8 Gmail | Unmatched mail reasons: ambiguous / untracked / no_company / all_retired, as a reason code on the Needs-you ask; log not retry | apply-potato | needs_review.py:9,27,39 (ok) | FITS (store: reason on ask) |
| 6 | A11 Offer | Salary observation ledger with trust tiers and "stated to interviewer" figures so rounds don't contradict | career-ops | salary-gap.mjs:1-24 (ok) | FITS (store tool: observation with tier) |
| 7 | A11 Offer | Offer side-by-side with Unknown != zero, no totals across mismatched currency/period | Reactive Resume | comparing-job-offers.mdx:35 (ok) | FITS (read-only view) / FITS AS RECIPE |
| 8 | A11 Offer | Contract companion: clause walk, lawyer questions, never "safe to sign", draft negotiation reply; talking points only from verbatim CV figures | career-ops | offer-prep.md:23-35; negotiation-roi.mjs:14 (ok) | FITS AS RECIPE (new offer.md) |
| 9 | A11 Offer | Negotiating / Offer received / Accepted sub-stages | FoundRole (web read), Himalayas (REA ok) | job-tracker page; REA cell 348-350 | FITS (event types), check settings first |
| 10 | A12 Feedback | Learning gate: a lesson needs >=3 compatible signals across >=2 jobs, no contradiction, then owner acceptance; copy says "descriptive not causal" | JobCtrl | outcomes-and-feedback.md:36-54 (ok) | FITS (tighten lessons.py gate; we already require owner yes) |
| 11 | A12 Feedback | ATS vendor / channel yield with coverage %, reported as channel yield not discrimination | career-ops | patterns.md:57-80; analyze-patterns.mjs:257-359 (ok) | FITS (new count dimension) |
| 12 | A12 Feedback | Tailored-vs-base CV split with n>=10 warning; resume-version win rate | Huntr (REA ok), Applendium (web read) | huntr viewing-application-insights.mdx via Reactive Resume doc :26 (ok); Applendium site | FITS (count dim; we already count by CV label) |
| 13 | A12 Feedback | Only trusted event sources (mail/calendar) enter timing math; self-recorded dates flagged | freehire | appevent.go:71-72 (ok) | FITS (small; verify feedback_numbers) |
| 14 | A12 Feedback | Right-censored medians; stage velocity notes in-flight | career-ops | funnel-velocity.mjs:22-28 (ok) | FITS (small) |
| 15 | A12 Feedback | One-page activity report (PDF) for a coach or work-search obligation | Applendium | CWS listing (web read) | FITS (read-only render) ; need unverified |
| 16 | A9 Outreach | Warm-lead-first via saved contacts; 3-search ceiling; never guess a name; contact type/persona | career-ops | contacto.md:23-35,54-74 (ok) | FITS AS RECIPE (reach.md) |
| 17 | A9 Outreach | Save recruiter/interviewer from rejection/invite email so identity is not lost | career-ops | contact-extract.mjs:1-12 (ok) | FITS AS RECIPE |
| 18 | A9 Outreach | Per-fact provenance on contacts (source kind, ref, confidence, confirmed) | JobCtrl | contacts-and-outreach.md:18 (ok) | FITS (extend found_via) |
| 19 | A9 Outreach | LinkedIn Connections.csv join to target/applied companies; LinkedIn people-search links per job; recruiter email from the ad | career-ops, ai-job-search, JobSpy | linkedin-join.mjs:1-12; SKILL.md:185-207; util.py:121 (ok) | FITS AS RECIPE; storing full connections list would CONFLICT decision 9 |
| 20 | A10 Interview | Consistency brief from the exact submitted CV; retracted-claims gate; earlier-stage feedback ranks questions | ai-job-search, career-ops | interview.md:26,70; practice.md:17 (ok) | FITS AS RECIPE (prep.md); retracted claims = FITS store |
| 21 | A10 Interview | Mock interview + re-drill from last debrief weak spots | Final Round, Reactive Resume, Rezi, Teal, Careerflow, AIApply, Jack & Jill, Simplify | web read / REA unverifiable | FITS AS RECIPE (agent roleplays; save transcript via debrief) |
| 22 | A10 Interview | Misfit detection from what the candidate says fluently (over interview_session signals) | career-ops | patterns.md:7 (ok) | FITS AS RECIPE (review.md) |
| 23 | A13 MCP | OAuth read-only vs read+write consent scope; Connected apps list with disconnect | jobwyre | REA ev_df897891 (unverifiable) | FITS; check what oauth.py does (unchecked) |
| 24 | A13 MCP | readOnlyHint / destructiveHint annotations on tools | jobctl-claude, Reactive Resume | server.mjs:248; tool-meta.ts:883 (unchecked) | FITS (grep annotations in mcp_server.py not done) |
| 25 | A6 Applying | At-most-once submit: durable intent before send, crash => needs_verification, never retry | JobCtrl, freehire | at-most-once guide:55 (ok); autoapply AGENTS.md (ok) | FITS (store: intent event) |
| 26 | A6 Applying | Approval freshness: bound to materials, profile version, URL, within 15 min; fingerprint-bound lease | JobCtrl, job-apply-plugin | apply.md:87-109 (ok); job_apply_policy.py:29-31 (ok) | FITS (check_submit token) |
| 27 | A6 Applying | Three state lines in hand-off; access-probe states; recover after context loss; permanent vs retryable failure classes | trackly-cli, ApplyPilot | review-handoff.md:23-29; access-probe.md:6-26; launcher.py:522 (ok) | FITS AS RECIPE (forms.md/apply.md) |
| 28 | A7 Tracker | Closed reasons accepted-other / withdrew / no-response | Reactive Resume | data.ts:5 (ok) | FITS (check existing reasons first) |
| 29 | A7 Tracker | User priority / excitement rating; waiting_referral; get_due agenda | Huntr, Teal, jobctl-claude, Himalayas | server.mjs:20,197,218 (unchecked); none found in our code | FITS (user's own words) but near ranking; owner decides |
| 30 | A1 Profile | Experience atoms with entry-point provenance; correction suppression marker; "Ask first" memory policy | freehire, Reactive Resume | AGENTS.md:13,26-27; tool-meta.ts:38 (ok) | FITS |
| 31 | A1 Profile | Claims-vs-GitHub check (PRs authored) | career-ops | github-evidence.mjs:1-14 (ok) | FITS AS RECIPE / extend github_enricher |
| 32 | A5 Docs | jd-similarity reuse|edit|regenerate; Conservative/Balanced/Turbo tailoring + change report; get_resume_format style schema tool | career-ops, Kickresume, Rezi | pdf.md ~22 (unchecked); REA ok; Rezi README (unchecked) | FITS AS RECIPE |
| 33 | A2 Intake | Posting provenance fields (resolved URL, retrievedAt, completeness); CSV import of past applications | Reactive Resume, Simplify | data.ts:12 (unchecked); mcp-tool-names.ts:39 (unchecked) | FITS (bring/import) |
| 34 | A14 Trust | CI check that every recipe carries the untrusted-content line | career-ops | validate-untrusted-content-coverage.mjs (unchecked) | FITS (test) |

### 2.2 ALREADY HAVE (false gaps, correct these)

- Furthest-stage funnel from history, not current status (ai-job-search html-report.md:52, apply-potato, Reactive Resume Insights, career-ops): ours status.py:102, feedback_numbers.py:434.
- Calibration by score band with inverted/flat/insufficient (career-ops calibrate.mjs): feedback_numbers.py:306-330.
- Small-sample flag on every rate; weekly digest; friction/assessments (career-ops, JobCtrl): feedback_numbers.py:209-534, stats.py:136.
- Verbatim feedback, post-mortem, lessons that return via get_profile (career-ops outcome.md, ai-job-search): lessons.py:39, recipes/feedback.md:11-52.
- Idempotent email source / skip processed message ids; per-application cursor; oldest-first (ai-job-search state.json, apply-potato): daily.md:23-26, mcp_server.py record_event L1220.
- Confirmation email = proof, not a second applied (ai-job-search, Tsenta): daily.md:34,44; proof.py.
- Single-match-or-ask (apply-potato job_row_match.txt:34; freehire maillink): daily.md matching.
- Receipt of exactly what was sent (Tsenta REA ok, Reactive Resume Mark as applied, Scale.jobs proof, career-ops outcome archive): routes/receipts.py:167, mcp_server.py L841,1843.
- Pre-submit fact guard on CV numbers (Resume-Matcher _novel_numbers, career-ops verify-cv-facts, Tsenta true-facts rule): cv_checks.py:215-560, check_submit unsupported-claims ask.
- Knock-out pre-scan, work-auth, salary parse (career-ops apply.md, ApplyPilot, GodsScion): kit.py:1719,1735; job_checks.py:188-245,298.
- Ghost/liveness/repost (career-ops, JobsPipe, FoundRole, freehire): hunt_kit.py:2040-2301, posting_history.py, reposts.py:3.
- Human approval gate, dry run/practice, daily cap, pause (Tsenta review gate, GodsScion, job-apply-plugin): assistant_settings.py:636,682; mcp_server.py L1629.
- Actor tag on events (jobctl-claude): authorship.py.
- Follow-up cadence and no-"just checking in" voice (career-ops followup.md, freehire followup.go): auto_emails.py:124, rules.md:314-352 (PR #871).
- Agent-writes/server-stores directive (jobsync, Resume-Matcher): save_fit, save_artifact.
- Private ICS interview feed (jobwyre REA): routes/calendar.py:64-149.
- Missing-capability channel (trackly get_more_tools, JobsPipe give_feedback): report_missing_tool (mcp_server.py L1859).

### 2.3 CONFLICTS (listed for the owner to decide)

| Mechanism | Who | Conflict |
|---|---|---|
| Job search, ranking, match feeds, saved-search alerts, signals, daily diffs, 3.3M-posting corpus | freehire, JobsPipe, FoundRole, Himalayas, Indeed, Jobright, 6figr, Simplify, Wobo, Kickresume, Teal, Massive, LoopCV, JobCopilot, JobSpy, ApplyPilot | rule 4, decision 1 |
| Job Match Score / percentages by Job360 | Huntr, Teal, Rezi, Jobright, Wobo, Tsenta, freehire, Resume-Matcher | rule 4/5, decision 5 (agent judges) |
| Own LLM: tailoring, cover letter, mock interview, fit analysis | freehire, Kickresume, Huntr, Rezi, Final Round, ApplyPilot | decision 28 |
| Gmail/Calendar OAuth and polling, forwarding address | freehire, JobCtrl, apply-potato, JobShinobi, Applendium, 6figr | decisions 8/19 |
| Salary/market benchmarks and company ratings of our own; DOL wage check; H-1B / sponsorship lookups | Himalayas, Jack & Jill, FoundRole, career-ops h1b plugin, freehire, Indeed | rule 5, decision 27 (the user's AI can read Indeed/Himalayas MCP itself) |
| Hard-constraint blockers capping a score | freehire | decision 27, rule 4 |
| People databases, recruiter/HM finder with credits, email finder, referral network, double-opt-in intros | JobCopilot, 6figr, Simplify, LoopCV, Jack & Jill, freehire | decision 9, recruiter side later |
| send_outreach directly from the server | 6figr | hard line 1 |
| Browser extension (clipper, autofill, side panel) | Huntr, Teal, Careerflow, Simplify, Tsenta, FoundRole, Rezi, JobCopilot, freehire, Scale.jobs, LazyApply | VISION table: no extensions |
| Unattended bulk auto-apply, CAPTCHA solving, "undetectable" | ApplyPilot, LazyApply, LoopCV, Massive, Tsenta, Wobo, Sonara | trust page, product rule: nothing rewards volume |
| Push, WhatsApp/iMessage/Telegram, mobile apps | Tsenta, freehire, Himalayas, AIHawk | decisions 11/23 |
| Credits, paywall, per-application price | Tsenta, 6figr, LazyApply, Massive, Scale.jobs, freehire | pricing line, hard-rule M4 |
| Signed webhooks | LoopCV, JobsPipe | pull not push (no worker) |
| Tracer links / resume-view tracker | freehire, LazyApply | third-party tracking; trust page. Owner decides |
| Cross-user reply-rate benchmark | freehire | needs consent-first aggregate; privacy |
| Charts-heavy dashboards, Sankey, kanban, daily goals, time tracking | career-ops web, Huntr, jobsync, JobCtrl | decision 13; no kanban |
| Undisclosed live interview copilot / stealth mode | Final Round | trust page |
| Community, Hired Wall, coaches, Career Map | career-ops, Careerflow, Kickresume, Scale.jobs, Jack & Jill | A15 deliberate non-goals |
| Opposite stance on outreach in feedback loop | (ours) | owner reversed decision 11 / "don't link" (PR #871); nothing to copy |

## 3. Where we are ahead (no competitor does it, or only we do)

Claim made only where the research lists the area under "not_found" for every competitor, so treat as "not found in what was read", not proof.
1. Memory layer for the user's own agent as the product (VISION one line), served as MCP + recipes + OAuth 2.1; closest are jobsync's 9 tools, Resume-Matcher MCP, jobwyre and Reactive Resume MCP, none with a recipe layer.
2. Server-gated check_submit with reason codes (paused, cap, duplicate, unknown_site, host mismatch, form not swept, unsupported CV claims, blocked) combined with practice-first-submit. Others have a review step; none list a code-reasoned gate (JobCtrl comes closest).
3. check_form: live-form questions vs saved answers with knock-outs and over_limit as a separate tool; closest trackly/career-ops, but those are skill text, not a callable store tool.
4. Automatic employer emails gate (check_auto_email) with idempotent one-time emails and verbatim text recorded. Not found elsewhere.
5. Outreach gate: company cap 2 per 14 days and daily cap, "already sent/replied" (check_outreach). Not found elsewhere.
6. Lessons loop: owner-approved, removable, returned via get_profile, with lesson_effect count. Others have analytics only (JobCtrl's gate is closest).
7. Answer bank with company-scoped > unscoped, fail-closed ambiguity AND remember-consent (trackly/neonwatty parts combined).
8. Receipts "N of M fields, S skipped, G guessed" plus proof levels and one-time screenshot link.
9. Country-agnostic visa signal (no country rules) and decision 27.
10. Refusal audit with closed reason codes and whole-account export/delete with password check.
11. Hunt kit as a downloadable file run by the user's own AI, with liveness classifier, run lock, dead boards and --why-missed. Competitors host a crawler instead.
12. Free, no credits, nothing that rewards volume (opposite of 12 competitors).

## 4. Coverage table

Method key: code = source read; REA = actual REA MCP capture; web = web read (Apify/curl/WebFetch); BLOCKED = could not read.
Feature counts are as supplied (per research pass). "Unchecked" counts quote verification only.

| Competitor | Method | Features | Not checked / limits |
|---|---|---|---|
| career-ops | code | 29 | 8 unchecked: JD archive, scan.mjs providers, discard analytics, calendar plugin, email drafts, CLI router, untrusted-content validator, hired-share; registry JSON for linkedin-alerts |
| ai-job-search | code | 17 | 9 unchecked incl. /setup calibration, /expand, scrape, evaluation gates, rank, drafter pipeline, Notion sync, security_guards |
| neonwatty job-apply-plugin | code | 11 | storage contract, preferences, replay harness partly; no tailoring/tracker analysed (not in repo) |
| borgius jobspy-mcp-server | code | 6 | schemas and prompts partly unchecked |
| trackly-cli | code | 14 | hosted backend closed; profile, search, tracker, CLI rows unchecked |
| jobctl-claude | code | 14 | backend closed; 10 of 14 unchecked; skill vs server status enum disagree |
| JobCtrl | code (docs/plans) | 23 | not run; 12 unchecked |
| ApplyPilot | code | 17 | AGPL (ideas only); 11 unchecked |
| Gsync/jobsync | code | 17 | 15 unchecked |
| apply-potato | code | 19 | 12 unchecked |
| get_jobs (loks666) | code | 15 | Chinese boards; 8 unchecked |
| GodsScion Auto_job_applier | code | 18 | 11 unchecked |
| JobSpy | code | 10 | 8 unchecked |
| Reactive Resume v6 | mixed (code+docs, 1 REA capture) | 26 | 14 unchecked; REA only for home sections (original ev id unverified, re-captured) |
| Resume-Matcher | code | 13 | 8 unchecked |
| open-resume | code | 5 | unmaintained since 2024-10; 3 unchecked |
| Awesome-CV | code | 4 | template only; all unchecked |
| AIHawk | code | 8 | repo is no longer a job applier; old job code not retrievable |
| freehire | mixed (code + 1 REA pricing capture) | 27 | 14 unchecked incl. A13 MCP surfaces, A14, A15 plan limits (REA only for $5/3-per-day/refund) |
| JobsPipe | mixed (REA + web) | 10 | A3 signals/ghost/MCP are web read; 6 unchecked; sighting log starts Jun/Jul 2026 |
| Himalayas MCP | REA | 9 | 6 unchecked; employer tools described but not opened; no apply guide opened |
| Indeed MCP | mixed | 6 | docs.indeed.com/mcp BLOCKED by Cloudflare; tool list taken from attached tool schemas (web read); scopes secondhand |
| Rezi | mixed (code rezi-mcp + web + REA) | 12 | most features are web read; Pro price from REA |
| 6figr JobGPT | mixed (code + REA) | 12 | calculate_match_score named in README but absent in src/tools; terms/credit prices not published; offer comparison mechanism not opened |
| Tsenta | mixed (REA + web) | 14 | 3 unverifiable (fit score, seven surfaces, extension); MCP tool names not published |
| FoundRole | web | 16 | REA BLOCKED (Cloudflare) on all 6 attempts; everything is web read |
| LoopCV | mixed (REA + web) | 15 | 6 unverifiable; MCP beta/waitlist vs live setup page disagree |
| JobCopilot | mixed (REA + web) | 13 | 3 unverifiable; no MCP page found |
| Massive | web | 8 | REA target_not_allowed; curl 403; only Apify worked; pricing is competitor-reported (Tsenta blog) |
| AIApply | mixed | 12 | pricing page redirected; mostly web read |
| Wobo | mixed | 14 | /connect/authorize "Loading your connection" text unconfirmed; no MCP found; REA target not allowed otherwise |
| Jack & Jill | web | 14 | candidate side only; all web read |
| Kickresume | mixed | 12 | tailoring levels REA ok; rest web read |
| Sonara | web | 5 | thin marketing; /pricing 404; shutdown/relaunch history is secondary search only |
| LazyApply | web | 9 | all web read |
| Scale.jobs | mixed | 14 | 2 REA ok (proof, pricing); rest web read; human service |
| Huntr | mixed (REA + web) | 13 | 4 unverifiable |
| Teal | web | 10 | Cloudflare 403 to curl/WebFetch and REA; Apify only; help/blog BLOCKED |
| Careerflow | REA | 10 | all 10 unverifiable (landing pages captured did not carry the quoted sentences) |
| Simplify | REA | 10 | all unverifiable |
| Jobright | REA | 9 | 2 ok, 7 unverifiable |
| jobwyre | mixed | 11 | 3 ok; MCP tool names not published; /mcp page not re-captured |
| Applendium | web | 13 | REA target_not_allowed; /upgrade rendered empty; tiny product (14 users) |
| JobShinobi | web | 11 | direct fetch blocked; Apify used |
| Final Round AI | web | 14 | docs.frai.pro BLOCKED; REA capture blocked by promo modal; 1 unverifiable |

Count of references: 45. Competitors whose names appeared in your message as "46-47" were not all present in this data; two numbers I could not map: none found beyond 45, so the owner's 46-47 may include products not in this research set (unchecked).

Not checked at all in this pass (explicit gaps):
- I did not re-open any competitor repository or page myself; all competitor claims are from the supplied data.
- I did not read mcp_server.py, oauth.py, settings.py event-type lists, recipes (reach.md, prep.md, offer area) or lessons.py in full. Items marked "unchecked on our side" need a grep before anyone builds: event types for rescheduled/cancelled/negotiating, MCP tool annotations, token expiry/rate limits, contact type field, recipe coverage of ATS domains and privacy skips.
- REA: no REA call was made in this pass.
- Features from the earliest competitors (career-ops 29, etc.) are mostly A12/A8 heavy; areas A2/A15 for oss repos are lightly covered.
- Session-lessons asks that every borrowed technique be quoted against the reference file before building: none of the items in 2.1 has been re-quoted here beyond the QC pass; run that check first.
