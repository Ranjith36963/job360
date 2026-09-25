"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import posthog from "posthog-js";
import { User, AlertCircle, History } from "lucide-react";
import { Skeleton } from "@/components/ui/skeleton";
import { Button } from "@/components/ui/button";
import { CVUpload } from "@/components/profile/CVUpload";
import { CVViewer } from "@/components/profile/CVViewer";
import { PreferencesForm } from "@/components/profile/PreferencesForm";
import { VersionHistoryDrawer } from "@/components/profile/VersionHistoryDrawer";
import { JsonResumeExportButton } from "@/components/profile/JsonResumeExportButton";
import { ClearButton } from "@/components/profile/ClearButton";
import { LessonsList } from "@/components/profile/LessonsList";
import { PageContainer } from "@/components/layout/PageContainer";
import {
  getProfile,
  uploadProfile,
  uploadLinkedin,
  uploadGithub,
  clearProfileSection,
  takeBackProfileEdit,
  keepProfileEdit,
  type ClearSection,
} from "@/lib/api";
import { ApiError, apiErrorMessage } from "@/lib/api-error";
import { countAssistantPreferenceEdits, type AgentEdit } from "@/lib/agent-edits";
import type { CVDetail, ProfileResponse, PreferencesRequest } from "@/lib/types";

/** Human names for the clear toasts — "cv" is not what a person calls it. */
const SECTION_LABEL: Record<ClearSection, string> = {
  cv: "CV",
  linkedin: "LinkedIn",
  github: "GitHub",
  preferences: "Preferences",
  all: "Profile",
};

// ── "What we extracted" gate ────────────────────────────────
//
// cv_detail is ONLY populated when the CV's raw_text is non-empty
// (_build_profile_response in backend/src/api/routes/profile.py). LinkedIn
// and GitHub data arrive on the SAME response independently of the CV, so
// gating the whole CVViewer on `cv_detail` alone hid real, paid-for,
// already-fetched LinkedIn/GitHub content whenever a user connected either
// of those BEFORE uploading a CV — the section just vanished, reading as a
// failed enrichment. The page calls LinkedIn/GitHub "Optional" (rule #29);
// the render must not silently require the one input it never demanded.
//
// CVViewer's own CV-only sections already stay silent on an empty CVDetail
// (every one of them checks its own field), so passing this stand-in when
// there is no CV is safe — it renders nothing extra, it just stops the
// LinkedIn/GitHub sections from being hidden along with it.
const EMPTY_CV_DETAIL: CVDetail = {
  achievements: [],
  certifications: [],
  companies: [],
  cv_experience_level: "",
  cv_industries: [],
  cv_positions: [],
  cv_projects: [],
  cv_right_to_work: "",
  education: [],
  experience_text: "",
  extraction_score: {},
  headline: "",
  highlights: [],
  job_titles: [],
  links: [],
  location: "",
  name: "",
  raw_text: "",
  skills: [],
  summary_text: "",
};

function hasLinkedinShelf(
  sections: ProfileResponse["linkedin_subsections"] | undefined
): boolean {
  if (!sections) return false;
  return Object.values(sections).some(
    (rows) => Array.isArray(rows) && rows.length > 0
  );
}

function hasGithubShelf(
  temporal: ProfileResponse["github_temporal"] | undefined,
  detail: ProfileResponse["github_detail"] | undefined
): boolean {
  const temporalHasContent = Object.values(temporal ?? {}).some(
    (bucket) => bucket && typeof bucket === "object" && Object.keys(bucket).length > 0
  );
  if (temporalHasContent) return true;
  return Object.values(detail ?? {}).some((value) => {
    if (Array.isArray(value)) return value.length > 0;
    if (typeof value === "string") return value.length > 0;
    if (value && typeof value === "object") return Object.keys(value).length > 0;
    return false;
  });
}

// ── What's missing (owner decisions, 2026-09-24 and 2026-09-25) ─────────
//
// The header names what is actually missing — one line, no percentage. Only
// a CV counts as "to finish": preferences are optional and an empty one means
// "don't care" (rule #29), so counting them as missing nagged a seeker into
// inventing constraints. LinkedIn and GitHub are optional extras, named on a
// quieter line ("You can also add: …") and never as unfinished work.

function missingPieces(profile: ProfileResponse | null): string[] {
  const summary = profile?.summary;
  return !summary || summary.cv_length === 0 ? ["a CV"] : [];
}

/** Optional inputs not given yet — the quiet "You can also add" line. */
function optionalExtras(profile: ProfileResponse | null): string[] {
  const summary = profile?.summary;
  if (!summary) return [];
  const extras: string[] = [];
  if (!summary.has_linkedin) extras.push("LinkedIn");
  if (!summary.has_github) extras.push("GitHub");
  return extras;
}

/** The one header line: a missing CV is the only thing "to finish". */
function headerLine(profile: ProfileResponse | null): string {
  const missing = missingPieces(profile);
  if (missing.length === 0) return "Your profile is ready for your assistant";
  return `To finish: ${missing.map((piece) => `add ${piece}`).join(", ")}`;
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
      // L7: 404 = no profile yet — that's OK. Check the typed status instead
      // of string-matching the error message (which could coincidentally
      // contain "404" for an unrelated reason, e.g. a detail mentioning a
      // job id).
      if (err instanceof ApiError && err.isNotFound) {
        setProfile(null);
      } else {
        setError(apiErrorMessage(err, "Failed to load profile"));
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
        posthog.capture("cv_uploaded");
        posthog.capture("extraction_completed", {
          skills_found: data.summary.skills_count,
        });
        toast.success("CV uploaded and parsed");
      } catch (err: unknown) {
        const msg = apiErrorMessage(err, "Failed to upload CV");
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
        const msg = apiErrorMessage(err, "Failed to upload LinkedIn data");
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
        const msg = apiErrorMessage(err, "Failed to enrich GitHub");
        setError(msg);
        toast.error(msg);
      }
    },
    [fetchProfile]
  );

  // One handler for all five clear buttons — the section is the only thing
  // that differs. The response IS the rebuilt profile, so the page updates from
  // it directly rather than re-fetching.
  const handleClear = useCallback(async (section: ClearSection) => {
    setError(null);
    try {
      const data = await clearProfileSection(section);
      setProfile(data);
      toast.success(
        section === "all" ? "Profile cleared" : `${SECTION_LABEL[section]} cleared`,
        { description: "Undo it from History if that was a mistake." }
      );
    } catch (err: unknown) {
      const msg = apiErrorMessage(err, "Failed to clear");
      setError(msg);
      toast.error(msg);
    }
  }, []);

  const handleSavePreferences = useCallback(
    async (prefs: PreferencesRequest) => {
      setError(null);
      try {
        const data = await uploadProfile(null, prefs);
        setProfile(data);
        toast.success("Preferences saved");
      } catch (err: unknown) {
        const msg = apiErrorMessage(err, "Failed to save preferences");
        setError(msg);
        toast.error(msg);
      }
    },
    []
  );

  // "Take back" an assistant's change: the backend appends a clearing row and
  // answers with the rebuilt profile, so the page updates from it directly.
  const handleTakeBack = useCallback(async (path: string) => {
    setError(null);
    try {
      const data = await takeBackProfileEdit(path);
      setProfile(data);
      toast.success("Change taken back");
    } catch (err: unknown) {
      const msg = apiErrorMessage(err, "Failed to take back the change");
      setError(msg);
      toast.error(msg);
    }
  }, []);

  // "Keep" an assistant's change: the human accepts it as their own.
  const handleKeep = useCallback(async (path: string) => {
    setError(null);
    try {
      const data = await keepProfileEdit(path);
      setProfile(data);
      toast.success("Change kept");
    } catch (err: unknown) {
      const msg = apiErrorMessage(err, "Failed to keep the change");
      setError(msg);
      toast.error(msg);
    }
  }, []);

  const oneLineStatus = headerLine(profile);
  const extras = optionalExtras(profile);

  // R11 — the live ASSISTANT edits, each with what the field held before
  // (`previous_value`). Typed by the generated `AgentEditOut`.
  const agentEdits: AgentEdit[] = profile?.agent_edits ?? [];
  const assistantPrefCount = countAssistantPreferenceEdits(agentEdits);

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

      <PageContainer className="relative py-8 lg:py-12">
        {/* ── Header ───────────────────────────────────── */}
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
                {/* Owner decision, 2026-09-24: ONE line, naming what is
                    actually missing (reusing the same six completeness
                    inputs the old %/"Almost there" meter scored) instead of
                    a percentage nobody could act on. */}
                <p className="text-sm text-muted-foreground">{oneLineStatus}</p>
                {/* Optional inputs — a quiet line, never "to finish". */}
                {extras.length > 0 && (
                  <p className="text-xs text-muted-foreground/70">
                    You can also add: {extras.join(", ")}
                  </p>
                )}
                {assistantPrefCount > 0 && (
                  <a
                    href="#preferences"
                    className="block text-xs text-primary underline-offset-2 hover:underline"
                  >
                    {assistantPrefCount === 1
                      ? "1 preference set by your assistant"
                      : `${assistantPrefCount} preferences set by your assistant`}
                  </a>
                )}
              </div>
            </div>

            {/* Actions row — every control here acts ON an existing profile, so
                none of them belong on a first visit. Before this, a brand-new
                account's most prominent buttons were "Export JSON Resume" (of a
                resume that does not exist) and "History" (of a profile that has
                never been saved), sitting ABOVE the one thing a new user should
                do: upload a CV. ClearButton below was already gated on
                `profile`; Export and History simply got missed. */}
            {profile && (
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
              {/* Sits next to History deliberately: History is the undo for it.
                  A profile you can empty in one click is what makes "upload a
                  CV and see exactly what came out" a clean experiment instead
                  of a reading of everything ever uploaded. */}
              <ClearButton
                label="Clear profile"
                confirmLabel="Click again to clear everything"
                onConfirm={() => handleClear("all")}
              />
            </div>
            )}
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
                onClearSection={handleClear}
                profile={profile?.summary ?? null}
                cvDetail={profile?.cv_detail ?? null}
                loading={loadingProfile}
              />

              {/* Right column: Preferences */}
              <PreferencesForm
                preferences={profile?.preferences ?? {}}
                // No suggestions: the stored "skills that go with yours" were
                // our own deleted model's output (decision 28) and are no
                // longer sent. The user's agent can suggest; we remember.
                onSave={handleSavePreferences}
                onClear={profile ? () => handleClear("preferences") : undefined}
                loading={loadingProfile}
                agentEdits={agentEdits}
                onTakeBack={handleTakeBack}
                onKeep={handleKeep}
                showFieldHistory={Boolean(profile)}
              />
            </div>

            {/* ── Lessons (slice 9, #516) — full width, below the grid ───── */}
            <LessonsList />

            {/* ── What we extracted (readable content, not just counts) ──
                The API already returns name/summary/skills/dated work
                history/education/certs/LinkedIn detail/GitHub detail — this
                was the only place none of it was ever rendered. Each
                sub-section inside CVViewer renders only when it has data.
                Gated on ANY of the three shelves having content, not just
                cv_detail — a LinkedIn or GitHub upload with no CV yet must
                still show what it found (see EMPTY_CV_DETAIL above). */}
            {(profile?.cv_detail ||
              hasLinkedinShelf(profile?.linkedin_subsections) ||
              hasGithubShelf(profile?.github_temporal, profile?.github_detail)) && (
              <CVViewer
                cv={profile?.cv_detail ?? EMPTY_CV_DETAIL}
                linkedinSubsections={profile?.linkedin_subsections}
                githubTemporal={profile?.github_temporal}
                githubDetail={profile?.github_detail}
                agentEdits={agentEdits}
                onTakeBack={handleTakeBack}
                onKeep={handleKeep}
              />
            )}

            {/* ── Your Skills (grouped by source) ────────── */}
            {(() => {
              const bySource = profile?.skills_by_source ?? {};
              const GROUPS: {
                key: string;
                label: string;
                chip: string;
              }[] = [
                {
                  key: "cv",
                  label: "From your CV",
                  chip: "bg-score-high/10 text-score-high",
                },
                {
                  key: "linkedin",
                  label: "From LinkedIn",
                  chip: "bg-sky-500/10 text-sky-400",
                },
                {
                  key: "github",
                  label: "From GitHub",
                  chip: "bg-violet-500/10 text-violet-400",
                },
                {
                  key: "preferences",
                  label: "Added by you",
                  chip: "bg-yellow-500/10 text-yellow-500",
                },
              ];
              // THE one skill count, from the backend (skill_tiering.
              // profile_skills) — the same number the CV card, the application
              // page and the user's agent see. Never re-unioned here: a second
              // count computed in the browser is how three numbers appeared.
              const total = profile?.summary?.skills_count ?? 0;
              // Suggestions no longer keep this block alive: they moved into
              // the preferences form. This panel shows skills the user HAS, so
              // with none of those there is nothing to show.
              if (total === 0) return null;

              // The whole point of this panel is telling sources APART. With a
              // CV as the only source there is nothing to tell apart, and
              // CVViewer directly above has already listed exactly these
              // skills — so a CV-only profile printed the same six chips three
              // times down one page (highlighted in the CV preview, again under
              // "What we extracted", again here), costing a full card of height
              // to repeat itself.
              //
              // Only the cv+CVViewer combination is suppressed: skills the user
              // typed themselves can be the sole group while CVViewer is not
              // rendered at all (it is gated on CV/LinkedIn/GitHub content), and
              // that case still needs somewhere to appear.
              const activeGroups = GROUPS.filter(
                (g) => (bySource[g.key] ?? []).length > 0
              );
              const alreadyShownByCVViewer =
                activeGroups.length === 1 &&
                activeGroups[0].key === "cv" &&
                Boolean(profile?.cv_detail);
              if (alreadyShownByCVViewer) return null;
              return (
                <div className="animate-fade-in-up glass-card rounded-xl p-6">
                  <h2 className="font-heading text-base font-semibold mb-1 text-foreground">
                    Your Skills{" "}
                    <span className="text-muted-foreground">({total})</span>
                  </h2>
                  <p className="mb-4 text-xs text-muted-foreground">
                    Everything we found, grouped by where it came from.
                  </p>
                  <div className="space-y-4">
                    {GROUPS.map((g) => {
                      const items = bySource[g.key] ?? [];
                      if (items.length === 0) return null;
                      return (
                        <div key={g.key}>
                          <p className="mb-2 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                            {g.label}{" "}
                            <span className="opacity-60">({items.length})</span>
                          </p>
                          <ul className="flex flex-wrap gap-1.5">
                            {items.map((skill) => (
                              <li
                                key={`${g.key}-${skill}`}
                                className={`rounded-full px-2.5 py-0.5 text-xs font-medium ${g.chip}`}
                              >
                                {skill}
                              </li>
                            ))}
                          </ul>
                        </div>
                      );
                    })}
                  </div>

                </div>
              );
            })()}

          </div>
        )}

      </PageContainer>

      {/* ── Version History Drawer ────────────────────── */}
      <VersionHistoryDrawer
        open={historyOpen}
        onOpenChange={setHistoryOpen}
        onRestore={fetchProfile}
      />
    </div>
  );
}
