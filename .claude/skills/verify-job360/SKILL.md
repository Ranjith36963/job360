---
name: verify-job360
description: >-
  Verify Job360 changes by actually running the app and watching the behavior — not by
  assuming tests or a clean compile prove it works. Use this AGGRESSIVELY: any time you
  touch backend (FastAPI, scoring, sources, DB, scheduler) or frontend (Next.js pages,
  API calls, auth) code, before saying something is "done" or "fixed", before opening a
  PR, and whenever the user asks to verify / test / confirm / "does it actually work" /
  "prove it". Drives a real browser with Playwright for UX, hits routes with curl and
  queries the SQLite DB for backend, and walks the full register→CV→search→jobs journey
  for end-to-end. If you changed Job360 code and haven't run it, this skill applies.
---

# Verify Job360

## The core idea

Code compiling, or unit tests passing, does **not** mean the feature works. Things break
at the seams the tests mock away — a route the frontend calls with the wrong shape, a
profile saved in the DB but read from a file, a job that scores 10 and silently gets
filtered out. The only way to know is to **run the real app and watch the behavior**.

So verification is a loop, not a one-shot (see `references/methodology.md` + the slide PNGs):

```
Run it → Drive it → See what happens → Read the logs → Fix → loop → Prove it with evidence
```

You are done only when you have **evidence** — a screenshot of the working UI, a DB row
that landed, a log line that proves the code path ran. "It should work now" is not evidence.

## Pick the flavor (or do all three)

Choose based on what you touched. When unsure, do the broader one.

- **Frontend / UX** — you changed a page, component, API call, or auth flow → drive a real browser, screenshot.
- **Backend** — you changed a route, scorer, source, DB, scheduler, worker → run the service, hit the route, query the DB, read the logs.
- **End-to-end** — you changed something that spans both, or the user wants the whole journey proven → walk register → CV → search → jobs.

`$ARGUMENTS` may name a flavor (`backend`, `frontend`, `e2e`) or a specific feature to focus on. If given, scope to that.

---

## Frontend / UX verification

**Run it.** Start the dev server in the background:
```
cd frontend && npm run dev          # → http://localhost:3000 (Next.js 16 + Turbopack)
```
Wait for `Ready in …`. If `:3000` is busy, a server is already up — reuse it.

**Drive it.** Use the **Playwright MCP** tools (already connected this session):
- `browser_navigate` to the page you changed.
- `browser_snapshot` to find element refs (the snapshot lists `[ref=eNN]` you click/fill by).
- `browser_fill_form` / `browser_click` / `browser_file_upload` to act like a user.
- `browser_take_screenshot` (use `fullPage: true` for long pages) — then **Read the PNG** and actually look at it. A blank/error frame is a failed launch, not a pass.
- `browser_console_messages` with `level: "error"` to catch runtime errors.

**Prove it.** Screenshot the relevant state before and after your change. For a flow
(e.g. login), screenshot each step. Looking at the image is the proof.

**Unblock it.**
- **Auth:** logged-out users are redirected to `/login?next=…`. To test a gated page, register/log in first through the UI (or reuse an existing test account — see "Known test state" below).
- **State:** a fresh account has no jobs, so empty states are expected. To see populated UI you need jobs in the DB (see Backend "seed" note) — don't mistake a correct empty state for a bug.

**Known benign console noise** (do NOT chase these as bugs):
- Dark-mode **hydration mismatch** on `<html className="dark">` — the Next.js dev "1 Issue" badge. Cosmetic.
- `GET /api/auth/me` → **401** when logged out — that's the auth check working.
- `GET /api/profile` → **404** for a brand-new user — means "no profile yet", expected.

---

## Backend verification

**Run it.** Start the API in the background:
```
cd backend && python main.py        # → http://127.0.0.1:8000  (/docs is the Swagger UI)
```
Wait for `Application startup complete`. Migrations auto-apply on boot via the `lifespan`
handler — a fresh DB gets the full schema. For pure logic changes, the test suite is also
"running it": `python -m pytest -q -p no:randomly --ignore=tests/test_main.py`.

**Drive it.** Hit the actual routes — don't just import a function:
```
curl -s http://127.0.0.1:8000/openapi.json | python -c "import sys,json;[print(m.upper(),p) for p in (d:=json.load(sys.stdin))['paths'] for m in d['paths'][p]]"
curl -s -w "\nHTTP %{http_code}\n" http://127.0.0.1:8000/api/<route>
```
For per-user routes you need a session cookie — register via `POST /api/auth/register`
(returns a `set-cookie: job360_session=…`) and pass it with `-b "job360_session=…"`.

**Prove it.** This is the part that catches the real bugs:
- **Did the row land?** Query the SQLite DB directly:
  ```
  cd backend && python -c "import sqlite3;c=sqlite3.connect('data/jobs.db',timeout=10);print(c.execute('SELECT COUNT(*) FROM jobs').fetchone()); [print(r) for r in c.execute('SELECT match_score,title FROM jobs ORDER BY match_score DESC LIMIT 5')]"
  ```
  Useful tables: `jobs` (shared catalog), `user_feed`, `user_profiles`, `users`, `sessions`, `applications`, `run_log`.
- **Did the path run?** Read the server's stdout/log. When you launched it as a background
  Bash task, its output goes to a task file — `tail` that file and grep for your route,
  for `ERROR`/`WARNING`, and for the run UUID. If a code path's execution is ambiguous,
  **add a structured log line** to prove it ran, then re-run and grep for it. Remove or
  keep the log as appropriate afterward.

**Unblock it.**
- Heavy jobs run async — the CLI pipeline (`python -m src.cli run --source <name> --no-email`)
  is the fastest way to exercise fetch→score→dedup→store for one source without the API.
- Add temporary structured logs Claude can grep to prove a branch executed.

---

## End-to-end verification (the full promise)

Run **both** servers, then walk the real journey with the browser and watch the DB/logs in parallel:

1. **Register** a fresh account (UI: `/register`, or `POST /api/auth/register`).
2. **Upload a CV** on `/profile` — use `test-artifacts/sample_cv.pdf` (a realistic ML-engineer CV).
   Confirm the profile populates (skills/titles chips, Skill Tiers) and the log shows
   `Profile saved for user …`.
3. **Trigger a search** ("New Search" on `/dashboard`, or `POST /api/search`).
4. **Verify jobs come through** — this is the whole point. Check `jobs` table count > 0 AND
   that the dashboard renders them with scores. If 0 jobs: read the log for *why*
   (no profile? all filtered by score? a source returned nothing?) — that diagnosis is the finding.
5. Spot-check downstream: job detail (`/jobs/[id]`), bookmark/apply → pipeline, notifications.

A good E2E run produces a short report: what works, what's broken (with the exact file:line
and the DB/log evidence), severity, and the fix. See `E2E_TEST_REPORT.md` at the repo root
for the format and the two real bugs this methodology already caught.

---

## Job360 gotchas (hard-won — don't relearn these)

These cost real time the first time. Reading them here saves the next run.

- **Stale on-disk DB crashes startup.** An old `data/jobs.db` from a previous schema makes
  boot fail (e.g. `no such column: staleness_state`) because `CREATE TABLE IF NOT EXISTS`
  is a no-op on the existing table and a later index/query hits the missing column. Fix for
  local dev: back up and reset — `mv data/jobs.db{,.bak}` (and the `-wal`/`-shm` siblings) —
  a fresh correct schema is created on next boot.
- **aiosqlite holds the DB lock after a crash.** A failed-startup `python main.py` often
  stays alive (a non-daemon aiosqlite worker thread blocks exit) and keeps `jobs.db` locked
  ("Device or resource busy"). Find it (`tasklist | grep python` / netstat on `:8000`) and
  `taskkill //PID <pid> //F` before moving the DB or restarting.
- **Auth needs secrets in the root `.env`.** Registration creates the user row, then fails
  to mint the session cookie if `SESSION_SECRET` is missing → "Failed to fetch" + a
  half-created account that then 409s "already registered". Both `SESSION_SECRET` and
  `CHANNEL_ENCRYPTION_KEY` (a Fernet key) must be set. Generate: `SESSION_SECRET` =
  `python -c "import secrets;print(secrets.token_urlsafe(64))"`; `CHANNEL_ENCRYPTION_KEY` =
  `python -c "from cryptography.fernet import Fernet;print(Fernet.generate_key().decode())"`.
- **Web profile vs pipeline profile mismatch (a real bug found).** The web app saves profiles
  per-user in the `user_profiles` DB table, but `run_search` (`src/main.py`) loads
  `load_profile(DEFAULT_TENANT_ID)`. So a logged-in user's "New Search" runs profile-less.
  When verifying search, check *which* profile the run actually loaded.
- **Editable install (`pip install -e`) may resolve `import src` to a git worktree** under
  `.claude/worktrees/…`, not the main checkout. If a standalone script imports the wrong
  copy, force it: `sys.path.insert(0, r'D:\dev\job360\backend')` and `os.chdir` to backend.
- **Playwright screenshots save to the repo root** by default. Read them from there, and
  tidy them into `test-artifacts/` afterward so they don't clutter the tree.
- **`test_main.py` hits live Indeed** via JobSpy — exclude it from fast test runs.

## Tools this skill uses

Playwright MCP (`browser_navigate`, `browser_snapshot`, `browser_fill_form`, `browser_click`,
`browser_file_upload`, `browser_take_screenshot`, `browser_console_messages`); Bash for
background servers, `curl`, and `python`-driven SQLite queries; Read for looking at screenshots.

## Keep this skill alive (self-improving)

This skill is only as good as the gotchas it remembers. **When you hit a blocker and solve
it during verification — a new env var, a seed step, a flaky source, a route shape — add it
to the "gotchas" section above.** The next run (yours or a teammate's) should never pay the
same debugging cost twice. Don't make it more prescriptive; make it more *knowing*.
