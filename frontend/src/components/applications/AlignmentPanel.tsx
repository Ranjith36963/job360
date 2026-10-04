"use client";

import { useEffect, useState } from "react";
import { getAlignment, type Alignment } from "@/lib/api";
import { FitRadar } from "./FitRadar";

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";

/** Colour for the fit-score bar fill — thresholds match the rest of the app's
 * "verdict" language (nothing here computes a score; it only paints one
 * that's already stored). */
function barColor(score: number): string {
  if (score >= 70) return "bg-success";
  if (score >= 40) return "bg-warning";
  return "bg-danger";
}

/**
 * The fit picture — STORED, never computed by Job360 (VISION rule 4). Shows
 * the agent's saved fit verdict alongside which
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

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
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

  const { fit, skills_in_ad, skills_total, ad_chars } = data;

  // Owner decision 3 (2026-09-24): when there is no fit, the whole panel is
  // one line pointing at the assistant — no score, no gaps, no skills.
  if (!fit) {
    return (
      <div>
        <p className="font-heading text-[15px] text-muted-foreground">
          No fit yet — ask your assistant to judge this job.
        </p>
      </div>
    );
  }

  return (
    <div>
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-heading text-lg leading-snug">
          {fit.verdict ?? "No verdict text"}
        </p>
        {fit.score != null && (
          <span className="shrink-0 font-mono text-xs tabular-nums text-faint">
            {fit.score}/100
          </span>
        )}
      </div>
      {fit.score != null && (
        <div
          data-testid="fit-score-bar"
          className="mt-2.5 h-0.5 w-full bg-border"
        >
          <div
            className={`h-0.5 ${barColor(fit.score)}`}
            style={{ width: `${fit.score}%` }}
          />
        </div>
      )}

      {fit.gaps.length > 0 && (
        <div className="mt-4">
          <p className={`${LABEL} mb-1.5`}>
            What the job asks for that you lack — from your assistant
          </p>
          <ul className="flex flex-col">
            {fit.gaps.map((gap) => (
              <li
                key={gap}
                data-testid="fit-gap"
                className="border-t border-border py-1.5 text-[13px] text-muted-foreground first:border-t-0"
              >
                {gap}
              </li>
            ))}
          </ul>
        </div>
      )}
      {fit.axes.length >= 3 && (
        <>
          <FitRadar axes={fit.axes} />
          <p className="mt-1 text-center font-mono text-[11px] text-faint">
            The lines are your assistant&apos;s own choice for this job.
          </p>
        </>
      )}

      <div className="mt-5">
        {skills_total === 0 ? (
          <p className="text-sm text-muted-foreground">No skills on your profile yet.</p>
        ) : ad_chars === 0 ? (
          <p className="text-sm text-muted-foreground">
            This application has no ad text to compare against.
          </p>
        ) : (
          <>
            <p data-testid="skills-summary" className={LABEL}>
              This ad mentions {skills_in_ad.length} of your skills
            </p>
            {skills_in_ad.length > 0 && (
              <ul data-testid="skills-in-ad" className="mt-2 flex flex-wrap gap-1.5">
                {skills_in_ad.map((skill) => (
                  <li
                    key={skill}
                    className="rounded-full border border-border px-2.5 py-0.5 text-xs text-muted-foreground"
                  >
                    {skill}
                  </li>
                ))}
              </ul>
            )}
          </>
        )}
      </div>

      <p className="mt-5 text-xs text-faint">
        Job360 draws what it stores: your assistant&apos;s verdict and your own skills found
        in the ad. It does not judge.
      </p>
    </div>
  );
}
