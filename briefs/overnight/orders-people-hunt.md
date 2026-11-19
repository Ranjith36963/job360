# Orders for People hunt test (from Kits, 01:10 UTC; replaces the 23:10 orders)

- #1688 closed the gap from #1682. Thank you for the correction and the test.
- The 22:56 failure reason: Kits has no Railway access in this session either. Do not wait for it. Make the run's
  own `mark_done` note carry the reason in plain words, and treat "one free tool down" as partial, never failed
  (your `_ONE_TOOL_DOWN` idea). A failed mark backs the duty off for two hours, which cost a whole hour tonight.
- reach is due now for 1094 to 1098 (the ten jobs the 00:11 hunt brought). Score the 00:56 and 01:56 runs.
- Hold a PR with a draft, not by turning auto-merge off.
