"use client";

import { useEffect, useState } from "react";
import { getAlignment, type Alignment } from "@/lib/api";
import { FitRadar } from "./FitRadar";

// Past this many pills, "Not in the ad" reads as a wall rather than a list —
// fold the rest behind "Show all N" so a large profile doesn't dominate the
// Fit section.
const SKILLS_FOLD_LIMIT = 12;

/** Colour for the fit-score bar fill — thresholds match the rest of the app's
 * "verdict" language (nothing here computes a score; it only paints one
 * that's already stored). */
function barColor(score: number): string {
  if (score >= 70) return "bg-emerald-500";
  if (score >= 40) return "bg-amber-500";
  return "bg-red-500";
}

/**
 * The fit picture — STORED, never computed by Job360 (VISION rule 4). Shows
 * the agent's saved fit verdict (same data `FitPanel` shows) alongside which
 * of the user's own profile skills occur in the ad text. Job360 draws what
 * it stores; it does not judge.
 */
export function AlignmentPanel({
  applicationId,
  refreshKey,
}: {
  applicationId: number;
  refreshKey?: string | number | null;
}) {
  const [data, setData] = useState<Alignment | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showAllSkills, setShowAllSkills] = useState(false);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    setShowAllSkills(false);
    (async () => {
      try {
        const res = await getAlignment(applicationId);
        if (cancelled) return;
        setData(res);
      } catch {
        if (cancelled) return;
        setError("Could not load the fit picture.");
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [applicationId, refreshKey]);

  if (loading) {
    return <p className="text-sm text-muted-foreground">Loading fit…</p>;
  }
  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!data) {
    return null;
  }

  const { fit, skills_in_ad, skills_not_in_ad, skills_total, ad_chars } = data;

  return (
    <div className="glass-card rounded-xl p-4">
      <div className="flex items-center justify-between gap-3">
        <span className="text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Fit
        </span>
        {fit?.score != null ? (
          <div className="flex flex-1 items-center gap-3">
            <div
              data-testid="fit-score-bar"
              className="h-2 flex-1 rounded-full bg-muted"
            >
              <div
                className={`h-2 rounded-full ${barColor(fit.score)}`}
                style={{ width: `${fit.score}%` }}
              />
            </div>
            <span className="shrink-0 text-sm font-semibold">{fit.score}/100</span>
          </div>
        ) : null}
      </div>

      {!fit && (
        <p className="mt-2 text-sm text-muted-foreground">
          No fit judgement yet — your agent saves one with save_fit.
        </p>
      )}

      {fit && (
        <>
          <p className="mt-2 font-semibold">{fit.verdict ?? "No verdict text"}</p>
          {fit.gaps.length > 0 && (
            <ul className="mt-2 flex flex-wrap gap-1.5">
              {fit.gaps.map((gap) => (
                <li
                  key={gap}
                  data-testid="fit-gap"
                  className="rounded-full bg-destructive/10 px-2.5 py-0.5 text-xs text-destructive"
                >
                  {gap}
                </li>
              ))}
            </ul>
          )}
          {fit.axes.length >= 3 && (
            <>
              <FitRadar axes={fit.axes} />
              <p className="mt-1 text-center text-xs text-muted-foreground/70">
                The lines are your agent&apos;s own choice for this job.
              </p>
            </>
          )}
        </>
      )}

      <div className="mt-4">
        {skills_total === 0 ? (
          <p className="text-sm text-muted-foreground">No skills on your profile yet.</p>
        ) : ad_chars === 0 ? (
          <p className="text-sm text-muted-foreground">
            This application has no ad text to compare against.
          </p>
        ) : (
          <>
            <p data-testid="skills-summary" className="text-sm text-muted-foreground">
              {skills_in_ad.length} of {skills_total} of your skills appear in this ad
            </p>
            <div className="mt-2 grid grid-cols-1 gap-4 md:grid-cols-2">
              <div data-testid="skills-in-ad">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  In the ad
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {skills_in_ad.map((skill) => (
                    <li
                      key={skill}
                      className="rounded-full bg-emerald-500/10 px-2.5 py-0.5 text-xs text-emerald-600 dark:text-emerald-400"
                    >
                      {skill}
                    </li>
                  ))}
                </ul>
              </div>
              <div data-testid="skills-not-in-ad">
                <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
                  Not in the ad
                </p>
                <ul className="flex flex-wrap gap-1.5">
                  {(showAllSkills ? skills_not_in_ad : skills_not_in_ad.slice(0, SKILLS_FOLD_LIMIT)).map(
                    (skill) => (
                      <li
                        key={skill}
                        className="rounded-full bg-muted px-2.5 py-0.5 text-xs text-muted-foreground"
                      >
                        {skill}
                      </li>
                    )
                  )}
                </ul>
                {skills_not_in_ad.length > SKILLS_FOLD_LIMIT && (
                  <button
                    type="button"
                    data-testid="skills-show-all"
                    onClick={() => setShowAllSkills((prev) => !prev)}
                    className="mt-1.5 text-xs font-medium text-primary hover:underline"
                  >
                    {showAllSkills ? "Show fewer" : `Show all ${skills_not_in_ad.length}`}
                  </button>
                )}
              </div>
            </div>
          </>
        )}
      </div>

      <p className="mt-4 text-xs text-muted-foreground/70">
        Job360 draws what it stores: your agent&apos;s verdict and your own skills found
        in the ad. It does not judge.
      </p>
    </div>
  );
}
