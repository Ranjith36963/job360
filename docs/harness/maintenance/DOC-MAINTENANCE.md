# Doc-Maintenance Framework (Loop 3)
<!-- doc: LIVING -->

> **One principle: the code is the only truth.** Every document is either
> (a) synced to the code, (b) archived history, or (c) a parked intention.
> Nothing else is allowed to exist. This file defines how that stays true
> while the codebase changes every day.

> **Deletion authority.** `DELETION-POLICY.md` (a DRAFT, never wired) and
> `loop1_safe_reenable.md` were retired 2026-09-05 — their still-true rules
> fold in here: (1) never auto-delete what has no durable copy elsewhere —
> git history is the copy for merged work, a pushed tag/branch is the copy for
> unmerged work; (2) ground truth (`CLAUDE.md`, `ARCHITECTURE.md`, `README`,
> `STATUS.md`, `CONTRIBUTING`, `SECURITY`) and permanent records
> (`IMPLEMENTATION_LOG.md`, decision records, reviews) are never auto-touched;
> (3) an unshipped plan is never silently deleted — park it, don't ticket-and-
> forget; (4) any loop may read freely, but a loop that **writes** lands only
> through a PR a human merges — never a direct push to `main` — the Loop-1
> lesson (2026-06-21: an unsupervised write+merge agent wiped worktrees,
> branches and the test DB).

## 1. Doc taxonomy — every doc gets exactly ONE type

| Type | Examples | Rule |
|------|----------|------|
| **LIVING** | `CLAUDE.md`, `ARCHITECTURE.md`, `README.md`, `STATUS.md`, `backend/CLAUDE.md`, `frontend/README.md` | Must always match the code. Any drift is a bug, same severity as a failing test. |
| **PLAN** | `docs/plans/*`, design docs for unbuilt features | Has a lifecycle (below). Never silently edited after execution starts — plans are promises, and history must stay honest. |
| **LOG** | `docs/harness/maintenance/PARKED.md` | Append-only. Never rewritten, so never stale by definition. |
| **REFERENCE** | decision records, research notes | Updated only when the decision itself changes; superseded ones get a banner pointing to the successor, content stays. |

### Every doc carries its type on line 2 (machine + human readable)

One HTML comment — invisible when the doc renders, parseable by the tripwire:

```markdown
<!-- doc: LIVING | last-verified: 2026-07-15 by /sync -->   ← written by /sync
<!-- doc: PLAN | status: ACTIVE -->                          ← written by /doc-audit
<!-- doc: LOG | append-only -->
<!-- doc: REFERENCE -->
```

LIVING headers (tag + freshness date) are owned by the `/sync` auto-fixer.
All other headers are applied by `/doc-audit` Phase B after the user approves
the classification. The daily tripwire verifies LIVING docs carry the right
tag and a fresh date; untagged non-living docs surface in the next audit, not
as daily alarms.

## 2. Plan lifecycle (fixes the "implemented docs still lying around" problem)

```
DRAFT ──► ACTIVE ──► IMPLEMENTED ──► ARCHIVED
                          │
                          └─► SUPERSEDED (banner + pointer, content frozen)
```

- A plan whose code has merged gets stamped at the top —
  `> **IMPLEMENTED** in PR #N (`<sha>`) — archived <date>` — and moved to an
  archive location under `docs/`. (`docs/_archive/` and `docs/archive/` were
  both deleted 2026-09-05 — the next archived plan re-creates whichever
  location the framework's next revival picks.)

  It is never UPDATED (honesty): a stale number in a dated record is correct for
  its date, and rewriting it falsifies the record.

  "Never deleted" was amended by the owner on 2026-08-25. Scaffolding whose
  output has merged — step plans, prompt batches — may be deleted, because git
  history holds the content and `IMPLEMENTATION_LOG.md` holds the narrative.
  Five scaffolding docs went that day, alongside four superseded audit
  records — nine files, 3,123 lines in total.

  What may NOT be deleted is anything still cited: `CurrentStatus.md` is
  hardcoded in `merge_cage.py`, and the two `*_progress.md` logs are cited from
  backend test docstrings. Before deleting any doc, grep the repo for its
  filename and repoint every hit to a `git show <sha>:<path>` reference first —
  a deletion that leaves dangling citations costs more than it saves.
- A plan that was abandoned or replaced gets a `> **SUPERSEDED by <doc>**` banner.
- A plan not yet built stays where it is — it is the backlog.

## 3. The three tiers (how the loop actually runs)

| Tier | What | When | Cost |
|------|------|------|------|
| **1 — Tripwire** | `scripts/doc_sync_check.py` compares hard code facts (source count, migration head, …) against every numeric doc claim; opens a GitHub issue on drift | **Daily, automatic** (CI cron, this PR) | Free, no LLM |
| **2 — Fixer** | The `/sync` skill: scan code → compare LIVING docs → **edit the docs** → docs-only PR. **Runs AUTOMATICALLY in CI** when Tier 1 finds drift (the `auto-fix` job launches a Claude session — needs the one-time `CLAUDE_CODE_OAUTH_TOKEN` repo secret). Can also be run by hand anytime. | Automatic on drift; manual anytime | One Claude session |
| **3 — Auditor** | The `/doc-audit` skill: full lifecycle pass — classify docs, archive IMPLEMENTED plans, park contradictions, find undocumented modules, write the health report | Weekly, or after a multi-PR day | One Claude session |

Tier 1 watches every day for free; Tier 2 fixes automatically when Tier 1
fires. The write-loop is caged, not banned: the auto-fixer may edit **only
`*.md` files**, its PR auto-merges **only when the diff is 100% markdown**
(a deterministic CI check, not the LLM's own claim), and any non-markdown
file in the diff blocks the merge and flags the PR for human review. Code
changes still always require a human — the Loop-1 lesson holds where it
matters.

## 4. Hard rules

1. **Code wins every contradiction.** When doc and code disagree there are only
   two legal outcomes: the doc was stale → fix the doc; or the feature is
   missing → record it in `docs/harness/maintenance/PARKED.md` (never silently delete
   an intention, never "fix" a doc to describe code that doesn't exist).
2. **Doc edits land by PR only.** The agent proposes, a human lands (the
   Loop-1 lesson, folded in above). No loop ever pushes doc edits to main.
3. **Implemented plans are archived, not updated.**
4. **Logs are append-only.**
5. **Every code PR declares its doc impact** — one line in the PR body:
   `docs: updated <files>` or `docs: no impact`. Reviewer (human or agent)
   checks the claim.
6. **Every LIVING doc carries a freshness stamp** — `<!-- last-verified:
   YYYY-MM-DD by /sync -->` near the top, updated by every Tier-2 run, so
   staleness is measurable ("verified 40 days ago" is itself a finding).

7. **One doc-writing session at a time.** `/sync` and `/doc-audit` both edit
   docs; with parallel agent sessions, two doc branches on the same evening
   collide. Before starting either: check for an open `docs:` PR — if one
   exists, stop. Branch names carry a `-<HHMM>` time suffix, and every docs
   branch rebases on fresh `origin/main` right before pushing.
8. **Rule 5 is currently self-policed, not CI-enforced.** A tiny PR-lint
   workflow (fail when the body lacks a `docs:` line) is the known upgrade —
   until it exists, agents opening PRs must include the line themselves.

**Scope note — out-of-repo memory.** Claude's memory files
(`~/.claude/projects/D--dev-job360/memory/*.md`) also make factual claims
about this repo. They are OUTSIDE this framework's write-scope (not in git,
not PR-reviewable). `/doc-audit` Step 4 may *flag* stale memory claims in its
report, but never edits them — memory hygiene is the session's own job.

## 5. Enterprise mapping (what big companies do → the Job360 version)

| Enterprise practice | Here |
|---|---|
| Docs-as-code: in repo, PR-reviewed, versioned | Already true — keep it |
| Dedicated technical writers | Loop 3 tooling is the writer; you are the editor who merges |
| Freshness SLAs + staleness dashboards | Tier-1 daily check + `DOC-HEALTH.md` scorecard |
| ADRs (architecture decision records) | `docs/decisions/` — keep appending |
| Archive-over-delete retention | An archive location under `docs/` + stamps (none exists today — see §2). Nothing deleted EXCEPT merged scaffolding, per the 2026-08-25 amendment in §2 |
| Doc impact required in code review | Rule 5 above |

## 6. Outputs this framework maintains

- `docs/harness/maintenance/DOC-HEALTH.md` — **written by the first Tier-3 audit; absent
  until one runs**, so an unresolved link here is expected, not rot. This is the
  destination `.claude/skills/doc-audit/SKILL.md`'s Step 5 writes to. Scorecard from each audit:
  docs checked, drifts fixed, plans archived, gaps parked, modules undocumented.
- `docs/harness/maintenance/PARKED.md` — the "code is behind the doc" list: intentions
  found in docs that are not yet implemented, each with source doc + date.
- Archived plans, when this framework is active again, live under `docs/`
  with an `> **IMPLEMENTED**` stamp — there is no archive directory today
  (`docs/_archive/` and `docs/archive/` were both deleted 2026-09-05).
