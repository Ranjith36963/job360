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
import { ClearButton } from "@/components/profile/ClearButton";
import { EditedMark } from "@/components/profile/EditedMark";
import { findAgentEdit, type AgentEdit } from "@/lib/agent-edits";
import type { PreferencesRequest } from "@/lib/types";

interface PreferencesFormProps {
  preferences: Record<string, unknown>;
  /** ProfileResponse.agent_edits (spec R11) — the current agent-edit overlay.
   *  Optional; each editable field looks up its own `preferences.<field>`
   *  path and renders nothing extra when there is no active edit for it. */
  agentEdits?: AgentEdit[];
  /** Adjacent skills the extractor proposed. Shown as one-tap chips beside
   *  Additional Skills.
   *
   *  These live INSIDE this form on purpose. Accepting one only changes local
   *  state, so the existing debounced auto-save persists it — several taps cost
   *  ONE save. Handling the tap in the parent instead would mean a save per
   *  chip (each one triggering a paid re-extraction), and the resulting
   *  `setProfile` would hand this component a brand-new `preferences` object,
   *  re-firing the hydration effect and wiping whatever the user was part-way
   *  through typing in any other field. */
  suggestions?: string[];
  onSave: (prefs: PreferencesRequest) => Promise<void>;
  /** Empty every typed preference. Undoable from History. Omitted when there
   *  is no profile yet — there is nothing to clear. */
  onClear?: () => Promise<void>;
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
  /** One-tap chips shown under the input. Already-added ones are hidden. */
  suggestions?: string[];
  suggestionsLabel?: string;
  suggestionsHint?: string;
  /** Extra content after the label — e.g. an `EditedMark` (spec R11). */
  trailing?: React.ReactNode;
}

function TagInput({
  label,
  icon,
  tags,
  onChange,
  placeholder = "Type and press Enter",
  description,
  variant = "default",
  suggestions,
  suggestionsLabel,
  suggestionsHint,
  trailing,
}: TagInputProps) {
  const [inputValue, setInputValue] = useState("");

  // Shared by typing and by tapping a suggestion. It used to live inline in
  // addTag; a suggestion handler that re-implemented the comparison would
  // eventually disagree with it and let "Python" sit next to "python".
  const appendTag = useCallback(
    (value: string) => {
      const trimmed = value.trim();
      if (!trimmed) return;
      if (tags.some((t) => t.toLowerCase() === trimmed.toLowerCase())) return;
      onChange([...tags, trimmed]);
    },
    [tags, onChange]
  );

  const addTag = useCallback(() => {
    appendTag(inputValue);
    setInputValue("");
  }, [appendTag, inputValue]);

  // Hide a suggestion once it has been taken, so the row shrinks as the user
  // works through it and never offers something already on the list.
  const openSuggestions = (suggestions ?? []).filter(
    (s) =>
      s.trim() && !tags.some((t) => t.toLowerCase() === s.trim().toLowerCase())
  );

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
        {trailing}
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

      {openSuggestions.length > 0 && (
        <div className="pt-1">
          {suggestionsLabel && (
            <p className="mb-1 text-xs font-semibold uppercase tracking-wider text-primary">
              {suggestionsLabel}
            </p>
          )}
          {suggestionsHint && (
            <p className="mb-2 text-xs text-muted-foreground">
              {suggestionsHint}
            </p>
          )}
          <div className="flex flex-wrap gap-1.5">
            {openSuggestions.map((s) => (
              <button
                key={`suggest-${s}`}
                type="button"
                // Not "add" / "save" in the accessible name: the form's tests
                // assert there is no button matching /save preferences/i, and
                // this control must stay clearly distinct from one.
                aria-label={`Add ${s} to ${label}`}
                onClick={() => appendTag(s)}
                className="inline-flex items-center gap-1 rounded-full border border-dashed border-primary/40 bg-primary/5 px-2.5 py-0.5 text-xs font-medium text-primary/90 transition-colors hover:border-primary/70 hover:bg-primary/10"
              >
                <Plus className="h-3 w-3" />
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
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

// The Select can't display "nothing chosen" as a real selected option, so it
// needs its OWN word for that state -- same trick work_arrangement plays with
// "any" a few fields down. The difference: nothing on the backend translates
// this back to "" the way `_normalize_work_arrangement` does for
// work_arrangement -- `_apply_preferences` stores experience_level with a bare
// `.get(pref_dict, "experience_level", "")`, no normalizer. So BOTH directions
// of the "" <-> sentinel translation have to happen here, or the sentinel word
// itself would be posted to the server as though the user had typed it.
const EXPERIENCE_UNSET = "unspecified";

function prefsFromRaw(raw: Record<string, unknown>): PreferencesRequest {
  return {
    target_job_titles: asArr(raw.target_job_titles),
    additional_skills: asArr(raw.additional_skills),
    excluded_skills: asArr(raw.excluded_skills),
    preferred_locations: asArr(raw.preferred_locations),
    industries: asArr(raw.industries),
    salary_min: raw.salary_min != null ? Number(raw.salary_min) : null,
    salary_max: raw.salary_max != null ? Number(raw.salary_max) : null,
    // "any" is this form's WORD for "not set"; the server's word is "". Both
    // read-points must agree, or the saved baseline ("") would never match the
    // form state ("any") and the dirty check would report unsaved changes on a
    // freshly loaded, untouched form.
    work_arrangement:
      typeof raw.work_arrangement === "string" && raw.work_arrangement
        ? raw.work_arrangement
        : "any",
    // Rule #29: an unstated level is silence, never a guess. This is the WIRE
    // value (fed to the auto-save baseline below), so it stays the server's
    // own word -- "" for "not chosen" -- not the Select's display sentinel.
    // Substituting "mid" here used to post a seniority the user never chose:
    // it drives real scoring at SENIORITY_WEIGHT=8, is stated to the LLM
    // judge, and goes into the semantic vector.
    experience_level:
      typeof raw.experience_level === "string" ? raw.experience_level : "",
    negative_keywords: asArr(raw.negative_keywords),
    about_me: typeof raw.about_me === "string" ? raw.about_me : "",
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
    p.needs_visa,
  ]);
}

// Debounce window for auto-save: long enough to batch fast typing, short
// enough to feel instant.
const AUTOSAVE_DELAY_MS = 800;

// ── Preferences Form ───────────────────────────────────────

export function PreferencesForm({
  preferences,
  suggestions,
  onSave,
  onClear,
  loading,
  agentEdits,
}: PreferencesFormProps) {
  const editOf = (field: string) => findAgentEdit(agentEdits, `preferences.${field}`);
  const [targetTitles, setTargetTitles] = useState<string[]>([]);
  const [additionalSkills, setAdditionalSkills] = useState<string[]>([]);
  const [excludedSkills, setExcludedSkills] = useState<string[]>([]);
  const [preferredLocations, setPreferredLocations] = useState<string[]>([]);
  const [industries, setIndustries] = useState<string[]>([]);
  const [salaryMin, setSalaryMin] = useState("");
  const [salaryMax, setSalaryMax] = useState("");
  const [workArrangement, setWorkArrangement] = useState("any");
  const [experienceLevel, setExperienceLevel] = useState(EXPERIENCE_UNSET);
  const [negativeKeywords, setNegativeKeywords] = useState<string[]>([]);
  const [aboutMe, setAboutMe] = useState("");
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
    // `||`, not `??`: the server now stores "I don't mind" as "" rather than the
    // literal "any", because "any" was reaching the LLM judge as a stated
    // constraint. `??` only falls back on null/undefined, so an empty string
    // would leave this select showing nothing at all.
    setWorkArrangement(p.work_arrangement || "any");
    // See EXPERIENCE_UNSET above: `p.experience_level` is the server's wire
    // word ("" for "not chosen"). The Select needs an actual item selected,
    // so an empty value becomes the display sentinel here -- and buildPrefs
    // mirrors this translation back to "" on the way out. Both read-points
    // must agree, or the dirty-check reports unsaved changes on a freshly
    // loaded, untouched form.
    setExperienceLevel(p.experience_level || EXPERIENCE_UNSET);
    setNeedsVisa(p.needs_visa === true);
    setNegativeKeywords(p.negative_keywords ?? []);
    setAboutMe(p.about_me ?? "");
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
      // Mirror the EXPERIENCE_UNSET translation back the other way: the
      // Select's display sentinel is a form-only word and must never reach
      // the wire, or "unspecified" would be stored as though it were a real
      // typed answer.
      experience_level:
        experienceLevel === EXPERIENCE_UNSET ? "" : experienceLevel,
      negative_keywords: negativeKeywords,
      about_me: aboutMe,
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
        {/* In the card header, not at the bottom: this form autosaves, so there
            is no "Save" row to sit beside, and the header is where a
            card-scoped action belongs. */}
        {onClear && (
          <div className="ml-auto">
            <ClearButton
              label="Clear"
              confirmLabel="Click again to clear"
              disabled={loading}
              onConfirm={onClear}
            />
          </div>
        )}
      </div>

      <div className="space-y-6">
        {/* ── Target Job Titles ────────────────────
            `id="target-job-titles"` + `scroll-mt-24` keep this field ready as
            a scroll target for any future deep link — Next.js skips
            fixed/sticky elements when it picks a scroll target, so without
            the margin the field would land underneath the sticky Navbar
            (documented in node_modules/next/dist/docs/…/components/link.md).
            No current caller deep-links here (the dashboard "Searching for…"
            row that used to was deleted with the sourcing era, slice 5). */}
        <div id="target-job-titles" className="scroll-mt-24">
          <TagInput
            label="Target Job Titles"
            icon={<Briefcase className="h-3.5 w-3.5" />}
            tags={targetTitles}
            onChange={setTargetTitles}
            placeholder="e.g. Data Scientist"
            description="Roles you're targeting"
            trailing={<EditedMark edit={editOf("target_job_titles")} />}
          />
        </div>

        {/* ── Additional Skills ──────────────────── */}
        <TagInput
          label="Additional Skills"
          tags={additionalSkills}
          onChange={setAdditionalSkills}
          placeholder="e.g. Python, SQL, Terraform"
          description="Skills beyond what your CV contains"
          suggestions={suggestions}
          suggestionsLabel="Skills that often go with yours"
          suggestionsHint="Tap any you actually have. Nothing is added until you tap."
          trailing={<EditedMark edit={editOf("additional_skills")} />}
        />

        {/* Excluded Skills input removed from UI (owner, 2026-08-08) — the
            excluded_skills field, scoring penalty, and payload key are kept
            fully intact; every existing user's value is empty in prod, so
            hiding the control drops no data and changes no live score. */}

        <Separator />

        {/* ── Preferred Locations ─────────────────── */}
        <TagInput
          label="Preferred Locations"
          icon={<MapPin className="h-3.5 w-3.5" />}
          tags={preferredLocations}
          onChange={setPreferredLocations}
          placeholder="e.g. London, Manchester, Remote"
          trailing={<EditedMark edit={editOf("preferred_locations")} />}
        />

        {/* ── Industries ─────────────────────────── */}
        <TagInput
          label="Industries"
          icon={<Building2 className="h-3.5 w-3.5" />}
          tags={industries}
          onChange={setIndustries}
          placeholder="e.g. FinTech, Healthcare, AI"
          description="Used only for AI similarity matching today — most jobs are not affected by this at all."
          trailing={<EditedMark edit={editOf("industries")} />}
        />

        <Separator />

        {/* ── Salary Range ───────────────────────── */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">
            <DollarSign className="h-3.5 w-3.5" />
            Salary Range
            <EditedMark edit={editOf("salary_min") ?? editOf("salary_max")} />
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
            <Label className="text-sm font-medium">
              Work Arrangement
              <EditedMark edit={editOf("work_arrangement")} />
            </Label>
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
            <Label className="text-sm font-medium">
              Experience Level
              <EditedMark edit={editOf("experience_level")} />
            </Label>
            <Select
              value={experienceLevel}
              onValueChange={(v) => setExperienceLevel(v ?? "")}
            >
              <SelectTrigger className="w-full">
                <SelectValue placeholder="Select..." />
              </SelectTrigger>
              <SelectContent>
                {/* Form-only word for "not chosen" -- see EXPERIENCE_UNSET.
                    Listed first since it is the actual starting state for
                    every account until they pick one. */}
                <SelectItem value={EXPERIENCE_UNSET}>No preference</SelectItem>
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
          <EditedMark edit={editOf("needs_visa")} />
        </label>

        <Separator />

        {/* ── Words to avoid in job titles ────────── */}
        {/* Restored 2026-08-13 with copy that matches what the code does.
            Verified in skill_matcher.py: the check reads job.title ONLY (never
            the description), matches whole words (so "sales" does not match
            "Wholesale"), and subtracts a flat 30 points on the first match —
            it never hides the job. The old label "Negative Keywords" implied a
            filter, which is why the copy below says "ranked lower" instead. */}
        <TagInput
          label="Words to avoid in job titles"
          tags={negativeKeywords}
          onChange={setNegativeKeywords}
          placeholder="e.g. sales, recruiter"
          description="A job whose TITLE contains one of these drops 30 points. It still shows up — this pushes it down the list, it doesn't hide it."
          variant="destructive"
          trailing={<EditedMark edit={editOf("negative_keywords")} />}
        />

        {/* ── About Me ───────────────────────────── */}
        <div className="space-y-2">
          <Label className="text-sm font-medium">
            About Me
            <EditedMark edit={editOf("about_me")} />
          </Label>
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

        {/* Excluded Companies removed entirely (not just the UI control) —
            a grep of backend/src turned up ZERO consumers of the
            `excluded_companies` key anywhere: no scorer, no route, no
            model field. The prior comment here claimed a "scoring
            zero-out" that does not exist in the code. Nothing ever read
            this, so nothing is lost by no longer collecting or sending it. */}

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
