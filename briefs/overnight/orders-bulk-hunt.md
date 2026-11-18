# Orders for Bulk Hunt test (from Kits, 23:10 UTC)

- **The owner already replaced the task prompt at 22:36 UTC.** It no longer passes `--max-years` or
  `--exclude-title-words`. It now reads: "... Read my profile's search plan first: target roles, level,
  every country and region, on-site, remote and contract. Run the hunt kit with --every-title so it keeps
  every title. You read the titles and decide which fit my profile; no word list drops a title. Then read
  the kept ads in full, judge each, and bring 10 new jobs." Remove your "Needs the owner" prompt request.
  The 23:11 run is the first with it.
- **The 22:11 run lapsed**: `failures_in_row.hunt` is 1 at 23:04 and `last_done_at.hunt` is still 20:54.
  It never recorded `mark_done`. Find why (the kit's 25-minute cap plus a resume, then the claim ran out?)
  and fix it: a hunt run must always record its outcome.
- **Do not hold PRs as drafts all night.** The no-merge window is only :06 to :21 for your task. Open
  #1665's follow-ups (coverage A and B) as soon as #1665 is merged, one at a time, so each real run tests
  one change.
- **Volume check on the 23:11 run**: with `--every-title` the kit hands over about 10 times more rows
  (Kits measured 10,300 rows and 7,593 distinct titles in 3 minutes, against 1,057 and 514). Report how
  many titles the run saw and judged, and how long it took. If it chokes, the fix is to judge distinct
  titles once; build that only if the run shows the need.
- You wrote that transcripts are not readable from your session. Score from Job360 events as you are
  doing; Kits reads the transcript and will add the kit command line and counts to `kits.md`.
