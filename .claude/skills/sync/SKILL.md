# /sync — Code ↔ Documentation Sync
<!-- doc: LIVING -->

Triggered by: `/sync` or `/sync <specific file or area>`

You are checking that the codebase and documentation reflect the same information. Follow these four steps IN ORDER.

---

## Step 1: Scan the Codebase for Real Facts

Read the actual code and extract current facts. **The mission is `docs/product/VISION.md` — a doc that still tells a session to build search, ranking or a feed is a mismatch even if its numbers are right.** Check ALL of these:

- **Product path**: which routes exist in `backend/src/api/routes/bring.py`, `receipts.py`, `tailor.py`; which tools `backend/src/api/mcp_server.py` registers (count them); which Railway services exist (three: backend, frontend, Postgres — worker + Redis deleted 2026-09-02)
- **Roadmap state**: which slices in `docs/plans/2026-09-03-mission-roadmap.md` have merged (check the issue #479–#483 state with `gh issue view`)
- **Test count**: Run (from `backend/`) `python -m pytest tests/ --collect-only -q 2>&1 | tail -3` to get exact test count
- **DB schema**: Read `backend/src/repositories/database.py` + `backend/migrations/` for table definitions, column names, UNIQUE constraints, indexes — the migrations are the head, the baseline is not
- **Features**: Check what modules exist under `backend/src/services/` and `backend/src/api/` — what's actually implemented
- **Commands**: Read `backend/src/cli.py` for actual CLI commands and flags
- **Dependencies**: Read `backend/pyproject.toml` for actual packages

**Output**: A bullet list of every fact extracted from the code.

---

## Step 2: Compare Against Documentation

Read each MD file and flag every mismatch:

- `CLAUDE.md` — mission block points at VISION.md + roadmap; product path vs legacy path split; commands; test count; three services
- `backend/CLAUDE.md` — thin backend pointer; product-path module lines
- `ARCHITECTURE.md` — deep system description, module relationships, data flow
- `STATUS.md` — what's done, what's in progress, what's next
- `README.md` — quickstart, features overview, usage examples

**Output**: A table showing each mismatch:
```
| File | What's Wrong | Code Says | Doc Says |
```

If no mismatches found, say so and stop.

---

## Step 3: Fix All Mismatches (on a docs branch, never main)

**Before editing anything:** create a branch `docs/sync-<YYYYMMDD>-<HHMM>`
off a freshly-fetched `origin/main` (the time suffix prevents collisions when
parallel sessions run the same day). All edits happen there.

For each mismatch found in Step 2:

- Read the MD file
- Edit ONLY the stale facts — do not rewrite sections that are correct
- Keep the same structure and tone of the existing document

**Then stamp every LIVING doc you verified** (even ones needing no fix):
add or update `<!-- doc: LIVING | last-verified: YYYY-MM-DD by /sync -->` near
the top of each file. **Do not type the list from memory — measure it**:
`grep -rl "doc: LIVING" --include="*.md" .` is the real surface, and
`scripts/doc_sync_check.py` reports its size on every run. Stamping a
hardcoded subset leaves the rest to go red on freshness.

**If a doc claims something the code does NOT do** (code is behind the doc —
an "AHEAD" doc): **leave that doc completely untouched** (user's rule —
promises are backlog, not bugs). Record the gap in
`docs/harness/maintenance/PARKED.md` only (doc, claim, evidence, date) and mention it
in the PR body so the user sees it. Never edit, move, or annotate the source
doc itself.

---

## Step 4: Report + PR

Rebase the docs branch on `origin/main` immediately before pushing (another
session may have merged meanwhile). Push and open ONE docs-only PR titled
`docs: sync <date>`. Never commit doc fixes to main directly.

Show a summary of what was updated:

- Number of files changed
- Number of facts corrected
- List each correction (one line each)
- Run `python -m pytest tests/ -q 2>&1 | tail -5` as a final sanity check (doc edits should not affect test outcomes — any failure means either the suite was already broken or a non-doc file was accidentally edited)
