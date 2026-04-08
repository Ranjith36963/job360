"use client";

import { useCallback, useRef, useState } from "react";
import {
  Upload,
  FileText,
  CheckCircle,
  GitBranch,
  Link2,
  Briefcase,
  AlertCircle,
  Wrench,
  GraduationCap,
  Award,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { ProfileSummary, CVDetail } from "@/lib/types";

interface CVUploadProps {
  onUpload: (file: File) => Promise<void>;
  onLinkedinUpload?: (file: File) => Promise<void>;
  onGithubEnrich?: (username: string) => Promise<void>;
  profile: ProfileSummary | null;
  cvDetail?: CVDetail | null;
  loading: boolean;
}

// ── Highlight engine ──────────────────────────────────────
// Marks extracted terms inline within the full CV text using
// neon-colored backgrounds by category.

interface HighlightTerm {
  text: string;
  category: "skill" | "title" | "education" | "certification";
}

const CATEGORY_STYLES: Record<string, string> = {
  skill: "bg-primary/25 text-primary border-b-2 border-primary/60 rounded-sm px-0.5",
  title: "bg-blue-500/25 text-blue-300 border-b-2 border-blue-400/60 rounded-sm px-0.5",
  education: "bg-amber-500/20 text-amber-300 border-b-2 border-amber-400/50 rounded-sm px-0.5",
  certification: "bg-purple-500/20 text-purple-300 border-b-2 border-purple-400/50 rounded-sm px-0.5",
};

function buildHighlightedCV(
  rawText: string,
  terms: HighlightTerm[]
): React.ReactNode[] {
  if (!terms.length) return [rawText];

  // Sort longest-first so "Machine Learning" matches before "Machine"
  const sorted = [...terms]
    .filter((t) => t.text.length > 2)
    .sort((a, b) => b.text.length - a.text.length);

  // Build regex from all terms
  const escaped = sorted.map((t) => ({
    pattern: t.text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"),
    category: t.category,
  }));

  if (!escaped.length) return [rawText];

  const regex = new RegExp(
    `(${escaped.map((e) => e.pattern).join("|")})`,
    "gi"
  );
  const parts = rawText.split(regex);

  return parts.map((part, i) => {
    const match = sorted.find(
      (t) => t.text.toLowerCase() === part.toLowerCase()
    );
    if (match) {
      return (
        <mark key={i} className={CATEGORY_STYLES[match.category]}>
          {part}
        </mark>
      );
    }
    return <span key={i}>{part}</span>;
  });
}

// ── Component ─────────────────────────────────────────────

export function CVUpload({
  onUpload,
  onLinkedinUpload,
  onGithubEnrich,
  profile,
  cvDetail,
  loading,
}: CVUploadProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const linkedinInputRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [githubUsername, setGithubUsername] = useState("");
  const [githubLoading, setGithubLoading] = useState(false);
  const [linkedinLoading, setLinkedinLoading] = useState(false);

  const hasCV = profile && profile.cv_length > 0;

  const handleDragOver = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(true);
  }, []);

  const handleDragLeave = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragging(false);
  }, []);

  const handleDrop = useCallback(
    async (e: React.DragEvent) => {
      e.preventDefault();
      e.stopPropagation();
      setDragging(false);

      const file = e.dataTransfer.files[0];
      if (!file) return;

      const ext = file.name.toLowerCase();
      if (!ext.endsWith(".pdf") && !ext.endsWith(".docx")) return;

      setUploading(true);
      try {
        await onUpload(file);
      } finally {
        setUploading(false);
      }
    },
    [onUpload]
  );

  const handleFileSelect = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file) return;

      setUploading(true);
      try {
        await onUpload(file);
      } finally {
        setUploading(false);
        if (fileInputRef.current) fileInputRef.current.value = "";
      }
    },
    [onUpload]
  );

  const handleLinkedinUpload = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      if (!file || !onLinkedinUpload) return;

      setLinkedinLoading(true);
      try {
        await onLinkedinUpload(file);
      } finally {
        setLinkedinLoading(false);
        if (linkedinInputRef.current) linkedinInputRef.current.value = "";
      }
    },
    [onLinkedinUpload]
  );

  const handleGithubEnrich = useCallback(async () => {
    if (!githubUsername.trim() || !onGithubEnrich) return;
    setGithubLoading(true);
    try {
      await onGithubEnrich(githubUsername.trim());
      setGithubUsername("");
    } finally {
      setGithubLoading(false);
    }
  }, [githubUsername, onGithubEnrich]);

  // Build highlight terms from CV detail
  const highlightTerms: HighlightTerm[] = [];
  if (cvDetail) {
    for (const skill of cvDetail.skills) {
      highlightTerms.push({ text: skill, category: "skill" });
    }
    for (const title of cvDetail.job_titles) {
      highlightTerms.push({ text: title, category: "title" });
    }
    for (const edu of cvDetail.education) {
      // Only highlight degree lines, not bullet points
      if (edu.length > 10 && !edu.startsWith("•") && !edu.startsWith("�")) {
        highlightTerms.push({ text: edu, category: "education" });
      }
    }
    for (const cert of cvDetail.certifications) {
      if (cert.length > 10) {
        highlightTerms.push({ text: cert, category: "certification" });
      }
    }
  }

  return (
    <div className="space-y-6">
      {/* ── CV Upload Section ───────────────────────────── */}
      <div className="glass-card rounded-xl p-6 animate-fade-in-up stagger-1">
        <div className="flex items-center gap-3 mb-4">
          <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
            <FileText className="h-5 w-5 text-primary" />
          </div>
          <div>
            <h3 className="font-heading text-base font-semibold">
              {hasCV ? "CV Uploaded" : "Upload CV"}
            </h3>
            <p className="text-xs text-muted-foreground">
              {hasCV
                ? "Your CV has been parsed — highlighted text drives your job matching"
                : "PDF or DOCX — we extract skills, titles, and more"}
            </p>
          </div>
          {hasCV && (
            <CheckCircle className="ml-auto h-5 w-5 text-score-high" />
          )}
        </div>

        {/* Drop zone (when no CV or re-uploading) */}
        {!hasCV || uploading ? (
          <div
            onDragOver={handleDragOver}
            onDragLeave={handleDragLeave}
            onDrop={handleDrop}
            onClick={() => !uploading && fileInputRef.current?.click()}
            className={`cursor-pointer border-2 border-dashed rounded-xl p-8 text-center transition-colors ${
              dragging
                ? "border-primary/60 bg-primary/5"
                : "border-primary/20 hover:border-primary/40"
            } ${uploading ? "pointer-events-none opacity-60" : ""}`}
          >
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              onChange={handleFileSelect}
              className="hidden"
            />
            {uploading || loading ? (
              <div className="flex flex-col items-center gap-3">
                <div className="h-10 w-10 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
                <p className="text-sm text-muted-foreground">
                  Parsing your CV...
                </p>
              </div>
            ) : (
              <div className="flex flex-col items-center gap-3">
                <div className="flex h-14 w-14 items-center justify-center rounded-2xl bg-primary/10 ring-1 ring-primary/20">
                  <Upload className="h-7 w-7 text-primary" />
                </div>
                <div>
                  <p className="text-sm font-medium text-foreground">
                    Drop your CV here or click to browse
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    Supports PDF and DOCX files
                  </p>
                </div>
              </div>
            )}
          </div>
        ) : (
          /* ── CV parsed: stats bar + full CV with highlights ── */
          <div className="space-y-4">
            {/* Compact stats bar */}
            <div className="flex flex-wrap items-center gap-3">
              <div className="flex items-center gap-1.5">
                <Wrench className="h-3.5 w-3.5 text-primary" />
                <span className="text-xs text-muted-foreground">Skills:</span>
                <Badge variant="secondary" className="font-mono text-xs">
                  {profile.skills_count}
                </Badge>
              </div>
              <div className="flex items-center gap-1.5">
                <Briefcase className="h-3.5 w-3.5 text-blue-400" />
                <span className="text-xs text-muted-foreground">Roles:</span>
                <Badge variant="secondary" className="font-mono text-xs">
                  {profile.job_titles.length}
                </Badge>
              </div>
              <div className="flex items-center gap-1.5">
                <GraduationCap className="h-3.5 w-3.5 text-amber-400" />
                <span className="text-xs text-muted-foreground">Education:</span>
                <Badge variant="secondary" className="font-mono text-xs">
                  {profile.education.length}
                </Badge>
              </div>
              {cvDetail && cvDetail.certifications.length > 0 && (
                <div className="flex items-center gap-1.5">
                  <Award className="h-3.5 w-3.5 text-purple-400" />
                  <span className="text-xs text-muted-foreground">Certs:</span>
                  <Badge variant="secondary" className="font-mono text-xs">
                    {cvDetail.certifications.length}
                  </Badge>
                </div>
              )}
            </div>

            {/* Full CV text with inline highlights */}
            {cvDetail && cvDetail.raw_text && (
              <div className="rounded-lg bg-muted/20 border border-border/40 p-4 max-h-[500px] overflow-y-auto">
                <pre className="whitespace-pre-wrap font-sans text-[13px] leading-relaxed text-foreground/85">
                  {buildHighlightedCV(cvDetail.raw_text, highlightTerms)}
                </pre>
              </div>
            )}

            {/* Legend */}
            <div className="flex flex-wrap items-center gap-3 text-[11px] text-muted-foreground">
              <span className="font-medium">Highlighted = extracted for job matching:</span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-2 rounded-sm bg-primary/30 border-b-2 border-primary/60" />
                Skills
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-2 rounded-sm bg-blue-500/30 border-b-2 border-blue-400/60" />
                Roles
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-2 rounded-sm bg-amber-500/25 border-b-2 border-amber-400/50" />
                Education
              </span>
              <span className="flex items-center gap-1">
                <span className="inline-block w-3 h-2 rounded-sm bg-purple-500/25 border-b-2 border-purple-400/50" />
                Certifications
              </span>
            </div>

            {/* Re-upload button */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || loading}
              className="gap-2"
            >
              <Upload className="h-3.5 w-3.5" />
              Re-upload CV
            </Button>
            <input
              ref={fileInputRef}
              type="file"
              accept=".pdf,.docx"
              onChange={handleFileSelect}
              className="hidden"
            />
          </div>
        )}
      </div>

      {/* ── Enrichment Section ──────────────────────────── */}
      <div className="glass-card rounded-xl p-6 animate-fade-in-up stagger-2">
        <h3 className="font-heading text-base font-semibold mb-1">
          Enrich Your Profile
        </h3>
        <p className="text-xs text-muted-foreground mb-4">
          Optional: add LinkedIn and GitHub data for better matching
        </p>

        {/* LinkedIn */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#0A66C2]/10 ring-1 ring-[#0A66C2]/20">
              <Link2 className="h-4 w-4 text-[#0A66C2]" />
            </div>
            <div className="flex-1 min-w-0">
              <Label className="text-sm font-medium">LinkedIn Export</Label>
              <p className="text-xs text-muted-foreground">
                Upload your LinkedIn data ZIP
              </p>
            </div>
            {profile?.has_linkedin && (
              <CheckCircle className="h-4 w-4 text-score-high shrink-0" />
            )}
          </div>
          <div>
            <input
              ref={linkedinInputRef}
              type="file"
              accept=".zip"
              onChange={handleLinkedinUpload}
              className="hidden"
            />
            <Button
              variant="outline"
              size="sm"
              onClick={() => linkedinInputRef.current?.click()}
              disabled={linkedinLoading}
              className="gap-2 w-full"
            >
              {linkedinLoading ? (
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
              ) : (
                <Upload className="h-3.5 w-3.5" />
              )}
              {profile?.has_linkedin ? "Re-enrich" : "Enrich"} LinkedIn
            </Button>
          </div>
        </div>

        <Separator className="my-4" />

        {/* GitHub */}
        <div className="space-y-3">
          <div className="flex items-center gap-2">
            <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-[#8B5CF6]/10 ring-1 ring-[#8B5CF6]/20">
              <GitBranch className="h-4 w-4 text-[#8B5CF6]" />
            </div>
            <div className="flex-1 min-w-0">
              <Label className="text-sm font-medium">GitHub Profile</Label>
              <p className="text-xs text-muted-foreground">
                Enrich with public repos and languages
              </p>
            </div>
            {profile?.has_github && (
              <CheckCircle className="h-4 w-4 text-score-high shrink-0" />
            )}
          </div>
          <div className="flex gap-2">
            <Input
              placeholder="github-username"
              value={githubUsername}
              onChange={(e) => setGithubUsername(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  handleGithubEnrich();
                }
              }}
              className="flex-1"
            />
            <Button
              variant="outline"
              size="default"
              onClick={handleGithubEnrich}
              disabled={githubLoading || !githubUsername.trim()}
              className="gap-2 shrink-0"
            >
              {githubLoading ? (
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-primary/30 border-t-primary" />
              ) : (
                <GitBranch className="h-3.5 w-3.5" />
              )}
              Enrich GitHub
            </Button>
          </div>
        </div>

        {/* Hint if neither enrichment */}
        {profile && !profile.has_linkedin && !profile.has_github && (
          <div className="mt-4 flex items-start gap-2 rounded-lg bg-primary/5 border border-primary/10 p-3">
            <AlertCircle className="h-4 w-4 text-primary shrink-0 mt-0.5" />
            <p className="text-xs text-muted-foreground">
              Adding LinkedIn or GitHub data improves skill detection and
              semantic matching accuracy.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
