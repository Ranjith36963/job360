"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { getApplication, recordApplicationReceipt } from "@/lib/api";
import type { ApplicationDetail } from "@/lib/api";
import { Timeline } from "@/components/applications/Timeline";
import { ArtifactVersions } from "@/components/applications/ArtifactVersions";
import { FitPanel } from "@/components/applications/FitPanel";
import { TailorSection } from "@/components/tailor/TailorSection";

const STATUS_LABEL: Record<string, string> = {
  considering: "Considering",
  applied: "Applied",
  replied: "Replied",
  interview_requested: "Interview requested",
  interview_scheduled: "Interview scheduled",
  interview_done: "Interview done",
  offer: "Offer",
  rejected: "Rejected",
  withdrawn: "Withdrawn",
  ghosted: "Ghosted",
};

/** The application record: status, the durable job snapshot (spec R2 —
 * survives the catalog purging the live row), every artifact version, the
 * event timeline, receipts, and the fit verdict. */
export function ApplicationClient({ applicationId }: { applicationId: number }) {
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [marking, setMarking] = useState(false);

  const load = useCallback(async () => {
    try {
      const res = await getApplication(applicationId);
      setDetail(res);
      setError(null);
    } catch {
      setError("Could not load this application.");
    }
  }, [applicationId]);

  useEffect(() => {
    void load();
  }, [load]);

  const markApplied = useCallback(async () => {
    setMarking(true);
    try {
      await recordApplicationReceipt(applicationId, {});
      await load();
    } catch (err) {
      // C10 (application-spine review) — see ApplicationList.tsx's identical
      // fix: without this the button just went quiet on failure.
      const msg = err instanceof Error ? err.message : "Could not mark this application applied.";
      toast.error(msg);
    } finally {
      setMarking(false);
    }
  }, [applicationId, load]);

  if (error) {
    return <p className="text-sm text-destructive">{error}</p>;
  }
  if (!detail) {
    return <p className="text-sm text-muted-foreground">Loading…</p>;
  }

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8">
      <Link href="/applications" className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All applications
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold">{detail.job.job_title || "Untitled role"}</h1>
          <p className="text-muted-foreground">{detail.job.job_company}</p>
          {!detail.job.catalog_present && (
            <p className="mt-1 text-xs text-muted-foreground/70">
              This listing is no longer in the catalog — the snapshot above is what it read when you brought it.
            </p>
          )}
        </div>
        <div className="flex items-center gap-3">
          <span className="rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary">
            {STATUS_LABEL[detail.status] ?? detail.status}
          </span>
          {detail.status === "considering" && (
            <button
              type="button"
              onClick={() => void markApplied()}
              disabled={marking}
              className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
            >
              {marking ? "Marking…" : "Mark Applied"}
            </button>
          )}
          {detail.job.job_url && (
            <a
              href={detail.job.job_url}
              target="_blank"
              rel="noreferrer"
              className="rounded-lg border border-border px-4 py-2 text-sm font-medium text-muted-foreground hover:text-foreground"
            >
              View ad
            </a>
          )}
        </div>
      </div>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Fit</h2>
        <FitPanel fit={detail.fit} />
      </section>

      <section>
        <TailorSection jobId={detail.job_id} />
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Artifacts
        </h2>
        <ArtifactVersions applicationId={detail.id} artifacts={detail.artifacts} />
      </section>

      <section>
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Timeline
        </h2>
        <Timeline events={detail.events} />
      </section>
    </div>
  );
}
