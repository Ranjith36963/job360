"use client";

import { useCallback, useEffect, useState } from "react";
import { User, CheckCircle, AlertCircle } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { CVUpload } from "@/components/profile/CVUpload";
import { CVViewer } from "@/components/profile/CVViewer";
import { PreferencesForm } from "@/components/profile/PreferencesForm";
import {
  getProfile,
  uploadProfile,
  uploadLinkedin,
  uploadGithub,
} from "@/lib/api";
import type { ProfileResponse, PreferencesRequest } from "@/lib/types";

// ── Completeness calculation ────────────────────────────────

function calcCompleteness(profile: ProfileResponse | null): {
  percent: number;
  label: string;
} {
  if (!profile) return { percent: 0, label: "No profile" };

  const { summary, preferences } = profile;
  let score = 0;

  // Has CV: 40%
  if (summary.cv_length > 0) score += 40;

  // Has job titles: 15%
  const prefTitles = Array.isArray(
    (preferences as Record<string, unknown>)?.target_job_titles
  )
    ? ((preferences as Record<string, unknown>).target_job_titles as string[])
    : [];
  if (summary.job_titles.length > 0 || prefTitles.length > 0) score += 15;

  // Has skills: 15%
  const prefSkills = Array.isArray(
    (preferences as Record<string, unknown>)?.additional_skills
  )
    ? ((preferences as Record<string, unknown>).additional_skills as string[])
    : [];
  if (summary.skills_count > 0 || prefSkills.length > 0) score += 15;

  // Has preferences (at least work arrangement or experience or about_me): 15%
  const prefs = preferences as Record<string, unknown>;
  const hasPrefs =
    (prefs?.work_arrangement && prefs.work_arrangement !== "any") ||
    (prefs?.experience_level && prefs.experience_level !== "") ||
    (typeof prefs?.about_me === "string" && prefs.about_me.length > 0) ||
    prefTitles.length > 0;
  if (hasPrefs) score += 15;

  // Has LinkedIn: 7.5%
  if (summary.has_linkedin) score += 7.5;

  // Has GitHub: 7.5%
  if (summary.has_github) score += 7.5;

  const percent = Math.round(score);

  let label = "Getting started";
  if (percent >= 100) label = "Complete";
  else if (percent >= 70) label = "Almost there";
  else if (percent >= 40) label = "Good progress";

  return { percent, label };
}

// ── Page component ──────────────────────────────────────────

export default function ProfilePage() {
  const [profile, setProfile] = useState<ProfileResponse | null>(null);
  const [loadingProfile, setLoadingProfile] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Fetch profile on mount
  const fetchProfile = useCallback(async () => {
    try {
      setError(null);
      const data = await getProfile();
      setProfile(data);
    } catch (err: unknown) {
      // 404 = no profile yet — that's OK
      if (err instanceof Error && err.message.includes("404")) {
        setProfile(null);
      } else {
        setError(
          err instanceof Error ? err.message : "Failed to load profile"
        );
      }
    } finally {
      setLoadingProfile(false);
    }
  }, []);

  useEffect(() => {
    fetchProfile();
  }, [fetchProfile]);

  // Handlers
  const handleCVUpload = useCallback(
    async (file: File) => {
      setError(null);
      try {
        const data = await uploadProfile(file);
        setProfile(data);
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : "Failed to upload CV"
        );
      }
    },
    []
  );

  const handleLinkedinUpload = useCallback(
    async (file: File) => {
      setError(null);
      try {
        await uploadLinkedin(file);
        await fetchProfile();
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : "Failed to upload LinkedIn data"
        );
      }
    },
    [fetchProfile]
  );

  const handleGithubEnrich = useCallback(
    async (username: string) => {
      setError(null);
      try {
        await uploadGithub(username);
        await fetchProfile();
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : "Failed to enrich GitHub"
        );
      }
    },
    [fetchProfile]
  );

  const handleSavePreferences = useCallback(
    async (prefs: PreferencesRequest) => {
      setError(null);
      try {
        const data = await uploadProfile(null, prefs);
        setProfile(data);
      } catch (err: unknown) {
        setError(
          err instanceof Error ? err.message : "Failed to save preferences"
        );
      }
    },
    []
  );

  const { percent, label } = calcCompleteness(profile);

  return (
    <div className="relative">
      {/* ── Ambient glow ─────────────────────────────── */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 overflow-hidden"
      >
        <div className="absolute -top-[20%] left-1/2 h-[600px] w-[800px] -translate-x-1/2 rounded-full bg-primary/[0.10] blur-[120px]" />
        <div className="absolute top-[30%] -left-[10%] h-[500px] w-[500px] rounded-full bg-primary/[0.07] blur-[100px]" />
        <div className="absolute top-[50%] -right-[10%] h-[400px] w-[400px] rounded-full bg-primary/[0.05] blur-[100px]" />
      </div>

      <div className="relative mx-auto max-w-6xl px-4 py-8 sm:px-6 lg:py-12">
        {/* ── Header + Completeness ───────────────────── */}
        <div className="animate-fade-in-up stagger-1 mb-8">
          <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-3">
              <div className="flex h-11 w-11 items-center justify-center rounded-xl bg-primary/10 ring-1 ring-primary/20">
                <User className="h-5 w-5 text-primary" />
              </div>
              <div>
                <h1 className="font-heading text-2xl font-bold tracking-tight sm:text-3xl">
                  <span className="text-gradient-lime">Profile</span>
                </h1>
                <p className="text-sm text-muted-foreground">
                  {profile?.summary.is_complete
                    ? "Your profile is ready for job matching"
                    : "Upload your CV and set preferences to get started"}
                </p>
              </div>
            </div>

            {/* Completeness badge */}
            <div className="flex items-center gap-3">
              {percent >= 100 ? (
                <CheckCircle className="h-5 w-5 text-score-high" />
              ) : (
                <AlertCircle className="h-5 w-5 text-muted-foreground" />
              )}
              <div className="min-w-[160px]">
                <div className="flex items-baseline justify-between mb-1">
                  <span className="text-xs font-medium text-muted-foreground">
                    {label}
                  </span>
                  <span className="font-mono text-xs font-bold text-foreground">
                    {percent}%
                  </span>
                </div>
                <div className="h-2 w-full rounded-full bg-muted overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-700 ease-out"
                    style={{
                      width: `${percent}%`,
                      background:
                        percent >= 70
                          ? "oklch(0.89 0.29 128)"
                          : percent >= 40
                            ? "oklch(0.78 0.25 130)"
                            : "oklch(0.75 0.15 85)",
                    }}
                  />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* ── Error banner ────────────────────────────── */}
        {error && (
          <div className="animate-fade-in-up mb-6 flex items-start gap-3 rounded-xl border border-destructive/20 bg-destructive/5 p-4">
            <AlertCircle className="h-5 w-5 text-destructive shrink-0 mt-0.5" />
            <div>
              <p className="text-sm font-medium text-destructive">
                Something went wrong
              </p>
              <p className="text-xs text-destructive/80 mt-0.5">{error}</p>
            </div>
          </div>
        )}

        {/* ── Loading skeleton ────────────────────────── */}
        {loadingProfile ? (
          <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
            <div className="space-y-6">
              <div className="glass-card rounded-xl p-6">
                <Skeleton className="h-10 w-40 mb-4" />
                <Skeleton className="h-40 w-full rounded-xl" />
              </div>
              <div className="glass-card rounded-xl p-6">
                <Skeleton className="h-8 w-32 mb-4" />
                <Skeleton className="h-10 w-full mb-3" />
                <Skeleton className="h-10 w-full" />
              </div>
            </div>
            <div className="glass-card rounded-xl p-6">
              <Skeleton className="h-10 w-40 mb-6" />
              {Array.from({ length: 6 }).map((_, i) => (
                <div key={i} className="mb-4">
                  <Skeleton className="h-4 w-24 mb-2" />
                  <Skeleton className="h-8 w-full" />
                </div>
              ))}
            </div>
          </div>
        ) : (
          /* ── Two-column layout ─────────────────────── */
          <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
            {/* Left column: CV + Enrichment */}
            <CVUpload
              onUpload={handleCVUpload}
              onLinkedinUpload={handleLinkedinUpload}
              onGithubEnrich={handleGithubEnrich}
              profile={profile?.summary ?? null}
              loading={loadingProfile}
            />

            {/* Right column: Preferences */}
            <PreferencesForm
              preferences={profile?.preferences ?? {}}
              onSave={handleSavePreferences}
              loading={loadingProfile}
            />
          </div>
        )}

        {/* ── CV Viewer — full CV with highlights ────── */}
        {profile?.cv_detail && (
          <div className="animate-fade-in-up stagger-3 mt-6">
            <CVViewer cv={profile.cv_detail} />
          </div>
        )}
      </div>
    </div>
  );
}
