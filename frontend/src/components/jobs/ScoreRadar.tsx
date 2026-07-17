"use client";

import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  Tooltip,
} from "recharts";

// ---------------------------------------------------------------------------
// 8D Score Radar — the hero element that makes Job360 distinctive
//
// Props use JobResponse field names verbatim so callers can spread `job`
// directly without renaming (prevents the silent-zero footgun).
//
// These are the EIGHT dimensions the engine ACTUALLY computes, at the weights
// it actually uses. Until now this chart drew a different, invented 8 whose
// maxes summed to a tidy 100 (role 15 / skill 20 / experience 10 /
// credentials 5 / semantic 20 …) — a mockup that predated the engine and was
// never reconciled with it. Consequences that shipped to users:
//   * role's real weight is TITLE_WEIGHT=40, so a perfect title match plotted
//     at 267% and spiked outside the grid;
//   * experience / credentials / semantic were never computed by anything, so
//     three axes were permanently 0 (exactly the trap rule #21 warns about);
//   * salary / visa / workplace WERE computed every request and silently
//     discarded — real signal the user never saw.
// The fix points the chart at the engine instead of inventing scoring to
// justify the chart. Weights: skill_matcher.TITLE/SKILL/LOCATION/RECENCY_WEIGHT
// (40/40/10/10) + settings.{SENIORITY,SALARY,VISA,WORKPLACE}_WEIGHT (8/10/6/6).
//
// NEVER derive the total by summing these axes: the raw max is 130 clamped to
// 100 (rule #27) and the title/location penalties (−30/−15) live on no axis.
// `match_score` from the API is the only truth for the total.
// ---------------------------------------------------------------------------

export interface ScoreRadarScores {
  role: number;
  skill: number;
  location_score: number;
  recency: number;
  seniority_score: number;
  salary_score: number;
  visa_score: number;
  workplace_score: number;
}

interface ScoreRadarProps {
  scores: Partial<ScoreRadarScores>;
  /**
   * Did the enrichment-driven dims (seniority/salary/visa/workplace) actually
   * run for this job? Comes from the API — NEVER inferred from zeros, because
   * 0 is ambiguous: it can be "not measured" OR a real earned 0 (visa_score=0
   * when you need sponsorship and the job offers none). When false, those four
   * axes render dormant instead of drawing a truthful-looking 0%.
   */
  dimsActive?: boolean;
  size?: number;
}

const DIMENSIONS: {
  key: keyof ScoreRadarScores;
  label: string;
  max: number;
  description: string;
  /** Populated only when enrichment ran for this job (rule #20). */
  enrichment?: boolean;
}[] = [
  { key: "role", label: "Role", max: 40, description: "Title match against your target job titles" },
  { key: "skill", label: "Skill", max: 40, description: "Primary + secondary + tertiary skill overlap" },
  { key: "location_score", label: "Location", max: 10, description: "Location / remote preference match" },
  { key: "recency", label: "Recency", max: 10, description: "How recently the job was posted" },
  { key: "seniority_score", label: "Seniority", max: 8, description: "Seniority alignment — can go negative when the level is wrong for you", enrichment: true },
  { key: "salary_score", label: "Salary", max: 10, description: "Overlap between the job's salary band and your target range", enrichment: true },
  { key: "visa_score", label: "Visa", max: 6, description: "Visa sponsorship vs your requirement", enrichment: true },
  { key: "workplace_score", label: "Workplace", max: 6, description: "Remote / hybrid / on-site vs your preference", enrichment: true },
];

function pctClass(pct: number): string {
  if (pct >= 80) return "text-score-high";
  if (pct >= 60) return "text-score-good";
  if (pct >= 40) return "text-score-mid";
  return "text-score-low";
}

interface RadarDataPoint {
  dimension: string;
  value: number;
  raw: number;
  max: number;
  dormant: boolean;
}

interface CustomTooltipPayload {
  payload?: Array<{ payload: RadarDataPoint }>;
}

function CustomTooltip({ payload }: CustomTooltipPayload) {
  if (!payload?.length) return null;
  const { dimension, value, raw, max, dormant } = payload[0].payload;
  return (
    <div className="bg-background/95 backdrop-blur border border-border rounded-lg px-3 py-2 text-xs shadow-lg">
      <div className="font-semibold text-foreground">{dimension}</div>
      {dormant ? (
        // Say "not measured" rather than "0/10 (0%)" — the latter reads as
        // "you scored nothing", which is a different claim entirely.
        <div className="text-muted-foreground">Not measured for this job</div>
      ) : (
        <div className="text-muted-foreground">
          {raw}/{max} pts ({value}%)
        </div>
      )}
    </div>
  );
}

export function ScoreRadar({ scores, dimsActive = true, size = 300 }: ScoreRadarProps) {
  const data = DIMENSIONS.map((d) => {
    const raw = scores[d.key] ?? 0;
    // An enrichment dim with no enrichment row is NOT a zero score — it was
    // never measured. Mark it dormant so we can say so, instead of plotting a
    // 0% that looks like a judgement (rule #21: a always-0 dim is a lie).
    const dormant = Boolean(d.enrichment) && !dimsActive;
    // Clamp the PLOTTED percentage to [0, 100]. Two real ways raw escapes
    // 0..max: `seniority_score` is legitimately NEGATIVE on a bad seniority
    // match (a penalty, down to -SENIORITY_WEIGHT=-8) — unclamped that inverts
    // the polygon through the centre; and a raw value can exceed `d.max` if a
    // backend weight is env-retuned above the max hardcoded here.
    // NOTE: we clamp the DRAWING only. `raw` stays verbatim in the tooltip and
    // aria-label, so the number we report is never a lie — just the geometry.
    const value = dormant
      ? 0
      : Math.max(0, Math.min(100, Math.round((raw / d.max) * 100)));
    return {
      dimension: d.label,
      value,
      raw,
      max: d.max,
      dormant,
    };
  });

  // Average of the axes we actually MEASURED. Dormant axes are excluded rather
  // than averaged as 0 — otherwise "enrichment didn't run" would drag the
  // headline number down as if the job had scored badly.
  // This is an average of the drawn axes, NOT the job's score: the axes don't
  // sum to match_score (130 clamped to 100, penalties on no axis). The real
  // total is rendered next to this chart from `match_score`.
  const measured = data.filter((d) => !d.dormant);
  const totalPct = measured.length
    ? Math.round(measured.reduce((sum, d) => sum + d.value, 0) / measured.length)
    : 0;
  const ariaLabel = `8-dimension score radar. Average ${totalPct}% across measured dimensions. ${data
    .map((d) =>
      d.dormant
        ? `${d.dimension}: not measured`
        : `${d.dimension}: ${d.raw} out of ${d.max}`
    )
    .join(", ")}.`;

  return (
    <div className="flex flex-col items-center gap-6">
      {/* Radar chart with ambient glow */}
      <div
        className="rounded-2xl"
        role="img"
        aria-label={ariaLabel}
        style={{
          width: size,
          height: size,
          boxShadow: "0 0 40px oklch(0.89 0.29 128 / 0.07)",
        }}
      >
        {/* Fixed numeric size (no ResponsiveContainer): the chart never measures
            a "100%" container, so Recharts can't log the width/height(-1) warning
            on first paint. The outer div is already exactly size×size. */}
        <RadarChart width={size} height={size} cx="50%" cy="50%" outerRadius="65%" data={data}>
          <PolarGrid stroke="oklch(1 0 0 / 0.08)" />
          <PolarAngleAxis
            dataKey="dimension"
            tick={{
              fill: "white",
              fontSize: 11,
              fontFamily: "var(--font-sora)",
            }}
          />
          <PolarRadiusAxis tick={false} axisLine={false} domain={[0, 100]} />
          <Radar
            name="Score"
            dataKey="value"
            fill="oklch(0.89 0.29 128 / 0.2)"
            stroke="oklch(0.89 0.29 128)"
            strokeWidth={2}
            dot={false}
          />
          <Tooltip content={<CustomTooltip />} />
        </RadarChart>
      </div>

      {/* Raw score grid */}
      <div
        className="grid grid-cols-2 gap-x-6 gap-y-2 w-full max-w-xs"
        aria-hidden="true"
      >
        {DIMENSIONS.map((d) => {
          const raw = scores[d.key] ?? 0;
          const dormant = Boolean(d.enrichment) && !dimsActive;
          const pct = Math.round((raw / d.max) * 100);
          return (
            <div
              key={d.key}
              className={`flex items-center justify-between ${dormant ? "opacity-40" : ""}`}
              title={dormant ? `${d.description} — not measured for this job` : d.description}
            >
              <span className="text-xs text-muted-foreground">{d.label}</span>
              {dormant ? (
                <span className="font-mono text-xs tabular-nums text-muted-foreground">
                  —
                </span>
              ) : (
                <span
                  className={`font-mono text-xs font-semibold tabular-nums ${pctClass(pct)}`}
                >
                  {raw}/{d.max}
                </span>
              )}
            </div>
          );
        })}
      </div>

      {!dimsActive && (
        <p className="text-xs text-muted-foreground text-center max-w-xs">
          Seniority, salary, visa and workplace aren&apos;t measured for this job yet —
          they need enrichment to have run.
        </p>
      )}
    </div>
  );
}
