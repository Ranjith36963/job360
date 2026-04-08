# FastAPI Backend API Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a FastAPI backend that bridges the existing Python pipeline to the Next.js frontend, implementing all 21 endpoints defined in `frontend/lib/api.ts`.

**Architecture:** Thin API layer wrapping existing modules (`database.py`, `main.py`, `profile/*`, `csv_export.py`). Two new DB tables (`user_actions`, `applications`) for features the Streamlit dashboard doesn't have. In-memory dict for search progress tracking (single-user tool, no persistence needed). All routes async, CORS enabled for `localhost:3000`.

**Tech Stack:** FastAPI, Pydantic v2, aiosqlite (existing), uvicorn, python-multipart (file uploads)

---

## File Structure

```
src/api/
├── main.py              # FastAPI app, CORS, lifespan (DB init/close)
├── models.py            # All Pydantic request/response models (matches frontend/lib/types.ts)
├── dependencies.py      # Shared dependencies: get_db(), temp file helpers
├── routes/
│   ├── health.py        # GET /api/health, /api/status, /api/sources
│   ├── jobs.py          # GET /api/jobs, /api/jobs/{id}, /api/jobs/export
│   ├── actions.py       # POST/DELETE /api/jobs/{id}/action, GET /api/actions, /api/actions/counts
│   ├── profile.py       # GET/POST /api/profile, POST /api/profile/linkedin, /api/profile/github
│   ├── search.py        # POST /api/search, GET /api/search/{id}/status
│   └── pipeline.py      # GET/POST /api/pipeline/*, POST /api/pipeline/{id}/advance
```

**Existing files to modify:**
- `src/storage/database.py` — Add `user_actions` + `applications` tables to `init_db()`, add query methods
- `requirements.txt` — Add `fastapi`, `uvicorn[standard]`, `python-multipart`

**Test file:**
- `tests/test_api.py` — All API endpoint tests using `httpx.AsyncClient` + `TestClient`

---

## Task 1: Add Dependencies and Pydantic Models

**Files:**
- Modify: `requirements.txt`
- Create: `src/api/__init__.py`
- Create: `src/api/models.py`

- [ ] **Step 1: Add FastAPI dependencies to requirements.txt**

Add these lines to `requirements.txt`:
```
fastapi>=0.115.0
uvicorn[standard]>=0.30.0
python-multipart>=0.0.9
httpx>=0.27.0
```

- [ ] **Step 2: Create empty `__init__.py`**

```bash
touch src/api/__init__.py
touch src/api/routes/__init__.py
```

- [ ] **Step 3: Install dependencies**

```bash
pip install -r requirements.txt
```

- [ ] **Step 4: Create Pydantic models**

Create `src/api/models.py` with all request/response models matching `frontend/lib/types.ts`:

```python
"""Pydantic models for the FastAPI backend — matches frontend/lib/types.ts."""
from __future__ import annotations

from pydantic import BaseModel


# --- Health & Status ---

class HealthResponse(BaseModel):
    status: str
    version: str


class SourceInfo(BaseModel):
    name: str
    type: str
    health: dict


class SourcesResponse(BaseModel):
    sources: list[SourceInfo]


class StatusResponse(BaseModel):
    jobs_total: int
    last_run: dict | None
    sources_active: int
    sources_total: int
    profile_exists: bool


# --- Jobs ---

class JobResponse(BaseModel):
    id: int
    title: str
    company: str
    location: str
    salary: str | None = None
    match_score: int
    source: str
    date_found: str
    apply_url: str
    visa_flag: bool
    job_type: str = ""
    experience_level: str = ""
    # 8D score breakdown (populated when detailed scoring is available)
    role: int = 0
    skill: int = 0
    seniority: int = 0
    experience: int = 0
    credentials: int = 0
    location_score: int = 0
    recency: int = 0
    semantic: int = 0
    penalty: int = 0
    # Skill matching
    matched_skills: list[str] = []
    missing_required: list[str] = []
    transferable_skills: list[str] = []
    # User interaction
    action: str | None = None
    bucket: str = ""


class JobListResponse(BaseModel):
    jobs: list[JobResponse]
    total: int
    filters_applied: dict


# --- Actions ---

class ActionRequest(BaseModel):
    action: str  # "liked", "applied", "not_interested"
    notes: str = ""


class ActionResponse(BaseModel):
    ok: bool
    job_id: int
    action: str


class ActionsListResponse(BaseModel):
    actions: list[ActionResponse]


# --- Profile ---

class ProfileSummary(BaseModel):
    is_complete: bool
    job_titles: list[str]
    skills_count: int
    cv_length: int
    has_linkedin: bool
    has_github: bool
    education: list[str]
    experience_level: str


class ProfileResponse(BaseModel):
    summary: ProfileSummary
    preferences: dict


class LinkedInResponse(BaseModel):
    ok: bool
    merged: bool


class GitHubResponse(BaseModel):
    ok: bool
    merged: bool


# --- Search ---

class SearchStartResponse(BaseModel):
    run_id: str
    status: str


class SearchStatusResponse(BaseModel):
    run_id: str
    status: str
    progress: str
    result: dict | None = None


# --- Pipeline ---

class PipelineApplication(BaseModel):
    job_id: int
    stage: str
    created_at: str
    updated_at: str
    notes: str = ""
    title: str = ""
    company: str = ""


class PipelineListResponse(BaseModel):
    applications: list[PipelineApplication]


class PipelineAdvanceRequest(BaseModel):
    stage: str


class PipelineRemindersResponse(BaseModel):
    reminders: list[PipelineApplication]
```

- [ ] **Step 5: Commit**

```bash
git add requirements.txt src/api/__init__.py src/api/routes/__init__.py src/api/models.py
git commit -m "feat(api): add FastAPI deps and Pydantic models matching frontend types"
```

---

## Task 2: FastAPI App Scaffold and Dependencies

**Files:**
- Create: `src/api/main.py`
- Create: `src/api/dependencies.py`

- [ ] **Step 1: Write test for health endpoint**

Create `tests/test_api.py`:

```python
"""Tests for FastAPI backend API."""
import pytest
from httpx import ASGITransport, AsyncClient

from src.api.main import app


@pytest.mark.asyncio
async def test_health_returns_ok():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "version" in data
```

- [ ] **Step 2: Run test to verify it fails**

```bash
python -m pytest tests/test_api.py::test_health_returns_ok -v
```
Expected: FAIL — `src.api.main` does not exist yet.

- [ ] **Step 3: Create dependencies module**

Create `src/api/dependencies.py`:

```python
"""Shared dependencies for FastAPI routes."""
import tempfile
import os
from pathlib import Path

from src.storage.database import JobDatabase
from src.config.settings import DB_PATH

_db: JobDatabase | None = None


async def init_db() -> JobDatabase:
    """Initialize and return the shared database connection."""
    global _db
    if _db is None:
        _db = JobDatabase(str(DB_PATH))
        await _db.init_db()
    return _db


async def get_db() -> JobDatabase:
    """FastAPI dependency — returns the initialized database."""
    if _db is None:
        await init_db()
    return _db


async def close_db():
    """Close the database connection."""
    global _db
    if _db is not None:
        await _db.close()
        _db = None


def save_upload_to_temp(content: bytes, suffix: str) -> str:
    """Save uploaded file bytes to a temp file, return path."""
    fd, path = tempfile.mkstemp(suffix=suffix)
    with os.fdopen(fd, "wb") as f:
        f.write(content)
    return path
```

- [ ] **Step 4: Create FastAPI app**

Create `src/api/main.py`:

```python
"""FastAPI application for Job360 backend."""
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.dependencies import init_db, close_db
from src.api.routes import health, jobs, actions, profile, search, pipeline


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize DB on startup, close on shutdown."""
    await init_db()
    yield
    await close_db()


app = FastAPI(title="Job360 API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health.router, prefix="/api")
app.include_router(jobs.router, prefix="/api")
app.include_router(actions.router, prefix="/api")
app.include_router(profile.router, prefix="/api")
app.include_router(search.router, prefix="/api")
app.include_router(pipeline.router, prefix="/api")
```

- [ ] **Step 5: Create stub health route**

Create `src/api/routes/health.py`:

```python
"""Health, status, and sources endpoints."""
from fastapi import APIRouter

from src.api.models import HealthResponse

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")
```

- [ ] **Step 6: Create stub route files (empty routers)**

Create minimal stubs for remaining routes so `main.py` imports don't fail:

`src/api/routes/jobs.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["jobs"])
```

`src/api/routes/actions.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["actions"])
```

`src/api/routes/profile.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["profile"])
```

`src/api/routes/search.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["search"])
```

`src/api/routes/pipeline.py`:
```python
from fastapi import APIRouter
router = APIRouter(tags=["pipeline"])
```

- [ ] **Step 7: Run test to verify it passes**

```bash
python -m pytest tests/test_api.py::test_health_returns_ok -v
```
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add src/api/ tests/test_api.py
git commit -m "feat(api): FastAPI app scaffold with health endpoint and CORS"
```

---

## Task 3: Database Schema — user_actions and applications Tables

**Files:**
- Modify: `src/storage/database.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test for action insert**

Add to `tests/test_api.py`:

```python
import aiosqlite
from src.storage.database import JobDatabase


@pytest.mark.asyncio
async def test_db_insert_and_get_action(tmp_path):
    db = JobDatabase(str(tmp_path / "test.db"))
    await db.init_db()
    # Insert a job first
    from src.models import Job
    job = Job(title="AI Engineer", company="TestCo", apply_url="https://x.com",
              source="test", date_found="2026-04-07T00:00:00Z", match_score=80)
    await db.insert_job(job)
    await db.commit()
    # Insert action
    await db.insert_action(1, "liked", "Great role")
    actions = await db.get_actions()
    assert len(actions) == 1
    assert actions[0]["job_id"] == 1
    assert actions[0]["action"] == "liked"
    await db.close()


@pytest.mark.asyncio
async def test_db_insert_and_advance_application(tmp_path):
    db = JobDatabase(str(tmp_path / "test.db"))
    await db.init_db()
    from src.models import Job
    job = Job(title="AI Engineer", company="TestCo", apply_url="https://x.com",
              source="test", date_found="2026-04-07T00:00:00Z", match_score=80)
    await db.insert_job(job)
    await db.commit()
    # Create application
    app_row = await db.create_application(1)
    assert app_row["stage"] == "applied"
    # Advance
    app_row = await db.advance_application(1, "interview")
    assert app_row["stage"] == "interview"
    await db.close()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_api.py::test_db_insert_and_get_action tests/test_api.py::test_db_insert_and_advance_application -v
```
Expected: FAIL — `insert_action` not defined.

- [ ] **Step 3: Add tables and methods to database.py**

Add to `src/storage/database.py` `init_db()` method, after the existing `CREATE TABLE` statements:

```python
await self._conn.execute("""
    CREATE TABLE IF NOT EXISTS user_actions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        action TEXT NOT NULL,
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        UNIQUE(job_id)
    )
""")
await self._conn.execute("""
    CREATE TABLE IF NOT EXISTS applications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        job_id INTEGER NOT NULL,
        stage TEXT NOT NULL DEFAULT 'applied',
        notes TEXT DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(job_id)
    )
""")
```

Add these methods to the `JobDatabase` class:

```python
# --- User Actions ---

async def insert_action(self, job_id: int, action: str, notes: str = "") -> dict:
    now = datetime.now(timezone.utc).isoformat()
    await self._conn.execute(
        "INSERT OR REPLACE INTO user_actions (job_id, action, notes, created_at) VALUES (?, ?, ?, ?)",
        (job_id, action, notes, now),
    )
    await self._conn.commit()
    return {"job_id": job_id, "action": action, "notes": notes, "created_at": now}

async def delete_action(self, job_id: int) -> None:
    await self._conn.execute("DELETE FROM user_actions WHERE job_id = ?", (job_id,))
    await self._conn.commit()

async def get_actions(self) -> list[dict]:
    cursor = await self._conn.execute(
        "SELECT job_id, action, notes, created_at FROM user_actions ORDER BY created_at DESC"
    )
    rows = await cursor.fetchall()
    return [{"job_id": r[0], "action": r[1], "notes": r[2], "created_at": r[3]} for r in rows]

async def get_action_counts(self) -> dict[str, int]:
    cursor = await self._conn.execute(
        "SELECT action, COUNT(*) FROM user_actions GROUP BY action"
    )
    rows = await cursor.fetchall()
    return {r[0]: r[1] for r in rows}

async def get_action_for_job(self, job_id: int) -> str | None:
    cursor = await self._conn.execute(
        "SELECT action FROM user_actions WHERE job_id = ?", (job_id,)
    )
    row = await cursor.fetchone()
    return row[0] if row else None

# --- Applications (Pipeline) ---

async def create_application(self, job_id: int) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    await self._conn.execute(
        "INSERT OR IGNORE INTO applications (job_id, stage, created_at, updated_at) VALUES (?, 'applied', ?, ?)",
        (job_id, now, now),
    )
    await self._conn.commit()
    return await self._get_application(job_id)

async def advance_application(self, job_id: int, stage: str) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    await self._conn.execute(
        "UPDATE applications SET stage = ?, updated_at = ? WHERE job_id = ?",
        (stage, now, job_id),
    )
    await self._conn.commit()
    return await self._get_application(job_id)

async def _get_application(self, job_id: int) -> dict:
    cursor = await self._conn.execute(
        """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                  j.title, j.company
           FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
           WHERE a.job_id = ?""",
        (job_id,),
    )
    row = await cursor.fetchone()
    if not row:
        return {}
    return {
        "job_id": row[0], "stage": row[1], "created_at": row[2],
        "updated_at": row[3], "notes": row[4] or "",
        "title": row[5] or "", "company": row[6] or "",
    }

async def get_applications(self, stage: str | None = None) -> list[dict]:
    if stage:
        cursor = await self._conn.execute(
            """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                      j.title, j.company
               FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
               WHERE a.stage = ? ORDER BY a.updated_at DESC""",
            (stage,),
        )
    else:
        cursor = await self._conn.execute(
            """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                      j.title, j.company
               FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
               ORDER BY a.updated_at DESC"""
        )
    rows = await cursor.fetchall()
    return [
        {"job_id": r[0], "stage": r[1], "created_at": r[2], "updated_at": r[3],
         "notes": r[4] or "", "title": r[5] or "", "company": r[6] or ""}
        for r in rows
    ]

async def get_application_counts(self) -> dict[str, int]:
    cursor = await self._conn.execute(
        "SELECT stage, COUNT(*) FROM applications GROUP BY stage"
    )
    return {r[0]: r[1] for r in await cursor.fetchall()}

async def get_stale_applications(self, days: int = 7) -> list[dict]:
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    cursor = await self._conn.execute(
        """SELECT a.job_id, a.stage, a.created_at, a.updated_at, a.notes,
                  j.title, j.company
           FROM applications a LEFT JOIN jobs j ON a.job_id = j.id
           WHERE a.updated_at < ? AND a.stage NOT IN ('offer', 'rejected')
           ORDER BY a.updated_at ASC""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    return [
        {"job_id": r[0], "stage": r[1], "created_at": r[2], "updated_at": r[3],
         "notes": r[4] or "", "title": r[5] or "", "company": r[6] or ""}
        for r in rows
    ]

async def get_job_by_id(self, job_id: int) -> dict | None:
    cursor = await self._conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,))
    row = await cursor.fetchone()
    if not row:
        return None
    cols = [d[0] for d in cursor.description]
    return dict(zip(cols, row))
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_api.py::test_db_insert_and_get_action tests/test_api.py::test_db_insert_and_advance_application -v
```
Expected: PASS

- [ ] **Step 5: Run existing DB tests to verify no regressions**

```bash
python -m pytest tests/test_database.py -v
```
Expected: All 9 pass.

- [ ] **Step 6: Commit**

```bash
git add src/storage/database.py tests/test_api.py
git commit -m "feat(db): add user_actions and applications tables with query methods"
```

---

## Task 4: Health, Status, and Sources Routes

**Files:**
- Modify: `src/api/routes/health.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing tests**

Add to `tests/test_api.py`:

```python
@pytest.mark.asyncio
async def test_status_returns_counts():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs_total" in data
    assert "sources_total" in data
    assert "profile_exists" in data


@pytest.mark.asyncio
async def test_sources_returns_list():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/sources")
    assert resp.status_code == 200
    data = resp.json()
    assert "sources" in data
    assert len(data["sources"]) == 48
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
python -m pytest tests/test_api.py::test_status_returns_counts tests/test_api.py::test_sources_returns_list -v
```

- [ ] **Step 3: Implement health routes**

Update `src/api/routes/health.py`:

```python
"""Health, status, and sources endpoints."""
from fastapi import APIRouter, Depends

from src.api.dependencies import get_db
from src.api.models import HealthResponse, StatusResponse, SourceInfo, SourcesResponse
from src.main import SOURCE_REGISTRY
from src.profile.storage import profile_exists
from src.storage.database import JobDatabase

router = APIRouter(tags=["health"])


@router.get("/health", response_model=HealthResponse)
async def health_check():
    return HealthResponse(status="ok", version="1.0.0")


@router.get("/status", response_model=StatusResponse)
async def get_status(db: JobDatabase = Depends(get_db)):
    jobs_total = await db.count_jobs()
    run_logs = await db.get_run_logs(limit=1)
    last_run = run_logs[0] if run_logs else None
    sources_total = len(SOURCE_REGISTRY)
    # Count sources that returned >0 in last run
    sources_active = 0
    if last_run and last_run.get("per_source"):
        sources_active = sum(1 for v in last_run["per_source"].values() if v > 0)
    return StatusResponse(
        jobs_total=jobs_total,
        last_run=last_run,
        sources_active=sources_active,
        sources_total=sources_total,
        profile_exists=profile_exists(),
    )


@router.get("/sources", response_model=SourcesResponse)
async def list_sources():
    sources = []
    for name in sorted(SOURCE_REGISTRY.keys()):
        cls = SOURCE_REGISTRY[name]
        source_type = "free"
        if hasattr(cls, "__init__") and "api_key" in (cls.__init__.__code__.co_varnames if hasattr(cls.__init__, '__code__') else ()):
            source_type = "keyed"
        sources.append(SourceInfo(name=name, type=source_type, health={}))
    return SourcesResponse(sources=sources)
```

- [ ] **Step 4: Run tests to verify they pass**

```bash
python -m pytest tests/test_api.py::test_health_returns_ok tests/test_api.py::test_status_returns_counts tests/test_api.py::test_sources_returns_list -v
```

- [ ] **Step 5: Commit**

```bash
git add src/api/routes/health.py tests/test_api.py
git commit -m "feat(api): implement /status and /sources endpoints"
```

---

## Task 5: Jobs List and Detail Routes

**Files:**
- Modify: `src/api/routes/jobs.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test for jobs list**

Add to `tests/test_api.py`:

```python
@pytest.mark.asyncio
async def test_jobs_list_returns_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "jobs" in data
    assert "total" in data
    assert isinstance(data["jobs"], list)
```

- [ ] **Step 2: Implement jobs routes**

Update `src/api/routes/jobs.py`:

```python
"""Job listing, detail, and export endpoints."""
import asyncio
from datetime import datetime, timezone, timedelta
from io import BytesIO
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse

from src.api.dependencies import get_db
from src.api.models import JobResponse, JobListResponse
from src.storage.database import JobDatabase
from src.storage.csv_export import export_to_csv, HEADERS
from src.models import Job

router = APIRouter(tags=["jobs"])


def _format_salary(row: dict) -> str | None:
    s_min = row.get("salary_min")
    s_max = row.get("salary_max")
    if s_min and s_max:
        return f"{int(s_min)}-{int(s_max)}"
    if s_min:
        return str(int(s_min))
    if s_max:
        return str(int(s_max))
    return None


def _compute_bucket(date_found: str) -> str:
    try:
        dt = datetime.fromisoformat(date_found.replace("Z", "+00:00"))
        hours = (datetime.now(timezone.utc) - dt).total_seconds() / 3600
    except (ValueError, TypeError):
        return "7d"
    if hours <= 24:
        return "24h"
    if hours <= 48:
        return "48h"
    if hours <= 72:
        return "3d"
    if hours <= 120:
        return "5d"
    if hours <= 168:
        return "7d"
    return "7d"


def _row_to_job_response(row: dict, action: str | None = None) -> JobResponse:
    return JobResponse(
        id=row.get("id", 0),
        title=row.get("title", ""),
        company=row.get("company", ""),
        location=row.get("location", ""),
        salary=_format_salary(row),
        match_score=row.get("match_score", 0),
        source=row.get("source", ""),
        date_found=row.get("date_found", ""),
        apply_url=row.get("apply_url", ""),
        visa_flag=bool(row.get("visa_flag", 0)),
        experience_level=row.get("experience_level", ""),
        action=action,
        bucket=_compute_bucket(row.get("date_found", "")),
    )


@router.get("/jobs", response_model=JobListResponse)
async def list_jobs(
    hours: Optional[int] = Query(None),
    min_score: Optional[int] = Query(None),
    source: Optional[str] = Query(None),
    bucket: Optional[str] = Query(None),
    action: Optional[str] = Query(None),
    visa_only: Optional[bool] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    db: JobDatabase = Depends(get_db),
):
    days = (hours // 24) + 1 if hours else 7
    score = min_score or 0
    all_rows = await db.get_recent_jobs(days=days, min_score=score)

    # Apply additional filters
    filtered = all_rows
    if hours:
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        filtered = [r for r in filtered if r.get("date_found", "") >= cutoff]
    if source:
        filtered = [r for r in filtered if r.get("source") == source]
    if visa_only:
        filtered = [r for r in filtered if r.get("visa_flag")]
    if bucket and bucket != "all":
        filtered = [r for r in filtered if _compute_bucket(r.get("date_found", "")) == bucket]

    total = len(filtered)
    page = filtered[offset:offset + limit]

    # Batch-fetch actions for visible jobs
    jobs = []
    for row in page:
        job_action = await db.get_action_for_job(row.get("id", 0))
        if action and job_action != action:
            continue
        jobs.append(_row_to_job_response(row, action=job_action))

    return JobListResponse(
        jobs=jobs,
        total=total,
        filters_applied={"hours": hours, "min_score": min_score, "source": source,
                         "bucket": bucket, "visa_only": visa_only},
    )


@router.get("/jobs/export")
async def export_jobs(db: JobDatabase = Depends(get_db)):
    rows = await db.get_recent_jobs(days=7, min_score=0)
    import csv
    from io import StringIO
    output = StringIO()
    writer = csv.writer(output)
    writer.writerow(HEADERS)
    for row in rows:
        writer.writerow([
            row.get("title", ""), row.get("company", ""), row.get("location", ""),
            _format_salary(row) or "", row.get("match_score", 0),
            row.get("apply_url", ""), row.get("source", ""),
            row.get("date_found", ""), "Yes" if row.get("visa_flag") else "No",
        ])
    content = output.getvalue().encode("utf-8")
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return StreamingResponse(
        BytesIO(content),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="job360_export_{ts}.csv"'},
    )


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(job_id: int, db: JobDatabase = Depends(get_db)):
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    action = await db.get_action_for_job(job_id)
    return _row_to_job_response(row, action=action)
```

- [ ] **Step 3: Run tests**

```bash
python -m pytest tests/test_api.py -v
```

- [ ] **Step 4: Commit**

```bash
git add src/api/routes/jobs.py tests/test_api.py
git commit -m "feat(api): implement /jobs list, detail, and export endpoints"
```

---

## Task 6: User Actions Routes

**Files:**
- Modify: `src/api/routes/actions.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_action_counts_empty():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/actions/counts")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
```

- [ ] **Step 2: Implement actions routes**

Update `src/api/routes/actions.py`:

```python
"""Job action endpoints (bookmark, apply, reject)."""
from fastapi import APIRouter, Depends, HTTPException

from src.api.dependencies import get_db
from src.api.models import ActionRequest, ActionResponse, ActionsListResponse
from src.storage.database import JobDatabase

router = APIRouter(tags=["actions"])

VALID_ACTIONS = {"liked", "applied", "not_interested"}


@router.post("/jobs/{job_id}/action", response_model=ActionResponse)
async def set_action(job_id: int, body: ActionRequest, db: JobDatabase = Depends(get_db)):
    if body.action not in VALID_ACTIONS:
        raise HTTPException(status_code=400, detail=f"Invalid action. Must be one of: {VALID_ACTIONS}")
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    await db.insert_action(job_id, body.action, body.notes)
    return ActionResponse(ok=True, job_id=job_id, action=body.action)


@router.delete("/jobs/{job_id}/action", response_model=ActionResponse)
async def remove_action(job_id: int, db: JobDatabase = Depends(get_db)):
    await db.delete_action(job_id)
    return ActionResponse(ok=True, job_id=job_id, action="")


@router.get("/actions", response_model=ActionsListResponse)
async def list_actions(db: JobDatabase = Depends(get_db)):
    actions = await db.get_actions()
    return ActionsListResponse(
        actions=[ActionResponse(ok=True, job_id=a["job_id"], action=a["action"]) for a in actions]
    )


@router.get("/actions/counts")
async def action_counts(db: JobDatabase = Depends(get_db)):
    counts = await db.get_action_counts()
    return {a: counts.get(a, 0) for a in VALID_ACTIONS}
```

- [ ] **Step 3: Run tests and commit**

```bash
python -m pytest tests/test_api.py -v
git add src/api/routes/actions.py tests/test_api.py
git commit -m "feat(api): implement job action CRUD endpoints"
```

---

## Task 7: Profile Routes

**Files:**
- Modify: `src/api/routes/profile.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Write failing test**

```python
@pytest.mark.asyncio
async def test_profile_returns_404_when_none():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/profile")
    # 404 when no profile exists
    assert resp.status_code == 404
```

- [ ] **Step 2: Implement profile routes**

Update `src/api/routes/profile.py`:

```python
"""Profile management endpoints (CV upload, LinkedIn, GitHub)."""
import json
import asyncio
from typing import Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from src.api.dependencies import get_db, save_upload_to_temp
from src.api.models import ProfileResponse, ProfileSummary, LinkedInResponse, GitHubResponse
from src.profile.storage import load_profile, save_profile, profile_exists
from src.profile.models import CVData, UserPreferences, UserProfile
from src.profile.cv_parser import parse_cv
from src.profile.preferences import merge_cv_and_preferences
from src.profile.linkedin_parser import parse_linkedin_zip, enrich_cv_from_linkedin
from src.profile.github_enricher import fetch_github_profile, enrich_cv_from_github

router = APIRouter(tags=["profile"])


def _profile_to_response(profile: UserProfile) -> ProfileResponse:
    cv = profile.cv_data
    prefs = profile.preferences
    return ProfileResponse(
        summary=ProfileSummary(
            is_complete=profile.is_complete,
            job_titles=cv.job_titles,
            skills_count=len(cv.skills),
            cv_length=len(cv.raw_text),
            has_linkedin=bool(cv.linkedin_skills),
            has_github=bool(cv.github_languages),
            education=cv.education,
            experience_level=prefs.experience_level,
        ),
        preferences={
            "target_job_titles": prefs.target_job_titles,
            "additional_skills": prefs.additional_skills,
            "excluded_skills": prefs.excluded_skills,
            "preferred_locations": prefs.preferred_locations,
            "industries": prefs.industries,
            "salary_min": prefs.salary_min,
            "salary_max": prefs.salary_max,
            "work_arrangement": prefs.work_arrangement,
            "experience_level": prefs.experience_level,
            "negative_keywords": prefs.negative_keywords,
            "about_me": prefs.about_me,
        },
    )


@router.get("/profile", response_model=ProfileResponse)
async def get_profile():
    profile = load_profile()
    if not profile:
        raise HTTPException(status_code=404, detail="No profile found")
    return _profile_to_response(profile)


@router.post("/profile", response_model=ProfileResponse)
async def update_profile(
    cv: Optional[UploadFile] = File(None),
    preferences: Optional[str] = Form(None),
):
    existing = load_profile() or UserProfile()
    cv_data = existing.cv_data

    # Parse CV if uploaded
    if cv:
        content = await cv.read()
        tmp = save_upload_to_temp(content, suffix=f".{cv.filename.rsplit('.', 1)[-1]}")
        try:
            cv_data = parse_cv(tmp)
        finally:
            import os
            os.unlink(tmp)

    # Parse preferences if provided
    prefs = existing.preferences
    if preferences:
        pref_dict = json.loads(preferences)
        prefs = UserPreferences(
            target_job_titles=pref_dict.get("target_job_titles", prefs.target_job_titles),
            additional_skills=pref_dict.get("additional_skills", prefs.additional_skills),
            excluded_skills=pref_dict.get("excluded_skills", prefs.excluded_skills),
            preferred_locations=pref_dict.get("preferred_locations", prefs.preferred_locations),
            industries=pref_dict.get("industries", prefs.industries),
            salary_min=pref_dict.get("salary_min", prefs.salary_min),
            salary_max=pref_dict.get("salary_max", prefs.salary_max),
            work_arrangement=pref_dict.get("work_arrangement", prefs.work_arrangement),
            experience_level=pref_dict.get("experience_level", prefs.experience_level),
            negative_keywords=pref_dict.get("negative_keywords", prefs.negative_keywords),
            about_me=pref_dict.get("about_me", prefs.about_me),
        )

    # Merge CV data with preferences
    if cv_data.skills or cv_data.job_titles:
        prefs = merge_cv_and_preferences(cv_data.skills, cv_data.job_titles, prefs)

    profile = UserProfile(cv_data=cv_data, preferences=prefs)
    save_profile(profile)
    return _profile_to_response(profile)


@router.post("/profile/linkedin", response_model=LinkedInResponse)
async def upload_linkedin(file: UploadFile = File(...)):
    content = await file.read()
    linkedin_data = parse_linkedin_zip_from_bytes(content)

    existing = load_profile() or UserProfile()
    enriched_cv = enrich_cv_from_linkedin(existing.cv_data, linkedin_data)
    profile = UserProfile(cv_data=enriched_cv, preferences=existing.preferences)
    save_profile(profile)
    return LinkedInResponse(ok=True, merged=True)


def parse_linkedin_zip_from_bytes(content: bytes) -> dict:
    """Parse LinkedIn ZIP from bytes using temp file."""
    import tempfile, os
    fd, tmp = tempfile.mkstemp(suffix=".zip")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(content)
        return parse_linkedin_zip(tmp)
    finally:
        os.unlink(tmp)


@router.post("/profile/github", response_model=GitHubResponse)
async def enrich_github(username: str = Form(...)):
    github_data = await fetch_github_profile(username)
    if not github_data.get("repositories"):
        raise HTTPException(status_code=404, detail="GitHub user not found or no public repos")

    existing = load_profile() or UserProfile()
    enriched_cv = enrich_cv_from_github(existing.cv_data, github_data)
    prefs = existing.preferences
    prefs.github_username = username
    profile = UserProfile(cv_data=enriched_cv, preferences=prefs)
    save_profile(profile)
    return GitHubResponse(ok=True, merged=True)
```

- [ ] **Step 3: Run tests and commit**

```bash
python -m pytest tests/test_api.py -v
git add src/api/routes/profile.py tests/test_api.py
git commit -m "feat(api): implement profile CRUD with CV/LinkedIn/GitHub upload"
```

---

## Task 8: Search Routes (with Progress Tracking)

**Files:**
- Modify: `src/api/routes/search.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Implement search routes with in-memory progress tracking**

Update `src/api/routes/search.py`:

```python
"""Search trigger and progress polling endpoints."""
import asyncio
import uuid
from typing import Optional

from fastapi import APIRouter, Query

from src.api.models import SearchStartResponse, SearchStatusResponse
from src.main import run_search

router = APIRouter(tags=["search"])

# In-memory search tracking (single-user tool)
_runs: dict[str, dict] = {}


@router.post("/search", response_model=SearchStartResponse)
async def start_search(
    source: Optional[str] = Query(None),
    safe: Optional[bool] = Query(None),
):
    run_id = uuid.uuid4().hex[:12]
    _runs[run_id] = {"status": "running", "progress": "Starting...", "result": None}

    async def _run():
        try:
            _runs[run_id]["progress"] = "Fetching from sources..."
            result = await run_search(
                source_filter=source,
                no_notify=True,
            )
            _runs[run_id]["status"] = "completed"
            _runs[run_id]["progress"] = "Done"
            _runs[run_id]["result"] = result
        except Exception as e:
            _runs[run_id]["status"] = "failed"
            _runs[run_id]["progress"] = str(e)

    asyncio.create_task(_run())
    return SearchStartResponse(run_id=run_id, status="running")


@router.get("/search/{run_id}/status", response_model=SearchStatusResponse)
async def search_status(run_id: str):
    run = _runs.get(run_id)
    if not run:
        return SearchStatusResponse(
            run_id=run_id, status="not_found", progress="Unknown run ID", result=None
        )
    return SearchStatusResponse(
        run_id=run_id,
        status=run["status"],
        progress=run["progress"],
        result=run["result"],
    )
```

- [ ] **Step 2: Run tests and commit**

```bash
python -m pytest tests/test_api.py -v
git add src/api/routes/search.py tests/test_api.py
git commit -m "feat(api): implement search trigger with async progress tracking"
```

---

## Task 9: Pipeline Routes

**Files:**
- Modify: `src/api/routes/pipeline.py`
- Test: `tests/test_api.py`

- [ ] **Step 1: Implement pipeline routes**

Update `src/api/routes/pipeline.py`:

```python
"""Application pipeline (Kanban board) endpoints."""
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from src.api.dependencies import get_db
from src.api.models import (
    PipelineApplication, PipelineListResponse,
    PipelineAdvanceRequest, PipelineRemindersResponse,
)
from src.storage.database import JobDatabase

router = APIRouter(tags=["pipeline"])

VALID_STAGES = {"applied", "outreach", "interview", "offer", "rejected"}


@router.get("/pipeline", response_model=PipelineListResponse)
async def list_applications(
    stage: Optional[str] = Query(None),
    db: JobDatabase = Depends(get_db),
):
    apps = await db.get_applications(stage=stage)
    return PipelineListResponse(
        applications=[PipelineApplication(**a) for a in apps]
    )


@router.get("/pipeline/counts")
async def pipeline_counts(db: JobDatabase = Depends(get_db)):
    counts = await db.get_application_counts()
    return {s: counts.get(s, 0) for s in VALID_STAGES}


@router.get("/pipeline/reminders", response_model=PipelineRemindersResponse)
async def pipeline_reminders(db: JobDatabase = Depends(get_db)):
    stale = await db.get_stale_applications(days=7)
    return PipelineRemindersResponse(
        reminders=[PipelineApplication(**a) for a in stale]
    )


@router.post("/pipeline/{job_id}", response_model=PipelineApplication)
async def create_application(job_id: int, db: JobDatabase = Depends(get_db)):
    row = await db.get_job_by_id(job_id)
    if not row:
        raise HTTPException(status_code=404, detail="Job not found")
    app_row = await db.create_application(job_id)
    return PipelineApplication(**app_row)


@router.post("/pipeline/{job_id}/advance", response_model=PipelineApplication)
async def advance_application(
    job_id: int,
    body: PipelineAdvanceRequest,
    db: JobDatabase = Depends(get_db),
):
    if body.stage not in VALID_STAGES:
        raise HTTPException(status_code=400, detail=f"Invalid stage. Must be one of: {VALID_STAGES}")
    app_row = await db.advance_application(job_id, body.stage)
    if not app_row:
        raise HTTPException(status_code=404, detail="Application not found")
    return PipelineApplication(**app_row)
```

- [ ] **Step 2: Run all API tests**

```bash
python -m pytest tests/test_api.py -v
```

- [ ] **Step 3: Commit**

```bash
git add src/api/routes/pipeline.py tests/test_api.py
git commit -m "feat(api): implement application pipeline endpoints"
```

---

## Task 10: Integration Test and CLI Entry Point

**Files:**
- Modify: `tests/test_api.py`
- Modify: `src/cli.py` (add `api` command)

- [ ] **Step 1: Add full integration test**

Add to `tests/test_api.py`:

```python
@pytest.mark.asyncio
async def test_full_workflow():
    """Integration test: health → status → jobs → action → pipeline."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Health
        resp = await client.get("/api/health")
        assert resp.status_code == 200

        # Status
        resp = await client.get("/api/status")
        assert resp.status_code == 200
        assert resp.json()["sources_total"] == 48

        # Jobs (empty DB)
        resp = await client.get("/api/jobs")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

        # Sources
        resp = await client.get("/api/sources")
        assert resp.status_code == 200
        assert len(resp.json()["sources"]) == 48

        # Action counts (empty)
        resp = await client.get("/api/actions/counts")
        assert resp.status_code == 200

        # Pipeline counts (empty)
        resp = await client.get("/api/pipeline/counts")
        assert resp.status_code == 200

        # Profile (should 404)
        resp = await client.get("/api/profile")
        assert resp.status_code == 404
```

- [ ] **Step 2: Add CLI command to start API server**

Add to `src/cli.py`:

```python
@cli.command()
@click.option("--port", default=8000, help="Port to run the API server on")
@click.option("--host", default="127.0.0.1", help="Host to bind to")
def api(port: int, host: str):
    """Start the FastAPI backend server."""
    import uvicorn
    click.echo(f"Starting Job360 API on {host}:{port}")
    uvicorn.run("src.api.main:app", host=host, port=port, reload=True)
```

- [ ] **Step 3: Run full test suite**

```bash
python -m pytest tests/test_api.py -v
python -m pytest tests/ --ignore=tests/test_main.py --ignore=tests/test_sources.py -v --timeout=60
```

- [ ] **Step 4: Manual smoke test**

```bash
python -m src.cli api
# In another terminal:
curl http://localhost:8000/api/health
curl http://localhost:8000/api/status
curl http://localhost:8000/api/sources
curl http://localhost:8000/api/jobs
```

- [ ] **Step 5: Final commit**

```bash
git add src/api/ src/cli.py src/storage/database.py tests/test_api.py requirements.txt
git commit -m "feat(api): complete FastAPI backend with 21 endpoints for Next.js frontend"
```

---

## Task 11: Update Documentation

**Files:**
- Modify: `CLAUDE.md`

- [ ] **Step 1: Add API section to CLAUDE.md**

Add to the Commands section:
```markdown
# API server
python -m src.cli api                              # Start FastAPI on localhost:8000
python -m src.cli api --port 3001 --host 0.0.0.0   # Custom host/port
```

Update folder structure to include `src/api/`.
Update test count.

- [ ] **Step 2: Commit**

```bash
git add CLAUDE.md
git commit -m "docs: add FastAPI API documentation to CLAUDE.md"
```
