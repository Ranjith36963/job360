# Job360
<!-- doc: LIVING | last-verified: 2026-10-05 by README rewrite (current product state) -->

**The job tracker your AI assistant fills in for you — every CV version, every reply, every receipt.**

Your AI assistant (Claude, ChatGPT, Claude Code, any MCP client) does the work of a job hunt: it finds the job, judges the fit, writes the CV and cover letter, reads your Gmail, fills the form. **Job360 is where all of that is recorded** — the profile it works from, every document version it wrote, every event with who wrote it and when, and a receipt you can trust when you apply.

Job360 has no AI of its own. It never searches for, ranks or recommends jobs (product rule 4), and it never judges you — it stores what your assistant decided and shows it back to you. Read [`docs/product/VISION.md`](./docs/product/VISION.md) first.

Live at **https://job360.uk** (Railway, auto-deployed from `main`).

---

## How it works

```
 You ──► your AI assistant ──(MCP)──► Job360 ──► the record you see on job360.uk
         (thinks + acts)               (stores)
```

1. **Connect your assistant** once — Settings → Connect gives the address `https://job360.uk/api/mcp`. Connector apps (Claude.ai is proven end to end; ChatGPT-style connectors use the same flow) sign in with OAuth 2.1; CLI clients like Claude Code use a personal token (`j360_…`, shown once, stored hashed).
2. **Type `run 360`.** The assistant fetches the setup recipe from Job360, reads your CV, fills your profile, asks to connect Gmail, and offers the inbox check.
3. **Hunt and apply.** The assistant searches with its own tools, brings each job it judges a fit, writes a tailored CV + cover letter, and stops before Submit. You say yes; it records the application and Job360 freezes a receipt.
4. **Follow up.** On its schedule the assistant reads Gmail, records replies, interviews and rejections against the right application, sends the outreach emails it wrote (Auto mode only), and tells you what is due and what has gone quiet.
5. **Look back any time.** Ask your assistant "how is my hunt going?" or "tell me everything about the Mistral application" — it reads it all from Job360. Or open the web app.

## What is live today

### For your assistant (MCP server at `/api/mcp`)
- **Recipes — `run 360` and friends.** Step-by-step playbooks the assistant fetches with `get_recipe`; which ones exist, and the order a new user runs them in, is `api/routes/recipes.RECIPE_NAMES` (text in `backend/src/recipes/`).
- **Profile** — `get_profile` returns the raw CV / LinkedIn / GitHub text plus the structured fields; the assistant writes skills, dated work history, projects, targets and preferences back with `update_profile`. Every agent edit is kept with its author and can be taken back.
- **Bring a job** — `bring_job` stores the ad (link or pasted text) and births one **Application**, with the job's country, remote flag and where it was found.
- **Fit and visa** — the assistant's own verdict, stored with `save_fit` (Job360 never computes a score).
- **Documents** — `save_artifact` keeps every CV, cover-letter, answer and outreach version, with the assistant's **ATS score and notes** on each CV.
- **Events** — `record_event` appends a typed history: replied, interview requested / scheduled / done, offer, rejected, follow-ups with dates, notes — each with its source (e.g. the Gmail message id) so a re-read never double-counts.
- **Receipts** — `record_application` freezes exactly what was sent (CV version, cover letter, answers, channel, confirmation) the moment you say you applied. Never edited afterwards.
- **People** — recruiters and hiring managers with **where they were found**, every outreach message version, sent and replied marks.
- **Needs you** — when the assistant would have to guess, it calls `ask_user`; you answer once, on the web or in chat, and every assistant reads the answer.
- **Inbox check** — your choice, stored in Job360 so every assistant obeys it:
  **Auto** (reads Gmail for your open applications, records what happened, sends the outreach emails it wrote) · **Ask me first** (asks before each check, only drafts) · **Off**.
  How often: every 3, 6 or 12 hours, or once a day.
- **Read-back** — `get_application` (one job, everything), `export_history` (the whole hunt), `stats`, `list_people`, `list_receipts`, `whats_new`.

### For you (web app at job360.uk)
- **Home** — one line on what your assistant did since you were last here, what needs you, your applications ledger, and what is due.
- **Application page** — the job, fit and visa, every document version with **Copy / Word / PDF**, the ATS opinion, the timeline with who wrote each line, people and messages, receipts, editable job details.
- **Needs you** — open questions from your assistant, answered in place.
- **Receipts** — each one a white sheet you can save as a PDF.
- **Stats** — counts plus reply and interview rates, split by country, where you found the job, how you applied, CV version, role, and where you found the person. Numbers only — your assistant does the judging.
- **Profile** — what Job360 extracted and what your assistant edited, with history and "take back".
- **Connect** — the address, per-assistant steps, the inbox mode and frequency, connected apps and personal tokens.
- Light and dark mode; magic-link or password login.

### What Job360 deliberately does not do
- Search, rank, score or recommend jobs.
- Run its own LLM, or read your email itself — your assistant does both with its own connectors.
- Submit an application without your yes for that one application, or follow instructions written inside an email.
- Push notifications or run background jobs — it is pull-based; your assistant's own scheduled task does the checking.

## Architecture

Two deployables on one Postgres database:

- **`backend/`** — FastAPI (Python 3.12 in prod). Product path: `POST /api/jobs/bring` (`api/routes/bring.py`) → the application spine (`services/applications/spine.py`: one Application, append-only events, versioned artifacts, frozen receipts) → the MCP server (`api/mcp_server.py`), with `services/profile/` feeding every step. `src/repositories/pg.py` is the single database door. Migrations apply automatically on boot.
- **`frontend/`** — Next.js 16 + React 19 + Tailwind 4, a thin screen over the same routes.

The deep reference (directory tree, schema, dependencies) is [`ARCHITECTURE.md`](./ARCHITECTURE.md). Code-verified counts (routes, migrations, workflows) live in [`docs/GENERATED.md`](./docs/GENERATED.md) — never copy a count from prose. Interactive API docs: `http://localhost:8000/docs` when the backend runs.

## Quick start (local)

```bash
git clone https://github.com/Ranjith36963/job360.git
cd job360

# Postgres (host port 5433)
docker compose -f docker-compose.dev.yml up -d postgres

# Config
cp .env.example .env          # DATABASE_URL, FRONTEND_ORIGIN, SITE_BASE_URL, RESEND_API_KEY

# Backend — FastAPI on :8000, MCP at /api/mcp
cd backend
pip install -e ".[dev]"
python main.py

# Frontend — Next.js on :3000
cd ../frontend
npm install
npm run dev
```

Optional: bootstrap a single dev profile from the command line —
`python -m src.cli setup-profile --cv cv.pdf --linkedin linkedin.pdf --github yourname` (all flags optional). Signed-in users build their profile on the web or through their assistant instead.

## Testing

```bash
cd backend && python -m pytest -q -p no:randomly      # needs the dev Postgres; runs offline
cd frontend && npm run test:unit && npm run test:e2e
```

The backend suite runs on a real Postgres, schema per test, with HTTP mocked by `aioresponses`. Measure counts, never quote them: `python -m pytest --collect-only -q -p no:randomly | tail -1`. Before a commit, `bash scripts/agent-gate.sh` runs the targeted tests, lint and type checks for what you changed.

## Deployment

`main` is production: every merge auto-deploys the `backend`, `frontend` and `Postgres` services on Railway. CI on every pull request runs the full backend and frontend suites, security scans, and two AI reviewers (bugs and security); a pull request merges only when all of them are green. System email (magic links, password reset) goes through Resend on `job360.uk`.

## Contributing

Branch, commit and PR conventions: [`CONTRIBUTING.md`](./CONTRIBUTING.md). Product rules: [`docs/product/product_design_rules.md`](./docs/product/product_design_rules.md). Current phase: [`STATUS.md`](./STATUS.md).
