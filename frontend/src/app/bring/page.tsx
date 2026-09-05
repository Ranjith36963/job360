"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { ClipboardPaste, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { bringJob } from "@/lib/api";
import { toast } from "@/lib/toast";

/**
 * Bring a job — the product's front door after the pivot
 * (docs/plans/2026-09-02-bring-a-job/intent.md).
 *
 * Job360 never sources jobs. The user pastes the ad they found; the backend
 * stores it, scores it against their profile and lands it in their feed. On
 * success we go straight to the job page — the same page a search hit opens.
 *
 * Paste, not a URL fetch: LinkedIn/Indeed/Workday block bots and fetching a
 * user-supplied URL is an SSRF surface. The link is kept so the receipt can
 * point back to the ad.
 */
export default function BringJobPage() {
  const router = useRouter();
  const [title, setTitle] = useState("");
  const [company, setCompany] = useState("");
  const [location, setLocation] = useState("");
  const [applyUrl, setApplyUrl] = useState("");
  const [description, setDescription] = useState("");
  const [submitting, setSubmitting] = useState(false);

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
        toast.success("Already in the catalog — opening it.");
      }
      router.push(`/jobs/${res.job.id}`);
    } catch (err) {
      toast.apiError(err, "Couldn't save this job — please check the fields and try again.");
      setSubmitting(false);
    }
  }

  return (
    <div className="mx-auto max-w-3xl px-4 py-8 sm:px-6">
      <div className="mb-6">
        <h1 className="font-heading text-2xl font-semibold tracking-tight">Bring a job</h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Paste the ad you found. We score it against your profile, tailor your CV, and keep
          a receipt of exactly what you sent.
        </p>
      </div>

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
            <Input
              id="bring-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Senior Python Engineer"
              maxLength={300}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="bring-company">Company</Label>
            <Input
              id="bring-company"
              value={company}
              onChange={(e) => setCompany(e.target.value)}
              placeholder="Acme Ltd"
              maxLength={300}
              required
            />
          </div>
          <div className="space-y-2">
            <Label htmlFor="bring-location">Location (optional)</Label>
            <Input
              id="bring-location"
              value={location}
              onChange={(e) => setLocation(e.target.value)}
              placeholder="London, UK · Remote · Berlin"
              maxLength={300}
            />
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
          <Label htmlFor="bring-description">The ad, as written</Label>
          <Textarea
            id="bring-description"
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
            {submitting ? "Scoring…" : "Score this job"}
          </Button>
          <p className="text-xs text-muted-foreground">
            Nothing is sent anywhere. You apply; we keep the record.
          </p>
        </div>
      </form>
    </div>
  );
}
