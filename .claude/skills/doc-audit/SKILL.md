# /doc-audit — Tier-3 Doc Lifecycle Audit (Loop 3)

Triggered by: `/doc-audit` (optionally `/doc-audit <area>`)

You are running the full document-lifecycle audit defined in
`docs/maintenance/DOC-MAINTENANCE.md`. Read that file first — it is the
contract. The code is the only truth. Follow the five steps IN ORDER.
All changes land on a `docs/audit-<date>` branch and end in ONE docs-only PR —
never commit to main.

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
  to `docs/archive/`. Do not edit its content.
- **SUPERSEDED** (replaced by a newer plan/design) → banner + pointer, leave in
  place or archive, content frozen.
- **ACTIVE / not yet built** → leave untouched. If a LIVING doc contradicts it
  because the code is behind, record the gap in `docs/maintenance/PARKED.md`
  (doc, claim, evidence the code lacks it, date). Never delete the intention.

## Step 4: Coverage pass (undocumented code)

Walk `backend/src/` top-level modules and `frontend/src/` top-level areas.
Anything with no mention in any LIVING doc is an "undocumented module"
finding. Do NOT write new docs for them in this audit — list them; writing
docs is a follow-up task the human prioritizes.

## Step 5: Health report + one PR

Write/overwrite `docs/maintenance/DOC-HEALTH.md`:

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

**Never do:** edit code, touch LOG files' history, delete any doc, update an
archived plan's content, or push to main. If the audit finds something that
needs a CODE change, it goes to PARKED.md or a GitHub issue — not into this PR.
