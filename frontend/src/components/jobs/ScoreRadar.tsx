"use client";

import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
  Tooltip,
} from "recharts";

// ---------------------------------------------------------------------------
// 8D Score Radar — the hero element that makes Job360 distinctive
//
// Props use JobResponse field names verbatim so callers can spread `job`
// directly without renaming (prevents the silent-zero footgun).
// ---------------------------------------------------------------------------

export interface ScoreRadarScores {
  role: number;
  skill: number;
  seniority_score: number;
  experience: number;
  credentials: number;
  location_score: number;
  recency: number;
  semantic: number;
}

interface ScoreRadarProps {
  scores: Partial<ScoreRadarScores>;
  size?: number;
}

const DIMENSIONS: {
  key: keyof ScoreRadarScores;
  label: string;
  max: number;
  description: string;
}[] = [
  { key: "role", label: "Role", max: 15, description: "Title match against target job titles" },
  { key: "skill", label: "Skill", max: 20, description: "Primary + secondary + tertiary skill overlap" },
  { key: "seniority_score", label: "Seniority", max: 10, description: "Experience level alignment" },
  { key: "experience", label: "Experience", max: 10, description: "Years of experience match" },
  { key: "credentials", label: "Credentials", max: 5, description: "Education and certification match" },
  { key: "location_score", label: "Location", max: 10, description: "Location / remote preference match" },
  { key: "recency", label: "Recency", max: 10, description: "How recently the job was posted" },
  { key: "semantic", label: "Semantic", max: 20, description: "Embedding-based semantic similarity" },
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
}

interface CustomTooltipPayload {
  payload?: Array<{ payload: RadarDataPoint }>;
}

function CustomTooltip({ payload }: CustomTooltipPayload) {
  if (!payload?.length) return null;
  const { dimension, value, raw, max } = payload[0].payload;
  return (
    <div className="bg-background/95 backdrop-blur border border-border rounded-lg px-3 py-2 text-xs shadow-lg">
      <div className="font-semibold text-foreground">{dimension}</div>
      <div className="text-muted-foreground">
        {raw}/{max} pts ({value}%)
      </div>
    </div>
  );
}

export function ScoreRadar({ scores, size = 300 }: ScoreRadarProps) {
  const data = DIMENSIONS.map((d) => {
    const raw = scores[d.key] ?? 0;
    return {
      dimension: d.label,
      value: Math.round((raw / d.max) * 100),
      raw,
      max: d.max,
    };
  });

  const totalPct = Math.round(data.reduce((sum, d) => sum + d.value, 0) / data.length);
  const ariaLabel = `8-dimension score radar. Average ${totalPct}%. ${DIMENSIONS.map((d) => `${d.label}: ${scores[d.key] ?? 0} out of ${d.max}`).join(", ")}.`;

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
        <ResponsiveContainer width="100%" height="100%">
          <RadarChart cx="50%" cy="50%" outerRadius="65%" data={data}>
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
        </ResponsiveContainer>
      </div>

      {/* Raw score grid */}
      <div
        className="grid grid-cols-2 gap-x-6 gap-y-2 w-full max-w-xs"
        aria-hidden="true"
      >
        {DIMENSIONS.map((d) => {
          const raw = scores[d.key] ?? 0;
          const pct = Math.round((raw / d.max) * 100);
          return (
            <div
              key={d.key}
              className="flex items-center justify-between"
              title={d.description}
            >
              <span className="text-xs text-muted-foreground">{d.label}</span>
              <span
                className={`font-mono text-xs font-semibold tabular-nums ${pctClass(pct)}`}
              >
                {raw}/{d.max}
              </span>
            </div>
          );
        })}
      </div>
    </div>
  );
}
