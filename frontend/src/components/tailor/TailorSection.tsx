"use client";

import Link from "next/link";
import { useState } from "react";
import { Bot, FileDown } from "lucide-react";
import { TailorPanel } from "./TailorPanel";

interface TailorSectionProps {
  jobId: number;
  applicationId: number;
  /** True when at least one CV / cover letter version is already saved. */
  hasDocuments?: boolean;
}

/**
 * "Ask your agent" — decision 28 (2026-09-21, slice A).
 *
 * Job360 has no brain of its own, so this card no longer offers to write a CV.
 * It tells the user the one sentence that makes their own agent do it, links to
 * the connect page, and opens the saved versions for editing and download once
 * the agent has saved one.
 */
export function TailorSection({ jobId, applicationId, hasDocuments = false }: TailorSectionProps) {
  const [open, setOpen] = useState(false);

  return (
    <div className="glass-card rounded-2xl p-6 space-y-4">
      <div className="space-y-1.5">
        <h2 className="flex items-center gap-2 font-heading text-base font-semibold">
          <Bot className="h-4 w-4 text-primary" aria-hidden="true" />
          Ask your agent
        </h2>
        <p className="text-sm text-muted-foreground">
          In Claude, ChatGPT or any MCP client with Job360 connected, say:{" "}
          <span className="font-medium text-foreground">
            &ldquo;write a tailored CV for application {applicationId} and save it&rdquo;
          </span>
          . It writes the document; Job360 keeps every version and renders the PDF
          or DOCX.
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <Link
          href="/settings/connect"
          className="rounded-xl border border-primary/20 bg-primary/5 px-4 py-2 text-sm font-medium transition-colors hover:bg-primary/10 hover:border-primary/40"
        >
          Connect your agent
        </Link>
        {hasDocuments && (
          <button
            type="button"
            onClick={() => setOpen(true)}
            className="inline-flex items-center gap-2 rounded-xl border border-border px-4 py-2 text-sm font-medium text-muted-foreground transition-colors hover:text-foreground"
          >
            <FileDown className="h-4 w-4" aria-hidden="true" />
            Edit &amp; download saved documents
          </button>
        )}
      </div>

      <TailorPanel jobId={jobId} open={open} onOpenChange={setOpen} />
    </div>
  );
}
