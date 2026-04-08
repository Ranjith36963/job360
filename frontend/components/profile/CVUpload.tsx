"use client";

import { useCallback, useRef, useState } from "react";
import {
  Upload,
  FileText,
  CheckCircle,
  GitBranch,
  Link2,
  X,
  Briefcase,
  AlertCircle,
  Save,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Separator } from "@/components/ui/separator";
import type { ProfileSummary } from "@/lib/types";

interface CVUploadProps {
  onUpload: (file: File) => Promise<void>;
  onLinkedinUpload?: (file: File) => Promise<void>;
  onGithubEnrich?: (username: string) => Promise<void>;
  profile: ProfileSummary | null;
  loading: boolean;
}

export function CVUpload({
  onUpload,
  onLinkedinUpload,
  onGithubEnrich,
  profile,
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
                ? "Your CV has been parsed and analyzed"
                : "PDF or DOCX — we extract skills, titles, and more"}
            </p>
          </div>
          {hasCV && (
            <CheckCircle className="ml-auto h-5 w-5 text-score-high" />
          )}
        </div>

        {/* Drop zone */}
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
          /* CV parsed results */
          <div className="space-y-4">
            {/* Skills count */}
            <div className="flex items-center gap-2">
              <span className="text-sm text-muted-foreground">
                Skills extracted:
              </span>
              <Badge variant="secondary" className="font-mono">
                {profile.skills_count}
              </Badge>
            </div>

            {/* Job titles */}
            {profile.job_titles.length > 0 && (
              <div>
                <span className="text-sm text-muted-foreground block mb-2">
                  Job titles found:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {profile.job_titles.map((title) => (
                    <span
                      key={title}
                      className="skill-matched inline-flex items-center rounded-md px-2.5 py-1 text-xs font-medium"
                    >
                      <Briefcase className="mr-1.5 h-3 w-3" />
                      {title}
                    </span>
                  ))}
                </div>
              </div>
            )}

            {/* Education */}
            {profile.education.length > 0 && (
              <div>
                <span className="text-sm text-muted-foreground block mb-2">
                  Education:
                </span>
                <div className="flex flex-wrap gap-1.5">
                  {profile.education.map((edu) => (
                    <Badge key={edu} variant="outline" className="text-xs">
                      {edu}
                    </Badge>
                  ))}
                </div>
              </div>
            )}

            {/* Experience level */}
            {profile.experience_level && (
              <div className="flex items-center gap-2">
                <span className="text-sm text-muted-foreground">
                  Experience level:
                </span>
                <Badge variant="secondary" className="capitalize">
                  {profile.experience_level}
                </Badge>
              </div>
            )}

            {/* Re-upload button */}
            <Button
              variant="outline"
              size="sm"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading || loading}
              className="mt-2 gap-2"
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

            {/* Save Profile button */}
            <Button
              className="w-full mt-4 gap-2 bg-primary text-primary-foreground hover:brightness-110"
              onClick={() => {
                /* CV is auto-saved on upload — visual confirmation */
              }}
            >
              <Save className="h-4 w-4" />
              Save Profile
            </Button>
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

        {/* Hint if neither enrichment has been done */}
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
