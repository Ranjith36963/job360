"use client";

import { useState } from "react";
import Link from "next/link";
import { FileCheck2, Loader2 } from "lucide-react";
import posthog from "posthog-js";
import { Button } from "@/components/ui/button";
import { createReceipt } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { JobResponse } from "@/lib/types";

interface ReceiptButtonProps {
  job: Pick<JobResponse, "id" | "title" | "company" | "action">;
  /** Called with the new receipt id so the page can flip its own state. */
  onApplied?: (receiptId: number) => void;
  className?: string;
}

/**
 * "I applied" — the moment that matters after the click.
 *
 * Freezes an application receipt (the ad as it read, the CV and cover letter
 * as they stood) and marks the job applied in the pipeline. Append-only on the
 * backend: a second click is a re-application and gets its own receipt, so the
 * button never turns into a toggle.
 *
 * Distinct from `ApplyButton`, which opens the ad's link and pre-creates the
 * pipeline row. That is "I'm going to apply"; this is "I did".
 */
export function ReceiptButton({ job, onApplied, className = "" }: ReceiptButtonProps) {
  const [loading, setLoading] = useState(false);
  const [receiptId, setReceiptId] = useState<number | null>(null);
  const applied = job.action === "applied" || receiptId !== null;

  async function handleClick() {
    if (loading) return;
    setLoading(true);
    try {
      const receipt = await createReceipt(job.id, { channel: "web" });
      setReceiptId(receipt.id);
      onApplied?.(receipt.id);
      posthog.capture("receipt_created", {
        job_id: job.id,
        has_cv: Boolean(receipt.cv_text),
        has_cover_letter: Boolean(receipt.cover_letter_text),
      });
      toast.success(`Receipt kept for ${job.company}. Nothing here changes later.`);
    } catch (err) {
      toast.apiError(err, "Couldn't save the receipt — please try again.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className={`flex flex-col gap-2 ${className}`}>
      <Button
        variant={applied ? "outline" : "default"}
        className={`w-full gap-2 ${
          applied
            ? "border-emerald-500/30 text-emerald-400 hover:bg-emerald-500/10"
            : "bg-emerald-600 text-white hover:bg-emerald-600/90"
        }`}
        onClick={handleClick}
        disabled={loading}
        aria-label={
          applied
            ? `Applied again to ${job.title} at ${job.company}? Keep another receipt`
            : `I applied to ${job.title} at ${job.company} — keep the receipt`
        }
      >
        {loading ? (
          <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        ) : (
          <FileCheck2 className="h-4 w-4" aria-hidden="true" />
        )}
        {applied ? "Applied again? Keep another receipt" : "I applied — keep the receipt"}
      </Button>
      {applied && (
        <Link
          href={receiptId !== null ? `/receipts/${receiptId}` : `/receipts?job_id=${job.id}`}
          className="text-center text-xs text-muted-foreground underline-offset-4 hover:text-primary hover:underline"
        >
          What did I send? View the receipt
        </Link>
      )}
    </div>
  );
}
