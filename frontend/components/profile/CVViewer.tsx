"use client";

import { useState } from "react";
import {
  FileText,
  ChevronDown,
  ChevronUp,
  Briefcase,
  GraduationCap,
  Award,
  Wrench,
  User,
  Building,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { CVDetail } from "@/lib/types";

interface CVViewerProps {
  cv: CVDetail;
}

/** Highlight extracted terms within the full CV text. */
function highlightText(
  text: string,
  terms: string[],
  className: string
): React.ReactNode[] {
  if (!terms.length) return [text];

  // Escape regex specials and sort longest-first for greedy matching
  const escaped = terms
    .filter((t) => t.length > 2)
    .map((t) => t.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
    .sort((a, b) => b.length - a.length);

  if (!escaped.length) return [text];

  const pattern = new RegExp(`(${escaped.join("|")})`, "gi");
  const parts = text.split(pattern);

  return parts.map((part, i) => {
    const isMatch = terms.some(
      (t) => t.toLowerCase() === part.toLowerCase()
    );
    if (isMatch) {
      return (
        <mark key={i} className={className}>
          {part}
        </mark>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

export function CVViewer({ cv }: CVViewerProps) {
  const [showFullCV, setShowFullCV] = useState(false);

  // All terms to highlight (combine skills + titles for unified highlighting)
  const allHighlightTerms = [
    ...cv.skills,
    ...cv.job_titles,
  ];

  return (
    <div className="space-y-6 animate-fade-in-up stagger-2">
      {/* ── Extracted sections ────────────────────────── */}
      <div className="glass-card rounded-xl p-6">
        <h3 className="font-heading text-base font-semibold mb-4 flex items-center gap-2">
          <FileText className="h-4 w-4 text-primary" />
          What we extracted from your CV
        </h3>

        {/* Professional Summary */}
        {cv.summary_text && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <User className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Professional Summary
              </span>
            </div>
            <p className="text-sm text-foreground/90 leading-relaxed pl-5 border-l-2 border-primary/20">
              {cv.summary_text}
            </p>
          </div>
        )}

        {/* Skills */}
        {cv.skills.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <Wrench className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Skills Extracted ({cv.skills.length})
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5 pl-5">
              {cv.skills.map((skill) => (
                <Badge
                  key={skill}
                  variant="secondary"
                  className="text-xs skill-matched"
                >
                  {skill}
                </Badge>
              ))}
            </div>
          </div>
        )}

        {/* Job Titles / Experience */}
        {cv.job_titles.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <Briefcase className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Experience Found
              </span>
            </div>
            <div className="flex flex-wrap gap-1.5 pl-5">
              {cv.job_titles.map((title) => (
                <span
                  key={title}
                  className="inline-flex items-center rounded-md bg-primary/10 border border-primary/20 px-2.5 py-1 text-xs font-medium text-primary"
                >
                  <Building className="mr-1.5 h-3 w-3" />
                  {title}
                </span>
              ))}
            </div>
          </div>
        )}

        {/* Education */}
        {cv.education.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <GraduationCap className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Education
              </span>
            </div>
            <ul className="space-y-1 pl-5 text-sm text-foreground/80">
              {cv.education.map((line, i) => (
                <li key={i} className="leading-relaxed">
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Certifications */}
        {cv.certifications.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <Award className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Certifications
              </span>
            </div>
            <ul className="space-y-1 pl-5 text-sm text-foreground/80">
              {cv.certifications.map((cert, i) => (
                <li key={i} className="leading-relaxed">
                  {cert}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* ── Full CV with highlights toggle ────────────── */}
      <div className="glass-card rounded-xl p-6">
        <button
          onClick={() => setShowFullCV(!showFullCV)}
          className="w-full flex items-center justify-between gap-2 group"
        >
          <div className="flex items-center gap-2">
            <FileText className="h-4 w-4 text-primary" />
            <h3 className="font-heading text-base font-semibold">
              Full CV Text
            </h3>
            <span className="text-xs text-muted-foreground">
              (extracted skills are{" "}
              <mark className="bg-primary/20 text-primary px-1 rounded text-xs">
                highlighted
              </mark>
              )
            </span>
          </div>
          {showFullCV ? (
            <ChevronUp className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors" />
          ) : (
            <ChevronDown className="h-4 w-4 text-muted-foreground group-hover:text-foreground transition-colors" />
          )}
        </button>

        {showFullCV && (
          <div className="mt-4 rounded-lg bg-muted/30 border border-border/50 p-4 max-h-[600px] overflow-y-auto">
            <pre className="whitespace-pre-wrap font-sans text-sm leading-relaxed text-foreground/85">
              {highlightText(
                cv.raw_text,
                allHighlightTerms,
                "bg-primary/20 text-primary rounded px-0.5"
              )}
            </pre>
          </div>
        )}
      </div>
    </div>
  );
}
