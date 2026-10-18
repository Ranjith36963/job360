<!-- doc: FROZEN | written 2026-10-18: Job360 feature map for the competitor gap analysis (agent-written, checked against code) -->
# Job360 own feature inventory (read-only, code read 2026-10-18, main 65fc5eb)
Evidence is repo/path:line. MCP tools = 32 (grep -cF '@mcp.tool()' mcp_server.py). Recipes: apply, daily, debrief, feedback, forms, hunt, prep, reach, research, review, rules, setup (backend/src/recipes/*.md). Frontend pages: bring, applications, applications/[id], needs-you, profile, receipts, stats, settings(/connect,/account), trust, privacy, terms, oauth consent.
Product rule frame: docs/product/VISION.md "We never build" column + decisions 1,5,8,9,11,17,28.

## A1 Profile & memory
- CV parse (PDF/DOCX), deterministic, no LLM: services/profile/cv_parser.py:49 extract_text_from_pdf, :147 extract_text_from_docx; layout.py section segmentation; extraction_quality.py scores any extraction.
- LinkedIn "Save to PDF" parse: linkedin_parser.py:554 parse_linkedin_pdf_async. GitHub enrich (languages, topics, dep files): github_enricher.py (docstring "infer skills from repos, languages, topics, and dependency files"). ESCO skill normalisation skill_normalizer.py; evidence tiering skill_tiering.py.
- Agent reads raw text then writes structure: mcp_server.py get_profile L548 ("include_raw=true returns raw"), update_profile L1571 (all-or-nothing, edits append-only: services/profile/edits.py "append-only overlay over extraction"). Profile versions + diff: routes/profile.py:2131,2244. JSON Resume export: routes/profile.py:2294.
- Memory/answer bank: user_info.py (six paths contact, right_to_work, logistics, languages, equality, answers) ; topics + aliases, ambiguity fails closed: answer_match.py:8 "Borrowed (reference code, read 2026-10-15): trackly-cli"; employer-scoped answers (PR #835); state confirmed/relayed/inferred/sensitive (PRs #847 #866); "remember" consent for sensitive (PR #860, 0063).
- Preferences, settings (score line, above/below line, submit_mode, daily_cap, pause): services/profile/assistant_settings.py; riskier changes wait for web OK: setting_requests.py ("Waiting for your OK"); web UI components/needs-you/SettingRequestCard.tsx.
- Work authorisation: user_info.right_to_work per country + job country list compare (services/applications/visa.py; VISION decision 27 "list membership").
- Search brief from setup answers (where to search): services/profile/search_brief.py:87.
- Guided setup interview (6 rounds): recipes/setup.md:1-70; progress shown components/profile/SetupProgress.tsx.
- CV visual style capture so rendered CV matches uploaded design: cv_style.py ("style fingerprint read deterministically from the PDF").
- Profile edit history, Keep / take-back: routes/profile.py:949,967,1008; lessons shown profile tab LessonsList.tsx.

## A2 Job intake
- bring_job (link-fetched or pasted text; births Application): mcp_server.py:717; routes/bring.py:300 POST /jobs/bring; web page app/bring/page.tsx.
- URL fetch with SSRF guard + 3-rung extraction ladder (JSON-LD, meta, body): routes/bring.py:586; services/fetch/guard.py, extract.py ("three-rung extraction ladder"). Web-only; "No MCP tool exposes this" (services/fetch/__init__.py).
- Dead apply link replacement: update_job apply_url (mcp_server.py:1530; PR #833).
- Duplicate/repost/similar-ad checks on bring: reposts.py ("ADAPTED from career-ops detect-reposts.mjs"), posting_history.py (SimHash fingerprint:62).
- NOT BUILT: browser extension clipper (VISION.md:57 "Chrome extensions, iOS/Android" in never-build column); import from boards; email-to-job import (jobs come from the agent's Gmail read + bring_job).
- Job-alert email intake: instruction only (recipes/hunt.md:37 "suggest LinkedIn, Indeed, freehire, Himalayas; the daily check reads them from Gmail").

## A3 Finding jobs & alerts (Job360 never searches; supplies the hunt kit)
- Hunt recipe (assistant searches): recipes/hunt.md:1-70, search_brief rows drive WHERE; coverage table report (hunt.md:64).
- Hunt kit script, stdlib only, user's AI runs it: backend/src/hunt_kit/hunt_kit.py; served GET /api/hunt-kit routes/hunt_kit.py:70. Sources: Greenhouse, Lever, Ashby, SmartRecruiters, Workday (13k sites, daily slice), Workable, Recruitee, Personio, Teamtailor, BambooHR, iCIMS, Oracle, SuccessFactors, Taleo, Jobvite, Eightfold (kit_ats.py:429,685,878,1113,1228), open feeds (kit_feeds.py), national services DE/SE/SG/CA (kit_feeds.py:638 "follow career-ops' providers"), GitHub lists, freehire.
- Country eligibility / visa filters: hunt_kit.py:145 "country_eligibility idea" ; --work-countries --remote --visa-onsite.
- Stale/closed detection: hunt_kit.py:2040 classify_liveness (alive/closed/unknown, multilingual phrases :1975-2000); dead-board memory :2109 (3 misses); --why-missed :2301; run lock :1020; atomic writes :1125; scan-run log :1155.
- Ghost-posting signals per brought job: posting_history.py "posting_signs" N of 5 (old ad, reposted, talent-pool wording, job-board link, company silent before) (PR #875).
- Dedup: seen_jobs.json in kit; bring dedup by title+company (bring_job docstring "returns the existing job").
- Alerts/schedules: NONE server-side. Daily run is the assistant's own scheduler (recipes/setup.md:67). Worker+Redis deleted 2026-09-02 (CLAUDE.md). Pull-only whats_new (mcp_server.py:1343).
- Connectors named in recipe: freehire, Himalayas, FoundRole, Indeed official (hunt.md:30).

## A4 Fit judgement (agent judges, we store)
- save_fit: score 0-100 needs reason<=140 + score_source full_ad|listing|owner; no score = null never 0; gaps hard/soft; jd_requirements; archetype; remote_policy; team_size; via (mcp_server.py:1145; fit_record.py:6 "career-ops rank-pipeline.mjs:8-13").
- Visa signal stored from ad (visa.py; mcp bring_job docstring "Visa (optional)").
- Pay advertised parsed from ad text: job_checks.py:298 pay_facts, :188-245 ports of career-ops salary-gap.mjs; web PayAdvertised.tsx.
- Skill alignment panel (profile skills found in ad text): services/applications/alignment.py:25; FitRadar.tsx, AlignmentPanel.tsx.
- Knock-out detection on the LIVE form: kit.py:1719 _is_knockout, check_form returns knockouts/needs_confirmation (kit.py:1735, mcp check_form L1806; PR #844 #863; kit.py:389 "NOT IN REFERENCE: career-ops apply.md:61-62 ... by prompt only").
- Skill-gap counts across fits: stats(view=skill_gaps), fit_patterns.py (needs 5 scored fits).
- Calibration: interview rate per fit-score band feedback_numbers.py:330.
- Line switch (above/below score line -> auto|ask) in save_fit reply (apply.md:13).
- NOT BUILT: any server-side match score/ranking (VISION rule 4).

## A5 Documents
- Agent writes CV/letter; Job360 stores every version: save_artifact mcp_server.py:1051 (kinds cv, cover_letter, answers, outreach, story_bank, interview_transcript, redflag_analysis, skill_gap_report: core/settings.py:371-377).
- Writing rules in recipes/rules.md:88-133 ("Writing the CV", letterhead rule PR #833).
- Save-time truth checks, no LLM: cv_checks.py:215 unsupported_numbers, :374 unsupported_terms, :401 missing_current_roles, :441 leaked_chatter, :469 letter_no_header, :516 stock_phrases (trackly-cli application-text.js:6-9), :560 check_pdf. check_submit asks cv_unsupported_claims (PR #875, career-ops verify-cv-facts.mjs).
- ATS: assistant's own ats_score/ats_notes stored per version (core/settings.py:383-388 "Job360 never computes or markets one"). No server ATS keyword scanner.
- Render PDF in user's CV style: tailoring/cv_render.py:575 render_cv_pdf, :623 render_letter_pdf; DOCX tailoring/docx.py:14; plain ATS PDF pdf.py.
- Version list, diff original vs tailored (read-only): diff.py:17; ArtifactDiff.tsx, ArtifactVersions.tsx; routes/applications.py:1936. No Keep button (VISION decision 26).
- Per-line provenance (own facts vs added): tailoring/provenance.py.
- Web fallback editing: routes/tailor.py:134-233, TailorPanel.tsx (text edit, keep, download); LLM removed (decision 28).
- NOT BUILT: our own CV/letter generation, template gallery.

## A6 Applying
- Apply kit (CV+letter file link, all stored answers with state, missing, duplicate, hold, autofill, settings): kit.py:1531 build_kit; mcp get_application_kit L1762.
- check_form: live form questions vs saved answers (field_type, options, limit, over_limit): kit.py:1735; PR #844 "Borrowed from career-ops apply mode" ; matching topic>wording>alias (answer_match.py).
- Human approval gate: check_submit -> submit|ask|stop with reason codes: assistant_settings.py:636 may_submit; mcp L1629; practice run first auto-submit (assistant_settings.py:682 item 10); Indeed/LinkedIn always ask; unknown_site/form_host_mismatch/email_not_verified/blocked never cleared by a yes.
- Web approval: Needs-you ReadyToSend / SendAllCard -> POST /applications/{id}/send/approve (routes/applications.py:2580), decline :2610, cv-seen :2536, autofill deny :2626, duplicate clear :2642, blocked resolve :2657; ready.py.
- Session hold across assistant sessions: hold/release routes/applications.py:2744; web "In flight" tab (PR #849/#864).
- Form events trail: form_filled, form_swept, form_checked, kit_read, cv_seen, submit_approved (core/settings.py:302-303); recipes/forms.md per-ATS notes (Greenhouse, Ashby, Lever, Rippling, Workable, JazzHR, Zoho, Wellfound).
- Blocked record (CAPTCHA/bot/login) opens a Needs-you ask: blocked.py; apply.md:60.
- Receipts frozen on record_application (CV version, letter, answers, fields, confirmation, channel): routes/receipts.py:167; mcp L841; counts line "N of M fields, S skipped, G guessed" (PR #855/#865).
- Proof of application: email > text > screenshot > none: proof.py; proof_text event; screenshot upload one-time link routes/proof.py:114,133; mcp get_proof_upload_link L1843; web ProofDropZone.tsx, ProofMarks.tsx; 7-day no-proof ask (daily.md:51).
- Applied date correction via corrects_event_id (PR #859). Repeat applied+email becomes proof not 2nd applied (PR #845).
- "Is it still open" rule before CV (rules.md:~690).
- NOT BUILT: our own browser automation / extension autofill (VISION "Browser automation of our own"); auto-submit exists only as user setting driving the USER'S agent.

## A7 Tracker
- Application object born at bring; status replayed from append-only events: status.py:102 replay_status; furthest stage wins; statuses brought, applied, replied, interview_requested/scheduled/done, offer, hired, offer_declined, rejected, withdrawn, ghosted (core/settings.py:281-288).
- Web list + detail: app/applications/page.tsx, applications/[id]/page.tsx, StatusMenu.tsx, Timeline.tsx, JobFacts.tsx. No kanban board (grep kanban: none in app UI).
- next_step state machine: next_step.py:34; shown on list rows.
- follow_up_on date + due/quiet filters: list_applications due=true quiet_days (mcp L993); FollowUpField.tsx.
- Notes/lessons: NoteForm.tsx, LessonForm.tsx.
- Auto-ghost after 45 days if inbox checked: ghosting.py:33; core/settings.py:500.
- Calendar: private .ics feed (web-only, token hashed) routes/calendar.py:64-149; calendar_ics.py; interview_scheduled + follow_up_on events (PR #820).
- Needs-you queue: asks.py, routes/asks.py:51-113, app/needs-you; MorningCheck.tsx; whats_new open_asks.
- Home = your applications (components/home/Home.tsx).
- Archive/delete: none ("Nothing is ever deleted", VISION). Account delete routes/auth.py:418.

## A8 Email & calendar
- Job360 reads no email (VISION decision 8, 19; inbox.py:docstring "Job360 reads no email"). The agent's Gmail connector reads; recipes/daily.md:24-39 defines the read: one search 3 ways in (company names OR from:<email_senders> OR label), whole email not preview, match to ONE application, record_event with source (message id idempotent: mcp record_event L1220).
- Cursor: applications.inbox_checked_at (migration 0061), mark_inbox_checked mcp L1031 (PR #853 "Modeled on MadsLorentzen/ai-job-search /gmail-sync").
- email_senders: ATS domains from apply link (inbox.py; OFFICIAL_ATS_HOSTS).
- Gmail modes auto|ask|paused, check_every 1h-24h: rules.md:202-270; preferences.daily_check.
- Status from emails: replied / interview_requested / scheduled (scheduled_at) / rejected / offer recorded at once (daily.md:29); confirmation email = applied proof (daily.md:34).
- Unmatched emails -> one ask_user (daily.md:37).
- Replies: assistant creates Gmail DRAFT replies to interview invites within 1 day + thank-you drafts, never auto-sent (daily.md:49; feedback.md:47).
- Automatic employer emails (checkin 7d, feedback_request after rejection, feedback_followup once): auto_emails.py:124 decide; check_auto_email mcp L1692; setting auto_employer_emails default off; voice from career-ops followup.md (rules.md:337).
- Calendar events: only ICS feed; assistant's own Calendar connector for creating events. NOT BUILT: Outlook, our own Gmail OAuth/polling, scheduling-link negotiation.

## A9 Outreach & networking
- Contacts: add_contact L1396 (found_via closed set incl. apollo, linkedin), update_contact L1447 (old value kept), list_people L1477 (grouped); contacts.py (1292 lines); web Contacts.tsx. Cold contacts with no job allowed.
- Message drafts as versioned artifacts kind outreach: save_artifact contact_id; stock_phrases check.
- Send gate: check_outreach mcp L1670, may_send_outreach assistant_settings.py:907 (reasons: paused, not_latest_draft, not_email_channel, already_sent, replied, application_closed, daily cap 10, company cap 2/14 days); only recorded sends count; sends only by pressing Send in user's browser; Gmail connector drafts only (reach.md:25-27, rules.md Outreach).
- Reply matching: replied event with source.sender marks contact replied (rules.md Outreach).
- Per found_via stats: by_contact_found_via (mcp stats L1494).
- Finding people = the agent (reach.md:7 "start with the company's own site and the job ad"; research.md:24).
- NOT BUILT: people database/Apollo/LinkedIn lookups (VISION table), sequences, referral tracking beyond found_via=referral, LinkedIn automation (LinkedIn msg always sent by user, reach.md:36). Outreach NOT linked to the feedback loop (owner reversed decision 11, PR #871).

## A10 Interview prep
- prep recipe (5 likely questions, 3 STAR stories, gaps, questions to ask): recipes/prep.md:14-21; saved as answers artifact + story_bank artifact versions (prep.md:22-25).
- Debrief recipe: transcript artifact, interview_session note (round, competency_tags, gaps solid|strong, signals), process_friction, assessment, red-flag signals w/ verbatim quotes, silence follow-up, offer money value: recipes/debrief.md:12-60; interview_payloads.py; interview_counts.py (digest, friction, assessments views, silent_after_interview 30 days).
- Company research recipe: research.md:14-22 (agent researches, one note, every fact with URL; Job360 history line).
- NOT BUILT: question bank, mock interview engine, live copilot (agent does these in chat).

## A11 Offer & salary
- offer event carries offer_amount/currency/period (core settings; interview_payloads.py; rules.md:~672); offer_declined reasons (feedback.md:34).
- Advertised pay parsed from ad: job_checks.py:298.
- Salary expectations per country stored (preferences.salary_by_country; setup.md:24) and used on forms (forms.md:125).
- NOT BUILT: salary benchmark data, offer comparison, negotiation help (grep negotiat/benchmark: NOT FOUND in product code).

## A12 Feedback loop & analytics
- Outcome capture: rejected/offer/hired/offer_declined with verbatim_feedback, reasons[], stage, reply_type, evidence, confidence (PR #868; spine.validate_outcome_payload); feedback.md:15-22.
- post_mortem note after outcome and for ghosted (feedback.md:23); lessons only after owner yes, removal by corrects_event_id (feedback.md:28); lessons come back via get_profile (lessons.py:39; core/settings.py:392).
- Auto feedback request after rejection (feedback.md:37).
- Numbers: stats mcp L1494 views funnel, velocity (median/p75), calibration, company, lesson_effect, followups, digest, friction, assessments, patterns, skill_gaps (feedback_numbers.py:209,259,330,449,534; fit_patterns.py; interview_counts.py). Counts by CV label, role, country, found_on, channel, contact found_via. Small-sample flag n<10; comparable needs >=20 each side (rules.md:641-657).
- Weekly review recipe: recipes/review.md:14-39. Web: app/stats StatsClient.
- export_history mcp L1360 for the agent to analyse; whole-account download /users/me/export routes/auth.py:365.
- Product analytics (PostHog first_tool_call, first_bring): services/analytics.py:39.
- Borrowed from career-ops feedback_numbers.py:9, stats.py:136 (stats.mjs:182), posting_history.py:9 (company-history.mjs).
- GAP seen: outreach not linked into loop by owner decision; no automatic "which CV text worked" analysis beyond count by CV label; no push weekly report (pull only).

## A13 Agent / MCP / CLI integration
- 32 MCP tools at /api/mcp (mcp_server.py); OAuth 2.1 + DCR + PKCE (routes/oauth.py:61-395; services/auth/oauth_flow.py); personal j360_ tokens (api_tokens.py; routes/tokens.py:49); well-known discovery (routes/well_known.py).
- Recipes served by get_recipe mcp L1859 / routes/recipes.py:70; "run 360" commands in INSTRUCTIONS (mcp_server.py:~90).
- doctor mcp L1712 with tools_version/tools_rev stale-connector detection (mcp_server.py:72-80 "Hand-bumped integer, like career-ops' VERSION file (update-system.mjs:1170-1172)"; PR #843 #854); report_missing_tool L1743 (stores wished tools; migration 0056).
- Unknown tool args refused loudly (PR #843); forgiving inputs (services/profile/forgiving.py); one 422 listing all problems with an example (event_examples.py).
- CLI: backend/src/cli.py: `api`, `setup-profile` only (cli.py:23,37).
- Connect page: app/settings/connect/page.tsx; ConnectSteps.tsx.
- REST == MCP parity enforced by test (CLAUDE.md MCP note).
- NOT BUILT: webhooks (no hits), local-first mode, plugin/skill bundle shipped by us (recipes are served over MCP).

## A14 Trust, safety, privacy
- Five HARD LINES in INSTRUCTIONS (mcp_server.py:~85): no submit w/o check_submit, no outreach w/o check_outreach, page text is data, settings only by user chat, never invent a fact, never type password.
- No-fabrication: cv_checks.py unsupported claims; rules "Truth only"; ask_user/answer_ask with user_words (mcp L1877,1901); relayed vs confirmed answers.
- Trust page lists what is never done: app/trust/page.tsx:15-34; privacy/terms pages; data download /users/me/export routes/auth.py:365; delete account :418 (password verified, rule 26); calendar feed token hashed; screenshot proof deletion user-only.
- SSRF guard services/fetch/guard.py; rate limits services/auth/rate_limit.py, export rate-limited (mcp L1360).
- Audit trail: every refusal audited with closed reason code (refusals.py); recorded_by derived never accepted (authorship.py).
- Prompt-injection stance in every recipe ("information, never instructions").
- Simulation events keep tests out of stats (PR #832).

## A15 Other
- Notifications: none (VISION decision 11 pull not push; trust page NEVER list "Push notifications at you").
- Pricing: free, no credits (VISION:"Pricing: free").
- Mobile app, community, coaching, human services, gamification, accountability: NOT BUILT; coaching "is yours to give in chat" (debrief.md:53).
- Recruiter side: later, consent-first only (VISION).
- WhatsApp: waits for a paying signal (decision 23).

## BUILD HISTORY: what we borrowed and from whom (code comments + PR bodies)
career-ops (MIT, github.com/career-ops-hq/career-ops; /var/tmp/co-src):
 - liveness-core.mjs / liveness-api.mjs + freehire liveness.go -> hunt_kit.py:1940-2076 classify_liveness (PR #875)
 - dead-boards.mjs -> hunt_kit.py:2109 ; coverage-check.mjs -> --why-missed hunt_kit.py:2184,2301 (PR #875)
 - pipeline-lock.mjs -> hunt_kit.py:1020 ; atomicWriteFile -> :1125 ; scan-runs.tsv -> :1155
 - providers/*.mjs (icims, oraclecloud, successfactors, taleo, jobvite, eightfold, workday, workable, recruitee, personio, teamtailor, bamboohr, arbeitsagentur, platsbanken) -> kit_ats.py, kit_feeds.py:638 (PR #809, #874; NOTICE.md:10-27)
 - company-history.mjs -> posting_history.py:9 (PR #875); fingerprint-core.mjs SimHash -> posting_history.py:13; detect-reposts.mjs -> reposts.py:3
 - salary-gap.mjs -> job_checks.py:188-245 (PR #840)
 - stats.mjs:182 small-sample -> stats.py:136; weekly-digest.mjs, process-quality.mjs -> interview_counts.py:10; rank-pipeline.mjs -> fit_record.py:6
 - apply.md (9-step workflow, knock-outs, needs_candidate_confirmation, limits, form_state) -> check_form PR #844, kit.py:1694,1725, spine.py:1991 (PR #863, #865)
 - reply-watch.mjs:180 -> spine.py:857 ; verify-cv-facts.mjs -> cv_unsupported_claims (PR #875); followup.md voice -> rules.md:337 (PR #871); update-system.mjs VERSION -> TOOLS_REV mcp_server.py:72 (PR #843/#854)
neonwatty/job-apply-plugin: answer-memory SKILL.md L126-133 state table -> kit.py:1003 ; job-apply-store.py:134-148,452-471,475-540 -> answer_match.py, user_info.py:632 (PR #849) ; job-apply/SKILL.md:118 "Show and confirm inferred answers" -> relayed-until-confirmed (PR #866); skills/job-search -> social "hiring" query (PR #874)
trackly-cli (kevinastuhuaman): answer-resolution.md:47-62,:145 -> answer_match.py:8,:23 (PR #849); lib/application-text.js:6-9 -> cv_checks.py:506 stock_phrases (PR #875)
MadsLorentzen/ai-job-search: /gmail-sync -> inbox cursor + senders (PR #853); /add-portal -> national services step (PR #874)
freehire (liveness.go, ghost/classify.go, MCP) -> liveness + posting_signs + connector (PR #875, #874)
jobsync / Feashliaa / SimplifyJobs lists -> companies.json starter list, universe, githublists (PR #809, #874)
Tsenta (YC) competitor read 2026-09-07 -> decisions 19-22: email evidence on events (#513), visa signal (#514), CV diff (#515), flag-for-next-time (#516) (VISION.md:Additions 2026-09-07)
Gsync_jobsync.md:184 -> doctor reconnect hint (PR #866); jobspipe_dev_agent.txt:319 -> 5xx retry in hunt_kit (PR #866)
Honesty note: session-lessons.md "Why mistakes repeated" - earlier "Borrowed from X" claims were written without opening the reference; PRs 844+ cite file:line.

## DELIBERATELY NOT BUILT (do not recommend blindly)
- Job search/feeds/ranking/recommendations on our side (VISION rule 4; hard-rules M1). Hunt kit is a file the user's AI runs.
- Any LLM of our own: fit judging, CV/letter/answer writing (decision 4,5,17,28; rule 5/M2).
- Gmail OAuth, inbox polling, email classification (decision 8, 19; table row).
- People databases / LinkedIn lookups (Apollo) - agent finds, we store (decision 9). Outreach NOT linked to feedback loop (owner reversed, PR #871; session-lessons).
- Push notifications, WhatsApp, mobile apps, Chrome extensions, iOS/Android (VISION table; decisions 11, 23).
- Our own browser automation; auto-submit only when the user turns it on (VISION:Taken from pivot; decision 10 receipts).
- Charts-heavy analytics; agent analyses export_history (decision 13).
- Credits, per-application charges, paywall (VISION pricing; M4).
- Keep button on CV diff (decision 26); version numbers hidden from humans.
- Any country/visa rules of ours (decision 27).
- Hardcoded skill lists in extraction (hard rule 28); ranking of companies from our numbers (rules.md:657 "Never skip a job, company or follow-up because of these numbers").
- Rebuilding sourcing, scorer, dedup, enrichment, ARQ worker, dashboard, notification/channel/kanban stack (CLAUDE.md "never rebuild them").
- Recruiter side now (later, consent-first, no selling candidate access).
