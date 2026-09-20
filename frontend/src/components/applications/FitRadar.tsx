import type { ApplicationFit } from "@/lib/api";

type FitAxis = ApplicationFit["axes"][number];

/** Colours for the two shapes — the role in blue, the seeker in green.
 * Plain values (not theme tokens) so the chart reads the same in both
 * themes and in a screenshot. */
const ROLE_STROKE = "#38bdf8"; // sky-400
const ROLE_FILL = "rgba(56, 189, 248, 0.18)";
const YOU_STROKE = "#34d399"; // emerald-400
const YOU_FILL = "rgba(52, 211, 153, 0.28)";
const GRID = "rgba(148, 163, 184, 0.35)"; // slate-400

const RINGS = [25, 50, 75, 100];

/** Point on axis `i` of `n` at `value` (0..100), 12 o'clock first, clockwise. */
function point(i: number, n: number, value: number, cx: number, cy: number, r: number) {
  const angle = -Math.PI / 2 + (2 * Math.PI * i) / n;
  const d = (r * Math.max(0, Math.min(100, value))) / 100;
  return { x: cx + d * Math.cos(angle), y: cy + d * Math.sin(angle) };
}

function polygon(values: number[], cx: number, cy: number, r: number): string {
  const n = values.length;
  return values
    .map((v, i) => {
      const p = point(i, n, v, cx, cy, r);
      return `${p.x.toFixed(1)},${p.y.toFixed(1)}`;
    })
    .join(" ");
}

/** Where a label sits relative to its axis end, from the axis direction. */
function anchor(x: number, cx: number): "start" | "middle" | "end" {
  if (x - cx > 6) return "start";
  if (cx - x > 6) return "end";
  return "middle";
}

/**
 * The fit picture as a radar (owner ask, 2026-09-20 — "this kind of view").
 * Two shapes over the agent's OWN axes: how much the role asks on each line
 * (blue) and how much the seeker brings (green). Nothing here computes a
 * number; the agent named the axes and put the values on them through
 * `save_fit`, and Job360 draws exactly that (VISION rule 4). Pure SVG, no
 * chart library; every label is a text node.
 */
export function FitRadar({ axes, size = 320 }: { axes: FitAxis[]; size?: number }) {
  if (axes.length < 3) return null;

  const cx = size / 2;
  const cy = size / 2;
  const r = size * 0.32;
  const labelR = r + size * 0.06;
  // Side labels hang outside the circle; give the viewBox room on both
  // sides so a long axis name is never clipped.
  const pad = size * 0.3;
  const n = axes.length;

  return (
    <figure data-testid="fit-radar" className="mt-4">
      <svg
        viewBox={`${-pad} 0 ${size + 2 * pad} ${size}`}
        className="mx-auto block h-auto w-full max-w-md"
        role="img"
        aria-labelledby="fit-radar-title"
      >
        <title id="fit-radar-title">
          Fit picture: what the role asks and what you bring, on {n} lines your agent chose
        </title>
        {RINGS.map((ring) => (
          <polygon
            key={ring}
            points={polygon(axes.map(() => ring), cx, cy, r)}
            fill="none"
            stroke={GRID}
            strokeWidth={ring === 100 ? 1.2 : 0.8}
          />
        ))}
        {axes.map((axis, i) => {
          const end = point(i, n, 100, cx, cy, r);
          return (
            <line
              key={axis.name}
              x1={cx}
              y1={cy}
              x2={end.x.toFixed(1)}
              y2={end.y.toFixed(1)}
              stroke={GRID}
              strokeWidth={0.8}
            />
          );
        })}
        <polygon
          data-testid="fit-radar-role"
          points={polygon(
            axes.map((a) => a.role),
            cx,
            cy,
            r
          )}
          fill={ROLE_FILL}
          stroke={ROLE_STROKE}
          strokeWidth={1.6}
          strokeLinejoin="round"
        />
        <polygon
          data-testid="fit-radar-you"
          points={polygon(
            axes.map((a) => a.you),
            cx,
            cy,
            r
          )}
          fill={YOU_FILL}
          stroke={YOU_STROKE}
          strokeWidth={1.6}
          strokeLinejoin="round"
        />
        {axes.map((axis, i) => {
          const p = point(i, n, 100, cx, cy, labelR);
          return (
            <text
              key={axis.name}
              data-testid="fit-radar-axis"
              x={p.x.toFixed(1)}
              y={p.y.toFixed(1)}
              textAnchor={anchor(p.x, cx)}
              dominantBaseline="middle"
              fontSize={size * 0.036}
              className="fill-current text-foreground"
            >
              {axis.name}
            </text>
          );
        })}
      </svg>
      <figcaption className="mt-1 flex flex-wrap items-center justify-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: ROLE_STROKE }}
          />
          The role asks
        </span>
        <span className="inline-flex items-center gap-1.5">
          <span
            aria-hidden="true"
            className="inline-block h-2.5 w-2.5 rounded-sm"
            style={{ backgroundColor: YOU_STROKE }}
          />
          You bring
        </span>
      </figcaption>
      <ul className="sr-only">
        {axes.map((axis) => (
          <li key={axis.name} data-testid="fit-radar-row">
            {axis.name}: the role asks {axis.role} of 100, you bring {axis.you} of 100
          </li>
        ))}
      </ul>
    </figure>
  );
}
