# Job360 Troubleshooting
<!-- doc: LIVING -->

Common **developer-environment** issues and fixes (ports, locks, env-var gotchas, install hiccups). Each entry: **Symptom → Cause → Fix**.

> **For operational / runtime issues** (digest queue stuck, channel decryption failures, scoring debug via 9-dim columns, breaker reset, source returning 0 jobs, profile force-rebuild), see [`docs/product/pillars/runbook.md`](./pillars/runbook.md) — 15-section operational reference with the SQL queries and CLI commands you actually need at 2am. For unfamiliar terminology, see [`docs/product/pillars/glossary.md`](./pillars/glossary.md).

---

## 1. Port already in use (8000 or 3000)

**Symptom:** `OSError: [Errno 48] Address already in use` (macOS/Linux) or `Only one usage of each socket address (protocol/network address/port) is normally permitted` (Windows) when starting FastAPI (8000) or Next.js (3000).

**Cause:** A previous dev process is still holding the port.

**Fix:**

```bash
# macOS / Linux
lsof -iTCP:8000 -sTCP:LISTEN
kill -9 <PID>

# Windows PowerShell
Get-NetTCPConnection -LocalPort 8000 | Select-Object OwningProcess
Stop-Process -Id <PID> -Force

# Or bind a different port
uvicorn main:app --port 8001
npm run dev -- -p 3001
```

---

## 2. Postgres connection / schema errors in tests

**Symptom:** `psycopg.OperationalError: connection refused`, or a test reading rows
it never wrote.

**Cause:** the suite runs against a REAL Postgres (docker-compose.dev.yml, port
5433) with a schema per test. Either the container is not up, or a fixture was
imported across modules — a double import breaks per-test schema isolation, so
one test writes to schema A while another reads schema B.

**Fix:**

```bash
make redis-up            # from the repo root; brings up the dev containers
cd backend && python -m pytest -q -p no:randomly
```

Never import a test fixture from another test module. Register fixtures in
`conftest.py` instead — cross-module fixture imports are what break schema
isolation, and the symptom looks like unrelated tests failing.

> **This section used to document SQLite "database is locked" and WAL mode.**
> The database has been Postgres via psycopg3 since 2026-07-02; `pg.py` is an
> aiosqlite-SHAPED shim, not SQLite. That failure mode cannot occur, so the old
> advice sent people to fix a lock that does not exist. Corrected 2026-08-24.

---

## 3. CV parse fails / LLM provider unreachable

**Symptom:** `setup-profile --cv ...` errors with `LLMProviderError` or hangs with no output.

**Cause:** No LLM API key set, or the first provider in the fallback chain is rate-limited.

**Fix:** At least ONE of these must be set in `.env`:

```
GEMINI_API_KEY=...
GROQ_API_KEY=...
CEREBRAS_API_KEY=...
```

Fallback chain (`src/services/profile/llm_provider.py:329-334`): **OpenAI (PRIMARY)** → Gemini → Groq → Cerebras. If every configured provider fails, `llm_extract` raises `LLMRateLimited` (retry later) or `LLMAllProvidersFailed`; with no key at all it raises `LLMKeyMissing`. Callers must NOT persist an empty result on `LLMRateLimited`.

Debug with:

```bash
cd backend
python -m src.cli setup-profile --cv path/to/cv.pdf --log-level DEBUG
```

Look for `[llm_provider]` lines — they log which provider was tried and why each failed.

---

## 4. Redis missing on Windows

**Symptom:** `ConnectionRefusedError: [WinError 10061]` or `check_worker.py` reports `tcp localhost:6379 unreachable`.

**Cause:** Redis has no native Windows build. The ARQ worker needs a Redis instance.

**Fix — pick one:**

- **A. WSL2 + Ubuntu** (recommended for dev)
  ```bash
  wsl --install -d Ubuntu
  # inside WSL:
  sudo apt-get install redis-server && sudo service redis-server start
  ```

- **B. Docker Desktop**
  ```bash
  docker run -d -p 6379:6379 --name redis redis:7-alpine
  ```

- **C. Memurai** (Redis-compatible native Windows fork) — https://www.memurai.com/

- **D. Skip the worker.** The CLI (`python -m src.cli run`), the read-only API, the frontend, and the full test suite all work without ARQ / Redis. Only the live notification dispatcher needs it.

---

## 5. `core.hooksPath` blocks pre-commit install

**Symptom:** `pre-commit install` refuses with:
```
Cowardly refusing to install hooks with `core.hooksPath` set.
hint: `git config --unset-all core.hooksPath`
```

**Cause:** Something (often a parent repo's shared hook dir) set `core.hooksPath` at the worktree level.

**Fix (exact command run on the generator worktree during Step 0):**

```bash
git config --local --unset core.hooksPath
pre-commit install
```

---

## 6. Unicode CV text crashes fpdf2

**Symptom:** `UnicodeEncodeError: 'latin-1' codec can't encode character` when the test suite builds a sample PDF with fpdf2.

**Cause:** fpdf2's core 14 fonts (Helvetica/Times/Courier) only support Latin-1. Unicode needs `add_font(..., uni=True)` with a TTF path.

**Fix — two options:**

```python
# Option A: use a Unicode TTF (heaviest, but handles any char)
from fpdf import FPDF
pdf = FPDF()
pdf.add_font("DejaVu", "", "/path/to/DejaVuSans.ttf", uni=True)
pdf.set_font("DejaVu", size=12)
pdf.cell(0, 10, "café naïve — résumé")

# Option B: strip to Latin-1, keep the core fonts (cheaper)
def safe_latin1(s: str) -> str:
    return s.encode("latin-1", errors="replace").decode("latin-1")

pdf = FPDF()
pdf.set_font("Helvetica", size=12)
pdf.cell(0, 10, safe_latin1("café naïve — résumé"))
```

Our `tests/conftest.py` helpers use Option B.

---

## 7. Pytest suite stalls / zero output on Windows

**Symptom:** `python -m pytest tests/` under git-bash / MSYS2 produces no output and never exits.

**Cause:** WinPTY / MSYS2 pipe buffering. Pytest's TTY detection sees a pipe, buffers output, and the pipe never flushes.

**Reproducer:** `python -m pytest tests/ 2>&1 | tee log.txt` inside Git Bash.

**Workaround:** Run the tests in a real Windows terminal, not git-bash.

```powershell
# PowerShell
cd backend; python -m pytest tests/ -v
```
```cmd
:: CMD
cd backend && python -m pytest tests\ -v
```

---

## 8. Migrations runner "already applied" confusion

**Symptom:** `python -m migrations.runner up` prints `no pending migrations` but you expected 0010 to run.

**Cause:** The `_schema_migrations` registry already has a row for that stem — likely from a prior partial run.

**Fix:** Inspect state first.

```bash
cd backend
python -m migrations.runner status        # lists applied + pending
# If you need to re-apply 0010:
python -m migrations.runner down 0010
python -m migrations.runner up
```

Migrations are forward-only by default. `down` is available per-stem and must be explicit — there is no `down --all`.

---

## 9. `pip install -e .` fails on Windows with long-path errors

**Symptom:** `OSError: [WinError 206] The filename or extension is too long` during `pip install -e backend/`.

**Cause:** Default Windows `MAX_PATH` is 260 chars. Nested `site-packages` under a deep worktree path blows past that (e.g. `.claude\worktrees\generator\backend\...\sentence_transformers\...`).

**Fix — either:**

- Enable long-path support (admin PowerShell):
  ```powershell
  reg add "HKLM\SYSTEM\CurrentControlSet\Control\FileSystem" /v LongPathsEnabled /t REG_DWORD /d 1 /f
  ```
  Log out / log in, or reboot.

- Move the repo to a short path:
  ```powershell
  git clone <repo> C:\j360
  cd C:\j360
  ```

---

## 10. Frontend: "Failed to fetch" from `/api/...` calls

**Symptom:** Network panel shows CORS error or `TypeError: Failed to fetch` when the dashboard calls the backend.

**Cause:** One of:
- FastAPI not running on the expected port.
- `NEXT_PUBLIC_API_URL` in `frontend/.env.local` doesn't match the URL the backend is actually listening on.
- `FRONTEND_ORIGIN` on the backend doesn't include the frontend's origin (CORS rejects the preflight).

**Fix:** Verify both halves match.

```bash
# Terminal 1 — backend
cd backend
FRONTEND_ORIGIN=http://localhost:3000 python main.py

# Terminal 2 — frontend (and check frontend/.env.local)
cd frontend
cat .env.local   # must contain NEXT_PUBLIC_API_URL=http://localhost:8000
npm run dev
```

If running on a non-default host (e.g. LAN IP for mobile testing), pass a comma-separated list:

```bash
FRONTEND_ORIGIN=http://localhost:3000,http://192.168.1.10:3000 python main.py
```

---
