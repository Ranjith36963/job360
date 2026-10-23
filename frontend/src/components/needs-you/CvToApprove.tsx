"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { downloadApplicationArtifact, getCvToApprove, markCvSeen } from "@/lib/api";
import type { CvToApproveRow } from "@/lib/api";
import { toast } from "@/lib/toast";

const sectionLabel = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const okBtn =
  "rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50";
const linkBtn =
  "text-xs font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50";

/**
 * "Approve CV" on Needs you (owner decision 2026-10-23): every open application whose newest CV you
 * have not seen yet. "CV OK" records cv_seen (where=web) through the same route as the application
 * page; an OK in chat counts too, and either clears the gate's cv_not_seen. Job titles and company
 * names are untrusted text, rendered as React text only. Nothing waiting, or a failed read: no section.
 */
export function CvToApprove() {
  const [rows, setRows] = useState<CvToApproveRow[]>([]);
  const [busy, setBusy] = useState<number | null>(null);

  const fetchRows = useCallback(async (): Promise<CvToApproveRow[]> => {
    try {
      return (await getCvToApprove()).items;
    } catch {
      return [];
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    void fetchRows().then((r) => {
      if (!cancelled) setRows(r);
    });
    return () => {
      cancelled = true;
    };
  }, [fetchRows]);

  async function approve(row: CvToApproveRow) {
    setBusy(row.application_id);
    try {
      await markCvSeen(row.application_id, row.cv.artifact_id);
      setRows(await fetchRows());
    } catch (err) {
      toast.apiError(err, "Couldn't save that you checked this CV");
    } finally {
      setBusy(null);
    }
  }

  async function download(row: CvToApproveRow) {
    try {
      await downloadApplicationArtifact(row.application_id, row.cv.artifact_id, "pdf", `CV_v${row.cv.version}`);
    } catch (err) {
      toast.apiError(err, "Couldn't download this CV");
    }
  }

  if (rows.length === 0) return null;
  return (
    <section aria-labelledby="approve-cv" data-testid="approve-cv" className="flex flex-col gap-3">
      <h2 id="approve-cv" className={sectionLabel}>
        Approve CV
      </h2>
      <ul className="flex flex-col gap-3">
        {rows.map((r) => (
          <li
            key={r.application_id}
            data-testid={`approve-cv-${r.application_id}`}
            className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-card p-4 text-sm shadow-card"
          >
            <div className="flex min-w-0 flex-col gap-1">
              <Link href={`/applications/${r.application_id}`} className="font-medium underline-offset-2 hover:underline">
                {[r.job_title || "Untitled role", r.job_company].filter(Boolean).join(" · ")}
              </Link>
              <p className="text-xs text-muted-foreground">
                CV version {r.cv.version}, not checked yet.{" "}
                <Link href={`/applications/${r.application_id}`} className={linkBtn}>
                  Read it
                </Link>{" "}
                ·{" "}
                <button type="button" className={linkBtn} onClick={() => void download(r)}>
                  PDF
                </button>
              </p>
            </div>
            <button
              type="button"
              data-testid={`approve-cv-ok-${r.application_id}`}
              disabled={busy !== null}
              onClick={() => void approve(r)}
              className={okBtn}
            >
              {busy === r.application_id ? "Saving…" : "CV OK"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
