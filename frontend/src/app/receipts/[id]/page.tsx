"use client";

import { useEffect, useState, type ReactNode } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ExternalLink, Printer } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Logo } from "@/components/brand/Logo";
import { PageContainer } from "@/components/layout/PageContainer";
import { Skeleton } from "@/components/ui/skeleton";
import { getReceipt } from "@/lib/api";
import { safeUrl } from "@/lib/utils";
import type { Receipt } from "@/lib/types";

function sentOn(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    weekday: "short",
    day: "numeric",
    month: "long",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

// `artifact` is what every document saved since the assistant writes them
// carries. An unknown value shows nothing rather than a raw code.
const ORIGIN_LABEL: Record<string, string> = {
  polished: "your edited version",
  ai_draft: "the AI draft, unedited",
  artifact: "saved by your assistant",
};

function originLabel(origin: string | null | undefined): string | null {
  if (!origin) return null;
  return ORIGIN_LABEL[origin] ?? null;
}

/**
 * One receipt: the ad as it read and the documents as they were sent.
 * Read-only. The backend has no update or delete for receipts, so this page
 * has none either — that is the point of a receipt.
 */
export default function ReceiptDetailPage() {
  // Next 16: route params are async on the server; on the client `useParams`
  // is the synchronous door.
  const params = useParams<{ id: string }>();
  const receiptId = Number(params?.id);

  const validId = Number.isFinite(receiptId);

  const [receipt, setReceipt] = useState<Receipt | null>(null);
  // The id that failed, not a flag: navigating to another receipt must not
  // inherit the previous one's "not found".
  const [failedId, setFailedId] = useState<number | null>(null);

  useEffect(() => {
    if (!validId) return;
    let cancelled = false;
    getReceipt(receiptId)
      .then((r) => {
        if (!cancelled) setReceipt(r);
      })
      .catch(() => {
        if (!cancelled) setFailedId(receiptId);
      });
    return () => {
      cancelled = true;
    };
  }, [receiptId, validId]);

  const error = !validId || failedId === receiptId ? "Receipt not found" : null;

  if (error) {
    return (
      <PageContainer className="flex flex-col items-center gap-4 py-24 text-center">
        <h2 className="font-heading text-xl font-medium">{error}</h2>
        <Link href="/receipts">
          <Button variant="outline" size="sm" className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            All receipts
          </Button>
        </Link>
      </PageContainer>
    );
  }

  if (!receipt) {
    return (
      <PageContainer className="flex flex-col items-center py-8">
        <div className="w-full max-w-[720px] space-y-4" aria-busy="true">
          <Skeleton className="h-8 w-2/3" />
          <Skeleton className="h-40 w-full rounded-md" />
          <Skeleton className="h-64 w-full rounded-md" />
        </div>
      </PageContainer>
    );
  }

  return (
    <PageContainer className="receipt-desk flex flex-col items-center gap-4 py-8">
      <div className="flex w-full max-w-[720px] items-center justify-between gap-3">
        <Link
          href="/receipts"
          data-print-hide
          className="inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-brand"
        >
          <ArrowLeft className="h-4 w-4" />
          All receipts
        </Link>
        <Button
          type="button"
          variant="outline"
          size="sm"
          data-print-hide
          className="gap-2"
          onClick={() => window.print()}
        >
          <Printer className="h-4 w-4" />
          Download PDF
        </Button>
      </div>

      <article className="receipt-sheet w-full max-w-[720px] border border-paper-line bg-paper p-6 text-paper-ink shadow-card sm:p-14">
        <div className="flex items-center justify-between gap-3 border-b-2 border-paper-green pb-3.5">
          <Logo />
          <span className="font-mono text-xs text-paper-dim">
            RECEIPT R-{String(receipt.id).padStart(4, "0")}
          </span>
        </div>

        <h1
          className="mt-6 font-heading text-3xl font-normal leading-tight tracking-tight [overflow-wrap:anywhere]"
          data-testid="receipt-title"
        >
          {receipt.job_title}
        </h1>
        <p className="mt-1.5 text-paper-dim">
          {receipt.job_company}
          {receipt.job_location ? ` · ${receipt.job_location}` : ""}
        </p>

        <dl className="mt-6 grid grid-cols-1 gap-x-8 sm:grid-cols-2">
          <Fact label="Frozen" value={sentOn(receipt.sent_at)} />
          {receipt.channel && <Fact label="via" value={receipt.channel} />}
          {receipt.profile_version != null && (
            <Fact label="Profile" value={`v${receipt.profile_version}`} />
          )}
          {/* safeUrl returns "#" for anything that is not http(s): no link then. */}
          {receipt.job_apply_url && safeUrl(receipt.job_apply_url) !== "#" && (
            <Fact
              label="The ad"
              value={
                <a
                  href={safeUrl(receipt.job_apply_url)}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="inline-flex items-center gap-1 underline"
                >
                  Open <ExternalLink className="h-3 w-3" />
                </a>
              }
            />
          )}
        </dl>
        {receipt.note && <p className="mt-4 text-sm italic text-paper-dim">“{receipt.note}”</p>}

        <Section title="CV you sent" origin={receipt.cv_origin} body={receipt.cv_text} testId="receipt-cv" />
        <Section
          title="Cover letter you sent"
          origin={receipt.cover_letter_origin}
          body={receipt.cover_letter_text}
          testId="receipt-cover-letter"
        />
        <Section title="The ad, as it read that day" body={receipt.job_description} testId="receipt-ad" />
      </article>
    </PageContainer>
  );
}

function Fact({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div className="receipt-fact flex justify-between gap-3 border-t border-paper-line py-2.5 text-[13.5px]">
      <dt className="text-paper-dim">{label}</dt>
      <dd className="min-w-0 text-right font-mono text-[12.5px] [overflow-wrap:anywhere]">{value}</dd>
    </div>
  );
}

function Section({
  title,
  origin,
  body,
  testId,
}: {
  title: string;
  origin?: string | null;
  body: string | null;
  testId: string;
}) {
  const label = originLabel(origin);
  return (
    <section className="mt-8 border-t border-paper-line pt-4" data-testid={testId}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-paper-dim">
          {title}
        </h2>
        {label && <span className="font-mono text-xs text-paper-dim">{label}</span>}
      </div>
      {body ? (
        <pre className="whitespace-pre-wrap font-sans text-[15px] leading-relaxed [overflow-wrap:anywhere]">
          {body}
        </pre>
      ) : (
        <p className="text-sm text-paper-dim">
          Nothing was tailored in Job360 for this one — you sent your own file.
        </p>
      )}
    </section>
  );
}
