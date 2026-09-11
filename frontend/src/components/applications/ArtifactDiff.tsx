"use client";

import type { ArtifactDiff as ArtifactDiffData } from "@/lib/api";

/**
 * Two panes, changed lines marked on both (slice 8, #515). Left = what the
 * version was compared against (the profile's CV, or an earlier version),
 * right = this version. Removed lines are painted on the left, added lines
 * on the right; unchanged lines sit on both so the eye can line them up.
 *
 * Read-only by design — there is no Keep here. The version that counts is
 * the one the receipt names (VISION decision 26); `target.applied` says so.
 */
export function ArtifactDiff({ diff }: { diff: ArtifactDiffData }) {
  const left = diff.lines.filter((line) => line.op !== "add");
  const right = diff.lines.filter((line) => line.op !== "del");
  const targetLabel = diff.target.applied
    ? "Applied version"
    : `This version · ${diff.target.made_by}`;

  return (
    <div data-testid="artifact-diff" className="mt-3 flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        <span data-testid="artifact-diff-added">{diff.added} added</span>
        {" · "}
        <span data-testid="artifact-diff-removed">{diff.removed} removed</span>
        {diff.truncated && " · long text, showing the first part only"}
      </p>
      <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
        <Pane testId="artifact-diff-left" title={diff.base.label} lines={left} />
        <Pane testId="artifact-diff-right" title={targetLabel} lines={right} />
      </div>
    </div>
  );
}

function Pane({
  testId,
  title,
  lines,
}: {
  testId: string;
  title: string;
  lines: ArtifactDiffData["lines"];
}) {
  return (
    <div data-testid={testId} className="min-w-0 rounded-md border border-border bg-muted/30">
      <p className="border-b border-border px-3 py-1.5 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
        {title}
      </p>
      <div className="max-h-[32rem] overflow-y-auto p-2 font-mono text-xs leading-5">
        {lines.length === 0 && <p className="px-1 text-muted-foreground">(empty)</p>}
        {lines.map((line, index) => (
          <div
            key={index}
            data-diff={line.op}
            className={
              line.op === "add"
                ? "whitespace-pre-wrap rounded-sm bg-emerald-500/15 px-1"
                : line.op === "del"
                  ? "whitespace-pre-wrap rounded-sm bg-red-500/15 px-1 line-through decoration-red-500/40"
                  : "whitespace-pre-wrap px-1"
            }
          >
            {line.text || " "}
          </div>
        ))}
      </div>
    </div>
  );
}
