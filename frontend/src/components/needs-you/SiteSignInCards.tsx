"use client";

import { useState } from "react";
import { toast } from "sonner";
import { signedInOnSite } from "@/lib/api";
import type { SiteSignIn } from "@/lib/api";

// Diagnosis D (2026-11-13): one sign-in on a site clears every job waiting on it, so Needs you shows ONE card
// per site, not one per job (the server leaves those jobs out of `job_ids`). A CAPTCHA or "finish on the site"
// stays on its job's card. The host is the server's parsed host name; it is only rendered as text and in an
// https link to that host.

const STEP: Record<string, string> = { "create an account": "Create an account on", "enter the code": "Enter the code on" };

function SiteCard({ site, onChanged }: { site: SiteSignIn; onChanged: () => void }) {
  const [busy, setBusy] = useState(false);
  const n = site.app_ids.length;
  async function done() {
    setBusy(true);
    try {
      await signedInOnSite(site.host);
      onChanged();
    } catch {
      toast.error(`Could not mark ${site.host} signed in.`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <li data-testid={`site-signin-${site.host}`}
      className="flex flex-col gap-2 rounded-xl border border-border bg-card p-5 shadow-card sm:flex-row sm:items-center sm:justify-between">
      <p className="font-heading text-lg leading-snug">
        {STEP[site.word] ?? "Sign in to"} {site.host} — unblocks {n} {n === 1 ? "job" : "jobs"}
      </p>
      <div className="flex items-center gap-3">
        <a href={`https://${site.host}`} target="_blank" rel="noopener noreferrer" data-testid="site-signin-link"
          className="text-sm font-medium underline underline-offset-2">
          Open {site.host}
        </a>
        <button type="button" data-testid="site-signin-done" disabled={busy} onClick={() => void done()}
          className="rounded-lg bg-primary px-3 py-1.5 text-sm font-semibold text-primary-foreground transition-opacity hover:opacity-90 disabled:opacity-50">
          {busy ? "Saving…" : "I've signed in"}
        </button>
      </div>
    </li>
  );
}

export function SiteSignInCards({ sites, onChanged }: { sites: SiteSignIn[]; onChanged: () => void }) {
  if (sites.length === 0) return null;
  return (
    <ul className="flex flex-col gap-3" data-testid="site-signins">
      {sites.map((s) => <SiteCard key={s.host} site={s} onChanged={onChanged} />)}
    </ul>
  );
}
