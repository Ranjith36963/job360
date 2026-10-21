"use client";

import { useEffect, useState, type FormEvent } from "react";
import { toast } from "sonner";
import { updateApplicationJob } from "@/lib/api";
import type { JobFactsPatch } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { JOB_FOUND_ON_VALUES, closedSetLabel } from "@/lib/closed-sets";
import { allCountries, countryName } from "@/lib/countries";
import { Button } from "@/components/ui/button";

export type JobFactsValue = {
  country: string | null;
  remote: boolean | null;
  found_on: string | null;
};

const LABEL = "font-mono text-[11px] font-medium uppercase tracking-[0.09em] text-faint";
const SELECT =
  "h-9 rounded-lg border border-border bg-background px-2.5 text-sm text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring";

const REMOTE_TO_FORM = (r: boolean | null): string => (r === true ? "yes" : r === false ? "no" : "");

/** "France · Remote · Found on Indeed" — only the facts that are set. */
export function describeJobFacts(f: JobFactsValue): string[] {
  const parts: string[] = [];
  if (f.country) parts.push(countryName(f.country));
  if (f.remote === true) parts.push("Remote");
  if (f.remote === false) parts.push("Not remote");
  if (f.found_on) parts.push(`Found on ${closedSetLabel(f.found_on)}`);
  return parts;
}

/** The job's facts (country, remote, where it was found) with a small Edit.
 * Saves optimistically; on failure the old values come back and a toast
 * says so. "Not set" clears a fact (null), never a default. */
export function JobFacts({
  applicationId,
  facts: initial,
}: {
  applicationId: number;
  facts: JobFactsValue;
}) {
  const [facts, setFacts] = useState<JobFactsValue>(initial);
  const [editing, setEditing] = useState(false);
  const [country, setCountry] = useState(initial.country ?? "");
  const [remote, setRemote] = useState(REMOTE_TO_FORM(initial.remote));
  const [foundOn, setFoundOn] = useState(initial.found_on ?? "");
  const [saving, setSaving] = useState(false);

  // A fresh load of the application re-hydrates us.
  useEffect(() => {
    setFacts(initial);
  }, [initial.country, initial.remote, initial.found_on]); // eslint-disable-line react-hooks/exhaustive-deps

  function openEditor() {
    setCountry(facts.country ?? "");
    setRemote(REMOTE_TO_FORM(facts.remote));
    setFoundOn(facts.found_on ?? "");
    setEditing(true);
  }

  async function save(e: FormEvent) {
    e.preventDefault();
    const next: JobFactsValue = {
      country: country || null,
      remote: remote === "yes" ? true : remote === "no" ? false : null,
      found_on: foundOn || null,
    };
    const body: JobFactsPatch = {};
    if (next.country !== facts.country) body.country = next.country;
    if (next.remote !== facts.remote) body.remote = next.remote;
    if (next.found_on !== facts.found_on) body.found_on = next.found_on;
    if (Object.keys(body).length === 0) {
      setEditing(false);
      return;
    }
    const previous = facts;
    setFacts(next);
    setEditing(false);
    setSaving(true);
    try {
      const saved = await updateApplicationJob(applicationId, body);
      // Trust what the server stored (it normalises the country code).
      setFacts({ country: saved.country, remote: saved.remote, found_on: saved.found_on });
      toast.success("Job details saved.");
    } catch (err) {
      setFacts(previous);
      toast.error(apiErrorMessage(err, "Could not save the job details."));
    } finally {
      setSaving(false);
    }
  }

  const parts = describeJobFacts(facts);
  const countries = allCountries();
  const countryKnown = !country || countries.some((c) => c.code === country);

  return (
    <div data-testid="job-facts" className="mt-1 text-sm text-muted-foreground">
      {!editing ? (
        <p className="flex flex-wrap items-baseline gap-x-2">
          {parts.length > 0 && <span data-testid="job-facts-text">{parts.join(" · ")}</span>}
          <button
            type="button"
            data-testid="job-facts-edit"
            onClick={openEditor}
            disabled={saving}
            className="font-mono text-[11px] text-faint underline-offset-2 hover:text-foreground hover:underline disabled:opacity-50"
          >
            {parts.length > 0 ? "Edit" : "Add job details"}
          </button>
        </p>
      ) : (
        <form onSubmit={save} className="mt-2 flex flex-col gap-3 rounded-lg border border-border p-3">
          <div className="grid gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <label htmlFor="job-country" className={LABEL}>Country</label>
              <select id="job-country" value={country} onChange={(e) => setCountry(e.target.value)} className={SELECT}>
                <option value="">Not set</option>
                {countries.map((c) => (
                  <option key={c.code} value={c.code}>
                    {c.name}
                  </option>
                ))}
                {!countryKnown && <option value={country}>{country}</option>}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="job-remote" className={LABEL}>Remote</label>
              <select id="job-remote" value={remote} onChange={(e) => setRemote(e.target.value)} className={SELECT}>
                <option value="">Not set</option>
                <option value="yes">Yes</option>
                <option value="no">No</option>
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label htmlFor="job-found-on" className={LABEL}>Found on</label>
              <select id="job-found-on" value={foundOn} onChange={(e) => setFoundOn(e.target.value)} className={SELECT}>
                <option value="">Not set</option>
                {JOB_FOUND_ON_VALUES.map((v) => (
                  <option key={v} value={v}>
                    {closedSetLabel(v)}
                  </option>
                ))}
              </select>
            </div>
          </div>
          <div className="flex gap-2">
            <Button type="submit" size="sm" className="bg-foreground text-background hover:bg-foreground/90">
              Save
            </Button>
            <Button type="button" size="sm" variant="outline" onClick={() => setEditing(false)} className="border-border bg-card">
              Cancel
            </Button>
          </div>
        </form>
      )}
    </div>
  );
}
