"use client";

import { useState } from "react";
import { FileText, Mail, Sparkles } from "lucide-react";
import { TailorPanel } from "./TailorPanel";
import type { TailorDocKind } from "@/lib/types";

interface TailorSectionProps {
  jobId: number;
}

/**
 * Prominent "Tailor with AI" section for the job detail page — a wide card that
 * sits below the job description with TWO separate entry points (CV and cover
 * letter), each opening the editor focused on that document. Replaces the small
 * sidebar button (docs/peruser_cv_coverletter.md §3.5).
 */
export function TailorSection({ jobId }: TailorSectionProps) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<TailorDocKind>("cv");

  function openFor(k: TailorDocKind) {
    setKind(k);
    setOpen(true);
  }

  return (
    <div className="glass-card rounded-2xl p-6 space-y-4">
      <div className="space-y-1.5">
        <h2 className="flex items-center gap-2 font-heading text-base font-semibold">
          <Sparkles className="h-4 w-4 text-primary" aria-hidden="true" />
          Tailor my ATS-friendly CV using AI
        </h2>
        <p className="text-sm text-muted-foreground">
          Reshape your existing CV for this exact role — and generate a matching
          cover letter. Every fact comes from your own CV; nothing is invented.
          Review, edit, then download as PDF or DOCX.
        </p>
      </div>

      <div className="grid gap-3 sm:grid-cols-2">
        <button
          type="button"
          onClick={() => openFor("cv")}
          className="group flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/5 p-4 text-left transition-colors hover:bg-primary/10 hover:border-primary/40"
        >
          <FileText className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
          <span>
            <span className="block font-medium">Tailor my CV</span>
            <span className="block text-xs text-muted-foreground">
              An ATS-friendly CV rewritten for this job
            </span>
          </span>
        </button>

        <button
          type="button"
          onClick={() => openFor("cover_letter")}
          className="group flex items-start gap-3 rounded-xl border border-primary/20 bg-primary/5 p-4 text-left transition-colors hover:bg-primary/10 hover:border-primary/40"
        >
          <Mail className="mt-0.5 h-5 w-5 shrink-0 text-primary" aria-hidden="true" />
          <span>
            <span className="block font-medium">Create Cover Letter</span>
            <span className="block text-xs text-muted-foreground">
              A matching cover letter for this role
            </span>
          </span>
        </button>
      </div>

      <TailorPanel
        jobId={jobId}
        open={open}
        onOpenChange={setOpen}
        initialKind={kind}
      />
    </div>
  );
}
