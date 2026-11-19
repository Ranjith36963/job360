# CV review test: overnight report (session "CV review test", task starts at :33)

Target: 10 new CVs written and stored strong per run (or all queued, plus weak CVs fixed to 10 pieces of work).
Coverage: every queued job without a strong CV is done this run or listed as left.

## 22:33 run  (score 0%)
- Achieved: 0 new strong CVs (0 of 10). 4 re-reviews recorded (561, 565, 568, 570), all stored weak. Run lasted about 1 minute.
- Missed: 569 reviewer verdict never recorded; 525, 542, 544, 550, 555 reviews not reported back when the run ended
  ("I'll end my turn here and wait for the reviewer notifications"); 572, 574, 575, 577, 578 not started. 190 items still need work.
  No CV was rewritten: the run only re-reviewed.
- Biggest cause: the run ended its turn while its reviewer sub-agents were still running, so five reviews were never
  recorded and nothing was rewritten. Second: weak codes `claim_not_in_quote` / `quote_too_thin` on bullet lines
  (561 lines 4, 11, 12; 570 line 9), `jd_term_not_in_ad` (561 jd item 16) were read as "reviewer quote problems", not fixed by rewriting.
- Fix: PR #1679 (recipe only: step 4 runs every writer and reviewer agent in the foreground and never ends the turn while one runs;
  step 6 sends the quote-coverage line codes to a free re-review, then a line rewrite on the same code). Failing test first
  (`test_the_cv_run_waits_for_its_agents_and_treats_quote_codes_as_a_fixable_review`), recipe tests, ruff and mypy ratchet clean. Auto-merge on.
- Decision taken without the owner: read Kits' section "the 20:33 run hit the target" as true for that run only; this 22:33 run missed it,
  so I fixed the recipe before starting audit section 2. No new refusals added.
- Needs the owner: none yet.

## 23:00 check (no new run yet; next run 23:33)
- #1679 merged at 23:00 UTC (CI, security and bugs reviewers green), 33 minutes before the 23:33 run, outside the no-merge window. I did not verify the Railway deploy (no Railway access in this session); the 23:33 run will show whether the recipe text is live.
- No `orders-cv-review.md` from Kits yet.
- Next: score the 23:33 run at the 23:58 check-in. Audit section 2 waits for that result, so that one run tests #1679 alone.

## Catch-up entry, written 08:13 UTC 19 Nov (this session went silent from 23:00)
- My 23:58 check-in and every later one never reached this session, so I scored no run after 22:33 and built nothing after #1679.
  I read Kits' `orders-cv-review.md` (01:10) only now, at 08:13. I did NOT take audit section 2 findings 2 and 3.
- The scores below are Kits' (`morning-report.md`), not my own reads of the runs: 23:33 unscored; 00:34 0%; 01:33 80%; 02:33 100%;
  03:33 40%; 04:33 100%; 05:33 100% (two in a row at 04:33 and 05:33: target met). Fixes that landed from Kits: #1693, #1703.
- Honest read of #1679: it merged before the 23:33 run, which Kits did not score; the 00:34 run still stopped after re-reviewing
  weak CVs, so #1679 alone did not fix the stop. #1693 and #1703 did the work.
- Open for the owner/Kits: audit section 2 findings 2 (closed skills-label list downgrades labels such as "Practice:") and
  3 (literal token coverage: "k8s" against "Kubernetes") are unbuilt. The 03:33 run's 1105 weak on its skills line (no tries left) is likely finding 2.
- Needs the owner: say whether to build findings 2 and 3 now that the overnight window is over (each ships to production on merge).

## Morning work, from Kits' 08:5x orders (written 09:20 UTC)

### 1. #1703's counter: PR #1707 (open, auto-merge on, pushed 08:58)
- Root cause: `cv_run.begin()` re-stamps the run's start on EVERY `cv_work` read shaped like the first one (no `after`,
  `include_profile` true). `strong_cvs()` counts only reviews since that start, so a mid-run first-shaped read drops
  the count to 0 and the next record says "1 of 10"; "CV run complete" is never reached.
- Checked against a `record_events` batch of several `cv_reviewed` items (how the recipe records): it counts 1, 2, 3
  correctly. The reset comes only from the read. The live 08:33 run (it used `after=` on every page) counted 2 to 10
  and saw "complete", which fits: the bug shows only when a run re-reads page 1.
- Fix: a run begun under 50 minutes ago is continued, not restarted; `cv_run_begin` logs `begun` or `continued`.
- Limitation: I could not open the 05:33 transcript; the mechanism is reproduced by a test that fails before the fix.

### 2. Audit finding 2 (skills labels): built, committed on `fix/cv-skills-label-flag`, PR held until the first run after #1707 is scored
- App 1105 confirmed: line 23 `Technologies: FastAPI, PostgreSQL, Redis, AWS, SageMaker, S3, Docker, GitHub Actions`
  (every item a saved skill) was never held. The owner's LinkedIn export has per-job `Technologies: Docker • AWS Bedrock
  • ...` lines; Job360 read each as a skills GROUP, so "technologies" became the owner's own group word and the label
  claimed that one job's tools. The same items under `Skills:` are held. No `skills_label_unknown` hint fired, so the
  reviewer saw only `claim_not_in_quote`.
- Fix (narrow): LinkedIn's per-job lists add items, never a group. The owner's own CV groups still bind their words.
- Decision without the owner: I did NOT make every label word a non-downgrading flag. That flips the Checker's
  2026-11-10 attack set ("Agents: Python, SQL", "Calnex:", "MSc:") and an unknown word cannot be told from an invented
  employer ("Microsoft:") without weakening a safety check. "Practice:" stays weak with its "use one of ..." advice.

### 3. Audit finding 3 (literal coverage): NOT built; the evidence does not show it
- 1088 is stored strong now. 1089 (`jd_coverage_low`, 10 of 13 must-haves) has no `jd_term_not_in_cv` code: the reviewer
  named NO line for 3 must-haves the profile has (e.g. vector databases, prompt engineering), so the CV simply does not
  name them. A "k8s"/"Kubernetes" miss would show as `jd_term_not_in_cv`; none appears here. Accepting reviewer-named
  lines would not change 1089. The fix for 1089 is the writer naming those terms, which cv.md step 6 already says.
- Needs Kits/owner: point me at a real `jd_term_not_in_cv` on a synonym if you want finding 3 built anyway.

### Check only, no fix: does check_submit stop CVs the review passed?
- No. Source-Right (617): at 05:05 the PC run held CV v1 (artifact 429), whose only review (2026-11-10) was stored
  WEAK. check_submit followed that weak review. Since 06:43, v3 (1786) is stored strong and has no "agentic coding" or
  "tool calling". But ask 706 is still open, so 617 still waits on "answer 1 question".
- The ask's text is partly wrong: the profile DOES hold "Stevenage" (CV: "Calnex Solutions | ... | Stevenage") and
  "Remote" (Nethermind) - the PC run's model wrote that, not the review.
- TechBaton (620): newest review stored weak with only `retry_limit` (no other code, tries_left 0), so it waits on the
  owner's CV approval. The gate is not stricter than the review; the review gave out of tries.
- Needs the owner: answer or withdraw ask 706 (617 now has a strong CV); approve or fix 620's CV.

## 09:33 run  (score 100%) - first run on #1707 (merged 09:18)
- Achieved: 10 stored strong (8 new: 1133, 1134, 1135, 1136, 1138, 1140, 668, 672; 2 weak fixed: 663, 667). Job360 replied
  "CV run complete: 10 stored strong." The count rose 5 to 9 across record replies and never reset.
- Missed: 669 stored weak (`jd_gap_claimed` on "Deep Learning", "API Development"); about 140 jobs left.
- Fix: #1707 holds. Next slice opened: PR #1711 (finding 2, LinkedIn per-job Technologies lists are no skills group),
  pushed 09:51, auto-merge on, outside the :28 to :43 window.
