"use client";

import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import { setApplicationVisa, type VisaShape } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";

/** What the ad says about sponsorship (slice 7, #514). Job360 never guesses —
 *  this hydrates from the currently-stored visa fields and writes back the
 *  same three: signal, country, detail. */
export function VisaSelect({
  applicationId,
  visa,
  onSaved,
}: {
  applicationId: number;
  visa: VisaShape;
  onSaved: () => Promise<void>;
}) {
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
      await onSaved();
    } catch (err) {
      toast.error(apiErrorMessage(err, "Could not save the visa signal."));
    } finally {
      setSaving(false);
    }
  }, [applicationId, signal, country, detail, onSaved]);

  return (
    <div className="mt-3 flex flex-col gap-2">
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
    </div>
  );
}
