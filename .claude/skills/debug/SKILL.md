# /debug — 3-Step Bug Fix

Triggered by: `/debug <error message or description>`

You are fixing a bug. Follow these three steps IN ORDER. Do NOT skip steps. Do NOT jump to implementing a fix before completing steps 1 and 2.

---

## Step 1: Reproduce

Figure out how to reproduce this error.

- Read the relevant source files to understand the code path
- Write or identify a minimal test/command that triggers the exact error
- Run it and confirm you see the same error
- If it's a UI issue, use Playwright MCP if available
- **Output**: Show the reproduction command/test and the exact error output
- **Do NOT propose any fix yet**

---

## Step 2: Diagnose & Propose

Think about ALL the possible reasons why we're getting this error, then propose two solutions and recommend the best one.

- List every possible root cause (not just the obvious one)
- If the bug involves a library/framework, use **Context7 MCP** to check latest docs — the fix might be an API change you're not aware of
- For each cause, explain why it could produce this exact error
- Propose **two** distinct solutions with trade-offs
- Recommend the best one and explain why
- **Wait for user confirmation before proceeding**

---

## Step 3: Fix & Verify

Implement the approved solution and add tests so this bug never happens again.

- Implement the fix (follow the Read → Write → Verify workflow from CLAUDE.md)
- Write a test that would have caught this bug — the test should FAIL without the fix and PASS with it
- Run the full relevant test suite to confirm no regressions
- Update any affected MD files if facts changed
- **Output**: Summary of what changed, which files, and test results
