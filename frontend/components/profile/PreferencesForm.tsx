"use client";

import { useCallback, useEffect, useState } from "react";
import {
  Save,
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
import { Badge } from "@/components/ui/badge";
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
  const [saving, setSaving] = useState(false);

  // Hydrate form from preferences prop
  useEffect(() => {
    if (!preferences) return;

    const asArr = (val: unknown): string[] => {
      if (Array.isArray(val)) return val.map(String);
      return [];
    };

    setTargetTitles(asArr(preferences.target_job_titles));
    setAdditionalSkills(asArr(preferences.additional_skills));
    setExcludedSkills(asArr(preferences.excluded_skills));
    setPreferredLocations(asArr(preferences.preferred_locations));
    setIndustries(asArr(preferences.industries));
    setSalaryMin(
      preferences.salary_min != null ? String(preferences.salary_min) : ""
    );
    setSalaryMax(
      preferences.salary_max != null ? String(preferences.salary_max) : ""
    );
    setWorkArrangement(
      typeof preferences.work_arrangement === "string"
        ? preferences.work_arrangement
        : "any"
    );
    setExperienceLevel(
      typeof preferences.experience_level === "string"
        ? preferences.experience_level
        : "mid"
    );
    setNegativeKeywords(asArr(preferences.negative_keywords));
    setAboutMe(
      typeof preferences.about_me === "string" ? preferences.about_me : ""
    );
    setExcludedCompanies(asArr(preferences.excluded_companies));
  }, [preferences]);

  const handleSubmit = useCallback(
    async (e: React.FormEvent) => {
      e.preventDefault();
      setSaving(true);
      try {
        await onSave({
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
        });
      } finally {
        setSaving(false);
      }
    },
    [
      onSave,
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
    ]
  );

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

      <form onSubmit={handleSubmit} className="space-y-6">
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

        {/* ── Save Button ────────────────────────── */}
        <Button
          type="submit"
          disabled={saving || loading}
          className="w-full h-10 gap-2 bg-primary text-primary-foreground shadow-lg shadow-primary/25 hover:shadow-primary/40 hover:brightness-110 transition-all"
        >
          {saving ? (
            <div className="h-4 w-4 animate-spin rounded-full border-2 border-primary-foreground/30 border-t-primary-foreground" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {saving ? "Saving..." : "Save Preferences"}
        </Button>
      </form>
    </div>
  );
}
