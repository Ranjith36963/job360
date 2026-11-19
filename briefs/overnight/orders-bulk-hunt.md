# Orders for Bulk Hunt test (from Kits, 01:10 UTC; replaces the 23:10 orders)

You have not reported since 23:30. If you are alive, write a line in your report file now.

The 00:11 run brought **10 of 10** on the owner's new prompt. Kits scored it about 80%. To reach 90% and keep it:

1. **Volume (the real problem now).** `--every-title` returned about 101,000 new ads. The run fell back to judging only
   the 728 titles that matched a target phrase. Build the smallest thing that lets Claude judge them all:
   - the kit writes a file of **distinct titles** with counts (one line per distinct title, not per ad), and fetches no
     ad text in that pass;
   - helpers on the cheapest model label the distinct titles in batches against the profile (keep or drop);
   - the kit then fetches full text only for the kept titles.
   This is the two-pass shape Kits deferred from #1675. Build it now; the run showed the need.
2. **Cost.** The run cost about $9.64 and took 41 minutes. Report the cost and time of each run. The two-pass shape
   should cut both. Do not add a title memory across runs yet.
3. **Read the whole ad.** Helpers read only the first 3,000 characters, and four of the ten jobs were judged on
   snippets. A job is brought only after its full ad is read.
4. **Employer page.** Most of the ten link to job boards. #1665's `employer_searched` proof covers this once merged.
5. **Coverage.** The run passed visa-needing countries as no-visa countries to widen the kit. Make that the real
   behaviour (your coverage slice B), so the run does not need a workaround, and report which plan rows were searched.
6. Do not hold PRs as drafts all night. Your no-merge window is :06 to :21 only.
