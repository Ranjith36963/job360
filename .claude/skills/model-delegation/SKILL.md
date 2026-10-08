---
name: model-delegation
description: Use at the start of EVERY task (owner rule, 2026-09-20, sharpened 2026-10-08) — decide which model does which part before doing any of it. Fable 5.1 decides, Opus 5.5 does the hard parts, Sonnet 5.5 does most of the work, Haiku reads logs and does clerical sweeps. Name the model on every dispatch.
---

<!-- doc: LIVING | last-verified: 2026-10-08 by the owner rule (Fable decides, Opus hard, Sonnet most, Haiku logs) -->

# Model delegation (owner rule — every prompt, STRICT for the whole build)

The owner said it four times. The lead model is the manager: it plans,
writes the contract, dispatches, and reviews. It does not type the
repetitive middle. Every part goes to the right model on the first try.

## The roster (owner, 2026-10-08)

| Model | `model:` value | Use it for | Never for |
|---|---|---|---|
| **Fable 5.1** | `fable` | **Decisions**: design calls, product judgement, picking between options, chairing a debate, final sign-off of a slice. | Typing code, greps, polling, log reading (expensive). |
| **Opus 5.5** | `opus` | **Hard tasks**: schema and migrations, auth and MCP gates, security, the application spine, root cause of a subtle bug, backend contracts, the bug + security review of every diff. | Mechanical edits, frontend boilerplate. |
| **Sonnet 5.5** | `sonnet` | **Most of the work — use it a lot**: implement from a written contract (routes, components, tests, recipes, docs), browser verification against a checklist, library or web research with sources. | Design calls, security sign-off. |
| **Haiku 4.5** | `haiku` | **Reading and clerical**: Railway / Sentry / CI logs, grep and rename sweeps, counting, polling a check until it finishes, copying a pattern to N places. | Anything needing judgement. |

There is no Haiku 5.5. `haiku` resolves to the newest Haiku (4.5); recheck the model list before claiming a newer one exists.

## Step 0 — before any tool call, split the task

Write one line per part with the model beside it, then dispatch the
independent parts in ONE message so they run in parallel. Name `model:` on
every `Agent` call and every `agent()` in a Workflow; never inherit by
accident.

## The build map (S0–S7 in the plan of record)

| Work | Model |
|---|---|
| Decide anything left open, sign off each slice | Fable |
| Backend contract + data model + migration + MCP/route gates | Opus |
| Implement the contract (backend code, tests, recipes) | Sonnet |
| Frontend pages, components, unit tests | Sonnet |
| Bug review + security review of the diff | Opus |
| Real-browser verification of the slice | Sonnet |
| CI logs, Railway deploy logs, Sentry trawl, gate polling | Haiku |
| Logging audit (find files with no logs) | Haiku finds → Sonnet adds logs → Opus reviews |

## The contract a worker gets

- The exact files it may touch and the ones it must not (`api-types.ts`,
  `openapi.json`, docs unless named).
- Every `data-testid`, heading, message and setting name it must use.
- The commands to run, with the pass/fail lines pasted verbatim.
- Logging: every new route, tool, job and page logs what happened, for whom
  (ids, never secrets or personal values), when, and whether it failed.
- "Do not run git." The lead commits after review.
- A worker that could clash with another worker's files gets
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
- Lead model running e2e loops, fixing selectors, or polling CI.
- Fable used for typing, greps or log reading.
- One worker doing three unrelated parts in sequence.
- Skipping the split because "it is small" — the split takes one line.
