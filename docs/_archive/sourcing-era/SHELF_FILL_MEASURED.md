# Shelf fill — MEASURED before/after (gate vocabulary batch)
<!-- doc: FROZEN -->

> **FROZEN — closed or superseded.** Kept as evidence of the sourcing era (deleted 2026-09-05, #483). Live truth: docs/product/VISION.md. <!-- banner: auto -->

Measured 2026-08-17 by an independent verification session. Re-measure, do not trust.

- **Before**: DB `shelf_baseline` (kept on local Postgres :5433) — the real 40-source run of 2026-08-17 08:00 UTC, run_uuid `6c84966b-bc5d-4379-b867-53b0b99d89c7`, 2,772 stored rows. Table regenerated from its `shelf_provenance` and verified cell-identical to `backend/data/logs/shelf_fill_baseline.md`.
- **After**: DB `shelf_after_verify` — the SAME pipeline, SAME search config, run 2026-08-17 ~10:52 UTC+1 against the current working tree (branch `fix/pillar3-source-repairs`, uncommitted).
- Fill rule: a shelf counts as filled when its provenance `how` != `absent` (same rule as the baseline report).

## How to re-measure (exact recipe)

```
# 1. fresh DB + schema exactly as the app boots (init_db THEN migrations):
docker exec job360-dev-postgres psql -U job360 -d postgres -c "DROP DATABASE IF EXISTS shelf_after_verify;" -c "CREATE DATABASE shelf_after_verify;"
# 2. runner script (write under backend/scripts/, delete after): set
#    DATABASE_URL=postgresql://job360:job360dev@127.0.0.1:5433/shelf_after_verify
#    and ENGINE2/3/4, ENRICHMENT, SEMANTIC, MATCHER all =false BEFORE importing src;
#    then: JobDatabase(DB_PATH).init_db(); migrations.runner.up(DB_PATH);
#    profile = UserProfile(cv_data=CVData(raw_text='AI/ML Engineer ... Python, SQL and Machine Learning.',
#        skills=['Python','SQL','Machine Learning'], job_titles=['AI/ML Engineer','Data Engineer','Software Engineer'],
#        headline='AI/ML Engineer | Generative AI Specialist'), preferences=UserPreferences())
#    run_search(user_id=None, search_config=generate_search_config(profile), no_notify=True)
# 3. count: SELECT source, shelf_provenance FROM jobs; filled = how != 'absent';
#    per-source % = filled / that source's stored rows.
```

## Run parity (network variance bounds)

| | baseline | after |
|---|---|---|
| stored rows | 2772 | 2915 |
| sources with stored rows | 34 | 35 |

Both runs: workday, devitjobs, nofluffjobs failed (timeout) — no rows either side.

## 1. Global per-shelf fill, before -> after (all stored rows)

| shelf | before | after | before rows | after rows |
|---|---|---|---|---|
| title | 100.0% | 100.0% | 2772/2772 | 2915/2915 |
| company | 98.7% | 99.1% | 2737/2772 | 2889/2915 |
| location | 99.9% | 99.9% | 2769/2772 | 2911/2915 |
| description | 96.8% | 96.9% | 2682/2772 | 2826/2915 |
| posted_at **(moved)** | 80.7% | 81.7% | 2236/2772 | 2381/2915 |
| deadline **(moved)** | 34.9% | 37.3% | 967/2772 | 1088/2915 |
| salary **(moved)** | 28.6% | 33.1% | 792/2772 | 964/2915 |
| visa_status | 1.6% | 1.6% | 45/2772 | 46/2915 |
| employment_type **(moved)** | 26.9% | 57.9% | 745/2772 | 1688/2915 |
| seniority **(moved)** | 0.1% | 7.5% | 3/2772 | 219/2915 |
| workplace_mode **(moved)** | 1.5% | 23.9% | 42/2772 | 696/2915 |
| skills | 4.3% | 4.6% | 119/2772 | 133/2915 |
| category **(moved)** | 0.0% | 7.5% | 0/2772 | 218/2915 |

## 2. Per-source before -> after on the five problem shelves

`-` means the source stored no rows in that run (n in brackets: baseline -> after).

| source | n (b->a) | employment_type | seniority | workplace_mode | category | visa_status |
|---|---|---|---|---|---|---|
| adzuna | 113 -> 110 | 30.1 -> 64.5 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| aijobs_ai | 2 -> 2 | 0.0 -> 50.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| arbeitnow | 32 -> 34 | 0.0 -> 55.9 | 0.0 -> 0.0 | 0.0 -> 8.8 | 0.0 -> 0.0 | 9.4 -> 8.8 |
| ashby | 38 -> 38 | 2.6 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 68.4 | 0.0 -> 0.0 | 2.6 -> 2.6 |
| bcs_jobs | 7 -> 7 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| careerjet | 147 -> 145 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| climatebase | 22 -> 11 | 0.0 -> 90.9 | 0.0 -> 0.0 | 27.3 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| eightykhours | 82 -> 83 | 0.0 -> 98.8 | 0.0 -> 91.6 | 0.0 -> 26.5 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| findwork | 9 -> 9 | 11.1 -> 11.1 | 0.0 -> 0.0 | 22.2 -> 22.2 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| google_jobs | 39 -> 35 | 0.0 -> 91.4 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 7.7 -> 5.7 |
| gov_apprenticeships | 156 -> 153 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 32.7 | 0.0 -> 0.0 |
| greenhouse | 137 -> 138 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 10.9 -> 10.9 |
| hackernews | 55 -> 55 | 0.0 -> 49.1 | 0.0 -> 0.0 | 0.0 -> 9.1 | 0.0 -> 0.0 | 3.6 -> 3.6 |
| himalayas | 2 -> 2 | 100.0 -> 100.0 | 0.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| indeed | 160 -> 159 | 1.9 -> 66.7 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 3.1 -> 3.1 |
| jobicy | 5 -> 5 | 100.0 -> 100.0 | 0.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 100.0 | 0.0 -> 0.0 |
| jsearch | 0 -> 27 | - -> 96.3 | - -> 0.0 | - -> 3.7 | - -> 0.0 | - -> 7.4 |
| landingjobs | 10 -> 10 | 100.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| lever | 34 -> 34 | 2.9 -> 91.2 | 0.0 -> 0.0 | 100.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| linkedin | 9 -> 21 | 0.0 -> 14.3 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| nhs_jobs | 7 -> 7 | 0.0 -> 57.1 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| personio | 36 -> 34 | 13.9 -> 94.1 | 0.0 -> 17.6 | 0.0 -> 0.0 | 0.0 -> 67.6 | 5.6 -> 2.9 |
| pinpoint | 371 -> 371 | 12.4 -> 96.0 | 0.0 -> 0.0 | 0.0 -> 95.7 | 0.0 -> 0.0 | 0.3 -> 0.3 |
| realworkfromanywhere | 58 -> 59 | 0.0 -> 15.3 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| recruitee | 196 -> 195 | 0.0 -> 99.5 | 0.0 -> 53.8 | 0.0 -> 100.0 | 0.0 -> 33.8 | 0.5 -> 0.5 |
| reed | 226 -> 362 | 0.0 -> 0.3 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.9 -> 0.8 |
| remoteok | 24 -> 23 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| remotive | 1 -> 1 | 0.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 100.0 | 0.0 -> 0.0 |
| smartrecruiters | 26 -> 24 | 100.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| successfactors | 5 -> 5 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 |
| teaching_vacancies | 391 -> 377 | 100.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.3 -> 0.3 |
| themuse | 30 -> 31 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 45.2 | 3.3 -> 3.2 |
| uni_jobs | 105 -> 106 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 1.9 -> 1.9 |
| weworkremotely | 46 -> 46 | 100.0 -> 100.0 | 0.0 -> 0.0 | 0.0 -> 0.0 | 0.0 -> 67.4 | 0.0 -> 0.0 |
| workable | 191 -> 196 | 91.1 -> 91.8 | 1.6 -> 12.8 | 0.0 -> 4.1 | 0.0 -> 14.3 | 3.1 -> 3.1 |

## 3. Full per-source x per-shelf fill % — AFTER run

| source | n | title | company | location | description | posted_at | deadline | salary | visa_status | employment_type | seniority | workplace_mode | skills | category |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| adzuna | 110 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% | 64.5% | 0.0% | 0.0% | 0.0% | 0.0% |
| aijobs_ai | 2 | 100.0% | 100.0% | 100.0% | 50.0% | 50.0% | 0.0% | 0.0% | 0.0% | 50.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| arbeitnow | 34 | 100.0% | 100.0% | 94.1% | 100.0% | 100.0% | 0.0% | 0.0% | 8.8% | 55.9% | 0.0% | 8.8% | 97.1% | 0.0% |
| ashby | 38 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 36.8% | 2.6% | 100.0% | 0.0% | 68.4% | 0.0% | 0.0% |
| bcs_jobs | 7 | 100.0% | 0.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| careerjet | 145 | 100.0% | 98.6% | 100.0% | 84.1% | 0.0% | 0.7% | 31.7% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| climatebase | 11 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% | 0.0% | 0.0% | 90.9% | 0.0% | 100.0% | 100.0% | 0.0% |
| eightykhours | 83 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 3.6% | 62.7% | 0.0% | 98.8% | 91.6% | 26.5% | 0.0% | 0.0% |
| findwork | 9 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 11.1% | 0.0% | 22.2% | 100.0% | 0.0% |
| google_jobs | 35 | 100.0% | 100.0% | 97.1% | 100.0% | 85.7% | 2.9% | 28.6% | 5.7% | 91.4% | 0.0% | 0.0% | 0.0% | 0.0% |
| gov_apprenticeships | 153 | 100.0% | 100.0% | 100.0% | 90.8% | 100.0% | 100.0% | 98.7% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 32.7% |
| greenhouse | 138 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 10.9% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| hackernews | 55 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 3.6% | 49.1% | 0.0% | 9.1% | 0.0% | 0.0% |
| himalayas | 2 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% |
| indeed | 159 | 100.0% | 95.6% | 100.0% | 100.0% | 100.0% | 1.9% | 0.0% | 3.1% | 66.7% | 0.0% | 0.0% | 0.0% | 0.0% |
| jobicy | 5 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 40.0% | 0.0% | 100.0% | 100.0% | 0.0% | 0.0% | 100.0% |
| jsearch | 27 | 100.0% | 100.0% | 96.3% | 100.0% | 100.0% | 0.0% | 0.0% | 7.4% | 96.3% | 0.0% | 3.7% | 0.0% | 0.0% |
| landingjobs | 10 | 100.0% | 0.0% | 100.0% | 100.0% | 100.0% | 100.0% | 50.0% | 0.0% | 100.0% | 0.0% | 100.0% | 100.0% | 0.0% |
| lever | 34 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 91.2% | 0.0% | 100.0% | 0.0% | 0.0% |
| linkedin | 21 | 100.0% | 100.0% | 100.0% | 14.3% | 100.0% | 14.3% | 0.0% | 0.0% | 14.3% | 0.0% | 0.0% | 0.0% | 0.0% |
| nhs_jobs | 7 | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 100.0% | 42.9% | 0.0% | 57.1% | 0.0% | 0.0% | 0.0% | 0.0% |
| personio | 34 | 100.0% | 100.0% | 100.0% | 94.1% | 100.0% | 0.0% | 11.8% | 2.9% | 94.1% | 17.6% | 0.0% | 41.2% | 67.6% |
| pinpoint | 371 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 18.1% | 50.1% | 0.3% | 96.0% | 0.0% | 95.7% | 0.0% | 0.0% |
| realworkfromanywhere | 59 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 15.3% | 5.1% | 0.0% | 15.3% | 0.0% | 0.0% | 0.0% | 0.0% |
| recruitee | 195 | 100.0% | 100.0% | 100.0% | 98.5% | 100.0% | 0.0% | 20.0% | 0.5% | 99.5% | 53.8% | 100.0% | 0.0% | 33.8% |
| reed | 362 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 73.8% | 0.8% | 0.3% | 0.0% | 0.0% | 0.0% | 0.0% |
| remoteok | 23 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 13.0% | 0.0% | 0.0% | 0.0% | 0.0% | 100.0% | 0.0% |
| remotive | 1 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 100.0% | 0.0% | 100.0% | 0.0% | 0.0% | 100.0% | 100.0% |
| smartrecruiters | 24 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 95.8% | 0.0% | 100.0% | 0.0% | 100.0% | 0.0% | 0.0% |
| successfactors | 5 | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| teaching_vacancies | 377 | 100.0% | 100.0% | 100.0% | 99.2% | 100.0% | 100.0% | 6.9% | 0.3% | 100.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| themuse | 31 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 3.2% | 0.0% | 0.0% | 0.0% | 67.7% | 45.2% |
| uni_jobs | 106 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 41.5% | 17.9% | 1.9% | 0.0% | 0.0% | 0.0% | 0.0% | 0.0% |
| weworkremotely | 46 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 100.0% | 0.0% | 0.0% | 19.6% | 67.4% |
| workable | 196 | 100.0% | 100.0% | 100.0% | 100.0% | 100.0% | 0.0% | 0.0% | 3.1% | 91.8% | 12.8% | 4.1% | 0.0% | 14.3% |

## 4. Still at 0% — and WHY (provenance `why` + raw samples, AFTER run)

### employment_type: 8 sources still at 0%

| source | n | why (count) | raw the gate saw (top, honest refusals) |
|---|---|---|---|
| bcs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| careerjet | 145 | not_mapped=145 | (no raw value — source sends nothing for this shelf) |
| gov_apprenticeships | 153 | not_mapped=153 | (no raw value — source sends nothing for this shelf) |
| greenhouse | 138 | not_mapped=138 | (no raw value — source sends nothing for this shelf) |
| remoteok | 23 | not_mapped=23 | (no raw value — source sends nothing for this shelf) |
| successfactors | 5 | not_mapped=5 | (no raw value — source sends nothing for this shelf) |
| themuse | 31 | not_mapped=31 | (no raw value — source sends nothing for this shelf) |
| uni_jobs | 106 | not_mapped=106 | (no raw value — source sends nothing for this shelf) |

### seniority: 29 sources still at 0%

| source | n | why (count) | raw the gate saw (top, honest refusals) |
|---|---|---|---|
| adzuna | 110 | not_mapped=110 | (no raw value — source sends nothing for this shelf) |
| aijobs_ai | 2 | not_mapped=2 | (no raw value — source sends nothing for this shelf) |
| arbeitnow | 34 | not_mapped=34 | (no raw value — source sends nothing for this shelf) |
| ashby | 38 | not_mapped=38 | (no raw value — source sends nothing for this shelf) |
| bcs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| careerjet | 145 | not_mapped=145 | (no raw value — source sends nothing for this shelf) |
| climatebase | 11 | not_mapped=11 | (no raw value — source sends nothing for this shelf) |
| findwork | 9 | not_mapped=9 | (no raw value — source sends nothing for this shelf) |
| google_jobs | 35 | not_mapped=35 | (no raw value — source sends nothing for this shelf) |
| gov_apprenticeships | 153 | not_mapped=153 | `Advanced` x91; `Intermediate` x47; `Higher` x13; `Degree` x2 |
| greenhouse | 138 | not_mapped=138 | (no raw value — source sends nothing for this shelf) |
| hackernews | 55 | not_mapped=55 | (no raw value — source sends nothing for this shelf) |
| indeed | 159 | not_mapped=159 | (no raw value — source sends nothing for this shelf) |
| jsearch | 27 | not_mapped=27 | (no raw value — source sends nothing for this shelf) |
| landingjobs | 10 | not_mapped=10 | (no raw value — source sends nothing for this shelf) |
| lever | 34 | not_mapped=34 | (no raw value — source sends nothing for this shelf) |
| linkedin | 21 | not_mapped=21 | (no raw value — source sends nothing for this shelf) |
| nhs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| pinpoint | 371 | not_mapped=371 | (no raw value — source sends nothing for this shelf) |
| realworkfromanywhere | 59 | not_mapped=59 | (no raw value — source sends nothing for this shelf) |
| reed | 362 | not_mapped=362 | (no raw value — source sends nothing for this shelf) |
| remoteok | 23 | not_mapped=23 | (no raw value — source sends nothing for this shelf) |
| remotive | 1 | not_mapped=1 | (no raw value — source sends nothing for this shelf) |
| smartrecruiters | 24 | not_mapped=24 | `mid_senior_level` x24 |
| successfactors | 5 | not_mapped=5 | (no raw value — source sends nothing for this shelf) |
| teaching_vacancies | 377 | not_mapped=377 | (no raw value — source sends nothing for this shelf) |
| themuse | 31 | not_mapped=31 | (no raw value — source sends nothing for this shelf) |
| uni_jobs | 106 | not_mapped=106 | (no raw value — source sends nothing for this shelf) |
| weworkremotely | 46 | not_mapped=46 | (no raw value — source sends nothing for this shelf) |

### workplace_mode: 22 sources still at 0%

| source | n | why (count) | raw the gate saw (top, honest refusals) |
|---|---|---|---|
| adzuna | 110 | not_mapped=110 | (no raw value — source sends nothing for this shelf) |
| aijobs_ai | 2 | not_mapped=2 | (no raw value — source sends nothing for this shelf) |
| bcs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| careerjet | 145 | not_mapped=145 | (no raw value — source sends nothing for this shelf) |
| google_jobs | 35 | not_mapped=35 | (no raw value — source sends nothing for this shelf) |
| gov_apprenticeships | 153 | not_mapped=153 | (no raw value — source sends nothing for this shelf) |
| greenhouse | 138 | not_mapped=138 | (no raw value — source sends nothing for this shelf) |
| himalayas | 2 | not_mapped=2 | (no raw value — source sends nothing for this shelf) |
| indeed | 159 | not_mapped=159 | (no raw value — source sends nothing for this shelf) |
| jobicy | 5 | not_mapped=5 | (no raw value — source sends nothing for this shelf) |
| linkedin | 21 | not_mapped=21 | (no raw value — source sends nothing for this shelf) |
| nhs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| personio | 34 | not_mapped=34 | (no raw value — source sends nothing for this shelf) |
| realworkfromanywhere | 59 | not_mapped=59 | `TELECOMMUTE` x9 |
| reed | 362 | not_mapped=362 | (no raw value — source sends nothing for this shelf) |
| remoteok | 23 | not_mapped=23 | (no raw value — source sends nothing for this shelf) |
| remotive | 1 | not_mapped=1 | (no raw value — source sends nothing for this shelf) |
| successfactors | 5 | not_mapped=5 | (no raw value — source sends nothing for this shelf) |
| teaching_vacancies | 377 | not_mapped=377 | (no raw value — source sends nothing for this shelf) |
| themuse | 31 | not_mapped=31 | (no raw value — source sends nothing for this shelf) |
| uni_jobs | 106 | not_mapped=106 | (no raw value — source sends nothing for this shelf) |
| weworkremotely | 46 | not_mapped=46 | (no raw value — source sends nothing for this shelf) |

### category: 27 sources still at 0%

| source | n | why (count) | raw the gate saw (top, honest refusals) |
|---|---|---|---|
| adzuna | 110 | not_mapped=110 | `IT Jobs` x100; `Engineering Jobs` x9; `Trade & Construction Jobs` x1 |
| aijobs_ai | 2 | not_mapped=2 | (no raw value — source sends nothing for this shelf) |
| arbeitnow | 34 | not_mapped=34 | (no raw value — source sends nothing for this shelf) |
| ashby | 38 | not_mapped=38 | (no raw value — source sends nothing for this shelf) |
| bcs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| careerjet | 145 | not_mapped=145 | (no raw value — source sends nothing for this shelf) |
| climatebase | 11 | not_mapped=11 | (no raw value — source sends nothing for this shelf) |
| eightykhours | 83 | not_mapped=83 | (no raw value — source sends nothing for this shelf) |
| findwork | 9 | not_mapped=9 | (no raw value — source sends nothing for this shelf) |
| google_jobs | 35 | not_mapped=35 | (no raw value — source sends nothing for this shelf) |
| greenhouse | 138 | not_mapped=138 | (no raw value — source sends nothing for this shelf) |
| hackernews | 55 | not_mapped=55 | (no raw value — source sends nothing for this shelf) |
| himalayas | 2 | not_mapped=2 | (no raw value — source sends nothing for this shelf) |
| indeed | 159 | not_mapped=159 | (no raw value — source sends nothing for this shelf) |
| jsearch | 27 | not_mapped=27 | (no raw value — source sends nothing for this shelf) |
| landingjobs | 10 | not_mapped=10 | (no raw value — source sends nothing for this shelf) |
| lever | 34 | not_mapped=34 | (no raw value — source sends nothing for this shelf) |
| linkedin | 21 | not_mapped=21 | (no raw value — source sends nothing for this shelf) |
| nhs_jobs | 7 | not_mapped=7 | (no raw value — source sends nothing for this shelf) |
| pinpoint | 371 | not_mapped=371 | (no raw value — source sends nothing for this shelf) |
| realworkfromanywhere | 59 | not_mapped=59 | (no raw value — source sends nothing for this shelf) |
| reed | 362 | not_mapped=362 | (no raw value — source sends nothing for this shelf) |
| remoteok | 23 | not_mapped=23 | (no raw value — source sends nothing for this shelf) |
| smartrecruiters | 24 | not_mapped=24 | (no raw value — source sends nothing for this shelf) |
| successfactors | 5 | not_mapped=5 | (no raw value — source sends nothing for this shelf) |
| teaching_vacancies | 377 | not_mapped=377 | (no raw value — source sends nothing for this shelf) |
| uni_jobs | 106 | not_mapped=106 | `Assistant staff` x10; `Academic` x6; `Academic-related` x4 |

### visa_status: 20 sources still at 0%

| source | n | why (count) | raw the gate saw (top, honest refusals) |
|---|---|---|---|
| adzuna | 110 | not_stated=110 | (no raw value — source sends nothing for this shelf) |
| aijobs_ai | 2 | not_stated=2 | (no raw value — source sends nothing for this shelf) |
| bcs_jobs | 7 | not_stated=7 | (no raw value — source sends nothing for this shelf) |
| careerjet | 145 | not_stated=145 | (no raw value — source sends nothing for this shelf) |
| climatebase | 11 | not_stated=11 | (no raw value — source sends nothing for this shelf) |
| eightykhours | 83 | not_stated=83 | (no raw value — source sends nothing for this shelf) |
| findwork | 9 | not_stated=9 | (no raw value — source sends nothing for this shelf) |
| gov_apprenticeships | 153 | not_stated=153 | (no raw value — source sends nothing for this shelf) |
| himalayas | 2 | not_stated=2 | (no raw value — source sends nothing for this shelf) |
| jobicy | 5 | not_stated=5 | (no raw value — source sends nothing for this shelf) |
| landingjobs | 10 | not_stated=10 | (no raw value — source sends nothing for this shelf) |
| lever | 34 | not_stated=34 | (no raw value — source sends nothing for this shelf) |
| linkedin | 21 | not_stated=21 | (no raw value — source sends nothing for this shelf) |
| nhs_jobs | 7 | not_stated=7 | (no raw value — source sends nothing for this shelf) |
| realworkfromanywhere | 59 | not_stated=59 | (no raw value — source sends nothing for this shelf) |
| remoteok | 23 | not_stated=23 | (no raw value — source sends nothing for this shelf) |
| remotive | 1 | not_stated=1 | (no raw value — source sends nothing for this shelf) |
| smartrecruiters | 24 | not_stated=24 | (no raw value — source sends nothing for this shelf) |
| successfactors | 5 | not_stated=5 | (no raw value — source sends nothing for this shelf) |
| weworkremotely | 46 | not_stated=46 | (no raw value — source sends nothing for this shelf) |

`not_stated` on visa means the regex detector DID look at the ad text and the ad never said —
different from `not_mapped` (no structured field reached the gate). visa_status is the one
problem shelf that did NOT move (1.6% -> 1.6%): its only structured feed, devitjobs'
`hasVisaSponsorship`, timed out in BOTH runs, so the new source-signal path in
`_fill_visa_status` never got a live input in either measurement.

## 5. Replay check — identical inputs, gate change isolated

The live before/after above mixes two causes: the gate's new vocabulary AND source files
that started sending raw values only after the 08:00 baseline run. To isolate the gate,
the 2,772 baseline rows' stored provenance raws were replayed through the new
`_normalize_closed_enum`:

| shelf | baseline gate | new gate (same inputs) |
|---|---|---|
| employment_type | 26.9% (745) | 49.1% (1361) |
| seniority | 0.1% (3) | 7.7% (213) |
| workplace_mode | 1.5% (42) | 2.1% (58) |
| category | 0.0% (0) | 0.0% (0) |

This exactly reproduces the gate agent's claimed replay numbers. The rest of the live
delta (workplace_mode 2.1% -> 23.9%, category 0% -> 7.5%) comes from the source-side raw
feeds (ashby `workplaceType`, pinpoint `workplace_type_text`, recruitee/smartrecruiters
booleans, and the seven category-sending sources) that were not yet in the tree at 08:00,
so the baseline DB holds no raws for the replay to act on.

## 6. Rule #29 + policy checks (verification session)

- 32/32 spot-check cases pass: never-seen strings (`Quantum Flexi-Shift`, `Moon Base`) land
  `absent/not_mapped` with raw preserved; deliberately-unmapped observed values
  (`experienced`, `Mid-Senior level`, `Advanced`, `Expert`, `IT Jobs`, `Bank`, `Zero Hours`)
  stay absent; alias hits keep the raw string; the enums' own `unknown` member is never a
  normalisation target.
- Alias fabrication audit: every alias key traced to an observed raw in the two run DBs or
  re-confirmed by direct live vocabulary probes (remotive/jobicy/nofluffjobs/devitjobs/
  adzuna categories endpoint/lever spotify/recruitee boards/personio boards, 2026-08-17).
  Residual keys resting on published closed vocabularies or the gate agent's larger dated
  probes, not re-observed today (rare tail values): `trainee`, `student`, personio's
  `human_resources`/`accounting_and_finance`/`production_and_operations`/
  `logistics_and_transportation` buckets, nofluffjobs `game_dev`/`hr`/`law`, DfE route
  `education_and_childcare`. One dead row: `marketing_and_pr_jobs` is NOT in Adzuna's
  official 30-category GB taxonomy (probed live) — harmless (never fires), prune or cite.
- **POLICY LEAK (must fix): `backend/src/sources/ats/pinpoint.py:47-65`** —
  `_EMPLOYMENT_TYPE_MAP` translates Pinpoint's vocabulary to target enum values INSIDE the
  source. Beyond violating the one-chokepoint rule, it destroys provenance: 351/371
  pinpoint rows in the after DB have `raw=NULL` on employment_type because the string was
  already `full_time` when the gate saw it (rows the GATE aliases handled — `Seasonal -
  Full Time`, `Apprentice` — kept their raws). The gate's `_EMPLOYMENT_TYPE_ALIASES`
  already covers the identical compound family (replay: `Permanent - Full Time` x156
  mapped from stored raws alone), so deleting the source-side map loses no fill.
- Newly observed refusals worth a future mapping pass (raws already preserved):
  realworkfromanywhere `TELECOMMUTE` x9 (workplace), smartrecruiters `mid_senior_level`
  x24 (seniority — same two-tier fusion as workable's, correctly refused), uni_jobs
  `Assistant staff`/`Academic`/`Academic-related` (category — job families, correctly
  refused).

## 7. Tests + lint (verification session, 2026-08-17)

```
python -X utf8 -m pytest tests/test_universal_shelf.py tests/test_sources.py \
  tests/test_database.py tests/test_scorer.py tests/test_design_rules.py -q -p no:randomly
330 passed in 157.59s

python -m ruff check src tests   -> All checks passed!
python -m ruff check .           -> 22 errors, all in untracked throwaway scripts under
                                    backend/scripts/ (other sessions' probes), none in src/ or tests/
```

Nothing was committed or pushed: HEAD `da99221` unchanged and equal to
`origin/fix/pillar3-source-repairs`; all changes remain working-tree only.

