"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { toast } from "sonner";

import {
  Channel,
  ChannelTestResult,
  createChannel,
  deleteChannel,
  listChannels,
  testChannel,
} from "@/lib/api";
import { apiErrorMessage } from "@/lib/api-error";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { EmptyState } from "@/components/ui/empty-state";

// ---------------------------------------------------------------------------
// Email add form — the primary, supported delivery channel
// ---------------------------------------------------------------------------

function EmailAddForm({ onRefresh }: { onRefresh: () => void }) {
  const [email, setEmail] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  function isValidEmail(v: string) {
    return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!isValidEmail(email)) {
      setError("Please enter a valid email address.");
      return;
    }

    setSubmitting(true);
    try {
      await createChannel({
        channel_type: "email",
        display_name: email,
        credential: email,
      });
      setEmail("");
      await onRefresh();
      toast.success("Email channel added");
    } catch (err) {
      const msg =
        apiErrorMessage(err, "Failed to add email channel");
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate>
      <div className="space-y-2">
        <Label htmlFor="email-cred">Your email address</Label>
        <div className="flex gap-2">
          <Input
            id="email-cred"
            type="email"
            autoComplete="email"
            placeholder="you@example.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
          <Button type="submit" disabled={submitting}>
            {submitting ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Webhook add form — advanced, unsupported raw-JSON escape hatch
// ---------------------------------------------------------------------------

function WebhookAddForm({ onRefresh }: { onRefresh: () => void }) {
  const [url, setUrl] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    if (!url.startsWith("https://") && !url.startsWith("http://")) {
      setError("Webhook URL must start with https:// or http://");
      return;
    }

    setSubmitting(true);
    try {
      await createChannel({
        channel_type: "webhook",
        display_name: "Webhook",
        credential: url,
      });
      setUrl("");
      await onRefresh();
      toast.success("Webhook added");
    } catch (err) {
      const msg =
        apiErrorMessage(err, "Failed to add webhook");
      setError(msg);
      toast.error(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={onSubmit} className="space-y-3" noValidate>
      <div className="space-y-2">
        <Label htmlFor="webhook-url">Webhook URL (https://&hellip;)</Label>
        <p className="text-xs text-muted-foreground">
          Job360 will POST a raw JSON payload with matching job details to
          this URL. This is a DIY integration point for your own tooling —
          there is no retry UI, no delivery guarantee beyond best effort, and
          no support for third-party chat apps.
        </p>
        <div className="flex gap-2">
          <Input
            id="webhook-url"
            type="url"
            placeholder="https://your-host/your-path"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            className="font-mono text-xs"
          />
          <Button type="submit" variant="outline" disabled={submitting}>
            {submitting ? "Adding…" : "Add"}
          </Button>
        </div>
      </div>
      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}

// ---------------------------------------------------------------------------
// Page
// ---------------------------------------------------------------------------

export default function ChannelsSettingsPage() {
  const [channels, setChannels] = useState<Channel[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const [lastTest, setLastTest] = useState<Record<number, ChannelTestResult>>({});
  const [testing, setTesting] = useState<number | null>(null);

  async function refresh() {
    setLoading(true);
    setError(null);
    try {
      const rows = await listChannels();
      setChannels(rows);
    } catch (err) {
      setError(apiErrorMessage(err, "Failed to load channels"));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function onDelete(id: number) {
    try {
      await deleteChannel(id);
      await refresh();
      toast.success("Channel removed");
    } catch (err) {
      const msg =
        apiErrorMessage(err, "Failed to delete channel");
      setError(msg);
      toast.error(msg);
    }
  }

  async function onTest(id: number) {
    setTesting(id);
    try {
      const result = await testChannel(id);
      setLastTest((prev) => ({ ...prev, [id]: result }));
      if (result.ok) {
        toast.success("Test message delivered");
      } else {
        toast.error(result.error ?? "Test failed");
      }
    } catch (err) {
      const errMsg = apiErrorMessage(err, "Test failed");
      setLastTest((prev) => ({ ...prev, [id]: { ok: false, error: errMsg } }));
      toast.error(errMsg);
    } finally {
      setTesting(null);
    }
  }

  return (
    <div className="mx-auto max-w-3xl space-y-8 py-12">
      <div>
        <h1 className="text-3xl font-semibold">Notification channels</h1>
        {/* No fixed threshold or cadence here. Both are per-user settings
            (score_threshold, notify_mode, interval_hours) editable on
            /settings/notifications, so stating "≥80, daily" would contradict
            whatever the user has actually saved. */}
        <p className="mt-2 text-muted-foreground">
          Send matching jobs to your email. When they arrive, and how good a
          match they have to be, is up to you — set that in{" "}
          <Link
            href="/settings/notifications"
            className="underline underline-offset-4"
          >
            notification settings
          </Link>
          .
        </p>
      </div>

      {/* ----------------------------------------------------------------- */}
      {/* Email — the supported, recommended channel                        */}
      {/* ----------------------------------------------------------------- */}
      <Card className="border-primary/30">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle>Email</CardTitle>
            <Badge variant="default">Recommended</Badge>
          </div>
          <CardDescription>
            Receive job matches in your inbox. This is the supported way to
            get notified.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <EmailAddForm onRefresh={refresh} />
        </CardContent>
      </Card>

      {/* ----------------------------------------------------------------- */}
      {/* Webhook — advanced, unsupported                                    */}
      {/* ----------------------------------------------------------------- */}
      <Card className="border-dashed opacity-90">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle className="text-base">Webhook</CardTitle>
            <Badge variant="outline">Advanced</Badge>
          </div>
          <CardDescription>
            Post job matches as raw JSON to any HTTP endpoint you control.
            Meant for your own scripts and tooling — not officially
            supported.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <WebhookAddForm onRefresh={refresh} />
        </CardContent>
      </Card>

      {error && (
        <p className="text-sm text-red-400" role="alert">
          {error}
        </p>
      )}

      {/* ----------------------------------------------------------------- */}
      {/* Configured channels list                                           */}
      {/* ----------------------------------------------------------------- */}
      <div className="space-y-4">
        <h2 className="text-xl font-semibold">Configured channels</h2>
        {loading ? (
          <p className="text-sm text-muted-foreground">Loading&hellip;</p>
        ) : channels.length === 0 ? (
          <EmptyState
            title="No channels yet"
            description="Add your email above to start receiving job notifications."
          />
        ) : (
          <ul className="space-y-3">
            {channels.map((ch) => {
              const result = lastTest[ch.id];
              return (
                <li key={ch.id}>
                  <Card>
                    <CardContent className="flex flex-col gap-3 py-4 sm:flex-row sm:items-center sm:justify-between">
                      <div>
                        <div className="font-medium">{ch.display_name}</div>
                        <div className="text-xs uppercase tracking-wide text-muted-foreground">
                          {ch.channel_type}
                          {ch.target_label ? (
                            <span className="ml-1 normal-case">
                              &mdash; {ch.target_label}
                            </span>
                          ) : null}
                        </div>
                        {result && (
                          <div
                            role="status"
                            className={`mt-1 text-xs ${
                              result.ok ? "text-emerald-400" : "text-red-400"
                            }`}
                          >
                            {result.ok
                              ? "Test succeeded"
                              : `Test failed: ${result.error ?? "unknown"}`}
                          </div>
                        )}
                      </div>
                      <div className="flex gap-2">
                        <Button
                          variant="outline"
                          aria-label={`Send test notification to ${ch.display_name}`}
                          onClick={() => onTest(ch.id)}
                          disabled={testing === ch.id}
                        >
                          {testing === ch.id ? "Testing…" : "Send test"}
                        </Button>
                        <Button
                          variant="destructive"
                          aria-label={`Remove ${ch.display_name} channel`}
                          onClick={() => onDelete(ch.id)}
                        >
                          Remove
                        </Button>
                      </div>
                    </CardContent>
                  </Card>
                </li>
              );
            })}
          </ul>
        )}
      </div>
    </div>
  );
}
