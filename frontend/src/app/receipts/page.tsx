"use client";

import { Suspense, useEffect, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { FileCheck2, FileText, Mail, ClipboardPaste } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/ui/empty-state";
import { listReceipts } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { ReceiptSummary } from "@/lib/types";

function sentOn(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString("en-GB", {
    day: "numeric",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  });
}

/**
 * Receipts — "what did I send X?"
 *
 * One row per application, newest first. Filter by `?job_id=` when arriving
 * from a job page. Read-only by design: receipts are append-only on the
 * backend, so there is no edit or delete here either.
 */
function ReceiptsList() {
  const params = useSearchParams();
  const jobIdParam = params.get("job_id");
  const jobId = jobIdParam ? Number(jobIdParam) : undefined;

  const [rows, setRows] = useState<ReceiptSummary[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    listReceipts(Number.isFinite(jobId) ? jobId : undefined)
      .then((res) => {
        if (!cancelled) setRows(res.receipts);
      })
      .catch((err) => {
        if (!cancelled) {
          setRows([]);
          toast.apiError(err, "Couldn't load your receipts.");
        }
      });
    return () => {
      cancelled = true;
    };
  }, [jobId]);

  if (rows === null) {
    return (
      <div className="space-y-3" aria-busy="true">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-20 w-full rounded-xl" />
        ))}
      </div>
    );
  }

  if (rows.length === 0) {
    return (
      <EmptyState
        icon={<FileCheck2 className="h-8 w-8" />}
        title="No receipts yet"
        description='When you click "I applied" on a job, the ad and the CV you sent are frozen here.'
        action={
          <Link href="/bring">
            <Button className="gap-2">
              <ClipboardPaste className="h-4 w-4" />
              Bring a job
            </Button>
          </Link>
        }
      />
    );
  }

  return (
    <ul className="space-y-3" data-testid="receipts-list">
      {rows.map((r) => (
        <li key={r.id}>
          <Link
            href={`/receipts/${r.id}`}
            className="glass-card block rounded-xl p-4 transition-colors hover:border-primary/30"
          >
            <div className="flex flex-wrap items-baseline justify-between gap-2">
              <div>
                <p className="font-medium">{r.job_title}</p>
                <p className="text-sm text-muted-foreground">
                  {r.job_company}
                  {r.job_location ? ` · ${r.job_location}` : ""}
                </p>
              </div>
              <p className="text-xs text-muted-foreground">Sent {sentOn(r.sent_at)}</p>
            </div>
            <div className="mt-2 flex flex-wrap gap-3 text-xs text-muted-foreground">
              <span className="inline-flex items-center gap-1">
                <FileText className="h-3.5 w-3.5" />
                {r.has_cv ? "CV kept" : "No tailored CV"}
              </span>
              <span className="inline-flex items-center gap-1">
                <Mail className="h-3.5 w-3.5" />
                {r.has_cover_letter ? "Cover letter kept" : "No cover letter"}
              </span>
              {r.channel && <span>via {r.channel}</span>}
              {r.note && <span className="italic">“{r.note}”</span>}
            </div>
          </Link>
        </li>
      ))}
    </ul>
  );
}

export default function ReceiptsPage() {
  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Receipts</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Every application, exactly as you sent it. These never change.
        </p>
      </div>
      <Suspense fallback={<Skeleton className="h-20 w-full rounded-xl" />}>
        <ReceiptsList />
      </Suspense>
    </div>
  );
}
