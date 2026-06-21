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

Example: `feature/adzuna-hourly-tier`, `fix/scorer-visa-negation`.

## Commit messages

Follow [Conventional Commits](https://www.conventionalcommits.org/) with an
imperative subject line (no trailing period, max 72 chars):

```
feat(sources): add Comeet ATS source with 5-slug catalog
fix(scorer): do not penalise remote-friendly visa phrasing
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
7. Open the PR against `main`. Fill the template. Link the issue.
8. Request review. Do not self-merge unless explicitly authorised.

## Test-before-merge gate

**Invariant baseline: ~1,409 collected (2 live deselected), 0 failing.**

A PR is mergeable only when:

- `cd backend && python -m pytest -q -p no:randomly` reports **0 failing** and
  **>= ~1,409 collected**. (The suite expands with every new source / feature;
  the floor only moves up — Step-0 baseline was 600; Step-3 close-out at
  ~1,409.)
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

- **`scripts/`** at repo root — repo-wide shell helpers the Makefile shells
  out to (currently `migration_roundtrip.sh`, `review_batch.sh`). Add new
  shell or cross-service tooling here.
- **`backend/scripts/`** — backend-only Python helpers, run via
  `cd backend && python scripts/X.py`. Add ESCO-index builders, dev
  bootstrappers, verification scripts, dump/inspection tools, and any
  Python that imports from `src/` here. The ruff override
  `scripts/* = [...]` in `backend/pyproject.toml` covers this folder.

If a script is a one-shot phase migrator (touches the tree, run-once,
then dead), drop it under `docs/_archive/one-shot-scripts/` instead of
either live `scripts/` directory.

## Architecture + rules

Read [`CLAUDE.md`](CLAUDE.md) at repo root before your first non-trivial change.
It documents the 26 hard rules (no `user_id` on `jobs`, no lazy-breaking heavy
imports, mandatory five-surface updates when adding sources, timezone-aware
quiet-hours dispatch, account-mgmt session invalidation, etc.) plus the
scoring algorithm and data-flow.

For docs and plans, start at [`docs/README.md`](docs/README.md).
