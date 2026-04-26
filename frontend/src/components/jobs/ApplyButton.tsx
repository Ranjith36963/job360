"use client";

import { useState } from "react";
import { ExternalLink, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { createPipelineApplication } from "@/lib/api";
import { toast } from "@/lib/toast";
import type { JobResponse } from "@/lib/types";

interface ApplyButtonProps {
  job: Pick<JobResponse, "id" | "apply_url" | "title" | "company">;
  size?: "sm" | "default" | "lg";
  className?: string;
  fullWidth?: boolean;
}

/**
 * Opens apply_url in new tab AND tracks the application in pipeline.
 * Follows the "setJobAction" pattern — success/failure communicated via toast.
 */
export function ApplyButton({
  job,
  size = "default",
  className = "",
  fullWidth = false,
}: ApplyButtonProps) {
  const [loading, setLoading] = useState(false);

  async function handleApply() {
    // Open link immediately (must happen in synchronous event handler)
    window.open(job.apply_url, "_blank", "noopener,noreferrer");

    // Track in pipeline asynchronously
    setLoading(true);
    try {
      await createPipelineApplication(job.id);
      toast.success(`Applied to ${job.title} — tracking in Pipeline`);
    } catch {
      // Non-blocking — the apply URL already opened
      toast.info("Job opened — add to pipeline manually if needed");
    } finally {
      setLoading(false);
    }
  }

  return (
    <Button
      size={size}
      className={`gap-2 bg-primary text-primary-foreground hover:bg-primary/90 ${fullWidth ? "w-full" : ""} ${className}`}
      onClick={handleApply}
      disabled={loading}
      aria-label={`Apply for ${job.title} at ${job.company}`}
    >
      {loading ? (
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      ) : (
        <ExternalLink className="h-4 w-4" aria-hidden="true" />
      )}
      Apply Now
    </Button>
  );
}
