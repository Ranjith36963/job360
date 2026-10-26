"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  Check,
  X,
  Plus,
  Briefcase,
  Building2,
  AlertCircle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Label } from "@/components/ui/label";
import { Separator } from "@/components/ui/separator";
import { ClearButton } from "@/components/profile/ClearButton";
import { EditedMark } from "@/components/profile/EditedMark";
import { FieldHistory } from "@/components/profile/FieldHistory";
import { findAgentEdit, type AgentEdit } from "@/lib/agent-edits";
import type { PreferencesRequest } from "@/lib/types";

interface PreferencesFormProps {
  preferences: Record<string, unknown>;
  /** ProfileResponse.agent_edits (spec R11) — the current agent-edit overlay.
   *  Optional; each editable field looks up its own `preferences.<field>`
   *  path and renders nothing extra when there is no active edit for it. */
  agentEdits?: AgentEdit[];
  /** "Take back" an assistant's change to one path (it falls back to the CV /
   *  form value). Omitted = no Take back button on the marks. */
  onTakeBack?: (path: string) => Promise<void>;
  /** "Keep" an assistant's change as the human's own. Omitted = no Keep button. */
  onKeep?: (path: string) => Promise<void>;
  /** Show the small per-field "History" links. Off when there is no profile
   *  yet — there is no history to show on a first visit. */
  showFieldHistory?: boolean;
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
  /** Extra content under the field — e.g. its `FieldHistory` link. */
  footer?: React.ReactNode;
  /** `data-testid` on the field's outer wrapper, for tests/e2e to target a
   *  specific TagInput among several on this form. */
  testId?: string;
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
  footer,
  testId,
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
      : "border border-border bg-card text-foreground";

  return (
    <div className="space-y-2" data-testid={testId}>
      <Label className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
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
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs font-medium ${badgeClass}`}
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
            <p className="mb-1 font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
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
                className="inline-flex items-center gap-1 rounded-full border border-dashed border-border px-2.5 py-0.5 text-xs font-medium text-muted-foreground transition-colors hover:border-foreground/40 hover:text-foreground"
              >
                <Plus className="h-3 w-3" />
                {s}
              </button>
            ))}
          </div>
        </div>
      )}
      {footer}
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


export function prefsFromRaw(raw: Record<string, unknown>): PreferencesRequest {
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
    // it would be stored on the profile as though the user had stated it,
    // and the agent reading the profile has no way to tell a guess from a
    // real answer.
    experience_level:
      typeof raw.experience_level === "string" ? raw.experience_level : "",
    negative_keywords: asArr(raw.negative_keywords),
    experience_years_min: raw.experience_years_min != null ? Number(raw.experience_years_min) : null,
    experience_years_max: raw.experience_years_max != null ? Number(raw.experience_years_max) : null,
    looking_for: asArr(raw.looking_for),
    dealbreakers: asArr(raw.dealbreakers),
    avoid_companies: asArr(raw.avoid_companies),
    about_me: typeof raw.about_me === "string" ? raw.about_me : "",
    needs_visa: raw.needs_visa === true,
    work_authorization_countries: asArr(raw.work_authorization_countries),
    assistant_notes: asArr(raw.assistant_notes),
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
    p.experience_years_min,
    p.experience_years_max,
    p.looking_for,
    p.dealbreakers,
    p.avoid_companies,
    p.about_me,
    p.needs_visa,
    p.work_authorization_countries,
    p.assistant_notes,
  ]);
}

/** The keys THIS card sends. Everything else is left out of the save, and the
 *  server keeps an omitted key as stored, so this card can never overwrite job
 *  targets, salary, visa answers or notes edited elsewhere (Memory, Home, the
 *  assistant) from a stale copy. excluded_skills has no control but is sent
 *  back unchanged, as before. */
function formPart(p: PreferencesRequest): PreferencesRequest {
  return {
    additional_skills: p.additional_skills,
    excluded_skills: p.excluded_skills,
    industries: p.industries,
    negative_keywords: p.negative_keywords,
    about_me: p.about_me,
  };
}

// Debounce window for auto-save: long enough to batch fast typing, short
// enough to feel instant.
const AUTOSAVE_DELAY_MS = 800;

// Every editable `preferences.*` path on this form, with the human label used
// both by the combined "See history" view (one row per change, labelled by
// field) and nowhere else — replaces the ~12 separate per-field FieldHistory
// instances that used to sit under every input (owner decision, 2026-09-27).
// Job targets, salary, visa and work arrangement moved to Memory -> Job
// targets (owner, 2026-10-26); this card keeps what describes you and your CV.
const ALL_PREFERENCE_FIELDS: { field: string; label: string }[] = [
  { field: "additional_skills", label: "Additional Skills" },
  { field: "industries", label: "Industries" },
  { field: "negative_keywords", label: "Words to avoid in job titles" },
  { field: "about_me", label: "About Me" },
];

// ── Preferences Form ───────────────────────────────────────

export function PreferencesForm({
  preferences,
  suggestions,
  onSave,
  onClear,
  loading,
  agentEdits,
  onTakeBack,
  onKeep,
  showFieldHistory = false,
}: PreferencesFormProps) {
  const editOf = (field: string) => findAgentEdit(agentEdits, `preferences.${field}`);
  // The "Changed by … · was …" mark (+ Take back) for one field.
  const markOf = (field: string) => (
    <EditedMark edit={editOf(field)} onTakeBack={onTakeBack} onKeep={onKeep} />
  );
  const [additionalSkills, setAdditionalSkills] = useState<string[]>([]);
  const [excludedSkills, setExcludedSkills] = useState<string[]>([]);
  const [industries, setIndustries] = useState<string[]>([]);
  const [negativeKeywords, setNegativeKeywords] = useState<string[]>([]);
  const [aboutMe, setAboutMe] = useState("");
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
    // eslint-disable-next-line react-hooks/set-state-in-effect -- existing working effect; refactor tracked
    setAdditionalSkills(p.additional_skills ?? []);
    setExcludedSkills(p.excluded_skills ?? []);
    setIndustries(p.industries ?? []);
    setNegativeKeywords(p.negative_keywords ?? []);
    setAboutMe(p.about_me ?? "");
    baselineRef.current = serializePrefs(formPart(p));
  }, [preferences]);

  // Build a PreferencesRequest from the current form state. Memoized on every
  // field so the auto-save effect re-runs (and re-debounces) on each edit.
  const buildPrefs = useCallback(
    (): PreferencesRequest => ({
      additional_skills: additionalSkills,
      excluded_skills: excludedSkills,
      industries: industries,
      negative_keywords: negativeKeywords,
      about_me: aboutMe,
    }),
    [additionalSkills, excludedSkills, industries, negativeKeywords, aboutMe]
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
    <div
      id="preferences"
      className="rounded-xl border border-border bg-card p-6 animate-fade-in-up stagger-3 scroll-mt-24"
    >
      <div className="flex items-center gap-3 mb-6">
        <div className="flex h-10 w-10 items-center justify-center rounded-lg border border-border bg-muted">
          <Briefcase className="h-5 w-5 text-muted-foreground" />
        </div>
        <div>
          <h3 className="font-heading text-base font-medium">Your preferences</h3>
          <p className="font-mono text-[11px] text-faint">
            What you want — your assistant reads this when it judges a job
          </p>
        </div>
        {/* In the card header, not at the bottom: this form autosaves, so there
            is no "Save" row to sit beside, and the header is where a
            card-scoped action belongs. ONE combined history for the whole
            section (owner decision, 2026-09-27) — replaces the ~12 per-field
            "History" links that used to sit under every input; same
            FieldHistory component, given every preference path at once, so
            the merged list still separates what changed by field. */}
        <div className="ml-auto flex items-center gap-3">
          {showFieldHistory && (
            <FieldHistory
              label="Your preferences"
              buttonLabel="See history"
              paths={ALL_PREFERENCE_FIELDS.map((f) => `preferences.${f.field}`)}
              pathLabels={Object.fromEntries(
                ALL_PREFERENCE_FIELDS.map((f) => [`preferences.${f.field}`, f.label])
              )}
            />
          )}
          {onClear && (
            <ClearButton
              label="Clear"
              confirmLabel="Click again to clear"
              disabled={loading}
              onConfirm={onClear}
            />
          )}
        </div>
      </div>

      <div className="space-y-6">

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
          trailing={markOf("additional_skills")}
        />

        {/* Excluded Skills input removed from UI (owner, 2026-08-08) — the
            excluded_skills field and payload key are kept fully intact;
            every existing user's value is empty in prod, so hiding the
            control drops no data. */}

        <Separator />

        {/* ── Industries ─────────────────────────── */}
        <TagInput
          label="Industries"
          icon={<Building2 className="h-3.5 w-3.5" />}
          tags={industries}
          onChange={setIndustries}
          placeholder="e.g. FinTech, Healthcare, AI"
          description="Context for your assistant — most jobs are not affected by this at all."
          trailing={markOf("industries")}
        />

        <Separator />

        {/* ── Words to avoid in job titles ────────── */}
        {/* Restored 2026-08-13 with copy that matches what the code does.
            These are titles/roles the user does not want. Job360 no longer
            scores or filters jobs itself (the user's own agent does that) —
            this list is stored on the profile so the agent can read it when
            judging whether a job fits. */}
        <TagInput
          label="Words to avoid in job titles"
          tags={negativeKeywords}
          onChange={setNegativeKeywords}
          placeholder="e.g. sales, recruiter"
          description="Titles or roles you don't want. Your assistant reads this when judging fit."
          variant="destructive"
          trailing={markOf("negative_keywords")}
        />

        <Separator />

        {/* ── About Me ───────────────────────────── */}
        <div className="space-y-2">
          <Label className="font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint">
            About Me
            {markOf("about_me")}
          </Label>
          <p className="text-xs text-muted-foreground -mt-1">
            Brief professional summary — your assistant uses it as context
          </p>
          <Textarea
            value={aboutMe}
            onChange={(e) => setAboutMe(e.target.value)}
            placeholder="e.g. Experienced data scientist with 5 years in NLP and computer vision, looking for senior roles in AI-first companies..."
            rows={4}
          />
        </div>

        {/* "Things your AI assistant should know" lives on Home only (owner,
            2026-10-19). The notes are still hydrated and sent back in every
            save above, so a Profile save never wipes them. */}

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
                <Check className="h-3.5 w-3.5 text-success" />
                <span>Changes save automatically</span>
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
