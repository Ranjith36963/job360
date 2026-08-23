// ---------------------------------------------------------------------------
// Score colour bands — TWO scales need TWO band sets.
//
// `primaryScore` on a job card/detail page is one of two DIFFERENT scales:
//   - JUDGED (llm_fit_score): the AI judge's 0-100 fit score. Calibrated so
//     that 70+/40+ read as strong/moderate/weak — this band set is ALSO used
//     by the "AI: <verdict>" pill (JobCard.tsx ~:412-418, JobDetailClient.tsx
//     "AI verdict" text), so the big score badge and that pill must NEVER
//     disagree on colour for the same score. Do not change 70/40 without
//     updating the pill too.
//   - UNJUDGED (match_score): the keyword engine's recall score. This is NOT
//     calibrated to 0-100 the same way — measured live (2026-08-15, n=10,508
//     scored jobs): median 10, p90 42, max 80, and only 10 rows EVER cleared
//     70. Using the judged 70/40 thresholds here meant almost every keyword
//     score rendered red ("weak"), including good matches.
//
// Bands below for match_score (green >= 45, amber >= 20) were chosen from
// that live distribution: green roughly brackets p90+, amber the upper-mid
// spread, red the long tail below median. RE-CHECK these constants after the
// feed refills (F1/F5 land) — the distribution they're derived from will
// shift once stale scores are replaced by fresh ones.
// ---------------------------------------------------------------------------

/** Bands for `llm_fit_score` — the AI judge's calibrated 0-100 fit score. */
const JUDGED_GREEN = 70;
const JUDGED_AMBER = 40;

/** Bands for `match_score` — the keyword engine's uncalibrated recall score. */
const KEYWORD_GREEN = 45;
const KEYWORD_AMBER = 20;

/**
 * Colour class for a job's primary score badge.
 *
 * @param score the value being displayed (llm_fit_score when judged, else match_score)
 * @param isJudged whether `score` came from the AI judge (llm_fit_score) or
 *   the keyword engine (match_score) — selects which band set applies.
 */
export function scoreClass(score: number, isJudged: boolean): string {
  const [green, amber] = isJudged
    ? [JUDGED_GREEN, JUDGED_AMBER]
    : [KEYWORD_GREEN, KEYWORD_AMBER];
  if (score >= green) return "score-high"; // green — strong
  if (score >= amber) return "score-mid"; // amber — moderate
  return "score-low"; // red — weak
}
