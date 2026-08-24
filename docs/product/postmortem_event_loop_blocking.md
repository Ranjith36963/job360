# Post-mortem: the server froze during every search (event-loop blocking, round 2)

**Date:** 2026-08-10
**Bug:** `backfill_feed_from_catalog()` in `backend/src/services/rescore.py` scored up to 50,000 catalog rows in a plain loop, on the event loop, with no `await` and no `asyncio.to_thread`. It ran on every search. While it ran, the whole server was frozen — every user, every request, including the status poll the UI needs.
**The bad part:** we already fixed this exact class of bug once (PR #123, `main.py`). It came back anyway.

---

## 1. Why the tests didn't catch it

The code that reintroduced the bug shipped WITH passing tests. Here is exactly why they lied:

- **The tests seeded ~5 rows.** Scoring 5 rows takes microseconds. The freeze only exists at real data sizes (thousands of rows). Small test data makes blocking invisible. Green means "the answer was right", not "the server stayed alive".
- **Correctness tests check WHAT the code returns, never WHERE it runs.** "On the loop thread" and "in a worker thread" produce identical results. No assertion could fail.
- **Our test suite makes it worse:** `conftest.py` patches `asyncio.sleep` to be instant. Even a timing-based test would have been blind.

So the review question "do the tests pass?" was the wrong question. They pass on both the broken and the fixed code.

## 2. Why the first fix didn't prevent the second bug

PR #123 fixed one call site (`_score_dedup_and_filter` in `main.py`) and left behind:

- a `to_thread` call at that one spot,
- a test pinned to that one spot (`test_main.py:274`),
- and a comment.

That is a **point guard**. It protects the two lines that already broke and nothing else. `rescore.py` was new code — the guard never saw it. Comments and site-specific tests fail as guards for one simple reason: **the natural way to write the line is the blocking way.** `for row in rows: score(row)` looks completely normal. Nothing red-flags it. A rule that depends on the author remembering to opt in loses every time, because forgetting IS the bug. A test file for upload blocking even wrote this down in its own docstring: "it will keep coming back — the natural way to write the line is the blocking way." We wrote the warning and then did it again.

## 3. The bug class, named

**"CPU-heavy sync work called directly inside async code."** Not "the rescore bug". Any regex loop, PDF render, big parse, or dedup pass called from an `async def` without `asyncio.to_thread` freezes every user at once, because FastAPI runs everyone on one thread. We have now found it in: search scoring (PR #123), catalog backfill (this bug), enrichment parsing, PDF/DOCX rendering in the tailor download route, and two CV/LinkedIn parsers. Six instances. One class.

## 4. What now makes it (nearly) impossible

**The guard moved from the caller to the callee.** New file: `backend/src/utils/loop_guard.py`.

- Every known CPU-heavy function is tagged `@cpu_bound`. The tag checks, at run time, which thread it is on. On the event loop thread → it raises (`LoopBlockError`) in tests and dev, and logs an error + Sentry in prod (so users get a slow request, not a crashed one).
- The check does not depend on timing or data size. **5 rows or 50,000 — same red test.** This is the property the old guards lacked. If this guard had existed, the rescore bug's own 5-row tests would have failed on their first run.
- It already paid for itself: turning it on found three MORE shipped instances of the same bug (tailor download PDF render, CV parser, LinkedIn parser) that nobody knew about.
- Backstop for what nobody tagged: a production watchdog samples loop lag every 100 ms and fires an error + Sentry if the loop goes quiet for >500 ms. Detection, not prevention — but it covers the blind spot.

**Honest limits.** The guard only protects functions somebody tagged. It cannot see untagged blocking code (that's the watchdog's job), sync sockets, `time.sleep`, blocking DB calls, or death-by-a-thousand-small-callbacks. And `to_thread` restores responsiveness, not raw speed — if the catalog grows enough to saturate a CPU core, a process pool is a separate, future decision.

## 5. The rule

**Any sync function that loops over rows, parses documents, renders files, or runs heavy regex must be tagged `@cpu_bound` and called via `asyncio.to_thread` — at the moment it is written, not after it freezes prod.** In review, one question: "is there a loop or a parse inside async code? Then where's the tag?"

And the meta-rule this incident teaches: **when a bug class bites twice, the fix is not another fix — it is a guard that fires automatically on every future instance, regardless of data size, or the class will bite a third time.**
