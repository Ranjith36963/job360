# Permanent Fix Engineering Policy (loads every session)
<!-- doc: LIVING | last-verified: 2026-11-09 by the owner ("every fix permanent, not a patch") -->

Owner rule, 2026-11-09: it applies to every bug in every area (backend, frontend, recipes, harness, docs).

For every bug, regression, or unexpected behaviour:

1. Never guess or immediately patch symptoms.
2. Reproduce the exact failure before modifying code.
3. Trace the failure to its original source.
4. Identify why existing safeguards failed.
5. Write a regression test that fails on the original bug.
6. Implement the smallest architectural fix that addresses the root cause.
7. Add defence-in-depth safeguards where appropriate.
8. Run relevant unit, integration and end-to-end tests.
9. Check for regressions in connected components.
10. Remove temporary debugging code and instrumentation.
11. Document the root cause and prevention mechanism.

Do not mark an issue resolved just because the error disappears.

A permanent fix requires:
- Confirmed root cause.
- Regression test or documented testing limitation.
- Verified fix.
- Prevention against recurrence.
- Evidence of successful verification.

If the same issue returns, investigate why the previous prevention mechanism failed. Do not repeat the previous fix.

Also (owner, same day):
- **Evidence first.** Diagnose from real logs, transcripts, reports and production reads (read-only), never from memory or the code alone. The `diagnose` skill is the procedure.
- **Blast radius.** Before changing code, list every caller and connected component the change touches (routes, MCP tools, recipes, frontend, scheduled runs) and run their tests.
- **Logs.** Every fix leaves a log line that would have shown the failure (ids and reason codes, never secrets or personal values).
- **PR body.** Every fix PR states: Root cause (with file:line) / Why safeguards failed / Prevention / Proof (the regression test failing before, passing after).
