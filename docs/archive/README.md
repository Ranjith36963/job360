# docs/archive/ — empty; the real archive is `docs/_archive/`
<!-- doc: LIVING -->

**This directory holds nothing but this file.** Archived docs live one directory
away, in **`docs/_archive/`**.

The split is an accident, not a design: the restructure that created
`docs/_archive/` never moved or retired this folder, and the tooling was never
repointed. Until 2026-08-25 this file described a populated archive — stamps,
conventions, the lot — for a directory containing only itself. Anything
following it archived into the wrong place, and anything looking here for
history found nothing and could reasonably conclude none was kept.

## What is actually where

| Path | Holds |
|---|---|
| `docs/_archive/` | the archived docs — Pillar 1/2 progress logs, `CurrentStatus.md`, `pillar2_implementation_plan.md`, and `one-shot-scripts/` |
| `docs/archive/` | this file |

## If you are archiving a doc

Put it in **`docs/_archive/`**. Stamp it `<!-- doc: FROZEN -->` (or `LOG` for a
dated record) so the nightly truth-check leaves it alone — a stale number in a
dated record is correct for its date, and rewriting it falsifies the record.

Then fix every inbound link. A moved doc with live references pointing at its
old path is a dead link that nothing will notice.

## Why this file still exists

Both referrers are now repointed — `.claude/skills/doc-audit/SKILL.md` and
`docs/harness/maintenance/DOC-MAINTENANCE.md` say `docs/_archive/`. What is left
is a signpost for anyone who still has the old path in their head or in a link,
so it stays as a tombstone rather than becoming a 404. Deleting the directory is
now safe and is its own PR.
