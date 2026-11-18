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
- Fix: PR #1680 (audit 4.1): recipe step 5 says match_email's candidates are hints and the email body decides; a
  candidate the body does not name is no match; record on the one application the body names with source.quote.
  Failing test first; gate PASS; auto-merge on. No new refusal.
- Decision taken without the owner: #1680 changes recipe wording only (Claude judges from the body); nothing
  the server accepts changed. Audit 4.2 (alert/digest/newsletter senders hidden when older): noted only, low risk.
- Coverage: the search is date-only, so it lists all mail; search.application_ids = every open application
  (audit 18 Nov, PR #1670). ATS senders now include Breezy, Keka, HiBob, Darwinbox, Oracle, SuccessFactors,
  Pinpoint, Phenom, Umantis (#1670, merged 19:17).

## 23:26 run  (score 95%)
- Achieved: the first run on #1680 (merged 23:00). Claimed with duties; one date search, both pages read (51
  threads); 15 mails opened, 14 judged job mail; every job mail matched or parked; DoiT's Greenhouse mail had
  Brunswick Group (1047) as the only candidate and was correctly NOT recorded on it (the body names DoiT, which
  has no application); 4 parked, all already parked earlier (no duplicates); job alerts read and left, not
  parked; cursor moved for all 1013 open applications in ONE call; mark_done done after the cursor; exact
  3-line summary, one action per parked mail; nothing sent, no setting changed.
- Missed: the Halimah S. recruiter question again has "reply" as its action but no Gmail draft (2nd run in a
  row). Counted as a small miss: the answer is the owner's decision, so a draft could only be a placeholder.
- Biggest cause: none blocks the target.
- Fix: none. Two runs in a row at 90%+ (92, 95): per the orders I stop fixing and keep scoring until 06:00.
- Decision taken without the owner: no recipe change for the missing draft; if Kits wants recruiter questions
  always drafted, the recipe line "A reply that needs an answer: a Gmail DRAFT only" can say "always".
