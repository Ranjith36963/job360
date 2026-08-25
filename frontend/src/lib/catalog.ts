/**
 * Single source of truth for catalog counts shown to users.
 *
 * WHY THIS FILE EXISTS
 *
 * The landing page advertised "47 Job Sources" in five separate places. Six
 * sources were pruned on 2026-08-17 and the registry dropped to 41, but the
 * copy never moved — so every visitor to job360.uk was told a number that was
 * wrong by six for a week.
 *
 * There WAS a regression test. `landing-sources-count.test.tsx` said in its own
 * docstring that it guarded "the live SOURCE_REGISTRY size", then hardcoded
 * `47` and only asserted the page did not say `46`. When the real count became
 * 41 the test passed, and its passing is precisely what kept the page lying.
 * A test that names a literal freezes that literal.
 *
 * So the number lives here, once. The page reads it. The test asserts every
 * rendered count matches THIS constant rather than naming a number of its own.
 * `scripts/doc_sync_check.py` (guard `landing-source-count`) is what ties this
 * constant back to `SOURCE_REGISTRY` in the backend and goes red when they
 * diverge — that guard is mutation-tested, so it is known to be able to fail.
 *
 * To change it: update `SOURCE_COUNT` and nothing else. If it disagrees with
 * the backend registry, CI fails with the two numbers side by side.
 */

/** Registry keys in `backend/src/main.py` `SOURCE_REGISTRY`. Verified 2026-08-24. */
export const SOURCE_COUNT = 41;

/** Scoring dimensions surfaced in the UI (the 8-axis radar on job detail). */
export const SCORING_DIMENSIONS = 8;
