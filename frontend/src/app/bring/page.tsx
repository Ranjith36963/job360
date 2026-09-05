"use client";

import { useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { ClipboardPaste, Link2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { bringJob, fetchJobUrl, type FetchUrlResponse } from "@/lib/api";
import { FETCH_URL_MESSAGES, type FetchUrlOutcome } from "@/lib/url-fetch-messages";
import { toast } from "@/lib/toast";

// R11/C5 — build-time hide only; the backend's URL_FETCH_ENABLED (404 on the
// route) is the control that actually stops the surface without a rebuild.
// Default ON: only an EXPLICIT "false" hides the box.
const URL_FETCH_UI_ENABLED = process.env.NEXT_PUBLIC_URL_FETCH_ENABLED !== "false";

/**
 * Bring a job — the product's front door
 * (docs/plans/2026-09-05-delete-sourcing-era), plus the URL-fetch web
 * fallback (docs/plans/2026-09-04-url-fetch/spec.md, R12).
 *
 * Job360 never sources, scores or ranks jobs (VISION rule 4). The user
 * pastes the ad they found, OR pastes a link and lets us try to fill the
 * form for them; either way the backend stores it and births the
 * Application. On success we go straight to that application's page —
 * where the tailor fallback and the receipt live. Paste is always the
 * fallback — a fetch pre-fills the form, it never submits it, and a fetch
 * that fails (or is disabled) always leaves the paste box ready with the
 * link kept (intent constraint 4).
 */
export default function BringJobPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [applyUrl, setApplyUrl] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const [fetchingUrl, setFetchingUrl] = useState(false);
  const [fetchOutcome, setFetchOutcome] = useState<FetchUrlOutcome | null>(null);
  const [fetchMessage, setFetchMessage] = useState("");
  const [filledFields, setFilledFields] = useState<string[]>([]);

  const titleInputRef = useRef<HTMLInputElement>(null);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);

  const canSubmit =
    title.trim().length > 0 &&
    company.trim().length > 0 &&
    description.trim().length > 0 &&
    !submitting;

  async function submit() {
    if (!canSubmit) return;
    setSubmitting(true);
    try {
      const res = await bringJob({
        title,
        company,
        location,
        apply_url: applyUrl,
        description,
      });
      if (res.existing) {
        toast.success("Already brought — opening it.");
      }
      router.push(`/applications/${res.application_id}`);
    } catch (err) {
      toast.apiError(err, "Couldn't save this job — please check the fields and try again.");
      setSubmitting(false);
    }
  }

  async function fetchFromLink() {
    const url = applyUrl.trim();
    if (!url || fetchingUrl) return;
    setFetchingUrl(true);
    setFetchOutcome(null);
    setFetchMessage("");
    try {
      const res: FetchUrlResponse = await fetchJobUrl(url);
      setFetchOutcome(res.outcome);
      // The server's message is shown when it sent one; the frontend's OWN
      // copy map (url-fetch-messages.ts) is the fallback — never a blank
      // sentence, and never a value the wire alone controls (spec item 40).
      setFetchMessage(res.message || FETCH_URL_MESSAGES[res.outcome]);
      if (res.outcome === "ok") {
        // B5 — per field, never blanket: an "ok" outcome can still leave
        // individual fields empty (e.g. the heuristic rung found a
        // description but no company). Unconditionally calling every
        // setter clobbered whatever the user had already typed with "".
        if (res.title) setTitle(res.title);
        if (res.company) setCompany(res.company);
        if (res.location) setLocation(res.location);
        if (res.description) setDescription(res.description);
        setApplyUrl(res.final_url || url);
        setFilledFields(res.found ?? []);
        // The form is NOT submitted — the user still reviews and presses
        // "Bring this job" themselves (intent constraint 4).
        titleInputRef.current?.focus();
      } else {
        // Never touch title/company/location/description here — a failed
        // fetch must not half-fill the form, and the paste box must stay
        // exactly as the user left it (spec R12).
        setFilledFields([]);
        descriptionRef.current?.focus();
      }
    } catch (err) {
      // A transport-level failure (network down, 429, 404 disabled) — same
      // fallback behaviour as a closed "outcome": show a sentence, keep the
      // link, focus the paste box.
      setFetchOutcome(null);
      toast.apiError(err, "Couldn't fetch that link — paste the ad text below instead.");
      descriptionRef.current?.focus();
    } finally {
      setFetchingUrl(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Bring a job</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste a link and we&apos;ll try to fill the form, or paste the ad you found. We keep
          it, tailor your CV, and keep a receipt of exactly what you sent.
        </p>
      </div>

      {URL_FETCH_UI_ENABLED && (
        <div className="mb-6 space-y-2 rounded-lg border border-border bg-muted/30 p-4">
          <Label htmlFor="bring-fetch-url">Fetch a job from a link</Label>
          <div className="flex gap-2">
            <Input
              id="bring-fetch-url"
              type="url"
              inputMode="url"
              value={applyUrl}
              onChange={(e) => setApplyUrl(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  // R12 — Enter fetches; it must NEVER submit the surrounding form.
                  e.preventDefault();
                  void fetchFromLink();
                }
              }}
              placeholder="https://boards.greenhouse.io/acme/jobs/12345"
              maxLength={2000}
              disabled={fetchingUrl}
            />
            <Button
              type="button"
              variant="secondary"
              className="gap-2"
              disabled={fetchingUrl || !applyUrl.trim()}
              onClick={() => void fetchFromLink()}
            >
              {fetchingUrl ? <Loader2 className="h-4 w-4 animate-spin" /> : <Link2 className="h-4 w-4" />}
              Fetch
            </Button>
          </div>
          {fetchOutcome && (
            <p
              data-testid="bring-fetch-outcome"
              className={fetchOutcome === "ok" ? "text-sm text-muted-foreground" : "text-sm text-destructive"}
            >
              {fetchMessage}
            </p>
          )}
        </div>
      )}

      <form
        onSubmit={(e) => {
          e.preventDefault();
          void submit();
        }}
        className="space-y-5"
        aria-label="Bring a job"
      >
        <div className="grid gap-5 sm:grid-cols-2">
          <div className="space-y-2">
            <Label htmlFor="bring-title">Job title</Label>
            <div className="flex items-center gap-2">
              <Input
                id="bring-title"
                ref={titleInputRef}
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder="Senior Python Engineer"
                maxLength={300}
                required
              />
              {filledFields.includes("title") && (
                <span
                  data-testid="bring-filled-title"
                  className="shrink-0 text-xs text-muted-foreground"
                  title="Filled from the link — check it"
                >
                  filled
                </span>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="bring-company">Company</Label>
            <div className="flex items-center gap-2">
              <Input
                id="bring-company"
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="Acme Ltd"
                maxLength={300}
                required
              />
              {filledFields.includes("company") && (
                <span
                  data-testid="bring-filled-company"
                  className="shrink-0 text-xs text-muted-foreground"
                  title="Filled from the link — check it"
                >
                  filled
                </span>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="bring-location">Location (optional)</Label>
            <div className="flex items-center gap-2">
              <Input
                id="bring-location"
                value={location}
                onChange={(e) => setLocation(e.target.value)}
                placeholder="London, UK · Remote · Berlin"
                maxLength={300}
              />
              {filledFields.includes("location") && (
                <span
                  data-testid="bring-filled-location"
                  className="shrink-0 text-xs text-muted-foreground"
                  title="Filled from the link — check it"
                >
                  filled
                </span>
              )}
            </div>
          </div>
          <div className="space-y-2">
            <Label htmlFor="bring-url">Link to the ad (optional)</Label>
            <Input
              id="bring-url"
              type="url"
              inputMode="url"
              value={applyUrl}
              onChange={(e) => setApplyUrl(e.target.value)}
              placeholder="https://…"
              maxLength={2000}
              pattern="https?://.*"
              title="Must start with http:// or https://"
            />
          </div>
        </div>

        <div className="space-y-2">
          <div className="flex items-center gap-2">
            <Label htmlFor="bring-description">The ad, as written</Label>
            {filledFields.includes("description") && (
              <span
                data-testid="bring-filled-description"
                className="shrink-0 text-xs text-muted-foreground"
                title="Filled from the link — check it"
              >
                filled
              </span>
            )}
          </div>
          <Textarea
            id="bring-description"
            ref={descriptionRef}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder="Paste the full job description here."
            className="min-h-[16rem] font-mono text-sm"
            maxLength={40000}
            required
          />
          <p className="text-xs text-muted-foreground">
            {description.length.toLocaleString()} / 40,000 characters
          </p>
        </div>

        <div className="flex items-center gap-3">
          <Button type="submit" disabled={!canSubmit} className="gap-2">
            {submitting ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ClipboardPaste className="h-4 w-4" />
            )}
            {submitting ? "Saving…" : "Bring this job"}
          </Button>
          <p className="text-xs text-muted-foreground">
            Nothing is sent anywhere. You apply; we keep the record.
          </p>
        </div>
      </form>
    </div>
  );
}
