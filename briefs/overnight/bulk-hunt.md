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
