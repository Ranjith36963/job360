<!-- doc: PLAN | status: ACTIVE | pr: — -->
# Intent: delete the sourcing era
Author: Ranjith (owner), decisions 1, 2, 5, 17 and build-order step 5 of
`docs/product/VISION.md` (2026-09-03). Issue #483. Status: draft — owner approves by merging.

## Problem
Job360 spent a year finding, scoring and ranking jobs. Since 2026-09-03 it does none of
that: the seeker's own agent finds the job and judges fit; Job360 **remembers**. Slice 2
(#480, merged 2026-09-04) hid the old product behind `SEARCH_UI_ENABLED=false` and switched
the crons off. The code is still all there, and it is most of the repo.

Measured on `origin/main` `1fba085` (2026-09-05):

| Surface | Size | Still wired into the product path? |
|---|---|---|
| `backend/src/sources/` — 48 files, 41 registry entries | 7,196 lines | no — only `src/main.py` builds them |
| `backend/src/main.py` — `SOURCE_REGISTRY`, `run_search`, `_build_sources` | 1,604 | `health.py` reports `len(SOURCE_REGISTRY)`; `search.py` (flag off) |
| `backend/src/workers/` — ARQ worker, dead since the Railway `worker` + `Redis` services went 2026-09-02 | 2,414 | `profile.py` still *tries* to enqueue a re-score on every profile save, then falls back to an in-process re-score |
| scorer / judge / enrichment / dedup / retrieval / embeddings / shelf gate (`services/*.py`) | ≈9,000 | **yes** — `POST /jobs/bring` still runs `fill_shelves`, scores the pasted ad, stamps `SCORER_VERSION`, and writes a `user_feed` row (`bring.py:96-152`) |
| legacy routes `jobs.py`, `search.py`, `runs.py` | 1,419 | `bring.py` imports three helpers from `jobs.py`; `/api/jobs/{id}` is a public read |
| `backend/scripts/` eval / probe / judge scripts | ≈10,300 of 10,951 | no |
| tests naming a sourcing module | ≈60 files of 244 | — |
| frontend dashboard, `components/jobs/*`, 4 Playwright specs | ≈4,300 | `/jobs/[id]` hosts the **web tailor fallback** (`TailorSection`) |
| 7 workflows already PAUSED 2026-09-03 + their scripts + drill rows | — | `drill_registry` turns red if a workflow goes without its row |
| `gen_doc_blocks.py`, `doc_sync_check.py` | — | both **parse `SOURCE_REGISTRY` out of `main.py`** and raise if it is gone |

`grep -rn SOURCE_REGISTRY` (repo, no `.git`/`node_modules`): **112 hits in 42 files**.
`pytest --collect-only`: **3,739 tests**.

Three live facts make this a product bug, not tidying:
1. **The product path still computes a score.** Every brought job gets `match_score`, eight
   dims and a feed row from a scorer the owner switched off (decision 5, rule 4). The
   `bring` page copy still says *"We score it against your profile."*
2. **Every profile save fires a re-score** of a feed nobody reads, through a queue that
   does not exist, into a fallback that runs inside the web process (`profile.py:100-175`).
3. **Docs and guards describe a product that is gone**, and two guards will *break* the
   moment the code goes — which is why nobody has deleted it.

## Proposed outcome
After this slice the repository contains only what the mission needs: profile extraction,
the application spine (applications, events, artifacts, receipts, contacts, profile edits),
URL fetch, the web tailor fallback, MCP + OAuth, auth, and the harness that keeps them
honest. Nothing finds, scores, ranks, dedups, enriches or embeds a job.

- `POST /jobs/bring` stores the ad and births the Application. **No score, no feed row,
  no shelf gate.** The response loses `match_score`, the dims and `scored`; it keeps
  `job`, `existing`, `application_id`, `status`, so agents already calling it keep working.
- The web tailor fallback moves onto `/applications/[id]`; `/jobs/[id]`, `/dashboard`,
  `/jobs` and the sources admin page are deleted, along with the flag that hid them.
- A profile save saves the profile. Nothing else happens.
- Tables that no remaining code reads (`run_log`, `job_enrichment`, `job_embeddings`) are
  dropped by migration `0039`. `jobs` stays — it is the spine's foreign key. `user_feed`
  stays for now: `services/delivery/decision_card.py` still reads it, and push
  notifications are their own deletion (decision 11), not this one.
- The seven paused sourcing workflows, their scripts and their drill rows go. The two doc
  guards stop counting sources. Pillars 02/03, the shelf/catalog measurements and the
  `add-source` skill move to `docs/_archive/sourcing-era/` under the FROZEN marker.
- `ARCHITECTURE.md` describes only what remains.

**Done when** (roadmap row 5): `grep -r SOURCE_REGISTRY` over the live surfaces finds
nothing; the test count drops and CI is green; ARCHITECTURE.md describes only what remains.
Pinned by `backend/tests/test_sourcing_era_deleted.py`, which is red before the build.

## Affected users and systems
- **The owner** — `bring` gets faster and honest; `/profile` loses the "search titles" line;
  the tailor fallback is reached from the application page instead of a job page.
- **Agents in the wild** — `bring_job` / `POST /jobs/bring` keep their request shape and
  their `application_id`; three response fields disappear. MCP tool list unchanged.
- **Production data** — migration `0039` drops three tables of scraped-era data
  (`run_log`, `job_enrichment`, `job_embeddings`). The down migration recreates them
  **empty**. `db-backup.yml` runs daily. The 19k scraped `jobs` rows are left in place
  (inert; a later cleanup once nothing points at them).
- **Harness** — 7 workflows deleted, 2 trimmed (`checker-scorecard` shelf x-ray step,
  `external-health` job-board keys), `drill_registry` shrinks, `doc_sync_check` /
  `gen_doc_blocks` lose their source-count logic.
- **Docs** — CLAUDE.md lines 44, 72, 75 describe the legacy path. CLAUDE.md is
  owner-approval-only: the edit ships as the **last, separate commit** on this branch so
  the owner can drop it.

## Constraints (owner's words + VISION rules, made rules)
1. **Delete, don't hide.** A flag that hides dead code is still dead code (decision 2:
   "hide now, delete later" — this is later). `SEARCH_UI_ENABLED` and
   `CATALOG_CRONS_ENABLED` go with the code they guarded.
2. **The product path computes nothing about fit** (rule 4, decision 5). No score field
   may survive on a response. If a helper both extracts profile facts and scores jobs, the
   extraction stays and the scoring goes.
3. **Not one spine row may be lost, and nothing user-authored is dropped.** Only tables
   with zero readers after the deletion are dropped; every drop is a migration with a
   down that recreates the schema; `jobs`, `applications*`, `profile*`, `user_actions`,
   receipts, tailored documents, tokens, OAuth, notifications are untouched.
4. **Backwards compatible on purpose.** `POST /jobs/bring`, `POST /receipts/{job_id}`,
   `/tailor/{job_id}` keep their request shapes. Removed response fields are listed in
   the PR body. MCP tool set: unchanged (measured, not quoted).
5. **Guards stay green by being edited, never by being skipped.** `drill_registry`,
   `doc_sync_check`, `gen_doc_blocks`, the MCP parity table, mypy ratchet, ruff — each is
   changed to reflect the new truth. No `--ignore`, no `xfail`, no deleted guard.
6. **Measure, never quote.** Every count in this plan and the PR is measured on this
   branch (`wc -l`, `pytest --collect-only`, `grep -c`).
7. **One brain per seam.** The backend trim (`bring.py`, `profile.py`, `database.py`,
   `settings.py`, the dependency cut) is one worker's job; frontend and harness/docs are
   separate workers that touch disjoint files.

## Open questions (resolved here, so the build does not re-litigate them)
- **Does `jobs` go?** No. `applications.job_id`, `application_receipts`, `tailored_documents`
  and `/tailor/{job_id}` key on it. It becomes "the ad the user brought", nothing more.
- **Does `user_feed` go?** Not in this slice — `decision_card.py` reads it. It goes with
  push notifications (decision 11 — follow-up issue, opened in the PR).
- **Does the pipeline (kanban) go?** No — it is application tracking, not sourcing. Slice 2
  left `stage` as a projection of `status`; folding it is a separate slice.
- **Does the public `/jobs/[id]` page survive "for unfurl bots"?** No. There is no shared
  catalog left to unfurl; a brought ad is one user's data. The tailor section it hosted
  moves to `/applications/[id]`. `sitemap.ts` lists static pages only.
- **`keyword_generator.py` / `search_titles`?** Go. Their only remaining reader is the
  `/profile` "what the boards were asked" line — a sourcing-era fact.
- **`job_signals.detect_seniority` / `scoring_dimensions._USER_EXPERIENCE_RANK` used by
  profile extraction?** Move the two functions and the rank table into
  `services/profile/seniority.py`; the modules that held them go.
- **Deps** — `arq`, `python-jobspy` go. `rapidfuzz`, `scikit-learn`, `chromadb`,
  `sentence-transformers` stay (profile-side importers measured).
- **When may this merge?** VISION step 5 says once slice 2 has been live for a release.
  The PR stays a draft; the owner decides the date by merging.
