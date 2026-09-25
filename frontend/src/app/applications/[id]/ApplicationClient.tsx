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
import { StatusMenu } from "@/components/applications/StatusMenu";
import { FollowUpField } from "@/components/applications/FollowUpField";
import { STATUS_LABEL } from "@/lib/event-labels";
import { formatDateTime } from "@/lib/format-date";
import { PageContainer } from "@/components/layout/PageContainer";

/** The application record: status, the durable job snapshot (spec R2 —
 * survives the catalog purging the live row), every artifact version, the
 * event timeline, receipts, and the fit verdict. */
export function ApplicationClient({ applicationId }: { applicationId: number }) {
  const [detail, setDetail] = useState<ApplicationDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [marking, setMarking] = useState(false);
  const [markConfirming, setMarkConfirming] = useState(false);
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
      setMarkConfirming(false);
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
    <PageContainer className="flex flex-col gap-6 py-8">
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
        <div className="flex flex-wrap items-center gap-3">
          <span
            data-testid="status-label"
            className="rounded-full bg-primary/10 px-3 py-1 text-sm font-medium text-primary"
          >
            {STATUS_LABEL[detail.status] ?? detail.status}
          </span>
          <StatusMenu applicationId={detail.id} onRecorded={load} />
          <FollowUpField applicationId={detail.id} followUpOn={detail.follow_up_on ?? null} onRecorded={load} />
          <VisaBadge
            signal={visa.signal}
            needsSponsorship={visa.needs_sponsorship}
            detail={visa.detail}
          />
          {detail.interview_at && (
            <span className="rounded-full bg-accent/20 px-3 py-1 text-sm font-medium text-accent-foreground">
              Interview {formatDateTime(detail.interview_at)}
            </span>
          )}
          {detail.status === "considering" &&
            (!markConfirming ? (
              <button
                type="button"
                data-testid="mark-applied"
                onClick={() => setMarkConfirming(true)}
                disabled={marking}
                className="rounded-lg bg-primary px-4 py-2 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
              >
                Mark Applied
              </button>
            ) : (
              <div className="flex flex-wrap items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-2 text-xs">
                <span>
                  Record that you applied? This creates a receipt that can&apos;t be deleted.
                </span>
                <button
                  type="button"
                  data-testid="mark-applied-confirm"
                  onClick={() => void markApplied()}
                  disabled={marking}
                  className="rounded-md bg-primary px-2.5 py-1 text-xs font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50"
                >
                  {marking ? "Recording…" : "Confirm"}
                </button>
                <button
                  type="button"
                  data-testid="mark-applied-cancel"
                  onClick={() => setMarkConfirming(false)}
                  disabled={marking}
                  className="rounded-md border border-border px-2.5 py-1 text-xs font-medium text-muted-foreground hover:text-foreground disabled:opacity-50"
                >
                  Cancel
                </button>
              </div>
            ))}
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

      {/* At lg: two columns — LEFT (main) carries Fit/Documents/Sent/History,
          RIGHT (side) carries Visa/People/Lessons (owner decision,
          2026-09-25). Below lg both columns render `display: contents` so
          their sections become direct items of the single-column grid below,
          letting the numbered `order-*` classes below interleave them into
          Fit, Visa, Documents, Sent, People, Lessons, History — the same
          order the page used before this had two columns. `lg:order-none`
          drops that override once the real two-column layout takes over. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)]">
        <div data-testid="app-col-main" className="contents lg:flex lg:flex-col lg:gap-6">
          <section data-testid="section-fit" className="order-1 lg:order-none">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">Fit</h2>
            <AlignmentPanel applicationId={detail.id} refreshKey={detail.updated_at} />
          </section>

          <section data-testid="section-documents" className="order-3 lg:order-none">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Documents
            </h2>
            <ArtifactVersions applicationId={detail.id} artifacts={detail.artifacts} receipts={detail.receipts} />
            <div className="mt-4">
              {!hasCvArtifact && (
                <p className="mb-2 text-xs text-muted-foreground">
                  No CV for this job yet — your assistant writes it and saves it here.
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
            <section data-testid="section-sent" className="order-4 lg:order-none">
              <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
                Sent
              </h2>
              <Receipts receipts={detail.receipts} />
            </section>
          )}

          <section data-testid="section-timeline" className="order-7 lg:order-none">
            <button
              type="button"
              data-testid="history-toggle"
              onClick={() => setHistoryOpen((open) => !open)}
              className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground hover:text-foreground"
            >
              {historyOpen ? "Hide history" : `Show history (${detail.events.length})`}
            </button>
            {historyOpen && (
              <>
                <Timeline events={detail.events} />
                <NoteForm applicationId={detail.id} onRecorded={load} />
              </>
            )}
          </section>
        </div>

        <div data-testid="app-col-side" className="contents lg:flex lg:flex-col lg:gap-6">
          <section data-testid="section-visa" className="order-2 lg:order-none">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Visa / sponsorship
            </h2>
            <VisaSelect applicationId={detail.id} visa={visa} onSaved={load} />
          </section>

          <section data-testid="section-people" className="order-5 lg:order-none">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              People
            </h2>
            <Contacts applicationId={detail.id} contacts={detail.contacts} />
          </section>

          <section data-testid="section-lessons" className="order-6 lg:order-none">
            <h2 className="mb-2 text-sm font-semibold uppercase tracking-wide text-muted-foreground">
              Lessons
            </h2>
            {lessonEvents.length > 0 && (
              <ul className="mb-3 flex flex-col gap-2">
                {lessonEvents.map((event) => (
                  <li key={event.id} data-testid="lesson-here" className="glass-card rounded-lg p-3 text-sm">
                    <p>{event.detail}</p>
                    <p className="mt-1 text-xs text-muted-foreground">
                      {formatDateTime(event.occurred_at)}
                    </p>
                  </li>
                ))}
              </ul>
            )}
            <LessonForm applicationId={detail.id} onRecorded={load} />
          </section>
        </div>
      </div>
    </PageContainer>
  );
}
