# /sync — Code ↔ Documentation Sync

Triggered by: `/sync` or `/sync <specific file or area>`

You are checking that the codebase and documentation reflect the same information. Follow these four steps IN ORDER.

---

## Step 1: Scan the Codebase for Real Facts

Read the actual code and extract current facts. Check ALL of these:

- **Source count**: Count classes in `backend/src/sources/` that extend `BaseJobSource`, count entries in `SOURCE_REGISTRY` in `backend/src/main.py`, count entries in `_build_sources()` (note: `SOURCE_INSTANCE_COUNT` in `main.py` is the unique-instance count — one less than the registry size, because `indeed` and `glassdoor` both map to `JobSpySource`)
- **Test count**: Run (from `backend/`) `python -m pytest tests/ --collect-only -q 2>&1 | tail -3` to get exact test count
- **Scoring rules**: Read `backend/src/services/skill_matcher.py` for actual dimensions, weights, penalties, threshold
- **DB schema**: Read `backend/src/repositories/database.py` for table definitions (jobs, run_log, user_actions, applications), column names, UNIQUE constraints, indexes
- **Features**: Check what modules exist in `backend/src/services/`, `backend/src/services/profile/`, `backend/src/services/notifications/`, `backend/src/api/` — what's actually implemented
- **Commands**: Read `backend/src/cli.py` for actual CLI commands and flags
- **Dependencies**: Read `backend/pyproject.toml` for actual packages

**Output**: A bullet list of every fact extracted from the code.

---

## Step 2: Compare Against Documentation

Read each MD file and flag every mismatch:

- `CLAUDE.md` — project overview, commands, architecture, scoring, source count, test count, core rules
- `backend/CLAUDE.md` — thin backend pointer; check its `SOURCE_REGISTRY (N)` count + module-path lines
- `ARCHITECTURE.md` — deep system description, module relationships, data flow
- `STATUS.md` — what's done, what's in progress, what's next
- `README.md` — quickstart, features overview, usage examples

**Output**: A table showing each mismatch:
```
| File | What's Wrong | Code Says | Doc Says |
```

If no mismatches found, say so and stop.

---

## Step 3: Fix All Mismatches

For each mismatch found in Step 2:

- Read the MD file
- Edit ONLY the stale facts — do not rewrite sections that are correct
- Keep the same structure and tone of the existing document

---

## Step 4: Report

Show a summary of what was updated:

- Number of files changed
- Number of facts corrected
- List each correction (one line each)
- Run `python -m pytest tests/ -q 2>&1 | tail -5` as a final sanity check (doc edits should not affect test outcomes — any failure means either the suite was already broken or a non-doc file was accidentally edited)
