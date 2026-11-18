# Gmail check test — overnight report

Owner of this file: the Gmail check test session. Task: "Job360 Gmail check (hourly, Haiku)", starts at :26.
Target: every mail since the last check is read; each job mail is matched or parked; the search covers every
open application. Scores come from the run's transcript (reads only).

## 22:26 run  (score 92%)
- Achieved: claim with duties=["inbox"]; ONE date search (after:2026/11/17), both pages read (51 threads,
  pageToken followed); 6 mails opened; 5 job mails (DoiT, Halimah S. recruiter, Amey, Placed, BNY), each
  matched with match_email then parked (1 new: Amey; 4 already parked, no duplicates); cursor moved for all
  1012 open applications in ONE call, no error; mark_done done after the cursor; one action per parked mail.
- Missed: (1) the Halimah S. recruiter asks whether the owner wants to stay under consideration: the recipe
  says a reply that needs an answer gets a Gmail DRAFT; the run wrote "reply, no draft was made". (2) The
  summary was not the exact 3-line form (prose plus a checklist first).
- Biggest cause: none blocks the target. The next one is audit item 4.1 seen live: the DoiT mail came via
  Greenhouse (sender greenhouse-mail.io) and match_email offered app 1047 (Brunswick Group, also on
  Greenhouse). The run read the body, saw DoiT, and parked it: correct, but the recipe gives Claude no way to
  pick a candidate the BODY names when the sender is a shared ATS.
- Fix: working audit item 4.1 next (recipe: let Claude pick the one application the body names, quote the line).
- Decision taken without the owner: none.
- Coverage: the search is date-only, so it lists all mail; search.application_ids = every open application
  (audit 18 Nov, PR #1670). ATS senders now include Breezy, Keka, HiBob, Darwinbox, Oracle, SuccessFactors,
  Pinpoint, Phenom, Umantis (#1670, merged 19:17).
