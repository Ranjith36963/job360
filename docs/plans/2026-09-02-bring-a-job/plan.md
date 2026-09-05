# Plan: bring a job, keep the receipt
<!-- doc: PLAN -->
> **PLAN — shipped.** A design record, not live truth; the code and `docs/product/VISION.md` win. <!-- banner: auto -->
Reads: `intent.md`, `spec.md`. Honest note: the backend code below was written BEFORE this plan (session of
2026-09-02, interrupted). The plan is written now and the diff is checked against it — see "Diff vs plan".

## Files that change
Backend
- `backend/migrations/0034_application_receipts.{up,down}.sql` (new)
- `backend/src/repositories/database.py` — `get_job_id_by_key`, `insert_receipt`, `get_receipt`, `list_receipts`
- `backend/src/api/routes/bring.py` (new) — R1, R2
- `backend/src/api/routes/receipts.py` (new) — R3–R6
- `backend/src/api/main.py` — include both routers under `/api`
- `backend/tests/test_bring_a_job.py`, `backend/tests/test_receipts.py` (new)

Frontend
- `frontend/src/lib/api.ts` — `bringJob`, `createReceipt`, `listReceipts`, `getReceipt`
- `frontend/src/app/bring/page.tsx` (new) — form → `/jobs/{id}`
- `frontend/src/app/jobs/[id]/JobDetailClient.tsx` — "I applied" button
- `frontend/src/app/receipts/page.tsx`, `frontend/src/app/receipts/[id]/page.tsx` (new)
- `frontend/src/components/layout/Navbar.tsx` — links
- `frontend/tests/e2e/bring-a-job.spec.ts` (new)

Process
- `.claude/agents/verifier.md`, `REVIEW.md` (new)

## Order of work
1. Backend tests fixed and frozen (`test_receipts.py:50` `user_action`→`action`; the field is `action`, models.py:103).
2. Backend tests green on real Postgres. Migration up/down/up.
3. Frontend API + pages + e2e spec. `type-check`, `lint`, api-types regen.
4. Gate (`git add -A && bash scripts/agent-gate.sh`).
5. Verifier subagent walks the flow + two neighbours; screenshots into the PR.
6. Review passes per `REVIEW.md`. Fix Important findings only.
7. Commit, push, draft PR. Owner merges. Prod check + real ad on job360.uk.

## Risks
- `insert_job` returns False for an existing key AND for a real insert failure; we then look the id up by key
  and assert. A silent insert failure surfaces as a 500, not a wrong row.
- `_personalize_dims` needs a profile; without one `scored=False`, score 0. The feed row still lands.
- Frontend `params` is a Promise (Next 16) — await it in the receipt detail page.
- Windows full suite flakes; targeted gate locally, Linux CI is the verdict.

## Proof
- 5 tests in `test_bring_a_job.py`: stores under `user_brought` + feed row; Tokyo accepted; re-paste same row;
  5 bad inputs → 422; no login → 401/403.
- 6 tests in `test_receipts.py`: freeze + both applied surfaces; polished beats draft and survives re-tailor +
  catalog rewrite; two receipts newest-first; owner-scoped + 404; unknown job 404; append-only (grep + routes).
- Playwright `bring-a-job.spec.ts`: /bring → /jobs/{id} → I applied → /receipts → detail shows CV.
- Verifier report + screenshots attached to the PR.

## Diff vs plan
Filled 2026-09-02, before the PR was opened.

As planned: migration 0034, `database.py`, `bring.py`, `receipts.py`, `main.py`, both backend test files,
`api.ts`, `bring/page.tsx`, `receipts/page.tsx`, `receipts/[id]/page.tsx`, `Navbar.tsx`,
`bring-a-job.spec.ts`, `verifier.md`, `REVIEW.md`.

Differs — each one is a thing the plan missed, not a scope choice:
- `backend/src/api/models.py` — `JobResponse.description` added (Optional, default None). Not in the plan.
  Needed because R2 says the job page shows the pasted ad, and the response model had no field to carry
  it. Filled only by the single-job read and by `POST /jobs/bring` — the list route leaves it None so
  N × 8k chars never ride on the dashboard cards.
- `backend/src/api/routes/jobs.py` — one line in `get_job` to fill that field. Same reason.
- `backend/src/api/routes/bring.py` — a `_not_blank` validator on title/company/description. The plan
  said "input guards"; `min_length` alone let `"   "` through (caught by the frozen test `bad2`).
- `frontend/src/components/jobs/ReceiptButton.tsx` (new) — the plan put the "I applied" button inline in
  `JobDetailClient.tsx`. Pulled into a component so the pipeline card can reuse it later without a copy.
- `frontend/src/lib/types.ts`, `api-types.ts`, `openapi.json` — regenerated; forgotten in the file list.
- `frontend/src/middleware.ts` — `/bring` and `/receipts` added to `PROTECTED_PATHS`. Forgotten in the
  file list; without it an anonymous visit renders the form instead of redirecting to login.
- `JobDetailClient.tsx` — also hides `ApplyButton` when `apply_url` is empty (a brought job may have no
  link). Not in the plan; a button that opens `""` is broken behaviour, so it is a bug fix, not scope.
- Receipt detail page uses `useParams` (client component) rather than awaiting `params` — the risk note
  above assumed a server page; the page is client-side because it holds fetch state.

Found by the e2e run, fixed in code (tests stayed frozen):
- `ReceiptButton` kept a static `aria-label` after its visible text flipped to "Applied again?" — a
  screen reader would hear the old state. Fixed to follow the state.

Found by the review passes (REVIEW.md), fixed in code; each has a NEW test in
`backend/tests/test_bring_review_findings.py` (the frozen files were not touched):
- `bring.py` — pasting an ad that matches a catalog row the ghost detector had marked stale hit a bare
  `assert` → 500 (reproduced on Postgres). Now the row is reactivated (`update_last_seen`) — the user
  is reading it, so it is live — and the two asserts are explicit 500s with a message.
- `jobs.py` — `description` was filled for anonymous reads too; ids are sequential, so anyone could
  walk other people's pasted ads. Now only a logged-in user gets the text.
- `rescore.py` + `settings.py` — the next search's `backfill_feed_from_catalog` evicted a brought job
  from the feed (not liked/applied, may score under `MIN_STORE_SCORE`). Brought jobs now join
  `protected_ids`, like liked jobs. `USER_BROUGHT_SOURCE` moved to `settings.py` so the service does
  not import a route.
- `database.py` + `receipts.py` — `list_receipts` selected every body column with no LIMIT. Now a
  summary column list (`has_cv`/`has_cover_letter` computed in SQL), `limit` (≤200) / `offset` query
  params, and `total` from `COUNT(*)`. Frontend types regenerated.
- Two nits on lines already being edited: receipt detail keeps the *id* that failed (not a flag), so
  navigating to another receipt does not inherit "not found"; the e2e mock now uses valid values
  (`staleness_state: "active"`, `note: ""`).

Not done in this slice, on purpose: URL fetch (spec C3), a receipt from the pipeline card, any
notification. See intent.md open questions.
