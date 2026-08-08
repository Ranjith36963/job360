"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  X,
  Plus,
  Briefcase,
  MapPin,
  DollarSign,
  Building2,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { Separator } from "@/components/ui/separator";
import type { PreferencesRequest } from "@/lib/types";

interface PreferencesFormProps {
  preferences: Record<string, unknown>;
  onSave: (prefs: PreferencesRequest) => Promise<void>;
  loading: boolean;
}

// ── Tag Input ──────────────────────────────────────────────
// Reusable sub-component for tag/pill-style inputs

interface TagInputProps {
  label: string;
  icon?: React.ReactNode;
  tags: string[];
  onChange: (tags: string[]) => void;
  placeholder?: string;
  description?: string;
  variant?: "default" | "destructive";
}

function TagInput({
  label,
  icon,
  tags,
  onChange,
  placeholder = "Type and press Enter",
  description,
  variant = "default",
}: TagInputProps) {
  const [inputValue, setInputValue] = useState("");

  const addTag = useCallback(() => {
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    if (tags.some((t) => t.toLowerCase() === trimmed.toLowerCase())) {
      setInputValue("");
      return;
    }
    onChange([...tags, trimmed]);
    setInputValue("");
  }, [inputValue, tags, onChange]);

  const removeTag = useCallback(
    (index: number) => {
      onChange(tags.filter((_, i) => i !== index));
    },
    [tags, onChange]
  );

  const badgeClass =
    variant === "destructive"
      ? "bg-destructive/10 text-destructive border border-destructive/20"
      : "skill-matched";

  return (
    <div className="space-y-2">
      <Label className="text-sm font-medium">
        {icon}
        {label}
      </Label>
      {description && (
        <p className="text-xs text-muted-foreground -mt-1">{description}</p>
      )}
      {tags.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {tags.map((tag, i) => (
            <span
              key={`${tag}-${i}`}
              className={`inline-flex items-center gap-1 rounded-md px-2.5 py-1 text-xs font-medium ${badgeClass}`}
            >
              {tag}
              <button
                type="button"
                onClick={() => removeTag(i)}
                className="ml-0.5 rounded-sm p-0.5 opacity-60 hover:opacity-100 transition-opacity"
              >
                <X className="h-3 w-3" />
              </button>
            </span>
          ))}
        </div>
      )}
      <div className="flex gap-2">
        <Input
          value={inputValue}
          onChange={(e) => setInputValue(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              addTag();
            }
          }}
          placeholder={placeholder}
          className="flex-1"
        />
        <Button
          type="button"
          variant="outline"
          size="default"
          onClick={addTag}
          disabled={!inputValue.trim()}
          className="shrink-0"
        >
          <Plus className="h-3.5 w-3.5" />
        </Button>
      </div>
    </div>
  );
}

// ── Normalization helpers ──────────────────────────────────
// One source of truth for turning either the raw `preferences` prop OR the
// live form state into a canonical PreferencesRequest. Auto-save compares a
// serialization of the two to decide whether anything actually changed — so
// both paths MUST normalize identically (e.g. salary "" → null), or hydration
// would look like an edit and fire a spurious save.

const asArr = (val: unknown): string[] =>
  Array.isArray(val) ? val.map(String) : [];

function prefsFromRaw(raw: Record<string, unknown>): PreferencesRequest {
  return {
    target_job_titles: asArr(raw.target_job_titles),
    additional_skills: asArr(raw.additional_skills),
    excluded_skills: asArr(raw.excluded_skills),
    preferred_locations: asArr(raw.preferred_locations),
    industries: asArr(raw.industries),
    salary_min: raw.salary_min != null ? Number(raw.salary_min) : null,
    salary_max: raw.salary_max != null ? Number(raw.salary_max) : null,
    work_arrangement:
      typeof raw.work_arrangement === "string" ? raw.work_arrangement : "any",
    experience_level:
      typeof raw.experience_level === "string" ? raw.experience_level : "mid",
    negative_keywords: asArr(raw.negative_keywords),
    about_me: typeof raw.about_me === "string" ? raw.about_me : "",
    excluded_companies: asArr(raw.excluded_companies),
    needs_visa: raw.needs_visa === true,
  };
}

// Order-stable serialization (array, not object) so key order can't cause a
// false "changed" verdict.
function serializePrefs(p: PreferencesRequest): string {
  return JSON.stringify([
    p.target_job_titles,
    p.additional_skills,
    p.excluded_skills,
    p.preferred_locations,
    p.industries,
    p.salary_min,
    p.salary_max,
    p.work_arrangement,
    p.experience_level,
    p.negative_keywords,
    p.about_me,
    p.excluded_companies,
    p.needs_visa,
  ]);
}

// Debounce window for auto-save: long enough to batch fast typing, short
// enough to feel instant.
const AUTOSAVE_DELAY_MS = 800;

// ── Preferences Form ───────────────────────────────────────

export function PreferencesForm({
  preferences,
  onSave,
  loading,
}: PreferencesFormProps) {
  const [targetTitles, setTargetTitles] = useState<string[]>([]);
  const [additionalSkills, setAdditionalSkills] = useState<string[]>([]);
  const [excludedSkills, setExcludedSkills] = useState<string[]>([]);
  const [preferredLocations, setPreferredLocations] = useState<string[]>([]);
  const [industries, setIndustries] = useState<string[]>([]);
  const [salaryMin, setSalaryMin] = useState("");
  const [salaryMax, setSalaryMax] = useState("");
  const [workArrangement, setWorkArrangement] = useState("any");
  const [experienceLevel, setExperienceLevel] = useState("mid");
  const [negativeKeywords, setNegativeKeywords] = useState<string[]>([]);
  const [aboutMe, setAboutMe] = useState("");
  const [excludedCompanies, setExcludedCompanies] = useState<string[]>([]);
  const [needsVisa, setNeedsVisa] = useState(false);
  const [saving, setSaving] = useState(false);
  // Distinct from `saving`: a failed auto-save used to fall back to the same
  // green "Changes save automatically" line as a successful one, so once the
  // parent's toast faded the user had NO way to tell their edit had not
  // persisted. They would navigate away believing it was saved.
  const [saveFailed, setSaveFailed] = useState(false);

  // Serialized snapshot of the last-saved (or just-hydrated) preferences.
  // Auto-save compares against this so hydration and no-op renders don't save.
  // null = not hydrated yet.
  const baselineRef = useRef<string | null>(null);

  // Hydrate form from the preferences prop, and set the auto-save baseline to
  // the same values so loading the form never looks like an edit.
  useEffect(() => {
    if (!preferences) return;
    const p = prefsFromRaw(preferences);
    setTargetTitles(p.target_job_titles ?? []);
    setAdditionalSkills(p.additional_skills ?? []);
    setExcludedSkills(p.excluded_skills ?? []);
    setPreferredLocations(p.preferred_locations ?? []);
    setIndustries(p.industries ?? []);
    setSalaryMin(p.salary_min != null ? String(p.salary_min) : "");
    setSalaryMax(p.salary_max != null ? String(p.salary_max) : "");
    setWorkArrangement(p.work_arrangement ?? "any");
    setExperienceLevel(p.experience_level ?? "mid");
    setNeedsVisa(p.needs_visa === true);
    setNegativeKeywords(p.negative_keywords ?? []);
    setAboutMe(p.about_me ?? "");
    setExcludedCompanies(p.excluded_companies ?? []);
    baselineRef.current = serializePrefs(p);
  }, [preferences]);

  // Build a PreferencesRequest from the current form state. Memoized on every
  // field so the auto-save effect re-runs (and re-debounces) on each edit.
  const buildPrefs = useCallback(
    (): PreferencesRequest => ({
      target_job_titles: targetTitles,
      additional_skills: additionalSkills,
      excluded_skills: excludedSkills,
      preferred_locations: preferredLocations,
      industries: industries,
      salary_min: salaryMin ? Number(salaryMin) : null,
      salary_max: salaryMax ? Number(salaryMax) : null,
      work_arrangement: workArrangement,
      experience_level: experienceLevel,
      negative_keywords: negativeKeywords,
      about_me: aboutMe,
      excluded_companies: excludedCompanies,
      needs_visa: needsVisa,
    }),
    [
      targetTitles,
      additionalSkills,
      excludedSkills,
      preferredLocations,
      industries,
      salaryMin,
      salaryMax,
      workArrangement,
      experienceLevel,
      negativeKeywords,
      aboutMe,
      excludedCompanies,
      needsVisa,
    ]
  );

  // Auto-save: whenever the form differs from the last-saved baseline, save it
  // after a short debounce. No "Save" button — preferences persist the moment
  // you stop editing, the same way the CV/LinkedIn/GitHub inputs do. The
  // debounce timer is cancelled on every change (and on unmount) so only the
  // final state of a burst of edits is sent.
  useEffect(() => {
    if (baselineRef.current === null) return; // not hydrated yet
    const snapshot = serializePrefs(buildPrefs());
    if (snapshot === baselineRef.current) return; // nothing actually changed

    const timer = setTimeout(async () => {
      setSaving(true);
      try {
        await onSave(buildPrefs());
        baselineRef.current = snapshot; // commit baseline only on success
        setSaveFailed(false);
      } catch {
        // Parent surfaces the error via toast; leave the baseline unchanged so
        // the next edit retries the save. Also flag it persistently — the toast
        // disappears in seconds, the status line does not.
        setSaveFailed(true);
      } finally {
        setSaving(false);
      }
    }, AUTOSAVE_DELAY_MS);

    return () => clearTimeout(timer);
  }, [buildPrefs, onSave]);

  return (
    <div className="glass-card rounded-xl p-6 animate-fade-in-up stagger-3">
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg bg-primary/10 ring-1 ring-primary/20">
          <Briefcase className="h-5 w-5 text-primary" />
        </div>
        <div>
          <h3 className="font-heading text-base font-semibold">Preferences</h3>
          <p className="text-xs text-muted-foreground">
            Customize your job search criteria
          </p>
        </div>
      </div>

      <div className="space-y-6">
        {/* ── Target Job Titles ──────────────────── */}
        <TagInput
          label="Target Job Titles"
          icon={<Briefcase className="h-3.5 w-3.5" />}
          tags={targetTitles}
          onChange={setTargetTitles}
          placeholder="e.g. Data Scientist"
          description="Roles you're targeting"
        />

        {/* ── Additional Skills ──────────────────── */}
        <TagInput
          label="Additional Skills"
          tags={additionalSkills}
          onChange={setAdditionalSkills}
          placeholder="e.g. Python, SQL, Terraform"
          description="Skills beyond what your CV contains"
        />

        {/* ── Excluded Skills ────────────────────── */}
        <TagInput
          label="Excluded Skills"
          tags={excludedSkills}
          onChange={setExcludedSkills}
          placeholder="e.g. COBOL, Fortran"
          description="Skills you don't want to work with (penalized in scoring)"
          variant="destructive"
        />

        <Separator />

        {/* ── Preferred Locations ─────────────────── */}
        <TagInput
          label="Preferred Locations"
          icon={<MapPin className="h-3.5 w-3.5" />}
          tags={preferredLocations}
          onChange={setPreferredLocations}
          placeholder="e.g. London, Manchester, Remote"
        />

        {/* ── Industries ─────────────────────────── */}
        <TagInput
          label="Industries"
          icon={<Building2 className="h-3.5 w-3.5" />}
          tags={industries}
          onChange={setIndustries}
          placeholder="e.g. FinTech, Healthcare, AI"
          description="Target industries for relevance scoring bonus"
        />

        <Separator />

        {/* ── Salary Range ───────────────────────── */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">
            <DollarSign className="h-3.5 w-3.5" />
            Salary Range
          </Label>
          <div className="grid grid-cols-2 gap-3">
            <div className="relative">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-muted-foreground font-mono">
                &pound;
              </span>
              <Input
                type="number"
                value={salaryMin}
                onChange={(e) => setSalaryMin(e.target.value)}
                placeholder="Min"
                className="pl-7"
              />
            </div>
            <div className="relative">
              <span className="absolute left-2.5 top-1/2 -translate-y-1/2 text-sm text-muted-foreground font-mono">
                &pound;
              </span>
              <Input
                type="number"
                value={salaryMax}
                onChange={(e) => setSalaryMax(e.target.value)}
                placeholder="Max"
                className="pl-7"
              />
            </div>
          </div>
        </div>

        {/* ── Work Arrangement & Experience Level ── */}
        <div className="grid grid-cols-2 gap-3">
          <div className="space-y-2">
            <Label className="text-sm font-medium">Work Arrangement</Label>
            <Select
              value={workArrangement}
              onValueChange={(v) => setWorkArrangement(v ?? "")}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="any">Any</SelectItem>
                <SelectItem value="remote">Remote</SelectItem>
                <SelectItem value="hybrid">Hybrid</SelectItem>
                <SelectItem value="onsite">Onsite</SelectItem>
              </SelectContent>
            </Select>
          </div>
          <div className="space-y-2">
            <Label className="text-sm font-medium">Experience Level</Label>
            <Select
              value={experienceLevel}
              onValueChange={(v) => setExperienceLevel(v ?? "")}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select..." />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="entry">Entry</SelectItem>
                <SelectItem value="mid">Mid</SelectItem>
                <SelectItem value="senior">Senior</SelectItem>
                <SelectItem value="lead">Lead</SelectItem>
                <SelectItem value="executive">Executive</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>

        {/* ── Visa sponsorship ──────────────────────
            Gates the backend VISA scoring dimension. Before this control
            existed the field was always the default False, so sponsors could
            never be ranked up for the people who need them. Empty/unchecked is
            a real answer ("I don't need sponsorship"), not a missing one. */}
        <label className="flex items-center gap-2 text-sm font-medium cursor-pointer">
          <input
            type="checkbox"
            className="h-4 w-4 rounded border-input"
            checked={needsVisa}
            onChange={(e) => setNeedsVisa(e.target.checked)}
          />
          I need visa sponsorship to work in the UK
        </label>

        <Separator />

        {/* ── Negative Keywords ──────────────────── */}
        <TagInput
          label="Negative Keywords"
          icon={<AlertCircle className="h-3.5 w-3.5" />}
          tags={negativeKeywords}
          onChange={setNegativeKeywords}
          placeholder="e.g. intern, junior, volunteer"
          description="Job title keywords to penalize"
          variant="destructive"
        />

        {/* ── About Me ───────────────────────────── */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">About Me</Label>
          <p className="text-xs text-muted-foreground -mt-1">
            Brief professional summary used for semantic matching
          </p>
          <Textarea
            value={aboutMe}
            onChange={(e) => setAboutMe(e.target.value)}
            placeholder="e.g. Experienced data scientist with 5 years in NLP and computer vision, looking for senior roles in AI-first companies..."
            rows={4}
          />
        </div>

        {/* ── Excluded Companies ──────────────────── */}
        <TagInput
          label="Excluded Companies"
          icon={<Building2 className="h-3.5 w-3.5" />}
          tags={excludedCompanies}
          onChange={setExcludedCompanies}
          placeholder="e.g. Acme Corp"
          description="Companies to zero-out from results"
          variant="destructive"
        />

        {/* ── Auto-save status ───────────────────────
            No "Save" button — preferences save automatically a moment after
            you stop editing (like the CV/LinkedIn/GitHub inputs). This line
            just tells you the state. Hidden until the form has loaded. */}
        {!loading && (
          <div
            className="flex items-center justify-end gap-1.5 pt-1 text-xs text-muted-foreground"
            aria-live="polite"
          >
            {saving ? (
              <>
                <div className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-primary" />
                <span>Saving…</span>
              </>
            ) : saveFailed ? (
              <>
                <AlertCircle className="h-3.5 w-3.5 text-destructive" />
                <span className="text-destructive">
                  Couldn&apos;t save — your next edit will retry
                </span>
              </>
            ) : (
              <>
                <Check className="h-3.5 w-3.5 text-score-high" />
                <span>Changes save automatically</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
