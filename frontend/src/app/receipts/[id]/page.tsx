"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { ArrowLeft, ExternalLink, Lock } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
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

const ORIGIN_LABEL: Record<string, string> = {
  polished: "your edited version",
  ai_draft: "the AI draft, unedited",
};

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
      <div className="mx-auto flex max-w-3xl flex-col items-center gap-4 px-4 py-24 text-center">
        <h2 className="font-heading text-xl font-semibold">{error}</h2>
        <Link href="/receipts">
          <Button variant="outline" size="sm" className="gap-2">
            <ArrowLeft className="h-4 w-4" />
            All receipts
          </Button>
        </Link>
      </div>
    );
  }

  if (!receipt) {
    return (
      <div className="mx-auto max-w-3xl space-y-4 px-4 py-8 sm:px-6" aria-busy="true">
        <Skeleton className="h-8 w-2/3" />
        <Skeleton className="h-40 w-full rounded-xl" />
        <Skeleton className="h-64 w-full rounded-xl" />
      </div>
    );
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <Link
        href="/receipts"
        className="mb-4 inline-flex items-center gap-1 text-sm text-muted-foreground hover:text-primary"
      >
        <ArrowLeft className="h-4 w-4" />
        All receipts
      </Link>

      <header className="mb-6">
        <h1 className="font-heading text-2xl font-semibold tracking-tight" data-testid="receipt-title">
          {receipt.job_title}
        </h1>
        <p className="text-muted-foreground">
          {receipt.job_company}
          {receipt.job_location ? ` · ${receipt.job_location}` : ""}
        </p>
        <div className="mt-3 flex flex-wrap items-center gap-2 text-sm">
          <Badge variant="outline" className="gap-1">
            <Lock className="h-3 w-3" />
            Frozen {sentOn(receipt.sent_at)}
          </Badge>
          {receipt.channel && <Badge variant="outline">via {receipt.channel}</Badge>}
          {receipt.profile_version != null && (
            <Badge variant="outline">profile v{receipt.profile_version}</Badge>
          )}
          {receipt.job_apply_url && (
            <a
              href={safeUrl(receipt.job_apply_url)}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-primary hover:underline"
            >
              The ad <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
        {receipt.note && <p className="mt-3 text-sm italic text-muted-foreground">“{receipt.note}”</p>}
      </header>

      <Section title="CV you sent" origin={receipt.cv_origin} body={receipt.cv_text} testId="receipt-cv" />
      <Section
        title="Cover letter you sent"
        origin={receipt.cover_letter_origin}
        body={receipt.cover_letter_text}
        testId="receipt-cover-letter"
      />
      <Section title="The ad, as it read that day" body={receipt.job_description} testId="receipt-ad" />
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
  return (
    <section className="glass-card mb-6 rounded-2xl p-6" data-testid={testId}>
      <div className="mb-3 flex flex-wrap items-baseline justify-between gap-2">
        <h2 className="font-heading text-sm font-semibold uppercase tracking-wider text-primary/80">
          {title}
        </h2>
        {origin && (
          <span className="text-xs text-muted-foreground">{ORIGIN_LABEL[origin] ?? origin}</span>
        )}
      </div>
      {body ? (
        <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-foreground/90">
          {body}
        </pre>
      ) : (
        <p className="text-sm text-muted-foreground">
          Nothing was tailored in Job360 for this one — you sent your own file.
        </p>
      )}
    </section>
  );
}
