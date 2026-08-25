# Pillar 3 — what each job source actually gives us
<!-- doc: LOG -->

> **DATED RECORD — true on the day it was written.** Numbers and statuses here are historical. Do not read as current state. <!-- banner: auto -->

**Audited 2026-08-08 against `main` @ `dc9e546`.** Two independent instruments:
1. **Code audit** — 5 parallel workers read every `fetch_jobs()` in all 46 source files.
2. **Real data** — 6,046 jobs, 28 sources, from the dev Postgres catalog (run 2026-08-05).

Where they disagree, **the data wins** and the gap is a bug. Companion to the Pillar-1
data design (52 user-side shelves) and the hand-drawn Pillar-3 sheets.

---

## 1. The measuring stick — 16 job-side shelves

The `Job` dataclass has 31 fields, but 15 are computed scores or lifecycle state the
pipeline writes. Only **16 are provider shelves** a source can fill:

`title` `company` `apply_url` `location` `description` `salary_min` `salary_max`
`visa_flag` `experience_level` `posted_at` `date_confidence` `date_posted_raw`
`deadline` `deadline_source` `category` `DOMAINS`

> **The asymmetry:** user side = **52** shelves, job side = **16**. Of those 16, three are
> ~0% filled. Real usable job-side structure ≈ **13 vs 52**. This is `big × 0 = 0` quantified.

---

## 2. League table — shelves filled, by code (46 sources)

| Shelves | Sources |
|:—:|---|
| **14** | `devitjobs` |
| **12** | `gov_apprenticeships` |
| **11** | `adzuna` `careerjet` `google_jobs` `jsearch` `reed` · `himalayas` `jobicy` `remotive` · `recruitee` · `nhs_jobs` · `indeed(JobSpy)` `nofluffjobs` |
| **10** | `aijobs` `landingjobs` `remoteok` `teaching_vacancies` · `nhs_jobs_xml` `weworkremotely` · `eightykhours` · `hackernews` `themuse` |
| **9** | `findwork` `jooble` · `arbeitnow` `hn_jobs` · `lever` `ashby` `smartrecruiters` `workday` `rippling` · `jobs_ac_uk` `realworkfromanywhere` `workanywhere` `biospace` |
| **8** | `pinpoint` `uni_jobs` `climatebase` |
| **7** | `greenhouse` (82 companies — most volume, thin data) |
| **6** | `workable` `personio` `aijobs_ai` `linkedin` |
| **4** | `successfactors` `bcs_jobs` |

**Workday 9/16 vs Ashby 9/16** — tied on count, but Ashby has a real `publishedAt`
timestamp (`date_confidence="high"`) while Workday regex-parses *"Posted 3 Days Ago"*
(capped at `"medium"`), and Workday caps description detail-fetches at 40/run.

---

## 3. Real fill rates — 6,046 jobs, 28 sources that actually returned data

`n` = jobs; other columns = % of that source's rows with a real value.

| source | n | loc | descr | sal | posted | dead | exp | visa |
|---|--:|--:|--:|--:|--:|--:|--:|--:|
| devitjobs | 2154 | 100 | **0** | 85 | **0** | 0 | 33 | 0 |
| greenhouse | 859 | 100 | **0** | 0 | **0** | 0 | 42 | 0 |
| workday | 823 | 100 | **0** | 0 | 100 | 0 | 44 | 0 |
| indeed | 350 | 100 | 100 | 0 | 100 | 1 | 43 | 7 |
| adzuna | 245 | 100 | 100 | **99** | 100 | 0 | 35 | 0 |
| reed | 207 | 100 | 100 | 61 | 100 | 0 | 30 | 0 |
| arbeitnow | 170 | 98 | 100 | 0 | 100 | 0 | 27 | 10 |
| hackernews | 149 | 88 | 100 | 0 | 100 | 0 | 1 | 7 |
| google_jobs | 135 | 100 | 100 | 11 | 73 | 1 | 50 | 9 |
| eightykhours | 126 | 100 | 100 | 0 | **0** | 0 | 43 | 0 |
| uni_jobs | 113 | 100 | 100 | 0 | 100 | **31** | 17 | 8 |
| ashby | 111 | 100 | 100 | 0 | 100 | 0 | 32 | 1 |
| remoteok | 99 | 100 | 100 | 0 | 100 | 1 | 4 | 1 |
| smartrecruiters | 93 | 100 | **0** | 0 | 100 | 0 | 54 | 0 |
| linkedin | 86 | 100 | **0** | 0 | **0** | 0 | 50 | 0 |
| realworkfromanywhere | 79 | 100 | 100 | 0 | 100 | 0 | 52 | 3 |
| weworkremotely | 50 | 100 | 100 | 0 | 100 | 0 | 40 | 10 |
| themuse | 44 | 100 | 100 | 0 | 100 | 0 | 52 | 11 |
| lever | 31 | 100 | 87 | 0 | 100 | 0 | 52 | 0 |
| aijobs_ai | 27 | **4** | 100 | 0 | **0** | 0 | 52 | 0 |
| nofluffjobs | 20 | 100 | **0** | 95 | 100 | 0 | 25 | 0 |
| hn_jobs | 16 | **0** | 100 | 0 | 100 | 0 | 50 | 6 |
| jobicy | 14 | 100 | 100 | 0 | 100 | 0 | 79 | 0 |
| remotive | 13 | 100 | 100 | 8 | 100 | 0 | 54 | 8 |
| landingjobs | 12 | 100 | 100 | 0 | 100 | 0 | 42 | 0 |
| himalayas | 10 | 50 | 100 | 0 | 100 | 0 | 40 | 0 |
| bcs_jobs | 7 | 100 | 100 | 0 | **0** | 0 | 0 | 0 |
| pinpoint | 3 | 100 | 100 | 0 | **0** | 0 | 67 | 0 |

**Only 28 of 46 sources returned any jobs.** 18 produced nothing in this run.

### Catalog-wide emptiness

| Shelf | Empty | % of catalog |
|---|--:|--:|
| `deadline` | 6,004 | **99%** |
| `description` | 4,039 | **67%** |
| `salary_min` | 3,814 | **63%** |
| `posted_at` | 3,299 | **55%** |

### `date_confidence` distribution (real values)

| value | rows |
|---|--:|
| `low` | 3,210 |
| `high` | 1,826 |
| `medium` | 921 |
| **`fabricated`** | **89** ← not a valid enum value |

---

## 4. Where code and data DISAGREE (these are bugs)

| Source | Code says | Data shows | Impact |
|---|---|---|---|
| **greenhouse** | sets `description` via `?content=true` for every job | **0%** of 859 rows | 859 jobs unscoreable on skills |
| **devitjobs** | sets `description` + `posted_at` | **0%** and **0%** of 2,154 rows | largest source, no text, no date |
| **workday** | description capped at 40/run | **0%** of 823 rows | even the 40 aren't landing |
| **smartrecruiters** | description capped at 60/run | **0%** of 93 rows | same |
| **nofluffjobs** | never reads `description` (known gap) | 0% ✓ consistent | recoverable — API has the field |
| **uni_jobs** | audit found **no** source sets `deadline` | **31%** filled | a downstream extractor fills it — undocumented |

**The top 3 sources by volume — devitjobs (2,154), greenhouse (859), workday (823) —
carry 3,836 jobs (63% of the catalog) with ZERO description.** `description` is what the
skill matcher reads. Those jobs cannot score on skills at all.

Two audit predictions the data **confirmed**: `aijobs_ai` location 4% (code comment said
the card markup carries no location) and `hn_jobs` location 0% (hardcoded `""`).

---

## 5. Shelves that are dead at source (code audit, all 46)

| Shelf | Sources filling it |
|---|---|
| `deadline` / `deadline_source` | **0 / 46** |
| `visa_flag` | **1 / 46** (devitjobs) |
| `experience_level` | **2 / 46** (devitjobs, themuse) |
| `salary_min/max` | 18 / 46 |

`experience_level` and `visa_flag` nevertheless show 0–79% and 0–11% in the data —
i.e. something **downstream** derives them. That derivation is undocumented and should be
made explicit in the Pillar-3 design (extracted vs derived, per the hand-drawn sheet).

---

## 6. Recoverable losses — the data exists, the code drops it

| Source | What is thrown away | Fix |
|---|---|---|
| `nhs_jobs` | parses real `closingDate` → writes it to `date_posted_raw`, never `deadline` | one line |
| `nhs_jobs_xml` | same NHS upstream as `nhs_jobs`, salary parsing never ported | port `_parse_salary` |
| `nofluffjobs` | structured API exposes `description`, never read | one line |
| `climatebase` | mines `__NEXT_DATA__` for salary but reads **no date field** | add date parse |
| `teaching_vacancies` | schema.org `JobPosting.validThrough` available, never read | maps to `deadline` |

---

## 7. Data-integrity defects found

1. **`date_confidence="fabricated"`** — hardcoded in `workable.py:50`, `pinpoint.py:58`,
   `personio.py:90`, `linkedin.py:95`. **89 real rows carry it.** It is outside the
   documented `high|medium|low` enum, so every recency comparison against those rows is
   undefined behaviour.
2. **`gov_apprenticeships.py:134`** sets `date_confidence="high"` from *presence*, not
   parsing, and stores `posted_at` unparsed — re-introducing exactly the bug
   `utils/dates.py:19-27` was written to prevent ("we certified as trustworthy a value we
   could not parse").
3. **`google_jobs.py`** can never reach `"high"` — only parses relative text, capped at
   `"medium"`. Permanently second-class in recency scoring.
4. **`successfactors.py`** sets `description = title` (`:77`) — the same guessed string
   twice — and hardcodes `location="UK"`. Only company + apply URL are trustworthy.
5. **Registry vs reality:** `SOURCE_REGISTRY` has both `indeed` and `glassdoor`, but
   `main.py:282` builds JobSpy with `sites=["indeed"]` only. **Glassdoor is inert.**

Honest counter-example worth preserving: **`jooble.py:74-75`** deliberately sets
`posted_at=None` because Jooble's `updated` is a mutation date, refusing to fake freshness.
That is the correct behaviour — honest-but-empty beats confident-but-wrong.

---

## 8. What this means for the Pillar-3 design

- The job side is structurally **13 usable shelves vs the user side's 52**. Fixing Pillar 1
  further yields nothing until the job side widens — the match is a product, so the weaker
  side caps it.
- **Most emptiness is code, not providers.** NHS publishes closing dates, NoFluffJobs
  publishes descriptions, Reed/Adzuna publish salary. The mappings were never written. So
  Pillar 3 is largely a *wiring* project, not a *data acquisition* project.
- **Priority 1 is `description` on the top-3 volume sources** (63% of the catalog). Nothing
  else moves match quality as much.
- Every derived shelf needs an explicit **confidence** notion, not just dates — the
  "extracted vs derived" split from the hand-drawn sheet, made real.

## 9. Reproduce

```bash
docker exec job360-dev-postgres psql -U job360 -d job360 -c "
SELECT source, COUNT(*) n,
 ROUND(100.0*COUNT(*) FILTER (WHERE description<>'')/COUNT(*)) descr,
 ROUND(100.0*COUNT(*) FILTER (WHERE salary_min IS NOT NULL)/COUNT(*)) sal,
 ROUND(100.0*COUNT(*) FILTER (WHERE posted_at IS NOT NULL)/COUNT(*)) posted
FROM jobs GROUP BY source ORDER BY n DESC;"
```
