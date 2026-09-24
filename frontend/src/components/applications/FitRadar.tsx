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

const MAX_LABEL_LINE_CHARS = 18;

/**
 * Wrap an axis name into at most 2 lines, breaking only at spaces (never
 * mid-word). A single word longer than the limit stays whole on its own
 * line. Whatever doesn't fit on line 1 — however long — goes on line 2,
 * since there is no line 3.
 */
function wrapLabel(name: string, maxLineChars = MAX_LABEL_LINE_CHARS): string[] {
  const words = name.split(" ");
  let line1 = "";
  let splitAt = words.length;
  for (let i = 0; i < words.length; i++) {
    const candidate = line1 ? `${line1} ${words[i]}` : words[i];
    if (line1 && candidate.length > maxLineChars) {
      splitAt = i;
      break;
    }
    line1 = candidate;
  }
  const line2 = words.slice(splitAt).join(" ");
  return line2 ? [line1, line2] : [line1];
}

/** Tspan text per wrapped line — the first line carries a trailing space so
 * the concatenated tspan textContent reconstructs the exact original name
 * (space-joined words, one space between the two lines). */
function tspanTexts(lines: string[]): string[] {
  return lines.length === 1 ? lines : [`${lines[0]} `, lines[1]];
}

/** Per-tspan `dy` (relative vertical offset) for a wrapped label, given
 * where it sits: top labels grow downward (toward the chart is fine — the
 * padding is on the outside), bottom labels stack upward so they don't run
 * into the chart, and side labels are simply centred on the axis point. */
function lineDy(textAnchor: "start" | "middle" | "end", isTop: boolean, lineCount: number): string[] {
  if (lineCount === 1) return ["0"];
  if (textAnchor === "middle") {
    return isTop ? ["0", "1.2em"] : ["-1.2em", "1.2em"];
  }
  return ["-0.6em", "1.2em"];
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
  const n = axes.length;
  const fontSize = size * 0.036;

  // Labels hang outside the circle and can wrap to 2 lines; size the
  // viewBox from what's actually going to be drawn so a long axis name is
  // never clipped, on any side.
  const wrappedLabels = axes.map((axis) => wrapLabel(axis.name));
  const longestLineChars = Math.max(...wrappedLabels.flat().map((line) => line.length));
  const padX = Math.ceil(longestLineChars * fontSize * 0.62) + 8;
  // Extra room for the small "asks N · you N" value line under each label
  // (owner decision 3, 2026-09-24).
  const valueFontSize = fontSize * 0.8;
  const padY = fontSize * 1.4 + valueFontSize * 1.6;

  return (
    <figure data-testid="fit-radar" className="mt-4">
      <svg
        viewBox={`${-padX} ${-padY} ${size + 2 * padX} ${size + 2 * padY}`}
        className="mx-auto block h-auto w-full max-w-lg"
        role="img"
        aria-labelledby="fit-radar-title"
      >
        <title id="fit-radar-title">
          Fit picture: what the role asks and what you bring, on {n} lines your assistant chose
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
          const x = p.x.toFixed(1);
          const textAnchor = anchor(p.x, cx);
          const lines = wrappedLabels[i];
          const texts = tspanTexts(lines);
          const isTop = p.y < cy;
          const dys = lineDy(textAnchor, isTop, lines.length);
          // The last label line's baseline, so the value line below it never
          // overlaps a 2-line wrapped label (see lineDy's cumulative dy math).
          const lastLineExtra = isTop && lines.length === 2 ? fontSize * 1.2 : 0;
          const valueY = (p.y + lastLineExtra + fontSize * 1.15).toFixed(1);
          return (
            <g key={axis.name}>
              <text
                data-testid="fit-radar-axis"
                x={x}
                y={p.y.toFixed(1)}
                textAnchor={textAnchor}
                dominantBaseline="middle"
                fontSize={fontSize}
                className="fill-current text-foreground"
              >
                {texts.map((line, idx) => (
                  <tspan key={idx} x={x} dy={dys[idx]}>
                    {line}
                  </tspan>
                ))}
              </text>
              <text
                data-testid="fit-radar-axis-value"
                x={x}
                y={valueY}
                textAnchor={textAnchor}
                dominantBaseline="middle"
                fontSize={valueFontSize}
                className="fill-current text-muted-foreground"
              >
                {`asks ${axis.role} · you ${axis.you}`}
              </text>
            </g>
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
