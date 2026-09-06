<!-- doc: LIVING | last-verified: 2026-08-24 by /sync -->
# Contributing to Job360

Thanks for helping improve Job360. This guide covers the conventions you need to
ship a change.

## Branch naming

Branch off `main`. Use one of these prefixes:

| Prefix       | Use for                                             |
|--------------|-----------------------------------------------------|
| `feature/*`  | New user-facing behaviour or new source / channel   |
| `fix/*`      | Bug fix with a reproducer test                      |
| `docs/*`     | README / CLAUDE.md / docs-only changes              |
| `refactor/*` | Internal restructure, no behaviour change           |
| `test/*`     | Test-only changes (new coverage, flake fixes)       |
| `chore/*`    | Tooling, deps, CI, config                           |

Example: `feat/application-export`, `fix/receipt-null-channel`.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) with an
imperative subject line (no trailing period, max 72 chars):

```
feat(spine): record_event accepts a recorded_by label
fix(receipts): keep channel NULL when the agent sends none
docs(backend): document FRONTEND_ORIGIN + NEXT_PUBLIC_API_URL wiring
refactor(profile): split keyword_generator tier logic into helpers
test(api): cover /api/profile IDOR regression
chore(deps): bump ruff to 0.6.x
```

Scope is optional. Body (blank line, wrap at 72) explains the *why*.

## Pull request flow

1. Branch off the latest `main`. Pull first.
2. **Write the test first.** TDD is the default; every bug fix needs a
   reproducer, every new behaviour needs a unit test. See
   [`docs/README.md`](docs/README.md) for the docs index.
3. Implement until tests pass.
4. Run the full suite locally from the `backend/` directory:
   ```bash
   cd backend && python -m pytest -q -p no:randomly
   ```
5. Run formatters + linters:
   ```bash
   pre-commit run --all-files
   ```
6. Commit with a conventional message (see above). Use one logical commit per
   concern; avoid mixing refactors and features.
7. Open the PR against `main`. There is **no PR template in this repo** — nothing under
   `.github/` provides one — so write the body yourself: what changed, why, what you ran
   to verify it, and the linked issue.
8. Request review. Do not self-merge unless explicitly authorised.

## ⚠️ Merging to `main` deploys to production

Railway is GitHub-linked to this repo on `main`. **A merge is a release** — it goes
straight to real users at **job360.uk**, with no manual step and no staging
environment. There is nowhere to catch a bad merge after the fact.

So: **never merge to tidy up the PR list.** Land a branch because the change is
ready to be live, or leave it open.

Check what is actually deployed:

```bash
railway deployment list --service backend --json   # meta.commitHash, meta.branch
```

`/api/health` cannot tell you — it returns a hardcoded `"version": "1.0.0"` with no
commit SHA.

If a merge does break production, the fastest recovery is Railway → Deployments →
redeploy the previous SUCCESS build, then fix forward on a branch.

## Test-before-merge gate

**Invariant baseline: 3,297 collected / 3,295 selected (2 `live` deselected), 0 failing.**

A PR is mergeable only when:

- `cd backend && python -m pytest -q -p no:randomly` reports **0 failing** and
  **>= 3,297 collected**. (The suite expands with every new source / feature;
  the floor only moves up — Step-0 baseline was 600, Step-3 close-out ~1,409,
  and it stands at 3,297 as of 2026-08-24. Measure it, never quote it.)
- `pre-commit run --all-files` is clean.
- CI is green on the PR branch.
- At least one reviewer has approved (or owner self-approval on
  trivial `docs/*` / `chore/*`).

If the suite was green before your change and is red after, your change is the
regression — fix it, do not merge around it.

## Local setup

- **Unix / macOS:** `bash setup.sh`
- **Windows:** `setup.bat`

Both create a venv, install backend deps, and validate `.env`. See
[`backend/README.md`](backend/README.md) and
[`frontend/README.md`](frontend/README.md) for service-specific run
instructions.

## Where scripts live

Two `scripts/` directories exist by design:

- **`scripts/`** at repo root — repo-wide tooling that must not import from
  `backend/src/`: **28 Python files + 4 shell files** today, not two shell
  scripts. Most of it is the CI/harness guard estate (`doc_sync_check.py`,
  `doc_sync_mutation_test.py`, `merge_cage.py`, `ruleset_gate.py`, …), which
  `.github/workflows/` runs directly; the Makefile shells out to
  `migration_roundtrip.sh`, and `agent-gate.sh` is the commit gate. Add new
  cross-service tooling here, in **either** language. (Measure it, never quote
  it: `ls scripts/*.py | wc -l` / `ls scripts/*.sh | wc -l`.)
- **`backend/scripts/`** — backend-only Python helpers, run via
  `cd backend && python scripts/X.py`. Add ESCO-index builders, dev
  bootstrappers, verification scripts, dump/inspection tools, and any
  Python that imports from `src/` here. The ruff override
  `scripts/* = [...]` in `backend/pyproject.toml` covers this folder.

If a script is a one-shot phase migrator (touches the tree, run-once,
then dead), delete it once its migration has run rather than leaving it in
either live `scripts/` directory — git history is the record. There is no
archive directory for one-shot scripts; the two doc-archive directories this
repo once had were both removed 2026-09-05.

## Architecture + rules

Read [`CLAUDE.md`](CLAUDE.md) at repo root before your first non-trivial change.
It points at the hard rules (`.claude/skills/hard-rules/SKILL.md`: no `user_id`
on `jobs`, no lazy-breaking heavy imports, append-only application history,
MCP/route gate parity, timezone-aware quiet-hours dispatch, account-mgmt session
invalidation, etc.) plus the product path and data-flow.

For docs and plans, start at [`docs/README.md`](docs/README.md).
