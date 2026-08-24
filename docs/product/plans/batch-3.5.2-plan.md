# Pillar 3 — Batch 3.5.2 Per-User Profile Storage Plan

> **For agentic workers:** REQUIRED SUB-SKILLS: `superpowers:test-driven-development`, `superpowers:verification-before-completion`. Steps use checkbox syntax.

**Goal:** Close the 2026-04-19 re-audit gap (CurrentStatus.md §6 + §13 issue #5). Today, every authenticated user shares one `data/user_profile.json`; user A's CV overwrites user B's silently. This batch rebases profile storage onto a new `user_profiles` table keyed by `user_id`, migrates legacy JSON once on first load, and threads `user.id` through every call site.

**Architecture:** New `user_profiles` SQLite table; `storage.py` rewritten to use stdlib `sqlite3` (sync — matches the Click-CLI boundary and lets `async` call-sites keep their shape without `asyncio.run` wrappers for a single-row read); one-shot "hydrate from legacy JSON then delete JSON" triggers on first `load_profile(DEFAULT_TENANT_ID)`. Worker `score_and_ingest` builds a per-user `SearchConfig` cache so each user scores against their own profile.

**Tech stack:** existing — stdlib `sqlite3` for profile I/O, aiosqlite elsewhere, migration runner from Batch 2, Click CLI.

---

## POST-BATCH-3.5.1 BASELINE

Run 2026-04-19 on `pillar3/batch-3.5.2` HEAD (branched from `origin/main` @ `3c47243`).

```
Command: cd backend && python -m pytest tests/ --ignore=tests/test_main.py -q
Result:  [filled at baseline completion — /tmp/pytest_baseline_3_5_2.log]
```

---

## Scope-out list (explicit — DO NOT re-scope in a later round)

The following are deliberately **NOT** in this batch and should not creep in:

- **No profile sharing.** Two users can't grant access to each other's profiles.
- **No admin routes.** No `GET /api/admin/users/{id}/profile` or similar.
- **No profile delete endpoint** (`DELETE /api/profile`). Users can't self-delete today; audit doesn't flag it; adding it is scope creep.
- **No LinkedIn/GitHub cross-user sharing.** Each user has their own LinkedIn + GitHub data.
- **No `--user-id` flag on the CLI.** The CLI stays single-tenant by design (Deliverable E).
- **No async rewrite of `storage.py`.** Stays sync (`sqlite3`) to keep the Click-CLI boundary clean.
- **No per-user deduplication of jobs.** `jobs` stays a shared catalog (CLAUDE.md rule #10).
- **No profile migration from arbitrary old schemas.** Only the single `data/user_profile.json` → DEFAULT_TENANT_ID hydrate is supported.

---

## File-level plan

### Created files

| Path | Responsibility |
|---|---|
| `backend/migrations/0006_user_profiles.up.sql` | `CREATE TABLE user_profiles` |
| `backend/migrations/0006_user_profiles.down.sql` | `DROP TABLE user_profiles` |
| `backend/tests/test_profile_storage.py` | Per-user storage unit tests (~10) |

### Modified files

| Path | Change |
|---|---|
| `backend/src/services/profile/storage.py` | Rewrite: sqlite3-backed per-user CRUD + legacy JSON one-shot migration |
| `backend/src/main.py:295` | `load_profile()` → `load_profile(DEFAULT_TENANT_ID)` |
| `backend/src/cli.py:117,204` | import + `save_profile(profile, DEFAULT_TENANT_ID)` + CLI single-tenant comment (Deliverable E) |
| `backend/src/workers/tasks.py` | Per-user `SearchConfig` cache in `score_and_ingest`; `_default_search_config()` keeps DEFAULT_TENANT_ID semantics for test paths |
| `backend/src/api/routes/health.py:38` | `profile_exists()` → `profile_exists(DEFAULT_TENANT_ID)` (stays "single-deployment has CV data?") |
| `backend/src/api/routes/profile.py` | 4 handlers thread `user.id` into every storage call (load × 3, save × 3) |
| `backend/tests/test_profile.py` | Existing tests: pass `DEFAULT_TENANT_ID` to `save_profile` / `load_profile` (signature change) |
| `backend/tests/test_linkedin_github.py` | Same (lines 632, 633, 654) |
| `backend/tests/test_api.py` | The two `patch("src.api.routes.profile.load_profile", return_value=None)` call-sites (L59, L137) — update patch target if needed |
| `backend/tests/test_main_scheduler_wiring.py:109` | `lambda: stub` → `lambda uid: stub` |
| `backend/tests/test_tenancy_isolation.py` | Extend with HTTP-level profile + LinkedIn + GitHub per-user isolation tests |

---

## Phase A — Plan committed (this doc)

**Commit:** `docs(pillar3): Batch 3.5.2 per-user profile storage plan + baseline`

- [ ] Step A1: Write this file
- [ ] Step A2: Commit after baseline numbers land

---

## Deliverable A — Migration `0006_user_profiles`

**Files:**
- Create: `backend/migrations/0006_user_profiles.up.sql`
- Create: `backend/migrations/0006_user_profiles.down.sql`

### Up migration

```sql
-- 0006_user_profiles: per-user CV / preferences / LinkedIn / GitHub store.
-- Replaces the single-file data/user_profile.json from the pre-Batch-3.5.2
-- era. One row per user; CASCADE-deletes when the parent users row is
-- removed. JSON columns carry dataclass-serialised payloads — storage.py
-- handles the round-trip.

CREATE TABLE IF NOT EXISTS user_profiles (
    user_id TEXT PRIMARY KEY
        REFERENCES users(id) ON DELETE CASCADE,
    cv_data TEXT,
    preferences TEXT,
    linkedin_data TEXT,
    github_data TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
```

### Down migration

```sql
DROP TABLE IF EXISTS user_profiles;
```

### Tasks

- [ ] **A-Step 1:** Write both SQL files verbatim.
- [ ] **A-Step 2:** Run the round-trip proof:

```
cd backend
python -m migrations.runner up /tmp/rt.db
python -m migrations.runner down /tmp/rt.db
python -m migrations.runner up /tmp/rt.db
```

Expected: three runs, no errors. Final state has `user_profiles` table present.

- [ ] **A-Step 3:** Commit `feat(migration): add user_profiles table (0006)`.

---

## Deliverable B — Per-user storage refactor

**Signature contract (post-refactor):**

```python
def save_profile(profile: UserProfile, user_id: str) -> None: ...
def load_profile(user_id: str) -> Optional[UserProfile]: ...
def profile_exists(user_id: str) -> bool: ...
```

All three are **synchronous**. No new helpers — `delete_profile`, `merge_cv_into_profile`, `merge_linkedin_into_profile`, `merge_github_into_profile` are explicitly **out of scope** (they don't exist today; don't invent them).

### Tasks

- [ ] **B-Step 1:** Write `backend/tests/test_profile_storage.py` RED (~10 tests per the prompt's spec). Every test builds its own tmp DB path + runs migrations up.

- [ ] **B-Step 2:** Verify RED — `pytest tests/test_profile_storage.py -q`.

- [ ] **B-Step 3:** Rewrite `backend/src/services/profile/storage.py` with the SQL contract below.

```python
import json
import logging
import sqlite3
from dataclasses import asdict, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from src.core.settings import DATA_DIR, DB_PATH
from src.core.tenancy import DEFAULT_TENANT_ID
from src.services.profile.models import CVData, UserPreferences, UserProfile

logger = logging.getLogger("job360.profile.storage")

LEGACY_PROFILE_PATH = DATA_DIR / "user_profile.json"


def save_profile(profile: UserProfile, user_id: str) -> None:
    """Upsert a UserProfile for ``user_id``."""
    cv_json = json.dumps(asdict(profile.cv_data), default=str)
    pref_json = json.dumps(asdict(profile.preferences), default=str)
    now = datetime.now(timezone.utc).isoformat()
    with sqlite3.connect(str(DB_PATH)) as conn:
        conn.execute(
            """
            INSERT INTO user_profiles (user_id, cv_data, preferences, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                cv_data = excluded.cv_data,
                preferences = excluded.preferences,
                updated_at = excluded.updated_at
            """,
            (user_id, cv_json, pref_json, now),
        )
        conn.commit()
    logger.info("Profile saved for user %s", user_id)


def load_profile(user_id: str) -> Optional[UserProfile]:
    """Load the UserProfile for ``user_id``, or None if absent.

    Side effect: on the first call for DEFAULT_TENANT_ID, if the legacy
    ``data/user_profile.json`` exists and no DB row yet, hydrate the DB
    from JSON and delete the file. See ``_maybe_hydrate_legacy_json``.
    """
    _maybe_hydrate_legacy_json(user_id)
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT cv_data, preferences FROM user_profiles WHERE user_id = ?",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return None
    cv = CVData(**_filter_fields(json.loads(row[0]) if row[0] else {}, CVData))
    prefs = UserPreferences(
        **_filter_fields(json.loads(row[1]) if row[1] else {}, UserPreferences)
    )
    return UserProfile(cv_data=cv, preferences=prefs)


def profile_exists(user_id: str) -> bool:
    _maybe_hydrate_legacy_json(user_id)
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        return cur.fetchone() is not None


def _filter_fields(d: dict, cls) -> dict:
    valid = {f.name for f in fields(cls)}
    return {k: v for k, v in d.items() if k in valid}


def _maybe_hydrate_legacy_json(user_id: str) -> None:
    """One-shot: legacy data/user_profile.json → user_profiles[DEFAULT_TENANT_ID].

    Only fires for DEFAULT_TENANT_ID and only if the DB row is missing.
    On success: writes the row + deletes the JSON file. On failure: logs
    and leaves the JSON in place (user can retry). Idempotent — second
    call is a no-op because the DB row or the JSON file is gone.
    """
    if user_id != DEFAULT_TENANT_ID or not LEGACY_PROFILE_PATH.exists():
        return
    with sqlite3.connect(str(DB_PATH)) as conn:
        cur = conn.execute(
            "SELECT 1 FROM user_profiles WHERE user_id = ? LIMIT 1",
            (user_id,),
        )
        if cur.fetchone() is not None:
            return
    try:
        data = json.loads(LEGACY_PROFILE_PATH.read_text(encoding="utf-8"))
        cv = CVData(**_filter_fields(data.get("cv_data", {}), CVData))
        prefs = UserPreferences(
            **_filter_fields(data.get("preferences", {}), UserPreferences)
        )
        save_profile(UserProfile(cv_data=cv, preferences=prefs), user_id)
        LEGACY_PROFILE_PATH.unlink()
        logger.info(
            "Hydrated legacy %s into user_profiles[%s] and deleted JSON",
            LEGACY_PROFILE_PATH, user_id,
        )
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "Legacy profile hydrate failed (file stays): %s", e
        )
```

- [ ] **B-Step 4:** Run `pytest tests/test_profile_storage.py -q` → GREEN.

- [ ] **B-Step 5:** Update existing tests whose signatures broke:
  - `backend/tests/test_profile.py` (L174, L175, L184, L188, L194, L200, L436)
  - `backend/tests/test_linkedin_github.py` (L632, L633, L654)
  - Each `save_profile(profile)` → `save_profile(profile, DEFAULT_TENANT_ID)`; each `load_profile()` → `load_profile(DEFAULT_TENANT_ID)`; each `profile_exists()` → `profile_exists(DEFAULT_TENANT_ID)`. These tests use `tmp_path` fixtures that point DATA_DIR at a fresh dir — under the new backend, they need to ALSO set up a tmp DB and run migrations up.

- [ ] **B-Step 6:** Run full test_profile.py + test_linkedin_github.py. Update the DATA_DIR / DB_PATH monkeypatch fixtures to include migration init.

- [ ] **B-Step 7:** Commit `feat(profile): per-user storage backed by user_profiles table`.

---

## Deliverable C — Thread `user.id` through profile routes

**Files:**
- Modify: `backend/src/api/routes/profile.py:18, 66, 79, 126, 145, 147, 163, 166`
- Extend: `backend/tests/test_tenancy_isolation.py`

### Tasks

- [ ] **C-Step 1:** Every `load_profile(...)` / `save_profile(...)` call in `profile.py` gains `user.id` from the existing `Depends(require_user)` fixture (added in Batch 3.5.1).
- [ ] **C-Step 2:** Also update `backend/src/api/routes/health.py:38`: `profile_exists()` → `profile_exists(DEFAULT_TENANT_ID)` (CLI-era semantics preserved; public `/health` is unauthenticated and reports "does the single-tenant deployment have data?").
- [ ] **C-Step 3:** Also update `backend/src/main.py:295` (CLI run path): `load_profile()` → `load_profile(DEFAULT_TENANT_ID)`.
- [ ] **C-Step 4:** Extend `backend/tests/test_tenancy_isolation.py` with:

```python
def test_profile_isolation_alice_cannot_see_bobs_cv(api):
    """Alice POSTs a CV, Bob GETs /api/profile — Bob must not see Alice's data."""
    _register(api, "alice@example.com")
    # Upload an in-memory fake CV (text file tagged as .pdf will fail
    # parse_cv gracefully — what matters is the storage path isolates)
    _save_alice_profile_via_api(api, ...)
    r_alice = api.get("/api/profile")
    alice_data = r_alice.json()

    api.post("/api/auth/logout"); api.cookies.clear()
    _register(api, "bob@example.com")
    r_bob = api.get("/api/profile")
    # Bob has no profile yet → 404 (storage returns None)
    assert r_bob.status_code == 404

def test_linkedin_isolation_alice_not_visible_to_bob(api):
    # Same shape but via /api/profile/linkedin
    ...

def test_github_isolation_alice_not_visible_to_bob(api):
    # Same shape but via /api/profile/github
    ...
```

- [ ] **C-Step 5:** Run the full test_tenancy_isolation.py → GREEN.

- [ ] **C-Step 6:** Commit `feat(api): thread user.id through profile routes`.

---

## Deliverable D — Per-user profile in `score_and_ingest`

**Files:**
- Modify: `backend/src/workers/tasks.py:100-116` (scorer-selection block) + `:308-326` (`_default_search_config` deprecation comment)

### Tasks

- [ ] **D-Step 1:** Read `backend/src/workers/tasks.py::score_and_ingest` (L46-131) top-to-bottom. The existing flow has a single shared `default_scorer = JobScorer(_default_search_config())` used for every user in `targets`. This is the bug — per-user isolation was deferred in Batch 2.

- [ ] **D-Step 2:** Replace the scorer-selection block with a per-user cache:

```python
    scorer_fn: Optional[Callable[[str, Job], int]] = ctx.get("scorer")
    user_scorers: dict[str, JobScorer] = {}

    def _scorer_for(user_id: str) -> JobScorer:
        if user_id not in user_scorers:
            user_scorers[user_id] = JobScorer(_search_config_for(user_id))
        return user_scorers[user_id]

    for user_id, profile, threshold in targets:
        if not passes_prefilter(profile, job):
            continue
        if scorer_fn is not None:
            score = int(scorer_fn(user_id, job))
        else:
            score = int(_scorer_for(user_id).score(job))
```

And add:

```python
def _search_config_for(user_id: str) -> SearchConfig:
    """Build the user's SearchConfig from their stored profile, else defaults."""
    try:
        from src.services.profile.keyword_generator import generate_search_config
        from src.services.profile.storage import load_profile

        profile = load_profile(user_id)
        if profile and profile.is_complete:
            return generate_search_config(profile)
    except Exception:  # noqa: BLE001
        pass
    return SearchConfig.from_defaults()
```

The legacy `_default_search_config()` stays (for existing tests that inject `scorer=None` + expect defaults); its docstring updates to point at `_search_config_for` as the production path.

- [ ] **D-Step 3:** Add/update worker tests in `backend/tests/test_worker_tasks.py` proving two users get different `JobScorer` instances when they have different profiles. If the change is <50 lines (Deliverable D budget), land it. If bigger, STOP and leave a TODO comment at `backend/src/workers/tasks.py:<line>` pointing at this plan doc; worker change lands in Batch 3.5.3.

- [ ] **D-Step 4:** Commit `feat(workers): per-user profile in score_and_ingest` OR `chore(workers): TODO stub for per-user profile (3.5.3)` depending on scope.

---

## Deliverable E — CLI single-tenant comment

**Files:**
- Modify: `backend/src/cli.py:117, 204` (add a block comment above the `save_profile(profile, DEFAULT_TENANT_ID)` call explaining the CLI-always-DEFAULT_TENANT_ID contract).

### Tasks

- [ ] **E-Step 1:** Add the comment:

```python
# The CLI is single-tenant by design — every `python -m src.cli` invocation
# writes to the placeholder user (DEFAULT_TENANT_ID). Per-user profiles are
# the HTTP API's job (/api/profile with a session cookie). No --user-id
# flag; scope creep. Batch 3.5.2 intentionally leaves this contract in place.
```

- [ ] **E-Step 2:** The actual code change (passing DEFAULT_TENANT_ID) already happened in Deliverable B — this step is comment-only.

- [ ] **E-Step 3:** Commit folded into Deliverable B's commit (small enough) OR separate if the diff is substantial.

---

## STEP 5 — Verify before completion (no reviewer)

- [ ] **Storage call-site grep:** `grep -rn "load_profile\|save_profile\|profile_exists" backend/src/ backend/tests/` — every call passes `user_id`.
- [ ] **Migration round-trip:** `python -m migrations.runner up /tmp/rt.db && python -m migrations.runner down /tmp/rt.db && python -m migrations.runner up /tmp/rt.db` → all three clean.
- [ ] **Legacy file disposition:** `ls backend/data/user_profile.json` — file STILL on disk after the batch (do not delete the user's actual data; runtime self-migration handles it on first boot).
- [ ] **Test names + counts:** list every new test in `test_profile_storage.py` + `test_tenancy_isolation.py`.
- [ ] **Pytest delta:** BEFORE / AFTER / NEW / REGRESSIONS with exact numbers.
- [ ] **Worker change verdict:** landed (with `tasks.py:<line>`) OR TODO (with TODO `tasks.py:<line>`).
- [ ] **CLI smoke:** `python -m src.cli sources` runs without crash.

---

## STEP 6 — Handoff

- [ ] `git push -u origin pillar3/batch-3.5.2`
- [ ] Report final SHA. STOP.

---

## Self-review

**Spec coverage.** Every requirement maps to a task:
- Migration (A) → Deliverable A
- Storage refactor + legacy JSON one-shot migration (B) → Deliverable B
- `user.id` threading (C) → Deliverable C
- Worker per-user profile (D) → Deliverable D with budget cap
- CLI comment (E) → Deliverable E

**Placeholder scan.** Line-number placeholders in STEP 5 §1 and commit-message hashes are filled at verification time. Everything else is concrete code.

**Scope honesty.** The scope-out list above is explicit and copy-pasted into the completion report so future readers know what was deliberately excluded.

---

_Last updated: 2026-04-19_
