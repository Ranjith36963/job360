# /implement — Feature Implementation

Triggered by: `/implement <what to add or change>`

You are adding a new feature or capability to the codebase. Follow these four steps IN ORDER. Do NOT skip steps. Do NOT write any code until Step 3.

---

## Step 1: Explore

Read and understand the relevant area of the codebase before touching anything.

- Identify which files and modules are involved
- Read each one — understand the current structure, data flow, and patterns
- Check how similar features were implemented (look for precedent)
- If the feature involves any library/framework (Streamlit, aiohttp, Click, etc.), use **Context7 MCP** to fetch latest docs — never code against outdated APIs
- Check tests — what's currently tested in this area
- **Output**: Brief summary of what exists and how the codebase works in this area
- **Do NOT propose a solution yet**

---

## Step 2: Plan

Figure out where this feature fits and choose the best approach.

- Determine exactly which files need to change and what changes are needed
- Consider: will this break any existing functionality? Any edge cases?
- If there are multiple valid approaches, pick the best one and explain why
- List the files to modify/create in order
- **Output**: A short plan — what changes, where, and why this approach
- **Proceed to Step 3 unless the change is large or risky — in that case, wait for user confirmation**

---

## Step 3: Implement

Write the code following the plan from Step 2.

- Follow existing patterns and conventions in the codebase
- Make the minimum changes needed — do not refactor unrelated code
- If creating new files, follow the same structure as existing similar files
- If modifying existing files, read them fully first (CLAUDE.md workflow rule)

---

## Step 4: Test & Verify

Confirm everything works and nothing is broken.

- Run the relevant test file(s) first: `python -m pytest tests/<relevant_test>.py -v`
- If you added new functionality, add tests for it
- Run the full test suite: `python -m pytest tests/ -v`
- If any test fails, fix the implementation (not the test)
- Update affected MD files if facts changed (source count, test count, features, architecture)
- **Output**: Test results and summary of what was implemented
