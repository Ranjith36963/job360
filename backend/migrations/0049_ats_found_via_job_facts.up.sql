-- 0049_ats_found_via_job_facts: three owner decisions (2026-10-04), all
-- nullable, all "unset = NULL" (rule #29 — Job360 invents no default).
--
-- 1. ATS opinion on a saved CV / cover letter. The ASSISTANT runs the ATS
--    check and passes its own score + notes with save_artifact; Job360 only
--    stores them on that artifact VERSION (append-only: a re-check is a new
--    version row, never an UPDATE of an old one — guard: tests/
--    test_application_spine.py::test_events_are_append_only).
--
-- 2. found_via on a contact — where this person was found (closed set
--    CONTACT_FOUND_VIA in src/core/settings.py). The base row is append-only
--    (S12); a later correction is a `contact_edits` row with field =
--    'found_via', exactly like name/role/email.
--
-- 3. The job's country (ISO 3166-1 alpha-2, upper), whether it is remote,
--    and where the user found the ad (closed set JOB_FOUND_ON). Stored on
--    `applications` — the USER's own snapshot of the job, beside job_title /
--    job_location — NOT on `jobs`: `jobs` is the SHARED catalog (hard rule
--    #10; two users bringing the same ad share one row), so a per-user fact
--    there would let one user's bring or fix overwrite another user's
--    stats. Like visa_*, these are a SLOT (overwritten by a fix), not
--    history.
--
-- DDL ONLY. No existing row is read, copied or changed. IF NOT EXISTS so a
-- re-run is harmless.

ALTER TABLE application_artifacts ADD COLUMN IF NOT EXISTS ats_score INTEGER
    CHECK (ats_score IS NULL OR (ats_score >= 0 AND ats_score <= 100));
ALTER TABLE application_artifacts ADD COLUMN IF NOT EXISTS ats_notes TEXT;

ALTER TABLE application_contacts ADD COLUMN IF NOT EXISTS found_via TEXT;

ALTER TABLE applications ADD COLUMN IF NOT EXISTS job_country TEXT;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS job_remote BOOLEAN;
ALTER TABLE applications ADD COLUMN IF NOT EXISTS job_found_on TEXT;
