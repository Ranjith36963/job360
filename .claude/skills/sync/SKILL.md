# /sync — Code ↔ Documentation Sync

Triggered by: `/sync` or `/sync <specific file or area>`

You are checking that the codebase and documentation reflect the same information. Follow these four steps IN ORDER.

---

## Step 1: Scan the Codebase for Real Facts

Read the actual code and extract current facts. Check ALL of these:

- **Source count**: Count classes in `src/sources/` that extend `BaseJobSource`, count entries in `SOURCE_REGISTRY` in `src/main.py`, count entries in `_build_sources()`
- **Test count**: Run `python -m pytest tests/ --collect-only -q 2>&1 | tail -3` to get exact test count
- **Scoring rules**: Read `src/filters/skill_matcher.py` for actual dimensions, weights, penalties, threshold
- **DB schema**: Read `src/storage/database.py` for `SCHEMA_VERSION`, table definitions, column names
- **Features**: Check what modules exist in `src/filters/`, `src/profile/`, `src/pipeline/` — what's actually implemented
- **Commands**: Read `src/cli.py` for actual CLI commands and flags
- **Dependencies**: Read `requirements.txt` for actual packages

**Output**: A bullet list of every fact extracted from the code.

---

## Step 2: Compare Against Documentation

Read each MD file and flag every mismatch:

- `CLAUDE.md` — project overview, commands, architecture, scoring, source count, test count, core rules
- `ARCHITECTURE.md` — deep system description, module relationships, data flow
- `STATUS.md` — what's done, what's in progress, what's next
- `TESTING.md` — test patterns, fixture names, test count
- `SOURCES.md` — source list, categories, templates
- `RULES.md` — invariant rules
- `CHANGELOG.md` — version history accuracy

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
- Run `python scripts/validate_rules.py` as a final sanity check
