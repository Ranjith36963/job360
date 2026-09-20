---
name: model-delegation
description: Use at the start of EVERY task (owner rule, 2026-09-20) — decide which model does which part before doing any of it; the big model manages and reviews, cheaper models implement, test and verify in parallel.
---

# Model delegation (owner rule — every prompt, not just big ones)

The owner said it three times. The lead model (Fable / Opus) is the manager.
It does not type the repetitive middle. It plans, writes the contract, and
reviews. Everything else goes down to the cheapest model that can do it
right the first time.

## Step 0 — before any tool call, split the task

Write one line per part with the model beside it. Then dispatch in ONE
message so they run in parallel. Name `model:` on every `Agent` call.

| Part of the work | Model | Why |
|---|---|---|
| Design call, product judgement, security, root cause of a subtle bug, final review of a worker's diff | **Fable / Opus** | wrong answer is expensive |
| Implement from a written contract (component, route, migration, test file), update specs, fix what a test says | **Sonnet** | tightly specified, parallelisable |
| Grep / rename sweeps, log trawling, reading many files for one fact, copying a pattern to N places, running a check list | **Haiku** | clerical |
| Research a library / spec (Context7, web) and report the API with sources | **Sonnet** (Explore) | cheap, verifiable |
| Verify in the browser (Playwright / Claude in Chrome) against a checklist | **Sonnet** | mechanical, needs the checklist written by the lead |

Rule of thumb: if the lead model would be *typing* for more than a few
minutes, it should have been a worker.

## The contract a worker gets

- The exact files it may touch and the ones it must not (`api-types.ts`,
  `openapi.json`, docs).
- Every `data-testid`, heading, message, setting name it must use.
- The commands to run and that it must paste the pass/fail lines verbatim.
- "Do not run git." The lead commits after review.
- Worktree: a worker that could clash with another worker's files gets
  `isolation: "worktree"`.

## The review the lead does

- Read the worker's diff in full (`git diff`), not its summary.
- Re-run the one check that matters (the test the contract named).
- Look for what the contract did not say and the worker guessed.

## Parallel by default

Independent parts go out in the same message. Two workers on two files beat
one worker twice. A worker that "waits for a background task" is stuck:
send it one message to finish in the foreground, else take over.

## Anti-patterns the owner has called out

- Lead model writing frontend components, test fixtures, spec updates.
- Lead model running e2e loops and fixing selectors.
- One worker doing three unrelated parts in sequence.
- Skipping the split because "it is small" — the split takes one line.
