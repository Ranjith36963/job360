# /doc-audit — Tier-3 Doc Lifecycle Audit (Loop 3)
<!-- doc: LIVING -->

Triggered by: `/doc-audit` (optionally `/doc-audit <area>`)

You are running the full document-lifecycle audit defined in
`docs/harness/maintenance/DOC-MAINTENANCE.md`. Read that file first — it is the
contract. The code is the only truth.

**TWO-PHASE CONTRACT — AMENDED 2026-07-27.** The old contract made *every*
action wait on the user. He never ran Phase B, so nothing was ever archived:
`docs/archive/` stayed empty, zero docs carried an IMPLEMENTED stamp, and 75% of
plan docs describing shipped work piled up. The gate is now **per-lane**, per
[`docs/harness/maintenance/DELETION-POLICY.md`](../../../docs/harness/maintenance/DELETION-POLICY.md):

- **Phase A — REPORT.** Run Steps 1–4 without changing ANY file. Present the
  findings, classifying every item into Lane A (auto-delete), Lane B
  (auto-archive) or Lane C (human-only). **Anything you cannot classify by a
  machine-checkable predicate is Lane C** — silence is not permission.
- **Phase B — APPLY.** Lane A and Lane B items are applied **without waiting for
  approval** (they are predicate-proven and reversible — reflog/git history).
  **Lane C items are NEVER applied**: list them in the PR body and stop. The
  human's only job is deciding Lane C.
- **While `DELETION-POLICY.md` is marked `DRAFT — NOT IN FORCE`, treat every
  item as Lane C** — i.e. the old report-only behaviour — until its status
  reads `ACTIVE`. That one word is the switch.

  When applying the classification,
  also write each doc's type header on line 2 (spec in DOC-MAINTENANCE.md):
  `<!-- doc: PLAN | status: ACTIVE -->`, `<!-- doc: LOG | append-only -->`,
  `<!-- doc: REFERENCE -->` (LIVING headers are owned by /sync — skip them;
  an untouchable AHEAD doc gets NO header unless the user approves that
  specific doc). Work on a `docs/audit-<YYYYMMDD>-<HHMM>` branch
  (time suffix — parallel sessions must not collide), ending in ONE docs-only
  PR — never commit to main. Rebase on freshly-fetched `origin/main` right
  before pushing. **Only one doc-writing session (sync or audit) at a time** —
  check for an open `docs:` PR first; if one exists, stop and say so.
  If `docs/_archive/` or `docs/harness/maintenance/PARKED.md` are missing, create them
  first (with a one-line header explaining their purpose).

**AHEAD docs are untouchable (user's rule):** a doc describing something not
yet built (a plan, a promise, a design) is the product backlog. LIST it in the
Phase-A report — never edit it, never move it, never park-annotate it, in
either phase, unless the user explicitly orders that specific doc changed.

---

## Step 1: Inventory + classify

List every `*.md` in the repo root, `docs/`, `backend/`, `frontend/`
(excluding `node_modules`, `.claude/worktrees`). Assign each exactly one type:
**LIVING / PLAN / LOG / REFERENCE** (definitions in DOC-MAINTENANCE.md §1).
Flag any doc you cannot classify — that is itself a finding.

## Step 2: Sync the LIVING docs (delegate to /sync)

Run the `/sync` skill's steps against every LIVING doc: extract real facts
from code (source registry, migrations head, test count, schema, commands,
deps), fix every stale claim, and update each doc's
`<!-- last-verified: YYYY-MM-DD by /sync -->` stamp.

## Step 3: Plan lifecycle pass

For every PLAN doc, determine its true state by checking the CODE and git
history (not the doc's own claims):

- **IMPLEMENTED** (the code it describes exists and is merged) → stamp the top
  with `> **IMPLEMENTED** in PR #N (<sha>) — archived <date>` and `git mv` it
  to `docs/_archive/`. Do not edit its content. **After EVERY move, grep the
  whole repo for the old path** (`grep -rn "<old path>" --include="*.md"`) and
  update each referrer — `docs/README.md` (the plan index) and CLAUDE.md's
  "Related documentation" section link to plan files; a move without a link
  fix leaves dead links the tripwire cannot see.
- **SUPERSEDED** (replaced by a newer plan/design) → banner + pointer, leave in
  place or archive, content frozen.
- **ACTIVE / not yet built** → leave untouched. If a LIVING doc contradicts it
  because the code is behind, record the gap in `docs/harness/maintenance/PARKED.md`
  (doc, claim, evidence the code lacks it, date). Never delete the intention.

## Step 4: Coverage pass (undocumented code)

Walk `backend/src/` top-level modules and `frontend/src/` top-level areas.
Anything with no mention in any LIVING doc is an "undocumented module"
finding. Do NOT write new docs for them in this audit — list them; writing
docs is a follow-up task the human prioritizes.

## Step 5: Health report + one PR

Write/overwrite `docs/harness/maintenance/DOC-HEALTH.md`:

```
# Doc health — <date>
- Docs inventoried: N (LIVING x, PLAN y, LOG z, REFERENCE w, unclassifiable u)
- Drifted claims fixed: N (list)
- Plans archived: N (list)
- Gaps parked: N (list)
- Undocumented modules: N (list)
- Oldest last-verified stamp: <doc> (<date>)
```

Commit everything on the audit branch, push, open ONE docs-only PR titled
`docs: lifecycle audit <date>` whose body is the health report. Stop —
the human merges.

---

**Never do:** edit code, touch LOG files' history, update an archived plan's
content, or push to main. If the audit finds something that needs a CODE change,
it goes to PARKED.md or a GitHub issue — not into this PR.

**Deletion — REPEALED 2026-07-27.** This list used to read "delete any doc",
a blanket ban. That ban is why 75% of plan docs describing already-shipped work
piled up: destruction was centralised on one human who never ran Phase B, so
every agent correctly obeyed and the pile grew. Deletion is now governed solely
by [`docs/harness/maintenance/DELETION-POLICY.md`](../../../docs/harness/maintenance/DELETION-POLICY.md):

- **Lane A / Lane B** (completed work, generated junk, merged-branch artifacts) —
  act by predicate, no approval needed.
- **Lane C** (anything unmerged, unpushed, uncommitted, ground truth, permanent
  record, PII, or needing judgment) — still human-only. Report, never act.
- **While that policy is marked `DRAFT — NOT IN FORCE`, nothing auto-deletes.**
  Treat everything as Lane C until its status is `ACTIVE`.
