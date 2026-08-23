# Pillar 3 — Batch 3.5.1 Security Patch Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`. Steps use checkbox syntax for tracking.

**Goal:** Close the two IDOR gaps the 2026-04-19 re-audit (`docs/CurrentStatus.md` §7) found on `profile.py` (4 routes) and `search.py` (2 routes) — Batch 3.5's IDOR fix scope missed these.

**Architecture:** Gate-only — same `Depends(require_user)` pattern Batch 3.5 applied to `jobs.py` / `actions.py` / `pipeline.py`. Storage stays single-file for profile (migration to per-user is a separate batch). Search runs stay in the existing module-level `_runs: dict` with a new `user_id` field on each record; mismatch → 404 (existence-hiding, not 403).

**Tech stack:** existing — FastAPI, `auth_deps.require_user`, pytest, `fastapi.testclient`.

---

## POST-BATCH-3.5 BASELINE

Run 2026-04-19 on `pillar3/batch-3.5.1` HEAD (branched from `origin/main` @ `554bcbc`, post-Batch-3.5-merge + CurrentStatus re-audit).

```
Command: cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:  23 failed, 558 passed, 3 skipped in 231.92s
Log:     /tmp/pytest_baseline_3_5_1.log
```

All regression claims compare against this exact number.

---

## Scope ceiling

- NO per-user profile storage migration (that's audit item #2; a dedicated batch).
- NO search-runs DB table (the existing `_runs` in-memory dict is sufficient for this gate).
- NO scope creep into channels/workers/scheduler surfaces.

---

## File-level plan

### Modified files

| Path | Change |
|---|---|
| `backend/src/api/routes/profile.py` | Import `CurrentUser, require_user`; add `user: CurrentUser = Depends(require_user)` to all 4 route signatures (L57, L66, L123, L146) |
| `backend/src/api/routes/search.py` | Import `CurrentUser, require_user`; gate both routes; tag each `_runs[run_id]` with `user_id`; on GET, return 404 when the stored `user_id != user.id` |
| `backend/tests/test_api_idor.py` | Append 8 new tests (4 profile unauth + 4 search — unauth/cross-user/positive-control) |

### Created files

None.

### Deleted files

None.

---

## Phase A — Plan committed (this doc)

**Commit:** `docs(pillar3): Batch 3.5.1 security-patch plan + baseline`

- [ ] Step A1: Write this file
- [ ] Step A2: Commit after STEP 0 baseline numbers are in

---

## Deliverable A — Gate profile.py routes

**Why it matters.** Today, any unauthenticated caller can POST a CV, upload a LinkedIn PDF, or merge GitHub data into the single shared profile store, and any unauth caller can read the CV text back. Per CurrentStatus.md §7 this is the highest-severity gap.

**Scope call.** Gate-only. Storage stays at `backend/data/user_profile.json` (single-file, shared). Multi-tenancy is a separate batch — for Batch 3.5.1 the gate just means "authenticated users only"; a second authenticated user can still overwrite the first's profile, but that's not reachable from an attacker who can't log in.

**Files:**
- Modify: `backend/src/api/routes/profile.py:1-154`
- Modify: `backend/tests/test_api_idor.py` (append profile-unauth tests + parametrize the existing auth-required parametrize list)

### Tasks

- [ ] **A-Step 1: Write 4 RED unauth tests** — append to `test_api_idor.py`. Four parametrize entries covering `GET /api/profile`, `POST /api/profile`, `POST /api/profile/linkedin`, `POST /api/profile/github`. Each expects 401.

```python
# Appended to the existing test_per_user_endpoint_requires_auth parametrize
# in tests/test_api_idor.py — each route + method pair added as a new case.
("GET",  "/api/profile"),
("POST", "/api/profile"),
("POST", "/api/profile/linkedin"),
("POST", "/api/profile/github"),
```

- [ ] **A-Step 2: Run, verify RED**

```
python -m pytest tests/test_api_idor.py::test_per_user_endpoint_requires_auth -q
```

Expected: the 4 new rows fail with `200 != 401` (routes currently return 200 / 422 without auth).

- [ ] **A-Step 3: Add `Depends(require_user)` to all 4 profile handlers**

Import at top of `backend/src/api/routes/profile.py`:

```python
from src.api.auth_deps import CurrentUser, require_user
```

Add `user: CurrentUser = Depends(require_user)` to each signature:

```python
@router.get("/profile", response_model=ProfileResponse)
async def get_profile(user: CurrentUser = Depends(require_user)):
    ...

@router.post("/profile", response_model=ProfileResponse)
async def upsert_profile(
    cv: UploadFile = File(None),
    preferences: str = Form(None),
    user: CurrentUser = Depends(require_user),
):
    ...

@router.post("/profile/linkedin", response_model=LinkedInResponse)
async def upload_linkedin(
    file: UploadFile = File(...),
    user: CurrentUser = Depends(require_user),
):
    ...

@router.post("/profile/github", response_model=GitHubResponse)
async def upload_github(
    username: str = Form(...),
    user: CurrentUser = Depends(require_user),
):
    ...
```

The `user` arg is unused inside the body on purpose — this batch is gate-only. The variable is named `user` (not `_user`) so a future multi-tenancy batch can wire it into the storage key without a rename.

- [ ] **A-Step 4: Run, verify GREEN**

```
python -m pytest tests/test_api_idor.py::test_per_user_endpoint_requires_auth -q
```

Expected: all parametrize cases (pre-existing + 4 new) pass.

- [ ] **A-Step 5: Commit**

```
git add backend/src/api/routes/profile.py backend/tests/test_api_idor.py
git commit -m "fix(api): gate profile routes with require_user"
```

---

## Deliverable B — Gate search.py + scope run_id by user_id

**Why it matters.** Today any unauth caller can trigger a pipeline run via `POST /api/search` and enumerate every active `run_id` on the server via `GET /api/search/{run_id}/status` (run_ids are 12-char hex — guessable in practice with a few thousand requests). This batch makes both routes auth-only AND prevents cross-user run_id lookup.

**Storage choice:** `search.py:15` stores runs in a module-level `_runs: dict[str, dict] = {}`. **Pure in-memory — no DB table exists.** So the simplest correct path is: add a `user_id` field to each run record dict; on GET, check `run["user_id"] == user.id` and return 404 otherwise. No migration needed, no URL shape change.

**Files:**
- Modify: `backend/src/api/routes/search.py:1-47`
- Modify: `backend/tests/test_api_idor.py` (append 4 search tests)

### Tasks

- [ ] **B-Step 1: Write 4 RED tests** — append to `test_api_idor.py`:

```python
# 1. Unauthenticated POST /api/search -> 401
("POST", "/api/search"),            # add to the parametrize
# 2. Unauthenticated GET /api/search/{run_id}/status -> 401
("GET",  "/api/search/abc123/status"),  # add to the parametrize

# 3 + 4. Cross-user + positive-control isolation tests (dedicated functions).


def test_search_run_id_is_scoped_by_user(api, monkeypatch):
    """User A creates a run; user B GETting that run_id's status must 404."""
    # Stub run_search so the background task completes instantly and
    # predictably — we are testing the gate, not the pipeline.
    async def _fake_run_search(**kwargs):
        return {"total_found": 0, "new_jobs": 0, "sources_queried": 0, "per_source": {}}

    import src.api.routes.search as search_route
    monkeypatch.setattr(search_route, "run_search", _fake_run_search)

    _register(api, "alice@example.com")
    r = api.post("/api/search")
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    # Alice can read her own run
    r_alice = api.get(f"/api/search/{run_id}/status")
    assert r_alice.status_code == 200
    assert r_alice.json()["run_id"] == run_id

    # Switch to bob
    api.post("/api/auth/logout")
    api.cookies.clear()
    _register(api, "bob@example.com")

    # Bob must get 404 (NOT 403 — existence-hiding)
    r_bob = api.get(f"/api/search/{run_id}/status")
    assert r_bob.status_code == 404


def test_search_status_for_unknown_run_id_returns_404(api):
    _register(api, "alice@example.com")
    r = api.get("/api/search/nonexistent123/status")
    assert r.status_code == 404
```

- [ ] **B-Step 2: Run, verify RED**

```
python -m pytest tests/test_api_idor.py -q -k "search"
```

Expected: the 2 new parametrize cases + `test_search_run_id_is_scoped_by_user` + `test_search_status_for_unknown_run_id_returns_404` all fail (search routes currently have no auth and no user_id tagging).

- [ ] **B-Step 3: Patch `backend/src/api/routes/search.py`** — add auth + user_id:

```python
"""Search routes for Job360 FastAPI backend."""
from __future__ import annotations

import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.auth_deps import CurrentUser, require_user
from src.api.models import SearchStartResponse, SearchStatusResponse
from src.main import run_search

router = APIRouter(tags=["search"])

_runs: dict[str, dict] = {}


@router.post("/search", response_model=SearchStartResponse)
async def start_search(
    source: Optional[str] = Query(None),
    user: CurrentUser = Depends(require_user),
):
    """Start an async job search run. Returns a run_id to poll for status."""
    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {
        "user_id": user.id,
        "status": "running",
        "progress": "Starting...",
        "result": None,
    }

    async def _run():
        try:
            _runs[run_id]["progress"] = "Fetching from sources..."
            result = await run_search(source_filter=source, no_notify=True)
            _runs[run_id].update(status="completed", progress="Done", result=result)
        except Exception as e:
            _runs[run_id].update(status="failed", progress=str(e))

    asyncio.create_task(_run())
    return SearchStartResponse(run_id=run_id, status="running")


@router.get("/search/{run_id}/status", response_model=SearchStatusResponse)
async def search_status(
    run_id: str,
    user: CurrentUser = Depends(require_user),
):
    """Poll the status of a running or completed search.

    Existence-hiding: unknown run_id OR run owned by a different user
    both return 404 with the same body. An attacker enumerating run_ids
    cannot distinguish "does not exist" from "exists but not mine".
    """
    run = _runs.get(run_id)
    if run is None or run.get("user_id") != user.id:
        raise HTTPException(status_code=404, detail="run not found")
    # Strip user_id from the response payload — it's an internal scoping
    # field, not part of the public SearchStatusResponse contract.
    payload = {k: v for k, v in run.items() if k != "user_id"}
    return SearchStatusResponse(run_id=run_id, **payload)
```

- [ ] **B-Step 4: Run, verify GREEN**

```
python -m pytest tests/test_api_idor.py -q -k "search or require_auth"
```

Expected: all 4 new tests pass, no existing IDOR tests regress.

- [ ] **B-Step 5: Run full `test_api_idor.py`**

```
python -m pytest tests/test_api_idor.py -q
```

Expected: all pre-existing IDOR tests + 8 new (4 profile + 4 search) pass. Target total: 25 tests (17 pre-existing + 8 new).

- [ ] **B-Step 6: Commit**

```
git add backend/src/api/routes/search.py backend/tests/test_api_idor.py
git commit -m "fix(api): gate search routes with require_user + scope run_id by user_id"
```

---

## STEP 5 — Verify before completion (critical, no reviewer)

No reviewer round means the self-report IS the audit trail. Produce exactly six artefacts:

### 1. Per-route anchor list

- `backend/src/api/routes/profile.py:<L>` — GET `/profile` handler has `Depends(require_user)`
- `backend/src/api/routes/profile.py:<L>` — POST `/profile` handler has `Depends(require_user)`
- `backend/src/api/routes/profile.py:<L>` — POST `/profile/linkedin` handler has `Depends(require_user)`
- `backend/src/api/routes/profile.py:<L>` — POST `/profile/github` handler has `Depends(require_user)`
- `backend/src/api/routes/search.py:<L>` — POST `/search` handler has `Depends(require_user)` + tags `_runs[run_id]['user_id']`
- `backend/src/api/routes/search.py:<L>` — GET `/search/{run_id}/status` has `Depends(require_user)` + filters by `user.id`

### 2. Grep proof

```
grep -n "require_user" backend/src/api/routes/profile.py backend/src/api/routes/search.py
```

Every route function body must show exactly one `Depends(require_user)` call; the file-level import must show up once per file.

### 3. Test names + counts

8 new tests in `backend/tests/test_api_idor.py`:
- `test_per_user_endpoint_requires_auth[GET-/api/profile]`
- `test_per_user_endpoint_requires_auth[POST-/api/profile]`
- `test_per_user_endpoint_requires_auth[POST-/api/profile/linkedin]`
- `test_per_user_endpoint_requires_auth[POST-/api/profile/github]`
- `test_per_user_endpoint_requires_auth[POST-/api/search]`
- `test_per_user_endpoint_requires_auth[GET-/api/search/abc123/status]`
- `test_search_run_id_is_scoped_by_user`
- `test_search_status_for_unknown_run_id_returns_404`

### 4. Smoke import test

```
python -c "from src.api.routes import profile, search; print('imports OK')"
```

Expected: `imports OK`.

### 5. Pytest delta

```
cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
```

Expected: baseline + 8 passing, 0 new failures, 0 regressions.

### 6. Search run_id storage choice

"In-memory dict with `user_id` field on each record. Chose this over a
new `search_runs` table because (a) no table exists today, and
introducing one expands scope to a DB migration + repo layer + tests;
(b) the existing `_runs` dict semantics are a developer-convenience poll
target — search runs are ephemeral (process-local), so persisting them
across restarts isn't the design intent. Any long-running job queue
belongs with the ARQ wiring in Batch 4, not here."

---

## STEP 6 — Handoff

- [ ] Push: `git push -u origin pillar3/batch-3.5.1`
- [ ] Print final SHA
- [ ] STOP — do not merge, do not tag, do not touch reviewer worktree

---

## Self-review

**Spec coverage.** Every requirement maps to a task:
- 4 profile routes gated → Deliverable A steps 1-5
- 2 search routes gated → Deliverable B steps 1-6
- Cross-user 404 (existence hiding) → Deliverable B step 3 SQL-less in-memory guard + test at step 1
- 8 new tests in `test_api_idor.py` → Deliverable A (4) + B (4)
- Per-route anchor list + grep + smoke + delta → STEP 5
- Push → STEP 6

**Placeholder scan.** The `<L>` line-number placeholders in STEP 5 §1 are filled at verification time, not plan time — which is correct, because they depend on the exact line where the new imports land. Everything else has concrete code.

**Scope honesty.** Storage stays single-file for profile. Search runs stay in-memory. No migration. No multi-tenancy rewrite. If any of those are needed, they are a separate batch.

---

_Last updated: 2026-04-19_
