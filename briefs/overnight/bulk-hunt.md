# Bulk Hunt test — overnight report

Session: Bulk Hunt test (owner's task trig_01QSkCh1, starts :11). Check-ins at :36.

## 22:39 start
- Read briefs/overnight.md and pattern-audit.md §1.
- Open work: #1665 (dedupe by company+role+place, employer_search proof, kit done file) is conflicting
  with main after #1675 (--every-title). Worker is merging main (keeping both sides) and applying
  audit finding 6 (keep "(Backend)"/"(Frontend)" qualifiers; only an identical key refuses/drops,
  anything weaker stays a possible_duplicate hint).
- Coverage slices built and pushed, not yet opened: A `claude/hunt-coverage` (report.coverage per plan
  row, 422 place_missing, plan row ids, effort split sums to 100), B `claude/hunt-coverage-kit`
  (kit keeps on-site ads in every plan place whatever they say about sponsorship, remote-in-country,
  all titles rotated + variants, freehire per plan country, visa_signal per result).
  They go after #1665; B will be reconciled with #1675's --every-title.
- Scoring of the 20:11 / 21:11 / 22:11 runs in progress.
- Decision without the owner: merges only between :21 and :06 so no deploy restarts the server mid-run;
  PRs are held as drafts until then.

## 20:11 run  (score ~40-50%, inferred from Job360 events; transcript not readable)
- Achieved: 4-5 new jobs at or above 60 (Venpep 62 IN, Towards AI 66 GB, Confer 76 IN, Mindrops 72 IN; Wadhwani 68 IN maybe).
- Missed: 5-6 of the 10; coverage only GB/IN rows (≤6 of 31 plan rows).
- Biggest cause: kit scoped to GB,IN (see 22:11).

## 21:11 run  (score ~10%, inferred)
- Achieved: 1 (Harmonic Security, AI Platform Engineer, GB, 66), attribution by time only. Possibly "not due" (last hunt done 20:54).
- Coverage: GB only.

## 22:11 run  (score 0% at 22:41, still running; rescore at 23:36)
- Kit: `--titles <23> --max-years 3 --sweep-start hour --exclude-title-words "Senior,...,Manager" --work-countries GB,IN --remote --visa-onsite --known --max-minutes 25`. No --every-title. Kit hit its 25-min cap (372 ads), resumed once.
- Other sources: Gmail, Dice + ZipRecruiter (US-only results), one web search.
- 325 new after the known list, 105 "maybe known"; 0 brought by 22:40.
- Coverage: GB/IN rows + a thin worldwide-remote sweep, ~6 of 31 rows.
- Biggest cause: the search is scoped to GB,IN with sponsor-only on-site abroad, and the owner's task
  prompt forces `--max-years 3` and the exclude list (so --every-title is skipped and many ads are dropped
  by fixed patterns before Claude judges them).
- Fix: in code — #1665 (merge with main in progress), then coverage A (every plan row reported, 422 on a
  missing row) and B (kit searches every plan row, keeps every ad whatever it says about sponsorship).
- **Needs the owner:** replace the bulk hunt task prompt with exactly:
  ```
  Scheduled Job360 HUNT run. Call get_recipe("hunt") and follow it exactly. Obey every Job360 reply and warning.
  Owner focus: graduate and junior roles first. Judge seniority and years yourself from each ad; do not pass --max-years or --exclude-title-words to the hunt kit.
  ```
- Decision without the owner: I will not add new refusals for the level filter; the recipe tells Claude
  to judge level from the ad (audit rule: patterns hint, Claude judges).

## 23:30 note
- The task prompt was changed at 22:36 (now: read the search plan, `--every-title`, no word list, bring 10).
  The "Needs the owner" prompt item above is done.
- 23:11 run ended after 19 s (the 22:11 run finished at 23:07; scored at 23:36).
- #1665 merged main (kept #1675's `--every-title` and this PR's kit.done / known-ct / employer_searched),
  plus audit finding 6: a "(Backend)"/"(Frontend)" qualifier is another job; only an identical key
  refuses or drops, a near match is a `possible_duplicate` hint. CI green; marked ready 23:30 (no run live).
- Decision without the owner: at the SAME company with the same title, a different city is still refused
  (main's existing test 328 Glasgow vs 126 Austin is a real prod duplicate); not loosened overnight.

## 02:15 — back (silent 23:30-02:06: my 23:36 self check-in never woke this session)
- Read orders-bulk-hunt.md (01:10) and kits.md (02:05). Kits scored 00:11 at ~80% (10/10 brought; volume,
  snippet judging, board links, coverage workaround). 01:11 not due. Kits owns the two-pass title judging
  (`feat/hunt-distinct-titles`); I do not duplicate it.
- #1665: merged current main in (clean, no conflicts); 161 targeted tests pass locally; gate running; then
  push. Per Kits: I mark it ready only when I can watch the run after it, outside :06-:21. Plan: push now so
  the merge-cage re-judges on the new head, ready at ~02:25, watch the 03:11 run, score it at 03:36.
- Coverage slice B (kit plan rows, keep every ad whatever it says about sponsorship) is the fix for order 5;
  it will be rebased on main after #1665 and checked against Kits' two-pass branch so they do not collide.
- Decision without the owner: I keep #1665's two refusals (`kit_not_done`, `employer_searched` proof); both
  come from real runs (16:13 marked done before the kit ended; 4 board links marked not_found with no search).

## 10:50 — #1665 merged (10:47 UTC)
- Merged main incl. #1706 (single kit pass, profile titles word for word): kept #1706's step 3a and added
  `--known-ct`, kit-first, no wait over 2 minutes, `kit.done`. This PR's own recipe asserts follow 25 minutes.
- Green at 09:40; the merge cage never posted a status, so it sat "blocked" until 10:47. That was safe: the
  10:11 run was not due (ended 10:12, $0.38).
- Next: score the 11:11 run (first with #1665: is `kit.done` reported, `employer_searched` used, any 422s,
  cost, minutes). Only then open coverage A.
