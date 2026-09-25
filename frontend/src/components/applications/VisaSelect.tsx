"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { setApplicationVisa, type VisaShape } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** Plain-word summary of the currently-stored signal, for the folded view. */
const SIGNAL_SUMMARY: Record<string, string> = {
  unknown: "Ad says nothing",
  sponsors: "Sponsors visas",
  no_sponsorship: "No sponsorship",
};

/** What the ad says about sponsorship (slice 7, #514). Job360 never guesses —
 *  this hydrates from the currently-stored visa fields and writes back the
 *  same three: signal, country, detail. Folded by default (owner decision 4,
 *  2026-09-24): a one-line summary with an Edit button reveals the form. */
export function VisaSelect({
  applicationId,
  visa,
  onSaved,
}: {
  applicationId: number;
  visa: VisaShape;
  onSaved: () => Promise<void>;
}) {
  const [editing, setEditing] = useState(false);
  const [signal, setSignal] = useState(visa.signal);
  const [country, setCountry] = useState(visa.country ?? "");
  const [detail, setDetail] = useState(visa.detail ?? "");
  const [saving, setSaving] = useState(false);

  // Re-hydrate whenever the stored visa changes underneath us (e.g. a fresh
  // `load()` after another save).
  useEffect(() => {
    setSignal(visa.signal);
    setCountry(visa.country ?? "");
    setDetail(visa.detail ?? "");
  }, [visa.signal, visa.country, visa.detail]);

  const save = useCallback(async () => {
    setSaving(true);
    try {
      await setApplicationVisa(applicationId, {
        visa_signal: signal,
        visa_country: country.trim().toUpperCase(),
        visa_detail: detail,
      });
      setEditing(false);
      await onSaved();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not save the visa signal."));
    } finally {
      setSaving(false);
    }
  }, [applicationId, signal, country, detail, onSaved]);

  // Cancel throws the draft away: re-hydrate from what's actually stored, so
  // reopening Edit never shows an abandoned edit as if it were the seeker's
  // considered signal (Job360 never guesses).
  const cancel = useCallback(() => {
    setSignal(visa.signal);
    setCountry(visa.country ?? "");
    setDetail(visa.detail ?? "");
    setEditing(false);
  }, [visa.signal, visa.country, visa.detail]);

  const summary = SIGNAL_SUMMARY[visa.signal] ?? visa.signal;

  // Owner decision, 2026-09-25 — the caller (ApplicationClient's
  // `section-visa`) already renders the "Visa / sponsorship" heading; this
  // component used to render its own copy of the same text right below it,
  // so the words appeared twice. This is the ONLY place VisaSelect is used,
  // so dropping its own heading here is safe.
  if (!editing) {
    return (
      <div>
        <div className="flex items-center justify-between gap-2">
          <p data-testid="visa-summary" className="text-sm text-muted-foreground">
            {summary}
            {visa.country ? ` · ${visa.country}` : ""}
          </p>
          <Button
            type="button"
            size="sm"
            variant="outline"
            data-testid="visa-edit"
            onClick={() => setEditing(true)}
          >
            Edit
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-2">
      <p className="text-xs text-muted-foreground">
        What the ad says about sponsorship. Job360 never guesses — leave it as
        &quot;Ad says nothing&quot; unless the ad says.
      </p>
      <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
        <div className="space-y-1">
          <Label htmlFor="visa-select-signal" className="text-xs font-medium">
            Sponsorship
          </Label>
          <select
            id="visa-select-signal"
            data-testid="visa-select"
            value={signal}
            onChange={(e) => setSignal(e.target.value)}
            className="h-9 w-full rounded-md border border-input bg-transparent px-2 text-sm"
          >
            <option value="unknown">Ad says nothing</option>
            <option value="sponsors">Sponsors visas</option>
            <option value="no_sponsorship">No sponsorship</option>
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor="visa-select-country" className="text-xs font-medium">
            Country
          </Label>
          <Input
            id="visa-select-country"
            data-testid="visa-country"
            value={country}
            onChange={(e) => setCountry(e.target.value)}
            maxLength={2}
            placeholder="Country (e.g. GB)"
          />
        </div>
        <div className="space-y-1">
          <Label htmlFor="visa-select-detail" className="text-xs font-medium">
            The ad&apos;s sentence
          </Label>
          <Input
            id="visa-select-detail"
            data-testid="visa-detail"
            value={detail}
            onChange={(e) => setDetail(e.target.value)}
            placeholder="The sentence in the ad, if any"
          />
        </div>
      </div>
      <div className="flex gap-2">
        <Button
          type="button"
          size="sm"
          data-testid="visa-save"
          disabled={saving}
          onClick={() => void save()}
          className="self-start"
        >
          {saving ? "Saving…" : "Save"}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="outline"
          data-testid="visa-cancel"
          disabled={saving}
          onClick={cancel}
          className="self-start"
        >
          Cancel
        </Button>
      </div>
    </div>
  );
}
