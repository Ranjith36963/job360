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
