# Spec: bring a job, keep the receipt
<!-- doc: PLAN -->
> **PLAN — shipped.** A design record, not live truth; the code and `docs/product/VISION.md` win. <!-- banner: auto -->
Reads: `intent.md`. Skills applied: `hard-rules` (31 invariants), `verify-job360`. Status: draft.

## Requirements
R1. `POST /api/jobs/bring` {title, company, description, location?, apply_url?} → the job exactly as
    `GET /api/jobs/{id}` returns it, plus `existing` (row already in catalog) and `scored` (user has a profile).
R2. The brought job lands in the caller's feed as `active` and is scored by the same scorer as search hits.
R3. `POST /api/receipts/{job_id}` {channel?, note?} → 201 with the frozen receipt. Copies: job title, company,
    location, apply_url, source, description; CV text + origin (`polished` beats `ai_draft`); cover letter
    text + origin; profile version; sent_at.
R4. Creating a receipt also marks the job `applied` in `user_actions` and `applications`, so the card and the
    pipeline agree.
R5. `GET /api/receipts?job_id=` lists newest first; `GET /api/receipts/{id}` returns one. Owner-scoped.
R6. No PATCH/PUT/DELETE on receipts. No code path UPDATEs or DELETEs `application_receipts`.
R7. Frontend: `/bring` form → redirect to `/jobs/{id}`; "I applied" button on the job page → receipt; `/receipts`
    list → `/receipts/{id}` detail showing the CV as sent.

## Design
- Storage per rule #10: the ad is a shared `jobs` row, `source='user_brought'`, no user_id. The per-user fact
  ("I brought / track this") is the `user_feed` row, like a search hit. `insert_job` is INSERT-OR-IGNORE on
  `normalized_key()` (company+title), so a re-paste finds the same row.
- Scoring reuses `_personalize_dims` (sets `job.id` — the dim-scoring-id fix) and `upsert_feed_row` stamped with
  `current_profile_version_id` + `SCORER_VERSION`, so backfills treat the row like any other.
- The receipt is a separate table (migration 0034) with its own copy of every field. It does not point at
  `tailored_documents`, because re-tailoring DELETE+INSERTs that table.
- Input guards: strings stripped; `apply_url` must be http(s) (it is rendered as `<a href>`); description ≤
  40,000 chars; title/company ≤ 300.
- Auth: `require_user` on every route (rules #12, #25). Audit log events `job_brought`, `receipt_create`.
- Heavy imports (Job, FeedService, shelf gate, scorer) are lazy inside the handler (rule #16).

## Flagged concerns
C1. **Shared-row description.** Second user pasting the same (company, title) sees the first paste's
    description. Mitigation today: `existing=True` returned; UI says "already in the catalog". Owner decides
    whether user-brought rows should key on description hash later.
C2. **UK door bypassed on purpose** (rule #30 is a door on the search pipeline). A brought job is never refused
    on location. Pinned by `test_bring_is_global_no_uk_door`.
C3. **No URL fetch.** SSRF + bot-blocked boards. Paste only. Link stored.
C4. **Receipt without a CV** is allowed (user applied with their own file). `cv_text` NULL, `has_cv=false`.
C5. **Migration is additive** — new table + 2 indexes, no change to existing tables. Down file drops them.
