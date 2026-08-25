<!-- doc: LIVING -->
---
name: add-source
description: Job360 recipes for adding or removing a job source (the five load-bearing surfaces), and for adding a notification channel. Use when adding/removing/rewriting a source in backend/src/sources/, changing SOURCE_REGISTRY or RATE_LIMITS, or wiring a new notification/delivery channel.
---

# Adding a source or a channel (.claude/skills/add-source/SKILL.md)

These recipes were moved verbatim out of the root `CLAUDE.md` (2026-08-11) — the
root file is auto-loaded by every session and must stay pointers-only. Hard-rule
numbers below refer to the Hard Rules index in `CLAUDE.md`.

## The FIVE load-bearing surfaces (rules #8 + #13)

Adding or removing a source touches **five** files, not four. Miss one and the
test suite fails on a hardcoded count:

| # | Surface | Where |
|---|---------|-------|
| 1 | `SOURCE_REGISTRY` dict | `backend/src/main.py` |
| 2 | `_build_sources()` list | `backend/src/main.py` |
| 3 | `RATE_LIMITS` dict | `backend/src/core/settings.py` |
| 4 | `len(SOURCE_REGISTRY) == N` + expected-name set | `backend/tests/test_cli.py` |
| 5 | `sources_total == N` / `len(sources) == N` | `backend/tests/test_api.py` (inside `test_sources_returns_*`, `test_status_returns_counts`, `test_full_api_workflow`) |

**Measure the current count, never copy it from a doc:**

```bash
cd backend && python -c "from src.main import SOURCE_REGISTRY as R; print(len(R), len(set(R.values())))"
grep -rn "== 41\|sources_total\|len(SOURCE_REGISTRY)" tests/test_cli.py tests/test_api.py
```

At the time of writing that prints `41 40` — 41 registry keys but 40 unique
classes, because `indeed` and `glassdoor` both alias `JobSpySource`. Test
assertions treat the **registry key count** as authoritative.

## Adding a new job source

- Class extends `BaseJobSource` (`backend/src/sources/base.py`), implements
  `async fetch_jobs() -> list[Job]`.
- **MUST set `.category`** (rule #15) to one of `"ats"`, `"rss"`, `"keyed_api"`,
  `"free_json"`, `"scrapers"`, `"other"` — or add a `NAME_TIER[source.name]`
  override in `scheduler.py`. Untagged sources silently fall to the 60-min tier.
  Folder location ≠ scheduler tier: `teaching_vacancies` lives in `apis_free/`
  but declares `category="rss"`.
- Use the `self.relevance_keywords` / `self.job_titles` / `self.search_queries`
  properties — never import the keyword lists directly.
- If you write a custom `__init__`, accept `search_config=None` and pass it
  through: `super().__init__(session, search_config=search_config)`.
- Update all **five surfaces** above.
- Add tests with mocked HTTP via `aioresponses` (rule #4 — never live HTTP).
- Conditional fetch is **opt-in** (rule #14): only call
  `self._get_json_conditional(url)` when the upstream genuinely honours
  ETag/Last-Modified; everyone else stays on `self._get_json(url)`.
- **Never change `BaseJobSource`** itself (constructor, properties, retry,
  `_get_json`/`_post_json`/`_get_text`) without checking every source file that
  inherits from it (rule #2).

### Per-category patterns

- **Keyed source:** accept `api_key`; return `[]` early with an info-log when it
  is empty; pass `search_config` through.
- **ATS source:** accept a `companies` list and `search_config=None`; iterate
  slugs from `core/companies.py`.
- **RSS/XML source:** `_get_text()` → parse with **defusedxml**, not plain
  `ElementTree.fromstring` — these feeds are untrusted input and M18 closed the
  XXE hole. The in-repo pattern (see `backend/src/sources/feeds/nhs_jobs.py:2-8`) is
  `import xml.etree.ElementTree as ET` for the types plus
  `from defusedxml.ElementTree import fromstring as _safe_fromstring  # type: ignore[import-untyped]`
  for the actual parse. Consider `_get_json_conditional` if upstream honours
  ETag (rule #14).
- **HTML scraper:** `_get_text()` → regex parse. Fragile by nature — tag new
  scrapers in `STATUS.md`'s "fragile/risky" table.

## Adding a notification channel

Work in `backend/src/services/channels/dispatcher.py` (the Apprise dispatcher) —
that is the **only** delivery path.

⚠️ **Older docs told you to implement a `NotificationChannel` ABC and register it
in `get_all_channels()` in `src/services/notifications/base.py`. That module and
both symbols no longer exist** (verified 2026-08-03, re-verified 2026-08-24 —
`src/services/notifications/` now holds `__init__.py`, `report_generator.py` and
`defaults.py`, and no channel classes). An agent following the old instruction would be writing
against a deleted API.

Respect rules #23 and #24 while you are in there:

- **#23** — one `notification_rules` row per user (`UNIQUE(user_id)`), governing
  ALL their channels at once. Dispatch converts UTC `now` into `users.timezone`
  via stdlib `zoneinfo` (not `pytz`) before comparing quiet hours.
- **#24** — `notify_mode` is `instant` | `daily` | `every_n_hours`. New dispatch
  paths need tests covering all three modes **and** both quiet-hours states.

## Before you claim it works

```bash
cd backend && python -m pytest tests/test_cli.py tests/test_api.py -q -p no:randomly
cd backend && python -m pytest -q -p no:randomly   # canonical full run
```
