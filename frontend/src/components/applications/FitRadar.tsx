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

/** Per-tspan `dy` in `em` (relative vertical offset) for a wrapped label,
 * given where it sits: top labels grow downward (toward the chart is fine —
 * the padding is on the outside), bottom labels stack upward so they don't
 * run into the chart, and side labels are simply centred on the axis point. */
function lineDy(textAnchor: "start" | "middle" | "end", isTop: boolean, lineCount: number): number[] {
  if (lineCount === 1) return [0];
  if (textAnchor === "middle") {
    return isTop ? [0, 1.2] : [-1.2, 1.2];
  }
  return [-0.6, 1.2];
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
  // Owner decision (2026-09-25): the radar numbers were unreadable (~9px
  // desktop, ~6px phone) — raise the base font factor and stop letting long
  // labels shrink the chart itself.
  const fontSize = size * 0.045;

  // Labels hang outside the circle and can wrap to 2 lines; size the
  // viewBox from what's actually going to be drawn so a long axis name is
  // never clipped, on any side — but cap how far a long label can push the
  // viewBox out, so a long name doesn't squeeze the chart down to fit inside
  // a fixed max-width container (owner decision 2026-09-25).
  const wrappedLabels = axes.map((axis) => wrapLabel(axis.name));
  const longestLineChars = Math.max(...wrappedLabels.flat().map((line) => line.length));
  const padX = Math.min(Math.ceil(longestLineChars * fontSize * 0.62) + 8, size * 0.28);
  // The value line under each label is now the SAME size as the label
  // (owner decision 2026-09-25 — it used to be 0.8x and unreadable).
  const valueFontSize = fontSize;
  // Gap between the name block and its value line, and the line-to-line
  // step for a wrapped 2-line name (matches the 1.2em used in the tspan
  // `dy` chain below) — both needed here so padY can be derived from the
  // actual worst-case stack instead of a flat guess.
  const valueGap = fontSize * 1.15;
  const lineStep = fontSize * 1.2;
  const textHalfHeight = fontSize * 0.6; // rough half-height of a text row
  const maxLabelLines = Math.max(...wrappedLabels.map((lines) => lines.length));
  // Top labels (owner fix 2026-09-25): the value line sits at labelR from
  // centre — nearest the chart — and the (up to 2) name lines stack UPWARD
  // above it, so nothing overlaps the polygon. Bottom labels are unchanged:
  // they grow downward from labelR by the value line's gap only (the
  // wrapped-line dy is balanced around the anchor, so line count doesn't
  // add extra reach there).
  const topReach = labelR + valueGap + (maxLabelLines - 1) * lineStep + textHalfHeight;
  const bottomReach = labelR + valueGap + textHalfHeight;
  const padY = Math.max(topReach - size / 2, bottomReach - size / 2, fontSize * 1.4 + valueFontSize * 1.6);

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
          const isTopMiddle = isTop && textAnchor === "middle";
          const dys = lineDy(textAnchor, isTop, lines.length);
          // Where the LAST label line's baseline lands relative to the text
          // block's own anchor. `dy` is cumulative in SVG, so sum the whole
          // chain rather than assuming one of lineDy's cases — side-anchored
          // labels net +0.6em, not +1.2em.
          const lastLineExtra = dys.reduce((total, dy) => total + dy, 0) * fontSize;
          // Top labels (owner fix 2026-09-25): the value line is the part of
          // the block closest to the chart, pinned at labelR from centre —
          // same spot the old single-anchor point used — and the name lines
          // stack UPWARD, away from the chart, above it. Bottom and side
          // labels are unchanged: the name block anchors at p.y and the value
          // line sits `dy`+gap below its last line.
          const textY = (isTopMiddle ? p.y - fontSize * 1.15 - lastLineExtra : p.y).toFixed(1);
          const valueY = (isTopMiddle ? p.y : p.y + lastLineExtra + fontSize * 1.15).toFixed(1);
          return (
            <g key={axis.name}>
              <text
                data-testid="fit-radar-axis"
                x={x}
                y={textY}
                textAnchor={textAnchor}
                dominantBaseline="middle"
                fontSize={fontSize}
                className="fill-current text-foreground"
              >
                {texts.map((line, idx) => (
                  <tspan key={idx} x={x} dy={`${dys[idx]}em`}>
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
      {/* Visible readable table — the on-chart numbers are small even at the
       * enlarged size, so this is the number everyone actually reads (owner
       * decision 2026-09-25). Compact enough for a 360px phone: small text,
       * tight padding, no horizontal scroll. */}
      <table data-testid="fit-radar-table" className="mx-auto mt-3 w-full max-w-lg border-collapse text-xs">
        <thead>
          <tr className="border-b border-border text-left text-muted-foreground">
            <th scope="col" className="py-1 pr-2 font-medium">
              Line
            </th>
            <th scope="col" className="px-1 py-1 text-right font-medium">
              The role asks
            </th>
            <th scope="col" className="py-1 pl-1 text-right font-medium">
              You bring
            </th>
          </tr>
        </thead>
        <tbody>
          {axes.map((axis) => (
            <tr key={axis.name} data-testid="fit-radar-row" className="border-b border-border/50 last:border-0">
              <td className="py-1 pr-2">{axis.name}</td>
              <td className="px-1 py-1 text-right tabular-nums">{axis.role}</td>
              <td className="py-1 pl-1 text-right tabular-nums">{axis.you}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
