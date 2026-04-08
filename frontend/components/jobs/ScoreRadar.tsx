"use client";

import {
  RadarChart,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  Radar,
  ResponsiveContainer,
} from "recharts";

// ---------------------------------------------------------------------------
// 8D Score Radar — the hero element that makes Job360 distinctive
// ---------------------------------------------------------------------------

interface ScoreRadarProps {
  scores: {
    role: number;
    skill: number;
    seniority: number;
    experience: number;
    credentials: number;
    location: number;
    recency: number;
    semantic: number;
  };
  size?: number;
}

const DIMENSIONS: {
  key: keyof ScoreRadarProps["scores"];
  label: string;
  max: number;
}[] = [
  { key: "role", label: "Role", max: 15 },
  { key: "skill", label: "Skill", max: 20 },
  { key: "seniority", label: "Seniority", max: 10 },
  { key: "experience", label: "Experience", max: 10 },
  { key: "credentials", label: "Credentials", max: 5 },
  { key: "location", label: "Location", max: 10 },
  { key: "recency", label: "Recency", max: 10 },
  { key: "semantic", label: "Semantic", max: 20 },
];

function pctClass(pct: number): string {
  if (pct >= 80) return "text-score-high";
  if (pct >= 60) return "text-score-good";
  if (pct >= 40) return "text-score-mid";
  return "text-score-low";
}

export function ScoreRadar({ scores, size = 300 }: ScoreRadarProps) {
  const data = DIMENSIONS.map((d) => ({
    dimension: d.label,
    value: Math.round((scores[d.key] / d.max) * 100),
  }));

  return (
    <div className="flex flex-col items-center gap-6">
      {/* Radar chart with ambient glow */}
      <div
        className="rounded-2xl"
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
          </RadarChart>
        </ResponsiveContainer>
      </div>

      {/* Raw score grid */}
      <div className="grid grid-cols-2 gap-x-6 gap-y-2 w-full max-w-xs">
        {DIMENSIONS.map((d) => {
          const raw = scores[d.key];
          const pct = Math.round((raw / d.max) * 100);
          return (
            <div key={d.key} className="flex items-center justify-between">
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
