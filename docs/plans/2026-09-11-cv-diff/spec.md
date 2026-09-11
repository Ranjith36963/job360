# Slice 8 spec — artifact diff, read-only
<!-- doc: PLAN | written 2026-09-11 -->

Intent: [`intent.md`](intent.md). Depends on slice 2 (spine, `0037`).

## R1 — one read route, no tool

`GET /api/applications/{application_id}/artifacts/{artifact_id}/diff`

| Query | Meaning | Default |
|---|---|---|
| `against` | `profile` (the candidate's stored CV text) or another artifact id of the same kind on the same application | `profile` when `kind == "cv"`; otherwise the previous version by `version_no`; if none, an empty base |

Response `ArtifactDiffOut`:

```
kind        "cv" | "cover_letter" | "answers" | "outreach"
base        { source: "profile" | "artifact" | "none", artifact_id?, version_no?, label }
target      { artifact_id, version_no, made_by, model, created_at, applied: bool }
lines       [{ op: "equal" | "add" | "del", text }]     # base order, then target order per hunk
added       int   # lines with op == add
removed     int   # lines with op == del
truncated   bool  # either side was cut to APPLICATION_DIFF_MAX_LINES before diffing
```

`applied` is true when any receipt on the application names `artifact_id` in
`cv_artifact_id` or `cover_letter_artifact_id`.

## R2 — the diff is deterministic and stdlib

`difflib.SequenceMatcher(None, base_lines, target_lines, autojunk=False)`
over `str.splitlines()`. Pure function in
`src/services/applications/diff.py`; the route only loads texts. Both sides
are capped at `APPLICATION_DIFF_MAX_LINES` (env, default 4000) — an artifact
is already ≤ `APPLICATION_ARTIFACT_MAX_CHARS`, the profile text is not, and
`SequenceMatcher` is quadratic in the worst case.

## R3 — the web shows it, marks the applied version, writes nothing

On `/applications/{id}`, in the Artifacts section:

- every version that a receipt names gets an **Applied** badge
  (`data-testid="artifact-applied-badge"`);
- every version gets a **Compare** button (`data-testid="artifact-compare"`)
  that opens two panes side by side (stacked under `md`), left = base, right
  = this version; a base picker offers "Original CV" (cv only) and every
  other version of the same kind;
- changed lines are marked on both panes: removed lines on the left
  (`data-diff="del"`), added lines on the right (`data-diff="add"`);
- there is **no Keep button** and no write from this view. The tailor
  fallback's provenance colouring (`TailorPanel.tsx`) is untouched.

## Security guardrails

- `Depends(require_user)`; the application is loaded through
  `spine.get_owned_application` (rule #12/#25) — a foreign application or
  artifact id, or an `against` id from another application or another kind,
  is 404 (existence-hiding, same as `GET …/artifacts/{artifact_id}`).
- Read-only: nothing is inserted, updated or deleted (M3 unaffected).
- Bounded work: `APPLICATION_DIFF_MAX_LINES` per side; `against` must parse
  as `profile` or a positive int, else 422.
- No new MCP tool, so `test_mcp_gate_parity` needs no row (M5 is about
  tools re-applying route gates; there is no tool).

## Done when

- `backend/tests/test_artifact_diff.py`: default base for cv is the profile
  CV; a changed line shows as one `del` + one `add`; `applied` flips to true
  after `record_application` names the version; foreign ids are 404;
  `against` of another kind is 404; the cap sets `truncated`.
- `frontend/tests/e2e/applications-detail-diff.spec.ts` (hermetic, route
  mocks like `applications-detail-tailor.spec.ts`): open an application
  with two cv versions and a receipt naming v2; see the Applied badge on v2;
  press Compare; see a `del` line on the left pane and an `add` line on the
  right; assert no element with text "Keep".
- Unit test for the pane renderer.
- `npm run gen:types` regenerated `api-types.ts` (route change).
