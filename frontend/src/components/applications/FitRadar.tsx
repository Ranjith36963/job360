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

/** Which half of the chart a label's axis point falls in, vertically —
 * decides which way its block (name lines + value line) has to grow to
 * stay clear of the ring. This is independent of the label's horizontal
 * `anchor` (start/middle/end): an axis near 1 or 5 o'clock is still "top"
 * or "bottom" even though its text isn't centred over the axis. */
function verticalPosition(y: number, cy: number): "top" | "bottom" | "middle" {
  if (cy - y > 6) return "top";
  if (y - cy > 6) return "bottom";
  return "middle";
}

const MAX_LABEL_LINE_CHARS = 18;

/** Rough advance width of one character, in `em`. Only used to size the
 * viewBox so no label is clipped; exported so the anti-clipping test can
 * check the real requirement against the same model the chart draws with. */
export const LABEL_CHAR_WIDTH_EM = 0.62;

/** The "asks N · you N" line drawn under an axis label. */
function valueText(axis: FitAxis): string {
  return `asks ${axis.role} · you ${axis.you}`;
}

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

/** Per-tspan `dy` in `em` (relative vertical offset) for a wrapped label, in
 * its own natural reading order — name line 1, then (if wrapped) line 2.
 * Top and bottom labels both grow straight down from their own text
 * anchor (`[0, 1.2]`); the caller (below, where `textY`/`valueY` are
 * computed) places that anchor so the block as a whole ends up clear of
 * the ring in the right direction. Side labels (vertically centred on the
 * axis point) keep the old centred wrap, since they never approach the
 * ring vertically no matter which way they grow. */
function lineDy(vPos: "top" | "bottom" | "middle", lineCount: number): number[] {
  if (lineCount === 1) return [0];
  return vPos === "middle" ? [-0.6, 1.2] : [0, 1.2];
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

  // The value line under each label is now the SAME size as the label
  // (owner decision 2026-09-25 — it used to be 0.8x and unreadable).
  const valueFontSize = fontSize;

  // Labels (and their value lines) hang outside the circle and can wrap to 2
  // lines; size the viewBox from what's actually going to be drawn so a long
  // axis name is never clipped, on any side. Pad by how far each label really
  // overhangs its own edge, per axis: a flat "longest line" pad assumes the
  // whole line sticks out past both edges, which is far more than any label
  // needs and squeezes the chart down inside a fixed max-width container
  // (owner decision 2026-09-25 — the chart itself must stay large).
  const wrappedLabels = axes.map((axis) => wrapLabel(axis.name));
  const labelOverhang = axes.map((axis, i) => {
    const p = point(i, n, 100, cx, cy, labelR);
    const textAnchor = anchor(p.x, cx);
    const width = Math.max(
      Math.max(...wrappedLabels[i].map((line) => line.length)) * fontSize * LABEL_CHAR_WIDTH_EM,
      valueText(axis).length * valueFontSize * LABEL_CHAR_WIDTH_EM
    );
    // Where the text box starts, given the anchor the label is drawn with.
    const left = textAnchor === "start" ? p.x : textAnchor === "end" ? p.x - width : p.x - width / 2;
    return Math.max(-left, left + width - size);
  });
  // padX applies to both sides, so take the worst overhang of either edge.
  const padX = Math.max(0, Math.ceil(Math.max(...labelOverhang))) + 8;
  // Gap between the name block and its value line, and the line-to-line
  // step for a wrapped 2-line name (matches the 1.2em used in the tspan
  // `dy` chain below) — both needed here so padY can be derived from the
  // actual worst-case stack instead of a flat guess.
  const valueGap = fontSize * 1.15;
  const lineStep = fontSize * 1.2;
  const textHalfHeight = fontSize * 0.6; // rough half-height of a text row
  const maxLabelLines = Math.max(...wrappedLabels.map((lines) => lines.length));
  // EVERY top or bottom label (owner fix 2026-09-25, generalised after CI
  // caught the first pass only covering the 12-o'clock axis — see
  // `verticalPosition` and the per-axis `textY`/`valueY` below) grows AWAY
  // from the ring along its own axis: the part of its block nearest the
  // centre — the value line for a top label, the name's first line for a
  // bottom one — sits pinned at labelR, and the rest of the (up to 2 name
  // lines + 1 value line) block extends outward from there. Top and bottom
  // are the same worst-case reach either way, so one number covers both.
  const blockReach = labelR + valueGap + (maxLabelLines - 1) * lineStep + textHalfHeight;
  const padY = Math.max(blockReach - size / 2, fontSize * 1.4 + valueFontSize * 1.6);

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
          // Vertical placement is driven by where the axis point actually
          // sits (top/bottom/middle of the chart), NOT by the horizontal
          // anchor — an axis at 1 or 5 o'clock is still "top", and its label
          // must grow away from the ring exactly like the 12-o'clock one
          // (owner fix 2026-09-25, generalised after CI found the first pass
          // only handled `textAnchor === "middle"`).
          const vPos = verticalPosition(p.y, cy);
          const isTop = vPos === "top";
          const dys = lineDy(vPos, lines.length);
          // Where the LAST label line's baseline lands relative to the text
          // block's own anchor. `dy` is cumulative in SVG, so sum the whole
          // chain rather than assuming one of lineDy's cases — side-anchored
          // labels net +0.6em, not +1.2em.
          const lastLineExtra = dys.reduce((total, dy) => total + dy, 0) * fontSize;
          // The part of the block nearest the ring is pinned at labelR — the
          // same point the old single-line anchor used — and the rest grows
          // AWAY from the ring from there:
          //  - top: the value line (last in reading order) sits at labelR;
          //    the name line(s) stack UPWARD above it.
          //  - bottom / middle: the name's first line sits at labelR (as it
          //    always did); the value line sits below it, growing downward,
          //    away from the ring.
          const textY = (isTop ? p.y - fontSize * 1.15 - lastLineExtra : p.y).toFixed(1);
          const valueY = (isTop ? p.y : p.y + lastLineExtra + fontSize * 1.15).toFixed(1);
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
                {valueText(axis)}
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
              {/* Row header, not a plain cell: the axis name is what names
               * both numbers in the row, so screen-reader table navigation
               * must announce it alongside the column header. */}
              <th scope="row" className="py-1 pr-2 text-left font-normal [overflow-wrap:anywhere]">
                {axis.name}
              </th>
              <td className="px-1 py-1 text-right tabular-nums">{axis.role}</td>
              <td className="py-1 pl-1 text-right tabular-nums">{axis.you}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </figure>
  );
}
