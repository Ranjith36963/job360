"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { User, CheckCircle, AlertCircle, History } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { CVUpload } from "@/components/profile/CVUpload";
import { PreferencesForm } from "@/components/profile/PreferencesForm";
import { VersionHistoryDrawer } from "@/components/profile/VersionHistoryDrawer";
import { JsonResumeExportButton } from "@/components/profile/JsonResumeExportButton";
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
  const [historyOpen, setHistoryOpen] = useState(false);

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
        toast.success("CV uploaded and parsed");
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to upload CV";
        setError(msg);
        toast.error(msg);
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
        toast.success("LinkedIn profile enriched");
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to upload LinkedIn data";
        setError(msg);
        toast.error(msg);
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
        toast.success("GitHub profile enriched");
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to enrich GitHub";
        setError(msg);
        toast.error(msg);
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
        toast.success("Preferences saved");
      } catch (err: unknown) {
        const msg = err instanceof Error ? err.message : "Failed to save preferences";
        setError(msg);
        toast.error(msg);
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

            {/* Actions row */}
            <div className="flex flex-wrap items-center gap-2">
              <JsonResumeExportButton />
              <Button
                variant="outline"
                size="sm"
                className="gap-1.5"
                onClick={() => setHistoryOpen(true)}
              >
                <History className="h-3.5 w-3.5" />
                History
              </Button>
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
          /* ── Main content ──────────────────────────── */
          <div className="space-y-6">
            {/* Two-column layout */}
            <div className="grid gap-6 lg:grid-cols-[1fr_1.2fr]">
              {/* Left column: CV + Enrichment */}
              <CVUpload
                onUpload={handleCVUpload}
                onLinkedinUpload={handleLinkedinUpload}
                onGithubEnrich={handleGithubEnrich}
                profile={profile?.summary ?? null}
                cvDetail={profile?.cv_detail ?? null}
                loading={loadingProfile}
              />

              {/* Right column: Preferences */}
              <PreferencesForm
                preferences={profile?.preferences ?? {}}
                onSave={handleSavePreferences}
                loading={loadingProfile}
              />
            </div>

            {/* ── Skill Tiers ──────────────────────────── */}
            {profile?.skill_tiers && (
              <div className="animate-fade-in-up glass-card rounded-xl p-6">
                <h2 className="font-heading text-base font-semibold mb-4 text-foreground">
                  Skill Tiers
                </h2>
                <div className="grid gap-4 sm:grid-cols-3">
                  {/* Primary */}
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-score-high">
                      Primary
                    </p>
                    {profile.skill_tiers.primary.length === 0 ? (
                      <p className="text-xs text-muted-foreground">None</p>
                    ) : (
                      <ul className="flex flex-wrap gap-1.5">
                        {profile.skill_tiers.primary.map((skill) => (
                          <li
                            key={skill}
                            className="rounded-full bg-score-high/10 px-2.5 py-0.5 text-xs font-medium text-score-high"
                          >
                            {skill}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {/* Secondary */}
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-yellow-500">
                      Secondary
                    </p>
                    {profile.skill_tiers.secondary.length === 0 ? (
                      <p className="text-xs text-muted-foreground">None</p>
                    ) : (
                      <ul className="flex flex-wrap gap-1.5">
                        {profile.skill_tiers.secondary.map((skill) => (
                          <li
                            key={skill}
                            className="rounded-full bg-yellow-500/10 px-2.5 py-0.5 text-xs font-medium text-yellow-500"
                          >
                            {skill}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>

                  {/* Tertiary */}
                  <div>
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                      Tertiary
                    </p>
                    {profile.skill_tiers.tertiary.length === 0 ? (
                      <p className="text-xs text-muted-foreground">None</p>
                    ) : (
                      <ul className="flex flex-wrap gap-1.5">
                        {profile.skill_tiers.tertiary.map((skill) => (
                          <li
                            key={skill}
                            className="rounded-full bg-muted px-2.5 py-0.5 text-xs font-medium text-muted-foreground"
                          >
                            {skill}
                          </li>
                        ))}
                      </ul>
                    )}
                  </div>
                </div>
              </div>
            )}

            {/* ── Skill ESCO Mappings ───────────────────── */}
            {profile?.skill_esco &&
              Object.keys(profile.skill_esco).length > 0 && (
                <div className="animate-fade-in-up glass-card rounded-xl p-6">
                  <h2 className="font-heading text-base font-semibold mb-1 text-foreground">
                    Skill Mappings
                  </h2>
                  <p className="mb-4 text-xs text-muted-foreground">
                    Raw skills extracted from your CV mapped to canonical ESCO
                    identifiers.
                  </p>
                  <ul className="grid gap-1.5 sm:grid-cols-2 lg:grid-cols-3">
                    {Object.entries(profile.skill_esco).map(
                      ([raw, canonical]) => (
                        <li
                          key={raw}
                          className="flex items-center gap-2 rounded-lg border border-border bg-muted/20 px-3 py-2 text-xs"
                        >
                          <span className="min-w-0 flex-1 truncate text-muted-foreground">
                            {raw}
                          </span>
                          <span className="shrink-0 text-muted-foreground/50">
                            →
                          </span>
                          <span className="min-w-0 flex-1 truncate font-medium text-foreground">
                            {canonical}
                          </span>
                        </li>
                      )
                    )}
                  </ul>
                </div>
              )}
          </div>
        )}

      </div>

      {/* ── Version History Drawer ────────────────────── */}
      <VersionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onRestore={fetchProfile}
      />
    </div>
  );
}
