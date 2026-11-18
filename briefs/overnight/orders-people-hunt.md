# Orders for People hunt test (from Kits, 23:10 UTC)

## #1682: auto-merge paused by Kits. One change needed, then re-arm it yourself.

The idea is right (facts about the recipient are not claims about the user). The way it picks what to
judge is not safe yet:

- `user_claims()` keeps only sentences with a first-person word, and `claims = user_claims(body) or body`
  then judges only those. So in a line like "I'm an AI engineer. Built systems serving 1M users.", the
  second sentence is a claim about the user and is no longer checked against the profile. An invented
  fact could be sent to a real person. That breaks the rule "never write a claim the profile does not
  contain".
- It is also a word list selecting what gets judged, which the owner ruled out (a pattern may hint,
  never select).

Fix it this way:

1. Judge every sentence of every line against the profile, as before, **except** a sentence the fresh
   reviewer explicitly marks as about the recipient, with its source page (`field_path` "page:..." and
   the quote from that page).
2. The reviewer decides which sentences are about the recipient. The first-person check may stay only
   as a hint in the reply ("this sentence has no I/my: is it about the recipient?").
3. A recipient-marked sentence still needs its page quote, so a made-up compliment fails too.
4. Add a test: a two-sentence line where the second sentence is an unsupported claim about the user
   with no first-person word must still store weak.

Then turn auto-merge back on. Kits will re-read the diff at the next check.

## Also
- `failures_in_row` at 23:04: reach 1, network 1. A claim lapsed after 22:57. Find the run and the cause.
- Your "next cause queued" (the run works one company and stops) is the right next fix.
