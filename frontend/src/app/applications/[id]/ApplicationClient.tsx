"use client";

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { ArrowLeft } from "lucide-react";
import { toast } from "sonner";
import { ASKS_CHANGED_EVENT, getApplication, listAsks, recordApplicationReceipt } from "@/lib/api";
import type { ApplicationDetail, Ask, VisaShape } from "@/lib/api";
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
import { AskCard } from "@/components/needs-you/NeedsYou";
import { STATUS_LABEL, whoLabel } from "@/lib/event-labels";
import { formatDate, formatDateTime } from "@/lib/format-date";
import { PageContainer } from "@/components/layout/PageContainer";

// Small mono uppercase section label, the same one Home and Needs-you use.
const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const SECTION = "border-t border-border pt-5";

type StageTone = "go" | "wait" | "quiet";

// Status -> look. Live/positive states are green, the one state that waits on
// the user (not applied yet) is amber, closed-out states stay muted.
const STAGE_TONE: Record<string, StageTone> = {
  considering: "wait",
  applied: "go",
  replied: "go",
  interview_requested: "go",
  interview_scheduled: "go",
  interview_done: "go",
  offer: "go",
};

const STAGE_TEXT: Record<StageTone, string> = {
  go: "text-brand",
  wait: "text-warning",
  quiet: "text-muted-foreground",
};
const STAGE_DOT: Record<StageTone, string> = {
  go: "bg-brand",
  wait: "bg-warning",
  quiet: "bg-faint",
};

/** "APP-042 · brought by Claude · 3 Oct 2026" - stored facts only. Without a
 * `brought` event it falls back to the id and the created date. */
export function buildEyebrow(detail: ApplicationDetail): string {
  const id = `APP-${String(detail.id).padStart(3, "0")}`;
  const brought = detail.events
    .filter((e) => e.event_type === "brought")
    .sort((a, b) => new Date(a.occurred_at).getTime() - new Date(b.occurred_at).getTime())[0];
  if (!brought) {
    const created = formatDate(detail.created_at);
    return created ? `${id} · ${created}` : id;
  }
  const who = whoLabel(brought.recorded_by);
  const name = who.who === "you" ? "you" : who.name;
  const when = formatDate(brought.occurred_at);
  return [id, `brought by ${name}`, when].filter(Boolean).join(" · ");
}

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

  // After an answer or withdraw: re-read the application, then tell the
  // sidebar badge the fresh open count (same as the Needs-you page).
  const reloadAfterAsk = useCallback(async () => {
    await load();
    try {
      const r = await listAsks("open");
      window.dispatchEvent(new CustomEvent(ASKS_CHANGED_EVENT, { detail: r.open_count }));
    } catch {
      // The badge corrects itself on its next load.
    }
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

  const openAsks: Ask[] = (detail.asks ?? []).filter((a) => a.status === "open" && !a.withdrawn_at);
  const tone = STAGE_TONE[detail.status] ?? "quiet";
  const place = detail.job.job_location;

  return (
    <PageContainer className="flex flex-col gap-8 py-8">
      <Link
        href="/applications"
        className="flex items-center gap-1.5 self-start py-1 text-sm text-muted-foreground hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" /> All applications
      </Link>

      {/* Full-width header (owner decision, 2026-09-25): back link, job title,
          company only — every action and status control moved into the
          sticky right column below. */}
      <header>
        <p className="font-mono text-xs text-faint" data-testid="app-eyebrow">
          {buildEyebrow(detail)}
        </p>
        <h1 className="mt-2 text-balance font-heading text-[clamp(1.9rem,3.6vw,2.75rem)] font-normal leading-[1.08] tracking-[-0.022em]">
          {detail.job.job_title || "Untitled role"}
        </h1>
        <p className="mt-2 text-muted-foreground">
          <span>{detail.job.job_company}</span>
          {place && (
            <>
              {" · "}
              <span>{place}</span>
            </>
          )}
        </p>
        {!detail.job.catalog_present && (
          <p className="mt-1 text-xs text-muted-foreground/70">
            This listing is no longer in the catalog — the snapshot above is what it read when you brought it.
          </p>
        )}
      </header>

      {openAsks.length > 0 && (
        <section aria-labelledby="app-asks" data-testid="app-asks" className="flex flex-col gap-3">
          <h2 id="app-asks" className={LABEL}>
            Needs you
          </h2>
          <ul className="flex flex-col gap-3">
            {openAsks.map((a) => (
              <AskCard key={a.id} ask={a} mode="open" onChanged={reloadAfterAsk} />
            ))}
          </ul>
        </section>
      )}

      {/* At lg: two columns — LEFT (main) carries Fit/Documents/Sent/History,
          RIGHT (side) carries the actions (Next line, status, "What
          happened?", Mark Applied, View ad) plus Visa/People/Lessons, and
          stays sticky (owner decision, 2026-09-25) —
          `lg:top-6` leaves a small gap (the desktop shell has a sidebar, no top bar), and
          `lg:max-h-[calc(100vh-3rem)] lg:overflow-y-auto` lets it scroll on
          its own if it's taller than the viewport instead of pushing off
          screen. Below lg both columns render `display: contents` so their
          children become direct items of the single-column grid below,
          letting the numbered `order-*` classes interleave them into
          Actions, Fit, Visa, Documents, Sent, People, Lessons, History — the
          phone reading order the owner specified. `lg:order-none` drops that
          override once the real two-column layout takes over. */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,1fr)] lg:gap-0">
        <div data-testid="app-col-main" className="contents lg:flex lg:flex-col lg:gap-6 lg:pr-10">
          <section data-testid="section-fit" className={`order-2 ${SECTION} lg:order-none`}>
            <h2 className={`mb-3 ${LABEL}`}>Fit</h2>
            <AlignmentPanel applicationId={detail.id} refreshKey={detail.updated_at} />
          </section>

          <section data-testid="section-documents" className={`order-4 ${SECTION} lg:order-none`}>
            <h2 className={`mb-3 ${LABEL}`}>Documents</h2>
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
            <section data-testid="section-sent" className={`order-5 ${SECTION} lg:order-none`}>
              <h2 className={`mb-3 ${LABEL}`}>Sent</h2>
              <Receipts receipts={detail.receipts} />
            </section>
          )}

          <section data-testid="section-timeline" className={`order-8 ${SECTION} lg:order-none`}>
            <button
              type="button"
              data-testid="history-toggle"
              onClick={() => setHistoryOpen((open) => !open)}
              className={`mb-2 ${LABEL} hover:text-foreground`}
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

        <div
          data-testid="app-col-side"
          className="scroll-quiet contents lg:flex lg:flex-col lg:gap-6 lg:border-l lg:border-border lg:pl-8 lg:sticky lg:top-6 lg:max-h-[calc(100vh-3rem)] lg:self-start lg:overflow-y-auto"
        >
          <div data-testid="app-actions" className="order-1 flex flex-col items-start gap-3 border-t border-border pt-5 lg:order-none lg:border-t-0 lg:pt-0">
            {nextStep?.label && (
              <p data-testid="next-step" className="font-heading text-lg leading-snug text-brand">
                Next: {nextStep.label}
              </p>
            )}
            <span
              data-testid="status-label"
              className={`inline-flex items-center gap-1.5 font-mono text-[11px] font-medium uppercase tracking-[0.04em] ${STAGE_TEXT[tone]}`}
            >
              <i aria-hidden="true" className={`h-1.5 w-1.5 rounded-full ${STAGE_DOT[tone]}`} />
              {STATUS_LABEL[detail.status] ?? detail.status}
            </span>
            <StatusMenu applicationId={detail.id} onRecorded={load} />
            {/* Bug fix (new-user walk, 2026-09-27): this used to gate on
                `status === "considering"`, so picking ANY option in the
                status menu above (e.g. "Replied") made this button vanish
                forever, with no way left to create a receipt. The real gate
                is simpler and survives every status: show it whenever there
                is no receipt yet. */}
            {detail.receipts.length === 0 &&
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
                className="text-sm font-medium text-muted-foreground underline-offset-2 hover:text-foreground hover:underline"
              >
                View ad
              </a>
            )}
            <FollowUpField applicationId={detail.id} followUpOn={detail.follow_up_on ?? null} onRecorded={load} />
            <VisaBadge
              signal={visa.signal}
              needsSponsorship={visa.needs_sponsorship}
              detail={visa.detail}
            />
            {detail.interview_at && (
              <span className="font-mono text-xs text-foreground">
                Interview {formatDateTime(detail.interview_at)}
              </span>
            )}
          </div>

          <section data-testid="section-visa" className={`order-3 ${SECTION} lg:order-none`}>
            <h2 className={`mb-3 ${LABEL}`}>Visa / sponsorship</h2>
            <VisaSelect applicationId={detail.id} visa={visa} onSaved={load} />
          </section>

          <section data-testid="section-people" className={`order-6 ${SECTION} lg:order-none`}>
            <h2 className={`mb-3 ${LABEL}`}>People</h2>
            <Contacts applicationId={detail.id} contacts={detail.contacts} />
          </section>

          <section data-testid="section-lessons" className={`order-7 ${SECTION} lg:order-none`}>
            <h2 className={`mb-3 ${LABEL}`}>Lessons</h2>
            {lessonEvents.length > 0 && (
              <ul className="mb-3 flex flex-col gap-2">
                {lessonEvents.map((event) => (
                  <li key={event.id} data-testid="lesson-here" className="border-t border-border pt-3 text-sm first:border-t-0 first:pt-0">
                    <p className="font-heading text-base leading-snug">{event.detail}</p>
                    <p className="mt-1 font-mono text-xs text-faint">
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
