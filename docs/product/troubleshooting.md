<!-- doc: LIVING | last-verified: 2026-09-11 by /sync -->
# Job360 Troubleshooting
<!-- doc: LIVING -->

Common **developer-environment** issues and fixes (ports, locks, env-var gotchas, install hiccups). Each entry: **Symptom → Cause → Fix**.

> **For production issues** read prod directly — Sentry, `railway logs`, the Postgres service — as root `CLAUDE.md` describes.

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
# From the repo root. `--wait` is load-bearing: plain `up -d` returns as soon as
# the container is RUNNING, so pytest can start before postgres accepts
# connections and you get this exact symptom back. `--wait` blocks on the
# postgres service's own pg_isready healthcheck in docker-compose.dev.yml.
docker compose -f docker-compose.dev.yml up -d --wait
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

## 3. "My CV uploaded but the profile is nearly empty"

**Symptom:** `setup-profile --cv ...` (or the web upload) succeeds, and the
profile shows the raw CV text, a handful of skills and a summary — and nothing
else. No roles, no dates, no companies, no certifications.

**Cause: that is the product working correctly (decision 28, 2026-09-21).**
Job360 has no model of its own. It extracts the TEXT and reads only the
structure it can PROVE — the delimited Skills section and the Summary. There is
no LLM key to set, because there is no provider: `services/profile/llm_provider.py`
and the four SDKs (`openai`, `google-generativeai`, `groq`, `cerebras-cloud-sdk`)
were deleted, along with `OPENAI_API_KEY`, `OPENAI_MODEL`, `GEMINI_API_KEY`,
`GEMINI_MODEL`, `GROQ_API_KEY` and `CEREBRAS_API_KEY`.

**Fix: connect an agent and let it fill the profile.** Point Claude / ChatGPT /
any MCP client at `/api/mcp` (see `docs/product/VISION.md`), then ask it to:

1. call `get_profile` — the `raw` key carries the CV text, the LinkedIn export
   text, the GitHub bio, the profile README and the repo briefs. The
   `editable_paths` key lists the exact dotted paths `update_profile` accepts
   right now — read it, don't assume a shape;
2. read the raw text, and call `update_profile` with the certifications,
   education, the skills that are only stated in prose, the dated work
   history (`cv_data.cv_positions`) and the projects (`cv_data.cv_projects`).
   The two lists take records with a closed key set — the `update_profile`
   tool description gives the shape — and a write replaces the whole list.
   A record with an unknown key, a wrong type, an over-long field or a date
   that is not a range like `Jan 2020 – Present` is refused with a 422 that
   names the problem.

What the agent writes survives every later re-upload — nothing in the extractor
clears a field the agent set.

Debug the TEXT extraction (the only half Job360 still owns) with:

```bash
cd backend
LOG_LEVEL=DEBUG python -m src.cli setup-profile --cv path/to/cv.pdf
```

(`setup_profile` in `src/cli.py` takes only `--cv` / `--linkedin` / `--github`;
verbosity is the `LOG_LEVEL` env var read by `core.settings`.)

If `raw_text` itself comes back empty, the PDF has no text layer — it is a scan
or a screenshot. Re-export it. `cv_parser.text_is_missing_spaces` also warns
when a PDF's text layer carries no space glyphs, which mangles every skill.

---

## 4. `core.hooksPath` blocks pre-commit install

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

## 5. Unicode CV text crashes fpdf2

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

---

## 6. Pytest suite stalls / zero output on Windows

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

## 7. Migrations runner "already applied" confusion

```bash
cd backend
python -m migrations.runner status        # lists applied + pending — read this FIRST
```

`down` pops exactly one migration off the HEAD, so what to do next depends on where
the stem you want sits:

```bash
# CASE A — the stem you want re-run IS the head (status lists it last).
# `down` reverts it, `up` re-applies it. This pair only does what you want here.
python -m migrations.runner down
python -m migrations.runner up

# CASE B — the stem is BURIED (e.g. you want 0010 and head is 0030).
# There is no way to reach it without reverting 0030…0011 first, one `down` at a
# time, re-reading `status` between each. That is ~20 destructive down-migrations
# against real data. In dev, rebuilding the database is almost always the right
# call instead; in prod, do neither without a backup (docs/product/RUNBOOK-backups.md).
```

> ⚠️ **`down` takes NO migration stem.** `migrations.runner.down` reverts the *last
> applied* migration and nothing else; the second positional argument of
> `migrations.runner._cli` is the **db_path**, not a selector. So
> `python -m migrations.runner down 0010` does **not** target migration 0010 — it
> swallows `0010` as a connection path and still reverts whatever is at the head.
> Against a head of `0030` that reverts `0030`, and following it with `up` re-applies
> `0030`: it looks like it worked and changes nothing about 0010.
> Pinned by `backend/tests/test_migrations_runner_cli_contract.py`.

Migrations are forward-only by default, and `down` is one step at a time — there is
no `down <stem>` and no `down --all`.

---

## 8. `pip install -e .` fails on Windows with long-path errors

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

## 9. Frontend: "Failed to fetch" from `/api/...` calls

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
