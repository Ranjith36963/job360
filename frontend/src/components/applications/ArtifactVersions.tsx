"use client";

import { useCallback, useState } from "react";
import { getApplicationArtifact } from "@/lib/api";
import type { ApplicationArtifact } from "@/lib/api";

/**
 * Every version of every artifact, grouped by kind — "every version still
 * readable" (spec's done-when). Artifact TEXT is off by default on
 * `GET /applications/{id}` (R11); clicking a version fetches its full text
 * from `GET /applications/{id}/artifacts/{artifact_id}` on demand.
 */
export function ArtifactVersions({
  applicationId,
  artifacts,
}: {
  applicationId: number;
  artifacts: ApplicationArtifact[];
}) {
  const [openId, setOpenId] = useState<number | null>(null);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [loadingId, setLoadingId] = useState<number | null>(null);

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
            {versions.map((artifact) => (
              <div key={artifact.id} className="glass-card rounded-lg p-3">
                <button
                  type="button"
                  onClick={() => void open(artifact)}
                  className="flex w-full items-center justify-between gap-2 text-left text-sm font-medium"
                >
                  <span>
                    <span data-testid="artifact-version-label">v{artifact.version_no}</span>{" "}
                    <span className="ml-2 text-xs font-normal text-muted-foreground">
                      {artifact.made_by} · {artifact.chars} chars
                    </span>
                  </span>
                  <span className="text-xs text-muted-foreground">
                    {new Date(artifact.created_at).toLocaleDateString()}
                  </span>
                </button>
                {openId === artifact.id && (
                  <div className="mt-2 whitespace-pre-wrap rounded-md bg-muted/30 p-3 text-sm">
                    {loadingId === artifact.id ? "Loading…" : texts[artifact.id]}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
