"use client";

import { useCallback, useRef, useState } from "react";
import { getApplicationArtifact, getArtifactDiff } from "@/lib/api";
import type { ApplicationArtifact, ApplicationReceiptEntry, ArtifactDiff as ArtifactDiffData } from "@/lib/api";
import { ArtifactDiff } from "@/components/applications/ArtifactDiff";

/**
 * Every version of every artifact, grouped by kind — "every version still
 * readable" (spec's done-when). Artifact TEXT is off by default on
 * `GET /applications/{id}` (R11); clicking a version fetches its full text
 * from `GET /applications/{id}/artifacts/{artifact_id}` on demand.
 *
 * Slice 8 (#515): the version a receipt names carries an **Applied** badge
 * (that is the tailored one — VISION decision 26, no Keep button), and every
 * version has a **Compare** that opens `ArtifactDiff` against the profile's
 * original CV or any other version of the same kind. Nothing here writes.
 */
export function ArtifactVersions({
  applicationId,
  artifacts,
  receipts = [],
}: {
  applicationId: number;
  artifacts: ApplicationArtifact[];
  receipts?: ApplicationReceiptEntry[];
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [compareId, setCompareId] = useState<number | null>(null);
  const [against, setAgainst] = useState<string>("");
  const [diff, setDiff] = useState<ArtifactDiffData | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);

  const appliedIds = new Set<number>();
  for (const receipt of receipts) {
    if (receipt.cv_artifact_id != null) appliedIds.add(receipt.cv_artifact_id);
    if (receipt.cover_letter_artifact_id != null) appliedIds.add(receipt.cover_letter_artifact_id);
  }

  const open = useCallback(
    async (artifact: ApplicationArtifact) => {
      if (openId === artifact.id) {
        setOpenId(null);
        return;
      }
      setOpenId(artifact.id);
      if (artifact.text != null) {
        setTexts((prev) => ({ ...prev, [artifact.id]: artifact.text as string }));
        return;
      }
      if (texts[artifact.id] != null) return;
      setLoadingId(artifact.id);
      try {
        const full = await getApplicationArtifact(applicationId, artifact.id);
        setTexts((prev) => ({ ...prev, [artifact.id]: full.text ?? "" }));
      } finally {
        setLoadingId(null);
      }
    },
    [applicationId, openId, texts]
  );

  // Every diff fetch is numbered; a response that is not the newest request
  // is dropped. Without this, Compare v2 → Compare v1 (or two quick base
  // changes on one version) let the LAST response to land win, painting a
  // diff for the wrong version or the wrong base.
  const requestSeq = useRef(0);

  const loadDiff = useCallback(
    async (artifactId: number, base: string) => {
      const seq = ++requestSeq.current;
      setDiffLoading(true);
      setDiffError(null);
      setDiff(null);
      try {
        const res = await getArtifactDiff(applicationId, artifactId, base || undefined);
        if (seq !== requestSeq.current) return;
        setDiff(res);
      } catch (err) {
        if (seq !== requestSeq.current) return;
        setDiffError(err instanceof Error ? err.message : "Could not load the comparison.");
      } finally {
        if (seq === requestSeq.current) setDiffLoading(false);
      }
    },
    [applicationId]
  );

  const compare = useCallback(
    async (artifact: ApplicationArtifact) => {
      if (compareId === artifact.id) {
        requestSeq.current += 1; // an in-flight response must not repopulate a closed panel
        setCompareId(null);
        setDiff(null);
        setDiffLoading(false);
        return;
      }
      setCompareId(artifact.id);
      setAgainst("");
      await loadDiff(artifact.id, "");
    },
    [compareId, loadDiff]
  );

  const changeBase = useCallback(
    async (artifactId: number, base: string) => {
      setAgainst(base);
      await loadDiff(artifactId, base);
    },
    [loadDiff]
  );

  if (artifacts.length === 0) {
    return <p className="text-sm text-muted-foreground">No CV or cover letter versions saved yet.</p>;
  }

  const byKind = new Map<string, ApplicationArtifact[]>();
  for (const a of artifacts) {
    const list = byKind.get(a.kind) ?? [];
    list.push(a);
    byKind.set(a.kind, list);
  }
  for (const list of byKind.values()) list.sort((a, b) => a.version_no - b.version_no);

  return (
    <div className="flex flex-col gap-4">
      {[...byKind.entries()].map(([kind, versions]) => (
        <div key={kind}>
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-muted-foreground">
            {kind.replace("_", " ")}
          </p>
          <div className="flex flex-col gap-2">
            {versions.map((artifact) => {
              const applied = appliedIds.has(artifact.id);
              const others = versions.filter((v) => v.id !== artifact.id);
              return (
                <div key={artifact.id} data-testid="artifact-version" className="glass-card rounded-lg p-3">
                  <div className="flex w-full items-center justify-between gap-2 text-sm font-medium">
                    <button type="button" onClick={() => void open(artifact)} className="min-w-0 flex-1 text-left">
                      <span data-testid="artifact-version-label">v{artifact.version_no}</span>
                      {applied && (
                        <span
                          data-testid="artifact-applied-badge"
                          className="ml-2 rounded-full bg-emerald-500/15 px-2 py-0.5 text-xs font-semibold text-emerald-700 dark:text-emerald-300"
                        >
                          Applied
                        </span>
                      )}
                      <span className="ml-2 text-xs font-normal text-muted-foreground">
                        {artifact.made_by} · {artifact.chars} chars
                      </span>
                    </button>
                    <span className="text-xs text-muted-foreground">
                      {new Date(artifact.created_at).toLocaleDateString()}
                    </span>
                    <button
                      type="button"
                      data-testid="artifact-compare"
                      onClick={() => void compare(artifact)}
                      className="rounded-md border border-border px-2 py-1 text-xs font-medium text-muted-foreground hover:text-foreground"
                    >
                      {compareId === artifact.id ? "Close" : "Compare"}
                    </button>
                  </div>
                  {openId === artifact.id && (
                    <div className="mt-2 whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-sm">
                      {loadingId === artifact.id ? "Loading…" : texts[artifact.id]}
                    </div>
                  )}
                  {compareId === artifact.id && (
                    <div className="mt-2">
                      <label className="flex items-center gap-2 text-xs text-muted-foreground">
                        Compare with
                        <select
                          data-testid="artifact-compare-base"
                          value={against}
                          onChange={(e) => void changeBase(artifact.id, e.target.value)}
                          className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                        >
                          <option value="">
                            {kind === "cv" ? "Original CV (your profile)" : "The version before this"}
                          </option>
                          {others.map((v) => (
                            <option key={v.id} value={String(v.id)}>
                              v{v.version_no}
                              {appliedIds.has(v.id) ? " (applied)" : ""}
                            </option>
                          ))}
                        </select>
                      </label>
                      {diffLoading && <p className="mt-2 text-xs text-muted-foreground">Comparing…</p>}
                      {diffError && <p className="mt-2 text-xs text-destructive">{diffError}</p>}
                      {!diffLoading && diff && diff.target.artifact_id === artifact.id && (
                        <ArtifactDiff diff={diff} />
                      )}
                    </div>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
