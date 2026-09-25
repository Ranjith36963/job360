"use client";

import { useState } from "react";
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
  ChevronDown,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { EditedMark } from "@/components/profile/EditedMark";
import { findAgentEdit, type AgentEdit } from "@/lib/agent-edits";
import type { CVDetail } from "@/lib/types";

interface CVViewerProps {
  cv: CVDetail;
  /** ProfileResponse.agent_edits (spec R11) — the current agent-edit overlay.
   *  Optional; each editable field looks up its own `cv_data.<field>` path and
   *  renders nothing extra when there is no active edit for it. */
  agentEdits?: AgentEdit[];
  /** "Take back" an assistant's change to one path. Omitted = no button. */
  onTakeBack?: (path: string) => Promise<void>;
  /** "Keep" an assistant's change as the human's own. Omitted = no button. */
  onKeep?: (path: string) => Promise<void>;
  /** ProfileResponse.linkedin_subsections — { languages, projects, volunteer, courses }.
   *  Each entry is a loosely-typed dict from the LLM parse; fields are read defensively. */
  linkedinSubsections?: Record<string, Record<string, unknown>[]>;
  /** ProfileResponse.github_temporal — { languages: {lang: bytes}, topics: {topic: 1} }. */
  githubTemporal?: Record<string, Record<string, unknown>>;
  /** ProfileResponse.github_detail — repos, frameworks, inferred + LLM-read
   *  skills, bio, profile README. Everything GitHub gave us beyond languages
   *  and topics, which until 2026-08-09 was stored and rendered nowhere. */
  githubDetail?: Record<string, unknown>;
}

interface GithubRepoEntry {
  name: string;
  language: string;
  description: string;
  topics: string[];
  stars: number;
  forks: number;
  archived: boolean;
  homepage: string;
  pushed_at: string;
  created_at: string;
}

function numField(obj: Record<string, unknown>, key: string): number {
  const v = obj[key];
  return typeof v === "number" && Number.isFinite(v) ? v : 0;
}

function normalizeGithubRepo(raw: Record<string, unknown>): GithubRepoEntry {
  return {
    name: strField(raw, "name"),
    language: strField(raw, "language"),
    description: strField(raw, "description"),
    topics: strArrField(raw, "topics"),
    stars: numField(raw, "stars"),
    forks: numField(raw, "forks"),
    archived: raw["archived"] === true,
    homepage: strField(raw, "homepage"),
    pushed_at: strField(raw, "pushed_at"),
    created_at: strField(raw, "created_at"),
  };
}

/** "2 yrs" / "8 mo" — how long this repo has been alive, first commit to last.
 *  Tenure is a different claim from recency: "touched last week" vs "worked on
 *  for two years". Empty when either end is missing or unparseable. */
function repoTenure(createdAt: string, pushedAt: string): string {
  if (!createdAt || !pushedAt) return "";
  const a = new Date(createdAt).getTime();
  const b = new Date(pushedAt).getTime();
  if (Number.isNaN(a) || Number.isNaN(b) || b <= a) return "";
  const months = Math.round((b - a) / (1000 * 60 * 60 * 24 * 30.44));
  if (months < 1) return "";
  if (months < 12) return `${months} mo`;
  const years = Math.floor(months / 12);
  return years === 1 ? "1 yr" : `${years} yrs`;
}

/** "updated 3 Aug 2026" — a repo's recency, in words, in the viewer's locale. */
function formatRepoDate(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return "";
  return d.toLocaleDateString(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  });
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

/** Small "icon + uppercase label" header used by every extracted section. */
function SectionLabel({
  icon: Icon,
  text,
  trailing,
}: {
  icon: React.ComponentType<{ className?: string }>;
  text: string;
  /** Extra content after the label text — e.g. an `EditedMark` for a field
   *  that carries an active agent-edit overlay. */
  trailing?: React.ReactNode;
}) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <Icon className="h-3.5 w-3.5 text-primary" />
      <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
        {text}
      </span>
      {trailing}
    </div>
  );
}

export function CVViewer({
  cv,
  linkedinSubsections,
  githubTemporal,
  githubDetail,
  agentEdits,
  onTakeBack,
  onKeep,
}: CVViewerProps) {
  const editOf = (field: string) => findAgentEdit(agentEdits, `cv_data.${field}`);
  // (The showFullCV toggle and its highlight terms went with the "Full CV
  // Text" panel — that text now lives only in the CV Uploaded card.)

  // Owner decision, 2026-09-24: both detail dumps are real content a person
  // may want to check, but neither is something most visits need — so both
  // stay collapsed until asked for, same reasoning as the raw CV text fold
  // in CVUpload.tsx.
  const [linkedinDetailOpen, setLinkedinDetailOpen] = useState(false);
  const [githubDetailOpen, setGithubDetailOpen] = useState(false);

  // Projects the CV states. Defensive field reads for the same reason as
  // cv_positions: these rows are LLM output with no strict wire schema.
  const cvProjects = ((cv.cv_projects ?? []) as Record<string, unknown>[])
    .map((raw) => ({
      name: strField(raw, "name"),
      description: strField(raw, "description"),
      technologies: strArrField(raw, "technologies"),
      dates: strField(raw, "dates"),
    }))
    .filter((p) => p.name || p.description);

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
  // 2026-08-09 — the seven sections the parser split and nobody read, plus the
  // Contact block. Same "stored but never rendered" gap that hid 92 GitHub
  // signals; closed here for the other input.
  const liHonors = linkedinSubsections?.honors ?? [];
  const liPublications = linkedinSubsections?.publications ?? [];
  const liPatents = linkedinSubsections?.patents ?? [];
  const liOrganizations = linkedinSubsections?.organizations ?? [];
  const liTestScores = linkedinSubsections?.test_scores ?? [];
  const liRecommendations = linkedinSubsections?.recommendations ?? [];
  const liInterests = linkedinSubsections?.interests ?? [];
  const liContact = (linkedinSubsections?.contact ?? [])[0] ?? {};
  // The LinkedIn "About" — the person's own first-person prose. It was
  // discarded whenever the CV already had a summary, so most profiles lost it
  // entirely; now it has a shelf, reaches the judge, and is finally shown.
  const liSummary = strField((linkedinSubsections?.summary ?? [])[0] ?? {}, "text");
  // The LinkedIn headline. Empty on every two-column export until the LLM was
  // asked for it — the flattened left rail pushes the name and tagline into the
  // middle of the text, where no structural reader was looking.
  const liHeadline = strField((linkedinSubsections?.headline ?? [])[0] ?? {}, "text");
  const liContactRows: [string, string][] = (
    [
      ["Email", strField(liContact, "email")],
      ["Phone", strField(liContact, "phone")],
      ["LinkedIn", strField(liContact, "linkedin_url")],
      ["Websites", strArrField(liContact, "websites").join(", ")],
    ] as [string, string][]
  ).filter(([, v]) => Boolean(v));
  const hasLinkedinDetail =
    liPositions.length > 0 ||
    liLanguages.length > 0 ||
    liProjects.length > 0 ||
    liVolunteer.length > 0 ||
    liCourses.length > 0 ||
    liHonors.length > 0 ||
    liPublications.length > 0 ||
    liPatents.length > 0 ||
    liOrganizations.length > 0 ||
    liTestScores.length > 0 ||
    liRecommendations.length > 0 ||
    liInterests.length > 0 ||
    liContactRows.length > 0 ||
    liSummary.length > 0 ||
    liHeadline.length > 0;

  // ── GitHub temporal (languages by byte share + topics) ─────
  const ghLanguages = Object.entries(githubTemporal?.languages ?? {}).filter(
    (entry): entry is [string, number] => typeof entry[1] === "number"
  );
  const ghLanguageTotal = ghLanguages.reduce((sum, [, bytes]) => sum + bytes, 0);
  const ghLanguagesSorted = [...ghLanguages].sort((a, b) => b[1] - a[1]);
  const ghTopics = Object.keys(githubTemporal?.topics ?? {});

  // ── The rest of GitHub — read defensively, same as every other loosely
  // typed block on this page. Each renders only when it has content, so a
  // profile with no GitHub (or a partial fetch) stays silent (rule #29).
  const ghDetail = githubDetail ?? {};
  const ghRepos = (Array.isArray(ghDetail.repos) ? ghDetail.repos : [])
    .filter((r): r is Record<string, unknown> => typeof r === "object" && r !== null)
    .map(normalizeGithubRepo)
    .filter((r) => r.name);
  const asStrings = (v: unknown): string[] =>
    Array.isArray(v) ? v.filter((x): x is string => typeof x === "string") : [];
  const ghFrameworks = asStrings(ghDetail.frameworks);
  // No "skills" shelves here on purpose. The one skill list (backend
  // skill_tiering.profile_skills) takes only significant languages from
  // GitHub; the raw inferred list (every language + topic slug again) and the
  // leftovers of our own deleted README-reading model (decision 28) are not
  // skills and are no longer sent. Languages, topics, repos and frameworks
  // stay, each on its own labelled shelf.
  // Structured identity → label/value rows. Only fields the person actually
  // filled in appear; an unset field stays absent rather than rendering an
  // empty row (rule #29). `hireable` is tri-state: shown only when GitHub has
  // a real boolean, because "never said" is not "not looking".
  const ghIdentity =
    typeof ghDetail.identity === "object" && ghDetail.identity !== null
      ? (ghDetail.identity as Record<string, unknown>)
      : {};
  const ghIdentityRows: [string, string][] = (
    [
      ["Name", strField(ghIdentity, "name")],
      ["Company", strField(ghIdentity, "company")],
      ["Location", strField(ghIdentity, "location")],
      ["Website", strField(ghIdentity, "blog")],
      [
        "Twitter/X",
        strField(ghIdentity, "twitter") ? `@${strField(ghIdentity, "twitter")}` : "",
      ],
      [
        "Open to work",
        ghIdentity.hireable === true
          ? "Yes"
          : ghIdentity.hireable === false
            ? "No"
            : "",
      ],
      [
        "On GitHub since",
        formatRepoDate(strField(ghIdentity, "account_created_at")),
      ],
      [
        "Public repos",
        numField(ghIdentity, "public_repos")
          ? String(numField(ghIdentity, "public_repos"))
          : "",
      ],
      [
        "Followers",
        numField(ghIdentity, "followers")
          ? String(numField(ghIdentity, "followers"))
          : "",
      ],
    ] as [string, string][]
  ).filter(([, v]) => Boolean(v));

  const ghBio = typeof ghDetail.bio === "string" ? ghDetail.bio : "";
  const ghProfileReadme =
    typeof ghDetail.profile_readme === "string" ? ghDetail.profile_readme : "";

  const hasGithubDetail =
    ghIdentityRows.length > 0 ||
    ghLanguagesSorted.length > 0 ||
    ghTopics.length > 0 ||
    ghRepos.length > 0 ||
    ghFrameworks.length > 0 ||
    Boolean(ghBio) ||
    Boolean(ghProfileReadme);

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
                <EditedMark edit={editOf("name")} onTakeBack={onTakeBack} onKeep={onKeep} />
              </h4>
            )}
            {cv.headline && (
              <p className="mt-0.5 text-sm text-muted-foreground">
                {cv.headline}
                <EditedMark edit={editOf("headline")} onTakeBack={onTakeBack} onKeep={onKeep} />
              </p>
            )}
            {cv.location && (
              <p className="mt-1.5 flex items-center gap-1 text-xs text-muted-foreground">
                <MapPin className="h-3 w-3" />
                {cv.location}
                <EditedMark edit={editOf("location")} onTakeBack={onTakeBack} onKeep={onKeep} />
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
              <EditedMark edit={editOf("summary")} onTakeBack={onTakeBack} onKeep={onKeep} />
            </div>
            <p className="text-sm text-foreground/90 leading-relaxed pl-5 border-l-2 border-primary/20">
              {cv.summary_text}
            </p>
          </div>
        )}

        {/* Skills — shown ONCE, in the "Your Skills" panel below (owner
            decision, 2026-09-24). This card used to print its own "Skills
            Extracted (N)" chip list too — the same skills, a second time,
            on the same page. */}

        {/* Job Titles / Experience */}
        {cv.job_titles.length > 0 && (
          <div className="mb-5">
            <div className="flex items-center gap-2 mb-2">
              <Briefcase className="h-3.5 w-3.5 text-primary" />
              <span className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                Experience Found
              </span>
              <EditedMark edit={editOf("job_titles")} onTakeBack={onTakeBack} onKeep={onKeep} />
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
              <EditedMark edit={editOf("education")} onTakeBack={onTakeBack} onKeep={onKeep} />
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
              <EditedMark edit={editOf("certifications")} onTakeBack={onTakeBack} onKeep={onKeep} />
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

        {/* Seniority and right-to-work, as the CV states them. Both are part of
            the stored profile, so they were being read by the agent while
            invisible to the person they describe - the same gap that hid
            linkedin_summary, made again one commit later on the shelves that
            replaced it. */}
        {(cv.cv_experience_level || cv.cv_right_to_work) && (
          <div className="mb-5">
            <SectionLabel icon={Briefcase} text="Stated on your CV" />
            <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pl-5 text-xs">
              {cv.cv_experience_level && (
                <div className="contents">
                  <dt className="text-muted-foreground">Seniority</dt>
                  <dd className="text-foreground/85">{cv.cv_experience_level}</dd>
                </div>
              )}
              {cv.cv_right_to_work && (
                <div className="contents">
                  <dt className="text-muted-foreground">Right to work</dt>
                  <dd className="text-foreground/85">
                    {cv.cv_right_to_work}
                    <EditedMark edit={editOf("cv_right_to_work")} onTakeBack={onTakeBack} onKeep={onKeep} />
                  </dd>
                </div>
              )}
            </dl>
          </div>
        )}

        {/* Projects stated on the CV. A "Projects" heading was already
            recognised — as a boundary that stops a skills block — and then
            discarded. For a junior or career-changing candidate these are
            often the strongest evidence on the document. */}
        {cvProjects.length > 0 && (
          <div className="mb-5">
            <SectionLabel icon={FolderKanban} text={`Projects (${cvProjects.length})`} />
            <div className="space-y-3 pl-5">
              {cvProjects.map((proj, i) => (
                <div key={i}>
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <span className="text-sm font-medium text-foreground">{proj.name}</span>
                    {proj.dates && (
                      <span className="text-xs text-muted-foreground">{proj.dates}</span>
                    )}
                  </div>
                  {proj.description && (
                    <p className="text-xs leading-relaxed text-foreground/80">
                      {proj.description}
                    </p>
                  )}
                  {proj.technologies.length > 0 && (
                    <div className="mt-1 flex flex-wrap gap-1">
                      {proj.technologies.map((t, j) => (
                        <Badge key={j} variant="secondary" className="text-[10px]">
                          {t}
                        </Badge>
                      ))}
                    </div>
                  )}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Achievements — returned by the API since day one, rendered
            nowhere until now. Same "stored but not shown" shape as
            cv_positions was. */}
        {cv.achievements.length > 0 && (
          <div className="mb-5">
            <SectionLabel
              icon={Trophy}
              text={`Achievements (${cv.achievements.length})`}
              trailing={<EditedMark edit={editOf("achievements")} onTakeBack={onTakeBack} onKeep={onKeep} />}
            />
            <ul className="space-y-1 pl-5 text-sm text-foreground/80">
              {cv.achievements.map((achievement, i) => (
                <li key={i} className="leading-relaxed">
                  {achievement}
                </li>
              ))}
            </ul>
          </div>
        )}

        {/* Links — portfolio / personal-site / other URLs (spec R9's new
            `cv_data.links` field). Agent-editable only (there is no
            extraction path for it yet), so this section is either empty or
            entirely agent-added. S5: only a value starting `https://` is
            rendered as a clickable link — anything else shows as plain
            text so a bad value can never become a clickable script. */}
        {(cv.links ?? []).length > 0 && (
          <div className="mb-5">
            <SectionLabel
              icon={Link2}
              text={`Links (${(cv.links ?? []).length})`}
              trailing={<EditedMark edit={editOf("links")} onTakeBack={onTakeBack} onKeep={onKeep} />}
            />
            <ul className="space-y-1 pl-5 text-sm text-foreground/80">
              {(cv.links ?? []).map((link, i) => (
                <li key={i} className="break-all leading-relaxed">
                  {link.startsWith("https://") ? (
                    <a href={link} target="_blank" rel="noreferrer" className="text-primary hover:underline">
                      {link}
                    </a>
                  ) : (
                    link
                  )}
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
          <button
            type="button"
            data-testid="linkedin-detail-toggle"
            onClick={() => setLinkedinDetailOpen((open) => !open)}
            aria-expanded={linkedinDetailOpen}
            className="flex w-full items-center gap-2 text-left"
          >
            <Link2 className="h-4 w-4 text-[#0A66C2]" />
            <h3 className="font-heading text-base font-semibold flex-1">
              LinkedIn detail
            </h3>
            <span className="text-xs font-medium text-muted-foreground">
              {linkedinDetailOpen ? "Hide" : "Show what we read from LinkedIn"}
            </span>
            <ChevronDown
              className={`h-4 w-4 text-muted-foreground transition-transform ${
                linkedinDetailOpen ? "rotate-180" : ""
              }`}
            />
          </button>

          {linkedinDetailOpen && (
          <div data-testid="linkedin-detail-content" className="mt-4">
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

          {/* Headline — LinkedIn's tagline. Empty on every two-column export
              until the LLM was asked for it; often states the stack AND current
              availability ("Open to X roles UK"), which no other input says. */}
          {liHeadline.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={User} text="Headline" />
              <p className="pl-5 text-xs leading-relaxed text-foreground/85">
                {liHeadline}
              </p>
            </div>
          )}

          {/* About — the person's own words. This was extracted, stored and
              MATCHED ON while being invisible to the person it describes: the
              third way a shelf goes dark, after a broken extractor and a
              dropping merge. */}
          {liSummary.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={User} text="About" />
              <p className="whitespace-pre-line pl-5 text-xs leading-relaxed text-foreground/85">
                {liSummary}
              </p>
            </div>
          )}

          {/* Contact — parsed today only to confirm the file WAS a LinkedIn
              export, then discarded. Structured now; `websites` excludes the
              linkedin.com URL itself, which is already its own row. */}
          {liContactRows.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={User} text="Contact" />
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pl-5 text-xs">
                {liContactRows.map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="break-all text-foreground/85">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

          {/* ── The seven sections that were split and then dropped ──
              Each renders only when the profile carries it, so a LinkedIn
              export without them stays silent (rule #29). */}

          {liRecommendations.length > 0 && (
            <div className="mb-5">
              <SectionLabel
                icon={HeartHandshake}
                text={`Recommendations (${liRecommendations.length})`}
              />
              {/* Third-party evidence — other people's words about this
                  person's work, the LinkedIn analogue of a GitHub README. */}
              <ul className="space-y-2 pl-5">
                {liRecommendations.map((item, i) => {
                  const text = strField(item, "text");
                  if (!text) return null;
                  const author = strField(item, "author");
                  const rel = strField(item, "relationship");
                  return (
                    <li
                      key={i}
                      className="rounded-lg border border-border/40 bg-muted/10 p-3"
                    >
                      <p className="text-xs leading-relaxed text-foreground/85">
                        {text}
                      </p>
                      {(author || rel) && (
                        <p className="mt-1 text-[11px] text-muted-foreground">
                          {[author, rel].filter(Boolean).join(" · ")}
                        </p>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liPublications.length > 0 && (
            <div className="mb-5">
              <SectionLabel
                icon={BookOpen}
                text={`Publications (${liPublications.length})`}
              />
              <ul className="space-y-1 pl-5 text-sm text-foreground/80">
                {liPublications.map((item, i) => {
                  const title = strField(item, "title");
                  if (!title) return null;
                  const meta = [
                    strField(item, "publisher"),
                    strField(item, "date"),
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={i} className="leading-relaxed">
                      <span className="font-medium">{title}</span>
                      {meta && (
                        <span className="text-xs text-muted-foreground"> — {meta}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liPatents.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Award} text={`Patents (${liPatents.length})`} />
              <ul className="space-y-1 pl-5 text-sm text-foreground/80">
                {liPatents.map((item, i) => {
                  const title = strField(item, "title");
                  if (!title) return null;
                  const meta = [
                    strField(item, "number"),
                    strField(item, "status"),
                    strField(item, "date"),
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={i} className="leading-relaxed">
                      <span className="font-medium">{title}</span>
                      {meta && (
                        <span className="text-xs text-muted-foreground"> — {meta}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liHonors.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Trophy} text={`Honors & awards (${liHonors.length})`} />
              <ul className="space-y-1 pl-5 text-sm text-foreground/80">
                {liHonors.map((item, i) => {
                  const title = strField(item, "title");
                  if (!title) return null;
                  const meta = [
                    strField(item, "issuer"),
                    strField(item, "date"),
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={i} className="leading-relaxed">
                      <span className="font-medium">{title}</span>
                      {meta && (
                        <span className="text-xs text-muted-foreground"> — {meta}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liOrganizations.length > 0 && (
            <div className="mb-5">
              <SectionLabel
                icon={Building}
                text={`Organisations (${liOrganizations.length})`}
              />
              <ul className="space-y-1 pl-5 text-sm text-foreground/80">
                {liOrganizations.map((item, i) => {
                  const name = strField(item, "name");
                  if (!name) return null;
                  const meta = [
                    strField(item, "role"),
                    [strField(item, "start"), strField(item, "end")]
                      .filter(Boolean)
                      .join(" – "),
                  ].filter(Boolean).join(" · ");
                  return (
                    <li key={i} className="leading-relaxed">
                      <span className="font-medium">{name}</span>
                      {meta && (
                        <span className="text-xs text-muted-foreground"> — {meta}</span>
                      )}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liTestScores.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Award} text={`Test scores (${liTestScores.length})`} />
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {liTestScores.map((item, i) => {
                  const name = strField(item, "name");
                  if (!name) return null;
                  const score = strField(item, "score");
                  return (
                    <li
                      key={i}
                      className="rounded-full bg-sky-500/10 px-2.5 py-0.5 text-xs font-medium text-sky-400"
                    >
                      {name}
                      {score ? ` · ${score}` : ""}
                    </li>
                  );
                })}
              </ul>
            </div>
          )}

          {liInterests.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={Hash} text={`Interests (${liInterests.length})`} />
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {liInterests.map((item, i) => {
                  const name = strField(item, "name");
                  if (!name) return null;
                  return (
                    <li
                      key={i}
                      className="rounded-full bg-muted/40 px-2.5 py-0.5 text-xs text-muted-foreground"
                    >
                      {name}
                    </li>
                  );
                })}
              </ul>
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
        </div>
      )}

      {/* ── GitHub detail ──────────────────────────────── */}
      {hasGithubDetail && (
        <div className="glass-card rounded-xl p-6">
          <button
            type="button"
            data-testid="github-detail-toggle"
            onClick={() => setGithubDetailOpen((open) => !open)}
            aria-expanded={githubDetailOpen}
            className="flex w-full items-center gap-2 text-left"
          >
            <GitBranch className="h-4 w-4 text-[#8B5CF6]" />
            <h3 className="font-heading text-base font-semibold flex-1">
              GitHub detail
            </h3>
            <span className="text-xs font-medium text-muted-foreground">
              {githubDetailOpen ? "Hide" : "Show what we read from GitHub"}
            </span>
            <ChevronDown
              className={`h-4 w-4 text-muted-foreground transition-transform ${
                githubDetailOpen ? "rotate-180" : ""
              }`}
            />
          </button>

          {githubDetailOpen && (
          <div data-testid="github-detail-content" className="mt-4">
          {/* Identity — the developer's OWN words about themselves, kept as
              values rather than a sentence. "Open to work" and location are
              the two that a future matcher can actually act on. */}
          {ghIdentityRows.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={User} text="Profile" />
              <dl className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1 pl-5 text-xs">
                {ghIdentityRows.map(([label, value]) => (
                  <div key={label} className="contents">
                    <dt className="text-muted-foreground">{label}</dt>
                    <dd className="text-foreground/85">{value}</dd>
                  </div>
                ))}
              </dl>
            </div>
          )}

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
            <div className="mb-5">
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

          {/* ── Everything below was FETCHED, STORED, and shown to nobody ──
              Measured 2026-08-09 on a live profile: 49 frameworks and 13 repos
              sat in the database while only languages and topics reached the
              screen. It is the input where invisibility
              costs most — a CV CLAIMS "FastAPI", a requirements.txt in shipped
              code PROVES it, which is stronger evidence than a CV mention
              alone. It was part of the stored profile already; they just
              could not see it. */}

          {ghRepos.length > 0 && (
            <div className="mb-5">
              <SectionLabel icon={GitBranch} text={`Repositories (${ghRepos.length})`} />
              <ul className="space-y-2 pl-5">
                {ghRepos.map((repo, i) => (
                  <li
                    key={`${repo.name}-${i}`}
                    className="rounded-lg border border-border/40 bg-muted/10 p-3"
                  >
                    <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                      <span className="text-sm font-medium text-foreground">
                        {repo.name}
                      </span>
                      {repo.language && (
                        <span className="rounded-full bg-violet-500/10 px-2 py-0.5 text-[11px] font-medium text-violet-400">
                          {repo.language}
                        </span>
                      )}
                      {repo.stars > 0 && (
                        <span className="text-[11px] text-muted-foreground">
                          ★ {repo.stars}
                        </span>
                      )}
                      {repo.forks > 0 && (
                        <span className="text-[11px] text-muted-foreground">
                          ⑂ {repo.forks}
                        </span>
                      )}
                      {repo.archived && (
                        <span className="rounded-full bg-amber-500/10 px-2 py-0.5 text-[10px] font-medium text-amber-500">
                          archived
                        </span>
                      )}
                      {repoTenure(repo.created_at, repo.pushed_at) && (
                        <span className="text-[11px] text-muted-foreground">
                          built over {repoTenure(repo.created_at, repo.pushed_at)}
                        </span>
                      )}
                      {repo.pushed_at && (
                        <span className="ml-auto text-[11px] text-muted-foreground">
                          updated {formatRepoDate(repo.pushed_at)}
                        </span>
                      )}
                    </div>
                    {repo.description && (
                      <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
                        {repo.description}
                      </p>
                    )}
                    {repo.topics.length > 0 && (
                      <ul className="mt-1.5 flex flex-wrap gap-1">
                        {repo.topics.map((t) => (
                          <li
                            key={t}
                            className="rounded-full bg-muted/40 px-2 py-0.5 text-[10px] text-muted-foreground"
                          >
                            {t}
                          </li>
                        ))}
                      </ul>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {ghFrameworks.length > 0 && (
            <div className="mb-5">
              <SectionLabel
                icon={Wrench}
                text={`Frameworks & libraries (${ghFrameworks.length})`}
              />
              {/* The strongest evidence on the page: read from real dependency
                  files (package.json / requirements.txt), so this is
                  DEMONSTRATED usage, not a claim. Said plainly, because the
                  distinction is the whole value. */}
              <p className="pl-5 mb-1.5 text-[11px] text-muted-foreground">
                Found in your dependency files — used in shipped code, not just listed.
              </p>
              <ul className="flex flex-wrap gap-1.5 pl-5">
                {ghFrameworks.map((f) => (
                  <li
                    key={f}
                    className="rounded-full bg-violet-500/10 px-2.5 py-0.5 text-xs font-medium text-violet-400"
                  >
                    {f}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {ghBio && (
            <div className="mb-5">
              <SectionLabel icon={User} text="Bio" />
              <p className="pl-5 text-sm leading-relaxed text-foreground/80">
                {ghBio}
              </p>
            </div>
          )}

          {ghProfileReadme && (
            <div>
              <SectionLabel icon={FileText} text="Profile README" />
              <pre className="ml-5 max-h-64 overflow-y-auto whitespace-pre-wrap rounded-lg border border-border/40 bg-muted/10 p-3 font-sans text-xs leading-relaxed text-foreground/80">
                {ghProfileReadme}
              </pre>
            </div>
          )}
          </div>
          )}
        </div>
      )}
    </div>
  );
}
