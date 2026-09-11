# Slice 8 plan
<!-- doc: PLAN | written 2026-09-11 -->

Branch `feat/cv-diff-515` off `main` at `1f807be`. One PR, owner merges.

1. Docs first: VISION.md decision 26 + build-order line 8; roadmap row 8;
   `docs/README.md` slices table row. (this folder)
2. Backend, test-first: `tests/test_artifact_diff.py` red →
   `src/services/applications/diff.py` (pure) + route in
   `src/api/routes/applications.py` + `APPLICATION_DIFF_MAX_LINES` in
   `src/core/settings.py` → green. Ruff + mypy.
3. `bash scripts/gen-api-types.sh` (route change → regenerate types).
4. Frontend: `lib/api.ts` `getArtifactDiff`; `components/applications/
   ArtifactDiff.tsx` (panes) + `ArtifactVersions.tsx` (Applied badge,
   Compare); `ApplicationClient.tsx` passes receipts. Unit test + hermetic
   e2e spec. Lint, type-check, unit, build.
5. Gate: targeted pytest (spine, receipts, parity, diff), frontend checks,
   `git diff origin/main...HEAD --name-only` = only these files.
6. Push, draft PR, `gh pr checks` green before `result:`.
