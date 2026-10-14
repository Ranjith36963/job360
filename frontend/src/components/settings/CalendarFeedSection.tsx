"use client";

import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { createCalendarFeed, getCalendarFeed, revokeCalendarFeed } from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import {
  CARD_CLASS,
  TITLE_CLASS,
  DESC_CLASS,
  PRIMARY_BTN_CLASS,
  QUIET_BTN_CLASS,
  DANGER_BTN_CLASS,
} from "@/components/connect/ConnectSteps";

/**
 * "Calendar feed" - a private .ics URL for interviews and follow-ups.
 *
 * The URL is a bearer credential: anyone who holds it sees the employers on it.
 * The server keeps only a hash, so the plain URL exists in this component's state
 * for as long as the page is open and is never refetched - lose it and you
 * regenerate (which kills the old one). The URL element carries
 * `data-sentry-mask` so Session Replay (maskAllText is off) never records it.
 */
export function CalendarFeedSection() {
  const [exists, setExists] = useState<boolean | null>(null);
  const [url, setUrl] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    let cancelled = false;
    getCalendarFeed()
      .then((s) => {
        if (!cancelled) setExists(s.exists);
      })
      .catch(() => {
        if (!cancelled) setExists(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  async function onCreate() {
    setBusy(true);
    setError(null);
    setCopied(false);
    try {
      const made = await createCalendarFeed();
      setUrl(made.url);
      setExists(true);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not create the calendar feed."));
    } finally {
      setBusy(false);
    }
  }

  async function onRevoke() {
    setBusy(true);
    setError(null);
    try {
      await revokeCalendarFeed();
      setUrl(null);
      setExists(false);
    } catch (err) {
      setError(apiErrorMessage(err, "Could not revoke the calendar feed."));
    } finally {
      setBusy(false);
    }
  }

  async function onCopy() {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
    } catch {
      setError("Could not copy - select the link and copy it by hand.");
    }
  }

  return (
    <Card className={CARD_CLASS}>
      <CardHeader>
        <CardTitle className={TITLE_CLASS}>Calendar feed</CardTitle>
        <CardDescription className={DESC_CLASS}>
          Subscribe in Google Calendar, Apple Calendar or Outlook to see your interviews and
          follow-up dates. Treat the link like a password: anyone who has it can see which
          employers you are talking to. Regenerate it to kill the old link.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {url && (
          <div className="space-y-2">
            <p
              data-testid="calendar-feed-url"
              data-sentry-mask
              className="break-all rounded-md border border-border bg-card px-3 py-2 font-mono text-xs"
            >
              {url}
            </p>
            <p className="text-xs text-muted-foreground">
              Shown once. Copy it now - after you leave this page only a hash is kept.
            </p>
          </div>
        )}
        <div className="flex flex-wrap gap-2">
          {url && (
            <Button type="button" className={PRIMARY_BTN_CLASS} data-testid="calendar-feed-copy" onClick={() => void onCopy()}>
              {copied ? "Copied" : "Copy link"}
            </Button>
          )}
          <Button
            type="button"
            className={url ? QUIET_BTN_CLASS : PRIMARY_BTN_CLASS}
            data-testid="calendar-feed-create"
            disabled={busy || exists === null}
            onClick={() => void onCreate()}
          >
            {busy ? "Working..." : exists ? "Regenerate link" : "Create calendar link"}
          </Button>
          {exists && (
            <Button
              type="button"
              className={DANGER_BTN_CLASS}
              data-testid="calendar-feed-revoke"
              disabled={busy}
              onClick={() => void onRevoke()}
            >
              Revoke
            </Button>
          )}
        </div>
        {exists && !url && (
          <p className="text-xs text-muted-foreground">
            A calendar link is active. It cannot be shown again - regenerate it to get a new one.
          </p>
        )}
        {error && (
          <p className="text-xs text-destructive" role="alert">
            {error}
          </p>
        )}
      </CardContent>
    </Card>
  );
}
