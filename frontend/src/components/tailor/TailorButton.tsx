"use client";

import { useState } from "react";
import { FileText } from "lucide-react";
import { Button } from "@/components/ui/button";
import { TailorPanel } from "./TailorPanel";

interface TailorButtonProps {
  jobId: number;
  variant?: "default" | "outline" | "secondary" | "ghost" | "destructive" | "link";
  size?: "sm" | "default" | "lg";
  className?: string;
  fullWidth?: boolean;
}

/**
 * "Tailor my CV" button — opens the TailorPanel dialog for a given job.
 * Same prop shape as ApplyButton (variant/size/className/fullWidth) so it
 * drops into the job page (fullWidth) and the compact job card alike.
 */
export function TailorButton({
  jobId,
  variant = "outline",
  size = "default",
  className = "",
  fullWidth = false,
}: TailorButtonProps) {
  const [open, setOpen] = useState(false);

  return (
    <>
      <Button
        variant={variant}
        size={size}
        className={`gap-1.5 ${fullWidth ? "w-full" : ""} ${className}`}
        onClick={(e) => {
          e.stopPropagation();
          setOpen(true);
        }}
        aria-label="Tailor my CV for this job"
      >
        <FileText className="h-4 w-4" aria-hidden="true" />
        Tailor my CV
      </Button>
      <TailorPanel jobId={jobId} open={open} onOpenChange={setOpen} />
    </>
  );
}
