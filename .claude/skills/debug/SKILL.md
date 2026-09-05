# /debug — Unified Debugging
<!-- doc: LIVING -->

**Methodology: CRIIVP** — Capture → Reproduce → Isolate → Implement → Verify → Prevent

One command to investigate errors and the bring-a-job dedup key with structured
root-cause analysis. The `score` and `notify` targets went with the sourcing era
(slice 5, #483): there is no scorer to trace and no notification dispatcher to
test-fire any more.

## Usage

```
/debug <target> [args...]
```

## Targets

| Target | Args | What It Does |
|--------|------|-------------|
| `logs` | `[severity]` | Read production errors (Sentry + Railway) or the local log files |
| `dedup` | `<title> <company>` | Trace the dedup key `POST /jobs/bring` uses for a job |

## Instructions

### Target: `logs`

**An owner-reported problem is LIVE PRODUCTION** (root `CLAUDE.md`). Read prod first:

1. Sentry MCP — `organizationSlug: "job360"`, `regionUrl: "https://de.sentry.io"`;
   search issues for the last 24h, newest first.
2. `timeout 30 railway logs --service backend | head -200` — it *streams*, so
   `tail` returns nothing; always `head` under a `timeout`.

Only on a local dev box do the files exist. **Capture** and **categorize**:

```bash
python -c "
from pathlib import Path
from collections import Counter

log_dir = Path('data/logs')
files = list(log_dir.glob('*.log')) + list(log_dir.glob('*.jsonl')) if log_dir.exists() else []
if not files:
    print('No local log files — read prod (Sentry / railway logs) instead.')
    exit(0)

CATEGORIES = {
    'Network': ['ConnectionError', 'TimeoutError', 'ClientError', 'aiohttp', 'httpx'],
    'Auth': ['401', '403', 'Unauthorized', 'Forbidden', 'session_required', 'email_not_verified'],
    'Parse': ['JSONDecodeError', 'KeyError', 'IndexError', 'TypeError', 'ValueError', 'ValidationError'],
    'Database': ['psycopg', 'OperationalError', 'IntegrityError', 'UndefinedTable', 'UndefinedColumn'],
    'Rate limit': ['429', 'rate limit', 'Too Many Requests', 'throttl'],
}
for lf in files:
    lines = lf.read_text(errors='ignore').strip().split('\n')
    errors = [l for l in lines if '[ERROR]' in l or '[WARNING]' in l or '\"level\": \"error\"' in l]
    print(f'{lf.name}: {len(lines)} lines, {len(errors)} errors/warnings')
    counts = Counter()
    for e in errors:
        cat = next((c for c, pats in CATEGORIES.items() if any(p.lower() in e.lower() for p in pats)), 'Other')
        counts[cat] += 1
    for cat, n in counts.most_common():
        print(f'  {cat}: {n}')
    for e in errors[-3:]:
        print('  last:', e[:160])
"
```

Then, per category — **Isolate** the first failing call, **Implement** the fix
with a reproducer test, **Verify** against the same instrument you captured
from, **Prevent** with a guard (a test, a drill, or a Sentry alert).

If the user provides `[severity]`, filter to that level.

### Target: `dedup`

`POST /api/jobs/bring` (`backend/src/api/routes/bring.py`) stores a `jobs` row
once per `normalized_key()` (`backend/src/models.py`) — the same ad brought twice
answers `existing: true` with the SAME `job_id`. **Capture** the key:

```bash
python -c "
from src.models import Job

title = '<TITLE_ARG>'
company = '<COMPANY_ARG>'
job = Job(title=title, company=company, location='', apply_url='https://example.com',
          source='brought', date_found='2026-01-01')
norm_company, norm_title = job.normalized_key()
print(f'Input:  title=\"{title}\", company=\"{company}\"')
print(f'Normalized key: (\"{norm_company}\", \"{norm_title}\")')
"
```

Then **query the database** for rows that share it. Prod is Postgres — run through
Railway with `DATABASE_PUBLIC_URL` (never print the value), or against the dev
container on `localhost:5433`:

```sql
SELECT id, title, company, source, date_found
FROM jobs
WHERE lower(company) LIKE '%<norm_company>%' AND lower(title) LIKE '%<norm_title>%'
ORDER BY id;
```

The EARLIEST row is the one `bring` returns; if two rows exist for one key, the
bug is in `normalized_key()` or in the insert path — read both before touching
the data.

Replace `<TITLE_ARG>` and `<COMPANY_ARG>` with the user's arguments.

## Tools Used
Bash, Read, Grep
