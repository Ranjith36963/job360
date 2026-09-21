"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { getApplication, recordApplicationReceipt } from "@/lib/api";
import type { ApplicationDetail, VisaShape } from "@/lib/api";
import { Timeline } from "@/components/applications/Timeline";
import { ArtifactVersions } from "@/components/applications/ArtifactVersions";
import { AlignmentPanel } from "@/components/applications/AlignmentPanel";
import { TailorSection } from "@/components/tailor/TailorSection";
import { Contacts } from "@/components/applications/Contacts";
import { Receipts } from "@/components/applications/Receipts";
import { NoteForm } from "@/components/applications/NoteForm";
import { LessonForm } from "@/components/applications/LessonForm";
import { VisaBadge } from "@/components/applications/VisaBadge";
import { VisaSelect } from "@/components/applications/VisaSelect";
import { STATUS_LABEL } from "@/lib/event-labels";

/** The application record: status, the durable job snapshot (spec R2 —
 * survives the catalog purging the live row), every artifact version, the
 * event timeline, receipts, and the fit verdict. */
export function ApplicationClient({ applicationId }: { applicationId: number }) {
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [marking, setMarking] = useState(false);
  const [historyOpen, setHistoryOpen] = useState(false);

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

  // Slice 7 (#514) — the backend always sends `visa`; the fallback only
  // guards a cached pre-slice-7 payload without the key.
  const visa: VisaShape = detail.visa ?? {
    signal: "unknown",
    detail: "",
    country: "",
    recorded_by: "",
    recorded_at: "",
    needs_sponsorship: null,
  };

  // Read off the stored record by the backend (`next_step.py`) — the same
  // line an agent sees on `get_application`, so human and agent agree.
  const nextStep = detail.next_step;

  const hasCvArtifact = detail.artifacts.some((a) => a.kind === "cv");

  // Newest first — a fresh "flag for next time" should read at the top.
  const lessonEvents = detail.events
    .filter((e) => e.event_type === "lesson" && !e.superseded)
    .sort((a, b) => new Date(b.occurred_at).getTime() - new Date(a.occurred_at).getTime());

  return (
    <div className="mx-auto flex max-w-3xl flex-col gap-6 px-4 py-8">
      <Link href="/applications" className="flex items-center gap-1.5 text-sm text-muted-foreground hover:text-foreground">
        <ArrowLeft className="h-4 w-4" /> All applications
      </Link>

      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="font-heading text-2xl font-bold">{detail.job.job_title || "Untitled role"}</h1>
          <p className="text-muted-foreground">{detail.job.job_company}</p>
          {nextStep?.label && (
            <p data-testid="next-step" className="text-sm text-primary">
              Next: {nextStep.label}
            </p>
          )}
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
          <VisaBadge
            signal={visa.signal}
            needsSponsorship={visa.needs_sponsorship}
            detail={visa.detail}
          />
          {detail.interview_at && (
            <span className="rounded-full bg-accent/20 px-3 py-1 text-sm font-medium text-accent-foreground">
              Interview {new Date(detail.interview_at).toLocaleString()}
            </span>
          )}
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

      <section data-testid="section-fit">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Fit</h2>
        <AlignmentPanel applicationId={detail.id} refreshKey={detail.updated_at} />
        <VisaSelect applicationId={detail.id} visa={visa} onSaved={load} />
      </section>

      <section data-testid="section-documents">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Documents
        </h2>
        <ArtifactVersions applicationId={detail.id} artifacts={detail.artifacts} receipts={detail.receipts} />
        <div className="mt-4">
          {!hasCvArtifact && (
            <p className="mb-2 text-xs text-muted-foreground">
              No CV for this job yet — your agent writes it and saves it here.
            </p>
          )}
          <TailorSection
            jobId={detail.job_id}
            applicationId={detail.id}
            hasDocuments={detail.artifacts.length > 0}
          />
        </div>
      </section>

      {detail.receipts.length > 0 && (
        <section data-testid="section-sent">
          <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
            Sent
          </h2>
          <Receipts receipts={detail.receipts} />
        </section>
      )}

      <section data-testid="section-people">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          People
        </h2>
        <Contacts applicationId={detail.id} contacts={detail.contacts} />
      </section>

      <section data-testid="section-lessons">
        <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
          Lessons
        </h2>
        {lessonEvents.length > 0 && (
          <ul className="mb-3 flex flex-col gap-2">
            {lessonEvents.map((event) => (
              <li key={event.id} data-testid="lesson-here" className="glass-card rounded-lg p-3 text-sm">
                <p>{event.detail}</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {new Date(event.occurred_at).toLocaleString()}
                </p>
              </li>
            ))}
          </ul>
        )}
        <LessonForm applicationId={detail.id} onRecorded={load} />
      </section>

      <section data-testid="section-timeline">
        <button
          type="button"
          data-testid="history-toggle"
          onClick={() => setHistoryOpen((open) => !open)}
          className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
        >
          {historyOpen ? "Hide history" : `Show history (${detail.events.length} events)`}
        </button>
        {historyOpen && (
          <>
            <Timeline events={detail.events} />
            <NoteForm applicationId={detail.id} onRecorded={load} />
          </>
        )}
      </section>
    </div>
  );
}
