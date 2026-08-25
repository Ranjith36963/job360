# /debug — Unified Debugging
<!-- doc: LIVING -->

**Methodology: CRIIVP** — Capture → Reproduce → Isolate → Implement → Verify → Prevent

One command to investigate scoring, logs, notifications, and deduplication with structured root-cause analysis.

## Usage

```
/debug <target> [args...]
```

## Targets

| Target | Args | What It Does |
|--------|------|-------------|
| `score` | `<title> <company>` | Trace scoring breakdown for a job |
| `logs` | `[source] [severity]` | Analyze log files for error patterns |
| `notify` | `<channel>` | Send test notification to verify configuration |
| `dedup` | `<title> <company>` | Trace deduplication for a job |

## Instructions

### Target: `score`

**Capture** the full component-by-component scoring breakdown:

```bash
python -c "
from src.services.profile.storage import load_profile
from src.services.profile.keyword_generator import generate_search_config
from src.services.skill_matcher import JobScorer

import sys
user_id = sys.argv[1] if len(sys.argv) > 1 else ''
if not user_id:
    print('ERROR: pass a user_id — load_profile(user_id) is per-user since multi-tenant.')
    exit(1)
profile = load_profile(user_id)
if not profile:
    print(f'ERROR: no profile for {user_id}. Run setup-profile first.')
    exit(1)

config = generate_search_config(profile)
scorer = JobScorer(config)
title = '<TITLE_ARG>'
company = '<COMPANY_ARG>'
print(f'Scoring: \"{title}\" @ {company}')
print(f'Config: {len(config.job_titles)} titles, {len(config.primary_skills)}P/{len(config.secondary_skills)}S/{len(config.tertiary_skills)}T skills')
print()

# Component breakdown table
title_lower = title.lower()
print('COMPONENT BREAKDOWN')
print('=' * 50)

# Title component (0-40)
title_matches = [jt for jt in config.job_titles if jt.lower() in title_lower]
title_score = 40 if any(jt.lower() == title_lower for jt in config.job_titles) else (20 if title_matches else 0)
print(f'  Title (0-40):    {title_score}')
for jt in title_matches:
    print(f'    matched: \"{jt}\"')
if not title_matches:
    print(f'    no match in {len(config.job_titles)} titles')

# Negative check
neg_matches = [neg for neg in config.negative_title_keywords if neg.lower() in title_lower]
neg_penalty = -30 if neg_matches else 0
if neg_matches:
    print(f'  Negative:       {neg_penalty}')
    for neg in neg_matches:
        print(f'    matched: \"{neg}\"')

# Skills component (0-40) — cannot trace without description
print(f'  Skills (0-40):   [needs job description to trace]')

# Location component (0-10) — cannot trace without location
print(f'  Location (0-10): [needs job location to trace]')

# Recency component (0-10) — cannot trace without date
print(f'  Recency (0-10):  [needs job date to trace]')

print()
estimated = title_score + neg_penalty
print(f'Estimated from title alone: {estimated}/100')
if estimated < 30:
    print(f'  Below MIN_MATCH_SCORE=30 — WHY: title did not match any of {len(config.job_titles)} configured titles')
print()
print('For full scoring, use the dashboard or run the pipeline.')
"
```

Replace `<TITLE_ARG>` and `<COMPANY_ARG>` with the user's arguments. If the score is surprising, **explain WHY** based on the component breakdown.

### Target: `logs`

**Capture** and **categorize** log errors by type, not just source:

```bash
python -c "
from pathlib import Path
import re
from collections import Counter

log_dir = Path('data/logs')
if not log_dir.exists():
    print('No logs directory found.')
    exit(0)

log_files = list(log_dir.glob('*.log'))
if not log_files:
    print('No log files found.')
    exit(0)

# Error categories
CATEGORIES = {
    'Network': ['ConnectionError', 'TimeoutError', 'ClientError', 'aiohttp'],
    'Auth': ['401', '403', 'Unauthorized', 'Forbidden', 'API key'],
    'Parse': ['JSONDecodeError', 'KeyError', 'IndexError', 'TypeError', 'ValueError'],
    'Rate limit': ['429', 'rate limit', 'Too Many Requests', 'throttl'],
}

for lf in log_files:
    text = lf.read_text(errors='ignore')
    lines = text.strip().split('\n')
    errors = [l for l in lines if '[ERROR]' in l or '[WARNING]' in l]
    print(f'{lf.name}: {len(lines)} lines, {len(errors)} errors/warnings')

    if errors:
        # Categorize errors
        categorized = Counter()
        uncategorized = []
        for e in errors:
            matched = False
            for cat, patterns in CATEGORIES.items():
                if any(p.lower() in e.lower() for p in patterns):
                    categorized[cat] += 1
                    matched = True
                    break
            if not matched:
                uncategorized.append(e)
                categorized['Other'] += 1

        print('  By category:')
        for cat, cnt in categorized.most_common():
            print(f'    {cat}: {cnt}')

        # Top 3 with root cause + fix + prevention
        print()
        print('  Top 3 errors (root cause + fix + prevention):')
        for i, e in enumerate(errors[-3:], 1):
            print(f'    {i}. {e[:150]}')
            for cat, patterns in CATEGORIES.items():
                if any(p.lower() in e.lower() for p in patterns):
                    if cat == 'Network':
                        print(f'       Root cause: Network/connection failure')
                        print(f'       Fix: Check internet, verify URL, add retry logic')
                        print(f'       Prevention: Use session retry adapter')
                    elif cat == 'Auth':
                        print(f'       Root cause: Authentication/authorization failure')
                        print(f'       Fix: Check API key in .env, verify key is valid')
                        print(f'       Prevention: Validate API key at source init')
                    elif cat == 'Parse':
                        print(f'       Root cause: Unexpected response format')
                        print(f'       Fix: Check API response, add defensive parsing')
                        print(f'       Prevention: Use .get() with defaults, add try/except')
                    elif cat == 'Rate limit':
                        print(f'       Root cause: Rate limit exceeded')
                        print(f'       Fix: Increase delay in RATE_LIMITS, reduce concurrency')
                        print(f'       Prevention: Respect Retry-After headers')
                    break
"
```

If the user provides `[source]` or `[severity]`, filter the output accordingly.

### Target: `notify`

> **Architecture note (2026):** notifications are no longer global per-channel classes
> (the old `SlackChannel`/`DiscordChannel`/`EmailChannel` are gone). They are now
> **per-user**: `src/services/channels/dispatcher.py::dispatch()` reads the user's single
> `notification_rules` row + their Fernet-encrypted `user_channels` and sends via Apprise.
> There is no standalone "send to slack" — and since 2026-08-24 there is no Slack
> channel at all. Delivery is email + webhook only; a test-send needs a user context.

Verify the notification path is importable and inspect a user's channel config:

```bash
python -c "
import asyncio
from src.services.channels.dispatcher import dispatch, ChannelSendResult  # import must succeed
from src.core.settings import DB_PATH
from src.repositories import pg as aiosqlite

user_email = '<CHANNEL_ARG>'  # pass the USER'S EMAIL (notifications are per-user now)

async def main():
    async with aiosqlite.connect(str(DB_PATH)) as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute('SELECT id FROM users WHERE email = ?', (user_email,))
        row = await cur.fetchone()
        if not row:
            print(f'No user with email {user_email!r}')
            return
        uid = row['id']
        chans = await (await db.execute(
            'SELECT channel_type, enabled FROM user_channels WHERE user_id = ?', (uid,)
        )).fetchall()
        rule = await (await db.execute(
            'SELECT notify_mode, min_score FROM notification_rules WHERE user_id = ?', (uid,)
        )).fetchone()
        print(f'user {uid}: {len(chans)} channel(s)')
        for c in chans:
            print(f'  {c[\"channel_type\"]}: enabled={bool(c[\"enabled\"])}')
        print(f'rule: {dict(rule) if rule else \"(none — no notifications will send)\"}')
        print('dispatcher import OK — to actually send, use the /verify-job360 skill or the'
              ' channels settings page test-send (both drive the real per-user path).')

asyncio.run(main())
"
```

Replace `<CHANNEL_ARG>` with the **user's email**. For an actual end-to-end test-send,
use the `/verify-job360` skill or the channels settings UI — both exercise the real
per-user dispatcher rather than a fabricated global send.

### Target: `dedup`

> **Note:** the `normalized_key()` demo below always works. The DB match query uses
> stdlib `sqlite3` against a local `data/jobs.db` — that only exists on a SQLite dev
> box. **Prod is Postgres**, so there the query prints "Database not found"; check the
> `jobs` table in Postgres instead. The normalized-key logic it demonstrates is identical.

**Capture** the dedup key and **query the database** for existing matches:

```bash
python -c "
import sqlite3
from pathlib import Path
from src.models import Job

title = '<TITLE_ARG>'
company = '<COMPANY_ARG>'

job = Job(
    title=title,
    company=company,
    location='',
    apply_url='https://example.com',
    source='debug',
    date_found='2024-01-01',
)

norm_company, norm_title = job.normalized_key()
print(f'Input:  title=\"{title}\", company=\"{company}\"')
print(f'Normalized key: (\"{norm_company}\", \"{norm_title}\")')
print()

# Query actual DB for matches
db = Path('data/jobs.db')
if db.exists():
    conn = sqlite3.connect(str(db))
    rows = conn.execute(
        'SELECT title, company, source, date_found FROM jobs WHERE company LIKE ? AND title LIKE ?',
        (f'%{norm_company}%', f'%{norm_title}%')
    ).fetchall()
    if rows:
        print(f'EXISTING MATCHES ({len(rows)} found):')
        for r in rows[:10]:
            print(f'  \"{r[0]}\" @ {r[1]} (source: {r[2]}, date: {r[3]})')
        if len(rows) > 10:
            print(f'  ... and {len(rows) - 10} more')
        print()
        print('These jobs share the same normalized key and would be deduplicated.')
        print('The EARLIEST record is kept; later duplicates are discarded.')
    else:
        print('No existing matches in database — this would be a new job.')
    conn.close()
else:
    print('Database not found — no existing data to check against.')
    print('Jobs with the same normalized key would be deduplicated.')
"
```

Replace `<TITLE_ARG>` and `<COMPANY_ARG>` with the user's arguments.

## Tools Used
Bash, Read, Grep
