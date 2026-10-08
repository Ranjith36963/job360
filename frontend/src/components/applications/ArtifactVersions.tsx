"use client";

import { useCallback, useRef, useState } from "react";
import { downloadApplicationArtifact, getApplicationArtifact, getArtifactDiff } from "@/lib/api";
import type { TailorFormat } from "@/lib/api";
import { toast } from "@/lib/toast";
import { CopyButton } from "@/components/applications/CopyButton";
import type { ApplicationArtifact, ApplicationReceiptEntry, ArtifactDiff as ArtifactDiffData } from "@/lib/api";
import { ArtifactDiff } from "@/components/applications/ArtifactDiff";
import { formatDate } from "@/lib/format-date";
import { CvSeenButton } from "@/components/applications/CvSeenButton";
import type { ApplicationControls } from "@/lib/api";

/** Plain words for who/what made a version (owner decision 6, 2026-09-24):
 * `web:tailor` -> "Made on the website", `agent:<Name>` -> "Written by
 * <Name>". Anything else (e.g. a bare "web" or "agent" from an older/mocked
 * payload) renders as-is — it is already plain text, never a code-font tool
 * name. */
function describeMadeBy(madeBy: string): string {
  if (madeBy === "web:tailor") return "Made on the website";
  if (madeBy.startsWith("agent:")) return `Written by ${madeBy.slice("agent:".length)}`;
  return madeBy;
}

/** Whose opinion the ATS score is: the assistant that saved the version —
 * never Job360's (it has no LLM and scores nothing). */
function opinionOwner(madeBy: string): string {
  if (madeBy.startsWith("agent:")) {
    const name = madeBy.slice("agent:".length).trim();
    if (name) return `${name}'s opinion`;
  }
  return "your assistant's opinion";
}

/** "ATS 82 (Claude's opinion)", the notes folded under it. Nothing when the
 * assistant gave no score (rule #29: empty stays silent). */
function AtsOpinion({ artifact }: { artifact: ApplicationArtifact }) {
  if (artifact.ats_score == null) return null;
  const head = (
    <>
      <span className="font-mono tabular-nums text-foreground">ATS {artifact.ats_score}</span>
      <span className="ml-1.5 text-muted-foreground">({opinionOwner(artifact.made_by)})</span>
    </>
  );
  const notes = artifact.ats_notes?.trim();
  return (
    <div data-testid="artifact-ats" className="mt-1 text-xs">
      {notes ? (
        <details>
          <summary className="cursor-pointer">{head}</summary>
          <p className="mt-1 whitespace-pre-wrap border-l border-border pl-2 text-muted-foreground">
            {notes}
          </p>
        </details>
      ) : (
        <p>{head}</p>
      )}
    </div>
  );
}

/** Approximate page count from a character count — chars/3000, minimum 1
 * page (owner decision 6). */
function pageCount(chars: number): string {
  const pages = Math.max(1, Math.round(chars / 3000));
  return `${pages} page${pages === 1 ? "" : "s"}`;
}

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
  controls = null,
  onControls,
}: {
  applicationId: number;
  artifacts: ApplicationArtifact[];
  receipts?: ApplicationReceiptEntry[];
  /** S3: the decision state. When given, the LATEST CV shows "I've checked this CV". */
  controls?: ApplicationControls | null;
  onControls?: (next: ApplicationControls) => void;
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [loadingId, setLoadingId] = useState<number | null>(null);
  const [compareId, setCompareId] = useState<number | null>(null);
  const [against, setAgainst] = useState<string>("");
  const [diff, setDiff] = useState<ArtifactDiffData | null>(null);
  const [diffError, setDiffError] = useState<string | null>(null);
  const [diffLoading, setDiffLoading] = useState(false);
  const [downloadingId, setDownloadingId] = useState<number | null>(null);

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

  /** The version's full text — already loaded, else fetched once. */
  const textOf = useCallback(
    async (artifact: ApplicationArtifact): Promise<string> => {
      if (artifact.text != null) return artifact.text;
      if (texts[artifact.id] != null) return texts[artifact.id];
      const full = await getApplicationArtifact(applicationId, artifact.id);
      const text = full.text ?? "";
      setTexts((prev) => ({ ...prev, [artifact.id]: text }));
      return text;
    },
    [applicationId, texts]
  );

  const download = useCallback(
    async (artifact: ApplicationArtifact, fmt: TailorFormat) => {
      setDownloadingId(artifact.id);
      try {
        await downloadApplicationArtifact(
          applicationId,
          artifact.id,
          fmt,
          `${artifact.kind.replace("_", "-")}-v${artifact.version_no}`
        );
      } catch (err) {
        toast.apiError(err, "Download failed");
      } finally {
        setDownloadingId(null);
      }
    },
    [applicationId]
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
    return (
      <p className="text-sm text-muted-foreground">
        No documents yet. Your assistant saves every CV and cover letter version here.
      </p>
    );
  }

  const byKind = new Map<string, ApplicationArtifact[]>();
  for (const a of artifacts) {
    const list = byKind.get(a.kind) ?? [];
    list.push(a);
    byKind.set(a.kind, list);
  }
  for (const list of byKind.values()) list.sort((a, b) => a.version_no - b.version_no);

  return (
    <div className="flex flex-col gap-6">
      {[...byKind.entries()].map(([kind, versions]) => (
        <div key={kind}>
          <p className="mb-1 font-mono text-[11px] uppercase tracking-wider text-faint">
            {kind.replace("_", " ")}
          </p>
          <div className="flex flex-col">
            {versions.map((artifact) => {
              const applied = appliedIds.has(artifact.id);
              const others = versions.filter((v) => v.id !== artifact.id);
              return (
                <div key={artifact.id} data-testid="artifact-version" className="border-t border-border py-2.5 first:border-t-0">
                  <div className="flex w-full items-center justify-between gap-3 text-sm">
                    <button type="button" onClick={() => void open(artifact)} className="min-w-0 flex-1 text-left">
                      <span data-testid="artifact-version-label" className="font-mono tabular-nums">
                        v{artifact.version_no}
                      </span>
                      {applied && (
                        <span
                          data-testid="artifact-applied-badge"
                          className="ml-2 font-mono text-[11px] text-brand"
                        >
                          Applied
                        </span>
                      )}
                      <span className="ml-2 text-[12.5px] text-muted-foreground">
                        {describeMadeBy(artifact.made_by)} · {pageCount(artifact.chars)}
                      </span>
                    </button>
                    <span className="shrink-0 font-mono text-xs tabular-nums text-faint">
                      {formatDate(artifact.created_at)}
                    </span>
                    <button
                      type="button"
                      data-testid="artifact-compare"
                      onClick={() => void compare(artifact)}
                      className="shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
                    >
                      {compareId === artifact.id ? "Close" : "Compare"}
                    </button>
                  </div>
                  {(kind === "cv" || kind === "cover_letter") && (
                    <div className="mt-1 flex items-center gap-3">
                      <CopyButton getText={() => textOf(artifact)} testId="artifact-copy" />
                      {kind === "cv" && controls?.cv?.artifact_id === artifact.id && (
                        <CvSeenButton
                          applicationId={applicationId}
                          artifactId={artifact.id}
                          seen={controls.cv.seen ?? null}
                          onChanged={onControls}
                        />
                      )}
                      {downloadingId === artifact.id ? (
                        <span data-testid="artifact-downloading" className="text-xs text-muted-foreground">
                          Downloading…
                        </span>
                      ) : (
                        (["docx", "pdf"] as const).map((fmt) => (
                          <button
                            key={fmt}
                            type="button"
                            data-testid={`artifact-download-${fmt}`}
                            disabled={downloadingId !== null}
                            onClick={() => void download(artifact, fmt)}
                            className="shrink-0 text-xs text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
                          >
                            {fmt === "docx" ? "Word" : "PDF"}
                          </button>
                        ))
                      )}
                    </div>
                  )}
                  <AtsOpinion artifact={artifact} />
                  {openId === artifact.id && (
                    <div className="mt-2 whitespace-pre-wrap rounded-lg border border-border bg-muted/30 p-3 text-sm">
                      {loadingId === artifact.id ? "Loading…" : texts[artifact.id]}
                    </div>
                  )}
                  {compareId === artifact.id && (
                    <div className="mt-2">
                      <label className="flex items-center gap-2 font-mono text-[11px] uppercase tracking-wider text-faint">
                        Compare with
                        <select
                          data-testid="artifact-compare-base"
                          value={against}
                          onChange={(e) => void changeBase(artifact.id, e.target.value)}
                          className="rounded-md border border-border bg-background px-2 py-1 font-sans text-xs normal-case tracking-normal text-foreground"
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
