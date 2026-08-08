"use client";

import {
  FileText,
  Briefcase,
  GraduationCap,
  Award,
  Wrench,
  User,
  Building,
  MapPin,
  Calendar,
  Link2,
  Languages,
  FolderKanban,
  HeartHandshake,
  BookOpen,
  GitBranch,
  Code2,
  Hash,
  Trophy,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import type { CVDetail } from "@/lib/types";

interface CVViewerProps {
  cv: CVDetail;
  /** skill -> list of raw provenance source labels (e.g. "cv_explicit", "linkedin").
   *  Optional — when absent, skills render as a plain list with no source hint. */
  skillProvenance?: Record<string, string[]>;
  /** ProfileResponse.linkedin_subsections — { languages, projects, volunteer, courses }.
   *  Each entry is a loosely-typed dict from the LLM parse; fields are read defensively. */
  linkedinSubsections?: Record<string, Record<string, unknown>[]>;
  /** ProfileResponse.github_temporal — { languages: {lang: bytes}, topics: {topic: 1} }. */
  githubTemporal?: Record<string, Record<string, unknown>>;
}

// ── Dict helpers — cv_positions / linkedin subsections arrive as loosely
// typed `{[key: string]: unknown}` records (LLM output, no strict schema on
// the wire), so every field read is defensive: wrong type or missing key
// just renders as empty rather than crashing. ──────────────────────────────

function strField(obj: Record<string, unknown>, key: string): string {
  const v = obj[key];
  return typeof v === "string" ? v : "";
}

function strArrField(obj: Record<string, unknown>, key: string): string[] {
  const v = obj[key];
  return Array.isArray(v)
    ? v.filter((x): x is string => typeof x === "string")
    : [];
}

interface CVPositionEntry {
  company: string;
  title: string;
  dates: string;
  location: string;
  bullets: string[];
}

function normalizePosition(raw: Record<string, unknown>): CVPositionEntry {
  return {
    company: strField(raw, "company"),
    title: strField(raw, "title"),
    dates: strField(raw, "dates"),
    location: strField(raw, "location"),
    bullets: strArrField(raw, "bullets"),
  };
}

interface LinkedinPositionEntry {
  title: string;
  company: string;
  start: string;
  end: string;
  description: string;
}

function normalizeLinkedinPosition(raw: Record<string, unknown>): LinkedinPositionEntry {
  return {
    title: strField(raw, "title"),
    company: strField(raw, "company"),
    start: strField(raw, "start"),
    end: strField(raw, "end"),
    description: strField(raw, "description"),
  };
}

// ── Skill provenance — map raw evidence-source labels (skill_tiering.py's
// `_SOURCE_WEIGHTS` keys) to the same 4 user-facing buckets the "Your Skills"
// section on the profile page already groups by, so the colour language
// stays consistent across the page. ─────────────────────────────────────────

const SOURCE_BUCKET: Record<string, "cv" | "linkedin" | "github" | "preferences"> = {
  cv_explicit: "cv",
  linkedin: "linkedin",
  github_llm: "github",
  github_lang: "github",
  github_dep: "github",
  user_declared: "preferences",
  about_me_llm: "preferences",
};

const BUCKET_LABEL: Record<string, string> = {
  cv: "CV",
  linkedin: "LinkedIn",
  github: "GitHub",
  preferences: "you added",
};

/** Human-readable "Found in: CV, LinkedIn" string for a skill badge's title
 * tooltip, or null when there's no provenance to show. Matches case- and
 * whitespace-insensitively since evidence names aren't guaranteed to be the
 * exact same string casing as `cv.skills`. */
function provenanceTooltip(
  skill: string,
  provenance: Record<string, string[]> | undefined
): string | null {
  if (!provenance) return null;
  const norm = (s: string) => s.trim().toLowerCase();
  const key = Object.keys(provenance).find((k) => norm(k) === norm(skill));
  if (!key) return null;
  const buckets = Array.from(
    new Set(
      provenance[key]
        .map((s) => SOURCE_BUCKET[s])
        .filter((b): b is "cv" | "linkedin" | "github" | "preferences" => Boolean(b))
    )
  );
  if (!buckets.length) return null;
  return `Found in: ${buckets.map((b) => BUCKET_LABEL[b]).join(", ")}`;
}

/** Small "icon + uppercase label" header used by every extracted section. */
function SectionLabel({
  icon: Icon,
  text,
}: {
  icon: React.ComponentType<{ className?: string }>;
  text: string;
}) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <Icon className="h-3.5 w-3.5 text-primary" />
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {text}
      </span>
    </div>
  );
}

export function CVViewer({
  cv,
  skillProvenance,
  linkedinSubsections,
  githubTemporal,
}: CVViewerProps) {
  // (The showFullCV toggle and its highlight terms went with the "Full CV
  // Text" panel — that text now lives only in the CV Uploaded card.)

  const positions = (cv.cv_positions ?? [])
    .map((raw) => normalizePosition(raw as Record<string, unknown>))
    .filter((p) => p.company || p.title);

  // ── LinkedIn subsections ──────────────────────────────────
  const liPositions = (linkedinSubsections?.positions ?? [])
    .map((raw) => normalizeLinkedinPosition(raw))
    .filter((p) => p.title || p.company);
  const liLanguages = linkedinSubsections?.languages ?? [];
  const liProjects = linkedinSubsections?.projects ?? [];
  const liVolunteer = linkedinSubsections?.volunteer ?? [];
  const liCourses = linkedinSubsections?.courses ?? [];
  const hasLinkedinDetail =
    liPositions.length > 0 ||
    liLanguages.length > 0 ||
    liProjects.length > 0 ||
    liVolunteer.length > 0 ||
    liCourses.length > 0;

  // ── GitHub temporal (languages by byte share + topics) ─────
  const ghLanguages = Object.entries(githubTemporal?.languages ?? {}).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number"
  );
  const ghLanguageTotal = ghLanguages.reduce((sum, [, bytes]) => sum + bytes, 0);
  const ghLanguagesSorted = [...ghLanguages].sort((a, b) => b[1] - a[1]);
  const ghTopics = Object.keys(githubTemporal?.topics ?? {});
  const hasGithubDetail = ghLanguagesSorted.length > 0 || ghTopics.length > 0;

  return (
    <div className="space-y-6 animate-fade-in-up stagger-2">
      {/* ── Extracted sections ────────────────────────── */}
      <div className="glass-card rounded-xl p-6">
        <h3 className="font-heading text-base font-semibold mb-4 flex items-center gap-2">
          <FileText className="h-4 w-4 text-primary" />
          What we extracted from your profile
        </h3>

        {/* Identity */}
        {(cv.name || cv.headline || cv.location) && (
          <div className="mb-5 pb-4 border-b border-border/40">
            {cv.name && (
              <h4 className="font-heading text-lg font-semibold text-foreground">
                {cv.name}
              </h4>
            )}
            {cv.headline && (
              <p className="mt-0.5 text-sm text-muted-foreground">
                {cv.headline}
              </p>
            )}
            {cv.location && (
              <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
                <MapPin className="h-3 w-3" />
                {cv.location}
              </p>
            )}
          </div>
        )}

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
                  title={provenanceTooltip(skill, skillProvenance) ?? undefined}
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

        {/* Work History — dated positions (company · title · dates · location + bullets).
            Stored server-side since 2026-08-06 but never shown until now — the only
            place a user can see WHEN and WHERE they worked, not just a role-title bag. */}
        {positions.length > 0 && (
          <div className="mb-5">
            <SectionLabel icon={Calendar} text={`Work History (${positions.length})`} />
            <div className="space-y-3 pl-5">
              {positions.map((pos, i) => (
                <div
                  key={i}
                  className="rounded-lg border border-border/50 bg-muted/10 p-3"
                >
                  <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                    <p className="text-sm font-medium text-foreground">
                      {pos.title || "Role"}
                      {pos.company && (
                        <span className="font-normal text-muted-foreground">
                          {" "}
                          · {pos.company}
                        </span>
                      )}
                    </p>
                    {pos.dates && (
                      <span className="shrink-0 text-xs text-muted-foreground">
                        {pos.dates}
                      </span>
                    )}
                  </div>
                  {pos.location && (
                    <p className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground">
                      <MapPin className="h-3 w-3" />
                      {pos.location}
                    </p>
                  )}
                  {pos.bullets.length > 0 && (
                    <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-foreground/80">
                      {pos.bullets.map((bullet, bi) => (
                        <li key={bi} className="leading-relaxed">
                          {bullet}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
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

        {/* Achievements — returned by the API since day one, rendered
            nowhere until now. Same "stored but not shown" shape as
            cv_positions was. */}
        {cv.achievements.length > 0 && (
          <div className="mb-5">
            <SectionLabel icon={Trophy} text={`Achievements (${cv.achievements.length})`} />
            <ul className="space-y-1 pl-5 text-sm text-foreground/80">
              {cv.achievements.map((achievement, i) => (
                <li key={i} className="leading-relaxed">
                  {achievement}
                </li>
              ))}
            </ul>
          </div>
        )}
      </div>

      {/* The "Full CV Text" panel used to sit here. REMOVED 2026-08-08 (owner
          decision): the identical full CV text is already rendered in the "CV
          Uploaded" card at the top of this same page, with the same
          highlighting — the same wall of text twice on one screen. The card
          keeps it, because that is where a user looks for the CV they just
          uploaded; this view stays focused on what we EXTRACTED from it. */}

      {/* ── LinkedIn detail ────────────────────────────── */}
      {hasLinkedinDetail && (
        <div className="glass-card rounded-xl p-6">
          <h3 className="font-heading text-base font-semibold mb-4 flex items-center gap-2">
            <Link2 className="h-4 w-4 text-[#0A66C2]" />
            LinkedIn detail
          </h3>

          {/* Work History — parsed and stored since Batch 1.5 but never
              exposed until now; same visual style as the CV Work History
              section above (title · company · dates, description under
              each — LinkedIn positions carry one description, not bullets). */}
          {liPositions.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Calendar} text={`Work History (${liPositions.length})`} />
              <div className="space-y-3 pl-5">
                {liPositions.map((pos, i) => (
                  <div
                    key={i}
                    className="rounded-lg border border-border/50 bg-muted/10 p-3"
                  >
                    <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                      <p className="text-sm font-medium text-foreground">
                        {pos.title || "Role"}
                        {pos.company && (
                          <span className="font-normal text-muted-foreground">
                            {" "}
                            · {pos.company}
                          </span>
                        )}
                      </p>
                      {(pos.start || pos.end) && (
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {[pos.start, pos.end].filter(Boolean).join(" – ")}
                        </span>
                      )}
                    </div>
                    {pos.description && (
                      <p className="mt-1 text-xs text-foreground/80 leading-relaxed">
                        {pos.description}
                      </p>
                    )}
                  </div>
                ))}
              </div>
            </div>
          )}

          {liLanguages.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Languages} text={`Languages (${liLanguages.length})`} />
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {liLanguages.map((item, i) => {
                  const lang = strField(item, "language");
                  const prof = strField(item, "proficiency");
                  if (!lang) return null;
                  return (
                    <li
                      key={i}
                      className="rounded-full bg-sky-500/10 text-sky-400 px-2.5 py-0.5 text-xs font-medium"
                    >
                      {lang}
                      {prof ? ` · ${prof}` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liProjects.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={FolderKanban} text={`Projects (${liProjects.length})`} />
              <div className="space-y-2 pl-5">
                {liProjects.map((item, i) => {
                  const title = strField(item, "title");
                  const description = strField(item, "description");
                  const start = strField(item, "start");
                  const end = strField(item, "end");
                  const dateRange = [start, end].filter(Boolean).join(" – ");
                  if (!title) return null;
                  return (
                    <div key={i} className="text-sm">
                      <p className="font-medium text-foreground">
                        {title}
                        {dateRange && (
                          <span className="ml-2 text-xs font-normal text-muted-foreground">
                            {dateRange}
                          </span>
                        )}
                      </p>
                      {description && (
                        <p className="text-xs text-foreground/70 leading-relaxed">
                          {description}
                        </p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {liVolunteer.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={HeartHandshake} text={`Volunteer (${liVolunteer.length})`} />
              <div className="space-y-2 pl-5">
                {liVolunteer.map((item, i) => {
                  const role = strField(item, "role");
                  const org = strField(item, "organisation");
                  const cause = strField(item, "cause");
                  if (!role && !org) return null;
                  return (
                    <div key={i} className="text-sm">
                      <p className="font-medium text-foreground">
                        {role || "Volunteer"}
                        {org && (
                          <span className="font-normal text-muted-foreground">
                            {" "}
                            · {org}
                          </span>
                        )}
                      </p>
                      {cause && (
                        <p className="text-xs text-foreground/70">{cause}</p>
                      )}
                    </div>
                  );
                })}
              </div>
            </div>
          )}

          {liCourses.length > 0 && (
            <div>
              <SectionLabel icon={BookOpen} text={`Courses (${liCourses.length})`} />
              <ul className="space-y-1 pl-5 text-sm text-foreground/80">
                {liCourses.map((item, i) => {
                  const title = strField(item, "title");
                  const institution = strField(item, "institution");
                  if (!title) return null;
                  return (
                    <li key={i} className="leading-relaxed">
                      {title}
                      {institution ? ` — ${institution}` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* ── GitHub detail ──────────────────────────────── */}
      {hasGithubDetail && (
        <div className="glass-card rounded-xl p-6">
          <h3 className="font-heading text-base font-semibold mb-4 flex items-center gap-2">
            <GitBranch className="h-4 w-4 text-[#8B5CF6]" />
            GitHub detail
          </h3>

          {ghLanguagesSorted.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Code2} text={`Languages (${ghLanguagesSorted.length})`} />
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {ghLanguagesSorted.map(([lang, bytes]) => {
                  const pct = ghLanguageTotal > 0 ? Math.round((bytes / ghLanguageTotal) * 100) : 0;
                  return (
                    <li
                      key={lang}
                      className="rounded-full bg-violet-500/10 text-violet-400 px-2.5 py-0.5 text-xs font-medium"
                    >
                      {lang} {pct > 0 ? `· ${pct}%` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {ghTopics.length > 0 && (
            <div>
              <SectionLabel icon={Hash} text={`Topics (${ghTopics.length})`} />
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {ghTopics.map((topic) => (
                  <li
                    key={topic}
                    className="rounded-full bg-muted/40 text-muted-foreground px-2.5 py-0.5 text-xs font-medium"
                  >
                    {topic}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
